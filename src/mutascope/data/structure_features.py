"""Per-residue features of each assay's AF2 structure, keyed by position in ``target_seq``.

Features are computed once per PDB (several assays share one) and then laid onto every assay
that maps to it (``structures.map_structures``). Assays without a mapping, including the
documented ``structure_exceptions`` (docs/adr/0003), get no rows at all.

Per residue:

- ``plddt``: AF2's confidence, stored in the B-factor column of every atom. It must be the same
  on all atoms of a residue, which confirms the column really holds per-residue pLDDT.
- ``sasa``: Shrake-Rupley solvent-accessible area of the residue's heavy atoms, measured in the
  whole chain (biotite, ProtOr radii). Hydrogens are removed first because ProtOr radii are
  united-atom radii that already account for them; the PDBs carry explicit hydrogens.
- ``rel_sasa``: ``sasa`` divided by the residue type's theoretical maximum (Tien et al. 2013).
  Not clipped: exposed termini legitimately exceed 1.
- ``sse``: P-SEA secondary structure from CA geometry (``biotite.structure.annotate_sse``):
  ``a`` helix, ``b`` strand, ``c`` coil. This is a 3-state assignment, not DSSP.
- ``contact_count``: number of other residues whose CB (CA for glycine) is closer than the
  cutoff to this residue's CB. Low-pLDDT regions are kept as they are; use ``plddt`` to
  down-weight them.

Each structure's residues are checked again against ``target_seq`` after biotite parses them,
independently of the CA-line parser in ``structures.py``.
"""

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import biotite.structure as _struc  # pyright: ignore[reportMissingTypeStubs]
import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd
from biotite.structure.io.pdb import (  # pyright: ignore[reportMissingTypeStubs]
    PDBFile as _PDBFile,
)
from omegaconf import DictConfig, OmegaConf

from mutascope.data.schemas import StructureFeatureSchema
from mutascope.data.structures import THREE_TO_ONE, StructureCheck, StructureMapping
from mutascope.utils.config import as_str_dict
from mutascope.utils.io import fresh_tmp_dir, replace_dir, write_partition
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

# biotite ships no type information. It is used as a dynamic library, and everything it returns
# is converted to typed numpy arrays in :func:`load_chain` or at the call site.
struc: Any = _struc
PDBFile: Any = _PDBFile

MAX_ASA: Mapping[str, float] = {
    "A": 129.0, "R": 274.0, "N": 195.0, "D": 193.0, "C": 167.0,
    "E": 223.0, "Q": 225.0, "G": 104.0, "H": 224.0, "I": 197.0,
    "L": 201.0, "K": 236.0, "M": 224.0, "F": 240.0, "P": 159.0,
    "S": 155.0, "T": 172.0, "W": 285.0, "Y": 263.0, "V": 174.0,
}  # fmt: skip
"""Theoretical maximum ASA in Å² per residue type (Tien et al. 2013, PLoS ONE 8:e80635, Table 1)."""


class StructureFeatureError(ValueError):
    """A structure could not be turned into valid per-residue features."""


@dataclass(frozen=True)
class FeatureParams:
    """How features are computed; from the ``structure`` config group."""

    contact_cutoff: float
    sasa_probe_radius: float
    sasa_point_number: int
    low_plddt_threshold: float

    @classmethod
    def from_config(cls, cfg: DictConfig) -> "FeatureParams":
        """Build from the resolved ``structure`` config group."""
        values: dict[str, Any] = as_str_dict(OmegaConf.to_container(cfg, resolve=True), "structure")
        return cls(
            contact_cutoff=float(values["contact_cutoff"]),
            sasa_probe_radius=float(values["sasa_probe_radius"]),
            sasa_point_number=int(values["sasa_point_number"]),
            low_plddt_threshold=float(values["low_plddt_threshold"]),
        )


@dataclass(frozen=True)
class Chain:
    """Heavy atoms of one chain, as typed arrays plus biotite's ``AtomArray``.

    biotite's annotation arrays are untyped, so they are converted once here; ``atoms`` is kept
    only to hand to biotite's own algorithms (SASA, P-SEA).
    """

    atoms: Any
    res_id: npt.NDArray[np.int64]
    res_name: npt.NDArray[np.str_]
    atom_name: npt.NDArray[np.str_]
    coord: npt.NDArray[np.float64]
    b_factor: npt.NDArray[np.float64]
    starts: npt.NDArray[np.int64]
    """Index of each residue's first atom."""


def load_chain(pdb_path: Path) -> Chain:
    """Read a single-model PDB with B-factors, without hydrogens.

    Raises:
        StructureFeatureError: The file has more than one model.
    """
    pdb = PDBFile.read(str(pdb_path))
    if pdb.get_model_count() != 1:
        raise StructureFeatureError(f"expected 1 model, found {pdb.get_model_count()}")
    atoms: Any = pdb.get_structure(model=1, extra_fields=["b_factor"])
    atoms = atoms[atoms.element != "H"]
    return Chain(
        atoms=atoms,
        res_id=np.asarray(atoms.res_id, dtype=np.int64),
        res_name=np.asarray(atoms.res_name, dtype=np.str_),
        atom_name=np.asarray(atoms.atom_name, dtype=np.str_),
        coord=np.asarray(atoms.coord, dtype=np.float64),
        b_factor=np.asarray(atoms.b_factor, dtype=np.float64),
        starts=np.asarray(struc.get_residue_starts(atoms), dtype=np.int64),
    )


def residue_features(chain: Chain, params: FeatureParams) -> pd.DataFrame:
    """Features of every residue of one chain, in file order.

    Returns:
        Columns ``residue`` (one-letter), ``plddt``, ``sasa``, ``rel_sasa``, ``sse``,
        ``contact_count``; row ``i`` is the structure's ``i``-th residue.

    Raises:
        StructureFeatureError: Non-canonical residue, numbering gap, varying B-factor within a
            residue, missing CB/CA, or an atom without a SASA radius.
    """
    if np.any(np.diff(chain.res_id[chain.starts]) != 1):
        raise StructureFeatureError("residue numbering is not consecutive")
    names = [str(n) for n in chain.res_name[chain.starts]]
    unknown = sorted(set(names) - THREE_TO_ONE.keys())
    if unknown:
        raise StructureFeatureError(f"non-canonical residues {unknown}")
    sequence = [THREE_TO_ONE[n] for n in names]
    sasa = _residue_sasa(chain, params)
    return pd.DataFrame(
        {
            "residue": sequence,
            "plddt": _plddt(chain),
            "sasa": sasa,
            "rel_sasa": sasa / np.array([MAX_ASA[aa] for aa in sequence]),
            "sse": _sse(chain),
            "contact_count": contact_counts(_contact_coords(chain), params.contact_cutoff),
        }
    )


def _residue_sasa(chain: Chain, params: FeatureParams) -> npt.NDArray[np.float64]:
    """Shrake-Rupley SASA summed over each residue's atoms."""
    atom_sasa = np.asarray(
        struc.sasa(
            chain.atoms,
            probe_radius=params.sasa_probe_radius,
            point_number=params.sasa_point_number,
            vdw_radii="ProtOr",
        ),
        dtype=np.float64,
    )
    if np.isnan(atom_sasa).any():
        bad = sorted({str(n) for n in chain.atom_name[np.isnan(atom_sasa)]})
        raise StructureFeatureError(f"no ProtOr radius for atoms {bad}")
    return np.add.reduceat(atom_sasa, chain.starts)


def _sse(chain: Chain) -> list[str]:
    """P-SEA state per residue; every residue must get one."""
    sse = [str(s) for s in struc.annotate_sse(chain.atoms)]
    if len(sse) != len(chain.starts) or "" in sse:
        raise StructureFeatureError("P-SEA did not assign every residue")
    return sse


def _plddt(chain: Chain) -> npt.NDArray[np.float64]:
    """Per-residue pLDDT; every atom of a residue must carry the same value."""
    low = np.minimum.reduceat(chain.b_factor, chain.starts)
    high = np.maximum.reduceat(chain.b_factor, chain.starts)
    varying = np.flatnonzero(low != high)
    if varying.size:
        raise StructureFeatureError(
            f"B-factor varies within {varying.size} residue(s), first at residue index "
            f"{varying[0]}: not a per-residue pLDDT"
        )
    return low


def _contact_coords(chain: Chain) -> npt.NDArray[np.float64]:
    """Coordinates of each residue's CB, or CA for glycine (which has no CB)."""
    sizes = np.diff(np.append(chain.starts, len(chain.atom_name)))
    residue_of_atom = np.repeat(np.arange(len(chain.starts)), sizes)
    wanted = np.where(chain.res_name == "GLY", "CA", "CB")
    hit = chain.atom_name == wanted
    per_residue = np.bincount(residue_of_atom[hit], minlength=len(chain.starts))
    bad = np.flatnonzero(per_residue != 1)
    if bad.size:
        i = chain.starts[bad[0]]
        raise StructureFeatureError(
            f"{bad.size} residue(s) without exactly one CB (CA for GLY), first "
            f"{chain.res_name[i]}{chain.res_id[i]}"
        )
    return chain.coord[hit]


def contact_counts(coords: npt.NDArray[np.float64], cutoff: float) -> npt.NDArray[np.int64]:
    """For each point, how many *other* points are closer than ``cutoff``."""
    distances = np.linalg.norm(coords[:, None, :] - coords[None, :, :], axis=-1)
    return ((distances < cutoff).sum(axis=1) - 1).astype(np.int64)  # minus itself


def assay_features(mapping: StructureMapping, features: pd.DataFrame) -> pd.DataFrame:
    """Place one structure's residue features at the assay's ``target_seq`` positions.

    Raises:
        StructureFeatureError: The structure's length or residues differ from the target range.
    """
    n = mapping.range_end - mapping.range_start + 1
    if len(features) != n:
        raise StructureFeatureError(
            f"{mapping.assay_id}: structure has {len(features)} residues, range has {n}"
        )
    position = np.arange(mapping.offset, mapping.offset + n, dtype=np.int64)
    expected = list(mapping.target_seq[mapping.offset : mapping.offset + n])
    differs = np.flatnonzero(features["residue"].to_numpy() != np.array(expected))
    if differs.size:
        i = int(differs[0])
        raise StructureFeatureError(
            f"{mapping.assay_id}: {differs.size} residue(s) differ from target_seq, first at "
            f"position {position[i]}: target {expected[i]}, structure {features['residue'].iat[i]}"
        )
    out = features.copy()
    out.insert(0, "position", position)
    out.insert(0, "assay_id", mapping.assay_id)
    return out


def build_structure_features(check: StructureCheck, out_dir: Path, params: FeatureParams) -> int:
    """Compute and write ``out_dir/assay_id=<id>/data.parquet`` for every mapped assay.

    Partitions are validated against :class:`StructureFeatureSchema` and built in
    ``<out_dir>.tmp``, which replaces ``out_dir`` only after every assay has succeeded.

    Returns:
        The number of residue rows written.

    Raises:
        StructureFeatureError: Any structure fails; nothing is written then.
    """
    tmp = fresh_tmp_dir(out_dir)
    per_pdb: dict[Path, pd.DataFrame] = {}
    rows = 0
    try:
        with duckdb.connect() as con:
            for mapping in check.mappings:
                if mapping.pdb_path not in per_pdb:
                    try:
                        chain = load_chain(mapping.pdb_path)
                        per_pdb[mapping.pdb_path] = residue_features(chain, params)
                    except StructureFeatureError as exc:
                        raise StructureFeatureError(f"{mapping.pdb_path.name}: {exc}") from exc
                table = assay_features(mapping, per_pdb[mapping.pdb_path])
                StructureFeatureSchema.validate(table, lazy=True)
                write_partition(con, table, tmp / f"assay_id={mapping.assay_id}")
                rows += len(table)
                log.info(
                    "structure_features",
                    assay_id=mapping.assay_id,
                    pdb_file=mapping.pdb_path.name,
                    residues=len(table),
                    mean_plddt=round(float(table["plddt"].mean()), 2),
                )
    except BaseException:
        shutil.rmtree(tmp)
        raise
    replace_dir(tmp, out_dir)
    log.info(
        "structure_features_written",
        assays=len(check.mappings),
        pdb_files=len(per_pdb),
        residues=rows,
        skipped_exceptions=len(check.allowed_exceptions),
        out_dir=str(out_dir),
    )
    return rows

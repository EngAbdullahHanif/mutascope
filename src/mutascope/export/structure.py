"""AF2 structures for the website bundle (ADR 0039): backbone only, checked against the target.

The structures are ProteinGym's AF2 models, matched to the DMS target sequences (CLAUDE.md, data
rule 6), read straight from ``ProteinGym_AF2_structures.zip`` after its sha256 matches
``data/manifest.yaml`` (:func:`read_structures_zip`), so the bundle never depends on an
extracted copy that nothing verifies.

A :class:`Backbone` keeps what the viewer draws: per residue its PDB number, amino acid, the
N, CA, C and O coordinates, and the pLDDT AF2 writes into the B-factor column. Everything else
(side chains, hydrogens, OXT) is dropped. Several assays share a structure, so a structure file
is written once per PDB and every assay that uses it is checked on its own:
:func:`check_against_target` asserts that each structure residue is the target's residue at its
mapped position and fails loudly, naming the assay and the residue, otherwise (data rule 3).
"""

import hashlib
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path, PurePosixPath

import numpy as np
import numpy.typing as npt

from mutascope.data.structures import THREE_TO_ONE
from mutascope.export.format import (
    BACKBONE_ATOMS,
    StructureFile,
    dequantize,
    quantize,
)
from mutascope.utils.hashing import file_digests

STRUCTURE_DIR = "structures"
COORDINATE_DECIMALS = 3
"""PDB coordinates have 3 decimals, so the bundle keeps them exactly."""

FloatArray = npt.NDArray[np.float64]


class StructureError(ValueError):
    """A structure file cannot be read, or disagrees with a target sequence."""


@dataclass(frozen=True)
class Backbone:
    """The backbone of a single-chain PDB, in file order."""

    pdb_file: str
    source_sha256: str
    chain: str
    residue_numbers: list[int]
    sequence: str
    coordinates: FloatArray
    """``(n, 4, 3)``, Å, atoms in :data:`~mutascope.export.format.BACKBONE_ATOMS` order."""
    plddt: FloatArray
    """``(n,)``."""


def read_structures_zip(path: Path, sha256: str) -> dict[str, str]:
    """PDB file name -> text of every ``.pdb`` in ProteinGym's AF2 archive.

    Raises:
        StructureError: The archive's sha256 is not the one recorded in ``data/manifest.yaml``,
            or two members have the same file name.
    """
    actual = file_digests(path).sha256
    if actual != sha256:
        raise StructureError(f"{path}: sha256 {actual} differs from the manifest's {sha256}")
    out: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            name = PurePosixPath(member.filename).name
            if member.is_dir() or not name.endswith(".pdb") or name.startswith("."):
                continue
            if name in out:
                raise StructureError(f"{path}: {name} appears twice")
            out[name] = archive.read(member).decode()
    return out


def parse_backbone(pdb_file: str, text: str) -> Backbone:
    """N, CA, C and O of every residue of a single-chain PDB, with its pLDDT (B-factor).

    Raises:
        StructureError: Not one chain, an alternate location, a non-canonical residue, a
            backbone atom missing or repeated, residue numbers not increasing, or a B-factor
            that differs between a residue's atoms (then it is not a per-residue pLDDT).
    """
    order: list[tuple[int, str]] = []
    atoms: dict[tuple[int, str], dict[str, tuple[float, float, float]]] = {}
    bfactors: dict[tuple[int, str], set[float]] = {}
    names: dict[tuple[int, str], str] = {}
    chains: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("ATOM"):
            continue
        where = f"{pdb_file} line {number}"
        if line[16] not in " A":
            raise StructureError(f"{where}: alternate location {line[16]!r}")
        chains.add(line[21])
        key = (int(line[22:26]), line[26])  # residue number and insertion code
        resname = line[17:20]
        if resname not in THREE_TO_ONE:
            raise StructureError(f"{where}: non-canonical residue {resname!r}")
        if order and key != order[-1] and key in atoms:
            raise StructureError(f"{where}: residue {key[0]} appears again after others")
        if key not in atoms:
            order.append(key)
            atoms[key], bfactors[key], names[key] = {}, set(), resname
        elif names[key] != resname:
            raise StructureError(f"{where}: residue {key[0]} is both {names[key]} and {resname}")
        bfactors[key].add(float(line[60:66]))
        atom = line[12:16].strip()
        if atom in BACKBONE_ATOMS:
            if atom in atoms[key]:
                raise StructureError(f"{where}: {atom} of residue {key[0]} appears twice")
            atoms[key][atom] = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
    if not order:
        raise StructureError(f"{pdb_file}: no ATOM records")
    if len(chains) != 1:
        raise StructureError(f"{pdb_file}: expected one chain, found {sorted(chains)}")
    numbers = [n for n, _ in order]
    if any(code != " " for _, code in order) or any(b <= a for a, b in pairwise(numbers)):
        raise StructureError(f"{pdb_file}: residue numbers are not strictly increasing")
    coordinates = np.empty((len(order), len(BACKBONE_ATOMS), 3))
    plddt = np.empty(len(order))
    for i, key in enumerate(order):
        missing = [a for a in BACKBONE_ATOMS if a not in atoms[key]]
        if missing:
            raise StructureError(f"{pdb_file}: residue {key[0]} has no {', '.join(missing)}")
        if len(bfactors[key]) != 1:
            raise StructureError(f"{pdb_file}: residue {key[0]} has several B-factors")
        coordinates[i] = [atoms[key][a] for a in BACKBONE_ATOMS]
        plddt[i] = next(iter(bfactors[key]))
    if not ((plddt >= 0) & (plddt <= 100)).all():
        raise StructureError(f"{pdb_file}: a B-factor outside 0-100 is not a pLDDT")
    return Backbone(
        pdb_file=pdb_file,
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        chain=next(iter(chains)),
        residue_numbers=numbers,
        sequence="".join(THREE_TO_ONE[names[k]] for k in order),
        coordinates=coordinates,
        plddt=plddt,
    )


def check_against_target(
    assay_id: str, backbone: Backbone, target: str, start: int, end: int
) -> None:
    """Every structure residue is the target's residue at its mapped position (data rule 3).

    Structure residue ``i`` maps to target position ``start + i`` (0-indexed; ``start`` is
    ``pdb_range``'s start - 1).

    Raises:
        StructureError: The structure has another number of residues than ``start..end``, or a
            residue differs; the message names the assay, the first differing residue (structure
            index, PDB number, target position) and how many differ.
    """
    n = len(backbone.sequence)
    if not 0 <= start < end <= len(target) or end - start != n:
        raise StructureError(
            f"{assay_id}: {backbone.pdb_file} has {n} residues, but maps to target "
            f"positions {start}-{end} (0-indexed, exclusive) of a {len(target)}-residue target"
        )
    expected = target[start:end]
    differ = [i for i, (a, b) in enumerate(zip(backbone.sequence, expected, strict=True)) if a != b]
    if differ:
        i = differ[0]
        raise StructureError(
            f"{assay_id}: {backbone.pdb_file} residue {i} (PDB number "
            f"{backbone.residue_numbers[i]}) is {backbone.sequence[i]}, target position "
            f"{start + i} is {expected[i]} ({len(differ)} of {n} residues differ)"
        )


def check_plddt(
    assay_id: str, backbone: Backbone, features: Mapping[int, float], start: int
) -> None:
    """The structure's pLDDT is the one in the published structure features, residue by residue.

    The features (``processed/structure_features``) were computed from the same PDBs by
    another parser (biotite); agreeing to their 2 decimals shows both read the same file the
    same way.

    Raises:
        StructureError: The features cover other residues, or a pLDDT differs.
    """
    positions = list(range(start, start + len(backbone.sequence)))
    if sorted(features) != positions:
        raise StructureError(f"{assay_id}: structure features cover other residues")
    ours = np.round(backbone.plddt, 2)
    theirs = np.array([features[p] for p in positions])
    bad = np.flatnonzero(np.abs(ours - theirs) > 0.005)
    if bad.size:
        i = int(bad[0])
        raise StructureError(
            f"{assay_id}: pLDDT at target position {start + i} is {ours[i]} in "
            f"{backbone.pdb_file}, {theirs[i]} in the structure features"
        )


def structure_file(backbone: Backbone, plddt_decimals: int) -> StructureFile:
    """The bundle's structure file; coordinates and pLDDT decode to the PDB's values.

    Raises:
        StructureError: A value does not decode exactly to the PDB's (a PDB with more
            decimals than its format allows).
    """
    out = StructureFile(
        pdb_file=backbone.pdb_file,
        source_sha256=backbone.source_sha256,
        chain=backbone.chain,
        residue_numbers=backbone.residue_numbers,
        sequence=backbone.sequence,
        coordinates=quantize(backbone.coordinates, COORDINATE_DECIMALS),
        plddt=quantize(backbone.plddt, plddt_decimals),
    )
    for name, source, encoded in (
        ("coordinates", backbone.coordinates.ravel(), out.coordinates),
        ("plddt", backbone.plddt, out.plddt),
    ):
        if np.abs(dequantize(encoded) - source).max() > 1e-9:
            raise StructureError(f"{backbone.pdb_file}: {name} do not round-trip exactly")
    return out


def structure_path(pdb_file: str) -> str:
    """``structures/<stem>.json.gz``, relative to the bundle root."""
    return f"{STRUCTURE_DIR}/{PurePosixPath(pdb_file).stem}.json.gz"

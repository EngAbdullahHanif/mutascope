"""Build the website bundle: one compressed file per protein, a manifest and the benchmarks.

:func:`build_protein` turns one assay's inputs into a :class:`~mutascope.export.format.ProteinFile`
and keeps the unrounded values beside it (:class:`SourceGrids`), so the written file can be
decoded and compared with what it was made from (:mod:`mutascope.export.fidelity`).
:class:`BundleWriter` writes the files into a temporary folder and swaps it in only when every
protein is within the size budget and the caller finishes it.

A "protein" is a ProteinGym assay (217): the measured values, the supervised model and its
landscape are per assay, and assays of one UniProt entry can have different target sequences.

Every input is checked against the target sequence before anything is encoded: a wild type
that disagrees, a missing or extra substitution, a measured value that differs between the
processed variants and the supervised landscape, or a structure residue that is not the
target's residue at its mapped position fails the build (CLAUDE.md, data rule 3).

Structure files (ADR 0039) are written once per PDB (:meth:`BundleWriter.add_structure`); they
have their own size budget and do not count towards a protein file's, since the browser
fetches them separately, after the heatmap.
"""

import gzip
import hashlib
import json
import math
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from mutascope.alphabet import AA_ORDER
from mutascope.eval.region import ScoringRegion
from mutascope.eval.schemas import LANDSCAPE_SCHEMES
from mutascope.export.format import (
    WIDTH,
    AssayInfo,
    Benchmarks,
    CalibrationScheme,
    Coded,
    DefaultModel,
    FloatArray,
    Manifest,
    ManifestEntry,
    MsaRegion,
    ProteinFile,
    StructureFeatures,
    StructureFile,
    StructureRef,
    SupervisedLandscape,
    VersionStamp,
    ZeroShotLandscape,
    decimals_below,
    decimals_for,
    dequantize,
    json_schemas,
    quantize,
)
from mutascope.export.hub import CHECKSUMS_FILE, checksums
from mutascope.export.structure import STRUCTURE_DIR, structure_path
from mutascope.utils.io import fresh_tmp_dir, replace_dir
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

PROTEIN_DIR = "proteins"
SCHEMA_DIR = "schema"
MANIFEST_FILE = "manifest.json"
BENCHMARKS_FILE = "benchmarks.json"
README_FILE = "README.md"
INTERVAL_FIELDS = ("lower_80", "upper_80", "lower_90", "upper_90")
SUPERVISED_GRIDS = ("prediction", *INTERVAL_FIELDS)
_AA_INDEX = {aa: i for i, aa in enumerate(AA_ORDER)}
IntArray = npt.NDArray[np.int64]


class BundleError(ValueError):
    """An input disagrees with the target, or a protein file is over its size budget."""


@dataclass(frozen=True)
class Encoding:
    """How numbers are rounded (``export.encoding``; see :mod:`mutascope.export.format`)."""

    score_decimals: int
    """Zero-shot scores (nats)."""
    dms_min_decimals: int
    dms_steps_per_sd: int
    """Fields in ``DMS_score`` units get a step of at most ``SD / dms_steps_per_sd``..."""
    dms_max_decimals: int
    """...and below the smallest measurement-to-bound gap, up to this many decimals."""
    plddt_decimals: int
    rel_sasa_decimals: int


@dataclass(frozen=True)
class ZeroShotModel:
    """One zero-shot model's full-target landscapes (``log P(mt) - log P(wt)``, ``(L, 20)``)."""

    name: str
    hf_id: str
    revision: str
    landscapes: Mapping[str, FloatArray]
    """By assay; an assay that is absent has no landscape from this model (no structure)."""


@dataclass(frozen=True)
class SupervisedInfo:
    """The deployed supervised model, as the landscapes and manifest name it."""

    model: str
    levels: tuple[float, float]


@dataclass(frozen=True)
class SourceGrids:
    """The unrounded ``(L, 20)`` values a protein file was encoded from, by field name
    (``measured``, ``zero_shot.<model>``, ``prediction``, ``lower_80``, ...)."""

    assay_id: str
    grids: dict[str, FloatArray]


def supervised_label(n_measured: int) -> str:
    """``"Supervised (n = 9,576)"``: the label the website shows (CLAUDE.md, Frontend)."""
    return f"Supervised (n = {n_measured:,})"


ZERO_SHOT_LABEL = "Zero-shot"


def build_protein(
    *,
    assay: Mapping[str, Any],
    variants: pd.DataFrame,
    structure: pd.DataFrame | None,
    manifest_row: Mapping[str, Any],
    supervised: pd.DataFrame | None,
    supervised_info: SupervisedInfo,
    zero_shot: list[ZeroShotModel],
    msa_region: ScoringRegion | None,
    n_assays_for_protein: int,
    encoding: Encoding,
    version: VersionStamp,
    structure_file: StructureRef | None = None,
) -> tuple[ProteinFile, SourceGrids]:
    """One assay's protein file, and the values it was encoded from.

    Args:
        assay: The assay's row of the published assays table (``assay_id``, ``UniProt_ID``,
            ``target_seq``, ``coarse_selection_type``, ``pdb_file``, ``pdb_range``, ...).
        variants: Its processed single mutants (``position``, ``wt_aa``, ``mt_aa``,
            ``DMS_score``; 0-indexed positions).
        structure: Its structure features (``position``, ``residue``, ``plddt``, ``rel_sasa``,
            ``contact_count``), or None without a structure.
        manifest_row: Its ``final_manifest`` row (``eligible``, ``reason``, ``default_model``,
            ``n_measured``, ``landscape_rows``).
        supervised: Its supervised landscape rows (``FinalLandscapeSchema``), or None if not
            eligible.
        supervised_info: The deployed model's name and interval levels.
        zero_shot: The zero-shot models the page shows.
        msa_region: Its ESM-2 benchmark region where that is part of the target, else None.
        n_assays_for_protein: Assays with this UniProt ID.
        encoding: Rounding settings.
        version: The bundle's version stamp.
        structure_file: Its written structure file, already checked against the target
            (:func:`mutascope.export.structure.check_against_target`); None without a structure.

    Raises:
        BundleError: An input disagrees with the target sequence or with another input.
    """
    assay_id = str(assay["assay_id"])
    target = str(assay["target_seq"])
    length = len(target)
    measured, n_positions = _measured_grid(assay_id, target, variants)
    sd = float(np.nanstd(measured))
    dms_decimals = decimals_for(
        sd, minimum=encoding.dms_min_decimals, steps_per_sd=encoding.dms_steps_per_sd
    )
    grids: dict[str, FloatArray] = {"measured": measured}

    eligible = bool(manifest_row["eligible"])
    landscape = None
    if eligible:
        if supervised is None:
            raise BundleError(f"{assay_id}: eligible in the manifest but has no landscape")
        landscape, supervised_grids, dms_decimals = _supervised(
            assay_id,
            target,
            supervised,
            measured,
            manifest_row,
            supervised_info,
            dms_decimals,
            encoding.dms_max_decimals,
        )
        grids |= supervised_grids
    elif supervised is not None and len(supervised):
        raise BundleError(f"{assay_id}: not eligible in the manifest but has a landscape")
    default_model = str(manifest_row["default_model"])
    default = DefaultModel(
        model=default_model,
        kind="supervised" if eligible else "zero_shot",
        label=supervised_label(int(manifest_row["n_measured"])) if eligible else ZERO_SHOT_LABEL,
        reason=None if eligible else str(manifest_row["reason"]),
    )

    zero: dict[str, ZeroShotLandscape] = {}
    for model in zero_shot:
        scores = model.landscapes.get(assay_id)
        if scores is None:
            continue
        _check_scores(assay_id, model.name, target, scores)
        grids[f"zero_shot.{model.name}"] = scores.astype(np.float64)
        zero[model.name] = ZeroShotLandscape(
            hf_id=model.hf_id,
            revision=model.revision,
            region="full",
            scores=quantize(scores, encoding.score_decimals),
        )
    if not eligible and default_model not in zero:
        raise BundleError(f"{assay_id}: default model {default_model} has no landscape here")

    has_structure = bool(assay["has_structure"])
    if has_structure != (structure is not None and len(structure) > 0):
        raise BundleError(f"{assay_id}: has_structure disagrees with the structure features")
    if has_structure != (structure_file is not None):
        raise BundleError(f"{assay_id}: has_structure disagrees with the structure file")
    info = AssayInfo(
        assay_id=assay_id,
        uniprot_id=str(assay["UniProt_ID"]),
        molecule_name=str(assay["molecule_name"]),
        source_organism=str(assay["source_organism"]),
        function_type=str(assay["coarse_selection_type"]),
        selection_assay=str(assay["selection_assay"]),
        target_seq=target,
        length=length,
        has_structure=has_structure,
        pdb_file=_optional_str(assay.get("pdb_file")),
        pdb_range=_optional_str(assay.get("pdb_range")),
        n_measured_variants=len(variants),
        n_measured_positions=n_positions,
        n_assays_for_protein=n_assays_for_protein,
    )
    protein = ProteinFile(
        assay=info,
        default=default,
        msa_region=_msa_region(msa_region, length),
        measured=quantize(measured, dms_decimals),
        structure=(
            _structure(assay_id, target, structure, encoding)
            if structure is not None and has_structure
            else None
        ),
        structure_file=structure_file,
        zero_shot=zero,
        supervised=landscape,
        version=version,
    )
    return protein, SourceGrids(assay_id, grids)


def _optional_str(value: object) -> str | None:
    return None if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)


def _measured_grid(assay_id: str, target: str, variants: pd.DataFrame) -> tuple[FloatArray, int]:
    """The ``(L, 20)`` grid of ``DMS_score`` and the number of measured positions."""
    position = variants["position"].to_numpy(np.int64)
    if len(position) == 0:
        raise BundleError(f"{assay_id}: no measured variants")
    if position.min() < 0 or position.max() >= len(target):
        raise BundleError(f"{assay_id}: a variant lies outside the {len(target)}-residue target")
    wt = variants["wt_aa"].astype(str).to_numpy()
    expected = np.array(list(target))[position]
    if (wt != expected).any():
        row = int(np.flatnonzero(wt != expected)[0])
        raise BundleError(
            f"{assay_id}: wild type {wt[row]} at position {position[row]} (row {row}), "
            f"target has {expected[row]}"
        )
    column = _columns(assay_id, variants["mt_aa"])
    if (column == np.array([_AA_INDEX[a] for a in wt])).any():
        raise BundleError(f"{assay_id}: a variant's mutant equals its wild type")
    grid = _grid(len(target), position, column, variants["DMS_score"].to_numpy(np.float64))
    if np.isnan(grid[position, column]).any():
        raise BundleError(f"{assay_id}: a measured variant has no DMS_score")
    if int(np.isfinite(grid).sum()) != len(variants):
        raise BundleError(f"{assay_id}: duplicate variants")
    return grid, len(np.unique(position))


def _columns(assay_id: str, mt_aa: "pd.Series[Any]") -> IntArray:
    unknown = sorted(set(mt_aa.astype(str)) - set(AA_ORDER))
    if unknown:
        raise BundleError(f"{assay_id}: mutant amino acids outside AA_ORDER: {unknown}")
    return np.asarray(mt_aa.astype(str).map(_AA_INDEX), dtype=np.int64)


def _grid(
    length: int,
    position: IntArray,
    column: IntArray,
    values: FloatArray,
) -> FloatArray:
    grid = np.full((length, WIDTH), np.nan)
    grid[position, column] = values
    return grid


def _supervised(
    assay_id: str,
    target: str,
    rows: pd.DataFrame,
    measured: FloatArray,
    manifest_row: Mapping[str, Any],
    info: SupervisedInfo,
    sd_decimals: int,
    max_decimals: int,
) -> tuple[SupervisedLandscape, dict[str, FloatArray], int]:
    """The supervised block, checked against the target, the measurements and the manifest,
    and the decimals of the assay's ``DMS_score``-unit fields (see :mod:`.format`)."""
    length = len(target)
    if len(rows) != (WIDTH - 1) * length or len(rows) != int(manifest_row["landscape_rows"]):
        raise BundleError(
            f"{assay_id}: {len(rows)} supervised rows, expected {(WIDTH - 1) * length} "
            f"(manifest says {manifest_row['landscape_rows']})"
        )
    position = rows["position"].to_numpy(np.int64)
    if (rows["wt_aa"].astype(str).to_numpy() != np.array(list(target))[position]).any():
        raise BundleError(f"{assay_id}: supervised landscape wild type disagrees with the target")
    column = _columns(assay_id, rows["mt_aa"])
    grids = {
        name: _grid(length, position, column, rows[name].to_numpy(np.float64))
        for name in SUPERVISED_GRIDS
    }
    wt_column = np.array([_AA_INDEX[a] for a in target])
    off_wt = np.ones((length, WIDTH), dtype=bool)
    off_wt[np.arange(length), wt_column] = False
    if np.isnan(grids["prediction"][off_wt]).any():
        raise BundleError(f"{assay_id}: the supervised landscape misses a substitution")
    dms = _grid(length, position, column, rows["dms_score"].to_numpy(np.float64))
    same = (np.isnan(dms) & np.isnan(measured)) | (dms == measured)
    if not same.all():
        raise BundleError(
            f"{assay_id}: {int((~same).sum())} measured values differ between the processed "
            "variants and the supervised landscape"
        )
    n_measured = int(np.isfinite(measured).sum())
    if n_measured != int(manifest_row["n_measured"]):
        raise BundleError(f"{assay_id}: {n_measured} measured, manifest says otherwise")
    inside = np.isfinite(measured)
    gaps = np.abs(np.stack([measured[inside] - grids[b][inside] for b in INTERVAL_FIELDS]))
    gaps = gaps[gaps > 0]
    decimals = max(
        sd_decimals,
        decimals_below(float(gaps.min()) if gaps.size else math.inf, maximum=max_decimals),
    )
    by_position = rows.groupby("position", sort=True)
    per_residue: dict[str, list[Any]] = {}
    for name in ("position_status", "structure_imputed"):
        values = by_position[name]
        if (values.nunique() != 1).any():
            raise BundleError(f"{assay_id}: {name} varies within a residue")
        per_residue[name] = values.first().tolist()
    landscape = SupervisedLandscape(
        model=info.model,
        n_measured=n_measured,
        levels=info.levels,
        prediction=quantize(grids["prediction"], decimals),
        lower_80=quantize(grids["lower_80"], decimals),
        upper_80=quantize(grids["upper_80"], decimals),
        lower_90=quantize(grids["lower_90"], decimals),
        upper_90=quantize(grids["upper_90"], decimals),
        position_status=per_residue["position_status"],
        calibration_scheme=_coded(
            length, position, column, rows["calibration_scheme"], LANDSCAPE_SCHEMES
        ),
        structure_imputed=[bool(v) for v in per_residue["structure_imputed"]],
    )
    return landscape, grids, decimals


def _coded(
    length: int,
    position: IntArray,
    column: IntArray,
    values: "pd.Series[Any]",
    codes: tuple[CalibrationScheme, ...],
) -> Coded:
    index = {code: i for i, code in enumerate(codes)}
    flat: list[int | None] = [None] * (length * WIDTH)
    for p, c, v in zip(position.tolist(), column.tolist(), values.astype(str), strict=True):
        flat[p * WIDTH + c] = index[v]
    return Coded(codes=list(codes), values=flat)


def _check_scores(assay_id: str, model: str, target: str, scores: FloatArray) -> None:
    if scores.shape != (len(target), WIDTH):
        raise BundleError(f"{assay_id}: {model} landscape shape {scores.shape}")
    if not np.isfinite(scores).all():
        raise BundleError(f"{assay_id}: {model} landscape is incomplete")
    wt = scores[np.arange(len(target)), [_AA_INDEX[a] for a in target]]
    if (wt != 0).any():
        raise BundleError(f"{assay_id}: {model} wild-type scores are not 0")


def _msa_region(region: ScoringRegion | None, length: int) -> MsaRegion | None:
    if region is None or region.is_full:
        return None
    if region.target_length != length:
        raise BundleError(f"{region.assay_id}: MSA region is for another target length")
    return MsaRegion(label=region.label, start=region.start, end=region.end)


def _structure(
    assay_id: str, target: str, frame: pd.DataFrame, encoding: Encoding
) -> StructureFeatures:
    position = frame["position"].to_numpy(np.int64)
    if position.min() < 0 or position.max() >= len(target) or frame["position"].duplicated().any():
        raise BundleError(f"{assay_id}: structure feature positions outside the target")
    if (frame["residue"].astype(str).to_numpy() != np.array(list(target))[position]).any():
        raise BundleError(f"{assay_id}: structure residue disagrees with the target")

    def per_residue(column: str) -> FloatArray:
        out = np.full(len(target), np.nan)
        out[position] = frame[column].to_numpy(np.float64)
        return out

    contacts: list[int | None] = [None] * len(target)
    for p, c in zip(position.tolist(), frame["contact_count"].astype(int).tolist(), strict=True):
        contacts[p] = c
    return StructureFeatures(
        plddt=quantize(per_residue("plddt"), encoding.plddt_decimals),
        rel_sasa=quantize(per_residue("rel_sasa"), encoding.rel_sasa_decimals),
        contact_count=contacts,
    )


def encode(document: ProteinFile | StructureFile) -> bytes:
    """The gzip file: compact JSON, level 9, no timestamp, so equal content gives equal bytes."""
    return gzip.compress(document.model_dump_json().encode(), compresslevel=9, mtime=0)


def load_protein(data: bytes) -> ProteinFile:
    """Parse and validate a protein file's bytes (what a reader does)."""
    return ProteinFile.model_validate_json(gzip.decompress(data))


def load_structure(data: bytes) -> StructureFile:
    """Parse and validate a structure file's bytes."""
    return StructureFile.model_validate_json(gzip.decompress(data))


def grid(values: Any) -> FloatArray:
    """A decoded :class:`~mutascope.export.format.Quantized` grid as ``(L, 20)`` floats."""
    return dequantize(values).reshape(-1, WIDTH)


def decoded_grids(protein: ProteinFile) -> dict[str, FloatArray]:
    """The protein file's grids, decoded, under :class:`SourceGrids`' names."""
    out = {"measured": grid(protein.measured)}
    out |= {f"zero_shot.{k}": grid(v.scores) for k, v in protein.zero_shot.items()}
    if protein.supervised is not None:
        out |= {name: grid(getattr(protein.supervised, name)) for name in SUPERVISED_GRIDS}
    return out


class BundleWriter:
    """Writes protein files into ``<out_dir>.tmp``; :meth:`finish` swaps it in.

    Every protein is written even when one is over the budget, so the failure lists all of
    them; :meth:`finish` then refuses, and the previous bundle stays as it was.
    """

    def __init__(self, out_dir: Path, *, budget_bytes: int, structure_budget_bytes: int) -> None:
        self.out_dir, self.budget_bytes = out_dir, budget_bytes
        self.structure_budget_bytes = structure_budget_bytes
        self.tmp = fresh_tmp_dir(out_dir)
        (self.tmp / PROTEIN_DIR).mkdir()
        (self.tmp / STRUCTURE_DIR).mkdir()
        self.entries: list[ManifestEntry] = []
        self.raw_bytes: dict[str, int] = {}
        self.over_budget: list[tuple[str, int]] = []
        self.structures: dict[str, tuple[str, bytes]] = {}
        """PDB file -> (bundle path, gzip bytes) of each structure written."""

    def add_structure(self, structure: StructureFile) -> tuple[str, bytes]:
        """Write one structure file, once per PDB; returns its bundle path and bytes."""
        written = self.structures.get(structure.pdb_file)
        if written is not None:
            return written
        data = encode(structure)
        relative = structure_path(structure.pdb_file)
        if any(path == relative for path, _ in self.structures.values()):
            raise BundleError(f"two PDB files would be written to {relative}")
        (self.tmp / relative).write_bytes(data)
        if len(data) > self.structure_budget_bytes:
            self.over_budget.append((relative, len(data)))
        self.structures[structure.pdb_file] = (relative, data)
        log.info("structure_written", pdb_file=structure.pdb_file, bytes=len(data))
        return relative, data

    def add(self, protein: ProteinFile) -> bytes:
        """Write one protein file; returns its bytes."""
        data = encode(protein)
        assay = protein.assay
        relative = f"{PROTEIN_DIR}/{assay.assay_id}.json.gz"
        (self.tmp / relative).write_bytes(data)
        self.raw_bytes[assay.assay_id] = len(gzip.decompress(data))
        if len(data) > self.budget_bytes:
            self.over_budget.append((assay.assay_id, len(data)))
        self.entries.append(
            ManifestEntry(
                assay_id=assay.assay_id,
                uniprot_id=assay.uniprot_id,
                molecule_name=assay.molecule_name,
                function_type=assay.function_type,
                length=assay.length,
                has_structure=assay.has_structure,
                n_measured_variants=assay.n_measured_variants,
                default_model=protein.default.model,
                label=protein.default.label,
                reason=protein.default.reason,
                file=relative,
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
        log.info("protein_written", assay_id=assay.assay_id, bytes=len(data))
        return data

    def finish(self, benchmarks: Benchmarks, version: VersionStamp, readme: str) -> Manifest:
        """Write the manifest, benchmarks, JSON Schemas, README and checksums; swap in.

        Raises:
            BundleError: A protein file is over the budget (nothing is swapped in).
        """
        if self.over_budget:
            shutil.rmtree(self.tmp)
            listed = ", ".join(f"{a} ({b:,} bytes)" for a, b in self.over_budget)
            raise BundleError(f"{len(self.over_budget)} files over their size budget: {listed}")
        entries = sorted(self.entries, key=lambda e: e.assay_id)
        manifest = Manifest(
            version=version, proteins=entries, total_bytes=sum(e.bytes for e in entries)
        )
        (self.tmp / MANIFEST_FILE).write_text(manifest.model_dump_json())
        (self.tmp / BENCHMARKS_FILE).write_text(benchmarks.model_dump_json())
        (self.tmp / SCHEMA_DIR).mkdir()
        for name, schema in json_schemas().items():
            (self.tmp / SCHEMA_DIR / name).write_text(json.dumps(schema, indent=2) + "\n")
        (self.tmp / README_FILE).write_text(readme)
        (self.tmp / CHECKSUMS_FILE).write_text(checksums(self.tmp))
        replace_dir(self.tmp, self.out_dir)
        return manifest

    def abort(self) -> None:
        """Remove the temporary folder (an input failed part-way)."""
        shutil.rmtree(self.tmp, ignore_errors=True)

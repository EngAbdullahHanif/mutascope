"""The website bundle's file format, as Pydantic models, and its compact number encoding.

Four kinds of file (ADR 0028, 0039), each exported as a versioned JSON Schema
(:func:`json_schemas`):

- ``proteins/<assay_id>.json.gz``: one :class:`ProteinFile` per ProteinGym assay, everything the
  protein page shows. The browser downloads one at a time.
- ``structures/<pdb>.json.gz``: one :class:`StructureFile` per ProteinGym AF2 structure that an
  assay with a structure uses (backbone atoms, residue numbers, pLDDT). Assays of one protein
  share it; each protein file points to its structure with a :class:`StructureRef`. The browser
  fetches it after the heatmap is drawn.
- ``manifest.json``: :class:`Manifest`, every protein with its default model and label.
- ``benchmarks.json``: :class:`Benchmarks`, the headline tables (no per-variant data).

**Grids.** A per-variant quantity is an ``(L, 20)`` grid, flattened row-major (residue, then
amino acid in ``AA_ORDER``), so one index rule, ``position * 20 + column``, serves every grid.
Positions are 0-indexed like everywhere in the package; the website adds 1 when it shows a
mutation string.

**Numbers.** A :class:`Quantized` array stores integers and one ``decimals`` count:
``value = q / 10**decimals``, ``null`` where there is no value. Integers are shorter than
decimals in JSON and decode to the same float in Python and JavaScript. Zero-shot scores (nats)
keep 3 decimals. The fields in ``DMS_score`` units of one assay (measurement, prediction,
intervals) share one ``decimals``, the larger of two rules:

- a step of at most 1/1000 of the assay's standard deviation: a fixed 3 decimals would leave
  16 steps per SD in ``HSP82_YEAST_Cote-Hammarlof_2020_growth-H2O2`` (SD 0.016);
- a step below the smallest gap between a measured value and its interval bounds
  (:func:`decimals_below`), so interval coverage is exactly the unrounded one.

That no reported number moves by more than the stated tolerance is checked, not assumed
(:mod:`mutascope.export.fidelity`).
"""

import math
from itertools import pairwise
from typing import Final, Literal, Self

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mutascope.alphabet import AA_ORDER

SCHEMA_VERSION: Final = "1.1.0"
"""Version of the bundle format. A change a reader can notice bumps it (semver); the JSON
Schema ``$id`` carries it. 1.1.0 (ADR 0039): structure files and
:attr:`ProteinFile.structure_file`."""

WIDTH = len(AA_ORDER)
BACKBONE_ATOMS: Final = ("N", "CA", "C", "O")
"""The atoms a structure file keeps per residue, in this order."""
MAX_SAFE_INTEGER = 2**53 - 1
"""Largest integer a JavaScript number holds exactly."""

FloatArray = npt.NDArray[np.float64]

PositionStatus = Literal["measured", "partially_measured", "unmeasured"]
CalibrationScheme = Literal["random", "contiguous"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)


class Quantized(_Model):
    """Fixed-point numbers: ``value = q / 10**decimals``; ``null`` is no value."""

    decimals: int = Field(ge=0, le=12)
    values: list[int | None]


class Coded(_Model):
    """Categorical values as indices into ``codes``; ``null`` is no value."""

    codes: list[CalibrationScheme]
    values: list[int | None]

    @model_validator(mode="after")
    def _indices(self) -> Self:
        if any(v is not None and not 0 <= v < len(self.codes) for v in self.values):
            raise ValueError("a coded value is not an index into codes")
        return self


def decimals_for(sd: float, *, minimum: int, steps_per_sd: int) -> int:
    """Decimals whose step is at most ``sd / steps_per_sd``, and never fewer than ``minimum``.

    Raises:
        ValueError: ``sd`` is not a positive finite number.
    """
    if not math.isfinite(sd) or sd <= 0:
        raise ValueError(f"cannot choose decimals for a spread of {sd}")
    return max(minimum, math.ceil(math.log10(steps_per_sd / sd)))


def decimals_below(gap: float, *, maximum: int) -> int:
    """Fewest decimals whose step is smaller than ``gap``, at most ``maximum``.

    Rounding to the nearest step is monotone, so ``y >= b`` stays true after rounding, but
    ``y < b`` can become a tie. Two values more than one step apart cannot round to the same
    value, so with a step below the smallest non-zero gap between a measurement and its
    interval bounds, every inclusive comparison, and with it the coverage, is unchanged.
    """
    if not math.isfinite(gap):
        return 0
    if gap <= 0:
        raise ValueError(f"gap must be positive, got {gap}")
    return min(maximum, max(0, math.floor(-math.log10(gap)) + 1))


def quantize(values: npt.ArrayLike, decimals: int) -> Quantized:
    """Round ``values`` (flattened) to ``decimals``; NaN becomes ``null``.

    Raises:
        ValueError: A value is infinite or too large to survive a JavaScript number.
    """
    array = np.asarray(values, dtype=np.float64).ravel()
    if np.isinf(array).any():
        raise ValueError("cannot encode an infinite value")
    finite = ~np.isnan(array)
    scaled = np.rint(array[finite] * 10.0**decimals)
    if scaled.size and np.abs(scaled).max() > MAX_SAFE_INTEGER:
        raise ValueError(f"values too large for {decimals} decimals")
    out: list[int | None] = [None] * array.size
    for index, value in zip(np.flatnonzero(finite), scaled.astype(np.int64).tolist(), strict=True):
        out[int(index)] = int(value)
    return Quantized(decimals=decimals, values=out)


def dequantize(quantized: Quantized) -> FloatArray:
    """The floats a reader gets back: NaN for ``null``."""
    values = [math.nan if v is None else float(v) for v in quantized.values]
    return np.asarray(values, dtype=np.float64) / 10.0**quantized.decimals


class AssayInfo(_Model):
    """What the protein page says about the assay (ProteinGym reference table)."""

    assay_id: str
    uniprot_id: str
    molecule_name: str
    source_organism: str
    function_type: str
    """ProteinGym's ``coarse_selection_type``: Activity, Binding, Expression,
    OrganismalFitness or Stability."""
    selection_assay: str
    target_seq: str
    length: int = Field(ge=1)
    has_structure: bool
    pdb_file: str | None
    pdb_range: str | None
    """1-indexed inclusive residues of ``target_seq`` the structure models."""
    n_measured_variants: int = Field(ge=0)
    """Single mutants with a ``DMS_score`` in this assay."""
    n_measured_positions: int = Field(ge=0)
    n_assays_for_protein: int = Field(ge=1)
    """Assays in the release with this UniProt ID, this one included."""


class DefaultModel(_Model):
    """What the page shows first, and why (``final_manifest``, ADR 0025)."""

    model: str
    kind: Literal["supervised", "zero_shot"]
    label: str
    """``"Supervised (n = 9,576)"`` (measured variants it was trained on) or ``"Zero-shot"``."""
    reason: str | None
    """Why there is no supervised model; null when there is one."""


class MsaRegion(_Model):
    """The benchmark scored this assay on part of its target (ADR 0012).

    ESM-2's benchmark numbers come from the target's MSA region; the landscape in this file is
    the full target, as for every assay, so its scores inside the region differ from the
    benchmark's (the model saw a longer sequence).
    """

    label: str
    """Cache region label, 1-indexed inclusive: ``"msa:281-804"``."""
    start: int = Field(ge=0)
    """0-indexed first residue."""
    end: int = Field(ge=1)
    """0-indexed, exclusive."""


class StructureFeatures(_Model):
    """Per-residue features of the assay's AF2 structure; ``null`` outside its range."""

    plddt: Quantized
    rel_sasa: Quantized
    contact_count: list[int | None]


class StructureRef(_Model):
    """Where a protein's AF2 structure is, and which residues of the target it models.

    Structure residue ``i`` (file order) is target residue ``start + i``; the builder checked
    every one of them against ``target_seq`` (CLAUDE.md, data rule 3). Residues outside
    ``start..end`` are not in the structure.
    """

    file: str
    """Path relative to the bundle root: ``structures/<pdb>.json.gz``."""
    bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    """Of the gzip file as stored; a reader checks it before parsing."""
    start: int = Field(ge=0)
    """0-indexed first target residue the structure models (``pdb_range`` start - 1)."""
    end: int = Field(ge=1)
    """0-indexed, exclusive: ``end - start`` residues."""
    low_plddt_threshold: float = Field(ge=0, le=100)
    """Residues with a lower pLDDT are low-confidence (``structure.low_plddt_threshold``)."""


class StructureFile(_Model):
    """One ProteinGym AF2 structure (matched to the DMS target sequences), backbone only.

    Per residue: its number and amino acid as in the PDB, the coordinates of
    :data:`BACKBONE_ATOMS` and AF2's pLDDT (the PDB's B-factor, the same on every atom of a
    residue). Side chains and hydrogens are dropped: the viewer draws a cartoon.
    """

    schema_version: Literal["1.1.0"] = SCHEMA_VERSION
    pdb_file: str
    """The file's name in ProteinGym's ``ProteinGym_AF2_structures.zip``."""
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    """Of the PDB file the structure was read from."""
    chain: str
    residue_numbers: list[int]
    """PDB residue numbers, strictly increasing."""
    sequence: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]+$")
    atoms: tuple[Literal["N"], Literal["CA"], Literal["C"], Literal["O"]] = BACKBONE_ATOMS
    coordinates: Quantized
    """Å, flattened ``(residue, atom, xyz)``: ``12 * n`` values, none null."""
    plddt: Quantized
    """Per residue, 0-100."""

    @model_validator(mode="after")
    def _shapes(self) -> Self:
        n = len(self.sequence)
        if len(self.residue_numbers) != n or len(self.plddt.values) != n:
            raise ValueError("residue_numbers, sequence and plddt differ in length")
        if len(self.coordinates.values) != n * len(BACKBONE_ATOMS) * 3:
            raise ValueError(f"{len(self.coordinates.values)} coordinates for {n} residues")
        if any(v is None for v in self.coordinates.values) or any(
            v is None for v in self.plddt.values
        ):
            raise ValueError("a structure file has no missing values")
        if any(b <= a for a, b in pairwise(self.residue_numbers)):
            raise ValueError("residue numbers are not strictly increasing")
        return self


class ZeroShotLandscape(_Model):
    """Masked-marginal scores ``log P(mt) - log P(wt)`` of the full target (ADR 0018)."""

    hf_id: str
    revision: str
    region: Literal["full"]
    scores: Quantized
    """``(L, 20)`` grid; the wild type is 0."""


class SupervisedLandscape(_Model):
    """The deployed supervised model's 19 x L landscape (ADR 0025); grids are ``(L, 20)`` with
    ``null`` at the wild type.

    ``prediction`` is out-of-fold at measured variants (from the fold model that never saw the
    variant) and the all-data model's elsewhere, never an in-sample fit; a variant is measured
    exactly where :attr:`ProteinFile.measured` has a value. Intervals are constant-width CV+
    in ``DMS_score`` units, calibrated on ``calibration_scheme``'s folds.
    """

    model: str
    n_measured: int = Field(ge=1)
    levels: tuple[float, float]
    prediction: Quantized
    lower_80: Quantized
    upper_80: Quantized
    lower_90: Quantized
    upper_90: Quantized
    position_status: list[PositionStatus]
    """Per residue: all 19 substitutions measured, some, or none."""
    calibration_scheme: Coded
    """``(L, 20)`` grid: whose folds the prediction and interval come from. Chosen per variant
    (``train.final.scheme_by_position``): ``random`` where the residue has other measured
    variants, ``contiguous`` where it has none, so a residue's only measured variant is
    ``contiguous`` while its unmeasured ones are ``random``."""
    structure_imputed: list[bool]
    """Per residue: a structure feature was missing (outside a partial structure) and replaced
    by its mean over measured variants."""


class SupervisedStamp(_Model):
    """What produced the supervised landscapes (``train final``)."""

    code_version: str
    config_sha256: str
    manifest_sha256: str


class VersionStamp(_Model):
    """Where every number in the bundle came from."""

    schema_version: str
    mutascope_version: str
    code_version: str
    """Git commit of the export (``-dirty`` if the code had uncommitted changes)."""
    proteingym_release: str
    dataset_repo: str
    dataset_revision: str
    model_repo: str
    model_revision: str
    supervised: SupervisedStamp


class ProteinFile(_Model):
    """Everything the website shows for one assay."""

    schema_version: Literal["1.1.0"] = SCHEMA_VERSION
    assay: AssayInfo
    default: DefaultModel
    msa_region: MsaRegion | None
    measured: Quantized
    """``(L, 20)`` grid of ``DMS_score`` (higher = fitter); ``null`` where not measured."""
    structure: StructureFeatures | None
    structure_file: StructureRef | None
    """The AF2 structure (ADR 0039); present exactly where :attr:`structure` is."""
    zero_shot: dict[str, ZeroShotLandscape]
    """By model name: ``esm2_650m`` always, ``saprot_650m_af2`` where there is a structure."""
    supervised: SupervisedLandscape | None
    version: VersionStamp

    @model_validator(mode="after")
    def _shapes(self) -> Self:
        length = self.assay.length
        if len(self.assay.target_seq) != length:
            raise ValueError("target_seq length differs from assay.length")
        grids: list[tuple[str, Quantized | Coded]] = [("measured", self.measured)]
        grids += [(f"zero_shot.{k}", v.scores) for k, v in self.zero_shot.items()]
        residues: list[tuple[str, int]] = []
        if self.supervised is not None:
            s = self.supervised
            grids += [
                (name, getattr(s, name))
                for name in ("prediction", "lower_80", "upper_80", "lower_90", "upper_90")
            ]
            grids += [("calibration_scheme", s.calibration_scheme)]
            residues += [
                ("position_status", len(s.position_status)),
                ("structure_imputed", len(s.structure_imputed)),
            ]
        if self.structure is not None:
            residues += [
                ("plddt", len(self.structure.plddt.values)),
                ("rel_sasa", len(self.structure.rel_sasa.values)),
                ("contact_count", len(self.structure.contact_count)),
            ]
        for name, grid in grids:
            if len(grid.values) != length * WIDTH:
                raise ValueError(f"{name}: {len(grid.values)} values, expected {length} x 20")
        for name, size in residues:
            if size != length:
                raise ValueError(f"{name}: {size} residues, expected {length}")
        if self.msa_region is not None and not self.msa_region.end <= length:
            raise ValueError("msa_region ends after the target")
        if (self.structure is None) != (self.structure_file is None):
            raise ValueError("structure features and structure file must come together")
        ref = self.structure_file
        if ref is not None and not ref.start < ref.end <= length:
            raise ValueError(f"structure range {ref.start}-{ref.end} outside the target")
        return self


class ManifestEntry(_Model):
    """One protein in the manifest."""

    assay_id: str
    uniprot_id: str
    molecule_name: str
    function_type: str
    length: int = Field(ge=1)
    has_structure: bool
    n_measured_variants: int = Field(ge=0)
    default_model: str
    label: str
    reason: str | None
    file: str
    """Path relative to the bundle root."""
    bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Manifest(_Model):
    """Every protein in the bundle; what the site's index page reads."""

    schema_version: Literal["1.1.0"] = SCHEMA_VERSION
    version: VersionStamp
    proteins: list[ManifestEntry]
    total_bytes: int = Field(ge=0)


class SummaryRow(_Model):
    """A ProteinGym-style aggregate (per UniProt -> per function type -> overall) with its
    bootstrap 95% CI over assays. ``group`` is the function type when ``level`` is
    ``function``."""

    model: str
    split: str | None
    metric: str
    level: str
    group: str | None
    value: float
    ci_low: float
    ci_high: float
    n_assays: int


class ComparisonRow(_Model):
    """A paired bootstrap of ``model_a - model_b`` on the same assays (ADR 0014): the only way
    two score sets are compared."""

    model_a: str
    model_b: str
    split: str | None
    metric: str
    level: str
    group: str | None
    size_bin: str | None
    value_a: float
    value_b: float
    diff: float
    ci_low: float
    ci_high: float
    share_a_better: float
    n_assays: int


class FunctionTypeRow(_Model):
    """The deployed model against its zero-shot comparator in one function type and split."""

    function_type: str
    split: str
    n_assays: int
    supervised: float
    zero_shot: float
    diff: float
    ci_low: float
    ci_high: float
    gain_shown: bool
    """The paired 95% CI of the difference lies above 0."""


class Caveat(_Model):
    """A function type whose supervised gain is not shown under some splits."""

    function_type: str
    n_assays: int
    splits: list[str]
    text: str


class CalibrationRow(_Model):
    """Coverage of the deployed interval design (``train uncertainty``, ADR 0024)."""

    scheme: str
    mode: str
    """Calibration folds: ``scheme`` (split like the test split) or ``random``."""
    level: float
    size_bin: str
    statistic: str
    value: float


class FinalCoverage(_Model):
    """Share of measured variants inside their out-of-fold interval in the deployed
    landscapes (``final_checks``), summarised over assays."""

    level: float
    n_assays: int
    mean: float
    median: float
    min: float
    max: float


class Benchmarks(_Model):
    """The benchmark numbers the website shows; every value is copied from a results table."""

    schema_version: Literal["1.1.0"] = SCHEMA_VERSION
    version: VersionStamp
    deployed_model: str
    zero_shot_comparator: str
    zero_shot_summary: list[SummaryRow]
    """Every zero-shot score set on its own assays (Spearman, AUC, MCC, NDCG, top recall)."""
    zero_shot_shared: list[SummaryRow]
    """Zero-shot score sets on the assays they share, for side-by-side display."""
    zero_shot_comparisons: list[ComparisonRow]
    supervised_summary: list[SummaryRow]
    """The supervised ladder and zero-shot on the same test folds, per split (Spearman)."""
    supervised_comparisons: list[ComparisonRow]
    """Each supervised run against zero-shot on the same folds, overall, per function type and
    per assay size bin."""
    function_types: list[FunctionTypeRow]
    caveats: list[Caveat]
    calibration: list[CalibrationRow]
    final_coverage: list[FinalCoverage]


def json_schemas() -> dict[str, dict[str, object]]:
    """File name -> JSON Schema of each bundle file, with a versioned ``$id``."""
    schemas: dict[str, dict[str, object]] = {}
    for name, model in (
        ("protein", ProteinFile),
        ("manifest", Manifest),
        ("benchmarks", Benchmarks),
        ("structure", StructureFile),
    ):
        schema = model.model_json_schema()
        schemas[f"{name}.schema.json"] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:mutascope:bundle:{SCHEMA_VERSION}:{name}",
            **schema,
        }
    return schemas

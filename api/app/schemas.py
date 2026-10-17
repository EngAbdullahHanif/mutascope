"""Response models of the ``/v1`` routes and the probes.

Bundle content is passed through with the bundle's own models (:mod:`mutascope.export.format`)
where the API adds nothing (assay metadata, benchmarks, version stamp). Landscapes differ: the
bundle's fixed-point integers are decoded to plain numbers, so an API client needs no decoding
rule. Grids keep the bundle's layout: ``(L, 20)`` flattened row-major, residue then amino acid
in ``aa_order``, 0-indexed positions, ``null`` where there is no value.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mutascope.export.format import (
    AssayInfo,
    Benchmarks,
    CalibrationScheme,
    DefaultModel,
    MsaRegion,
    PositionStatus,
    VersionStamp,
)

Grid = list[float | None]
FunctionType = Literal["Activity", "Binding", "Expression", "OrganismalFitness", "Stability"]
ZERO_SHOT_LABEL = "Zero-shot"


class LandscapeModel(StrEnum):
    """Which landscape to return: the protein's default model, or a zero-shot one."""

    default = "default"
    esm2_650m = "esm2_650m"
    saprot = "saprot"


ZERO_SHOT_KEYS = {LandscapeModel.esm2_650m: "esm2_650m", LandscapeModel.saprot: "saprot_650m_af2"}
"""Query value -> model name in the bundle's ``zero_shot`` block."""


class _Response(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True)


class ApiVersion(_Response):
    """What produced a response: the API code and the bundle it serves."""

    api_version: str
    bundle_revision: str | None
    """Hub commit of the bundle; null when read from a local copy (development only)."""
    bundle: VersionStamp


class ProteinSummary(_Response):
    """One protein (ProteinGym assay) in the index."""

    assay_id: str
    uniprot_id: str
    molecule_name: str
    function_type: str
    length: int
    has_structure: bool
    n_measured_variants: int
    default_model: str
    label: str
    """``"Supervised (n = …)"`` or ``"Zero-shot"``."""


class ProteinList(_Response):
    count: int
    proteins: list[ProteinSummary]
    version: ApiVersion


class AvailableModel(_Response):
    """A landscape this protein has, by the ``model`` query value that returns it."""

    model: LandscapeModel
    name: str
    """The model the landscape comes from."""
    kind: Literal["supervised", "zero_shot"]
    label: str


class ProteinDetail(_Response):
    assay: AssayInfo
    default: DefaultModel
    """The default model, its label and, for zero-shot proteins, why there is no supervised
    model."""
    msa_region: MsaRegion | None
    models: list[AvailableModel]
    version: ApiVersion


class _Landscape(_Response):
    assay_id: str
    model: str
    """The model the landscape comes from (``ridge_seq_struct``, ``esm2_650m``, …)."""
    label: str
    """``"Supervised (n = …)"`` or ``"Zero-shot"``: always shown with the numbers."""
    target_seq: str
    length: int
    aa_order: str
    measured: Grid
    """``DMS_score`` of measured variants (higher = fitter); null elsewhere."""
    version: ApiVersion


class SupervisedLandscapeResponse(_Landscape):
    """The deployed supervised model (ADR 0025) with CV+ intervals (ADR 0024)."""

    kind: Literal["supervised"] = "supervised"
    n_measured: int
    levels: tuple[float, float]
    prediction: Grid
    """Out-of-fold at measured variants (the fold model that never saw the variant), the
    all-data model elsewhere; null at the wild type."""
    oof_prediction: Grid
    """``prediction`` at measured variants only: the out-of-fold prediction to set against
    ``measured``; null elsewhere."""
    lower_80: Grid
    upper_80: Grid
    lower_90: Grid
    upper_90: Grid
    position_status: list[PositionStatus]
    calibration_scheme: list[CalibrationScheme | None]
    """Per variant (grid): whose folds the prediction and interval come from."""
    structure_imputed: list[bool]
    """Per residue: a structure feature was missing and imputed."""


class ZeroShotLandscapeResponse(_Landscape):
    """Masked-marginal scores of the full target, ``log P(mt) - log P(wt)`` (nats)."""

    kind: Literal["zero_shot"] = "zero_shot"
    hf_id: str
    revision: str
    scores: Grid
    """Wild type 0."""
    msa_region: MsaRegion | None
    """ESM-2 only: the benchmark scored this region of the target, not the full target."""


Landscape = Annotated[
    SupervisedLandscapeResponse | ZeroShotLandscapeResponse, Field(discriminator="kind")
]


class BenchmarksResponse(_Response):
    benchmarks: Benchmarks
    version: ApiVersion


class Health(_Response):
    status: Literal["ok"]
    version: str


class Ready(_Response):
    status: Literal["ready"]
    schema_version: str
    bundle_revision: str | None
    proteins: int
    models: list[str]

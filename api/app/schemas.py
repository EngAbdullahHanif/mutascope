"""Response models of the ``/v1`` routes and the probes.

Bundle content is passed through with the bundle's own models (:mod:`mutascope.export.format`)
where the API adds nothing (assay metadata, benchmarks, version stamp). Landscapes differ: the
bundle's fixed-point integers are decoded to plain numbers, so an API client needs no decoding
rule. Grids keep the bundle's layout: ``(L, 20)`` flattened row-major, residue then amino acid
in ``aa_order``, 0-indexed positions, ``null`` where there is no value.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from api.app.live import LiveModelName
from mutascope.alphabet import AA_ORDER
from mutascope.data.parse import MutationParseError, parse_mutation
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
    score_default_model: str
    """Model of ``POST /v1/score`` when the request names none."""
    precise_max_length: int
    """Longest sequence ``POST /v1/jobs`` accepts."""


# Live scoring (ADR 0031) ------------------------------------------------------------------

MAX_SEQUENCE_LENGTH = 1022
"""ESM-2's longest input without windows (1,024 tokens with ``<cls>`` and ``<eos>``)."""
MAX_MUTATIONS = 50
CANONICAL_PATTERN = f"^[{AA_ORDER}]+$"
MUTATION_PATTERN = f"^[{AA_ORDER}][1-9][0-9]*[{AA_ORDER}]$"


def _canonical(sequence: str) -> str:
    bad = [(i, aa) for i, aa in enumerate(sequence) if aa not in AA_ORDER]
    if bad:
        i, aa = bad[0]
        more = f" (and {len(bad) - 1} more)" if len(bad) > 1 else ""
        raise ValueError(
            f"only the 20 canonical amino acids are accepted, as uppercase letters "
            f"({AA_ORDER}); found {aa!r} at position {i + 1}{more}"
        )
    return sequence


def _substitution(text: str) -> str:
    try:
        parse_mutation(text)
    except MutationParseError as exc:
        raise ValueError(
            f"{exc}; a mutation is <wild type><1-indexed position><mutant>, e.g. M1A, "
            "with canonical amino acids and mutant different from wild type"
        ) from exc
    return text


ProteinSequence = Annotated[
    str,
    Field(
        min_length=1,
        max_length=MAX_SEQUENCE_LENGTH,
        description=f"Protein sequence: 1-{MAX_SEQUENCE_LENGTH} canonical amino acids, uppercase.",
        json_schema_extra={"pattern": CANONICAL_PATTERN},
    ),
    AfterValidator(_canonical),
]
Mutation = Annotated[
    str,
    Field(
        description="A single substitution, 1-indexed (`M1A`); its wild type must match.",
        json_schema_extra={"pattern": MUTATION_PATTERN},
    ),
    AfterValidator(_substitution),
]


class _Request(BaseModel):
    model_config = ConfigDict(use_attribute_docstrings=True, extra="forbid")


class ScoreRequest(_Request):
    """Fast mode (wild-type marginal) for one sequence."""

    sequence: ProteinSequence
    model: LiveModelName | None = None
    """``esm2_35m`` or ``esm2_150m``; null: the live default (``esm2_150m`` unless the Space
    measurement moved it to 35M, ADR 0029)."""
    mutations: Annotated[list[Mutation], Field(min_length=1, max_length=MAX_MUTATIONS)] | None = (
        None
    )
    """Up to 50 substitutions to score; null: the full 19 x L landscape."""


class JobRequest(_Request):
    """Precise mode (masked marginal) for one sequence, as a background job."""

    sequence: ProteinSequence
    """At most the precise-mode length limit (``/ready`` reports it; 422 beyond it)."""
    model: Literal["esm2_35m"] = "esm2_35m"
    """Precise mode runs on ESM-2 35M only."""


class _Live(_Response):
    """A live zero-shot result and the model files behind it."""

    kind: Literal["zero_shot"] = "zero_shot"
    label: Literal["Zero-shot"] = "Zero-shot"
    """Always zero-shot: no measurement was used. Not a clinical prediction."""
    model: str
    """The served model config, e.g. ``esm2_150m_onnx_fp32``."""
    hf_id: str
    file: str
    revision: str
    """Hub commit of the ONNX file; ``local:<dir>`` for a development copy."""
    target_seq: str
    length: int
    aa_order: str
    version: ApiVersion


class MutationScore(_Response):
    mutation: str
    """As given (1-indexed)."""
    position: int
    """0-indexed, as in the landscape grids."""
    wt: str
    mt: str
    score: float
    """``log P(mt) - log P(wt)`` in nats; higher = predicted fitter."""


class ScoreResponse(_Live):
    """Wild-type marginal: ``log P(mt | x) - log P(wt | x)`` from one unmasked pass."""

    method: Literal["wt_marginal"] = "wt_marginal"
    scores: Grid | None
    """The full landscape, ``(L, 20)`` row-major, wild type 0; null when ``mutations`` were
    given."""
    mutations: list[MutationScore] | None


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class JobResponse(_Live):
    """A precise-mode job: masked marginal, ``log P(mt | x_masked) - log P(wt | x_masked)``."""

    id: str
    status: JobStatus
    method: Literal["masked_marginal"] = "masked_marginal"
    done: int
    """Residues scored so far."""
    total: int
    """Residues to score (the sequence length)."""
    cached: bool
    """The result came from the cache (an identical earlier job); nothing was run."""
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    expires_at: datetime | None
    """When a finished job and its result are deleted (one hour after it finished)."""
    error: str | None
    scores: Grid | None
    """The full landscape once ``succeeded``, ``(L, 20)`` row-major; null before."""


class LandscapeRow(_Response):
    position: int
    """0-indexed residue."""
    scores: list[float]
    """20 scores in ``aa_order``, wild type 0."""


class JobProgress(_Response):
    """One finished forward batch of a running job (SSE ``progress`` event)."""

    id: str
    kind: Literal["zero_shot"] = "zero_shot"
    label: Literal["Zero-shot"] = "Zero-shot"
    done: int
    total: int
    rows: list[LandscapeRow]

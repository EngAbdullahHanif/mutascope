"""Routes: the read-only ``/v1`` resources and the liveness / readiness probes."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from api.app import problems
from api.app.bundle import Bundle
from api.app.schemas import (
    ZERO_SHOT_KEYS,
    ZERO_SHOT_LABEL,
    ApiVersion,
    AvailableModel,
    BenchmarksResponse,
    FunctionType,
    Grid,
    Health,
    Landscape,
    LandscapeModel,
    ProteinDetail,
    ProteinList,
    ProteinSummary,
    Ready,
    SupervisedLandscapeResponse,
    ZeroShotLandscapeResponse,
)
from mutascope import __version__
from mutascope.alphabet import AA_ORDER
from mutascope.export.format import CalibrationScheme, Coded, ProteinFile, Quantized
from mutascope.scoring.base import Scorer


@dataclass(frozen=True)
class AppState:
    """What the lifespan hook loads once."""

    bundle: Bundle
    scorers: Mapping[str, Scorer]


def get_state(request: Request) -> AppState:
    state = getattr(request.app.state, "mutascope", None)
    if not isinstance(state, AppState):
        raise problems.not_ready("The bundle and models are not loaded yet.")
    return state


State = Annotated[AppState, Depends(get_state)]


def _version(state: AppState) -> ApiVersion:
    return ApiVersion(
        api_version=__version__,
        bundle_revision=state.bundle.source.revision,
        bundle=state.bundle.manifest.version,
    )


def decode(grid: Quantized) -> Grid:
    """Fixed-point to numbers: ``q / 10**decimals`` (correctly rounded), null kept."""
    scale = 10**grid.decimals
    return [None if q is None else q / scale for q in grid.values]


def decode_codes(coded: Coded) -> list[CalibrationScheme | None]:
    return [None if v is None else coded.codes[v] for v in coded.values]


def _protein(state: AppState, assay_id: str) -> ProteinFile:
    if assay_id not in state.bundle.entries:
        raise problems.protein_not_found(assay_id)
    return state.bundle.protein(assay_id)


def _available(protein: ProteinFile) -> list[AvailableModel]:
    default = protein.default
    models = [
        AvailableModel(
            model=LandscapeModel.default, name=default.model, kind=default.kind, label=default.label
        )
    ]
    models += [
        AvailableModel(model=query, name=name, kind="zero_shot", label=ZERO_SHOT_LABEL)
        for query, name in ZERO_SHOT_KEYS.items()
        if name in protein.zero_shot
    ]
    return models


router = APIRouter()
v1 = APIRouter(prefix="/v1", responses=problems.responses(503))


@router.get("/health", response_model=Health)
async def health() -> Health:
    """Liveness: the process answers. Used by the Docker HEALTHCHECK."""
    return Health(status="ok", version=__version__)


@router.get("/ready", response_model=Ready, responses=problems.responses(503))
async def ready(state: State) -> Ready:
    """Readiness: the bundle (supported schema version) and the ONNX models are loaded."""
    return Ready(
        status="ready",
        schema_version=state.bundle.schema_version,
        bundle_revision=state.bundle.source.revision,
        proteins=len(state.bundle.entries),
        models=sorted(state.scorers),
    )


@v1.get("/proteins", response_model=ProteinList, responses=problems.responses(422))
async def list_proteins(
    state: State,
    function_type: Annotated[
        FunctionType | None, Query(description="ProteinGym function type")
    ] = None,
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=100,
            description="Case-insensitive search in assay id, UniProt id and molecule name",
        ),
    ] = None,
) -> ProteinList:
    """Every protein (ProteinGym assay) with its default model and label."""
    needle = q.casefold() if q is not None else None
    proteins = [
        ProteinSummary(
            assay_id=e.assay_id,
            uniprot_id=e.uniprot_id,
            molecule_name=e.molecule_name,
            function_type=e.function_type,
            length=e.length,
            has_structure=e.has_structure,
            n_measured_variants=e.n_measured_variants,
            default_model=e.default_model,
            label=e.label,
        )
        for e in state.bundle.manifest.proteins
        if (function_type is None or e.function_type == function_type)
        and (
            needle is None
            or any(needle in s.casefold() for s in (e.assay_id, e.uniprot_id, e.molecule_name))
        )
    ]
    return ProteinList(count=len(proteins), proteins=proteins, version=_version(state))


@v1.get(
    "/proteins/{assay_id}", response_model=ProteinDetail, responses=problems.responses(404, 422)
)
def get_protein(state: State, assay_id: str) -> ProteinDetail:
    """Assay metadata, the default model and why it was chosen, and the landscapes on offer."""
    protein = _protein(state, assay_id)
    return ProteinDetail(
        assay=protein.assay,
        default=protein.default,
        msa_region=protein.msa_region,
        models=_available(protein),
        version=_version(state),
    )


@v1.get(
    "/proteins/{assay_id}/landscape",
    response_model=Landscape,
    responses=problems.responses(404, 422),
)
def get_landscape(
    state: State,
    assay_id: str,
    model: Annotated[
        LandscapeModel,
        Query(description="default: the protein's default model; or a zero-shot model"),
    ] = LandscapeModel.default,
) -> SupervisedLandscapeResponse | ZeroShotLandscapeResponse:
    """A full 19 x L landscape as ``(L, 20)`` grids, with its label and version stamp.

    Supervised landscapes carry the prediction, its 80% and 90% CV+ intervals, the measured
    value and out-of-fold prediction at measured variants, and per-residue status. 404 when
    the model has no landscape for this protein (SaProt needs a structure).
    """
    protein = _protein(state, assay_id)
    measured = decode(protein.measured)
    version = _version(state)
    seq, length = protein.assay.target_seq, protein.assay.length
    if model is LandscapeModel.default and protein.supervised is not None:
        s = protein.supervised
        prediction = decode(s.prediction)
        oof = [p if m is not None else None for p, m in zip(prediction, measured, strict=True)]
        return SupervisedLandscapeResponse(
            assay_id=assay_id,
            target_seq=seq,
            length=length,
            aa_order=AA_ORDER,
            measured=measured,
            version=version,
            model=s.model,
            label=protein.default.label,
            n_measured=s.n_measured,
            levels=s.levels,
            prediction=prediction,
            oof_prediction=oof,
            lower_80=decode(s.lower_80),
            upper_80=decode(s.upper_80),
            lower_90=decode(s.lower_90),
            upper_90=decode(s.upper_90),
            position_status=s.position_status,
            calibration_scheme=decode_codes(s.calibration_scheme),
            structure_imputed=s.structure_imputed,
        )
    name = protein.default.model if model is LandscapeModel.default else ZERO_SHOT_KEYS[model]
    landscape = protein.zero_shot.get(name)
    if landscape is None:
        why = (
            "SaProt is structure-aware and this assay has no AF2 structure "
            "(SaProt covers 213 of 217 assays)"
            if not protein.assay.has_structure
            else "the bundle has no landscape from this model"
        )
        raise problems.model_not_available(assay_id, name, why)
    return ZeroShotLandscapeResponse(
        assay_id=assay_id,
        target_seq=seq,
        length=length,
        aa_order=AA_ORDER,
        measured=measured,
        version=version,
        model=name,
        label=ZERO_SHOT_LABEL,
        hf_id=landscape.hf_id,
        revision=landscape.revision,
        scores=decode(landscape.scores),
        msa_region=protein.msa_region if name == "esm2_650m" else None,
    )


@v1.get("/benchmarks", response_model=BenchmarksResponse)
async def get_benchmarks(state: State) -> BenchmarksResponse:
    """The headline tables: zero-shot and supervised summaries, paired comparisons, caveats."""
    return BenchmarksResponse(benchmarks=state.bundle.benchmarks, version=_version(state))


router.include_router(v1)

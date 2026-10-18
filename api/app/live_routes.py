"""Live scoring routes: ``POST /v1/score`` (fast mode) and ``/v1/jobs`` (precise mode, SSE).

Everything here is zero-shot: ESM-2 35M / 150M on ONNX Runtime, no measurement involved, and
every response says so (``kind``, ``label``). Validation happens before any model runs: the
request models check the alphabet, the length and the mutation format, and the routes check
each mutation's wild type and the precise-mode length limit (422 problem details either way).

Rate limits (ADR 0032) are dependencies, checked before the body's content is: fast mode per
client and over all clients, job submission per client, and reads (status, cancel, opening a
stream) under the read limit. An open stream also holds one of the client's stream slots.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.sse import EventSourceResponse, ServerSentEvent

from api.app import problems
from api.app.jobs import Job, parse_last_event_id
from api.app.live import (
    DECIMALS,
    LiveModelName,
    cached_fast,
    compute_fast,
    mutation_errors,
    to_grid,
)
from api.app.logs import sequence_fields
from api.app.ratelimit import client_of, rate_limit, stream_slot
from api.app.routes import State, api_version
from api.app.schemas import (
    JobRequest,
    JobResponse,
    MutationScore,
    ScoreRequest,
    ScoreResponse,
)
from mutascope.alphabet import AA_ORDER
from mutascope.data.parse import parse_mutation
from mutascope.scoring.base import score_mutants
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

CACHE_HEADER = "X-MutaScope-Cache"
"""``hit`` or ``miss``: whether a fast-mode landscape came from the cache."""

live = APIRouter(prefix="/v1", responses=problems.responses(503))
READ = Depends(rate_limit("read"))


def get_job(state: State, job_id: str) -> Job:
    return state.jobs.get(job_id)


JobDep = Annotated[Job, Depends(get_job)]


@live.post(
    "/score",
    response_model=ScoreResponse,
    responses=problems.responses(400, 413, 422, 429),
    dependencies=[Depends(rate_limit("score", "score_global"))],
)
async def score(state: State, request: ScoreRequest, response: Response) -> ScoreResponse:
    """Fast mode: the wild-type marginal of ESM-2 35M or 150M (one forward pass).

    Without ``mutations`` the full 19 x L landscape; with them, just their scores. An identical
    request (model, revision, sequence) is answered from the cache. A new one waits for a
    scoring slot (one forward pass at a time, precise-mode batches included); 503 with
    ``Retry-After`` when none frees up in time.
    """
    seq = request.sequence
    if request.mutations is not None and (errors := mutation_errors(seq, request.mutations)):
        raise RequestValidationError(errors)
    model = state.live[request.model or LiveModelName(state.settings.score_default_model)]
    landscape = await asyncio.to_thread(cached_fast, model, seq, state.score_cache, state.metrics)
    hit = landscape is not None
    if landscape is None:
        landscape = await state.inference.run(
            compute_fast, model, seq, state.score_cache, state.metrics, wait=False
        )
    response.headers[CACHE_HEADER] = "hit" if hit else "miss"
    log.info(
        "score_request",
        model=model.key,
        cache="hit" if hit else "miss",
        mutations=None if request.mutations is None else len(request.mutations),
        **sequence_fields(seq),
    )
    mutations = None
    if request.mutations is not None:
        values = score_mutants(landscape, seq, request.mutations)
        mutations = [
            MutationScore(
                mutation=text,
                position=(sub := parse_mutation(text)).position,
                wt=sub.wt,
                mt=sub.mt,
                score=round(float(value), DECIMALS),
            )
            for text, value in zip(request.mutations, values, strict=True)
        ]
    return ScoreResponse(
        model=model.name,
        hf_id=model.hf_id,
        file=model.file,
        revision=model.revision,
        target_seq=seq,
        length=len(seq),
        aa_order=AA_ORDER,
        version=api_version(state.bundle),
        scores=to_grid(landscape) if mutations is None else None,
        mutations=mutations,
    )


@live.post(
    "/jobs",
    status_code=202,
    response_model=JobResponse,
    responses=problems.responses(400, 413, 422, 429),
    dependencies=[Depends(rate_limit("jobs"))],
)
async def create_job(
    state: State, http: Request, request: JobRequest, response: Response
) -> JobResponse:
    """Precise mode: the masked marginal of ESM-2 35M (one pass per residue), as a job.

    The job runs when the ones before it have finished (one at a time); follow it with
    ``GET /v1/jobs/{id}/stream``. An identical earlier job's result is returned at once,
    already ``succeeded``. 429 when the queue is full, or this client already has a job
    queued or running.
    """
    limit = state.settings.precise_max_length
    if len(request.sequence) > limit:
        raise problems.too_long_for_precise(len(request.sequence), limit)
    job = state.jobs.submit(
        state.live[LiveModelName(request.model)],
        request.sequence,
        client=client_of(http),
        request_id=getattr(http.state, "request_id", None),
    )
    response.headers["Location"] = f"/v1/jobs/{job.id}"
    return state.jobs.response(job)


@live.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    responses=problems.responses(404, 422, 429),
    dependencies=[READ],
)
async def get_job_status(state: State, job: JobDep, response: Response) -> JobResponse:
    """Status, progress and, once it has succeeded, the full landscape."""
    response.headers["Cache-Control"] = "no-store"
    return state.jobs.response(job)


@live.delete(
    "/jobs/{job_id}",
    response_model=JobResponse,
    responses=problems.responses(404, 409, 422, 429),
    dependencies=[READ],
)
async def cancel_job(state: State, job_id: str) -> JobResponse:
    """Cancel a queued or running job (409 if it has already finished)."""
    return state.jobs.response(state.jobs.cancel(job_id))


@live.get(
    "/jobs/{job_id}/stream",
    response_class=EventSourceResponse,
    responses=problems.responses(404, 422, 429),
    dependencies=[READ, Depends(stream_slot)],
)
async def stream_job(
    state: State,
    job: JobDep,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> AsyncIterator[ServerSentEvent]:
    """Server-Sent Events: ``queued``, ``running``, one ``progress`` per forward batch (its
    landscape rows), then ``succeeded`` (with the landscape), ``failed`` or ``cancelled``.

    Each event's ``id`` is its index; reconnecting with ``Last-Event-ID`` resumes after it.
    The stream ends when the job has finished. A client may hold a few streams open at once
    (429 beyond that). Event data are JSON: the job (``JobResponse``)
    for status events, ``JobProgress`` for ``progress``.
    """
    async for event in state.jobs.events(job, after=parse_last_event_id(last_event_id)):
        yield event

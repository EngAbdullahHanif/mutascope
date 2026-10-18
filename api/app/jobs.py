"""Precise-mode jobs: one running at a time, a bounded queue, results kept for an hour.

The masked marginal takes minutes beyond a few hundred residues (ADR 0029), so it runs in the
background. :class:`JobManager` lives on the event loop: one worker task takes queued jobs in
order and runs each forward batch on the inference pool (:mod:`api.app.inference`), taking
turns with fast-mode requests, so the loop keeps serving while the CPU scores. All job state is
changed on the loop, between batches, which is why no lock is needed.

- **Queue.** At most ``queue_size`` jobs wait behind the running one; another submission is a
  429. A cached result (an identical earlier job) never enters the queue.
- **Events.** Each job keeps an append-only log: ``queued``, ``running``, one ``progress`` per
  forward batch (with that batch's landscape rows), then ``succeeded``, ``failed`` or
  ``cancelled``. A stream replays the log from the start (or after ``Last-Event-ID``) and then
  waits for new entries, so a late or reconnecting client sees every event once, in order.
- **Cancellation.** A queued job leaves the queue. A running job is marked cancelled at once;
  the worker drops the batch in flight and moves on.
- **Expiry.** A finished job is deleted ``ttl_seconds`` after it finished; later requests get
  404. Expired jobs are swept whenever the manager is used.
- **Per client.** A client may have ``max_active_per_client`` jobs queued or running; another
  is a 429, so one client cannot fill the queue (ADR 0032).

Log lines carry the job id, the id of the request that submitted it, and the sequence's sha256
and length, never the sequence.
"""

import asyncio
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import sentry_sdk
import structlog
from fastapi.sse import ServerSentEvent

from api.app import problems
from api.app.inference import Inference
from api.app.live import DECIMALS, LiveModel, to_grid
from api.app.logs import sequence_fields
from api.app.metrics import Metrics
from api.app.schemas import (
    ApiVersion,
    JobProgress,
    JobResponse,
    JobStatus,
    LandscapeRow,
)
from api.app.score_cache import ScoreCache
from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import FloatArray
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

FINISHED: frozenset[JobStatus] = frozenset({"succeeded", "failed", "cancelled"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class JobEvent:
    """One entry of a job's log: the SSE event name and its JSON payload."""

    name: str
    data: str


@dataclass(eq=False)
class Job:
    """A precise-mode request and everything computed for it so far."""

    id: str
    model: LiveModel
    sequence: str
    created_at: datetime
    client: str
    """The address that submitted it (``max_active_per_client``)."""
    request_id: str | None
    """The id of the submitting request, bound into the worker's log lines for this job."""
    landscape: FloatArray
    """``(L, 20)``, NaN until a row's batch finishes."""
    status: JobStatus = "queued"
    done: int = 0
    cached: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    expires_at: datetime | None = None
    error: str | None = None
    events: list[JobEvent] = field(default_factory=list[JobEvent])
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    """Set (and replaced) whenever an event is added; streams wait on it."""

    @property
    def finished(self) -> bool:
        return self.status in FINISHED


class JobManager:
    """Queue, worker and store of precise-mode jobs (see the module docstring)."""

    def __init__(
        self,
        *,
        cache: ScoreCache,
        inference: Inference,
        version: ApiVersion,
        queue_size: int,
        ttl_seconds: float,
        metrics: Metrics,
        max_active_per_client: int,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.cache = cache
        self.inference = inference
        self.metrics = metrics
        self.max_active_per_client = max_active_per_client
        self.version = version
        self.queue_size = queue_size
        self.ttl = timedelta(seconds=ttl_seconds)
        self.now = now
        self._jobs: dict[str, Job] = {}
        self._pending: deque[Job] = deque()
        self._running: Job | None = None
        self._wakeup = asyncio.Event()

    @property
    def waiting(self) -> int:
        """Jobs in the queue (not the running one)."""
        return len(self._pending)

    @property
    def running(self) -> int:
        return int(self._running is not None and not self._running.finished)

    # -- requests -----------------------------------------------------------------------------

    def submit(
        self, model: LiveModel, sequence: str, *, client: str, request_id: str | None = None
    ) -> Job:
        """A new job for ``sequence``: finished at once from the cache, or queued.

        Raises:
            ApiError: 429 when ``client`` already has ``max_active_per_client`` unfinished
                jobs, or ``queue_size`` jobs are already waiting.
        """
        self._sweep()
        now = self.now()
        job = Job(
            id=uuid.uuid4().hex,
            model=model,
            sequence=sequence,
            created_at=now,
            client=client,
            request_id=request_id,
            landscape=np.full((len(sequence), len(AA_ORDER)), np.nan, dtype=np.float32),
        )
        cached = self.cache.get(model.key_for("masked", sequence), len(sequence))
        self.metrics.cache_lookup("masked", hit=cached is not None)
        if cached is not None:
            job.landscape, job.done, job.cached, job.started_at = cached, len(sequence), True, now
            self._jobs[job.id] = job
            self._finish(job, "succeeded")
            log.info("job_cached", job=job.id, **sequence_fields(sequence))
            return job
        active = sum(1 for j in self._jobs.values() if j.client == client and not j.finished)
        if active >= self.max_active_per_client:
            raise problems.too_many_jobs(self.max_active_per_client)
        if len(self._pending) >= self.queue_size:
            raise problems.queue_full(self.queue_size)
        self._jobs[job.id] = job
        self._pending.append(job)
        self._publish(job, "queued", self.response(job).model_dump_json())
        self._wakeup.set()
        log.info("job_queued", job=job.id, waiting=len(self._pending), **sequence_fields(sequence))
        return job

    def get(self, job_id: str) -> Job:
        """Raises: ApiError: 404 for an unknown or expired job."""
        self._sweep()
        job = self._jobs.get(job_id)
        if job is None:
            raise problems.job_not_found(job_id)
        return job

    def cancel(self, job_id: str) -> Job:
        """Cancel a queued or running job.

        Raises:
            ApiError: 404 for an unknown job, 409 for one that has already finished.
        """
        job = self.get(job_id)
        if job.finished:
            raise problems.job_finished(job_id, job.status)
        if job.status == "queued":
            self._pending.remove(job)
        self._finish(job, "cancelled")
        log.info("job_cancelled", job=job.id, done=job.done, total=len(job.sequence))
        return job

    def response(self, job: Job) -> JobResponse:
        """The job as the API returns it; the landscape only once it has succeeded."""
        model = job.model
        return JobResponse(
            id=job.id,
            status=job.status,
            model=model.name,
            hf_id=model.hf_id,
            file=model.file,
            revision=model.revision,
            target_seq=job.sequence,
            length=len(job.sequence),
            aa_order=AA_ORDER,
            version=self.version,
            done=job.done,
            total=len(job.sequence),
            cached=job.cached,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            expires_at=job.expires_at,
            error=job.error,
            scores=to_grid(job.landscape) if job.status == "succeeded" else None,
        )

    async def events(self, job: Job, after: int | None = None) -> AsyncIterator[ServerSentEvent]:
        """The job's events after id ``after`` (all of them for None), then new ones as they
        happen, until the job has finished."""
        index = 0 if after is None else max(after + 1, 0)
        while True:
            while index < len(job.events):
                event = job.events[index]
                yield ServerSentEvent(raw_data=event.data, event=event.name, id=str(index))
                index += 1
            if job.finished:
                return
            await job.changed.wait()

    # -- worker -------------------------------------------------------------------------------

    async def run(self) -> None:
        """Run queued jobs one at a time, forever (cancel the task to stop)."""
        while True:
            while not self._pending:
                self._wakeup.clear()
                await self._wakeup.wait()
            job = self._pending.popleft()
            self._running = job
            with structlog.contextvars.bound_contextvars(request_id=job.request_id, job=job.id):
                await self._execute(job)
            self._running = None
            self._sweep()

    async def _execute(self, job: Job) -> None:
        job.status, job.started_at = "running", self.now()
        self._publish(job, "running", self.response(job).model_dump_json())
        log.info("job_started", **sequence_fields(job.sequence))
        scorer = job.model.scorer
        try:
            for positions in job.model.precise_batches(job.sequence):
                start = time.perf_counter()
                rows = await self.inference.run(scorer.landscape, job.sequence, positions=positions)
                self.metrics.observe_inference(job.model.key, "masked", time.perf_counter() - start)
                if job.finished:  # cancelled while the batch ran: drop it
                    return
                job.landscape[positions] = rows[positions]
                job.done += len(positions)
                self._publish(job, "progress", self._progress(job, positions))
        except Exception as exc:
            log.error("job_failed", error=repr(exc))
            sentry_sdk.capture_exception(exc)
            if job.finished:  # cancelled meanwhile: it stays cancelled
                return
            job.error = "Scoring failed; the error was logged."
            self._finish(job, "failed")
            return
        self.cache.put(job.model.key_for("masked", job.sequence), job.landscape)
        self._finish(job, "succeeded")
        seconds = (job.finished_at or job.started_at) - job.started_at
        log.info("job_succeeded", seconds=round(seconds.total_seconds(), 2))

    # -- internals ----------------------------------------------------------------------------

    def _progress(self, job: Job, positions: list[int]) -> str:
        """The ``progress`` payload: the rows this batch filled (every one scored, no NaN)."""
        values = np.round(job.landscape[positions].astype(np.float64), DECIMALS)
        rows = [
            LandscapeRow(position=p, scores=row.tolist())
            for p, row in zip(positions, values, strict=True)
        ]
        progress = JobProgress(id=job.id, done=job.done, total=len(job.sequence), rows=rows)
        return progress.model_dump_json()

    def _finish(self, job: Job, status: JobStatus) -> None:
        now = self.now()
        job.status, job.finished_at, job.expires_at = status, now, now + self.ttl
        self._publish(job, status, self.response(job).model_dump_json())

    def _publish(self, job: Job, name: str, data: str) -> None:
        job.events.append(JobEvent(name=name, data=data))
        changed, job.changed = job.changed, asyncio.Event()
        changed.set()

    def _sweep(self) -> None:
        now = self.now()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.expires_at is not None and job.expires_at <= now
        ]
        for job_id in expired:
            del self._jobs[job_id]
        if expired:
            log.info("jobs_expired", count=len(expired))

    def __len__(self) -> int:
        return len(self._jobs)


def parse_last_event_id(value: str | None) -> int | None:
    """``Last-Event-ID`` as an event index; anything else replays from the start."""
    if value is None or not value.strip().isdigit():
        return None
    return int(value.strip())

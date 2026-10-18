"""FastAPI application: the bundle and the ONNX scorers are loaded once, in ``lifespan``.

Startup reads the website bundle at the pinned revision (ADR 0028) and refuses to start if its
checksums fail or its schema version is not one this code reads; then it loads the fp32 ONNX
scorers (ADR 0029), opens the live-score cache and starts the precise-mode job worker
(ADR 0031). Every forward pass, fast or precise, runs in one bounded pool (``inference``), so
reads never wait behind scoring. Nothing in the API imports torch or transformers.

Hardening (ADR 0032): a host-wide lock keeps the server to one process, every request gets an
id and a client address, bodies are size-limited, routes are rate-limited, CORS is restricted
to the configured origins, ``/metrics`` needs a token, and Sentry runs only with a DSN.
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from api.app import problems
from api.app.bundle import open_bundle
from api.app.caching import ETagMiddleware
from api.app.inference import Inference
from api.app.jobs import JobManager
from api.app.live import live_models
from api.app.live_routes import CACHE_HEADER, live
from api.app.logs import configure_api_logging
from api.app.metrics import Metrics, metrics_router
from api.app.middleware import BodyLimitMiddleware, RequestContextMiddleware
from api.app.process import ProcessLock
from api.app.ratelimit import RateLimits, StreamSlots
from api.app.routes import AppState, api_version, router
from api.app.schemas import JobProgress
from api.app.score_cache import ScoreCache
from api.app.scorers import load_scorers
from api.app.sentry import init_sentry
from api.app.settings import Settings
from mutascope import __version__
from mutascope.utils.logging import get_logger

CACHED_PREFIXES = ("/v1/proteins", "/v1/benchmarks")
"""Read-only routes that get an ETag and Cache-Control."""
CORS_HEADERS = ["Content-Type", "If-None-Match", "Last-Event-ID", problems.REQUEST_ID_HEADER]
EXPOSED_HEADERS = ["ETag", "Location", "Retry-After", problems.REQUEST_ID_HEADER, CACHE_HEADER]


def load_state(settings: Settings, metrics: Metrics) -> AppState:
    """Open the pinned bundle and build the live scorers (blocking; startup only)."""
    bundle = open_bundle(
        repo=settings.bundle_repo,
        path_in_repo=settings.bundle_path,
        revision=settings.bundle_revision,
        local_dir=settings.bundle_dir,
        cache_size=settings.protein_cache_size,
    )
    scorers = load_scorers(
        settings.config_dir,
        settings.onnx_models,
        revision=settings.onnx_revision,
        threads=settings.onnx_threads,
        local_dir=settings.onnx_local_dir,
    )
    cache = ScoreCache(settings.score_cache_dir, size_limit=settings.score_cache_bytes)
    inference = Inference(
        concurrency=settings.inference_concurrency, wait_seconds=settings.inference_wait_seconds
    )
    jobs = JobManager(
        cache=cache,
        inference=inference,
        version=api_version(bundle),
        queue_size=settings.job_queue_size,
        ttl_seconds=settings.job_ttl_seconds,
        metrics=metrics,
        max_active_per_client=settings.max_active_jobs_per_client,
    )
    return AppState(
        bundle=bundle,
        scorers=scorers,
        live=live_models(
            scorers, revision=settings.onnx_revision, local_dir=settings.onnx_local_dir
        ),
        score_cache=cache,
        inference=inference,
        jobs=jobs,
        settings=settings,
        metrics=metrics,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Load the bundle and models once; any failure stops the server from starting."""
    configure_api_logging()
    log = get_logger("api")
    settings: Settings = app.state.settings
    metrics: Metrics = app.state.metrics
    log.info("startup", version=__version__, sentry=app.state.sentry)
    lock = ProcessLock(settings.lock_file)
    lock.acquire()
    try:
        state = load_state(settings, metrics)
        slots: StreamSlots = app.state.stream_slots
        metrics.watch(
            queue_length=lambda: state.jobs.waiting,
            running=lambda: state.jobs.running,
            streams=lambda: slots.count,
            inference_running=lambda: state.inference.running,
            inference_waiting=lambda: state.inference.waiting,
            score_cache_bytes=state.score_cache.volume,
        )
        app.state.mutascope = state
        worker = asyncio.create_task(state.jobs.run(), name="precise-jobs")
        log.info("ready", proteins=len(state.bundle.entries))
        yield
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        state.inference.close()
        state.score_cache.close()
        app.state.mutascope = None
    finally:
        lock.release()


def _openapi(app: FastAPI) -> dict[str, Any]:
    """The generated schema plus components no route returns as JSON: the problem details of
    the error responses, and ``JobProgress``, the data of an SSE ``progress`` event."""
    if app.openapi_schema is None:
        schema = FastAPI.openapi(app)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for model in (problems.Problem, JobProgress):
            extra = model.model_json_schema(ref_template="#/components/schemas/{model}")
            components.update(extra.pop("$defs", {}))
            components[model.__name__] = extra
        app.openapi_schema = schema
    return app.openapi_schema


def create_app(settings: Settings | None = None) -> FastAPI:
    """The API with ``settings`` (default: from the environment)."""
    app = FastAPI(
        title="MutaScope API",
        version=__version__,
        description=(
            "Functional-effect landscapes of single amino-acid substitutions in ProteinGym "
            "DMS assays. Zero-shot and supervised predictions are labelled as such; neither "
            "is a clinical prediction."
        ),
        lifespan=lifespan,
    )
    settings = settings if settings is not None else Settings()
    app.state.settings = settings
    app.state.mutascope = None
    app.state.sentry = init_sentry(settings)
    app.state.metrics = metrics = Metrics()
    app.state.rate_limits = RateLimits(settings)
    app.state.stream_slots = StreamSlots(
        per_client=settings.max_streams_per_client, total=settings.max_streams_total
    )
    problems.install(app)
    app.include_router(router)
    app.include_router(live)
    app.include_router(metrics_router)
    # Starlette runs the last-added middleware first. From the inside out: the ETag is computed
    # on the uncompressed body, then gzip; the body limit runs before any route reads a body;
    # the metrics see every response, 413s included; CORS headers go on every response
    # (problems too) and preflights are answered there; the request context is outermost, so
    # every log line, the access line and the X-Request-ID header have the id.
    app.add_middleware(
        ETagMiddleware, prefixes=CACHED_PREFIXES, cache_control=settings.cache_control
    )
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    metrics.instrument(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=CORS_HEADERS,
        expose_headers=EXPOSED_HEADERS,
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware, trusted_proxies=settings.trusted_proxies)
    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    return app


app = create_app()

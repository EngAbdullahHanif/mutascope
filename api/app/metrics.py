"""Prometheus metrics and the token-protected ``GET /metrics`` (left out of the OpenAPI schema).

Request latency and counts come from prometheus-fastapi-instrumentator (by route template,
method and status; an event stream's time is to its first byte, not its whole life). The API
adds its own:

- ``mutascope_inference_seconds{model, method}``: one forward call: a fast landscape
  (``wt``), or one precise-mode batch of up to 16 masked residues (``masked``).
- ``mutascope_score_cache_requests_total{method, result}``: lookups of the live-score cache,
  ``result`` = ``hit`` | ``miss``; the hit rate is ``hit / (hit + miss)``.
- ``mutascope_job_queue_length`` (waiting jobs), ``mutascope_jobs_running`` and
  ``mutascope_sse_streams_open``, read when scraped.

Each app has its own registry, so the tests can build many apps in one process.
"""

import hmac
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.app import problems
from api.app.ratelimit import rate_limit
from api.app.settings import Settings

Method = Literal["wt", "masked"]
LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0)
"""Request latency (s) by route; 2 s is ADR 0029's fast-mode p95 rule."""
INFERENCE_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
UNINSTRUMENTED = ["/metrics", "/health", "/ready"]
"""Probes and scrapes: frequent, cheap and not user traffic."""


class Metrics:
    """The app's registry and the API's own collectors."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.inference = Histogram(
            "mutascope_inference_seconds",
            "Time of one forward call of a live model.",
            ["model", "method"],
            buckets=INFERENCE_BUCKETS,
            registry=self.registry,
        )
        self.cache = Counter(
            "mutascope_score_cache_requests",
            "Lookups of the live-score cache.",
            ["method", "result"],
            registry=self.registry,
        )
        self.queue_length = Gauge(
            "mutascope_job_queue_length", "Precise-mode jobs waiting.", registry=self.registry
        )
        self.jobs_running = Gauge(
            "mutascope_jobs_running", "Precise-mode jobs running (0 or 1).", registry=self.registry
        )
        self.streams_open = Gauge(
            "mutascope_sse_streams_open", "Open job event streams.", registry=self.registry
        )

    def cache_lookup(self, method: Method, hit: bool) -> None:
        self.cache.labels(method=method, result="hit" if hit else "miss").inc()

    def observe_inference(self, model: str, method: Method, seconds: float) -> None:
        self.inference.labels(model=model, method=method).observe(seconds)

    def watch(
        self,
        *,
        queue_length: Callable[[], float],
        running: Callable[[], float],
        streams: Callable[[], float],
    ) -> None:
        """Read the gauges from these callables when scraped (set once the state exists)."""
        self.queue_length.set_function(queue_length)
        self.jobs_running.set_function(running)
        self.streams_open.set_function(streams)

    def instrument(self, app: FastAPI) -> None:
        """Add the request-latency middleware to ``app`` (before it starts)."""
        Instrumentator(
            registry=self.registry,
            excluded_handlers=UNINSTRUMENTED,
            should_exclude_streaming_duration=True,
        ).instrument(app, latency_lowr_buckets=LATENCY_BUCKETS)


def token_matches(authorization: str | None, token: str) -> bool:
    """Whether ``Authorization: Bearer <token>`` carries ``token``, compared in constant time."""
    supplied = ""
    if authorization is not None and authorization[:7].lower() == "bearer ":
        supplied = authorization[7:].strip()
    return hmac.compare_digest(supplied.encode(), token.encode())


metrics_router = APIRouter()


@metrics_router.get("/metrics", include_in_schema=False, dependencies=[Depends(rate_limit("read"))])
async def metrics(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Response:
    """Prometheus text format; 404 without a configured token, 401 without the right one.

    Under the read limit, so guessing tokens costs the guesser its quota (a scrape every 15 s
    uses 4 of 120 a minute).
    """
    settings: Settings = request.app.state.settings
    if settings.metrics_token is None:
        raise StarletteHTTPException(404, "Not Found")
    if not token_matches(authorization, settings.metrics_token.get_secret_value()):
        raise problems.unauthorized()
    registry: CollectorRegistry = request.app.state.metrics.registry
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

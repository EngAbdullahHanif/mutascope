"""Per-client rate limits (slowapi) and the open-stream limit, as FastAPI dependencies.

Each app builds its own slowapi :class:`~slowapi.Limiter` from its settings, with in-memory
storage and the moving-window strategy: at most N requests in any window, with no burst at a
window boundary. slowapi's route decorators are not used, because they register limit strings
on one module-level limiter when the routes are imported, and our limits come from the settings
of each app (the tests build many). The routes depend on :func:`rate_limit` instead, which
checks the app's limiter for the client the request-context middleware identified.

Limits are counted in this process's memory, so the server runs as one process (ADR 0032).
"""

import math
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from fastapi import Request
from limits import RateLimitItem, parse_many
from slowapi import Limiter

from api.app import problems
from api.app.clients import UNKNOWN
from api.app.settings import Settings

LimitGroup = Literal["read", "score", "score_global", "jobs"]
GLOBAL_KEY = "*"
"""The one key of a limit over all clients."""
DESCRIPTIONS: dict[LimitGroup, str] = {
    "read": "read requests",
    "score": "POST /v1/score",
    "score_global": "POST /v1/score over all clients",
    "jobs": "POST /v1/jobs",
}


def client_of(request: Request) -> str:
    """The client address :class:`~api.app.middleware.RequestContextMiddleware` resolved."""
    return str(getattr(request.state, "client", UNKNOWN))


class RateLimits:
    """The app's limiter and its limits by group."""

    def __init__(self, settings: Settings) -> None:
        self.limiter = Limiter(
            key_func=lambda: GLOBAL_KEY,  # unused: keys are passed to hit() explicitly
            storage_uri="memory://",
            strategy="moving-window",
        )
        self.limits: dict[LimitGroup, list[RateLimitItem]] = {
            "read": parse_many(settings.rate_limit_read),
            "score": parse_many(settings.rate_limit_score),
            "score_global": parse_many(settings.rate_limit_score_global),
            "jobs": parse_many(settings.rate_limit_jobs),
        }
        self.texts: dict[LimitGroup, str] = {
            "read": settings.rate_limit_read,
            "score": settings.rate_limit_score,
            "score_global": settings.rate_limit_score_global,
            "jobs": settings.rate_limit_jobs,
        }

    def hit(self, group: LimitGroup, key: str) -> None:
        """Count one request of ``key`` against every limit of ``group``.

        Raises:
            ApiError: 429 with ``Retry-After`` (seconds until the window has room again).
        """
        strategy = self.limiter.limiter
        for item in self.limits[group]:
            if not strategy.hit(item, group, key):
                reset_at, _remaining = strategy.get_window_stats(item, group, key)
                retry_after = max(1, math.ceil(reset_at - time.time()))
                raise problems.rate_limited(DESCRIPTIONS[group], self.texts[group], retry_after)

    def reset(self) -> None:
        self.limiter.reset()


def rate_limit(*groups: LimitGroup) -> Callable[[Request], Awaitable[None]]:
    """A dependency counting the request against ``groups`` in order (``score_global`` is
    keyed by :data:`GLOBAL_KEY`, every other group by the client).

    ``async`` so it runs on the event loop: the limiter's memory storage is then only ever
    touched from one thread.
    """

    async def check(request: Request) -> None:
        limits: RateLimits = request.app.state.rate_limits
        client = client_of(request)
        for group in groups:
            limits.hit(group, GLOBAL_KEY if group == "score_global" else client)

    return check


@dataclass
class StreamSlots:
    """Open SSE streams, per client and in total."""

    per_client: int
    total: int
    open: Counter[str] = field(default_factory=Counter[str])

    def acquire(self, client: str) -> None:
        """Raises: ApiError: 429 when the client or the server is at its limit."""
        if self.open[client] >= self.per_client:
            raise problems.too_many_streams(self.per_client, "from your address")
        if self.open.total() >= self.total:
            raise problems.too_many_streams(self.total, "on the server")
        self.open[client] += 1

    def release(self, client: str) -> None:
        self.open[client] -= 1
        if self.open[client] <= 0:
            del self.open[client]

    @property
    def count(self) -> int:
        return self.open.total()


async def stream_slot(request: Request) -> AsyncIterator[None]:
    """A dependency holding one stream slot until the response has been sent.

    A ``yield`` dependency's exit runs after the response (FastAPI's default ``request``
    scope), which for an event stream is when the stream ends or the client disconnects.
    """
    slots: StreamSlots = request.app.state.stream_slots
    client = client_of(request)
    slots.acquire(client)
    try:
        yield
    finally:
        slots.release(client)

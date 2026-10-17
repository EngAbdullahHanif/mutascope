"""Pure ASGI middleware: the request context (id, client, access log) and the body-size limit.

Both are plain ASGI callables rather than ``BaseHTTPMiddleware``: they never buffer a response,
so Server-Sent Events stream through them unchanged, and the log context they bind is visible
to the route in the same task (and in its worker threads, which copy the context).
"""

import re
import time
import uuid
from collections.abc import Sequence

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.app import problems
from api.app.clients import IPNetwork, client_address
from mutascope.utils.logging import get_logger

log = get_logger("api.access")

REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
"""An incoming ``X-Request-ID`` is kept only if it matches: 8-64 letters, digits, ``.``, ``_``,
``-`` (a UUID, a hex digest, a ULID). Anything else, including a value that could break a log
line or a header, is replaced by a fresh id."""


def request_id_from(value: str | None) -> str:
    """``value`` if it is a well-formed request id, else a new one (32 hex digits)."""
    if value is not None and REQUEST_ID.fullmatch(value):
        return value
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Give every request an id and a client address, log one line when it ends.

    The id and the client are stored in the request state (``request.state.request_id``,
    ``request.state.client``) for the routes and the rate limiter, the id is bound into the log
    context of everything the request logs, and it is returned as ``X-Request-ID``.
    """

    def __init__(self, app: ASGIApp, *, trusted_proxies: Sequence[IPNetwork]) -> None:
        self.app = app
        self.trusted_proxies = tuple(trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_id = request_id_from(headers.get("x-request-id"))
        peer = scope.get("client")
        client = client_address(
            peer[0] if peer else None, headers.getlist("x-forwarded-for"), self.trusted_proxies
        )
        state = scope.setdefault("state", {})
        state["request_id"], state["client"] = request_id, client
        status = 500  # unless a response starts: an exception escaped to the server

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[problems.REQUEST_ID_HEADER] = request_id
            await send(message)

        start = time.perf_counter()
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                log.info(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status,
                    ms=round((time.perf_counter() - start) * 1000, 1),
                    client=client,
                )


class BodyLimitMiddleware:
    """413 for a body over ``max_bytes``, in the problem format, before the route reads it.

    A declared ``Content-Length`` over the limit is refused at once. A body without one
    (chunked) is counted as it arrives, and reading past the limit raises
    :class:`problems.BodyTooLarge`, which the problem handlers turn into the same 413.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            response = problems.body_too_large(Request(scope), self.max_bytes)
            await response(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise problems.BodyTooLarge(self.max_bytes)
            return message

        await self.app(scope, counted, send)

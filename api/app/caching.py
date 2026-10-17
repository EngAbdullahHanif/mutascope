"""ETag and Cache-Control for the read-only routes (pure ASGI middleware).

A successful ``GET`` under one of the configured path prefixes is buffered, and its ETag is the
sha256 of the body, so it changes exactly when the content does (a new bundle revision, or a
code change to a response). ``If-None-Match`` with that tag gets ``304 Not Modified`` without a
body. The tag is weak (``W/``): gzip, applied outside this middleware, changes the bytes on the
wire but not the content.
"""

import hashlib
from collections.abc import Sequence

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def etag_for(body: bytes) -> str:
    return f'W/"{hashlib.sha256(body).hexdigest()[:32]}"'


def matches(if_none_match: str, etag: str) -> bool:
    """RFC 9110 weak comparison of ``If-None-Match`` against ``etag``."""
    if if_none_match.strip() == "*":
        return True
    bare = etag.removeprefix("W/")
    return any(tag.strip().removeprefix("W/") == bare for tag in if_none_match.split(","))


class ETagMiddleware:
    """Add ``ETag`` and ``Cache-Control`` to successful GETs under ``prefixes``."""

    def __init__(self, app: ASGIApp, *, prefixes: Sequence[str], cache_control: str) -> None:
        self.app = app
        self.prefixes = tuple(prefixes)
        self.cache_control = cache_control

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "GET"
            or not str(scope["path"]).startswith(self.prefixes)
        ):
            await self.app(scope, receive, send)
            return
        if_none_match = Headers(scope=scope).get("if-none-match")
        start: Message | None = None
        chunks: list[bytes] = []

        async def buffer(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body" or start is None:
                await send(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            await self._finish(start, b"".join(chunks), if_none_match, send)

        await self.app(scope, receive, buffer)

    async def _finish(
        self, start: Message, body: bytes, if_none_match: str | None, send: Send
    ) -> None:
        if start["status"] != 200:
            await send(start)
            await send({"type": "http.response.body", "body": body})
            return
        etag = etag_for(body)
        headers = MutableHeaders(raw=list(start["headers"]))
        headers["etag"] = etag
        headers["cache-control"] = self.cache_control
        if if_none_match is not None and matches(if_none_match, etag):
            for name in ("content-length", "content-type"):
                del headers[name]
            await send({**start, "status": 304, "headers": headers.raw})
            await send({"type": "http.response.body", "body": b""})
            return
        await send({**start, "headers": headers.raw})
        await send({"type": "http.response.body", "body": body})

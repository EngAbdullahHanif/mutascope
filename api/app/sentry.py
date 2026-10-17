"""Optional error reporting to Sentry, on only when ``MUTASCOPE_API_SENTRY_DSN`` is set.

An event must never carry a submitted sequence. Sentry is therefore told to collect no request
body (``http_bodies`` empty, ``max_request_body_size="never"``), no local variables of stack
frames (a frame of the scoring code holds the sequence), no cookies, no query strings and no
user identity (``data_collection``), and every event passes through
:func:`scrub_event` before it is sent: the request's body, cookies and identifying headers are
dropped, and every string left is redacted like a log line (:func:`api.app.logs.redact`).
"""

from typing import Any

import sentry_sdk

from api.app.logs import redact
from api.app.settings import Settings
from mutascope import __version__

DROPPED_HEADERS = frozenset({"authorization", "cookie", "x-forwarded-for", "x-real-ip"})


def scrub_event(event: Any, _hint: Any = None) -> Any:
    """``event`` without request bodies, cookies or identifying headers, sequences redacted."""
    request = event.get("request")
    if isinstance(request, dict):
        fields: dict[str, Any] = request  # pyright: ignore[reportUnknownVariableType]
        for key in ("data", "cookies", "env"):
            fields.pop(key, None)
        headers = fields.get("headers")
        if isinstance(headers, dict):
            items: dict[str, Any] = headers  # pyright: ignore[reportUnknownVariableType]
            fields["headers"] = {k: v for k, v in items.items() if k.lower() not in DROPPED_HEADERS}
    return redact(event)


def init_sentry(settings: Settings) -> bool:
    """Start the Sentry client if a DSN is configured; whether it was started."""
    if settings.sentry_dsn is None:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.sentry_environment,
        release=f"mutascope@{__version__}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        data_collection={
            "user_info": False,
            "cookies": {"mode": "off"},
            "http_bodies": [],
            "url_query_params": {"mode": "off"},
            "stack_frame_variables": False,
        },
        max_request_body_size="never",
        include_local_variables=False,
        before_send=scrub_event,
        before_send_transaction=scrub_event,
    )
    return True

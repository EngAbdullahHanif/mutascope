"""API logging: one JSON object per line, with the request id, and never a sequence.

A submitted sequence (or a list of mutations of it) can be unpublished research data, so the
API logs only its sha256 and its length (:func:`sequence_fields`). As a safety net for messages
the API does not write itself (an exception's text, a library's log line), every string in a log
event is scanned for runs of amino-acid letters long enough to be a sequence fragment, and for
mutation-shaped tokens (``A123G``), and those are replaced (:func:`redact`). The same function
scrubs Sentry events (:mod:`api.app.sentry`).

structlog renders the API's own events; the standard library's loggers (uvicorn's) are routed
through the same processors, so they are JSON too and carry the request id.
"""

import hashlib
import logging
import re
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from mutascope.utils.logging import CurrentStderr, configure_logging

SEQUENCE_RUN = re.compile(r"[ACDEFGHIKLMNPQRSTVWY]{10,}")
"""Ten or more uppercase amino-acid letters in a row: no event name, id or hash has that (they
are lowercase, or mixed with digits and underscores), but any sequence fragment worth hiding
does."""
MUTATION = re.compile(r"\b[ACDEFGHIKLMNPQRSTVWY][1-9][0-9]*[ACDEFGHIKLMNPQRSTVWY]\b")
"""A substitution like ``A123G``. The API never logs a mutation list; this catches one quoted
in an exception's text."""
REDACTED = "[sequence redacted]"
REDACTED_MUTATION = "[mutation redacted]"
_HANDLER_NAME = "mutascope-api-json"


def sequence_fields(sequence: str) -> dict[str, Any]:
    """What may be logged about a submitted sequence: its sha256 and its length."""
    return {
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "length": len(sequence),
    }


def redact(value: Any) -> Any:
    """``value`` with sequence-like runs and mutations replaced, in strings nested in dicts,
    lists and tuples."""
    if isinstance(value, str):
        return MUTATION.sub(REDACTED_MUTATION, SEQUENCE_RUN.sub(REDACTED, value))
    if isinstance(value, dict):
        items: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
        return {k: redact(v) for k, v in items.items()}
    if isinstance(value, list | tuple):
        seq: list[Any] | tuple[Any, ...] = value  # pyright: ignore[reportUnknownVariableType]
        return type(seq)(redact(v) for v in seq)
    return value


def redact_processor(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor form of :func:`redact`."""
    return {key: redact(value) for key, value in event_dict.items()}


def configure_api_logging(level: int = logging.INFO) -> None:
    """structlog JSON with redaction, and the standard library's loggers through it.

    Idempotent: the standard-library handler is replaced, not added again, so a test that
    starts the app many times gets one line per record.
    """
    configure_logging(json=True, level=level, extra=[redact_processor])
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ExtraAdder(),
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            redact_processor,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(CurrentStderr())  # type: ignore[arg-type]
    handler.name = _HANDLER_NAME
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if h.name != _HANDLER_NAME] + [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        stdlib = logging.getLogger(name)
        stdlib.handlers, stdlib.propagate = [], True

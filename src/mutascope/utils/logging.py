"""structlog configuration shared by the CLI and the API."""

import logging
import sys

import structlog
from structlog.typing import Processor


def configure_logging(*, json: bool = False, level: int = logging.INFO) -> None:
    """Configure structlog once per process.

    Args:
        json: Emit one JSON object per line (for pipelines and the API) instead of
            human-readable console output.
        level: Minimum log level.
    """
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # Logs go to stderr so stdout stays clean for command output.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally bound to a component name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger

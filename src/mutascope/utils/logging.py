"""structlog configuration shared by the CLI and the API."""

import logging
import sys

import structlog
from structlog.typing import Processor


class _CurrentStderr:
    """Writes to whatever ``sys.stderr`` is at write time, not at configure time.

    The logger factory is built when logging is configured, so binding the stream object itself
    would keep writing to a stream that was later swapped out and closed (e.g. by test runners
    capturing CLI output).
    """

    def write(self, message: str) -> int:
        return sys.stderr.write(message)

    def flush(self) -> None:
        sys.stderr.flush()


def configure_logging(*, json: bool = False, level: int = logging.INFO) -> None:
    """Configure structlog; a later call replaces the earlier configuration for every logger.

    Loggers are not cached on first use. A cached logger keeps the processors of the
    configuration it was first used under, and the modules create their loggers at import
    time: a second call (every CLI invocation makes one) would then leave those loggers on the
    old format and level, and ``structlog.testing.capture_logs`` would not see them. Building
    the logger on each call costs microseconds, and we log a few lines per assay.

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
        logger_factory=structlog.PrintLoggerFactory(file=_CurrentStderr()),  # type: ignore[arg-type]
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally bound to a component name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger

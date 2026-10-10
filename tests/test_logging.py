"""structlog setup."""

import io

import pytest

from mutascope.utils.logging import configure_logging, get_logger


def test_cached_logger_follows_replaced_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: loggers cached on first use kept writing to the stderr captured at configure
    time, which fails with "I/O operation on closed file" once that stream is swapped and closed
    (as CliRunner does between invocations)."""
    first, second = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stderr", first)
    configure_logging(json=True)
    log = get_logger("test")
    log.info("one")
    first.close()

    monkeypatch.setattr("sys.stderr", second)
    log.info("two")
    assert '"event": "two"' in second.getvalue()

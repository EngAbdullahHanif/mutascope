"""structlog setup."""

import io
import json

import pytest
from structlog.testing import capture_logs

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


def test_a_logger_used_before_a_second_configure_follows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: with loggers cached on first use, a module-level logger kept the processors
    of the configuration it was first used under. Every CLI invocation configures logging, so
    from the second invocation in a process on, such a logger ignored ``--log-json``."""
    stream = io.StringIO()
    monkeypatch.setattr("sys.stderr", stream)
    log = get_logger("module_level")
    configure_logging(json=False)
    log.info("console")
    configure_logging(json=True)
    log.info("as json")
    last = stream.getvalue().splitlines()[-1]
    assert json.loads(last)["event"] == "as json"


def test_capture_logs_sees_a_logger_used_under_an_earlier_configuration() -> None:
    """Regression for the order-dependent ``test_undefined_metrics_are_nan_and_logged``: after
    two CLI tests in one process, ``capture_logs`` captured nothing from ``eval.benchmark``.
    It edits the current processor list, and the cached logger held the previous one."""
    log = get_logger("module_level")
    configure_logging()
    log.warning("first use")
    configure_logging()
    with capture_logs() as logs:
        log.warning("captured")
    assert [entry["event"] for entry in logs] == ["captured"]

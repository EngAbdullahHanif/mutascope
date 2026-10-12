"""Local MLflow tracking: what a run records, where the store lives, and when it is skipped."""

import math
import subprocess
from pathlib import Path

import pytest
import yaml
from mlflow.tracking import MlflowClient

from mutascope.utils.tracking import (
    ARTIFACT_DIR,
    DB_FILE,
    UNKNOWN_VERSION,
    TrackedRun,
    TrackingSettings,
    code_version,
    log_run,
)


def _run(artifact: Path) -> TrackedRun:
    return TrackedRun(
        name="benchmark toy",
        params={"model.name": "toy", "model.revision": "a" * 40, "method": "masked"},
        metrics={"spearman": 0.41, "spearman/Binding": 0.3, "auc": math.nan},
        tags={"step": "benchmark"},
        config={"model": {"name": "toy"}, "eval": {"top_percent": 10}},
        artifacts=[artifact],
    )


def test_a_run_records_params_metrics_tags_config_and_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Everything lands under ``tracking.dir``, and nothing in the directory the command is
    started from (MLflow's default artifact root is ``./mlruns`` of the working directory)."""
    store, elsewhere = tmp_path / "store", tmp_path / "elsewhere"
    elsewhere.mkdir()
    artifact = tmp_path / "results.parquet"
    artifact.write_bytes(b"stub")
    monkeypatch.chdir(elsewhere)
    settings = TrackingSettings.from_config(
        {"enabled": True, "dir": str(store), "experiment": "zs"}
    )

    run_id = log_run(settings, _run(artifact))
    second = log_run(settings, _run(artifact))

    assert (store / DB_FILE).is_file() and not list(elsewhere.iterdir())
    assert run_id is not None and second is not None and run_id != second

    client = MlflowClient(tracking_uri=settings.uri)
    experiment = client.get_experiment_by_name("zs")
    assert experiment is not None
    assert len(client.search_runs([experiment.experiment_id])) == 2
    run = client.get_run(run_id)
    assert run.info.status == "FINISHED" and run.info.run_name == "benchmark toy"
    assert run.data.params == {"model.name": "toy", "model.revision": "a" * 40, "method": "masked"}
    # NaN (an undefined aggregate) is left out, not logged as a number.
    assert run.data.metrics == {"spearman": 0.41, "spearman/Binding": 0.3}
    assert run.data.tags["step"] == "benchmark" and run.data.tags["code_version"]

    files = store / ARTIFACT_DIR / run_id / "artifacts"
    assert (files / "results.parquet").read_bytes() == b"stub"
    assert yaml.safe_load((files / "config.yaml").read_text()) == {
        "model": {"name": "toy"},
        "eval": {"top_percent": 10},
    }


def test_disabled_tracking_writes_nothing(tmp_path: Path) -> None:
    settings = TrackingSettings(enabled=False, directory=tmp_path / "store", experiment="zs")
    assert log_run(settings, _run(tmp_path / "missing.parquet")) is None
    assert not (tmp_path / "store").exists()


def test_a_failed_run_is_marked_failed(tmp_path: Path) -> None:
    """A missing artifact fails the command; the run stays in the store, marked FAILED."""
    settings = TrackingSettings(enabled=True, directory=tmp_path / "store", experiment="zs")
    with pytest.raises(Exception, match=r"missing\.parquet"):
        log_run(settings, _run(tmp_path / "missing.parquet"))
    client = MlflowClient(tracking_uri=settings.uri)
    experiment = client.get_experiment_by_name("zs")
    assert experiment is not None
    assert [r.info.status for r in client.search_runs([experiment.experiment_id])] == ["FAILED"]


def test_code_version_is_the_commit_and_flags_changed_code_only(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        identity = ["-c", "user.name=t", "-c", "user.email=t@example.org"]
        subprocess.run(["git", "-C", str(tmp_path), *identity, *args], check=True)

    assert code_version(tmp_path) == UNKNOWN_VERSION  # not a git checkout
    git("init", "-q")
    for name in ("src/a.py", "results/report.txt"):
        (tmp_path / name).parent.mkdir()
        (tmp_path / name).write_text("1\n")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    clean = code_version(tmp_path)
    assert len(clean) == 40 and set(clean) <= set("0123456789abcdef")
    # A run rewrites tracked reports and adds new files; neither is a code change.
    (tmp_path / "results" / "report.txt").write_text("2\n")
    (tmp_path / "src" / "untracked.py").write_text("new\n")
    assert code_version(tmp_path / "src") == clean
    (tmp_path / "src" / "a.py").write_text("2\n")
    assert code_version(tmp_path) == f"{clean}-dirty"

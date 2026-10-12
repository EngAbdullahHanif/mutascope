"""Local experiment tracking with MLflow (ADR 0014).

One tracked run per CLI invocation that scores or evaluates one of our models. A run records
which model produced the numbers (name, Hub id, pinned revision, scoring method), the code
version, the full resolved config, the metrics, and the files the command wrote.

The store is a SQLite file plus an artifact folder under ``tracking.dir``; nothing is sent
anywhere. MLflow 3 no longer accepts a plain ``./mlruns`` file store for new runs, so SQLite is
the local backend. ``mlflow`` is imported only when a run is logged, and it is an optional
extra (``tracking``): the serving image does not install it.
"""

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mutascope.utils.logging import get_logger

log = get_logger(__name__)

DB_FILE = "mlflow.db"
ARTIFACT_DIR = "artifacts"
CONFIG_ARTIFACT = "config.yaml"
UNKNOWN_VERSION = "unknown"
CODE_PATHS = ("src", "conf", "pipeline", "pyproject.toml", "uv.lock")
"""What decides a run's numbers: the package, the configs, the workflow and the locked deps."""


class TrackingError(RuntimeError):
    """Tracking is enabled but a run could not be logged."""


@dataclass(frozen=True)
class TrackingSettings:
    """``conf/tracking/*.yaml``."""

    enabled: bool
    directory: Path
    experiment: str

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "TrackingSettings":
        return cls(
            enabled=bool(cfg["enabled"]),
            directory=Path(str(cfg["dir"])),
            experiment=str(cfg["experiment"]),
        )

    @property
    def uri(self) -> str:
        """SQLAlchemy URI of the store; absolute, so it does not depend on the caller's cwd."""
        return f"sqlite:///{self.directory.resolve() / DB_FILE}"


@dataclass(frozen=True)
class TrackedRun:
    """What one command reports about itself."""

    name: str
    params: Mapping[str, object]
    metrics: Mapping[str, float]
    tags: Mapping[str, str] = field(default_factory=dict[str, str])
    config: Mapping[str, Any] = field(default_factory=dict[str, Any])
    """The full resolved config, stored as the ``config.yaml`` artifact."""
    artifacts: Sequence[Path] = ()


def code_version(repo: Path | None = None, paths: Sequence[str] = CODE_PATHS) -> str:
    """The git commit of the running code, with ``-dirty`` if ``paths`` differ from it.

    Only tracked files under ``paths`` count: a pipeline run rewrites tracked reports under
    ``results/`` while it runs, and that must not mark the code that produced them as modified.

    Returns:
        ``"unknown"`` when the code is not in a git checkout (an installed wheel, a Docker
        image) or git is not available.
    """
    where = str(repo if repo is not None else Path(__file__).parent)
    try:
        top = _git(where, "rev-parse", "--show-toplevel")
        commit = _git(top, "rev-parse", "HEAD")
        dirty = _git(top, "status", "--porcelain", "--untracked-files=no", "--", *paths)
    except (OSError, subprocess.CalledProcessError):
        return UNKNOWN_VERSION
    return f"{commit}-dirty" if dirty else commit


def _git(where: str, *args: str) -> str:
    done = subprocess.run(["git", "-C", where, *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def log_run(settings: TrackingSettings, run: TrackedRun) -> str | None:
    """Log ``run`` to the local MLflow store and return its run id (None when disabled).

    Metrics that are NaN (an undefined aggregate) are not logged: a missing metric is clearer
    in the MLflow UI than a NaN, and the results files keep the NaN.

    Raises:
        TrackingError: Tracking is enabled and ``mlflow`` is not installed.
    """
    if not settings.enabled:
        log.info("tracking_disabled", run=run.name)
        return None
    # MLflow prints a hint aimed at coding agents on import unless told not to.
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    try:
        from mlflow.entities import Metric, Param, RunStatus
        from mlflow.tracking import MlflowClient
    except ImportError as exc:
        raise TrackingError(
            "tracking.enabled is true but mlflow is not installed: "
            "`uv sync --extra tracking`, or pass tracking.enabled=false"
        ) from exc

    root = settings.directory.resolve()
    root.mkdir(parents=True, exist_ok=True)
    client = MlflowClient(tracking_uri=settings.uri)
    experiment = client.get_experiment_by_name(settings.experiment)
    experiment_id = (
        experiment.experiment_id
        if experiment is not None
        # Without an explicit location MLflow puts artifacts in ./mlruns of the caller's cwd.
        else client.create_experiment(
            settings.experiment, artifact_location=(root / ARTIFACT_DIR).as_uri()
        )
    )
    tags = {**run.tags, "code_version": code_version()}
    run_id = client.create_run(experiment_id, run_name=run.name, tags=tags).info.run_id
    now = int(time.time() * 1000)
    finite = {k: float(v) for k, v in run.metrics.items() if v == v}  # NaN != NaN
    try:
        client.log_batch(
            run_id,
            metrics=[Metric(key, value, now, 0) for key, value in finite.items()],
            params=[Param(key, str(value)) for key, value in run.params.items()],
        )
        client.log_dict(run_id, dict(run.config), CONFIG_ARTIFACT)
        for path in run.artifacts:
            # untyped in mlflow
            client.log_artifact(run_id, str(path))  # pyright: ignore[reportUnknownMemberType]
    except BaseException:
        client.set_terminated(run_id, status=RunStatus.to_string(RunStatus.FAILED))
        raise
    client.set_terminated(run_id)
    log.info("run_tracked", run=run.name, run_id=run_id, store=str(root), metrics=len(finite))
    return run_id

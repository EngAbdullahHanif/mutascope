"""The API image, run as the free Space runs it: 2 CPUs, 16 GB, UID 1000 (ADR 0033).

``make test-container`` (``pytest -m container``), after ``make api-image``. Needs Docker; the
image is ``MUTASCOPE_API_IMAGE`` (default ``mutascope-api:dev``). Each check talks to one
container over HTTP, as a client would: health (Docker's own ``HEALTHCHECK`` too), readiness at
the pinned revisions, one fast-mode score and one precise-mode job end to end, the user and
its write paths, the thread settings, no torch, and the one-process rule.

Latencies printed here are from a local container, not the Space.
"""

import json
import os
import secrets
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from tests.scoring.test_esm2 import UBIQUITIN

from api.app.settings import Settings
from api.tests.test_live import parse_sse
from mutascope.utils.logging import get_logger

pytestmark = pytest.mark.container
log = get_logger(__name__)

IMAGE = os.environ.get("MUTASCOPE_API_IMAGE", "mutascope-api:dev")
SPACE_LIMITS = ("--cpus=2", "--memory=16g")
"""The free Hugging Face Space: 2 vCPUs, 16 GB."""


def docker(
    *args: str, check: bool = True, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=timeout
    )


@dataclass(frozen=True)
class Container:
    name: str
    base: str
    token: str

    def exec(self, *command: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return docker("exec", self.name, *command, check=check)

    def inspect(self) -> dict[str, Any]:
        out: list[dict[str, Any]] = json.loads(docker("inspect", self.name).stdout)
        return out[0]


def _wait_healthy(container: Container, seconds: float = 240) -> None:
    """Until Docker's HEALTHCHECK reports ``healthy`` (every 5 s during its start period)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = container.inspect()["State"]
        if not state["Running"]:
            raise AssertionError(
                f"container exited: {docker('logs', container.name).stderr[-2000:]}"
            )
        if state.get("Health", {}).get("Status") == "healthy":
            return
        time.sleep(2)
    raise AssertionError(f"not healthy after {seconds} s: {container.inspect()['State']}")


@pytest.fixture(scope="module")
def container() -> Iterator[Container]:
    name = f"mutascope-api-test-{secrets.token_hex(4)}"
    token = secrets.token_hex(24)
    docker(
        "run", "--detach", "--name", name, *SPACE_LIMITS,
        "--publish", "127.0.0.1::7860",
        "--env", f"MUTASCOPE_API_METRICS_TOKEN={token}",
        IMAGE,
    )  # fmt: skip
    try:
        port = docker("port", name, "7860/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        started = Container(name=name, base=f"http://127.0.0.1:{port}", token=token)
        _wait_healthy(started)
        yield started
    finally:
        docker("rm", "--force", name, check=False)


def test_runs_under_the_space_limits_as_uid_1000(container: Container) -> None:
    host = container.inspect()["HostConfig"]
    assert host["NanoCpus"] == 2 * 10**9
    assert host["Memory"] == 16 * 2**30
    assert container.exec("id", "-u").stdout.strip() == "1000"
    assert container.inspect()["State"]["Health"]["Status"] == "healthy"


def test_health_and_ready_at_the_pinned_revisions(container: Container) -> None:
    assert httpx.get(f"{container.base}/health").status_code == 200
    ready = httpx.get(f"{container.base}/ready")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    pins = Settings()
    assert body["bundle_revision"] == pins.bundle_revision
    assert body["proteins"] == 217
    assert set(body["models"]) == set(pins.onnx_models)
    assert (body["score_default_model"], body["precise_max_length"]) == ("esm2_150m", 600)


def test_fast_score_end_to_end(container: Container) -> None:
    started = time.perf_counter()
    reply = httpx.post(
        f"{container.base}/v1/score", json={"sequence": UBIQUITIN, "model": "esm2_150m"}, timeout=30
    )
    seconds = time.perf_counter() - started
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert (body["kind"], body["label"], body["method"]) == (
        "zero_shot",
        "Zero-shot",
        "wt_marginal",
    )
    assert body["revision"] == Settings().onnx_revision
    assert len(body["scores"]) == len(UBIQUITIN) * 20
    log.info(
        "container_fast_score", seconds=round(seconds, 3), note="local container, not the Space"
    )


def test_precise_job_end_to_end(container: Container) -> None:
    submitted = httpx.post(f"{container.base}/v1/jobs", json={"sequence": UBIQUITIN}, timeout=30)
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["id"]
    started = time.perf_counter()
    stream = httpx.get(f"{container.base}/v1/jobs/{job_id}/stream", timeout=300)
    seconds = time.perf_counter() - started
    events = parse_sse(stream.text)
    kinds = [kind for kind, _, _ in events]
    assert kinds[0] == "queued" and kinds[-1] == "succeeded", kinds
    assert kinds.count("progress") == -(-len(UBIQUITIN) // 16)  # one per forward batch of 16
    final = events[-1][2]
    assert (final["status"], final["label"], final["method"]) == (
        "succeeded",
        "Zero-shot",
        "masked_marginal",
    )
    assert len(final["scores"]) == len(UBIQUITIN) * 20
    log.info(
        "container_precise_job", seconds=round(seconds, 2), note="local container, not the Space"
    )


def test_write_paths_are_the_users_and_code_is_read_only(container: Container) -> None:
    home = "/home/user/.cache/mutascope"
    assert container.exec("test", "-f", f"{home}/api.lock").returncode == 0
    entries = container.exec("find", f"{home}/scores", "-type", "f").stdout.split()
    assert entries  # the fast and precise landscapes above were cached
    for path in ("/app/api", "/opt/venv", "/opt/mutascope/hf"):
        assert container.exec("touch", f"{path}/x", check=False).returncode != 0, path


def test_threads_offline_and_no_torch(container: Container) -> None:
    env = dict(
        line.split("=", 1) for line in container.exec("env").stdout.splitlines() if "=" in line
    )
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        assert env[name] == "1", name
    assert env["HF_HUB_OFFLINE"] == "1"
    for module in ("torch", "transformers"):
        assert container.exec("python", "-c", f"import {module}", check=False).returncode != 0


def test_metrics_need_the_token(container: Container) -> None:
    assert httpx.get(f"{container.base}/metrics").status_code == 401
    reply = httpx.get(
        f"{container.base}/metrics", headers={"Authorization": f"Bearer {container.token}"}
    )
    assert reply.status_code == 200
    assert "mutascope_inference_seconds" in reply.text


def test_no_sequence_in_the_container_logs(container: Container) -> None:
    # Its own request: tests run in random order (pytest-randomly), so another test's score
    # may not have happened yet.
    reply = httpx.post(
        f"{container.base}/v1/score", json={"sequence": UBIQUITIN, "model": "esm2_35m"}, timeout=30
    )
    assert reply.status_code == 200
    logs = docker("logs", container.name)
    output = logs.stdout + logs.stderr
    assert UBIQUITIN[:30] not in output
    assert "sequence_sha256" in output


def test_a_second_server_in_the_container_refuses_to_start(container: Container) -> None:
    second = docker(
        "exec", container.name, "python", "-m", "api.app.serve", check=False, timeout=180
    )
    assert second.returncode != 0
    assert "SecondProcessError" in second.stderr


def test_more_workers_are_refused() -> None:
    run = docker(
        "run", "--rm", *SPACE_LIMITS, "--env", "WEB_CONCURRENCY=2", IMAGE,
        check=False, timeout=180,
    )  # fmt: skip
    assert run.returncode != 0
    assert "WEB_CONCURRENCY=2" in run.stderr

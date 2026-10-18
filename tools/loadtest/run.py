"""Run the local load test: each scenario against a fresh API process (docs/load-test.md).

Usage (from the repository root)::

    uv run --project tools/loadtest python tools/loadtest/run.py --out <dir> [scenario ...]
    uv run --project tools/loadtest python tools/loadtest/run.py --image mutascope-api:dev \
        --out <dir> [scenario ...]

For each scenario this starts the API with its production start command
(``python -m api.app.serve``: one process, one worker, ONNX threads = 2, the real fp32 models
and bundle at their pinned revisions, an empty score cache), waits for ``/ready``, runs Locust
headless, samples the server's memory and ``/metrics`` every second, stops the server with
SIGTERM, and writes ``<dir>/summary.json`` with the status counts, Locust's latency
percentiles, memory and the gauges' maxima.

- Without ``--image``: a process of the main environment, ``trusted_proxies = 127.0.0.1``;
  memory is the resident set of the process tree (ADR 0032).
- With ``--image``: a container of that image under the free Space's limits
  (``--cpus=2 --memory=16g``; ADR 0033). The container sees every request come from the Docker
  bridge's gateway, which is therefore the trusted proxy, as the Space's proxy will be.
  Memory is the container's cgroup: ``usage`` as ``docker stats`` reports it
  (``memory.current`` minus inactive page cache) and ``anon`` (heap and other anonymous memory,
  which a leak would grow; page cache is reclaimable).

A fresh process or container per scenario means no scenario sees another's rate-limit counts or
cache. The default scenarios are Task 5's seven; ``soak5`` (5 users for 20 minutes) is run by
name.
"""

import argparse
import contextlib
import csv
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Protocol

import psutil

REPO = Path(__file__).resolve().parents[2]
LOCUSTFILE = Path(__file__).with_name("locustfile.py")
log = logging.getLogger("loadtest")

SCENARIOS: dict[str, dict[str, Any]] = {
    # One limit at a time; 50 s is inside one 60 s moving window, so each client's
    # successful count must equal its limit exactly.
    "read": {"users": "ReadHammer", "n": 1, "seconds": 50},
    "score": {"users": "ScoreHammer", "n": 1, "seconds": 50},
    "score_global": {"users": "ScoreHammer", "n": 10, "seconds": 50},
    "jobs": {"users": "JobHammer", "n": 1, "seconds": 50},
    "streams": {"users": "StreamHolder", "n": 6, "seconds": 45, "long_job": 400},
    # Mixed traffic.
    "load5": {"users": "Visitor", "n": 5, "seconds": 180},
    "load20": {"users": "Visitor", "n": 20, "seconds": 600},
    # The 20-user run of the live measurement (at most 2 minutes on Cloud Run's free tier).
    "load20_2m": {"users": "Visitor", "n": 20, "seconds": 120},
    # Memory soak (ADR 0033): mixed traffic long enough to see whether memory levels off.
    "soak5": {"users": "Visitor", "n": 5, "seconds": 1200},
}
DEFAULT_SCENARIOS = ("read", "score", "score_global", "jobs", "streams", "load5", "load20")
SPACE_LIMITS = ("--cpus=2", "--memory=16g")
"""The free Hugging Face Space: 2 vCPUs, 16 GB."""
GAUGES = (
    "mutascope_job_queue_length",
    "mutascope_jobs_running",
    "mutascope_sse_streams_open",
    "mutascope_inference_running",
    "mutascope_inference_waiting",
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get(url: str, token: str | None = None, timeout: float = 5) -> tuple[int, str]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except OSError:
        return 0, ""


def post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def tree_rss(process: psutil.Process) -> int:
    """Resident bytes of the server and its children (``uv run`` spawns the interpreter)."""
    total = 0
    for p in [process, *process.children(recursive=True)]:
        with contextlib.suppress(psutil.Error):
            total += p.memory_info().rss
    return total


def parse_gauges(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for line in text.splitlines():
        name, _, value = line.partition(" ")
        if name in GAUGES:
            values[name] = float(value)
    return values


class Server(Protocol):
    """The API under test: a local process or a container."""

    def running(self) -> bool: ...
    def memory(self) -> dict[str, float]: ...
    def stop(self) -> None: ...
    def log_text(self) -> str: ...


class Sampler(threading.Thread):
    """Memory and gauges once a second until stopped."""

    def __init__(self, server: Server, base: str, token: str) -> None:
        super().__init__(daemon=True)
        self.server, self.base, self.token = server, base, token
        self.samples: list[dict[str, float]] = []
        self.stop = threading.Event()
        self.start_time = time.monotonic()

    def run(self) -> None:
        while not self.stop.is_set():
            _, text = get(f"{self.base}/metrics", self.token)
            sample = {"t": round(time.monotonic() - self.start_time, 1)}
            sample.update(self.server.memory())
            sample.update(parse_gauges(text))
            self.samples.append(sample)
            self.stop.wait(1.0)


class LocalServer:
    """``python -m api.app.serve`` in the main environment (Task 5's runs)."""

    def __init__(self, workdir: Path, port: int, token: str) -> None:
        self.workdir = workdir
        self.process = start_server(workdir, port, token)
        self.ps = psutil.Process(self.process.pid)

    def running(self) -> bool:
        return self.process.poll() is None

    def memory(self) -> dict[str, float]:
        return {"rss_mb": round(tree_rss(self.ps) / 2**20, 1)}

    def stop(self) -> None:
        self.process.send_signal(signal.SIGTERM)  # what Docker and the Space send
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def log_text(self) -> str:
        return (self.workdir / "server.log").read_text()


def docker(*args: str, check: bool = True) -> str:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check, timeout=120
    ).stdout


class ContainerServer:
    """The API image under the Space's limits, published on ``127.0.0.1:<port>``."""

    def __init__(
        self, image: str, port: int, token: str, limits: tuple[str, ...] = SPACE_LIMITS
    ) -> None:
        self.limits = limits
        self.name = f"mutascope-load-{secrets.token_hex(4)}"
        gateway = docker(
            "network", "inspect", "bridge", "-f", "{{(index .IPAM.Config 0).Gateway}}"
        ).strip()
        docker(
            "run", "--detach", "--name", self.name, *limits,
            "--publish", f"127.0.0.1:{port}:7860",
            "--env", f"MUTASCOPE_API_TRUSTED_PROXIES={gateway}",
            "--env", f"MUTASCOPE_API_METRICS_TOKEN={token}",
            image,
        )  # fmt: skip
        self.image_id = docker("inspect", "-f", "{{.Image}}", self.name).strip()
        self.trusted_proxy = gateway
        self._log = ""

    def running(self) -> bool:
        state = docker("inspect", "-f", "{{.State.Running}}", self.name, check=False)
        return state.strip() == "true"

    def memory(self) -> dict[str, float]:
        out = docker(
            "exec", self.name, "cat", "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.stat",
            check=False,
        )  # fmt: skip
        lines = out.splitlines()
        if not lines:
            return {}
        stat = dict(line.split() for line in lines[1:] if " " in line)
        current = int(lines[0])
        mb = 2**20
        return {
            "usage_mb": round((current - int(stat.get("inactive_file", 0))) / mb, 1),
            "anon_mb": round(int(stat.get("anon", 0)) / mb, 1),
            "file_mb": round(int(stat.get("file", 0)) / mb, 1),
        }

    def stop(self) -> None:
        docker("stop", "--time", "30", self.name, check=False)  # SIGTERM, then 30 s
        logs = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True)
        self._log = logs.stdout + logs.stderr
        docker("rm", "--force", self.name, check=False)

    def log_text(self) -> str:
        return self._log


def start_server(workdir: Path, port: int, token: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "MUTASCOPE_API_HOST": "127.0.0.1",
        "MUTASCOPE_API_PORT": str(port),
        "MUTASCOPE_API_ONNX_THREADS": "2",
        "MUTASCOPE_API_TRUSTED_PROXIES": "127.0.0.1",
        "MUTASCOPE_API_SCORE_CACHE_DIR": str(workdir / "score-cache"),
        "MUTASCOPE_API_LOCK_FILE": str(workdir / "api.lock"),
        "MUTASCOPE_API_METRICS_TOKEN": token,
    }
    env.pop("VIRTUAL_ENV", None)  # the main project's env, not this one's
    stderr = (workdir / "server.log").open("wb")
    return subprocess.Popen(
        ["uv", "run", "--project", str(REPO), "python", "-m", "api.app.serve"],
        cwd=REPO,
        env=env,
        stdout=stderr,
        stderr=stderr,
    )


def wait_ready(base: str, server: Server, seconds: float = 300) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not server.running():
            raise RuntimeError(f"the API exited: {server.log_text()[-2000:]}")
        if get(f"{base}/ready")[0] == 200:
            return
        time.sleep(0.5)
    raise RuntimeError("the API did not become ready")


def read_stats(prefix: Path) -> list[dict[str, Any]]:
    """Locust's per-request-name rows: count, failures and latency percentiles (ms)."""
    rows: list[dict[str, Any]] = []
    with Path(f"{prefix}_stats.csv").open() as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "name": row["Name"],
                    "count": int(row["Request Count"]),
                    "failures": int(row["Failure Count"]),
                    "median_ms": float(row["50%"]) if row["50%"] != "N/A" else None,
                    "p95_ms": float(row["95%"]) if row["95%"] != "N/A" else None,
                    "p99_ms": float(row["99%"]) if row["99%"] != "N/A" else None,
                    "max_ms": float(row["Max Response Time"]),
                    "rps": float(row["Requests/s"]),
                }
            )
    return rows


def run_scenario(
    name: str,
    spec: dict[str, Any],
    out: Path,
    image: str | None = None,
    limits: tuple[str, ...] = SPACE_LIMITS,
) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix=f"mutascope-load-{name}-"))
    port, token = free_port(), secrets.token_hex(24)
    base = f"http://127.0.0.1:{port}"
    server: Server = (
        ContainerServer(image, port, token, limits) if image else LocalServer(workdir, port, token)
    )
    try:
        wait_ready(base, server)
        log.info("scenario %s: server ready on %s", name, base)
        env = {**os.environ, "LOADTEST_COUNTS": str(out / f"{name}_counts.json")}
        if "long_job" in spec:
            sequence = ("MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQ" * 10)[
                : spec["long_job"]
            ]
            job = post_json(
                f"{base}/v1/jobs", {"sequence": sequence}, {"X-Forwarded-For": "198.51.250.1"}
            )
            env["LOADTEST_JOB"] = job["id"]
        sampler = Sampler(server, base, token)
        sampler.start()
        prefix = out / name
        started = time.monotonic()
        locust = subprocess.run(
            [
                "locust",
                "-f",
                str(LOCUSTFILE),
                spec["users"],
                "--headless",
                "--users",
                str(spec["n"]),
                "--spawn-rate",
                str(spec["n"]),
                "--run-time",
                f"{spec['seconds']}s",
                "--host",
                base,
                "--csv",
                str(prefix),
                "--only-summary",
                "--loglevel",
                "WARNING",
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        elapsed = time.monotonic() - started
        sampler.stop.set()
        sampler.join()
        (out / f"{name}_locust.log").write_text(locust.stdout + locust.stderr)
    finally:
        server.stop()
    counts = json.loads((out / f"{name}_counts.json").read_text())
    server_log = server.log_text()
    (out / f"{name}_server.log").write_text(server_log)
    by_status: Counter[int] = Counter()
    for row in counts:
        by_status[row["status"]] += row["count"]
    key = "anon_mb" if image else "rss_mb"
    rss = [s[key] for s in sampler.samples if key in s]
    warm = [s[key] for s in sampler.samples if s["t"] >= 30 and key in s] or rss
    return {
        "users": spec["users"],
        "n_users": spec["n"],
        "seconds": spec["seconds"],
        "elapsed_s": round(elapsed, 1),
        "locust_exit": locust.returncode,
        "status_counts": dict(sorted(by_status.items())),
        "per_client": counts,
        "stats": read_stats(prefix),
        "runtime": (
            {
                "image": image,
                "image_id": server.image_id,
                "limits": list(server.limits),
                "trusted_proxy": server.trusted_proxy,
                "memory": "anon_mb (cgroup)",
            }
            if isinstance(server, ContainerServer)
            else {"memory": "rss_mb (process tree)"}
        ),
        "per_minute": per_minute(sampler.samples),
        "rss_mb": {
            "first": rss[0] if rss else None,
            "after_30s": warm[0] if warm else None,
            "max": max(rss) if rss else None,
            "last": rss[-1] if rss else None,
        },
        "gauge_max": {
            g: max((s.get(g, 0.0) for s in sampler.samples), default=0.0) for g in GAUGES
        },
        "server_log_errors": sum(
            1 for line in server_log.splitlines() if '"level": "error"' in line
        ),
        "server_tracebacks": server_log.count("Traceback"),
        "samples": sampler.samples,
    }


def per_minute(samples: list[dict[str, float]]) -> list[dict[str, float]]:
    """The sample nearest each whole minute (memory curve of the soak run)."""
    rows: list[dict[str, float]] = []
    if not samples:
        return rows
    last = samples[-1]["t"]
    for minute in range(int(last // 60) + 1):
        nearest = min(samples, key=lambda s: abs(s["t"] - 60 * minute))
        rows.append({"minute": minute, **{k: v for k, v in nearest.items() if k.endswith("_mb")}})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--image", help="run the API image in Docker instead of a local process")
    parser.add_argument(
        "--limits",
        default=" ".join(SPACE_LIMITS),
        help="docker run limits of the container, e.g. '--cpus=2 --memory=4g' (Cloud Run)",
    )
    parser.add_argument("scenarios", nargs="*", default=list(DEFAULT_SCENARIOS))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    for name in args.scenarios:
        result = run_scenario(name, SCENARIOS[name], out, args.image, tuple(args.limits.split()))
        summary[name] = result
        summary_path.write_text(json.dumps(summary, indent=1))
        log.info(
            "scenario %s: statuses %s, rss %s MB, gauges %s",
            name,
            result["status_counts"],
            result["rss_mb"],
            result["gauge_max"],
        )


if __name__ == "__main__":
    main()

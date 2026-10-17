"""Run the local load test: each scenario against a fresh API process (docs/load-test.md).

Usage (from the repository root)::

    uv run --project tools/loadtest python tools/loadtest/run.py --out <dir> [scenario ...]

For each scenario this starts the API with its production start command
(``python -m api.app.serve`` in the main environment: one process, one worker, ONNX threads = 2,
the real fp32 models and bundle at their pinned revisions, ``trusted_proxies = 127.0.0.1``, an
empty score cache), waits for ``/ready``, runs Locust headless, samples the server's resident
memory and ``/metrics`` every second, stops the server, and writes ``<dir>/summary.json`` with
the status counts, Locust's latency percentiles, memory and the gauges' maxima.

A fresh process per scenario means no scenario sees another's rate-limit counts or cache.
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
from typing import Any

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
}
GAUGES = ("mutascope_job_queue_length", "mutascope_jobs_running", "mutascope_sse_streams_open")


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


class Sampler(threading.Thread):
    """RSS and gauges once a second until stopped."""

    def __init__(self, process: psutil.Process, base: str, token: str) -> None:
        super().__init__(daemon=True)
        self.process, self.base, self.token = process, base, token
        self.samples: list[dict[str, float]] = []
        self.stop = threading.Event()
        self.start_time = time.monotonic()

    def run(self) -> None:
        while not self.stop.is_set():
            _, text = get(f"{self.base}/metrics", self.token)
            sample = {"t": round(time.monotonic() - self.start_time, 1)}
            sample["rss_mb"] = round(tree_rss(self.process) / 2**20, 1)
            sample.update(parse_gauges(text))
            self.samples.append(sample)
            self.stop.wait(1.0)


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


def wait_ready(base: str, server: subprocess.Popen[bytes], seconds: float = 300) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError(f"the API exited with {server.returncode}")
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


def run_scenario(name: str, spec: dict[str, Any], out: Path) -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix=f"mutascope-load-{name}-"))
    port, token = free_port(), secrets.token_hex(24)
    base = f"http://127.0.0.1:{port}"
    server = start_server(workdir, port, token)
    try:
        wait_ready(base, server)
        log.info("scenario %s: server ready on %s (pid %d)", name, base, server.pid)
        env = {**os.environ, "LOADTEST_COUNTS": str(out / f"{name}_counts.json")}
        if "long_job" in spec:
            sequence = ("MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQ" * 10)[
                : spec["long_job"]
            ]
            job = post_json(
                f"{base}/v1/jobs", {"sequence": sequence}, {"X-Forwarded-For": "198.51.250.1"}
            )
            env["LOADTEST_JOB"] = job["id"]
        sampler = Sampler(psutil.Process(server.pid), base, token)
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
        server.send_signal(signal.SIGTERM)  # what Docker and the Space send
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()
    counts = json.loads((out / f"{name}_counts.json").read_text())
    server_log = (workdir / "server.log").read_text()
    by_status: Counter[int] = Counter()
    for row in counts:
        by_status[row["status"]] += row["count"]
    rss = [s["rss_mb"] for s in sampler.samples]
    warm = [s["rss_mb"] for s in sampler.samples if s["t"] >= 30] or rss
    return {
        "users": spec["users"],
        "n_users": spec["n"],
        "seconds": spec["seconds"],
        "elapsed_s": round(elapsed, 1),
        "locust_exit": locust.returncode,
        "status_counts": dict(sorted(by_status.items())),
        "per_client": counts,
        "stats": read_stats(prefix),
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    for name in args.scenarios:
        result = run_scenario(name, SCENARIOS[name], out)
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

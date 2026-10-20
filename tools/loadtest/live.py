"""Measure the deployed API on Cloud Run: latency, one precise job, load (ADR 0034, 0036).

Usage (from the repository root, after a deploy)::

    MUTASCOPE_API_METRICS_TOKEN=<token> uv run --project tools/loadtest python \
        tools/loadtest/live.py --url https://<service>.run.app --out build/live-measure \
        --load-users 5 20

The token (read from the environment, never from the command line) lets the script check
``/metrics`` (401 without it, 200 with it) and sample the server's memory during the load runs.

0. **Cold start:** the first ``GET /ready``, timed. The service scales to zero, so after 15+
   idle minutes this is the wait of a first visitor (``cold_start_s``; the instance may also
   have been warm, which ``/ready``'s timing shows).

It runs on Cloud Run's free tier, so this is deliberately short (about 10 minutes, ~250
requests) and stays inside one client's rate limits where it can:

1. **Network baseline:** ``GET /health`` ``--rtt`` times, so the latencies below can be read
   against the round trip to the service.
2. **Reads, one user:** ``GET /v1/proteins/{id}``, its landscape and ``/v1/benchmarks``, ``--n``
   each, one at a time (no ``If-None-Match``, so every one is a full answer).
3. **Fast mode:** ``POST /v1/score`` for 300 and 1,000 residues with ``esm2_35m`` and
   ``esm2_150m``, ``--n`` requests each, one at a time, ``--spacing`` seconds apart (the
   per-client limit is 20 a minute). Every sequence is new, so every request scores
   (``X-MutaScope-Cache: miss`` is checked); one warm-up request per model is not counted, so
   a cold start (the service scales to zero) is not in the percentiles.
   Latency is the client's wall time for the whole request, network included.
4. **Precise mode:** one job of ``--precise-length`` residues (the deployed limit, 400),
   timed from submission to ``succeeded``.
5. **Load:** Locust's ``Visitor`` mix (locustfile.py) for ``--load-seconds`` at each of
   ``--load-users``. Through Google's front end every user is this machine's address, so all
   users share one client's limits: 429s here are the limiter doing its job, and 503s
   ``scoring-busy`` the inference pool's (ADR 0036); both are counted, not failures. With the
   token, ``/metrics`` is scraped every ``--scrape-seconds`` meanwhile: resident memory plus
   the score cache (the instance's disk is memory) against the 4 GiB limit, and the inference
   gauges. The scrapes count against the same client's read limit.

``--precise-only L [L ...]`` runs step 0 and then only step 4, one job per length in turn
(each waits for the one before), and writes ``precise.json``: for measuring the precise limit
without the rest of the run.

Then ADR 0031's rule: if 150M's fast-mode p95 at 1,000 residues exceeds 2 s, the default
model becomes 35M (``MUTASCOPE_API_SCORE_DEFAULT_MODEL`` in ``conf/deploy/cloudrun.yaml``, ``env``).
"""

import argparse
import http.client
import json
import logging
import math
import os
import random
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from run import LOCUSTFILE, read_stats

AA = "ACDEFGHIKLMNPQRSTVWY"
MODELS = ("esm2_35m", "esm2_150m")
LENGTHS = (300, 1000)
LIMIT_WINDOW_S = 60
"""The rate limits' moving window (``.../minute``)."""
MEMORY_LIMIT_MIB = 4096
"""``memory: 4Gi`` in conf/deploy/cloudrun.yaml."""
FALLBACK_P95_S = 2.0
"""ADR 0029 / 0031: 150M stays the default only if its p95 at 1,000 residues is at most 2 s."""
log = logging.getLogger("live")


def request(
    url: str,
    body: dict[str, Any] | None = None,
    timeout: float = 120,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes, float]:
    """One request; returns status, headers, body and wall time in seconds.

    A connection the server (or Google's front end) closes without an answer is status 0, so
    the caller counts it instead of the whole measurement stopping (the first run of ADR 0036
    lost one request this way, with the instance still up).
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read()
            status, headers = response.status, dict(response.headers)
    except urllib.error.HTTPError as error:
        payload, status, headers = error.read(), error.code, dict(error.headers)
    except (http.client.HTTPException, ConnectionError, urllib.error.URLError) as error:
        log.warning("no answer from %s: %r", url.split("?")[0], error)
        payload, status, headers = b"", 0, {}
    return status, headers, payload, time.perf_counter() - started


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (no interpolation: with 20 values, p95 is the 19th)."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


def summarise(seconds: list[float]) -> dict[str, float | int]:
    return {
        "n": len(seconds),
        "p50_s": round(statistics.median(seconds), 3),
        "p95_s": round(percentile(seconds, 95), 3),
        "max_s": round(max(seconds), 3),
        "mean_s": round(statistics.fmean(seconds), 3),
    }


def network_baseline(base: str, n: int) -> dict[str, float | int]:
    times: list[float] = []
    for _ in range(n):
        status, _, _, elapsed = request(f"{base}/health")
        if status == 200:
            times.append(elapsed)
    return summarise(times)


def read_latency(base: str, n: int, spacing: float, rng: random.Random) -> dict[str, Any]:
    """Each read route, one request at a time; 429s (shared limit) are waited out, not counted."""
    _, _, payload, _ = request(f"{base}/v1/proteins")
    assays = [p["assay_id"] for p in json.loads(payload)["proteins"]]
    cells: dict[str, Any] = {}
    routes = {
        "GET /v1/proteins/{id}": lambda a: f"/v1/proteins/{a}",
        "GET /v1/proteins/{id}/landscape": lambda a: f"/v1/proteins/{a}/landscape",
        "GET /v1/benchmarks": lambda _: "/v1/benchmarks",
    }
    for name, path in routes.items():
        times: list[float] = []
        dropped = 0
        while len(times) < n:
            status, headers, _, elapsed = request(f"{base}{path(rng.choice(assays))}")
            if status == 429:
                time.sleep(float(headers.get("Retry-After", "10")))
                continue
            if status == 0:
                dropped += 1
                continue
            if status != 200:
                raise RuntimeError(f"{name}: unexpected {status}")
            times.append(elapsed)
            time.sleep(spacing)
        cells[name] = {
            **summarise(times),
            "p99_s": round(percentile(times, 99), 3),
            "no_answer": dropped,
        }
        log.info("%s: %s", name, cells[name])
    return cells


def metrics_check(base: str, token: str | None) -> dict[str, Any]:
    """``/metrics``: the status without a token and with it (ADR 0032)."""
    without, _, _, _ = request(f"{base}/metrics")
    result: dict[str, Any] = {"without_token": without}
    if token is not None:
        status, _, body, _ = request(f"{base}/metrics", headers=bearer(token))
        result["with_token"] = status
        result["has_inference_gauges"] = b"mutascope_inference_running" in body
    return result


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


SAMPLED = (
    "process_resident_memory_bytes",
    "mutascope_score_cache_bytes",
    "mutascope_inference_running",
    "mutascope_inference_waiting",
    "mutascope_jobs_running",
)


def scrape(base: str, token: str) -> dict[str, float]:
    """The :data:`SAMPLED` series of one ``/metrics`` scrape (unlabelled samples only)."""
    status, _, body, _ = request(f"{base}/metrics", headers=bearer(token), timeout=30)
    if status != 200:
        return {}
    values: dict[str, float] = {}
    for line in body.decode().splitlines():
        name, _, value = line.partition(" ")
        if name in SAMPLED:
            values[name] = float(value)
    return values


class Sampler(threading.Thread):
    """Scrapes ``/metrics`` every ``every`` seconds until stopped."""

    def __init__(self, base: str, token: str, every: float) -> None:
        super().__init__(daemon=True)
        self.base, self.token, self.every = base, token, every
        self.samples: list[dict[str, float]] = []
        self.stopped = threading.Event()
        self.started_at = time.monotonic()

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                sample = scrape(self.base, self.token)
            except OSError as error:
                log.warning("scrape failed: %s", error)
                sample = {}
            if sample:
                self.samples.append({"t": round(time.monotonic() - self.started_at, 1), **sample})
            self.stopped.wait(self.every)

    def summary(self) -> dict[str, Any]:
        mib = 2**20
        totals = [
            (s.get("process_resident_memory_bytes", 0) + s.get("mutascope_score_cache_bytes", 0))
            / mib
            for s in self.samples
        ]
        rss = [s.get("process_resident_memory_bytes", 0) / mib for s in self.samples]
        return {
            "scrapes": len(self.samples),
            "rss_mib_max": round(max(rss), 1) if rss else None,
            "rss_plus_cache_mib_max": round(max(totals), 1) if totals else None,
            "limit_mib": MEMORY_LIMIT_MIB,
            "share_of_limit_max": round(max(totals) / MEMORY_LIMIT_MIB, 3) if totals else None,
            "inference_running_max": max(
                (s.get("mutascope_inference_running", 0) for s in self.samples), default=None
            ),
            "inference_waiting_max": max(
                (s.get("mutascope_inference_waiting", 0) for s in self.samples), default=None
            ),
            "samples": self.samples,
        }


def fast_latency(base: str, n: int, spacing: float, rng: random.Random) -> dict[str, Any]:
    """Each (model, length) cell, cache misses only, at most one request in flight."""
    cells: dict[str, Any] = {}
    for model in MODELS:
        warm = "".join(rng.choice(AA) for _ in range(LENGTHS[-1]))
        status, _, _, elapsed = request(f"{base}/v1/score", {"sequence": warm, "model": model})
        log.info("warm-up %s: %s in %.2f s", model, status, elapsed)
        time.sleep(spacing)
        for length in LENGTHS:
            times: list[float] = []
            refused = dropped = 0
            while len(times) < n:
                sequence = "".join(rng.choice(AA) for _ in range(length))
                status, headers, _, elapsed = request(
                    f"{base}/v1/score", {"sequence": sequence, "model": model}
                )
                if status == 429:  # someone else shares the limit; wait it out, do not count
                    refused += 1
                    time.sleep(float(headers.get("Retry-After", "10")))
                    continue
                if status == 0:  # no answer: counted, not timed
                    dropped += 1
                    time.sleep(spacing)
                    continue
                if status != 200:
                    raise RuntimeError(f"{model} {length}: unexpected {status}")
                if headers.get("X-MutaScope-Cache", headers.get("x-mutascope-cache")) != "miss":
                    raise RuntimeError(
                        f"{model} {length}: not a cache miss (a warm instance keeps its cache: "
                        "pass another --seed than the last run's)"
                    )
                times.append(elapsed)
                time.sleep(spacing)
            cells[f"{model}@{length}"] = {
                **summarise(times),
                "refused_429": refused,
                "no_answer": dropped,
            }
            log.info("%s @ %d: %s", model, length, cells[f"{model}@{length}"])
    return cells


def precise_job(base: str, length: int, rng: random.Random, timeout: float = 600) -> dict[str, Any]:
    """Submit one job and follow it on its SSE stream, as the website does.

    On Cloud Run the instance has CPU only while a request is in flight (request-based
    billing), so a job advances while its stream is open; polling ``GET /v1/jobs/{id}`` every
    few seconds would leave it without CPU in between and measure that instead.
    """
    sequence = "".join(rng.choice(AA) for _ in range(length))
    started = time.perf_counter()
    status, _, payload, _ = request(f"{base}/v1/jobs", {"sequence": sequence})
    if status != 202:
        raise RuntimeError(f"precise job: unexpected {status}: {payload[:200]!r}")
    job = json.loads(payload)["id"]
    log.info("precise job %s: %d residues", job, length)
    events = 0
    with urllib.request.urlopen(f"{base}/v1/jobs/{job}/stream", timeout=timeout) as stream:
        for line in stream:  # the server ends the stream when the job has finished
            events += line.startswith(b"event:")
    _, _, payload, _ = request(f"{base}/v1/jobs/{job}")
    return {
        "length": length,
        "status": json.loads(payload)["status"],
        "seconds": round(time.perf_counter() - started, 1),
        "events": events,
    }


def load_run(
    base: str, out: Path, users: int, seconds: int, token: str | None, every: float
) -> dict[str, Any]:
    sampler = Sampler(base, token, every) if token is not None else None
    if sampler is not None:
        sampler.start()
    prefix = out / f"load{users}"
    counts = out / f"load{users}_counts.json"
    proc = subprocess.run(
        [
            "locust", "-f", str(LOCUSTFILE), "Visitor", "--headless",
            "--users", str(users), "--spawn-rate", str(users), "--run-time", f"{seconds}s",
            "--host", base, "--csv", str(prefix), "--only-summary", "--loglevel", "WARNING",
        ],
        env={**os.environ, "LOADTEST_COUNTS": str(counts)},
        capture_output=True,
        text=True,
    )  # fmt: skip
    if sampler is not None:
        sampler.stopped.set()
        sampler.join()
    (out / f"load{users}_locust.log").write_text(proc.stdout + proc.stderr)
    by_status: dict[int, int] = {}
    for row in json.loads(counts.read_text()):
        by_status[row["status"]] = by_status.get(row["status"], 0) + row["count"]
    return {
        "users": users,
        "seconds": seconds,
        "locust_exit": proc.returncode,
        "status_counts": dict(sorted(by_status.items())),
        "stats": read_stats(prefix),
        "server": sampler.summary() if sampler is not None else None,
    }


def decision(cells: dict[str, Any]) -> dict[str, Any]:
    p95 = cells["esm2_150m@1000"]["p95_s"]
    default = "esm2_35m" if p95 > FALLBACK_P95_S else "esm2_150m"
    return {
        "rule": f"150M fast-mode p95 at 1,000 residues > {FALLBACK_P95_S} s -> default 35M",
        "esm2_150m_p95_1000_s": p95,
        "score_default_model": default,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=20, help="requests per (model, length) cell")
    parser.add_argument("--rtt", type=int, default=20)
    parser.add_argument("--spacing", type=float, default=3.5)
    parser.add_argument("--read-spacing", type=float, default=0.6)
    parser.add_argument("--precise-length", type=int, default=400)
    parser.add_argument("--load-users", type=int, nargs="+", default=[5])
    parser.add_argument("--load-seconds", type=int, default=120, help="per load run (<= 120)")
    parser.add_argument("--scrape-seconds", type=float, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--precise-only", type=int, nargs="+", metavar="LENGTH",
        help="only time one precise job per length (no reads, fast mode or load)",
    )  # fmt: skip
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    base = args.url.rstrip("/")
    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    token = os.environ.get("MUTASCOPE_API_METRICS_TOKEN")

    status, _, payload, cold = request(f"{base}/ready", timeout=300)
    if status != 200:
        raise RuntimeError(f"/ready answered {status}")
    _, _, health, _ = request(f"{base}/health")
    summary: dict[str, Any] = {
        "url": base,
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cold_start_s": round(cold, 2),
        "ready": json.loads(payload),
        "version": json.loads(health)["version"],
    }
    if args.precise_only:
        summary["precise"] = []
        for length in args.precise_only:
            try:
                result = precise_job(base, length, rng)
            except (OSError, RuntimeError) as error:  # one failed length does not end the run
                result = {"length": length, "status": "error", "error": repr(error)}
            summary["precise"].append(result)
            log.info("precise: %s", result)
        (args.out / "precise.json").write_text(json.dumps(summary, indent=1))
        return
    summary["metrics"] = metrics_check(base, token)
    log.info("metrics: %s", summary["metrics"])
    summary["network"] = network_baseline(base, args.rtt)
    summary["reads"] = read_latency(base, args.n, args.read_spacing, rng)
    summary["fast"] = fast_latency(base, args.n, args.spacing, rng)
    summary["decision"] = decision(summary["fast"])
    summary["precise"] = precise_job(base, args.precise_length, rng)
    summary["load"] = {}
    for index, users in enumerate(args.load_users):
        if index:
            time.sleep(LIMIT_WINDOW_S)  # each run starts with an empty rate-limit window
        run = load_run(base, args.out, users, args.load_seconds, token, args.scrape_seconds)
        summary["load"][str(users)] = run
        log.info(
            "load %d: %s, server %s",
            users,
            run["status_counts"],
            run["server"] and {k: v for k, v in run["server"].items() if k != "samples"},
        )
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    log.info("decision: %s", summary["decision"])
    log.info("precise: %s", summary["precise"])


if __name__ == "__main__":
    main()

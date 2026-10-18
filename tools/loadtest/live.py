"""Measure the deployed API on Cloud Run: latency, one precise job, 5 users (ADR 0034).

Usage (from the repository root, after a deploy)::

    uv run --project tools/loadtest python tools/loadtest/live.py \
        --url https://<service>.run.app --out build/live-measure

0. **Cold start:** the first ``GET /ready``, timed. The service scales to zero, so after 15+
   idle minutes this is the wait of a first visitor (``cold_start_s``; the instance may also
   have been warm, which ``/ready``'s timing shows).

It runs on Cloud Run's free tier, so this is deliberately short (about 10 minutes, ~250
requests) and stays inside one client's rate limits where it can:

1. **Network baseline:** ``GET /health`` ``--rtt`` times, so the latencies below can be read
   against the round trip to the service.
2. **Fast mode:** ``POST /v1/score`` for 300 and 1,000 residues with ``esm2_35m`` and
   ``esm2_150m``, ``--n`` requests each, one at a time, ``--spacing`` seconds apart (the
   per-client limit is 20 a minute). Every sequence is new, so every request scores
   (``X-MutaScope-Cache: miss`` is checked); one warm-up request per model is not counted, so
   a cold start (the service scales to zero) is not in the percentiles.
   Latency is the client's wall time for the whole request, network included.
3. **Precise mode:** one job of ``--precise-length`` residues (the provisional limit, ADR 0031
   section 6), timed from submission to ``succeeded``.
4. **Load:** Locust's ``Visitor`` mix (locustfile.py) at 5 users for ``--load-seconds``. Through
   Google's front end every user is this machine's address, so the five share one client's
   limits: 429s here are the limiter doing its job, and are counted, not failures.

Then ADR 0031's rule: if 150M's fast-mode p95 at 1,000 residues exceeds 2 s, the default
model becomes 35M (``MUTASCOPE_API_SCORE_DEFAULT_MODEL`` in ``conf/deploy/cloudrun.yaml``, ``env``).
"""

import argparse
import json
import logging
import math
import os
import random
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from run import LOCUSTFILE, read_stats

AA = "ACDEFGHIKLMNPQRSTVWY"
MODELS = ("esm2_35m", "esm2_150m")
LENGTHS = (300, 1000)
FALLBACK_P95_S = 2.0
"""ADR 0029 / 0031: 150M stays the default only if its p95 at 1,000 residues is at most 2 s."""
log = logging.getLogger("live")


def request(
    url: str, body: dict[str, Any] | None = None, timeout: float = 120
) -> tuple[int, dict[str, str], bytes, float]:
    """One request; returns status, headers, body and wall time in seconds."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read()
            status, headers = response.status, dict(response.headers)
    except urllib.error.HTTPError as error:
        payload, status, headers = error.read(), error.code, dict(error.headers)
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
            refused = 0
            while len(times) < n:
                sequence = "".join(rng.choice(AA) for _ in range(length))
                status, headers, _, elapsed = request(
                    f"{base}/v1/score", {"sequence": sequence, "model": model}
                )
                if status == 429:  # someone else shares the limit; wait it out, do not count
                    refused += 1
                    time.sleep(float(headers.get("Retry-After", "10")))
                    continue
                if status != 200:
                    raise RuntimeError(f"{model} {length}: unexpected {status}")
                if headers.get("X-MutaScope-Cache", headers.get("x-mutascope-cache")) != "miss":
                    raise RuntimeError(f"{model} {length}: not a cache miss")
                times.append(elapsed)
                time.sleep(spacing)
            cells[f"{model}@{length}"] = {**summarise(times), "refused_429": refused}
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


def load_run(base: str, out: Path, users: int, seconds: int) -> dict[str, Any]:
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
    parser.add_argument("--precise-length", type=int, default=600)
    parser.add_argument("--load-users", type=int, default=5)
    parser.add_argument("--load-seconds", type=int, default=120)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    base = args.url.rstrip("/")
    args.out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

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
    summary["network"] = network_baseline(base, args.rtt)
    summary["fast"] = fast_latency(base, args.n, args.spacing, rng)
    summary["decision"] = decision(summary["fast"])
    summary["precise"] = precise_job(base, args.precise_length, rng)
    summary["load"] = load_run(base, args.out, args.load_users, args.load_seconds)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    log.info("decision: %s", summary["decision"])
    log.info("precise: %s", summary["precise"])


if __name__ == "__main__":
    main()

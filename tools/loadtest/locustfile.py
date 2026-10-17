"""Locust users for the local load test of the API (docs/load-test.md, ADR 0032).

The API runs with ``trusted_proxies = 127.0.0.1``, as it will behind the Space's proxy, and each
user sends its own ``X-Forwarded-For`` address, so every user is a separate client for the rate
limits. A 429 is an expected answer here, not a failure: it is recorded under its own request
name (``... [429]``), and checked to be a problem with ``Retry-After``. Any other status that the
route does not document, a 5xx, or a connection error is a failure.

Classes ending in ``Hammer`` send as fast as they can (one per limit, run alone, to see the 429s
start exactly at the limit); ``Visitor`` is mixed traffic with think time (the 5- and 20-user
runs). Each user's status counts are written to ``$LOADTEST_COUNTS`` (JSON) when Locust stops.
"""

import itertools
import json
import os
import random
from collections import Counter
from pathlib import Path
from typing import Any

from locust import FastHttpUser, between, constant, events, task
from locust.env import Environment

AA = "ACDEFGHIKLMNPQRSTVWY"
COUNTS: Counter[tuple[str, str, int]] = Counter()
"""(user class, client address, status) -> responses."""
_addresses = itertools.count(1)


def random_sequence(rng: random.Random, length: int) -> str:
    return "".join(rng.choice(AA) for _ in range(length))


class Client(FastHttpUser):
    """One client address; records every status and judges each response."""

    abstract = True
    address = ""

    def on_start(self) -> None:
        n = next(_addresses)
        self.address = f"198.51.{n // 250}.{n % 250 + 1}"
        self.rng = random.Random(n)
        self.proteins: list[str] = []

    def call(self, method: str, path: str, name: str, ok: tuple[int, ...], **kwargs: Any) -> Any:
        headers = {"X-Forwarded-For": self.address, **kwargs.pop("headers", {})}
        label = f"{method} {name}"
        with self.client.request(
            method, path, headers=headers, name=label, catch_response=True, **kwargs
        ) as response:
            status = response.status_code
            COUNTS[(type(self).__name__, self.address, status)] += 1
            if status == 429:
                response.request_meta["name"] = f"{label} [429]"
                retry_after = response.headers.get("retry-after", "")
                if response.headers.get("content-type") != "application/problem+json":
                    response.failure("429 without a problem body")
                elif "rate-limited" in response.text and not retry_after.isdigit():
                    response.failure("rate-limited 429 without Retry-After")
                else:
                    response.success()
            elif status in ok:
                response.success()
            else:
                response.failure(f"unexpected {status}: {response.text[:200]}")
            return response


# -- one limit at a time -------------------------------------------------------------------------


class ReadHammer(Client):
    """``rate_limit_read``: protein detail as fast as possible."""

    wait_time = constant(0)

    @task
    def detail(self) -> None:
        path = "/v1/proteins/TCRG1_MOUSE_Tsuboyama_2023_1E0L"
        self.call("GET", path, "/v1/proteins/{id}", (200,))


class ScoreHammer(Client):
    """``rate_limit_score`` (one user) or ``rate_limit_score_global`` (many users)."""

    wait_time = constant(0)

    @task
    def score(self) -> None:
        body = {"sequence": random_sequence(self.rng, 50), "model": "esm2_35m"}
        self.call("POST", "/v1/score", "/v1/score", (200,), json=body)


class JobHammer(Client):
    """``rate_limit_jobs``: short jobs, one a second (each finishes before the next, so the
    one-active-job rule is not what refuses them)."""

    wait_time = constant(1)

    @task
    def submit(self) -> None:
        body = {"sequence": random_sequence(self.rng, 30)}
        self.call("POST", "/v1/jobs", "/v1/jobs", (202,), json=body)


class StreamHolder(Client):
    """``max_streams_per_client``: every user is the same address, holding a stream open on the
    long job named by ``$LOADTEST_JOB`` until it finishes."""

    wait_time = constant(2)  # a refused user retries slowly: its retries count as reads
    network_timeout = 180.0

    def on_start(self) -> None:
        super().on_start()
        self.address = "198.51.200.200"

    @task
    def hold(self) -> None:
        job = os.environ["LOADTEST_JOB"]
        self.call("GET", f"/v1/jobs/{job}/stream", "/v1/jobs/{id}/stream", (200,))


# -- mixed traffic -------------------------------------------------------------------------------


class Visitor(Client):
    """Someone using the site and the live scorer: browse, score a few sequences (some of them
    again: cache hits), now and then a precise job followed on its stream."""

    wait_time = between(1, 3)

    def on_start(self) -> None:
        super().on_start()
        listing = self.call("GET", "/v1/proteins", "/v1/proteins", (200,))
        if listing.status_code == 200:
            self.proteins = [p["assay_id"] for p in listing.json()["proteins"]]
        self.mine = [random_sequence(self.rng, self.rng.randint(50, 1000)) for _ in range(3)]

    @task(6)
    def browse(self) -> None:
        if not self.proteins:
            return
        assay = self.rng.choice(self.proteins)
        self.call("GET", f"/v1/proteins/{assay}", "/v1/proteins/{id}", (200,))
        if self.rng.random() < 0.3:
            path = f"/v1/proteins/{assay}/landscape"
            self.call("GET", path, "/v1/proteins/{id}/landscape", (200,))

    @task(1)
    def benchmarks(self) -> None:
        self.call("GET", "/v1/benchmarks", "/v1/benchmarks", (200,))

    @task(3)
    def score(self) -> None:
        sequence = (
            self.rng.choice(self.mine)
            if self.rng.random() < 0.5
            else random_sequence(self.rng, self.rng.randint(50, 1000))
        )
        model = self.rng.choice(["esm2_35m", "esm2_150m"])
        body: dict[str, Any] = {"sequence": sequence, "model": model}
        if self.rng.random() < 0.3:
            body["mutations"] = [f"{sequence[0]}1A" if sequence[0] != "A" else "A1C"]
        self.call("POST", "/v1/score", "/v1/score", (200,), json=body)

    @task(1)
    def precise(self) -> None:
        body = {"sequence": random_sequence(self.rng, self.rng.randint(30, 120))}
        submitted = self.call("POST", "/v1/jobs", "/v1/jobs", (202,), json=body)
        if submitted.status_code == 202:
            job = submitted.json()["id"]
            self.call("GET", f"/v1/jobs/{job}/stream", "/v1/jobs/{id}/stream", (200,))


@events.quitting.add_listener
def write_counts(environment: Environment, **_: Any) -> None:
    path = os.environ.get("LOADTEST_COUNTS")
    if path:
        rows = [
            {"user": user, "client": client, "status": status, "count": n}
            for (user, client, status), n in sorted(COUNTS.items())
        ]
        Path(path).write_text(json.dumps(rows, indent=1))

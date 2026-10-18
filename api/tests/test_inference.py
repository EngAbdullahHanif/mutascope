"""The inference pool: bounded forward passes, 503 when no slot frees, reads unaffected.

The pool's own rules are tested on plain blocking functions; the routes are tested with the
stand-in graphs held at a gate (``test_live.gate``), so "saturated" means every slot is taken by
a forward pass that has not finished and more requests are waiting. Whether reads then stay fast
on 2 real vCPUs is ``test_container.py``'s check (real models, ``--cpus=2``).
"""

import asyncio
import contextvars
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from api.app.inference import Inference
from api.app.live import LiveModelName
from api.app.main import create_app
from api.app.problems import ApiError
from api.app.settings import Settings
from api.tests.test_live import PROBLEM, SEQ, eventually, jobs, scorer

pytestmark = pytest.mark.anyio


def body(length: int) -> dict[str, str]:
    """A fast-mode request on 35M, the gated model (the test settings default to 150M)."""
    return {"sequence": SEQ[:length], "model": "esm2_35m"}


class Probe:
    """A blocking call that records how many copies run at once and waits at a gate."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self._lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.calls = 0

    def __call__(self, value: int = 0) -> int:
        with self._lock:
            self.now += 1
            self.calls += 1
            self.peak = max(self.peak, self.now)
        try:
            if not self.gate.wait(timeout=30):
                raise TimeoutError("gate never opened")
            return value
        finally:
            with self._lock:
                self.now -= 1


# -- the pool -----------------------------------------------------------------------------------


@pytest.mark.parametrize("concurrency", [1, 2])
async def test_at_most_concurrency_calls_run_at_once(concurrency: int) -> None:
    pool = Inference(concurrency=concurrency, wait_seconds=10)
    probe = Probe()
    tasks = [asyncio.create_task(pool.run(probe, i)) for i in range(6)]
    await eventually(lambda: pool.running == concurrency and pool.waiting == 6 - concurrency)
    assert probe.now == concurrency
    probe.gate.set()
    assert await asyncio.gather(*tasks) == list(range(6))
    assert probe.peak == concurrency and probe.calls == 6
    assert pool.running == 0 and pool.waiting == 0
    pool.close()


async def test_no_slot_in_time_is_a_503_with_retry_after() -> None:
    pool = Inference(concurrency=1, wait_seconds=0.2)
    probe = Probe()
    holder = asyncio.create_task(pool.run(probe))
    await eventually(lambda: pool.running == 1)

    started = time.monotonic()
    with pytest.raises(ApiError) as caught:
        await pool.run(probe, wait=False)
    assert time.monotonic() - started >= 0.2
    error = caught.value
    assert error.status == 503 and error.type == PROBLEM + "scoring-busy"
    assert error.headers == {"Retry-After": "1"}  # 0.2 s, rounded up
    assert probe.calls == 1 and pool.waiting == 0  # the refused call never ran

    probe.gate.set()
    await holder
    assert await pool.run(probe, 7, wait=False) == 7  # a free slot is taken at once
    pool.close()


async def test_a_slot_is_freed_when_the_pass_ends_not_when_its_caller_leaves() -> None:
    """A forward pass cannot be interrupted: if its slot were freed when the request was
    cancelled, a second pass would share the CPU with it."""
    pool = Inference(concurrency=1, wait_seconds=0.1)
    probe = Probe()
    caller = asyncio.create_task(pool.run(probe))
    await eventually(lambda: probe.now == 1)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert pool.running == 1  # still running in its thread
    with pytest.raises(ApiError):
        await pool.run(probe, wait=False)
    probe.gate.set()
    await eventually(lambda: pool.running == 0)
    assert await pool.run(probe, 1, wait=False) == 1
    assert probe.peak == 1
    pool.close()


async def test_the_callers_context_reaches_the_thread() -> None:
    """The request id is a contextvar (structlog); the scorer's log lines must keep it."""
    var: contextvars.ContextVar[str] = contextvars.ContextVar("var")
    var.set("request-1")
    pool = Inference(concurrency=1, wait_seconds=1)
    assert await pool.run(var.get) == "request-1"
    assert await pool.run(threading.current_thread) is not threading.current_thread()
    pool.close()


async def test_a_failing_call_frees_its_slot() -> None:
    pool = Inference(concurrency=1, wait_seconds=0.5)

    def broken() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await pool.run(broken)
    assert pool.running == 0
    assert await pool.run(int, "3", wait=False) == 3
    pool.close()


def test_a_pass_that_outlives_the_event_loop_ends_quietly(caplog: pytest.LogCaptureFixture) -> None:
    """At shutdown the loop closes while a pass may still run (seen in the 20-user load test);
    its slot cannot be released into a closed loop, and that must not be logged as an error."""
    probe = Probe()
    pools: list[Inference] = []

    async def start() -> None:
        pool = Inference(concurrency=1, wait_seconds=1)
        pools.append(pool)
        asyncio.get_running_loop().create_task(pool.run(probe))
        await eventually(lambda: probe.now == 1)

    asyncio.run(start())  # cancels the waiting task and closes the loop; the pass still runs
    with caplog.at_level(logging.ERROR, logger="concurrent.futures"):
        probe.gate.set()
        pools[0].close()
        pools[0]._executor.shutdown(wait=True)  # pyright: ignore[reportPrivateUsage]
    assert probe.calls == 1 and probe.now == 0
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], caplog.text


# -- through the routes -------------------------------------------------------------------------


@pytest.fixture
def busy(settings: Settings) -> Settings:
    """A short wait, so a refused fast request does not hold the test for 10 s."""
    return settings.model_copy(update={"inference_wait_seconds": 0.3})


def hold(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Probe:
    """Make every 35M forward pass of ``app`` wait at the returned probe's gate."""
    probe = Probe()
    model = scorer(app, LiveModelName.esm2_35m)
    original: Callable[..., Any] = model.landscape

    def landscape(*args: Any, **kwargs: Any) -> Any:
        probe()
        return original(*args, **kwargs)

    monkeypatch.setattr(model, "landscape", landscape)
    return probe


@pytest.fixture
def held(started: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[Probe]:
    """Every 35M forward pass waits at the probe's gate (opened again on teardown)."""
    probe = hold(started, monkeypatch)
    yield probe
    probe.gate.set()


async def test_reads_answer_while_scoring_is_saturated(
    client: httpx.AsyncClient, started: FastAPI, held: Probe
) -> None:
    """One pass runs, four requests wait for its slot, and reads are answered meanwhile.

    Before the pool, each fast request ran its own pass in its own thread: five passes would
    have shared the CPU here (``held.peak`` would be 5).
    """
    pool = started.state.mutascope.inference
    scores = [asyncio.create_task(client.post("/v1/score", json=body(20 + i))) for i in range(5)]
    await eventually(lambda: pool.running == 1 and pool.waiting == 4)

    for path in ("/v1/proteins", "/v1/benchmarks", "/v1/proteins/TCRG1_MOUSE_Tsuboyama_2023_1E0L"):
        started_at = time.monotonic()
        response = await client.get(path)
        assert response.status_code == 200, path
        assert time.monotonic() - started_at < 0.5, path
    assert (await client.get("/ready")).status_code == 200

    held.gate.set()
    responses = await asyncio.gather(*scores)
    assert [r.status_code for r in responses] == [200] * 5
    assert held.peak == 1 and held.calls == 5


async def test_fast_mode_waits_for_a_precise_batch_and_then_runs(
    client: httpx.AsyncClient, started: FastAPI, held: Probe
) -> None:
    """A job's batches and fast requests share the slots and take turns."""
    job = (await client.post("/v1/jobs", json={"sequence": SEQ})).json()
    await eventually(lambda: held.now == 1)  # the job's first batch holds the slot
    fast = asyncio.create_task(client.post("/v1/score", json=body(30)))
    await eventually(lambda: started.state.mutascope.inference.waiting == 1)
    assert not fast.done()

    held.gate.set()
    assert (await fast).status_code == 200
    await eventually(lambda: jobs(started).get(job["id"]).status == "succeeded")
    assert held.peak == 1


async def test_saturated_fast_mode_is_a_503_problem(
    busy: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(busy)
    async with app.router.lifespan_context(app):
        probe = hold(app, monkeypatch)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/score", json=body(20)))
            await eventually(lambda: probe.now == 1)
            refused = await client.post("/v1/score", json=body(21))
            assert refused.status_code == 503
            assert refused.headers["content-type"] == "application/problem+json"
            assert refused.headers["retry-after"] == "1"
            problem = refused.json()
            assert (
                problem["type"] == PROBLEM + "scoring-busy" and problem["instance"] == "/v1/score"
            )
            assert "x-request-id" in refused.headers

            # A cached landscape needs no slot, even while the slot is held.
            probe.gate.set()
            assert (await first).status_code == 200
            probe.gate.clear()
            second = asyncio.create_task(client.post("/v1/score", json=body(22)))
            await eventually(lambda: probe.now == 1)
            hit = await client.post("/v1/score", json=body(20))
            assert hit.status_code == 200 and hit.headers["x-mutascope-cache"] == "hit"
            probe.gate.set()
            assert (await second).status_code == 200


async def test_inference_gauges(client: httpx.AsyncClient, started: FastAPI, held: Probe) -> None:
    metrics = started.state.metrics
    task = asyncio.create_task(client.post("/v1/score", json=body(20)))
    await eventually(lambda: held.now == 1)
    assert metrics.registry.get_sample_value("mutascope_inference_running") == 1
    assert metrics.registry.get_sample_value("mutascope_inference_waiting") == 0
    held.gate.set()
    await task
    assert metrics.registry.get_sample_value("mutascope_inference_running") == 0
    cache_bytes = metrics.registry.get_sample_value("mutascope_score_cache_bytes")
    assert cache_bytes is not None and cache_bytes > 0

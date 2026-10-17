"""Live scoring with the stand-in graphs: validation, the cache, the job queue, SSE.

The stand-in ONNX graphs make every forward pass take microseconds, so a test that needs a job
to stay running holds the 35M scorer at a gate (:func:`gated`) and opens it when done.
"""

import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from api.app.jobs import JobManager
from api.app.live import LiveModelName, to_grid
from api.app.main import create_app
from api.app.settings import Settings
from mutascope.alphabet import AA_ORDER
from mutascope.scoring.onnx_esm2 import OnnxEsm2Scorer

pytestmark = pytest.mark.anyio

SEQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
"""Ubiquitin, 76 residues: five forward batches of 16 in precise mode."""
PROBLEM = "urn:mutascope:problem:"


def scorer(app: FastAPI, key: LiveModelName) -> OnnxEsm2Scorer:
    return app.state.mutascope.live[key].scorer


def jobs(app: FastAPI) -> JobManager:
    return app.state.mutascope.jobs


async def eventually(check: Callable[[], bool], seconds: float = 10) -> None:
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def parse_sse(text: str) -> list[tuple[str, int, dict[str, Any]]]:
    """``(event, id, data)`` of each event in a ``text/event-stream`` body (comments skipped)."""
    events: list[tuple[str, int, dict[str, Any]]] = []
    for block in text.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if fields:
            events.append((fields["event"], int(fields["id"]), json.loads(fields["data"])))
    return events


@pytest.fixture
def gate(started: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """Hold every 35M forward batch until the returned event is set (set again on teardown)."""
    model = scorer(started, LiveModelName.esm2_35m)
    original = model.landscape
    opened = threading.Event()

    def held(*args: Any, **kwargs: Any) -> Any:
        if not opened.wait(timeout=30):
            raise TimeoutError("gate never opened")
        return original(*args, **kwargs)

    monkeypatch.setattr(model, "landscape", held)
    yield opened
    opened.set()


# -- validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "loc", "fragment"),
    [
        ({"sequence": "MKXA"}, ["body", "sequence"], "found 'X' at position 3"),
        ({"sequence": "mkta"}, ["body", "sequence"], "uppercase"),
        ({"sequence": "MK TA"}, ["body", "sequence"], "found ' ' at position 3"),
        ({"sequence": "MKBZJ"}, ["body", "sequence"], "(and 2 more)"),
        ({"sequence": ""}, ["body", "sequence"], "at least 1 character"),
        ({"sequence": "A" * 1023}, ["body", "sequence"], "at most 1022 characters"),
        ({}, ["body", "sequence"], "Field required"),
        ({"sequence": "MKTA", "model": "esm2_650m"}, ["body", "model"], "esm2_35m"),
        ({"sequence": "MKTA", "mutations": []}, ["body", "mutations"], "at least 1 item"),
        ({"sequence": "MKTA", "mutations": ["M1A"] * 51}, ["body", "mutations"], "at most 50"),
        ({"sequence": "MKTA", "mutations": ["M1"]}, ["body", "mutations", 0], "e.g. M1A"),
        ({"sequence": "MKTA", "mutations": ["M0A"]}, ["body", "mutations", 0], "not a subst"),
        ({"sequence": "MKTA", "mutations": ["M1M"]}, ["body", "mutations", 0], "wild type equals"),
        ({"sequence": "MKTA", "mutation": ["M1A"]}, ["body", "mutation"], "Extra inputs"),
    ],
)
async def test_score_rejects_invalid_requests(
    client: httpx.AsyncClient, body: dict[str, Any], loc: list[str | int], fragment: str
) -> None:
    response = await client.post("/v1/score", json=body)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    problem = response.json()
    assert problem["type"] == PROBLEM + "invalid-request"
    assert [e["loc"] for e in problem["errors"]] == [loc]
    assert fragment in problem["errors"][0]["msg"]


async def test_score_reports_every_wrong_wild_type(client: httpx.AsyncClient) -> None:
    body = {"sequence": "MKTA", "mutations": ["M1A", "K2A", "A2G", "T9A"]}
    response = await client.post("/v1/score", json=body)
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert [e["loc"] for e in errors] == [["body", "mutations", 2], ["body", "mutations", 3]]
    assert errors[0]["msg"] == (
        "A2G: wild type A does not match the sequence, which has K at position 2"
    )
    assert errors[1]["msg"] == "T9A: position 9 is past the end (4 residues)"


async def test_score_accepts_the_length_limits(client: httpx.AsyncClient) -> None:
    for seq in ("M", "A" * 1022):
        response = await client.post("/v1/score", json={"sequence": seq})
        assert response.status_code == 200, response.text
        assert len(response.json()["scores"]) == 20 * len(seq)


async def test_jobs_reject_150m_and_long_sequences(client: httpx.AsyncClient) -> None:
    wrong = await client.post("/v1/jobs", json={"sequence": SEQ, "model": "esm2_150m"})
    assert wrong.status_code == 422
    assert wrong.json()["errors"][0]["loc"] == ["body", "model"]
    long = await client.post("/v1/jobs", json={"sequence": "A" * 601})
    assert long.status_code == 422
    assert long.json()["type"] == PROBLEM + "sequence-too-long"
    assert "at most 600 residues" in long.json()["detail"]
    invalid = await client.post("/v1/jobs", json={"sequence": "MKXA"})
    assert invalid.json()["errors"][0]["loc"] == ["body", "sequence"]


# -- fast mode ----------------------------------------------------------------------------------


async def test_score_full_landscape(client: httpx.AsyncClient, started: FastAPI) -> None:
    response = await client.post("/v1/score", json={"sequence": SEQ})
    assert response.status_code == 200
    body = response.json()
    assert (body["kind"], body["label"], body["method"]) == (
        "zero_shot",
        "Zero-shot",
        "wt_marginal",
    )
    assert body["model"] == "esm2_150m_onnx_fp32"  # the default
    assert body["revision"].startswith("local:")  # stand-in files, never the Hub revision
    assert body["mutations"] is None and body["aa_order"] == AA_ORDER
    expected = scorer(started, LiveModelName.esm2_150m).landscape(SEQ, fast=True)
    assert body["scores"] == to_grid(expected)
    assert all(body["scores"][i * 20 + AA_ORDER.index(aa)] == 0 for i, aa in enumerate(SEQ))


async def test_score_mutations(client: httpx.AsyncClient) -> None:
    full = (await client.post("/v1/score", json={"sequence": SEQ, "model": "esm2_35m"})).json()
    body = {"sequence": SEQ, "model": "esm2_35m", "mutations": ["M1A", "G76W", "M1A"]}
    response = (await client.post("/v1/score", json=body)).json()
    assert response["scores"] is None and response["model"] == "esm2_35m_onnx_fp32"
    assert response["label"] == "Zero-shot"
    assert [(m["mutation"], m["position"], m["wt"], m["mt"]) for m in response["mutations"]] == [
        ("M1A", 0, "M", "A"),
        ("G76W", 75, "G", "W"),
        ("M1A", 0, "M", "A"),
    ]
    scores = [m["score"] for m in response["mutations"]]
    assert scores == [
        full["scores"][0 * 20 + AA_ORDER.index("A")],
        full["scores"][75 * 20 + AA_ORDER.index("W")],
        full["scores"][0 * 20 + AA_ORDER.index("A")],
    ]


async def test_identical_requests_are_cache_hits(
    client: httpx.AsyncClient, started: FastAPI, settings: Settings
) -> None:
    model = scorer(started, LiveModelName.esm2_35m)
    body = {"sequence": SEQ, "model": "esm2_35m"}
    first = await client.post("/v1/score", json=body)
    computed = model.positions_computed
    assert first.headers["x-mutascope-cache"] == "miss" and computed == len(SEQ)
    second = await client.post("/v1/score", json=body)
    assert second.headers["x-mutascope-cache"] == "hit"
    assert second.content == first.content
    # The same landscape answers a mutations request; nothing more is computed.
    subset = await client.post("/v1/score", json={**body, "mutations": ["M1A"]})
    assert subset.headers["x-mutascope-cache"] == "hit"
    assert model.positions_computed == computed
    # Another model, or another sequence, is another entry.
    other = await client.post("/v1/score", json={"sequence": SEQ, "model": "esm2_150m"})
    assert other.headers["x-mutascope-cache"] == "miss"
    shorter = await client.post("/v1/score", json={**body, "sequence": SEQ[:-1]})
    assert shorter.headers["x-mutascope-cache"] == "miss"
    assert len(started.state.mutascope.score_cache) == 3

    # The cache is on disk: a restarted API answers from it.
    restarted = create_app(settings)
    async with restarted.router.lifespan_context(restarted):
        transport = httpx.ASGITransport(app=restarted)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as again:
            third = await again.post("/v1/score", json=body)
        assert scorer(restarted, LiveModelName.esm2_35m).positions_computed == 0
    assert third.headers["x-mutascope-cache"] == "hit"
    assert third.json()["scores"] == first.json()["scores"]


# -- precise mode -------------------------------------------------------------------------------


async def run_job(client: httpx.AsyncClient, seq: str = SEQ) -> dict[str, Any]:
    created = await client.post("/v1/jobs", json={"sequence": seq})
    assert created.status_code == 202, created.text
    assert created.headers["location"] == f"/v1/jobs/{created.json()['id']}"
    return created.json()


async def test_job_events_arrive_in_order(client: httpx.AsyncClient, started: FastAPI) -> None:
    job = await run_job(client)
    assert job["status"] == "queued" and job["label"] == "Zero-shot"
    stream = await client.get(f"/v1/jobs/{job['id']}/stream")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(stream.text)

    names = [name for name, _, _ in events]
    batches = -(-len(SEQ) // 16)
    assert names == ["queued", "running"] + ["progress"] * batches + ["succeeded"]
    assert [event_id for _, event_id, _ in events] == list(range(len(events)))
    assert all(data["label"] == "Zero-shot" for _, _, data in events)
    progress = [data for name, _, data in events if name == "progress"]
    assert [p["done"] for p in progress] == [min(16 * (i + 1), len(SEQ)) for i in range(batches)]
    assert [r["position"] for p in progress for r in p["rows"]] == list(range(len(SEQ)))

    # The streamed rows are the final landscape, which is the masked marginal.
    final = events[-1][2]
    assert final["status"] == "succeeded" and final["done"] == final["total"] == len(SEQ)
    streamed = [v for p in progress for r in p["rows"] for v in r["scores"]]
    assert final["scores"] == streamed
    expected = scorer(started, LiveModelName.esm2_35m).landscape(SEQ)
    assert final["scores"] == to_grid(expected)
    status = (await client.get(f"/v1/jobs/{job['id']}")).json()
    assert status == final
    assert (status["method"], status["kind"], status["cached"]) == (
        "masked_marginal",
        "zero_shot",
        False,
    )

    # Reconnecting with Last-Event-ID resumes after that event.
    resumed = await client.get(f"/v1/jobs/{job['id']}/stream", headers={"Last-Event-ID": "2"})
    assert [event_id for _, event_id, _ in parse_sse(resumed.text)] == list(range(3, len(events)))


async def test_identical_job_returns_the_cached_result(
    client: httpx.AsyncClient, started: FastAPI
) -> None:
    first = await run_job(client)
    await client.get(f"/v1/jobs/{first['id']}/stream")
    computed = scorer(started, LiveModelName.esm2_35m).positions_computed
    second = await run_job(client)
    assert second["status"] == "succeeded" and second["cached"] is True
    assert second["id"] != first["id"]
    assert second["scores"] == (await client.get(f"/v1/jobs/{first['id']}")).json()["scores"]
    events = parse_sse((await client.get(f"/v1/jobs/{second['id']}/stream")).text)
    assert [name for name, _, _ in events] == ["succeeded"]
    assert scorer(started, LiveModelName.esm2_35m).positions_computed == computed
    # Fast and precise results of one sequence are separate entries.
    fast = await client.post("/v1/score", json={"sequence": SEQ, "model": "esm2_35m"})
    assert fast.headers["x-mutascope-cache"] == "miss"


async def test_queue_full_is_429(
    client: httpx.AsyncClient, started: FastAPI, settings: Settings, gate: threading.Event
) -> None:
    running = await run_job(client, SEQ)
    await eventually(lambda: jobs(started).get(running["id"]).status == "running")
    queued = [await run_job(client, SEQ[: 10 + i]) for i in range(settings.job_queue_size)]
    assert all(job["status"] == "queued" for job in queued)

    full = await client.post("/v1/jobs", json={"sequence": SEQ[:5]})
    assert full.status_code == 429
    assert full.headers["content-type"] == "application/problem+json"
    assert full.json()["type"] == PROBLEM + "queue-full"
    # Fast mode does not queue.
    assert (await client.post("/v1/score", json={"sequence": SEQ[:5]})).status_code == 200

    gate.set()
    await eventually(lambda: all(jobs(started).get(j["id"]).finished for j in queued))
    statuses = [(await client.get(f"/v1/jobs/{j['id']}")).json()["status"] for j in queued]
    assert statuses == ["succeeded"] * settings.job_queue_size
    assert (await client.post("/v1/jobs", json={"sequence": SEQ[:5]})).status_code == 202


async def test_cancel_queued_and_running_jobs(
    client: httpx.AsyncClient, started: FastAPI, gate: threading.Event
) -> None:
    running = await run_job(client, SEQ)
    await eventually(lambda: jobs(started).get(running["id"]).status == "running")
    waiting = await run_job(client, SEQ[:20])
    after = await run_job(client, SEQ[:30])

    cancelled = await client.delete(f"/v1/jobs/{waiting['id']}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["scores"] is None
    stopped = (await client.delete(f"/v1/jobs/{running['id']}")).json()
    assert stopped["status"] == "cancelled" and stopped["expires_at"] is not None

    gate.set()  # the batch in flight finishes and is dropped; the next job runs
    await eventually(lambda: jobs(started).get(after["id"]).status == "succeeded")
    running_events = parse_sse((await client.get(f"/v1/jobs/{running['id']}/stream")).text)
    assert [name for name, _, _ in running_events] == ["queued", "running", "cancelled"]
    final = (await client.get(f"/v1/jobs/{running['id']}")).json()
    assert final["status"] == "cancelled" and final["done"] == 0 and final["scores"] is None
    waiting_events = parse_sse((await client.get(f"/v1/jobs/{waiting['id']}/stream")).text)
    assert [name for name, _, _ in waiting_events] == ["queued", "cancelled"]
    assert (await client.get(f"/v1/jobs/{waiting['id']}")).json()["started_at"] is None

    again = await client.delete(f"/v1/jobs/{running['id']}")
    assert again.status_code == 409 and again.json()["type"] == PROBLEM + "job-finished"
    done = await client.delete(f"/v1/jobs/{after['id']}")
    assert done.status_code == 409
    assert "has succeeded" in done.json()["detail"]
    # A cancelled job's partial result is never cached.
    assert (await run_job(client, SEQ))["cached"] is False


async def test_unknown_job_is_404(client: httpx.AsyncClient) -> None:
    for method, path in (("GET", ""), ("GET", "/stream"), ("DELETE", "")):
        response = await client.request(method, f"/v1/jobs/nope{path}")
        assert response.status_code == 404, (method, path)
        assert response.json()["type"] == PROBLEM + "job-not-found"


async def test_finished_jobs_expire_after_an_hour(
    client: httpx.AsyncClient, started: FastAPI
) -> None:
    job = await run_job(client)
    await client.get(f"/v1/jobs/{job['id']}/stream")
    status = (await client.get(f"/v1/jobs/{job['id']}")).json()
    finished_at = jobs(started).get(job["id"]).finished_at
    assert finished_at is not None
    assert status["expires_at"] == (finished_at + timedelta(hours=1)).isoformat().replace(
        "+00:00", "Z"
    )
    manager = jobs(started)
    manager.now = lambda: finished_at + timedelta(minutes=59)
    assert (await client.get(f"/v1/jobs/{job['id']}")).status_code == 200
    manager.now = lambda: finished_at + timedelta(hours=1)
    assert (await client.get(f"/v1/jobs/{job['id']}")).status_code == 404
    assert len(manager) == 0


async def test_failed_job(
    client: httpx.AsyncClient, started: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("model exploded")

    monkeypatch.setattr(scorer(started, LiveModelName.esm2_35m), "landscape", broken)
    job = await run_job(client)
    events = parse_sse((await client.get(f"/v1/jobs/{job['id']}/stream")).text)
    assert [name for name, _, _ in events] == ["queued", "running", "failed"]
    failed = events[-1][2]
    assert failed["status"] == "failed" and failed["scores"] is None
    assert "model exploded" not in failed["error"]  # no internals in the response


async def test_stream_waits_for_events(
    client: httpx.AsyncClient, started: FastAPI, gate: threading.Event
) -> None:
    """A stream opened before the job runs gets every event as it happens."""
    job = await run_job(client)
    reader = asyncio.create_task(client.get(f"/v1/jobs/{job['id']}/stream"))
    await asyncio.sleep(0.05)
    assert not reader.done()  # the job is held at the gate: the stream is still open
    gate.set()
    events = parse_sse((await asyncio.wait_for(reader, 10)).text)
    assert events[0][0] == "queued" and events[-1][0] == "succeeded"
    assert len([e for e in events if e[0] == "progress"]) == -(-len(SEQ) // 16)

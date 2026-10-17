"""Hardening (ADR 0032): client identity, rate limits, stream slots, CORS, body limit, request
ids, logs without sequences, /metrics, Sentry scrubbing and the single-process rule.

Requests come from an in-process transport whose socket address each test chooses
(``httpx.ASGITransport(client=...)``), so the trusted-proxy rule is tested with both a proxy
and a direct client sending a spoofed ``X-Forwarded-For``.
"""

import asyncio
import functools
import hashlib
import json
import logging
import re
import threading
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from ipaddress import ip_network
from pathlib import Path
from typing import Any

import httpx
import pytest
import sentry_sdk
from fastapi import FastAPI
from pydantic import SecretStr, ValidationError
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from api.app.clients import client_address
from api.app.live import LiveModelName
from api.app.main import create_app
from api.app.metrics import token_matches
from api.app.middleware import request_id_from
from api.app.process import ProcessLock, SecondProcessError
from api.app.ratelimit import StreamSlots
from api.app.sentry import init_sentry, scrub_event
from api.app.serve import ConfigurationError, uvicorn_config
from api.app.settings import Settings

pytestmark = pytest.mark.anyio

SEQ = (
    "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKWKRQTL"
    "GQHDFSAGEGLYTHMKALRPDEDRLSPLHSVYVDQWDW"
)
"""119 residues: long enough that a leak is unmistakable, short enough to score quickly."""
MUTATIONS = ["M1A", "K2R", "T3W"]
PROBLEM = "urn:mutascope:problem:"
TOKEN = "t" * 40
PROXY = "10.1.2.3"
"""The (simulated) Space proxy: ``trusted_proxies = 10.0.0.0/8``."""


def settings_for(base: Settings, **overrides: Any) -> Settings:
    return base.model_copy(update=overrides)


@asynccontextmanager
async def serve(settings: Settings) -> AsyncGenerator[FastAPI]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


def client_for(app: FastAPI, peer: str = "203.0.113.9", **kwargs: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(peer, 50000), **kwargs)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def assert_problem(response: httpx.Response, status: int, slug: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == PROBLEM + slug and body["status"] == status
    return body


async def eventually(check: Callable[[], bool], seconds: float = 10) -> None:
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


# -- client identity ------------------------------------------------------------------------------

TRUSTED = (ip_network("10.0.0.0/8"),)


def test_socket_address_without_trusted_proxies() -> None:
    # A spoofed header from a direct client is ignored.
    assert client_address("203.0.113.9", ["1.2.3.4"], ()) == "203.0.113.9"
    assert client_address("203.0.113.9", ["1.2.3.4"], TRUSTED) == "203.0.113.9"
    assert client_address("203.0.113.9", [], TRUSTED) == "203.0.113.9"
    assert client_address(None, ["1.2.3.4"], TRUSTED) == "unknown"


def test_forwarded_for_from_a_trusted_proxy() -> None:
    assert client_address(PROXY, ["198.51.100.7"], TRUSTED) == "198.51.100.7"
    # The client prepended a spoofed entry; the proxy appended the real address.
    assert client_address(PROXY, ["1.2.3.4, 198.51.100.7"], TRUSTED) == "198.51.100.7"
    # Two trusted hops, and the header split over two lines.
    assert client_address(PROXY, ["1.2.3.4, 198.51.100.7", "10.9.9.9"], TRUSTED) == ("198.51.100.7")
    # No header: the proxy itself. Garbage at the end: stop at the last trusted hop.
    assert client_address(PROXY, [], TRUSTED) == PROXY
    assert client_address(PROXY, ["198.51.100.7, not-an-ip"], TRUSTED) == PROXY
    # IPv6 is normalised, so one address is one key.
    assert client_address(PROXY, ["2001:DB8:0:0::1"], TRUSTED) == "2001:db8::1"


def test_trusted_proxies_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUTASCOPE_API_TRUSTED_PROXIES", "10.0.0.0/8, 127.0.0.1")
    monkeypatch.setenv("MUTASCOPE_API_CORS_ORIGINS", "https://site.example,http://localhost:3000")
    settings = Settings()
    assert settings.trusted_proxies == (ip_network("10.0.0.0/8"), ip_network("127.0.0.1/32"))
    assert settings.cors_origins == ("https://site.example", "http://localhost:3000")
    monkeypatch.setenv("MUTASCOPE_API_RATE_LIMIT_SCORE", "20 per fortnight")
    with pytest.raises(ValidationError):
        Settings()


# -- rate limits ----------------------------------------------------------------------------------


@pytest.fixture
def strict(settings: Settings) -> Settings:
    """Small limits, the simulated proxy trusted."""
    return settings_for(
        settings,
        rate_limit_read="3/minute",
        rate_limit_score="2/minute",
        rate_limit_score_global="1000/minute",
        rate_limit_jobs="1000/minute",
        trusted_proxies=TRUSTED,
    )


async def test_read_limit_per_client_with_retry_after(strict: Settings) -> None:
    async with serve(strict) as app, client_for(app) as client:
        for _ in range(3):
            assert (await client.get("/v1/benchmarks")).status_code == 200
        limited = await client.get("/v1/proteins")  # one limit over every read route
        body = assert_problem(limited, 429, "rate-limited")
        assert "3/minute" in body["detail"]
        assert 1 <= int(limited.headers["retry-after"]) <= 60
        # Probes are never limited.
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200


async def test_spoofed_forwarded_for_from_an_untrusted_client(strict: Settings) -> None:
    """A direct client cannot get a fresh quota by sending a new X-Forwarded-For each time."""
    async with serve(strict) as app, client_for(app, peer="203.0.113.9") as client:
        statuses = [
            (
                await client.get("/v1/benchmarks", headers={"X-Forwarded-For": f"1.1.1.{i}"})
            ).status_code
            for i in range(5)
        ]
    assert statuses == [200, 200, 200, 429, 429]


async def test_forwarded_clients_behind_the_trusted_proxy(strict: Settings) -> None:
    async with serve(strict) as app, client_for(app, peer=PROXY) as client:

        async def read(forwarded: str) -> int:
            headers = {"X-Forwarded-For": forwarded}
            return (await client.get("/v1/benchmarks", headers=headers)).status_code

        assert [await read("198.51.100.1") for _ in range(4)] == [200, 200, 200, 429]
        # Another client behind the same proxy has its own quota...
        assert await read("198.51.100.2") == 200
        # ...and a client that prepends a fake address is still itself.
        assert await read("1.2.3.4, 198.51.100.1") == 429


async def test_score_limits_per_client_and_global(strict: Settings) -> None:
    body = {"sequence": SEQ[:30], "model": "esm2_35m"}
    async with serve(strict) as app, client_for(app, peer=PROXY) as client:

        async def post(forwarded: str) -> httpx.Response:
            return await client.post("/v1/score", json=body, headers={"X-Forwarded-For": forwarded})

        assert [(await post("198.51.100.1")).status_code for _ in range(2)] == [200, 200]
        assert_problem(await post("198.51.100.1"), 429, "rate-limited")
        assert (await post("198.51.100.2")).status_code == 200

    shared = settings_for(
        strict, rate_limit_score="1000/minute", rate_limit_score_global="3/minute"
    )
    async with serve(shared) as app, client_for(app, peer=PROXY) as client:
        codes = [
            (
                await client.post(
                    "/v1/score", json=body, headers={"X-Forwarded-For": f"198.51.100.{i}"}
                )
            ).status_code
            for i in range(5)
        ]
        assert codes == [200, 200, 200, 429, 429]  # many addresses, one global budget


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[FastAPI], threading.Event]]:
    """Hold the app's 35M forward batches until the returned event is set."""
    gates: list[threading.Event] = []

    def hold(app: FastAPI) -> threading.Event:
        scorer = app.state.mutascope.live[LiveModelName.esm2_35m].scorer
        original = scorer.landscape
        gate = threading.Event()
        gates.append(gate)

        def wait(*args: Any, **kwargs: Any) -> Any:
            if not gate.wait(timeout=30):
                raise TimeoutError("gate never opened")
            return original(*args, **kwargs)

        monkeypatch.setattr(scorer, "landscape", wait)
        return gate

    yield hold
    for gate in gates:
        gate.set()


async def test_job_rate_limit_and_one_active_job_per_client(
    settings: Settings, held: Callable[[FastAPI], threading.Event]
) -> None:
    limited = settings_for(
        settings, rate_limit_jobs="3/minute", max_active_jobs_per_client=1, trusted_proxies=TRUSTED
    )
    async with serve(limited) as app, client_for(app, peer=PROXY) as client:
        gate = held(app)

        async def submit(sequence: str, forwarded: str) -> httpx.Response:
            headers = {"X-Forwarded-For": forwarded}
            return await client.post("/v1/jobs", json={"sequence": sequence}, headers=headers)

        first = await submit(SEQ[:20], "198.51.100.1")
        assert first.status_code == 202
        # A second job while the first is active: refused, though the queue has room.
        assert_problem(await submit(SEQ[:21], "198.51.100.1"), 429, "too-many-jobs")
        # Someone else's job is fine.
        assert (await submit(SEQ[:22], "198.51.100.2")).status_code == 202
        gate.set()
        await eventually(
            lambda: (
                len(app.state.mutascope.jobs) == 2
                and not app.state.mutascope.jobs.running
                and not app.state.mutascope.jobs.waiting
            )
        )
        # The first finished: a new job is allowed, until the rate limit (3/minute; the
        # refused one counted) says no.
        assert (await submit(SEQ[:23], "198.51.100.1")).status_code == 202
        assert_problem(await submit(SEQ[:24], "198.51.100.1"), 429, "rate-limited")


async def test_open_streams_per_client(
    settings: Settings, held: Callable[[FastAPI], threading.Event]
) -> None:
    limited = settings_for(settings, max_streams_per_client=2, trusted_proxies=TRUSTED)
    async with serve(limited) as app, client_for(app, peer=PROXY) as client:
        gate = held(app)
        slots: StreamSlots = app.state.stream_slots
        job = (await client.post("/v1/jobs", json={"sequence": SEQ[:40]})).json()
        url = f"/v1/jobs/{job['id']}/stream"
        alice = {"X-Forwarded-For": "198.51.100.1"}
        readers = [asyncio.create_task(client.get(url, headers=alice)) for _ in range(2)]
        await eventually(lambda: slots.count == 2)
        assert_problem(await client.get(url, headers=alice), 429, "too-many-streams")
        bob = asyncio.create_task(client.get(url, headers={"X-Forwarded-For": "198.51.100.2"}))
        await eventually(lambda: slots.count == 3)
        # A client that goes away gives its slot back.
        readers[0].cancel()
        await eventually(lambda: slots.count == 2)
        gate.set()
        done = await asyncio.wait_for(asyncio.gather(readers[1], bob), 10)
        assert all(r.status_code == 200 and "event: succeeded" in r.text for r in done)
        assert slots.count == 0


def test_stream_slots_total() -> None:
    slots = StreamSlots(per_client=5, total=2)
    slots.acquire("a")
    slots.acquire("b")
    with pytest.raises(Exception, match="on the server"):
        slots.acquire("c")
    slots.release("a")
    slots.acquire("c")
    assert slots.count == 2


# -- CORS, body size, request ids -----------------------------------------------------------------


async def test_cors_only_for_configured_origins(client: httpx.AsyncClient) -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    allowed = await client.options(
        "/v1/score", headers={"Origin": "http://localhost:3000", **preflight}
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    denied = await client.options(
        "/v1/score", headers={"Origin": "https://evil.example", **preflight}
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers
    simple = await client.get("/v1/benchmarks", headers={"Origin": "http://localhost:3000"})
    assert simple.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-request-id" in simple.headers["access-control-expose-headers"].lower()
    other = await client.get("/v1/benchmarks", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


async def test_body_limit_is_a_413_problem(settings: Settings) -> None:
    small = settings_for(settings, max_body_bytes=1024)
    async with serve(small) as app, client_for(app) as client:
        ok = await client.post("/v1/score", json={"sequence": SEQ[:30]})
        assert ok.status_code == 200
        big = {"sequence": "A" * 2000}
        declared = await client.post("/v1/score", json=big)
        assert_problem(declared, 413, "body-too-large")
        assert "1024 bytes" in declared.json()["detail"]

        async def chunks() -> AsyncIterator[bytes]:  # no Content-Length: counted as read
            payload = json.dumps(big).encode()
            for i in range(0, len(payload), 256):
                yield payload[i : i + 256]

        chunked = await client.post(
            "/v1/jobs", content=chunks(), headers={"Content-Type": "application/json"}
        )
        assert_problem(chunked, 413, "body-too-large")
        assert "x-request-id" in chunked.headers


def test_request_id_format() -> None:
    for good in ("3f2b8c1e9a7d4e6f8b0c2d4e6f8a0b1c", "6ba7b810-9dad-11d1-80b4-00c04fd430c8"):
        assert request_id_from(good) == good
    for bad in (None, "", "short", "x" * 65, "has space inside", 'quote"inject', "-leading"):
        generated = request_id_from(bad)
        assert generated != bad and re.fullmatch(r"[0-9a-f]{32}", generated)


async def test_request_id_header_and_log_lines(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    mine = "client-req-0001"
    echoed = await client.get("/v1/benchmarks", headers={"X-Request-ID": mine})
    assert echoed.headers["x-request-id"] == mine
    replaced = await client.get("/v1/benchmarks", headers={"X-Request-ID": "bad id\r\n"})
    new_id = replaced.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", new_id)
    problem = await client.get("/v1/proteins/NOPE")
    assert problem.status_code == 404 and "x-request-id" in problem.headers
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    access = [line for line in lines if line["event"] == "request"]
    assert [line["request_id"] for line in access] == [
        mine,
        new_id,
        problem.headers["x-request-id"],
    ]
    assert all(line["status"] in (200, 404) and "ms" in line for line in access)


# -- logs never carry a sequence ------------------------------------------------------------------


async def test_no_sequence_or_mutation_in_any_log(
    started: FastAPI,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every live route, success and failure, with a sequence that must never be logged.

    The output checked is what the API writes (stderr: structlog and the standard library's
    loggers through the API's JSON handler), not pytest's own copy of log records.
    """
    capsys.readouterr()
    async with client_for(started, raise_app_exceptions=False) as client:
        await client.post("/v1/score", json={"sequence": SEQ})
        await client.post("/v1/score", json={"sequence": SEQ, "mutations": MUTATIONS})
        await client.post("/v1/score", json={"sequence": SEQ, "mutations": ["A1G"]})  # 422
        await client.post("/v1/score", json={"sequence": SEQ + "X"})  # 422
        job = (await client.post("/v1/jobs", json={"sequence": SEQ})).json()
        await client.get(f"/v1/jobs/{job['id']}/stream")
        await client.get(f"/v1/jobs/{job['id']}")

        # Failures whose exception text holds the sequence: an unhandled 500 and a failed job.
        def explode(sequence: str, **_: Any) -> Any:
            raise RuntimeError(f"cannot score {sequence} with {MUTATIONS}")

        for key in LiveModelName:
            monkeypatch.setattr(started.state.mutascope.live[key].scorer, "landscape", explode)
        crashed = await client.post("/v1/score", json={"sequence": SEQ[::-1], "model": "esm2_35m"})
        assert crashed.status_code == 500
        failed = (await client.post("/v1/jobs", json={"sequence": SEQ[1:]})).json()
        await client.get(f"/v1/jobs/{failed['id']}/stream")
    try:
        raise ValueError(f"a library fails on {SEQ}")
    except ValueError:
        logging.getLogger("uvicorn.error").exception("a library logs %s", SEQ)

    output = capsys.readouterr().err
    assert (
        '"score_request"' in output and '"job_failed"' in output and '"unhandled_error"' in output
    )
    assert hashlib.sha256(SEQ.encode()).hexdigest() in output  # the lines are there, hashed
    for secret in (SEQ, SEQ[::-1], SEQ[1:], SEQ[10:40], *MUTATIONS):
        assert secret not in output, secret


# -- /metrics -------------------------------------------------------------------------------------


async def test_metrics_needs_the_token(settings: Settings) -> None:
    async with serve(settings) as app, client_for(app) as client:
        assert (await client.get("/metrics")).status_code == 404  # no token: the route is off
    with_token = settings_for(settings, metrics_token=SecretStr(TOKEN))
    async with serve(with_token) as app, client_for(app) as client:
        missing = await client.get("/metrics")
        assert_problem(missing, 401, "unauthorized")
        assert missing.headers["www-authenticate"] == "Bearer"
        wrong = await client.get("/metrics", headers={"Authorization": "Bearer " + "x" * 40})
        assert wrong.status_code == 401
        await client.post("/v1/score", json={"sequence": SEQ[:30], "model": "esm2_35m"})
        await client.post("/v1/score", json={"sequence": SEQ[:30], "model": "esm2_35m"})
        scraped = await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert scraped.status_code == 200
        text = scraped.text
        for line in (
            'mutascope_score_cache_requests_total{method="wt",result="miss"} 1.0',
            'mutascope_score_cache_requests_total{method="wt",result="hit"} 1.0',
            'mutascope_inference_seconds_count{method="wt",model="esm2_35m"} 1.0',
            "mutascope_job_queue_length 0.0",
            "mutascope_sse_streams_open 0.0",
        ):
            assert line in text, line
        assert 'http_request_duration_seconds_count{handler="/v1/score",method="POST"} 2.0' in text
        schema = (await client.get("/openapi.json")).json()
        assert "/metrics" not in schema["paths"]


def test_token_comparison() -> None:
    assert token_matches(f"Bearer {TOKEN}", TOKEN)
    assert token_matches(f"bearer {TOKEN}", TOKEN)
    assert not token_matches(TOKEN, TOKEN)  # no scheme
    assert not token_matches(f"Bearer {TOKEN[:-1]}", TOKEN)
    assert not token_matches(None, TOKEN)
    with pytest.raises(ValidationError):
        Settings(metrics_token=SecretStr("too-short"))


# -- Sentry ---------------------------------------------------------------------------------------


class CapturingTransport(Transport):
    def __init__(self, options: Any = None) -> None:
        super().__init__(options)
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))


def test_sentry_off_without_dsn(settings: Settings) -> None:
    assert init_sentry(settings) is False


def test_scrub_event_drops_bodies_and_sequences() -> None:
    event = {
        "request": {
            "url": "http://test/v1/score",
            "data": {"sequence": SEQ},
            "cookies": {"a": "b"},
            "headers": {"Authorization": "Bearer x", "X-Forwarded-For": "1.2.3.4", "Accept": "*/*"},
        },
        "exception": {"values": [{"type": "RuntimeError", "value": f"cannot score {SEQ}"}]},
    }
    scrubbed = scrub_event(event)
    assert "data" not in scrubbed["request"] and "cookies" not in scrubbed["request"]
    assert scrubbed["request"]["headers"] == {"Accept": "*/*"}
    assert SEQ not in json.dumps(scrubbed)


async def test_sentry_event_of_a_500_has_no_body(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = CapturingTransport()
    monkeypatch.setattr(sentry_sdk, "init", functools.partial(sentry_sdk.init, transport=transport))
    configured = settings_for(
        settings, sentry_dsn=SecretStr("https://public@o0.ingest.sentry.io/1")
    )
    try:
        async with serve(configured) as app:
            assert app.state.sentry is True

            def explode(sequence: str, **_: Any) -> Any:
                raise RuntimeError(f"cannot score {sequence}")

            scorer = app.state.mutascope.live[LiveModelName.esm2_35m].scorer
            monkeypatch.setattr(scorer, "landscape", explode)
            async with client_for(app, raise_app_exceptions=False) as client:
                crashed = await client.post(
                    "/v1/score", json={"sequence": SEQ, "model": "esm2_35m"}
                )
                assert crashed.status_code == 500
                # A failed job is no request error; the worker reports it itself.
                job = (await client.post("/v1/jobs", json={"sequence": SEQ[1:]})).json()
                await client.get(f"/v1/jobs/{job['id']}/stream")
            sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.get_global_scope().set_client(None)
    errors = [e for e in transport.events if e.get("exception")]
    assert len(errors) == 2  # the 500 once (not twice), the failed job once
    for event in errors:
        assert "data" not in event.get("request", {})
        text = json.dumps(event, default=str)
        assert SEQ not in text and SEQ[1:] not in text and "redacted" in text


# -- one process ----------------------------------------------------------------------------------


def test_process_lock(tmp_path: Path) -> None:
    first, second = ProcessLock(tmp_path / "api.lock"), ProcessLock(tmp_path / "api.lock")
    first.acquire()
    with pytest.raises(SecondProcessError, match="one process"):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


async def test_second_server_refuses_to_start(started: FastAPI, settings: Settings) -> None:
    other = create_app(settings)  # same lock file as the running app
    with pytest.raises(SecondProcessError):
        async with other.router.lifespan_context(other):
            pass


def test_start_command_runs_one_worker(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    config = uvicorn_config(settings)
    assert config.workers == 1 and not isinstance(config.app, str)  # an object cannot fork
    assert config.proxy_headers is False and config.access_log is False
    assert config.limit_concurrency == settings.max_connections
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    with pytest.raises(ConfigurationError, match="one process"):
        uvicorn_config(settings)

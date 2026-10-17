"""Property-based fuzzing of every route against the API's own OpenAPI schema (schemathesis).

The API runs in a real uvicorn server in a background thread, with its ``lifespan`` (bundle
fixture, stand-in ONNX graphs, job worker), and schemathesis sends requests generated from the
schema, valid and invalid, to every operation. Each response must:

- never be a 5xx (``not_a_server_error``);
- have a status code, content type, headers and body the schema documents for that operation;
- reject a request that violates the schema with a 4xx (``negative_data_rejection``).

Left out: ``positive_data_acceptance`` (a request that matches the schema must succeed). Some
rules cannot be written in OpenAPI: a mutation's wild type must match the sequence, precise
mode has a length limit from the settings, and an unknown job id is 404. Those requests are
valid by schema and correctly 404 / 422, and the tests in ``test_live.py`` cover them.
"""

import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import schemathesis
import uvicorn
from schemathesis import checks

from api.app.main import create_app
from api.app.settings import Settings
from api.tests.conftest import LOOSE_LIMITS


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def base_url(
    bundle_dir: Any, onnx_dir: Any, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[str]:
    """A served API (module scope: one startup for every generated case)."""
    settings = Settings(
        bundle_dir=bundle_dir,
        onnx_local_dir=onnx_dir,
        score_cache_dir=(cache := tmp_path_factory.mktemp("score-cache")),
        lock_file=cache / "api.lock",
        job_queue_size=2,
        **LOOSE_LIMITS,  # pyright: ignore[reportArgumentType]
    )
    port = _free_port()
    config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(600):
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("the API did not start")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def api_schema(base_url: str) -> Any:
    return schemathesis.openapi.from_url(f"{base_url}/openapi.json")


schema = schemathesis.pytest.from_fixture("api_schema")

EXCLUDED = [checks.positive_data_acceptance]  # see the module docstring


@schema.parametrize()  # pyright: ignore[reportUnknownMemberType, reportUntypedFunctionDecorator]
def test_api_conforms_to_its_schema(case: Any) -> None:
    case.call_and_validate(excluded_checks=EXCLUDED, timeout=30)

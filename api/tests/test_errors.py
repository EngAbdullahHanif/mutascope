"""Every error is RFC 9457 problem details (``application/problem+json``)."""

from typing import Any

import httpx
import pytest

from api.app.main import create_app
from api.app.settings import Settings
from api.tests.conftest import NO_STRUCTURE, SUPERVISED

pytestmark = pytest.mark.anyio

PROBLEM_KEYS = {"type", "title", "status", "detail", "instance"}


def _problem(response: httpx.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    body: dict[str, Any] = response.json()
    assert body.keys() >= PROBLEM_KEYS and body["status"] == status
    assert body["instance"] == response.request.url.path
    return body


async def test_unknown_protein_is_404(client: httpx.AsyncClient) -> None:
    for url in ("/v1/proteins/NOT_AN_ASSAY", "/v1/proteins/NOT_AN_ASSAY/landscape"):
        body = _problem(await client.get(url), 404)
        assert body["type"] == "urn:mutascope:problem:protein-not-found"
        assert "NOT_AN_ASSAY" in body["detail"]


async def test_saprot_without_a_structure_is_404_with_the_reason(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(f"/v1/proteins/{NO_STRUCTURE}/landscape?model=saprot")
    body = _problem(response, 404)
    assert body["type"] == "urn:mutascope:problem:model-not-available"
    assert "no AF2 structure" in body["detail"] and "213 of 217" in body["detail"]


@pytest.mark.parametrize(
    ("url", "field"),
    [
        (f"/v1/proteins/{SUPERVISED}/landscape?model=esm1v", "model"),
        ("/v1/proteins?function_type=Folding", "function_type"),
        ("/v1/proteins?q=", "q"),
        (f"/v1/proteins?q={'x' * 101}", "q"),
    ],
)
async def test_bad_parameters_are_422(client: httpx.AsyncClient, url: str, field: str) -> None:
    body = _problem(await client.get(url), 422)
    assert body["type"] == "urn:mutascope:problem:invalid-request"
    assert [e["loc"] for e in body["errors"]] == [["query", field]]
    assert field in body["detail"]


async def test_routing_errors(client: httpx.AsyncClient) -> None:
    body = _problem(await client.get("/v2/proteins"), 404)
    assert body["type"] == "about:blank" and body["title"] == "Not Found"
    body = _problem(await client.post("/v1/proteins"), 405)
    assert body["title"] == "Method Not Allowed"


async def test_unhandled_errors_are_500_problems(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("internal detail")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = _problem(await client.get("/boom"), 500)
    assert "internal detail" not in body["detail"]


def test_openapi_documents_problem_responses() -> None:
    schema = create_app().openapi()
    landscape = schema["paths"]["/v1/proteins/{assay_id}/landscape"]["get"]["responses"]
    for status in ("404", "422", "503"):
        content = landscape[status]["content"]
        assert content == {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
        }
    assert "HTTPValidationError" not in schema["components"]["schemas"]

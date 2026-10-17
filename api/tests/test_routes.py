"""Routes against the fixture bundle: listing, detail, benchmarks, probes, caching headers."""

import gzip
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from api.app.main import create_app
from api.app.settings import Settings
from api.tests.conftest import NO_STRUCTURE, SUPERVISED, ZERO_SHOT

pytestmark = pytest.mark.anyio


async def test_ready_reports_the_loaded_bundle_and_models(client: httpx.AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "schema_version": "1.0.0",
        "bundle_revision": None,  # a local copy
        "proteins": 217,
        "models": ["esm2_150m_onnx_fp32", "esm2_35m_onnx_fp32"],
        "score_default_model": "esm2_150m",
        "precise_max_length": 600,
    }
    assert "etag" not in response.headers


async def test_ready_is_503_before_startup(settings: Settings) -> None:
    transport = httpx.ASGITransport(app=create_app(settings))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).status_code == 200
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "urn:mutascope:problem:not-ready"


async def test_list_proteins(client: httpx.AsyncClient, bundle_dir: Path) -> None:
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    body = (await client.get("/v1/proteins")).json()
    assert body["count"] == len(body["proteins"]) == 217
    first = body["proteins"][0]
    assert first["default_model"] == manifest["proteins"][0]["default_model"]
    assert first["label"] == manifest["proteins"][0]["label"]
    assert body["version"]["bundle"] == manifest["version"]
    labels = {p["label"] for p in body["proteins"]}
    assert "Zero-shot" in labels and any(label.startswith("Supervised (n = ") for label in labels)


async def test_filter_by_function_type_and_search(client: httpx.AsyncClient) -> None:
    binding = (await client.get("/v1/proteins", params={"function_type": "Binding"})).json()
    assert binding["count"] == 13
    assert {p["function_type"] for p in binding["proteins"]} == {"Binding"}
    found = (await client.get("/v1/proteins", params={"q": "zika"})).json()
    assert [p["assay_id"] for p in found["proteins"]] == ["A0A140D2T1_ZIKV_Sourisseau_2019"]
    both = await client.get("/v1/proteins", params={"q": "p53_human", "function_type": "Binding"})
    assert both.json()["count"] == 0


async def test_protein_detail(client: httpx.AsyncClient) -> None:
    supervised = (await client.get(f"/v1/proteins/{SUPERVISED}")).json()
    assert supervised["default"] == {
        "model": "ridge_seq_struct",
        "kind": "supervised",
        "label": "Supervised (n = 621)",
        "reason": None,
    }
    assert supervised["assay"]["length"] == 37
    assert [m["model"] for m in supervised["models"]] == ["default", "esm2_650m", "saprot"]

    zero_shot = (await client.get(f"/v1/proteins/{ZERO_SHOT}")).json()
    assert zero_shot["default"]["label"] == "Zero-shot"
    assert zero_shot["default"]["reason"] == "fewer than 300 measured variants (37)"

    no_structure = (await client.get(f"/v1/proteins/{NO_STRUCTURE}")).json()
    assert no_structure["default"]["reason"] == "no structure"
    assert [m["model"] for m in no_structure["models"]] == ["default", "esm2_650m"]


async def test_benchmarks_are_the_bundle_file(client: httpx.AsyncClient, bundle_dir: Path) -> None:
    body = (await client.get("/v1/benchmarks")).json()
    assert body["benchmarks"] == json.loads((bundle_dir / "benchmarks.json").read_text())


async def test_etag_and_cache_control(client: httpx.AsyncClient, settings: Settings) -> None:
    url = f"/v1/proteins/{SUPERVISED}/landscape"
    first = await client.get(url)
    etag = first.headers["etag"]
    assert etag.startswith('W/"')
    assert first.headers["cache-control"] == settings.cache_control
    again = await client.get(url, headers={"If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == etag
    stale = await client.get(url, headers={"If-None-Match": 'W/"other", "x"'})
    assert stale.status_code == 200 and stale.headers["etag"] == etag
    other = await client.get(f"{url}?model=esm2_650m")
    assert other.headers["etag"] != etag
    assert "etag" not in (await client.get("/health")).headers


async def test_errors_are_not_cached(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/proteins/NOPE")
    assert response.status_code == 404
    assert "etag" not in response.headers and "cache-control" not in response.headers


async def test_gzip(client: httpx.AsyncClient) -> None:
    response = await client.get(
        f"/v1/proteins/{SUPERVISED}/landscape", headers={"Accept-Encoding": "gzip"}
    )
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["kind"] == "supervised"  # httpx decodes it


async def test_protein_files_are_parsed_once(started: FastAPI) -> None:
    bundle = started.state.mutascope.bundle
    transport = httpx.ASGITransport(app=started)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(3):
            await client.get(f"/v1/proteins/{SUPERVISED}/landscape")
    assert bundle.protein.cache_info().misses == 1


def test_fixture_bundle_is_the_published_one(bundle_dir: Path) -> None:
    """The fixture's protein files are gzip JSON at schema 1.0.0 (sanity for the tests above)."""
    for path in (bundle_dir / "proteins").iterdir():
        assert json.loads(gzip.decompress(path.read_bytes()))["schema_version"] == "1.0.0"

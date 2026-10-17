"""The live fp32 ONNX scores agree with our PyTorch landscapes of a ProteinGym protein.

The API is started with the real fp32 models from the pinned Hub revision (``slow``: about
730 MB on first download) and scores the bundle's ``TCRG1_MOUSE_Tsuboyama_2023_1E0L`` target
through its own routes: fast mode on 35M and 150M (``POST /v1/score``) and precise mode on 35M
(``POST /v1/jobs`` and its stream). Each must reach a Spearman of at least 0.99 with the golden
landscape of the same model and method, our PyTorch scores read from the pipeline cache
(``make_live_golden``), over every substitution except the wild type (0 in both by
construction, so they would only inflate the agreement). The threshold is the model gate's
per-assay one (ADR 0010, 0029).
"""

import gzip
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi import FastAPI

from api.app.main import create_app
from api.app.settings import Settings
from api.tests.conftest import SUPERVISED
from api.tests.make_live_golden import golden_path
from api.tests.test_live import parse_sse
from mutascope.alphabet import AA_ORDER
from mutascope.eval.metrics import FloatArray, spearman
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

pytestmark = [pytest.mark.anyio, pytest.mark.slow]

MIN_SPEARMAN = 0.99


@pytest.fixture
async def hub_client(bundle_dir: Path, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """The API with the pinned Hub fp32 models (cache in a fresh directory)."""
    app: FastAPI = create_app(Settings(bundle_dir=bundle_dir, score_cache_dir=tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _substitutions(grid: list[float | None], seq: str) -> FloatArray:
    values = np.array(grid, dtype=np.float64).reshape(len(seq), len(AA_ORDER))
    keep = np.ones_like(values, dtype=bool)
    keep[np.arange(len(seq)), [AA_ORDER.index(aa) for aa in seq]] = False
    return values[keep]


async def test_live_scores_agree_with_pytorch(
    hub_client: httpx.AsyncClient, bundle_dir: Path
) -> None:
    golden = json.loads(golden_path().read_text())
    raw = json.loads(
        gzip.decompress((bundle_dir / "proteins" / f"{SUPERVISED}.json.gz").read_bytes())
    )
    seq = golden["target_seq"]
    assert seq == raw["assay"]["target_seq"]  # the bundle's protein

    live: dict[str, list[float | None]] = {}
    for model in ("esm2_35m", "esm2_150m"):
        response = await hub_client.post("/v1/score", json={"sequence": seq, "model": model})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["revision"] == Settings().onnx_revision and body["label"] == "Zero-shot"
        live[f"{model}_wt"] = body["scores"]
    created = (await hub_client.post("/v1/jobs", json={"sequence": seq})).json()
    events = parse_sse((await hub_client.get(f"/v1/jobs/{created['id']}/stream")).text)
    assert events[-1][0] == "succeeded"
    live["esm2_35m_masked"] = events[-1][2]["scores"]

    agreement = {
        key: spearman(_substitutions(golden["landscapes"][key], seq), _substitutions(scores, seq))
        for key, scores in live.items()
    }
    log.info("live_agreement", **agreement)  # the numbers in ADR 0031
    assert all(rho >= MIN_SPEARMAN for rho in agreement.values()), agreement

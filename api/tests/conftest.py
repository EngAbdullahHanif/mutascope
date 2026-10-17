"""Shared API fixtures: a local bundle copied from the pinned Hub revision, stand-in ONNX models.

The bundle fixture holds the real ``SHA256SUMS``, ``manifest.json`` and ``benchmarks.json`` of
``export.publish.revision`` and three small protein files, one per kind of default model, so
golden values are the published ones (about 0.5 MB, cached by the Hugging Face cache). The
ONNX "models" are the stand-in graphs of ``tests/scoring/test_onnx_esm2.py`` in the export's
layout, so the real configs (``conf/model/esm2_*_onnx_fp32.yaml``) load them.
"""

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from huggingface_hub import hf_hub_download  # pyright: ignore[reportUnknownVariableType]
from tests.scoring.test_esm2 import ESM_VOCAB
from tests.scoring.test_onnx_esm2 import standin_graph

from api.app.main import create_app
from api.app.settings import Settings
from mutascope.export.onnx import esm_tokenizer

SUPERVISED = "TCRG1_MOUSE_Tsuboyama_2023_1E0L"
"""Smallest supervised assay: 37 residues, ``Supervised (n = 621)``."""
ZERO_SHOT = "F7YBW8_MESOW_Aakre_2015"
"""Zero-shot default (SaProt): 37 measured variants, under the 300 a supervised model needs."""
NO_STRUCTURE = "P53_HUMAN_Giacomelli_2018_WT_Nutlin"
"""No AF2 structure: ESM-2 650M is the default and SaProt has no landscape."""
FIXTURE_PROTEINS = (SUPERVISED, ZERO_SHOT, NO_STRUCTURE)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def bundle_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A local bundle: the pinned revision's top-level files and three protein files."""
    settings = Settings()
    root = tmp_path_factory.mktemp("bundle")
    names = ["SHA256SUMS", "manifest.json", "benchmarks.json"]
    names += [f"proteins/{assay}.json.gz" for assay in FIXTURE_PROTEINS]
    for name in names:
        path = hf_hub_download(
            settings.bundle_repo,
            f"{settings.bundle_path}/{name}",
            repo_type="dataset",
            revision=settings.bundle_revision,
        )
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, root / name)
    return root


@pytest.fixture(scope="session")
def onnx_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Stand-in graphs at the paths the fp32 configs name (``<model>/fp32/model.onnx``)."""
    root = tmp_path_factory.mktemp("onnx")
    tokenizer = esm_tokenizer(ESM_VOCAB)
    for source in ("esm2_35m", "esm2_150m"):
        (root / source / "fp32").mkdir(parents=True)
        standin_graph(root / source / "fp32" / "model.onnx", 1)
        tokenizer.save(str(root / source / "tokenizer.json"))
    return root


@pytest.fixture
def settings(bundle_dir: Path, onnx_dir: Path, tmp_path: Path) -> Settings:
    return Settings(
        bundle_dir=bundle_dir, onnx_local_dir=onnx_dir, score_cache_dir=tmp_path / "score-cache"
    )


@pytest.fixture
async def started(settings: Settings) -> AsyncIterator[FastAPI]:
    """The app after its lifespan startup."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(started: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=started)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

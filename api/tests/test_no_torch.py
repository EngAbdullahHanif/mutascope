"""The API starts and serves without importing torch or transformers (ADR 0029's test pattern).

The app is started (lifespan: bundle and ONNX scorers) and queried in a fresh interpreter,
which must end with neither module loaded: the test process itself has imported both.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.scoring.test_esm2 import UBIQUITIN

from api.tests.conftest import SUPERVISED

SCRIPT = f"""
import asyncio, sys
import httpx
from api.app.main import create_app

async def main():
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/ready")).status_code == 200
            landscape = await client.get("/v1/proteins/{SUPERVISED}/landscape")
            assert landscape.status_code == 200, landscape.text
        for scorer in app.state.mutascope.scorers.values():
            assert scorer.landscape("{UBIQUITIN}", fast=True).shape == ({len(UBIQUITIN)}, 20)

asyncio.run(main())
loaded = sorted(m for m in ("torch", "transformers") if m in sys.modules)
assert not loaded, loaded
"""


def _run(env: dict[str, str]) -> None:
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        text=True,
        env=os.environ | env,
        cwd=Path(__file__).parents[2],
    )
    assert done.returncode == 0, done.stderr[-3000:]


def test_api_runs_without_torch_or_transformers(bundle_dir: Path, onnx_dir: Path) -> None:
    _run(
        {"MUTASCOPE_API_BUNDLE_DIR": str(bundle_dir), "MUTASCOPE_API_ONNX_LOCAL_DIR": str(onnx_dir)}
    )


@pytest.mark.slow
def test_api_runs_from_the_pinned_hub_revisions_without_torch() -> None:
    """The real thing: the full bundle and both fp32 models (about 760 MB) from the Hub."""
    _run({})

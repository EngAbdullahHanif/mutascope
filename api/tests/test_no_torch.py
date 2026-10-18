"""The API starts and serves without importing torch or transformers (ADR 0029's test pattern).

``python -m api.app.selfcheck`` starts the app (lifespan: bundle and ONNX scorers), queries it
and scores with every scorer in a fresh interpreter, which must end with neither module loaded:
the test process itself has imported both. The API image runs the same command as its last
build step (ADR 0033).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from api.tests.conftest import SUPERVISED


def _run(env: dict[str, str], *args: str) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "api.app.selfcheck", *args],
        capture_output=True,
        text=True,
        env=os.environ | env,
        cwd=Path(__file__).parents[2],
    )
    assert done.returncode == 0, done.stderr[-3000:]


def test_api_runs_without_torch_or_transformers(bundle_dir: Path, onnx_dir: Path) -> None:
    _run(
        {
            "MUTASCOPE_API_BUNDLE_DIR": str(bundle_dir),
            "MUTASCOPE_API_ONNX_LOCAL_DIR": str(onnx_dir),
        },
        SUPERVISED,
    )


@pytest.mark.slow
def test_api_runs_from_the_pinned_hub_revisions_without_torch() -> None:
    """The real thing: the full bundle and both fp32 models (about 760 MB) from the Hub."""
    _run({})

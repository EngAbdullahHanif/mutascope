"""Write the golden landscapes the live-scoring agreement test compares with (ADR 0031).

The bundle ships zero-shot landscapes of ESM-2 650M and SaProt only, not of the 35M / 150M
models the API serves, so the reference is our PyTorch landscapes of those two models, read
from the pipeline's log-probability cache (never recomputed: a missing or partial entry is an
error) for one ProteinGym target, the bundle fixture's smallest (its sequence from the
pinned bundle). Usage::

    uv run python -m api.tests.make_live_golden

It writes ``api/tests/golden/live_<assay>.json``: the target sequence, each reference model's
Hub id and revision, and its ``(L, 20)`` landscape (row-major, ``AA_ORDER``, 6 decimals).
"""

import gzip
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from omegaconf import OmegaConf

from api.app.settings import REPO_ROOT, Settings
from api.tests.conftest import SUPERVISED
from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import landscape_from_log_probs
from mutascope.scoring.cache import CacheKey, LogProbCache
from mutascope.scoring.marginals import cache_model_id
from mutascope.utils.config import as_str_dict, load_config

GOLDEN_DIR = Path(__file__).parent / "golden"
REFERENCES = {
    # golden key: (PyTorch model config, method)
    "esm2_35m_wt": ("esm2_35m", "wt"),
    "esm2_150m_wt": ("esm2_150m", "wt"),
    "esm2_35m_masked": ("esm2_35m", "masked"),
}
"""What the live routes serve: fast mode on both models, precise mode on 35M."""


def golden_path(assay_id: str = SUPERVISED) -> Path:
    return GOLDEN_DIR / f"live_{assay_id}.json"


def target_sequence(assay_id: str) -> str:
    """The assay's target as the pinned bundle has it (``assay.target_seq``)."""
    from huggingface_hub import hf_hub_download  # pyright: ignore[reportUnknownVariableType]

    settings = Settings()
    path = hf_hub_download(
        settings.bundle_repo,
        f"{settings.bundle_path}/proteins/{assay_id}.json.gz",
        repo_type="dataset",
        revision=settings.bundle_revision,
    )
    return str(json.loads(gzip.decompress(Path(path).read_bytes()))["assay"]["target_seq"])


def write_golden(assay_id: str = SUPERVISED) -> Path:
    cfg = load_config(REPO_ROOT / "conf")
    cache = LogProbCache(Path(str(cfg.scoring.cache_dir)))
    seq = target_sequence(assay_id)
    sources: dict[str, Any] = {}
    landscapes: dict[str, list[float]] = {}
    for key, (name, method) in REFERENCES.items():
        model = as_str_dict(
            OmegaConf.to_container(
                load_config(REPO_ROOT / "conf", [f"model={name}"]).model, resolve=True
            ),
            name,
        )
        cache_key = CacheKey(
            model_id=cache_model_id(str(model["hf_id"])),
            revision=str(model["revision"]),
            sequence=seq,
            struct_tokens=None,
            method=method,
            region="full",
        )
        log_probs = cache.get(cache_key)
        if log_probs is None or np.isnan(log_probs).any():
            raise SystemExit(f"{key}: no complete cache entry for {assay_id}")
        landscape = landscape_from_log_probs(log_probs, seq)
        sources[key] = {
            "model": name,
            "hf_id": model["hf_id"],
            "revision": model["revision"],
            "method": method,
        }
        landscapes[key] = [round(float(v), 6) for v in landscape.ravel()]
        assert all(math.isfinite(v) for v in landscapes[key])
    path = golden_path(assay_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    golden = {
        "assay_id": assay_id,
        "target_seq": seq,
        "aa_order": AA_ORDER,
        "sources": sources,
        "landscapes": landscapes,
    }
    path.write_text(json.dumps(golden, indent=1) + "\n")
    return path


if __name__ == "__main__":
    write_golden()

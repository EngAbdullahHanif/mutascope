"""Load the live scorers: ESM-2 35M / 150M as fp32 ONNX graphs on the CPU (ADR 0029).

The scorers are the package's own ``esm2_onnx`` scorer, built from ``conf/model/<name>.yaml``
with the pinned revision from the API settings. Nothing here imports torch or transformers
(``api/tests/test_no_torch.py`` checks it in a fresh interpreter).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from mutascope.scoring.base import Scorer, build_scorer
from mutascope.utils.config import as_str_dict, load_config
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

SERVED_PRECISION = "fp32"
"""int8 failed the per-assay agreement check and is rejected (ADR 0029, section 6)."""


class ScorerLoadError(RuntimeError):
    """A configured model cannot be served."""


def model_config(
    config_dir: Path, name: str, *, revision: str, threads: int, local_dir: Path | None
) -> dict[str, Any]:
    """``conf/model/<name>.yaml`` resolved with the API's revision, threads and file source.

    Raises:
        ScorerLoadError: The config is not an fp32 ONNX scorer.
    """
    cfg = load_config(config_dir, [f"model={name}", f"onnx.publish.revision={revision}"])
    resolved = as_str_dict(OmegaConf.to_container(cfg.model, resolve=True), f"model {name}")
    if resolved.get("scorer") != "esm2_onnx" or resolved.get("precision") != SERVED_PRECISION:
        raise ScorerLoadError(
            f"{name} is not an {SERVED_PRECISION} ONNX scorer "
            f"(scorer={resolved.get('scorer')}, precision={resolved.get('precision')})"
        )
    resolved["threads"] = threads
    if local_dir is not None:
        resolved["local_dir"] = str(local_dir)
    return resolved


def load_scorers(
    config_dir: Path,
    names: tuple[str, ...],
    *,
    revision: str,
    threads: int,
    local_dir: Path | None,
) -> Mapping[str, Scorer]:
    """Build each named scorer once, on the CPU, without the log-probability cache."""
    scorers: dict[str, Scorer] = {}
    for name in names:
        cfg = model_config(
            config_dir, name, revision=revision, threads=threads, local_dir=local_dir
        )
        scorers[name] = build_scorer(cfg, device="cpu", cache_dir=None)
        log.info("scorer_ready", model=name, revision=revision[:7], threads=threads)
    return scorers

"""Per-residue embeddings from one unmasked pass over the wild-type sequence (ADR 0020).

The embedding of residue ``i`` is the mean, over the last ``layers`` layers, of the model's
hidden state at token ``i + 1`` (``<cls>`` is token 0; :data:`RESIDUE_OFFSET`). "Layer" means
an entry of Hugging Face's ``hidden_states``: entry 0 is the embedding layer's output and entry
``k`` the output of transformer block ``k``, except that the last entry is the output after the
final LayerNorm (``emb_layer_norm_after``), as in fair-esm's ``representations[33]``. With
``layers = 4`` and a 33-block model this is blocks 30, 31 and 32 and the normalised block 33.

The pass is the wild-type marginal's: the sequence as given, nothing masked. A sequence longer
than the model's window (ESM-2, over 1,022 residues) is cut into the same half-overlapping tiles
(:func:`~mutascope.scoring.windows.tiled_windows`), and each residue is read from its most
central tile. SaProt is never windowed (ADR 0015), so it sees the whole sequence and its
structure tokens at once.

Embeddings are cached like log-probabilities, in a cache of their own (``features.
embedding_cache_dir``), keyed by model, revision, sequence, structure tokens, method
(:func:`embedding_method`) and region. Each entry is written once, after its pass, so a run
that is stopped loses at most the sequence in progress: one forward pass.

The scorer is only borrowed for its tokenizer and model; nothing here changes how it scores.
"""

import time
from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
import torch

from mutascope.eval.landscapes import LandscapeTarget
from mutascope.features.schemas import EmbeddingRunSchema
from mutascope.scoring.cache import CacheKey, LogProbCache
from mutascope.scoring.masked_lm import RESIDUE_OFFSET, MaskedLMScorer
from mutascope.scoring.windows import tiled_windows
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

Embedding = npt.NDArray[np.float32]


def embedding_method(layers: int) -> str:
    """The cache key's method for a mean over the last ``layers`` layers."""
    if layers < 1:
        raise ValueError(f"layers must be at least 1, got {layers}")
    return f"embedding_mean_last{layers}"


def hidden_size(scorer: MaskedLMScorer) -> int:
    """Width of the model's hidden states (1,280 for the 650M checkpoints)."""
    config: Any = getattr(scorer.model, "config", None)
    size = getattr(config, "hidden_size", None)
    if not isinstance(size, int):
        raise TypeError(f"{scorer.model_id}: the model has no config.hidden_size")
    return size


@torch.inference_mode()
def mean_last_layers(scorer: MaskedLMScorer, tokens: torch.Tensor, layers: int) -> Embedding:
    """``(L, hidden)`` mean of the last ``layers`` hidden states at every residue token.

    Args:
        scorer: Supplies the model, its device and its window.
        tokens: ``<cls> residues <eos>`` from ``scorer.encode``.
        layers: How many of the last ``hidden_states`` entries to average.

    Raises:
        ValueError: The model has fewer than ``layers`` transformer blocks.
    """
    n_tokens = tokens.numel()
    window = scorer.settings.window_tokens
    tiles, owner = tiled_windows(n_tokens, n_tokens if window is None else window)
    per_tile: list[torch.Tensor] = []
    for tile in tiles:  # one tile at a time: 34 hidden states of a 1,024-token tile are 178 MB
        input_ids = tokens[tile.start : tile.end].unsqueeze(0).to(scorer.device)
        output = scorer.model(
            input_ids=input_ids,
            attention_mask=torch.ones_like(input_ids),
            output_hidden_states=True,
        )
        hidden: Sequence[torch.Tensor] = output.hidden_states
        if len(hidden) <= layers:
            raise ValueError(
                f"{scorer.model_id}: {len(hidden) - 1} blocks, cannot average the last {layers}"
            )
        per_tile.append(torch.stack(list(hidden[-layers:])).float().mean(dim=0)[0].cpu())
    positions = range(RESIDUE_OFFSET, n_tokens - 1)
    rows = [per_tile[owner[p]][p - tiles[owner[p]].start] for p in positions]
    return torch.stack(rows).numpy().astype(np.float32)


def embedding_key(scorer: MaskedLMScorer, target: LandscapeTarget, layers: int) -> CacheKey:
    """The cache key of ``target``'s embedding under ``scorer``'s pinned checkpoint."""
    return CacheKey(
        model_id=scorer.settings.hf_id,
        revision=scorer.settings.revision,
        sequence=target.sequence,
        struct_tokens=target.struct_tokens,
        method=embedding_method(layers),
        region=target.region.label,
    )


def fill_embeddings(
    scorer: MaskedLMScorer,
    cache: LogProbCache,
    targets: Sequence[LandscapeTarget],
    *,
    layers: int,
) -> pd.DataFrame:
    """Embed every landscape in ``targets`` that ``cache`` lacks; one pass per cache entry.

    Assays of one protein share an entry (same sequence, structure tokens and region), so the
    number of passes is the number of distinct entries, not of assays or variants.

    Returns:
        One row per landscape (:class:`EmbeddingRunSchema`). ``computed`` is false for a
        landscape that was cached already or shares its entry with an earlier row.

    Raises:
        ValueError: ``cache.width`` is not the model's hidden size.
    """
    width = hidden_size(scorer)
    if cache.width != width:
        raise ValueError(f"cache width {cache.width}, but {scorer.model_id} has {width}")
    rows: list[dict[str, object]] = []
    started = time.monotonic()
    for index, target in enumerate(targets, start=1):
        key = embedding_key(scorer, target, layers)
        computed, seconds = False, 0.0
        if cache.get(key) is None:
            tick = time.monotonic()
            embedding = mean_last_layers(
                scorer, scorer.encode(target.sequence, target.struct_tokens), layers
            )
            if not np.isfinite(embedding).all():
                raise RuntimeError(f"{target.assay_id}: non-finite embedding")
            seconds = time.monotonic() - tick
            cache.put(key, embedding)
            cache.record(key, positions=len(target.sequence), seconds=seconds)
            computed = True
            if scorer.device.type == "mps":
                torch.mps.empty_cache()  # see MaskedLMScorer._release_device_cache
            log.info(
                "embedded",
                model=scorer.model_id,
                assay_id=target.assay_id,
                region=target.region.label,
                length=len(target.sequence),
                landscape=f"{index}/{len(targets)}",
                seconds=round(seconds, 2),
                elapsed=round(time.monotonic() - started, 1),
            )
        rows.append(
            {
                "assay_id": target.assay_id,
                "model": scorer.model_id,
                "region": target.region.label,
                "region_length": len(target.sequence),
                "benchmark": target.benchmark,
                "full_target": target.full_target,
                "computed": computed,
                "seconds": seconds,
            }
        )
    return EmbeddingRunSchema.validate(pd.DataFrame.from_records(rows), lazy=True)

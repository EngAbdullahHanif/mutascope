"""ESM-2 zero-shot scorer (Hugging Face ``transformers``, pinned revisions).

Follows ProteinGym's ``proteingym/baselines/esm/compute_fitness.py`` at the pinned commit:

- **Masked marginal** (``fast=False``; ProteinGym's ``masked-marginals``, which produced its
  ``ESM2_*`` scores): one forward pass per residue with that residue's token replaced by
  ``<mask>``; the log-probabilities at the masked position come from a log-softmax over the full
  vocabulary. Passes are batched here (``batch_size``); upstream runs them one at a time, which
  gives the same numbers.
- **Wild-type marginal** (``fast=True``; ProteinGym's ``wt-marginals``): one unmasked pass,
  log-probabilities at every position.
- **Long sequences** (more than ``window_tokens`` tokens, i.e. > 1022 residues): masked marginals
  use ProteinGym's ``get_optimal_window`` exactly
  (:func:`~mutascope.scoring.windows.optimal_window`): a ``window_tokens``-long slice of the
  token sequence centred on the masked position. Like upstream, a slice is cut from the
  tokenised sequence as is, so a middle window has neither ``<cls>`` nor ``<eos>``. Wild-type
  marginals use half-overlapping tiles, each position read from its most central tile;
  ProteinGym instead runs the whole sequence in one pass (ADR 0012).

Token offset: the tokenizer prepends ``<cls>``, so residue ``i`` (0-indexed) is token ``i + 1``
(upstream: ``token_probs[0, 1 + idx, ...]``, "add 1 for BOS"). :data:`RESIDUE_OFFSET` is the only
place this appears, and :meth:`Esm2Scorer.tokenize` checks the layout on every sequence.

The cache, the two marginals and the windows are in :mod:`mutascope.scoring.masked_lm`, shared
with SaProt; this module holds what depends on ESM-2's vocabulary.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import torch

from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import ScorerConfigError, check_sequence
from mutascope.scoring.cache import LogProbCache
from mutascope.scoring.masked_lm import (
    RESIDUE_OFFSET,
    MaskedLMScorer,
    ModelSettings,
    token_id,
)
from mutascope.utils.device import select_device
from mutascope.utils.logging import get_logger

log = get_logger(__name__)


class Esm2Settings(ModelSettings):
    """``conf/model/esm2_*.yaml``."""


class Esm2Scorer(MaskedLMScorer):
    """Masked- and wild-type-marginal landscapes from an ESM-2 masked language model.

    Construct with :func:`from_config` for a pinned Hub checkpoint. The constructor takes the
    model and tokenizer directly so tests can pass small stand-ins.
    """

    def __init__(
        self,
        *,
        settings: Esm2Settings,
        model: torch.nn.Module,
        tokenizer: Any,
        device: torch.device,
        cache: LogProbCache | None = None,
    ) -> None:
        super().__init__(
            settings=settings, model=model, tokenizer=tokenizer, device=device, cache=cache
        )
        if settings.window_tokens is None:
            raise ScorerConfigError(f"{settings.name}: ESM-2 needs window_tokens")
        self.mask_id = token_id(tokenizer, "mask_token_id")
        unk_id = token_id(tokenizer, "unk_token_id")
        self.aa_ids = [int(i) for i in tokenizer.convert_tokens_to_ids(list(AA_ORDER))]
        if unk_id in self.aa_ids:
            raise ScorerConfigError("tokenizer does not know all 20 amino acids")
        self.aa_token_ids = torch.tensor(self.aa_ids)

    def tokenize(self, seq: str) -> torch.Tensor:
        """Token ids ``<cls> residues <eos>``; fails unless residue ``i`` is token ``i + 1``."""
        check_sequence(seq)
        ids = [int(i) for i in self.tokenizer(seq)["input_ids"]]
        aa = dict(zip(AA_ORDER, self.aa_ids, strict=True))
        expected = [self.cls_id, *(aa[r] for r in seq), self.eos_id]
        if ids != expected:
            raise RuntimeError(
                f"unexpected tokenisation of a {len(seq)}-residue sequence ({len(ids)} tokens): "
                f"residue i must be token i + {RESIDUE_OFFSET}"
            )
        return torch.tensor(ids)

    def encode(self, seq: str, struct_tokens: str | None) -> torch.Tensor:
        """:meth:`tokenize`; ESM-2 is sequence-only, so ``struct_tokens`` must be None."""
        if struct_tokens is not None:
            raise ValueError("ESM-2 is sequence-only; struct_tokens must be None")
        return self.tokenize(seq)

    def mask_ids(self, tokens: torch.Tensor) -> torch.Tensor:
        """``<mask>`` for every token."""
        return torch.full_like(tokens, self.mask_id)

    def aa_log_probs(self, logits: torch.Tensor) -> torch.Tensor:
        """Log-softmax over the whole vocabulary, then the 20 amino-acid columns in AA_ORDER."""
        return torch.log_softmax(logits, dim=-1)[:, self.aa_token_ids]


def from_config(model_cfg: Mapping[str, Any], *, device: str, cache_dir: Path | None) -> Esm2Scorer:
    """Load the pinned checkpoint named by ``model_cfg`` (float32, eval mode) on ``device``."""
    from transformers import AutoTokenizer, EsmForMaskedLM

    settings = Esm2Settings.from_config(model_cfg)
    torch_device = select_device(device)
    # transformers is untyped at this boundary; the scorer validates what it reads from these.
    tokenizer = cast(Any, AutoTokenizer).from_pretrained(settings.hf_id, revision=settings.revision)
    model = cast(Any, EsmForMaskedLM).from_pretrained(
        settings.hf_id, revision=settings.revision, dtype=torch.float32
    )
    log.info("model_loaded", model=settings.name, hf_id=settings.hf_id, revision=settings.revision)
    return Esm2Scorer(
        settings=settings,
        model=model.to(torch_device),
        tokenizer=tokenizer,
        device=torch_device,
        cache=LogProbCache(cache_dir) if cache_dir is not None else None,
    )

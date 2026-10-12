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
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import torch

from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import (
    FloatArray,
    ScorerConfigError,
    check_sequence,
    landscape_from_log_probs,
)
from mutascope.scoring.cache import CacheKey, LogProbCache
from mutascope.scoring.windows import optimal_window, tiled_windows
from mutascope.utils.device import select_device
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

RESIDUE_OFFSET = 1
"""Token index of residue 0: the tokenizer puts ``<cls>`` at token 0."""


@dataclass(frozen=True)
class Esm2Settings:
    """``conf/model/esm2_*.yaml``."""

    name: str
    hf_id: str
    revision: str
    batch_size: int
    window_tokens: int

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "Esm2Settings":
        try:
            settings = cls(
                name=str(cfg["name"]),
                hf_id=str(cfg["hf_id"]),
                revision=str(cfg["revision"]),
                batch_size=int(cfg["batch_size"]),
                window_tokens=int(cfg["window_tokens"]),
            )
        except KeyError as exc:
            raise ScorerConfigError(f"model config lacks {exc}") from exc
        if len(settings.revision) != 40:
            raise ScorerConfigError(f"{settings.name}: revision must be a full commit sha")
        return settings


class Esm2Scorer:
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
        self.settings = settings
        self.model = model.eval()
        self.tokenizer = tokenizer  # a Hugging Face ESM tokenizer (untyped upstream)
        self.device = device
        self.cache = cache
        self.cls_id = _token_id(tokenizer, "cls_token_id")
        self.eos_id = _token_id(tokenizer, "eos_token_id")
        self.mask_id = _token_id(tokenizer, "mask_token_id")
        unk_id = _token_id(tokenizer, "unk_token_id")
        self.aa_ids = [int(i) for i in tokenizer.convert_tokens_to_ids(list(AA_ORDER))]
        if unk_id in self.aa_ids:
            raise ScorerConfigError("tokenizer does not know all 20 amino acids")
        self.aa_token_ids = torch.tensor(self.aa_ids)

    @property
    def model_id(self) -> str:
        return self.settings.name

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

    def landscape(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
    ) -> FloatArray:
        """``(L, 20)`` scores; see :mod:`mutascope.scoring.base`. ESM-2 is sequence-only."""
        if struct_tokens is not None:
            raise ValueError("ESM-2 is sequence-only; struct_tokens must be None")
        return landscape_from_log_probs(self.log_probs(seq, fast=fast, region=region), seq)

    def log_probs(self, seq: str, *, fast: bool = False, region: str = "full") -> FloatArray:
        """``(L, 20)`` log-probabilities in ``AA_ORDER`` (normalised over the full vocabulary).

        Read from the cache when present; computed and stored otherwise.
        """
        key = CacheKey(
            model_id=self.settings.hf_id,
            revision=self.settings.revision,
            sequence=seq,
            struct_tokens=None,
            method="wt" if fast else "masked",
            region=region,
        )
        if self.cache is not None and (hit := self.cache.get(key)) is not None:
            return hit
        start = time.monotonic()
        tokens = self.tokenize(seq)
        result = self._wt_marginal(tokens) if fast else self._masked_marginal(tokens)
        log.info(
            "scored",
            model=self.model_id,
            method=key.method,
            length=len(seq),
            seconds=round(time.monotonic() - start, 2),
        )
        if self.cache is not None:
            self.cache.put(key, result)
        return result

    @torch.inference_mode()
    def _masked_marginal(self, tokens: torch.Tensor) -> FloatArray:
        n_tokens = tokens.numel()
        residues = range(RESIDUE_OFFSET, n_tokens - 1)  # every token but <cls> and <eos>
        rows: list[torch.Tensor] = []
        for batch in _chunks(list(residues), self.settings.batch_size):
            inputs: list[torch.Tensor] = []
            local: list[int] = []
            for position in batch:
                window = optimal_window(position, n_tokens, self.settings.window_tokens)
                masked = tokens[window.start : window.end].clone()
                masked[position - window.start] = self.mask_id
                inputs.append(masked)
                local.append(position - window.start)
            logits = self._forward(torch.stack(inputs))
            at_mask = logits[torch.arange(len(batch)), torch.tensor(local)]
            rows.append(self._aa_log_probs(at_mask))
        return torch.cat(rows).numpy()

    @torch.inference_mode()
    def _wt_marginal(self, tokens: torch.Tensor) -> FloatArray:
        n_tokens = tokens.numel()
        tiles, owner = tiled_windows(n_tokens, self.settings.window_tokens)
        logits = self._forward(torch.stack([tokens[t.start : t.end] for t in tiles]))
        positions = range(RESIDUE_OFFSET, n_tokens - 1)
        tile = torch.tensor([owner[p] for p in positions])
        local = torch.tensor([p - tiles[owner[p]].start for p in positions])
        return self._aa_log_probs(logits[tile, local]).numpy()

    def _forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        """Logits ``(batch, tokens, vocab)`` as float32 on the CPU."""
        input_ids = input_ids.to(self.device)
        output = self.model(input_ids=input_ids, attention_mask=torch.ones_like(input_ids))
        logits: torch.Tensor = output.logits
        return logits.float().cpu()

    def _aa_log_probs(self, logits: torch.Tensor) -> torch.Tensor:
        """Log-softmax over the whole vocabulary, then the 20 amino-acid columns in AA_ORDER."""
        return torch.log_softmax(logits, dim=-1)[:, self.aa_token_ids]


def _token_id(tokenizer: Any, attribute: str) -> int:
    value = getattr(tokenizer, attribute, None)
    if not isinstance(value, int):
        raise ScorerConfigError(f"tokenizer has no {attribute}")
    return value


def _chunks(items: Sequence[int], size: int) -> list[Sequence[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


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

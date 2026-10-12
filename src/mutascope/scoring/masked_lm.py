"""What every masked-language-model scorer shares: the cache, the two marginals, the windows.

ESM-2 and SaProt are the same transformer with different vocabularies, so scoring a sequence is
the same procedure for both. A subclass supplies only the three things that depend on the
vocabulary:

- :meth:`MaskedLMScorer.encode`: the token ids of a sequence (and its structure tokens);
- :meth:`MaskedLMScorer.mask_ids`: which token hides each residue in a masked pass;
- :meth:`MaskedLMScorer.aa_log_probs`: the log-probability of each of the 20 amino acids from
  the logits at one position.

Token offset: both tokenizers prepend ``<cls>``, so residue ``i`` (0-indexed) is token ``i + 1``.
:data:`RESIDUE_OFFSET` is the only place this appears, and ``encode`` must check the layout on
every sequence.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self

import numpy as np
import torch

from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import (
    FloatArray,
    ScorerConfigError,
    landscape_from_log_probs,
    resolve_positions,
)
from mutascope.scoring.cache import CacheKey, LogProbCache
from mutascope.scoring.windows import Window, optimal_window, tiled_windows
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

RESIDUE_OFFSET = 1
"""Token index of residue 0: the tokenizer puts ``<cls>`` at token 0."""


@dataclass(frozen=True)
class ModelSettings:
    """One ``conf/model/*.yaml`` group of a masked-language-model scorer."""

    name: str
    hf_id: str
    revision: str
    batch_size: int
    window_tokens: int | None
    """Longest input in tokens; longer sequences are windowed. None: never window."""
    max_batch_tokens: int | None = None
    """Cap on tokens per forward call, for inputs longer than a window would be. None: no cap."""

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> Self:
        try:
            window, cap = cfg["window_tokens"], cfg.get("max_batch_tokens")
            settings = cls(
                name=str(cfg["name"]),
                hf_id=str(cfg["hf_id"]),
                revision=str(cfg["revision"]),
                batch_size=int(cfg["batch_size"]),
                window_tokens=None if window is None else int(window),
                max_batch_tokens=None if cap is None else int(cap),
            )
        except KeyError as exc:
            raise ScorerConfigError(f"model config lacks {exc}") from exc
        if len(settings.revision) != 40:
            raise ScorerConfigError(f"{settings.name}: revision must be a full commit sha")
        return settings


class MaskedLMScorer(ABC):
    """Masked- and wild-type-marginal landscapes from a masked language model.

    The constructor takes the model and tokenizer directly so tests can pass small stand-ins.
    """

    def __init__(
        self,
        *,
        settings: ModelSettings,
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
        self.cls_id = token_id(tokenizer, "cls_token_id")
        self.eos_id = token_id(tokenizer, "eos_token_id")
        self._positions_computed = 0

    @property
    def model_id(self) -> str:
        return self.settings.name

    @property
    def positions_computed(self) -> int:
        return self._positions_computed

    @abstractmethod
    def encode(self, seq: str, struct_tokens: str | None) -> torch.Tensor:
        """Token ids ``<cls> residues <eos>``; fails unless residue ``i`` is token ``i + 1``."""

    @abstractmethod
    def mask_ids(self, tokens: torch.Tensor) -> torch.Tensor:
        """For every token of ``tokens``, the id that replaces it when its residue is masked."""

    @abstractmethod
    def aa_log_probs(self, logits: torch.Tensor) -> torch.Tensor:
        """``(n, 20)`` amino-acid log-probabilities in ``AA_ORDER`` from ``(n, vocab)`` logits."""

    def landscape(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
        positions: Sequence[int] | None = None,
    ) -> FloatArray:
        """``(L, 20)`` scores; see :mod:`mutascope.scoring.base`."""
        log_probs = self.log_probs(
            seq, struct_tokens, fast=fast, region=region, positions=positions
        )
        return landscape_from_log_probs(log_probs, seq)

    def log_probs(
        self,
        seq: str,
        struct_tokens: str | None = None,
        *,
        fast: bool = False,
        region: str = "full",
        positions: Sequence[int] | None = None,
    ) -> FloatArray:
        """``(L, 20)`` log-probabilities in ``AA_ORDER`` (normalised over the full vocabulary).

        Only the requested residues that the cache entry lacks go through the model; the entry
        is then rewritten with those rows filled in. Rows that were never scored are NaN. The
        wild-type marginal gets every residue from its single pass, so it ignores ``positions``.
        """
        tokens = self.encode(seq, struct_tokens)  # also validates seq and struct_tokens
        key = CacheKey(
            model_id=self.settings.hf_id,
            revision=self.settings.revision,
            sequence=seq,
            struct_tokens=struct_tokens,
            method="wt" if fast else "masked",
            region=region,
        )
        wanted = resolve_positions(None if fast else positions, len(seq))
        cached = self.cache.get(key) if self.cache is not None else None
        if cached is None:
            cached = np.full((len(seq), len(AA_ORDER)), np.nan, dtype=np.float32)
        missing = [p for p in wanted if np.isnan(cached[p, 0])]
        if not missing:
            return cached
        start = time.monotonic()
        if fast:
            result = self._wt_marginal(tokens)
        else:
            result = cached
            result[missing] = self._masked_marginal(tokens, missing)
        self._release_device_cache()
        seconds = time.monotonic() - start
        self._positions_computed += len(missing)
        log.info(
            "scored",
            model=self.model_id,
            method=key.method,
            region=region,
            length=len(seq),
            positions=len(missing),
            seconds=round(seconds, 2),
        )
        if self.cache is not None:
            self.cache.put(key, result)
            self.cache.record(key, positions=len(missing), seconds=seconds)
        return result

    def _release_device_cache(self) -> None:
        """Give the MPS allocator's cached buffers back to the system after a sequence.

        PyTorch keeps freed MPS buffers for reuse. Every sequence length needs buffers of new
        sizes, and with the 8M and 35M checkpoints the kept ones were never released: driver
        memory grew by about 1.6 GiB per 1,000 positions until the 35M benchmark run failed
        with "MPS backend out of memory" (build log entry 13). Emptying the cache once per
        sequence costs nothing measurable and changes no number.
        """
        if self.device.type == "mps":
            torch.mps.empty_cache()

    def _window(self, position: int, n_tokens: int) -> Window:
        """The slice of the token sequence the model sees when token ``position`` is masked."""
        if self.settings.window_tokens is None:
            return Window(0, n_tokens)
        return optimal_window(position, n_tokens, self.settings.window_tokens)

    def _rows_per_batch(self, n_tokens: int) -> int:
        """Masked passes per forward call for inputs of ``n_tokens`` tokens (at least 1)."""
        cap = self.settings.max_batch_tokens
        if cap is None:
            return self.settings.batch_size
        return max(1, min(self.settings.batch_size, cap // n_tokens))

    @torch.inference_mode()
    def _masked_marginal(self, tokens: torch.Tensor, residues: Sequence[int]) -> FloatArray:
        """Log-probabilities of ``residues`` (0-indexed), one row each, in the order given."""
        n_tokens = tokens.numel()
        mask_ids = self.mask_ids(tokens)
        masked_tokens = [RESIDUE_OFFSET + r for r in residues]  # never <cls> or <eos>
        first = self._window(RESIDUE_OFFSET, n_tokens)  # every window has this length
        rows_per_batch = self._rows_per_batch(first.end - first.start)
        rows: list[torch.Tensor] = []
        for batch in _chunks(masked_tokens, rows_per_batch):
            inputs: list[torch.Tensor] = []
            local: list[int] = []
            for position in batch:
                window = self._window(position, n_tokens)
                masked = tokens[window.start : window.end].clone()
                masked[position - window.start] = mask_ids[position]
                inputs.append(masked)
                local.append(position - window.start)
            logits = self._forward(torch.stack(inputs))
            at_mask = logits[torch.arange(len(batch)), torch.tensor(local)]
            rows.append(self.aa_log_probs(at_mask))
        return torch.cat(rows).numpy()

    @torch.inference_mode()
    def _wt_marginal(self, tokens: torch.Tensor) -> FloatArray:
        n_tokens = tokens.numel()
        window = self.settings.window_tokens
        tiles, owner = tiled_windows(n_tokens, n_tokens if window is None else window)
        logits = self._forward(torch.stack([tokens[t.start : t.end] for t in tiles]))
        positions = range(RESIDUE_OFFSET, n_tokens - 1)
        tile = torch.tensor([owner[p] for p in positions])
        local = torch.tensor([p - tiles[owner[p]].start for p in positions])
        return self.aa_log_probs(logits[tile, local]).numpy()

    def _forward(self, input_ids: torch.Tensor) -> torch.Tensor:
        """Logits ``(batch, tokens, vocab)`` as float32 on the CPU."""
        input_ids = input_ids.to(self.device)
        output = self.model(input_ids=input_ids, attention_mask=torch.ones_like(input_ids))
        logits: torch.Tensor = output.logits
        return logits.float().cpu()


def token_id(tokenizer: Any, attribute: str) -> int:
    """A special token's id, e.g. ``token_id(tokenizer, "cls_token_id")``."""
    value = getattr(tokenizer, attribute, None)
    if not isinstance(value, int):
        raise ScorerConfigError(f"tokenizer has no {attribute}")
    return value


def _chunks(items: Sequence[int], size: int) -> list[Sequence[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]

"""The torch-free core of every masked-language-model scorer: settings, cache, pass planning.

A masked-language-model scorer turns token ids into log-probabilities in two ways, the masked
and the wild-type marginal (:mod:`mutascope.scoring.base`). What surrounds the model is the
same whichever runtime runs it, PyTorch (:mod:`~mutascope.scoring.masked_lm`) or ONNX Runtime
(:mod:`~mutascope.scoring.onnx_esm2`), and lives here so it exists once:

- the cache: which residues an entry already has, which go through the model, NaN for the rest
  (ADR 0013);
- which window of a long sequence each masked pass sees, and how passes are batched (ADR 0012).

Nothing here imports torch, so the ONNX scorer can be served without it.

Token offset: both tokenizers prepend ``<cls>``, so residue ``i`` (0-indexed) is token ``i + 1``.
:data:`RESIDUE_OFFSET` is the only place this appears, and ``encode`` must check the layout on
every sequence.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Self, TypeVar

import numpy as np

from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import (
    FloatArray,
    ScorerConfigError,
    landscape_from_log_probs,
    resolve_positions,
)
from mutascope.scoring.cache import CacheKey, LogProbCache
from mutascope.scoring.windows import Window, optimal_window
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

RESIDUE_OFFSET = 1
"""Token index of residue 0: the tokenizer puts ``<cls>`` at token 0."""

TokensT = TypeVar("TokensT")
"""A runtime's token-id array: ``torch.Tensor`` or a numpy integer array."""


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
    def from_config(cls, cfg: Mapping[str, Any], *, pinned: bool = True) -> Self:
        """Read a model config; ``pinned=False`` accepts a revision that is not a commit sha
        (only for files read from a local export, never from the Hub)."""
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
        if pinned and len(settings.revision) != 40:
            raise ScorerConfigError(f"{settings.name}: revision must be a full commit sha")
        return settings


def cache_model_id(hf_id: str, file: str | None = None) -> str:
    """The ``model_id`` of a model's cache entries: its Hub repo, plus the file if it names one.

    A repository that holds several models (the ONNX repo has fp32 and int8 files of two
    checkpoints at one revision) must give each its own entries, or an int8 landscape could be
    served for an fp32 request.
    """
    return hf_id if file is None else f"{hf_id}/{file}"


@dataclass(frozen=True)
class MaskedPass:
    """One masked forward pass: token ``position`` hidden, the model sees ``window``."""

    position: int
    window: Window

    @property
    def local(self) -> int:
        """Index of the masked token within the window."""
        return self.position - self.window.start


class MarginalScorer(ABC, Generic[TokensT]):
    """Masked- and wild-type-marginal log-probabilities, with the cache, for any runtime.

    A subclass encodes sequences and runs the model; this class decides what to run.
    """

    def __init__(
        self, *, settings: ModelSettings, cache: LogProbCache | None, cache_id: str
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.cache_id = cache_id
        """``model_id`` of the cache keys (:func:`cache_model_id`)."""
        self._positions_computed = 0

    @property
    def model_id(self) -> str:
        return self.settings.name

    @property
    def positions_computed(self) -> int:
        return self._positions_computed

    @abstractmethod
    def encode(self, seq: str, struct_tokens: str | None) -> TokensT:
        """Token ids ``<cls> residues <eos>``; fails unless residue ``i`` is token ``i + 1``."""

    @abstractmethod
    def _masked_marginal(self, tokens: TokensT, residues: Sequence[int]) -> FloatArray:
        """Log-probabilities of ``residues`` (0-indexed), one row each, in the order given."""

    @abstractmethod
    def _wt_marginal(self, tokens: TokensT) -> FloatArray:
        """Log-probabilities of every residue from unmasked passes (tiled if windowed)."""

    def _release_device_cache(self) -> None:
        """Called once per computed sequence; a runtime that holds device memory frees it."""

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
            model_id=self.cache_id,
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

    def masked_batches(self, n_tokens: int, residues: Sequence[int]) -> list[list[MaskedPass]]:
        """The masked passes for ``residues`` (0-indexed), grouped into forward calls.

        Every window of one sequence has the same length, so a batch stacks without padding.
        """
        passes = [
            MaskedPass(t, self._window(t, n_tokens))
            for t in (RESIDUE_OFFSET + r for r in residues)  # never <cls> or <eos>
        ]
        first = self._window(RESIDUE_OFFSET, n_tokens)
        size = self._rows_per_batch(first.end - first.start)
        return [passes[i : i + size] for i in range(0, len(passes), size)]

"""What every PyTorch masked-language-model scorer shares: the model, the device, the passes.

ESM-2 and SaProt are the same transformer with different vocabularies, so scoring a sequence is
the same procedure for both. The cache, the windows and the batching are in
:mod:`mutascope.scoring.marginals`, shared with the ONNX scorer; this class runs the passes in
PyTorch. A subclass supplies only the three things that depend on the vocabulary:

- :meth:`MaskedLMScorer.encode`: the token ids of a sequence (and its structure tokens);
- :meth:`MaskedLMScorer.mask_ids`: which token hides each residue in a masked pass;
- :meth:`MaskedLMScorer.aa_log_probs`: the log-probability of each of the 20 amino acids from
  the logits at one position.
"""

from abc import abstractmethod
from collections.abc import Sequence
from typing import Any

import torch

from mutascope.scoring.base import FloatArray, ScorerConfigError
from mutascope.scoring.cache import LogProbCache
from mutascope.scoring.marginals import RESIDUE_OFFSET, MarginalScorer, ModelSettings
from mutascope.scoring.windows import tiled_windows

__all__ = ["RESIDUE_OFFSET", "MaskedLMScorer", "ModelSettings", "token_id"]


class MaskedLMScorer(MarginalScorer[torch.Tensor]):
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
        super().__init__(settings=settings, cache=cache, cache_id=settings.hf_id)
        self.model = model.eval()
        self.tokenizer = tokenizer  # a Hugging Face ESM tokenizer (untyped upstream)
        self.device = device
        self.cls_id = token_id(tokenizer, "cls_token_id")
        self.eos_id = token_id(tokenizer, "eos_token_id")

    @abstractmethod
    def encode(self, seq: str, struct_tokens: str | None) -> torch.Tensor:
        """Token ids ``<cls> residues <eos>``; fails unless residue ``i`` is token ``i + 1``."""

    @abstractmethod
    def mask_ids(self, tokens: torch.Tensor) -> torch.Tensor:
        """For every token of ``tokens``, the id that replaces it when its residue is masked."""

    @abstractmethod
    def aa_log_probs(self, logits: torch.Tensor) -> torch.Tensor:
        """``(n, 20)`` amino-acid log-probabilities in ``AA_ORDER`` from ``(n, vocab)`` logits."""

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

    @torch.inference_mode()
    def _masked_marginal(self, tokens: torch.Tensor, residues: Sequence[int]) -> FloatArray:
        mask_ids = self.mask_ids(tokens)
        rows: list[torch.Tensor] = []
        for batch in self.masked_batches(tokens.numel(), residues):
            inputs: list[torch.Tensor] = []
            for p in batch:
                masked = tokens[p.window.start : p.window.end].clone()
                masked[p.local] = mask_ids[p.position]
                inputs.append(masked)
            logits = self.forward(torch.stack(inputs))
            at_mask = logits[torch.arange(len(batch)), torch.tensor([p.local for p in batch])]
            rows.append(self.aa_log_probs(at_mask))
        return torch.cat(rows).numpy()

    @torch.inference_mode()
    def _wt_marginal(self, tokens: torch.Tensor) -> FloatArray:
        n_tokens = tokens.numel()
        window = self.settings.window_tokens
        tiles, owner = tiled_windows(n_tokens, n_tokens if window is None else window)
        logits = self.forward(torch.stack([tokens[t.start : t.end] for t in tiles]))
        positions = range(RESIDUE_OFFSET, n_tokens - 1)
        tile = torch.tensor([owner[p] for p in positions])
        local = torch.tensor([p - tiles[owner[p]].start for p in positions])
        return self.aa_log_probs(logits[tile, local]).numpy()

    def forward(self, input_ids: torch.Tensor) -> torch.Tensor:
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

"""SaProt zero-shot scorer: a structure-aware masked language model (Su et al., ICLR 2024).

SaProt is the ESM-2 architecture with a different vocabulary. Each residue is one token made of
two letters: the amino acid (uppercase) and the Foldseek 3Di state of that residue in the
structure (lowercase), e.g. ``Md``. Either letter can be ``#``, "unknown": ``A#`` is alanine
with no structure information, ``#d`` is an unknown amino acid in 3Di state ``d``. The
vocabulary is 5 special tokens followed by the 21 x 21 combinations, amino acid first
(:data:`SAPROT_AA` x :data:`STRUCT_ALPHABET`).

The scoring is ported from ``predict_mut`` in the official repository,
https://github.com/westlake-repl/SaProt, ``model/saprot/saprot_foldseek_mutation_model.py`` at
commit ``e91e4858b55944523f1f8d385f7b96a0d3d34c1d``. ProteinGym's baseline
(``proteingym/baselines/saprot/compute_fitness.py`` at the pinned commit ``1f8de97``), which
produced its ``SaProt_650M_AF2`` scores, contains the same function:

- **Masking.** The mutated residue's token becomes ``#`` + its own 3Di letter: the amino acid is
  hidden and the structure stays visible. ``<mask>`` is never used.
- **Amino-acid probability.** The model predicts combined tokens, so after a softmax over the
  full vocabulary the probability of amino acid ``a`` is the sum over its 21 tokens
  (``probs[pos, a_start : a_start + 21].sum()``). The score is
  ``log(P(mt) / P(wt))``; we keep ``log P`` per amino acid (a log-sum-exp, the same number) so
  the cache and :func:`~mutascope.scoring.base.landscape_from_log_probs` work as for ESM-2.
- **No windows.** Upstream runs the whole sequence in one pass whatever its length (the model
  has rotary position embeddings, so nothing limits it to 1,022 residues). ``window_tokens:
  null`` in the model config reproduces that; ``max_batch_tokens`` only limits how many masked
  copies share a forward call.

Upstream scores one variant per forward pass; here each position is one pass that yields all 20
substitutions, which is the same computation for single mutants.

Structure tokens are an input: one character per residue of ``seq``, a lowercase 3Di letter or
``#`` (pLDDT below the threshold, or no structure at that residue). They are built from the
processed tables by :mod:`mutascope.scoring.struct_tokens`; this module never runs Foldseek.
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
)
from mutascope.scoring.struct_tokens import STRUCT_ALPHABET, UNKNOWN, check_struct_tokens
from mutascope.utils.device import select_device
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

SAPROT_AA = AA_ORDER + UNKNOWN
"""Amino-acid letters of the vocabulary, in token order: the 20 of ``AA_ORDER``, then ``#``."""


class SaprotSettings(ModelSettings):
    """``conf/model/saprot_*.yaml``."""


class SaprotScorer(MaskedLMScorer):
    """Masked-marginal landscapes from a SaProt checkpoint, given a sequence and its 3Di tokens.

    Construct with :func:`from_config` for a pinned Hub checkpoint. The constructor takes the
    model and tokenizer directly so tests can pass small stand-ins.
    """

    def __init__(
        self,
        *,
        settings: SaprotSettings,
        model: torch.nn.Module,
        tokenizer: Any,
        device: torch.device,
        cache: LogProbCache | None = None,
    ) -> None:
        super().__init__(
            settings=settings, model=model, tokenizer=tokenizer, device=device, cache=cache
        )
        vocab: dict[str, int] = dict(tokenizer.get_vocab())
        n = len(STRUCT_ALPHABET)
        missing = [a + s for a in SAPROT_AA for s in STRUCT_ALPHABET if a + s not in vocab]
        if missing:
            raise ScorerConfigError(
                f"tokenizer lacks {len(missing)} combined tokens: {missing[:3]}"
            )
        self.vocab = vocab
        # Upstream reads an amino acid's tokens as the slice [start, start + 21). That is only
        # right if the 21 structure variants are contiguous and in STRUCT_ALPHABET order.
        for aa in SAPROT_AA:
            start = vocab[aa + STRUCT_ALPHABET[0]]
            if [vocab[aa + s] for s in STRUCT_ALPHABET] != list(range(start, start + n)):
                raise ScorerConfigError(f"tokens of {aa!r} are not contiguous in the vocabulary")
        starts = torch.tensor([vocab[aa + STRUCT_ALPHABET[0]] for aa in AA_ORDER])
        self.aa_token_ids = starts[:, None] + torch.arange(n)[None, :]
        """``(20, 21)``: the token ids of each amino acid of ``AA_ORDER``."""
        # Token id -> id of the token with the same 3Di letter and the amino acid hidden.
        masked = torch.arange(max(vocab.values()) + 1)
        for aa in SAPROT_AA:
            for s in STRUCT_ALPHABET:
                masked[vocab[aa + s]] = vocab[UNKNOWN + s]
        self._masked_token = masked

    def encode(self, seq: str, struct_tokens: str | None) -> torch.Tensor:
        """Token ids ``<cls> residues <eos>``, residue ``i`` = ``seq[i] + struct_tokens[i]``.

        The ids are built from the vocabulary and must equal what the tokenizer makes of the
        space-separated tokens (upstream's input), so a tokenizer that splits the string
        differently fails here instead of shifting every position.

        Raises:
            ValueError: No structure tokens, or not one valid token per residue.
        """
        check_sequence(seq)
        if struct_tokens is None:
            raise ValueError("SaProt needs struct_tokens (one 3Di letter or '#' per residue)")
        check_struct_tokens(struct_tokens, len(seq))
        pairs = [aa + s for aa, s in zip(seq, struct_tokens, strict=True)]
        expected = [self.cls_id, *(self.vocab[pair] for pair in pairs), self.eos_id]
        ids = [int(i) for i in self.tokenizer(" ".join(pairs))["input_ids"]]
        if ids != expected:
            raise RuntimeError(
                f"unexpected tokenisation of a {len(seq)}-residue sequence ({len(ids)} tokens): "
                f"residue i must be token i + {RESIDUE_OFFSET}"
            )
        return torch.tensor(ids)

    def mask_ids(self, tokens: torch.Tensor) -> torch.Tensor:
        """``#`` + the token's own 3Di letter (upstream: ``"#" + tokens[pos - 1][-1]``)."""
        return self._masked_token[tokens]

    def aa_log_probs(self, logits: torch.Tensor) -> torch.Tensor:
        """Log of each amino acid's probability summed over its 21 structure tokens."""
        log_probs = torch.log_softmax(logits, dim=-1)
        return torch.logsumexp(log_probs[:, self.aa_token_ids], dim=-1)


def from_config(
    model_cfg: Mapping[str, Any], *, device: str, cache_dir: Path | None
) -> SaprotScorer:
    """Load the pinned checkpoint named by ``model_cfg`` (float32, eval mode) on ``device``."""
    from transformers import EsmForMaskedLM, EsmTokenizer

    settings = SaprotSettings.from_config(model_cfg)
    torch_device = select_device(device)
    # transformers is untyped at this boundary; the scorer validates what it reads from these.
    tokenizer = cast(Any, EsmTokenizer).from_pretrained(settings.hf_id, revision=settings.revision)
    # use_safetensors=False: the repository holds only pytorch_model.bin. Without it,
    # transformers looks for an automatic safetensors conversion on another Hub ref
    # (refs/pr/5), and would load weights that are not the pinned revision.
    model = cast(Any, EsmForMaskedLM).from_pretrained(
        settings.hf_id, revision=settings.revision, dtype=torch.float32, use_safetensors=False
    )
    log.info("model_loaded", model=settings.name, hf_id=settings.hf_id, revision=settings.revision)
    return SaprotScorer(
        settings=settings,
        model=model.to(torch_device),
        tokenizer=tokenizer,
        device=torch_device,
        cache=LogProbCache(cache_dir) if cache_dir is not None else None,
    )

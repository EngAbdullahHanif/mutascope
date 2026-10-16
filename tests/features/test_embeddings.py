"""Per-residue embeddings: which layers, which token, windows, one pass per cache entry.

A two-block ESM model with random weights stands in for the real checkpoints: the checks are
about indexing (token offset, layer choice, tiles), which no trained model is needed for.
"""

from pathlib import Path

import numpy as np
import pytest
import torch
from transformers import EsmConfig, EsmForMaskedLM, EsmTokenizer

from mutascope.alphabet import AA_ORDER
from mutascope.eval.landscapes import LandscapeTarget
from mutascope.eval.region import ScoringRegion
from mutascope.features.embeddings import (
    embedding_key,
    fill_embeddings,
    hidden_size,
    mean_last_layers,
)
from mutascope.scoring.cache import LogProbCache, embedding_method
from mutascope.scoring.esm2 import Esm2Scorer, Esm2Settings
from mutascope.scoring.windows import tiled_windows
from mutascope.utils.seeding import seed_all

ESM_VOCAB = ["<cls>", "<pad>", "<eos>", "<unk>", *"LAGVSERTIDPKQNFYMHWCXBUZO", ".", "-",
             "<null_1>", "<mask>"]  # fmt: skip
BLOCKS = 5


@pytest.fixture(scope="module")
def tokenizer(tmp_path_factory: pytest.TempPathFactory) -> EsmTokenizer:
    vocab = tmp_path_factory.mktemp("vocab") / "vocab.txt"
    vocab.write_text("\n".join(ESM_VOCAB))
    return EsmTokenizer(vocab_file=str(vocab))


@pytest.fixture(scope="module")
def model() -> EsmForMaskedLM:
    seed_all(0)
    config = EsmConfig(
        vocab_size=len(ESM_VOCAB), hidden_size=16, num_hidden_layers=BLOCKS,
        num_attention_heads=2, intermediate_size=32, position_embedding_type="rotary",
        token_dropout=True, mask_token_id=32, pad_token_id=1,
    )  # fmt: skip
    return EsmForMaskedLM(config).eval()


class _Counting(torch.nn.Module):
    """Passes through to the model and counts forward calls."""

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model, self.calls = model, 0
        self.config = model.config  # pyright: ignore[reportAttributeAccessIssue]

    def forward(self, **kwargs: torch.Tensor | bool) -> object:
        self.calls += 1
        return self.model(**kwargs)


def _scorer(tokenizer: EsmTokenizer, model: torch.nn.Module, window: int = 1024) -> Esm2Scorer:
    settings = Esm2Settings("tiny", "hub/tiny", "0" * 40, batch_size=4, window_tokens=window)
    return Esm2Scorer(
        settings=settings, model=model, tokenizer=tokenizer, device=torch.device("cpu")
    )


def _sequence(length: int, seed: int) -> str:
    rng = np.random.default_rng(seed)
    return "".join(rng.choice(list(AA_ORDER), size=length))


@torch.inference_mode()
def _hidden(model: EsmForMaskedLM, ids: torch.Tensor) -> tuple[torch.Tensor, ...]:
    out = model(input_ids=ids[None], attention_mask=torch.ones_like(ids[None]),
                output_hidden_states=True)  # fmt: skip
    return out.hidden_states


def test_the_mean_of_the_last_four_hidden_states_at_token_i_plus_1(
    tokenizer: EsmTokenizer, model: EsmForMaskedLM
) -> None:
    """``hidden_states`` has one entry per block plus the embeddings; the last is after the
    final LayerNorm. Residue i is token i + 1: reading token i or averaging other layers
    gives other numbers."""
    seq = _sequence(30, seed=1)
    scorer = _scorer(tokenizer, model)
    tokens = scorer.encode(seq, None)
    hidden = _hidden(model, tokens)
    assert len(hidden) == BLOCKS + 1
    with torch.inference_mode():
        last = model.esm(input_ids=tokens[None]).last_hidden_state  # after the final LayerNorm
    np.testing.assert_allclose(hidden[-1], last, rtol=1e-5, atol=1e-6)
    expected = torch.stack(hidden[-4:]).mean(dim=0)[0, 1:-1].numpy()
    embedding = mean_last_layers(scorer, tokens, layers=4)
    assert embedding.shape == (30, 16) and embedding.dtype == np.float32
    np.testing.assert_allclose(embedding, expected, rtol=1e-5, atol=1e-6)
    shifted = torch.stack(hidden[-4:]).mean(dim=0)[0, :-2].numpy()
    assert not np.allclose(embedding, shifted, atol=1e-3)
    three = torch.stack(hidden[-3:]).mean(dim=0)[0, 1:-1].numpy()
    assert not np.allclose(embedding, three, atol=1e-3)


def test_a_long_sequence_reads_each_residue_from_its_most_central_tile(
    tokenizer: EsmTokenizer, model: EsmForMaskedLM
) -> None:
    """Window of 12 tokens over a 40-residue sequence: residue p's row is what the model gives
    for token p + 1 when it sees only that residue's tile, computed here tile by tile."""
    seq = _sequence(40, seed=2)
    scorer = _scorer(tokenizer, model, window=12)
    tokens = scorer.encode(seq, None)
    tiles, owner = tiled_windows(tokens.numel(), 12)
    assert len(tiles) > 3
    embedding = mean_last_layers(scorer, tokens, layers=2)
    for residue in range(40):
        tile = tiles[owner[residue + 1]]
        hidden = _hidden(model, tokens[tile.start : tile.end])
        local = residue + 1 - tile.start
        expected = torch.stack(hidden[-2:]).mean(dim=0)[0, local].numpy()
        np.testing.assert_allclose(embedding[residue], expected, rtol=1e-5, atol=1e-6)
    unwindowed = mean_last_layers(_scorer(tokenizer, model), tokens, layers=2)
    assert not np.allclose(embedding, unwindowed, atol=1e-3)


def test_more_layers_than_the_model_has_fails(
    tokenizer: EsmTokenizer, model: EsmForMaskedLM
) -> None:
    scorer = _scorer(tokenizer, model)
    with pytest.raises(ValueError, match="cannot average the last 6"):
        mean_last_layers(scorer, scorer.encode("MKV", None), layers=BLOCKS + 1)
    with pytest.raises(ValueError, match="at least 1"):
        embedding_method(0)


def _targets(seq: str) -> list[LandscapeTarget]:
    """Two assays of one protein (one cache entry) and a region of it (another entry)."""
    full = ScoringRegion("A", 0, len(seq), len(seq))
    part = ScoringRegion("C", 2, 8, len(seq))
    return [
        LandscapeTarget("A", full, seq, None, benchmark=True),
        LandscapeTarget("B", ScoringRegion("B", 0, len(seq), len(seq)), seq, None, benchmark=True),
        LandscapeTarget("C", part, part.sequence(seq), None, benchmark=True),
    ]


def test_one_pass_per_cache_entry_and_none_on_a_rerun(
    tokenizer: EsmTokenizer, model: EsmForMaskedLM, tmp_path: Path
) -> None:
    counting = _Counting(model)
    scorer = _scorer(tokenizer, counting)
    cache = LogProbCache(tmp_path, width=hidden_size(scorer))
    targets = _targets(_sequence(20, seed=3))
    report = fill_embeddings(scorer, cache, targets, layers=4)
    assert counting.calls == 2
    assert report["computed"].tolist() == [True, False, True]
    assert report["region"].tolist() == ["full", "full", "msa:3-8"]
    stored = cache.get(embedding_key(scorer, targets[0], 4))
    assert stored is not None
    np.testing.assert_array_equal(
        stored, mean_last_layers(scorer, scorer.encode(targets[0].sequence, None), 4)
    )
    rerun = fill_embeddings(scorer, cache, targets, layers=4)
    assert counting.calls == 3  # the direct call above; the rerun ran nothing
    assert not rerun["computed"].any()
    assert len(cache.computations()) == 2


def test_a_cache_of_another_width_is_refused(
    tokenizer: EsmTokenizer, model: EsmForMaskedLM, tmp_path: Path
) -> None:
    scorer = _scorer(tokenizer, model)
    with pytest.raises(ValueError, match="cache width 20"):
        fill_embeddings(scorer, LogProbCache(tmp_path), _targets("MKVARNDCQE"), layers=4)


def test_the_embedding_key_differs_from_the_log_probability_keys(
    tokenizer: EsmTokenizer, model: EsmForMaskedLM
) -> None:
    key = embedding_key(_scorer(tokenizer, model), _targets("MKVARNDCQE")[0], 4)
    assert key.method == "embedding_mean_last4"
    assert key.method not in ("masked", "wt")

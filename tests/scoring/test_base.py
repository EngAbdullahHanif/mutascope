"""Landscape helpers, mutant lookup and the registry."""

from pathlib import Path

import numpy as np
import pytest

from mutascope.alphabet import AA_ORDER
from mutascope.scoring.base import (
    ScorerConfigError,
    build_scorer,
    check_sequence,
    landscape_from_log_probs,
    score_mutants,
)


def test_landscape_subtracts_the_wild_type() -> None:
    rng = np.random.default_rng(0)
    seq = "MKV"
    log_probs = rng.normal(size=(3, 20)).astype(np.float32)
    landscape = landscape_from_log_probs(log_probs, seq)
    for i, wt in enumerate(seq):
        assert landscape[i, AA_ORDER.index(wt)] == 0
    assert landscape[1, AA_ORDER.index("A")] == pytest.approx(
        log_probs[1, AA_ORDER.index("A")] - log_probs[1, AA_ORDER.index("K")]
    )


def test_score_mutants_is_one_indexed_and_checks_the_wild_type() -> None:
    landscape = np.zeros((3, 20), dtype=np.float32)
    landscape[1, AA_ORDER.index("A")] = 2.5  # residue 2 (K) -> A
    assert score_mutants(landscape, "MKV", ["K2A"]).tolist() == [2.5]
    with pytest.raises(ValueError, match="row 0: M2A but the sequence has K"):
        score_mutants(landscape, "MKV", ["M2A"])
    with pytest.raises(ValueError, match="out of range"):
        score_mutants(landscape, "MKV", ["M9A"])


@pytest.mark.parametrize("seq", ["", "MKX", "mkv"])
def test_non_canonical_sequences_are_rejected(seq: str) -> None:
    with pytest.raises(ValueError):
        check_sequence(seq)


def test_unknown_scorer_is_an_error() -> None:
    with pytest.raises(ScorerConfigError, match="unknown scorer 'nope'"):
        build_scorer({"scorer": "nope"}, device="cpu", cache_dir=Path("unused"))

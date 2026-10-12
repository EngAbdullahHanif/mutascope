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
    mutant_positions,
    resolve_positions,
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
    with pytest.raises(ValueError, match="row 0: M2A but the scored sequence has K"):
        score_mutants(landscape, "MKV", ["M2A"])
    with pytest.raises(ValueError, match="no residue there"):
        score_mutants(landscape, "MKV", ["M9A"])


def test_score_mutants_with_an_offset_reads_region_coordinates() -> None:
    """A region starting at target residue 11 (offset 10): K12A is row 1 of the region."""
    landscape = np.zeros((3, 20), dtype=np.float32)
    landscape[1, AA_ORDER.index("A")] = 2.5
    assert score_mutants(landscape, "MKV", ["K12A"], offset=10).tolist() == [2.5]
    with pytest.raises(ValueError, match="no residue there"):
        score_mutants(landscape, "MKV", ["K2A"], offset=10)


@pytest.mark.parametrize("seq", ["", "MKX", "mkv"])
def test_non_canonical_sequences_are_rejected(seq: str) -> None:
    with pytest.raises(ValueError):
        check_sequence(seq)


def test_unknown_scorer_is_an_error() -> None:
    with pytest.raises(ScorerConfigError, match="unknown scorer 'nope'"):
        build_scorer({"scorer": "nope"}, device="cpu", cache_dir=Path("unused"))


def test_resolve_positions_sorts_dedupes_and_checks_the_range() -> None:
    assert resolve_positions(None, 3) == [0, 1, 2]
    assert resolve_positions([2, 0, 2], 3) == [0, 2]
    assert resolve_positions([], 3) == []
    for bad in ([3], [-1, 1]):
        with pytest.raises(ValueError, match=r"within 0\.\.2"):
            resolve_positions(bad, 3)


def test_mutant_positions_are_distinct_zero_indexed_and_checked() -> None:
    assert mutant_positions("MKV", ["V3A", "M1A", "V3C"]) == [0, 2]
    assert mutant_positions("KV", ["V3A"], offset=1) == [1]  # region starting at residue 2
    with pytest.raises(ValueError, match="row 1: M2A but the scored sequence has K"):
        mutant_positions("MKV", ["V3A", "M2A"])


def test_an_unscored_row_stays_nan_in_the_landscape() -> None:
    log_probs = np.zeros((3, 20), dtype=np.float32)
    log_probs[1] = np.nan
    landscape = landscape_from_log_probs(log_probs, "MKV")
    assert np.isnan(landscape[1]).all()  # wild-type column too: "not scored" is never 0
    assert np.isfinite(landscape[[0, 2]]).all()

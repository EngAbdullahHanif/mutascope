"""The ensemble: z-score within an assay, then the mean over members."""

import numpy as np
import pandas as pd
import pytest

from mutascope.eval.metrics import spearman
from mutascope.scoring.ensemble import zscore_mean

MUTANTS = ["M1A", "K2C", "V3G", "A4C"]


def _scores(model: str, by_assay: dict[str, list[float]]) -> pd.DataFrame:
    frames = [
        pd.DataFrame({"assay_id": assay, "mutant": MUTANTS[: len(v)], "model": model, "score": v})
        for assay, v in by_assay.items()
    ]
    return pd.concat(frames, ignore_index=True)


def test_each_member_is_standardised_within_its_assay_before_the_mean() -> None:
    a = _scores("a", {"X": [1.0, 2.0, 3.0, 4.0], "Y": [10.0, 30.0]})
    b = _scores("b", {"X": [8.0, 4.0, 6.0, 2.0], "Y": [-1.0, 1.0]})
    out = zscore_mean({"a": a, "b": b}, "ens")
    assert list(out.columns) == ["assay_id", "mutant", "model", "score"]
    assert set(out["model"]) == {"ens"}
    x = out[out["assay_id"] == "X"].set_index("mutant")["score"]
    za = (np.array([1.0, 2.0, 3.0, 4.0]) - 2.5) / np.std([1.0, 2.0, 3.0, 4.0])
    zb = (np.array([8.0, 4.0, 6.0, 2.0]) - 5.0) / np.std([8.0, 4.0, 6.0, 2.0])
    assert x[MUTANTS].to_numpy() == pytest.approx((za + zb) / 2)
    # Y: both members rank K2C above M1A, with z-scores of exactly -1 and +1.
    y = out[out["assay_id"] == "Y"].set_index("mutant")["score"]
    assert y["M1A"] == pytest.approx(-1.0) and y["K2C"] == pytest.approx(1.0)


def test_a_member_with_a_wider_spread_does_not_decide_the_ranking() -> None:
    """Averaging raw scores would follow the member with scores 1,000 times larger; after
    z-scoring the two count equally, so rescaling a member changes nothing."""
    small = np.array([1.0, 2.0, 3.0, 5.0])
    large = np.array([2.0, 1.0, 4.0, 3.0])
    plain = zscore_mean(
        {"a": _scores("a", {"X": list(small)}), "b": _scores("b", {"X": list(large)})}, "ens"
    )
    scaled = zscore_mean(
        {"a": _scores("a", {"X": list(small)}), "b": _scores("b", {"X": list(1000 * large + 7)})},
        "ens",
    )
    assert scaled["score"].to_numpy() == pytest.approx(plain["score"].to_numpy())
    raw_mean = (small + 1000 * large) / 2
    by_mutant = scaled.set_index("mutant")["score"][MUTANTS].to_numpy()
    assert spearman(raw_mean, large) == pytest.approx(1.0)
    assert spearman(by_mutant, large) < 1.0


def test_row_order_of_a_member_does_not_matter() -> None:
    a = _scores("a", {"X": [1.0, 2.0, 3.0, 5.0]})
    b = _scores("b", {"X": [2.0, 1.0, 4.0, 3.0]})
    shuffled = b.sample(frac=1, random_state=0)
    first = zscore_mean({"a": a, "b": b}, "ens")
    second = zscore_mean({"a": a, "b": shuffled}, "ens")
    pd.testing.assert_frame_equal(first, second)


def test_members_must_cover_the_same_variants() -> None:
    a = _scores("a", {"X": [1.0, 2.0, 3.0, 4.0]})
    with pytest.raises(ValueError, match="not on the same variants"):
        zscore_mean({"a": a, "b": _scores("b", {"X": [1.0, 2.0, 3.0]})}, "ens")
    with pytest.raises(ValueError, match="not on the same variants"):
        zscore_mean({"a": a, "b": _scores("b", {"Z": [1.0, 2.0, 3.0, 4.0]})}, "ens")


def test_constant_scores_and_a_single_member_are_refused() -> None:
    a = _scores("a", {"X": [1.0, 2.0, 3.0, 4.0]})
    flat = _scores("b", {"X": [2.0, 2.0, 2.0, 2.0]})
    with pytest.raises(ValueError, match=r"b: constant scores, no z-score, in \['X'\]"):
        zscore_mean({"a": a, "b": flat}, "ens")
    with pytest.raises(ValueError, match="at least two members"):
        zscore_mean({"a": a}, "ens")

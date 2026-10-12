"""Per-assay metrics agree with ProteinGym's code, and undefined values are NaN, never 0."""

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays
from sklearn.metrics import (  # pyright: ignore[reportMissingTypeStubs]
    matthews_corrcoef,  # pyright: ignore[reportUnknownVariableType]
)
from tests.eval.proteingym_reference import calc_ndcg, calc_toprecall

from mutascope.eval.metrics import (
    METRICS,
    MetricInputError,
    assay_metrics,
    auc,
    mcc,
    ndcg,
    spearman,
    top_recall,
)

# Values drawn from a small grid so ties (the hard case for ranks and percentiles) are common.
_grid = st.integers(min_value=-20, max_value=20).map(lambda i: i / 4)


@st.composite
def score_pairs(draw: st.DrawFn, min_size: int = 10) -> tuple[np.ndarray, np.ndarray]:
    n = draw(st.integers(min_value=min_size, max_value=200))
    y_true = draw(arrays(np.float64, n, elements=_grid))
    y_score = draw(arrays(np.float64, n, elements=_grid))
    return y_true, y_score


@given(score_pairs())
@settings(max_examples=300)
def test_ndcg_matches_proteingym(pair: tuple[np.ndarray, np.ndarray]) -> None:
    y_true, y_score = pair
    ours = ndcg(y_true, y_score)
    if np.ptp(y_true) == 0:
        assert math.isnan(ours)
    else:
        assert ours == pytest.approx(calc_ndcg(y_true, y_score), abs=1e-12)


@given(score_pairs(min_size=1))
@settings(max_examples=300)
def test_top_recall_matches_proteingym(pair: tuple[np.ndarray, np.ndarray]) -> None:
    y_true, y_score = pair
    assert top_recall(y_true, y_score) == pytest.approx(calc_toprecall(y_true, y_score))


def test_ndcg_is_one_for_a_perfect_ranking_and_nan_below_ten_variants() -> None:
    y = np.arange(30, dtype=np.float64)
    assert ndcg(y, y) == pytest.approx(1.0)
    assert ndcg(y, -y) < 0.2
    # k = floor(9 * 10%) = 0: ProteinGym returns 0, which would read as "worst possible".
    assert calc_ndcg(y[:9], y[:9]) == 0
    assert math.isnan(ndcg(y[:9], y[:9]))


def test_ndcg_uses_continuous_gains() -> None:
    """Gains are min-max scaled DMS scores, not binary labels: swapping the two best variants
    costs more when their scores are far apart."""
    close = np.array([10.0, 9.9, *range(8, -10, -1)])
    far = np.array([10.0, 2.0, *range(1, -17, -1)])
    swap = np.array([1.0, 2.0, *range(-1, -19, -1)])  # model ranks variant 1 above variant 0
    assert ndcg(close, swap) > ndcg(far, swap)


def test_mcc_binarises_the_model_at_its_median_inclusive() -> None:
    """Scores >= median predict 1 (ProteinGym: ``1 if x >= median_cutoff else 0``)."""
    y_bin = np.array([0, 0, 1, 1, 1])
    y_score = np.array([0.0, 1.0, 2.0, 2.0, 3.0])  # median 2.0 -> predictions 0 0 1 1 1
    assert mcc(y_bin, y_score) == pytest.approx(1.0)


def test_single_label_class_gives_nan_not_zero() -> None:
    """F7YBW8_MESOW_Aakre_2015's 37 single mutants all have DMS_score_bin = 1."""
    y_bin = np.ones(37, dtype=np.int64)
    y_score = np.linspace(-3, 1, 37)
    y_pred = (y_score >= np.median(y_score)).astype(np.int64)
    assert matthews_corrcoef(y_bin, y_pred) == 0  # what sklearn (and so ProteinGym) reports
    assert math.isnan(mcc(y_bin, y_score))
    assert math.isnan(auc(y_bin, y_score))


def test_constant_predictions_give_nan() -> None:
    y_true = np.arange(20, dtype=np.float64)
    y_bin = (y_true >= 10).astype(np.int64)
    flat = np.zeros(20)
    assert math.isnan(spearman(y_true, flat))
    assert math.isnan(mcc(y_bin, flat))
    assert auc(y_bin, flat) == pytest.approx(0.5)  # defined: every pair is a tie


def test_spearman_and_auc_known_values() -> None:
    y_true = np.array([1.0, 2.0, 3.0, 4.0])
    assert spearman(y_true, np.array([1.0, 3.0, 2.0, 4.0])) == pytest.approx(0.8)
    assert auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.4, 0.35, 0.8])) == pytest.approx(0.75)


def test_assay_metrics_returns_every_metric() -> None:
    y = np.arange(50, dtype=np.float64)
    result = assay_metrics(y, (y >= 25).astype(np.int64), y)
    assert tuple(result) == METRICS
    assert all(v == pytest.approx(1.0) for v in result.values())


@pytest.mark.parametrize(
    ("y_true", "y_score"),
    [
        (np.zeros(3), np.zeros(4)),
        (np.zeros(0), np.zeros(0)),
        (np.zeros(3), np.array([0.0, np.nan, 1.0])),
    ],
    ids=["length", "empty", "nan"],
)
def test_malformed_inputs_fail_loudly(y_true: np.ndarray, y_score: np.ndarray) -> None:
    with pytest.raises(MetricInputError):
        spearman(y_true, y_score)


def test_labels_must_be_binary() -> None:
    with pytest.raises(MetricInputError, match="0 or 1"):
        auc(np.array([0, 2, 1]), np.array([0.0, 1.0, 2.0]))

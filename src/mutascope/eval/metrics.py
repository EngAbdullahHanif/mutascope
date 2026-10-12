"""Per-assay metrics, matching ProteinGym's ``performance_DMS_benchmarks.py``.

Ported from ProteinGym at the pinned commit (``data.release.git_commit``,
``proteingym/performance_DMS_benchmarks.py``): the same library calls (scipy ``spearmanr``,
sklearn ``roc_auc_score`` / ``matthews_corrcoef``), the same binarisation and the same NDCG and
top-recall definitions. Both arrays are "higher = fitter" (``DMS_score``) and "higher = predicted
fitter" (the model score); nothing is sign-flipped here.

One deliberate difference: a metric that is undefined for an assay is NaN, never 0. ProteinGym
returns 0 in two such cases (MCC with a single label class, where sklearn returns 0, and NDCG
when the top 10% holds no variant, i.e. fewer than 10 variants); MutaScope returns NaN so an
undefined value cannot pull an average down (ADR 0011). Where ProteinGym itself gives NaN (AUC
with one class, Spearman of a constant), so do we.
"""

import warnings
from collections.abc import Mapping
from typing import Any, Final, cast

import numpy as np
import numpy.typing as npt
from scipy.stats import (  # pyright: ignore[reportMissingTypeStubs]
    ConstantInputWarning,
    spearmanr,  # pyright: ignore[reportUnknownVariableType]
)
from sklearn.metrics import (  # pyright: ignore[reportMissingTypeStubs]
    matthews_corrcoef,  # pyright: ignore[reportUnknownVariableType]
    roc_auc_score,  # pyright: ignore[reportUnknownVariableType]
)

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

METRICS: Final = ("spearman", "auc", "mcc", "ndcg", "top_recall")
"""Metric names in report order. Spearman is the primary metric."""

DEFAULT_TOP_PERCENT: Final = 10.0
"""ProteinGym's default for NDCG (``top=10``) and top recall (``top_true = top_model = 10``)."""


class MetricInputError(ValueError):
    """The inputs to a metric are malformed (lengths differ, NaN, labels not 0/1)."""


def spearman(y_true: FloatArray, y_score: FloatArray) -> float:
    """Spearman's rho with average ranks for ties (scipy). NaN if either input is constant."""
    _check(y_true, y_score)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConstantInputWarning)
        result: Any = spearmanr(y_true, y_score)
    return float(result.statistic)


def auc(y_bin: IntArray, y_score: FloatArray) -> float:
    """ROC AUC of the model score against ``DMS_score_bin``. NaN with a single label class.

    ProteinGym catches sklearn's ``ValueError`` for one class and records NaN; we check for it
    up front instead of catching every exception.
    """
    _check_labels(y_bin, y_score)
    if np.unique(y_bin).size < 2:
        return float("nan")
    # sklearn's inferred return type is partly unknown; it is a numpy float.
    return float(cast("float", roc_auc_score(y_bin, y_score)))


def mcc(y_bin: IntArray, y_score: FloatArray) -> float:
    """Matthews correlation of the median-binarised model score against ``DMS_score_bin``.

    ProteinGym binarises the model at its own median: ``1 if score >= median else 0``. The
    labels come from the assay file, binarised by ProteinGym at ``DMS_binarization_cutoff``.

    MCC is undefined (0/0) when the labels or the predictions have a single class. sklearn
    returns 0 there, and so does ProteinGym; we return NaN (ADR 0011).
    """
    _check_labels(y_bin, y_score)
    y_pred = (y_score >= np.median(y_score)).astype(np.int64)
    if np.unique(y_bin).size < 2 or np.unique(y_pred).size < 2:
        return float("nan")
    score: Any = matthews_corrcoef(y_bin, y_pred)
    return float(score)


def ndcg(
    y_true: FloatArray, y_score: FloatArray, top_percent: float = DEFAULT_TOP_PERCENT
) -> float:
    """NDCG of the model's top ``top_percent`` %, with min-max scaled ``DMS_score`` as gains.

    ProteinGym's ``calc_ndcg`` (quantile mode): ``k = floor(n * top_percent / 100)``; gains are
    the continuous ``DMS_score`` scaled to [0, 1]; a variant's rank is its position when sorted
    by descending model score (``argsort(argsort(-score)) + 1``, so ties are broken by numpy's
    default sort, exactly as upstream). DCG sums ``gain / log2(rank + 1)`` over the model's top
    k; the ideal DCG does the same over the true top k.

    NaN when k is 0 (fewer than ``100 / top_percent`` variants; ProteinGym returns 0) or when
    ``y_true`` is constant (no gains to rank; NaN upstream too).
    """
    _check(y_true, y_score)
    k = int(np.floor(y_true.shape[0] * (top_percent / 100)))
    span = np.max(y_true) - np.min(y_true)
    if k == 0 or span == 0:
        return float("nan")
    gains = (y_true - np.min(y_true)) / span
    idcg = _dcg(gains, gains, k)  # > 0: the best variant has gain 1 and rank 1
    return _dcg(gains, y_score, k) / idcg


def _dcg(gains: FloatArray, order_by: FloatArray, k: int) -> float:
    """Discounted gain of the k variants ranked highest by ``order_by``."""
    ranks = np.argsort(np.argsort(-order_by)) + 1
    top = ranks <= k
    return float(np.sum(gains[top] / np.log2(ranks[top] + 1)))


def top_recall(
    y_true: FloatArray, y_score: FloatArray, top_percent: float = DEFAULT_TOP_PERCENT
) -> float:
    """Fraction of the truly top ``top_percent`` % variants that the model also ranks top.

    ProteinGym's ``calc_toprecall``: "top" means ``>=`` the ``100 - top_percent`` percentile
    (numpy's default linear interpolation), for the true and the model scores separately, so
    ties at the threshold can make either set larger than ``top_percent`` %.
    """
    _check(y_true, y_score)
    true_top = y_true >= np.percentile(y_true, 100 - top_percent)
    model_top = y_score >= np.percentile(y_score, 100 - top_percent)
    # Never empty (the maximum is always >= the percentile); ProteinGym's 0 fallback is dead code.
    return float(np.sum(true_top & model_top) / np.sum(true_top))


def assay_metrics(
    y_true: FloatArray,
    y_bin: IntArray,
    y_score: FloatArray,
    top_percent: float = DEFAULT_TOP_PERCENT,
) -> Mapping[str, float]:
    """All of :data:`METRICS` for one model on one assay, NaN where undefined."""
    return {
        "spearman": spearman(y_true, y_score),
        "auc": auc(y_bin, y_score),
        "mcc": mcc(y_bin, y_score),
        "ndcg": ndcg(y_true, y_score, top_percent),
        "top_recall": top_recall(y_true, y_score, top_percent),
    }


def _check(y_true: npt.NDArray[np.generic], y_score: FloatArray) -> None:
    """Fail loudly on inputs no metric should silently accept."""
    if y_true.shape != y_score.shape or y_true.ndim != 1:
        raise MetricInputError(f"shapes differ or are not 1-D: {y_true.shape} vs {y_score.shape}")
    if y_true.size == 0:
        raise MetricInputError("no variants")
    if np.isnan(y_score).any() or np.isnan(y_true.astype(np.float64)).any():
        raise MetricInputError("NaN in scores: every variant must be scored")


def _check_labels(y_bin: IntArray, y_score: FloatArray) -> None:
    _check(y_bin, y_score)
    if not np.isin(y_bin, (0, 1)).all():
        raise MetricInputError("binary labels must be 0 or 1")

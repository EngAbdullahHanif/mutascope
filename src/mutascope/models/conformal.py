"""Cross-conformal (CV+) prediction intervals, with absolute and normalised scores (ADR 0024).

**CV+** (Barber, Candès, Ramdas & Tibshirani, 2021, *Ann. Statist.* 49:486, "Predictive inference
with the jackknife+", section 3 / algorithm "CV+"). The calibration rows are split into K folds.
For each fold k a fresh model ``M_k`` is fit on the other folds, and each row ``i`` of fold k gets
the residual ``R_i = |y_i - M_k(x_i)|`` from the one model that never saw it. At a new point x
the interval is::

    lower = the floor(a (n + 1))-th smallest of  M_k(i)(x) - R_i
    upper = the ceil((1 - a)(n + 1))-th smallest of  M_k(i)(x) + R_i

over the n calibration rows, for miscoverage ``a`` (level ``1 - a``). Each row is scored by its
own fold's model, so no model is ever judged on rows it trained on, and nothing is left out of
training as split conformal would. The guarantee is ``>= 1 - 2a - sqrt(2 / n)``-ish in theory
and about ``1 - a`` in practice (Barber et al., section 5).

**Absolute vs normalised.** With the absolute residual the interval is nearly constant-width (it
widens only where the K models disagree at x). The normalised (adaptive) version divides each
residual by a difficulty score and multiplies it back at x: ``R_i = |y_i - M_k(x_i)| /
(s_k(x_i) + eps)`` and ``M_k(x) +/- R_i (s_k(x) + eps)``. The difficulty is a **fold-ensemble
spread**: the standard deviation of the predictions of a model's fold ensemble (one member per
training fold, fit without it). It must be the spread of ``M_k``'s own ensemble, ``s_k``: the
spread of the K CV+ models themselves cannot score a calibration row, because K - 1 of them
trained on it, so it would be systematically smaller there than at a new point. ``s_k`` is a
function fitted without fold k, like ``M_k``, so the CV+ argument holds unchanged.

A model with a :class:`FoldEnsembled` hook (ridge: its inner-CV fits at the chosen penalty) gives
its ensemble for free; any other model gets one member fit per training fold.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

import numpy as np

from mutascope.features.matrix import FeatureMatrix, FloatArray, FloatMatrix, IntArray

INTERVALS: Final = ("absolute", "normalized")
"""Interval types: (a) constant width, from absolute residuals; (b) adaptive, from residuals
divided by the fold-ensemble spread plus ``epsilon``."""

_CHUNK_ELEMENTS = 2**22
"""Rows x calibration values held at once when taking order statistics (32 MB of float64)."""


class ConformalError(ValueError):
    """Intervals cannot be computed as asked (too few calibration rows or folds, bad level)."""


class _Model(Protocol):
    def fit(self, x: FeatureMatrix, y: FloatArray, groups: IntArray) -> object: ...

    def predict(self, x: FeatureMatrix) -> FloatArray: ...


@runtime_checkable
class FoldEnsembled(Protocol):
    """A fitted model that already holds its fold ensemble (one member per training fold)."""

    def fold_predict(self, x: FeatureMatrix) -> FloatMatrix:
        """``(members, rows)``: each member was fit without one of the training folds."""
        ...


ModelFactory = Callable[[], _Model]


def spread(predictions: FloatMatrix) -> FloatArray:
    """Fold-ensemble spread: the sample standard deviation over members (rows) per column."""
    if predictions.shape[0] < 2:
        raise ConformalError(f"a spread needs at least 2 members, got {predictions.shape[0]}")
    return np.asarray(np.std(predictions, axis=0, ddof=1), dtype=np.float64)


@dataclass(frozen=True)
class EnsembledModel:
    """A fitted model and its fold ensemble.

    ``members`` is empty when the model provides the ensemble itself (:class:`FoldEnsembled`).
    """

    model: _Model
    members: tuple[_Model, ...]

    @classmethod
    def fit(
        cls, x: FeatureMatrix, y: FloatArray, groups: IntArray, make_model: ModelFactory
    ) -> "EnsembledModel":
        """Fit ``make_model()`` on all rows; fit one member per group too if it has no hook."""
        model = make_model()
        model.fit(x, y, groups)
        if isinstance(model, FoldEnsembled):
            return cls(model=model, members=())
        members: list[_Model] = []
        for group in np.unique(groups):
            keep = groups != group
            member = make_model()
            member.fit(x.take(keep), y[keep], groups[keep])
            members.append(member)
        return cls(model=model, members=tuple(members))

    def predict(self, x: FeatureMatrix) -> FloatArray:
        return np.asarray(self.model.predict(x), dtype=np.float64)

    def spread(self, x: FeatureMatrix) -> FloatArray:
        """The fold-ensemble spread of this model at each row of ``x``."""
        if isinstance(self.model, FoldEnsembled):
            return spread(self.model.fold_predict(x))
        return spread(np.stack([np.asarray(m.predict(x), dtype=np.float64) for m in self.members]))


@dataclass(frozen=True)
class Calibration:
    """What CV+ keeps from its calibration rows, in row order.

    These are the residuals and spreads Task 8 needs for the all-data models (ADR 0024).
    """

    fold: IntArray
    """Index (0..K-1) of the calibration fold each row is in, i.e. of the model that scored it."""
    prediction: FloatArray
    """``M_k(x_i)`` from the model fit without the row's fold."""
    spread: FloatArray
    """``s_k(x_i)``: the spread of that model's fold ensemble at the row."""
    target: FloatArray

    def scores(self, interval: str, epsilon: float) -> FloatArray:
        """Conformity score of every row: absolute residual, or divided by spread + epsilon."""
        residual = np.abs(self.target - self.prediction)
        if interval == "absolute":
            return residual
        if interval == "normalized":
            return residual / (self.spread + epsilon)
        raise ConformalError(f"unknown interval {interval!r}; use one of {INTERVALS}")


@dataclass(frozen=True)
class FoldPredictions:
    """The K calibration models at new rows: ``(K, rows)`` predictions and spreads."""

    prediction: FloatMatrix
    spread: FloatMatrix

    def ensemble_spread(self) -> FloatArray:
        """Spread of the K fold models themselves: the difficulty score of a new variant."""
        return spread(self.prediction)


@dataclass(frozen=True)
class CVPlus:
    """K models, each fit without one calibration fold, and their calibration residuals."""

    models: tuple[EnsembledModel, ...]
    groups: tuple[int, ...]
    """The calibration fold labels, in model order."""
    calibration: Calibration

    @classmethod
    def fit(
        cls, x: FeatureMatrix, y: FloatArray, groups: IntArray, make_model: ModelFactory
    ) -> "CVPlus":
        """Calibrate on ``(x, y)`` with calibration folds ``groups``.

        Each ``M_k`` gets the remaining rows' ``groups`` as its own folds, so a model that tunes
        itself by inner CV (ridge's penalty, the MLP's early stopping) splits the same way.

        Raises:
            ConformalError: Fewer than 3 calibration folds (``M_k`` needs 2 folds of its own for
                an inner CV and a fold-ensemble spread).
        """
        labels = np.unique(groups)
        if labels.size < 3:
            raise ConformalError(f"CV+ needs at least 3 calibration folds, got {labels.tolist()}")
        n = y.shape[0]
        fold = np.full(n, -1, dtype=np.int64)
        prediction = np.full(n, np.nan)
        spreads = np.full(n, np.nan)
        models: list[EnsembledModel] = []
        for index, label in enumerate(labels):
            held_out = groups == label
            fitted = EnsembledModel.fit(
                x.take(~held_out), y[~held_out], groups[~held_out], make_model
            )
            scored = x.take(held_out)
            fold[held_out] = index
            prediction[held_out] = fitted.predict(scored)
            spreads[held_out] = fitted.spread(scored)
            models.append(fitted)
        if not (np.isfinite(prediction).all() and np.isfinite(spreads).all()):
            raise ConformalError("a calibration model returned a non-finite prediction or spread")
        return cls(
            models=tuple(models),
            groups=tuple(int(g) for g in labels),
            calibration=Calibration(
                fold=fold, prediction=prediction, spread=spreads, target=y.astype(np.float64)
            ),
        )

    def predict(self, x: FeatureMatrix) -> FoldPredictions:
        """Each calibration model's prediction and fold-ensemble spread at the rows of ``x``."""
        return FoldPredictions(
            prediction=np.stack([m.predict(x) for m in self.models]),
            spread=np.stack([m.spread(x) for m in self.models]),
        )

    def intervals(
        self,
        at: FoldPredictions,
        interval: str,
        levels: Sequence[float],
        epsilon: float,
    ) -> dict[float, tuple[FloatArray, FloatArray]]:
        """CV+ ``(lower, upper)`` at each level for the rows ``at`` describes."""
        scale = None if interval == "absolute" else at.spread + epsilon
        return cvplus_bounds(
            at.prediction,
            scale,
            self.calibration.fold,
            self.calibration.scores(interval, epsilon),
            levels,
        )


def order_ranks(n: int, level: float) -> tuple[int, int]:
    """1-based ranks of CV+'s lower and upper order statistics among ``n`` values.

    ``floor(a (n + 1))`` and ``ceil((1 - a)(n + 1))`` for ``a = 1 - level``, with a tolerance so
    that a product like ``0.1 * 100`` is not taken as just above 10.

    Raises:
        ConformalError: ``level`` is not in (0, 1), or ``n`` is too small for it (a bound would
            be infinite).
    """
    if not 0 < level < 1:
        raise ConformalError(f"level must be in (0, 1), got {level}")
    alpha = 1.0 - level
    low = math.floor(alpha * (n + 1) + 1e-9)
    high = math.ceil(level * (n + 1) - 1e-9)
    if low < 1 or high > n:
        raise ConformalError(f"{n} calibration rows are too few for a {level:.0%} interval")
    return low, high


def cvplus_bounds(
    center: FloatMatrix,
    scale: FloatMatrix | None,
    fold: IntArray,
    scores: FloatArray,
    levels: Sequence[float],
) -> dict[float, tuple[FloatArray, FloatArray]]:
    """CV+ bounds from the K models' values at new rows and the calibration scores.

    Args:
        center: ``(K, m)``: each calibration model's prediction at the m new rows.
        scale: ``(K, m)`` multipliers of the scores (spread + epsilon), or None for 1.
        fold: Index (0..K-1) of the model that scored each calibration row.
        scores: Conformity score of each calibration row.
        levels: Nominal coverages, e.g. ``(0.8, 0.9)``.

    Returns:
        Level -> ``(lower, upper)``, each of length m.
    """
    n = scores.shape[0]
    ranks = {level: order_ranks(n, level) for level in levels}
    m = center.shape[1]
    out = {level: (np.empty(m), np.empty(m)) for level in levels}
    lows = sorted({low - 1 for low, _ in ranks.values()})
    highs = sorted({high - 1 for _, high in ranks.values()})
    step = max(1, _CHUNK_ELEMENTS // max(n, 1))
    for start in range(0, m, step):
        cols = slice(start, min(start + step, m))
        # (rows, n): row r's candidate values, one per calibration row, from that row's model.
        mid = center[fold, cols].T
        half = scores[None, :] if scale is None else scale[fold, cols].T * scores[None, :]
        lower = np.partition(mid - half, lows, axis=1)
        upper = np.partition(mid + half, highs, axis=1)
        for level, (low, high) in ranks.items():
            out[level][0][cols] = lower[:, low - 1]
            out[level][1][cols] = upper[:, high - 1]
    return out

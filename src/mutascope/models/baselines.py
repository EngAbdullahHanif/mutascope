"""Reference models that learn nothing from the labels' relation to the features."""

import numpy as np

from mutascope.features.matrix import FeatureMatrix, FloatArray, IntArray

MEAN_DECIMALS = 12
"""Decimals :class:`TrainingMean` keeps. The CV loop's standardised targets have mean 0 up to
rounding error, at most 4.1e-15 over all 3,249 training sets of the pinned release."""


class TrainingMean:
    """Predicts the mean training target for every variant.

    The cross-validation loop standardises targets with the training folds' mean, so this
    predicts 0 everywhere: its Spearman is undefined (a constant) and its MSE is the test
    targets' mean square on that scale, about 1 when train and test look alike. It shows the
    loop runs end to end, and its MSE is the bar any model has to clear (ADR 0019).

    The mean is rounded to :data:`MEAN_DECIMALS`. Unrounded, the rounding error of the
    standardised mean (around 1e-17) differs between folds, the pooled predictions are no
    longer one constant, and the pooled Spearman ranks that noise: it came out between -0.67
    and +0.91 instead of undefined.
    """

    def __init__(self) -> None:
        self.mean_: float | None = None

    def fit(self, x: FeatureMatrix, y: FloatArray, groups: IntArray) -> "TrainingMean":
        del x, groups  # uses no features
        # + 0.0 turns -0.0 into 0.0, so every fold predicts the identical value.
        self.mean_ = round(float(np.mean(y)), MEAN_DECIMALS) + 0.0
        return self

    def predict(self, x: FeatureMatrix) -> FloatArray:
        if self.mean_ is None:
            raise RuntimeError("TrainingMean.predict called before fit")
        prediction = np.empty(x.n_rows, dtype=np.float64)
        prediction.fill(self.mean_)
        return prediction


class ZeroShot:
    """A zero-shot score, scored by the supervised protocol on the same test folds.

    The feature set holds one column, a zero-shot score (``esm2_score``: ESM-2 650M's masked
    marginal). The prediction is that score standardised with the training rows' mean and
    population standard deviation, the transform the loop applies to the targets. Labels are
    never used, so the sign and ranking within a fold are the zero-shot model's own; pooled
    over folds, the Spearman is the zero-shot ranking judged exactly as a supervised model's
    is. That is the zero-shot number every supervised model is paired with (ADR 0021).
    """

    def __init__(self) -> None:
        self.mean_: float | None = None
        self.scale_: float | None = None

    def fit(self, x: FeatureMatrix, y: FloatArray, groups: IntArray) -> "ZeroShot":
        del y, groups  # a zero-shot model does not learn from labels
        score = self._score(x)
        self.mean_, self.scale_ = float(np.mean(score)), float(np.std(score))
        if not self.scale_ > 0:
            raise ValueError("the zero-shot score is constant on the training rows")
        return self

    def predict(self, x: FeatureMatrix) -> FloatArray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("ZeroShot.predict called before fit")
        return (self._score(x) - self.mean_) / self.scale_

    @staticmethod
    def _score(x: FeatureMatrix) -> FloatArray:
        if x.dense.shape[1] != 1 or x.site is not None:
            raise ValueError(
                f"ZeroShot needs exactly one dense column (a score), got {x.dense.shape[1]}"
            )
        return x.dense[:, 0]

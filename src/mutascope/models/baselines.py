"""Reference models that learn nothing from the features."""

import numpy as np

from mutascope.models.cv import FloatArray, FloatMatrix

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

    def fit(self, x: FloatMatrix, y: FloatArray) -> "TrainingMean":
        del x  # uses no features
        # + 0.0 turns -0.0 into 0.0, so every fold predicts the identical value.
        self.mean_ = round(float(np.mean(y)), MEAN_DECIMALS) + 0.0
        return self

    def predict(self, x: FloatMatrix) -> FloatArray:
        if self.mean_ is None:
            raise RuntimeError("TrainingMean.predict called before fit")
        prediction = np.empty(x.shape[0], dtype=np.float64)
        prediction.fill(self.mean_)
        return prediction

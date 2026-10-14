"""A small learner that is known to find signal, for testing the CV loop and the canary.

Test scaffolding, not a MutaScope model: ridge regression on one-hot wild-type residue, mutant
residue and position. The residue one-hots generalise to unseen positions, so it learns
something under every scheme (modulo and contiguous test folds hold positions the training
folds never saw); the position one-hot adds memorisation under the random scheme.
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge  # pyright: ignore[reportMissingTypeStubs]

from mutascope.alphabet import AA_ORDER
from mutascope.features.matrix import FeatureMatrix
from mutascope.models.cv import Regressor


def probe_features(variants: pd.DataFrame) -> FeatureMatrix:
    """One-hot wt (20) + mt (20) + position (one column per distinct position)."""
    identity = np.eye(20)
    wt = identity[[AA_ORDER.index(a) for a in variants["wt_aa"]]]
    mt = identity[[AA_ORDER.index(a) for a in variants["mt_aa"]]]
    codes = pd.Categorical(variants["position"]).codes
    site = np.eye(int(codes.max()) + 1)[codes]
    return FeatureMatrix.of(np.hstack([wt, mt, site]))


class _Probe:
    """scikit-learn's Ridge(alpha=1) on the dense columns; ignores the training folds."""

    def __init__(self) -> None:
        self.ridge = Ridge(alpha=1.0)

    def fit(self, x: FeatureMatrix, y: np.ndarray, groups: np.ndarray) -> "_Probe":
        self.ridge.fit(x.dense, y)  # pyright: ignore[reportUnknownMemberType]
        return self

    def predict(self, x: FeatureMatrix) -> np.ndarray:
        return np.asarray(self.ridge.predict(x.dense), dtype=np.float64)  # pyright: ignore[reportUnknownMemberType]


def probe_model() -> Regressor:
    return _Probe()

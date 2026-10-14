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
from mutascope.models.cv import Regressor


def probe_features(variants: pd.DataFrame) -> np.ndarray:
    """One-hot wt (20) + mt (20) + position (one column per distinct position)."""
    identity = np.eye(20)
    wt = identity[[AA_ORDER.index(a) for a in variants["wt_aa"]]]
    mt = identity[[AA_ORDER.index(a) for a in variants["mt_aa"]]]
    codes = pd.Categorical(variants["position"]).codes
    site = np.eye(int(codes.max()) + 1)[codes]
    return np.hstack([wt, mt, site]).astype(np.float64)


def probe_model() -> Regressor:
    # sklearn is untyped; Ridge has the fit/predict interface the loop needs.
    model: Regressor = Ridge(alpha=1.0)  # pyright: ignore[reportAssignmentType]
    return model

"""Key-only feature sets, and the rule every feature builder follows.

``train.feature_set`` names either one of these (``none``) or a feature-store set from
``features.sets`` (:mod:`mutascope.features.sets`).

A feature builder sees the variants' keys only (:data:`KEY_COLUMNS`), never ``DMS_score`` or
``DMS_score_bin``. That is the guard against target leakage through features: a feature
computed from labels would include the test folds' labels, and the label-permutation canary
cannot catch it (ADR 0019). A feature that needs labels (a target encoding) has to be fit
inside the training folds, as a model is.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

import numpy as np
import pandas as pd

from mutascope.features.matrix import FeatureMatrix

FeatureBuilder = Callable[[pd.DataFrame], FeatureMatrix]

KEY_COLUMNS: Final = ("assay_id", "mutant", "position", "wt_aa", "mt_aa")
"""What a feature builder may read about a variant."""


def no_features(keys: pd.DataFrame) -> FeatureMatrix:
    """An ``(n, 0)`` matrix, for models that use no features (the training-mean baseline)."""
    return FeatureMatrix.of(np.zeros((len(keys), 0)))


FEATURE_SETS: Final[Mapping[str, FeatureBuilder]] = {"none": no_features}


def build_features(name: str, variants: pd.DataFrame) -> FeatureMatrix:
    """The ``name`` feature matrix for ``variants``, one row per variant, in order.

    Raises:
        KeyError: ``name`` is not a known feature set.
    """
    if name not in FEATURE_SETS:
        raise KeyError(f"unknown feature set {name!r}; known: {sorted(FEATURE_SETS)}")
    features = FEATURE_SETS[name](variants[list(KEY_COLUMNS)].copy())
    if features.n_rows != len(variants):
        raise ValueError(f"feature set {name!r} returned {features.n_rows} rows")
    return features


@dataclass(frozen=True)
class KeyDesign:
    """``(assay_id, keys) -> features`` for a key-only set: a picklable design builder.

    ``train cv --jobs`` sends the design to worker processes, so it cannot be a closure.
    """

    name: str

    def __call__(self, assay_id: str, keys: pd.DataFrame) -> FeatureMatrix:
        del assay_id  # key-only sets are the same function in every assay
        return build_features(self.name, keys)

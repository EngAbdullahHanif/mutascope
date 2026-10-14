"""Label-permutation canary: with shuffled training labels, test Spearman must be about 0.

The cross-validation loop is run several times with the training targets shuffled before
every fit (test targets untouched). A model that can only learn from its training labels then
has nothing to learn, so the pooled out-of-fold Spearman scatters around 0. If its mean over
the permutations stays clearly away from 0, the test labels are reaching the predictions some
other way: test rows in the fit, predictions joined to the wrong rows, a metric comparing the
wrong arrays.

One permutation is not enough to judge. On real assays a single shuffled run reaches
|Spearman| ≈ 0.2 at a few hundred variants, because the predictions are a random projection of
features that do carry signal; the mean over permutations is what has to be near 0, judged
against its own standard error (ADR 0019).

What the canary cannot see: a feature computed from labels, test folds included (a target
encoding). Fit on shuffled labels, the model gives that feature a weight of random sign, so
the mean stays near 0. Feature builders never receive labels (:mod:`mutascope.features.base`)
for that reason.
"""

from dataclasses import dataclass

import numpy as np

from mutascope.eval.metrics import spearman
from mutascope.models.cv import (
    FloatArray,
    FloatMatrix,
    IntArray,
    ModelFactory,
    OutOfFold,
    cross_validate,
)


@dataclass(frozen=True)
class CanaryResult:
    """Pooled out-of-fold Spearman with the true training labels and with shuffled ones."""

    observed: float
    permuted: FloatArray

    @property
    def mean(self) -> float:
        return float(np.mean(self.permuted))

    @property
    def standard_error(self) -> float:
        """Standard error of :attr:`mean` (sample standard deviation over √permutations)."""
        return float(np.std(self.permuted, ddof=1) / np.sqrt(self.permuted.size))

    def passed(self, max_standard_errors: float) -> bool:
        """The permuted mean is within ``max_standard_errors`` standard errors of 0.

        An undefined (NaN) permuted Spearman fails: the canary cannot vouch for that run.
        """
        if np.isnan(self.permuted).any():
            return False
        return abs(self.mean) <= max_standard_errors * self.standard_error


def permutation_canary(
    x: FloatMatrix,
    y: FloatArray,
    folds: IntArray,
    make_model: ModelFactory,
    *,
    n_permutations: int,
    rng: np.random.Generator,
) -> CanaryResult:
    """Run the CV loop once as is and ``n_permutations`` times with shuffled training labels."""
    if n_permutations < 2:
        raise ValueError("need at least 2 permutations to estimate a standard error")
    observed = cross_validate(x, y, folds, make_model)
    permuted = np.array(
        [
            _pooled_spearman(cross_validate(x, y, folds, make_model, permute_rng=rng))
            for _ in range(n_permutations)
        ]
    )
    return CanaryResult(observed=_pooled_spearman(observed), permuted=permuted)


def _pooled_spearman(oof: OutOfFold) -> float:
    return spearman(oof.target, oof.prediction)

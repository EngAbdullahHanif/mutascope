"""Label-permutation canary (ADR 0019): shuffled training labels must give test Spearman ≈ 0.

Two layers. Synthetic assays run everywhere, CI included: the canary passes on an honest loop
and fails when test labels reach the predictions. Three small real assays run where the
processed data and fold table exist (``make pipeline``, ``mutascope data folds``) and are
skipped otherwise, as the harness gate is.

The learner is the test probe (:mod:`tests.models.probe`), since the training-mean baseline has
no Spearman to drop. Each case runs the loop once with true labels (a positive control: the
probe must find signal, or a near-0 permuted Spearman would prove nothing) and
``N_PERMUTATIONS`` times with shuffled training labels; the mean of those must lie within
``MAX_STANDARD_ERRORS`` of its standard error of 0.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tests.models.probe import probe_features, probe_model
from tests.synthetic_assays import synthetic_assay

from mutascope.data.folds import load_cv_table
from mutascope.data.release import load_release
from mutascope.models.canary import permutation_canary
from mutascope.utils.config import load_config

N_PERMUTATIONS = 20
MAX_STANDARD_ERRORS = 4.0
"""About 1 in 16,000 for one case under a normal null; the seed makes each run deterministic."""
MIN_OBSERVED = 0.2
"""Positive control: the probe's pooled Spearman with true labels, in every case."""
SCHEMES = ("random", "modulo", "contiguous")

CANARY_ASSAYS = (
    "ERBB2_HUMAN_Elazar_2016",  # Expression, 326 variants
    "OTU7A_HUMAN_Tsuboyama_2023_2L2D",  # Stability, 635
    "NPC1_HUMAN_Erwood_2022_HEK293T",  # Activity, 637
)
"""Small, three function types, and the probe finds signal in each under every scheme."""

_data = load_config(Path(__file__).parents[2] / "conf").data
_processed = Path(str(_data.processed_dir))
VARIANTS = _processed / "variants"
FOLDS = _processed / "folds"
needs_processed = pytest.mark.skipif(
    not (VARIANTS.is_dir() and FOLDS.is_dir() and load_release(_data).raw_dir.is_dir()),
    reason="needs processed variants and folds (`make pipeline`, `mutascope data folds`)",
)


class Oracle:
    """A model with a back door to every label: column 0 is the row id, ``labels`` the truth.

    Stands in for any leak that bypasses the training labels (test rows in the fit,
    predictions joined to the wrong rows): what the canary exists to catch.
    """

    def __init__(self, labels: np.ndarray) -> None:
        self.labels = labels

    def fit(self, x: np.ndarray, y: np.ndarray) -> "Oracle":
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return self.labels[x[:, 0].astype(int)]


@pytest.mark.parametrize("scheme", SCHEMES)
def test_canary_passes_on_synthetic_assay(scheme: str) -> None:
    assay = synthetic_assay("A_TEST", seed=11)
    result = permutation_canary(
        probe_features(assay),
        assay["DMS_score"].to_numpy(),
        assay[f"fold_{scheme}"].to_numpy(),
        probe_model,
        n_permutations=N_PERMUTATIONS,
        rng=np.random.default_rng(0),
    )
    assert result.observed >= MIN_OBSERVED
    assert result.passed(MAX_STANDARD_ERRORS), (result.mean, result.standard_error)


@pytest.mark.parametrize("scheme", SCHEMES)
def test_canary_catches_labels_that_bypass_training(scheme: str) -> None:
    assay = synthetic_assay("A_TEST", seed=11)
    y = assay["DMS_score"].to_numpy()
    row_ids = np.arange(len(y), dtype=np.float64)[:, None]
    result = permutation_canary(
        row_ids,
        y,
        assay[f"fold_{scheme}"].to_numpy(),
        lambda: Oracle(y),
        n_permutations=N_PERMUTATIONS,
        rng=np.random.default_rng(0),
    )
    assert result.mean > 0.99
    assert not result.passed(MAX_STANDARD_ERRORS)


@pytest.fixture(scope="module")
def canary_data() -> pd.DataFrame:
    return load_cv_table(VARIANTS, FOLDS, CANARY_ASSAYS)


@needs_processed
@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("assay_id", CANARY_ASSAYS)
def test_canary_on_real_assays(canary_data: pd.DataFrame, assay_id: str, scheme: str) -> None:
    assay = canary_data[canary_data["assay_id"] == assay_id]
    result = permutation_canary(
        probe_features(assay),
        assay["DMS_score"].to_numpy(),
        assay[f"fold_{scheme}"].to_numpy(),
        probe_model,
        n_permutations=N_PERMUTATIONS,
        rng=np.random.default_rng(0),
    )
    assert result.observed >= MIN_OBSERVED, f"positive control: {result.observed:.3f}"
    assert result.passed(MAX_STANDARD_ERRORS), (
        f"permuted mean {result.mean:+.3f}, standard error {result.standard_error:.3f}"
    )

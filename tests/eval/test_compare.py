"""Paired bootstrap comparisons: the same resampled assays for both score sets."""

from typing import TypedDict

import numpy as np
import pandas as pd
import pytest

from mutascope.eval.aggregate import AssayGroups, aggregate, bootstrap_ci, paired_bootstrap
from mutascope.eval.benchmark import BootstrapParams
from mutascope.eval.compare import paired_bootstrap_diff
from mutascope.eval.metrics import METRICS

BOOTSTRAP = BootstrapParams(n_resamples=2000, confidence=0.95)


class _Kwargs(TypedDict):
    n_resamples: int
    confidence: float


KWARGS: _Kwargs = {"n_resamples": 2000, "confidence": 0.95}
N_PROTEINS = 30


def _reference() -> pd.DataFrame:
    """60 assays: 30 proteins with two assays each, function types alternating by protein."""
    ids = [f"A{i:02d}" for i in range(2 * N_PROTEINS)]
    proteins = [f"P{i // 2:02d}" for i in range(2 * N_PROTEINS)]
    functions = ["Stability" if (i // 2) % 2 else "Binding" for i in range(2 * N_PROTEINS)]
    return pd.DataFrame({"DMS_id": ids, "UniProt_ID": proteins, "coarse_selection_type": functions})


def _groups(reference: pd.DataFrame) -> AssayGroups:
    return AssayGroups.from_reference(reference, reference["DMS_id"].tolist())


def _correlated(rng: np.random.Generator, gain: float) -> tuple[np.ndarray, np.ndarray]:
    """B varies a lot between assays; A is B plus ``gain`` plus a little noise."""
    b = rng.uniform(0.1, 0.7, size=2 * N_PROTEINS)
    return b + gain + rng.normal(scale=0.01, size=b.size), b


def _results(model: str, reference: pd.DataFrame, values: np.ndarray) -> pd.DataFrame:
    """Long-format results with the same ``values`` for every metric."""
    frames = [
        pd.DataFrame(
            {"model": model, "assay_id": reference["DMS_id"], "metric": m, "value": values}
        )
        for m in METRICS
    ]
    table = pd.concat(frames, ignore_index=True)
    table.insert(2, "split", pd.Series(pd.NA, index=table.index, dtype="string"))
    table.insert(3, "fold", pd.Series(pd.NA, index=table.index, dtype="Int64"))
    return table


def test_difference_is_the_difference_of_the_two_aggregates() -> None:
    reference = _reference()
    groups = _groups(reference)
    a, b = _correlated(np.random.default_rng(0), gain=0.02)
    result = paired_bootstrap(
        a, b, groups, n_resamples=500, confidence=0.95, rng=np.random.default_rng(1)
    )
    assert result.overall.value_a == aggregate(a, groups).overall
    assert result.overall.value_b == aggregate(b, groups).overall
    assert result.overall.diff == result.overall.value_a - result.overall.value_b
    # The aggregate is linear in the assay values, so this is also the aggregated difference.
    assert result.overall.diff == pytest.approx(aggregate(a - b, groups).overall, abs=1e-12)
    assert result.n_assays == 60 and result.n_by_function == {"Binding": 30, "Stability": 30}
    assert set(result.by_function) == {"Binding", "Stability"}


def test_a_constant_gain_has_a_zero_width_interval() -> None:
    """A = B + 0.05 on every assay: every resample gives exactly 0.05, whatever it draws.
    Identical score sets give exactly 0 and are never "better"."""
    groups = _groups(_reference())
    b = np.random.default_rng(0).uniform(0.1, 0.7, size=60)
    shifted = paired_bootstrap(b + 0.05, b, groups, rng=np.random.default_rng(1), **KWARGS)
    assert shifted.overall.ci == pytest.approx((0.05, 0.05), abs=1e-12)
    assert shifted.overall.share_a_better == 1.0
    same = paired_bootstrap(b, b, groups, rng=np.random.default_rng(1), **KWARGS)
    assert same.overall.diff == 0 and same.overall.ci == (0.0, 0.0)
    assert same.overall.share_a_better == 0.0
    worse = paired_bootstrap(b, b + 0.05, groups, rng=np.random.default_rng(1), **KWARGS)
    assert worse.overall.share_a_better == 0.0 and worse.overall.ci[1] < 0


def test_pairing_finds_a_difference_that_separate_intervals_hide() -> None:
    """The reason the function exists. A beats B by 0.02 on nearly every assay, but assays
    differ from each other by far more, so the two separate CIs overlap almost completely."""
    groups = _groups(_reference())
    a, b = _correlated(np.random.default_rng(0), gain=0.02)
    ci_a = bootstrap_ci(a, groups, rng=np.random.default_rng(1), **KWARGS).overall
    ci_b = bootstrap_ci(b, groups, rng=np.random.default_rng(1), **KWARGS).overall
    assert ci_b[1] > ci_a[0]  # separate intervals overlap
    paired = paired_bootstrap(a, b, groups, rng=np.random.default_rng(1), **KWARGS).overall
    assert paired.ci[0] > 0 and paired.share_a_better == 1.0
    assert paired.ci[1] - paired.ci[0] < 0.1 * (ci_a[1] - ci_a[0])
    assert paired.ci[0] < paired.diff < paired.ci[1]


def test_an_assay_undefined_for_either_is_left_out_of_both() -> None:
    reference = _reference()
    groups = _groups(reference)
    a, b = _correlated(np.random.default_rng(0), gain=0.02)
    a[0], b[3] = np.nan, np.nan
    result = paired_bootstrap(
        a, b, groups, n_resamples=500, confidence=0.95, rng=np.random.default_rng(1)
    )
    keep = np.ones(60, dtype=bool)
    keep[[0, 3]] = False
    assert result.n_assays == 58
    assert result.n_by_function == {"Binding": 29, "Stability": 29}  # one lost in each
    assert result.overall.value_a == aggregate(np.where(keep, a, np.nan), groups).overall
    assert result.overall.value_b == aggregate(np.where(keep, b, np.nan), groups).overall
    assert np.isfinite(result.overall.ci).all()


def test_results_frames_give_one_row_per_metric_and_level() -> None:
    reference = _reference()
    a, b = _correlated(np.random.default_rng(0), gain=0.02)
    results_a, results_b = _results("big", reference, a), _results("small", reference, b)
    table = paired_bootstrap_diff(
        results_a, results_b, reference, BOOTSTRAP, np.random.default_rng(5)
    )

    assert len(table) == len(METRICS) * 3  # overall + two function types
    assert set(table["model_a"]) == {"big"} and set(table["model_b"]) == {"small"}
    overall = table[(table["metric"] == "spearman") & (table["level"] == "overall")].iloc[0]
    groups = _groups(reference)
    assert overall["diff"] == aggregate(a, groups).overall - aggregate(b, groups).overall
    assert overall["ci_low"] > 0 and overall["share_a_better"] == 1.0 and overall["n_assays"] == 60
    binding = table[(table["metric"] == "auc") & (table["group"] == "Binding")].iloc[0]
    assert binding["n_assays"] == 30 and binding["ci_low"] < binding["diff"] < binding["ci_high"]

    again = paired_bootstrap_diff(
        results_a, results_b, reference, BOOTSTRAP, np.random.default_rng(5)
    )
    pd.testing.assert_frame_equal(table, again)
    # Swapping the two flips the sign and the interval.
    swapped = paired_bootstrap_diff(
        results_b, results_a, reference, BOOTSTRAP, np.random.default_rng(5)
    )
    assert swapped["diff"].to_numpy() == pytest.approx(-table["diff"].to_numpy())
    assert swapped["ci_low"].to_numpy() == pytest.approx(-table["ci_high"].to_numpy())
    assert (swapped["share_a_better"] == 0).all()


def test_result_sets_must_be_two_different_models_on_the_same_rows() -> None:
    reference = _reference()
    a, b = _correlated(np.random.default_rng(0), gain=0.02)
    results_a, results_b = _results("big", reference, a), _results("small", reference, b)
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError, match="nothing to compare"):
        paired_bootstrap_diff(results_a, results_a, reference, BOOTSTRAP, rng)
    with pytest.raises(ValueError, match="do not cover the same"):
        paired_bootstrap_diff(results_a, results_b.iloc[1:], reference, BOOTSTRAP, rng)
    both = pd.concat([results_a, results_b], ignore_index=True)
    with pytest.raises(ValueError, match="exactly one model"):
        paired_bootstrap_diff(both, results_b, reference, BOOTSTRAP, rng)
    doubled = pd.concat([results_a, results_a], ignore_index=True)
    with pytest.raises(ValueError, match="average folds"):
        paired_bootstrap_diff(doubled, results_b, reference, BOOTSTRAP, rng)

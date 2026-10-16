"""Intervals under the supervised CV: no test-fold label reaches an interval, calibration folds,
and the metrics computed from them (ADR 0024)."""

from functools import partial

import numpy as np
import pandas as pd
import pytest
from tests.synthetic_assays import synthetic_assay

from mutascope.alphabet import AA_ORDER
from mutascope.eval.uncertainty import (
    SchemeIntervals,
    bin_summary,
    interval_metrics,
    size_bin,
    summarise_intervals,
)
from mutascope.features.base import KEY_COLUMNS
from mutascope.features.matrix import FeatureMatrix
from mutascope.models.conformal import CVPlus
from mutascope.models.ridge import InnerCVRidge
from mutascope.models.uncertainty import (
    UncertaintySettings,
    assay_calibration,
    calibration_groups,
    scheme_intervals,
    seed_key,
)

SETTINGS = UncertaintySettings(
    levels=(0.8, 0.9),
    modes=("random", "scheme"),
    intervals=("absolute", "normalized"),
    epsilon=0.01,
    seed=0,
    width_bins=4,
)
make_ridge = partial(InnerCVRidge, [0.1, 1.0, 10.0, 100.0])


def features(assay: pd.DataFrame) -> FeatureMatrix:
    """Wild-type and mutant one-hot plus a smooth position feature: learnable in every scheme."""
    wt = (assay["wt_aa"].to_numpy()[:, None] == np.array(list(AA_ORDER))).astype(float)
    mt = (assay["mt_aa"].to_numpy()[:, None] == np.array(list(AA_ORDER))).astype(float)
    position = assay["position"].to_numpy(np.float64)[:, None] / 60.0
    return FeatureMatrix.of(np.hstack([wt, mt, position, position**2]))


def design(assay_id: str, keys: pd.DataFrame) -> FeatureMatrix:
    assert list(keys.columns) == list(KEY_COLUMNS)
    return features(keys)


@pytest.fixture
def assay() -> pd.DataFrame:
    return synthetic_assay("A_TEST", length=40, per_position=6, seed=3)


@pytest.mark.parametrize("scheme", ["random", "modulo", "contiguous"])
def test_no_test_fold_label_reaches_its_intervals(assay: pd.DataFrame, scheme: str) -> None:
    x, y = features(assay), assay["DMS_score"].to_numpy(np.float64)
    folds = assay[f"fold_{scheme}"].to_numpy(np.int64)
    kwargs = {"assay_id": "A_TEST", "scheme": scheme}
    base = scheme_intervals(x, y, folds, make_ridge, SETTINGS, **kwargs)
    test = folds == 2
    for changed in (np.random.default_rng(0).permutation(y[test]), y[test] * 7 - 3):
        y2 = y.copy()
        y2[test] = changed
        other = scheme_intervals(x, y2, folds, make_ridge, SETTINGS, **kwargs)
        np.testing.assert_array_equal(other.prediction[test], base.prediction[test])
        for mode in SETTINGS.modes:
            np.testing.assert_array_equal(other.spread[mode][test], base.spread[mode][test])
        for key, (lower, upper) in base.bounds.items():
            np.testing.assert_array_equal(other.bounds[key][0][test], lower[test])
            np.testing.assert_array_equal(other.bounds[key][1][test], upper[test])
        # The guard has teeth: the other folds' intervals did move.
        assert not np.array_equal(
            other.bounds[("scheme", "absolute", 0.9)][0][~test],
            base.bounds[("scheme", "absolute", 0.9)][0][~test],
        )


def test_intervals_are_finite_and_ordered(assay: pd.DataFrame) -> None:
    folds = assay["fold_contiguous"].to_numpy(np.int64)
    found = scheme_intervals(
        features(assay),
        assay["DMS_score"].to_numpy(np.float64),
        folds,
        make_ridge,
        SETTINGS,
        assay_id="A_TEST",
        scheme="contiguous",
    )
    for lower, upper in found.bounds.values():
        assert np.isfinite(lower).all() and np.isfinite(upper).all()
        assert (upper > lower).all()
    # Nested: the 90% interval contains the 80% one.
    for mode in SETTINGS.modes:
        for interval in SETTINGS.intervals:
            lo80, hi80 = found.bounds[(mode, interval, 0.8)]
            lo90, hi90 = found.bounds[(mode, interval, 0.9)]
            assert (lo90 <= lo80).all() and (hi90 >= hi80).all()


def test_calibration_groups() -> None:
    groups = np.repeat(np.array([0, 1, 3, 4]), [10, 11, 9, 12]).astype(np.int64)
    np.testing.assert_array_equal(calibration_groups("scheme", groups, [0]), groups)
    key = seed_key(0, "A_TEST", "modulo", 2)
    random = calibration_groups("random", groups, key)
    assert sorted(np.bincount(random).tolist()) == [10, 10, 11, 11]  # 4 folds, equal sizes
    np.testing.assert_array_equal(random, calibration_groups("random", groups, key))
    assert not np.array_equal(
        random, calibration_groups("random", groups, seed_key(0, "A_TEST", "modulo", 3))
    )
    with pytest.raises(ValueError, match="unknown calibration mode"):
        calibration_groups("blocks", groups, key)


def test_interval_metrics() -> None:
    result = SchemeIntervals(
        fold=np.zeros(4, dtype=np.int64),
        target=np.array([0.0, 1.0, 2.0, 3.0]),
        prediction=np.array([0.0, 1.5, 2.0, 0.0]),
        train_sd=np.full(4, 2.0),
        spread={"scheme": np.array([0.1, 0.2, 0.3, 0.4])},
        bounds={
            ("scheme", "normalized", 0.9): (
                np.array([-0.5, 0.0, 1.0, -1.0]),
                np.array([0.5, 0.9, 3.0, 4.0]),
            )
        },
    )
    records, bins = interval_metrics("A", "random", result, assay_sd=4.0, n_bins=2)
    found = {(r[2], r[5]): r[6] for r in records}
    assert found[(None, "n_variants")] == 4
    assert found[("scheme", "coverage")] == 0.75  # row 1 (target 1.0 > 0.9) is outside
    # widths 1, 0.9, 2, 5 -> x train_sd / assay_sd = 0.5
    assert found[("scheme", "width_sd")] == pytest.approx(np.mean([1.0, 0.9, 2.0, 5.0]) * 0.5)
    # |error| = 0, 0.5, 0, 3: average ranks 1.5, 3, 1.5, 4 (Pearson of ranks, by hand).
    error_rank = np.array([1.5, 3.0, 1.5, 4.0])
    width_rank = np.array([2.0, 1.0, 3.0, 4.0])
    spread_rank = np.array([1.0, 2.0, 3.0, 4.0])
    expected = np.corrcoef(width_rank, error_rank)[0, 1]
    assert found[("scheme", "width_error_spearman")] == pytest.approx(expected)
    expected = np.corrcoef(spread_rank, error_rank)[0, 1]
    assert found[("scheme", "spread_error_spearman")] == pytest.approx(expected)
    # Two width bins: {0.9, 1.0} and {2.0, 5.0}.
    assert [(b[5], b[6]) for b in bins] == [(0, 2), (1, 2)]
    assert bins[0][9] == 0.5 and bins[1][9] == 1.0
    assert bins[1][8] == pytest.approx(np.mean([0.0, 3.0]) * 0.5)


def test_size_bins() -> None:
    edges = [300, 1000]
    assert [size_bin(n, edges) for n in (33, 299, 300, 999, 1000, 20000)] == [
        "0-299", "0-299", "300-999", "300-999", ">=1000", ">=1000"
    ]  # fmt: skip


def test_summary_counts_assays_below_target() -> None:
    rows: list[tuple[object, ...]] = []
    for assay, n, coverage in (("A", 100, 0.90), ("B", 500, 0.75), ("C", 2000, 0.85)):
        rows += [
            ("m", "s", assay, "random", None, None, np.nan, "n_variants", float(n)),
            ("m", "s", assay, "random", "scheme", "absolute", 0.9, "coverage", coverage),
        ]
    metrics = pd.DataFrame(
        rows,
        columns=["model", "feature_set", "assay_id", "scheme", "mode", "interval", "level",
                 "metric", "value"],
    )  # fmt: skip
    reference = pd.DataFrame(
        {"DMS_id": ["A", "B", "C"], "UniProt_ID": ["P1", "P2", "P3"],
         "coarse_selection_type": ["Stability"] * 3}
    )  # fmt: skip
    summary = summarise_intervals(metrics, reference, size_edges=[300, 1000], below_points=0.10)
    stat = summary[summary["size_bin"] == "all"].set_index("statistic")["value"]
    assert stat["mean"] == pytest.approx(np.mean([0.90, 0.75, 0.85]))
    assert stat["n_below"] == 1  # only B (0.75 < 0.80)
    assert stat["aggregate"] == pytest.approx(np.mean([0.90, 0.75, 0.85]))
    by_size = summary[summary["statistic"] == "n_assays"].set_index("size_bin")["value"]
    assert by_size.to_dict() == {"all": 3, "0-299": 1, "300-999": 1, ">=1000": 1}


def test_bin_summary_averages_assays() -> None:
    bins = pd.DataFrame(
        [("A", "random", "scheme", "normalized", 0.9, 0, 5, 1.0, 0.2, 1.0),
         ("B", "random", "scheme", "normalized", 0.9, 0, 50, 3.0, 0.4, 0.5)],
        columns=["assay_id", "scheme", "mode", "interval", "level", "bin", "n", "width_sd",
                 "abs_error_sd", "coverage"],
    )  # fmt: skip
    out = bin_summary(bins).iloc[0]
    assert [out["width_sd"], out["abs_error_sd"], out["coverage"]] == pytest.approx(
        [2.0, 0.3, 0.75]
    )
    assert out["n_assays"] == 2


def test_all_data_calibration_reproduces_on_refit(assay: pd.DataFrame) -> None:
    rows = assay_calibration("A_TEST", assay, design, make_ridge, ["contiguous"], SETTINGS)
    assert len(rows) == 2 * len(assay)
    y = assay["DMS_score"].to_numpy(np.float64)
    scheme = rows[rows["mode"] == "scheme"]
    np.testing.assert_allclose(scheme["target"], (y - y.mean()) / y.std())
    np.testing.assert_array_equal(scheme["fold"], assay["fold_contiguous"])
    # Task 8's recipe: refitting CV+ on the same inputs gives the same fold models.
    refit = CVPlus.fit(
        features(assay),
        (y - y.mean()) / y.std(),
        assay["fold_contiguous"].to_numpy(np.int64),
        make_ridge,
    )
    np.testing.assert_array_equal(scheme["prediction"], refit.calibration.prediction)
    np.testing.assert_array_equal(scheme["spread"], refit.calibration.spread)

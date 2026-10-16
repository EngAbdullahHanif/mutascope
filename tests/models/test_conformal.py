"""CV+ intervals: exact order statistics, coverage on data with a known noise level, and every
calibration row scored by the one model that did not train on it (ADR 0024)."""

from functools import partial

import numpy as np
import pytest

from mutascope.features.matrix import FeatureMatrix, FloatArray, IntArray
from mutascope.models import conformal
from mutascope.models.conformal import (
    Calibration,
    ConformalError,
    CVPlus,
    EnsembledModel,
    cvplus_bounds,
    order_ranks,
    spread,
)
from mutascope.models.ridge import InnerCVRidge, ridge_path

ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0]
make_ridge = partial(InnerCVRidge, ALPHAS)


class LeastSquares:
    """A model with no fold-ensemble hook: :class:`EnsembledModel` must fit its members."""

    def __init__(self) -> None:
        self.coef: FloatArray | None = None

    def fit(self, x: FeatureMatrix, y: FloatArray, groups: IntArray) -> "LeastSquares":
        design = np.hstack([np.ones((x.n_rows, 1)), x.dense])
        self.coef = np.asarray(np.linalg.lstsq(design, y, rcond=None)[0], dtype=np.float64)
        return self

    def predict(self, x: FeatureMatrix) -> FloatArray:
        assert self.coef is not None
        return np.hstack([np.ones((x.n_rows, 1)), x.dense]) @ self.coef


def linear_data(
    seed: int, n: int, *, heteroscedastic: bool = False
) -> tuple[FeatureMatrix, FloatArray, FeatureMatrix, FloatArray]:
    """``y = x b + noise``, noise SD 1 (or ``0.3 + |x_0|``): training rows, then 2,000 new rows."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n + 2000, 8))
    sd = 0.3 + np.abs(x[:, 0]) if heteroscedastic else np.ones(n + 2000)
    y = x @ rng.normal(size=8) + rng.normal(size=n + 2000) * sd
    return FeatureMatrix.of(x[:n]), y[:n], FeatureMatrix.of(x[n:]), y[n:]


def test_order_ranks() -> None:
    assert order_ranks(99, 0.9) == (10, 90)  # 0.1 * 100 is exactly 10, not just above it
    assert order_ranks(99, 0.8) == (20, 80)
    assert order_ranks(9, 0.9) == (1, 9)
    with pytest.raises(ConformalError, match="too few"):
        order_ranks(8, 0.9)
    with pytest.raises(ConformalError, match="level"):
        order_ranks(100, 1.0)


@pytest.mark.parametrize("scaled", [False, True], ids=["absolute", "normalized"])
def test_bounds_are_the_order_statistics(scaled: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conformal, "_CHUNK_ELEMENTS", 50)  # several chunks
    rng = np.random.default_rng(0)
    k, m, n = 4, 13, 37
    center = rng.normal(size=(k, m))
    scale = rng.uniform(0.5, 2.0, size=(k, m)) if scaled else None
    fold = rng.integers(0, k, size=n)
    scores = rng.exponential(size=n)
    found = cvplus_bounds(center, scale, fold, scores, (0.8, 0.9))
    for level, (lower, upper) in found.items():
        low, high = order_ranks(n, level)
        for col in range(m):
            mult = np.ones(n) if scale is None else scale[fold, col]
            assert lower[col] == np.sort(center[fold, col] - scores * mult)[low - 1]
            assert upper[col] == np.sort(center[fold, col] + scores * mult)[high - 1]


def known_noise_runs(level: float, interval: str) -> tuple[float, float, float]:
    """Mean coverage and width of CV+, and coverage of the same scores used as split conformal
    (model 0 calibrated on fold 0 alone), over 20 draws of 400 rows with noise SD 1."""
    coverage: list[float] = []
    widths: list[float] = []
    split: list[float] = []
    for seed in range(20):
        x, y, x_new, y_new = linear_data(seed, 400)
        groups = np.random.default_rng(seed).permutation(np.arange(400) % 4).astype(np.int64)
        cvplus = CVPlus.fit(x, y, groups, make_ridge)
        at = cvplus.predict(x_new)
        lower, upper = cvplus.intervals(at, interval, (level,), 0.01)[level]
        coverage.append(float(np.mean((y_new >= lower) & (y_new <= upper))))
        widths.append(float(np.mean(upper - lower)))
        rows = cvplus.calibration.fold == 0
        scores = np.sort(cvplus.calibration.scores(interval, 0.01)[rows])
        q = float(scores[order_ranks(int(rows.sum()), level)[1] - 1])
        half: FloatArray = q * (
            np.ones(x_new.n_rows) if interval == "absolute" else at.spread[0] + 0.01
        )
        error: FloatArray = np.abs(y_new - at.prediction[0])
        split.append(float(np.mean(error <= half)))
    return float(np.mean(coverage)), float(np.mean(widths)), float(np.mean(split))


@pytest.mark.parametrize("level", [0.8, 0.9])
def test_cvplus_reaches_target_coverage_with_known_noise(level: float) -> None:
    coverage, width, split = known_noise_runs(level, "absolute")
    assert abs(coverage - level) < 0.02
    assert abs(split - level) < 0.02
    # Close to the oracle's width, 2 z_(1 - a/2) for noise SD 1, plus estimation error.
    oracle = {0.8: 2 * 1.2816, 0.9: 2 * 1.6449}[level]
    assert oracle < width < 1.15 * oracle


@pytest.mark.parametrize("level", [0.8, 0.9])
def test_normalized_scores_are_exchangeable_and_cvplus_does_not_undercover(level: float) -> None:
    """The normalised scores are valid: as split conformal they hit the level. CV+ over them
    over-covers when the spread says nothing about the error (here: homoscedastic noise),
    because each test point's scale differs between the K models (ADR 0024); it never
    under-covers. Measured: 0.939 at 90%, 0.84 at 80%."""
    coverage, _, split = known_noise_runs(level, "normalized")
    assert abs(split - level) < 0.02
    assert level - 0.02 < coverage < level + 0.06


def test_normalized_cvplus_still_covers_heteroscedastic_noise() -> None:
    coverage: list[float] = []
    for seed in range(10):
        x, y, x_new, y_new = linear_data(seed, 400, heteroscedastic=True)
        groups = np.random.default_rng(seed).permutation(np.arange(400) % 4).astype(np.int64)
        cvplus = CVPlus.fit(x, y, groups, make_ridge)
        lower, upper = cvplus.intervals(cvplus.predict(x_new), "normalized", (0.9,), 0.01)[0.9]
        coverage.append(float(np.mean((y_new >= lower) & (y_new <= upper))))
    assert np.mean(coverage) > 0.88


def test_each_calibration_row_is_scored_by_the_model_without_its_fold() -> None:
    x, y, _, _ = linear_data(1, 120)
    groups = np.repeat(np.array([3, 5, 7, 9]), 30).astype(np.int64)
    cvplus = CVPlus.fit(x, y, groups, make_ridge)
    assert cvplus.groups == (3, 5, 7, 9)
    for index, label in enumerate(cvplus.groups):
        rows = groups == label
        alone = make_ridge().fit(x.take(~rows), y[~rows], groups[~rows])
        assert (cvplus.calibration.fold[rows] == index).all()
        np.testing.assert_array_equal(
            cvplus.calibration.prediction[rows], alone.predict(x.take(rows))
        )
        np.testing.assert_array_equal(
            cvplus.calibration.spread[rows], spread(alone.fold_predict(x.take(rows)))
        )


def test_ridge_fold_ensemble_is_its_inner_fits_at_the_chosen_alpha() -> None:
    x, y, x_new, _ = linear_data(2, 90)
    groups = np.repeat(np.arange(3), 30).astype(np.int64)
    model = make_ridge().fit(x, y, groups)
    members = model.fold_predict(x_new)
    assert members.shape == (3, x_new.n_rows)
    for j in range(3):
        keep = groups != j
        expected = ridge_path(x.take(keep), y[keep], [model.selected()["alpha"]], [])[0]
        np.testing.assert_allclose(members[j], expected.predict(x_new), rtol=0, atol=1e-12)


def test_a_model_without_the_hook_gets_one_member_per_fold() -> None:
    x, y, x_new, _ = linear_data(3, 90)
    groups = np.repeat(np.arange(3), 30).astype(np.int64)
    fitted = EnsembledModel.fit(x, y, groups, LeastSquares)
    assert len(fitted.members) == 3
    expected = [
        LeastSquares().fit(x.take(groups != j), y[groups != j], groups).predict(x_new)
        for j in range(3)
    ]
    np.testing.assert_array_equal(fitted.spread(x_new), np.std(np.stack(expected), axis=0, ddof=1))


def test_scores() -> None:
    cal = Calibration(
        fold=np.array([0, 1]),
        prediction=np.array([1.0, -1.0]),
        spread=np.array([0.5, 0.0]),
        target=np.array([2.0, -3.0]),
    )
    np.testing.assert_array_equal(cal.scores("absolute", 0.01), [1.0, 2.0])
    np.testing.assert_allclose(cal.scores("normalized", 0.01), [1.0 / 0.51, 2.0 / 0.01])
    with pytest.raises(ConformalError, match="unknown interval"):
        cal.scores("quantile", 0.01)


def test_cvplus_needs_three_calibration_folds() -> None:
    x, y, _, _ = linear_data(4, 40)
    with pytest.raises(ConformalError, match="at least 3"):
        CVPlus.fit(x, y, np.repeat(np.arange(2), 20).astype(np.int64), make_ridge)

"""Ridge with inner-CV penalty: exact against the closed form, and blind to the test fold."""

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge  # pyright: ignore[reportMissingTypeStubs]
from tests.synthetic_assays import synthetic_assay

from mutascope.features.matrix import FeatureMatrix, SiteOneHot
from mutascope.models.cv import cross_validate
from mutascope.models.ridge import InnerCVRidge, RidgeError, ridge_path

ALPHAS = [0.01, 0.3, 3.0, 100.0]


def closed_form(
    dense: np.ndarray, site: np.ndarray | None, y: np.ndarray, alpha: float, free: list[int]
) -> np.ndarray:
    """Fitted values of the documented loss, solved directly on the full design matrix."""
    shrunk = [j for j in range(dense.shape[1]) if j not in free]
    raw = dense[:, shrunk]
    scale = raw.std(axis=0)
    scale[scale == 0] = 1.0
    blocks = [np.ones((len(y), 1)), dense[:, free], (raw - raw.mean(axis=0)) / scale]
    penalty = [0.0] * (1 + len(free)) + [alpha] * len(shrunk)
    if site is not None:
        blocks.append(site)
        penalty += [alpha] * site.shape[1]
    design = np.hstack(blocks)
    theta = np.linalg.solve(design.T @ design + np.diag(penalty), design.T @ y)
    return design @ theta


@pytest.fixture
def assay() -> pd.DataFrame:
    return synthetic_assay("A_TEST", length=30, per_position=6, seed=5)


def site_of(assay: pd.DataFrame) -> SiteOneHot:
    return SiteOneHot.from_keys(
        assay["position"].to_numpy(), assay["wt_aa"].to_numpy(), assay["mt_aa"].to_numpy(), 30
    )


def score_column(assay: pd.DataFrame, seed: int = 0) -> np.ndarray:
    """A noisy zero-shot-like score that carries part of the signal."""
    rng = np.random.default_rng(seed)
    y = assay["DMS_score"].to_numpy()
    return (y + rng.normal(scale=1.5, size=len(y)))[:, None]


@pytest.mark.parametrize("shape", [(120, 7), (12, 30)], ids=["rows>cols", "cols>rows"])
@pytest.mark.parametrize("free", [[], [2]], ids=["all-penalised", "one-free"])
def test_dense_path_is_the_closed_form(shape: tuple[int, int], free: list[int]) -> None:
    rng = np.random.default_rng(1)
    dense = rng.normal(size=shape) * rng.uniform(0.1, 10, size=shape[1]) + 3.0
    dense[:, 4] = 2.5  # a constant column is left at 0, not divided by 0
    y = rng.normal(size=shape[0])
    x = FeatureMatrix.of(dense)
    for alpha, fit in zip(ALPHAS, ridge_path(x, y, ALPHAS, free), strict=True):
        expected = closed_form(dense, None, y, alpha, free)
        np.testing.assert_allclose(fit.predict(x), expected, atol=1e-8)


def test_dense_path_matches_scikit_learn_on_standardised_columns() -> None:
    rng = np.random.default_rng(2)
    dense = rng.normal(size=(80, 5)) * [1, 10, 100, 0.1, 3]
    y = rng.normal(size=80)
    scaled = (dense - dense.mean(axis=0)) / dense.std(axis=0)  # what StandardScaler does
    for alpha, fit in zip(ALPHAS, ridge_path(FeatureMatrix.of(dense), y, ALPHAS, []), strict=True):
        reference = Ridge(alpha=alpha).fit(scaled, y)  # pyright: ignore[reportUnknownMemberType]
        expected = np.asarray(reference.predict(scaled), dtype=np.float64)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        np.testing.assert_allclose(fit.predict(FeatureMatrix.of(dense)), expected, atol=1e-10)


@pytest.mark.parametrize("augmented", [False, True], ids=["onehot", "augmented"])
def test_site_path_is_the_closed_form(assay: pd.DataFrame, augmented: bool) -> None:
    site = site_of(assay)
    dense = score_column(assay) if augmented else np.zeros((len(assay), 0))
    x = FeatureMatrix(dense=dense, names=("score",) if augmented else (), site=site)
    y = assay["DMS_score"].to_numpy()
    free = [0] if augmented else []
    for alpha, fit in zip(ALPHAS, ridge_path(x, y, ALPHAS, free), strict=True):
        expected = closed_form(dense, site.to_dense(), y, alpha, free)
        np.testing.assert_allclose(fit.predict(x), expected, atol=1e-6)


def test_site_one_hot_is_the_mutant_minus_wild_type_sequence(assay: pd.DataFrame) -> None:
    site = site_of(assay)
    dense = site.to_dense()
    row = assay.iloc[7]
    position, wt, mt = int(row["position"]), str(row["wt_aa"]), str(row["mt_aa"])
    from mutascope.alphabet import AA_ORDER

    expected = np.zeros(20 * 30)
    expected[20 * position + AA_ORDER.index(mt)] = 1
    expected[20 * position + AA_ORDER.index(wt)] = -1
    np.testing.assert_array_equal(dense[7], expected)
    v = np.random.default_rng(0).normal(size=len(assay))
    np.testing.assert_allclose(site.rmatvec(v), dense.T @ v)


def test_unpenalised_score_is_not_shrunk(assay: pd.DataFrame) -> None:
    """Augmented ridge: at a huge penalty the one-hot terms vanish, the score's weight stays."""
    y = assay["DMS_score"].to_numpy()
    dense = score_column(assay)
    x = FeatureMatrix(dense=dense, names=("score",), site=site_of(assay))
    fit = ridge_path(x, y, [1e9], [0])[0]
    centred = dense[:, 0] - dense[:, 0].mean()
    slope = float(centred @ (y - y.mean()) / (centred @ centred))  # least-squares slope
    assert fit.coef_dense[0] == pytest.approx(slope, rel=1e-6)
    assert fit.coef_site is not None and np.abs(fit.coef_site).max() < 1e-6


def test_collinear_free_columns_fail(assay: pd.DataFrame) -> None:
    x = FeatureMatrix(dense=np.ones((len(assay), 1)), names=("score",))
    with pytest.raises(RidgeError, match="collinear"):
        ridge_path(x, assay["DMS_score"].to_numpy(), ALPHAS, [0])


def test_inner_cv_picks_the_alpha_with_least_held_out_error(assay: pd.DataFrame) -> None:
    rng = np.random.default_rng(3)
    dense = rng.normal(size=(len(assay), 12))
    y = assay["DMS_score"].to_numpy()
    groups = assay["fold_contiguous"].to_numpy()
    x = FeatureMatrix.of(np.hstack([dense, score_column(assay)]))
    model = InnerCVRidge(ALPHAS).fit(x, y, groups)
    squared = np.zeros(len(ALPHAS))
    for g in np.unique(groups):
        held = groups == g
        for k, fit in enumerate(ridge_path(x.take(~held), y[~held], ALPHAS, [])):
            squared[k] += np.sum((fit.predict(x.take(held)) - y[held]) ** 2)
    assert model.alpha_ is not None
    assert model.alpha_ == ALPHAS[int(np.argmin(squared))]
    assert model.inner_mse_ is not None
    np.testing.assert_allclose(model.inner_mse_, squared / len(y))
    refit = ridge_path(x, y, [model.alpha_], [])[0]
    np.testing.assert_allclose(model.predict(x), refit.predict(x))


def test_inner_cv_needs_two_training_folds(assay: pd.DataFrame) -> None:
    x = FeatureMatrix.of(np.ones((len(assay), 1)))
    with pytest.raises(RidgeError, match="at least 2"):
        InnerCVRidge(ALPHAS).fit(x, assay["DMS_score"].to_numpy(), np.zeros(len(assay), int))


@pytest.mark.parametrize("scheme", ["random", "modulo", "contiguous"])
@pytest.mark.parametrize("kind", ["dense", "augmented"])
def test_test_fold_labels_never_reach_its_predictions(
    assay: pd.DataFrame, scheme: str, kind: str
) -> None:
    """Change every label of one test fold: that fold's predictions must not move at all.

    The penalty, the scaling and the coefficients all come from the other folds, so this holds
    exactly. Labels leaking through the inner CV (or anywhere else) would break it.
    """
    y = assay["DMS_score"].to_numpy()
    folds = assay[f"fold_{scheme}"].to_numpy()
    if kind == "dense":
        x = FeatureMatrix.of(np.random.default_rng(4).normal(size=(len(assay), 10)))
        unpenalized: tuple[str, ...] = ()
    else:
        x = FeatureMatrix(dense=score_column(assay), names=("score",), site=site_of(assay))
        unpenalized = ("score",)

    def make() -> InnerCVRidge:
        return InnerCVRidge(ALPHAS, unpenalized)

    before = cross_validate(x, y, folds, make)
    changed = y.copy()
    test = folds == 2
    changed[test] = np.random.default_rng(9).permutation(changed[test]) * 7 - 3
    after = cross_validate(x, changed, folds, make)
    np.testing.assert_array_equal(after.prediction[test], before.prediction[test])
    assert not np.array_equal(after.prediction[~test], before.prediction[~test])

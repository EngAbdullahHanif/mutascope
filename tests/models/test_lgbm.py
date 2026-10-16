"""LightGBM tuned inside the training folds, and the PCA of the embeddings (ADR 0022)."""

from typing import Any, ClassVar

import numpy as np
import pytest
from tests.models.embedding_data import features
from tests.synthetic_assays import synthetic_assay

from mutascope.eval.metrics import spearman
from mutascope.features.matrix import FeatureMatrix, SiteOneHot
from mutascope.models.cv import cross_validate
from mutascope.models.lgbm import InnerCVLightGBM, LightGBMError, search_space
from mutascope.models.registry import model_factory

pytestmark = pytest.mark.lightgbm

SMALL: dict[str, Any] = {
    "n_trials": 3,
    "max_rounds": 60,
    "patience": 10,
    "seed": 0,
    "fixed": {
        "objective": "regression",
        "metric": "l2",
        "bagging_freq": 1,
        "num_threads": 1,
        "deterministic": True,
        "force_row_wise": True,
        "verbose": -1,
    },
    "search": {
        "learning_rate": {"low": 0.05, "high": 0.3, "log": True},
        "num_leaves": {"low": 4, "high": 16, "log": True, "int": True},
        "min_data_in_leaf": {"low": 5, "high": 20, "int": True},
        "feature_fraction": {"low": 0.5, "high": 1.0},
    },
}


def folds_of(n: int, scheme: str = "contiguous") -> np.ndarray:
    if scheme == "contiguous":
        return np.repeat(np.arange(5), -(-n // 5))[:n].astype(np.int64)
    return (np.arange(n) % 5).astype(np.int64)


def lgbm(pca: int | None = None, **changes: Any) -> InnerCVLightGBM:
    params = SMALL | changes | {"pca_components": pca}
    factory = model_factory("lgbm_pca" if pca else "lgbm", params)
    model = factory()
    assert isinstance(model, InnerCVLightGBM)
    return model


class Recorder(InnerCVLightGBM):
    """Keeps every fitted model, in fit order, so a test can inspect each fold's PCA."""

    fitted: ClassVar[list["Recorder"]] = []

    def fit(self, x: FeatureMatrix, y: np.ndarray, groups: np.ndarray) -> "Recorder":
        super().fit(x, y, groups)
        Recorder.fitted.append(self)
        return self


def recorder(pca: int | None) -> Recorder:
    base = lgbm(pca)
    return Recorder(
        base.search,
        base.fixed,
        n_trials=base.n_trials,
        max_rounds=base.max_rounds,
        patience=base.patience,
        seed=base.seed,
        pca_components=pca,
    )


@pytest.mark.parametrize("scheme", ["random", "contiguous"])
def test_changing_a_test_row_embedding_leaves_the_pca_unchanged(scheme: str) -> None:
    """The PCA is fitted on training rows only: a test fold's embeddings cannot move it."""
    x, y = features(150)
    folds = folds_of(150, scheme)
    Recorder.fitted = []
    cross_validate(x, y, folds, lambda: recorder(pca=4))
    original = Recorder.fitted

    changed = x.dense.copy()
    test = folds == 2
    embedding = [j for j, name in enumerate(x.names) if ":embedding_" in name]
    changed[np.ix_(test, embedding)] = 1e3 * np.random.default_rng(9).normal(
        size=(int(test.sum()), len(embedding))
    )
    Recorder.fitted = []
    cross_validate(FeatureMatrix(dense=changed, names=x.names), y, folds, lambda: recorder(pca=4))
    modified = Recorder.fitted

    for fold, (before, after) in enumerate(zip(original, modified, strict=True)):
        assert before.pca_ is not None and after.pca_ is not None
        assert before.pca_.blocks_ is not None and after.pca_.blocks_ is not None
        pairs = zip(before.pca_.blocks_, after.pca_.blocks_, strict=True)
        same = all(
            np.array_equal(b.components, a.components) and np.array_equal(b.mean, a.mean)
            for b, a in pairs
        )
        # Fold 2 is the test fold whose rows were changed: its model never saw them. Every
        # other fold trained on them, so its PCA must move (the check can tell).
        assert same == (fold == 2), fold


# --- LightGBM --------------------------------------------------------------------------------


@pytest.mark.parametrize("pca", [None, 4])
@pytest.mark.parametrize("scheme", ["random", "modulo", "contiguous"])
def test_test_fold_labels_never_reach_its_predictions(pca: int | None, scheme: str) -> None:
    """Search, rounds, PCA and trees all come from the other folds (ADR 0021's guard)."""
    assay = synthetic_assay("A_TEST", seed=5)
    n = len(assay)
    x, _ = features(n, seed=3)
    y = assay["DMS_score"].to_numpy(np.float64)
    folds = assay[f"fold_{scheme}"].to_numpy(np.int64)
    test = folds == 1
    baseline = cross_validate(x, y, folds, lambda: lgbm(pca))
    for change in ("permute", "scale"):
        y_changed = y.copy()
        if change == "permute":
            y_changed[test] = np.random.default_rng(0).permutation(y[test])
        else:
            y_changed[test] = 7 * y[test] - 3
        after = cross_validate(x, y_changed, folds, lambda: lgbm(pca))
        np.testing.assert_array_equal(after.prediction[test], baseline.prediction[test])


def test_learns_a_nonlinear_signal_under_contiguous_folds() -> None:
    x, y = features(400)
    oof = cross_validate(x, y, folds_of(400), lambda: lgbm())
    assert spearman(oof.target, oof.prediction) > 0.6


def test_fit_is_deterministic_and_reports_its_choices() -> None:
    x, y = features(200)
    groups = folds_of(200)
    first, second = lgbm(), lgbm()
    first.fit(x, y, groups)
    second.fit(x, y, groups)
    np.testing.assert_array_equal(first.predict(x), second.predict(x))
    chosen = first.selected()
    assert set(chosen) == {*SMALL["search"], "num_boost_round"}
    assert 1 <= chosen["num_boost_round"] <= SMALL["max_rounds"]
    assert 4 <= chosen["num_leaves"] <= 16 and chosen["num_leaves"].is_integer()


def test_contributions_sum_to_the_prediction() -> None:
    """TreeSHAP: per row, the contributions plus a constant expected value are the prediction."""
    x, y = features(200)
    for pca in (None, 4):
        model = lgbm(pca)
        model.fit(x, y, folds_of(200))
        values, names = model.contributions(x)
        assert values.shape == (200, len(names))
        expected = model.predict(x) - values.sum(axis=1)
        np.testing.assert_allclose(expected, expected[0], atol=1e-9)
        assert any("embedding_pc" in name for name in names) == (pca is not None)


def test_refuses_a_site_one_hot_and_bad_settings() -> None:
    x, y = features(50)
    site = SiteOneHot(plus=np.ones(50, np.int64), minus=np.zeros(50, np.int64), width=20)
    with pytest.raises(LightGBMError, match="site one-hot"):
        lgbm().fit(FeatureMatrix(dense=x.dense, names=x.names, site=site), y, folds_of(50))
    with pytest.raises(LightGBMError, match="2 training folds"):
        lgbm().fit(x, y, np.zeros(50, np.int64))
    with pytest.raises(LightGBMError, match="low < high"):
        search_space({"num_leaves": {"low": 8, "high": 4}})

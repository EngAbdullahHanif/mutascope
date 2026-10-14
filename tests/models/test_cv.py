"""Cross-validation loop: every variant predicted once, by a model that never saw it."""

from typing import Any

import numpy as np
import pandas as pd
import pytest
from tests.models.probe import probe_features, probe_model
from tests.models.proteingym_reference import merge_supervised_metrics, proteinnpt_standardise
from tests.synthetic_assays import synthetic_assay

from mutascope.eval.schemas import SupervisedMetricSchema
from mutascope.features.base import KEY_COLUMNS, build_features
from mutascope.models.baselines import TrainingMean
from mutascope.models.cv import CrossValidationError, cross_validate, cv_metrics_table, oof_metrics


class Spy:
    """Records what it is trained on; predicts the row id (column 0) so tests can trace rows."""

    fitted: list[tuple[np.ndarray, np.ndarray]] = []  # noqa: RUF012 — shared on purpose

    def fit(self, x: np.ndarray, y: np.ndarray) -> "Spy":
        Spy.fitted.append((x[:, 0].astype(int), y.copy()))
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        return x[:, 0].astype(np.float64)


@pytest.fixture
def assay() -> pd.DataFrame:
    return synthetic_assay("A_TEST", seed=3)


@pytest.fixture(autouse=True)
def reset_spy() -> None:
    Spy.fitted = []


def row_ids(n: int) -> np.ndarray:
    return np.arange(n, dtype=np.float64)[:, None]


@pytest.mark.parametrize("scheme", ["random", "modulo", "contiguous"])
def test_each_fold_trains_on_the_others_only(assay: pd.DataFrame, scheme: str) -> None:
    folds = assay[f"fold_{scheme}"].to_numpy()
    y = assay["DMS_score"].to_numpy()
    oof = cross_validate(row_ids(len(y)), y, folds, Spy)
    assert len(Spy.fitted) == 5  # one fresh model per fold
    for fold, (seen, _) in zip(range(5), Spy.fitted, strict=True):
        assert set(seen) == set(np.flatnonzero(folds != fold))
    # Spy predicts the row id: every row is predicted once, in its own fold.
    assert (oof.prediction == np.arange(len(y))).all()
    assert (oof.fold == folds).all()


def test_targets_use_training_statistics_only(assay: pd.DataFrame) -> None:
    """Training and test targets are standardised exactly as ProteinNPT does it."""
    folds = assay["fold_contiguous"].to_numpy()
    y = assay["DMS_score"].to_numpy()
    oof = cross_validate(row_ids(len(y)), y, folds, Spy)
    for fold, (_, trained_on) in zip(range(5), Spy.fitted, strict=True):
        train, test = folds != fold, folds == fold
        expected_train, expected_test = proteinnpt_standardise(y[train], y[test])
        np.testing.assert_array_equal(trained_on, expected_train)
        np.testing.assert_array_equal(oof.target[test], expected_test)


def test_folds_present_are_used_not_assumed(assay: pd.DataFrame) -> None:
    """Two ProteinGym assays have 4 folds in every scheme (4 mutated positions)."""
    four = assay[assay["fold_contiguous"] < 4]
    folds = four["fold_contiguous"].to_numpy()
    oof = cross_validate(row_ids(len(four)), four["DMS_score"].to_numpy(), folds, Spy)
    assert len(Spy.fitted) == 4
    assert not np.isnan(oof.prediction).any()


@pytest.mark.parametrize(
    ("folds", "y", "model", "message"),
    [
        (np.zeros(6, dtype=int), np.arange(6.0), Spy, "at least 2 folds"),
        (np.array([0, 0, 0, 1, 1, 1]), np.array([1.0, 1, 1, 2, 3, 4]), Spy, "constant"),
        (np.array([0, 0, 1, 1]), np.arange(4.0), lambda: NaNModel(), "non-finite"),
    ],
    ids=["one-fold", "constant-train", "nan-prediction"],
)
def test_bad_inputs_fail(folds: np.ndarray, y: np.ndarray, model: Any, message: str) -> None:
    with pytest.raises(CrossValidationError, match=message):
        cross_validate(row_ids(len(y)), y, folds, model)


class NaNModel:
    def fit(self, x: np.ndarray, y: np.ndarray) -> "NaNModel":
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        prediction = np.empty(x.shape[0])
        prediction.fill(np.nan)
        return prediction


@pytest.mark.parametrize("scheme", ["random", "modulo", "contiguous"])
def test_pooled_metrics_match_proteingym(assay: pd.DataFrame, scheme: str) -> None:
    """Pooled Spearman and MSE equal ``merge_supervised.py`` on the same out-of-fold rows."""
    oof = cross_validate(
        probe_features(assay),
        assay["DMS_score"].to_numpy(),
        assay[f"fold_{scheme}"].to_numpy(),
        probe_model,
    )
    pooled = {metric: value for fold, metric, value in oof_metrics(oof) if fold is None}
    spearman, mse = merge_supervised_metrics(oof.target, oof.prediction)
    assert pooled["spearman"] == pytest.approx(spearman, abs=1e-12)
    assert pooled["mse"] == pytest.approx(mse, abs=1e-12)
    assert pooled["spearman"] > 0.3  # the probe learns something under every scheme


def test_per_fold_rows_are_each_fold_alone(assay: pd.DataFrame) -> None:
    folds = assay["fold_random"].to_numpy()
    oof = cross_validate(probe_features(assay), assay["DMS_score"].to_numpy(), folds, probe_model)
    rows = {(fold, metric): value for fold, metric, value in oof_metrics(oof)}
    for fold in range(5):
        test = folds == fold
        expected = merge_supervised_metrics(oof.target[test], oof.prediction[test])
        assert rows[(fold, "spearman")] == pytest.approx(expected[0], abs=1e-12)
        assert rows[(fold, "mse")] == pytest.approx(expected[1], abs=1e-12)


def test_training_mean_spearman_is_undefined_and_mse_is_the_mean_square() -> None:
    """Regression: the standardised training mean is 0 only up to rounding, differently per
    fold, so unrounded "constant" predictions got a pooled Spearman from ranking that noise."""
    assays = pd.concat([synthetic_assay(f"A{i}_TEST", seed=i) for i in range(4)])
    table = cv_metrics_table(
        assays,
        build_features("none", assays),
        TrainingMean,
        model="training_mean",
        feature_set="none",
        schemes=["random", "modulo", "contiguous"],
    )
    SupervisedMetricSchema.validate(table)
    assert table.loc[table["metric"] == "spearman", "value"].isna().all()
    for (assay_id, scheme), rows in table[table["fold"].isna()].groupby(["assay_id", "scheme"]):
        one = assays[assays["assay_id"] == assay_id]
        oof = cross_validate(
            np.zeros((len(one), 0)),
            one["DMS_score"].to_numpy(),
            one[f"fold_{scheme}"].to_numpy(),
            TrainingMean,
        )
        mse = rows.loc[rows["metric"] == "mse", "value"].item()
        assert mse == pytest.approx(float(np.mean(oof.target**2)), abs=1e-12)


def test_results_table_is_long_format(assay: pd.DataFrame) -> None:
    table = cv_metrics_table(
        assay,
        build_features("none", assay),
        TrainingMean,
        model="training_mean",
        feature_set="none",
        schemes=["modulo"],
    )
    assert list(table.columns) == [
        "model",
        "feature_set",
        "assay_id",
        "scheme",
        "fold",
        "metric",
        "value",
    ]
    # Pooled (fold null) plus 5 folds, two metrics each.
    assert len(table) == 2 * 6
    assert table["fold"].isna().sum() == 2


def test_feature_builders_never_see_labels(assay: pd.DataFrame) -> None:
    from mutascope.features import base

    seen: list[list[str]] = []

    def spy(keys: pd.DataFrame) -> np.ndarray:
        seen.append(list(keys.columns))
        return np.zeros((len(keys), 0))

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(base.FEATURE_SETS, "spy", spy)  # pyright: ignore[reportArgumentType]
        build_features("spy", assay)
    assert seen == [list(KEY_COLUMNS)]


def test_unknown_feature_set_fails(assay: pd.DataFrame) -> None:
    with pytest.raises(KeyError, match="unknown feature set"):
        build_features("nope", assay)

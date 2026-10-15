"""Cross-validation loop: every variant predicted once, by a model that never saw it."""

import pickle
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from tests.models.probe import probe_design, probe_features, probe_model
from tests.models.proteingym_reference import merge_supervised_metrics, proteinnpt_standardise
from tests.synthetic_assays import synthetic_assay

from mutascope.eval.schemas import SupervisedMetricSchema
from mutascope.features.base import KEY_COLUMNS, KeyDesign, build_features
from mutascope.features.matrix import FeatureMatrix
from mutascope.features.sets import FeatureSet, StoreDesign
from mutascope.models.baselines import TrainingMean
from mutascope.models.cv import (
    Checkpoint,
    CrossValidationError,
    DesignBuilder,
    cross_validate,
    cv_metrics_table,
    oof_metrics,
)
from mutascope.models.registry import model_factory


class Spy:
    """Records what it is trained on; predicts the row id (column 0) so tests can trace rows."""

    fitted: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []  # noqa: RUF012 — shared on purpose

    def fit(self, x: FeatureMatrix, y: np.ndarray, groups: np.ndarray) -> "Spy":
        Spy.fitted.append((x.dense[:, 0].astype(int), y.copy(), groups.copy()))
        return self

    def predict(self, x: FeatureMatrix) -> np.ndarray:
        return x.dense[:, 0].astype(np.float64)


@pytest.fixture
def assay() -> pd.DataFrame:
    return synthetic_assay("A_TEST", seed=3)


@pytest.fixture(autouse=True)
def reset_spy() -> None:
    Spy.fitted = []


def row_ids(n: int) -> FeatureMatrix:
    return FeatureMatrix.of(np.arange(n, dtype=np.float64)[:, None])


def key_design(name: str) -> DesignBuilder:
    return lambda assay_id, keys: build_features(name, keys)


@pytest.mark.parametrize("scheme", ["random", "modulo", "contiguous"])
def test_each_fold_trains_on_the_others_only(assay: pd.DataFrame, scheme: str) -> None:
    folds = assay[f"fold_{scheme}"].to_numpy()
    y = assay["DMS_score"].to_numpy()
    oof = cross_validate(row_ids(len(y)), y, folds, Spy)
    assert len(Spy.fitted) == 5  # one fresh model per fold
    for fold, (seen, _, groups) in zip(range(5), Spy.fitted, strict=True):
        assert set(seen) == set(np.flatnonzero(folds != fold))
        # The model gets its training rows' folds (for an inner CV), never the test fold.
        np.testing.assert_array_equal(groups, folds[seen])
        assert fold not in set(groups)
    # Spy predicts the row id: every row is predicted once, in its own fold.
    assert (oof.prediction == np.arange(len(y))).all()
    assert (oof.fold == folds).all()


def test_targets_use_training_statistics_only(assay: pd.DataFrame) -> None:
    """Training and test targets are standardised exactly as ProteinNPT does it."""
    folds = assay["fold_contiguous"].to_numpy()
    y = assay["DMS_score"].to_numpy()
    oof = cross_validate(row_ids(len(y)), y, folds, Spy)
    for fold, (_, trained_on, _) in zip(range(5), Spy.fitted, strict=True):
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
    def fit(self, x: FeatureMatrix, y: np.ndarray, groups: np.ndarray) -> "NaNModel":
        return self

    def predict(self, x: FeatureMatrix) -> np.ndarray:
        prediction = np.empty(x.n_rows)
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
        key_design("none"),
        TrainingMean,
        model="training_mean",
        feature_set="none",
        schemes=["random", "modulo", "contiguous"],
    ).metrics
    SupervisedMetricSchema.validate(table)
    assert table.loc[table["metric"] == "spearman", "value"].isna().all()
    for (assay_id, scheme), rows in table[table["fold"].isna()].groupby(["assay_id", "scheme"]):
        one = assays[assays["assay_id"] == assay_id]
        oof = cross_validate(
            FeatureMatrix.of(np.zeros((len(one), 0))),
            one["DMS_score"].to_numpy(),
            one[f"fold_{scheme}"].to_numpy(),
            TrainingMean,
        )
        mse = rows.loc[rows["metric"] == "mse", "value"].item()
        assert mse == pytest.approx(float(np.mean(oof.target**2)), abs=1e-12)


def test_results_table_is_long_format(assay: pd.DataFrame) -> None:
    result = cv_metrics_table(
        assay,
        key_design("none"),
        TrainingMean,
        model="training_mean",
        feature_set="none",
        schemes=["modulo"],
    )
    table = result.metrics
    assert result.hyperparameters.empty  # the baseline tunes nothing
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

    def spy(keys: pd.DataFrame) -> FeatureMatrix:
        seen.append(list(keys.columns))
        return FeatureMatrix.of(np.zeros((len(keys), 0)))

    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(base.FEATURE_SETS, "spy", spy)  # pyright: ignore[reportArgumentType]
        build_features("spy", assay)
    assert seen == [list(KEY_COLUMNS)]


def test_the_loop_gives_designs_keys_only_one_assay_at_a_time() -> None:
    """``cv_metrics_table`` builds each assay's features from its keys, never its labels."""
    assays = pd.concat([synthetic_assay(f"A{i}_TEST", seed=i) for i in range(3)])
    calls: list[tuple[str, list[str], int]] = []

    def design(assay_id: str, keys: pd.DataFrame) -> FeatureMatrix:
        calls.append((assay_id, list(keys.columns), len(keys)))
        return probe_features(keys)

    cv_metrics_table(
        assays, design, probe_model, model="probe", feature_set="spy", schemes=["random"]
    )
    sizes = Counter(assays["assay_id"].astype(str).tolist())
    assert calls == [(a, list(KEY_COLUMNS), sizes[a]) for a in sorted(sizes)]


def test_a_model_refusing_its_input_names_the_assay(assay: pd.DataFrame) -> None:
    class Refuses(TrainingMean):
        def fit(self, x: FeatureMatrix, y: np.ndarray, groups: np.ndarray) -> "Refuses":
            raise ValueError("no thanks")

    with pytest.raises(CrossValidationError, match="A_TEST, random: no thanks"):
        cv_metrics_table(
            assay, key_design("none"), Refuses, model="m", feature_set="none", schemes=["random"]
        )


def test_unknown_feature_set_fails(assay: pd.DataFrame) -> None:
    with pytest.raises(KeyError, match="unknown feature set"):
        build_features("nope", assay)


@pytest.fixture
def three_assays(monkeypatch: pytest.MonkeyPatch) -> pd.DataFrame:
    # Worker processes unpickle the test's probe from `tests.models.probe`. Pytest imports test
    # modules without putting the repo root on sys.path (--import-mode=importlib); spawned
    # workers copy the parent's sys.path, so it goes there.
    monkeypatch.setattr(sys, "path", [str(Path(__file__).parents[2]), *sys.path])
    return pd.concat([synthetic_assay(f"{name}_TEST", seed=i) for i, name in enumerate("ABC")])


def test_parallel_assays_give_the_serial_table(three_assays: pd.DataFrame) -> None:
    """``jobs`` changes where each assay runs, never what it computes or the row order."""
    make_model = model_factory("ridge", {"alphas": {"log10_min": -2, "log10_max": 3, "num": 6}})
    runs = {
        jobs: cv_metrics_table(
            three_assays,
            probe_design,
            make_model,
            model="ridge",
            feature_set="probe",
            schemes=["random", "contiguous"],
            jobs=jobs,
        )
        for jobs in (1, 3)
    }
    pd.testing.assert_frame_equal(runs[1].metrics, runs[3].metrics)
    pd.testing.assert_frame_equal(runs[1].hyperparameters, runs[3].hyperparameters)
    assert list(runs[3].metrics["assay_id"].unique()) == ["A_TEST", "B_TEST", "C_TEST"]


def test_a_worker_error_names_the_assay(three_assays: pd.DataFrame) -> None:
    """An assay failing in a worker process fails the run, named as in a serial run."""
    flat = three_assays.copy()
    flat.loc[flat["assay_id"] == "B_TEST", "DMS_score"] = 1.0
    with pytest.raises(CrossValidationError, match="B_TEST, random: fold 0: training targets"):
        cv_metrics_table(
            flat,
            probe_design,
            probe_model,
            model="probe",
            feature_set="probe",
            schemes=["random"],
            jobs=2,
        )


def test_jobs_must_be_positive(assay: pd.DataFrame) -> None:
    with pytest.raises(CrossValidationError, match="jobs must be at least 1"):
        cv_metrics_table(
            assay, probe_design, probe_model, model="m", feature_set="f", schemes=["random"], jobs=0
        )


@pytest.mark.parametrize(
    "factory",
    [
        model_factory("ridge", {"alphas": {"log10_min": 0, "log10_max": 1, "num": 2}}),
        model_factory("training_mean"),
        model_factory("zero_shot"),
        KeyDesign("none"),
        StoreDesign(Path("store"), FeatureSet("s", ("onehot",), structure=False)),
    ],
    ids=["ridge", "training_mean", "zero_shot", "key_design", "store_design"],
)
def test_factories_and_designs_pickle(factory: object) -> None:
    """Worker processes receive the model factory and the design by pickling."""
    assert pickle.loads(pickle.dumps(factory)) is not None


def test_a_resumed_run_loads_finished_assays_and_gives_the_same_table(
    three_assays: pd.DataFrame, tmp_path: Path
) -> None:
    """A run killed after some assays resumes from the checkpoint without redoing them."""
    make_model = model_factory("ridge", {"alphas": {"log10_min": -2, "log10_max": 3, "num": 6}})
    common: dict[str, Any] = {"model": "ridge", "feature_set": "probe", "schemes": ["random"]}
    full = cv_metrics_table(three_assays, probe_design, make_model, **common)

    checkpoint = Checkpoint(tmp_path / "ckpt", "settings-1")
    first_two = three_assays[three_assays["assay_id"] != "C_TEST"]
    cv_metrics_table(first_two, probe_design, make_model, checkpoint=checkpoint, **common)
    built: list[str] = []

    def design(assay_id: str, keys: pd.DataFrame) -> FeatureMatrix:
        built.append(assay_id)
        return probe_features(keys)

    resumed = cv_metrics_table(three_assays, design, make_model, checkpoint=checkpoint, **common)
    assert built == ["C_TEST"]  # A and B came from the checkpoint
    pd.testing.assert_frame_equal(resumed.metrics, full.metrics)
    pd.testing.assert_frame_equal(resumed.hyperparameters, full.hyperparameters)

    with pytest.raises(CrossValidationError, match="other settings"):
        cv_metrics_table(
            three_assays,
            probe_design,
            make_model,
            checkpoint=Checkpoint(checkpoint.directory, "x"),
            **common,
        )

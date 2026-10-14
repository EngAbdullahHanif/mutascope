"""Per-assay cross-validation under ProteinGym's supervised protocol (ADR 0019).

For one assay and one scheme, each fold in turn is the test set and the model is trained on
all the others (no validation fold, as in ProteinNPT's default). What happens per fold follows
ProteinNPT (``proteinnpt/utils/data_utils.py``), which produced ProteinGym's supervised scores:

- The target is ``DMS_score`` standardised with the training folds' mean and population
  standard deviation (``np.nanstd``, ddof 0). The test fold's targets get the same transform,
  so predictions and targets share one scale and test data never shapes it.
- The model is fit on the standardised training targets and predicts the test fold.

The metrics follow ProteinGym's ``merge_supervised.py``: Spearman and MSE over the out-of-fold
predictions of all folds pooled, one number per (assay, scheme). We also report each fold
alone as a diagnostic; those rows are never the headline.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt
import pandas as pd

from mutascope.eval.metrics import SUPERVISED_METRICS, mse, spearman
from mutascope.eval.schemas import SupervisedMetricSchema

FloatArray = npt.NDArray[np.float64]
FloatMatrix = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]


class Regressor(Protocol):
    """The scikit-learn regressor interface, which is all the loop needs from a model."""

    def fit(self, x: FloatMatrix, y: FloatArray) -> object: ...

    def predict(self, x: FloatMatrix) -> FloatArray: ...


ModelFactory = Callable[[], Regressor]
"""Returns a fresh, unfitted model: one per fold, so nothing carries over between folds."""


class CrossValidationError(ValueError):
    """The inputs cannot be cross-validated as asked (too few folds, constant targets...)."""


@dataclass(frozen=True)
class OutOfFold:
    """Every variant's prediction from the one model that did not see it during training."""

    fold: IntArray
    """The test fold each variant was predicted in."""
    target: FloatArray
    """``DMS_score`` standardised with its training folds' mean and standard deviation."""
    prediction: FloatArray


def cross_validate(
    x: FloatMatrix,
    y: FloatArray,
    folds: IntArray,
    make_model: ModelFactory,
    *,
    permute_rng: np.random.Generator | None = None,
) -> OutOfFold:
    """Train on all folds but one, predict that one, for every fold present.

    Args:
        x: Features, one row per variant.
        y: ``DMS_score`` (higher = fitter), raw.
        folds: Each variant's fold in one scheme. Only the folds that occur are used: two
            ProteinGym assays mutate 4 positions and have 4 folds in every scheme.
        make_model: Builds the model fit on each fold's training set.
        permute_rng: If given, the training targets are shuffled before every fit (the
            label-permutation canary, :mod:`mutascope.models.canary`). Test targets are not.

    Raises:
        CrossValidationError: Fewer than 2 folds, mismatched lengths, constant training
            targets, or a model that returns the wrong number of predictions or a non-finite
            one.
    """
    n = y.shape[0]
    if x.shape[0] != n or folds.shape[0] != n:
        raise CrossValidationError(f"lengths differ: x {x.shape[0]}, y {n}, folds {folds.shape[0]}")
    fold_ids = np.unique(folds)
    if fold_ids.size < 2:
        raise CrossValidationError(f"need at least 2 folds, got {fold_ids.tolist()}")
    target = np.full(n, np.nan)
    prediction = np.full(n, np.nan)
    for fold in fold_ids:
        test = folds == fold
        train = ~test
        mean = float(np.mean(y[train]))
        std = float(np.std(y[train]))
        if not std > 0:
            raise CrossValidationError(f"fold {fold}: training targets are constant")
        y_train = (y[train] - mean) / std
        if permute_rng is not None:
            y_train = permute_rng.permutation(y_train)
        model = make_model()
        model.fit(x[train], y_train)
        predicted = np.asarray(model.predict(x[test]), dtype=np.float64)
        if predicted.shape != (int(test.sum()),) or not np.isfinite(predicted).all():
            raise CrossValidationError(
                f"fold {fold}: model returned shape {predicted.shape} or non-finite values "
                f"for {int(test.sum())} test variants"
            )
        prediction[test] = predicted
        target[test] = (y[test] - mean) / std
    return OutOfFold(fold=folds.astype(np.int64), target=target, prediction=prediction)


def oof_metrics(oof: OutOfFold) -> list[tuple[int | None, str, float]]:
    """``(fold, metric, value)`` for the pooled predictions (fold None) and for each fold."""
    measures: Mapping[str, Callable[[FloatArray, FloatArray], float]] = {
        "spearman": spearman,
        "mse": mse,
    }
    rows: list[tuple[int | None, str, float]] = [
        (None, name, measures[name](oof.target, oof.prediction)) for name in SUPERVISED_METRICS
    ]
    for fold in np.unique(oof.fold):
        test = oof.fold == fold
        rows += [
            (int(fold), name, measures[name](oof.target[test], oof.prediction[test]))
            for name in SUPERVISED_METRICS
        ]
    return rows


def cv_metrics_table(
    data: pd.DataFrame,
    features: FloatMatrix,
    make_model: ModelFactory,
    *,
    model: str,
    feature_set: str,
    schemes: Sequence[str],
) -> pd.DataFrame:
    """Cross-validate one model on every assay in ``data`` under each scheme.

    Args:
        data: One row per variant: ``assay_id``, ``DMS_score`` and ``fold_<scheme>`` for each
            scheme (:func:`mutascope.data.folds.load_cv_table`).
        features: One row per row of ``data``, in the same order.
        make_model: Builds a fresh model per fold.
        model: Written to the ``model`` column.
        feature_set: Written to the ``feature_set`` column.
        schemes: CV schemes to run, e.g. ``("random", "modulo", "contiguous")``.

    Returns:
        A table valid under :class:`SupervisedMetricSchema`.
    """
    if features.shape[0] != len(data):
        raise CrossValidationError(f"{features.shape[0]} feature rows for {len(data)} variants")
    records: list[tuple[str, str, int | None, str, float]] = []
    positions = np.arange(len(data))
    for assay_id, rows in data.groupby("assay_id", sort=True).indices.items():
        index = positions[rows]
        y = data["DMS_score"].to_numpy(np.float64)[index]
        for scheme in schemes:
            folds = data[f"fold_{scheme}"].to_numpy(np.int64)[index]
            try:
                oof = cross_validate(features[index], y, folds, make_model)
            except CrossValidationError as exc:
                raise CrossValidationError(f"{assay_id}, {scheme}: {exc}") from exc
            records += [
                (str(assay_id), scheme, fold, metric, value)
                for fold, metric, value in oof_metrics(oof)
            ]
    table = pd.DataFrame.from_records(
        records, columns=["assay_id", "scheme", "fold", "metric", "value"]
    )
    table["fold"] = table["fold"].astype("Int64")
    table.insert(0, "model", model)
    table.insert(1, "feature_set", feature_set)
    return SupervisedMetricSchema.validate(table, lazy=True)

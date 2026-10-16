"""Per-assay cross-validation under ProteinGym's supervised protocol (ADR 0019).

For one assay and one scheme, each fold in turn is the test set and the model is trained on
all the others (no validation fold, as in ProteinNPT's default). What happens per fold follows
ProteinNPT (``proteinnpt/utils/data_utils.py``), which produced ProteinGym's supervised scores:

- The target is ``DMS_score`` standardised with the training folds' mean and population
  standard deviation (``np.nanstd``, ddof 0). The test fold's targets get the same transform,
  so predictions and targets share one scale and test data never shapes it.
- The model is fit on the standardised training targets and predicts the test fold. It also
  gets each training row's fold, so a model that tunes itself (ridge's penalty) can run an
  inner cross-validation over the training folds, split the same way as the outer one, and
  never touch the test fold.

The metrics follow ProteinGym's ``merge_supervised.py``: Spearman and MSE over the out-of-fold
predictions of all folds pooled, one number per (assay, scheme). We also report each fold
alone as a diagnostic; those rows are never the headline.

Features are built one assay at a time (:data:`DesignBuilder`): every assay's matrix of the
largest feature set at once would take 14.7 GB in float64 (ADR 0020). Assays are independent,
so :func:`cv_metrics_table` can run them in parallel processes (ADR 0022).
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd
import structlog

from mutascope.eval.metrics import SUPERVISED_METRICS, mse, spearman
from mutascope.eval.schemas import SelectedHyperparameterSchema, SupervisedMetricSchema
from mutascope.features.base import KEY_COLUMNS
from mutascope.features.matrix import FeatureMatrix, FloatArray, IntArray
from mutascope.utils.parallel import run_parallel

log = structlog.get_logger(__name__)


class Regressor(Protocol):
    """What the loop needs from a model: scikit-learn's fit / predict, plus training folds."""

    def fit(self, x: FeatureMatrix, y: FloatArray, groups: IntArray) -> object:
        """Fit on training rows; ``groups`` is each row's (outer) fold, for an inner CV."""
        ...

    def predict(self, x: FeatureMatrix) -> FloatArray: ...


@runtime_checkable
class Tuned(Protocol):
    """A model that chooses hyperparameters itself (inside its training rows) and reports them."""

    def selected(self) -> Mapping[str, float]:
        """The hyperparameters the last fit chose, by name."""
        ...


ModelFactory = Callable[[], Regressor]
"""Returns a fresh, unfitted model: one per fold, so nothing carries over between folds."""

DesignBuilder = Callable[[str, pd.DataFrame], FeatureMatrix]
"""``(assay_id, keys) -> features``, one row per key in order. Keys are :data:`KEY_COLUMNS`
only, never labels (ADR 0019)."""


_MetricRecord = tuple[str, str, int | None, str, float]
_ChoiceRecord = tuple[str, str, int, str, float]
_AssayOutput = tuple[list[_MetricRecord], list[_ChoiceRecord], float]
"""One assay's metric rows, chosen hyperparameters and wall seconds."""


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
    selected: tuple[Mapping[str, float], ...] = ()
    """Per fold, in fold order: the hyperparameters a :class:`Tuned` model chose; else empty."""


@dataclass(frozen=True)
class CVResult:
    """What :func:`cv_metrics_table` returns."""

    metrics: pd.DataFrame
    """:class:`SupervisedMetricSchema` rows."""
    hyperparameters: pd.DataFrame
    """:class:`SelectedHyperparameterSchema` rows; empty for a model that tunes nothing."""


def cross_validate(
    x: FeatureMatrix,
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
    if x.n_rows != n or folds.shape[0] != n:
        raise CrossValidationError(f"lengths differ: x {x.n_rows}, y {n}, folds {folds.shape[0]}")
    fold_ids = np.unique(folds)
    if fold_ids.size < 2:
        raise CrossValidationError(f"need at least 2 folds, got {fold_ids.tolist()}")
    target = np.full(n, np.nan)
    prediction = np.full(n, np.nan)
    selected: list[Mapping[str, float]] = []
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
        model.fit(x.take(train), y_train, folds[train].astype(np.int64))
        predicted = np.asarray(model.predict(x.take(test)), dtype=np.float64)
        if predicted.shape != (int(test.sum()),) or not np.isfinite(predicted).all():
            raise CrossValidationError(
                f"fold {fold}: model returned shape {predicted.shape} or non-finite values "
                f"for {int(test.sum())} test variants"
            )
        prediction[test] = predicted
        target[test] = (y[test] - mean) / std
        if isinstance(model, Tuned):
            selected.append(dict(model.selected()))
    return OutOfFold(
        fold=folds.astype(np.int64),
        target=target,
        prediction=prediction,
        selected=tuple(selected),
    )


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


def _assay_cv(
    assay_id: str,
    assay: pd.DataFrame,
    design: DesignBuilder,
    make_model: ModelFactory,
    schemes: Sequence[str],
) -> _AssayOutput:
    """Every scheme of one assay: the unit of work :func:`cv_metrics_table` runs in parallel."""
    started = time.monotonic()
    x = design(assay_id, assay[list(KEY_COLUMNS)].reset_index(drop=True))
    if x.n_rows != len(assay):
        raise CrossValidationError(f"{assay_id}: {x.n_rows} feature rows for {len(assay)}")
    y = assay["DMS_score"].to_numpy(np.float64)
    records: list[_MetricRecord] = []
    choices: list[_ChoiceRecord] = []
    for scheme in schemes:
        folds = assay[f"fold_{scheme}"].to_numpy(np.int64)
        try:
            oof = cross_validate(x, y, folds, make_model)
        except ValueError as exc:  # CrossValidationError, or a model refusing its input
            raise CrossValidationError(f"{assay_id}, {scheme}: {exc}") from exc
        records += [
            (assay_id, scheme, fold, metric, value) for fold, metric, value in oof_metrics(oof)
        ]
        if oof.selected:
            fold_ids: list[int] = sorted(set(folds.tolist()))
            choices += [
                (assay_id, scheme, fold, name, float(value))
                for fold, chosen in zip(fold_ids, oof.selected, strict=True)
                for name, value in chosen.items()
            ]
    return records, choices, time.monotonic() - started


@dataclass(frozen=True)
class Checkpoint:
    """Finished assays of one ``train cv`` run, so a killed run resumes where it stopped.

    Each assay's metric rows and chosen hyperparameters are written to ``directory`` as soon
    as it finishes (to a temporary name, then renamed, so a file is never half-written). A
    rerun loads those assays instead of recomputing them. ``fingerprint`` identifies the
    settings (model, feature set, schemes, params): a directory written under other settings
    is refused rather than mixed in. Code changes are not fingerprinted; delete the directory
    after changing a model.
    """

    directory: Path
    fingerprint: str

    def open(self) -> set[str]:
        """Create or check the directory; return the assays already finished."""
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = self.directory / "fingerprint.txt"
        if stamp.exists() and stamp.read_text().strip() != self.fingerprint:
            raise CrossValidationError(
                f"{self.directory} holds a run with other settings; delete it to start over"
            )
        stamp.write_text(self.fingerprint + "\n")
        return {path.name.removesuffix(".metrics.parquet") for path in self._files("metrics")}

    def _files(self, kind: str) -> list[Path]:
        return sorted(self.directory.glob(f"*.{kind}.parquet"))

    def save(self, assay_id: str, output: _AssayOutput) -> None:
        records, choices, _ = output
        # Choices first: an assay counts as finished once its metrics file exists.
        for kind, rows, columns in (
            ("choices", choices, ["assay_id", "scheme", "fold", "name", "value"]),
            ("metrics", records, ["assay_id", "scheme", "fold", "metric", "value"]),
        ):
            frame = pd.DataFrame.from_records(rows, columns=columns)
            frame["fold"] = frame["fold"].astype("Int64")
            target = self.directory / f"{assay_id}.{kind}.parquet"
            partial = target.with_suffix(".tmp")
            frame.to_parquet(partial, index=False)
            partial.replace(target)

    def load(self, assay_id: str) -> _AssayOutput:
        metrics = pd.read_parquet(self.directory / f"{assay_id}.metrics.parquet")
        choices = pd.read_parquet(self.directory / f"{assay_id}.choices.parquet")
        records: list[_MetricRecord] = [
            (str(a), str(s), None if pd.isna(f) else int(f), str(m), float(v))
            for a, s, f, m, v in metrics.itertuples(index=False)
        ]
        chosen: list[_ChoiceRecord] = [
            (str(a), str(s), int(f), str(n), float(v))
            for a, s, f, n, v in choices.itertuples(index=False)
        ]
        return records, chosen, 0.0


def cv_metrics_table(
    data: pd.DataFrame,
    design: DesignBuilder,
    make_model: ModelFactory,
    *,
    model: str,
    feature_set: str,
    schemes: Sequence[str],
    jobs: int = 1,
    max_variants: int | None = None,
    checkpoint: Checkpoint | None = None,
) -> CVResult:
    """Cross-validate one model on every assay in ``data`` under each scheme.

    One assay's features exist at a time per worker: ``design`` builds them, every scheme is
    run, and they are dropped before that worker's next assay. Assays are independent, so with
    ``jobs > 1`` they run in that many processes (:func:`~mutascope.utils.parallel.
    run_parallel`), largest first, with at most ``max_variants`` variants in flight. The result
    does not depend on ``jobs``: each assay's numbers come from the same calls in any process,
    and rows are sorted by assay.
    ``design`` and ``make_model`` must then be picklable (module-level functions, classes,
    ``functools.partial`` or dataclass instances; not closures).

    Args:
        data: One row per variant: ``assay_id``, ``DMS_score`` and ``fold_<scheme>`` for each
            scheme (:func:`mutascope.data.folds.load_cv_table`).
        design: Builds an assay's features from its keys (:data:`KEY_COLUMNS` only).
        make_model: Builds a fresh model per fold.
        model: Written to the ``model`` column.
        feature_set: Written to the ``feature_set`` column.
        schemes: CV schemes to run, e.g. ``("random", "modulo", "contiguous")``.
        jobs: Worker processes; 1 runs in this process.
        max_variants: With ``jobs > 1``, the most variants whose assays run at once (a
            memory budget; an assay larger than it runs alone). None: no limit.
        checkpoint: Where finished assays are saved and, on a rerun, loaded from.

    Returns:
        The metrics (:class:`SupervisedMetricSchema`) and, for a :class:`Tuned` model, the
        hyperparameters chosen in each fold.
    """
    if jobs < 1:
        raise CrossValidationError(f"jobs must be at least 1, got {jobs}")
    columns = [*KEY_COLUMNS, "DMS_score", *(f"fold_{s}" for s in schemes)]
    groups = data.groupby("assay_id", sort=True).indices
    assays = {str(a): data.iloc[rows][columns].reset_index(drop=True) for a, rows in groups.items()}
    outputs: dict[str, _AssayOutput] = {}
    if checkpoint is not None:
        resumed = sorted(checkpoint.open() & set(assays))
        outputs |= {a: checkpoint.load(a) for a in resumed}
        if resumed:
            log.info("cv.resumed", assays=len(resumed), of=len(assays))
    # Largest first, so the longest assays do not start last and hold up the end of the run.
    order = sorted(set(assays) - set(outputs), key=lambda a: (-len(assays[a]), a))

    def done(index: int, output: _AssayOutput) -> None:
        if checkpoint is not None:
            checkpoint.save(order[index], output)
        log.info(
            "cv.assay",
            assay_id=order[index],
            variants=len(assays[order[index]]),
            seconds=round(output[2], 2),
        )

    finished = run_parallel(
        _assay_cv,
        [(a, assays[a], design, make_model, schemes) for a in order],
        jobs=jobs,
        weights=[len(assays[a]) for a in order],
        budget=max_variants,
        on_done=done,
    )
    outputs |= dict(zip(order, finished, strict=True))
    records = [r for assay_id in sorted(outputs) for r in outputs[assay_id][0]]
    choices = [c for assay_id in sorted(outputs) for c in outputs[assay_id][1]]
    table = pd.DataFrame.from_records(
        records, columns=["assay_id", "scheme", "fold", "metric", "value"]
    )
    table["fold"] = table["fold"].astype("Int64")
    table.insert(0, "model", model)
    table.insert(1, "feature_set", feature_set)
    chosen = pd.DataFrame.from_records(
        choices, columns=["assay_id", "scheme", "fold", "name", "value"]
    ).astype({"assay_id": str, "scheme": str, "fold": "int64", "name": str, "value": "float64"})
    chosen.insert(0, "model", model)
    chosen.insert(1, "feature_set", feature_set)
    return CVResult(
        metrics=SupervisedMetricSchema.validate(table, lazy=True),
        hyperparameters=SelectedHyperparameterSchema.validate(chosen, lazy=True),
    )

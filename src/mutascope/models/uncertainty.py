"""Prediction intervals under ProteinGym's supervised CV, judged on the test folds (ADR 0024).

For one assay, scheme and outer fold (the test fold), everything is computed from the other
folds (the training rows), with ``DMS_score`` standardised as in :func:`~mutascope.models.cv.
cross_validate`:

1. **The outer model** is fit on all training rows (exactly as ``train cv``): its prediction is
   the point prediction an interval goes with, and its errors are what width is judged against.
2. **CV+** (:class:`~mutascope.models.conformal.CVPlus`) is calibrated on the training rows only,
   once per calibration mode:

   - ``random``: the training rows split into K random folds (K = number of training folds),
     seeded by ``(seed, assay, scheme, outer fold)``;
   - ``scheme``: the training rows' own outer folds, so under ``modulo`` and ``contiguous`` each
     calibration model is scored on positions it never saw, as the test fold will be.

   The calibration models get those same folds for their own inner CV (penalty, early
   stopping), so a mode is one consistent way of splitting, all the way down.
3. Intervals at the test rows, for both interval types and every level.

The test fold's labels reach none of it: they are only compared with the intervals afterwards
(a test changes them and requires bit-identical intervals).

:func:`assay_calibration` runs step 2 on all of an assay's variants with its real folds: the
residuals and spreads the final all-data models need for their intervals (Task 8).
"""

import time
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

from mutascope.eval.uncertainty import (
    MODES,
    BinRecord,
    Key,
    MetricRecord,
    SchemeIntervals,
    interval_metrics,
)
from mutascope.features.base import KEY_COLUMNS
from mutascope.features.matrix import FeatureMatrix, FloatArray, IntArray
from mutascope.models.conformal import INTERVALS, CVPlus, ModelFactory
from mutascope.models.cv import CrossValidationError, DesignBuilder
from mutascope.utils.parallel import run_parallel

log = structlog.get_logger(__name__)

ALL_DATA_FOLD = -1
"""Stands for "no outer test fold" in a seed key (:func:`assay_calibration`)."""


@dataclass(frozen=True)
class UncertaintySettings:
    """``train.uncertainty``: fixed before any result (ADR 0024)."""

    levels: tuple[float, ...]
    modes: tuple[str, ...]
    intervals: tuple[str, ...]
    epsilon: float
    """Added to every spread before dividing by it (normalised intervals), in training-SD units."""
    seed: int
    width_bins: int

    def __post_init__(self) -> None:
        unknown = sorted((set(self.modes) - set(MODES)) | (set(self.intervals) - set(INTERVALS)))
        if unknown:
            raise ValueError(f"unknown modes or intervals {unknown}")
        if not self.epsilon > 0 or not self.levels or self.width_bins < 1:
            raise ValueError("epsilon must be positive, levels non-empty, width_bins >= 1")


def seed_key(seed: int, assay_id: str, scheme: str, outer_fold: int) -> list[int]:
    """Seed of the random calibration folds: fixed per (assay, scheme, outer fold), whatever runs
    before it or in which process."""
    return [seed, zlib.crc32(assay_id.encode()), zlib.crc32(scheme.encode()), outer_fold + 1]


def calibration_groups(mode: str, groups: IntArray, key: Sequence[int]) -> IntArray:
    """Calibration fold of each training row under ``mode``.

    ``scheme``: the rows' own (outer-scheme) folds. ``random``: as many folds as those, of equal
    size (within one row), assigned by a random permutation seeded with ``key``. It depends only
    on the number of rows and folds, never on labels.
    """
    if mode == "scheme":
        return groups.astype(np.int64)
    if mode != "random":
        raise ValueError(f"unknown calibration mode {mode!r}; use one of {MODES}")
    n, k = groups.shape[0], int(np.unique(groups).size)
    out = np.empty(n, dtype=np.int64)
    out[np.random.default_rng(list(key)).permutation(n)] = np.arange(n) % k
    return out


def scheme_intervals(
    x: FeatureMatrix,
    y: FloatArray,
    folds: IntArray,
    make_model: ModelFactory,
    settings: UncertaintySettings,
    *,
    assay_id: str,
    scheme: str,
) -> SchemeIntervals:
    """Point predictions and CV+ intervals for every variant, each from its own outer fold.

    Raises:
        CrossValidationError: Fewer than 2 folds or constant training targets.
    """
    n = y.shape[0]
    fold_ids = np.unique(folds)
    if fold_ids.size < 2:
        raise CrossValidationError(f"need at least 2 folds, got {fold_ids.tolist()}")
    target, prediction, train_sd = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    spread = {mode: np.full(n, np.nan) for mode in settings.modes}
    bounds: dict[Key, tuple[FloatArray, FloatArray]] = {
        (mode, interval, level): (np.full(n, np.nan), np.full(n, np.nan))
        for mode in settings.modes
        for interval in settings.intervals
        for level in settings.levels
    }
    for fold in fold_ids:
        test, train = folds == fold, folds != fold
        mean, std = float(np.mean(y[train])), float(np.std(y[train]))
        if not std > 0:
            raise CrossValidationError(f"fold {fold}: training targets are constant")
        y_train = (y[train] - mean) / std
        x_train, x_test, groups = x.take(train), x.take(test), folds[train].astype(np.int64)
        outer = make_model()
        outer.fit(x_train, y_train, groups)
        prediction[test] = np.asarray(outer.predict(x_test), dtype=np.float64)
        target[test] = (y[test] - mean) / std
        train_sd[test] = std
        for mode in settings.modes:
            key = seed_key(settings.seed, assay_id, scheme, int(fold))
            cvplus = CVPlus.fit(x_train, y_train, calibration_groups(mode, groups, key), make_model)
            at = cvplus.predict(x_test)
            spread[mode][test] = at.ensemble_spread()
            for interval in settings.intervals:
                found = cvplus.intervals(at, interval, settings.levels, settings.epsilon)
                for level, (lower, upper) in found.items():
                    bounds[(mode, interval, level)][0][test] = lower
                    bounds[(mode, interval, level)][1][test] = upper
    return SchemeIntervals(
        fold=folds.astype(np.int64),
        target=target,
        prediction=prediction,
        train_sd=train_sd,
        spread=spread,
        bounds=bounds,
    )


_AssayOutput = tuple[list[MetricRecord], list[BinRecord], float]


def assay_uncertainty(
    assay_id: str,
    assay: pd.DataFrame,
    design: DesignBuilder,
    make_model: ModelFactory,
    schemes: Sequence[str],
    settings: UncertaintySettings,
) -> _AssayOutput:
    """Every scheme of one assay: the unit of work :func:`uncertainty_table` runs in parallel."""
    started = time.monotonic()
    x = design(assay_id, assay[list(KEY_COLUMNS)].reset_index(drop=True))
    if x.n_rows != len(assay):
        raise CrossValidationError(f"{assay_id}: {x.n_rows} feature rows for {len(assay)}")
    y = assay["DMS_score"].to_numpy(np.float64)
    assay_sd = float(np.std(y))
    metrics: list[MetricRecord] = []
    bins: list[BinRecord] = []
    for scheme in schemes:
        folds = assay[f"fold_{scheme}"].to_numpy(np.int64)
        try:
            result = scheme_intervals(
                x, y, folds, make_model, settings, assay_id=assay_id, scheme=scheme
            )
        except ValueError as exc:
            raise CrossValidationError(f"{assay_id}, {scheme}: {exc}") from exc
        found, binned = interval_metrics(assay_id, scheme, result, assay_sd, settings.width_bins)
        metrics += found
        bins += binned
    return metrics, bins, time.monotonic() - started


METRIC_COLUMNS = ["assay_id", "scheme", "mode", "interval", "level", "metric", "value"]
BIN_COLUMNS = [
    "assay_id",
    "scheme",
    "mode",
    "interval",
    "level",
    "bin",
    "n",
    "width_sd",
    "abs_error_sd",
    "coverage",
]


@dataclass(frozen=True)
class FrameCheckpoint:
    """Finished assays of a long run, one Parquet file per assay and kind (ADR 0022's idea).

    ``fingerprint`` identifies the settings; a directory written under others is refused.
    Code changes are not fingerprinted: delete the directory after changing a model.
    """

    directory: Path
    fingerprint: str
    kinds: tuple[str, ...]

    def open(self) -> set[str]:
        """Create or check the directory; return the assays already finished."""
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = self.directory / "fingerprint.txt"
        if stamp.exists() and stamp.read_text().strip() != self.fingerprint:
            raise CrossValidationError(
                f"{self.directory} holds a run with other settings; delete it to start over"
            )
        stamp.write_text(self.fingerprint + "\n")
        last = self.kinds[-1]
        return {
            p.name.removesuffix(f".{last}.parquet")
            for p in self.directory.glob(f"*.{last}.parquet")
        }

    def save(self, assay_id: str, frames: Sequence[pd.DataFrame]) -> None:
        # The last kind is written last: an assay counts as finished once it exists.
        for kind, frame in zip(self.kinds, frames, strict=True):
            target = self.directory / f"{assay_id}.{kind}.parquet"
            partial = target.with_suffix(".tmp")
            frame.to_parquet(partial, index=False)
            partial.replace(target)

    def load(self, assay_id: str) -> list[pd.DataFrame]:
        return [pd.read_parquet(self.directory / f"{assay_id}.{k}.parquet") for k in self.kinds]


def _frames(output: _AssayOutput) -> list[pd.DataFrame]:
    metrics = pd.DataFrame.from_records(output[0], columns=METRIC_COLUMNS)
    bins = pd.DataFrame.from_records(output[1], columns=BIN_COLUMNS)
    return [metrics, bins]


def uncertainty_table(
    data: pd.DataFrame,
    design: DesignBuilder,
    make_model: ModelFactory,
    *,
    schemes: Sequence[str],
    settings: UncertaintySettings,
    jobs: int = 1,
    max_variants: int | None = None,
    checkpoint: FrameCheckpoint | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-assay interval metrics and width bins of every assay in ``data``.

    Assays run in ``jobs`` processes, largest first, under a ``max_variants`` memory budget, as
    in :func:`~mutascope.models.cv.cv_metrics_table`; the result does not depend on ``jobs``.

    Returns:
        ``(metrics, bins)`` with :data:`METRIC_COLUMNS` and :data:`BIN_COLUMNS`, sorted by assay.
    """
    columns = [*KEY_COLUMNS, "DMS_score", *(f"fold_{s}" for s in schemes)]
    groups = data.groupby("assay_id", sort=True).indices
    assays = {str(a): data.iloc[rows][columns].reset_index(drop=True) for a, rows in groups.items()}
    outputs: dict[str, list[pd.DataFrame]] = {}
    if checkpoint is not None:
        resumed = sorted(checkpoint.open() & set(assays))
        outputs |= {a: checkpoint.load(a) for a in resumed}
        if resumed:
            log.info("uncertainty.resumed", assays=len(resumed), of=len(assays))
    order = sorted(set(assays) - set(outputs), key=lambda a: (-len(assays[a]), a))

    def done(index: int, output: _AssayOutput) -> None:
        if checkpoint is not None:
            checkpoint.save(order[index], _frames(output))
        log.info(
            "uncertainty.assay",
            assay_id=order[index],
            variants=len(assays[order[index]]),
            seconds=round(output[2], 2),
        )

    finished = run_parallel(
        assay_uncertainty,
        [(a, assays[a], design, make_model, schemes, settings) for a in order],
        jobs=jobs,
        weights=[len(assays[a]) for a in order],
        budget=max_variants,
        on_done=done,
    )
    outputs |= {a: _frames(o) for a, o in zip(order, finished, strict=True)}
    metrics = pd.concat([outputs[a][0] for a in sorted(outputs)], ignore_index=True)
    bins = pd.concat([outputs[a][1] for a in sorted(outputs)], ignore_index=True)
    return metrics, bins


CALIBRATION_COLUMNS = [
    "assay_id",
    "position",
    "wt_aa",
    "mt_aa",
    "scheme",
    "mode",
    "fold",
    "target",
    "prediction",
    "spread",
    "target_mean",
    "target_sd",
]


def assay_calibration(
    assay_id: str,
    assay: pd.DataFrame,
    design: DesignBuilder,
    make_model: ModelFactory,
    schemes: Sequence[str],
    settings: UncertaintySettings,
) -> pd.DataFrame:
    """CV+ calibration of the final, all-data model of one assay (Task 8; ADR 0024).

    For each scheme's folds and each mode, :class:`CVPlus` is fit on every variant (``DMS_score``
    standardised with all variants' mean and SD, as the final model will be). Each variant's row
    holds the fold that scored it, that model's prediction and fold-ensemble spread, and its
    standardised target: with ``epsilon`` these give both interval types' scores. Refitting
    :class:`CVPlus` on the same inputs reproduces the fold models (fits are deterministic), which
    is how intervals for new variants are produced from these rows.
    """
    x = design(assay_id, assay[list(KEY_COLUMNS)].reset_index(drop=True))
    y: FloatArray = assay["DMS_score"].to_numpy(np.float64)
    mean, std = float(np.mean(y)), float(np.std(y))
    if not std > 0:
        raise CrossValidationError(f"{assay_id}: constant targets")
    z = (y - mean) / std
    frames: list[pd.DataFrame] = []
    for scheme in schemes:
        folds = assay[f"fold_{scheme}"].to_numpy(np.int64)
        for mode in settings.modes:
            key = seed_key(settings.seed, assay_id, scheme, ALL_DATA_FOLD)
            cal = CVPlus.fit(x, z, calibration_groups(mode, folds, key), make_model)
            labels = np.asarray(cal.groups, dtype=np.int64)
            frames.append(
                pd.DataFrame(
                    {
                        "assay_id": assay_id,
                        "position": assay["position"].to_numpy(np.int64),
                        "wt_aa": assay["wt_aa"].astype(str).to_numpy(),
                        "mt_aa": assay["mt_aa"].astype(str).to_numpy(),
                        "scheme": scheme,
                        "mode": mode,
                        "fold": labels[cal.calibration.fold],
                        "target": cal.calibration.target,
                        "prediction": cal.calibration.prediction,
                        "spread": cal.calibration.spread,
                        "target_mean": mean,
                        "target_sd": std,
                    }
                )
            )
    return pd.concat(frames, ignore_index=True)[CALIBRATION_COLUMNS]

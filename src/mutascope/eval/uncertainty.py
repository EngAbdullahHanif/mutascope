"""Judging prediction intervals on test folds: coverage, width, and whether width tracks error.

One assay under one scheme gives a :class:`SchemeIntervals`: every variant's out-of-fold point
prediction (the outer model, as deployed), its standardised target, and for each calibration
mode, interval type and level its CV+ bounds (:mod:`mutascope.models.uncertainty`). From it:

- **coverage**: the share of variants whose target lies inside its interval (pooled over the
  assay's test folds, like the pooled Spearman of ADR 0019);
- **width_sd**: mean interval width in units of the assay's ``DMS_score`` standard deviation
  (all its variants), so assays on different scales compare;
- **width_error_spearman**: Spearman between each variant's width and its absolute error (the
  point prediction's), asked of adaptive intervals: do they widen where the model is wrong?
- **spread_error_spearman**: the same for the fold-ensemble spread alone, as a difficulty score;
- **prediction_spearman**: the outer model's pooled Spearman, which must equal ``train cv``'s.

Summaries (:func:`summarise_intervals`) are over assays, each assay counted once. Coverage is a
property of the interval procedure, not a model score; its plain mean over assays is what
"averaged per assay" means here, and the ProteinGym aggregate is reported next to it.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

from mutascope.eval.aggregate import AssayGroups, aggregate
from mutascope.eval.metrics import spearman
from mutascope.features.matrix import FloatArray, IntArray

MODES: Final = ("random", "scheme")
"""Calibration modes: (a) random calibration folds; (b) the training rows' own outer-scheme folds
(held-out positions under ``modulo`` and ``contiguous``)."""

UNCERTAINTY_METRICS: Final = (
    "coverage",
    "width_sd",
    "width_error_spearman",
    "spread_error_spearman",
    "prediction_spearman",
    "n_variants",
)

Key = tuple[str, str, float]
"""``(mode, interval, level)``."""

MetricRecord = tuple[str, str, str | None, str | None, float, str, float]
"""``assay_id, scheme, mode, interval, level (NaN if none), metric, value``."""
BinRecord = tuple[str, str, str, str, float, int, int, float, float, float]
"""``assay_id, scheme, mode, interval, level, bin, n, width_sd, abs_error_sd, coverage``."""


@dataclass(frozen=True)
class SchemeIntervals:
    """One assay under one scheme: every variant's point prediction and intervals."""

    fold: IntArray
    target: FloatArray
    """``DMS_score`` standardised with its outer training folds' mean and SD."""
    prediction: FloatArray
    """The outer model's prediction (trained on all the training folds), same units."""
    train_sd: FloatArray
    """The ``DMS_score`` SD of each variant's outer training folds: back to raw units."""
    spread: Mapping[str, FloatArray]
    """Mode -> spread of the K calibration models at each variant (difficulty score)."""
    bounds: Mapping[Key, tuple[FloatArray, FloatArray]]


def interval_metrics(
    assay_id: str, scheme: str, result: SchemeIntervals, assay_sd: float, n_bins: int
) -> tuple[list[MetricRecord], list[BinRecord]]:
    """Per-assay metrics and width-decile bins of one scheme's intervals.

    Bins: variants ranked by width within the assay, cut into ``n_bins`` equal-count bins; per
    bin the mean width and absolute error (assay-SD units) and the coverage.
    """
    error = np.abs(result.prediction - result.target)
    to_sd = result.train_sd / assay_sd
    n = result.target.shape[0]
    records: list[MetricRecord] = [
        (assay_id, scheme, None, None, np.nan, "n_variants", float(n)),
        (
            assay_id,
            scheme,
            None,
            None,
            np.nan,
            "prediction_spearman",
            spearman(result.target, result.prediction),
        ),
    ]
    for mode, values in result.spread.items():
        records.append(
            (assay_id, scheme, mode, None, np.nan, "spread_error_spearman", spearman(values, error))
        )
    bins: list[BinRecord] = []
    for (mode, interval, level), (lower, upper) in result.bounds.items():
        width = upper - lower
        inside = (result.target >= lower) & (result.target <= upper)
        width_sd = width * to_sd
        records += [
            (assay_id, scheme, mode, interval, level, "coverage", float(inside.mean())),
            (assay_id, scheme, mode, interval, level, "width_sd", float(width_sd.mean())),
            (
                assay_id,
                scheme,
                mode,
                interval,
                level,
                "width_error_spearman",
                spearman(width, error),
            ),
        ]
        # Stable sort: ties (near-constant widths) keep variant order, so bins are equal-count.
        rank = np.empty(n, dtype=np.int64)
        rank[np.argsort(width, kind="stable")] = np.arange(n)
        which = rank * n_bins // n
        for b in range(n_bins):
            members = which == b
            if not members.any():
                continue
            bins.append(
                (
                    assay_id,
                    scheme,
                    mode,
                    interval,
                    level,
                    b,
                    int(members.sum()),
                    float(width_sd[members].mean()),
                    float((error * to_sd)[members].mean()),
                    float(inside[members].mean()),
                )
            )
    return records, bins


def size_bin(n_variants: int, edges: Sequence[int]) -> str:
    """Label of the size bin ``n_variants`` falls in, e.g. ``300-999`` or ``>=5000``."""
    bounds = [0, *edges]
    for low, high in zip(bounds, [*edges, None], strict=True):
        if high is None or n_variants < high:
            return f">={low}" if high is None else f"{low}-{high - 1}"
    raise AssertionError("unreachable")


def _stats(values: FloatArray) -> dict[str, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {"mean": np.nan, "median": np.nan, "n_assays": 0.0}
    return {
        "mean": float(finite.mean()),
        "median": float(np.median(finite)),
        "q10": float(np.quantile(finite, 0.10)),
        "q25": float(np.quantile(finite, 0.25)),
        "q75": float(np.quantile(finite, 0.75)),
        "q90": float(np.quantile(finite, 0.90)),
        "min": float(finite.min()),
        "max": float(finite.max()),
        "share_positive": float((finite > 0).mean()),
        "n_assays": float(finite.size),
    }


def summarise_intervals(
    metrics: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    size_edges: Sequence[int],
    below_points: float,
) -> pd.DataFrame:
    """Statistics over assays of each per-assay metric, overall and by assay size.

    Args:
        metrics: :class:`~mutascope.eval.schemas.UncertaintyMetricSchema` rows of one run.
        reference: ProteinGym reference table (for the aggregate and nothing else).
        size_edges: Assay-size bin edges in variants (``n_variants`` of the scheme).
        below_points: Coverage this far below the level counts as an assay "below target".

    Returns:
        ``scheme, mode, interval, level, size_bin, metric, statistic, value`` rows. Coverage
        also gets ``aggregate`` (ProteinGym: UniProt -> function type -> overall) and
        ``n_below`` (assays more than ``below_points`` under the level).
    """
    sizes = metrics[metrics["metric"] == "n_variants"].set_index(["assay_id", "scheme"])["value"]
    table = metrics[metrics["metric"] != "n_variants"].copy()
    table["size_bin"] = [
        size_bin(int(sizes[(a, s)]), size_edges)
        for a, s in zip(table["assay_id"], table["scheme"], strict=True)
    ]
    rows: list[dict[str, object]] = []
    table["mode"] = table["mode"].fillna("")
    table["interval"] = table["interval"].fillna("")
    for (scheme, mode, interval, level, metric), group in table.groupby(
        ["scheme", "mode", "interval", "level", "metric"], dropna=False, sort=True
    ):
        level_value = float(str(level))
        for label, part in [("all", group), *group.groupby("size_bin", sort=True)]:
            values = part["value"].to_numpy(np.float64)
            stats = _stats(values)
            if metric == "coverage":
                stats["n_below"] = float((values < level_value - below_points).sum())
            if metric in ("coverage", "prediction_spearman") and label == "all":
                order = part.sort_values("assay_id")
                stats["aggregate"] = aggregate(
                    order["value"].to_numpy(np.float64),
                    AssayGroups.from_reference(reference, order["assay_id"].astype(str).tolist()),
                ).overall
            base: dict[str, object] = {
                "scheme": str(scheme),
                "mode": str(mode) or None,
                "interval": str(interval) or None,
                "level": level_value,
                "size_bin": str(label),
                "metric": str(metric),
            }
            rows += [base | {"statistic": name, "value": value} for name, value in stats.items()]
    return pd.DataFrame.from_records(rows)


def bin_summary(bins: pd.DataFrame) -> pd.DataFrame:
    """Width-decile bins averaged over assays (each assay once): the width-vs-error figure."""
    keys = ["scheme", "mode", "interval", "level", "bin"]
    summary = (
        bins.groupby(keys, sort=True)
        .agg(
            width_sd=("width_sd", "mean"),
            abs_error_sd=("abs_error_sd", "mean"),
            coverage=("coverage", "mean"),
            n_assays=("assay_id", "nunique"),
        )
        .reset_index()
    )
    summary["n_assays"] = summary["n_assays"].astype("int64")
    return summary


def uncertainty_paths(results_dir: Path, label: str) -> dict[str, Path]:
    """``train uncertainty``'s files for a run label: per-assay metrics, summary, width bins."""
    stem = f"uncertainty_{label}"
    return {
        "metrics": results_dir / f"{stem}.parquet",
        "summary": results_dir / f"{stem}_summary.parquet",
        "bins": results_dir / f"{stem}_bins.parquet",
    }

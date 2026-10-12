"""Score models per assay and summarise them ProteinGym-style, in the long results format.

:func:`assay_metrics_table` turns a table of variants (labels plus one score column per model)
into per-assay metrics; :func:`summarise` turns those into aggregates with bootstrap CIs. Both
return validated frames; :func:`write_results` writes them to Parquet. File I/O stays in the
CLI and :mod:`mutascope.eval.proteingym`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from mutascope.data.db import literal
from mutascope.eval.aggregate import AssayGroups, aggregate, bootstrap_ci
from mutascope.eval.metrics import METRICS, assay_metrics
from mutascope.eval.schemas import AssayMetricSchema, SummarySchema
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

LABEL_COLUMNS = ("assay_id", "DMS_score", "DMS_score_bin")


@dataclass(frozen=True)
class BootstrapParams:
    """How :func:`summarise` computes CIs (``eval.bootstrap`` in the config)."""

    n_resamples: int
    confidence: float


def assay_metrics_table(
    variants: pd.DataFrame, models: Mapping[str, str], top_percent: float
) -> pd.DataFrame:
    """Every metric of every model on every assay, in :class:`AssayMetricSchema` long format.

    Undefined metrics are NaN and logged with a count per model and metric (see
    :func:`log_undefined`).

    Args:
        variants: Columns ``assay_id, DMS_score, DMS_score_bin`` plus one column per model.
        models: Score column -> model name written to the ``model`` column.
        top_percent: Top fraction (in %) for NDCG and top recall.
    """
    missing = sorted(set(LABEL_COLUMNS) - set(variants.columns) | set(models) - set(variants))
    if missing:
        raise KeyError(f"variants table lacks columns {missing}")
    records: list[tuple[str, str, str]] = []
    values: list[float] = []
    for assay_id, assay in variants.groupby("assay_id", sort=True):
        y_true = assay["DMS_score"].to_numpy(np.float64)
        y_bin = assay["DMS_score_bin"].to_numpy(np.int64)
        for column, model in models.items():
            scores = assay_metrics(y_true, y_bin, assay[column].to_numpy(np.float64), top_percent)
            for metric in METRICS:
                records.append((model, str(assay_id), metric))
                values.append(scores[metric])
    table = pd.DataFrame.from_records(records, columns=["model", "assay_id", "metric"])
    table.insert(2, "split", pd.Series(pd.NA, index=table.index, dtype="string"))
    table.insert(3, "fold", pd.Series(pd.NA, index=table.index, dtype="Int64"))
    table["value"] = values
    table = AssayMetricSchema.validate(table, lazy=True)
    log_undefined(table)
    return table


def log_undefined(per_assay: pd.DataFrame) -> dict[tuple[str, str], list[str]]:
    """Log, per model and metric, how many assays have an undefined (NaN) value, and which.

    Returns:
        ``(model, metric) -> assay ids`` for every pair with at least one NaN.
    """
    undefined: dict[tuple[str, str], list[str]] = {}
    nan_rows = per_assay[per_assay["value"].isna()]
    for (model, metric), rows in nan_rows.groupby(["model", "metric"], sort=True):
        assays = sorted(rows["assay_id"].astype(str))
        undefined[(str(model), str(metric))] = assays
        log.warning(
            "undefined metric", model=model, metric=metric, count=len(assays), assays=assays
        )
    log.info("undefined metric values", count=len(nan_rows), of=len(per_assay))
    return undefined


def summarise(
    per_assay: pd.DataFrame,
    reference: pd.DataFrame,
    bootstrap: BootstrapParams,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """ProteinGym aggregates with bootstrap CIs, in :class:`SummarySchema` long format.

    Args:
        per_assay: :class:`AssayMetricSchema` rows with one value per (model, split, assay,
            metric). Supervised fold-level rows must be averaged over folds first.
        reference: ProteinGym reference table (``DMS_id, UniProt_ID, coarse_selection_type``).
        bootstrap: Resampling settings.
        rng: Seeded generator (:func:`mutascope.utils.seeding.seed_all`).
    """
    keys = ["model", "split", "assay_id", "metric"]
    if per_assay.duplicated(keys).any():
        raise ValueError("more than one value per (model, split, assay, metric): average folds")
    records: list[dict[str, Any]] = []
    for (model, split, metric), rows in per_assay.groupby(
        ["model", "split", "metric"], sort=True, dropna=False
    ):
        rows = rows.sort_values("assay_id")
        groups = AssayGroups.from_reference(reference, rows["assay_id"].astype(str).tolist())
        values = rows["value"].to_numpy(np.float64)
        point = aggregate(values, groups)
        ci = bootstrap_ci(
            values,
            groups,
            n_resamples=bootstrap.n_resamples,
            confidence=bootstrap.confidence,
            rng=rng,
        )
        defined = ~np.isnan(values)
        function_of_assay = np.array(groups.functions)[
            groups.function_of_group[groups.group_of_assay]
        ]
        base: dict[str, Any] = {"model": model, "split": split, "metric": metric}
        records.append(
            base
            | {"level": "overall", "group": None, "value": point.overall}
            | {"ci_low": ci.overall[0], "ci_high": ci.overall[1], "n_assays": int(defined.sum())}
        )
        for function in groups.functions:
            low, high = ci.by_function[function]
            n = int((defined & (function_of_assay == function)).sum())
            records.append(
                base
                | {"level": "function", "group": function, "value": point.by_function[function]}
                | {"ci_low": low, "ci_high": high, "n_assays": n}
            )
    summary = pd.DataFrame.from_records(records)
    summary = summary.astype({"split": "string", "group": "string"})
    return SummarySchema.validate(summary, lazy=True)


def write_results(
    per_assay: pd.DataFrame, summary: pd.DataFrame, out_dir: Path, name: str
) -> tuple[Path, Path]:
    """Validate and write ``<name>.parquet`` (per assay) and ``<name>_summary.parquet``.

    Returns:
        The two paths.
    """
    per_assay = AssayMetricSchema.validate(per_assay, lazy=True)
    summary = SummarySchema.validate(summary, lazy=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = out_dir / f"{name}.parquet", out_dir / f"{name}_summary.parquet"
    with duckdb.connect() as con:
        for frame, path in zip((per_assay, summary), paths, strict=True):
            con.from_df(frame).write_parquet(str(path))
    return paths


def read_results(path: Path) -> pd.DataFrame:
    """Read a results Parquet file back with the dtypes the schemas expect."""
    with duckdb.connect() as con:
        frame = con.execute(f"SELECT * FROM read_parquet({literal(str(path.resolve()))})").df()
    nullable = {c: "string" for c in ("split", "group") if c in frame}
    if "fold" in frame:
        nullable["fold"] = "Int64"
    return frame.astype(nullable)

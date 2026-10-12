"""Compare two score sets with a paired bootstrap over assays (ADR 0014).

Any statement that one model is better than another must come from here, not from two
separately computed CIs: those overlap for strongly correlated models even when the difference
is clear. The resampling and the aggregation are :func:`mutascope.eval.aggregate.paired_bootstrap`;
this module applies it to results in the long format and returns the comparison as a table.
"""

from typing import Any

import numpy as np
import pandas as pd

from mutascope.eval.aggregate import AssayGroups, Difference, paired_bootstrap
from mutascope.eval.benchmark import BootstrapParams
from mutascope.eval.schemas import ComparisonSchema

_KEYS = ["split", "assay_id", "metric"]


def paired_bootstrap_diff(
    results_a: pd.DataFrame,
    results_b: pd.DataFrame,
    reference: pd.DataFrame,
    bootstrap: BootstrapParams,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Paired bootstrap of ``A - B`` for every metric, overall and per function type.

    The same resampled assays are used for both score sets, and each resample recomputes the
    full ProteinGym aggregate (per UniProt ID, per function type, overall) of both. ``diff`` is
    the difference of the two aggregates. The aggregate is a weighted mean of assays, so this
    is also the aggregate of the per-assay differences: the mean difference.

    Args:
        results_a: Per-assay results of one model (:class:`AssayMetricSchema`, one value per
            split, assay and metric; average supervised folds first).
        results_b: The same for the other model, on exactly the same splits, assays and metrics.
        reference: ProteinGym reference table (``DMS_id, UniProt_ID, coarse_selection_type``).
        bootstrap: Resampling settings (``eval.bootstrap``).
        rng: Seeded generator.

    Returns:
        :class:`ComparisonSchema` rows.

    Raises:
        ValueError: A frame holds more than one model or repeated keys, both hold the same
            model, or the two do not cover the same splits, assays and metrics.
    """
    name_a, a = _one_model(results_a, "results_a")
    name_b, b = _one_model(results_b, "results_b")
    if name_a == name_b:
        raise ValueError(f"both result sets are {name_a!r}; nothing to compare")
    joined = a.merge(b, on=_KEYS, how="outer", suffixes=("_a", "_b"), indicator=True)
    if (joined["_merge"] != "both").any() or len(joined) != len(a) or len(joined) != len(b):
        sides = joined["_merge"].astype(str).value_counts().to_dict()
        raise ValueError(
            f"{name_a} and {name_b} do not cover the same (split, assay, metric) rows: {sides}"
        )
    records: list[dict[str, Any]] = []
    for (split, metric), rows in joined.groupby(["split", "metric"], sort=True, dropna=False):
        rows = rows.sort_values("assay_id")
        groups = AssayGroups.from_reference(reference, rows["assay_id"].astype(str).tolist())
        result = paired_bootstrap(
            rows["value_a"].to_numpy(np.float64),
            rows["value_b"].to_numpy(np.float64),
            groups,
            n_resamples=bootstrap.n_resamples,
            confidence=bootstrap.confidence,
            rng=rng,
        )
        base: dict[str, Any] = {
            "model_a": name_a,
            "model_b": name_b,
            "split": split,
            "metric": metric,
        }
        records.append(base | _row("overall", None, result.overall, result.n_assays))
        for function, difference in result.by_function.items():
            records.append(
                base | _row("function", function, difference, result.n_by_function[function])
            )
    table = pd.DataFrame.from_records(records).astype({"split": "string", "group": "string"})
    return ComparisonSchema.validate(table, lazy=True)


def _one_model(results: pd.DataFrame, what: str) -> tuple[str, pd.DataFrame]:
    """The single model name in ``results`` and its ``split, assay_id, metric, value`` rows."""
    models = sorted(set(results["model"]))
    if len(models) != 1:
        raise ValueError(f"{what} must hold exactly one model, found {models}")
    if results.duplicated(_KEYS).any():
        raise ValueError(f"{what}: more than one value per (split, assay, metric): average folds")
    return models[0], results[[*_KEYS, "value"]]


def _row(level: str, group: str | None, difference: Difference, n_assays: int) -> dict[str, Any]:
    return {
        "level": level,
        "group": group,
        "value_a": difference.value_a,
        "value_b": difference.value_b,
        "diff": difference.diff,
        "ci_low": difference.ci[0],
        "ci_high": difference.ci[1],
        "share_a_better": difference.share_a_better,
        "n_assays": n_assays,
    }

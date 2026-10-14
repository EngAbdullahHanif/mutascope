"""Supervised results as benchmark tables: summaries, paired comparisons, the leaderboard check.

A supervised run is named by :func:`supervised_label` (``<model>_<feature_set>``), the name of its
results file (``cv_<label>.parquet``) and the ``model`` column once its pooled values enter the
benchmark tables. Only the pooled values (``fold`` null; ADR 0019) are aggregated or compared;
per-fold rows are diagnostics.

The zero-shot comparator is the ``zero_shot`` model run through the same loop
(:class:`mutascope.models.baselines.ZeroShot`), so it is scored on exactly the same test folds
and pooled the same way as the model it is paired with.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from mutascope.eval.aggregate import AssayGroups, aggregate
from mutascope.eval.metrics import METRICS, spearman
from mutascope.eval.schemas import AssayMetricSchema, SupervisedLeaderboardSchema


def supervised_label(model: str, feature_set: str) -> str:
    """Name of a supervised run; the only place it is built."""
    return f"{model}_{feature_set}"


def results_path(results_dir: Path, label: str) -> Path:
    """``cv_<label>.parquet`` (``mutascope train cv``)."""
    return results_dir / f"cv_{label}.parquet"


def hyperparameters_path(results_dir: Path, label: str) -> Path:
    """``cv_<label>_hyperparameters.parquet``: what the inner CV chose per outer fold."""
    return results_dir / f"cv_{label}_hyperparameters.parquet"


def pooled_assay_metrics(cv: pd.DataFrame) -> pd.DataFrame:
    """One run's pooled values as :class:`AssayMetricSchema` rows (``split`` = scheme).

    Only metrics the benchmark tables know (Spearman) are kept; supervised MSE is in
    training-standard-deviation units and is reported from the CV file itself.

    Raises:
        ValueError: ``cv`` holds more than one run.
    """
    runs = sorted(
        {supervised_label(m, f) for m, f in zip(cv["model"], cv["feature_set"], strict=True)}
    )
    if len(runs) != 1:
        raise ValueError(f"expected one supervised run, found {runs}")
    pooled = cv[cv["fold"].isna() & cv["metric"].isin(METRICS)]
    table = pd.DataFrame(
        {
            "model": runs[0],
            "assay_id": pooled["assay_id"].astype(str),
            "split": pooled["scheme"].astype("string"),
            "fold": pd.Series(pd.NA, index=pooled.index, dtype="Int64"),
            "metric": pooled["metric"].astype(str),
            "value": pooled["value"].astype(np.float64),
        }
    ).reset_index(drop=True)
    return AssayMetricSchema.validate(table, lazy=True)


def published_names(baselines: Mapping[str, Sequence[str]]) -> list[str]:
    """Every published model named in ``train.leaderboard.baselines``, in order of first use."""
    return list(dict.fromkeys(name for names in baselines.values() for name in names))


def leaderboard_table(
    pooled: Mapping[str, pd.DataFrame],
    baselines: Mapping[str, Sequence[str]],
    summary: pd.DataFrame,
    assay_level: Mapping[str, pd.DataFrame],
    reference: pd.DataFrame,
) -> pd.DataFrame:
    """Our aggregate Spearman per scheme next to published baselines of the same type.

    Args:
        pooled: Run label -> :func:`pooled_assay_metrics` of that run.
        baselines: Run label -> published model names to set it against.
        summary: ProteinGym's supervised summary (``Model_name``,
            ``Average_Spearman_fold_<scheme>_5``).
        assay_level: Scheme -> ProteinGym's per-assay table for it (``DMS_id`` plus one column
            per model).
        reference: ProteinGym reference table, for our aggregate.

    Returns:
        :class:`SupervisedLeaderboardSchema` rows: ``ours`` and ``published`` aggregates,
        their difference, and how our per-assay values rank against theirs (Spearman over
        the assays both have).

    Raises:
        KeyError: A run, published model or scheme is missing.
    """
    published = summary.set_index("Model_name")
    records: list[dict[str, object]] = []
    for label, names in baselines.items():
        if label not in pooled:
            raise KeyError(f"no results for {label}")
        rows = pooled[label]
        rows = rows[rows["metric"] == "spearman"]
        for scheme in sorted(set(rows["split"].astype(str))):
            ours = rows[rows["split"] == scheme].sort_values("assay_id")
            assays = ours["assay_id"].astype(str).tolist()
            values = ours["value"].to_numpy(np.float64)
            our_value = aggregate(values, AssayGroups.from_reference(reference, assays)).overall
            per_assay = assay_level[scheme].set_index("DMS_id")
            for name in names:
                if name not in published.index or name not in per_assay.columns:
                    raise KeyError(f"published model {name!r} not in the leaderboard files")
                theirs = per_assay[name].reindex(assays).to_numpy(np.float64)
                both = ~np.isnan(theirs) & ~np.isnan(values)
                column = published[f"Average_Spearman_fold_{scheme}_5"]
                published_value = float(column.to_numpy(np.float64)[published.index == name][0])
                records.append(
                    {
                        "run": label,
                        "published_model": name,
                        "scheme": scheme,
                        "ours": our_value,
                        "published": published_value,
                        "diff": our_value - published_value,
                        "n_assays": len(assays),
                        "n_shared": int(both.sum()),
                        "assay_spearman": spearman(values[both], theirs[both]),
                    }
                )
    return SupervisedLeaderboardSchema.validate(pd.DataFrame.from_records(records), lazy=True)

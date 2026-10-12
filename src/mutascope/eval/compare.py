"""Compare two score sets with a paired bootstrap over assays (ADR 0014).

Any statement that one model is better than another must come from here, not from two
separately computed CIs: those overlap for strongly correlated models even when the difference
is clear. The resampling and the aggregation are :func:`mutascope.eval.aggregate.paired_bootstrap`;
this module applies it to results in the long format and returns the comparison as a table.

A structure-aware model has no scores for the assays without a structure, so anything that puts
it next to a sequence-only model must use the assays both have. :func:`shared_assays` is that
rule, and :func:`summarise_shared` gives each score set's aggregate and CI on those assays, for
a figure that shows several score sets side by side.
"""

from collections.abc import Collection, Mapping
from typing import Any

import numpy as np
import pandas as pd

from mutascope.eval.aggregate import AssayGroups, Difference, paired_bootstrap
from mutascope.eval.benchmark import BootstrapParams, summarise
from mutascope.eval.schemas import ComparisonSchema, SummarySchema

_KEYS = ["split", "assay_id", "metric"]


def shared_assays(
    assays_by_set: Mapping[str, Collection[str]], may_be_absent: Collection[str]
) -> list[str]:
    """The assays every score set has, sorted.

    Args:
        assays_by_set: Score-set label -> the assays it has.
        may_be_absent: Assays a score set is allowed to lack (``data.structure_exceptions``: a
            structure-aware model has no scores for them). They are left out for every set.

    Raises:
        ValueError: A score set lacks an assay that is not in ``may_be_absent``. Nothing else
            is ever dropped to make the sets match.
    """
    if not assays_by_set:
        raise ValueError("no score sets given")
    present = [set(assays) for assays in assays_by_set.values()]
    everything, allowed = set[str]().union(*present), set(may_be_absent)
    for label, assays in assays_by_set.items():
        unexplained = sorted(everything - set(assays) - allowed)
        if unexplained:
            raise ValueError(f"{label} has no values for {unexplained}")
    return sorted(set[str].intersection(*present))


def summarise_shared(
    results: Mapping[str, pd.DataFrame],
    reference: pd.DataFrame,
    bootstrap: BootstrapParams,
    seed: int,
    may_be_absent: Collection[str] = (),
) -> pd.DataFrame:
    """Each score set's ProteinGym aggregate and bootstrap CI on the assays all of them have.

    Every score set is resampled from the same seed. A score set that already covers exactly
    the shared assays therefore gets the numbers of its own summary file.

    The CIs are still one per score set: they say how well each does on assays like these and
    do not test a difference between two of them (:func:`paired_bootstrap_diff` does).

    Args:
        results: Score-set label -> its per-assay results (:class:`AssayMetricSchema`).
        reference: ProteinGym reference table (``DMS_id, UniProt_ID, coarse_selection_type``).
        bootstrap: Resampling settings (``eval.bootstrap``).
        seed: Seed of every score set's resampling.
        may_be_absent: See :func:`shared_assays`.

    Returns:
        :class:`SummarySchema` rows for every score set, in the order given.
    """
    assays = shared_assays({k: set(v["assay_id"]) for k, v in results.items()}, may_be_absent)
    frames: list[pd.DataFrame] = []
    for label, frame in results.items():
        own = _one_set(frame, label)
        on_shared = own[own["assay_id"].isin(assays)]
        frames.append(summarise(on_shared, reference, bootstrap, np.random.default_rng(seed)))
    return SummarySchema.validate(pd.concat(frames, ignore_index=True), lazy=True)


def _one_set(results: pd.DataFrame, label: str) -> pd.DataFrame:
    models = sorted(set(results["model"]))
    if models != [label]:
        raise ValueError(f"results of {label!r} hold {models}")
    return results


def paired_bootstrap_diff(
    results_a: pd.DataFrame,
    results_b: pd.DataFrame,
    reference: pd.DataFrame,
    bootstrap: BootstrapParams,
    rng: np.random.Generator,
    may_be_absent: Collection[str] = (),
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
        may_be_absent: Assays one model may lack (``data.structure_exceptions``: a
            structure-aware model has no scores for them). If either side lacks one, it is
            left out of both, so the comparison is on the shared assays and ``n_assays`` says
            how many. Any other difference between the two assay sets is an error.

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
    one_sided = (set(a["assay_id"]) ^ set(b["assay_id"])) & set(may_be_absent)
    a = a[~a["assay_id"].isin(one_sided)]
    b = b[~b["assay_id"].isin(one_sided)]
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

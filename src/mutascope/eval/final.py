"""The final supervised results table: every run against the best zero-shot model (ADR 0025).

Results only: everything here is read from ``cv_<label>.parquet`` files that ``train cv`` already
wrote. For each run, the ProteinGym aggregate of pooled Spearman with its own bootstrap CI, and
the paired bootstrap of ``run - zero-shot`` on the same test folds and the same assays (ADR 0014,
0021). The comparator is the best zero-shot model the run's assays allow: SaProt for a structure
feature set, ESM-2 650M otherwise.

The by-size tables say where supervision pays: per assay, the run's pooled Spearman minus its
comparator's, and per size bin, a paired bootstrap of the mean difference over that bin's assays
(a plain mean, see :func:`size_comparisons`). A figure reads both; it computes nothing (ADR 0016).
"""

from collections.abc import Collection, Mapping, Sequence
from itertools import pairwise

import numpy as np
import pandas as pd

from mutascope.eval.benchmark import BootstrapParams, summarise
from mutascope.eval.compare import paired_bootstrap_diff
from mutascope.eval.schemas import (
    AssayDifferenceSchema,
    ComparisonSchema,
    SizeComparisonSchema,
    SummarySchema,
)


def comparator(structure: bool, comparators: Mapping[str, str]) -> str:
    """The zero-shot run a supervised run is paired with: ``comparators["structure"]`` for a
    structure feature set, else ``comparators["sequence"]``."""
    return comparators["structure" if structure else "sequence"]


def size_bin_labels(edges: Sequence[int]) -> list[str]:
    """Labels of the bins ``edges`` (lower edges, ascending) make: ``<e0``, ``e0-e1-1``, ...,
    ``>=eN``."""
    if list(edges) != sorted(set(edges)) or not edges:
        raise ValueError(f"size edges must be ascending and distinct, got {list(edges)}")
    labels = [f"<{edges[0]}"]
    labels += [f"{low}-{high - 1}" for low, high in pairwise(edges)]
    return [*labels, f">={edges[-1]}"]


def size_bin(n: int, edges: Sequence[int]) -> str:
    """The label of the bin holding an assay of ``n`` variants (:func:`size_bin_labels`)."""
    return size_bin_labels(edges)[int(np.searchsorted(np.asarray(edges), n, side="right"))]


def final_summary(
    pooled: Mapping[str, pd.DataFrame],
    reference: pd.DataFrame,
    bootstrap: BootstrapParams,
    seed: int,
) -> pd.DataFrame:
    """Each run's aggregate and own CI over its own assays (:class:`SummarySchema`).

    ``pooled`` maps a run label to :func:`~mutascope.eval.supervised.pooled_assay_metrics`.
    Every run is resampled from the same seed, as in ``train compare``.
    """
    frames = [
        summarise(pooled[label], reference, bootstrap, np.random.default_rng(seed))
        for label in pooled
    ]
    return SummarySchema.validate(pd.concat(frames, ignore_index=True), lazy=True)


def final_comparisons(
    pooled: Mapping[str, pd.DataFrame],
    pairs: Sequence[tuple[str, str]],
    reference: pd.DataFrame,
    bootstrap: BootstrapParams,
    seed: int,
    may_be_absent: Mapping[str, Collection[str]],
) -> pd.DataFrame:
    """Paired bootstrap of ``A - B`` for every pair, on the assays both have.

    Args:
        pooled: Run label -> pooled per-assay values.
        pairs: ``(run, comparator)`` labels.
        reference: ProteinGym reference table.
        bootstrap: Resampling settings.
        seed: Seed of every pair's resampling.
        may_be_absent: Run label -> assays it is allowed to lack (the structure exceptions, the
            assays below a model's minimum). Anything else missing is an error.
    """
    frames = [
        paired_bootstrap_diff(
            pooled[a],
            pooled[b],
            reference,
            bootstrap,
            np.random.default_rng(seed),
            may_be_absent=[*may_be_absent.get(a, ()), *may_be_absent.get(b, ())],
        )
        for a, b in pairs
    ]
    return ComparisonSchema.validate(pd.concat(frames, ignore_index=True), lazy=True)


def _spearman_rows(pooled: pd.DataFrame) -> pd.DataFrame:
    rows = pooled[pooled["metric"] == "spearman"]
    return rows[["split", "assay_id", "value"]].astype({"split": str, "assay_id": str})


def assay_differences(
    pooled: Mapping[str, pd.DataFrame],
    pairs: Sequence[tuple[str, str]],
    sizes: Mapping[str, int],
    edges: Sequence[int],
) -> pd.DataFrame:
    """Per assay and scheme: pooled Spearman of each run and its comparator, and the difference.

    Only the assays both have (the comparisons have already checked nothing else is missing).
    ``n_variants`` is the reference's single-mutant count, ``size_bin`` its bin.
    """
    frames: list[pd.DataFrame] = []
    for a, b in pairs:
        joined = _spearman_rows(pooled[a]).merge(
            _spearman_rows(pooled[b]), on=["split", "assay_id"], suffixes=("_a", "_b")
        )
        n = joined["assay_id"].map(sizes)
        if n.isna().any():
            missing = sorted(joined.loc[n.isna(), "assay_id"].unique())
            raise KeyError(f"no variant count for {missing}")
        frames.append(
            pd.DataFrame(
                {
                    "model_a": a,
                    "model_b": b,
                    "split": joined["split"],
                    "assay_id": joined["assay_id"],
                    "n_variants": n.astype(np.int64),
                    "size_bin": [size_bin(int(v), edges) for v in n],
                    "value_a": joined["value_a"].astype(np.float64),
                    "value_b": joined["value_b"].astype(np.float64),
                    "diff": (joined["value_a"] - joined["value_b"]).astype(np.float64),
                }
            ).sort_values(["split", "assay_id"])
        )
    return AssayDifferenceSchema.validate(pd.concat(frames, ignore_index=True), lazy=True)


def size_comparisons(
    differences: pd.DataFrame,
    bootstrap: BootstrapParams,
    seed: int,
    edges: Sequence[int],
) -> pd.DataFrame:
    """Paired bootstrap of the mean per-assay difference ``A - B`` within each size bin.

    **A plain mean here, not the ProteinGym aggregate**, on purpose. Within one size bin the
    function types are few and unbalanced (the 200-299 bin of the 213 structure assays: 4
    organismal fitness, 2 activity, 1 expression), and the aggregate's bootstrap resamples within
    function types, so a type with one assay adds no spread: there it gave a CI of
    [+0.041, +0.066] for differences ranging from -0.094 to +0.129. The by-size figure is a
    diagnostic, never a headline; assays are resampled with replacement, unstratified, and the
    same resampled assays give both runs' means (paired). ``value_a`` / ``value_b`` are plain
    means too. A bin with no assay for a pair has no row (the MLP has none below its minimum).
    """
    records: list[dict[str, object]] = []
    alpha = (1 - bootstrap.confidence) / 2
    for (a, b, split), rows in differences.groupby(["model_a", "model_b", "split"], sort=False):
        for label in size_bin_labels(edges):
            part = rows[(rows["size_bin"] == label) & rows["diff"].notna()]
            if part.empty:
                continue
            diff = part["diff"].to_numpy(np.float64)
            rng = np.random.default_rng(seed)
            draws = rng.integers(0, diff.size, size=(bootstrap.n_resamples, diff.size))
            means = diff[draws].mean(axis=1)
            records.append(
                {
                    "model_a": a,
                    "model_b": b,
                    "split": split,
                    "metric": "spearman",
                    "value_a": float(part["value_a"].mean()),
                    "value_b": float(part["value_b"].mean()),
                    "diff": float(diff.mean()),
                    "ci_low": float(np.quantile(means, alpha)),
                    "ci_high": float(np.quantile(means, 1 - alpha)),
                    "share_a_better": float((means > 0).mean()),
                    "n_assays": int(diff.size),
                    "size_bin": label,
                }
            )
    table = pd.DataFrame.from_records(records).astype({"split": "string"})
    return SizeComparisonSchema.validate(table, lazy=True)

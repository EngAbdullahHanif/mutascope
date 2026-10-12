"""ProteinGym-style aggregation of per-assay metrics, and bootstrap CIs over assays.

ProteinGym does not average assays directly: proteins with many assays (P53 has four) would
dominate. Its ``performance_DMS_benchmarks.py`` averages per ``(UniProt_ID,
coarse_selection_type)``, then per ``coarse_selection_type``, then over the function types,
skipping NaN at every level (pandas ``mean``). :func:`aggregate` does exactly that.

Both the point estimate and the bootstrap use one routine, :func:`_weighted_aggregate`, in
which each assay carries a weight: 1 for the point estimate, its draw count in a bootstrap
resample. A resample is then just another weight vector, so 10,000 resamples are a few matrix
products rather than 10,000 pandas group-bys, and the two can never disagree on the protocol.

:func:`paired_bootstrap` compares two score sets: one set of resampled assays is applied to
both, and the CI is of the difference of their aggregates (ADR 0014).
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatMatrix = npt.NDArray[np.float64]


@dataclass(frozen=True)
class AssayGroups:
    """Which (UniProt, function) group and function type each assay belongs to.

    Built once per assay list with :meth:`from_reference`; ``values`` passed to the
    aggregation functions must be in ``assay_ids`` order.
    """

    assay_ids: tuple[str, ...]
    functions: tuple[str, ...]
    """Function types, sorted."""
    group_of_assay: npt.NDArray[np.int64]
    """Index of each assay's ``(UniProt_ID, coarse_selection_type)`` group."""
    function_of_group: npt.NDArray[np.int64]
    """Index into ``functions`` of each group."""

    @classmethod
    def from_reference(cls, reference: pd.DataFrame, assay_ids: list[str]) -> "AssayGroups":
        """Groups for ``assay_ids`` from ProteinGym's reference table.

        Args:
            reference: Has columns ``DMS_id``, ``UniProt_ID`` and ``coarse_selection_type``.
            assay_ids: The assays to aggregate, in the order their values will be given.

        Raises:
            KeyError: An assay is missing from the reference.
        """
        ref = reference.set_index("DMS_id")
        missing = sorted(set(assay_ids) - set(ref.index))
        if missing:
            raise KeyError(f"assays not in the reference file: {missing}")
        rows = ref.loc[assay_ids, ["UniProt_ID", "coarse_selection_type"]]
        keys = list(zip(rows["UniProt_ID"], rows["coarse_selection_type"], strict=True))
        groups = sorted(set(keys))
        functions = sorted({f for _, f in groups})
        return cls(
            assay_ids=tuple(assay_ids),
            functions=tuple(functions),
            group_of_assay=np.array([groups.index(k) for k in keys], dtype=np.int64),
            function_of_group=np.array([functions.index(f) for _, f in groups], dtype=np.int64),
        )


@dataclass(frozen=True)
class Aggregate:
    """An aggregate over assays: the overall value and one per function type."""

    overall: float
    by_function: dict[str, float]


def aggregate(values: npt.NDArray[np.float64], groups: AssayGroups) -> Aggregate:
    """ProteinGym aggregate of one metric: per (UniProt, function) → per function → overall.

    NaN values (undefined metrics) are left out of every mean, as pandas does upstream.
    """
    overall, by_function = _weighted_aggregate(np.ones((1, values.size)), values, groups)
    return Aggregate(
        overall=float(overall[0]),
        by_function={f: float(by_function[0, i]) for i, f in enumerate(groups.functions)},
    )


@dataclass(frozen=True)
class BootstrapCI:
    """Percentile confidence intervals of an aggregate, overall and per function type."""

    overall: tuple[float, float]
    by_function: dict[str, tuple[float, float]]


def bootstrap_ci(
    values: npt.NDArray[np.float64],
    groups: AssayGroups,
    *,
    n_resamples: int,
    confidence: float,
    rng: np.random.Generator,
) -> BootstrapCI:
    """Percentile bootstrap CI of :func:`aggregate`, resampling assays within function types.

    Each resample draws, within every function type, as many assays as it has (with
    replacement), then recomputes the full hierarchical aggregate. Stratifying keeps all five
    function types in every resample, so the overall mean always averages the same categories;
    an unstratified draw could miss the smallest one and change what is being estimated.
    Assays with an undefined (NaN) metric are left out before resampling, consistent with the
    point estimate.
    """
    weights = _resample_weights(~np.isnan(values), groups, n_resamples, rng)
    overall, by_function = _weighted_aggregate(weights, values, groups)
    return BootstrapCI(
        overall=_interval(overall, confidence),
        by_function={
            f: _interval(by_function[:, i], confidence) for i, f in enumerate(groups.functions)
        },
    )


@dataclass(frozen=True)
class Difference:
    """A paired difference ``A - B`` of one aggregate, with its bootstrap summary."""

    value_a: float
    value_b: float
    ci: tuple[float, float]
    """Percentile CI of the difference."""
    share_a_better: float
    """Share of resamples in which A's aggregate is strictly greater than B's."""

    @property
    def diff(self) -> float:
        return self.value_a - self.value_b


@dataclass(frozen=True)
class PairedDifference:
    """Paired bootstrap of ``A - B``, overall and per function type."""

    overall: Difference
    by_function: dict[str, Difference]
    n_assays: int
    """Assays with a defined value for both A and B (the only ones used)."""
    n_by_function: dict[str, int]


def paired_bootstrap(
    values_a: npt.NDArray[np.float64],
    values_b: npt.NDArray[np.float64],
    groups: AssayGroups,
    *,
    n_resamples: int,
    confidence: float,
    rng: np.random.Generator,
) -> PairedDifference:
    """Paired bootstrap of the difference between two score sets' ProteinGym aggregates.

    Two models scored on the same assays are strongly correlated: an assay that is hard for
    one is hard for the other. Their separate CIs (:func:`bootstrap_ci`) are dominated by that
    shared assay-to-assay spread and overlap even when one model wins on almost every assay.
    Here every resample draws one set of assays (stratified by function type, as in
    :func:`bootstrap_ci`) and applies it to both, so the shared spread cancels in ``A - B``.
    Each resample recomputes the full hierarchical aggregate of both.

    An assay counts only if its value is defined for both; it is left out of both otherwise,
    so the two aggregates are always over the same assays.

    Args:
        values_a: Per-assay metric of A, in ``groups.assay_ids`` order.
        values_b: The same for B.
        groups: Assay grouping.
        n_resamples: Bootstrap resamples.
        confidence: CI level, e.g. 0.95.
        rng: Seeded generator.
    """
    if values_a.shape != values_b.shape:
        raise ValueError("A and B must have one value per assay, in the same order")
    paired = ~(np.isnan(values_a) | np.isnan(values_b))
    a = np.where(paired, values_a, np.nan)
    b = np.where(paired, values_b, np.nan)
    point_a, point_b = aggregate(a, groups), aggregate(b, groups)
    weights = _resample_weights(paired, groups, n_resamples, rng)
    overall_a, function_a = _weighted_aggregate(weights, a, groups)
    overall_b, function_b = _weighted_aggregate(weights, b, groups)

    def difference(value_a: float, value_b: float, samples: npt.NDArray[np.float64]) -> Difference:
        share = float("nan") if np.isnan(samples).all() else float(np.mean(samples > 0))
        return Difference(value_a, value_b, _interval(samples, confidence), share)

    function_of_assay = groups.function_of_group[groups.group_of_assay]
    return PairedDifference(
        overall=difference(point_a.overall, point_b.overall, overall_a - overall_b),
        by_function={
            f: difference(
                point_a.by_function[f], point_b.by_function[f], function_a[:, i] - function_b[:, i]
            )
            for i, f in enumerate(groups.functions)
        },
        n_assays=int(paired.sum()),
        n_by_function={
            f: int((paired & (function_of_assay == i)).sum())
            for i, f in enumerate(groups.functions)
        },
    )


def _resample_weights(
    defined: npt.NDArray[np.bool_], groups: AssayGroups, n_resamples: int, rng: np.random.Generator
) -> FloatMatrix:
    """Draw counts per assay (resamples x assays): within each function type, as many of its
    ``defined`` assays as it has, with replacement. Other assays get 0."""
    weights = np.zeros((n_resamples, defined.size))
    function_of_assay = groups.function_of_group[groups.group_of_assay]
    for f in range(len(groups.functions)):
        stratum = np.flatnonzero(defined & (function_of_assay == f))
        if stratum.size:
            uniform = np.full(stratum.size, 1 / stratum.size)
            weights[:, stratum] = rng.multinomial(stratum.size, uniform, size=n_resamples)
    return weights


def _interval(samples: npt.NDArray[np.float64], confidence: float) -> tuple[float, float]:
    """Percentile interval of ``samples``; NaN if every sample is NaN."""
    if np.isnan(samples).all():
        return float("nan"), float("nan")
    tail = (1 - confidence) / 2 * 100
    low, high = np.percentile(samples, (tail, 100 - tail))
    return float(low), float(high)


def _weighted_aggregate(
    weights: FloatMatrix, values: npt.NDArray[np.float64], groups: AssayGroups
) -> tuple[npt.NDArray[np.float64], FloatMatrix]:
    """Hierarchical means for each row of ``weights`` (shape: resamples x assays).

    A group's mean is the weighted mean of its assays; a function type's mean is the plain mean
    of its groups that have any weight; the overall value is the plain mean of the function
    types that have any. NaN values get weight 0.

    Returns:
        Overall values (resamples,) and per-function values (resamples x functions).
    """
    if values.shape != (len(groups.assay_ids),) or weights.shape[1] != values.size:
        raise ValueError("values and weights must have one entry per assay")
    defined = ~np.isnan(values)
    w = np.where(defined, weights, 0.0)
    v = np.where(defined, values, 0.0)
    assay_to_group = _one_hot(groups.group_of_assay, len(groups.function_of_group))
    group_to_function = _one_hot(groups.function_of_group, len(groups.functions))

    group_weight = w @ assay_to_group
    group_present = group_weight > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        group_mean = np.where(group_present, ((w * v) @ assay_to_group) / group_weight, 0.0)
        function_mean = (group_mean @ group_to_function) / (group_present @ group_to_function)
    function_present = ~np.isnan(function_mean)
    with np.errstate(invalid="ignore", divide="ignore"):
        overall = np.where(function_present, function_mean, 0.0).sum(axis=1) / function_present.sum(
            axis=1
        )
    return overall, function_mean


def _one_hot(index: npt.NDArray[np.int64], size: int) -> FloatMatrix:
    matrix = np.zeros((index.size, size))
    matrix[np.arange(index.size), index] = 1.0
    return matrix

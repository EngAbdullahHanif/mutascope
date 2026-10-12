"""ProteinGym-style aggregation of per-assay metrics, and bootstrap CIs over assays.

ProteinGym does not average assays directly: proteins with many assays (P53 has four) would
dominate. Its ``performance_DMS_benchmarks.py`` averages per ``(UniProt_ID,
coarse_selection_type)``, then per ``coarse_selection_type``, then over the function types,
skipping NaN at every level (pandas ``mean``). :func:`aggregate` does exactly that.

Both the point estimate and the bootstrap use one routine, :func:`_weighted_aggregate`, in
which each assay carries a weight: 1 for the point estimate, its draw count in a bootstrap
resample. A resample is then just another weight vector, so 10,000 resamples are a few matrix
products rather than 10,000 pandas group-bys, and the two can never disagree on the protocol.
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
    defined = ~np.isnan(values)
    weights = np.zeros((n_resamples, values.size))
    function_of_assay = groups.function_of_group[groups.group_of_assay]
    for f in range(len(groups.functions)):
        stratum = np.flatnonzero(defined & (function_of_assay == f))
        if stratum.size:
            uniform = np.full(stratum.size, 1 / stratum.size)
            weights[:, stratum] = rng.multinomial(stratum.size, uniform, size=n_resamples)
    overall, by_function = _weighted_aggregate(weights, values, groups)
    tail = (1 - confidence) / 2 * 100
    quantiles = (tail, 100 - tail)

    def interval(samples: npt.NDArray[np.float64]) -> tuple[float, float]:
        if np.isnan(samples).all():
            return float("nan"), float("nan")
        low, high = np.percentile(samples, quantiles)
        return float(low), float(high)

    return BootstrapCI(
        overall=interval(overall),
        by_function={f: interval(by_function[:, i]) for i, f in enumerate(groups.functions)},
    )


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

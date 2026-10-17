"""``benchmarks.json``: the headline tables, copied from the results files (ADR 0028).

Nothing is computed here except which rows to keep and the caveats, which are read off the
paired comparisons: a function type gets one when, under some split, the deployed model's
paired 95% CI against its zero-shot comparator does not lie above 0. With the current results
that is Binding under modulo and contiguous splits (ADR 0026), but the rule, not a hand-written
list, decides.
"""

import math
from collections.abc import Sequence

import pandas as pd

from mutascope.export.format import (
    Benchmarks,
    CalibrationRow,
    Caveat,
    ComparisonRow,
    FinalCoverage,
    FunctionTypeRow,
    SummaryRow,
    VersionStamp,
)


class BenchmarkError(ValueError):
    """A results table lacks the rows ``benchmarks.json`` needs."""


def _none(value: object) -> object:
    """pandas missing values -> None (the JSON ``null``)."""
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else value


def _records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return [
        {str(k): _none(v) for k, v in row.items()}
        for row in frame.to_dict(orient="records")  # pyright: ignore[reportUnknownMemberType]
    ]


def summary_rows(frame: pd.DataFrame) -> list[SummaryRow]:
    """Rows of a ``SummarySchema`` table."""
    return [SummaryRow.model_validate(r) for r in _records(frame)]


def comparison_rows(frame: pd.DataFrame) -> list[ComparisonRow]:
    """Rows of a ``ComparisonSchema`` table, or a ``SizeComparisonSchema`` one (whose rows
    get ``level`` ``size`` and their ``size_bin``)."""
    if "size_bin" in frame.columns:
        frame = frame.assign(level="size", group=None)
    else:
        frame = frame.assign(size_bin=None)
    return [ComparisonRow.model_validate(r) for r in _records(frame)]


def function_type_rows(
    comparisons: pd.DataFrame, *, deployed: str, comparator: str
) -> list[FunctionTypeRow]:
    """The deployed model against its comparator per function type and split.

    Raises:
        BenchmarkError: The comparisons have no such rows.
    """
    rows = comparisons[
        (comparisons["model_a"] == deployed)
        & (comparisons["model_b"] == comparator)
        & (comparisons["level"] == "function")
        & (comparisons["metric"] == "spearman")
    ].sort_values(["group", "split"])
    if rows.empty:
        raise BenchmarkError(f"no per-function-type comparison of {deployed} with {comparator}")
    return [
        FunctionTypeRow(
            function_type=str(r["group"]),
            split=str(r["split"]),
            n_assays=int(r["n_assays"]),
            supervised=float(r["value_a"]),
            zero_shot=float(r["value_b"]),
            diff=float(r["diff"]),
            ci_low=float(r["ci_low"]),
            ci_high=float(r["ci_high"]),
            gain_shown=bool(r["ci_low"] > 0),
        )
        for _, r in rows.iterrows()
    ]


def caveats(rows: Sequence[FunctionTypeRow], comparator_name: str) -> list[Caveat]:
    """One caveat per function type whose gain is not shown under some split."""
    out: list[Caveat] = []
    for function_type in sorted({r.function_type for r in rows}):
        weak = [r for r in rows if r.function_type == function_type and not r.gain_shown]
        if not weak:
            continue
        details = "; ".join(
            f"{r.split}: {r.diff:+.3f} (95% CI {r.ci_low:+.3f} to {r.ci_high:+.3f})" for r in weak
        )
        out.append(
            Caveat(
                function_type=function_type,
                n_assays=weak[0].n_assays,
                splits=[r.split for r in weak],
                text=(
                    f"{function_type} ({weak[0].n_assays} assays): the supervised model's gain "
                    f"over zero-shot {comparator_name} is not shown under the "
                    f"{' and '.join(r.split for r in weak)} split"
                    f"{'s' if len(weak) > 1 else ''}; the paired 95% CI of the Spearman "
                    f"difference includes 0 ({details})."
                ),
            )
        )
    return out


def calibration_rows(
    summary: pd.DataFrame, *, model: str, feature_set: str, interval: str
) -> list[CalibrationRow]:
    """Coverage of the deployed interval design from ``train uncertainty``'s summary.

    Raises:
        BenchmarkError: The summary has no coverage rows for the design.
    """
    rows = summary[
        (summary["model"] == model)
        & (summary["feature_set"] == feature_set)
        & (summary["interval"] == interval)
        & (summary["metric"] == "coverage")
    ].sort_values(["scheme", "mode", "level", "size_bin", "statistic"])
    if rows.empty:
        raise BenchmarkError(f"no coverage rows for {model} {feature_set} ({interval})")
    return [
        CalibrationRow(
            scheme=str(r["scheme"]),
            mode=str(r["mode"]),
            level=float(r["level"]),
            size_bin=str(r["size_bin"]),
            statistic=str(r["statistic"]),
            value=float(r["value"]),
        )
        for _, r in rows.iterrows()
    ]


def final_coverage(checks: pd.DataFrame, levels: Sequence[int]) -> list[FinalCoverage]:
    """Out-of-fold coverage of the deployed landscapes over assays (``final_checks``)."""
    out: list[FinalCoverage] = []
    for level in levels:
        values = checks[f"oof_coverage_{level}"].astype(float)
        out.append(
            FinalCoverage(
                level=level / 100,
                n_assays=len(values),
                mean=float(values.mean()),
                median=float(values.median()),
                min=float(values.min()),
                max=float(values.max()),
            )
        )
    return out


def build_benchmarks(
    *,
    version: VersionStamp,
    deployed: str,
    comparator: str,
    comparator_name: str,
    zero_shot_summaries: Sequence[pd.DataFrame],
    zero_shot_shared: pd.DataFrame,
    zero_shot_comparisons: pd.DataFrame,
    supervised_summary: pd.DataFrame,
    supervised_comparisons: pd.DataFrame,
    size_comparisons: pd.DataFrame,
    uncertainty_summary: pd.DataFrame,
    final_checks: pd.DataFrame,
    model: str,
    feature_set: str,
    interval: str,
    levels: Sequence[int],
) -> Benchmarks:
    """Assemble ``benchmarks.json`` from the results tables (see the module docstring)."""
    function_types = function_type_rows(
        supervised_comparisons, deployed=deployed, comparator=comparator
    )
    return Benchmarks(
        version=version,
        deployed_model=deployed,
        zero_shot_comparator=comparator,
        zero_shot_summary=[r for f in zero_shot_summaries for r in summary_rows(f)],
        zero_shot_shared=summary_rows(zero_shot_shared),
        zero_shot_comparisons=comparison_rows(zero_shot_comparisons),
        supervised_summary=summary_rows(supervised_summary),
        supervised_comparisons=[
            *comparison_rows(supervised_comparisons),
            *comparison_rows(size_comparisons),
        ],
        function_types=function_types,
        caveats=caveats(function_types, comparator_name),
        calibration=calibration_rows(
            uncertainty_summary, model=model, feature_set=feature_set, interval=interval
        ),
        final_coverage=final_coverage(final_checks, levels),
    )

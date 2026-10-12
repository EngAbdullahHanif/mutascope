"""ProteinGym aggregation and the stratified bootstrap."""

import math

import numpy as np
import pandas as pd
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.eval.proteingym_reference import proteingym_aggregate

from mutascope.eval.aggregate import AssayGroups, BootstrapCI, aggregate, bootstrap_ci


def _reference(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["DMS_id", "UniProt_ID", "coarse_selection_type"])


# Two Stability assays of one protein, one of another, one Binding assay.
REFERENCE = _reference(
    [
        ("p1_a", "P1", "Stability"),
        ("p1_b", "P1", "Stability"),
        ("p2_a", "P2", "Stability"),
        ("p3_a", "P3", "Binding"),
    ]
)


def test_hand_worked_example() -> None:
    """P1 = mean(0.2, 0.4) = 0.3; Stability = mean(0.3, 0.9) = 0.6; overall = mean(0.6, 0.1)."""
    groups = AssayGroups.from_reference(REFERENCE, ["p1_a", "p1_b", "p2_a", "p3_a"])
    result = aggregate(np.array([0.2, 0.4, 0.9, 0.1]), groups)
    assert result.by_function == pytest.approx({"Binding": 0.1, "Stability": 0.6})
    assert result.overall == pytest.approx(0.35)


def test_nan_is_skipped_at_every_level() -> None:
    groups = AssayGroups.from_reference(REFERENCE, ["p1_a", "p1_b", "p2_a", "p3_a"])
    result = aggregate(np.array([np.nan, 0.4, 0.9, np.nan]), groups)
    assert result.by_function["Stability"] == pytest.approx(0.65)
    assert math.isnan(result.by_function["Binding"])
    assert result.overall == pytest.approx(0.65)


@st.composite
def per_assay_tables(draw: st.DrawFn) -> pd.DataFrame:
    n = draw(st.integers(min_value=1, max_value=40))
    rows = [
        (
            f"assay{i}",
            draw(st.sampled_from(["P1", "P2", "P3", "P4"])),
            draw(st.sampled_from(["Activity", "Binding", "Stability"])),
            draw(st.one_of(st.floats(-1, 1), st.just(float("nan")))),
        )
        for i in range(n)
    ]
    return pd.DataFrame(rows, columns=["DMS_id", "UniProt_ID", "coarse_selection_type", "value"])


@given(per_assay_tables())
@settings(max_examples=200)
def test_matches_proteingym_group_by(table: pd.DataFrame) -> None:
    groups = AssayGroups.from_reference(table, table["DMS_id"].tolist())
    ours = aggregate(table["value"].to_numpy(np.float64), groups)
    theirs = proteingym_aggregate(
        table.rename(columns={"coarse_selection_type": "Selection Type"})[
            ["UniProt_ID", "Selection Type", "value"]
        ]
    )
    np.testing.assert_allclose(ours.overall, theirs[0], rtol=1e-12, atol=1e-12)
    for function, value in theirs[1].items():
        np.testing.assert_allclose(ours.by_function[function], value, rtol=1e-12, atol=1e-12)


def test_unknown_assay_is_an_error() -> None:
    with pytest.raises(KeyError, match="nope"):
        AssayGroups.from_reference(REFERENCE, ["p1_a", "nope"])


def _ci(values: np.ndarray, seed: int = 0, n: int = 2000) -> BootstrapCI:
    groups = AssayGroups.from_reference(REFERENCE, ["p1_a", "p1_b", "p2_a", "p3_a"])
    return bootstrap_ci(
        values, groups, n_resamples=n, confidence=0.95, rng=np.random.default_rng(seed)
    )


def test_bootstrap_is_reproducible_and_brackets_the_estimate() -> None:
    values = np.array([0.2, 0.4, 0.9, 0.1])
    first, again = _ci(values), _ci(values)
    assert first == again
    low, high = first.overall
    assert low <= 0.35 <= high
    assert low < high


def test_bootstrap_keeps_a_lone_assay_fixed() -> None:
    """Stratifying by function type means a one-assay function never varies."""
    ci = _ci(np.array([0.2, 0.4, 0.9, 0.1]))
    assert ci.by_function["Binding"] == pytest.approx((0.1, 0.1))


def test_bootstrap_of_an_undefined_function_is_nan() -> None:
    ci = _ci(np.array([0.2, 0.4, 0.9, np.nan]))
    assert all(math.isnan(x) for x in ci.by_function["Binding"])
    assert not any(math.isnan(x) for x in ci.overall)


def test_bootstrap_ci_shrinks_with_more_assays() -> None:
    rng = np.random.default_rng(1)
    widths: list[float] = []
    for n in (10, 160):
        reference = _reference([(f"a{i}", f"P{i}", "Stability") for i in range(n)])
        groups = AssayGroups.from_reference(reference, reference["DMS_id"].tolist())
        ci = bootstrap_ci(
            rng.normal(0.4, 0.2, n), groups, n_resamples=2000, confidence=0.95, rng=rng
        )
        widths.append(ci.overall[1] - ci.overall[0])
    assert widths[1] < widths[0] / 2  # ~ 1/sqrt(n): 4x narrower in expectation

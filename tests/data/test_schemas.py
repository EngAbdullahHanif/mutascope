"""Raw and processed variant schemas accept valid tables and reject each broken rule."""

from collections.abc import Callable
from typing import Any, cast

import pandas as pd
import pandera.pandas as pa
import pytest

from mutascope.data.schemas import ProcessedVariantSchema, RawVariantSchema


def raw(**overrides: list[Any]) -> pd.DataFrame:
    data: dict[str, list[Any]] = {
        "mutant": ["M1A", "K2A:V3A"],
        "mutated_sequence": ["AKV", "MAA"],
        "DMS_score": ["0.5", "-1.25"],
        "DMS_score_bin": ["1", "0"],
    }
    return pd.DataFrame({**data, **overrides}, dtype=str)


def processed() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "assay_id": ["A", "A", "B"],
            "mutant": ["M1A", "K2C", "M1A"],
            "position": [0, 1, 0],
            "wt_aa": ["M", "K", "M"],
            "mt_aa": ["A", "C", "A"],
            "DMS_score": [0.5, -1.0, 2.0],
            "DMS_score_bin": [1, 0, 1],
        }
    )


def failing_checks(schema: type[pa.DataFrameModel], df: pd.DataFrame) -> set[str]:
    with pytest.raises(pa.errors.SchemaErrors) as excinfo:
        schema.validate(df, lazy=True)
    failure_cases = cast("pd.DataFrame", excinfo.value.failure_cases)  # pyright: ignore[reportUnknownMemberType]
    return {str(c) for c in failure_cases["check"]}


def test_raw_valid_table_is_coerced() -> None:
    out = RawVariantSchema.validate(raw())
    assert out["DMS_score"].tolist() == [0.5, -1.25]
    assert out["DMS_score_bin"].tolist() == [1, 0]


def test_raw_tolerates_extra_columns() -> None:
    df = raw()
    df["DMS_bin_score"] = df["DMS_score_bin"]
    RawVariantSchema.validate(df)


@pytest.mark.parametrize(
    ("column", "values", "check"),
    [
        ("mutant", ["M1A", "m2a"], "str_matches"),
        ("mutant", ["M1A", "K2A:"], "str_matches"),
        ("mutant", ["M1A", "K2A "], "str_matches"),
        ("mutated_sequence", ["AKV", "MXA"], "str_matches"),
        ("DMS_score", ["0.5", "high"], "coerce_dtype"),
        ("DMS_score", ["0.5", None], "not_nullable"),
        ("DMS_score_bin", ["1", "2"], "isin"),
    ],
)
def test_raw_rejects(column: str, values: list[Any], check: str) -> None:
    checks = failing_checks(RawVariantSchema, raw(**{column: values}))
    assert any(c.startswith(check) for c in checks), checks


def test_raw_requires_columns() -> None:
    checks = failing_checks(RawVariantSchema, raw().drop(columns="DMS_score_bin"))
    assert "column_in_dataframe" in checks


def test_processed_valid_table_passes() -> None:
    ProcessedVariantSchema.validate(processed())


Mutation = Callable[[pd.DataFrame], None]


def _set(column: str, row: int, value: Any) -> Mutation:
    def apply(df: pd.DataFrame) -> None:
        df.loc[row, column] = value

    return apply


def _add_extra_column(df: pd.DataFrame) -> None:
    df["mutated_sequence"] = "MKV"


@pytest.mark.parametrize(
    ("mutate", "check"),
    [
        (_set("position", 0, 1), "derived_columns_match_mutant"),  # off by one
        (_set("wt_aa", 0, "K"), "derived_columns_match_mutant"),
        (_set("mutant", 0, "M1A:K2C"), "str_matches"),  # processed is singles only
        (_set("position", 0, -1), "greater_than_or_equal_to"),
        (_set("mt_aa", 1, "X"), "isin"),
        (_set("DMS_score", 0, float("nan")), "not_nullable"),
        (_add_extra_column, "column_in_schema"),
    ],
)
def test_processed_rejects(mutate: Mutation, check: str) -> None:
    df = processed()
    mutate(df)
    checks = failing_checks(ProcessedVariantSchema, df)
    assert any(c.startswith(check) for c in checks), checks


def test_processed_rejects_synonymous() -> None:
    df = processed()
    df.loc[0, ["mutant", "mt_aa"]] = ["M1M", "M"]
    assert "wt_differs_from_mt" in failing_checks(ProcessedVariantSchema, df)


def test_processed_rejects_duplicate_assay_mutant() -> None:
    df = processed()
    df.loc[2, "assay_id"] = "A"  # now (A, M1A) appears twice
    assert "multiple_fields_uniqueness" in failing_checks(ProcessedVariantSchema, df)

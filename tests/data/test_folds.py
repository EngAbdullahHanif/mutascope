"""Fold join: one fold per scheme for every processed variant, checked, never patched."""

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pandera.pandas as pa
import pytest
from tests.synthetic_assays import synthetic_assay, write_fold_csvs, write_variants

from mutascope.data.folds import (
    FoldJoinError,
    build_folds,
    join_folds,
    load_cv_table,
    read_fold_file,
)
from mutascope.data.schemas import FoldAssignmentSchema

FOLD_COLUMNS = ["fold_random", "fold_modulo", "fold_contiguous"]


@pytest.fixture
def assays() -> list[pd.DataFrame]:
    return [synthetic_assay("A_TEST", length=12, seed=1), synthetic_assay("B_TEST", seed=2)]


def folds_of(assay: pd.DataFrame) -> pd.DataFrame:
    return assay[["mutant", *FOLD_COLUMNS]]


def test_join_gives_every_variant_its_folds(assays: list[pd.DataFrame]) -> None:
    assay = assays[0]
    shuffled = folds_of(assay).sample(frac=1.0, random_state=0)
    joined = join_folds("A_TEST", assay, shuffled)
    FoldAssignmentSchema.validate(joined)
    assert list(joined["mutant"]) == list(assay["mutant"])
    assert (joined[FOLD_COLUMNS].to_numpy() == assay[FOLD_COLUMNS].to_numpy()).all()


def test_read_fold_file_keeps_only_keys_and_folds(
    assays: list[pd.DataFrame], tmp_path: Path
) -> None:
    folds_dir = write_fold_csvs(assays, tmp_path / "cv")
    read = read_fold_file(folds_dir / "A_TEST.csv")
    assert list(read.columns) == ["mutant", *FOLD_COLUMNS]
    assert str(read["fold_random"].dtype) == "int64"


def drop_first(f: pd.DataFrame) -> pd.DataFrame:
    return f.iloc[1:]


def repeat_two(f: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([f, f.iloc[:2]])


def add_unknown(f: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([f, pd.DataFrame([["W99Y", 0, 0, 0]], columns=f.columns)])


def null_one(f: pd.DataFrame) -> pd.DataFrame:
    return f.assign(fold_modulo=f["fold_modulo"].where(f.index != 3))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (drop_first, "1 variants without a fold"),
        (repeat_two, "2 mutants repeated"),
        (add_unknown, "1 fold-file mutants not in the variants"),
        (null_one, "null fold"),
    ],
    ids=["missing", "repeated", "extra", "null"],
)
def test_join_mismatch_fails_loudly(
    assays: list[pd.DataFrame], change: Callable[[pd.DataFrame], pd.DataFrame], message: str
) -> None:
    with pytest.raises(FoldJoinError, match=message):
        join_folds("A_TEST", assays[0], change(folds_of(assays[0])))


def test_schema_rejects_a_position_split_across_modulo_folds(assays: list[pd.DataFrame]) -> None:
    joined = join_folds("A_TEST", assays[0], folds_of(assays[0]))
    first = joined.index[0]
    joined.loc[first, "fold_modulo"] = (joined.loc[first, "fold_modulo"] + 1) % 5
    with pytest.raises(pa.errors.SchemaError, match="position_schemes_hold_whole_positions"):
        FoldAssignmentSchema.validate(joined)


def test_schema_rejects_unordered_contiguous_folds(assays: list[pd.DataFrame]) -> None:
    """Swapping the modulo and contiguous columns is caught: modulo folds are not ordered."""
    joined = join_folds("B_TEST", assays[1], folds_of(assays[1]))
    swapped = joined.rename(
        columns={"fold_modulo": "fold_contiguous", "fold_contiguous": "fold_modulo"}
    )[joined.columns]
    with pytest.raises(pa.errors.SchemaError, match="contiguous_folds_are_ordered"):
        FoldAssignmentSchema.validate(swapped)


def test_build_folds_writes_validated_partitions(
    assays: list[pd.DataFrame], tmp_path: Path
) -> None:
    variants_dir = write_variants(assays, tmp_path / "variants")
    folds_dir = write_fold_csvs(assays, tmp_path / "cv")
    report = build_folds(variants_dir, folds_dir, tmp_path / "folds")
    assert list(report["assay_id"]) == ["A_TEST", "B_TEST"]
    assert list(report["variants"]) == [len(a) for a in assays]
    assert (report[["folds_random", "folds_modulo", "folds_contiguous"]] == 5).all().all()
    assert sorted(p.name for p in (tmp_path / "folds").iterdir()) == [
        "assay_id=A_TEST",
        "assay_id=B_TEST",
    ]


def test_build_folds_writes_nothing_when_any_assay_fails(
    assays: list[pd.DataFrame], tmp_path: Path
) -> None:
    variants_dir = write_variants(assays, tmp_path / "variants")
    folds_dir = write_fold_csvs(assays, tmp_path / "cv")
    (folds_dir / "B_TEST.csv").unlink()
    with pytest.raises(FoldJoinError, match="B_TEST: no fold file"):
        build_folds(variants_dir, folds_dir, tmp_path / "folds")
    assert not (tmp_path / "folds").exists()


def test_load_cv_table_takes_labels_from_variants(
    assays: list[pd.DataFrame], tmp_path: Path
) -> None:
    variants_dir = write_variants(assays, tmp_path / "variants")
    build_folds(variants_dir, write_fold_csvs(assays, tmp_path / "cv"), tmp_path / "folds")
    table = load_cv_table(variants_dir, tmp_path / "folds", ["B_TEST"])
    expected = assays[1].sort_values(["position", "mutant"]).reset_index(drop=True)
    assert list(table["mutant"]) == list(expected["mutant"])
    assert (table["DMS_score"] == expected["DMS_score"]).all()  # bit for bit
    assert (table[FOLD_COLUMNS].to_numpy() == expected[FOLD_COLUMNS].to_numpy()).all()


def test_load_cv_table_rejects_a_stale_fold_table(
    assays: list[pd.DataFrame], tmp_path: Path
) -> None:
    """Variants rewritten after the folds were built have no fold: an error, not dropped rows."""
    build_folds(
        write_variants(assays, tmp_path / "variants"),
        write_fold_csvs(assays, tmp_path / "cv"),
        tmp_path / "folds",
    )
    grown = [assays[0], synthetic_assay("B_TEST", length=61, seed=2)]
    with pytest.raises(FoldJoinError, match="B_TEST"):
        load_cv_table(write_variants(grown, tmp_path / "variants2"), tmp_path / "folds")

"""Cleaning: every removed row has one reason, malformed rows fail, output is validated."""

import csv
from pathlib import Path

import duckdb
import pandas as pd
import pandera.pandas as pa
import pytest
from typer.testing import CliRunner

from mutascope.cli import app
from mutascope.data.clean import clean_assay, clean_dataset, cleaning_report
from mutascope.data.report import render_markdown
from mutascope.data.schemas import CleaningReportSchema
from mutascope.data.validate import VariantValidationError

TARGET = "MKVAR"
HEADER = ["mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"]

# One row per drop reason, plus two kept singles. CSV rows are index + 2.
MIXED = [
    ["M1A", "AKVAR", "0.5", "1"],  # kept
    ["K2C:V3A", "MCAAR", "-1.0", "0"],  # multi_mutant
    ["V3G", "MKGAR", "", "0"],  # missing_value (score)
    ["A4C", "MKVCR", "0.2", "1"],  # kept
    ["M1A", "AKVAR", "0.5", "1"],  # duplicate of row 2
    ["R5G", "MKVAG", "0.1", "0"],  # conflicting_duplicate
    ["R5G", "MKVAG", "0.9", "1"],  # conflicting_duplicate
]


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def frame(rows: list[list[str]], header: list[str] = HEADER) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=header).replace("", None)


def test_each_removed_row_has_one_reason() -> None:
    cleaned = clean_assay("A", TARGET, frame(MIXED))
    assert cleaned.problems == []
    assert cleaned.counts is not None and cleaned.variants is not None
    assert cleaned.counts.dropped == {
        "multi_mutant": 1,
        "missing_value": 1,
        "duplicate": 1,
        "conflicting_duplicate": 2,
    }
    assert (cleaned.counts.rows_in, cleaned.counts.rows_out) == (7, 2)
    assert cleaned.variants.to_dict("records") == [
        {
            "assay_id": "A",
            "mutant": "M1A",
            "position": 0,
            "wt_aa": "M",
            "mt_aa": "A",
            "DMS_score": 0.5,
            "DMS_score_bin": 1,
        },
        {
            "assay_id": "A",
            "mutant": "A4C",
            "position": 3,
            "wt_aa": "A",
            "mt_aa": "C",
            "DMS_score": 0.2,
            "DMS_score_bin": 1,
        },
    ]


def test_multi_mutant_with_missing_value_counts_as_multi() -> None:
    cleaned = clean_assay("A", TARGET, frame([["K2C:V3A", "MCAAR", "", "0"]]))
    assert cleaned.counts is not None
    assert cleaned.counts.dropped["multi_mutant"] == 1
    assert cleaned.counts.dropped["missing_value"] == 0


def test_missing_mutated_sequence_or_mutant_is_dropped_not_failed() -> None:
    cleaned = clean_assay("A", TARGET, frame([["M1A", "", "0.5", "1"], ["", "AKVAR", "0.5", "1"]]))
    assert cleaned.problems == []
    assert cleaned.counts is not None
    assert cleaned.counts.dropped["missing_value"] == 2


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (["V1A", "AKVAR", "0.5", "1"], "wild-type mismatch"),
        (["M1A", "MKVAR", "0.5", "1"], "mutated_sequence differs"),
        (["M1M", "MKVAR", "0.5", "1"], "wild type equals mutant"),
        (["K2C:V9A", "MCVAR", "0.5", "1"], "past end"),  # multi-mutants are checked too
        (["M1A", "AKVAR", "high", "1"], "DMS_score: expected a finite number, got 'high'"),
        (["M1A", "AKVAR", "inf", "1"], "DMS_score: expected a finite number"),
        (["M1A", "AKVAR", "1_0", "1"], "DMS_score: expected a finite number"),
        (["M1A", "AKVAR", " 1.0", "1"], "DMS_score: expected a finite number"),
        (["M1A", "AKVAR", "0.5", "2"], "DMS_score_bin: expected 0 or 1, got '2'"),
    ],
)
def test_malformed_rows_fail_and_are_never_dropped(row: list[str], reason: str) -> None:
    cleaned = clean_assay("A", TARGET, frame([MIXED[0], row]))
    assert cleaned.variants is None
    [problem] = cleaned.problems
    assert (problem.assay_id, problem.row) == ("A", 3)
    assert reason in problem.reason


def test_missing_column_fails() -> None:
    cleaned = clean_assay("A", TARGET, frame([["M1A", "AKVAR", "0.5"]], HEADER[:-1]))
    assert [str(p) for p in cleaned.problems] == ["A: missing columns ['DMS_score_bin']"]


def test_report_schema_rejects_counts_that_do_not_add_up() -> None:
    report = cleaning_report([])
    bad = pd.concat(
        [report, pd.DataFrame([["A", 10, 1, 0, 0, 0, 8]], columns=report.columns)],
        ignore_index=True,
    )
    with pytest.raises(pa.errors.SchemaErrors, match="rows_add_up"):
        CleaningReportSchema.validate(bad, lazy=True)


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[Path, Path]:
    """Reference with assays A (mixed rows) and B (multi-mutants only)."""
    dms = tmp_path / "dms"
    dms.mkdir()
    ref = write_csv(tmp_path / "ref.csv", ["DMS_id", "target_seq"], [["A", TARGET], ["B", TARGET]])
    write_csv(dms / "A.csv", HEADER, MIXED)
    write_csv(dms / "B.csv", [*HEADER, "DMS_bin_score"], [["M1A:K2C", "ACVAR", "0.1", "0", "0"]])
    return ref, dms


def test_clean_dataset_writes_validated_partitions(
    dataset: tuple[Path, Path], tmp_path: Path
) -> None:
    out = tmp_path / "processed" / "variants"
    report = clean_dataset(*dataset, out)

    assert sorted(p.name for p in out.iterdir()) == ["assay_id=A", "assay_id=B"]
    assert not out.with_name("variants.tmp").exists()
    assert report.to_dict("records") == [
        {
            "assay_id": "A",
            "rows_in": 7,
            "multi_mutant": 1,
            "missing_value": 1,
            "duplicate": 1,
            "conflicting_duplicate": 2,
            "rows_out": 2,
        },
        {
            "assay_id": "B",
            "rows_in": 1,
            "multi_mutant": 1,
            "missing_value": 0,
            "duplicate": 0,
            "conflicting_duplicate": 0,
            "rows_out": 0,
        },
    ]


def test_failure_writes_nothing_and_keeps_previous_output(
    dataset: tuple[Path, Path], tmp_path: Path
) -> None:
    ref, dms = dataset
    out = tmp_path / "variants"
    clean_dataset(ref, dms, out)
    write_csv(dms / "B.csv", HEADER, [["V1A", "AKVAR", "0.1", "0"]])

    with pytest.raises(VariantValidationError) as excinfo:
        clean_dataset(ref, dms, out)
    assert [str(p) for p in excinfo.value.problems] == [
        "B (row 2): V1A: wild-type mismatch: target_seq has M where the mutant says V"
    ]
    assert sorted(p.name for p in out.iterdir()) == ["assay_id=A", "assay_id=B"]
    assert not out.with_name("variants.tmp").exists()


def test_markdown_summary(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    md = render_markdown(clean_dataset(*dataset, tmp_path / "variants"))
    assert "| rows in | 8 |" in md
    assert "| removed: multi-mutant | 2 |" in md
    assert "| **rows out** | **2** |" in md
    assert "| A | 7 | 1 | 1 | 1 | 2 | 2 |" in md
    sections = md.split("## ")
    assert "| A |" in sections[2] and "| B |" not in sections[2]  # other reasons: A only
    assert "| B |" in sections[3] and "| A |" not in sections[3]  # nothing left: B only


def test_cli_clean(dataset: tuple[Path, Path], tmp_path: Path) -> None:
    ref, dms = dataset
    report_dir = tmp_path / "results" / "data"
    args = ["data", "clean", "--reference", str(ref), "--dms-dir", str(dms)]
    args += ["--out-dir", str(tmp_path / "variants"), "--report-dir", str(report_dir)]

    ok = CliRunner().invoke(app, args)
    assert ok.exit_code == 0, ok.output
    assert "2 assays: 8 rows in, 2 single mutants out" in ok.stdout
    assert (report_dir / "cleaning_report.md").exists()
    report = duckdb.read_parquet(str(report_dir / "cleaning_report.parquet"))
    assert report.select("rows_out").fetchall() == [(2,), (0,)]

    write_csv(dms / "A.csv", HEADER, [["V1A", "AKVAR", "0.1", "0"]])
    failed = CliRunner().invoke(app, args)
    assert failed.exit_code == 1
    assert "A (row 2): V1A: wild-type mismatch" in failed.stderr


@pytest.mark.parametrize(
    "text",
    [
        "3.5267435205148114",  # CAPSD_AAV2S_Sinai_2021 R585V: pd.to_numeric is 1 ulp off
        "-0.00012111549999999999",  # HSP82_YEAST_..._growth-H2O2 F322A: 7,378 ulps off
    ],
)
def test_scores_are_parsed_exactly(text: str) -> None:
    """Regression: pd.to_numeric mis-parsed 3,124 of 696,311 v1.3 scores.

    The processed score must be exactly the float the upstream text denotes.
    """
    cleaned = clean_assay("A", TARGET, frame([["M1A", "AKVAR", text, "1"]]))
    assert cleaned.variants is not None
    assert cleaned.variants["DMS_score"].tolist() == [float(text)]

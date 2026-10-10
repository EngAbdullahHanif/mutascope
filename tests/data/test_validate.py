"""Wild-type check: every mismatch is reported with assay id and row; nothing is fixed."""

import csv
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from typer.testing import CliRunner

from mutascope.alphabet import AA_ORDER
from mutascope.cli import app
from mutascope.data.validate import (
    VariantProblem,
    VariantValidationError,
    check_assays,
    check_wild_type,
)

TARGET = "MKVAR"


@st.composite
def assay(draw: st.DrawFn) -> tuple[str, list[str], list[str]]:
    """A random target plus correct single mutants (1-based strings) and their sequences."""
    target = draw(st.text(alphabet=AA_ORDER, min_size=1, max_size=60))
    n = draw(st.integers(min_value=1, max_value=30))
    mutants: list[str] = []
    sequences: list[str] = []
    for _ in range(n):
        i = draw(st.integers(min_value=0, max_value=len(target) - 1))
        mt = draw(st.sampled_from(AA_ORDER).filter(lambda aa, wt=target[i]: aa != wt))
        mutants.append(f"{target[i]}{i + 1}{mt}")
        sequences.append(target[:i] + mt + target[i + 1 :])
    return target, mutants, sequences


@given(assay())
def test_consistent_assay_has_no_problems(data: tuple[str, list[str], list[str]]) -> None:
    target, mutants, sequences = data
    assert check_wild_type("X", target, mutants, sequences) == []


@given(assay(), st.data())
def test_corrupted_wild_type_is_reported_at_its_row(
    data: tuple[str, list[str], list[str]], draw: st.DataObject
) -> None:
    target, mutants, sequences = data
    k = draw.draw(st.integers(min_value=0, max_value=len(mutants) - 1))
    wt, rest = mutants[k][0], mutants[k][1:]
    wrong = draw.draw(st.sampled_from(AA_ORDER).filter(lambda aa: aa not in (wt, rest[-1])))
    mutants[k] = wrong + rest

    [problem] = check_wild_type("X", target, mutants, sequences)
    assert (problem.assay_id, problem.row, problem.mutant) == ("X", k + 2, mutants[k])
    assert f"target_seq has {wt} where the mutant says {wrong}" in problem.reason


def test_off_by_one_numbering_reports_every_row() -> None:
    # Mutants numbered as if 0-based: every row should fail, not just the first.
    shifted = ["M0A", "K1A", "V2A", "A3C", "R4A"]
    problems = check_wild_type("X", TARGET, shifted)
    assert [p.row for p in problems] == [2, 3, 4, 5, 6]
    assert "not a substitution" in problems[0].reason  # position 0 does not parse
    assert all("wild-type mismatch" in p.reason for p in problems[1:])


def test_multi_mutant_checks_each_part() -> None:
    [problem] = check_wild_type("X", TARGET, ["M1A:K2C", "M1A:V2C"], ["ACVAR", "ACVAR"])
    assert problem.row == 3
    assert "has K where the mutant says V" in problem.reason


def test_position_past_end() -> None:
    [problem] = check_wild_type("X", TARGET, ["A6C"])
    assert "past end of target_seq (length 5)" in problem.reason


def test_mutated_sequence_disagreement() -> None:
    [problem] = check_wild_type("X", TARGET, ["M1A", "K2C"], ["AKVAR", "MKCAR"])
    assert problem.row == 3
    assert "mutated_sequence differs" in problem.reason


def test_problem_str_names_assay_and_row() -> None:
    p = VariantProblem("ASSAY_1", 7, "K2C", "wild-type mismatch")
    assert str(p) == "ASSAY_1 (row 7): K2C: wild-type mismatch"


# --- check_assays / CLI over files ------------------------------------------------------------

HEADER = ["mutant", "mutated_sequence", "DMS_score", "DMS_score_bin"]


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[Path, Path]:
    """Reference with assays A and B (target MKVAR), each with a valid CSV."""
    dms = tmp_path / "dms"
    dms.mkdir()
    ref = write_csv(tmp_path / "ref.csv", ["DMS_id", "target_seq"], [["A", TARGET], ["B", TARGET]])
    good = [["M1A", "AKVAR", "0.5", "1"], ["K2C:V3A", "MCAAR", "-1.0", "0"]]
    write_csv(dms / "A.csv", HEADER, good)
    write_csv(dms / "B.csv", [*HEADER, "DMS_bin_score"], [[*r, r[-1]] for r in good])
    return ref, dms


def problems_of(ref: Path, dms: Path) -> list[str]:
    with pytest.raises(VariantValidationError) as excinfo:
        check_assays(ref, dms)
    return [str(p) for p in excinfo.value.problems]


def test_valid_dataset_summary_reports_extra_columns(dataset: tuple[Path, Path]) -> None:
    check = check_assays(*dataset)
    assert (len(check.assays), check.rows, check.singles, check.multis) == (2, 4, 2, 2)
    assert [a.extra_columns for a in check.assays] == [(), ("DMS_bin_score",)]


def test_every_problem_in_every_assay_is_reported(dataset: tuple[Path, Path]) -> None:
    ref, dms = dataset
    write_csv(
        dms / "A.csv",
        HEADER,
        [
            ["M1A", "AKVAR", "0.5", "1"],
            ["V2A", "MAVAR", "0.1", "0"],  # wrong wild type
            ["K2C", "MCVAR", "high", "1"],  # non-numeric score
            ["K2c", "MCVAR", "0.1", "1"],  # malformed mutant: schema only, not twice
            ["R5A", "MKVAR", "0.1", "2"],  # bad bin and wrong mutated_sequence
        ],
    )
    write_csv(dms / "B.csv", HEADER, [["M2A", "MAVAR", "0.0", "0"]])
    write_csv(dms / "C.csv", HEADER, [["M1A", "AKVAR", "0.0", "0"]])  # not in reference

    problems = problems_of(ref, dms)
    assert problems[0] == "C: CSV has no entry in the reference file"  # file-level first
    assert problems[1:3] == [
        "A (row 3): V2A: wild-type mismatch: target_seq has K where the mutant says V",
        "A (row 4): K2C: DMS_score: coerce_dtype('float64') failed on 'high'",
    ]
    assert problems[3].startswith("A (row 5): K2c: mutant: str_matches(")
    assert problems[4:] == [
        "A (row 6): R5A: DMS_score_bin: isin([0, 1]) failed on 2",
        "A (row 6): R5A: mutated_sequence differs from target_seq with the mutations applied",
        "B (row 2): M2A: wild-type mismatch: target_seq has K where the mutant says M",
    ]


def test_missing_csv_and_missing_column(dataset: tuple[Path, Path]) -> None:
    ref, dms = dataset
    (dms / "B.csv").unlink()
    write_csv(dms / "A.csv", HEADER[:-1], [["M1A", "AKVAR", "0.5"]])
    problems = problems_of(ref, dms)
    assert any(p == "A: RawVariantSchema: column_in_dataframe (DMS_score_bin)" for p in problems)
    assert any(p.startswith("B: in reference but no B.csv") for p in problems)


def test_cli_check_variants(dataset: tuple[Path, Path]) -> None:
    ref, dms = dataset
    runner = CliRunner()
    args = ["data", "check-variants", "--reference", str(ref), "--dms-dir", str(dms)]

    ok = runner.invoke(app, args)
    assert ok.exit_code == 0, ok.output
    assert "2 assays, 4 variants (2 single, 2 multi): 0 problems" in ok.stdout
    assert "B: extra columns DMS_bin_score" in ok.stdout

    write_csv(dms / "A.csv", HEADER, [["V2A", "MAVAR", "0.1", "0"]])
    failed = runner.invoke(app, args)
    assert failed.exit_code == 1
    assert "A (row 2): V2A: wild-type mismatch" in failed.stderr


def test_shifted_target_seq_fails_every_row_of_that_assay(tmp_path: Path) -> None:
    """Regression: a target_seq off by one residue must fail every row, not just most.

    Repeated residues (KK, AA) and the leading M make some rows' wild type match the shifted
    target by chance; only the mutated_sequence check catches those, so it is exercised here.
    """
    targets = {"GOOD_A": "MKVAR", "SHIFTED": "MKKVAAR", "GOOD_B": "MSTW"}
    dms = tmp_path / "dms"
    dms.mkdir()
    for assay_id, target in targets.items():
        rows = [
            [f"{wt}{i + 1}{mt}", target[:i] + mt + target[i + 1 :], "0.0", "0"]
            for i, wt in enumerate(target)
            for mt in ("C", "G")
        ]
        write_csv(dms / f"{assay_id}.csv", HEADER, rows)

    shifted = "M" + targets["SHIFTED"][:-1]  # MMKKVAA: residues moved one position right
    ref = write_csv(
        tmp_path / "ref.csv",
        ["DMS_id", "target_seq"],
        [[a, shifted if a == "SHIFTED" else t] for a, t in targets.items()],
    )

    with pytest.raises(VariantValidationError) as excinfo:
        check_assays(ref, dms)
    problems = excinfo.value.problems

    n_rows = 2 * len(targets["SHIFTED"])
    assert {p.assay_id for p in problems} == {"SHIFTED"}
    assert len(problems) == n_rows
    assert sorted(p.row for p in problems if p.row is not None) == list(range(2, 2 + n_rows))
    by_chance = [p for p in problems if "mutated_sequence differs" in p.reason]
    assert len(by_chance) == 6  # M1, K3, A6 (two substitutions each) match the shifted target

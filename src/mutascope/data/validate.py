"""Check every raw DMS variant against its assay's target sequence.

For each assay CSV, every row must pass :class:`~mutascope.data.schemas.RawVariantSchema`, and
every substitution in its ``mutant`` must name the residue ``target_seq`` actually has at that
position (CLAUDE.md data rule 3). ``mutated_sequence`` must equal ``target_seq`` with the
mutations applied, which independently catches a numbering offset between the two columns.

Multi-mutant rows are checked too: v1 filters them out later, but a wrong wild type in a
multi-mutant still points at an indexing problem in the assay.

Nothing is fixed or dropped here. All assays are checked before raising, so one run reports the
complete list of problems, each with its assay id and CSV row (header = row 1, as in
``structures.py``).
"""

import csv
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pandas as pd
import pandera.pandas as pa

from mutascope.alphabet import AA_ORDER
from mutascope.data.parse import MutationParseError, parse_mutant
from mutascope.data.schemas import RawVariantSchema
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

FIRST_DATA_ROW = 2
"""CSV line number of the first data row (line 1 is the header)."""
_RAW_COLUMNS = frozenset(RawVariantSchema.to_schema().columns)


class VariantValidationError(ValueError):
    """One or more variants failed validation; ``problems`` lists every one."""

    def __init__(self, problems: Sequence["VariantProblem"], n_assays: int) -> None:
        self.problems = list(problems)
        super().__init__(
            f"{len(self.problems)} variant problem(s) in {n_assays} assays:\n  "
            + "\n  ".join(str(p) for p in self.problems)
        )


@dataclass(frozen=True)
class VariantProblem:
    """One failed check. ``row`` and ``mutant`` are None for whole-file problems."""

    assay_id: str
    row: int | None
    mutant: str | None
    reason: str

    def __str__(self) -> str:
        where = "" if self.row is None else f" (row {self.row})"
        what = "" if self.mutant is None else f" {self.mutant}:"
        return f"{self.assay_id}{where}:{what} {self.reason}"


@dataclass(frozen=True)
class AssaySummary:
    """Counts for one assay that passed every check."""

    assay_id: str
    rows: int
    singles: int
    multis: int
    extra_columns: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class VariantCheck:
    """Outcome of :func:`check_assays` when every variant passed."""

    assays: list[AssaySummary]

    @property
    def rows(self) -> int:
        """Total variants checked."""
        return sum(a.rows for a in self.assays)

    @property
    def singles(self) -> int:
        """Total single-substitution variants."""
        return sum(a.singles for a in self.assays)

    @property
    def multis(self) -> int:
        """Total multi-substitution variants."""
        return sum(a.multis for a in self.assays)


def check_wild_type(
    assay_id: str,
    target_seq: str,
    mutants: Sequence[str],
    mutated_sequences: Sequence[str] | None = None,
    *,
    first_row: int = FIRST_DATA_ROW,
) -> list[VariantProblem]:
    """Check each mutant's wild-type residues (and optionally its mutated sequence).

    Args:
        assay_id: Used only to label problems.
        target_seq: The assay's wild-type sequence.
        mutants: ProteinGym mutant strings, in file order.
        mutated_sequences: The file's ``mutated_sequence`` column, aligned with ``mutants``.
        first_row: Row number of ``mutants[0]``, for reporting.

    Returns:
        Every problem found, in row order; empty when all rows pass.
    """
    if mutated_sequences is not None and len(mutated_sequences) != len(mutants):
        raise ValueError("mutants and mutated_sequences differ in length")
    problems: list[VariantProblem] = []
    for offset, mutant in enumerate(mutants):
        reasons: list[str] = []
        try:
            subs = parse_mutant(mutant)
        except MutationParseError as exc:
            subs, reasons = (), [str(exc)]
        for sub in subs:
            if sub.position >= len(target_seq):
                reasons.append(f"position past end of target_seq (length {len(target_seq)})")
            elif target_seq[sub.position] != sub.wt:
                reasons.append(
                    f"wild-type mismatch: target_seq has {target_seq[sub.position]} where the "
                    f"mutant says {sub.wt}"
                )
        if subs and not reasons and mutated_sequences is not None:
            expected = list(target_seq)
            for sub in subs:
                expected[sub.position] = sub.mt
            if "".join(expected) != mutated_sequences[offset]:
                reasons.append(
                    "mutated_sequence differs from target_seq with the mutations applied"
                )
        problems.extend(VariantProblem(assay_id, first_row + offset, mutant, r) for r in reasons)
    return problems


@dataclass(frozen=True)
class AssayInput:
    """One assay present in both the reference file and the DMS directory."""

    assay_id: str
    target_seq: str
    csv_path: Path


def pair_assays(
    reference_csv: Path, dms_dir: Path
) -> tuple[list[AssayInput], list[VariantProblem], int]:
    """Match reference rows to ``<DMS_id>.csv`` files one to one.

    Returns:
        The assays with a file and a canonical ``target_seq`` (sorted by id), a whole-file
        problem for every assay that lacks either, and the number of distinct assay ids seen.
    """
    with reference_csv.open(newline="") as fh:
        targets = {row["DMS_id"]: row["target_seq"] for row in csv.DictReader(fh)}
    files = {path.stem: path for path in sorted(dms_dir.glob("*.csv"))}

    problems: list[VariantProblem] = []
    problems.extend(
        VariantProblem(a, None, None, f"in reference but no {a}.csv in {dms_dir}")
        for a in sorted(targets.keys() - files.keys())
    )
    problems.extend(
        VariantProblem(a, None, None, "CSV has no entry in the reference file")
        for a in sorted(files.keys() - targets.keys())
    )
    assays: list[AssayInput] = []
    for assay_id in sorted(targets.keys() & files.keys()):
        target_seq = targets[assay_id]
        if not target_seq or set(target_seq) - set(AA_ORDER):
            problems.append(
                VariantProblem(assay_id, None, None, "target_seq has non-canonical residues")
            )
            continue
        assays.append(AssayInput(assay_id, target_seq, files[assay_id]))
    return assays, problems, len(targets.keys() | files.keys())


def raise_if_problems(problems: Sequence[VariantProblem], n_assays: int) -> None:
    """Log every problem, then raise :class:`VariantValidationError` if there is any."""
    for p in problems:
        log.error(
            "variant_problem", assay_id=p.assay_id, row=p.row, mutant=p.mutant, reason=p.reason
        )
    if problems:
        raise VariantValidationError(problems, n_assays)


def check_assays(reference_csv: Path, dms_dir: Path) -> VariantCheck:
    """Validate every assay CSV in ``dms_dir`` against the reference file's target sequences.

    Args:
        reference_csv: ProteinGym reference file (one row per assay: ``DMS_id``, ``target_seq``).
        dms_dir: Directory of per-assay CSVs named ``<DMS_id>.csv``.

    Raises:
        VariantValidationError: Any schema, parse, wild-type or sequence problem, or an assay
            present in only one of the reference and ``dms_dir``.
    """
    assays, problems, n_assays = pair_assays(reference_csv, dms_dir)

    summaries: list[AssaySummary] = []
    for assay in assays:
        summary, assay_problems = _check_assay(assay.assay_id, assay.target_seq, assay.csv_path)
        problems.extend(assay_problems)
        if not assay_problems:
            summaries.append(summary)

    raise_if_problems(problems, n_assays)
    check = VariantCheck(summaries)
    log.info(
        "variants_checked",
        assays=len(summaries),
        rows=check.rows,
        singles=check.singles,
        multis=check.multis,
    )
    return check


def _check_assay(
    assay_id: str, target_seq: str, path: Path
) -> tuple[AssaySummary, list[VariantProblem]]:
    df = pd.read_csv(path, dtype=str)
    extra = tuple(c for c in df.columns if c not in _RAW_COLUMNS)
    if extra:
        log.warning("extra_columns", assay_id=assay_id, columns=list(extra))

    problems, bad_mutant_rows = _schema_problems(assay_id, df)
    if "mutant" in df.columns:
        mutants = df["mutant"].fillna("").tolist()
        sequences = (
            df["mutated_sequence"].fillna("").tolist() if "mutated_sequence" in df.columns else None
        )
        problems.extend(
            p
            for p in check_wild_type(assay_id, target_seq, mutants, sequences)
            if p.row is not None
            and p.row - FIRST_DATA_ROW not in bad_mutant_rows  # already reported
        )
    problems.sort(key=lambda p: -1 if p.row is None else p.row)

    multis = int(df["mutant"].str.contains(":", regex=False).sum()) if "mutant" in df else 0
    summary = AssaySummary(assay_id, len(df), len(df) - multis, multis, extra)
    return summary, problems


def _schema_problems(assay_id: str, df: pd.DataFrame) -> tuple[list[VariantProblem], set[int]]:
    """Every RawVariantSchema failure as a problem, plus the rows whose ``mutant`` failed."""
    try:
        RawVariantSchema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        cases = cast("pd.DataFrame", exc.failure_cases)  # pyright: ignore[reportUnknownMemberType]
    else:
        return [], set()

    problems: list[VariantProblem] = []
    bad_mutant_rows: set[int] = set()
    seen: set[tuple[str, int]] = set()
    for column, check, index, value in zip(
        cases["column"], cases["check"], cases["index"], cases["failure_case"], strict=True
    ):
        if pd.isna(index):  # whole-column problem, e.g. a missing column
            problems.append(VariantProblem(assay_id, None, None, f"{column}: {check} ({value})"))
            continue
        row = int(index)
        if (str(column), row) in seen:  # a failed coercion is reported as coerce_dtype and dtype
            continue
        seen.add((str(column), row))
        if column == "mutant":
            bad_mutant_rows.add(row)
        mutant = df.at[row, "mutant"] if "mutant" in df.columns else None
        problems.append(
            VariantProblem(
                assay_id,
                FIRST_DATA_ROW + row,
                None if pd.isna(mutant) else str(mutant),
                f"{column}: {check} failed on {value!r}",
            )
        )
    return problems, bad_mutant_rows

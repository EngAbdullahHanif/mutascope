"""Turn raw ProteinGym assay CSVs into the processed single-mutant table.

Rows are removed for exactly one reason each, the first that applies (``DROP_REASONS``):

1. ``multi_mutant``: the ``mutant`` has more than one substitution (v1 is single mutants only).
2. ``missing_value``: any of ``mutant``, ``mutated_sequence``, ``DMS_score``, ``DMS_score_bin`` is
   empty. Without ``mutated_sequence`` the row cannot be checked independently, so it goes too.
3. ``duplicate``: the same single mutant appeared earlier in the assay with the same score and
   label. The first copy is kept.
4. ``conflicting_duplicate``: the same single mutant appears more than once with different
   scores or labels. No copy is kept, because picking or averaging one would be a silent fix.

Everything else is an error, never a drop (CLAUDE.md data rule 3): a wild-type mismatch, a
``mutated_sequence`` that disagrees with the mutant, an unparseable mutant, a non-numeric or
non-finite score, or a label outside {0, 1}. Such problems are collected across all assays and
raised together, and no output is written.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from mutascope.data.parse import parse_mutation
from mutascope.data.schemas import DROP_REASONS, CleaningReportSchema, ProcessedVariantSchema
from mutascope.data.validate import (
    FIRST_DATA_ROW,
    VariantProblem,
    check_wild_type,
    pair_assays,
    raise_if_problems,
)
from mutascope.utils.io import fresh_tmp_dir, replace_dir
from mutascope.utils.io import write_partition as write_validated_partition
from mutascope.utils.logging import get_logger

log = get_logger(__name__)

REQUIRED_COLUMNS = ("mutant", "mutated_sequence", "DMS_score", "DMS_score_bin")


@dataclass(frozen=True)
class AssayCleaning:
    """Row counts for one assay; ``rows_in == rows_out + sum(dropped.values())``."""

    assay_id: str
    rows_in: int
    rows_out: int
    dropped: dict[str, int]


@dataclass(frozen=True)
class CleanedAssay:
    """Result of :func:`clean_assay`: the processed rows, or the problems that prevent them."""

    variants: pd.DataFrame | None
    counts: AssayCleaning | None
    problems: list[VariantProblem] = field(default_factory=list[VariantProblem])


def clean_assay(assay_id: str, target_seq: str, raw: pd.DataFrame) -> CleanedAssay:
    """Clean one assay read with every column as text (``pd.read_csv(..., dtype=str)``).

    Args:
        assay_id: ProteinGym ``DMS_id``.
        target_seq: The assay's wild-type sequence from the reference file.
        raw: The assay CSV with its default 0-based index (row ``i`` is CSV line ``i + 2``).

    Returns:
        The rows matching :class:`ProcessedVariantSchema` (not yet validated) and the counts per
        drop reason, or, if any row is malformed, only the list of problems.
    """
    absent = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if absent:
        problem = VariantProblem(assay_id, None, None, f"missing columns {absent}")
        return CleanedAssay(None, None, [problem])

    mutant = raw["mutant"].fillna("").astype(str)
    reason = pd.Series("", index=raw.index, dtype="str")  # "" = kept so far
    reason[mutant.str.contains(":", regex=False)] = "multi_mutant"
    reason[(reason == "") & raw[list(REQUIRED_COLUMNS)].isna().any(axis=1)] = "missing_value"

    score = _parse_floats(raw["DMS_score"])
    label = pd.to_numeric(raw["DMS_score_bin"], errors="coerce")
    problems = _wild_type_problems(assay_id, target_seq, raw)
    problems += _value_problems(assay_id, raw, score, label)
    if problems:
        problems.sort(key=lambda p: -1 if p.row is None else p.row)
        return CleanedAssay(None, None, problems)

    pending = reason == ""
    singles = pd.DataFrame(
        {"mutant": mutant[pending], "DMS_score": score[pending], "DMS_score_bin": label[pending]}
    )
    distinct = singles.drop_duplicates()
    conflicting = singles["mutant"].isin(distinct["mutant"][distinct["mutant"].duplicated()])
    duplicate = ~conflicting & singles["mutant"].duplicated()
    reason[conflicting[conflicting].index] = "conflicting_duplicate"
    reason[duplicate[duplicate].index] = "duplicate"
    conflict_rows: dict[str, list[int]] = {}
    for i, text in singles["mutant"][conflicting].items():
        conflict_rows.setdefault(str(text), []).append(int(str(i)) + FIRST_DATA_ROW)
    for text, rows in conflict_rows.items():
        log.warning("conflicting_duplicate", assay_id=assay_id, mutant=text, rows=rows)

    kept = singles[~conflicting & ~duplicate]
    texts = [str(m) for m in kept["mutant"]]
    subs = [parse_mutation(m) for m in texts]
    variants = pd.DataFrame(
        {
            "assay_id": pd.array([assay_id] * len(texts), dtype="str"),
            "mutant": pd.array(texts, dtype="str"),
            "position": np.array([s.position for s in subs], dtype=np.int64),
            "wt_aa": pd.array([s.wt for s in subs], dtype="str"),
            "mt_aa": pd.array([s.mt for s in subs], dtype="str"),
            "DMS_score": kept["DMS_score"].to_numpy(dtype=np.float64),
            "DMS_score_bin": kept["DMS_score_bin"].to_numpy(dtype=np.int64),
        }
    )
    dropped = {r: int((reason == r).sum()) for r in DROP_REASONS}
    counts = AssayCleaning(assay_id, len(raw), len(variants), dropped)
    log.info(
        "assay_cleaned",
        assay_id=assay_id,
        rows_in=counts.rows_in,
        rows_out=counts.rows_out,
        multi_mutants_removed=dropped["multi_mutant"],
        **{r: n for r, n in dropped.items() if r != "multi_mutant" and n},
    )
    return CleanedAssay(variants, counts)


def _parse_floats(text: "pd.Series[str]") -> "pd.Series[float]":
    """Parse decimal text exactly; missing or non-numeric text becomes NaN.

    Not ``pd.to_numeric``: its fast parser is not correctly rounded and ignores digits past
    about the 16th, so 3,124 ProteinGym v1.3 scores came out wrong (1,936 by one ulp, the worst
    by 9.3e-13 relative). Python's ``float`` rounds correctly, but also accepts ``"1_0"`` and
    padding, which are rejected here so they fail as malformed.
    """

    def parse(value: object) -> float:
        value = str(value)
        if "_" in value or value != value.strip():
            return np.nan
        try:
            return float(value)
        except ValueError:
            return np.nan

    return pd.Series(
        [np.nan if pd.isna(v) else parse(v) for v in text], index=text.index, dtype=np.float64
    )


def _wild_type_problems(assay_id: str, target_seq: str, raw: pd.DataFrame) -> list[VariantProblem]:
    """Wild-type and ``mutated_sequence`` problems on every row that has both columns.

    Multi-mutants are checked too: they are dropped afterwards, but a wrong wild type in one
    still means the assay's numbering is off.
    """
    checkable = raw["mutant"].notna() & raw["mutated_sequence"].notna()
    problems = check_wild_type(
        assay_id,
        target_seq,
        raw["mutant"].fillna("").astype(str).tolist(),
        raw["mutated_sequence"].fillna("").astype(str).tolist(),
    )
    ok_rows = set((raw.index[checkable] + FIRST_DATA_ROW).tolist())
    return [p for p in problems if p.row in ok_rows]


def _value_problems(
    assay_id: str, raw: pd.DataFrame, score: "pd.Series[float]", label: "pd.Series[float]"
) -> list[VariantProblem]:
    """A score that is present but not a finite number, or a label that is not 0 or 1."""
    bad_score = raw["DMS_score"].notna() & ~np.isfinite(score.fillna(np.inf))
    bad_label = raw["DMS_score_bin"].notna() & ~label.isin([0, 1])
    problems: list[VariantProblem] = []
    for column, bad, expected in (
        ("DMS_score", bad_score, "a finite number"),
        ("DMS_score_bin", bad_label, "0 or 1"),
    ):
        for i in raw.index[bad]:
            mutant = raw.at[i, "mutant"]
            problems.append(
                VariantProblem(
                    assay_id,
                    int(i) + FIRST_DATA_ROW,
                    None if pd.isna(mutant) else str(mutant),
                    f"{column}: expected {expected}, got {raw.at[i, column]!r}",
                )
            )
    return problems


def clean_dataset(reference_csv: Path, dms_dir: Path, out_dir: Path) -> pd.DataFrame:
    """Clean every assay and write ``out_dir/assay_id=<id>/data.parquet``.

    Each partition is validated against :class:`ProcessedVariantSchema` before it is written.
    The partitions are built in ``<out_dir>.tmp`` and moved into place only after every assay
    has passed, so ``out_dir`` never holds a partial or stale mix.

    Returns:
        The cleaning report, one row per assay, validated against
        :class:`CleaningReportSchema`.

    Raises:
        VariantValidationError: Any assay/CSV mismatch or malformed row, across all assays.
    """
    assays, problems, n_assays = pair_assays(reference_csv, dms_dir)
    tmp = fresh_tmp_dir(out_dir)
    counts: list[AssayCleaning] = []
    with duckdb.connect() as con:
        for assay in assays:
            raw = pd.read_csv(assay.csv_path, dtype=str)
            cleaned = clean_assay(assay.assay_id, assay.target_seq, raw)
            problems.extend(cleaned.problems)
            if cleaned.variants is None or cleaned.counts is None or problems:
                continue  # keep checking the rest, but write nothing more
            write_partition(con, cleaned.variants, tmp / f"assay_id={assay.assay_id}")
            counts.append(cleaned.counts)
    try:
        raise_if_problems(problems, n_assays)
        report = cleaning_report(counts)
    except BaseException:
        shutil.rmtree(tmp)
        raise
    replace_dir(tmp, out_dir)
    log.info(
        "dataset_cleaned",
        assays=len(report),
        rows_in=int(report["rows_in"].sum()),
        rows_out=int(report["rows_out"].sum()),
        out_dir=str(out_dir),
    )
    return report


def write_partition(con: duckdb.DuckDBPyConnection, variants: pd.DataFrame, part_dir: Path) -> None:
    """Validate one assay's rows, then write them to ``part_dir/data.parquet``."""
    ProcessedVariantSchema.validate(variants, lazy=True)
    write_validated_partition(con, variants, part_dir)


def cleaning_report(counts: list[AssayCleaning]) -> pd.DataFrame:
    """Build and validate the report table (columns in :class:`CleaningReportSchema` order)."""
    report = pd.DataFrame(
        [
            {"assay_id": c.assay_id, "rows_in": c.rows_in, **c.dropped, "rows_out": c.rows_out}
            for c in counts
        ],
        columns=["assay_id", "rows_in", *DROP_REASONS, "rows_out"],
    )
    # Explicit dtypes: an empty frame would otherwise get float64 columns.
    report = report.astype({c: "str" if c == "assay_id" else np.int64 for c in report.columns})
    return CleaningReportSchema.validate(report, lazy=True)

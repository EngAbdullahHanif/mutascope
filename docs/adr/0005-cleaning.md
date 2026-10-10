# 0005 — Cleaning to the processed single-mutant table

- Status: accepted
- Date: 2026-10-10

## Context

v1 models single mutants only (CLAUDE.md data rule 4). The raw ProteinGym CSVs mix single and
multi-mutants, and a future release may contain missing values or repeated mutants. Cleaning
must remove rows without hiding why, and must never "repair" a row (data rule 3).

## Decision

- **One reason per removed row**, the first that applies: `multi_mutant`, `missing_value`,
  `duplicate`, `conflicting_duplicate`. So `rows_in == rows_out + sum(reasons)` holds per
  assay, and `CleaningReportSchema` enforces it.
- **Missing value** means any of `mutant`, `mutated_sequence`, `DMS_score`, `DMS_score_bin` is
  empty. A row without `mutated_sequence` cannot get the independent sequence check from
  ADR 0004, so it is removed even though that column is not stored.
- **Duplicates.** An exact repeat (same mutant, score and label) keeps its first copy. A mutant
  repeated with different values keeps *no* copy and is logged with its rows: choosing one or
  averaging would be a silent fix.
- **Errors are not drops.** Wild-type mismatches, `mutated_sequence` disagreements, unparseable
  mutants (including multi-mutants), non-finite scores and labels outside {0, 1} fail the run
  with assay id and CSV row, across all assays at once, and nothing is written.
- **Exact score parsing.** The first version of `clean.py` read each CSV with
  `pd.read_csv(path, dtype=str)` and converted scores with

  ```python
  score = pd.to_numeric(raw["DMS_score"], errors="coerce")
  ```

  That conversion is not correctly rounded and ignores digits past about the 16th. It
  mis-parsed 3,124 of 696,311 v1.3 scores in 95 assays (1,936 by one ulp, the worst by 9.3e-13
  relative). The line was fixed before the first commit, so it is not in git history. The
  attribution was checked by rerunning that code path step by step on v1.3:
  `read_csv(dtype=str)` keeps every score's text exactly (0 differ from the file), the line
  above alone produces the 3,124 wrong values, and `.astype(float)` on the same strings gets
  all of them right. pandas' default `read_csv` float parsing has the same flaw (the same 3,124
  wrong), so it is no alternative. Scores are now parsed with Python's correctly rounded
  `float`, and the processed scores equal the raw text bit for bit.
- **Layout.** `variants/assay_id=<id>/data.parquet`, written with DuckDB (no pyarrow dependency),
  `assay_id` stored only in the partition path. Each partition is validated against
  `ProcessedVariantSchema` before writing; partitions are built in `variants.tmp` and swapped in
  only after every assay passes. `mutascope.data.db.connect` exposes `variants`,
  `cleaning_report` and `assays` (the reference file) as DuckDB views.
- **Report.** `results/data/cleaning_report.parquet` plus a Markdown rendering of the same
  table, with no timestamps or local paths, so reruns are byte-identical.

## Consequences

- On v1.3: 217 assays, 2,465,767 rows in, 1,769,456 multi-mutants removed (69 assays have
  any), 0 missing values, 0 duplicates, 696,311 single mutants out. Per assay, `rows_out`
  equals `DMS_number_single_mutants` in the reference file, and the mutant sets equal
  ProteinGym's `cv_folds_singles` files.
- The `cv_folds_singles` files are **not** a source of scores or labels: they print scores with
  one digit fewer (3,297 values differ, ≤ 9e-13 relative) and have empty `DMS_score_bin` in 6
  assays. Phase 3 joins fold assignments on `(assay_id, mutant)` and takes values from the
  processed table.

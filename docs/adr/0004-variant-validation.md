# 0004 — Variant parsing and validation

- Status: accepted
- Date: 2026-10-10

## Context

Each ProteinGym assay CSV gives mutants as 1-indexed strings (`H24C`, or `K2A:V3A` for
multi-mutants), plus `mutated_sequence`, `DMS_score` and `DMS_score_bin`. A wrong offset between
the mutant numbering and `target_seq` produces a model that looks plausible but scores the wrong
residues. CLAUDE.md data rules 2, 3 and 7 require one place for index conversion, a loud
wild-type check, and a pandera schema for every table.

## Decision

- **One conversion.** `data.parse.parse_mutation` is the only code that turns a 1-indexed position
  into a 0-indexed one. There is deliberately no formatter back to strings, so no second
  conversion exists. The parser accepts exactly `<wt><pos><mt>`: canonical residues
  (`mutascope.alphabet.AA_ORDER`), a positive position with no leading zeros, no whitespace.
  It rejects synonymous `wt == mt`, which is not a substitution and would score 0 by construction.
  `parse_mutant` handles `:`-joined multi-mutants and rejects repeated positions.
- **What `mutascope data check-variants` checks**, for every row of every assay, singles and
  multi-mutants alike:
  1. `RawVariantSchema` (the mutant grammar, a canonical `mutated_sequence`, a numeric non-null
     `DMS_score`, `DMS_score_bin` ∈ {0, 1});
  2. every position lies inside `target_seq`, and `target_seq` has the stated wild-type residue;
  3. `mutated_sequence` equals `target_seq` with the mutations applied.
  It also checks that assays and CSV files match one to one. Check 3 is not redundant: in a test
  that shifted one target by one residue, 399 of 4,996 rows still had matching wild types by
  chance, and only check 3 caught them.
- **Reporting.** Every problem names the assay id and its CSV row (header = row 1). All assays
  are checked before failing, so one run lists every problem. Nothing is fixed or dropped.
- **Raw is non-strict, processed is strict.** Upstream files may carry extra columns:
  `ANCSZ_Hobbs_2022` has `DMS_bin_score`, identical to `DMS_score_bin` on all rows. These are
  reported but tolerated. `ProcessedVariantSchema`, which MutaScope writes itself, allows exactly
  its own columns and requires `position`/`wt_aa`/`mt_aa` to equal `parse_mutation(mutant)`.
- **pandas backend for pandera.** It is the most mature backend, and the tables fit in memory
  one assay at a time.

## Consequences

- On ProteinGym v1.3, all 217 assays (2,465,767 variants: 696,311 single, 1,769,456 multi) pass
  with 0 problems.
- A future release with a numbering change fails this check with a row-level list. Any fix must
  be an explicit, documented decision, never a silent correction.
- The cleaning step (single mutants only, validated on write) builds on `ProcessedVariantSchema`.

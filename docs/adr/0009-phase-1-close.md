# 0009 — Phase 1 close: foundations and data engineering

- Status: accepted
- Date: 2026-10-10

## Context

Phase 1 (weeks 1–2) had one goal: a single command that turns the raw ProteinGym download into
clean, validated, versioned data plus structures and 3Di tokens. Its done-when criteria were
`make pipeline` producing clean Parquet, structure features and 3Di, `make lint test` green, and
a data card written. All three are met. This record summarises what exists now and what Phase 2
builds on. Details are in ADRs 0001–0008 and build-log entries 1–7.

## What Phase 1 produced

**Data (ProteinGym v1.3, pinned to Zenodo 10.5281/zenodo.15293562 and git commit `1f8de97`)**

| | |
|---|---:|
| Raw variant rows checked (schema, wild type, `mutated_sequence`) | 2,465,767, 0 problems |
| Multi-mutants removed (69 assays) | 1,769,456 |
| Single mutants kept (217 assays, 186 UniProt IDs) | 696,311 |
| Assays with structure features and 3Di (185 PDB files) | 213 of 217 |
| Residues with structure features / 3Di | 77,338 / 77,338 |
| Documented structure exceptions (CAS9 tag, three P53 R72P) | 4 |

**Timings (M4 Pro).** `make pipeline` takes 42 s on all 217 assays with the download cached.
`make pipeline-test` (2 assays) takes 32 s from an empty cache. `make test` runs 185 tests
(1 skipped without Foldseek) in about 10 s with 97% line coverage. Staging the Hub dataset
takes about 3 s.

**In git.** The `mutascope` package (data pipeline, EDA summaries and charts, Hub export), the
Nextflow pipeline and CI, Hydra configs (`data`, `structure`, `publish`), `data/manifest.yaml`
(sha256 of every downloaded file), the validation, cleaning and structure reports in
`results/data/`, the marimo notebook `notebooks/eda.py`, `docs/DATA_CARD.md`, ADRs 0001–0009,
the build log and an MIT `LICENSE`. The data itself is not in git: it lives under the gitignored
`data/` and is rebuilt by `make pipeline`.

**On the Hub, once pushed** (`mutascope data publish --push`, a new private repo): `variants`
(696,311 rows, 8.6 MB), `assays` (217), `structure_features` (77,338) and `three_di` (77,338) as
one Parquet file each with `assay_id` as a column, plus the cleaning and structure reports, the
data card as `README.md`, `provenance.json` and `SHA256SUMS`. MIT licence, as for ProteinGym.

## Decisions that mattered most

1. **Fail loudly, fix nothing (ADRs 0003–0005).** Every check reports all problems with assay and
   row, and nothing is repaired or silently dropped. This rule surfaced the 4 upstream structure
   mismatches and a pandas parsing flaw that would have altered 3,124 scores. Its three
   independent variant checks also catch a deliberately shifted assay even where 399 of its
   4,996 rows still match the wild type by coincidence.
2. **A checksum chain from Zenodo to git (ADR 0002).** Each file must match Zenodo's md5 on
   first download. Its sha256 is then committed, and every run re-verifies it. A fresh clone
   gets exactly the bytes we benchmarked, or a clear error, which rules data drift out when the
   reproduction gate is checked.
3. **One CLI, orchestrated but never bypassed (ADRs 0001, 0007).** Every step is a `mutascope`
   command reading and writing paths; Nextflow only calls them, and one Hydra override list
   reaches all of them. The 2-assay test profile runs the real steps in CI, and the pipeline's
   output is byte-identical to the hand-run outputs.

## What Phase 2 inherits

- **Inputs:** the processed `variants` table and the DuckDB views (`mutascope.data.db.connect`),
  `AA_ORDER`, and `parse_mutation` as the only place 1-based positions become 0-based.
- **For SaProt:** per-residue 3Di and pLDDT for 213 assays (mask 3Di as `#` where pLDDT < 70;
  23% of residues are below it). Structure-based results cover 213 assays, sequence-only 217,
  and must be labelled that way.
- **For the reproduction gate:** ProteinGym's zero-shot scores (12 columns, 150 MB Parquet) and
  the pinned leaderboard. The target is ESM-2 650M average Spearman 0.414 ± 0.01.
- **Known data issues:** 16 targets exceed 1,022 residues and need sliding windows.
  `F7YBW8_MESOW_Aakre_2015` has all binary labels equal to 1, so AUC and MCC are undefined for it.
  ProteinGym's median cutoffs include multi-mutants, which skews single-mutant labels towards 1
  in 32 assays.
- **Still open from Phase 1:** the `docker` pipeline profile (waits for the Phase 4 image) and
  the Hub upload (after the data card review).

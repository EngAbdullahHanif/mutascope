# MutaScope build log

A technical journal of how MutaScope was built, one entry per completed task, in chronological
order. Commit hashes refer to this repository's history.

Every entry has these sections, in this order:

1. **Plain-language summary:** what was built and why, readable without the code.
2. **The 3 most important design choices:** each with the reasoning and the evidence behind it.
3. **Problems encountered and how they were solved:** a numbered list. Each item gives the
   problem in one line, how it was found, the fix, and how the fix was verified, with the actual
   numbers. If nothing qualifies, the section says "None worth documenting." Problems are never
   invented to fill it.
4. **Supporting details:** results, dependencies, and loose ends. Entry 1 calls this section
   "Context (from the author)".

---

## Entry 1 — Repository skeleton and tooling baseline (Phase 1 setup, 2026-10-09)

**Plain-language summary.** Before any science, the project got a fixed shape and a set of
automatic quality checks. One tool (uv) installs exactly the same Python packages on any machine,
and one file of short commands (the Makefile) covers setup, linting, testing, running the pipeline
and starting the API. All functionality is reached through a single command-line program,
`mutascope`, and a workflow engine (Nextflow) is set up to chain its commands together later; for
now it is an empty stub. Every commit is automatically checked for formatting, type errors and
stray `print` statements, and a small test suite confirms the package installs and the CLI and API
respond. The reasoning behind the tool choices is recorded in ADR 0001. Everything landed in a
single commit, `0742ede` (`chore: scaffold repository skeleton`).

**The 3 most important design choices.**

1. **One CLI as the only way into the code:** every pipeline step will be a `mutascope` command
   that reads input files and writes output files, and Nextflow may only call those commands. Any
   step can therefore be run, tested and debugged on its own, without the workflow engine.
2. **Separate `local` and `docker` pipeline profiles:** Docker on macOS cannot reach the Apple GPU
   (MPS), so model work runs natively from the uv environment (`local` puts `.venv/bin` on `PATH`),
   while the `docker` profile builds for linux/amd64 to match the Hugging Face Spaces deployment.
3. **Strict checks that run the locked tool versions:** pyright strict, ruff (with `print` banned in
   favour of structured logging) and pytest all run from the uv environment through pre-commit, so
   local hooks, `make lint` and CI behave identically. Tests that need the large real models are
   marked `slow` and excluded by default so `make test` stays under two minutes.

**Problems encountered and how they were solved.**

1. **A `.gitignore` rule hid source code.** *Found:* source files under `src/mutascope/data/`
   and `conf/data/` were missing from `git status`. *Cause:* the first `.gitignore` had a bare
   `data/` rule, and Git applies a pattern without a leading slash at every depth, so it ignored
   every directory named `data`, not only the top-level data folder. *Fix:* anchor the rule as
   `/data/`, so it matches only the top-level directory. *Verified:* the scaffold commit
   `0742ede` tracks `src/mutascope/data/__init__.py` and `conf/data/.gitkeep`.

**Context (from the author).** The scaffold was squashed into a single commit, so the work before
it is not in git; these notes record it.
- *Alternatives considered:* Poetry and pip-tools instead of uv, and Black + isort + flake8 instead
  of ruff. uv and ruff won because they are much faster and each replaces several tools: uv handles
  the Python version, virtual environment, dependency resolution and lockfile, and ruff does
  formatting, import sorting and linting in one pass.

---

## Entry 2 — ProteinGym v1.3 data acquisition (Phase 1, 2026-10-09)

**Plain-language summary.** MutaScope learns from ProteinGym, a public benchmark of laboratory
experiments measuring how single amino-acid changes affect a protein's function. One command,
`mutascope data download`, fetches a pinned release (v1.3) of every file the project needs: the
assay list, the measurements, the cross-validation splits, predicted 3D structures, other models'
published scores and the leaderboard. Each file is checked against the fingerprint (md5) published
on Zenodo before it is accepted, then its own sha256 fingerprint is committed to git so every later
run can prove nothing has changed. The 1.9 GB file of published scores, which covers about 100
models, is reduced to the 12 columns we use and stored as 150 MB of Parquet. A second command,
`mutascope data check-structures`, confirms that each experiment's 3D structure really has the same
protein sequence as the experiment. Four assays fail that check because of problems in the
upstream data, and they are listed in the config with their reasons rather than dropped quietly.

**The 3 most important design choices.**

1. **Every check fails loudly and nothing is repaired automatically:** a checksum mismatch, an
   unparseable value or a structure that doesn't match its sequence stops the run with a message
   naming the file or assay, because silent data errors (a shifted position, a structure paired
   with the wrong sequence) are the usual cause of results that look too good, and this rule is
   what surfaced the four upstream mismatches.
2. **A checksum chain from Zenodo to git:** files come from the Marks lab mirror, but the first
   download must match the md5 on Zenodo (archived, DOI-backed, cannot change), and from then on
   the sha256 in the committed `data/manifest.yaml` is the reference. A fresh clone either gets
   exactly the bytes we benchmarked on or a clear error, which rules the data out when the ESM-2
   reproduction gate (published 0.414, tolerance ±0.01) is checked (ADR 0002).
3. **Known data problems are written down and the list must stay accurate:** the four failing
   assays (CAS9: the PDB includes a 22-residue tag; three P53 Giacomelli assays: the natural R72P
   variant) are listed in `conf/data/proteingym.yaml` with reasons, are left out of structure-based
   models only, and the check fails if a listed assay starts mapping or disappears from the
   reference. That keeps structure-based (213 assays) and sequence-only (217) results honestly
   labelled (ADR 0003).

**Problems encountered and how they were solved.**

1. **4 of 217 assays have a structure that doesn't match their sequence.** *Found:*
   `check-structures` on the real download mapped 213 assays and failed 4. *Cause:* upstream
   data, not our code. `CAS9_STRP1_Spencer_2017_positive`'s PDB carries a 22-residue C-terminal
   tag, so its `pdb_range` (1-1390) runs past the 1,368-residue target. Three
   `P53_HUMAN_Giacomelli_2018` assays have R72 in the target but P72 (a natural variant) in the
   shared PDB. *Fix:* an explicit `structure_exceptions` list in the config, each entry with a
   written reason. These assays are left out of structure-based models only. We rejected
   dropping them silently, editing the PDB, or failing the whole pipeline (ADR 0003).
   *Verified:* the check reports 213 mapped and 4 allowed exceptions, and fails if a listed
   assay starts mapping or disappears from the reference, so the list cannot go stale.
2. **Logging crashed on the second CLI call in a test.** *Found:* invoking the CLI twice with
   Typer's `CliRunner` failed with "I/O operation on closed file". *Cause:* structlog caches
   loggers on first use, and the logger factory was bound to the `sys.stderr` object present at
   configure time. The test runner later swapped out and closed that stream, but the cached
   logger kept writing to it. *Fix:* a small proxy that looks up the current `sys.stderr` on
   every write (`ee1809e`). *Verified:* a regression test in `tests/test_logging.py`.
3. **The Nextflow `test` profile could never pass the structure check.** *Found:* while
   setting up the 2-assay smoke-test profile. *Cause:* the check fails on any listed exception
   that is missing from the reference, and a 2-assay subset never contains the 4 listed assays.
   *Fix:* a `params.hydra_overrides` hook appended to every `mutascope` command, set to
   `data.structure_exceptions=null` in the `test` profile (`d89eb00`). *Verified:* on a
   2-assay subset of the real reference, `check-structures` exits 1 without the override and
   0 with it.

**Supporting details.**
- *Verified on the real download:* every file matched Zenodo; a second run downloaded nothing; all
  217 zero-shot assays have row counts equal to their DMS files (2,465,767 rows) with no missing
  ESM2_650M or SaProt_650M_AF2 scores; 213 assays map to structures, plus the 4 allowed exceptions.
- *Committing the manifest:* `.gitignore` changed from `/data/` to `/data/*` plus
  `!/data/manifest.yaml` (`2cbd89b`), so the checksums are versioned while the data stays out of
  git. The switch to `/data/*` is required because Git cannot re-include a file whose parent
  directory is itself excluded.
- *Crash safety:* downloads go to `.part` files and extractions to `.tmp` directories, and are
  renamed only when complete and verified, so a file at its final path is always complete and
  verified.
- *Tests run offline:* a mock HTTP server and zips built during the test keep `make test` fast and
  CI-safe.

---

## Entry 3 — Mutation parsing and variant validation (Phase 1, 2026-10-10)

**Plain-language summary.** Each ProteinGym experiment lists its protein variants as short codes
such as `H24C`: "position 24 has histidine (H); replace it with cysteine (C)". Positions count
from 1, as biologists do, but Python counts from 0. If the two are ever confused, a model quietly
scores the residue next to the right one. A single function, `parse_mutation`, now reads these
codes and does the 1-to-0 conversion; no other code does. A new command,
`mutascope data check-variants`, then checks every one of the 2,465,767 variants in all 217
experiments in three ways: the row is well formed, the stated original residue is really at
that position in the protein, and the full mutated sequence in the file matches the protein with
the change applied. All three checks passed on every row, with 0 problems. To show that the
check can fail, a test deliberately shifts one protein's sequence by one position and confirms
that every row of that experiment is flagged. ADR 0004 records the rules.

**The 3 most important design choices.**

1. **Exactly one index conversion, with no way back:** `data.parse.parse_mutation` is the only
   place a 1-based position becomes 0-based (`a3322d7`), and no function formats a 0-based
   position back into a code. An off-by-one error can therefore only live in one line, and that
   line is covered by hypothesis property tests over random residues and positions up to 10⁶.
   The parser is strict on purpose. It rejects lowercase, non-canonical residues, position 0,
   leading zeros, whitespace and "substitutions" that change nothing (`A12A`), because a lenient
   parser is where silent misreadings start.
2. **Three independent checks, because one is not enough:** each row passes a pandera schema
   (`0b86b9f`), a wild-type check against `target_seq`, and a check that `mutated_sequence`
   equals `target_seq` with the mutations applied (`a9d1042`). The third check looks redundant,
   but it is not. With one real assay (BLAT_ECOLX_Deng_2012) shifted by a single residue, 399 of
   its 4,996 rows still had a matching original residue by coincidence, and only the sequence
   comparison caught them. A committed regression test (`78e43f3`) uses a small three-assay
   fixture to pin this down: one assay is shifted, every one of its rows must be reported, the
   other two must not be, and the rows that match by coincidence must still be caught.
3. **Report everything, fix nothing, and be strict only about our own data:** the checker reads
   all assays before failing and lists every problem with its assay id and CSV row; it never
   corrects or drops a row. The raw schema describes files we do not control, so it accepts
   extra columns and reports them. `ANCSZ_Hobbs_2022` has one: `DMS_bin_score`, identical to
   `DMS_score_bin` on all 4,670 rows. The processed schema describes the table MutaScope will
   write, so it allows only its own columns. It also requires the stored position and residues
   to equal what `parse_mutation` returns for the mutant code, so the two can never drift apart.

**Problems encountered and how they were solved.**

1. **The wild-type check alone misses a shifted sequence on some rows.** *Found:* by
   deliberately shifting one real assay's target sequence (BLAT_ECOLX_Deng_2012) by one residue
   to confirm the checker fails. 399 of its 4,996 rows still had the stated original residue at
   the stated position, by coincidence. *Fix:* the `mutated_sequence` comparison stays a
   required third check, even though it looks redundant. It caught all 399. *Verified:* a
   committed regression test (`78e43f3`) with a three-assay fixture. Repeated residues make 6
   rows of the shifted assay match by chance, and the test asserts that every row of that assay
   is reported and no row of the other two is.
2. **An upstream file has an extra column.** *Found:* validating the real v1.3 download, where
   `ANCSZ_Hobbs_2022` carries `DMS_bin_score` next to `DMS_score_bin`. *Fix:* the raw schema is
   non-strict and the checker reports extra columns instead of failing, because we don't control
   upstream files. The processed schema, which describes our own output, stays strict.
   *Verified:* the column equals `DMS_score_bin` on all 4,670 rows, and `check-variants` lists
   it in its summary.

**Supporting details.**
- *Result on v1.3:* 217 assays, 2,465,767 variants (696,311 single, 1,769,456 multi-mutant),
  0 problems, in about 16 s. Multi-mutant rows are checked too, even though v1 filters them out
  later, because a wrong residue in a multi-mutant still signals a numbering problem.
- *Dependencies:* pandera and pandas were added (`6b03273`), plus pandas-stubs so pyright strict
  can type-check pandas code.
- *One shared alphabet:* `AA_ORDER` now lives in `mutascope.alphabet`, so the Phase 2 scorers
  will import it instead of defining their own.
- *Not done yet:* filtering to single mutants and writing processed Parquet (validated against
  `ProcessedVariantSchema`) belong to the cleaning task that comes next.

---

## Entry 4 — Cleaning to the processed single-mutant table (Phase 1, 2026-10-10)

**Plain-language summary.** The raw ProteinGym files mix variants with one amino-acid change
and variants with several. Version 1 of MutaScope models only single changes, so a new command,
`mutascope data clean`, builds the table every later step reads: one row per single-change
variant, with its position, original and new residue, measured score and fit/unfit label. It
removes rows for four possible reasons (multiple changes, a missing value, an exact duplicate,
or the same variant listed twice with different measurements), and it records how many rows
each experiment lost for each reason. It never removes a row that looks wrong, such as one
whose stated original residue isn't in the protein; it stops and lists every such row instead.
On ProteinGym v1.3 it reads 2,465,767 rows from 217 experiments, removes 1,769,456
multi-change variants, and keeps 696,311 single-change variants. No row had a missing value or
a duplicate. The output is written as one Parquet file per experiment, with DuckDB views for
querying, plus a report in Parquet and Markdown (`results/data/`). ADR 0005 records the rules.

**The 3 most important design choices.**

1. **Every removed row is counted under exactly one reason:** the first that applies, in a
   fixed order (`multi_mutant`, `missing_value`, `duplicate`, `conflicting_duplicate`). So, for
   every experiment, rows in = rows out + rows removed. The report's pandera schema
   (`CleaningReportSchema`) enforces that equation, so a report that doesn't add up cannot be
   written. Anyone auditing the data can account for every row without trusting the code.
2. **Errors stop the run, and output is all or nothing:** a wrong original residue, a
   `mutated_sequence` that disagrees with the mutation, an unreadable mutation code, a
   non-numeric score or a label other than 0/1 fails the run. All experiments are checked first,
   so one run lists every problem with its experiment and CSV row. Each experiment's table is
   validated against `ProcessedVariantSchema` before it is written, and the output is built in
   a temporary folder that replaces the old one only after all 217 pass (`41f7fc4`). A failed
   run therefore leaves the previous good output untouched.
3. **Check the output against sources we didn't produce:** our counts were compared with two
   independent upstream sources. Per experiment, rows in, multi-mutants removed and rows out
   equal the totals published in ProteinGym's reference file. The set of kept variants equals
   ProteinGym's own single-mutant cross-validation files exactly, with none missing and none
   extra. Comparing scores as well, not just counts, is what exposed the parsing bug below.

**Problems encountered and how they were solved.**

1. **3,124 of 696,311 scores were silently changed when text was turned into numbers.**
   *Found:* by comparing our cleaned scores with the same variants in ProteinGym's CV-fold
   files. At first 771 differed, by at most 4.9 × 10⁻¹⁶ relative. For the examples checked, both
   upstream files had identical text (e.g. `CAPSD_AAV2S_Sinai_2021` R585V:
   `3.5267435205148114`), so the error was ours. *Cause:* one line in the first version of
   `clean.py`, which read each CSV as text with `pd.read_csv(path, dtype=str)` and then did
   `score = pd.to_numeric(raw["DMS_score"], errors="coerce")`. That parser is not correctly
   rounded and ignores digits past about the 16th. It turned
   `3.5267435205148114` into `3.526743520514811`, the neighbouring float, and
   `-0.00012111549999999999` (`HSP82_YEAST_Cote-Hammarlof_2020_growth-H2O2` F322A) into
   `-0.0001211154999999`. Measured against exact parsing, it got 3,124 scores in 95 assays
   wrong: 1,936 by one ulp, the worst by 9.3 × 10⁻¹³ relative. The cross-check had shown only
   771 because 2,799 of the wrong values happened to equal the fold files' shorter printed
   values, which hid the error. These sizes cannot move a Spearman correlation, but the
   processed table must hold exactly what ProteinGym published. *Fix:* scores are parsed from the
   raw text with Python's correctly rounded `float()`, with underscores and padding rejected
   (`float()` accepts both) (`41f7fc4`). *Verified:* a regression test pins both examples, and
   all 696,311 processed scores equal the raw files' text exactly, with 0 differences.
   *Attribution checked:* the line was fixed before the first commit, so `git show 41f7fc4^`
   has no `clean.py` to recover it from. The code path was therefore rerun step by step on v1.3
   instead. `read_csv(dtype=str)` keeps every score's text exactly (0 of 696,311 differ from
   the file), the `pd.to_numeric` line alone produces exactly the 3,124 wrong values, and
   `.astype(float)` on the same strings gets all of them right. pandas' default `read_csv` float
   parsing has the same flaw (the same 3,124 wrong).
   *Lesson:* the first count (771) was itself wrong. Only measuring the parser against an
   exact reference gave the real size of the bug.
2. **ProteinGym's fold files are not a safe source of scores or labels.** *Found:* the same
   cross-check after the fix. Scores now differed from the fold files on 3,297 rows, more than
   before, because the fix removed the matches that had been hiding real differences.
   Re-parsing both files exactly gave the same 3,297 and showed two real upstream
   differences. (a) The fold files print scores with fewer digits than the raw files (16
   significant digits instead of 17 in the examples checked), so 3,297 values in 82 assays
   differ, by at most 9.3 × 10⁻¹³ relative. (b) 6 assays have empty `DMS_score_bin` in the fold
   files although the raw files have labels, for example `Q53Z42_HUMAN_McShan_2019_binding-TAPBPR`
   and `_expression` (3,344 rows each) and `HSP82_YEAST_Cote-Hammarlof_2020_growth-H2O2`
   (2,252). *Decision:* Phase 3 uses the fold files only for fold assignments, joined on
   `(assay_id, mutant)`. Scores and labels always come from the processed table (ADR 0005).
   *Verified:* labels agree on every row where the fold files have one, and the mutant sets
   are identical.
3. **What to do when the same variant has two different measurements.** *Found:* while
   designing the duplicate rule, not in the data: v1.3 has no such rows. *Decision:* remove
   every copy and log the experiment, mutant and CSV rows, instead of keeping the first copy or
   averaging. Either of those would be a silent correction of published data, which the data
   rules forbid. Exact repeats (same score and label) keep their first copy, since nothing is
   being chosen. *Verified:* unit tests cover both cases. The report schema's
   rows in = rows out + removed check guarantees that any such removal shows up in the counts.

**Supporting details.**
- *Result on v1.3:* 217 experiments, 2,465,767 rows in, 1,769,456 multi-change variants
  removed (from 69 experiments), 0 missing values, 0 duplicates, 696,311 out. The smallest
  experiment keeps 33 variants and the largest 16,897. A full run takes about 18 s, and the
  processed data is 9.3 MB.
- *Storage:* `variants/assay_id=<id>/data.parquet`, written with DuckDB so pyarrow is not
  needed. `assay_id` is stored only in the folder name, where DuckDB's hive partitioning
  reads it. `mutascope.data.db.connect` exposes the views `variants`, `cleaning_report` and
  `assays` (the reference file, joinable on `assay_id`, for per-UniProt and per-function-type
  aggregation later) (`23a2c72`).
- *Reproducible report:* the Markdown has no timestamps or local paths, so a rerun on the same
  data produces an identical file, and its git diff shows only real count changes (`9648d4f`).
- *Refactor:* assay/CSV matching and the temp-folder helpers are shared with
  `check-variants` and the downloader instead of duplicated (`dae1dbb`).
- *Not done yet:* the Nextflow `CLEAN` module comes after the pipeline's `DOWNLOAD` and
  `VALIDATE` modules.

---

## Entry 5 — Structure features and Foldseek 3Di (Phase 1, 2026-10-10)

**Plain-language summary.** Each ProteinGym experiment comes with a 3D structure of its protein,
predicted by AlphaFold2. Three new commands turn those structures into per-residue
information that later models can use. `mutascope data structure-features` records, for every
residue, how confident AlphaFold was (pLDDT), how exposed it is to water (relative solvent
accessibility), and how many other residues are packed around it. It deliberately does not
record helix/strand/loop (see problem 2): the only method available portably got disordered
regions wrong, and it was better to ship less data than wrong data.
`mutascope data foldseek-3di` runs the Foldseek tool to describe each residue's 3D
neighbourhood as one of 20 letters (the "3Di" alphabet), which the structure-aware SaProt
model will read in Phase 2. `mutascope data structure-report` checks the two outputs
against each other and summarises them per experiment. On ProteinGym v1.3, 213 experiments
get both tables (185 structure files, 77,338 residues each). The 4 documented exceptions get
nothing, and all 665,793 variants in the other 213 fall on a residue that has structure
data. ADR 0006 records the rules.

**The 3 most important design choices.**

1. **Key everything by position in the experiment's own sequence:** both tables use the same
   0-based `position` as the variants table, so a variant's structure features are a plain
   join on `(assay_id, position)`. Some structures model only a window of the protein (ZIKV,
   for example, models residues 291–794 of 3,423), so the offset is applied in one place,
   `StructureMapping.offset`, just as `parse_mutation` is the one place mutation codes change
   from 1-based to 0-based. The ZIKV variants span exactly 291–794, so the full coverage is
   genuine and not a join error.
2. **Three independent sequence checks, plus a cross-check:** a structure is accepted only if
   its residues equal the experiment's sequence range according to three parsers that share
   no code: our CA-line reader, biotite, and Foldseek's own amino-acid output. The report then
   requires the two tables to have identical keys and residues, and requires that no
   documented exception has any rows. As a final check, the wild-type residue of all 665,793
   variants equals the structure table's residue at that position.
3. **Pin the tool, not just the data:** 3Di letters depend on the Foldseek build, so the
   config pins the exact commit (`10-941cd33`) and the command refuses any other binary. This
   is the same reasoning as the data checksums (ADR 0002): a rerun either produces identical
   tokens or stops. Both tables were byte-identical across reruns (all 213 Parquet files), and
   the 3Di strings match a standalone Foldseek run.

**Problems encountered and how they were solved.**

1. **Foldseek appends a PDB's title to the structure name.** *Found:* the first real
   `foldseek-3di` run failed with `'AICDA_HUMAN.pdb_A ALPHAFOLD MONOMER V2.0 PREDICTION FOR
   ...' is not chain A`, because the parser accepts only names of the form `<file>_A`. *Cause:*
   13 of the 185 structures in use have a `TITLE` record, and Foldseek appends it to the
   entry name after a space. *Fix:* use only the first word of the name, as the official
   SaProt code does (`desc.split(" ")[0]`). *Verified:* a regression test, and on the full
   run all 185 Foldseek sequences equal their target ranges. The strict parser turned what
   could have been 13 silently mis-keyed structures into a loud failure.
2. **The secondary-structure method called disordered regions "strand".** *Found:* while
   checking whether the features make physical sense, BRCA1 (mean pLDDT 41) came out 35%
   strand. *Cause:* P-SEA, biotite's method, uses only the geometry of the CA atoms, and
   AlphaFold draws disordered regions as long extended chains that look like strands. Of the
   12,301 residues with pLDDT < 50, 32.9% were called strand, against 18.4% of the 40,323 with
   pLDDT ≥ 90. *Fix tried:* replace it with DSSP, the hydrogen-bond-based standard. DSSP could
   not be installed portably: Homebrew has no `dssp` formula (`brew info dssp`: "No available
   formula"), conda is not used, and PyPI has no official bindings (`mkdssp` and `dssp` do not
   exist there). Building it from source would have to be repeated in CI and Docker, and
   `pydssp` is a simplified reimplementation rather than DSSP. *Decision:* the `sse` column
   was removed from the schema, the code and the report, rather than kept with a caveat, because
   a model would learn "strand" as a proxy for disorder. Low `plddt` is the disorder flag.
   *Verified:* the rerun Parquet has columns `position, residue, plddt, sasa, rel_sasa,
   contact_count` (213 assays, 77,338 rows), a test pins that column list, and the
   regenerated report has no SSE columns (ADR 0006, "Alternatives rejected").
3. **Foldseek could not be installed from Homebrew or conda, and its location was fragile.**
   *Found:* `brew info foldseek` reports no formula, and conda is not installed. The first
   version used the official release tarball (`10-941cd33`, sha256 `6d8b07e1…cae5`) in
   `~/.local/bin`, a location that does not exist in CI or on a fresh clone. *Fix:*
   `foldseek-3di` now resolves the binary before any work (`FOLDSEEK_BIN` if set, else
   `foldseek` on `PATH`) and fails with install instructions if neither exists. It records the
   binary's version, sha256 and how it was found in `three_di/foldseek_run.json` (plus its path,
   for local debugging only), which `structure-report` requires. The README's "Installing Foldseek" section
   documents the exact release, both checksums and where to put the binary. The release is
   immutable and also ships the `linux-avx2` binary CI and Docker will need. *Verified:* with
   neither set, the real command exits 1 with "Foldseek not found: FOLDSEEK_BIN is not set and
   `foldseek` is not on PATH" before mapping any structure, and creates no output. A CLI test
   confirms the check runs before the structure mapping. The real-data report identifies the
   binary by version, sha256 (`1b446bbe…aadab`) and lookup (`FOLDSEEK_BIN`) only. The absolute
   path was kept out of the report so it stays reproducible across machines, and lives only in
   the gitignored `foldseek_run.json`.
4. **A pre-commit hook rewrote the test structure.** *Found:* the first commit attempt
   reported `trailing-whitespace ... Fixing tests/data/fixtures/SR43C_ARATH.pdb`. PDB lines are
   fixed-width, so trailing spaces are part of the format, and the hook would have committed a
   file that no longer matched ProteinGym's. *Fix:* exclude `tests/data/fixtures/` from the
   whitespace hooks (`0d1882a`). *Verified:* `cmp` shows the committed fixture is
   byte-identical to the raw download.
5. **biotite has no type information.** *Found:* the first `make lint` gave 46 pyright-strict
   errors in `structure_features.py`. *Fix:* biotite is treated as dynamic in one place, and
   everything it returns is converted to typed numpy arrays in `load_chain`, so the rest of
   the module stays fully checked without scattered `ignore` comments. *Verified:* pyright
   strict reports 0 errors.

**Supporting details.**
- *Run time and size on v1.3:* features 11 s, 3Di 1 s, report under 1 s. The tables take
  2.4 MB and 1.0 MB.
- *Sanity checks on real data:* pLDDT equals the PDB text in 500 of 500 random residues.
  Relative SASA correlates with contact count at r = −0.90, as buried residues should, and
  0.28% of residues exceed 1 (max 1.38, exposed termini; not clipped).
- *Tests:* 32 new tests (153 in the suite, 1 skipped without Foldseek), using a real
  48-residue ProteinGym structure checked against a hand-written parser of the PDB text,
  plus golden values. A
  shell script stands in for Foldseek so CI needs no binary, and one golden test runs the real
  Foldseek whenever it is installed.
- *Dependencies:* biotite 1.6 (`uv add biotite`) for PDB parsing and SASA. Foldseek is an
  external binary pinned by version (README, ADR 0006).
- *Not done yet:* the Nextflow `STRUCT_FEATURES` and `FOLDSEEK_3DI` modules (next task, after
  review), and installing the Linux Foldseek in CI and the Docker image.

---

## Entry 6 — Pipeline wiring and CI (Phase 1, 2026-10-10)

**Plain-language summary.** Until now each data step was a command run by hand. The workflow
engine (Nextflow) now runs them in order: download and verify the release, check every variant
and structure, clean the variants, compute structure features and 3Di tokens, and cross-check it
all in a final report. `make pipeline` does this for all 217 experiments in 42 seconds once the
data is downloaded, and its output is byte-identical to the hand-run results of entries 4 and 5.
A fast test version, `make pipeline-test`, runs the same steps on two small experiments in about
30 seconds from an empty cache. GitHub Actions runs it on every push, after linting,
type-checking and the test suite with coverage. ADR 0007 records the rules. Commits: `26dd71e`
(CLI), `ba35f79` (pipeline), `c6720ad` (CI), `b30436b` (validation report).

**The 3 most important design choices.**

1. **The release is cached outside the pipeline and linked in, not copied.** Nextflow normally
   writes each step's output into its own work directory, which here would mean downloading
   2 GB on every fresh run. Instead `DOWNLOAD` keeps the release in `data/`, where the existing
   downloader re-verifies every file against the committed checksums (about 75 MB to hash), and
   `data download --link-dir` then symlinks it into a fixed three-entry layout. The 2-assay
   `SUBSET` step writes the same layout, so no later step can tell a test run from a real one.
2. **The test assays are named explicitly.** `AICDA_HUMAN_Gajula_2014_3cycles` and
   `B2L11_HUMAN_Dutta_2010_binding-Mcl-1` are both 198 residues long, with 209 and 170 single
   mutants, and they cover two function types. AICDA's structure file has the `TITLE` record that
   broke 3Di parsing in entry 5, so CI re-tests that fix on real data. A rule such as "the 2
   smallest assays" would have picked NPC1_HUMAN (1,278 residues), and its choice would change
   whenever the reference file changed.
3. **One list of config overrides reaches every command, and the pipeline proves it.** Every
   `mutascope` call in the pipeline ends with `${params.hydra_overrides}`, and every `data`
   command accepts overrides, which a test enforces. The test profile needs
   `data.structure_exceptions=null`. Without it, the run stops at `VALIDATE` with "4 structure
   mapping problem(s) in 2 assays", because the four documented exceptions are not among the two
   test assays. With it, validation passes. Validation's log is an input to every output step, so
   nothing is written unless both checks pass.

**Problems encountered and how they were solved.**

1. **Two commands would have rejected the override list.** *Found:* while reading the CLI before
   wiring it. `check-variants` and `clean` took no config arguments, and Typer refuses
   undeclared ones (`Got unexpected extra argument(s) (data.structure_exceptions=null)`). *Fix:*
   both now take `--config-dir` and overrides. They read nothing from the config yet, but they
   still compose it, so a misspelt override fails instead of being silently ignored.
   *Verified:* a test runs `check-variants` with `data.structure_exceptons=null` (misspelt) and
   expects Hydra's `ConfigCompositionException`. A parametrised test checks that all 8 `data`
   commands take overrides.
2. **The test profile would have downloaded 1.9 GB.** *Found:* `zero_shot_scores` is
   1,911,045,703 of the release's roughly 2 GB, and no Phase 1 step reads it. *Fix:* the test
   profile adds the Hydra deletion override `~data.files.zero_shot_scores`. *Verified:* from an
   empty cache, the test run downloads the other files (42 MB DMS, 19 MB structures, 13 MB CV
   folds and three CSVs) and finishes in 32 s. `data/manifest.yaml` is unchanged (`cmp`), because
   the downloader keeps the manifest entries of files it skips.

**Supporting details.**
- *Equivalence with the hand-run outputs:* `diff -rq` finds no differences between the pipeline's
  `variants` (217 partitions), `structure_features` (213) and `three_di` (213) and the entry 4/5
  outputs. The committed `cleaning_report.*` and `structure_report.*` regenerate unchanged. The
  test run's partitions for its two assays equal the full run's (0 rows differ either way).
- *Timings (M4 Pro):* `make pipeline` 42 s with the download cached, `make pipeline-test` 32 s
  from an empty cache and about 5 s when cached.
- *CI:* three jobs. Lint (`make lint`) and tests (`make test` with coverage via a new
  `PYTEST_ARGS`, at 97% line coverage) run first, then the smoke test. The smoke test installs
  Nextflow 26.04.7 and the linux-avx2 Foldseek (sha256 `e6a3e386…74ac7`, computed from the
  release asset because GitHub publishes no digest for it), caches the ~75 MB of downloads, and
  fails if `data/manifest.yaml` changes. The workflow passes actionlint (with shellcheck). It has
  not run on GitHub yet: this PR is its first run.
- *Tests:* 19 new (172 in the suite, 1 skipped without Foldseek).
- *Dependencies:* none added.
- *Not done yet:* `docker,test` (needs the Phase 4 image, which also needs `params.data_dir`
  mounted). The old hand-run outputs in `data/processed/proteingym_v1.3/` are now superseded by
  `data/processed/<table>/` and can be deleted.

---

## Entry 7 — EDA notebook, data card and Hub staging (Phase 1, 2026-10-10)

**Plain-language summary.** Phase 1's data is now described in three ways. A marimo notebook,
`notebooks/eda.py`, charts the 217 experiments: how many variants each has, which kinds of
function they measure, how their scores are distributed, and how long the proteins are. Every
number and chart comes from a new package module, `mutascope.eda`; the notebook only lays them
out. `docs/DATA_CARD.md` documents the dataset for outside readers: where it comes from, how it
was cleaned, its counts, its known weaknesses and its licence (MIT, like ProteinGym v1.3). A new
command, `mutascope data publish`, builds the exact folder the Hugging Face Hub will hold and
checks it. Uploading is a separate flag (`--push`) and has not been run yet: the card is waiting
for review. ADR 0008 records the decisions.

**The 3 most important design choices.**

1. **Summaries in SQL, charts in the package, layout in the notebook.** The 696,311 variants are
   binned in DuckDB, and only the bins reach a chart. Altair refuses more than 5,000 rows by
   default, and z-scoring per assay is a window query anyway. Because the summaries are package
   functions, they have tests (13 new in total, 100% line coverage of `mutascope.eda`), and the
   data card's numbers come from the same functions as the charts.
2. **Stage, review, then push the same bytes.** `publish` writes `build/hf_dataset/`: one
   schema-validated Parquet file per table, the reports, the card, `provenance.json` (release
   pin, input checksums, Foldseek version) and `SHA256SUMS`. `--push` re-verifies those checksums
   and refuses a folder edited after staging, so what reaches the Hub is exactly what was looked
   at. New repos are created private. Staging twice gives byte-identical files, and all 696,311
   published rows equal the processed table on `(assay_id, mutant)`, position, score and label.
3. **Flat files with `assay_id` as a column for the Hub.** Locally, `assay_id` lives only in the
   partition path (ADR 0005). The `datasets` loader and the Hub viewer do not read hive paths,
   so the published tables carry it as a column and are sorted. Before anything is written, each
   assay's published `DMS_number_single_mutants` must equal its processed row count; a test
   changes one count and checks that nothing is staged.

**Problems encountered and how they were solved.**

1. **The sequence-length histogram rendered with no bars.** *Found:* every chart was rendered to
   PNG (vl-convert) and checked by eye before the notebook was called done. The length chart
   showed axes and the 1,022-residue marker but no bars. *Cause:* isolated by rendering layer by
   layer. The bars alone drew 3 bar marks on a 3-row input, and adding the marker layer (either
   as a data row or as a constant `datum`) dropped them to 0. *Fix:* bin with an explicit
   `transform_bin` and encode the pre-binned edges (`bin="binned"`, `x2`). *Verified:* the
   layered chart draws 3 bars on the same input, and the full chart shows all 217 assays with the
   marker. The other six charts had no layered bins and rendered correctly.
2. **The structure tables failed their schemas at the first real staging run.**
   *Found:* `mutascope data publish` stopped with pandera `column_ordered` errors on
   `StructureFeatureSchema` ("column 'assay_id' out-of-order"). The `variants` and `assays`
   tables had already passed. *Cause:* `SELECT *` over a hive-partitioned view puts the
   partition column `assay_id` last, and the schema is `ordered`. *Fix:* explicit column lists
   for both structure tables. *Verified:* staging writes 77,338 rows to each, and the
   toy-dataset tests stage all four tables.
3. **The EDA found one assay whose binary labels are all 1.** *Found:* the label-balance chart
   had one assay at 100%. *Cause:* upstream. `F7YBW8_MESOW_Aakre_2015`'s cutoff (−0.0017) is the
   median over all 9,192 rows, 9,155 of them multi-mutants, and all 37 single mutants score at
   least 0.0037. The same effect is milder elsewhere. The 83 median-binarized assays with only
   single mutants have 45–53% labelled 1. The 43 with multi-mutants have a median of 67%, and 32
   of them are more than 10 points from 50%. *Fix:* none to the data, as data rule 3 requires.
   It is documented in the data card, and AUC and MCC are undefined for that assay, which the
   Phase 2 metrics must handle. *Verified:* recomputed from the raw CSV (median of all rows =
   −0.001725, single-mutant minimum 0.003679).

**Supporting details.**
- *Counts in the card:* 217 assays, 186 UniProt IDs, 696,311 single mutants (1,769,456
  multi-mutants removed in 69 assays), 213 assays and 77,338 residues with structure features.
  Function types: OrganismalFitness 77 assays / 375,411 variants, Stability 66 / 68,855,
  Activity 43 / 145,367, Expression 18 / 74,923, Binding 13 / 31,755. 64 of the 66 stability
  assays are Tsuboyama et al. 2023 (37–72 residues). 16 targets exceed 1,022 residues, with a
  median coverage of 4.6% of all 19 × L substitutions (90.1% for the rest).
- *`marimo edit notebooks/eda.py`* serves the notebook (HTTP 200); `marimo check` is clean, and
  running it as a script executes every cell without error.
- *Dependencies:* `huggingface_hub` (runtime, for `publish`); `marimo` and `altair` in a new
  `notebooks` extra, which `make setup` and CI install via `--all-extras`.
- *Config:* `data.processed_dir` and `data.reports_dir` (paths only, no pins changed), and a new
  `publish` group (`repo_id`, `private: true`, `stage_dir`, card front matter).
- *Tests:* 13 new (185 in the suite, 1 skipped without Foldseek), total line coverage 97%. The
  push path is tested with a fake `HfApi`; nothing touches the network.
- *Not done yet:* the upload itself (after the card review). The repository has no `LICENSE`
  file yet, although the card states MIT for the dataset.

---

## Entry 8 — Phase 1 close-out (2026-10-10)

**Plain-language summary.** Phase 1 is finished: one command downloads the pinned ProteinGym
release, proves the files are the right ones, checks every one of 2,465,767 measurements, and
writes 696,311 clean single-mutant variants for 217 experiments, plus structure features and 3Di
tokens for 213 of them. ADR 0009 summarises what exists, the decisions that mattered most and
what Phase 2 inherits. The roadmap's remaining Phase 1 items are ticked, except the `docker`
profile (Phase 4) and the Hub upload (after the data card review). CLAUDE.md now points at
Phase 2.

**The 3 most important design choices.**

1. **Fail loudly, fix nothing:** every upstream problem found in Phase 1 was reported and
   documented rather than repaired (ADRs 0003–0005).
2. **A checksum chain from Zenodo to git**, so a fresh clone rebuilds exactly the benchmarked data
   (ADR 0002).
3. **One CLI, orchestrated by Nextflow but never bypassed**, so every step runs and tests on its
   own (ADRs 0001, 0007).

**Problems encountered and how they were solved.** None worth documenting in this close-out.
One lesson from Phase 1 became a rule: CI once pinned `astral-sh/setup-uv@v10`, a moving major
tag that setup-uv does not publish (it has only full version tags), so the workflow failed to
resolve it (fixed in `3608dec`). CLAUDE.md's coding conventions now require checking the upstream releases
page for the exact tag before pinning any action, image, binary or package version.

**Supporting details.**
- *State at close:* `make pipeline` 42 s (download cached), `make pipeline-test` 32 s (empty
  cache), 185 tests passing (1 skipped without Foldseek) in about 10 s, 97% line coverage.
- *Open items:* the `docker` pipeline profile and the Hub upload.

---

## Entry 9 — Hub repo id and the split reproduction gate (before Phase 2, 2026-10-12)

**Plain-language summary.** Two fixes before Phase 2 starts. First, the dataset card's loading
examples still used a `<repo_id>` placeholder. They now name the published Hub repo,
`engAbdullahHanif/mutascope-proteingym-v1.3`, which is also recorded as `publish.repo_id` in
`conf/publish/hub.yaml` and linked from the README. The card was re-staged so it can be pushed on
its own. Second, the reproduction gate compared a single-mutant score with ProteinGym's
leaderboard number, which is computed over single and multi-mutants. ADR 0010 splits it in two.
The *harness gate* checks our metrics and aggregation code against the published 0.414 using
ProteinGym's own precomputed scores on all rows. The *model gate* checks our ESM-2 650M against
those precomputed scores on single mutants only. CLAUDE.md, the roadmap and the placeholder tests
follow the new definition.

**The 3 most important design choices.**

1. **Test the harness and the model separately.** If a single number fails, it cannot say
   whether the metrics code or the scoring model is wrong. The harness gate uses ProteinGym's
   scores, so the model cannot be the cause, and the model gate uses our harness on both sides,
   so the harness cannot be the cause.
2. **Compute the model gate's reference at test time.** "Within ±0.01 of theirs" uses the
   aggregate our harness computes from ProteinGym's precomputed scores on the same 696,311 single
   mutants (0.4125 today), not a hard-coded value. The comparison stays like-for-like if the row
   set ever changes.
3. **Per-assay agreement, not only an aggregate.** Spearman ≥ 0.99 between our scores and theirs
   in every assay catches model bugs that an average can hide. The scratch check below shows how
   much an average hides.

**Problems encountered and how they were solved.**

1. *The old gate could pass by coincidence.* Found by computing both aggregates from ProteinGym's
   precomputed `ESM2_650M` scores with a scratch script (per UniProt ID, then per function type,
   then overall). All 2,465,767 rows give 0.4137, matching the published 0.414, and per-assay
   values match ProteinGym's DMS-level file within 0.0005. The 696,311 single mutants give
   0.4125. The overall gap is only 0.0012, but all 69 multi-mutant assays change: 26 rise, 43
   fall and 28 move by more than 0.1. The extremes are `SPG1_STRSG_Wu_2016` (0.128 → 0.551) and
   `HIS7_YEAST_Pokusaeva_2019` (0.411 → 0.028). Fixed by the gate split in ADR 0010. The numbers
   were verified by rerunning the script. The real harness is still to be written in Phase 2.
2. *Setting a default `repo_id` would have made a test push for real.* `test_push_needs_a_repo_id`
   relied on the config default being `null`. With the real repo id as the default, `--push`
   would have passed the guard and called the real `HfApi`. Found by reading the test before
   changing the config. The test now passes `publish.repo_id=null` explicitly, and
   `tests/export/test_hub.py` passes (7 tests).

**Supporting details.**
- *Re-staged card:* compared with the Hub's `SHA256SUMS`, only `README.md` changed (and so its
  checksum line). All four Parquet tables, the reports and `provenance.json` are byte-identical,
  so the upload is limited to `README.md` and `SHA256SUMS`.
- *Not changed:* `conf/data/proteingym.yaml` pins and the ±0.01 tolerance. The new per-assay
  threshold (0.99) and the harness tolerance (|Δ| ≤ 0.0005, the published value's rounding) are
  in ADR 0010.

---

## Entry 10 — Benchmark harness and harness gate (Phase 2, 2026-10-12)

**Plain-language summary.** To say how good a model is, we need a fixed way to score its
predictions against the lab measurements. MutaScope now has one, `mutascope.eval`, built to
match the official ProteinGym benchmark code line by line. For each experiment (assay) it
computes five numbers. Spearman, the main one, asks whether the model ranks the variants in the
same order as the lab did. AUC and MCC ask whether it separates functional from broken variants.
NDCG@10% and top-10% recall ask whether it finds the best variants. These are then averaged the
way ProteinGym averages them: per protein and function, then per function type (activity,
binding, expression, organismal fitness, stability), then overall, so that a protein with many
experiments does not dominate. Each average gets a 95% confidence interval from resampling the
experiments. If a number cannot be computed for an experiment (for example AUC when every
variant is labelled "functional"), it is recorded as missing and counted in the log, never as 0.
To prove the harness is right, we ran it on ProteinGym's own published model scores and got back
the published leaderboard: 0.4137 for ESM-2 650M (published 0.414) and 0.4566 for SaProt 650M
(published 0.457). The other four metrics match too. This check is now a test, the *harness
gate* of ADR 0010, covering all five metrics for both models against leaderboard files that are
part of the pinned, checksummed download. Code in `20e2031`, gate and results in `e1784f2`,
all-metric gate in `61b90f0` and `afe82ed`; decisions in ADR 0011.

**The 3 most important design choices.**

1. **Copy ProteinGym's definitions exactly, then use their code as the test oracle.** Several
   details that change the numbers appear only in their script, not in the paper. NDCG uses the
   continuous score as gain, not the binary label. MCC splits the model's scores at their own
   median, using `>=`. The averaging groups by protein *and* function type. Their `calc_ndcg`,
   `calc_toprecall` and group-by chain are copied verbatim into `tests/eval/` and compared with
   ours on random inputs full of ties. On the real data all five metrics agree with the published
   per-assay values within rounding, for both models and all 217 assays.
2. **Undefined means NaN, not 0, even where ProteinGym says 0.** sklearn returns 0 for MCC when
   only one label class exists, and ProteinGym records it. A 0 reads as "no better than chance",
   which is a claim the data cannot support. The deviation is deliberate, measured (below) and
   recorded in ADR 0011, and it does not touch the gate: on all rows no metric is undefined.
3. **One weighted aggregation for both the estimate and the bootstrap.** A bootstrap resample is
   just a vector of how many times each assay was drawn, so the point estimate is the same
   computation with every weight set to 1. The 95% CI and the reported value therefore cannot
   follow different protocols, and 10,000 resamples take 0.08 s per model and metric.
   Resampling is stratified by function type, so every resample still averages the same five
   categories. The unit is the assay, as CLAUDE.md specifies and the author confirmed.

**Problems encountered and how they were solved.**

1. **The function-level check failed for SaProt's Binding score.** *Found:* the first run of the
   gate test failed with SaProt 650M Binding at 0.37853 against a published 0.378 (Δ = +0.00053,
   over the 0.0005 tolerance). *Cause:* ProteinGym rounds twice: each assay's value to 3 decimals,
   then the mean. From their rounded per-assay values, Binding is exactly 0.3785, which pandas
   rounds half-to-even to 0.378. Our unrounded 0.37853 rounds to 0.379. Each rounding can move a
   value by up to 0.0005, so a bound of 0.0005 was wrong for function-level values. *Fix:* the
   function-level check (our addition, not part of ADR 0010's gate) allows 0.001, with the reason
   in the test. A separate test feeds ProteinGym's own rounded per-assay values to our
   aggregation and requires the published aggregates exactly. The overall tolerance stays 0.0005.
   *Verified:* the gate passes (ESM-2 Δ −0.0003, SaProt Δ −0.0004 overall). Once all five
   metrics were in the gate, the same effect showed up in 3 of the 50 function-level values:
   SaProt Binding Spearman (+0.00053), ESM-2 Activity MCC (−0.00056) and ESM-2 Binding top-10%
   recall (+0.00056), all under 0.001. All 10 overall values are within 0.00044. Our aggregation
   of the published per-assay values reproduces all 60 published aggregates exactly (5 metrics ×
   2 models × overall and 5 function types).
2. **A rounding-mode mismatch made a correct aggregation look wrong.** *Found:* the first version
   of that exact check matched 9 of 10 metric–model pairs; the odd one out was SaProt Spearman.
   *Cause:* the check used Python's `round`, which rounds the exact binary value of 0.3785
   (slightly above it) up to 0.379. pandas, and so ProteinGym, multiplies by 1,000 first, gets
   exactly 378.5 and rounds half-to-even to 378. *Fix:* use `np.round`, as pandas does; the test
   docstring names the trap. *Verified:* 60 of 60 aggregates exact (above).
3. **The NaN example in the task does not occur on all rows.** *Found:* the all-row run logged
   0 undefined values out of 2,170 (2 models × 217 assays × 5 metrics), while
   `F7YBW8_MESOW_Aakre_2015` was expected to have undefined AUC and MCC. *Cause:* its 37 single
   mutants all have label 1, but on all 9,192 rows exactly 4,596 are 1, because the binarisation
   cutoff is the median over all rows. *Fix:* nothing in the code; the harness also runs on single
   mutants (`--rows single`), where the NaN path does fire, and the toy test release has an assay
   built the same way. *Verified:* the single-mutant run logs exactly 4 undefined values: AUC and
   MCC of `F7YBW8_MESOW_Aakre_2015`, for each model. ProteinGym would record that assay's MCC
   as 0. Its protein, `F7YBW8_MESOW`, would then have MCC 0.0801 (the mean with
   `F7YBW8_MESOW_Ding_2023`'s 0.1601) instead of our 0.1601. ESM-2's overall single-mutant MCC
   would be 0.3251 instead of 0.3253 (OrganismalFitness 0.2808 instead of 0.2820); SaProt's
   0.3544 instead of 0.3547.
4. **A Hydra dict override merged instead of replacing, so a test read real data.** *Found:*
   after `eval.published` became one entry per metric, the CLI test overrode it with a
   Spearman-only dict pointing at the toy release, and passed. Checking the composed config
   showed all five keys still present: Hydra merges a dict override into the existing one. The
   test had read the real AUC, MCC, NDCG and top-recall files on this machine, and would have
   failed in CI, where they are absent. *Fix:* the test sets the Spearman paths and deletes the
   other four with `~eval.published.<metric>`. *Verified:* the test now asserts that no `auc:`
   block is printed, and the composed config has only `spearman`.

**Supporting details.**
- *Results* (ProteinGym's precomputed scores, ProteinGym-style aggregate, 95% CI):

  | Rows | Model | Spearman | AUC | MCC | NDCG@10% | Top-10% recall |
  |---|---|---|---|---|---|---|
  | All 2,465,767 | ESM2 650M | 0.4137 [0.3895, 0.4406] | 0.7286 | 0.3269 | 0.7472 | 0.2163 |
  | All 2,465,767 | SaProt 650M | 0.4566 [0.4382, 0.4771] | 0.7509 | 0.3586 | 0.7682 | 0.2322 |
  | Singles 696,311 | ESM2 650M | 0.4125 [0.3893, 0.4408] | 0.7326 | 0.3253 | 0.7586 | 0.1946 |
  | Singles 696,311 | SaProt 650M | 0.4559 [0.4379, 0.4795] | 0.7540 | 0.3547 | 0.7772 | 0.2115 |

  The single-mutant ESM-2 aggregate, 0.4125, is the model gate's reference (ADR 0010). The
  precomputed single-mutant rows are exactly our 696,311 processed variants: same keys and
  labels, with `DMS_score` differing by at most 3.7e-9 (float formatting in the CSVs).
- *Leaderboard files:* the AUC, MCC, NDCG and top-recall summary and DMS-level files (8 files,
  about 0.7 MB) were added to `conf/data/proteingym.yaml` with the author's approval, at the
  same pinned commit. `benchmarks_url` now ends at `substitutions/`, and each file names its
  metric directory, so the two Spearman URLs are unchanged. No upstream md5 exists. `mutascope
  data download` recorded their sha256 in `data/manifest.yaml`, and the files are
  byte-identical to the ones used for the first check.
- *Not comparable:* the leaderboard's `Bootstrap_standard_error` is the standard error of each
  model's difference from the top model, not a CI of its own score (ADR 0011).
- *Dependencies:* `scipy` and `scikit-learn`, the libraries ProteinGym calls; both are needed
  again in Phase 3.
- *Tests:* 53 new (238 passing, 1 skipped without Foldseek). The harness gate is 16 tests
  (3 checks × 5 metrics, plus one config check) and takes about 4 s; without the release they
  skip, as in CI. `mutascope eval precomputed --rows all` takes about 9 s.
- *Not done yet:* the model gate (needs our scorers), MLflow/W&B tracking, figures, and a
  Nextflow step for evaluation.

---

## Entry 11 — Scorer protocol, cache, ESM-2 scorer and scoring region (Phase 2, 2026-10-12)

**Plain-language summary.** MutaScope can now ask a protein language model, ESM-2, how much it
"likes" every possible single amino-acid change in a protein. The model reads the sequence with
one position hidden (masked) and says how probable each of the 20 amino acids is there. The score
of a change is how much more or less probable the new amino acid is than the original. Doing this
for every position gives a landscape: a table of positions by amino acids. A faster mode reads
the sequence once without hiding anything; it is about 200 times quicker but less faithful. Every
zero-shot model will implement the same small interface (`Scorer`), results are cached on disk
so each protein is scored once per model, and the code runs on the Mac's GPU (MPS) or on the CPU.
The method was copied from ProteinGym's own scoring script, and the results match ProteinGym's
published scores to about five decimal places, both for short proteins and for long ones that
must be read in pieces. For five experiments, ProteinGym scored only the part of the protein its
evolutionary alignment covers, so every benchmark number now does the same for those five, while
the website shows landscapes of the whole protein. Code in `e1994ba`, tests in `1baa0ed`, CI cache
in `08a3f61`, scoring region in the `feat(eval)` commit that follows; decisions in ADR 0012.

**The 3 most important design choices.**

1. **Match ProteinGym's script, then prove it on their numbers.** The masked marginal, the
   "+1 for `<cls>`" token offset and the long-sequence window (`get_optimal_window`, ported
   verbatim) follow `compute_fitness.py`. The proof is agreement with their precomputed scores
   from the same checkpoints: max difference ≤ 3.4e-05 on six assays with the 8M model,
   including four longer than 1,022 residues whose mutations fall in left-edge, centred and
   right-edge windows, and ≤ 2.3e-05 on two assays with the 650M model.
2. **Pin the token offset three ways.** `tokenize` refuses any layout other than `<cls>`, one
   token per residue, `<eos>`. A stand-in model that predicts each token from its left neighbour
   must reproduce the sequence shifted by exactly one, through windows too; reading token i
   instead of i + 1 cannot pass. And the real 8M model's unmasked pass recovers 72 of 76
   ubiquitin residues when aligned and 3 of 75 when shifted by one.
3. **A neutral scorer, and a derived scoring-region rule outside it.** The scorer scores whatever
   sequence it is given. Which part of a protein to score is decided in `eval/region.py`: in
   benchmark mode, the MSA region wherever it is not the full target; in landscape mode, always
   the full target. The set of MSA-region assays is derived from the reference file and must
   equal the five ids in `conf/eval/scoring_region.yaml`, so a changed reference cannot shift
   benchmark numbers silently. The region is part of the cache key, so the two kinds of
   landscape are never confused on disk.

**Problems encountered and how they were solved.**

1. **Three long proteins disagreed with ProteinGym, for a reason outside the scorer.** *Found:*
   comparing our 8M scores with ProteinGym's on long targets. BRCA1 (1,863 residues) matched
   exactly, but KCNH2 gave Spearman 0.36, POLG_HCVJF 0.91 and ZIKV 0.96. *Cause:* these are
   assays whose MSA covers only part of the target, and ProteinGym's precomputed scores for them
   were computed on that MSA-covered region, not the full target. The script at the pinned commit
   crops only for MSA Transformer, so these columns were not produced by the script published
   with them. *Fix:* none in the scorer, which is correct. The author decided that every
   benchmark number (model gate, zero-shot results, Phase 3 features) uses the MSA region for
   these assays, while website landscapes use the full target and label the five. `eval/region.py`
   derives the set from the reference file and fails if it differs from the configured list.
   *Verified:* through `score_assay` in benchmark mode, all five reproduce ProteinGym (Spearman
   1.000000, max difference ≤ 2.7e-05) and two full-target controls stay exact. On the full
   target the five give 0.36–0.99; three further long assays with a full-range MSA (UBE4B, ERBB2,
   BRCA2) match exactly on the full target. Left as it was, this would have failed the model
   gate's "≥ 0.99 in every assay" on KCNH2 and SCN5A.
2. **The cache key in CLAUDE.md would have mixed up two results.** *Found:* while writing the
   cache. The rule keyed entries by model, revision, sequence and structure tokens. Masked and
   wild-type marginals of the same sequence are different arrays, so a fast result could be
   served for a masked request. *Fix:* the scoring method is part of the key, the fields are
   JSON-encoded before hashing (so `"rev" + "MK"` and `"revM" + "K"` cannot collide), and
   CLAUDE.md's rule now names the method. *Verified:* a test changes each of the five fields and
   requires a different key; another checks that a fast call after a masked one runs the model.
3. **Two new tests were wrong, not the code.** *Found:* their first run. A window property test
   required every window to be exactly 1,024 tokens long, but for an odd window upstream's
   centred window `[p − w//2, p + w//2)` is one token short; ours matches it, as the oracle test
   shows. The real-model offset test required 95% aligned agreement, and ubiquitin gives 72/76 =
   94.7%. *Fix:* the property applies to even windows (ESM-2's is 1,024), and the threshold is
   90%, still far from the 4% (3/75) seen when shifted. *Verified:* 36 scoring tests pass.

**Supporting details.**
- *Throughput* (M4 Pro, MPS): with the 650M model, masked marginals take 22.5 s for 281 residues
  and 58.9 s for 448; the wild-type marginal takes 0.1–0.2 s for the same proteins and agrees with
  ProteinGym's masked scores at Spearman 0.96–0.97. With the 8M model, CPU and MPS agree with
  each other to 4.4e-05 and take about the same time; the 3,418-residue BRCA2 takes 111 s.
- *Dependencies:* `torch` 2.14.1 (from PyTorch's CPU-only index on Linux, so CI and Docker do not
  pull CUDA; from PyPI on macOS) and `transformers` 5.19.0. Hub revisions were read from the Hub
  API: 8M `c731040`, 35M `6fbf070`, 150M `a695f60`, 650M `08e4846`.
- *Tests:* 46 new (284 passing, 1 skipped without Foldseek; `make test` about 24 s). Three `slow`
  tests cover the long-sequence windows in about 3.5 minutes.
- *Scoring every position:* the website needs masked marginals at every residue, so the scoring
  step computes full landscapes; benchmark metrics read only the measured variants. Estimated
  650M cost on MPS: about 4 hours for the 187 distinct targets (72,933 residues; the 13 longer
  than 1,022 residues take about half), plus about 5 minutes for the five MSA regions. A
  full-target benchmark landscape and the website landscape are the same cache entry.
- *Not done yet:* scoring all 217 assays and the model gate, the CLI command and Nextflow step for
  scoring, SaProt, and the ensemble.

---

## Entry 12 — Scoring all 217 assays with ESM-2 650M and the model gate (Phase 2, 2026-10-12)

**Plain-language summary.** The 650M ESM-2 model has now scored every single mutant in the
benchmark: 696,311 variants across 217 experiments. To save time it was asked only about the
positions each experiment actually measured, which is 37,753 of the 62,671 positions in the
proteins involved, and the stored table for each protein keeps its full size with the unmeasured
rows left empty, to be filled in later for the website. The work took 1 hour 41 minutes of model
time on the Mac's GPU. It is started by one new command, `mutascope score zero-shot`, which the
Nextflow pipeline calls as a new step. Because a laptop can sleep or crash, each protein's
result is saved the moment it is finished, and starting the command again skips everything
already saved. This was tested by killing the real run twice. Finally, our scores were compared
with the scores ProteinGym published for the same model: in every one of the 217 experiments
the two rank the variants identically (lowest Spearman 0.99999996), and the overall benchmark
number is the same, 0.4125. This is the "model gate", and it passes. Decisions are in ADR 0013.

**The 3 most important design choices.**

1. **A cache entry is always the full `(L, 20)` table, and empty rows are filled in later.**
   The cache key did not change. A row is NaN until its position is scored, and a later request
   runs the model only for the rows that are missing. Two experiments on the same protein
   share one entry (the 217 assays need only 187), and the website's full landscapes will reuse
   the benchmark rows instead of recomputing them. NaN, never 0, marks "not scored", and a
   variant whose score would be NaN fails the run.
2. **The cache is the only checkpoint.** Each protein is written to disk by an atomic rename
   as soon as it is done, in a directory outside both the output folder and Nextflow's work
   folder. The score files are rebuilt from the cache on every run, which takes seconds, so
   there is one resume mechanism rather than two that could disagree. A crash loses at most the
   protein in progress: 455 s in the worst case on this run.
3. **The gate is one function shared by the command and the test, and it refuses to guess.**
   It requires the two score tables to contain exactly the same 696,311 variants, counts an
   undefined agreement as a failure, and checks every assay separately, because one broken
   assay barely moves an aggregate (a unit test shifts one assay's scores by a row and the gate
   fails on that assay alone).

**Problems encountered and how they were solved.**

1. **A git commit swapped source files under the running pipeline.** *Found:* a commit made
   seconds after the full run started failed in the pyright hook with "`AgreementSchema` is
   unknown import symbol". *Cause:* pre-commit stashes unstaged changes while its hooks run, so
   for a few seconds `eval/schemas.py` on disk was the old version, and the package is an
   editable install that the pipeline imports from the same directory. *Fix:* none needed for
   the run. The scoring process had started at 13:53:29 UTC and had already imported its
   modules; it went on to finish all 217 assays. No commit was made until the run ended.
   *Verified:* the run exited 0 with 696,311 scores, and the gate passed. ADR 0013 records the
   rule: do not commit during a run.
2. **The scoring command failed on a subset of assays.** *Found:* the new CLI test on a
   two-assay toy reference exited 1 with "in eval.scoring_region but MSA covers the full
   target", listing the five configured MSA-region assays. *Cause:* the region check requires
   the configured list to equal the set derived from the reference, and a subset contains none
   of the five. The Nextflow test profile would have failed the same way. *Fix:*
   `eval.scoring_region.msa_region_assays=null` is accepted and the test profile sets it, the
   same pattern as `data.structure_exceptions=null`. *Verified:* `make pipeline-test` completes
   all 8 processes and writes scores for both assays with the 8M model.

**Supporting details.**
- *Run:* `caffeinate -ims make pipeline`, 13:52:44–15:29:00 UTC, 1 h 36 min for the whole
  pipeline. The scoring step took 5,725 s, scored 35,775 positions in 189 assays and read 28
  assays from the cache. Including the 359 s the two deliberately killed runs had finished, the
  model time is 6,081 s for 37,753 positions. The 11 assays scored on more than 1,022 residues
  took 774 s. The cache is 5.2 MB.
- *Resume test on the real run:* killed with `SIGKILL` after 298 s, partway into assay 5. The
  rerun computed 0 positions for assays 1–4 in 0.0 s each, scored assay 5 in full (564
  positions, 93.5 s) and read assay 6 (same protein) from the cache.
- *Agreement:* lowest per-variant Spearman 0.99999996; largest absolute score difference
  2.35e-04 (`RDRP_I33A0_Li_2023`); 22 assays have a difference above 1e-04. Aggregate Spearman
  against `DMS_score` 0.4125 for both; the largest per-assay difference is 9.9e-06.
- *Our results* (ProteinGym v1.3, single mutants, zero-shot, 95% bootstrap CI): Spearman 0.4125
  [0.389, 0.441], AUC 0.733, MCC 0.325, NDCG@10% 0.759, top-10% recall 0.195. AUC and MCC are
  defined on 216 assays.
- *Tests:* 24 new (308 passing, 1 skipped without Foldseek on `PATH`; `make test` about 25 s).
  The slow model-gate test takes about 10 s with the cache warm.
- *Not done yet:* ESM-2 35M and 150M runs, the remaining 24,918 positions for website
  landscapes, SaProt, the ensemble, figures and experiment tracking.

---

## Entry 13 — ESM-2 35M and 150M, wild-type marginals, MLflow tracking (Phase 2, 2026-10-12)

**Plain-language summary.** The two smaller ESM-2 models, 35M and 150M, have now scored the
same 696,311 variants in 217 experiments that the 650M model scored in entry 12. All three
models were also run with the quick scoring method, the "wild-type marginal", which reads every
substitution from a single pass over the unchanged protein instead of one pass per position.
That gives six sets of scores, and each was benchmarked in the same way. Bigger models are
better (overall Spearman 0.311, 0.382 and 0.4125), and the quick method loses very little
(0.303, 0.377 and 0.408) while taking seconds instead of 19 minutes to 1 hour 41 minutes. For
all three models our masked scores rank the variants like the scores ProteinGym published
(lowest Spearman in any experiment 0.9999999), so the model gate passes three times. Finally,
every scoring, benchmark and gate run is now written to a local MLflow database, with the model,
its pinned revision, the code version, the full configuration and the resulting numbers. It
needs no server and no account, and `make mlflow` opens it in a browser. Decisions are in
ADR 0014.

**The 3 most important design choices.**

1. **The scoring method is part of a score set's name, built in one function.** Results tables
   have a `model` column and no method column, and the old directory and file names had only
   the model in them, so a wild-type run would have overwritten the masked run of the same
   checkpoint. `run_label` gives the masked marginal the bare model name (existing 650M files
   keep theirs) and the wild-type marginal `<model>_wt`. Scores, reports, results and tracked
   runs all use it, and the benchmark command refuses a score set whose label is not the one
   the config names.
2. **One command writes benchmark results, and tracking copies from them.** Until now only the
   model gate wrote results, and it needs ProteinGym's scores for the same method, which do not
   exist for the wild-type marginal. `mutascope eval zero-shot` benchmarks any score set against
   the labels in the processed data; the gate only checks agreement. The numbers in MLflow are
   read from the summary file the command has just written, so a tracked run and a results
   file cannot disagree.
3. **Tracking is local, one run per command, and tied to a commit.** The store is a SQLite file
   and an artifact folder under `mlruns/`. Each run carries the pinned model revision, the
   method, the whole resolved config and a `code_version` tag. The code was committed before
   the final runs, so all 15 runs carry the clean commit `41bafb3` and none says `-dirty`. A
   tracking failure fails the command, but only after its outputs are on disk.

**Problems encountered and how they were solved.**

1. **The 35M run died twice with "MPS backend out of memory".** *Found:* the first run stopped
   at assay 42 after 9,199 positions, with "MPS allocated: 3.05 GiB, other allocations:
   27.78 GiB". The test suite had been using the same GPU at that moment, and that was taken to
   be the cause. It was not: the rerun, with nothing else on the GPU, read 41 assays from the
   cache and died at assay 98 after another 9,166 positions (25.98 GiB of "other allocations").
   Between the two, the 150M run had scored all 217 assays at a steady 3.4 GB. *Cause:* reproduced
   without the cache on 14 targets (4,618 positions). With the 35M checkpoint the memory held
   by the Metal driver grew from 1.31 to 8.62 GiB while PyTorch reported 0.13 GiB in use; the
   8M checkpoint did the same (1.18 to 7.51 GiB). The 150M checkpoint stayed at 1.63 GiB over
   all 14, and the 650M checkpoint at 4.11 GiB over the first six, which is where its output
   was read. PyTorch keeps freed MPS buffers for reuse, and with the two small
   checkpoints the buffers kept for each new sequence length were never released. Why only
   those two is not known. *Fix:* the scorer calls `torch.mps.empty_cache()` after each
   sequence. *Verified:* the same 14 targets stay at or below 0.67 GiB and take 83.1 s instead
   of 81.1 s; a regression test with the 8M checkpoint goes from 1.27–2.75 GiB before the fix
   to 0.10–0.32 GiB after it; the third 35M invocation scored the remaining 19,388 positions in
   540 s without error, and the 35M gate passed. Each crash lost only the sequence in progress,
   as designed in entry 12.
2. **The regenerated 650M results were not identical to the committed ones.** *Found:* a check
   that the new benchmark command reproduces the committed 650M results showed a largest
   per-assay difference of 6.9e-06 where 0 was expected. *Cause:* the old results took their
   labels from ProteinGym's zero-shot score files, the new ones from the processed DMS data,
   and the two store `DMS_score` with different rounding: 3,297 of 696,311 labels differ, in 82
   assays, by at most 3.7e-09. In `CCR5_HUMAN_Gill_2023` 122 labels differ by at most 2.2e-16,
   which splits ties (955 distinct values in one file, 1,014 in the other) and moves that
   assay's Spearman from 0.347414 to 0.347421. *Fix:* none to the numbers. The processed
   variants are the label source from now on, because the supervised models train on them; the
   docstring that claimed the two sources had the same labels was corrected. *Verified:* the
   one assay is the only Spearman that changes, AUC, MCC and top recall are unchanged, the
   aggregate moves from 0.4125268 to 0.4125269, and the gate table is bit-identical.
3. **MLflow's default local store no longer works, and its artifacts would have gone to the
   wrong place.** *Found:* a probe before writing the tracking code. MLflow 3.17.0 raised "The
   filesystem tracking backend (e.g., './mlruns') is in maintenance mode" for a file store, and
   with a SQLite store it wrote artifacts to `./mlruns` of the current directory, which under
   Nextflow is a throwaway work directory. *Fix:* a SQLite store with an explicit artifact
   location, both under `tracking.dir`, which Nextflow passes as an absolute path. *Verified:*
   a test logs runs from another working directory and requires that directory to stay empty;
   after the full pipeline run all 15 runs and their artifacts are under `mlruns/`.

**Supporting details.**
- *Results* (ProteinGym v1.3, single mutants, zero-shot, 95% bootstrap CI). Masked: 35M
  Spearman 0.3113 [0.285, 0.338], 150M 0.3820 [0.358, 0.408], 650M 0.4125 [0.389, 0.441].
  Wild-type: 35M 0.3028 [0.277, 0.329], 150M 0.3772 [0.354, 0.403], 650M 0.4078 [0.386, 0.436].
  The other four metrics are in ADR 0014.
- *Model time* (M4 Pro, MPS, from the cache log). Masked, 37,753 positions: 35M 1,119 s in
  three invocations (335 s, 244 s and 540 s; the first shared the GPU with the test suite),
  150M 1,718 s, 650M 6,081 s (entry 12). Wild-type, 62,671 positions: 7.7 s, 8.7 s and 24.5 s.
- *Model gate:* lowest per-assay Spearman 0.99999996 (35M), 0.99999991 (150M) and 0.99999996
  (650M); largest score difference 1.33e-04, 2.04e-04 and 2.35e-04; aggregates equal to
  ProteinGym's to four decimals (0.3113, 0.3820, 0.4125).
- *Wild-type against masked,* per assay, same checkpoint: median Spearman 0.983, 0.986 and
  0.985; lowest 0.930, 0.923 and 0.834.
- *The final run:* `caffeinate -ims make pipeline`, 18 tasks in about 11 minutes, of which 9
  minutes were the 35M masked scoring; the other five score sets came from the cache. Then
  `mutascope eval model-gate` three times.
- *Run reports:* a rerun on a warm cache writes a report with `positions_computed = 0`. The
  reports committed in `results/data/` are those of the invocations that ran the model: 650M
  from entry 12, 150M and the three wild-type runs from their first run earlier in this
  session, 35M from the final pipeline run (its third invocation).
- *Dependencies:* `mlflow` 3.17.0, the latest release on PyPI, as the optional `tracking`
  extra, so the serving image does not install it.
- *Tests:* 9 new fast tests (317 passing, 1 skipped without Foldseek on `PATH`; `make test`
  about 33 s) and 2 new slow cases (6 passing in 219 s).
- *A wrong number in a commit message:* commit `ee825b1` says the 35M runs died "after about
  11,000 positions". The cache log gives 9,199 and 9,166. The message was left as it is,
  because the tracked runs refer to the commit that follows it.
- *Noticed, not fixed:* `test_undefined_metrics_are_nan_and_logged` fails when it runs after a
  CLI test in the same process (the CLI reconfigures logging). In the default order it runs
  first and passes.
- *Not done yet:* the remaining positions for website landscapes, SaProt, the ensemble and the
  figures (model size, fast vs masked).

---

## Entry 14 — Order-independent tests and paired model comparisons (Phase 2, 2026-10-12)

**Plain-language summary.** Two loose ends from entry 13 are closed. First, one test passed or
failed depending on which tests ran before it. The cause was in the logging setup, not in the
test: once a part of the program had written a log line, it kept using the logging settings of
that moment and ignored any later change. That is fixed, and the test suite now runs in a
different random order every time, so a test that leans on another one is found quickly.
Second, entry 13 could not say whether the quick scoring method is really worse than the slow
one, because the two error bars overlapped. Those error bars were the wrong tool. Two models
scored on the same 217 experiments rise and fall together from one experiment to the next, and
that shared variation fills most of each error bar. The new comparison resamples the
experiments once and applies the same draw to both models, so the shared variation cancels and
only the difference is left. With it, every size step and every masked-versus-quick difference
is clearly above zero on Spearman. Decisions are in ADR 0014, decision 13.

**The 3 most important design choices.**

1. **The logging fix is in the program, not in the tests.** Loggers are no longer cached on
   first use, so every log call follows the current configuration. A test-side reset could not
   have worked: a cached logger keeps its old settings even after structlog is reset. The
   cost is building the logger on each call, microseconds for a few lines per assay.
2. **The paired bootstrap reuses the existing resampling and aggregation.** `bootstrap_ci` and
   `paired_bootstrap` draw their resamples from one function (stratified by function type) and
   aggregate with one routine (per UniProt ID, per function type, overall). A paired and an
   unpaired interval can therefore differ only in what is being estimated. Regenerating the
   650M summary after the refactor gave a bit-identical file.
3. **Comparisons are a results file, produced by one command from a list in the config.**
   `mutascope eval compare` reads `eval.comparisons`, starts every pair from the same seed (so
   a pair's numbers do not depend on which other pairs are listed), and writes
   `results/zero_shot/comparisons.parquet`. Figures read that file. CLAUDE.md now forbids
   judging a difference from two separate CIs.

**Problems encountered and how they were solved.**

1. **A test failed only after two CLI tests had run in the same process.** *Found:* in entry
   13, by running `test_cli_eval.py` before `test_benchmark.py`:
   `test_undefined_metrics_are_nan_and_logged` captured 0 warnings instead of 4. *Cause:* the
   first guess, that a cached logger is invisible to `capture_logs`, was wrong: `capture_logs`
   edits the configured processor list in place, and a logger cached under that same
   configuration is captured (1 of 1 in a direct check). The failure needs a second
   `configure_logging` call, which every CLI invocation makes. It installs a new processor
   list, and a logger cached earlier keeps the old one (0 of 1 captured; a logger created
   afterwards, 1 of 1). One CLI test before the failing test is not enough; two are. The same
   state made a later `--log-json` a no-op for loggers already used. *Fix:*
   `cache_logger_on_first_use=False`. *Verified:* two new tests in `tests/test_logging.py` fail
   without the fix and pass with it. With `pytest-randomly`, the fast suite passed under seeds
   1, 20261012, 314159, 987654321 and 4242 (319 passed, 1 skipped each). With the fix
   reverted, two of those five orders (314159 and 4242) reproduced the original failure in
   `tests/eval`.
2. **A sentence written from memory about the results was wrong.** *Found:* while checking the
   ADR text against the data. The draft said the separate CIs of the two score sets overlap
   "in every one of the five pairs". They overlap in four: 150M (0.358 to 0.408) and 35M (0.285
   to 0.338) do not. *Fix:* the sentence now says four of five. *Verified:* a script compares
   the two intervals for each pair from the summary files.

**Supporting details.**
- *Paired comparisons* (Spearman, overall, A − B, 95% paired CI, 10,000 resamples, seed 0,
  217 assays): 150M − 35M +0.0707 [+0.0589, +0.0840]; 650M − 150M +0.0306 [+0.0225, +0.0405];
  masked − wild-type +0.0085 [+0.0061, +0.0107] for 35M, +0.0048 [+0.0022, +0.0070] for 150M
  and +0.0047 [+0.0007, +0.0079] for 650M. The first four are positive in all 10,000
  resamples, the last in 99.0%.
- *How much pairing narrows the interval:* the paired interval of 650M masked against
  wild-type is 0.0073 wide; the unpaired intervals of the two score sets are 0.0515 and 0.0502
  wide.
- *Not every difference is clear.* On top-10% recall the wild-type marginal is not worse at
  35M (−0.0032 [−0.0082, +0.0033]) or 150M (−0.0023 [−0.0045, +0.0004]). On Spearman by
  function type, 650M against 150M is not clear for Binding (+0.028 [−0.002, +0.059], 13
  assays), Expression or Stability. The intervals are not corrected for the number of
  comparisons (150 rows in the file).
- *Checked:* `mlruns/` is ignored (`/mlruns/` in `.gitignore`, no file tracked). The CI
  pipeline job runs `make pipeline-test`, whose `test` profile sets `models = ['esm2_8m']`;
  the fast tests load only the 8M checkpoint.
- *Dependencies:* `pytest-randomly` 5.0.0, the latest release on PyPI, in the dev group. A
  failing order is rerun with `-p randomly --randomly-seed=<seed>`; the seed is printed at the
  top of every run.
- *Tests:* 2 new in the first commit and 7 in the second (326 passing, 1 skipped without
  Foldseek on `PATH`; `make test` about 34 s). All 6 slow tests pass in 223 s.
- *Not done yet:* `eval compare` is a command, not a Nextflow step; it joins the pipeline with
  the figures. One of the five seeded runs took 189 s instead of about 35 s for a reason that
  was not looked into; the others, and every run since, took 34 to 53 s.

---

## Entry 15 — SaProt 650M scorer, the 213-assay benchmark and two upstream errors (Phase 2, 2026-10-12)

**Plain-language summary.** MutaScope now has a second kind of model. ESM-2 reads only the
protein's sequence; SaProt also reads a one-letter description of the local 3D shape around
every residue (Foldseek's "3Di" letters, computed in Phase 1 from the AlphaFold structures).
The scoring was ported from the official SaProt code: to score a position, the amino acid is
hidden while its shape letter stays visible, and the model says how likely each of the 20 amino
acids is there. SaProt scored 665,793 variants in the 213 experiments that have a usable
structure (4 of the 217 do not). On those 213 it is better than ESM-2 650M: overall Spearman
0.4600 against 0.4144, a difference of +0.0456 with a 95% paired interval of +0.0283 to +0.0625.
Our scores match the ones ProteinGym published in 211 of the 213 experiments. In the other two,
ProteinGym's published scores turned out to be wrong: they were computed from an older version
of its own reference file, and feeding our scorer the same wrong inputs reproduces them exactly.
Those two are now documented exceptions to the model gate, each backed by a test, and a report
for the ProteinGym maintainers is drafted but not sent. The decisions are in ADR 0015.

**The 3 most important design choices.**

1. **SaProt is scored on the residues its structure covers, in one pass, without windows.**
   This breaks two rules written for ESM-2 (1,022-residue windows; the MSA region for five
   assays), so it was measured before it was decided, and approved by the author. On samples of
   11 to 40 positions per assay, the structure range in one pass gave Spearman 1.000000 against
   ProteinGym in every case; 1,024-token windows gave 0.9827 on SCN5A and 0.9812 on POLG_CXB3N,
   below the gate's 0.99; the MSA region gave 0.1228 on SCN5A and 0.2297 on KCNH2. CLAUDE.md
   now states the SaProt rule on its own instead of as an exception.
2. **A gate exception must be proven, and must not be able to go stale.** The two assays are
   listed in `conf/eval/model_gate.yaml` with the reason. The threshold stays 0.99. For each
   entry a slow test replays the upstream error and requires Spearman above 0.9999 with
   ProteinGym's scores, and the gate fails if a listed assay ever reaches 0.99 with correct
   inputs, or if the list names an assay that was not compared. An exception therefore says
   "their scores are wrong, and here is how", never "we could not match this one".
3. **One engine for both models, checked to change nothing.** The cache handling, the masked
   and wild-type marginals and the windows moved to `scoring/masked_lm.py`; ESM-2 and SaProt
   each supply tokenisation, the masking token and how amino-acid probabilities are read from
   the logits. Recomputing cached ESM-2 650M entries with the new code gave bit-identical arrays
   (masked and wild-type, two assays), and the three ESM-2 model gates print the same numbers
   as in entry 13 (aggregates 0.3113, 0.3820, 0.4125).

**Problems encountered and how they were solved.**

1. **Two assays were nowhere near ProteinGym's scores.** *Found:* in a prototype run before the
   full scoring: `POLG_HCVJF_Qi_2014` Spearman 0.21 and `BRCA2_HUMAN_Erwood_2022_HEK293T` 0.05 on
   samples, while every other assay tried gave 1.000000. The full gate later gave 0.2770 (1,630
   variants) and -0.0611 (265 variants). *Cause:* the reference file from March 2024, when
   ProteinGym's SaProt scores were made, differs from v1.3 in exactly these two assays (and in
   CAS9, which has no structure). POLG_HCVJF had `pdb_range` 1981-2224 instead of 1982-2225, so
   every residue was paired with the next residue's structure letter. BRCA2 had three structure
   files, and ProteinGym's script writes the concatenated per-file scores back in file order;
   the file is not sorted by position, so all 265 rows hold another variant's score. *Fix:*
   none to our scorer. The work stopped at the failed gate as instructed; the author then
   decided on documented exceptions. *Verified:* replaying the shifted range gives Spearman
   1.000000 (max difference 2.0e-05); replaying chunks plus misassignment gives 1.000000
   (2.9e-05), while chunks with scores on the right rows give -0.07. Both replays are tests.
   ProteinGym's scores for these assays have Spearman 0.0005 and 0.1732 with the experiment;
   ours have 0.4626 and 0.4047.
2. **The model loader started downloading weights from an unpinned place.** *Found:* the
   Hugging Face cache showed a second snapshot directory and a second 2.6 GB download after the
   pinned file was complete. *Cause:* the SaProt repository has only `pytorch_model.bin`;
   `transformers` 5.19 then looks for an automatic safetensors conversion on another ref
   (`refs/pr/5`). *Fix:* `use_safetensors=False` in the SaProt loader. *Verified:* the loaded
   file's sha256 (`513c3908…`, 2,606,517,773 bytes) equals the LFS object id of
   `pytorch_model.bin` at the pinned revision, and runs with `HF_HUB_OFFLINE=1` load it.
3. **One passing assay differed 10,000 times more than the others.**
   `F7YBW8_MESOW_Ding_2023`: Spearman 0.999695 but a largest difference of 0.099, against less
   than 8e-05 everywhere else. *Found:* by the author, in the gate's worst-five list. *Cause:*
   one structure letter. Trying every single-letter change for residues 1 to 74 (the search was
   stopped at its time limit), exactly one reproduces ProteinGym's scores: residue 3 as `D`
   instead of our `P` (largest difference 1.3e-05; the next best change gives 0.068). ProteinGym
   does not record its Foldseek version. *Fix:* none; documented in ADR 0015 as a difference
   between Foldseek builds at one residue.
4. **A test of the port could not have failed.** *Found:* its second assertion failed on the
   first run. The test compares our scorer with the official `predict_mut` on a randomly
   initialised model, and the scores were all below 0.1 in size, so agreement to 1e-5 said
   little. *Fix:* wider initial weights. *Verified:* the scores now exceed 1 and the two
   implementations still agree to 1e-4 on all 480 substitutions of a 24-residue sequence.
5. **The task named this "entry 14".** Entry 14 already exists (paired comparisons), so this
   is entry 15.

**Supporting details.**
- *Sources:* scoring ported from `predict_mut`, westlake-repl/SaProt commit `e91e485` (MIT);
  ProteinGym's `baselines/saprot/compute_fitness.py` at the pinned `1f8de97` has the same
  function. Checkpoint `westlake-repl/SaProt_650M_AF2` at revision `d9b9ad0`.
- *Scoring run:* 213 assays, 42,260 measured positions, 35,992 computed (21 assays came from
  the cache because they share sequence and structure with an earlier one), 6,293 s on MPS.
  Three regions are a part of the target (ZIKV 291-794, BRCA2 1-2832, POLG_HCVJF 1982-2225);
  13 are longer than 1,022 residues. The slowest was POLG_CXB3N (2,185 residues, 851 positions,
  685 s).
- *Model gate:* 211 gated assays, 663,898 variants, lowest per-variant Spearman 0.999695,
  aggregate 0.4595 for both. With the two exceptions included (213 assays): ours 0.4600,
  ProteinGym's 0.4578.
- *Benchmark (213 assays):* Spearman 0.4600 [0.4417, 0.4827], AUC 0.7567, MCC 0.3581 (212
  assays each), NDCG@10% 0.7774, top-10% recall 0.2126. The published leaderboard value for
  SaProt (650M) is 0.457, over 217 assays and all rows including multi-mutants; it is not a
  target for a single-mutant aggregate.
- *SaProt against ESM-2 650M (paired, 213 assays):* clear for Stability (+0.0685 [+0.0418,
  +0.0952]), Expression (+0.0728 [+0.0333, +0.1063]) and Activity (+0.0319 [+0.0022, +0.0560]);
  not clear for Binding (+0.0502 [-0.0103, +0.1125]) or OrganismalFitness (+0.0046 [-0.0156,
  +0.0208]). Not corrected for multiple comparisons. `comparisons.parquet` now has 180 rows.
- *Upstream:* ProteinGym `main` (`144fe22`, 2026-03-25) has the corrected reference rows and an
  unchanged script, and v1.3 is still its latest score release. The draft report is
  `docs/upstream/proteingym_saprot_issue.md`; it has not been posted.
- *Tests:* 29 new fast tests (355 passing, 1 skipped; about 36 s) and 6 new slow tests (12
  passing in 235 s). `make lint` clean. `make pipeline-test` passes with the new wiring.
- *Not done yet:* a SaProt task has not run under Nextflow (the benchmark run used the CLI
  commands; the smoke test runs `esm2_8m` only). SaProt's wild-type marginal exists in the
  scorer but is not benchmarked and has nothing upstream to be checked against. `SaProt_35M_AF2`
  was not scored or checked for the same two errors. Full-target SaProt landscapes for the
  website are not built.

---

## Entry 16 — Z-score ensemble and the Phase 2 figures (Phase 2, 2026-10-13)

**Plain-language summary.** MutaScope now combines its two best models. ESM-2 650M reads the
sequence and SaProt also reads the structure, and their scores are not on the same scale, so
they cannot simply be added. Within each experiment, each model's scores are first rescaled to
have mean 0 and spread 1 (a z-score), and the two rescaled scores are averaged. No model runs
for this: the ensemble is built in seconds from the scores already on disk, for the 213
experiments both models scored. The result is honest rather than exciting. The ensemble's
overall Spearman is 0.4663, clearly better than ESM-2 650M (0.4144; paired difference +0.0519,
95% interval +0.0419 to +0.0615) but not shown to be better than SaProt alone (0.4600;
+0.0063, interval −0.0018 to +0.0145, which includes zero). It helps clearly in one of the
five function types, OrganismalFitness. The task also produced the four Phase 2 figures, as
SVG and PNG in `results/figures/`: model size against Spearman, bars per function type, the
ensemble's gain, and the fast (wild-type) against the masked marginal. Every number in them is
read from a results file; the drawing code computes nothing. The decisions are in ADR 0016.

**The 3 most important design choices.**

1. **The combination rule was fixed in advance and kept when another one scored higher.** The
   task specified per-assay z-score, then mean. A side check of two other rules on the same
   213 assays gave 0.4735 for the mean of raw scores and 0.4627 for the mean of percentile
   ranks, against 0.4663 for the z-score mean. Switching to the raw mean because it scored
   higher would be choosing the method on the test data, so the rule stayed, the check is
   written down in ADR 0016 as an ad hoc number without a CI, and CLAUDE.md now says not to
   change the rule for that reason. The case for standardising is the data: within an assay
   SaProt's score spread is between 0.50 and 3.43 times ESM-2's (median 1.04), so a raw mean
   weights the members differently in every assay.
2. **The ensemble is a score set like any other, built from written scores on the shared
   assays.** It is a model config with `members`, each a model and a method, named through
   `run_label`. `mutascope score ensemble` writes it in the score schema, and the existing
   `mutascope eval zero-shot` benchmarks it, so there is still one writer of benchmark results
   and the paired comparison needed no new code. It exists only where every member has scores:
   a member may lack an assay only if it is a documented structure exception, and the members
   must hold exactly the same variants. Its tracked runs record both members' pinned revisions.
3. **Figure code computes nothing, and the two kinds of interval look different.** A missing
   value or an uncompared pair stops the command with a message naming the config entry to
   add. To draw ESM-2, SaProt and the ensemble side by side, `mutascope eval shared` first
   writes each one's summary on the 213 shared assays (SaProt's rows come out identical to its
   own summary file, which checks the command). A score set's own CI is a grey whisker with a
   note that it does not compare score sets; a paired CI is a coloured line around the
   difference, with a filled marker when it excludes zero. The ensemble gain and the fast
   against masked comparison are drawn only as paired differences.

**Problems encountered and how they were solved.**

1. **The commit hook rewrote the figures.** *Found:* the commit of the results failed; the
   trailing-whitespace hook reported "Fixing" all four SVG files. *Cause:* matplotlib ends many
   SVG lines with a space (1,393 of the 2,279 lines of `model_size_vs_spearman.svg`). The hook
   removes them, so the committed file would not have been the file the command wrote, and
   every later redraw would have shown as a change. *Fix:* `save_figure` removes trailing
   whitespace itself before writing (commit `719c395`). *Verified:* the raw output with the
   spaces removed is equal to the new file; the four SVGs parse as XML; redrawing after the
   commit leaves `git status` clean; a test asserts no line ends in whitespace and that two
   draws are byte-identical.
2. **Three layout faults in the first drawings.** *Found:* by opening the PNGs. The row label
   "Organismal fitness (n = 77)" was cut off at the left edge, the x tick labels of the
   three-panel figure ran into each other, and a difference of −0.00008 was printed as
   "−0.000". *Fix:* a wider left margin, at most five ticks per panel, and "0.000" for a value
   that rounds to zero. *Verified:* by opening the redrawn files.
3. **A new test depended on a lucky draw.** *Found:* it failed on its first run. The test
   meant to show that a raw mean follows the member with the larger scores while the z-score
   mean does not, using two random vectors of four values; with that seed the two rankings
   happened to coincide. *Fix:* fixed vectors (1, 2, 3, 5 and 2, 1, 4, 3) for which the
   rankings differ by construction.

**Supporting details.**
- *Ensemble (213 assays, 665,793 single mutants):* Spearman 0.4663 [0.4467, 0.4902], AUC
  0.7616, MCC 0.3657 (212 assays each), NDCG@10% 0.7777, top-10% recall 0.2137.
- *Against SaProt by function type (paired, Spearman):* OrganismalFitness +0.0295 [+0.0202,
  +0.0403], the only clear one; Activity +0.0082 [−0.0030, +0.0219]; Binding +0.0038
  [−0.0276, +0.0313]; Expression −0.0100 [−0.0253, +0.0070]; Stability −0.0001 [−0.0119,
  +0.0112]. Overall on the other metrics: MCC +0.0076 [+0.0004, +0.0141]; AUC, NDCG and
  top-10% recall include zero. Against ESM-2 650M the gain is clear in every function type.
  Not corrected for the number of comparisons.
- *Per assay:* the ensemble is above both members in 97 of 213 assays, above SaProt in 131,
  above ESM-2 in 177, below both in 2. The two members' scores have a median per-assay
  Spearman of 0.80 with each other (lowest 0.16).
- *Fast against masked (217 assays, from entry 14's comparisons):* the figure adds the
  function types. The masked marginal's advantage is not clear for Binding at any size, nor
  for Expression at 150M and 650M, nor for Stability at 650M (−0.0012 [−0.0058, +0.0039]).
- *Files:* `comparisons.parquet` now has 240 rows (8 pairs; the first 180 are unchanged, which
  was checked against the previous file). `shared_summary.parquet` has 90 rows.
- *Dependency:* matplotlib, as the new optional extra `figures`. It was already installed as a
  dependency of MLflow; Altair, used by the EDA notebook, needs another package to write SVG
  or PNG.
- *Tracking:* four runs (score, benchmark, compare, shared summary) with `code_version`
  `ff03678`. The figure command is not tracked: it evaluates nothing.
- *Tests:* 16 new fast test cases (13 new tests, and the generic "takes Hydra overrides" check
  now covers the three new commands): 371 passing, 1 skipped, about 39 s. `make lint` clean.
  No slow test was added or run; no model code changed.
- *Not done yet:* the five commands (`score ensemble`, `eval zero-shot`, `eval compare`,
  `eval shared`, `eval figures`) are not Nextflow processes; the two-assay test profile cannot
  fill a figure with five function types. The raw-mean ensemble has no paired comparison, and
  by the author's decision it will not get one: the z-score rule stands. The ensemble is defined per assay and has
  no landscape for the website. Full masked landscapes at every position (the other open
  Phase 2 item) are untouched.

---

## Entry 17 — Phase 2 close-out (2026-10-13)

**Plain-language summary.** Phase 2 is closed, with one item left open on purpose. Four
pretrained models now score every mutation that the 217 experiments measured: ESM-2 in three
sizes, which read only the sequence, and SaProt, which also reads the structure and so covers
the 213 experiments that have one. A fifth score set averages the two best. The benchmark
harness reproduces ProteinGym's published number from ProteinGym's own scores, and our scores
match theirs variant by variant, so the results can be trusted as far as theirs can. The
ranking is clear and unsurprising: bigger ESM-2 models do better (0.3113, 0.3820, 0.4125
overall Spearman), SaProt does better than ESM-2 650M on the assays they share (0.4600 against
0.4144), and the ensemble (0.4663) beats ESM-2 clearly but is not shown to beat SaProt. This
task wrote no code. It checked every Phase 2 roadmap item against the repository, wrote down
what is unchecked or weak, put the headline table in ADR 0017 and pointed CLAUDE.md at Phase 3.
What is not done is full landscapes at every position: the benchmark needs only the measured
positions, so the rest was never computed, and the website export in Phase 4 will need it.

**The 3 most important design choices.**

1. **Close the phase with the open item named, not ticked.** The done-when line asks for full
   landscapes for all assays. The cache was counted row by row instead of trusting the
   checklist: each ESM-2 size has 37,753 of 62,671 residues scored, SaProt 35,992 of 64,878,
   and the five MSA-region assays have no full-target entry at all. Phase 3 does not need the
   missing rows (its features are at measured positions), so the item stays open, is linked
   from the Phase 4 export that needs it, and the roadmap says the phase closed without it.
2. **One table, with the assay count in every row.** The headline table has a 217-assay row
   and a 213-assay row for ESM-2 650M, because 0.4125 and 0.4144 are the same model on
   different assays, and only the second may sit beside SaProt and the ensemble. Each row's CI
   is its own; every statement that one score set is better than another comes from the paired
   comparisons printed under the table.
3. **The gates were rerun for the close instead of quoted.** No model code changed since entry
   15, so the earlier results still applied, but a phase summary that says "gates pass" should
   rest on a run of the closing commit. `make test-slow` ran all four model gates and the two
   replay tests again from the cache.

**Problems encountered and how they were solved.**

1. **The open roadmap item undercounted what is missing.** *Found:* by loading all 1,311 cache
   entries, counting the NaN rows per model, and matching entries to models through the cache
   log. The roadmap said "the remaining 24,918 residues". That is right for each ESM-2 size and
   leaves SaProt out: its 185 benchmark entries hold 64,878 residues, of which 28,886 are
   unscored. *Fix:* the roadmap item and ADR 0017 give both figures. *Verified:* 62,671 −
   37,753 = 24,918 for each of the three ESM-2 sizes, 64,878 − 35,992 = 28,886 for SaProt, and
   no entry has a partly filled row.
2. **Four cache entries looked like strays.** *Found:* the same count gave 189 SaProt entries
   where the benchmark accounts for 185. *Cause:* none is a fault. The four are the replay
   tests' inputs, written at 21:09 UTC on 2026-10-12 with regions named `upstream-error:…` (139
   positions, 21 s), so they cannot be read as benchmark entries. Nothing was changed; ADR 0017
   counts them separately (1,307 benchmark entries and 4 replay entries).

**Supporting details.**
- *Headline table, paired comparisons and gate results:* ADR 0017. Every value is read from
  `results/zero_shot_<label>_single_summary.parquet`, `results/zero_shot/shared_summary.parquet`,
  `results/zero_shot/comparisons.parquet` and `results/model_gate_*.parquet`; the table was
  generated by a script, not typed.
- *Assay counts:* ESM-2 35M, 150M and 650M, masked and wild-type marginal, 217 assays (43
  Activity, 13 Binding, 18 Expression, 77 OrganismalFitness, 66 Stability). SaProt and the
  ensemble, 213 (42, 13, 18, 74, 66): one Activity and three OrganismalFitness assays have no
  usable structure.
- *Unchecked:* full masked landscapes at every position; ESM-1v and ESM C 300M (stretch); the
  `docker` profile from Phase 1.
- *Weak:* the ensemble, comparison, shared-summary and figure commands are not Nextflow
  processes; CI runs neither gate (the harness gate needs the 1.9 GB score archive, the model
  gates need MPS and the cache); the ensemble's gain over SaProt includes zero; Binding has 13
  assays; only three score sets have a 213-assay summary; the upstream SaProt report is a
  draft; the committed 35M run report shows the last of three invocations.
- *For Phase 3:* the fold assignments are downloaded and checksummed but not joined to the
  processed variants. Position embeddings are not cached. The ensemble's z-score uses every
  variant of an assay, test folds included, so as a supervised feature it must be rebuilt per
  fold or replaced by the two member scores.
- *Tests:* `make lint` clean (ruff, pyright 0 errors). `make test`: 371 passed, 1 skipped.
  `make test-slow`: 12 passed in 270 s, with no new cache entry and no change to any tracked
  file.
- *Docs changed:* ADR 0017, CLAUDE.md (current phase), ROADMAP (open item, close note, Phase 4
  link), README (status line), PROJECT_MAP (step 17 and a Phase 2 index).

---

## Entry 18 — Full landscapes at every residue (Phase 2 leftover, 2026-10-13 to 10-14)

**Plain-language summary.** Until now each model had scored only the mutations the experiments
measured, about 60% of the residues. The website will show every possible mutation of a
protein, so every residue needs a score. This task filled in the rest for all four models
(ESM-2 35M, 150M, 650M and SaProt): 148,962 more positions, 11 h 20 min of laptop GPU time. For
the few proteins where the benchmark scores only part of the sequence (five for ESM-2, three for
SaProt), each model also scored the whole protein, stored separately and labelled `full`, so
the benchmark's version and the website's version never mix. The scores the benchmark already
used were not touched: every one of them is bit-identical afterwards, and the model gates pass
as before. The run had to be stopped three times on request, and
each restart continued exactly where it left off. A new command checks, without loading any
model, that all 882 landscapes are complete.

**The 3 most important design choices.**

1. **Save every slice, not every protein.** The benchmark run saved a protein when it was done,
   which lost at most 455 s. Full SaProt landscapes of 3,000+ residue proteins take up to 82
   minutes each, so the runner now asks the model for about 131,000 tokens of work at a time
   (128 positions of a 1,024-token ESM-2 window, 38 positions of a 3,423-residue SaProt input)
   and each request is written to the cache before the next starts. The slicing lives in the
   runner, not the scorer, so the code the model gates verified did not change. *Evidence:* four
   stops (one deliberate `SIGKILL`, three for the laptop), and the positions in the cache log
   add up to exactly the missing count for every model: nothing was scored twice.
2. **Two landscapes where the benchmark region is partial, both complete.** The benchmark's
   landscape of `KCNH2_HUMAN_Kozek_2020` covers residues 535–565, the website's covers all
   1,159. They hold different numbers for the same residue (the model sees a different input),
   so they are separate cache entries with different region labels, and the tables have a
   `benchmark` and a `full_target` column. Finishing the benchmark-region one as well cost 52
   minutes of SaProt time (BRCA2), and keeps "the landscape the gate verified" whole.
3. **A check that loads no model.** `check-landscapes` rebuilds each landscape's cache key from
   the config (Hub id, revision, sequence, structure tokens, method, region) and counts the
   residues with all 20 values finite. It runs in seconds, so it can gate the pipeline and the
   Phase 4 export, and it cannot be fooled by a run log that says "done".

**Problems encountered and how they were solved.**

1. **The run had to be stopped three times.** *Found:* the author asked for it to stop (once for
   a flat laptop battery, twice more at set times). *What happened:* stops at ESM-2 150M
   20,289 of 36,734 positions, ESM-2 650M 12,201 of 36,734 and then 19,072 of the 24,533 left.
   *Fix:* none needed; each restart was the same command. *Verified:* the restarts planned
   16,445, 24,533 and 5,461 positions, exactly the remainders; no `*.tmp` file was left; the
   cache-log totals equal the missing counts (36,734 per ESM-2 size, 38,760 for SaProt).
2. **The smoke test's cache was private to each task.** *Found:* while wiring
   `CHECK_LANDSCAPES`: the test profile set `scoring.cache_dir=logprob_cache`, a path relative
   to each Nextflow task's work directory, so the check would look at an empty cache and fail
   although `LANDSCAPES` had filled its own. *Fix:* one cache for the run under
   `pipeline/results/logprob_cache`, deleted by `make pipeline-test` first so the smoke test
   still always runs the model. *Verified:* `make pipeline-test`, 13 tasks; the check reports
   2/2 landscapes and 396/396 residues.
3. **A cache path with a space would have split the command line.** *Found:* in review before
   the first smoke run, not by a failure: the absolute cache path contains the project folder
   `Developmet Projects`, and `params.hydra_overrides` is pasted into every command unquoted, so
   the override would have become two arguments. *Fix:* a separate `params.cache_dir`, quoted on
   its own in each of the three scoring modules. *Verified:* the smoke run above, from this
   folder.
4. **A Hydra list override cannot replace a mapping.** *Found:* the new CLI test failed with
   "Error merging override eval.scoring_region.msa_region_assays=[C]"; the key holds a mapping
   (assay → evidence), and `{C: toy}` was refused because the config is in struct mode.
   *Fix:* delete the key, then add it: `~eval.scoring_region.msa_region_assays
   +eval.scoring_region.msa_region_assays=[C]`. *Verified:* the test passes.

**Supporting details.**
- *Run, model time from the cache log:* 35M 2,123 s, 150M 2,931 s, 650M 9,926 s, SaProt
  25,847 s (six entries over 2,000 residues took 74.7% of SaProt's time). Total 40,827 s.
- *Coverage:* each ESM-2 size 222/222 landscapes of 217 assays (5 full-target beside a partial
  benchmark region), SaProt 216/216 of 213 (3); `results/data/landscape_coverage.parquet`.
- *Benchmark unchanged:* 337,403 previously scored cache rows bit-identical in 1,311 entries;
  the four masked score sets rebuilt from the cache equal the committed ones exactly (0
  positions computed); `make test-slow` 12 passed in 244 s; no tracked file in `results/`
  changed.
- *SaProt full targets:* the pLDDT rule leaves a 3Di letter at 6.0% (`POLG_HCVJF`), 13.8%
  (ZIKV) and 16.5% (BRCA2) of residues, so these three landscapes are close to sequence-only.
  They have no upstream scores to compare with.
- *Time estimate:* first estimates within 10% of the actual time (SaProt 7 h 25 min against
  7 h 10 min).
- *Run reports:* `landscape_run_<model>.parquet` shows the last invocation only (35M 35,818,
  150M 16,445, 650M 5,461, SaProt 38,760 positions); the cache log keeps the totals.
- *Tests:* `make lint` clean; `make test` 385 passed, 1 skipped (12 new in
  `tests/eval/test_landscapes.py`); `make pipeline-test` passes with the two new processes.
- *Not done:* the Phase 4 export itself; the stretch models.

---

## Entry 19 — Supervised cross-validation harness and a leakage canary (Phase 3, 2026-10-14)

**Plain-language summary.** Phase 3 trains a model per experiment on the measured data and
checks it on mutations it never saw. Before any real model, this task built and tested the
machinery for that. ProteinGym splits each experiment's mutations into 5 groups ("folds") in
three ways: at random, by interleaving positions, and by cutting the protein into 5 stretches.
A new command attaches those folds to our cleaned mutations and refuses to run if any mutation
has no fold, or more than one. The training loop then predicts each fold with a model trained
on the other four, and scores the predictions the way ProteinGym does: one Spearman and one
error (MSE) over all five folds' predictions together. A baseline that always predicts the
average proves the loop runs on all 217 experiments in about 3 seconds. A "canary" test
shuffles the labels the model trains on and checks that the score drops to about zero; if it
does not, the test labels are leaking into the predictions. The reasoning is in ADR 0019.

**The 3 most important design choices.**

1. **Score the pooled predictions, as ProteinGym does, after reading the code that does it.**
   ProteinGym's summary script only averages numbers it is given; the per-experiment number is
   computed in `merge_supervised.py` (Spearman over all out-of-fold rows), on targets
   standardised with the training folds' mean and standard deviation in ProteinNPT. Both are
   copied into the tests as oracles, and our values match to 1e-12. *Evidence that it matters:*
   on a small probe model, pooled and mean-of-folds Spearman differ by more than 0.05 in 45 of
   217 experiments under the contiguous split, by up to 0.49, while their averages differ by
   only 0.015.
2. **Folds are joined on `(assay_id, mutant)` and checked twice; labels never come from the
   fold files.** The fold files carry scores too, but with one digit fewer and with empty
   labels in 6 assays (ADR 0005), so only keys and folds are kept. The schema also checks what
   the position-based splits must satisfy: one fold per position, and contiguous folds in
   sequence order. *Evidence:* all 696,311 variants matched exactly once.
3. **The canary judges the mean over 20 permutations against its standard error.** One
   shuffled run reached |ρ| = 0.165 on the canary assays, because random weights on informative
   features still correlate with the truth by chance. The mean over 20 runs stays within 1.72
   standard errors of 0 in all 9 assay × split cases, while a loop that fits on the test rows
   gives +0.50 to +0.80. *Limit, measured and documented:* a label-derived feature (position
   mean of the scores, test rows included) passes the canary, so feature builders are never
   given the labels at all.

**Problems encountered and how they were solved.**

1. **A constant baseline got a "defined" Spearman, between −0.67 and +0.91.** *Found:* the
   first full run printed a Spearman for all 217 assays, where a constant predictor must give
   none. *Cause:* targets are standardised per fold, so the training mean is 0 only up to
   rounding (median 5e-17, at most 4.1e-15, nonzero in 3,070 of 3,249 training sets). That
   residue differs between folds, so the pooled predictions were five slightly different
   constants, and Spearman ranked them; where folds differ in their average label, that ranking
   can line up with the truth, hence values far from 0. *Fix:* the baseline rounds its
   mean to 12 decimals (and turns −0.0 into 0.0). *Verified:* Spearman is undefined in all 217
   assays and the MSE is unchanged; a regression test fails without the rounding.
2. **The modulo split is not "residue number mod 5".** *Found:* checking the fold files before
   writing the schema: `position % 5` matches only 53.0% of variants. *Cause:* ProteinGym takes
   the rank of the position among the mutated positions, mod 5 (true in all 217 assays).
   *Fix:* the schema does not assume either formula; it checks that a position never spans two
   modulo folds, which holds under both. *Verified:* all 217 assays pass.
3. **Two assays have only 4 folds.** *Found:* the same check: `F7YBW8_MESOW_Aakre_2015` (37
   variants) and `SPG1_STRSG_Wu_2016` (76) mutate 4 positions, so every split has 4 folds.
   *Fix:* the loop iterates over the folds present instead of 0–4. *Verified:* `data folds`
   reports "2 assays with 4 folds, 215 assays with 5 folds" per split, and a unit test runs a
   4-fold assay.
4. **Training-label permutation cannot see a target-encoding leak.** *Found:* while choosing
   the canary's statistic, by injecting the leak on purpose: the permuted mean stayed near 0
   (all 9 cases within 1.70 standard errors). *Cause:* fit on shuffled labels, the model gives
   the leaking feature a weight of random sign, so the leak shows up as spread, not as a shift.
   *Fix:* not a canary change but a structural rule: feature builders receive only the variant
   keys, enforced by `build_features` and a test. *Verified:* the test sees exactly
   `assay_id, mutant, position, wt_aa, mt_aa`.

**Supporting details.**
- *Baseline (ProteinGym aggregate of pooled MSE, 217 assays):* random 1.0310, modulo 1.1182,
  contiguous 1.1450. `results/supervised/cv_training_mean_none.parquet`; MLflow experiment
  `supervised`.
- *Canary assays:* `ERBB2_HUMAN_Elazar_2016` (Expression, 326), `OTU7A_HUMAN_Tsuboyama_2023_2L2D`
  (Stability, 635), `NPC1_HUMAN_Erwood_2022_HEK293T` (Activity, 637). Probe Spearman with true
  labels 0.265–0.763; permuted means within ±0.021.
- *Not yet done:* the fold step is not a Nextflow process (it needs the fold files in the staged
  layout); AUC/MCC/NDCG/recall, bootstrap CIs and paired comparisons for supervised results
  come with the first real model.
- *Tests:* `make lint` clean; `make test` 434 passed, 1 skipped (Foldseek not on PATH); 49 new.

## Entry 20 — Feature store for the supervised models (Phase 3, 2026-10-14)

**Plain-language summary.** The supervised models need numbers that describe each mutation,
and the final models will predict every possible mutation of a protein, not only the ones an
experiment measured. This task computed those numbers for all 1,637,401 single substitutions
of the 217 experiments' proteins (19 per residue). Per mutation: which amino acid is replaced
and by which, how similar the two are (BLOSUM62), where in the protein it is, the zero-shot
score of ESM-2 650M and SaProt, the 20 amino-acid probabilities both models give at that
position, a 1,280-number summary ("embedding") of the position from each model, and its
structure (pLDDT, surface exposure, contacts). The embeddings needed one model run per
protein, 380 in total and about a minute of model time; everything else came from the existing
caches. A residue's numbers are stored once, not 19 times, so the store is 900 MB where a plain
table would be 8.8 GB (sequence features) and 16.8 GB (with structure). No step reads the
measured fitness values. The reasoning is in ADR 0020.

**The 3 most important design choices.**

1. **Per-residue data is stored once and expanded per assay when a model asks for it.** 98%
   of a row (log-probabilities, embedding, structure) depends only on the residue. Storing it
   per residue and repeating it for that residue's 19 variants only when one assay's matrix is
   built keeps the store at 900 MB. *Evidence:* the largest assay (ZIKV, 65,037 variants)
   assembles in 0.2 s, 0.69 GB for `seq_struct`, against 16.8 GB for all assays at once.
2. **Inside the benchmark region, the benchmark landscape; outside it, the full target.** Five
   ESM-2 and three SaProt assays score only part of their target (ADR 0012, 0015), and their
   full-target landscapes hold different numbers for the same residue (ADR 0018). Training rows
   must see the numbers the model gate verified. *Evidence:* every measured variant is inside
   its model's benchmark region (0 of 696,311 outside for ESM-2, 0 of 665,793 for SaProt), and
   the stored scores of all of them equal the committed benchmark score sets exactly. The
   10,868 residues that read a full-target landscape have no measured variant.
3. **The store reads keys, never labels, and names variants by `(position, mt_aa)`.** The
   leakage guard of ADR 0019 is structural: `features build` selects only
   `assay_id, position, wt_aa, mt_aa` from the processed variants, to check every measured
   variant is in the store with the same wild type. Using the 0-indexed position as the key
   also means no 1-indexed mutation string is ever formatted, so `parse_mutation` stays the
   only place positions are converted. *Evidence:* a test builds the store from two variant
   tables with different labels and gets byte-identical files.

**Problems encountered and how they were solved.**

None worth documenting. The estimate made before building held: 192 and 188 passes as
counted, 900 MB against the 0.9 GB estimated.

**Supporting details.**
- *Embedding:* mean of the last 4 `hidden_states` (blocks 30–32 plus block 33 after the final
  LayerNorm, as in fair-esm) at token i + 1, one unmasked pass per cache entry; ESM-2
  sequences over 1,022 residues use the wild-type marginal's tiles. Alignment check: a linear
  probe recovers the wild type from row i with 99.8% (ESM-2) and 100.0% (SaProt) accuracy on
  held-out proteins, against 77.8% and 82.7% from row i + 1.
- *Runs:* `features embed` 31 s (ESM-2 650M) and 27 s (SaProt) of model time, MLflow
  experiment `features`; `features build` 8.5 s. Reports:
  `results/data/embedding_run_{esm2_650m,saprot_650m_af2}.parquet`,
  `results/data/feature_store.parquet`.
- *Feature sets* (`conf/features/default.yaml`): `seq` 1,343 columns on 217 assays,
  `seq_struct` 2,647 columns on 213. In `seq_struct`, structure features are NaN at 6,294
  unmeasured residues outside the structure of 3 assays; imputing them belongs inside the
  training folds.
- *Not yet done:* `train cv` still runs the `none` set; it needs per-assay assembly inside
  `cv_metrics_table`, which comes with the augmented ridge. The ensemble score is not a feature
  (its z-score is defined over measured variants only). The two commands are not Nextflow
  processes yet.
- *Dependencies:* none added (BLOSUM62 from biotite, already a dependency).
- *Tests:* `make lint` clean; `make test` 465 passed, 1 skipped (Foldseek not on PATH); 31 new.

## Entry 21 — Ridge rung: one-hot, augmented and all-feature ridge (Phase 3, 2026-10-14)

**Plain-language summary.** The first real supervised models. Each experiment's measured
mutations are split into ProteinGym's five folds three ways (random; by position, interleaved;
by position, in contiguous blocks), and a model trained on four folds predicts the fifth. Three
ridge regressions were trained on all 217 experiments: one that only knows "which amino acid at
which position" (one-hot), the same plus the zero-shot score of ESM-2 650M (Hsu et al.'s
"augmented" model), and one on every feature in the store (`seq`, 217 experiments; `seq_struct`
with structure and SaProt, 213). How strongly each model is held back (the ridge penalty) is
chosen by a second, inner cross-validation inside the four training folds, so the test fold
never influences it. Each model is compared with the zero-shot score evaluated on exactly the
same test folds, with a paired bootstrap. The headline: memorising positions works only when
the test positions were seen (one-hot: 0.608 under random, ~0 otherwise); adding the zero-shot
score does not help on unseen positions; the store features do (`seq`: 0.711 / 0.538 / 0.475,
above zero-shot under every split). Structure and SaProt help most where it is hardest: `seq_struct` scores 0.721 / 0.593 /
0.553 on 213 experiments, +0.076 over `seq` under contiguous. The reasoning is in ADR 0021.

**The 3 most important design choices.**

1. **The penalty is tuned on the training folds, split the way the test split is.** Models now
   receive each training row's fold, and ridge holds each training fold out in turn to choose α.
   Under the contiguous split, α is therefore tuned for predicting an unseen stretch of protein,
   the same task the test fold sets. *Evidence:* a test replaces every label of one test fold
   and requires its predictions to stay bit-identical (unit tests, all schemes; and 20 of 20
   folds on 4 real assays). The canary passes on all 36 real cases.
2. **The site one-hot is two numbers per variant, not a matrix.** Hsu et al.'s encoding has
   20 × L columns (68,460 for ZIKV); a single mutant differs from the wild type in two of them.
   Storing +1 at (position, mutant) and −1 at (position, wild type) and solving with conjugate
   gradients makes one-hot ridge on all 217 assays take 107 s. *Evidence:* the solver matches
   the closed-form solution on the densified matrix to 1e-6.
3. **Zero-shot is run through the same loop.** The comparator is the zero-shot score scored on
   the same test folds and pooled the same way, so a paired bootstrap compares like with like.
   *Evidence:* it differs from the Phase 2 zero-shot number by at most 0.002 (ESM-2 0.412–0.414
   vs 0.4125), but the pairing needs identical rows.

**Problems encountered and how they were solved.**

1. *Augmented ridge looked too good.* Under random it scored 0.676 against 0.523–0.565 for
   ProteinGym's five "+ One-Hot" baselines (modulo and contiguous agreed within 0.03). Treated
   as a leak until shown otherwise. Checked: replacing a test fold's labels with noise left its
   predictions bit-identical in 20 of 20 real folds; the canary passed 9 of 9 informative cases;
   our plain one-hot matches theirs (0.608 vs 0.582). The gap is theirs: their augmented
   baselines score below their own one-hot under random (0.565 < 0.582), so their learner drops
   one-hot signal, which ours, with the score unpenalised, cannot. Reported as a sanity range,
   not a gate.
2. *Many penalties landed on the ends of the grid* (one-hot: 1,196 low and 1,019 high of 3,249
   fits). Found from the edge counts `train cv` prints. Widening the grid to 10⁻⁶ … 10⁹ changed
   pooled Spearman by at most 0.0010 (augmented), 0.0003 (one-hot, random) and < 0.00005
   (`seq`): flat ends, explained in ADR 0021, so the grid stayed 10⁻³ … 10⁶.
3. *One-hot ridge's modulo and contiguous numbers looked like small real values* (0.006,
   −0.014; ProteinGym 0.022, 0.059). All 1,083 of 1,083 test folds there are one constant
   prediction: an unseen position's one-hot coefficients are exactly 0. The pooled value ranks
   five fold constants; it is reported as zero and the canary marks those cases uninformative.
4. *`train leaderboard` exited 1 after writing its table.* MLflow rejects `+` in metric names
   ("ESM-1v + One-Hot Encodings"). The test ran with tracking off and missed it; the key is now
   sanitised and the test runs with tracking on.
5. *The fold loop's interface changed for every model.* `fit(x, y)` became
   `fit(x, y, groups)` so ridge could see the training folds; the training mean, the test
   probe, the canary's oracle and the spies were updated, and a test checks the groups passed
   are exactly the training rows' folds and never the test fold.

**Supporting details.**
- *Runs* (M4 Pro, CPU): zero-shot 2 s each, one-hot 107 s, augmented 153 s, `seq` 1,037 s,
  `seq_struct` 4,256 s. MLflow experiment `supervised`. Results in `results/supervised/`:
  `cv_<label>.parquet`, `cv_<label>_hyperparameters.parquet`, `canary_<label>.parquet`,
  `supervised_summary.parquet`, `supervised_comparisons.parquet`, `leaderboard.parquet`.
- *Augmented ridge on unseen positions is zero-shot with a learned sign:* within-fold Spearman
  equal to zero-shot's in 1,057 / 1,054 of 1,083 folds (contiguous / modulo), exactly negated in
  the rest; paired difference −0.006 / −0.003.
- *Leaderboard files:* ProteinGym's supervised Spearman summary and per-scheme assay-level
  tables, pinned at the release commit, saved with a `Supervised_` prefix (the summary shares
  the zero-shot summary's file name); sha256 in `data/manifest.yaml`.
- *Not yet done:* the commands are not Nextflow processes; LightGBM, MLP, conformal intervals.
- *Dependencies:* none added.
- *Tests:* `make lint` clean; `make test` 492 passed, 1 skipped (Foldseek not on PATH); 27 new.

---

## Entry 22 — LightGBM rung, parallel `train cv`, SHAP (Phase 3, 2026-10-15 to 10-16)

**Plain-language summary.** The second supervised model is gradient-boosted trees (LightGBM) on
the feature store. Its settings are tuned by Optuna, with 10 tries per test fold. Every try is
scored only on the training folds, split the same way as the test split, so the test fold never
influences tuning. A second version first compresses each 1,280-number embedding to 32 numbers
(PCA), learned only from the rows the model trains on. Because this is hundreds of times more work
than ridge, `train cv` first learned to run experiments in parallel on all 13 spare cores, under a
memory budget, and to resume after an interruption. The headline is not the expected one:
LightGBM beats ridge only when test mutations sit at positions seen in training (random split,
+0.013 to +0.024); on unseen positions (modulo, contiguous) it is significantly worse than ridge
(−0.011 to −0.025). It beats zero-shot ESM-2 and SaProt everywhere. A post-hoc run without
embeddings shows the embeddings are not to blame. SHAP shows LightGBM leans on the zero-shot
scores and SaProt's structure-aware blocks, and almost not at all on the explicit structure
features. The reasoning is in ADR 0022.

**The 3 most important design choices.**

1. **Tuning stays inside the training folds, per test fold.** Each outer fold runs its own 10
   Optuna trials, scored by an inner CV over the training folds split like the outer scheme; the
   PCA is refitted for every inner split and for the final fit. Sharing one search across the
   outer folds would leak, because another fold's training rows contain this fold's test rows.
   *Evidence:* changing every label of a test fold leaves its predictions bit-identical (all
   schemes, with and without PCA); changing a test fold's embeddings leaves that fold's PCA
   bit-identical while every other fold's changes; the canary passes on both models.
2. **Parallelism across assays, limited by memory, not cores.** Assays run in spawned,
   single-threaded workers, largest first, with at most 160,000 variants in flight; results are
   independent of the number of workers. *Evidence:* ridge `seq_struct` with 13 jobs reproduced
   the committed results (same 3,189 penalties, Spearman within 1.2e-7) in 1,913 s instead of
   4,256 s, and the 8.9-hour LightGBM run peaked at 6.6 GB with no swapping.
3. **The budget was cut where it cost no accuracy, and nothing was tuned on test results.** The
   pilot projected 35–85 hours. Profiling one fold showed row-wise histograms give identical
   models 1.3× faster and a learning-rate floor of 0.05 (300 rounds) gives 3.8× at test Spearman
   0.899 vs 0.901; the trial budget stayed at 10 per fold. After the first results, no
   hyperparameter was changed; the shortfall against ridge is reported as found.

**Problems encountered and how they were solved.**

1. *The first parallel run swapped.* Found when the workers sat at 3–17% CPU with an empty log
   after 10 minutes. Largest-first scheduling put the 13 biggest assays (10,972–16,897 variants)
   in flight at once; ridge peaks near 2 GB per worker there, ~26 GB on a 24 GB machine. Fix: a
   memory budget in variants; when a large assay does not fit, smaller ones fill the free
   workers. Verified: the rerun used 9.1 GB with 51% of memory free and finished in 1,913 s.
2. *LightGBM and torch segfaulted together.* LightGBM tests passed alone and crashed after the CLI
   tests. Torch loaded first → LightGBM exit 139 on `Dataset.construct()`; the first fix, loading
   LightGBM first, moved the crash into ESM-2's forward pass in `test_landscapes.py`. Cause: two
   OpenMP runtimes (Homebrew's `libomp` for LightGBM, torch's own). Fix: no process loads both —
   the CLI imports neither at import time, LightGBM tests run in their own pytest process, and a
   conftest guard fails a test that breaks the rule. Verified: the regression test fails with
   SIGSEGV (−11) when the fix is removed; `make test` passes.
3. *LightGBM would not import on macOS.* `Library not loaded: @rpath/libomp.dylib`: the wheel
   expects Homebrew's OpenMP. Fix: `brew install libomp` (recorded in ADR 0022).
4. *Spawned test workers could not import the test helpers.* `ModuleNotFoundError: No module
   named 'tests'`: pytest's importlib mode does not put the repository on `sys.path`, and a
   `PYTHONPATH` set for the children was overwritten, because spawn copies the parent's
   `sys.path`. Fix: the parallel tests prepend the repository to the parent's `sys.path`.
5. *The pilot projected 35–85 hours against a 12-hour budget.* 0.55–2.25 s per variant per
   worker on 7 finished pilot assays. Profiled one outer fold (table in ADR 0022), adopted
   row-wise histograms and the learning-rate floor, and re-piloted: about 3× faster on every
   assay (RBP1 1,992 → 535 s, OTC 4,308 → 1,580 s). The full `lgbm` `seq_struct` run still took
   8.9 h rather than the estimated 6.5 h: with all 13 workers busy each ran at 0.63 s per variant
   instead of 0.45 (shared memory bandwidth, 4 efficiency cores). `lgbm` on `seq` was dropped.
6. *The parallel speedup was 2.2×, not 13×.* Not a bug: the "serial" ridge run already used
   several cores through Accelerate's BLAS (the parallel run used 18,187 CPU-seconds).
7. *SHAP gave the ESM-2 embedding 27% on the smallest assay.* Investigated before reporting, as
   overfitting was suspected: the share comes from 17 of 1,280 columns, and no supervised model
   learns SCN5A (out-of-fold Spearman 0.013–0.095 under random, below SaProt zero-shot's 0.221).
   Reported as memorisation of 224 noisy labels, not as feature biology.

**Supporting details.**
- *Results* (pooled Spearman, random / modulo / contiguous): `lgbm_pca` `seq_struct` 0.745 /
  0.582 / 0.532; `lgbm` `seq_struct` 0.734 / 0.568 / 0.530; `lgbm_pca` `seq` 0.731 / 0.531 /
  0.465; post-hoc `seq_struct_noemb` 0.741 / 0.560 / 0.520. Paired against ridge `seq_struct`:
  `lgbm_pca` +0.024 / −0.011 / −0.020, `lgbm` +0.013 / −0.025 / −0.023 (all CIs exclude 0).
  Against zero-shot SaProt: `lgbm_pca` +0.287 / +0.124 / +0.072.
- *Runs* (13 jobs): `lgbm_pca` `seq_struct` 1,832 s, `lgbm_pca` `seq` 876 s, `lgbm` `seq_struct`
  32,054 s, post-hoc 544 s. Canary: `lgbm_pca` 9 / 9 passed, all informative, max |z| 1.76;
  `lgbm` 9 / 9, all informative, max |z| 2.16.
- *Files:* `results/supervised/cv_{lgbm,lgbm_pca}_*.parquet`, `shap_lgbm_seq_struct.parquet`,
  `canary_lgbm*_seq_struct.parquet`, updated `supervised_summary.parquet` and
  `supervised_comparisons.parquet`; `results/figures/shap_lgbm_seq_struct.{svg,png}`.
- *Dependencies:* `lightgbm` 4.7.0 (gradient boosting; its TreeSHAP replaces the `shap`
  package), `optuna` 5.0.0 (seeded search). macOS needs `brew install libomp`.
- *Consequence:* the deployed model is chosen on modulo and contiguous splits, because the website
  predicts unmeasured positions; the current leader is ridge `seq_struct`, not LightGBM (ADR 0022,
  "Consequence for the deployed model").
- *Not yet done:* MLP rung; the `train` commands are not Nextflow processes.
- *Tests:* `make lint` clean; `make test` 520 passed, 1 skipped (Foldseek not on PATH), then 15
  LightGBM tests in their own process; 43 new.

---

## Entry 23 — MLP head, MSE vs RankNet (Phase 3, 2026-10-16)

**Plain-language summary.** The third supervised model is a small neural network (about 143,000
weights) on the same features as ridge and LightGBM, with each 1,280-number embedding compressed
to 32 numbers. Its whole design was written into the config before any result: architecture,
optimiser, learning rate, batch size, epoch budget. It was trained two ways, by plain squared
error and by a ranking loss (RankNet) that only asks it to put fitter variants above less fit
ones of the same assay. Training stops when a held-out training fold stops improving, a fold split
the same way as the test split, and the network is then retrained on all training rows. Assays
under 300 variants are skipped (15 of 213), a cut set from the ridge and LightGBM results before
the network ever ran. The run took 17 minutes. The result matches LightGBM's: the network beats
ridge only on positions it has seen (+0.026). On unseen positions it ties ridge (modulo) or is
worse (contiguous, −0.011). It beats LightGBM on unseen positions (+0.016 under modulo) and
zero-shot SaProt everywhere. The ranking loss is no better than squared error, and worse under
modulo. The reasoning is in ADR 0023.

**The 3 most important design choices.**

1. **Early stopping on one training fold, split like the outer scheme, then refit.** The
   validation set is one of the training folds, so under modulo and contiguous it holds positions
   the network never trained on, which is the situation the test fold poses. The network is then
   refit on all training rows for the chosen epoch count, as LightGBM is for its rounds, so every
   rung's final model sees 80% of the assay. *Evidence:* a test checks the validation and training
   positions are disjoint exactly under modulo and contiguous; changing every label of a test fold
   leaves its predictions bit-identical (both losses, all schemes); the canary passes 18 / 18.
2. **The minimum assay size came from data size and earlier rungs only.** Below 300 variants both
   ridge and LightGBM lose to zero-shot SaProt on unseen positions (−0.20 under contiguous for
   ≤ 200 variants); from 300 up both beat it on average in every size bin and scheme. 300 variants
   also leaves ~60 validation variants for early stopping. `train compare` lets a run lack exactly
   the assays below its minimum and nothing else. *Evidence:* the size table in ADR 0023; a test
   shows that dropping the minimum turns the same gap into an error.
3. **Plain torch with process isolation, no Lightning.** One thread per fit, a forked and seeded
   torch random state, torch imported only inside `fit`, no files written. *Evidence:* fits are
   identical whatever the global torch state; the thread count is restored; importing the CLI still
   loads neither torch nor LightGBM; pilot seed-to-seed spread is measured (below).

**Problems encountered and how they were solved.**

1. *The MLP canary failed on a synthetic assay without a leak.* `train canary` in the new CLI test
   reported a permuted mean of +0.154 ± 0.019 (z = +7.9, 3 permutations). It was treated as a leak
   until shown otherwise. Cause: the test trained for 6 epochs, so the shuffled-label networks
   stayed near their seeded initial weights, one fixed random function of features that carry
   signal. The seed-0 untrained network alone has Spearman −0.28 with the labels, and varying the
   seed moves permuted results between −0.59 and +0.45; with one seed the offset is identical in
   every permutation. At the configured budget (200 epochs, patience 20) the same synthetic assay
   passes (4 cases, |z| ≤ 0.86), and the real canary passes 18 / 18 (max |z| 2.56). A zero-initialised
   output layer was tried and rejected: no verdict changed, and true-label Spearman fell (0.769 →
   0.709). Fix: the test trains with the real epoch budget, and the pitfall is documented.
2. *The MLP's aggregate looked better than ridge until it was paired.* Under modulo `mlp_mse`
   scores 0.622 against ridge's published 0.593, apparently +0.029. But ridge's 0.593 is over 213
   assays, including the 15 small ones where supervised models do badly. On the same 198 assays
   ridge is 0.621, and the paired difference is +0.001 [−0.005, +0.007]. Only the paired numbers are
   reported; ADR 0023 says never to set the MLP's aggregate against a 213-assay number.

**Supporting details.**
- *Results* (198 assays, pooled Spearman, random / modulo / contiguous): `mlp_mse` 0.762 / 0.622 /
  0.570; `mlp_rank` 0.762 / 0.613 / 0.565; on the same assays ridge `seq_struct` 0.736 / 0.621 /
  0.581, `lgbm_pca` 0.759 / 0.607 / 0.562, zero-shot SaProt 0.459 / 0.459 / 0.460. Paired:
  `mlp_mse` − ridge +0.026 / +0.001 (n.s.) / −0.011; `mlp_rank` − ridge +0.026 / −0.008 / −0.016;
  `mlp_mse` − `lgbm_pca` +0.003 / +0.016 / +0.008 (n.s.); `mlp_rank` − `mlp_mse` +0.000 / −0.009 /
  −0.006 (n.s.); against SaProt +0.105 to +0.303. Ridge `seq_struct` stays the deployment candidate.
- *Pilot* (10 assays at size deciles, both losses, 3 seeds): 59–68 s per run; extrapolated
  12–16 min for the full run; per-assay seed SD of pooled Spearman median 0.002–0.003 (random),
  0.006–0.009 (modulo), 0.012–0.014 (contiguous), worst 0.034.
- *Full run* (13 jobs): `mlp_mse` 558 s, `mlp_rank` 486 s, parent peak RSS 1.7 GB. Early stopping
  chose a median of 6 epochs over 5,940 fits; 1,261 stopped at epoch 1–2, none at the cap of 200.
- *Canary:* `mlp_mse` 9 / 9 passed, all informative (ρ 0.57–0.89), max |z| 2.56; `mlp_rank` 9 / 9,
  all informative (ρ 0.63–0.88), max |z| 1.65.
- *Files:* `results/supervised/cv_mlp_{mse,rank}_seq_struct{,_hyperparameters}.parquet`,
  `canary_mlp_{mse,rank}_seq_struct.parquet`; updated `supervised_summary.parquet` and
  `supervised_comparisons.parquet` (all earlier rows unchanged).
- *Dependencies:* none added (torch was already one).
- *Not yet done:* conformal intervals; the `train` commands are not Nextflow processes.
- *Tests:* `make lint` clean; `make test` 550 passed, 1 skipped (Foldseek not on PATH), then 15
  LightGBM tests in their own process; 30 new.

---

## Entry 24 — Prediction intervals: CV+ with fold-ensemble spreads (Phase 3, 2026-10-16)

**Plain-language summary.** Every prediction on the website will now come with a range that should
contain the true score 80% or 90% of the time. The ranges come from cross-conformal prediction
(CV+). The training variants are split into folds, each fold is predicted by a model that never saw
it, and the size of those honest errors sets the width of the range. This was all done inside the
training folds of the existing cross-validation, so each range was then checked on test variants
that played no part in building it. Two ways of making the calibration folds were compared: random,
or the same way as the test split (held-out positions). So were two kinds of range: the same width
for every variant, or wider where the model's fold copies disagree (the "fold-ensemble spread").
The design was fixed in config before any result. The main finding: the calibration folds must mimic
the test split. Calibrated that way, the ranges hit their target on every split (0.908–0.911 at
90%), with at most 1 of 213 assays more than 10 points short. Calibrated on random folds, they are
far too narrow on positions the model never saw: 0.806 under contiguous, with 86 assays short.
Honest ranges on unseen positions are wide, about 2.5 assay standard deviations at 90%. The
adaptive ranges over-cover by 3–5 points, are 35–43% wider, and follow the error only weakly. MAPIE
could not do this, so CV+ is about 150 lines of our own, tested. The reasoning is in ADR 0024.

**The 3 most important design choices.**

1. **Calibration folds split like the outer scheme, all the way down.** In `scheme` mode the
   calibration folds are the training rows' own outer folds, and each calibration model's own inner
   CV uses them too, so under modulo and contiguous every residual comes from positions the model
   never saw, which is what the test fold asks. *Evidence:* scheme mode is on target under every
   split (0.815–0.819 at 80%, 0.908–0.911 at 90%), while random calibration under-covers on unseen
   positions (contiguous 0.673 / 0.806; 138 / 86 assays more than 10 points below). Its intervals
   stay ~2.0 SDs wide under every split, the width that only fits seen positions.
2. **The normaliser is the scoring model's own fold-ensemble spread.** The brief said to divide
   residuals by the fold-ensemble spread. But at a calibration row, K − 1 of the K CV+ models trained
   on that row, so their spread would be too small there. Each row is therefore divided by the
   spread of the fold ensemble of `M_k`, the model that scored it; all of that ensemble's members
   exclude the row's fold. Ridge already fits those members to choose α, so it now keeps them at the
   chosen α (`fold_predict`, predictions unchanged). *Evidence:* on synthetic data, the normalised
   scores used as split conformal hit 0.806 / 0.905, so they are exchangeable; the ridge fold
   ensemble equals refits at the chosen α to 1e-12.
3. **The test fold never reaches an interval, and the evaluation repeats `train cv`.** Each outer
   fold refits the outer model (the point prediction), then calibrates CV+ on its training rows only.
   *Evidence:* changing every label of one test fold (permuted; ×7 − 3) leaves its predictions,
   spreads and all 8 bounds bit-identical under all three schemes, while the other folds' intervals
   move; the outer model's pooled Spearman equals `cv_ridge_seq_struct.parquet` in all 639 (assay,
   scheme) cells (max difference 1.2e-7).

**Problems encountered and how they were solved.**

1. *Normalised CV+ over-covered on synthetic data with a known noise level.* *Found:* the first
   version of the known-noise test (y = x b + N(0, 1), 20 draws × 400 rows) failed for normalised
   intervals: coverage 0.939 at 90%, against 0.905 for absolute. It was treated as a bug. *Cause:*
   used as split conformal (one fold's model and residuals only), the same normalised scores covered
   0.905, so the scores are valid. The excess comes from CV+ combining K models whose spreads differ
   at the same point (by 29% on average there), which widens its order statistics. CV+ guarantees a
   lower bound, not an exact level. *Fix:* the test now checks absolute CV+ within 0.02 of target
   (0.809 / 0.905), the normalised scores as split conformal within 0.02 (0.806 / 0.905), and
   normalised CV+ for no under-coverage (0.836 / 0.939). *Verified:* the real run shows the same
   pattern (adaptive 0.845–0.850 / 0.940–0.945).
2. *An assay with 0.000 coverage.* *Found:* in the summary, the minimum coverage of random
   calibration under modulo was exactly 0. *Cause:* CCDB_ECOLI_Adkar_2012 reports one score per
   position: all 100 positions have a single value for every substitution (within-position SD 0.0).
   A model that has seen a position fits it exactly, so with random calibration folds, which share
   positions with training, the residuals are ~0 and the 90% interval is 0.009 SDs wide. Every test
   variant (unseen positions) falls outside. *Fix:* none needed; it is the starkest instance of
   finding 1. *Verified:* rerun alone, scheme calibration on the same assay gives width 2.64 and
   coverage 0.906.
3. *The run took 3.0 h, not the 1.4 h estimated.* *Found:* per-assay times in the log against the
   single-process pilot. With 10 workers sharing the machine, mid-size assays ran 2.7–3.3× slower
   than alone (NUSA_ECOLI_Tsuboyama_2023_1WCL 39.5 s vs 14.6 s; SPIKE_SARS2_Starr_2020_binding 623 s
   vs 189 s), most likely from memory-bandwidth contention in the large Gram and eigh computations
   (not measured directly). Total 27.8 worker-hours against 12.4 projected. The machine did not
   sleep (pmset log). *Fix:* not fixed; the estimate for `train calibrate` was scaled by the measured
   ratio instead. 81% of a fold's time is eigendecompositions (185 per scheme for a 3,802-variant
   assay), so subset caching would be the next lever if this has to run again.

**Supporting details.**
- *Results* (ridge `seq_struct`, 213 assays, 90%, scheme calibration, constant width; random /
  modulo / contiguous): coverage 0.911 / 0.909 / 0.908, no assay more than 10 points below except
  F7YBW8_MESOW_Ding_2023 under modulo (80 variants, 0.675); 0.904–0.923 in every size bin; width
  2.05 / 2.90 / 3.07 assay SDs (median 2.04 / 2.46 / 2.61). Adaptive: 0.943 / 0.942 / 0.940, width
  2.93 / 3.94 / 4.15. Per-assay Spearman of width with |error| (median): adaptive 0.185 / 0.095 /
  0.097, constant 0.093 / 0.025 / 0.010; fold-ensemble spread alone 0.171 / 0.087 / 0.084.
- *MLP not run:* the full design measured 16.2× a plain CV fit on NUSA (no free fold ensemble), so
  ~2.5 h against the 1-hour limit. A reduced design (scheme calibration, constant width) measured
  5.8× (~55 min) and was left as an option.
- *MAPIE 1.5.0* (latest) was checked in its source: no normalised score with cross-conformal, folds
  cannot reach the estimator, array inputs only. No dependency added.
- *For Task 8:* `train calibrate` wrote CV+ calibration rows of the all-data models (3 schemes × 2
  modes) to `data/processed/calibration/ridge_seq_struct/` (gitignored): 3,994,758 rows (6 × 665,793), 75 MB, 3,880 s on 13 workers. A refit of
  NUSA_ECOLI_Tsuboyama_2023_1WCL reproduced the saved predictions and spreads with difference 0.0.
  Intervals for any variant: refit `CVPlus` on the same inputs (it must reproduce the saved
  predictions; exactly with one BLAS thread), then `intervals(predict(x), ...)` (ADR 0024, section 5). Recommended
  configuration: scheme calibration on modulo or contiguous folds, constant width.
- *Files:* `results/supervised/uncertainty_ridge_seq_struct{,_summary,_bins}.parquet`,
  `results/figures/{calibration,width_error}_ridge_seq_struct.{svg,png}`; MLflow run in `supervised`.
- *Tests:* `make lint` clean; `make test` 579 passed, 1 skipped, then 15 LightGBM tests in their own
  process; 26 new (`test_conformal.py` 13, `test_uncertainty.py` 10, `test_cli_uncertainty.py` 3).

---

## Entry 25 — Final models: results table, deployed ridge landscapes, Hub bundles (Phase 3, 2026-10-16)

**Plain-language summary.** Phase 3 closes with three things. First, one final table puts every
supervised model next to the best zero-shot model it can fairly be compared with, on the same
assays: SaProt where the model uses structure, ESM-2 otherwise. Every row carries its own
confidence interval, the paired difference and how many assays it covers. This step reused
existing results only, with no training. Second, two figures drawn from those files: the ladder
of models per split, and where supervision pays by assay size. Third, the deployed model: one
fixed ridge regression per assay, for the 198 assays that have a structure and at least 300
measured variants. It predicts all 19 substitutions at every position, 1,379,989 in total, each
with an 80% and a 90% range. Measured variants show an honest out-of-fold prediction, never the
model's fit to their own label. Every position is handled according to how much of it was
measured. The run refuses to write anything unless it reproduces the earlier calibration exactly
and the cross-validation results to within 3.5e-7, and it passed. The model and the landscapes
are staged for the Hugging Face Hub with their cards. The reasoning is in ADR 0025.

**The 3 most important design choices.**

1. **Two final fits per assay, matched to the position.** A landscape mixes two tasks:
   predicting at positions that have measurements (interpolation) and at positions with none
   (extrapolation). The penalty chosen by inner CV on random folds and on contiguous folds
   differs by a median of one decade (equal in 18% of Task 4's folds; in the final fits the
   contiguous penalty is larger in 62% of assays). So each assay has a `random` and a
   `contiguous` ridge, and each variant takes its prediction, interval calibration and (if
   measured) out-of-fold value from the one scheme that matches its position. *Evidence:* ADR
   0024 showed calibration must mimic the task (random calibration on unseen positions: 0.806 at
   90%, 86 assays more than 10 points short); the unseen-position widths here (median 2.70 assay
   SDs at 90%) match ADR 0024's contiguous result (2.61). Chosen with the author before the run.
2. **Measured variants are out-of-fold, interval included.** The final ridge trained on every
   measured variant, so its value there is an in-sample fit and is never stored. A measured
   variant shows `M_k(x)` from the CV+ fold model that never saw it, and its interval uses the CV+
   order statistic of the *other* rows' residuals, its own left out. Plain CV+ at that point would
   lean on the 4 of 5 fold models that trained on it. *Evidence:* a test checks the bounds
   against a brute-force leave-one-out at every row; the measured predictions equal the saved
   calibration rows (1e-12).
3. **The release is blocked by reproduction checks.** `train final` refits CV+ on exactly
   `train calibrate`'s inputs and writes no bundle unless the refit matches (≤ 1e-9, identical
   fold labels) and the out-of-fold pooled Spearman matches `train cv` in every scheme (≤ 1e-6,
   after mapping each fold back to its own training scale, since `train cv` standardises per fold
   and `train calibrate` over all variants). *Evidence:* real run: refit difference 0 in all 198
   assays; Spearman differences 0 / 3.5e-7 / 6.6e-8 (random / modulo / contiguous). A CLI test
   feeds calibration rows from another penalty grid and requires the failure and no bundle.

**Problems encountered and how they were solved.**

1. *A confidence interval that was far too narrow.* *Found:* in the first version of the by-size
   table, ridge − SaProt under contiguous in the 200–299-variant bin was +0.053 [+0.041, +0.066],
   from 7 assays whose own differences ran from −0.094 to +0.129. A tight interval from 7 widely
   spread values was treated as a bug. *Cause:* the bin used the ProteinGym aggregate, which
   averages function types equally, and its bootstrap resamples within function types. The bin
   held 4 organismal-fitness, 2 activity and 1 expression assay. The single expression assay
   contributes no resampling spread at all, and the two activity assays very little. *Fix:*
   within size bins, the statistic is now the plain mean of per-assay paired differences with an
   unstratified bootstrap over assays (both runs drawn on the same assays), as ADR 0023's size
   table used. Headline numbers stay ProteinGym aggregates. *Verified:* the same bin is now
   +0.018 [−0.043, +0.079]. The figure's note and the schema docstring say it is a plain mean.
2. *The final run took 30 minutes, not "a few".* *Found:* the first estimate came from
   single-process smoke tests (NUSA 3.2 s, POLG_HCVJF 7.3 s per assay). The run was still going
   after 10 minutes, with 12–13 workers busy at ~1,100% CPU and 8 GB resident, so it was not
   stuck. *Cause:* the same memory-bandwidth contention as in Task 7: many workers doing large
   eigendecompositions at once. *Fix:* re-estimated from `train calibrate`'s parallel timing
   (3,880 s for 6 CV+ fits per assay; this run does 2 CV+ and 2 final fits) as 25–30 min, and
   said so. *Verified:* 1,808 s for the run itself, 30:15 wall time including loading.

**Supporting details.**
- *Final table* (`results/supervised/final_*.parquet`; pooled Spearman, own CI, paired against
  the comparator): ridge `seq_struct` 0.721 / 0.593 / 0.553 (random / modulo / contiguous, 213
  assays), − SaProt +0.263 / +0.135 / +0.092, every interval above 0. MLP-MSE (198 assays) 0.762 /
  0.622 / 0.570, − SaProt +0.303 / +0.163 / +0.111. By function type under contiguous, ridge −
  SaProt is above 0 for all but binding (+0.010 [−0.106, +0.121], 13 assays). By size: below 300
  variants no unseen-position interval is above 0 (−0.244 under contiguous below 200), from 300 up
  every one is. The summary and comparison rows are bit-identical to `train compare`'s (maximum
  difference 0.0). The check also turned up a rounding slip in ADR 0022 (LightGBM-PCA `seq`
  contiguous is 0.464473, written there as 0.465).
- *Eligibility:* 198 of 217 assays. Not eligible: 4 without a structure (website default ESM-2
  650M), 15 with 33–298 variants (SaProt). Stored per assay in the manifest with the reason.
- *Landscapes:* 1,379,989 rows (663,263 measured; 585,637 at unmeasured positions), 71 MB,
  stamped with code `6fb0d07`, the config hash and the data-manifest hash. 108,452 rows have
  imputed structure features, all in ZIKV and POLG_HCVJF, whose structures cover a small part of
  the target. Out-of-fold coverage of measured variants is 0.800 / 0.900, but that holds by
  construction; the real coverage evidence is ADR 0024's test folds.
- *Sanity check:* buried residues (relative SASA < 0.2) are predicted lower than exposed ones in
  194 of 195 assays. KCNE1 (×2) and α-synuclein have no buried residue in their AF2 models. The
  exception, TADBP_HUMAN_Bolognesi_2019 (0.039 vs 0.029), measures toxicity of TDP-43's
  disordered C-terminal domain.
- *Publication:* staged, not pushed. There is no Hugging Face token on the machine (`whoami`
  fails). After `hf auth login`: `uv run mutascope train publish --results-dir results/supervised
  --push` (model repo `engAbdullahHanif/mutascope-ridge-seq-struct`, created private) and `uv run
  mutascope data publish --landscapes --push` (new revision of the dataset).
- *Tests:* `make lint` clean; `make test` 600 passed, 1 skipped, then 15 LightGBM tests in their
  own process; 17 new (`test_final.py` 10, `eval/test_final.py` 3, `test_cli_final.py` 2,
  `test_hub.py` +2). MLflow runs `final-table supervised` and `final ridge seq_struct` in
  `supervised`.

---

## Entry 26 — Phase 3 close-out (2026-10-17)

**Plain-language summary.** Phase 3 is closed, with one item left open on purpose: the Phase 3
model and landscapes are not on the Hugging Face Hub yet, because this machine has no token. The phase asked whether training on
an assay's own measurements beats the pretrained models that never see them, and the answer is
yes in all three ways of splitting the data. How much depends on the question. When a position
already has some measured mutations, the best supervised model gets about 0.30 more Spearman than
the best zero-shot model. When the position has none, the gain is about 0.09 to 0.16. The deployed
model is a plain ridge regression on rich features, not the fancier LightGBM or neural network.
Those two win only when the position was seen in training, and the website mostly shows positions
that were not. It is deployed only where it clearly pays: the 198 assays with a structure and at
least 300 measurements. The other 19 show SaProt or ESM-2, labelled zero-shot. This task wrote no
code. It checked every Phase 3 roadmap item against the repository, regenerated the headline
numbers from the results files, wrote down what is open or weak in ADR 0026 and pointed the
project at Phase 4.

**The 3 most important design choices.**

1. **Close the phase with the Hub item open, not ticked.** The done-when line ends with "model
   cards on the HF Hub". The card exists and the bundles are staged and tested, but `hf auth
   whoami` says "Not logged in", so nothing from Phase 3 was uploaded. The item stays unticked, is repeated as
   the first Phase 4 export item, and CLAUDE.md names it.
2. **One headline table with all four rungs, each against the comparator it can fairly face.**
   The final table (ADR 0025) has no augmented ridge, but the done-when line asks for 4 rungs, so
   ADR 0026 adds augmented ridge's row from `supervised_summary.parquet`. Every row keeps its own
   CI on the first line and the paired difference on the second, with the assay count (217, 213 or
   198). The rung-against-rung differences come from `supervised_comparisons.parquet`, never from
   reading two CIs.
3. **State the deployment decision as a rule, with the numbers that make it hold.** Ridge
   `seq_struct` is not the best model on every split: the MLP (MSE) beats it by +0.026 under
   random. The ADR says so and explains why that does not decide the deployment. The website
   predicts at unmeasured positions (585,637 of the 716,726 unmeasured substitutions are at
   positions with no measurement at all). There, LightGBM is below ridge (−0.011, −0.020) and the
   MLP ties it (+0.001 [−0.005, +0.007]) or is below it (−0.011).

**Problems encountered and how they were solved.**

1. **CI on `main` was red for two merges, and no entry said so.** *Found:* `gh run list --branch
   main` during the audit. The pushes after PR #24 (run 38906523981) and PR #25 (38945800654)
   both failed on `tests/models/test_cv.py::test_parallel_assays_give_the_serial_table`. *Cause*
   (from the fix, `e6592db`, in PR #26): with `--jobs 1` the tasks ran in the main process, where
   OpenBLAS on the Linux runner used every core. Multi-threaded OpenBLAS adds numbers in a
   different order, and on an ill-conditioned ridge that moved pooled Spearman by up to 3.4e-3
   against the single-threaded workers. macOS's Accelerate did not show it. *Fix:* the serial
   path runs under `threadpoolctl`'s one-thread limit. *Verified:* in a linux/amd64 container,
   serial vs parallel differed by 5.6e-4 at 4 threads and 1.5e-3 at 8, and by 0.0 with the cap.
   CI on `main` has been green since the PR #26 merge (run 39013659351). The committed results are
   unaffected. The ridge CV runs predate `--jobs` and ran serially on macOS. Every later run used
   13 single-threaded workers (`train.jobs` = 13 in MLflow). `train final` reproduced
   `cv_ridge_seq_struct.parquet` to 3.5e-7 with those workers.
2. **ADR 0025 understated where supervision fails to help Binding.** *Found:* regenerating the
   per-function-type table from `final_comparisons.parquet`. ADR 0025 names contiguous as Binding's
   only interval that includes 0. Modulo includes it too: ridge `seq_struct` − SaProt +0.076
   [−0.033, +0.172], 13 assays. *Fix:* ADR 0026 has the full 5 × 3 table and says both. ADR 0025
   is left as accepted.
3. **Hand-typed numbers were wrong in three places.** *Found:* by printing the headline table
   from the parquet files with a script and comparing it with the draft. The draft of ADR 0026 had
   −0.395 where the file rounds to −0.394 (one-hot ridge − ESM-2, contiguous, upper bound
   −0.394500) and +0.044 where it rounds to +0.043 (LightGBM-PCA `seq_struct` − SaProt,
   contiguous, lower bound). The same check found ADR 0021 giving augmented ridge − zero-shot under
   contiguous as −0.006 where the file says −0.005483. *Fix:* the two draft values were corrected.
   ADR 0021's slip is recorded in ADR 0026 (item 17) next to the ADR 0022 slip that entry 25
   found. *Verified:* every value in ADR 0026's headline table matches the script's output.
4. **The draft said the Phase 1 dataset was never pushed.** *Found:* the project map's step 8
   says commit `54f6ef0` marked it published. *Cause:* "no token now" was read as "never
   uploaded". The dataset repo is private, so an anonymous API call returns 401 and cannot tell
   the two apart. *Fix:* ADR 0026 says the dataset repo was marked published, and that the
   landscapes go to it as a new revision, which waits for a login.

**Supporting details.**
- *Headline table, per-function-type table, rung comparisons and deployment decision:* ADR 0026.
  Values are from `results/supervised/final_{summary,comparisons}.parquet` and
  `supervised_{summary,comparisons}.parquet`. Deployed ridge `seq_struct` (213 assays): 0.721 /
  0.593 / 0.553 (random / modulo / contiguous), − SaProt +0.263 / +0.135 / +0.092, every interval
  above 0.
- *Release checks, reread from `final_checks.parquet`:* 198 assays, no NaN, no bad interval,
  calibration refit difference 0, out-of-fold Spearman against `train cv` 0 / 3.5e-7 / 6.6e-8,
  buried below exposed in 194 of 195 assays. The manifest gives `default_model` ridge 198,
  SaProt 15, ESM-2 650M 4, and 1,379,989 landscape rows.
- *Unchecked:* Hub publication (moved to Phase 4); LoRA and the leave-protein-out model
  (stretch); carried from earlier phases, the `docker` profile and ESM-1v / ESM C.
- *Weak (ADR 0026, items 4–17):* no Phase 3 command is a Nextflow process (now a Phase 4 item);
  CI trains nothing real; Binding has 13 assays; the deployed model is not the best under random;
  interval width does not track difficulty; the MLP has no intervals; measured-variant coverage
  holds by construction; two targets have mostly imputed structure features (108,452 rows); SHAP
  is on 3 assays; by-size bins use a plain mean. The project map lacked steps 19–25; they were
  filled in on the same branch, from the commit ranges and file lists of each task.
- *Corrected in passing:* CLAUDE.md's summary said uncertainty used MAPIE. It now says CV+ is
  implemented directly (ADR 0024).
- *Tests:* `make lint` clean (ruff, format, pyright 0 errors). `make test`: 601 passed, 1
  skipped, then 15 LightGBM tests passed in their own process. No code changed, so
  `make test-slow` was not rerun.
- *Docs changed:* ADR 0026, CLAUDE.md (current phase → Phase 4, MAPIE line), ROADMAP (close
  note, publish item and Nextflow item in Phase 4, export reads the manifest), README (status
  line), PROJECT_MAP (steps 19–26, a Phase 3 index and a Phase 3 retrospective).

---

## Entry 27 — Hub publication recorded, Phase 3 in Nextflow (Phase 4 Task 0, 2026-10-17)

**Plain-language summary.** Two loose ends from Phase 3 are tied up. First, the trained model
and its landscapes are now public on the Hugging Face Hub. This task checked that through the
Hub's API, confirmed that every published file is exactly the file staged on this machine, and
pinned both published versions in the config, so the website and API will read those exact
files, never "whatever is latest". Second, `make pipeline` now rebuilds the deployed model from
raw data. It builds the feature store, cross-validates the ridge, calibrates its intervals and
fits the final models. It then publishes the result only if every number matches what was
released, down to the last bit. The first full run (2 h 07 min) showed that the deployed
landscapes and intervals reproduce exactly. One committed file of cross-validation scores did
not, in the 8th decimal, because it had been made before a fix to multithreaded arithmetic. It
was replaced by the rerun's output, with everything computed from it, and the rerun then passed.
The slower LightGBM and neural-network runs are an option, off by default. The quick test
version of the pipeline runs the same ridge steps on two small assays in under a minute.

**The 3 most important design choices.**

1. **Check the rerun against the release, not only against itself.** `train final` already
   refuses a bundle whose refit disagrees with `train calibrate` or `train cv`. But when one
   pipeline reruns all three, that only proves the run is self-consistent: a different feature
   store would move all three together and pass. The new `train check-release` compares every
   output with the released file: the same files, columns, dtypes and rows, and every number
   byte for byte (a 1-ulp change fails). The only exceptions are the two provenance stamps (git
   commit and config hash), which change on every rerun by construction. *Evidence:* it caught
   the 1.2e-7 difference described below on its first real run.
2. **Publish only through the check.** The deployed run's processes have no `publishDir`;
   `CHECK_RELEASE` publishes, and only after a pass. A failed check therefore leaves the released
   files untouched. It publishes per model label and per file, because a Nextflow copy replaces
   a target directory whole, and `results/supervised/` holds 40 other runs' files. *Evidence:*
   after the failed first run, `git status` showed no change in `results/supervised/`
   (`final_manifest.parquet` changed only after the passing rerun published it).
3. **Keep the smoke test's assays and override two settings instead.** The two test assays are
   below the deployed 300-variant minimum, and the real feature sets need two 650M models.
   Rather than pick new assays (losing the PDB `TITLE` case the smoke test exists for), the
   test profile lowers the minimum to 100 and uses `conf/features/smoke.yaml`: `seq_struct` with
   `esm2_8m`, the model the smoke test already scores. *Evidence:* 22 tasks in 55 s, with
   LightGBM-PCA and the MLP included; CV+ refit and out-of-fold checks both 0.

**Problems encountered and how they were solved.**

1. **The committed ridge CV results could not be reproduced bit for bit.** *Found:* comparing
   `TRAIN_CV`'s output with the committed file as soon as it finished, and then the release
   check, which failed on it and on `final_checks.parquet`. *Numbers:* per-fold Spearman 0 of
   3,189 different and selected penalties identical; per-fold MSE 2,416 of 3,189 different (max
   5.8e-8), pooled MSE 503 of 639 (5.7e-9), pooled Spearman 3 of 639 (1.2e-7: HMDH_HUMAN,
   OXDA_RHOTO, SC6A4_HUMAN, contiguous). Calibration rows (213 files) and the bundle (202 files)
   were bit-identical. *Cause:* the committed file came from the serial run of 2026-10-14, made
   on uncommitted code (`598c6ad…-dirty`, MLflow) with macOS Accelerate using several threads.
   Since PR #26 every path runs with one BLAS thread (entry 26, problem 1), which adds floats in
   another order. Nothing in the current code can recreate those bits. *Fix* (the author chose
   re-baselining over a tolerance): this run's `cv_ridge_seq_struct.parquet` and
   `final_checks.parquet` replace the committed ones. `train compare` and `train final-table`
   were rerun into `results/supervised/` (moved ≤ 1.5e-8; per-assay differences ≤ 1.2e-7),
   `train leaderboard` did not move, and the two final SVGs changed one coordinate in the sixth
   decimal (PNGs byte-identical). *Verified:* `nextflow run ... -resume` reran only
   `CHECK_RELEASE`, which printed "PASS: ridge_seq_struct reproduces the release bit for bit (4
   results files, 213 calibration files, 202 bundle files)".
2. **A file-for-file "bit-identical" was impossible by construction.** *Found:* reading
   `train final` before writing the check. Every landscape row and the manifest carry
   `code_version` (git commit) and `config_sha256` (hash of the resolved config, which contains
   absolute paths), so a rerun from a new commit always changes 200 files. *Fix:* the check
   leaves out exactly those two columns and keys (`train.release.ignore`) and requires them on
   both sides. `manifest_sha256`, the input-data fingerprint, is compared. *Verified:* after
   publication, `fits.parquet`, `coefficients.parquet`, the calibration rows and the folds are
   byte-identical to before. The 198 partitions, `manifest.parquet` and `version.json` differ only
   in stamps.

**Supporting details.**
- *Hub* (ADR 0026 addendum): model `engAbdullahHanif/mutascope-ridge-seq-struct` @ `45a04b8`,
  dataset `engAbdullahHanif/mutascope-proteingym-v1.3` @ `0b0fe65`, both `private: false`,
  `gated: false`, and both commits were the heads. The 18 LFS files match `build/` by sha256,
  the 11 small files by size. Pinned as `train.publish.revision` and `publish.revision`.
- *Landscape check:* `mutascope score check-landscapes` passes, 882 of 882 landscapes complete
  (ESM-2 35M / 150M / 650M 222 each, SaProt 216). The table equals the committed
  `landscape_coverage.parquet`.
- *Default run timings:* Phase 1 and 2, embeddings and the feature store 3 min (all cached);
  `train cv` 1,878 s, `train calibrate` 3,807 s, `train final` 1,748 s, all with 13 workers.
  The ~2.5 h estimate scaled from earlier parallel runs; the actual was 2 h 07 min.
- *Noise:* the run rewrites the tracked run reports (`zero_shot_run_*`, `landscape_run_*`,
  `embedding_run_*`: only `seconds` and `*_computed`) and `structure_report.md` ("found via"),
  restored with `git checkout`. `final_manifest.parquet` is committed with the new stamps, to
  match the republished bundle.
- *Not wired:* `train compare`, `final-table`, `final-figures`, `canary`, `shap`, `uncertainty`
  (results-only steps or diagnostics). The `docker` profile waits for the Phase 4 image.

---

## Entry 28 — The website bundle (Phase 4 Task 1, 2026-10-17)

**Plain-language summary.** The website needs every protein's data as a ready-made file, so a
page works even when the backend is asleep. `mutascope export bundle` now writes one compressed
file per ProteinGym assay (217). Each holds what the page shows: which model to show first and
why, the measured values, the two zero-shot landscapes, the structure features and, for the 198
assays with a supervised model, its predictions with 80% and 90% intervals. A manifest lists
every protein with its label ("Supervised (n = 9,576)" or "Zero-shot"), and `benchmarks.json`
holds the headline tables. The format is written down as Pydantic models and exported as
versioned JSON Schemas for the frontend. To keep the files small, numbers are stored as
integers with a fixed number of decimals. The build then proves that this rounding changes no
reported number: it reads every file back, recomputes each Spearman correlation and each
interval coverage, and fails if any moves by more than 0.001 or 0.002. The bundle is 29.2 MB;
the largest file is 1.15 MB, under the 2 MB budget. It is published on the Hub as a tagged,
pinned revision of the dataset repo.

**The 3 most important design choices.**

1. **Choose the decimals so that ties cannot form.** Rounding to the nearest step keeps the
   order of two numbers, but two different numbers can round to the same value. Because
   interval bounds are inclusive, a measurement just outside its interval then counts as inside.
   So each assay's step is below the smallest gap between a measured value and its bounds, as
   well as at most 1/1000 of the assay's SD. *Evidence:* with the SD rule alone, 5 numbers in 3
   assays failed (problem 2). With the gap rule, coverage is reproduced exactly in all 198 assays
   at both levels, for 12% more bytes.
2. **Prove fidelity on the written bytes, against the reported numbers.** The check decodes the
   files the browser will receive, not an in-memory copy. It compares against the unrounded
   inputs and against the results tables wherever the bundle holds the same numbers: zero-shot
   Spearman on the 212 / 210 assays whose benchmark region is the whole target, and coverage
   from the published `final_checks`. *Evidence:* the largest changes are Spearman 4.30e-4
   (ESM-2), 6.56e-4 (SaProt) and 4.04e-4 (ridge), and the aggregates move by less than 1e-5 (ESM-2: 9.5e-6).
3. **Read the published revisions and check them.** Supervised landscapes, metadata,
   measurements, structure features and final tables come from the pinned Hub commits. Each
   file is compared with the `SHA256SUMS` published in the same commit, and a branch name is
   refused. The website therefore shows exactly what was released. *Evidence:* the published
   final tables differ from the local copies by up to 1.2e-7 (problem 4). The bundle carries the
   released numbers.

**Problems encountered and how they were solved.**

1. **The interval's calibration scheme is per variant, not per residue.** *Found:* the build's
   input check stopped at `AACC1_PSEAI_Dandage_2018` ("calibration_scheme varies within a
   residue"; 2 of its 177 residues). *Cause:* `train.final.scheme_by_position` asks whether a
   variant's residue has *other* measured variants. So a residue's only measured variant uses
   contiguous folds while its 18 unmeasured substitutions use random ones (residue 90: F
   measured and `contiguous`, the rest `random`). *Fix:* stored as a coded `(L, 20)` grid. The
   check stays for `position_status` and `structure_imputed`, which are per residue. *Verified:*
   the full build passes, and a synthetic test checks a lone measured variant against its
   neighbours.
2. **Rounding moved interval coverage.** *Found:* the first full build's fidelity check: 5
   failures. `CCDB_ECOLI_Adkar_2012` coverage +0.200 (80%) and +0.099 (90%), supervised Spearman
   0.0021; `EPHB2_HUMAN_Tsuboyama_2023_1F0M` 80% coverage 0.0024 (3 of 1,239 variants);
   `LYAM1_HUMAN_Elazar_2016` 90% coverage 0.0028 (1 of 359). *Cause:* ties at the bound, as in
   design choice 1. CCDB is the extreme: it reports one score per position (ADR 0024), so its
   random-split predictions equal the measurement to ~1e-6 and its intervals are 3.0e-6 wide;
   at 3 decimals all 1,176 variants tie a bound. *Fix:* the gap rule, capped at 9 decimals
   (CCDB reaches 9). *Verified:* the rebuild passes. Coverage differs by 0 everywhere, and
   CCDB's Spearman is within tolerance. Decimals: 3 in 36 assays, 4 in 120, 5 in 52, 6 in 6, 7
   in 2, 9 in 1.
3. **A fixed 3 decimals would have flattened small-scale assays.** *Found:* while choosing the
   encoding, from the per-assay SD of `DMS_score`, before any code. `HSP82_YEAST_Cote-
   Hammarlof_2020_growth-H2O2` has SD 0.016, so 3 decimals give about 16 steps per SD. *Fix:*
   at least SD / 1000 per step (5 decimals there). *Verified:* that assay is in the passing
   fidelity table.
4. **Local final tables are not the published ones.** *Found:* comparing local
   `results/supervised/final_*` with the model repo's `SHA256SUMS` at `45a04b8`: 6 of 7
   differ. *Cause:* the single-threaded re-baseline (`ed9cdf4`, entry 27, problem 1) landed
   after publication: values move by up to 1.2e-7 (`cv_spearman_contiguous`), plus the
   provenance stamps. *Fix:* none needed. The bundle reads the published tables, as the task
   asks, and ADR 0028 records the difference.
5. **An all-null column broke the fidelity table's schema.** *Found:* the synthetic tests, where
   no reported value is comparable, so pandas made `reported` an object column. *Fix:* the
   numeric columns are cast to float64 before validation. *Verified:* 35 new tests pass.

**Supporting details.**
- *Bundle (built at `5c8d778`):* 217 files, 29,218,051 bytes compressed (79,167,098 JSON),
  median 88,105, largest 1,153,890 (ZIKV, 3,423 residues); `benchmarks.json` 351,937 bytes,
  `manifest.json` 93,115. Budgets set after measuring: 2,000,000 per file, 40,000,000 in total.
  Defaults: 198 `ridge_seq_struct`, 15 `saprot_650m_af2`, 4 `esm2_650m`; MSA region on 5 assays;
  one caveat (Binding under contiguous and modulo).
- *Hub:* dataset repo commit `3ae767217bb0a56b8f2e7c79c68b73119e308066`, tag `bundle-v1.0.0`
  (resolves to it), public. Checked through the API without a token: 224 files under
  `bundle/` equal to `build/bundle` (217 by sha256, 7 by size), and the 14 files outside it
  identical to `0b0fe65`. Pinned as `export.publish.revision`.
- *Run time:* 13 s with the Hub files cached.
- *Tests:* `make lint` clean; `make test` passes (652 + 15 LightGBM). 35 new tests: encoding,
  a property test of the gap rule, the round trip, input checks, budgets, the fidelity
  failure path, schemas, benchmark rows, the pinned fetch and the tagged push.
- *Loose ends:* not a Nextflow process yet; no MLflow run (no model is evaluated); the root
  dataset card does not mention `bundle/` until the next `data publish` (the folder has its own
  README).

---

## Entry 29 — ESM-2 on ONNX Runtime for CPU serving (Phase 4 Task 2, 2026-10-17)

**Plain-language summary.** The API will score user sequences live on a small cloud machine
with two CPU cores and no PyTorch. So ESM-2 35M and 150M were converted to ONNX, a portable
model format that the lightweight ONNX Runtime library executes. Each model now exists twice:
an exact (fp32) copy, and a 4× smaller copy whose weights are stored as 8-bit integers (int8).
A new scorer runs these files with the same masking, windowing and caching rules as the PyTorch
scorer, without loading PyTorch at all. Three checks compare the ONNX models with the PyTorch
ones:

- The fp32 copy reproduces PyTorch's raw outputs to within 1e-4.
- Across all 217 benchmark assays, the int8 copy changes the headline benchmark number by
  −0.0005 (35M) and −0.0006 (150M), too little to tell from zero.
- In 3 assays (35M) and 1 assay (150M), the int8 copy's scores agree with PyTorch's slightly
  less than the 0.99 the project requires. So the int8 copy fails that check, and the fp32
  copy passes it.

Speed was measured on two threads. Fast mode scores a 1,000-residue protein in 0.25 s (35M) or
0.5 s (150M). Precise mode takes minutes. On this machine int8 was not faster than fp32. Both
copies are published on the Hugging Face Hub with a model card, pinned by commit. Which one the
API serves is left to the author.

**The 3 most important design choices.**

1. **One torch-free core for both runtimes instead of a second copy of the scoring logic.** The
   cache rules (which rows are scored, NaN for the rest), the windows and the batching moved out
   of the PyTorch class into `scoring/marginals.py`. The PyTorch and ONNX scorers now only
   encode tokens and run forward passes, so a cache or window rule cannot drift between them.
   *Evidence:* the 109 existing scoring, feature and landscape tests pass unchanged, including
   the real 8M checkpoint against ProteinGym. With the same stand-in function in both
   runtimes, the two scorers agree within 1e-5 through windowed and unwindowed, masked and
   wild-type passes. A test runs the ONNX scorer in a fresh interpreter and fails if torch or
   transformers is loaded.
2. **Export from a local snapshot of the pinned revision, in an isolated environment, then judge
   it against our own PyTorch model.** optimum never sees a revision it could resolve
   differently. It runs in its own locked uv project because it cannot run on transformers 5
   (problem 1). Check 1 then compares the result with the transformers-5 model the benchmark
   uses. *Evidence:* two exports are bit-identical. fp32 logits differ from PyTorch by at most
   6.7e-5 (35M) and 9.0e-5 (150M) over every token and vocabulary entry of five proteins of 37
   to 934 residues, unmasked and masked. The fp32 fast-mode scores reproduce PyTorch's aggregate
   exactly (0.3028, 0.3772) and agree at per-assay Spearman 1.0000 in all 217 assays.
3. **The int8 recipe was fixed in advance and not tuned to the gate that failed it; both
   precisions are published and the choice is left open.** The recipe was set before any check:
   every MatMul, per-channel int8 weights, 7-bit range so x86 CPUs without VNNI cannot saturate.
   When check 2 failed, I compared alternatives on fidelity to PyTorch only and labelled them
   post-hoc. None passes, and the gate was not re-run with a new recipe. *Evidence:* four
   recipes leave 1–3 assays (35M) and 1 (150M) below 0.99, and the failing assay moves between
   them. Keeping any one group of layers in fp32 recovers at most 21% of the error on the dev
   proteins. The benchmark rerun from the published Hub revision gives bit-identical scores
   (696,311 per scorer, four scorers) and identical results tables, so the card's numbers are
   the published files' numbers.

**Problems encountered and how they were solved.**

1. **optimum's ONNX exporter does not support transformers 5.** *Found:* while checking
   versions before adding dependencies. optimum-onnx 0.1.0 (latest, 2025-12-23) requires
   `transformers>=4.36,<4.58`, and so does its `main` branch's `pyproject.toml`. MutaScope runs
   5.19. Installing it anyway (`--no-deps`, optimum 2.3.0) fails at import: "Could not import
   module 'main_export'". *Fix:* `tools/onnx-export`, a separate locked uv project
   (optimum-onnx 0.1.0, optimum 2.1.0, transformers 4.57.6, torch 2.14.1), called by
   `mutascope onnx export` on a local snapshot. *Verified:* check 1 passes (max |Δ| 9.0e-5
   against the 1e-4 tolerance), and the export is bit-identical across two runs.
2. **transformers' ESM tokenizer imports torch.** *Found:* loading `AutoTokenizer` for ESM-2 in
   transformers 5.19 left `'torch' in sys.modules == True`, which a torch-free server cannot
   have. *Fix:* the export writes a `tokenizers` `tokenizer.json` (WordLevel over `vocab.txt`,
   `<cls> … <eos>`). It must give transformers' ids on 205 sequences (200 random, 1–1,022
   residues, plus the five parity targets) or nothing is written. *Verified:* the export passes
   that check. A fresh-interpreter test scores with the ONNX scorer and finds neither torch nor
   transformers loaded.
3. **int8 fails the per-assay agreement gate.** *Found:* `onnx check-scores --fast` on all 217
   assays. 35M: 3 assays below 0.99 (NPC1_HUMAN_Erwood_2022_RPE1 0.9780 with 63 variants,
   VILI_CHICK_Tsuboyama_2023_1YU5 0.9869, NRAM_I33A0_Jiang_2016 0.9882; median 0.9975). 150M:
   1 assay (DN7A_SACS2_Tsuboyama_2023_1JIC 0.9819; median 0.9980). *Cause:* quantization alone.
   The fp32 file agrees at ≥ 0.99999996 per assay, so the runtime and export are not the cause.
   The error is spread over every layer: on the five dev proteins, keeping any one MatMul group
   in fp32 lowers the mean (1 − ρ) of the fast landscape only from 0.00250 to at best 0.00197
   (FFN output). *Not fixed:* none of four dynamic-int8 recipes passes (35M: 3, 1, 2 and 1
   assays below; 150M: 1 each). The threshold was not changed, and the recipe was not tuned to
   the gate. The fp32 files pass, and the API's choice is recorded as open in ADR 0029 section
   6, with fp32 recommended.
4. **The test graphs could not be loaded.** *Found:* 14 of the 15 new scorer tests failed on
   their first run: "Unsupported model IR version: 14, max supported IR version: 13". *Cause:*
   onnx 1.23 writes IR 14 by default, which ONNX Runtime 1.31 cannot read. The optimum export
   was unaffected. *Fix:* the hand-built stand-in graphs set `ir_version=10`. *Verified:* all 15
   pass.

**Supporting details.**
- *Checks:* check 1 `results/onnx/logit_parity.parquet` and `landscape_parity.parquet` (fp32
  landscapes against PyTorch on MPS: Spearman 1.00000, max |Δ| 7.9e-5 nats; int8: fast ≥
  0.9951, precise ≥ 0.9966). Check 2 `results/onnx/score_parity_*.parquet`. Check 3
  `results/zero_shot/comparisons.parquet` (two new pairs, 10,000 resamples; the 240 existing
  rows are bit-identical). Spearman int8 − PyTorch: 35M −0.0005 [−0.0013, +0.0002], 150M −0.0006
  [−0.0012, +0.0002]. Secondary metrics with CIs excluding 0: 35M AUC −0.0004 and top-10% recall
  −0.0059; 150M MCC −0.0019 and top-10% recall +0.0014.
- *Latency (2 threads, M4 Pro, seconds, fp32 / int8):* fast at L = 1,000: 0.247 / 0.265 (35M),
  0.530 / 0.676 (150M). Precise at L = 1,000: 241 / 253 (35M), 554 / 591 (150M). int8 is 0–28%
  slower here. The full table is in ADR 0029. The latency run took about 38 min (37.5 min
  of timed calls), inside the ~40 min estimate.
- *Hub:* `engAbdullahHanif/mutascope-esm2-onnx` at `82e15287c50f45f771cca75ac3786f0376ecabb2`.
  The repo was created private, and all 34 files were verified against `SHA256SUMS` (20 by LFS
  sha256, 14 downloaded and hashed) before it was made public. Read without a token. Pinned as
  `onnx.publish.revision`. Sizes: 35M fp32 134.5 MB, int8 34.8 MB; 150M fp32 593.8 MB, int8
  151.8 MB.
- *Dependencies:* the `onnx` extra adds onnxruntime 1.31.0 (the runtime), onnx 1.23.2 (needed by
  `onnxruntime.quantization`) and tokenizers 0.23.3 (already a transformers dependency, now
  imported directly). The export environment's versions are pinned in `tools/onnx-export`.
- *Run times:* export 22 s for both models (snapshots cached), check 1 22 min (masked
  landscapes on the CPU), fast benchmark 10–49 s per scorer.
- *Tests:* `make lint` clean; `make test` passes (675 + 15 LightGBM; 1 pre-existing Foldseek
  skip); `make test-slow` includes the published 35M int8 file against PyTorch (passes). 24 new
  tests.
- *Loose ends:* the API's precision (ADR 0029 section 6; re-measure latency on the Space
  first); precise mode is not benchmarked on all 217 assays in ONNX (five proteins only); the
  150M fp32 logit difference is within 10% of the tolerance on the longest protein checked; the
  next `export bundle` will carry the two int8 pairs in `benchmarks.json` (content, not format);
  the export and checks are not Nextflow processes (one run per release).

---

## Entry 30 — The API core: pinned bundle, read-only routes, one error format (Phase 4 Task 3, 2026-10-17)

**Plain-language summary.** The FastAPI service now serves what the website shows. At startup
it downloads the published website bundle at its pinned commit, checks every file against the
bundle's checksum list, and refuses to start if the bundle's format version is not one the code
understands. It then loads the two exact (fp32) ONNX models chosen in Task 2. Routes list the
217 proteins (filter by function type, search by name), describe one protein and why it has the
default model it has, return a full mutation landscape (the supervised model with its 80% and
90% intervals, or a zero-shot model) and return the benchmark tables. Every landscape says whether
it is "Supervised (n = …)" or "Zero-shot" and which bundle, code and model revisions produced
it. Every error, from an unknown protein to a crash, has the same standard JSON shape (RFC
9457). Read-only responses carry an ETag, so a browser can ask "has this changed?" and get an
empty 304 answer, and they are gzipped. The API never loads PyTorch, and a test checks that in a
fresh Python process. Finally, `make types` turns the API's OpenAPI description into TypeScript
types for the frontend, and CI fails if the committed types are out of date. Decisions in
ADR 0030.

**The 3 most important design choices.**

1. **Refuse to start rather than serve something unverified.** Startup checks all 223 files
   in the bundle's `SHA256SUMS`, that every protein in the manifest has its file, and that the
   schema version is in `SUPPORTED_SCHEMA_VERSIONS` (read before parsing, so the error names the
   version). It accepts only fp32 `esm2_onnx` model configs. Any failure stops uvicorn. A
   protein file is checked again against the manifest's sha256 when it is first read.
   *Evidence:* tests refuse a manifest rewritten to schema `2.0.0` (with a consistent checksum,
   so only the version check can catch it), a one-byte change to `benchmarks.json`, a protein
   file edited after startup, a snapshot missing protein files, the int8 and PyTorch configs,
   and any revision that is not a 40-character hash (`main`, `bundle-v1.0.0`, `3ae7672`).
2. **The settings copy the pins, and a test holds them to `conf/`.** The API is configured by
   environment variables (pydantic-settings), as a container should be. Its defaults are the
   bundle revision `3ae7672` and the ONNX revision `82e1528`, and a test compares them with
   `export.publish.revision`, `onnx.publish.revision`, `publish.repo_id` and
   `export.publish.path_in_repo`. A re-publish that updates `conf/` but not the API fails in
   CI instead of quietly serving the old bundle.
3. **Landscapes are decoded, labelled and stamped, with the out-of-fold pairing made
   explicit.** The bundle's fixed-point integers become plain numbers, so API clients need no
   decoding rule. A supervised landscape adds `oof_prediction`: the prediction only where a
   variant was measured. In the bundle that value already comes from the fold model that
   never saw the variant, and the separate grid stops a client from comparing an all-data
   prediction with a measurement. *Evidence:* golden tests compare every grid of
   `TCRG1_MOUSE_Tsuboyama_2023_1E0L` (supervised, n = 621) and both zero-shot landscapes of
   `F7YBW8_MESOW_Aakre_2015` with values decoded straight from the published gzip JSON, plus
   pinned cells (TCRG1 residue 0 → A: measured −0.1039, prediction −0.1262, 80% interval
   [−0.4863, 0.2339]).

**Problems encountered and how they were solved.**

1. **The API tests could not run on their own.** *Found:* the first `pytest api/tests` run
   stopped at collection: `ModuleNotFoundError: No module named 'tests'` from the conftest,
   which reuses the ONNX stand-in graphs in `tests/scoring/test_onnx_esm2.py`. *Cause:* with
   `--import-mode=importlib` pytest does not add the repo root to `sys.path`. `tests` could be
   imported only when `tests/` had been collected first, as in `make test`. *Fix:*
   `pythonpath = ["."]` in the pytest config. *Verified:* `pytest api/tests` alone collects
   and passes all 37 tests, and the full suite is unchanged.
2. **Refusing a PyTorch config crashed instead of refusing.** *Found:* `test_int8_is_refused`
   failed with Hydra's `ConfigCompositionException: Could not override 'model.threads'`
   instead of `ScorerLoadError`. *Cause:* the thread count was a Hydra override applied before
   the precision check, and PyTorch model configs have no `threads` key. *Fix:* compose the
   config, check `scorer` and `precision`, then set `threads` (and `local_dir`) on the
   resolved dict. *Verified:* the int8 and PyTorch configs are both refused with the intended
   message.
3. **The OpenAPI schema still advertised FastAPI's own error format.** *Found:* reading the
   generated schema, `HTTPValidationError` was still among the components after every route
   declared problem responses. *Cause:* `GET /v1/proteins/{id}` has a path parameter and
   declared only 404. FastAPI then adds its default 422 schema, which the generated TypeScript
   types would have offered the frontend as a real response. *Fix:* declare 422 as a problem
   response there too. *Verified:* a test asserts every documented error is
   `application/problem+json` with the `Problem` schema and that `HTTPValidationError` is gone.

**Supporting details.**
- *Real startup* (`make test-slow`, fresh interpreter): the bundle snapshot plus both fp32 models
  from the pinned Hub revisions load in 48.6 s (models already in the local Hugging Face cache
  from Task 2). `/ready` returns 200, both scorers score ubiquitin, and neither torch nor
  transformers is loaded.
- *Cost:* largest landscape (A0A140D2T1_ZIKV, 3,423 residues) 0.69 s per request with its file
  already parsed, 4,374,165 bytes of JSON, 935,669 gzipped. Smallest (TCRG1) 5 ms. Parsing all
  217 protein files raised peak RSS by 462 MB, so they are parsed on demand into a 32-entry LRU.
- *Types:* `web/package.json` + `pnpm-lock.yaml` pin openapi-typescript 7.13.0, TypeScript
  5.9.3 and pnpm 12.10.1. `web/src/lib/api-types.ts` (973 lines) is identical across two
  generations. CI job `types` uses `pnpm/action-setup@v6` and `actions/setup-node@v7` (both
  tags checked upstream), Node 26.
- *Dependencies:* no new Python packages (fastapi and pydantic-settings were already in the
  `api` extra; the API also needs the `onnx` extra).
- *Tests:* `make lint` clean. `make test` passes: 712 tests plus 15 LightGBM, 37 of them new
  API tests (36 fast, 1 slow). `make test-slow`'s new API test passes. Run time 1 min 48 s.
- *Loose ends:* `/v1/score`, `/v1/jobs` (SSE), `/metrics`, CORS, rate limiting, diskcache and
  schemathesis are later Phase 4 tasks. The 0.69 s worst case is response building and could
  be cached as bytes if the Space needs it. `bundle_dir` and `onnx_local_dir` are for tests
  and development, and a local bundle reports `bundle_revision: null`.

## Entry 31 — Live scoring: fast mode, precise-mode jobs with SSE, the score cache (Phase 4 Task 4, 2026-10-17)

**Plain-language summary.** The API can now score any protein sequence, not only the 217
ProteinGym ones. `POST /v1/score` (fast mode) runs ESM-2 35M or 150M once over the sequence
and returns either the full landscape (every one of the 19 × L substitutions) or the scores of
up to 50 named mutations, in well under a second. `POST /v1/jobs` (precise mode) runs the
slower and better masked marginal on 35M: one model pass per residue, so about a minute for 600
residues. It runs in the background. The client follows it on a live stream (Server-Sent
Events) that sends a progress message with the newly scored rows after each batch of 16
residues, and the finished landscape at the end. Only one job runs at a time (the server has 2
CPUs). Four more can wait, a fifth is told to retry (429), a job can be cancelled, and results
are kept for an hour. Identical requests are answered from a disk cache. Every input is checked
before any model runs, and every response says "Zero-shot". A slow test shows that the served
ONNX models give the same rankings as our PyTorch scores for a ProteinGym protein, and a fuzzer
(schemathesis) sends about a thousand generated requests to check that every response matches
the published API description. Decisions in ADR 0031.

**The 3 most important design choices.**

1. **Each job event is one forward batch of the real scorer, and events are a replayable
   log.** The job asks the ONNX scorer how it would batch the masked passes (16 residues per
   call) and runs exactly those batches. A progress event therefore is real work done, not a
   timer. Each job keeps its events in a list, and a stream replays the list and then waits,
   with `Last-Event-ID` to resume. A client that connects late or reconnects sees every event
   once, in order. *Evidence:* a 76-residue job streams `queued, running, progress × 5,
   succeeded`, ids 0–7. Its rows cover residues 0–75 once each and equal the final landscape,
   which equals the scorer's own masked marginal. A 600-residue job with the real 35M model
   took 63.8 s through the API against 63.9 s for the bare scorer (ADR 0029), so the batching
   adds no measurable time.
2. **The cache key never trusts a revision that the files did not come from.** Keys are
   `[hf_id/file, revision, method, sha256(sequence)]`, the pipeline cache's fields. Files read
   from a local directory are keyed `local:<dir>`, not by the pinned Hub commit, so stand-in
   or unreleased files can never fill entries that the released model will read. A cancelled
   or failed job's partial landscape is never written. *Evidence:* tests show a repeat request
   returns the same bytes with no new forward pass (also after a restart, since the cache is on
   disk), that a mutations request reuses the full landscape's entry, that fast and precise
   results of one sequence are separate entries, and that a cancelled job's sequence is
   computed again.
3. **The agreement test compares like with like.** The task asked for live scores against
   "the bundle's scores". The bundle has zero-shot landscapes of ESM-2 650M and SaProt only,
   and the live models are 35M and 150M. Comparing them would test model similarity, not
   serving. The reference became our PyTorch landscapes of the same models and methods for the
   bundle's TCRG1 target, written from the pipeline cache into a 31 kB golden file. *Evidence:*
   Spearman 0.99999997 for all three (35M fast, 150M fast, 35M precise) with the real fp32
   files at `82e1528`, against a 0.99 threshold.

**Problems encountered and how they were solved.**

1. **The requested agreement test could not pass as specified.** *Found:* before writing it,
   reading `conf/export/default.yaml`: `zero_shot_models: [esm2_650m, saprot_650m_af2]`, with
   the comment that 35M / 150M landscapes are not shipped. *Fix:* asked the author, who chose a
   golden file of our PyTorch landscapes, read from the cache only
   (`make_live_golden` fails on a missing or partial entry; all three entries were cache hits).
   *Verified:* the slow test passes (above). The test can also tell the methods apart: the
   golden 35M wild-type and masked landscapes differ by up to 4.64 nats, with a Spearman of
   0.977 between them, below 0.99.
2. **Two agreement numbers were identical to 16 digits.** *Found:* the first slow run printed
   0.9999999740953683 for both 35M fast and 35M precise, two different methods. *Cause:* not
   a mix-up. Rounding the responses to 4 decimals creates tied values: 3 ties in each 35M
   landscape and 4 in the 150M one. The Spearman of each golden landscape with its own rounded
   copy is exactly the live number (0.9999999740953684 for both 35M, 0.9999999654604911 for
   150M). *Verified:* the two golden landscapes differ (problem 1), so the identical numbers
   come from the rounding: apart from the ties it creates, the live ranks match PyTorch's.
3. **Schemathesis: an undocumented 400.** *Found:* the first fuzzing run sent request bodies
   that are not valid UTF-8 to both POST routes. FastAPI answers "There was an
   error parsing the body" with 400, which the schema did not list. In the counting run it
   happened 6 times on `/v1/score` and 4 on `/v1/jobs`. *Fix:* document 400 as a problem
   response on both routes (a body that is not UTF-8 is a bad request, not a validation
   error). *Verified:* the fuzz test passes.
4. **Schemathesis: a 405 with the wrong `Allow` header.** *Found:* `OPTIONS /v1/jobs/0`
   returned `Allow: GET`, but that path also serves `DELETE`. *Cause:* `GET` and `DELETE
   /v1/jobs/{id}` are two routes, and Starlette's 405 names only the first route that
   matched. *Fix:* the 405 handler lists every method of every route that matches the path
   (`problems.allowed_methods`). *Verified:* the fuzz test passes, over about 100–156 requests
   per operation (11 operations).
5. **FastAPI's own error schema came back.** *Found:* `test_openapi_documents_problem_responses`
   failed on the first full run: `HTTPValidationError` was in the components again. *Cause:*
   the same as Entry 30's problem 3. The job routes have path and header parameters and did
   not declare 422, so FastAPI added its default. *Fix:* declare the problem 422 on them.
   *Verified:* the test passes, and the generated TypeScript types have no
   `HTTPValidationError`.

**Supporting details.**
- *Cost* (M4 Pro, real fp32 files, 2 threads, random sequences): fast mode at 1,000 residues,
  the whole request with JSON: 0.29 s (35M) and 0.60 s (150M) on a miss, 0.037 s on a hit.
  Precise mode at 600 residues: 63.8 s, 38 progress events.
- *Settings:* `score_default_model` (`esm2_150m`, ADR 0029's switch to 35M is this variable),
  `precise_max_length` (600, provisional: under 3 min unless the Space is more than 2.8×
  slower than the M4 Pro), `job_queue_size` (4), `job_ttl_seconds` (3,600), `score_cache_dir`,
  `score_cache_bytes` (256 MB). `/ready` reports the default model and the limit.
- *Dependencies:* `diskcache` 5.6.3 (`api` extra; the live-score cache the plan named),
  `schemathesis` 4.31.1 (dev; fuzzing against the OpenAPI schema). The fastapi lower bound
  moves from 0.115 to 0.143, the version tested, for `fastapi.sse.EventSourceResponse`
  (no `sse-starlette` needed).
- *Types:* `make types` regenerated `web/src/lib/api-types.ts` (+624 lines, `JobProgress`
  included through the OpenAPI components), identical across two runs.
- *Tests:* `make lint` clean. `make test` passes: 741 tests plus 15 LightGBM (Entry 30: 712 +
  15). New: 28 in `test_live.py` and the schemathesis test (11 operations as subtests), plus
  the slow agreement test, which passes (4 s with the models in the local Hugging Face cache).
- *Loose ends:* jobs live in one process's memory, so a restart loses queued and running jobs
  (finished results stay in the score cache). Rate limiting (slowapi), CORS, `/metrics` and
  Sentry are the next task. Nothing yet limits fast-mode request rate. After the first
  deploy, the Space measurement sets the precise limit and the default model.

---

## Entry 32 — API hardening and observability (Phase 4 Task 5, 2026-10-17)

**Plain-language summary.** The API is now safe to put on the public internet. Every client
(an IP address) has rate limits: 120 reads, 20 fast scores and 2 precise jobs a minute (10 an
hour). Fast mode also has one limit for everyone together (120 a minute), so many addresses
cannot add up to more than the 2 CPUs can do. A client may have one job queued or running at a
time and four event streams open. Bodies over 16 KiB are refused (413). Only the configured
website may call the API from a browser (CORS). Every refusal is the API's usual problem
format, and a rate-limit refusal says when to retry. Every request gets an id, returned in a
header and written in every log line it causes. Logs are JSON and never contain a submitted
sequence or mutation list, only the sequence's hash and length, and a test fails if one ever
appears. `/metrics` reports latency, the job queue, the cache hit rate and model time to
Prometheus, behind a token. Sentry error reports are optional and scrubbed. Because the queue
and the counters live in one process's memory, the server must run as one process, and the
start command and a lock enforce that. A local Locust run showed the 429s starting exactly at
each limit, no server errors at 5 and 20 users, and memory levelling off. Decisions in ADR
0032; threats and the limit for each in `docs/threat-model.md`; the run in `docs/load-test.md`.

**The 3 most important design choices.**

1. **The client is an address it cannot choose.** `X-Forwarded-For` is read only when the
   connection comes from a configured proxy (the Space's), and from the right: the proxy
   appends the address it saw, so anything the client wrote is further left and ignored.
   uvicorn's own proxy-header handling is off, since it believes the header from 127.0.0.1.
   *Evidence:* a direct client sending a new `X-Forwarded-For` on each of 5 reads under a
   3/minute limit gets `200, 200, 200, 429, 429`. Behind the proxy, a client that prepends a
   fake address is still counted as itself. In the load test, 10 users behind the proxy each
   had their own count.
2. **Per-client and global limits together, and a per-client cap on jobs.** Per-client limits
   alone let many addresses exceed the CPU, and a global limit alone lets one client use it
   all. The job queue (4) bounded jobs globally, but one client could fill it within its rate
   limit, so a client may have one active job. *Evidence:* in 50 s, inside one 60 s moving
   window, the load test accepted exactly 120 reads, 20 scores for one client, 120 scores over
   10 clients (10–13 each, none near its own 20), 2 jobs, and at most 4 open streams, with
   191,413 / 182,489 / 246,403 / 48 / 22 refusals. Each refusal took about 1 ms.
3. **One process, enforced where a deployment would break it.** With two workers every limit
   would silently double, two precise jobs could share 2 vCPUs, and a job could 404 on the
   other worker. `python -m api.app.serve` hands uvicorn the app object with `workers=1`
   (uvicorn cannot fork workers from an object) and refuses `WEB_CONCURRENCY` above 1. A
   `flock` taken in the lifespan stops any other launch, like `uvicorn --workers 4` or
   gunicorn. *Evidence:* tests for both, and the lock caught a real second app in the existing
   tests (problem 1).

**Problems encountered and how they were solved.**

1. **The new lock failed an existing test.** *Found:* the first API test run after adding the
   lock: `test_identical_requests_are_cache_hits` failed with `SecondProcessError`. *Cause:*
   the test checks that the disk cache survives a restart by starting a second app on the same
   settings while the first is still running. That is exactly what the lock forbids. *Fix:* the
   "restarted" app gets its own lock file (`model_copy`), with a comment saying why.
   *Verified:* the test passes, and `test_second_server_refuses_to_start` keeps the
   same-lock case as a deliberate failure.
2. **The limits broke tests of other behaviour.** *Found:* the same run:
   `test_cancel_queued_and_running_jobs` got a 429 on its second job. *Cause:* every
   in-process test client is 127.0.0.1, so tests that submit several jobs met the new
   one-active-job rule and the 2/minute job limit. *Fix:* the shared fixtures use loose limits
   (`LOOSE_LIMITS`: 10,000 a minute, 100 active jobs and streams), and `test_hardening.py` sets
   each limit explicitly. The defaults were not changed to make tests pass. *Verified:* all API
   tests pass, schemathesis included.
3. **The leak test first failed on pytest's own log copy.** *Found:* the first run of
   `test_no_sequence_or_mutation_in_any_log` found the sequence in the checked output.
   *Cause:* the test read `caplog.text` as well as stderr. `caplog` is pytest's unformatted
   copy of standard-library records (here, a deliberate `logger.exception` with the sequence in
   its traceback), not what the API writes. *Fix:* check what the API writes (stderr, where
   structlog and the API's JSON handler for library loggers both go). The same test asserts
   that the sequence's hash does appear, so the check cannot pass on empty output.
   *Verified:* the test passes. The API's handler output had the traceback redacted.
4. **Was the 500 reported to Sentry twice?** *Found:* the 500 handler called
   `sentry_sdk.capture_exception` explicitly, and the Starlette integration may also report.
   The test saw exactly 1 event. *Probe:* removing the explicit call still gave 1 event, so
   the integration reports a 500 by itself, and Sentry had deduplicated the second capture of
   the same exception. *Fix:* the handler no longer calls it. A failed job is not a request
   error, so the worker keeps its explicit call, and the test now expects exactly 2 events (one
   500, one failed job), both without bodies or sequences. *Verified:* passes.
5. **Two tracebacks in every server log of the first load run.** *Found:* the dry run's summary
   counted 2 tracebacks per scenario, with no error lines. *Cause:* the runner stopped the
   server with SIGINT, and uvicorn re-raises it as `KeyboardInterrupt` after a clean shutdown.
   *Fix:* the runner sends SIGTERM, which is what Docker and the Space send. *Verified:* 0
   tracebacks and 0 error lines in all 7 scenarios of the final run.
6. **Secrets as plain strings in tests.** *Found:* two tests failed with `'str' object has no
   attribute 'get_secret_value'`. *Cause:* `Settings.model_copy(update=...)` does not
   validate, so a token or DSN passed as `str` stayed a `str`. *Fix:* the tests pass
   `SecretStr`. *Verified:* both pass.

**Supporting details.**

- **Load test** (M4 Pro, not representative of the Space; one process, ONNX threads = 2, real
  fp32 models): at 5 users (180 s) 524 requests and at 20 users (600 s) 6,922 requests, with 0
  failures, 0 5xx and 0 error lines. At 20 users the 360 refused scores match the global
  limit: 1.97 accepted a second, 118 a minute. Memory at 20 users levelled off at about 2.5 GB
  (quarter means 2,406 → 2,471 → 2,503 → 2,523 MB). At 5 users it was still rising after 3
  minutes (1,264 → 1,998 MB). The likely cause is ONNX Runtime's arena keeping the buffers of
  the largest concurrent passes (inferred, not measured), and Task 7 should watch memory on the
  Space. One unexplained reading: in the nearly idle `jobs` scenario resident memory fell from
  939 to 281 MB, probably macOS compressing idle pages. Fast-mode p95 was 390 ms (5 users) and
  520 ms (20 users), max 1.0 s.
- The first load run was a dry run. A code change during it (`/metrics` under the read limit)
  would have mixed code versions across scenarios, because each scenario starts a fresh
  server. All 7 scenarios were therefore run again on the final code, and only that run is
  reported.
- Found while writing the threat note: `/metrics` had no rate limit, so wrong-token guesses
  were free. It is now under the read limit (a scrape every 15 s uses 4 of 120 a minute).
- Dependencies (`api` extra): slowapi 0.1.10, limits 5.8.0, prometheus-client 0.26.0,
  prometheus-fastapi-instrumentator 8.1.0, sentry-sdk 2.72.0. Locust 2.46.7 and psutil 7.2.2
  live in `tools/loadtest/` (their own lock), like the ONNX exporter.
- To set at deploy (Task 6): `MUTASCOPE_API_TRUSTED_PROXIES` (the Space proxy's range; empty
  means all users share one quota), `MUTASCOPE_API_CORS_ORIGINS` (the production site does not
  exist yet), `MUTASCOPE_API_METRICS_TOKEN`, and optionally `MUTASCOPE_API_SENTRY_DSN`. The
  limits are provisional until the Space is measured (Task 7).
- Loose end: IPv6 clients are keyed by full address, so one /64 is many clients (threat note,
  "What is left").

---

## Entry 33 — Docker images: a torch-free API image and a pipeline image (Phase 4 Task 6, 2026-10-18)

**Plain-language summary.** The project now ships as two container images.

- **The API image** is what the Hugging Face Space will run. It contains the API, the two exact
  (fp32) ONNX models and the website bundle, but not PyTorch. The models and the bundle are
  downloaded while the image is built, at the pinned commits, and every file is checked against
  the published checksum list. The running container then never needs the internet. It runs as
  user 1000, as the Space requires, can write only to its cache and lock file, and runs as one
  process. Its last build step starts the API inside the image and scores a protein with both
  models, so an image that cannot serve is never produced.
- **The pipeline image** has CPU-only PyTorch and the pinned Foldseek. It runs the whole
  Nextflow smoke test, through the Phase 3 ridge model, with the data and outputs staying on
  the host.

Both were built for linux/amd64 (the Space) and linux/arm64 (the Mac). Tests ran the API image
under the Space's CPU limit: health, readiness, a fast score and a precise job, the Task 5 load
test, and a 20-minute memory soak. Memory levels off, and the rate limits hold exactly. But at
20 users the 2 CPUs saturate and fast mode becomes slow, which Task 7 must address. Decisions in
ADR 0033; results in `docs/load-test.md`, section 5.

**The 3 most important design choices.**

1. **Split the package's dependencies instead of filtering the install.** torch and
   transformers were core dependencies, so any install of `mutascope` pulled them in. The base
   now holds only what the API imports (measured from `sys.modules`: httpx, huggingface-hub,
   hydra-core, numpy, pydantic, structlog). The pipeline's stack is the `pipeline` extra. The
   image installs from the lock with `--extra api --extra onnx`, and there is no list of
   packages to skip that could go stale. *Evidence:* `uv lock` changed metadata only (no
   version moved). The serving set is 54 packages with no torch, transformers or pandas. The
   builder step fails if `import torch` works. `make setup` and `make test` are unchanged.
2. **Bake the pinned files into the image, verified, and run offline.** `api.app.fetch`
   downloads the bundle and the fp32 graphs and tokenizers at the pinned commits and checks
   each file against that commit's `SHA256SUMS`. A file the list does not name fails like a
   wrong digest. The runtime sets `HF_HUB_OFFLINE=1`. A cold start on the Space then neither
   downloads 760 MB nor depends on the Hub, and every check happens once, before anything
   ships. *Evidence:* 223 bundle files and 4 ONNX files verified on both architectures. Unit
   tests reject a changed and an unlisted file and confirm int8 is never fetched. The container
   tests show the image starting offline.
3. **One self-check, run by the test suite and by the image build.** `python -m
   api.app.selfcheck` starts the app in-process, requests `/ready` and a landscape, scores with
   both models, and fails if torch or transformers was loaded. `test_no_torch.py` runs it in a
   fresh interpreter. The image runs it as its last build step, as UID 1000, offline, on the
   real write paths. *Evidence:* it passes in both places. Problem 1 shows why it must run on
   the real write paths.

**Problems encountered and how they were solved.**

1. **The image's user could not create its lock file.** *Found:* the first container test run.
   The container exited at startup with `PermissionError: [Errno 13] Permission denied:
   '/home/user/.cache/mutascope/api.lock'`. The build had passed, because its self-check used
   a scratch directory. *Cause:* `install -d -o user` gives only the last directory of the path
   to the user. `/home/user/.cache` and `/home/user/.cache/mutascope` stayed root-owned.
   *Fix:* create each directory on the path owned by the user, and run the self-check on the
   real write paths, then empty them in the same layer. *Verified:* all 10 container checks
   pass, including that UID 1000 can write its cache and lock and cannot write code, venv or
   weights. A regression of this kind now fails the build itself.
2. **biotraj would not install on linux/arm64.** *Found:* the first arm64 pipeline-image
   build: `error: [Errno 2] No such file or directory: 'gcc'` while building `biotraj` 1.2.2
   (biotite's dependency). *Cause:* biotraj publishes Linux wheels for x86_64 only, so uv builds
   it from source on arm64. *Fix:* `build-essential` in the builder stage only; the runtime
   stage has no compiler. *Verified:* the arm64 image builds, and its last step checks CPU
   torch, the Foldseek version and the CLI.
3. **`-profile docker` broke on this repository's path.** *Found:* the first `docker,test`
   run. `SCORE_ZERO_SHOT` failed with `PermissionError at /Users/abdullahhanif/Documents/Developmet
   when downloading facebook/esm2_t6_8M_UR50D`. *Cause:* `HF_HOME` was set in the profile's
   `env` scope. Nextflow hands that scope to the container through an unquoted `eval
   $(nxf_container_env)` in `.command.run`, which split the value at the space in
   "Developmet Projects", so the cache path was cut short. Earlier, `file()` had also failed
   inside the config closure ("Unknown method invocation `file`"). *Fix:* pass `HF_HOME` with
   a quoted `-e` in `process.containerOptions`, next to the mounts, and use the params' absolute
   paths as they are. *Verified:* all 20 tasks pass in 62 s.
4. **Trivy flagged two Python packages in the images.** *Found:* the first scan: HIGH
   CVE-2026-23949 (`jaraco.context` 5.3.0) and CVE-2026-24049 (`wheel` 0.45.1) in both images.
   *Cause:* they are copies vendored inside the base image's own setuptools, which the
   servers never use (they run from `/opt/venv`, which does not see the system site-packages).
   *Fix:* uninstall the base image's pip, setuptools and wheel in both runtime stages.
   *Verified:* the rescan finds no Python package. 8 HIGH CVEs remain, all in Debian 13
   packages with no fixed version, and there is no critical.
5. **The amd64 pipeline build failed once.** *Found:* under emulation on the Mac, `uv sync`
   ended after 7,326 s with `Failed to download duckdb==1.5.6 … error reading a body from
   connection`. *Cause:* a stalled download, not the Dockerfile. *Fix:* none needed; the
   identical retry passed. *Verified:* the amd64 image runs on x86_64 with the pinned Foldseek
   and torch `2.14.1+cpu`.

**Supporting details.**
- *Sizes* (unpacked / compressed): API arm64 1,138 MiB / 597 MB, amd64 1,124 MiB / 600 MB
  (target about 1.5 GB; 762 MB is the baked-in Hub cache, 276 MB the venv). Pipeline arm64
  2,165 MiB / 642 MB, amd64 2,256 MiB / 687 MB.
- *Docker against native pipeline,* same 2 assays:
  - variants, folds, structure features and 3Di are value-identical, so Linux Foldseek gives
    the macOS build's tokens;
  - zero-shot scores differ by at most 2.2e-5, CPU against MPS;
  - B2L11's ridge fit differs by 40 in an intercept of 1.16 × 10⁷ (DMS scores on a 2 × 10⁷
    scale), a relative 3.4e-6, which is not a bug.

  `train check-release` stays a same-machine check.
- *Container run* (local container, not the Space; `--cpus=2 --memory=16g`; the Docker VM had
  7.75 GiB, so that was the real ceiling):
  - every limit accepted exactly its value;
  - 0 5xx and 0 failures at 5 users (418 requests), at 20 users (1,921) and in the soak
    (3,141);
  - soak memory flat at 2,472–2,503 MB from minute 7 to minute 20; 20 users levelled off at
    about 3.19 GB;
  - at 20 users fast mode's median was 5.6 s and its p95 46 s, against 72 / 520 ms for the
    unconstrained process in Task 5.
- *Trivy 0.76.0:* remaining HIGH CVEs CVE-2026-76642, -78408, -78409, -78410 (util-linux),
  CVE-2025-69720 (ncurses), CVE-2026-16742 (systemd libraries), CVE-2026-54369 (libacl1),
  CVE-2026-9538 (perl-base, fix deferred). No critical.
- *New:*
  - files: `api/Dockerfile`, `pipeline/Dockerfile`, `.dockerignore`, `.env.example`,
    `docker-compose.yml`, `api/app/{fetch,selfcheck,healthcheck}.py`, `api/tests/test_fetch.py`
    (5) and `api/tests/test_container.py` (10, marker `container`), plus tests for
    `.env.example` and the health probe;
  - Make targets `api-image`, `pipeline-image`, `images`, `test-container`,
    `pipeline-test-docker`;
  - `tools/loadtest/run.py --image` and a `soak5` scenario.
- *Dependencies:* no new package. numpy is now a direct dependency (it was transitive at the
  same version). The `pipeline` extra holds what was core.
- *Tests:* `make lint` clean; `make test` 770 + 15 LightGBM passed, 1 skipped (Foldseek);
  `make test-container` 10 passed.
- *Loose ends:*
  - Fast-mode concurrency on 2 CPUs and the limits: Task 7, measured on the Space.
  - CI still runs the smoke test natively. Building the images in CI, `docker,test` there and
    the GHCR push are the CI task.
  - The memory soak did not run under a real 16 GB limit.

---

## Entry 34 — CI/CD: required checks, release-please and deployment to Cloud Run (Phase 4 Task 7, 2026-10-18)

**Plain-language summary.** Every pull request now runs five checks: lint and type-check, the
tests with coverage, the generated API types, the Nextflow smoke test inside the pipeline image,
and a build of the API image followed by its container tests. Merging into main will require all
five to pass and the branch to be up to date (the rule is set in GitHub by the author). Versions
are managed by release-please: it keeps a release PR open with the next version and the
changelog, and merging it creates a tag. A tag builds the API image for linux/amd64 once, pushes
it to Google's Artifact Registry and to GitHub's registry (GHCR), and deploys it to Google Cloud
Run. The workflow then calls the public URL and fails unless it reports the new version and
`/ready` passes. GitHub holds no Google key: Google trusts GitHub's identity token for this one
workflow. Dependabot opens weekly update PRs for Python packages, GitHub Actions and the Docker
base images. The plan was a Hugging Face Docker Space, but those now need a paid plan (problem
4). Decisions in ADR 0034. The first deploy and the live latency measurement wait for the
author's one-time Google Cloud setup.

**The 3 most important design choices.**

1. **The service is a file in the repository, applied whole.** `conf/deploy/cloudrun.yaml` is
   rendered into the complete Cloud Run service (2 vCPUs, 4 GiB, one instance, probes,
   environment, image) and applied with `gcloud run services replace`. Nothing is set in the
   console, and a hand-made change is undone by the next deploy. ADR 0031's 2-second fallback,
   the measured `trusted_proxies` and the precise-mode limit therefore become reviewed one-line
   changes, applied by a redeploy without a rebuild. *Evidence:* `api/tests/test_deploy.py`
   checks the rendered service: image by digest from Artifact Registry, `maxScale` 1, 2 CPUs and
   4 GiB, the startup probe on `/ready`, the runtime account, and an environment that is exactly
   the config's.
2. **Deployed means the tagged version is serving and ready, by digest.** Cloud Run runs the
   image by digest, so the revision is the bytes CI built and tested. Its startup probe on
   `/ready` keeps traffic on the previous revision until the new one has loaded the models. Then
   `wait` checks from outside that `/health` reports the tag's version and `/ready` is 200. The
   release first checks that the tag equals the package version, so a mismatched tag fails
   before the build. *Evidence:* the tests script a cold start (no answer), the old version, then
   not ready, and `wait` passes only on the last poll. The timeout case fails.
3. **Keyless deployment, scoped to one workflow.** Workload Identity Federation lets GitHub's
   OIDC token act as the deployer service account only when the repository is this one, the
   workflow is `release.yml` and the ref is a `v` tag or main. No service-account key exists to
   leak or rotate. A PR or a fork cannot deploy. The deployer can manage Cloud Run, write to one
   Artifact Registry repository and run services as the runtime account, which has no roles.
   *Evidence:* `tools/deploy/setup-gcp.sh` (shellcheck clean) creates exactly these bindings and
   the provider's attribute condition.

**Problems encountered and how they were solved.**

1. **main had a protection rule that required nothing.** *Found:* reading the repository's
   rules through the API for this task. The active ruleset "protect main" has a
   required-status-checks rule whose list is empty (`"required_status_checks": []`), and
   `strict_required_status_checks_policy` is `false`. A PR could therefore merge with red checks
   or with checks that ran against an old main, which is how main went red twice in Phase 3.
   *Fix:* CI's job names are now stable check names, and the PR gives the exact steps to add the
   five checks and turn on "up to date" in that ruleset. *Verified:* not yet. The rule has to be
   set by the author, and the check names appear in GitHub's picker only after this PR's CI has
   run once.
2. **12 of the 16 action references in CI were moving major tags.** *Found:* reviewing
   `ci.yml` against CLAUDE.md's pinning rule: `actions/checkout@v7`, `actions/cache@v6`,
   `actions/upload-artifact@v7`, `pnpm/action-setup@v6`, `actions/setup-node@v7` and
   `nf-core/setup-nextflow@v3`. *Fix:* each is pinned to its latest exact release, looked up on
   the action's releases (`checkout@v7.0.1`, `cache@v6.1.0`, `upload-artifact@v7.0.2`,
   `action-setup@v6.1.0`, `setup-node@v7.1.0`, `setup-nextflow@v3.0.1`), as are the new Docker
   and release-please actions. Dependabot moves them from now on. *Verified:* no `uses:` line in
   the three workflows ends in a bare major version.
3. **release-please cannot bump `uv.lock`, and a stale lock fails every CI job.** *Found:*
   uv records the project's own version in `uv.lock`, and CI installs with
   `uv sync --locked`, which refuses a lock that does not match `pyproject.toml`. release-please
   has no uv support (googleapis/release-please#2561). A release PR that bumped only
   `pyproject.toml` would therefore be red and could never merge. *Fix:* release-please's
   generic TOML updater with `$.package[?(@.name.value=='mutascope')].version`. The `.value` is
   needed because its TOML parser wraps each string in an object (#2455). *Verified:* locally,
   with release-please 17.11.2's own updaters on this repository's files: exactly one line of
   `uv.lock` changes (line 1476, mutascope's `0.1.0` → `0.2.0`), `pyproject.toml` changes one
   line, and `uv lock --check` passes on the bumped pair.
4. **The planned host became paid.** *Found:* creating the Hugging Face Space. The form allows
   only Static Spaces on a free account ("Gradio and Docker Spaces require a paid plan"). Every
   ADR since 0029 assumed a free Docker Space with 2 vCPUs. *Alternatives checked against the
   measured memory:* the API container uses 1,227 MB right after startup, 2,080 MB at the
   end of the 5-user run, and peaks at 3,208 MB at 20 users
   (`docs/load-test-container-summary.json`). Render's free instance (512 MB, 0.1 CPU) cannot
   load the models. *Fix:* Google Cloud Run with 2 vCPUs and 4 GiB, at most one instance, scaled
   to zero, inside the free tier. Only the deploy changed: the image, CI, release-please and
   Dependabot are as they were, and the image needed no change. *Verified:* the rendered
   service is checked by unit tests and the workflow by actionlint. The first real deploy is
   pending (it needs the author's Google Cloud project).

**Supporting details.**

- **CI jobs** (ADR 0034, section 1). The smoke test moved from the native environment to
  `-profile docker,test` in an image built in the job. CI no longer installs Foldseek; the
  image's checksum-verified build is the one tested. Coverage now includes `api/`.
- **Author's setup:** a Google Cloud project with billing and a budget alert,
  `tools/deploy/setup-gcp.sh` once, its project id and number in `conf/deploy/cloudrun.yaml`,
  and one GitHub secret, `RELEASE_PLEASE_TOKEN` (fine-grained PAT for this repository:
  Contents, Pull requests and Issues read and write). release-please needs its own token because
  GitHub starts no workflow for events made with `GITHUB_TOKEN`: its PR would get no checks and
  its tag would start no release.
- **Cloud Run trade-offs** (ADR 0034, section 4). Request-based billing gives an instance CPU
  only during a request, so a precise job advances while its SSE stream is open (the website's
  way). Scale-to-zero means a cold start for the first visitor and no score cache across it.
- **Dependabot** uses the `uv` ecosystem, not `pip`, so `uv.lock` is updated too. The
  Dockerfiles' `FROM` lines are now literal (tag and digest) instead of `ARG`s, so that
  Dependabot can read them. The images are unchanged. Python is held to 3.11 patches.
- **Live measurement tool:** `tools/loadtest/live.py` (ADR 0034, section 6). It was dry-run
  against a local server (the numbers there are M4 Pro numbers and are not reported), and every
  step completed: cold start, baseline, four latency cells of cache misses, a precise job
  followed on its stream, the 5-user Locust run, the decision.
- **Loose ends:** the first release, the deploy, the live numbers and ADR 0031's decision;
  `trusted_proxies` from the API's access lines in Cloud Logging; ADR 0033's 20-user CPU
  saturation is still open (bounding concurrent forward passes), to be judged on the live
  numbers.

---

## Entry 35 — First deploy to Cloud Run, measured live (Phase 4 Task 7, 2026-10-18)

**Plain-language summary.** Merging the release PR tagged `v0.2.0`, and the release workflow
built the API image, pushed it, deployed it to Google Cloud Run and confirmed the public URL
served version 0.2.0 with `/ready` passing. The live service was then measured from a laptop in
Germany. At 1,000 residues the larger model (150M) takes 5.72 s at the 95th percentile, well over
the 2-second rule, so the default became the smaller model (35M, 2.09 s). A 600-residue precise
job took almost 7 minutes, so the length limit dropped to 400 residues (163 s, measured). The
service needs 20.5 s to wake from zero. Cloud Run's front end turned out to be one fixed address,
which now becomes the trusted proxy, so each visitor gets their own rate limits. All three
settings are in `conf/deploy/cloudrun.yaml` and reach the service by a redeploy. Numbers in
`docs/load-test.md`, section 6; decisions in ADR 0034, section 6.

**The 3 most important design choices.**

1. **Change behaviour by configuration, from measurements.** ADR 0031 made the default model a
   setting so that the Space measurement could switch it without a code change. That is what
   happened, on a different host: one line in `conf/deploy/cloudrun.yaml`, with the measurement
   beside it. *Evidence:* 150M p95 5.72 s at 1,000 residues (20 cache misses), against the 2 s
   rule.
2. **Measure the limit, do not extrapolate it.** The 600-residue job (406 s) showed the limit was
   wrong, but not what it should be. Rather than divide by the slowdown, a second job at 400
   residues was timed: 163 s. Fitted through both, time grows as length^2.24, so 450 would be
   about 210 s, over the target. *Evidence:* both jobs followed on their SSE stream, as the
   website does, because on Cloud Run an instance has CPU only while a request is open.
3. **Find the proxy from the logs before trusting it.** `trusted_proxies` was left empty for the
   first deploy, so every request was logged with the address it really came from. The logs
   showed one address, `169.254.169.126`, on all 413 requests. Only that /32 is trusted: anyone
   could otherwise fake their address in `X-Forwarded-For`. *Evidence:* Cloud Logging query on
   `jsonPayload.client`. The settings accept the value, and a spoofed address left of the real
   one is ignored (`client_address`).

**Problems encountered and how they were solved.**

1. **A container test passed or failed depending on test order.** *Found:* CI on main after
   merging #35, and on Dependabot's #38: `test_no_sequence_in_the_container_logs` failed with
   `'sequence_sha256'` missing from the logs. *Cause:* it checked the logs for a hashed sequence
   but relied on another test having scored one in the shared container; pytest-randomly
   shuffles the order. *Fix:* the test sends its own score request first (#39). *Verified:* run
   alone it failed every time before the fix and passed after; it also passed under three
   random orders (seeds 11, 22, 33).
2. **Two load-test failures were not the API's.** *Found:* Locust reported 2 failures, "429
   without a problem body", among job submissions. The API's 429s always carry one. *Cause:*
   Cloud Logging showed both at 14:10:36 UTC with the text "The request was aborted because there
   was no available instance": Cloud Run itself, with its one instance busy. *Fix:* none to the
   API. The count is reported as the platform's (2 of 226), and bounding concurrent forward
   passes is listed as the next step. *Verified:* the other 29 429s match the API's limits
   (17 jobs + 12 scores), and 191 + 4 + 31 = 226 requests.
3. **A redeploy would have used the release's old configuration.** *Found:* before the first
   redeploy, reading `release.yml`. The deploy job checked out the tag, so a manual redeploy of
   `v0.2.0` would have read `v0.2.0`'s `cloudrun.yaml`, without the new settings, and passed,
   because `/health` and `/ready` do not show them. *Fix:* the deploy job checks out the commit
   of the run (the tag's on a tag, main's on a manual run); the image still comes from the tag by
   digest. *Verified:* actionlint. The redeploy itself, and `/ready` reporting `esm2_35m` and 400,
   is the check after this merges.

**Supporting details.**

- **Live numbers** (p50 / p95, network included; round trip 0.20 / 0.28 s): 35M 0.59 / 0.92 s at
  300 residues, 1.83 / 2.09 s at 1,000; 150M 1.56 / 1.85 s and 4.80 / 5.72 s. The M4 Pro did
  150M at 1,000 residues in 0.53 s; the deployed vCPUs are much slower.
- **5 users, 2 minutes:** 226 requests, no 5xx. Scores p50 1.4 s, p95 10 s; protein detail p99
  9.9 s: reads wait behind scoring.
- **Where earlier documents say "the Space"**, they mean the deployed service (ADR 0034).
- **Loose ends:** bounding concurrent forward passes; checking, after the redeploy, that the
  access lines show visitors' addresses instead of the front end's.

---

## Entry 36 — Phase 4 close-out (2026-10-18)

**Plain-language summary.** Phase 4 is closed. Its goal was a tested, observable service that
deploys itself, and most of that is true. Every pull request runs five required checks. A
version tag builds the API image, pushes it and deploys it to Google Cloud Run, which serves
version 0.2.0 at https://mutascope-api-2lvmztrsha-ew.a.run.app with its interactive docs at
`/docs`. Two parts fall short and are written down. The 2-second rule for fast scoring holds at
300 residues (0.92 s) but not at 1,000 (2.09 s) or with five users at once (10 s). And
"observable" is weaker live than in the code: the metrics endpoint and error reporting exist and
are tested, but the deployed service has neither switched on. This task wrote no code. It
checked every Phase 4 item against the repository, GitHub and the live service, rebuilt the
released image to measure it, recomputed the int8 and bundle numbers from their files, wrote
ADR 0035, and pointed the project at Phase 5.

**The 3 most important design choices.**

1. **Judge the done-when line part by part, and close with the miss stated.** All but one
   checklist item are ticked, but the done-when line is what the phase promised. Three of its
   four parts are met. The fourth, fast mode under 2 s, is met at 300 residues and missed at
   1,000 and under load, so ADR 0035 and the roadmap's close note say exactly that, with the
   numbers, instead of a tick.
2. **Measure the image again rather than copy the old figure.** ADR 0033's 600 MB was measured
   before the base images were pinned to literal `FROM` lines, and the registry could not be read
   from this machine. The `v0.2.0` commit was rebuilt for linux/amd64 into an OCI archive: 12
   layers, 600,190,175 bytes compressed, 1,124 MiB unpacked (`du -sxm /`, as ADR 0033). The
   number held, and the ADR says it is a rebuild, not the pushed bytes.
3. **Turn every weak point into an owned next step.** The open item and four follow-ups
   (forward-pass bound, live metrics and Sentry secrets, GHCR visibility, the trusted-proxy log
   check) and the frontend's CORS origin are now Phase 5 roadmap items, and CLAUDE.md's current
   phase names them. A weak point with no owner stays weak.

**Problems encountered and how they were solved.**

1. **The live service is not observable through the API's own metrics.** *Found:* reading
   `conf/deploy/cloudrun.yaml` (`secrets: {}`), then `GET /metrics` on the live URL: 404, which
   is the designed answer when no `metrics_token` is set. No `sentry_dsn` either. *Cause:* ADR
   0034 wired Secret Manager into the deploy, but no secret was created. *Fix:* none in this task
   (it needs a secret created in Google Cloud by the author). Recorded as ADR 0035 weak point 3
   and a Phase 5 roadmap item. *Verified:* the 404 above; `/health` and `/ready` answer 200.
2. **The "public" GHCR image could not be pulled anonymously.** *Found:* `docker buildx
   imagetools inspect ghcr.io/engabdullahhanif/mutascope-api:v0.2.0` failed: GHCR refused an
   anonymous pull token (401). The release log shows the push itself succeeded (manifest
   `sha256:4692a941…` to both registries). *Cause (likely, not confirmed):* new GHCR packages are
   private by default; the `gh` token here lacks `read:packages`, so the visibility could not be
   read. *Fix:* none here. ADR 0035 weak point 4 says "probably not public", and the roadmap asks
   the author to set it or drop the claim.
3. **The frontend could not call the live API.** *Found:* a request with `Origin:
   https://mutascope.abdullahhanif.me` got no `Access-Control-Allow-Origin` header back.
   `cors_origins` defaults to `http://localhost:3000` and the deploy config does not set it.
   *Fix:* none yet, because the frontend's domain does not exist. It is a Phase 5 roadmap item and
   is listed under "What Phase 5 inherits".
4. **A Phase 1 item was still open although done.** *Found:* the roadmap audit. "Profiles:
   `local`, `docker`, `test`" waited for "the Phase 4 image". ADR 0033 built it, and CI's
   `Nextflow smoke test (docker,test, 2 assays)` passes on main (`24d5adf`). *Fix:* ticked, with
   the evidence on the item.
5. **ADR 0029 gives only the aggregate int8 change.** *Found:* recomputing it from
   `results/zero_shot/comparisons.parquet`. The aggregate matched (−0.000534 [−0.001349,
   +0.000158] for 35M, −0.000615 [−0.001223, +0.000153] for 150M), but within function types
   int8 is lower with intervals that exclude 0 for Organismal Fitness (35M −0.0014, 150M
   −0.0015, 77 assays), Stability (35M, −0.0022, 66) and Activity (150M, −0.0011, 43). *Fix:*
   recorded in ADR 0035. int8 was rejected on the per-assay gate, so the serving decision does
   not change.

**Supporting details.**

- **Reported numbers** (all rechecked 2026-10-18):
  - Live URL: https://mutascope-api-2lvmztrsha-ew.a.run.app; `/health` 0.2.0; `/ready`
    `esm2_35m`, limit 400, bundle `3ae76721…`, 217 proteins. The redeploy check left open in
    entry 35 passed.
  - Image (linux/amd64, rebuild of `v0.2.0`): 600 MB compressed, 1,124 MiB unpacked.
  - Fast-mode p95, network included: 35M 0.92 s (300 aa) / 2.09 s (1,000 aa); 150M 1.85 s /
    5.72 s. Precise 400 aa 163 s. Cold start 20.5 s.
  - int8: aggregate Spearman −0.0005 (35M) and −0.0006 (150M), both CIs include 0; per-assay
    agreement below 0.99 on 3 and 1 assays → rejected; fp32 served.
  - Bundle: 217 protein files, 29,218,051 bytes (29.2 MB); 29.7 MB in all; largest 1,153,890
    bytes under the 2 MB budget; `shasum -c SHA256SUMS` all OK.
- **CI on main in Phase 4:** one red run (`9de9434`, the merge of #35), fixed by #39 (entry 35,
  problem 1); green since `9533655`. Open PRs #37, #38 (Dependabot) and #41 (release 0.3.0) pass
  all five checks. The running image's code equals main's.
- **Unchecked:** bounding concurrent forward passes (moved to Phase 5); stretch items from
  Phases 2 and 3 (ESM-1v / ESM C, LoRA, leave-protein-out).
- **Weak (ADR 0035, items 2–10):** 2 s missed at 1,000 residues and under load; no live metrics
  or Sentry; GHCR visibility; trusted proxy unchecked in logs; memory at 20 users on 4 GiB not
  measured; scale-to-zero costs; thin live sample; image size from a rebuild; "the Space" in
  older ADRs.
- **Tests:** `make lint` clean (ruff, format: 260 files, pyright 0 errors). `make test`: 650
  passed, 1 skipped, then 15 LightGBM tests passed in their own process. No code changed, so
  `make test-slow` and `make test-container` were not rerun here; CI's API image job ran the
  container tests on main (`24d5adf`).
- **Docs changed:** ADR 0035; CLAUDE.md (current phase → Phase 5); ROADMAP (Phase 1 profile
  item ticked, Phase 4 close note, Phase 5 "Carried from Phase 4"); README (status line, live API
  link, two "Space" comments); PROJECT_MAP (steps 28–36, the Phase 4 index and retrospective).

---

## Entry 37 — Bounded live inference, secrets, live re-measurement (Phase 5 Task 0, 2026-10-18)

**Plain-language summary.** The live API on Cloud Run answered simple page reads slowly whenever
someone was scoring a sequence. At 5 users the slowest 1% took 9.9 s. The suspected cause was that
every scoring request started its own model run, so several runs fought over the two CPUs. This
task put every model run through one queue, so that only one runs at a time. A request that cannot
start within 10 s now gets a clear "busy, retry later" answer (503 with `Retry-After`) instead of
hanging. In a local container this halved the worst scoring delays at 20 users and saved about
600 MB of memory. It also wired the metrics token from Google Secret Manager into the deployed
service, so `/metrics` now answers there. The token is required: 401 without it, 200 with it. On
Cloud Run the queue works as designed, but **reads are still slow**: the slowest 1% of protein
reads at 5 users took 8.5 s, not under 1 s. So the model runs piling up were not the main cause
there, and finding the real one is the next task. Code in PR #43 (`748fe32`), the deploy fix in
PR #46 (`5b254ea`), measurement and docs in this entry's PR. Decision record: ADR 0036.

**The 3 most important design choices.**

1. **One pool for every forward pass, with the slot released when the thread ends.** Fast mode
   and precise-mode batches both go through `api.app.inference.Inference`: one slot (one pass
   already uses both vCPUs), fast mode waits up to 10 s, precise batches wait their turn, and
   asyncio's semaphore wakes waiters in order. The slot is freed by the future's done-callback,
   not when the request goes away. A forward pass cannot be interrupted, so freeing it on
   cancellation would let a second pass share the CPU. A unit test cancels a caller mid-pass and
   checks that the next caller still waits.
2. **A test that fails on the old code, and a before/after on the same load.** The first container
   test passed against the image from `main` (reads were 23–53 ms locally either way), so it
   proved nothing. It now also requires the inference gauges to show one pass running and others
   waiting, and the `main` image fails it. The local before/after ran the same Locust mix against
   both images under the same limits (`--cpus=2 --cpuset-cpus=0,1 --memory=4g`), and a fresh
   container per run.
3. **Report the live miss as a miss.** The task asked whether read p99 at 5 users is now under
   1 s. It is 8.5 s. ADR 0036 says so in its status line, keeps ADR 0035's item 1 open (reworded),
   and records what the numbers point to. A 429 does no model work, yet at 20 users those answers
   took up to 28 s. So requests wait for the event loop or the GIL, not for an inference slot.
   The next experiments are listed, not guessed at in this PR.

**Problems encountered and how they were solved.**

1. **Reads on Cloud Run did not get fast.** *Found:* the live run (`live.py --load-users 5 20`,
   v0.4.0). At 5 users `GET /v1/proteins/{id}` had a p99 of 8,500 ms (89 requests), against
   9,900 ms on v0.2.0, and `/v1/benchmarks` reached a p95 of 4,900 ms (v0.2.0: 200 ms). At 20
   users reads took up to 29 s, scores up to 34 s, and refused job submissions (429s) up to
   28 s. *Not solved:* the pool held (inference running never above 1; one 503 after 10.5 s), so
   the waiting is outside it. Recorded in ADR 0036, section 6, and as a Phase 5 roadmap item with
   three concrete experiments.
2. **The first v0.4.0 deploy failed its startup probe.** *Found:* the release run's deploy step:
   "The user-provided container failed the configured startup probe checks"; v0.3.0 kept serving
   (`/health` 0.3.0). *Diagnosis:*
   - Not IAM: the revision was created.
   - Not the code: the same image started in about 2 s locally with the deployed environment
     and a well-formed DSN.
   - Not the token: 64 characters, checked by length only.
   - Locally, a malformed DSN gives `sentry_sdk.utils.BadDsn` at startup.

   The author had no Sentry project, so the DSN secret was not a DSN. *Fix:* PR #46 removed it
   from `secrets`, and `workflow_dispatch` redeployed v0.4.0 from main. *Verified:* `/health`
   0.4.0, `/metrics` 401 without the token and 200 with it.
3. **A rejected setting printed its value.** *Found:* while reproducing problem 2, a too-short
   token gave `input_value='short-token'` in pydantic's error, which on Cloud Run goes to the
   logs. *Fix:* a regression test first, then `hide_input_in_errors=True` on the settings.
   *Verified:* the test passes, and the message now names the field and the reason only. The
   live token was never rejected, so nothing leaked.
4. **A pass outliving the event loop logged an error at shutdown.** *Found:* the local 20-user
   run's server log: one `RuntimeError: Event loop is closed` from the done-callback
   (`concurrent.futures`, one line, after "Application shutdown complete"). *Fix:* a regression
   test that closes the loop with a pass in flight, then the release ignores a closed loop.
   *Verified:* the test, and `make test-container` (11 passed) on the rebuilt image.
5. **`live.py` stopped twice before finishing.**
   - *First:* one 150M request at 1,000 residues got `RemoteDisconnected`. The instance had not
     restarted: its 150M pass count was 39 = 1 warm-up + 20 + 18, so the request never reached
     the model. *Fix:* a connection closed without an answer is counted (`no_answer`) instead of
     ending the run.
   - *Second:* the rerun used the same seed as the first, so it sent the same sequences to a
     warm instance whose cache kept them, and the script stopped at the first cache hit (as
     designed: it times misses only). *Fix:* rerun with `--seed 36`; the error now says to pass
     another seed.
   - *Verified:* the third run completed with `no_answer` 0 in every cell.

**Supporting details.**

- Local before/after, 20 users, 120 s:

  | | Before | After |
  |---|---|---|
  | Fast-mode p95 | 16,000 ms | 7,600 ms |
  | Fast-mode median | 2,400 ms | 3,400 ms |
  | Landscape p99 | 2,900 ms | 1,600 ms |
  | Scores accepted | 145 | 163 |
  | Memory | 2,584 MB | 1,989 MB |

  No 503 in either local run.
- Live, one user (p95): reads 0.25–0.33 s. Fast mode:

  | | 300 aa | 1,000 aa |
  |---|---|---|
  | 35M | 0.64 s | 2.48 s |
  | 150M | 1.91 s | 7.22 s |

  The precise job at 400 aa took 203 s. Every long computation was 19–26% slower than on v0.2.0
  with no competing traffic. Not explained: the pass code is unchanged, and a slower host is
  likely but unverified. 203 s exceeds the ~3 min precise target; a roadmap item asks for a
  second measurement before the limit changes.
- Live memory: 2,592 MiB at most (resident plus score cache), 63% of the 4 GiB limit.
- CORS now lists `https://mutascope.abdullahhanif.me` and `https://mutascope-web.vercel.app`; it
  is live from the next deploy.
- Settings added: `inference_concurrency` (1), `inference_wait_seconds` (10). No new
  dependency.
- Loose ends:
  - the cause of slow reads (roadmap);
  - the precise limit (roadmap);
  - Sentry (no project; roadmap);
  - GHCR visibility and the trusted-proxy log check, still open from ADR 0035.

---

## Entry 38 — One ONNX thread on Cloud Run: reads fixed (Phase 5 Task 0, 2026-10-18)

**Plain-language summary.** Entry 37 ended with a miss. Model runs now queue up one at a time,
but simple page reads on the live API were still slow under load (the slowest 1% at 5 users took
8.5 s). The numbers pointed at the server's main loop being starved of CPU: one model run with
two threads kept both of Cloud Run's CPUs busy, so even a one-line "too many requests" answer
could take 28 s. This task gave the model one thread instead of two, leaving the other CPU to
the loop. It was a single line of deploy config (PR #50), measured live with the same script.
Reads at 5 users now take 140 ms at the slowest 1%, about 60 times faster, and every other answer
is prompt again. The price: a long precise job takes 44% longer, and under heavy load the server
finishes about half as many scores, telling the rest "busy, retry in 10 s". The author chose to
keep it. Decision record: ADR 0036, section 7.

**The 3 most important design choices.**

1. **Change one thing, measure with the same method.** Only `MUTASCOPE_API_ONNX_THREADS`
   changed. The image (v0.4.0), the pool settings, the Locust mix, the user counts and the
   durations stayed the same, and only the seed differed (a warm instance caches earlier
   sequences). So the 2-thread and 1-thread tables in ADR 0036 compare like with like.
2. **A config change, not code.** The thread count was already a setting, so the experiment was
   a reviewed one-line PR and a redeploy of the same image. Reverting would be the same in
   reverse.
3. **Weigh both sides, and let the author decide.** The write-up gave the gain (read p99 at 5
   users 8,500 → 140 ms; 429s at 20 users 28 s → 330 ms) next to the costs (precise job 203 →
   293 s; at 20 users 19 scores answered and 21 refused, against 39 and 1). It recommended
   keeping one thread and measuring a lower precise limit; the author agreed.

**Problems encountered and how they were solved.**

1. **The PR watcher reported a merge that had not happened.** *Found:* the watcher stopped while
   PR #50 was still open. Its loop waited for the state to differ from `OPEN`, and a failed `gh`
   call returns an empty string, which also differs. *Fix:* the loop now stops only on `MERGED`
   or `CLOSED`. *Verified:* it then waited until the real merge (`e4e69ed`).
2. **One request failed its TLS handshake** during the 35M 300-residue cell (`URLError
   (TimeoutError('_ssl.c:999: The handshake operation timed out'))`). *Handled:* entry 37's
   change counted it as `no_answer` (1) and the run went on. The script waited 120 s for it, so
   the cell took about 2 minutes longer. It is a network event, not the service.
3. **One thread barely slowed single passes, but slowed batched ones.** *Found:* comparing the
   cells:

   | | 2 threads | 1 thread |
   |---|---|---|
   | 35M, 1,000 aa, p95 | 2.48 s | 2.35 s |
   | 150M, 300 aa, p95 | 1.91 s | 1.75 s |
   | 150M, 1,000 aa, p95 | 7.22 s | 8.10 s |
   | precise job, 400 aa | 203 s | 293 s |

   *Not solved here:* 293 s exceeds the ~3 min precise target. The precise limit stays at 400
   until a lower value is measured (roadmap), as ADR 0031's rule asks.

**Supporting details.**

- Live, one ONNX thread, v0.4.0 (`docs/live-measure-0036-t1-summary.json`; ms, p99):

  | | Protein | Landscape | Benchmarks | Slowest score |
  |---|---|---|---|---|
  | 5 users | 140 | 770 | 150 | — |
  | 20 users | 560 | 2,000 | 520 | 14,000 (was 34,000) |

  - Memory: at most 2,614 MiB (64% of 4 GiB).
  - Inference running at most 1, waiting at most 14 (10 s scrapes).
- `/metrics`: 401 without the token, 200 with it. CORS: both website origins allowed, another
  origin gets no header. `/health`: 0.4.0.
- Settings deployed: `MUTASCOPE_API_ONNX_THREADS=1` (`conf/deploy/cloudrun.yaml`). The image's
  default stays 2.
- Loose ends:
  - the precise limit (roadmap);
  - profiling the loop and moving gzip / ETag off it, now optional;
  - GHCR visibility, the trusted-proxy log check and Sentry, still open.

---

## Entry 39 — Website foundation (Phase 5 Task 1, 2026-10-18)

**Plain-language summary.** The website now exists as a working shell. It has a header with
Explorer, Benchmarks, How it works and About, a footer with the GitHub and Hugging Face links and
the not-for-clinical-use note, light and dark mode, and placeholder pages. The important part is
underneath. When the site is built, a script downloads the published data bundle (217 assay files
plus manifest and benchmarks, 29 MB) from the exact Hub commit the API uses. It checks every file
against the bundle's checksum list and places the files on the site's own CDN only if every check
passes. The browser checks every file again when it loads it, with validators generated
automatically from the bundle's schema, so the site can never quietly show data it misreads. The
site needs neither the Hub nor the API to show precomputed results, so it works while the API
sleeps. The visual rules (colours, type, spacing, the blue–white–red effect scale and its legend)
are written down in `web/DESIGN.md`. CI gets a sixth job for the website. Decision record:
ADR 0037.

**The 3 most important design choices.**

1. **Copy the bundle into the site at build time, all-or-nothing.** The pin (`web/bundle.json`)
   is the same commit as `export.publish.revision`, and a pytest fails if they differ. The
   download checks all 223 files against `SHA256SUMS`, checks the manifest's 217 protein digests
   against the same list, and validates every file with Zod. It writes to a staging directory and
   swaps it in only when all of that passes. A tampered file therefore fails the build and leaves
   the previous data in place, which a unit test checks. The alternatives were weaker: fetching
   from the Hub in the browser ties every page view to a third party, and committing the data puts
   29 MB of generated files in git.
2. **Generate the validators, and fail on what the generator does not understand.** A small
   generator turns the Pydantic-written JSON Schemas into Zod code. It handles exactly the
   keywords those schemas use and throws on any other, so the validator can never be looser than
   the schema. Off-the-shelf converters skip unsupported keywords silently. The bundle also ships
   its own copy of the schemas, and the download requires them to equal the repo copy the
   validators came from. In the browser, each file passes a schema-version check, then Zod, then
   (for protein files) the manifest's sha256 of the gzip bytes. Validating all 217 protein files
   takes 0.6 s, so checking everything costs little.
3. **A fixed effect scale, separate from the interface colour.** Effects use ColorBrewer RdBu
   arms (blue = harmful, red = tolerated) around a pure-white 0, interpolated in OKLab, symmetric,
   and the same in both themes. Missing values are grey, never white. The interface uses one teal
   accent away from both poles. Tests keep the CSS tokens and the TypeScript stops identical,
   check that lightness falls steadily on both arms, and check that the arms match within OKLab
   ΔL 0.08. Every text and accent token pair passes WCAG AA, computed rather than eyeballed (the
   lowest is 5.5:1).

**Problems encountered and how they were solved.**

1. **The shadcn CLI installed an unrelated npm package.** *Found:* after
   `shadcn add button`, `package.json` had a new dependency `"cn": "^0.5.0"` (a caret range in a
   repo of exact pins), and `button.tsx` imported `cn` from it instead of from our
   `@/lib/utils`. *Fix:* `pnpm remove cn` and the import pointed at `@/lib/utils`. DESIGN.md now
   says to check the diff after any `shadcn add`. *Verified:* `grep -c "cn@" pnpm-lock.yaml` gives
   0, and lint, typecheck and build pass.
2. **The first real download failed on the bundle's own schema files.** *Found:* the first
   `pnpm data:fetch` stopped at file 223 with "schema/benchmarks.schema.json has schema version
   undefined". The script treated every `.json` as a bundle file, and the bundle also ships its
   JSON Schemas under `schema/`. *Fix:* files get a role from their path (manifest, benchmarks,
   protein, schema, other). Schema files must now equal `docs/schema/bundle/`, which turns them
   into a check that the validators were generated from this bundle's schema. *Verified:* 223
   files, 217 proteins, 14 s; `data:verify` re-checks 223 files in 0.2 s; a unit test feeds a
   changed schema file and expects the refusal.
3. **The logo's colour cells did not render.** *Found:* in the first screenshots the logo was an
   empty outline. Tailwind 4 emits a theme variable only when a utility uses it, and the logo reads
   `var(--color-effect-*)` from inline styles, so the variables did not exist. *Fix:* the effect
   tokens moved to `@theme static`. *Verified:* the logo shows the five cells in the light, dark
   and phone screenshots.
4. **"217 proteins" was wrong.** *Found:* while writing the hero text, checked against the
   manifest: 217 entries, but 186 distinct UniProt IDs. The bundle's "protein" files are per
   assay (ADR 0028). *Fix:* the hero says "every ProteinGym assay", and the Explorer status says
   "217 assays". *Verified:* the end-to-end test expects "217 assays".
5. **pnpm 12 refused to install.** *Found:* `ERR_PNPM_IGNORED_BUILDS` for `unrs-resolver`, a
   dependency of eslint-config-next. pnpm 12 fails when a dependency's build script has neither
   been allowed nor denied. *Fix:* `pnpm-workspace.yaml` denies it (`allowBuilds: unrs-resolver:
   false`). It ships prebuilt binaries as optional dependencies, so the script is only a
   fallback. *Verified:* install, ESLint (which loads the resolver) and CI's
   `--frozen-lockfile` install all work.
6. **The phone header scrolled sideways.** *Found:* at 375 px the nav was cut off at
   "Benchmar…". *Fix:* below the `sm` breakpoint the nav takes its own row. *Verified:* phone
   screenshot, `document.documentElement.scrollWidth` = 375.

7. **The first Vercel builds failed, and my first diagnosis was wrong.** *Found:* the preview
   build of `660d0fc` stopped after 1 s with "Found invalid or discontinued Node.js Version:
   ">=26"": Vercel builds with Node 24 at most. `bbf80b3` set `engines` to `24.x`, and that build
   failed too. Redeploying `660d0fc` from the dashboard reused the old `engines`. Without the
   log, I reproduced `bbf80b3` from a clean clone under Node 24.22.0 (it built) and guessed pnpm:
   pnpm 12 labels its lockfile `lockfileVersion: '9.0'`. `c875c54` added a `web/vercel.json`
   with a Corepack install command and `"framework": "nextjs"`, and Vercel passed. *Actual
   cause:* once logged in to the Vercel CLI, the `bbf80b3` log showed that install (pnpm 12.10.1,
   taken from `packageManager`), the bundle download (223 files) and `next build` had all
   succeeded. The build then failed with "No Output Directory named "dist" found": the project's
   Framework Preset was "Other". The `framework` line was the fix; the Corepack line was not
   needed. *Fix:* `web/vercel.json` is now only `"framework": "nextjs"` (Node 25+ no longer ships
   Corepack, so an unneeded dependency on it was removed). *Verified:* the Vercel log of
   `bbf80b3` (`vercel inspect --logs`), the project settings (`vercel project inspect`: preset
   "Other", Node 24.x, root `web`), and the next preview build.

8. **A failed download could hide its own error.** *Found:* after the move to Node 24, CI's
   `web` job failed 2 of the 14 download tests: the tamper test expected "benchmarks.json: sha256
   differs from SHA256SUMS" but got "ENOTEMPTY: directory not empty, rmdir
   '.../data.staging'". *Cause:* the downloads ran 8 at a time under `Promise.all`, which
   rejects at the first failure while the other downloads are still writing into the staging
   directory. The cleanup's `rm` raced those writes, and its error replaced the real one.
   Locally the race had not shown; the CI runner's timing exposed it. *Fix:* `mapLimit` starts no
   new download after the first failure, returns only when every started one has settled, then
   throws the first error. A failing cleanup can no longer replace that error. *Verified:* a
   regression test (one bad file served at once, the others after 50 ms) failed before the fix
   and passes after it, 5 runs out of 5, and all 39 unit tests pass on Node 24.22.0 and Node 26.

**Supporting details.**

- Versions (exact, from the npm registry on 2026-10-18): Next.js 16.4.2, React 19.3.1,
  Tailwind 4.3.3, Zod 4.6.7, Vitest 5.0.5, Playwright 1.64.1, Prettier 3.9.10, shadcn CLI 4.22.1.
  Held back on purpose: TypeScript 5.9.3 (7.0.2 is out, but openapi-typescript 7.13 needs `^5`
  and typescript-eslint `<6.1`) and ESLint 9.39.5 (10.13 is out, but eslint-config-next's
  `react`, `import` and `jsx-a11y` plugins declare `^9`). Dependabot now covers `/web` and does
  not propose those two majors.
- Tests: 39 Vitest tests (loader, both scripts, scale, legend) and 5 Playwright tests (navigation,
  footer on every page, dark mode across a reload, Explorer data with every request to another
  origin blocked, protein files served as gzip bytes). Mutation check: with the digest
  comparison disabled in the script or in the loader, exactly the two tamper tests fail.
- `make web-check` and `make web-e2e` were added. `make lint test` passes.
- Gzip serving checked on production (2026-10-19), after PR #53 merged; previews are behind
  Vercel's deployment protection, so this waited for the public site:
  - `curl` of `https://mutascope.abdullahhanif.me/data/proteins/A0A140D2T1_ZIKV_Sourisseau_2019.json.gz`:
    HTTP 200, `content-type: application/gzip`, no `Content-Encoding`,
    `cache-control: public, max-age=86400, must-revalidate`; 1,153,890 bytes and a sha256
    equal to the manifest's;
  - in the browser, the Explorer reported manifest.json valid (217 assays), benchmarks.json valid
    (240 rows) and the sample protein valid (F7YBW8_MESOW_Aakre_2015, 93 residues).
- Redeploying a deployment rebuilds its own commit: a redeploy of `b40c42a` (before PR #53)
  fails with `NEXT_NO_VERSION` because that commit has no website. Production runs a later
  commit and is unaffected.
- The `main` ruleset ("protect main") requires `web (lint, typecheck, unit tests, build)`
  alongside the other five checks (read through the GitHub API, 2026-10-19).
- Loose end: the Explorer itself is next.

---

## Entry 40 — Explorer and mutation heatmap (Phase 5 Task 2, 2026-10-19)

**Plain-language summary.** The website can now show any of the 217 assays. `/explore` lists
them, with a search box that matches the protein name, UniProt id, function type and the default
model's label (typing "zero-shot" leaves the 19 assays without a supervised model). Each protein
has its own page with a heatmap: one column per residue, one row per amino acid, one coloured
cell per substitution. Up to 68,460 cells (Zika env, 3,423 residues). The reader can switch the
model (the default, ESM-2 650M, SaProt where there is a structure), show measured values instead
of predictions, and grey out unmeasured substitutions. Every number on the page carries its
label: "Supervised (n = …)", "Zero-shot" or "Measured". Hovering a cell, or moving to it with the
arrow keys, shows the mutation, the prediction, the 80% and 90% intervals, the measured value
and out-of-fold prediction, and what was measured at that residue. A bar above the grid shows
each residue's mean effect. A side panel lists the ten most harmful and most tolerated
substitutions and downloads the current view as CSV. A table shows the same numbers for screen
readers. The five assays whose ESM-2 benchmark used only part of the protein say so. A
1,000-residue protein is drawn in about 30 ms after its data arrives, against a 300 ms budget.
Decision record: ADR 0038.

**The 3 most important design choices.**

1. **A neutral point per quantity, not 0 for everything.** Zero-shot scores are log-ratios
   against the wild type, so 0 is the wild type and stays white. ProteinGym's DMS scores have no
   common zero. In the bundle, 55 assays have fewer than 20% of their measured scores below 0,
   and 56 have more than 80% below 0. Centred on 0, half the landscapes would be almost all red
   or almost all blue. DMS-unit views (measured, and supervised predictions, which share the
   units) are therefore centred on the assay's median measured score. Measured and predicted
   views share one scale, and the legend says "Assay median", not "Neutral". The half-width is
   the 99th percentile of the deviations, with the clipped ends marked "≤" / "≥", because in 44
   assays the largest deviation is at least twice that percentile (11.1 times in Zika env).
2. **One canvas the size of the window, drawn from a one-pixel-per-cell image.** Each view is
   rasterised once (L × 20 pixels, coloured through a 257-step lookup table that a test keeps
   equal to `effectColor`). Every scroll or zoom frame then scales only the visible slice onto
   a canvas the width of the window, and draws the overlays for visible columns only. SVG would
   be 68,000 nodes. A canvas as wide as the protein would be 82,152 px at the widest zoom, past
   browser limits. Drawing costs the same at 37 residues as at 3,423.
3. **Everything on the canvas is also text, with the label attached.** The focused cell is
   described in a live region (mutation, label, model, value, intervals, measured value,
   out-of-fold prediction, residue status, imputed structure). The tooltip, the paged table and
   every CSV row carry the same facts and the view's label. The grid maths is pure and lives in
   `src/lib/explorer/` (unit-tested). `mutationCode` is the only place the website writes a
   1-indexed residue, mirroring `parse_mutation` in the Python package.

**Problems encountered and how they were solved.**

1. **Centring on 0 would have mislabelled half the assays.** *Found:* before writing the colour
   code, I checked the share of measured scores below 0 in every protein file. All 9,576 of
   Zika env's measured scores are positive (0.002 to 8.44, median 0.04), and all 224 of SCN5A's
   are negative (−205.5 to −9.5). *Fix:* DMS-unit views are centred on the assay median
   (design choice 1; ADR 0038 amends ADR 0037's "symmetric around 0"). *Verified:* unit tests
   (the centre is the median; measured and supervised share centre and half-width; zero-shot
   stays at 0), and screenshots of PIN1, MSH2, Zika env and p53 in both themes.
2. **My first count of one-sided assays was wrong.** *Found:* while writing the ADR. The quick
   script rounded each share with `toFixed(2)` before testing "> 0.8", so five assays just
   above 80% (80.06% to 80.39% below 0: ARGR, DLG4, DN7A, OBSCN, SBI) printed as "0.80" and were
   left out. It reported 51 assays above 80% negative. *Fix:* recounted on the raw fractions:
   56, so 111 of 217 one-sided, not 106. *Verified:* the five assays listed by a check that
   compares both tests. The ADR, DESIGN.md and this entry quote the recount, and I corrected
   the number in the session where I had first given it.
3. **The table said "Supervised (n = 16,749)" over measured values.** *Found:* in a screenshot
   of MSH2 in measured mode. The table caption read the selected model's label, but the numbers
   were the assay's own. *Fix:* a view now carries its own `label` ("Measured" for measured
   values), and the badge, table caption, heatmap name and CSV all read that one field.
   *Verified:* a unit test checks the three labels (supervised, zero-shot, measured), and the
   Playwright test for PIN1 checks the badge after switching to Measured.

**Supporting details.**

- Render time, from the protein file loaded and validated to the first frame drawn (decode,
  view, raster, draw). Production build on an M4 Pro, 20 cold loads per protein, median / max:
  PIN1 (39 residues) 15.4 / 16.5 ms; MSH2 (934) 30.6 / 31.3 ms; CAR11 `Meitlis_2020_gof`
  (1,154) 26.4 / 45.1 ms; Zika env (3,423) 56.3 / 57.2 ms. With the CPU throttled 4× in
  Chrome: 60.7, 122.3, 100.5 and 210.1 ms (medians). A view change takes 9.1 ms (MSH2), 11.5 ms
  (CAR11) and 32.9 ms (Zika env). The page exposes the time as `data-render-ms` and as a
  `performance.measure`, and a Playwright test fails at 300 ms for MSH2 and CAR11, the two
  proteins nearest 1,000 residues.
- Routes: `/explore` and `/explore/[assayId]`, all 217 prerendered from the downloaded manifest
  (through the same validator); any other id is a 404. `/explorer` redirects to `/explore`. The
  bundle-status box moved to About.
- Tests: Vitest 39 → 70 (data mapping, views, masks, extremes, cell details, CSV, raster, ruler,
  zoom, search, lookup table, robust half-width, legend around a centre). Playwright 5 → 15:
  three proteins with their labels and model options (PIN1, small and supervised; Zika env, the
  largest, with its MSA note; p53 `Null_Nutlin`, zero-shot only, no structure, so no SaProt),
  each with every request to another origin blocked. Also the picker search, keyboard
  navigation and descriptions, the top lists and CSV (686 rows = the assay's measured
  variants), the table, the render budget, and the redirect and 404. The suite passed 3 runs out
  of 3.
- No new dependencies: the canvas, the zoom and the table are plain React and DOM.
- `make lint test`, `make web-check` and `make web-e2e` pass.
- Loose ends: link the heatmap's hovered column to the 3D structure (next task). The view state
  is not in the URL yet, so a shared link opens the default view.

---

## Entry 41 — Structure view linked to the heatmap (Phase 5 Task 3, 2026-10-19)

**Plain-language summary.** Protein pages now show the protein's 3D structure next to its
mutation heatmap. The website bundle had per-residue structure numbers but no coordinates, so a
new bundle, `bundle-v1.1.0` (format 1.1.0), adds one small file per ProteinGym AF2 structure:
185 files for the 213 assays that have one, 2.6 MB in all. Each keeps only what a cartoon needs:
the backbone atoms, the residue numbers and AF2's confidence (pLDDT). While building them, every
structure residue of every assay is checked against that assay's protein sequence, and the build
stops on any mismatch. In the browser, the structure loads only after the heatmap is drawn, in
3Dmol.js. Residues are coloured like the heatmap's mean bar (same model, same scale, same legend)
or by pLDDT, and low-confidence regions are drawn as a thin trace. Pointing at a heatmap column
marks that residue in 3D. Pointing at a residue in 3D outlines its column. Clicking a residue in
3D scrolls the heatmap to it and selects it. The four assays without a structure say why, and
the page says the 3D view is not keyboard-accessible: the heatmap and table are. The API serves
no structures and never downloads or reads these files. Decision record: ADR 0039.

**The 3 most important design choices.**

1. **One backbone file per PDB, checked per assay.** Assays of one protein share a structure
   (213 assays, 185 files), so each file is written once, and its sha256 sits in every protein
   file that uses it. The residue check runs per assay, because the mapping (`pdb_range`) and the
   target are per assay: structure residue `i` must equal `target_seq[start + i]`, and the
   structure must have exactly `end − start` residues. The pLDDT must equal the published
   structure features, which a different parser (biotite) read from the same PDBs in Phase 1. The
   PDBs are read from the zip after its sha256 matches `data/manifest.yaml`, not from the
   unverified extracted folder. Coordinates and pLDDT keep the PDB's own decimals, so they are
   exact.
2. **Structure files are not protein bytes.** `protein_bytes` is what the browser must download
   before it can draw the heatmap. A structure comes later, separately. So structure files have
   their own budget, `structure_bytes` (500,000; the largest, BRCA2 with 2,832 residues, is
   114,664 bytes), and count towards `total_bytes` (31,837,522 of 40,000,000), which is what the
   Hub folder and a static deployment hold. The API lists `structures/` as ignored: it is not in
   the Hub snapshot, its checksum lines are skipped, and no structure file is opened.
3. **The two views meet only through target positions, and expose them.** The page holds one
   linked residue (0-indexed target position) and which view set it, so a pointer leaving one
   view clears only its own highlight. PDB residue numbers become positions in one pair of
   functions. Each view writes its state as data attributes (`data-highlighted-position`,
   `data-linked-position`, `data-focus-position`, ...). The Playwright tests find a residue in
   3D by moving the pointer until the viewer reports one, then assert on those attributes,
   never on pixels or WebGL output.

**Problems encountered and how they were solved.**

1. **The backbone parser could merge two residues that share a number.** *Found:* by a unit test
   that renumbers the last residue of a synthetic PDB to 1. The first parser grouped atoms by
   (residue number, insertion code), so a number used again later attached that residue's atoms
   to the earlier one. Here it failed only because the names differed ("residue 1 is both MET and
   LEU"). With equal residue names it would have overwritten coordinates silently, or failed with
   a misleading "appears twice". *Fix:* a residue number that comes back after other residues is
   its own error ("appears again after others"), separate from "not strictly increasing".
   *Verified:* two parametrised cases (a number reused, numbers decreasing); the 185 PDBs the assays use all
   parse, and all 213 assays pass the residue check.
2. **The 3D hover tests found no residue.** *Found:* the first Playwright run. Two of the three new
   tests failed with "no residue found under the pointer in the 3D view", although the same scan
   had worked in a 1280 × 1400 probe. Playwright's default viewport is 1280 × 720, so the viewer
   was below the fold and pointer moves outside the viewport reached nothing. *Fix:* scroll the
   viewer's canvas into view before scanning. *Verified:* 15 of 15 runs with `--repeat-each 5`;
   the full suite 18 of 18.
3. **Updating a ref during render.** *Found:* `pnpm lint` (react-hooks/refs). 3Dmol.js keeps the
   hover and click handlers it is given at creation, so they call the latest props through a ref,
   which I had assigned in the render body. *Fix:* assign it in an effect. *Verified:* lint
   clean; the linking tests pass.

**Supporting details.**

- Bundle `bundle-v1.1.0` built at `3889105` (code version stamp without `-dirty`): 217 protein
  files, 29,249,984 bytes (largest Zika env 1,154,127); 185 structure files, 2,587,538 bytes;
  409 files in `SHA256SUMS`. Fidelity unchanged from 1.0.0 (largest |encoded − source| Spearman
  4.30e-4 ESM-2, 6.56e-4 SaProt, 4.04e-4 ridge; coverage exact). `benchmarks.json` also gains
  the 60 int8 comparison rows (300, was 240), as build log 29 expected. Partial structures:
  Zika env 291–794 of 3,423, BRCA2 1–2,832 of 3,418, POLG_HCVJF 1,982–2,225 of 3,033.
- Loading (production build, local): heatmap drawn about 150 ms after navigation; only then the
  viewer chunk (7.0 kB) and 3Dmol.js (573 kB, 161 kB gzip) are requested, and the structure file
  at about 460 ms. The 300 ms heatmap budget tests pass.
- Layout at 1440 × 900: heatmap grid y 453–807, viewer from 982. The colour switch and legend sit
  beside the viewer, not above it, so both views fit on one screen while linking.
- Tests: Python `tests/export/test_structure.py` (parser, residue and pLDDT checks, archive
  checksum, exact round trip) and six bundle tests (structure written once and referenced, a
  residue mismatch, a shifted `pdb_range`, a missing PDB, pLDDT disagreement, budgets); the real
  bundle test now checks all 213 structures (sequence = target slice) and the 3 partial ones. API:
  structure files never required or read; snapshot `ignore_patterns`. All API tests also pass
  against the local 1.1.0 build (golden landscapes unchanged). Web: Vitest 89 (structure
  mapping, PDB text, colours equal to the summary bar's, coverage strip, pLDDT tokens,
  `loadStructure`, fetch checks of structure files); Playwright 18 (PIN1 both directions and
  click; Zika env click scrolls a 3,423-residue heatmap; p53 no structure).
- New dependency: `3dmol` 2.5.5 (BSD-3-Clause). Licence line for the AF2 structures in
  `docs/DATA_CARD.md` (ProteinGym, MIT; AlphaFold2, Jumper et al. 2021, parameters CC BY 4.0).
- Published by the author with `mutascope export push` on 2026-10-19: the Hub split the upload
  into two commits (`df77169`, then `1bfc294`), and tag `bundle-v1.1.0` resolves to `1bfc294`.
  Checked through the Hub API without a token: public, its 410 bundle files match `build/bundle`
  by sha256, and the 14 files outside `bundle/` are identical to `3ae7672`. Pinned in
  `conf/export/default.yaml`, `api/app/settings.py`, `.env.example` and `web/bundle.json`. The
  API started on it (`/ready`: schema 1.1.0, revision `1bfc294`, 217 proteins; 224 files checked,
  no `structures/` in its snapshot). `make web-check` downloaded and verified 409 files, and
  Playwright passed 18 of 18 on them.

---

## Entry 42 — Live scoring page (Phase 5 Task 4, 2026-10-19)

**Plain-language summary.** The website gets a page, `/score`, where anyone can paste a protein
sequence and get a predicted effect for every single substitution, computed live by the API.
Fast mode is one request and returns the whole landscape in seconds. Precise mode starts a job
on the server and fills the heatmap batch by batch as results stream back, with a progress bar
and a cancel button. The API sleeps when nobody uses it, so the page pings it as soon as it
loads and says "Waking up the server…" for the ~20 s a cold start takes, rather than showing
an error. Mistakes in the sequence are caught in the browser with the API's own rules and shown
under the input, naming the first bad residue. Busy or rate-limited answers say when to try again.
Every result is labelled "Zero-shot, ESM-2 35M" and drawn with the Explorer's heatmap. The
sequence is only ever sent in a request body. Decision record: ADR 0040.

**The 3 most important design choices.**

1. **Job streams over `fetch`, not `EventSource`.** On Cloud Run a precise job advances only
   while its stream is open, so a dropped connection must be reopened. `EventSource` cannot send
   `Last-Event-ID` on a first connection, hides the status code and `Retry-After` of a refused
   stream, and retries on its own schedule. `followJob` reads the stream with `fetch` and a small
   SSE parser, reconnects after 1, 2, 4, 8 s (or `Retry-After`) sending the last event id, and
   relies on the server's replayable event log (ADR 0031) so no row is lost or applied twice. It
   stops on a 404 (a restarted instance has forgotten the job) or after 8 failed reconnects.
2. **Nothing about the server is assumed.** The precise limit comes from `/ready`
   (`precise_max_length`: 400 live, 600 in the image, under review in the ROADMAP), and the
   label comes from the response's `model`, not from what the page asked for. A cold start is
   detected, not configured: `/health` unanswered after 1.5 s means "waking".
3. **A live result is an Explorer landscape with `source: "live"`.** The same canvas heatmap,
   top-10 lists, CSV and table draw it, and the one flag removes every mention of measurements
   (tooltip, screen-reader text, CSV column). Unscored rows are NaN, so a running job shows grey
   columns, never neutral white.

**Problems encountered and how they were solved.**

1. **A Playwright reconnect test timed out.** *Found:* the first run of the suite, 17 of 18 passed.
   The test refused two reconnects before letting one through. The backoffs were 1, 2 and 4 s,
   7 s in all, which is longer than Playwright's 5 s expect timeout. The page behaved correctly.
   *Fix:* refuse one reconnect (backoffs 1 + 2 s), assert "attempt 2" and that exactly two
   reconnects were made. *Verified:* 39 of 39 with `--repeat-each 3`; full suite 31 passed, 1
   skipped (the live test).
2. **The legend said "Not measured" for a live sequence.** *Found:* reviewing screenshots of a
   half-finished precise job. Grey columns there are rows not scored yet. A pasted sequence has no
   measurements, so the label was wrong. *Fix:* `EffectLegend` takes `missingLabel` (default
   unchanged). The live view shows "Not scored yet", and only while a job is incomplete.
   *Verified:* a legend unit test; the Explorer's legends are unchanged.

**Supporting details.**

- Live service before the change (v0.6.0): `/health` answered in 20.4 s from cold; `/ready`
  reported `precise_max_length` 400 and `score_default_model` `esm2_35m`. A preflight from
  `https://mutascope.abdullahhanif.me` for the stream with `Last-Event-ID` returned 200 with that
  origin. `conf/deploy/cloudrun.yaml` already listed both production origins, so it is unchanged
  and nothing is deployed by this task.
- `POST /v1/score` without `mutations` returns the full `(L, 20)` landscape (`scores`), so fast
  mode needed no API change. Precise jobs send `model: "esm2_35m"` explicitly: the generated
  `JobRequest` type requires it.
- Live smoke test (`MUTASCOPE_LIVE_API=1`, warm instance): ubiquitin, 128 residues, fast mode,
  full landscape and label "Zero-shot, ESM-2 35M", 1.3 s. The API's CORS refuses the local test
  server's origin, so the test relays API calls through Playwright and adds only the CORS header.
- Examples are target sequences copied from the pinned bundle: ubiquitin
  (`RL40A_YEAST_Roscoe_2013`, 128), GFP (`GFP_AEQVI_Sarkisyan_2016`, 238) and PTEN
  (`PTEN_HUMAN_Mighell_2018`, 403, over the live precise limit, so it shows the limit at work).
  A scored example links to its assay in the Explorer.
- Tests: Vitest 113 (24 new in `src/lib/live/live.test.ts`); Playwright 31 + 1 skipped (13 new
  mocked `/score` tests; `/score` added to the navigation and footer tests, which block the API).
- No new dependencies.

---

## Entry 43 — Content pages and SEO (Phase 5 Task 5, 2026-10-19)

**Plain-language summary.** The four placeholder pages are now real. The Landing page says what
MutaScope does in one sentence, shows GFP's structure slowly turning (it loads after the page
appears and stays still for readers who ask for reduced motion), offers three example proteins,
points to live scoring, and gives three headline numbers: how much SaProt's structure tokens add
over ESM-2, how much the supervised model gains at positions it never saw, and how often its 90%
intervals actually contain the measurement. The Benchmarks page draws every comparison as a
difference with its 95% interval: zero-shot models against each other, the supervised ladder per
split, the gain by assay size and per function type (with the Binding caveat), and the coverage
of the intervals. How it works explains the pipeline, the decisions, the deployment rule (ridge on
198 assays, zero-shot on 19), the limitations and the two upstream ProteinGym errors, with links
to the ADRs, model card, dataset and API docs. About has the author's links and a sentence on AI
use. Every page has metadata, a canonical URL and an Open Graph image; the site has a sitemap and
robots.txt. Decision record: ADR 0041.

**The 3 most important design choices.**

1. **No number is typed into a page.** `benchmarks.json` is read and validated at build time, and
   each number is looked up by its keys (model, split, metric, level, group). A lookup that finds
   no row, or two, throws, so a missing number fails the build instead of showing a stale or
   invented one. The Playwright tests read their expected values from the served file too, so a
   new bundle cannot pass with old numbers on the page.
2. **The headlines are the honest ones, not the biggest.** The supervised headline is the
   contiguous split (+0.092), not random (+0.263): the website predicts at unmeasured positions,
   and that is the job contiguous and modulo test (ADR 0026). The coverage headline is test-fold
   coverage with split-matched calibration (90.8% at 90%), not the deployed landscapes' 0.900 on
   measured variants, which holds by construction and proves nothing (ADR 0026, weak point 11).
3. **Charts show differences, never two bars.** Every chart is a paired difference with its
   interval and a line at 0, in ink only (DESIGN.md keeps the accent and the effect colours out
   of charts). Each model's own score and CI is in a table that says it is not a comparison. A
   hollow marker means the interval includes 0, and the row says so in words. Each chart states
   its assay count.

**Problems encountered and how they were solved.**

1. **The first build failed on an Open Graph image.** *Found:* `next build` stopped prerendering
   `/explore/opengraph-image` with "Expected <div> to have explicit display: flex … if it has
   more than one child node". The footer line `{SITE.url…} · Not for clinical use` is two text
   nodes to the image renderer (Satori), which needs flex for more than one child. *Fix:* one
   template string in a `display: flex` div. *Verified:* the build prerenders all six images,
   and a Playwright test fetches each page's `og:image` and checks it is `image/png`.
2. **ESLint refused the reduced-motion state.** *Found:* `pnpm lint` reported
   `react-hooks/set-state-in-effect` on `setReduced(query.matches)` inside an effect. *Fix:* read
   the media query with `useSyncExternalStore` (subscribe to its `change` event; the server
   snapshot is "reduced", so nothing moves before the setting is known). *Verified:* a Playwright
   test switches `reducedMotion` from reduce to no-preference and back and sees `data-spinning`
   follow each time.

**Supporting details.**

- Headlines from `benchmarks.json` (bundle-v1.1.0): SaProt 650M − ESM-2 650M +0.046 [+0.028,
  +0.062], 213 assays; ridge `seq_struct` − zero-shot SaProt, contiguous +0.092 [+0.065,
  +0.115], modulo +0.135, 213 assays; 90% interval coverage, contiguous, split-matched: mean
  0.908, 0 of 213 assays more than 10 points below (random calibration: 0.806, 86 below).
- The Binding caveat is documented in ADR 0026 (weak point 7) and written into the bundle by ADR
  0028 section 8; the page shows the file's own text and cites both.
- About: the CV link came without a URL and is not shown (ROADMAP item). The AI statement names
  Claude Code and CLAUDE.md, which are in the repository; the author should confirm the wording.
- Found while checking phone widths: the Task 1 header's navigation is 13 px wider than a 390 px
  screen (scrollWidth 403). Not changed here; added to the ROADMAP.
- Tests: Vitest 131 (18 new: selectors, axis, ADR links); Playwright 40 + 1 skipped (9 new in
  `e2e/content.spec.ts`).
- No new dependencies (`next/og` ships with Next).

**Follow-up (2026-10-19).** Two changes to the About page after review (commit `5f65698`): the CV
link was dropped by choice, not for lack of a URL (ADR 0041, decision 10), and the AI statement
was replaced with the author's own text.

**Follow-up (2026-10-19): About text replaced.** The About page now shows the author's own text:
three paragraphs with bold lead-ins ("Hi, I'm Abdullah Hanif.", "Why I built MutaScope.",
"Let's talk.") and a links line (email as a `mailto:` link, abdullahhanif.me, GitHub, LinkedIn).
It replaces the link to the author's site and the AI statement. The AI statement was removed by
the author's choice: `ABOUT.aiStatement` and its test are gone (ADR 0041, decision 10). The
Playwright test checks the three paragraphs, the bold lead-ins and the four links in order.

## Entry 44 — Website quality: Lighthouse, axe, phones, error pages (Phase 5 Task 6, 2026-10-19)

**Plain-language summary.** The website is now checked for speed, accessibility and phone
layouts on every PR. Lighthouse loads the Landing page and the GFP protein page three times
each, as a slow phone and as a desktop, and the median must score at least 90 for performance
and 95 for accessibility. axe checks all eight pages in light and dark for accessibility
problems, and a test checks that no page scrolls sideways on a 375 px phone. The baseline missed
both budgets on the protein page (mobile 0.81 / 0.92). Six fixes made the site pass without
touching the budgets:

- links in text are underlined;
- the side panel's headings are in order;
- its buttons are big enough to tap;
- the header folds into a Menu button on phones;
- the model menu fits the screen;
- each protein page now carries its title, facts and model label in the HTML, so they appear at
  once instead of after the data loads.

Pages that crash now show a "Something went wrong / Try again" page. A size report keeps each
page's JavaScript within budget and checks that the 3D viewer is not in the Landing page's
first download. There are no analytics. Decision records: ADR 0042, and an addendum to ADR 0038.

**The 3 most important design choices.**

1. **Change the site, not the budget.** The budgets (≥ 0.90, ≥ 0.95, median of 3) were set before
   measuring, and the Explorer missed them. The fixes went into the page itself: prerendered text
   (ADR 0038 addendum) and a reserved height while loading. Nothing makes Lighthouse see
   something different from what a reader gets.
2. **Prerender only what does not need the grids.** The build reads each protein file, checked
   like the browser checks it (sha256 against the manifest, then the validator), and puts a
   small summary in the HTML. The heatmap, side panel, structure and table still come from the
   file in the browser. The summary comes from one function used in both places, and a test
   checks the prerendered label and description equal the loaded view's.
3. **Budgets that cannot go blind.** The JavaScript budget reads what each prerendered page
   actually loads, because Turbopack prints no sizes. The 3Dmol check fails if it can no longer
   find 3Dmol in any chunk, rather than passing because a minifier renamed things.

**Problems encountered and how they were solved.**

1. **Fixing the layout shift moved the problem.** *Found:* the protein page's footer was the
   largest paint, then jumped down when the heatmap arrived (CLS 0.22, mobile 0.81). Reserving a
   screen's height and prerendering the title fixed the shift (CLS 0), but the score only reached
   0.83: the largest paint became the model description, drawn after the scripts, the manifest
   and the 88 kB protein file (4.0 s simulated). *Fix:* after asking (a change to ADR 0038's
   loading design), the description, facts and controls are prerendered from the bundle at build
   time. The controls are in the HTML too (disabled), because inserting them later would push
   the label line down. *Verified:* the largest paint is the description, at 36 ms in Chromium at
   phone size with the protein file held back. Mobile 0.92, then 0.91 on a second measurement,
   CLS 0. All 217 pages carry their manifest name and label in the HTML.
2. **Every page was 27 px too wide on a phone.** *Found:* the new 375 px test failed on all pages
   (scrollWidth 402–403), and on protein pages 442: the header's five links, and the model menu
   as wide as its longest option. *Fix:* a Menu disclosure button below 640 px (squeezing the
   padding would still overflow at 360 px), and `max-w-full` on the menu. *Verified:* the test
   passes on all pages and on the 3,423-residue protein. A Playwright test opens the menu,
   follows a link and sees it close.
3. **A careless `git checkout` while measuring the baseline.** *Found:* to measure main's
   JavaScript sizes I stashed my work and ran `git checkout -- .` in `web/`, which also reverted
   the uncommitted `package.json` and lockfile changes. The untracked size script was deleted in
   the same step. *Fix:* the script was restored from a scratch copy, and the two dev
   dependencies were re-added at the same pinned versions. *Verified:* `package.json` differs from
   main only by `@axe-core/playwright` 4.14.0, `@lhci/cli` 0.15.1 and two scripts; no lockfile
   entry of main's is missing (its diff's 4 "deleted" lines are realignments); `pnpm install
   --frozen-lockfile` passes.

**Supporting details.**

- Lighthouse, median of 3 (M4 Pro, `next start`; performance / accessibility):

  | Page | Before: mobile | Before: desktop | After: mobile | After: desktop |
  | --- | --- | --- | --- | --- |
  | Landing | 0.94 / 0.96 | 1.00 / 0.96 | 0.95 / 1.00 | 1.00 / 1.00 |
  | GFP protein page | 0.81 / 0.92 | 0.99 / 0.92 | 0.92 / 1.00 | 1.00 / 1.00 |

  A second full run (`make web-quality`): Landing mobile 0.94, GFP mobile 0.91 (runs 0.92, 0.91,
  0.91). The mobile margin is small; the CI job is not required yet (ROADMAP).
- axe violations at baseline: `link-in-text-block` on Landing, Benchmarks, How it works and the
  protein page (both themes); `heading-order` and `target-size` (20 px rows in the top-10 lists)
  on the protein page. Now none, on 8 pages × 2 themes.
- Initial JavaScript, gzip, on main: Landing 195.5 kB, `/explore` 232.2, protein page 241.8,
  `/score` 211.6, Benchmarks / How it works / 404 193.7, About 228.6. Budgets are +10%, rounded
  up to 5 kB. This branch adds about 1 kB per page (the error boundary and the menu icons).
  React and the Next.js runtime are about 160 kB of every page.
- Build with prerendered protein text: clean `pnpm build` 7.6 s. Generating the 233 static pages
  takes 650 ms (about 505 ms before, warm build). A protein page's HTML is about 21.5 kB (5.4 kB
  gzip).
- Tests: Vitest 143 (12 new: error pages, size script, Lighthouse summary, prerendered summary);
  Playwright 67 + 1 skipped (27 new in `e2e/quality.spec.ts`). CI runs Playwright for the first
  time, in the new job.
- New dev dependencies: `@axe-core/playwright` 4.14.0, `@lhci/cli` 0.15.1 (Lighthouse 12.6.1),
  versions checked on npm. No new GitHub Action.
- `<protein id>` was left blank in the task; GFP (`GFP_AEQVI_Sarkisyan_2016`) was used, the
  Landing page's first example.
- Found and listed in the ROADMAP, not fixed: no favicon (a 404 in every page's console),
  3Dmol's chunk has no source map, `global-error` is always light, about 13 kB of legacy
  polyfills.

**Follow-up (2026-10-19): the budgets are met on the Mac, not in CI.** The entry above reports
the budgets as met. That was measured on the M4 Pro, which renders with its GPU. CI's first run
scored performance 0.67 / 0.72 (mobile, Landing / Explorer) and 0.70 / 0.88 (desktop), with
accessibility 1.00.

- *Found:* the runner's CPU benchmark (2,850–3,090 against the Mac's 3,900) and its first and
  largest paints were close to the Mac's. Total blocking time was not: Landing 7.3 s mobile and
  1.7 s desktop, Explorer 1.2 s mobile. GitHub's runners have no GPU, so 3Dmol's WebGL runs in
  software on the main thread: the Landing structure's turn costs 60–180 ms a frame, and the
  Explorer viewer's setup is one task of up to 0.9 s. Software rendering on the Mac
  (`--use-angle=swiftshader`) reproduced it.
- *Fix (agreed in review):*
  - the 3D views load only when on screen (ADR 0039 and 0041 addenda): the Explorer's viewer
    when its box scrolls into view, the Landing structure when the figure is visible, never
    turning with reduced motion;
  - the Lighthouse budgets are asserted by `make web-quality` on a machine with GPU rendering;
  - CI records and reports its scores as "no-GPU runner" and fails only on axe, 375 px, the
    end-to-end suite and the JavaScript budget (ADR 0042, follow-up).
- *Verified, on the Mac with GPU rendering* (`make web-quality`, exit 0, median of 3): Landing
  mobile 0.95 / 1.00, desktop 1.00 / 1.00; Explorer GFP mobile 0.95 / 1.00 (total blocking time
  13 ms, was 236), desktop 1.00 / 1.00. Playwright 69 + 1 skipped. Two new tests: nothing 3D is
  fetched before the structure is reached, and on a 375 × 667 phone the Landing structure loads
  only when scrolled to. The hover-linking tests scroll to the structure first.
- *Without a GPU,* mobile Explorer reaches about 0.87 (software rendering on the Mac, current
  code; the same with both 3D views switched off), because software painting delays the largest
  paint (3.9 s simulated). Landing reaches 0.73 mobile and 0.90 desktop: on Lighthouse's screens
  the figure is visible at load, so it still loads and turns. A no-GPU runner cannot meet the
  mobile budget, which is why CI does not assert it.
- Also fixed: the CI artifact had no Lighthouse reports (`upload-artifact` skips hidden
  directories), and the summary now lists the metrics behind each score and the machine's CPU
  benchmark.

**Follow-up (2026-10-19): a race in the Landing structure test.** After the merge, CI on `main`
(run #154) failed "the Landing structure loads after first paint and turns" with
`scrollIntoViewIfNeeded: Element is not attached to the DOM`; the run before it (#152) had
passed.
- *Found:* on a desktop-sized window the placeholder is on screen at once, so React replaces
  it, first with the module's loading fallback, which had no test id, then with the viewer.
  The test was scrolling the element being replaced. Reproduced locally: 5 failures in 45 runs
  of the three Landing structure tests (8 workers, no retries).
- *Fix:* the loading fallback carries the same test id (`data-state="loading"`), so there is
  always exactly one `landing-structure` element (waiting → loading → ready). The tests scroll
  the figure, which is never replaced. The Explorer tests had the same pattern with the
  structure placeholder: they now scroll the Structure section.
- *Verified:* 90 of 90 runs of the Landing tests and 105 of 105 of the structure and quality
  tests under the same load. Full suite 69 + 1 skipped; `make lint test` passes.

---

## Entry 45 — Launch checks (Phase 5 Task 7, 2026-10-19)

**Plain-language summary.** The website (Vercel, https://mutascope.abdullahhanif.me) and the API
(Cloud Run) were already live. This task added one script that checks them together, the way a
visitor meets them: `tools/smoke/launch.py`. It wakes the API (up to 30 s for a cold start),
loads the seven pages, checks that a protein page names its protein, sends CORS preflights from
the site's origins and from a stranger's, and scores one substitution in fast mode from the
site's origin. It is not a CI job, because it tests the production deployment, which changes
outside any pull request. The README now says the project is launched and links the site.

**The 3 most important design choices.**

1. *Standard library only, run with `uv run python`.* The script needs nothing beyond `urllib`,
   so it runs in the project's environment with no new dependency and no locked tool project
   (unlike `tools/loadtest`, which needs Locust).
2. *The sequence comes from the API, and only its length is logged.* The fast-mode request
   scores the assay's own target sequence (from `GET /v1/proteins/<assay>`) with one
   substitution at residue 1, so wild-type matching always passes, and neither the sequence nor
   the mutation reaches the output (CLAUDE.md, API logs).
3. *A refusal must be an answer.* The `https://evil.example` preflight passes only if the API
   answers (Starlette: 400) without `access-control-allow-origin`; no answer at all is a failure,
   not a refusal (problem 1).

**Problems encountered and how they were solved.**

1. *Every request failed, and the refused-origin check passed anyway.* The first run used
   python.org's `python3`, which has no CA certificates on this Mac: every request failed with
   `CERTIFICATE_VERIFY_FAILED` (13 of 14 checks failed), and the `evil.example` check passed, because
   "no header" was all it asked for. Fix: run with `uv run python` (documented in the usage), and
   the refused-origin check needs a status other than 0. Verified: the next run gave the refusal
   as status 400 with no header.
2. *The protein-name check could not fail.* It looked for "GFP" in the page, and "GFP" is also
   in the assay id `GFP_AEQVI_Sarkisyan_2016`, which every page for it contains. Fix: it looks
   for the page title, `<title>GFP (GFP_AEQVI_Sarkisyan_2016)`, built by `pageMetadata` from the
   name. Verified: the title is in the production HTML, and a run against `--site
   https://example.com` fails 9 checks.

**Supporting details.**

- *Result, 2026-10-19:* 15 of 15 passed. `/health` 200 (version 0.8.0, warm, 0.2–0.3 s, so the
  30 s cold-start allowance was not used); seven pages 200; protein title found; preflights from
  `https://mutascope.abdullahhanif.me` and `https://mutascope-web.vercel.app` allowed (200, own
  origin echoed), from `https://evil.example` refused (400, no header); `POST /v1/score` 200,
  `esm2_35m_onnx_fp32`, 238 residues, with `access-control-allow-origin:
  https://mutascope.abdullahhanif.me`.
- *CORS:* `conf/deploy/cloudrun.yaml` allows exactly `https://mutascope.abdullahhanif.me` and
  `https://mutascope-web.vercel.app`; preview deployments and local development are not allowed
  against the live API.
- *Loose ends (the author's):* no favicon (`/favicon.ico` 404); `web quality` is not yet a
  required check; GHCR visibility, Sentry, the trusted-proxy log check and the precise-mode
  limit stay open (ROADMAP); demo video and write-up.

## Entry 46 — README and launch drafts (Phase 5 Task 8, 2026-10-19)

**Plain-language summary.** The README was written for a developer running the pipeline; it is
now written for a first-time visitor. Its first screen has the live site link, a hero image
(`docs/img/explorer.png`, captured by hand: `docs/launch/hero-image.md` says exactly what to
capture), a one-paragraph summary and the headline results, followed by an architecture
diagram (Mermaid), short "run it yourself" steps, links to the site, the API docs, the dataset
and the models, a line on AI assistance and the statement that the results are not for clinical
use. Everything operational (the pipeline steps, resuming a run, the model gate, experiment
tracking, the benchmark harness, publishing, Docker, Foldseek) moved word for word to
`docs/OPERATIONS.md`. `docs/launch/` holds drafts for the demo video script, a LinkedIn post and
a portfolio description.

**The 3 most important design choices.**

1. **Numbers come only from the rows the site shows.** Each headline number is a row of
   `benchmarks.json` in `bundle-v1.1.0` (revision `1bfc294`, whose `SHA256SUMS` the local copy
   matches) that the Landing or Benchmarks page reads through `web/src/lib/benchmarks/select.ts`,
   rounded as the pages round it. An HTML comment beside each number names its row, and each
   launch draft ends with a table of its sources. Nothing was copied from an ADR.
2. **The headline table shows paired differences.** The first table lists four paired
   comparisons: SaProt − ESM-2 650M; the ensemble − SaProt, whose interval includes 0 and is
   kept to show it; the deployed ridge − zero-shot SaProt on the contiguous split (the
   headline); and the same on modulo. Each model's own Spearman is in a second table, with a
   note not to compare models from it (ADR 0014).
3. **Moved, not rewritten.** The operational sections went into `docs/OPERATIONS.md`
   unchanged, with only their relative links adjusted (every one checked to resolve). The one
   pointer to them in code, Foldseek's install hint, now names the new file.

**Problems encountered and how they were solved.**

1. *Foldseek's install hint pointed at a README section that no longer exists.*
   `mutascope.data.three_di.INSTALL_HINT` said "see README, 'Installing Foldseek'". Found by
   grepping `src`, `tests` and `pipeline` for "README". Fix: it names `docs/OPERATIONS.md`, and
   so does the comment in `pipeline/modules/foldseek_3di.nf`. The section heading is unchanged,
   so `tests/data/test_three_di.py`'s check for "Installing Foldseek" still holds.
2. *The LinkedIn draft was 1,299 characters, with no margin under 1,300.* Measured with
   Python's `len` on the post text after the first draft. Fix: three sentences shortened, to
   1,249. A later edit (the reproduction test's wording, "in cross-validation" beside +0.092 and
   the author's closing line) took it to 1,320; four phrases shortened again, now 1,276. The
   file records the measured count.

**Supporting details.**

- GitHub's Markdown API (`POST /markdown`, `gfm`) renders the new README with 3 tables and the
  Mermaid block, and none of the source comments shows in the rendered text.
- The site, API `/health`, the dataset, both model repositories and proteingym.org all
  returned 200 on 2026-10-19.
- *Loose ends (the author's):* capture `docs/img/explorer.png` before merging (until then the
  README shows a broken image); record the video; check the flags at the end of each draft in
  `docs/launch/`. ROADMAP's "README, 2-minute demo video, write-up" stays open until the video
  exists.

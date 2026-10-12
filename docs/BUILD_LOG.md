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

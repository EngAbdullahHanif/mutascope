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
   (ESM-2), 6.56e-4 (SaProt) and 4.04e-4 (ridge), and the aggregates move by at most 9e-6.
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

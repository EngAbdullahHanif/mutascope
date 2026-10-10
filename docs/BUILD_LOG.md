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
accessibility), whether it sits in a helix, a strand or a loop, and how many other residues
are packed around it. `mutascope data foldseek-3di` runs the Foldseek tool to describe each
residue's 3D neighbourhood as one of 20 letters (the "3Di" alphabet), which the structure-aware
SaProt model will read in Phase 2. `mutascope data structure-report` checks the two outputs
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
2. **The secondary-structure method calls disordered regions "strand".** *Found:* while
   checking whether the features make physical sense, BRCA1 (mean pLDDT 41) came out 35%
   strand. *Cause:* P-SEA, biotite's method, uses only the geometry of the CA atoms, and
   AlphaFold draws disordered regions as long extended chains that look like strands. Of the
   12,301 residues with pLDDT < 50, 32.9% are called strand, against 18.4% of the 40,323 with
   pLDDT ≥ 90. *Status:* **open, for review.** Nothing was changed, since overriding the
   assignment would be a silent fix. The options are to keep P-SEA (models also get pLDDT) or
   to switch to DSSP, the hydrogen-bond-based standard, which needs the `mkdssp` binary.
3. **Foldseek could not be installed from Homebrew or conda.** *Found:* `brew info foldseek`
   reports no formula, and conda is not installed. *Fix:* the official GitHub release
   tarball (`10-941cd33`, sha256 `6d8b07e1…cae5`), installed to `~/.local/bin` and found
   through `FOLDSEEK_BIN`. This turned out to be better for reproducibility, because the
   release is immutable and also ships the `linux-avx2` binary the Docker image will need.
   *Verified:* `foldseek version` prints the pinned commit, and a wrong binary is refused with
   a clear message.
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
- *Tests:* 29 new tests (149 in the suite, 1 skipped), using a real 48-residue ProteinGym
  structure checked against a hand-written parser of the PDB text, plus golden values. A
  shell script stands in for Foldseek so CI needs no binary, and one golden test runs the real
  Foldseek whenever it is installed.
- *Dependencies:* biotite 1.6 (`uv add biotite`) for PDB parsing, SASA and P-SEA. Foldseek is
  an external binary pinned by version (README, ADR 0006).
- *Not done yet:* the Nextflow `STRUCT_FEATURES` and `FOLDSEEK_3DI` modules (next task, after
  review), the Linux Foldseek in the Docker image, and the P-SEA vs DSSP decision.

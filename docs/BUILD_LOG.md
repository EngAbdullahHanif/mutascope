# MutaScope build log

A technical journal of how MutaScope was built, one entry per completed task, in chronological
order. Each entry has a plain-language summary and the three design choices that mattered most.
Commit hashes refer to this repository's history.

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

**Context (from the author).** The scaffold was squashed into a single commit, so the work before
it is not in git; these notes record it.
- *`.gitignore` anchor fix:* the first `.gitignore` had a bare `data/` rule. Git applies a pattern
  without a leading slash at every depth, so it also ignored `src/mutascope/data/` and
  `conf/data/`. This showed up as source files missing from `git status`. Anchoring the rule to
  `/data/` limits it to the top-level data directory, and `0742ede` already contains the fix.
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
- *Fixed along the way:* a logging bug where cached loggers wrote to a closed stderr (`ee1809e`,
  with a regression test), and the Nextflow `test` profile now sets
  `data.structure_exceptions=null` so a 2-assay test subset does not trip the exceptions check.
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

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

**Gaps (not recoverable from git).** The scaffold was squashed into a single commit, so the work
before it left no record.
- *`.gitignore` anchor fix:* in `0742ede`, `.gitignore` already uses anchored patterns
  (`/data/`, `/outputs/`, `/work/`, …). The original problem and its fix happened before that
  commit. **TODO (author): The original .gitignore had a bare data/ rule that was ignoring src/mutascope/data/ and conf/data/. I noticed this when my source files weren't showing up in git status. I fixed it by anchoring the rule to /data/ and adding !/data/manifest.yaml.**
- *Alternatives considered:* anything that was tried and dropped while setting up the tooling, and
  any reasons beyond those written in ADR 0001. **TODO (author). Before settling on uv, I considered Poetry and pip-tools. Before ruff, I considered Black + isort + flake8. I chose uv and ruff because they're faster, modern, and reduce the number of tools I need to manage.**

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
- *Crash safety:* downloads go to `.part` files and extractions to `.tmp` directories, and are
  renamed only when complete and verified, so a file at its final path is always complete and
  verified.
- *Fixed along the way:* a logging bug where cached loggers wrote to a closed stderr (`ee1809e`,
  with a regression test), and the Nextflow `test` profile now sets
  `data.structure_exceptions=null` so a 2-assay test subset does not trip the exceptions check.
- *Tests run offline:* a mock HTTP server and zips built during the test keep `make test` fast and
  CI-safe.

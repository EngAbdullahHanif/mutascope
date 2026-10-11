# MutaScope — project map

A one-page index from each completed step to the files it created or changed, what each file does
and why it exists. Use it to find your way around after a break or before an interview. The
reasoning, problems and numbers for each step are in [BUILD_LOG.md](BUILD_LOG.md) (same step
numbers as its entries), and the design decisions are in [adr/](adr/). Commit ranges are inclusive
and exclude merge commits.

## Step 1 — Repository skeleton and tooling baseline (Phase 1, 2026-10-09, `0742ede`)

- **What this step did:** set up the project layout, tooling and quality checks before any
  science (ADR 0001).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/__init__.py` | Exposes `__version__` from package metadata | Single version source for CLI and API |
| `src/mutascope/cli.py` | Typer entry point (`mutascope <group> <cmd>`) | Every pipeline step is a CLI command; created here, extended in steps 2–7 |
| `src/mutascope/utils/logging.py` | structlog setup shared by the CLI and the API | `print` is banned; fixed in step 2 (stderr at write time) |
| `src/mutascope/{data,scoring,features,models,eval,export,utils}/__init__.py` | Empty subpackages | Fix the layout from CLAUDE.md; `scoring/features/models/eval` are still empty |
| `api/app/main.py` | FastAPI app with `/health`; models to load in `lifespan` | Placeholder for the Phase 4 API |
| `api/app/openapi.py` | Writes the OpenAPI schema without a server | Used by `make types` |
| `conf/config.yaml`, `conf/{data,eval,model,train}/.gitkeep` | Root Hydra config and empty groups | Config groups are added as their code lands |
| `pipeline/main.nf`, `pipeline/nextflow.config`, `pipeline/modules/.gitkeep` | Nextflow stub and profiles | Orchestration shell; filled in step 6 |
| `pyproject.toml`, `uv.lock`, `.python-version` | uv-managed deps, ruff, pyright strict, pytest config; Python 3.11 | One reproducible environment; deps added in steps 2–5 and 7 |
| `Makefile` | Canonical commands (`setup`, `lint`, `test`, `pipeline`, `api`, `types`…) | Same commands locally and in CI; extended in step 6 |
| `.pre-commit-config.yaml` | Whitespace/YAML/TOML hooks + ruff and pyright from the uv env | Checks on every commit; fixtures excluded in step 5 |
| `.gitignore` | Ignores envs, data, Nextflow output | Data never committed; changed in step 2 to version the manifest |
| `tests/test_smoke.py` | Package imports, CLI runs | Install sanity check |
| `tests/test_reproduction.py` | `slow` placeholder for the ESM-2 650M reproduction gate | Implemented in Phase 2 |
| `api/tests/test_health.py` | `/health` via httpx AsyncClient | API smoke test |
| `CLAUDE.md` | Project rules for Claude Code | Updated in steps 6 and 8 |
| `README.md` | Project overview and quick start | Extended in steps 5, 6 and 7 |
| `docs/ROADMAP.md` | Per-phase task checklist | Items ticked as each step lands |
| `docs/adr/0001-tooling-baseline.md` | Why uv, ruff, pyright, Typer, Nextflow, Hydra | First design record |
| `notebooks/.gitkeep` | Placeholder | Notebook added in step 7 |

- **CLI commands added:** `mutascope version` prints the installed version.
- **Outputs:** none.

## Step 2 — ProteinGym v1.3 data acquisition (Phase 1, 2026-10-09 to 10-10, `ee1809e`–`20bd2c9`)

- **What this step did:** fetch a pinned ProteinGym release, verify every file against Zenodo
  and committed checksums, and check every assay's structure against its sequence.
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/data/download.py` | Idempotent download, md5/sha256 verification, extraction, zero-shot CSV → Parquet | One trusted way to get the data (ADR 0002); helpers moved to `utils/io.py` in step 4 |
| `src/mutascope/data/manifest.py` | Reads and writes `data/manifest.yaml` | Committed sha256 per file is what later runs verify against |
| `src/mutascope/data/release.py` | Typed view of `conf/data/proteingym.yaml` | Config errors fail with the key name; exceptions added in `962a9e2` |
| `src/mutascope/data/structures.py` | Maps each assay to its AF2 PDB over `pdb_range` and checks the sequence | A structure can never pair with the wrong sequence; extended in step 5 |
| `src/mutascope/utils/config.py` | Composes the Hydra config outside `@hydra.main` | The CLI is Typer, not a Hydra app |
| `src/mutascope/utils/hashing.py` | Streaming md5/sha256 of large files | Verify multi-GB archives without loading them |
| `src/mutascope/utils/logging.py` | Logger writes to the current `sys.stderr` | Fix: cached loggers wrote to a closed stream (`ee1809e`) |
| `src/mutascope/cli.py` | Adds `data download`, `data check-structures` | Created in step 1, extended here |
| `conf/data/proteingym.yaml` | Release pin (v1.3, Zenodo DOI, git commit), URLs, sizes, md5, `structure_exceptions` | Single source of the data pin; paths added in step 7 |
| `conf/config.yaml` | Adds the `data` group | Created in step 1 |
| `pipeline/main.nf`, `pipeline/nextflow.config` | `params.hydra_overrides`; test profile disables exceptions | The 2-assay test subset cannot contain the 4 exceptions (`deeed9a`) |
| `pyproject.toml`, `uv.lock` | Adds `duckdb` and `httpx` as runtime deps | Parquet conversion and the HTTP client for the downloader |
| `.gitignore` | `/data/*` plus `!/data/manifest.yaml` | Version the checksums, not the data |
| `.claude/settings.json` | `includeCoAuthoredBy: false` | Disables the Co-Authored-By commit trailer (`46b2936`) |
| `tests/test_logging.py` | Regression test for the stderr fix | Bug fix gets a test first |
| `tests/data/test_download.py`, `tests/data/test_release.py`, `tests/data/test_structures.py` | Mock HTTP server and zips; config parsing; mapping and exceptions | Offline, fast tests |
| `docs/adr/0002-data-source-and-checksums.md` | Zenodo → mirror → manifest checksum chain | Decision record |
| `docs/adr/0003-structure-exceptions.md` | The 4 documented structure mismatches | Decision record |
| `docs/BUILD_LOG.md` | Technical journal, entries 1–2 | Created here; one entry added per step |

- **CLI commands added:**
  - `mutascope data download` fetches and verifies the release into `data/raw/`.
  - `mutascope data check-structures` fails unless every non-exception assay maps to a matching PDB.
- **Outputs:** `data/raw/proteingym_v1.3/` (gitignored); `data/manifest.yaml` (committed in `63f7b67`).

## Step 3 — Mutation parsing and variant validation (Phase 1, 2026-10-10, `6b03273`–`0e33c2e`)

- **What this step did:** parse mutation strings in exactly one place and check all 2,465,767
  raw variants against their target sequences (ADR 0004).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/alphabet.py` | `AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"` | Defined once, never redefined |
| `src/mutascope/data/parse.py` | `parse_mutation`: the only 1-based → 0-based conversion | An off-by-one can live in one line only |
| `src/mutascope/data/schemas.py` | `RawVariantSchema`, `ProcessedVariantSchema` (pandera) | Validate on write; extended in steps 4, 5 and 7 |
| `src/mutascope/data/validate.py` | Schema, wild-type and `mutated_sequence` checks across all assays | Report every problem, fix none; shared helpers in step 4 |
| `src/mutascope/cli.py` | Adds `data check-variants` | Created in step 1, extended here |
| `pyproject.toml`, `uv.lock` | Adds `pandas`, `pandera` (+ `pandas-stubs` for dev) | Tables and schemas; stubs for pyright strict |
| `tests/data/test_parse.py` | Hypothesis property tests for the parser | Guard the index conversion |
| `tests/data/test_schemas.py` | Schema accept/reject cases | Schema behaviour pinned |
| `tests/data/test_validate.py` | Checker tests + shifted-sequence regression (`78e43f3`) | Proves a shifted assay is caught |
| `docs/adr/0004-variant-validation.md` | Validation rules | Decision record |

- **CLI commands added:** `mutascope data check-variants` checks every variant; exits 1 listing
  each problem with assay and row.
- **Outputs:** none (a pass/fail check).

## Step 4 — Cleaning to the processed single-mutant table (Phase 1, 2026-10-10, `dae1dbb`–`94289f9`)

- **What this step did:** write the 696,311 single mutants as validated Parquet with a per-assay
  cleaning report (ADR 0005).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/data/clean.py` | One drop reason per row, exact score parsing, partitioned output | Remove rows without hiding why; partition writer moved to `utils/io.py` in step 5 |
| `src/mutascope/data/report.py` | Cleaning report as Parquet + timestamp-free Markdown | Reruns diff only on real changes |
| `src/mutascope/data/db.py` | DuckDB views: `variants`, `cleaning_report`, `assays` | Query processed data without copying; extended in step 5 |
| `src/mutascope/utils/io.py` | `fresh_tmp_dir`, `replace_dir` | Outputs appear only when complete; extended in step 5 |
| `src/mutascope/data/validate.py`, `src/mutascope/data/download.py` | Share `pair_assays`, `raise_if_problems` | Refactor so cleaning reuses the same matching (`dae1dbb`) |
| `src/mutascope/data/schemas.py` | Adds `CleaningReportSchema`, `DROP_REASONS` | Created in step 3, extended here |
| `src/mutascope/cli.py` | Adds `data clean` | Created in step 1, extended here |
| `tests/data/test_clean.py`, `tests/data/test_db.py` | Drop reasons, failures, exact parsing; view joins | Cleaning behaviour pinned |
| `results/data/cleaning_report.{md,parquet}` | Per-assay rows in/out by reason | Committed evidence of what cleaning did |
| `docs/adr/0005-cleaning.md` | Cleaning rules, the 3,124-score parsing bug | Decision record |

- **CLI commands added:** `mutascope data clean` writes the processed table and the cleaning report.
- **Outputs:** `data/processed/variants/assay_id=<id>/data.parquet`; `results/data/cleaning_report.*`.

## Step 5 — Structure features and Foldseek 3Di (Phase 1, 2026-10-10, `1a4935c`–`494c7af`)

- **What this step did:** compute per-residue pLDDT, SASA and contact counts plus Foldseek 3Di
  for the 213 mapped assays, and cross-check them (ADR 0006).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/data/structure_features.py` | pLDDT, SASA / rel_sasa (Shrake-Rupley), Cβ contact counts | Structure features for models; SSE dropped in `67ac52b` |
| `src/mutascope/data/three_di.py` | Runs the pinned Foldseek, checks its sequence, writes 3Di | Input for SaProt; binary resolved and recorded in `67ac52b` |
| `src/mutascope/data/structure_report.py` | Cross-checks both tables, per-assay report | Fails on any disagreement before summarising |
| `src/mutascope/data/structures.py` | `target_seq` and `offset` on mappings; exception guard | Created in step 2, extended here |
| `src/mutascope/data/schemas.py` | `StructureFeatureSchema`, `ThreeDiSchema`, `StructureReportSchema` | Created in step 3, extended here |
| `src/mutascope/data/db.py` | Adds `structure_features`, `three_di` views | Created in step 4, extended here |
| `src/mutascope/data/clean.py`, `src/mutascope/utils/io.py` | Partition writer moved to `utils/io.write_partition` | Shared by clean and structure steps |
| `src/mutascope/cli.py` | Adds three structure commands | Created in step 1, extended here |
| `conf/structure/default.yaml`, `conf/config.yaml` | Contact cutoff, SASA probe, pLDDT threshold, Foldseek version pin | No hard-coded parameters |
| `pyproject.toml`, `uv.lock` | Adds `biotite` | Structure parsing and SASA |
| `.pre-commit-config.yaml` | Skips `tests/data/fixtures/` in whitespace hooks | Keep the real PDB fixture byte-identical (`0d1882a`) |
| `tests/data/conftest.py`, `tests/data/fixtures/SR43C_ARATH.pdb` | Real 48-residue structure; fake Foldseek | Realistic, offline tests |
| `tests/data/test_structure_features.py`, `tests/data/test_three_di.py`, `tests/data/test_structure_report.py` | Features, 3Di parsing, cross-checks | One golden test skipped without Foldseek |
| `results/data/structure_report.{md,parquet}` | Per-assay coverage, pLDDT, Foldseek binary | Committed evidence; regenerated in `1160dc1` |
| `docs/adr/0006-structure-features.md`, `README.md` | Feature definitions, SSE decision; Foldseek install | Decision record and setup notes |

- **CLI commands added:**
  - `mutascope data structure-features` writes per-residue pLDDT, SASA and contacts.
  - `mutascope data foldseek-3di` writes 3Di states; refuses a different Foldseek version.
  - `mutascope data structure-report` cross-checks the tables and writes the report.
- **Outputs:** `data/processed/structure_features/`, `data/processed/three_di/` (+ gitignored
  `foldseek_run.json`); `results/data/structure_report.*`.

## Step 6 — Pipeline wiring and CI (Phase 1, 2026-10-10, `26dd71e`–`3608dec`)

- **What this step did:** chain all data commands in Nextflow with a 2-assay test profile, and
  run lint, tests and that smoke test in GitHub Actions (ADR 0007).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/data/staging.py` | Links the release (or copies a subset) into one fixed input layout | Test runs and full runs look identical to later steps |
| `src/mutascope/cli.py` | Adds `data subset`, `download --link-dir`; overrides on every data command | Created in step 1, extended here |
| `pipeline/main.nf` | `DOWNLOAD → [SUBSET] → VALIDATE → CLEAN, STRUCT_FEATURES, FOLDSEEK_3DI → STRUCTURE_REPORT` | Created in step 1, wired here |
| `pipeline/modules/{download,subset,validate,clean,struct_features,foldseek_3di,structure_report}.nf` | One process per CLI command | Nextflow only calls the CLI |
| `pipeline/nextflow.config` | `local` and `test` profiles, output dirs | Created in step 1, extended here |
| `Makefile` | `pipeline-test` runs `local,test` | Created in step 1, extended here |
| `.github/workflows/ci.yml` | Lint, tests + coverage, Nextflow smoke test | CI on every push; `setup-uv` pin fixed in `3608dec` |
| `tests/data/test_staging.py` | Layout and subset tests | Staging behaviour pinned |
| `tests/test_cli.py` | Every data command takes overrides; typos fail | The pipeline relies on it |
| `results/data/validation.txt` | Output of the validation step | Committed record of the full-run checks |
| `docs/adr/0007-pipeline-wiring-and-ci.md`, `README.md`, `CLAUDE.md` | Wiring decisions; pipeline usage; `docker,test` deferred | Decision record and docs |

- **CLI commands added:**
  - `mutascope data subset` copies named assays into the staged layout (test profile).
  - `mutascope data download --link-dir` symlinks the verified release into the staged layout.
- **Outputs:** `results/data/validation.txt`; test-profile outputs in `pipeline/results/` (gitignored).

## Step 7 — EDA notebook, data card and Hub staging (Phase 1, 2026-10-10, `ef454d7`–`af52fc2`)

- **What this step did:** add a marimo EDA notebook backed by package code, the data card, and a
  command that stages and pushes the dataset to the Hugging Face Hub (ADR 0008).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/eda/summary.py` | Per-assay overview, function-type counts, score histograms in DuckDB | Notebook holds no logic; card numbers come from here |
| `src/mutascope/eda/charts.py` | Altair charts from those frames | Testable, consistent charts |
| `src/mutascope/eda/__init__.py` | Package marker | New `eda` subpackage |
| `src/mutascope/export/hub.py` | Stages validated tables, reports, card, checksums; pushes after re-verifying | What reaches the Hub is what was reviewed |
| `src/mutascope/data/schemas.py` | Adds `PublishedAssaySchema`, `FUNCTION_TYPES` | Created in step 3, extended here |
| `src/mutascope/cli.py` | Adds `data publish` | Created in step 1, extended here |
| `conf/publish/hub.yaml`, `conf/config.yaml` | Repo id, `private: true`, stage dir, card front matter | Publish settings in config |
| `conf/data/proteingym.yaml` | Adds `processed_dir`, `reports_dir` | Created in step 2; paths only, no pins changed |
| `notebooks/eda.py` | marimo notebook: sizes, function types, scores, lengths | Phase 1 EDA output |
| `.gitignore` | Ignores `__marimo__/` | marimo session cache |
| `pyproject.toml`, `uv.lock` | Adds `huggingface_hub`; `notebooks` extra (`marimo`, `altair`); MIT licence fields | New deps; licence in `af52fc2` |
| `tests/conftest.py` | Toy processed dataset (2 assays) and overrides fixture | Shared by EDA and publish tests |
| `tests/eda/test_summary.py`, `tests/export/test_hub.py` | Summaries and charts; staging, checksums, push with a fake `HfApi` | No network in tests |
| `docs/DATA_CARD.md` | Dataset card: source, cleaning, counts, limitations, licence | Becomes the Hub `README.md` |
| `docs/adr/0008-eda-and-hub-publication.md`, `README.md` | EDA and Hub decisions; usage; License section | Decision record and docs |
| `LICENSE` | MIT, © 2026 Abdullah Hanif | Required by the card's licence claim (`af52fc2`) |

- **CLI commands added:** `mutascope data publish` stages `build/hf_dataset/`; `--push` uploads it.
- **Outputs:** `build/hf_dataset/` (gitignored): `data/*.parquet`, `reports/`, `README.md`,
  `provenance.json`, `SHA256SUMS`.

## Step 8 — Phase 1 close-out (Phase 1, 2026-10-10 to 10-11, `9fcf737`–`54f6ef0`)

- **What this step did:** summarise Phase 1, point the project at Phase 2, and mark the dataset
  as published.
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `docs/adr/0009-phase-1-close.md` | Phase 1 outputs, key decisions, what Phase 2 inherits | One-page phase summary |
| `CLAUDE.md` | Current phase → Phase 2; rule to verify exact tags before pinning | Created in step 1; the rule follows the `3608dec` CI fix |
| `docs/ROADMAP.md` | Phase 1 items ticked; Hub upload ticked in `54f6ef0` | Only the `docker` profile item stays open |
| `docs/BUILD_LOG.md` | Entry 8 | Created in step 2 |

- **CLI commands added:** none.
- **Outputs:** none in the repository. `54f6ef0` marks the dataset as published to the Hub; the
  repo id is not recorded in the repository.

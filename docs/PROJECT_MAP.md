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
| `tests/test_reproduction.py` | Placeholders for the harness and model reproduction gates | Implemented in Phase 2; split in two by ADR 0010 |
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

## Step 9 — Hub repo id and split reproduction gate (before Phase 2, 2026-10-12)

- **What this step did:** record the published Hub repo id, and split the reproduction gate into
  a harness gate and a model gate (ADR 0010).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `docs/DATA_CARD.md`, `README.md`, `conf/publish/hub.yaml` | Name `engAbdullahHanif/mutascope-proteingym-v1.3` | The card's loading examples had a placeholder |
| `tests/export/test_hub.py` | Push-guard test clears `publish.repo_id` | The config default is no longer `null` |
| `docs/adr/0010-split-reproduction-gate.md` | Harness gate vs model gate, with the evidence | The leaderboard includes multi-mutants; v1 data does not |
| `CLAUDE.md`, `docs/ROADMAP.md`, `tests/test_reproduction.py` | Gate definition, checklist and placeholders follow ADR 0010 | Keep rules, plan and tests in step |
| `docs/BUILD_LOG.md` | Entry 9 | Created in step 2 |

- **CLI commands added:** none.
- **Outputs:** re-staged `build/hf_dataset/` (only `README.md` and `SHA256SUMS` differ from the Hub).

## Step 10 — Benchmark harness and harness gate (Phase 2, 2026-10-12)

- **What this step did:** implemented ProteinGym's metrics, aggregation and bootstrap CIs, and
  the harness gate, which reproduces the published leaderboard from ProteinGym's own scores
  (ADR 0011).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/eval/metrics.py` | Spearman, AUC, MCC, NDCG@10%, top-10% recall per assay | ProteinGym's definitions; undefined → NaN |
| `src/mutascope/eval/aggregate.py` | (UniProt, function) → function → overall; stratified bootstrap | One weighted routine for estimate and CIs |
| `src/mutascope/eval/benchmark.py` | Per-assay long table, summary with CIs, NaN logging, Parquet I/O | Shared by every model we benchmark |
| `src/mutascope/eval/schemas.py` | `AssayMetricSchema`, `SummarySchema` | Results are validated on write |
| `src/mutascope/eval/proteingym.py` | Reads precomputed scores and published leaderboard files | Inputs of the harness gate |
| `src/mutascope/utils/seeding.py` | `seed_all` | CLAUDE.md's seeding rule; bootstrap seed |
| `src/mutascope/data/db.py` | `literal` made public | Reused by the eval readers |
| `src/mutascope/cli.py` | Adds `eval precomputed`, `eval compare-leaderboard` | Created in step 1, extended here |
| `conf/eval/proteingym.yaml`, `conf/config.yaml` | Top 10%, bootstrap (10,000, 95%, seed 0), models, leaderboard paths | No hard-coded settings |
| `conf/data/proteingym.yaml`, `data/manifest.yaml` | Adds the AUC, MCC, NDCG and top-recall leaderboard files at the pinned commit, with sha256 | Gate covers all five metrics; author-approved, pins unchanged |
| `pyproject.toml`, `uv.lock` | Adds `scipy`, `scikit-learn` | The same metric calls as upstream |
| `tests/eval/*` | Metric, aggregation, benchmark, reader and CLI tests on a toy release | `proteingym_reference.py` holds upstream's code as an oracle |
| `tests/test_reproduction.py` | Harness gate plus two diagnostics (metric, aggregation), for all five metrics | ADR 0010; skipped without the release |
| `tests/test_cli.py` | Override check covers the `eval` commands | Created in step 1 |
| `results/proteingym_precomputed_{all,single}{,_summary}.parquet` | Per-assay and aggregate results for ESM2_650M, SaProt_650M_AF2 | Committed evidence; figures read these |
| `docs/adr/0011-benchmark-harness.md`, `README.md`, `docs/ROADMAP.md` | Decisions and evidence; usage; checklist | Decision record and docs |
| `docs/BUILD_LOG.md` | Entry 10 | Created in step 2 |

- **CLI commands added:**
  - `mutascope eval precomputed --rows all|single` benchmarks ProteinGym's precomputed scores.
  - `mutascope eval compare-leaderboard` prints our values next to the published ones.
- **Outputs:** the four `results/proteingym_precomputed_*.parquet` files.

## Step 11 — Scorer protocol, cache, ESM-2 scorer and scoring region (Phase 2, 2026-10-12)

- **What this step did:** the `Scorer` protocol and registry, the log-probability cache, device
  selection, and an ESM-2 scorer that reproduces ProteinGym's precomputed scores (ADR 0012).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/scoring/base.py` | `Scorer` protocol, registry, landscape and mutant-lookup helpers | One interface for every zero-shot model |
| `src/mutascope/scoring/esm2.py` | Masked and wild-type marginals via `transformers` | ProteinGym's strategy, batched |
| `src/mutascope/scoring/windows.py` | `optimal_window` (ProteinGym port) and tiles for long sequences | > 1022 residues; reused by SaProt |
| `src/mutascope/scoring/cache.py` | `(L, 20)` log-probs on disk, sha256 keys, atomic writes | Score each sequence once per model |
| `src/mutascope/utils/device.py` | MPS when available, else CPU | CLAUDE.md environment rules |
| `src/mutascope/eval/region.py`, `conf/eval/scoring_region.yaml`, `conf/eval/proteingym.yaml` | Benchmark scoring region: derived MSA-region set checked against config; `score_assay` | Comparable with the leaderboard; website uses full targets |
| `conf/model/esm2_{8m,35m,150m,650m}.yaml`, `conf/scoring/default.yaml`, `conf/config.yaml` | Pinned Hub revisions, batch size, window; device and cache dir | No hard-coded models or paths |
| `pyproject.toml`, `uv.lock` | Adds `torch` (CPU-only index on Linux), `transformers` | Model runtime |
| `tests/scoring/*`, `tests/test_device.py`, `tests/eval/test_region.py` | Stand-in models, real `esm2_t6_8M`, ProteinGym window oracle, region rule | Offset, windows, cache, agreement, region |
| `.github/workflows/ci.yml` | Caches the pinned 8M checkpoint | Tests download it from the Hub |
| `CLAUDE.md` | Cache key includes the scoring method and region | Masked and fast results differ; regions stay separate |
| `docs/adr/0012-esm2-scorer.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions and evidence; checklist; entry 11 | Decision record and docs |

- **CLI commands added:** none yet (scoring all assays is the next step).
- **Outputs:** none in the repository; the cache lives in `data/cache/logprobs/` (gitignored).

## Step 12 — Scoring all assays with ESM-2 650M and the model gate (Phase 2, 2026-10-12)

- **What this step did:** scored the 696,311 single mutants of all 217 assays at the positions
  they measure, made the run resumable from the disk cache, and passed the model gate (ADR 0013).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/scoring/base.py`, `esm2.py` | `positions=` on the scorer; `mutant_positions`; `positions_computed` | Score only what the benchmark needs |
| `src/mutascope/scoring/cache.py` | Entries with NaN rows, filled in incrementally; `computations.jsonl` | Checkpoint of a run; run time across resumes |
| `src/mutascope/eval/region.py` | `score_assay` requests only mutated positions, checks mutants first | Fail before model time is spent |
| `src/mutascope/eval/zero_shot.py` | `score_assays`: every assay → score partitions + run report | The benchmark scoring step |
| `src/mutascope/eval/gate.py` | Model gate: per-variant agreement and aggregate check | One function for the CLI and the test |
| `src/mutascope/eval/schemas.py`, `utils/io.py` | `ZeroShotScoreSchema`, `ScoringRunSchema`, `AgreementSchema`; `write_table` | Validate on write |
| `src/mutascope/cli.py` | `score zero-shot`, `eval model-gate` | Pipeline steps are CLI commands |
| `pipeline/modules/score_zero_shot.nf`, `main.nf`, `nextflow.config` | `SCORE_ZERO_SHOT`; `params.model`; 8M model in the test profile | Nextflow calls the CLI |
| `conf/eval/proteingym.yaml`, `conf/model/esm2_*.yaml`, `conf/scoring/default.yaml` | Gate thresholds; ProteinGym column per checkpoint | No hard-coded settings |
| `tests/scoring/*`, `tests/eval/test_{zero_shot,gate,region}.py`, `tests/test_reproduction.py` | Positions, incremental fill, crash and resume, gate verdicts, the real gate (`slow`) | 24 new fast tests |
| `.github/workflows/ci.yml` | 8M checkpoint cache for the pipeline job | The smoke test now scores |
| `CLAUDE.md`, `README.md`, `docs/adr/0013-benchmark-scoring-run.md`, `docs/adr/0012-esm2-scorer.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Rules, usage, decisions, checklist, entry 12 | Decision record and docs |

- **CLI commands added:**
  - `mutascope score zero-shot` scores every assay with the configured model.
  - `mutascope eval model-gate` compares our scores with ProteinGym's and writes our results.
- **Outputs:** `results/model_gate_esm2_650m.parquet`, `results/zero_shot_esm2_650m_single.parquet`
  and `_summary.parquet`, `results/data/zero_shot_run_esm2_650m.parquet`. Scores are in
  `data/processed/zero_shot/esm2_650m/` and the cache in `data/cache/logprobs/` (both gitignored).

## Step 13 — ESM-2 35M and 150M, wild-type marginals, MLflow tracking (Phase 2, 2026-10-12)

- **What this step did:** scored all 217 assays with the two smaller checkpoints, added the
  wild-type marginal for all three sizes, benchmarked the six score sets, passed the model gate
  for all three checkpoints, and tracked every run in a local MLflow store (ADR 0014).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/utils/tracking.py` | `log_run`: one MLflow run (params, metrics, tags, config, files) in a local SQLite store; `code_version` | Record which model, revision and config produced which numbers |
| `conf/tracking/default.yaml`, `conf/config.yaml` | `tracking.enabled`, `dir`, `experiment` | No hard-coded settings; tracking can be turned off |
| `src/mutascope/eval/zero_shot.py` | `run_label` (`<model>` or `<model>_wt`); `with_labels` joins scores with assay labels | Two methods of one model must not share a name; labels for any score set |
| `src/mutascope/eval/benchmark.py` | `summary_metrics`: a summary as flat metric names | Tracked metrics are copied from the results, not recomputed |
| `src/mutascope/cli.py` | `eval zero-shot` (new); `score zero-shot` writes `<root>/<label>/`; `model-gate` writes the agreement only; all three log a run | One writer of benchmark results; pipeline steps are CLI commands |
| `src/mutascope/scoring/esm2.py` | Empties the MPS buffer cache after each sequence | The 35M run died twice with "MPS backend out of memory" |
| `src/mutascope/eval/proteingym.py` | Docstring: labels in the score files differ in the last digits | Found when the 650M results did not come out identical |
| `pipeline/modules/score_zero_shot.nf`, `benchmark_zero_shot.nf`, `main.nf`, `nextflow.config` | One scoring and one benchmark task per (model, method); `params.models`, `params.methods`, `results_dir`, `tracking_dir` | Nextflow calls the CLI |
| `pyproject.toml`, `uv.lock` | `mlflow` 3.17.0 as the optional `tracking` extra | Not installed in the serving image |
| `Makefile` | `make mlflow` (UI on port 5001) | Canonical command for the tracking UI |
| `tests/test_tracking.py`, `tests/eval/test_zero_shot.py`, `tests/eval/test_benchmark.py`, `tests/scoring/test_esm2.py`, `tests/test_reproduction.py` | Tracking store, labels, label join, the three commands end to end, MPS memory regression, gate for three checkpoints (`slow`) | 9 new fast tests, 2 new slow cases |
| `CLAUDE.md`, `README.md`, `docs/adr/0014-model-sizes-wt-marginal-tracking.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Rules, usage, decisions, checklist, entry 13 | Decision record and docs |

- **CLI commands added or changed:**
  - `mutascope eval zero-shot` (new) benchmarks one score set and writes its results.
  - `mutascope score zero-shot --out-dir` is now the root of `<label>/` directories.
  - `mutascope eval model-gate` writes only the agreement table.
- **Outputs:** `results/zero_shot_<label>_single.parquet` and `_summary.parquet` for
  `esm2_{35m,150m,650m}` and `esm2_{35m,150m,650m}_wt`; `results/model_gate_esm2_{35m,150m}.parquet`;
  `results/data/zero_shot_run_<label>.parquet` for the five new score sets. Scores are in
  `data/processed/zero_shot/<label>/` and the MLflow store in `mlruns/` (both gitignored).

## Step 14 — Order-independent tests and paired model comparisons (Phase 2, 2026-10-12)

- **What this step did:** fixed the logging state that made one test depend on test order,
  shuffled the test order from now on, and added the paired bootstrap that every model
  comparison must use (ADR 0014, decision 13).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/utils/logging.py` | Loggers are no longer cached on first use | A cached logger ignored every later `configure_logging` call |
| `tests/test_logging.py` | Two regression tests: a later configuration and `capture_logs` reach a logger already used | The root cause, tested without relying on test order |
| `pyproject.toml`, `uv.lock` | `pytest-randomly` 5.0.0 (dev) | Shuffles test order on every run and prints the seed |
| `src/mutascope/eval/aggregate.py` | `paired_bootstrap`; resampling weights and percentile interval shared with `bootstrap_ci` | One resampling scheme and one aggregation for paired and unpaired CIs |
| `src/mutascope/eval/compare.py` | `paired_bootstrap_diff(results_a, results_b, …)` on long-format results | The only place a model comparison is computed |
| `src/mutascope/eval/schemas.py` | `ComparisonSchema` | Validate on write |
| `src/mutascope/cli.py` | `eval compare`; `_log_run` shared by single-model and comparison runs | Pipeline steps are CLI commands; the run is tracked |
| `conf/eval/proteingym.yaml` | `eval.comparisons`: the five pairs | No hard-coded model names |
| `tests/eval/test_compare.py`, `tests/eval/test_zero_shot.py` | Exact cases (constant gain, identical sets), pairing vs separate CIs, NaN handling, the command end to end | 7 new tests (one is the generic check that every command takes Hydra overrides) |
| `CLAUDE.md`, `README.md`, `docs/adr/0014-model-sizes-wt-marginal-tracking.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Rule, usage, decision 13 and results, checklist, entry 14 | Decision record and docs |

- **CLI commands added:** `mutascope eval compare` runs every pair in `eval.comparisons`.
- **Outputs:** `results/zero_shot/comparisons.parquet` (150 rows: 5 pairs × 5 metrics ×
  overall and 5 function types).

## Step 15 — SaProt 650M scorer, 213-assay benchmark and gate exceptions (Phase 2, 2026-10-12)

- **What this step did:** added the structure-aware SaProt scorer as a port of the official
  scoring, scored the 213 assays with a structure, gated it against ProteinGym's scores with two
  documented upstream errors left out, and compared it with ESM-2 650M (ADR 0015).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/scoring/masked_lm.py` | Cache handling, masked and wild-type marginals, windows, batch cap | One engine for ESM-2 and SaProt |
| `src/mutascope/scoring/esm2.py` | Now only ESM-2's tokenisation, mask token and amino-acid columns | Same numbers as before, less code |
| `src/mutascope/scoring/saprot.py` | Combined tokens, `#` + 3Di masking, probability summed over 21 structure tokens | Port of `predict_mut` (SaProt commit `e91e485`) |
| `src/mutascope/scoring/struct_tokens.py` | Structure tokens per assay from the processed tables; `#` at low pLDDT and outside the structure | Scoring never runs Foldseek |
| `src/mutascope/eval/region.py` | `structure_regions`; a region's label says `msa:` or `pdb:` | SaProt's benchmark region is `pdb_range` |
| `src/mutascope/eval/zero_shot.py`, `compare.py` | Structure-aware runs score only assays with a structure; comparisons on shared assays | 213 assays, stated everywhere |
| `src/mutascope/eval/gate.py`, `schemas.py` | Gate exceptions, stale-entry check, `exception` column, aggregates with and without them | Documented upstream errors |
| `src/mutascope/cli.py` | `--three-di-dir`, `--structure-features-dir`; `_model_assays`; gate exceptions from config | One definition of a model's assays |
| `conf/model/saprot_650m_af2.yaml`, `conf/eval/model_gate.yaml`, `conf/eval/proteingym.yaml` | Pinned checkpoint and settings; the two exceptions with evidence; the SaProt vs ESM-2 pair | No hard-coded settings |
| `pipeline/main.nf`, `modules/score_zero_shot.nf`, `nextflow.config` | Structure tables passed to scoring; SaProt in `models`, masked marginal only | Pipeline steps stay CLI commands |
| `tests/scoring/test_saprot.py`, `saprot_reference.py`, `test_struct_tokens.py`, `tests/eval/*`, `tests/test_reproduction.py` | Stand-ins, the official `predict_mut` as oracle, token rules, gate exceptions, the SaProt gate and two replay tests | 29 new fast tests, 6 new slow tests |
| `docs/adr/0015-saprot-scorer.md`, `docs/upstream/proteingym_saprot_issue.md`, `CLAUDE.md`, `README.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions and evidence; draft upstream report (not posted); rules; usage; checklist; entry 15 | Decision record and docs |

- **CLI commands added:** none; `score zero-shot`, `eval model-gate`, `eval zero-shot` and
  `eval compare` now handle structure-aware models.
- **Outputs:** `results/zero_shot_saprot_650m_af2_single{,_summary}.parquet`,
  `results/model_gate_saprot_650m_af2.parquet`, `results/data/zero_shot_run_saprot_650m_af2.parquet`,
  `results/zero_shot/comparisons.parquet` (180 rows: 6 pairs); the three ESM-2 gate files gained
  the `exception` column.

## Step 16 — Ensemble and figures (Phase 2, 2026-10-13)

- **What this step did:** built the z-score ensemble of ESM-2 650M and SaProt on the 213 shared
  assays, compared it with both members, and drew the four Phase 2 figures from the results
  files (ADR 0016).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/scoring/ensemble.py` | `zscore_mean`: within-assay z-score per member, then the mean | The combination rule, with no I/O |
| `src/mutascope/eval/compare.py` | `shared_assays`, `summarise_shared` | One rule for "the assays all score sets have"; summaries that can be drawn side by side |
| `src/mutascope/eval/figures.py` | Four figure functions and `save_figure` (SVG, PNG) | Figures from results files only; fails instead of computing |
| `src/mutascope/eval/zero_shot.py` | `write_scores` | Writes a whole score set that no model produced assay by assay |
| `src/mutascope/cli.py` | `score ensemble`, `eval shared`, `eval figures`; `_members`; ensembles in `_model_assays` and tracking | Pipeline steps are CLI commands |
| `conf/model/ensemble_esm2_650m_saprot_650m_af2.yaml`, `conf/eval/figures.yaml`, `conf/eval/proteingym.yaml` | Members; what each figure shows; two new pairs and `shared_summary` | No hard-coded model names |
| `pyproject.toml`, `uv.lock` | `figures` extra (matplotlib) | Static SVG and PNG output |
| `tests/scoring/test_ensemble.py`, `tests/eval/test_figures.py`, `tests/eval/test_compare.py`, `tests/eval/test_zero_shot.py` | The rule on exact cases, shared summaries, figures end to end and redrawn identically, the ensemble through the CLI | 16 new fast tests |
| `docs/adr/0016-ensemble-and-figures.md`, `CLAUDE.md`, `README.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions and evidence; rules; usage; checklist; entry 16 | Decision record and docs |

- **CLI commands added:** `mutascope score ensemble`, `mutascope eval shared`,
  `mutascope eval figures`.
- **Outputs:** `results/zero_shot_ensemble_esm2_650m_saprot_650m_af2_single{,_summary}.parquet`,
  `results/zero_shot/shared_summary.parquet` (90 rows: 3 score sets),
  `results/zero_shot/comparisons.parquet` (240 rows: 8 pairs), `results/figures/*.{svg,png}`
  (4 figures).

## Step 17 — Phase 2 close-out (Phase 2, 2026-10-13)

- **What this step did:** reviewed the Phase 2 checklist against the repository, recorded what is
  open or weak, summarised Phase 2 with its headline table, and pointed the project at Phase 3
  (ADR 0017). No code changed and no number in `results/` changed.
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `docs/adr/0017-phase-2-close.md` | Headline table (8 score sets, per function type, CIs, assay counts), paired comparisons, gate results, open and weak items, what Phase 3 inherits | One-page phase summary, as ADR 0009 was for Phase 1 |
| `CLAUDE.md` | Current phase → Phase 3, with what Phase 2 delivered and the one open item | Created in step 1; the section is updated at the start of each phase |
| `docs/ROADMAP.md` | Full-landscape item now counts SaProt's 28,886 residues beside ESM-2's 24,918 and is linked from the Phase 4 export; close note under Phase 2 | The item named only the ESM-2 figure; the export depends on it |
| `README.md` | Status line → Phase 3, link to ADR 0017 | The line named Phase 2 |
| `docs/BUILD_LOG.md`, `docs/PROJECT_MAP.md` | Entry 17; this step | Journal and index |

- **CLI commands added:** none.
- **Outputs:** none. The audit read `results/*.parquet`, `results/zero_shot/*.parquet` and the
  log-probability cache, and reran `make lint test` and `make test-slow`.

## Step 18 — Full landscapes at every residue (Phase 2 leftover, 2026-10-13 to 10-14)

- **What this step did:** filled every residue of every assay's landscape for ESM-2 35M, 150M,
  650M and SaProt in the existing cache, added full-target landscapes where the benchmark region
  is partial, and a model-free check that all 882 are complete (ADR 0018). No benchmark number
  changed.
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/eval/landscapes.py` | Which landscapes an assay has; fills them in slices with progress and time remaining; coverage from the cache | Website landscapes need every residue; resumable inside a long protein |
| `src/mutascope/eval/schemas.py` | `LandscapeRunSchema`, `LandscapeCoverageSchema` | Every written table has a schema |
| `src/mutascope/cli.py` | `score landscapes`, `score check-landscapes`; `_benchmark_inputs` shared with `score zero-shot` | Pipeline steps are CLI commands |
| `src/mutascope/scoring/base.py` | Documents `positions=[]` (compute nothing, return what is known) | How the runner asks what is missing |
| `conf/scoring/default.yaml` | `landscapes.checkpoint_tokens`, `landscapes.models` | No hard-coded settings |
| `pipeline/modules/{landscapes,check_landscapes}.nf`, `pipeline/main.nf`, `pipeline/nextflow.config`, `pipeline/modules/score_zero_shot.nf`, `Makefile` | Two processes after scoring; `params.landscapes`, `params.cache_dir`; smoke test shares one cache, deleted first | The pipeline reproduces the landscapes |
| `tests/eval/test_landscapes.py` | Toy scorer (slices, kill and resume, shared entries), coverage, real 8M CLI run with unchanged scores | Tests alongside the code |
| `results/data/landscape_run_<model>.parquet`, `results/data/landscape_coverage.parquet` | Last invocation per model; coverage of all 882 landscapes | Committed evidence |
| `docs/adr/0018-full-landscapes.md`, `CLAUDE.md`, `README.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions and evidence; scoring rule; usage; item ticked; entry 18 | Decision record and docs |

- **CLI commands added:** `mutascope score landscapes`, `mutascope score check-landscapes`.
- **Outputs:** 18 new cache entries and the remaining rows of the benchmark entries (cache 37 →
  41 MB, not in git); the five `results/data/landscape_*.parquet` files.

## Where Phase 2 left things

A map of what exists after steps 9–18, by the question it answers.

| Question | Where to look |
|---|---|
| What does a model score, and how? | `src/mutascope/scoring/` (`base.py` protocol, `masked_lm.py`, `esm2.py`, `saprot.py`, `windows.py`, `struct_tokens.py`, `ensemble.py`, `cache.py`); ADRs 0012, 0013, 0015, 0016 |
| How is a score set benchmarked? | `src/mutascope/eval/` (`metrics.py`, `aggregate.py`, `benchmark.py`, `zero_shot.py`, `region.py`); ADR 0011 |
| How are two score sets compared? | `src/mutascope/eval/compare.py`, `conf/eval/proteingym.yaml` (`comparisons`, `shared_summary`); ADR 0014 |
| How do we know the numbers are right? | `tests/test_reproduction.py`, `src/mutascope/eval/gate.py`, `conf/eval/model_gate.yaml`, `results/model_gate_*.parquet`; ADRs 0010, 0015 |
| Which models, at which revisions? | `conf/model/*.yaml` |
| Where are the numbers? | `results/zero_shot_<label>_single{,_summary}.parquet`, `results/zero_shot/{comparisons,shared_summary}.parquet`, `results/figures/`; summary in ADR 0017 |
| What runs in the pipeline, and what by hand? | `pipeline/main.nf` runs `SCORE_ZERO_SHOT` and `BENCHMARK_ZERO_SHOT` per model and method. `score ensemble`, `eval compare`, `eval shared`, `eval figures` and the model gate are run by hand |
| Where are the full landscapes? | The cache, one entry per model, sequence, structure tokens and region; `results/data/landscape_coverage.parquet` lists them; ADR 0018 |
| What is not in git? | `data/cache/logprobs/` (cache and checkpoint), `data/processed/zero_shot/` (score sets), `mlruns/` (MLflow store) |

## Step 19 — Supervised CV harness and a leakage canary (Phase 3, 2026-10-14, `5573742`–`579672a`)

- **What this step did:** joined ProteinGym's fold assignments to the processed variants and
  built the per-assay cross-validation loop (random / modulo / contiguous, pooled Spearman and
  MSE as ProteinGym computes them), with a training-mean baseline and a label-permutation canary
  (ADR 0019). No real model yet.
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/data/folds.py` | Joins `fold_random_5`, `fold_modulo_5`, `fold_contiguous_5` to the processed single mutants and checks every variant has a fold | The supervised protocol needs ProteinGym's own folds, not ours |
| `src/mutascope/data/schemas.py` | Schema of the folds table | Every written table has a schema |
| `src/mutascope/models/cv.py` | Per-assay CV loop: fit on four folds, predict the fifth, pool, score | One loop for every model; the test fold is never passed to `fit` |
| `src/mutascope/models/baselines.py` | `TrainingMean` (predicts the training mean) | Floor every model must clear |
| `src/mutascope/models/canary.py` | Retrains with shuffled training labels; test Spearman must be about 0 | Catches leaks the code review misses |
| `src/mutascope/models/registry.py` | Models by name (`train.model`), built from `train.params.<name>` | No model names in code |
| `src/mutascope/features/base.py` | Key-only feature sets; the rule that builders never read labels | Features cannot carry the label |
| `src/mutascope/eval/metrics.py`, `src/mutascope/eval/schemas.py` | Supervised metric schema (`model, feature_set, assay_id, scheme, fold, metric, value`) | Long results format with the scheme and pooled rows |
| `src/mutascope/cli.py` | `data folds`, `train cv`; MLflow experiment `supervised` | Pipeline steps are CLI commands |
| `conf/train/default.yaml`, `conf/config.yaml` | `train` config group | No hard-coded settings |
| `tests/data/test_folds.py`, `tests/models/test_{cv,canary,cli_train}.py` | Fold join, CV loop, canary, CLI | Tests alongside the code |
| `tests/synthetic_assays.py`, `tests/models/probe.py`, `tests/models/proteingym_reference.py` | Synthetic assays with folds; a learner known to find signal; ProteinGym's metric code as a test oracle | Pooled Spearman must match ProteinGym's own code |
| `results/supervised/cv_training_mean_none.parquet` | Baseline under the three schemes | Committed evidence |
| `docs/adr/0019-supervised-cv-harness.md`, `CLAUDE.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions; supervised rules; item ticked; entry 19 | Decision record and docs |

- **CLI commands added:** `mutascope data folds`, `mutascope train cv`.
- **Outputs:** `data/processed/folds/` (not in git); the baseline results file.

## Step 20 — Feature store for the supervised models (Phase 3, 2026-10-14, `5e2de8b`–`7747581`)

- **What this step did:** built one store with every possible substitution (19 × L) of every
  target and its features, per-residue embeddings of ESM-2 650M and SaProt, and the named feature
  sets `seq` (217 assays) and `seq_struct` (213) (ADR 0020).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/scoring/cache.py` | Cache entries may hold per-residue arrays of any width | Embeddings (1,280-d) reuse the logit cache's keys and checkpointing |
| `src/mutascope/features/embeddings.py` | Mean of the last `features.embedding_layers` hidden states from one unmasked pass | Position-i embeddings without a model run per variant |
| `src/mutascope/features/store.py` | The store: `variants/` (per-variant scalars), `positions/` (structure, source region), `arrays/` (`.npy` log-probabilities and embeddings) | Per-residue data stored once, repeated only when one assay's matrix is built |
| `src/mutascope/features/sets.py` | Feature sets assembled into one matrix per assay | Never all assays at once (memory) |
| `src/mutascope/features/schemas.py` | Store and run-report schemas | Validate on write |
| `conf/features/default.yaml` | Embedding layers and cache directory; `seq`, `seq_struct` | Sets are config, not code |
| `src/mutascope/cli.py` | `features embed`, `features build` | Pipeline steps are CLI commands |
| `tests/features/test_{embeddings,feature_store,feature_sets,cli_features}.py` | Embeddings, store, sets, CLI | Tests alongside the code |
| `results/data/embedding_run_{esm2_650m,saprot_650m_af2}.parquet`, `results/data/feature_store.parquet` | Embedding runs and the store report | Committed evidence |
| `docs/adr/0020-feature-store.md`, `CLAUDE.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions; feature rules; items ticked; entry 20 | Decision record and docs |

- **CLI commands added:** `mutascope features embed`, `mutascope features build`.
- **Outputs:** `data/processed/features/` and the embedding cache (not in git).

## Step 21 — Ridge rung: one-hot, augmented and all-feature ridge (Phase 3, 2026-10-14, `598c6ad`–`1af33ea`)

- **What this step did:** wired the feature store into `train cv` one assay at a time and ran the
  first real models: one-hot ridge, augmented ridge (Hsu et al., 2022) and ridge on `seq` /
  `seq_struct`, each with its penalty chosen by inner CV split like the outer scheme. Added the
  zero-shot comparator through the same loop, paired comparisons, the leaderboard sanity range and
  the canary on real models (ADR 0021).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/models/ridge.py` | Ridge with penalty by inner CV over the training folds; the augmented score exactly unpenalised | Penalty never sees the test fold; Hsu et al.'s model |
| `src/mutascope/features/matrix.py` | Dense columns plus `SiteOneHot` (two entries per row, never densified) | A dense site one-hot of MSH2 alone is 2.5 GB |
| `src/mutascope/features/sets.py`, `src/mutascope/features/base.py` | Assemble one assay's matrix from its variant keys only | `train cv` assembles one assay at a time |
| `src/mutascope/models/cv.py`, `src/mutascope/models/registry.py` | CV loop takes a `DesignBuilder` (`(assay_id, keys) -> FeatureMatrix`, defined here); registry builds ridge and zero-shot models | Every rung plugs into `fit(x, y, groups)` |
| `src/mutascope/models/baselines.py` | `ZeroShot`: the zero-shot score run through the CV loop | The comparator on the same test folds |
| `src/mutascope/models/canary.py` | Canary on the real models, with "informative" cases | The canary must test models that can learn |
| `src/mutascope/eval/supervised.py`, `src/mutascope/eval/schemas.py` | Summaries, paired bootstrap comparisons, leaderboard check | No difference judged from two CIs |
| `src/mutascope/cli.py` | `train canary`, `train compare`, `train leaderboard` | Pipeline steps are CLI commands |
| `conf/train/default.yaml`, `conf/features/default.yaml` | Penalty grid, comparisons, canary assays; `site_onehot` | No hard-coded settings |
| `conf/data/proteingym.yaml`, `data/manifest.yaml`, `tests/data/test_release.py` | ProteinGym's supervised leaderboard files pinned with sha256 | Inputs are pinned and checksummed |
| `tests/models/test_{ridge,cv,canary,cli_train}.py`, `tests/models/probe.py`, `tests/synthetic_assays.py` | Ridge, leakage (bit-identical predictions when a test fold's labels change), CLI | Tests alongside the code |
| `results/supervised/cv_{ridge_site_onehot,augmented_ridge_site_onehot_esm2,ridge_seq,ridge_seq_struct,zero_shot_esm2_score,zero_shot_saprot_score}*.parquet`, `canary_*.parquet`, `supervised_{summary,comparisons}.parquet`, `leaderboard.parquet` | CV results, chosen penalties, canaries, comparisons, leaderboard | Committed evidence |
| `docs/adr/0021-ridge-models.md`, `CLAUDE.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions; supervised rules; item ticked; entry 21 | Decision record and docs |

- **CLI commands added:** `mutascope train canary`, `mutascope train compare`,
  `mutascope train leaderboard`.
- **Outputs:** the results files above.

## Step 22 — LightGBM rung, parallel `train cv`, SHAP (Phase 3, 2026-10-15 to 10-16, `d796156`–`2036f3a`)

- **What this step did:** ran assays in parallel worker processes under a memory budget, added
  LightGBM with Optuna inside the training folds and PCA fitted on training rows only, a post-hoc
  run without embeddings and TreeSHAP on 3 assays. LightGBM beat ridge under random only, which
  set the rule that the deployed model is chosen on unseen positions (ADR 0022; the rule was
  added in `2036f3a`, PR #23).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/utils/parallel.py` | Spawned single-threaded workers, largest assay first, `max_variants_in_flight`; results in task order | 13 workers without swapping; results never depend on `--jobs` |
| `src/mutascope/models/cv.py` | `--jobs`, per-assay checkpoints | Long runs resume after a kill |
| `src/mutascope/models/lgbm.py` | LightGBM, 10 Optuna trials per outer fold, inner CV split like the outer scheme | Nested search; LightGBM imported only inside `fit` |
| `src/mutascope/models/pca.py` | PCA per embedding block, fitted on the rows the model trains on | No test rows in the components |
| `src/mutascope/models/importance.py` | Exact TreeSHAP (`pred_contrib`) summed per feature block | Which features the model uses; no `shap` dependency |
| `src/mutascope/eval/figures.py`, `src/mutascope/eval/schemas.py` | SHAP figure; SHAP schema | Figures from results files only |
| `src/mutascope/models/registry.py`, `src/mutascope/models/canary.py`, `src/mutascope/scoring/cache.py`, `src/mutascope/features/*` | Registry entries; parallel canary; small fixes | Same interfaces for the new rung |
| `src/mutascope/cli.py` | `train shap`, `train figures`; `--jobs` on `train cv` and `train canary` | Pipeline steps are CLI commands |
| `conf/train/default.yaml`, `conf/features/default.yaml` | LightGBM search space, jobs, memory budget; post-hoc `seq_struct_noemb` | No hard-coded settings |
| `Makefile`, `tests/conftest.py`, `tests/test_openmp_isolation.py` | LightGBM tests in a second process; a guard that fails a test loading torch and LightGBM together | Two OpenMP runtimes segfault together on macOS |
| `tests/models/test_{lgbm,pca,cv,cli_train}.py`, `tests/test_parallel.py`, `tests/models/embedding_data.py` | LightGBM, PCA leakage, serial = parallel tables | Tests alongside the code |
| `pyproject.toml`, `uv.lock`, `.gitignore` | `lightgbm`, `optuna`; checkpoints ignored | New dependencies |
| `results/supervised/cv_lgbm*.parquet`, `canary_lgbm*.parquet`, `shap_lgbm_seq_struct.parquet`, `results/figures/shap_lgbm_seq_struct.{svg,png}` | CV, post-hoc run, canaries, SHAP | Committed evidence |
| `docs/adr/0022-lightgbm-parallel-cv.md`, `CLAUDE.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions and the deployment rule; parallel and OpenMP rules; items ticked; entry 22 | Decision record and docs |

- **CLI commands added:** `mutascope train shap`, `mutascope train figures`.
- **Outputs:** the results and figure files above.

## Step 23 — MLP head, MSE vs RankNet (Phase 3, 2026-10-16, `5dc3328`–`a9cce63`)

- **What this step did:** added a small MLP (plain torch) on the same features, trained with
  squared error and with a within-assay ranking loss, early-stopped on a training fold split like
  the outer scheme, on the 198 assays with at least 300 variants (ADR 0023).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/models/mlp.py` | LayerNorm → 512 → GELU → Dropout → 128 → GELU → 1; MSE or RankNet; early stopping, then refit for the chosen epochs; one thread, seeded | Third rung; deterministic whatever process runs it |
| `src/mutascope/models/registry.py`, `src/mutascope/eval/supervised.py` | MLP entries; comparisons that allow exactly the assays below a model's minimum to be missing | Runs on fewer assays are still paired correctly |
| `src/mutascope/cli.py` | `train.min_variants.<model>` in `train cv` and `train compare` | Small assays skipped by rule |
| `conf/train/default.yaml` | Architecture, optimiser, epochs, `min_variants.mlp: 300`, fixed before any result | Nothing tuned on test folds |
| `tests/models/test_{mlp,cli_mlp}.py`, `tests/test_openmp_isolation.py` | Leakage, validation split, determinism, CLI; `models.mlp` imports neither library | Tests alongside the code |
| `results/supervised/cv_mlp_{mse,rank}_seq_struct*.parquet`, `canary_mlp_*.parquet`, `supervised_{summary,comparisons}.parquet` | CV, canaries, comparisons | Committed evidence |
| `docs/adr/0023-mlp-head.md`, `CLAUDE.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions; supervised rules; item ticked; entry 23 | Decision record and docs |

- **CLI commands added:** none (options on `train cv` and `train compare`).
- **Outputs:** the results files above.

## Step 24 — Prediction intervals: CV+ with fold-ensemble spreads (Phase 3, 2026-10-16, `011abcc`–`5885598`)

- **What this step did:** implemented CV+ conformal intervals directly (MAPIE cannot normalise
  cross-conformal scores or take per-row folds), judged them on test folds they never saw for
  ridge `seq_struct`, and wrote all-data calibration rows for the final models (ADR 0024).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/models/conformal.py` | CV+ with absolute and normalised scores | Intervals with a coverage guarantee |
| `src/mutascope/models/uncertainty.py` | Intervals inside each outer fold, calibrated on training rows only; all-data calibration rows | Test labels compared only afterwards |
| `src/mutascope/models/ridge.py` | `fold_predict`: the inner-CV fits kept as a fold ensemble | Ridge's spread for free |
| `src/mutascope/eval/uncertainty.py`, `src/mutascope/eval/schemas.py`, `src/mutascope/eval/figures.py` | Coverage, width, width-vs-error; calibration and width figures | Judging intervals; figures from results files |
| `src/mutascope/cli.py` | `train uncertainty`, `train calibrate`, `train uncertainty-figures` | Pipeline steps are CLI commands |
| `conf/train/default.yaml` | `train.uncertainty` design, fixed before any result | Not tuned on coverage |
| `tests/models/test_{conformal,uncertainty,cli_uncertainty}.py` | Known-noise coverage, bit-identical intervals when test labels change, CLI | Tests alongside the code |
| `results/supervised/uncertainty_ridge_seq_struct{,_bins,_summary}.parquet`, `results/figures/{calibration,width_error}_ridge_seq_struct.{svg,png}` | Coverage and width per assay, bin and scheme; figures | Committed evidence |
| `docs/adr/0024-conformal-intervals.md`, `CLAUDE.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions; uncertainty rules; items ticked; entry 24 | Decision record and docs |

- **CLI commands added:** `mutascope train uncertainty`, `mutascope train calibrate`,
  `mutascope train uncertainty-figures`.
- **Outputs:** `data/processed/calibration/ridge_seq_struct/` (3,994,758 rows, not in git); the
  results and figures above.

## Step 25 — Final models: results table, deployed ridge landscapes, Hub bundles (Phase 3, 2026-10-16, `6fb0d07`–`6b297de`)

- **What this step did:** wrote the final results table and figures from existing results, fitted
  the deployed ridge `seq_struct` (two fits per assay, matched to the position) on the 198
  eligible assays with full 19 × L landscapes and intervals behind reproduction checks, and staged
  the model and dataset repos for the Hub (ADR 0025). Also fixed the serial path of
  `utils.parallel` to run single-threaded (`e6592db`), which had made CI fail on Linux.
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `src/mutascope/eval/final.py`, `src/mutascope/eval/schemas.py` | Final summary, paired comparisons against the best zero-shot model, per assay and per size bin | One table for every run |
| `src/mutascope/eval/figures.py` | `final_ladder`, `final_by_size` | Figures from results files only |
| `src/mutascope/models/final.py` | Eligibility, two final fits, out-of-fold values and intervals at measured variants, CV+ elsewhere, release checks, manifest | The deployed model, blocked unless it reproduces the CV results |
| `src/mutascope/export/hub.py` | Stages the model repo and the landscapes as dataset configs; `--push` uploads | Publication on the Hub |
| `src/mutascope/utils/hashing.py` | `config_sha256` | Every landscape row carries its config hash |
| `src/mutascope/utils/parallel.py`, `tests/test_parallel.py` | `jobs = 1` under `threadpoolctl`'s one-thread limit | Serial and parallel results identical on Linux too |
| `src/mutascope/features/sets.py` | Every substitution of a set, keys included | Landscapes need all 19 × L rows |
| `src/mutascope/cli.py` | `train final-table`, `train final-figures`, `train final`, `train publish`; `data publish --landscapes` | Pipeline steps are CLI commands |
| `conf/train/default.yaml` | `train.final_table`, `train.final`, `train.publish` | No hard-coded settings |
| `tests/models/test_{final,cli_final}.py`, `tests/eval/test_final.py`, `tests/export/test_hub.py` | Brute-force leave-one-out intervals, failing checks write no bundle, Hub staging | Tests alongside the code |
| `pyproject.toml`, `uv.lock` | `threadpoolctl` as a direct dependency | The package imports it |
| `results/supervised/final_{summary,comparisons,assay_differences,size_comparisons,checks,manifest}.parquet`, `results/figures/final_{ladder,by_size}.{svg,png}` | Final table, checks, manifest, figures | Committed evidence |
| `docs/adr/0025-final-models.md`, `docs/MODEL_CARD.md`, `docs/DATA_CARD.md`, `docs/ROADMAP.md`, `docs/BUILD_LOG.md` | Decisions and caveats; model card; landscapes in the data card; items ticked; entry 25 | Decision record and docs |

- **CLI commands added:** `mutascope train final-table`, `mutascope train final-figures`,
  `mutascope train final`, `mutascope train publish`; option `--landscapes` on
  `mutascope data publish`.
- **Outputs:** `data/processed/supervised_landscapes/ridge_seq_struct/` (1,379,989 rows),
  `build/hf_model`, `build/hf_dataset` (not in git); the results and figures above.

## Step 26 — Phase 3 close-out (Phase 3, 2026-10-17)

- **What this step did:** reviewed the Phase 3 checklist against the repository, regenerated the
  headline numbers from `results/supervised/`, recorded what is open or weak, stated the
  deployment decision, and pointed the project at Phase 4 (ADR 0026). No code changed and no
  number in `results/` changed.
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `docs/adr/0026-phase-3-close.md` | Headline table (4 rungs × 3 splits, own CIs and paired differences against zero-shot), per function type, rung comparisons, deployment decision, release checks, open and weak items, what Phase 4 inherits | One-page phase summary, as ADR 0017 was for Phase 2 |
| `CLAUDE.md` | Current phase → Phase 4, with what Phase 3 delivered and what is open; uncertainty line says CV+ instead of MAPIE | The section is updated at the start of each phase; the MAPIE line was out of date since ADR 0024 |
| `docs/ROADMAP.md` | Phase 3 close note; the publish item moved to Phase 4's export; export reads the supervised manifest; Phase 3 commands as Nextflow processes added to Phase 4 | The open items need an owner in the next phase |
| `README.md` | Status line → Phase 4, links to ADR 0017 and 0026 | The line named Phase 3 |
| `docs/BUILD_LOG.md`, `docs/PROJECT_MAP.md` | Entry 26; steps 19–26, the Phase 3 index and the Phase 3 retrospective | Journal and index; steps 19–25 were missing |

- **CLI commands added:** none.
- **Outputs:** none. The audit read `results/supervised/*.parquet`, the MLflow store and the CI
  history, and reran `make lint test`.

## Step 27 — Hub publication recorded, Phase 3 in Nextflow (Phase 4 Task 0, 2026-10-17)

- **What this step did:** recorded the Hub push (both repos public, every file matching its staged
  copy) and pinned both commits in config (ADR 0026 addendum); wired `data folds` through `train
  final` into the pipeline behind a new release check that publishes only a bit-identical rerun;
  put LightGBM and the MLP behind a flag; ran the landscape check (ADR 0027).
- **Files created or modified:**

| Path | What it does | Why it exists |
|---|---|---|
| `pipeline/modules/{folds,embed,features_build,train_cv,train_calibrate,train_final,check_release}.nf`, `pipeline/main.nf`, `pipeline/nextflow.config` | The Phase 3 processes, their order, `params.supervised`, `final_run`, `release_check`, `extra_models` | `make pipeline` rebuilds the deployed model |
| `pipeline/modules/download.nf`, `src/mutascope/data/staging.py` | The staged layout also links the CV folds | `FOLDS` needs them as a path |
| `src/mutascope/models/release.py`, `src/mutascope/cli.py` | `train check-release`: files, columns, dtypes, rows and every value's bytes, stamps aside | A rerun must match the release, not only itself |
| `conf/train/default.yaml`, `conf/publish/hub.yaml` | `train.release.ignore`; `train.publish.revision`, `publish.revision` | Stamps named in config; published files read by commit |
| `conf/features/smoke.yaml` | `seq_struct` with `esm2_8m` | The smoke test runs Phase 3 without 650M models |
| `Makefile` | `pipeline-test` also deletes the embedding cache | Every smoke run uses the model |
| `tests/models/test_release.py`, `tests/data/test_staging.py`, `tests/export/test_hub.py` | Comparison cases, CLI pass and fail, folds link, pinned revisions | Tests alongside the code |
| `docs/adr/0027-phase-3-in-nextflow.md`, `docs/adr/0026-phase-3-close.md`, `docs/ROADMAP.md`, `CLAUDE.md`, `docs/BUILD_LOG.md` | Decision; addendum; items ticked; current phase and release-check rule; entry 27 | Decision record and docs |

- **CLI commands added:** `mutascope train check-release`.
- **Outputs:** none new in git; the default `make pipeline` republished the deployed run after the
  release check passed.

## Where Phase 3 left things

A map of what exists after steps 19–26, by the question it answers.

| Question | Where to look |
|---|---|
| Which features does a variant have? | `src/mutascope/features/` (`store.py`, `sets.py`, `matrix.py`, `embeddings.py`), `conf/features/default.yaml`; ADR 0020, 0021 (`site_onehot`) |
| How is a supervised model cross-validated? | `src/mutascope/models/cv.py`, `utils/parallel.py`, `conf/train/default.yaml`; ADR 0019, 0022 |
| Which models are there? | `src/mutascope/models/` (`ridge.py`, `lgbm.py`, `pca.py`, `mlp.py`, `baselines.py`, `registry.py`); ADR 0021–0023 |
| How do we know nothing leaks? | Leakage tests in `tests/models/`, the permutation canary (`models/canary.py`, `results/supervised/canary_*.parquet`), the leaderboard sanity range (`leaderboard.parquet`); ADR 0019, 0021 |
| Where are the numbers? | `results/supervised/final_{summary,comparisons,assay_differences,size_comparisons}.parquet`, `supervised_{summary,comparisons}.parquet`, `cv_*.parquet`; summary in ADR 0026 |
| How are the intervals made and judged? | `src/mutascope/models/conformal.py`, `models/uncertainty.py`, `eval/uncertainty.py`, `results/supervised/uncertainty_*.parquet`; ADR 0024 |
| What is deployed, and is it right? | `src/mutascope/models/final.py`, `eval/final.py`, `results/supervised/final_{checks,manifest}.parquet`, `docs/MODEL_CARD.md`; the bundle in `data/processed/supervised_landscapes/`; ADR 0025 |
| What runs in the pipeline, and what by hand? | `pipeline/main.nf` runs through the zero-shot landscapes and, since step 27, `data folds` to `train final` with a release check (ADR 0027). `train compare`, `final-table`, `final-figures`, `canary`, `shap` and `uncertainty` are run by hand |
| What is not in git? | `data/processed/{features,calibration,supervised_landscapes}/`, the embedding cache, `build/hf_model`, `build/hf_dataset` (staged Hub repos), `mlruns/` |

## Phase 3 retrospective

What Phase 3 (steps 19–26) is worth remembering for, in three themes. Numbers are from the
build-log entries and `results/supervised/`.

**1. Two safeguards between the code and a wrong number.**

- *The label-permutation canary* (`models/canary.py`, `train canary`; ADR 0019, 0021). It
  retrains a model on shuffled training labels 20 times. Test Spearman must then be about 0, and
  the command fails if the permuted mean is more than 4 standard errors from 0. A leak (test rows
  in training, a scaler fitted on everything, a feature built from labels) shows up as signal that
  survives the shuffle. It ran on every real model, 3 assays × 3 schemes: 72 of 72 cases passed,
  66 of them informative (true-label Spearman ≥ 0.2), largest |z| 3.49. It raised one alarm, an
  MLP test trained for 6 epochs (z = +7.9). The cause was untrained networks, not a leak, and it
  was traced before anything was reported. It sits on top of the unit tests that change every
  label of a test fold and require that fold's predictions to stay bit-identical.
- *The release gate* (`train final`; ADR 0025). The deployed bundle is written only if a refit of
  CV+ on all of an assay's data matches the saved calibration rows to 1e-9, and the out-of-fold
  pooled Spearman matches `train cv` to 1e-6 in every scheme. It guards something different from
  the canary: that the model shipped is the model the tables evaluated. The real run matched to
  0 and to 0 / 3.5e-7 / 6.6e-8 (random / modulo / contiguous). A test feeds it calibration rows
  from another penalty grid and requires failure with no bundle. Measured variants show
  out-of-fold predictions, never the final model's fit to their own label.

**2. Compute: memory, not cores, and pilots that lie.**

- *Parallelism* (`utils/parallel.py`; ADR 0022): assays run in spawned workers with one
  numerical thread each, largest first, under a budget of 160,000 variants in flight. The first
  parallel run swapped: the 13 largest assays needed about 26 GB on a 24 GB machine. With the
  budget, ridge `seq_struct` ran in 1,913 s instead of 4,256 s. That is only 2.2× faster,
  because the serial run already used several cores through Accelerate. Torch and LightGBM never
  share a process: their OpenMP runtimes segfault together on macOS.
- *Single-process pilots underestimated parallel runs by about 3×.* With 10–13 workers busy,
  per-assay times were 2.7–3.3× the single-process pilot's: `train uncertainty` took 3.0 h
  against 1.4 h estimated, and `train final` took 30 min where smoke tests suggested minutes.
  LightGBM ran at 0.63 s per variant per worker instead of 0.45. The likely cause is
  memory-bandwidth contention on the M4 Pro (4 of its cores are efficiency cores). It was never
  measured directly, and it is not shown to be specific to macOS. Estimates now scale from an
  earlier parallel run of similar work.
- *Cutting work without cutting accuracy.* The LightGBM pilot projected 35–85 h. Row-wise
  histograms and a learning-rate floor made it about 3× faster at nearly the same test Spearman
  (0.899 vs 0.901 on the profiled fold). The
  full run still took 8.9 h, so `lgbm` on `seq` was dropped. The MLP's interval design (16.2× a
  plain CV fit) was not run.
- *The 300-variant minimum* (ADR 0023, 0025) skips 15 of the 213 structure assays for the MLP and
  the deployed model. It saves time, but it was set for a statistical reason: below 300
  variants, ridge and LightGBM lose to SaProt on unseen positions (−0.244 under contiguous below
  200, ADR 0025). It also leaves about 60 variants for the MLP's early stopping. It was fixed
  from the earlier rungs' results before the MLP ran.

**3. The deployment split: supervised where it pays, zero-shot elsewhere.**

| Assays | Shown on the website | Why |
|---:|---|---|
| 198 | Ridge `seq_struct`, supervised, 80% / 90% CV+ intervals | A structure and ≥ 300 measured variants; best or tied on unseen positions (contiguous 0.553, +0.092 over SaProt) |
| 15 | SaProt 650M, zero-shot | Structure, but fewer than 300 variants |
| 4 | ESM-2 650M, zero-shot | No usable structure |

Ridge was chosen over LightGBM and the MLP because a landscape is mostly predictions at
unmeasured positions. There, LightGBM is below ridge and the MLP ties or is below it. Both are
better only under random (+0.024, +0.026). The 217-assay choice is stored per assay in
`results/supervised/final_manifest.parquet` (`default_model`, with the reason). Details: ADR
0025 and 0026.

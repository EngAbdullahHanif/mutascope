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

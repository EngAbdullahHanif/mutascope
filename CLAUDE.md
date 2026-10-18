# CLAUDE.md — MutaScope

Instructions for Claude Code working in this repository. Read this fully before making changes.

## What this project is

MutaScope predicts the functional effect of every single amino-acid substitution in a protein and
presents it as an interactive mutation landscape (heatmap + 3D structure) with an honest benchmark.

- **Data:** ProteinGym DMS substitution benchmark (single mutants only in v1).
- **Zero-shot models:** ESM-2 (35M / 150M / 650M), SaProt 650M (AF2, structure-aware via Foldseek 3Di tokens). Stretch: ESM-1v, ESM C 300M.
- **Supervised models:** per-assay models following the ProteinGym supervised protocol (random / modulo / contiguous 5-fold CV): augmented ridge → LightGBM → MLP head. Uncertainty via fold ensembles + CV+ conformal intervals (implemented directly; ADR 0024).
- **Serving:** FastAPI backend (fp32 ONNX ESM-2 35M/150M for live scoring; ADR 0029), Next.js frontend; precomputed landscapes for all ProteinGym proteins served statically.
- **Pipeline:** Nextflow DSL2 orchestrating the `mutascope` Python CLI.
- **Tracking:** MLflow, local SQLite store in `mlruns/` (no server, no account).

This is a public portfolio project. Code quality, reproducibility and honest evaluation matter more than
squeezing out leaderboard points. It must not contain any thesis code or data.

## Repository layout

```
mutascope/
├── CLAUDE.md
├── pyproject.toml            # uv-managed; single source of Python deps
├── Makefile                  # canonical commands (see below)
├── conf/                     # Hydra configs (data/, model/, scoring/, tracking/, train/, eval/)
├── src/mutascope/
│   ├── cli.py                # Typer entry point: `mutascope <group> <cmd>`
│   ├── data/                 # download, parse, validate (pandera), clean, structures, 3Di
│   ├── scoring/              # Scorer protocol, registry, marginals.py, masked_lm.py, esm2.py, saprot.py, onnx_esm2.py, struct_tokens.py, ensemble.py, cache.py
│   ├── features/             # per-variant feature builders
│   ├── models/               # ridge, lgbm, mlp (plain torch), conformal (CV+), uncertainty
│   ├── eval/                 # metrics, ProteinGym-style aggregation, bootstrap CIs, figures (matplotlib)
│   ├── export/               # landscapes → JSON/Arrow, ONNX export + quantization
│   └── utils/                # hashing, io, logging, seeding, tracking (MLflow)
├── pipeline/                 # Nextflow: main.nf, modules/, nextflow.config (profiles: local, docker, test)
├── tools/onnx-export/        # separate uv project: optimum's ONNX exporter (needs transformers < 4.58)
├── api/                      # FastAPI app (app/, tests/), Dockerfile
├── web/                      # Next.js + TypeScript + Tailwind + shadcn/ui
├── notebooks/                # marimo notebooks — exploration only
├── tests/                    # pytest for src/mutascope
├── docs/adr/                 # architecture decision records
├── data/                     # gitignored: raw/, interim/, processed/, cache/
└── mlruns/                   # gitignored: local MLflow store (mlflow.db, artifacts/)
```

## Commands

Always use these; do not invent alternatives.

```bash
make setup        # uv sync --all-extras && pre-commit install
make lint         # ruff check . && ruff format --check . && pyright
make fmt          # ruff format . && ruff check --fix .
make test         # pytest -q, then LightGBM tests in their own process (esm2_t6_8M; < 2 min)
make test-slow    # pytest -m slow (real 650M models, MPS; run locally only)
make pipeline     # nextflow run pipeline/main.nf -profile local
make pipeline-test# nextflow run pipeline/main.nf -profile local,test
make pipeline-test-docker # the same in the pipeline image (-profile docker,test; ADR 0033)
make api-image    # API image (PLATFORM=linux/amd64 for Cloud Run); make pipeline-image, make images
make test-container # pytest -m container: the API image under --cpus=2 --memory=16g
make mlflow       # MLflow UI on http://127.0.0.1:5001 over mlruns/mlflow.db
make api          # uvicorn api.app.main:app --reload --port 7860
make web          # cd web && pnpm dev
make types        # regenerate web/src/lib/api-types.ts from OpenAPI (openapi-typescript)
```

Run `make lint test` before declaring any task done.

## Environment facts

- Dev machine: Apple M4 Pro, 24 GB RAM. Use `torch.device("mps")` when available, else CPU.
  Set `PYTORCH_ENABLE_MPS_FALLBACK=1`. Use float32 (no fp16/bf16 on MPS).
- Docker on macOS cannot access MPS. GPU/MPS work runs in `-profile local` (uv env); Docker is CPU-only.
- Deployment target (Google Cloud Run, 2 vCPU / 4 GiB, ADR 0034; earlier ADRs say "the Space")
  is linux/amd64. Build images with
  `docker buildx build --platform linux/amd64` or in CI. Never assume arm64 in deployment.
- Optional GPU (LoRA fine-tuning only): Kaggle T4. Code must not require CUDA anywhere else.

## Coding conventions

- Python 3.11, `src/` layout, full type hints, pyright strict must pass.
- ruff for lint + format (line length 100). No unused imports, no `print` — use `structlog`.
- All logic lives in `src/mutascope/`. Notebooks import from the package; never copy logic into them.
- Every pipeline step is a Typer CLI command that reads inputs from paths and writes outputs to paths.
  Nextflow only calls CLI commands; no Python inline in `.nf` files.
- Configuration via Hydra; no hard-coded paths, model names or hyperparameters in code.
- Pure functions where possible; I/O at the edges. Small modules, one concern each.
- Docstrings on public functions (Google style), explaining *why* when non-obvious.
- Seed everything (`mutascope.utils.seeding.seed_all`) for any stochastic step.
- New dependency → add via `uv add`, justify in the PR description.
- Before pinning any external action, image, binary, or package version, verify the exact tag exists
  by checking the upstream releases page. Do not infer a moving major-version tag from a full version
  number.

## Data rules (non-negotiable)

1. Pin the ProteinGym release in `conf/data/proteingym.yaml`; verify sha256 checksums from `data/manifest.yaml`.
2. Mutation strings are 1-indexed (`A123G`). Internally use 0-indexed positions; convert in exactly one
   function (`data.parse.parse_mutation`) and nowhere else.
3. **Always assert the wild-type residue matches the target sequence at that position.** Mismatches fail loudly
   and are logged with assay id and row — never silently dropped or "fixed".
4. v1 keeps single mutants only. Multi-mutant rows are filtered with a logged count.
5. `DMS_score`: higher = fitter (ProteinGym convention). Never flip sign silently. `DMS_score_bin` is the binary label.
6. Use ProteinGym's AF2 structures (matched to DMS target sequences), not AlphaFold DB full-length structures.
7. Every processed table has a pandera schema; validate on write.
8. Processed data is Parquet partitioned by `assay_id`; query with DuckDB.

## Scoring rules

- All zero-shot models implement the `Scorer` protocol in `scoring/base.py`:
  `landscape(seq, struct_tokens | None) -> np.ndarray` of shape `(L, 20)`, amino-acid order
  `ACDEFGHIKLMNPQRSTVWY` (constant `AA_ORDER`; never redefine it).
- Score definition: `log P(mt | x_masked_i) - log P(wt | x_masked_i)` (masked marginal).
  The wild-type marginal (single unmasked pass) is a separate method, `fast=True`.
- ESM-2, sequences > 1022 residues: overlapping windows; each position is scored from the window
  where it is most central. Benchmark runs score the MSA region where it is not the full target
  (ADR 0012).
- SaProt (ADR 0015): combined tokens (uppercase AA + lowercase 3Di). The 3Di letter is `#` where
  pLDDT < `structure.low_plddt_threshold` (70) and at residues outside the structure's range.
  Tokens are read from `processed/three_di` and `processed/structure_features`; scoring never
  runs Foldseek. A residue is masked as `#` + its own 3Di letter, and an amino acid's
  probability is the sum over its 21 structure tokens (ported from the official SaProt
  repository, cited in the docstring).
- SaProt is never windowed, and its benchmark region is the residues the assay's structure
  models (`pdb_range`), in one pass whatever the length. This is the SaProt rule, not an
  exception to the ESM-2 one: it is what the official scoring and ProteinGym's baseline do.
- SaProt is scored and evaluated only on assays with a structure (213 of 217; ADR 0003). Any
  SaProt result, and any comparison with a sequence-only model, states the assay count and uses
  the shared assays.
- Ensemble (ADR 0016): mean over members of each member's within-assay z-score, built from
  written score sets by `mutascope score ensemble`, never from a model. An ensemble is a model
  config with `members` and exists only on the assays every member has (213 with SaProt). Do
  not change the combination rule because another one scores higher on the benchmark.
- Cache logits on disk keyed by
  `sha256(model_id + revision + sequence + struct_tokens + method + region)` (method = masked or
  wild-type marginal; region = full target or MSA region; ADR 0012).
- Benchmark runs score only the positions an assay measures (`positions=`). A cache entry always
  has shape `(L, 20)`; a row is NaN until that residue is scored, and later requests fill in the
  missing rows. NaN never counts as a score (ADR 0013).
- A score set is named by `mutascope.eval.zero_shot.run_label`: the model name for the masked
  marginal, `<model>_wt` for the wild-type marginal. That label is the `model` column of scores
  and results and the name of their directories and files; never build it anywhere else.
- The cache is the checkpoint of a scoring run: it lives outside any output or Nextflow work
  directory, and a rerun must not recompute a finished sequence.
- Full landscapes (ADR 0018) live in the same cache entries: each assay has its benchmark-region
  landscape and, where that region is partial, a full-target one (region label `full`). They are
  filled by `mutascope score landscapes`, a slice of `scoring.landscapes.checkpoint_tokens` at a
  time, and `mutascope score check-landscapes` must pass before anything reads them.
- Pin every Hugging Face model by `revision` in config.
- ONNX (ADR 0029): `scoring.onnx_esm2` (`scorer: esm2_onnx`) serves ESM-2 35M / 150M on ONNX
  Runtime, CPU only, and never imports torch or transformers (a test runs it in a fresh
  interpreter). Its tokenizer is the exported `tokenizer.json`. The cache, windows and batching
  live once, in `scoring.marginals`, for both runtimes. A cache entry's model id is
  `hf_id/file` (`cache_model_id`). `model.local_dir` reads an unreleased export with the cache
  off. `mutascope onnx export` runs optimum in `tools/onnx-export`, on a local snapshot of the
  pinned revision.
- ONNX checks: `onnx check-parity` (fp32 logits within `onnx.parity.logits_atol` of PyTorch)
  and `onnx check-scores` (the model gate's checks against our PyTorch score set). The
  quantization recipe (`onnx.quantization`) was fixed before any result. Never change it, or
  the thresholds, to make a check pass. The int8 files fail `check-scores` on 3 + 1 assays and
  are rejected. The API serves fp32 ONNX (`esm2_*_onnx_fp32`, Hub `82e1528`). 150M's
  fast-mode p95 at 1,000 residues on the deployed service was 5.72 s (> 2 s), so the live default
  is 35M fp32 (ADR 0034, section 6). Precise mode is a background job with 35M only, limited to
  about 3 min: 400 residues (163 s measured; 600 took 406 s).

## Evaluation rules (non-negotiable)

- Metrics per assay: Spearman (primary), AUC, MCC, NDCG@10%, top-10% recall.
- Aggregate ProteinGym-style: per UniProt ID → per function type → overall. Never report a plain mean over assays as the headline.
- 95% CIs by bootstrap over assays.
- Comparing two models or methods: paired bootstrap only (`eval.compare.paired_bootstrap_diff`,
  `mutascope eval compare` → `results/zero_shot/comparisons.parquet`; ADR 0014). Never judge a
  difference from two separately computed CIs, in text or in a figure.
- **Reproduction gate** (`tests/test_reproduction.py`, ADR 0010). It has two parts, and both must pass.
  If either fails, fix the bug before any new modelling work.
  - *Harness gate* (no model; needs the downloaded data): our metrics and aggregation, run on ProteinGym's precomputed `ESM2_650M`
    scores over **all** rows (single and multi-mutants), must reproduce the published average
    Spearman of 0.414 (rounds to it: |Δ| ≤ 0.0005). This tests the harness, not our model.
  - *Model gate* (`slow`): on single mutants, our ESM-2 650M scores against ProteinGym's precomputed
    `ESM2_650M` scores must have per-variant Spearman ≥ 0.99 in every assay, and our aggregate
    Spearman must be within ±0.01 of the aggregate the same harness computes from their scores on
    the same single-mutant rows. Never compare a single-mutant aggregate with the published 0.414.
  - *Gate exceptions* (ADR 0015): `conf/eval/model_gate.yaml` lists, per model, assays where
    ProteinGym's precomputed scores are shown to come from wrong inputs (two for SaProt 650M).
    They are left out of both gate checks; the benchmark uses our scores for them. An entry
    needs a replay test in `tests/test_reproduction.py` showing that the wrong inputs reproduce
    ProteinGym's scores, and the gate fails if a listed assay starts to agree. Never add an
    entry to make a gate pass without that evidence, and never touch the 0.99 threshold.
- Supervised: use ProteinGym's fold assignments for random, modulo and contiguous splits. Report all three.
- **No leakage:** scalers, feature selection and hyperparameter search (Optuna) are fit on training folds only
  (nested CV). Test folds are touched exactly once per final evaluation.
- Results go to `results/*.parquet` in long format (`model, assay_id, split, fold, metric, value`;
  supervised: `model, feature_set, assay_id, scheme, fold, metric, value`, `fold` null = pooled
  out-of-fold, ADR 0019);
  figures are generated from these files only (`mutascope eval figures` → `results/figures/`,
  ADR 0016). Figure code computes nothing: a missing value or pair is an error. Score sets shown
  side by side use `results/zero_shot/shared_summary.parquet` (`mutascope eval shared`), and a
  score set's own CI is never drawn as if it compared two score sets.
- Every command that scores or evaluates one of our models logs one MLflow run: model, pinned
  revision, method, code version, the resolved config and the metrics (ADR 0014). Tracked
  metrics are copied from the results files, never computed separately. The model gate compares
  masked marginals only; ProteinGym has no wild-type-marginal ESM-2 scores to compare with.
- Never present zero-shot predictions as supervised, or either as clinical. Variants are "functional effect in DMS assays", not "pathogenic".

## Feature rules (ADR 0020)

- The feature store (`processed/features`, `mutascope features build`) holds all 19 × L
  substitutions of every target: `variants/` (per-variant scalars), `positions/` (per-residue
  structure features and source region), `arrays/` (per-residue log-probabilities and
  embeddings, `.npy`). Per-residue data is stored once per residue and repeated only when
  `features.sets.assemble` builds one assay's matrix.
- Feature builders read variant keys only (`position, wt_aa, mt_aa`), never `DMS_score` or
  `DMS_score_bin` (ADR 0019). A variant is `(position, mt_aa)`, 0-indexed; the store never
  formats a mutation string.
- A residue inside a model's benchmark region reads that model's benchmark landscape; outside
  it, the full-target one. Every measured variant is inside.
- Embeddings: mean of the last `features.embedding_layers` `hidden_states` from one unmasked
  pass per cache entry (`mutascope features embed`), cached in `features.embedding_cache_dir`.
- Feature sets are declared in `conf/features/default.yaml`: `seq` (217 assays) and
  `seq_struct` (213). A structure set refuses an assay without a structure.
- `site_onehot` (Hsu et al. one-hot sequence, 20 × L columns) is never stored or densified: it is
  two entries per row (`features.matrix.SiteOneHot`, ADR 0021).
- `train cv` assembles one assay's features at a time (`DesignBuilder`); never all assays at once.

## Supervised rules (ADR 0019, 0021, 0022, 0023)

- A model's `fit(x, y, groups)` gets its training rows' outer folds; any hyperparameter search is
  an inner CV over those folds (split like the outer scheme). The test fold is never passed in.
- A supervised run is named by `eval.supervised.supervised_label` (`<model>_<feature_set>`).
- The zero-shot comparator of a supervised model is the `zero_shot` model run through the same
  loop on the same test folds (`esm2_score`, `saprot_score`); compare with `train compare`
  (paired bootstrap of pooled Spearman), never with the Phase 2 zero-shot numbers.
- ProteinGym's supervised leaderboard (`train leaderboard`) is a sanity range, not a gate.
- `train cv|shap|canary --jobs N` run assays (or canary cases) in spawned, single-threaded
  workers via `utils.parallel.run_parallel`, under `train.max_variants_in_flight` (memory, not
  cores, is the limit). Work sent to workers must be picklable; results never depend on `--jobs`.
- **Torch and LightGBM never share a process** (their OpenMP runtimes segfault together on
  macOS). Importing the CLI, the registry or `models.lgbm` loads neither; LightGBM tests carry
  the `lightgbm` marker and `make test` runs them in a second process.
- Early stopping (MLP) uses one training fold as validation, so it is split like the outer
  scheme, then refits on all training rows for the chosen epochs. Preprocessing (PCA, scaling) is
  fitted on the rows each model trains on.
- `train.min_variants.<model>` skips smaller assays in `train cv`; `train compare` lets such a run
  lack exactly those assays. The minimum is never chosen from that model's test results.
- A post-hoc diagnostic run is labelled post-hoc in config, results and ADR and is never a
  headline model.
- `make pipeline` rebuilds the deployed run and `train check-release` (ADR 0027) must find it
  bit-identical to the released files (only `train.release.ignore`'s provenance stamps may
  differ) before `CHECK_RELEASE` publishes it. Turn the check off (`--release_check false`) only
  for a deliberate model change, and say so in the PR.

## Uncertainty rules (ADR 0024)

- Intervals are CV+ (`models.conformal`, implemented here: MAPIE has no normalised cross-conformal
  score and cannot pass per-row folds to our models). Per outer fold they are calibrated on the
  training rows only; the test fold's labels are compared with them afterwards, never before
  (bit-identical-interval test).
- The design in `train.uncertainty` (levels, modes `random` / `scheme`, intervals `absolute` /
  `normalized`, `epsilon`) was fixed before any result; do not tune it on test-fold coverage.
- A normalised score divides by the spread of the fold ensemble of the calibration model that
  scored the row (`M_k`'s own), never by the spread of the K calibration models (K − 1 of them
  trained on the row). A model exposes its ensemble via `fold_predict` or gets members fitted.
- The point prediction an interval goes with is the outer model's (as deployed); width is judged
  against its error.
- Final models' intervals (Task 8) come from `train calibrate` rows plus refitting `CVPlus` on the
  same inputs; the refit must reproduce the saved predictions (exactly with one BLAS thread).

## Export rules (ADR 0028)

- The website bundle (`mutascope export bundle`) is one gzip JSON file per assay, a manifest and
  `benchmarks.json`, defined by the Pydantic models in `export.format` (`SCHEMA_VERSION`; JSON
  Schemas in `docs/schema/bundle/`, regenerated by `mutascope export schema`). A change a
  reader can notice bumps the version.
- Per-variant fields are `(L, 20)` grids, row-major, `AA_ORDER`; numbers are fixed-point
  (`decimals` + integers). Never loosen the encoding without the fidelity check passing:
  Spearman within `export.fidelity.spearman`, coverage within `export.fidelity.coverage`, per
  assay and aggregated, against source and reported values.
- Inputs come from the pinned Hub revisions (checked against their `SHA256SUMS`) where they
  exist; zero-shot landscapes from the cache (full target; MSA region as a label).
- Every protein file stays within `export.budget.protein_bytes`; the build fails otherwise.
- Readers use the bundle by `export.publish.revision`; a new published bundle gets a new tag.

## API (`api/`)

- FastAPI + Pydantic v2 + pydantic-settings (`api.app.settings`, env `MUTASCOPE_API_*`). Its
  defaults are the `conf/` pins (bundle `export.publish.revision`, ONNX `onnx.publish.revision`);
  a test fails if they differ. Commit hashes only (ADR 0030).
- `lifespan` loads, once: the bundle at the pinned revision (every `SHA256SUMS` file checked,
  schema version in `SUPPORTED_SCHEMA_VERSIONS` or the API does not start) and the fp32 ONNX
  scorers (any other precision is refused). The API reads only the bundle, never `data/`.
- Versioned routes under `/v1`: `proteins`, `proteins/{id}`, `proteins/{id}/landscape`, `score`
  (fast mode), `jobs` + `jobs/{id}/stream` (precise mode via Server-Sent Events), `benchmarks`;
  plus `/health` (liveness), `/ready`, `/metrics`.
- Every error is RFC 9457 `application/problem+json` (`api.app.problems`; `type`
  `urn:mutascope:problem:<slug>`). Every landscape carries its label and the version stamp.
- Read-only `/v1` GETs get a body-hash weak ETag (304 on `If-None-Match`) and `Cache-Control`;
  gzip outside it.
- The API never imports torch or transformers (`api/tests/test_no_torch.py`, fresh interpreter).
- Input validation: only canonical 20 amino acids, length 1–1022, max 50 mutations per `score` request.
- Live scoring (ADR 0031): `POST /v1/score` is fast mode (wild-type marginal, `esm2_35m` |
  `esm2_150m`, default `score_default_model`); `POST /v1/jobs` is precise mode (masked marginal,
  35M only, at most `precise_max_length` residues: 400 on the deployed service, measured, ADR 0034;
  the image's default is still 600). One job runs at a time (`api.app.jobs`, in-process worker); `job_queue_size` wait,
  then 429; finished jobs expire after `job_ttl_seconds`. SSE events are a replayable log per
  job: `queued`, `running`, one `progress` per forward batch (its rows), then `succeeded` |
  `failed` | `cancelled`; `id` = index, `Last-Event-ID` resumes. Every live response is
  `kind: zero_shot`, `label: Zero-shot`. Mutations go through `parse_mutation`, and every bad
  one is reported with its own `loc`.
- The live-score cache (`api.app.score_cache`, diskcache) is keyed by sha256 of
  `[hf_id/file, revision, method, sha256(sequence)]`; files from `onnx_local_dir` are keyed
  `local:<dir>`, never by a Hub commit. A partial (cancelled, failed) landscape is never cached.
- Live agreement (slow): fp32 ONNX through the routes against our PyTorch landscapes in
  `api/tests/golden/` (written from the pipeline cache by `python -m api.tests.make_live_golden`,
  never recomputed), Spearman ≥ 0.99. The bundle has no 35M / 150M landscapes to compare with.
- Live inference uses ONNX Runtime (fp32 ONNX ESM-2 35M/150M; int8 rejected, ADR 0029). SaProt is not served live.
- Caching with `diskcache`; CORS restricted to `cors_origins`.
- Hardening (ADR 0032; threats in `docs/threat-model.md`): every limit is a setting.
  - Clients are IP addresses: the socket address, or `X-Forwarded-For` read from the right only
    when the peer is in `trusted_proxies` (`api.app.clients`, the only place). uvicorn's proxy
    headers stay off.
  - Rate limits: a per-app slowapi `Limiter` (memory, moving window), checked by the
    `ratelimit.rate_limit(...)` dependency, never by slowapi decorators. Groups: `read`,
    `score` + `score_global`, `jobs`; plus one active job per client, stream slots per client
    and in total, `max_body_bytes` (413), `max_connections`. Refusals are problems; rate limits
    carry `Retry-After`. Probes are never limited. Never raise a default to make a test pass;
    tests set their own limits (`LOOSE_LIMITS` elsewhere).
  - **One process.** The queue, the counters and the slots are in memory: production starts with
    `python -m api.app.serve` (app object, `workers=1`, `WEB_CONCURRENCY` > 1 refused), and the
    lifespan holds `lock_file`. Never add workers without moving that state to a shared store.
- Logs: structlog JSON, one line per event, `request_id` in every line of a request (and in its
  job's worker lines), one access line from our middleware. **Never log a sequence or a
  mutation list**: log `logs.sequence_fields` (sha256 + length). The redaction processor is a
  safety net, not a licence; `test_no_sequence_or_mutation_in_any_log` must cover any new route.
- Metrics: prometheus-fastapi-instrumentator + the API's collectors (`api.app.metrics`, one
  registry per app). `/metrics` needs `metrics_token` (constant-time), 404 without one, and is
  not in the OpenAPI schema. Sentry only with `sentry_dsn`, no bodies or frame variables,
  events redacted.
- Tests: pytest + httpx AsyncClient, golden-value tests against pipeline outputs, schemathesis against
  OpenAPI (`api/tests/test_schemathesis.py`, a real uvicorn server; `positive_data_acceptance` is
  off because wild-type matching and the precise limit cannot be written in the schema).
- After changing any response schema, run `make types` and commit the regenerated TypeScript types
  (CI's `types` job fails when `web/src/lib/api-types.ts` is stale).

## Docker (ADR 0033)

- Multi-stage, `python:3.11-slim`, uv, CPU-only torch/onnxruntime wheels, non-root user, `HEALTHCHECK`, port 7860.
- Model weights fetched from the Hugging Face Hub at pinned revisions.
- Never copy `data/` or secrets into images.
- Dependencies: the base `dependencies` are what the API imports; torch, transformers and the
  rest of the pipeline's stack are the `pipeline` extra. Never move a pipeline-only package
  back into the base.
- API image (`api/Dockerfile`): `--extra api --extra onnx` only. `api.app.fetch` bakes the
  bundle and the fp32 ONNX files in at the pinned commits, checked against their `SHA256SUMS`.
  The image runs offline (`HF_HUB_OFFLINE=1`) as UID 1000, BLAS/OpenMP at 1 thread. Its last
  build step is `api.app.selfcheck`, the same check as `test_no_torch.py`. The write paths
  (`score_cache_dir`, `lock_file`) are settings, under `/home/user/.cache/mutascope`.
- `.env.example` lists every `MUTASCOPE_API_*` setting, commented out at its default; a test
  fails if one is missing or set. New setting → add it there.
- Pipeline image (`pipeline/Dockerfile`): `--extra pipeline --extra tracking`, Foldseek per
  architecture (`ADD --checksum`). `-profile docker` mounts the directories that task commands
  name by host path, and reads the configs from the image (`params.conf_dir`).

## Frontend (`web/`)

- Next.js (App Router) + TypeScript strict + Tailwind + shadcn/ui; 3Dmol.js for structures; D3/visx for the heatmap.
- API types come only from the generated `api-types.ts`; never hand-write response types.
- Precomputed ProteinGym landscapes are static assets; the site must work with the backend asleep.
- Heatmap colour scale: colourblind-safe diverging (blue–white–red), symmetric around 0.
- Linked views: hovering a heatmap column highlights the residue in 3D and vice versa.
- Always label results as "Supervised (n = …)" or "Zero-shot".

## CI (GitHub Actions)

ADR 0034. `ci.yml` on every PR and push to main, five parallel jobs whose names are the required
checks of the `main` ruleset (rename a job → update the rule): `lint + typecheck (ruff, pyright)`,
`pytest + coverage`, `API types up to date`, `Nextflow smoke test (docker,test, 2 assays)`,
`API image (build + container tests)`. Branches must be up to date with main to merge. CI must
stay green on `main`.

- Every action is pinned by exact release tag (never `@v7`); Dependabot (`uv`, Actions, Docker)
  moves the pins. Dockerfile `FROM` lines stay literal so Dependabot can read them.
- Versions: release-please (`RELEASE_PLEASE_TOKEN`, not `GITHUB_TOKEN`, or its PR gets no CI and
  its tag starts nothing) bumps `pyproject.toml` and `uv.lock` and writes `CHANGELOG.md`. Never
  edit the version by hand.
- `release.yml` on a tag `vX.Y.Z`: tag must equal the package version → API image (amd64),
  built once, pushed to Artifact Registry (what Cloud Run pulls) and GHCR → `python -m
  api.deploy.cloudrun render` → `gcloud run services replace` (image by digest, startup probe on
  `/ready`, max 1 instance) → `wait` (`/health` version = tag, `/ready` 200 on the public URL)
  or the workflow fails. `workflow_dispatch` (from main) redeploys a tag without rebuilding.
- Google Cloud access is keyless (Workload Identity Federation: only `release.yml`, on a `v` tag
  or main, may act as the deployer; `tools/deploy/setup-gcp.sh`). Never add a service-account
  key to GitHub.
- The service is defined only by `conf/deploy/cloudrun.yaml` (`env`: exactly the container's
  environment); never change it in the console, the next deploy undoes it. `max_instances` stays
  1 (ADR 0032). The only GitHub secret is `RELEASE_PLEASE_TOKEN`.
- Request-based billing: an instance has CPU only during a request, so a precise job advances
  while its SSE stream is open. Scale-to-zero: the site must work with the API asleep.
- Live measurement: `tools/loadtest/live.py --url <service url>` (short: free tier). ADR 0031's
  rule is applied in `conf/deploy/cloudrun.yaml` from its result.

## How to work in this repo

- For any non-trivial task: propose a short plan first, then implement.
- Write or update tests before or alongside the code. Bug fix → add a regression test first.
- Keep changes small and focused; one concern per commit. Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`…).
- When a design decision is made (tool, model, protocol change), add an ADR in `docs/adr/NNNN-title.md`.
- If a metric looks surprisingly good, assume leakage or an indexing bug and investigate before reporting it.
- Do not modify `conf/data/proteingym.yaml` pins, the reproduction test tolerance, the model gate
  exceptions or the evaluation protocol without explicitly asking.
- Explain non-obvious code in plain language in the PR/commit description — the author must be able to defend every line in an interview.

## Current phase

Weeks 8–10: Frontend and launch (Phase 5). Phase 1 delivered a pinned, checksum-verified ProteinGym
v1.3 pipeline producing 696,311 validated single mutants across 217 assays, structure features and
3Di for 213 of them, an EDA notebook and a data card (ADR 0009). Phase 2 delivered zero-shot scores
from ESM-2 35M / 150M / 650M (217 assays), SaProt 650M and the ESM-2 650M + SaProt ensemble (213),
the benchmark harness with paired comparisons, passing harness and model gates (ADR 0017), and full
masked landscapes at every residue for all four models in the cache (ADR 0018). Phase 3 delivered
the feature store, the supervised ladder (ridge, LightGBM, MLP) under random / modulo / contiguous
CV against zero-shot on the same folds, CV+ intervals, and the deployed model: ridge `seq_struct`
on the 198 assays with a structure and ≥ 300 variants, 19 × L landscapes with 80% / 90% intervals
in `data/processed/supervised_landscapes/`, and a manifest naming each assay's default model
(ADR 0025, 0026). Phase 4 Task 0 closed the Phase 3 leftovers: both Hub repos are public and
pinned by commit (`train.publish.revision`, `publish.revision`; ADR 0026 addendum), and `make
pipeline` runs `data folds` through `train final` and publishes only after `train check-release`
(ADR 0027). Phase 4 delivered the website bundle (`bundle-v1.0.0`, 217 files, 29.2 MB), fp32
ONNX ESM-2 35M / 150M (int8 rejected), the FastAPI service with live scoring and hardening, a
torch-free API image (600 MB compressed) and a pipeline image, five required CI checks,
release-please and keyless deployment to Cloud Run: `v0.2.0` serves at
https://mutascope-api-2lvmztrsha-ew.a.run.app (ADR 0035). Carried into Phase 5 (ROADMAP): bound
concurrent forward passes, live metrics token / Sentry, GHCR visibility, the trusted-proxy log
check, and the frontend's CORS origin. The frontend reads the bundle from the Hub at
`export.publish.revision` and must work with the API asleep (cold start ~20 s).
Update this section at the start of each phase.
The per-phase task checklist lives in `docs/ROADMAP.md`; mark items `[x]` there as they land.

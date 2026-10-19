# MutaScope roadmap

Technical plan by phase. CLAUDE.md holds the rules; this file holds the work. Mark items `[x]` as they land.
Items tagged **[stretch]** are cut first if time runs short.

---

## Phase 1 — Weeks 1–2: Foundations and data engineering

Goal: one command turns the raw ProteinGym download into clean, validated, versioned data plus structures and 3Di tokens.

### Tooling
- [x] uv + `pyproject.toml`, `src/mutascope/`, ruff, pyright strict, pytest + hypothesis, pre-commit
- [x] Typer CLI as the single entry point; Nextflow calls CLI commands only
- [x] Hydra configs in `conf/` (`data`, `structure`, `publish`; model/train/eval groups land with their code)
- [x] marimo for notebooks
- [x] ADRs in `docs/adr/`
- [x] GitHub Actions: lint + typecheck + tests (+ coverage, Nextflow `local,test` smoke run)

### Data acquisition
- [x] Pin the ProteinGym release in `conf/data/proteingym.yaml`
- [x] Download: DMS substitutions archive, reference file `DMS_substitutions.csv`, CV fold assignments,
      AF2 structures matched to DMS targets, precomputed zero-shot baseline scores (for reproduction in Phase 2)
- [x] `data/manifest.yaml` with sha256 per file; download verifies checksums and is idempotent

### Parsing and cleaning
- [x] `parse_mutation` — the only 1-indexed → 0-indexed conversion; hypothesis property tests
- [x] Wild-type residue check against the target sequence; mismatches fail loudly with assay id + row
- [x] Keep single mutants; log count of filtered multi-mutants per assay
- [x] Keep `DMS_score` (higher = fitter) and `DMS_score_bin`; drop NaN; dedupe
- [x] pandera schemas for every processed table; validate on write
- [x] Parquet partitioned by `assay_id`; DuckDB views over processed data
- [x] Cleaning report (per assay: rows in, rows out, reasons)

### Structure features
- [x] Per assay: AF2 structure → Foldseek 3Di string (`foldseek structureto3didescriptor`)
- [x] Per residue: pLDDT, relative SASA, contact count (Cα/Cβ within 8 Å). Secondary structure dropped
      (ADR 0006): biotite's P-SEA mislabels disordered regions and DSSP has no portable install
- [x] Check that structure length/sequence matches the DMS target sequence

### Pipeline
- [x] Nextflow DSL2 modules: `DOWNLOAD → VALIDATE → CLEAN → STRUCT_FEATURES → FOLDSEEK_3DI`
      (+ `SUBSET` for the test profile, `STRUCTURE_REPORT` as the final cross-check; ADR 0007)
- [x] Profiles: `local` (uv env, MPS), `docker` (CPU, amd64), `test` (2 small assays).
      `docker` done with the Phase 4 pipeline image (ADR 0033); CI runs `docker,test` on every PR

### Outputs
- [x] marimo EDA notebook (assay sizes, function types, score distributions, sequence lengths)
- [x] Dataset card (`docs/DATA_CARD.md`)
- [x] Processed dataset published to the Hugging Face Hub (`mutascope data publish` stages it;
      `--push` waits for the card review, ADR 0008)

Done when: `make pipeline` produces clean Parquet + structure features + 3Di; `make lint test` green; data card written.

---

## Phase 2 — Weeks 3–4: Zero-shot model zoo and benchmark harness

Goal: several pretrained models score every mutation; our numbers reproduce the ProteinGym leaderboard.

### Scorers
- [x] `Scorer` protocol (`scoring/base.py`) returning `(L, 20)` landscapes in `AA_ORDER`; registry
- [x] ESM-2 35M / 150M / 650M via Hugging Face `transformers`, pinned revisions (ADR 0012;
      all three scored on all 217 assays at measured positions, ADR 0013 and 0014)
- [x] SaProt 650M (AF2): combined AA + 3Di tokens, `#` where pLDDT < 70; scoring ported from the official repo
      (ADR 0015: one unwindowed pass over `pdb_range`, 213 assays; model gate on 211, two
      documented upstream errors; issue drafted in `docs/upstream/`)
- [ ] [stretch] ESM-1v ensemble; ESM C 300M (check licence first)

### Scoring methods
- [x] Masked marginal: one masked pass per position, batched 16–32, `torch.inference_mode()`, float32 on MPS
- [x] Wild-type marginal (`fast=True`): one unmasked pass (all three sizes scored on all assays as
      `<model>_wt` score sets, 8–24 s of model time each; ADR 0014)
- [x] Sliding windows for L > 1022; each position from its most central window
- [x] Disk cache keyed by `sha256(model_id + revision + sequence + struct_tokens + method + region)`
- [x] Benchmark scoring region: MSA region where it is not the full target (5 assays, derived and
      checked against `conf/eval/scoring_region.yaml`); website landscapes use the full target (ADR 0012)
- [x] Benchmark run: `mutascope score zero-shot` + Nextflow `SCORE_ZERO_SHOT`, measured positions
      only, resumable from the cache (ADR 0013; 37,753 positions on MPS: 650M 1 h 41 min,
      150M 29 min, 35M 19 min), once per model and method, then `BENCHMARK_ZERO_SHOT` (ADR 0014)
- [x] Full masked landscapes at every position (ADR 0018): the remaining 24,918 residues of the
      benchmark entries for each ESM-2 size and 28,886 for SaProt, plus full-target landscapes
      for the five MSA-region assays (ESM-2) and the three partial-structure assays (SaProt),
      in the same cache. `mutascope score landscapes` (resumable inside a sequence) and
      `mutascope score check-landscapes` (model-free; all 882 landscapes complete). 11 h 20 min
      of model time; benchmark scores unchanged. Left open at the Phase 2 close (ADR 0017)
- [x] Ensemble: per-assay z-score, then mean (ADR 0016: ESM-2 650M + SaProt on the 213 shared
      assays, Spearman 0.4663; +0.0519 over ESM-2 650M, +0.0063 over SaProt with a paired CI
      that includes zero)

### Benchmark harness
- [x] Metrics per assay: Spearman, AUC, MCC, NDCG@10%, top-10% recall (ADR 0011; undefined → NaN)
- [x] ProteinGym aggregation: per UniProt → per function type → overall
- [x] Bootstrap 95% CIs over assays (stratified by function type)
- [x] Reproduction gate, split in two (ADR 0010):
  - [x] Harness gate: our metrics + aggregation on ProteinGym's precomputed `ESM2_650M` scores over
        all rows (singles + multis) reproduce the published 0.414 (|Δ| ≤ 0.0005); also all five metrics for
        `ESM2_650M` and `SaProt_650M_AF2` (ADR 0011)
  - [x] Model gate (`slow`): our ESM-2 650M vs their `ESM2_650M` on single mutants, per-variant
        Spearman ≥ 0.99 in every assay, and single-mutant aggregate within ±0.01 of theirs
        computed by the same harness (passed: lowest assay 0.99999996, aggregate 0.4125 vs 0.4125)
  - [x] The same check for 35M and 150M against `ESM2_35M` and `ESM2_150M` (ADR 0014; passed:
        lowest assay 0.99999996 and 0.99999991, aggregates 0.3113 and 0.3820, equal to theirs)
- [x] Results in `results/*.parquet` (long format) for ESM-2 35M / 150M / 650M, masked and
      wild-type marginal; MLflow tracking of every scoring, benchmark and gate run, local SQLite
      store (ADR 0014). SaProt and the ensemble add their results when they land
- [x] Paired bootstrap for model comparisons (`mutascope eval compare`, ADR 0014): 150M vs 35M,
      650M vs 150M, masked vs wild-type per size, in `results/zero_shot/comparisons.parquet`
- [x] Figures: model size vs Spearman, per-function-type bars, ensemble gain, fast vs masked marginal.
      Any comparison between score sets uses the paired CIs from `comparisons.parquet`
      (`mutascope eval figures`, SVG + PNG in `results/figures/`; ADR 0016)

Done when: full landscapes for all assays from ESM-2 (3 sizes) + SaProt; harness and model gates pass; figures generated.

Closed 2026-10-13 (ADR 0017): gates pass and figures are generated; landscapes exist at the measured
positions only, so the full-landscape item above stays open, with the stretch models.
Full landscapes landed 2026-10-14 (ADR 0018), so the done-when line is met in full; only the
stretch models remain open.

---

## Phase 3 — Weeks 5–6: Supervised models

Goal: beat zero-shot using the assay data, under ProteinGym's supervised protocol.

### Protocol
- [x] Fold assignments joined and checked; CV loop with pooled Spearman/MSE as ProteinGym;
      label-permutation canary; training-mean baseline (ADR 0019)
- [x] Per-assay models, ProteinGym fold assignments, random / modulo / contiguous 5-fold; features
      assembled one assay at a time from the store (ADR 0021)
- [x] Optuna hyperparameter search inside training folds only (nested CV); scalers fit on train folds only
      (ridge: α by inner CV over the training folds, split like the outer scheme; scaling on the
      fit rows only — ADR 0021; LightGBM: 10 Optuna trials per outer fold, PCA fit on training rows
      only — ADR 0022)
- [x] `train cv --jobs` across assays under a memory budget, per-assay checkpoints (ADR 0022)

### Features (variant at position i, wt → mt)
Feature store for all 19 × L substitutions, sets `seq` (217 assays) and `seq_struct` (213) (ADR 0020).
- [x] Zero-shot scores, benchmark scoring region (ADR 0012): ESM-2 650M and SaProt 650M. The ensemble
      is left out: its z-score is defined over an assay's measured variants only (ADR 0020)
- [x] One-hot wt and mt, BLOSUM62
- [x] 20-dim log-probabilities at i (ESM-2, SaProt)
- [x] Position-i embeddings: ESM-2 650M (mean of last 4 layers, 1280-d) and SaProt
- [x] Structure: pLDDT, relative SASA, contact count; relative position

### Model ladder
- [x] Ridge rung (ADR 0021): one-hot ridge, augmented ridge (one-hot + unpenalised ESM-2 650M score;
      Hsu et al., 2022), ridge on `seq` / `seq_struct`; zero-shot on the same test folds, paired
      bootstrap, ProteinGym leaderboard sanity range, permutation canary on the real models
- [x] LightGBM on all features + SHAP (ADR 0022): `lgbm` / `lgbm_pca` on `seq_struct`, `lgbm_pca` on
      `seq`; beats ridge under random only, below it under modulo / contiguous; post-hoc run without
      embeddings; TreeSHAP on 3 assays; canary
- [x] MLP head (ADR 0023; plain torch, not Lightning): LayerNorm → 512 → GELU → Dropout 0.2 → 128 →
      GELU → 1; AdamW, early stopping on a training fold split like the outer scheme; MSE vs RankNet;
      198 assays (≥ 300 variants); beats ridge under random only, ties / loses on unseen positions
- [ ] [stretch] LoRA fine-tuning of ESM-2 150M on Kaggle T4 (`peft`)
- [ ] [stretch] Leave-protein-out global model

### Uncertainty
- [x] Fold-ensemble spread (ADR 0024): SD of the fold models' predictions, as a difficulty score
- [x] Conformal intervals + calibration plot (ADR 0024; CV+ implemented directly, MAPIE cannot
      normalise cross-conformal scores): ridge `seq_struct`, 80% / 90%, random vs scheme-like
      calibration folds, constant vs adaptive width; scheme-like calibration on target under every
      split, random calibration under-covers on unseen positions; MLP comparison not run (~2.5 h)
- [x] All-data calibration rows for Task 8 (`train calibrate` → `data/processed/calibration/`)

### Final models (Task 8)
- [x] Final results table (`train final-table`, ADR 0025): every run vs the best zero-shot model on
      the same assays (SaProt on structure sets, ESM-2 650M otherwise), paired, by function type
      and assay size
- [x] Final figures (`train final-figures`): model ladder per scheme, supervised vs zero-shot by
      assay size; calibration plot from Task 7
- [x] Deployed model (`train final`, ADR 0025): ridge `seq_struct` on the 198 assays with a structure
      and ≥ 300 variants, two fits per assay (penalty by random / contiguous inner CV) matched to the
      position, 19 × L landscapes with out-of-fold values at measured variants and constant-width
      CV+ intervals; checks against `train calibrate` and `train cv` passed
- [x] Publish (`train publish --push`, `data publish --landscapes --push`): left open at the
      Phase 3 close (ADR 0026) for lack of a token; pushed 2026-10-17 as Phase 4 Task 0 (ADR 0026
      addendum)

Done when: Spearman table (3 splits × 4 rungs vs zero-shot, bootstrap CIs), SHAP figure, calibration plot, model cards on the HF Hub.

Closed 2026-10-17 (ADR 0026): the Spearman table, SHAP figure, calibration plot and model card exist
and the deployed landscapes are built and checked; the model card is staged, not on the Hub (no
token on the dev machine), so the publish item stays open, with the two stretch items. Weak points
carried forward: none of Phase 3 is a Nextflow process, CI trains nothing real, Binding (13 assays)
is not shown to gain on unseen positions.

---

## Phase 4 — Week 7: API, Docker, CI/CD

Goal: a tested, observable service deployed automatically.

### Export
- [x] Phase 3 leftover: push the staged model repo and the dataset revision with the supervised
      landscapes to the Hub (ADR 0026 addendum): model `45a04b8`, dataset `0b0fe65`, both public,
      every file matching its staged copy; both commits pinned in config (`train.publish.revision`,
      `publish.revision`) for later steps to read by revision
- [x] Precomputed landscapes for all ProteinGym proteins → compressed JSON/Arrow (served statically by the frontend).
      Done as the website bundle (ADR 0028, `mutascope export bundle`): one gzip JSON file per
      assay (217, 29.2 MB, largest 1.15 MB under a 2 MB budget), `manifest.json`,
      `benchmarks.json`, versioned JSON Schemas; fixed-point encoding proved to move no Spearman
      by more than 6.6e-4 and no coverage at all; published to the dataset repo's `bundle/`
      (tag `bundle-v1.0.0`, pinned as `export.publish.revision`).
      Reads the full landscapes from the cache (ADR 0018); run `mutascope score check-landscapes` first
      (passed 2026-10-17: 882 / 882 landscapes complete, coverage table unchanged; ADR 0027).
      Supervised landscapes for the 198 eligible assays from `data/processed/supervised_landscapes`,
      chosen per assay by `default_model` in `results/supervised/final_manifest.parquet` (ADR 0025)
- [x] ESM-2 35M/150M → ONNX (`optimum`) → int8 dynamic quantization → onnxruntime CPU
      (ADR 0029, `mutascope onnx ...`): optimum in its own env (`tools/onnx-export`; it needs
      transformers < 4.58), `tokenizer.json` instead of transformers' tokenizer (it imports
      torch), a torch-free `OnnxEsm2Scorer` on a shared `scoring.marginals` core. fp32 logits
      within 1e-4 of PyTorch (max 9.0e-5). int8 fast mode: aggregate Spearman change −0.0005 /
      −0.0006 (paired CIs include 0), but per-assay agreement < 0.99 on 3 (35M) / 1 (150M) of
      217 assays, so int8 is rejected: **the API serves fp32 ONNX** (decided 2026-10-17, ADR 0029
      section 6; 35M fp32 becomes the default if 150M's p95 on the Space exceeds 2 s at
      1,000 residues). Latency at 2 threads in
      `results/onnx/latency.parquet`; published to `engAbdullahHanif/mutascope-esm2-onnx`,
      pinned as `onnx.publish.revision`

### FastAPI
- [x] Pydantic v2, pydantic-settings, models loaded in `lifespan` (ADR 0030): the bundle at
      `export.publish.revision` (checksums and schema version checked, or no start) and the fp32
      ONNX scorers; `/ready`; RFC 9457 problem details for every error; ETag + Cache-Control +
      gzip on read-only routes; no torch (fresh-interpreter test)
- [x] Read-only routes (ADR 0030): `/v1/proteins` (function type filter, search),
      `/v1/proteins/{id}`, `/v1/proteins/{id}/landscape?model=default|esm2_650m|saprot`,
      `/v1/benchmarks`, `/health`, `/ready`; golden tests against `bundle-v1.0.0`
- [x] `POST /v1/score` (fast mode), `POST /v1/jobs` + `GET /v1/jobs/{id}/stream` (precise mode,
      SSE), `GET` / `DELETE /v1/jobs/{id}` (ADR 0031): one job at a time, queue of 4 then 429,
      results kept 1 h; precise limit 600 residues, provisional until the Space is measured
- [x] `/metrics` (ADR 0032): latency by route, job queue length, cache hit rate, inference time;
      bearer token (constant-time), 404 without one, not in the OpenAPI schema
- [x] Validation: 20 canonical AAs, length 1–1022, ≤ 50 mutations per request (ADR 0031;
      every bad mutation reported with its own `loc`)
- [x] diskcache (ADR 0031: model + revision + method + sequence hash; identical requests and jobs
      are answered from it)
- [x] slowapi rate limiting, CORS to configured origins, structlog JSON, Prometheus metrics, Sentry
      (ADR 0032): per-client limits by trusted-proxy client address, global fast-mode limit, one
      active job per client, stream slots, 413 body limit, request ids, no sequence in any log,
      one process enforced (`python -m api.app.serve` + lock); Locust run in `docs/load-test.md`,
      threats in `docs/threat-model.md`
- [x] Tests: httpx AsyncClient, golden values vs pipeline outputs, schemathesis (ADR 0031: live
      fp32 vs our PyTorch landscapes of TCRG1, Spearman ≥ 0.99; schemathesis on a real server
      over all 11 operations)

### Docker and CI/CD
- [x] Multi-stage `python:3.11-slim`, uv, CPU-only wheels, non-root, HEALTHCHECK, port 7860, pinned HF revisions
      (ADR 0033): API image without torch (`pipeline` extra split out), bundle and fp32 ONNX
      baked in at the pinned commits and checksum-verified, offline, UID 1000, one process,
      self-check as the last build step; pipeline image with CPU torch and Foldseek;
      `-profile docker,test` runs all 20 tasks; `.env.example`, `docker-compose.yml`
- [x] Build linux/amd64 (buildx or CI): both images built with buildx for linux/amd64 and
      linux/arm64 (API 1,124 / 1,138 MiB unpacked, 600 / 597 MB compressed); CI builds and pushes in the CI task
- [x] CI: lint → typecheck → tests + coverage → Nextflow `docker,test` smoke → push to GHCR + Artifact Registry → deploy to Cloud Run on tags
      (ADR 0034): five required checks on every PR (both images built for linux/amd64, API
      container tests), actions pinned by exact tag; on a tag, image to Artifact Registry + GHCR,
      deployed to Google Cloud Run by digest (Docker Spaces need a paid plan), keyless
      (Workload Identity Federation); deploy fails unless the live `/ready` passes
- [x] Dependabot (`uv`, Actions, Docker), release-please (version in `pyproject.toml` + `uv.lock`, `CHANGELOG.md`)
- [x] Branch protection: the `main` ruleset requires the five checks and up-to-date branches (set by the author)
- [x] First release deployed to Cloud Run (`v0.2.0`); live service measured (ADR 0034 section 6,
      `docs/load-test.md` section 6): fast-mode p95 at 1,000 residues 2.09 s (35M) / 5.72 s
      (150M) → default 35M; precise 400 residues 163 s (600: 406 s) → limit 400; cold start
      20.5 s; `trusted_proxies` = Cloud Run's front end
- [ ] Bound concurrent forward passes: at 5 users reads wait behind scoring (p99 9.9 s) and Cloud
      Run refused 2 of 226 requests (ADR 0033, 0034)
- [x] Phase 3 in Nextflow (ADR 0027): `FOLDS → EMBED → FEATURES_BUILD → TRAIN_CV (ridge seq_struct)
      → TRAIN_CALIBRATE → TRAIN_FINAL → CHECK_RELEASE`; `train check-release` publishes only a
      bit-identical rerun (provenance stamps aside); LightGBM / MLP behind `params.extra_models`
      (off); test profile runs the ridge path on its 2 assays (55 s). `train compare`,
      `final-table`, `final-figures`, `canary`, `shap`, `uncertainty` stay by hand: they read
      results files or are diagnostics
- [x] `openapi-typescript` type generation for the frontend (`make types` →
      `web/src/lib/api-types.ts`, openapi-typescript 7.13.0 pinned in `web/`; CI job `types`
      fails when the committed file is stale; ADR 0030)

Done when: CI green on every push, tags deploy automatically, `/docs` live, fast-mode response < 2 s.

Closed 2026-10-18 (ADR 0035): tags deploy automatically (`v0.2.0`, and a manual redeploy) and
`/docs` is live at https://mutascope-api-2lvmztrsha-ew.a.run.app. CI on main has been green since
`9533655`, after one red merge (`9de9434`, a test-order bug fixed by #39). Fast mode meets 2 s at
300 residues (35M p95 0.92 s) but not at 1,000 (2.09 s) or under 5 users (10 s). Open: bounding
concurrent forward passes. Weak points carried forward: no metrics token or Sentry DSN on the
live service, GHCR image probably private, trusted proxy not yet checked in the logs, memory not
measured at 20 users on 4 GiB.

---

## Phase 5 — Weeks 8–10: Frontend, launch

### Carried from Phase 4 (ADR 0035)
- [x] Bound concurrent forward passes (ADR 0036: one pass at a time, 503 `scoring-busy` after
      10 s); measure 5 users live again (ADR 0036, section 6: read p99 8.5 s, not fixed)
- [x] Reads stay slow on Cloud Run under load although the pool holds (ADR 0036, section 6):
      fixed by one ONNX thread (section 7): read p99 at 5 users 140 ms (was 8,500 ms)
- [ ] Precise job at 400 residues takes 293 s live with one ONNX thread (ADR 0036, section 7)
      against the ~3 min target: measure at a lower `PRECISE_MAX_LENGTH` (about 250–300) and set it
- [ ] Optional, for heavier load: profile the event loop, move gzip / ETag of large bodies off it
      (ADR 0036, section 6, experiments 2 and 3)
- [x] Live observability: `metrics_token` from Secret Manager (ADR 0036); `/metrics` 401 / 200
      checked live
- [ ] Sentry: create a project, add `MUTASCOPE_API_SENTRY_DSN` under `secrets` again (removed in
      PR #46: the secret was not a DSN and stopped the API at startup)
- [ ] Make `ghcr.io/engabdullahhanif/mutascope-api` public (package settings), or stop calling it
      the public copy
- [ ] Check in Cloud Logging that access lines show visitors' addresses (trusted proxy, ADR 0034)
- [x] Add the frontend's origin to `MUTASCOPE_API_CORS_ORIGINS` in `conf/deploy/cloudrun.yaml`
      (`mutascope.abdullahhanif.me`, `mutascope-web.vercel.app`; live with the next deploy)

### Frontend

- [x] Foundation (Task 1, ADR 0037): Next.js (App Router) + TypeScript + Tailwind + shadcn/ui,
      bundle downloaded at build and checksum-verified, generated Zod validators, design tokens
      (`web/DESIGN.md`), layout and placeholder pages, CI `web` job
- [x] Add `web (lint, typecheck, unit tests, build)` to the `main` ruleset's required checks;
      gzip serving of protein files checked on production (build log 39)
- [x] Explorer page (Task 2, ADR 0038): `/explore` picker (name, UniProt, function type, label),
      `/explore/[assayId]` for all 217 assays
- [x] Heatmap (Task 2, ADR 0038): canvas, colourblind-safe diverging scale, model switch with
      labels, predicted / measured, unmeasured mask, mean bar, tooltip, top 10, CSV, keyboard,
      table alternative; the 5 MSA-region proteins labelled (ADR 0012); 1,000 residues drawn in
      ~30 ms
- [x] Link the heatmap to the 3D structure (Task 3, ADR 0039): bundle `bundle-v1.1.0` (format
      1.1.0) with 185 backbone structure files for 213 assays, every residue checked against its
      target; 3Dmol.js loaded after the heatmap, coloured by mean effect or pLDDT; hover linked
      both ways, click in 3D selects the column; no-structure message and accessibility note
- [x] Live scoring page `/score` (Task 4, ADR 0040): fast mode (full landscape) and precise mode
      (job over fetch-streamed SSE, filled batch by batch, reconnect with `Last-Event-ID`,
      cancel), precise limit read from `/ready`, "Waking up the server…" on a cold start, friendly
      429 / 503 with `Retry-After`, validation at the input; Playwright with a mocked API plus an
      opt-in live smoke test
- [ ] Pages: Landing, Explorer, Benchmarks, How it works (pipeline, model card, limitations), About
- [ ] Deploy frontend (Vercel / Cloudflare Pages) on a subdomain
- [ ] README, 2-minute demo video, write-up

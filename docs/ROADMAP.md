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
- [ ] Profiles: `local` (uv env, MPS), `docker` (CPU, amd64), `test` (2 small assays).
      `local` and `test` done; `docker` waits for the Phase 4 image

### Outputs
- [x] marimo EDA notebook (assay sizes, function types, score distributions, sequence lengths)
- [x] Dataset card (`docs/DATA_CARD.md`)
- [ ] Processed dataset published to the Hugging Face Hub (`mutascope data publish` stages it;
      `--push` waits for the card review, ADR 0008)

Done when: `make pipeline` produces clean Parquet + structure features + 3Di; `make lint test` green; data card written.

---

## Phase 2 — Weeks 3–4: Zero-shot model zoo and benchmark harness

Goal: several pretrained models score every mutation; our numbers reproduce the ProteinGym leaderboard.

### Scorers
- [ ] `Scorer` protocol (`scoring/base.py`) returning `(L, 20)` landscapes in `AA_ORDER`; registry
- [ ] ESM-2 35M / 150M / 650M via Hugging Face `transformers`, pinned revisions
- [ ] SaProt 650M (AF2): combined AA + 3Di tokens, `#` where pLDDT < 70; scoring ported from the official repo
- [ ] [stretch] ESM-1v ensemble; ESM C 300M (check licence first)

### Scoring methods
- [ ] Masked marginal: one masked pass per position, batched 16–32, `torch.inference_mode()`, float32 on MPS
- [ ] Wild-type marginal (`fast=True`): one unmasked pass
- [ ] Sliding windows for L > 1022; each position from its most central window
- [ ] Disk cache keyed by `sha256(model_id + revision + sequence + struct_tokens)`
- [ ] Ensemble: per-assay z-score, then mean

### Benchmark harness
- [ ] Metrics per assay: Spearman, AUC, MCC, NDCG@10%, top-10% recall
- [ ] ProteinGym aggregation: per UniProt → per function type → overall
- [ ] Bootstrap 95% CIs over assays
- [ ] Reproduction gate: ESM-2 650M average Spearman within ±0.01 of ProteinGym's published value
- [ ] Results in `results/*.parquet` (long format); MLflow or W&B tracking
- [ ] Figures: model size vs Spearman, per-function-type bars, ensemble gain, fast vs masked marginal

Done when: full landscapes for all assays from ESM-2 (3 sizes) + SaProt; reproduction test passes; figures generated.

---

## Phase 3 — Weeks 5–6: Supervised models

Goal: beat zero-shot using the assay data, under ProteinGym's supervised protocol.

### Protocol
- [ ] Per-assay models, ProteinGym fold assignments, random / modulo / contiguous 5-fold
- [ ] Optuna hyperparameter search inside training folds only (nested CV); scalers fit on train folds only

### Features (variant at position i, wt → mt)
- [ ] Zero-shot scores (each model + ensemble)
- [ ] One-hot wt and mt, BLOSUM62
- [ ] 20-dim log-probabilities at i (ESM-2, SaProt)
- [ ] Position-i embeddings: ESM-2 650M (mean of last 4 layers, 1280-d) and SaProt
- [ ] Structure: pLDDT, relative SASA, contact count; relative position

### Model ladder
- [ ] Augmented ridge (one-hot + zero-shot score; Hsu et al., 2022)
- [ ] LightGBM on all features + SHAP
- [ ] MLP head (Lightning): LayerNorm → 512 → GELU → Dropout 0.2 → 128 → GELU → 1; AdamW, early stopping;
      compare MSE vs ranking-aware loss
- [ ] [stretch] LoRA fine-tuning of ESM-2 150M on Kaggle T4 (`peft`)
- [ ] [stretch] Leave-protein-out global model

### Uncertainty
- [ ] Fold-ensemble spread
- [ ] Conformal intervals (MAPIE) + calibration plot

Done when: Spearman table (3 splits × 4 rungs vs zero-shot, bootstrap CIs), SHAP figure, calibration plot, model cards on the HF Hub.

---

## Phase 4 — Week 7: API, Docker, CI/CD

Goal: a tested, observable service deployed automatically.

### Export
- [ ] Precomputed landscapes for all ProteinGym proteins → compressed JSON/Arrow (served statically by the frontend)
- [ ] ESM-2 35M/150M → ONNX (`optimum`) → int8 dynamic quantization → onnxruntime CPU

### FastAPI
- [ ] Pydantic v2, pydantic-settings, models loaded in `lifespan`
- [ ] `/v1/proteins`, `/v1/proteins/{id}/landscape`, `POST /v1/score` (fast mode),
      `POST /v1/jobs` + `GET /v1/jobs/{id}/stream` (precise mode, SSE), `/v1/benchmarks`, `/health`, `/metrics`
- [ ] Validation: 20 canonical AAs, length 1–1022, ≤ 50 mutations per request
- [ ] diskcache, slowapi rate limiting, CORS to configured origins, structlog JSON, Prometheus metrics, Sentry
- [ ] Tests: httpx AsyncClient, golden values vs pipeline outputs, schemathesis

### Docker and CI/CD
- [ ] Multi-stage `python:3.11-slim`, uv, CPU-only wheels, non-root, HEALTHCHECK, port 7860, pinned HF revisions
- [ ] Build linux/amd64 (buildx or CI)
- [ ] CI: lint → typecheck → tests + coverage → Nextflow `docker,test` smoke → push to GHCR → deploy to HF Space on tags
- [ ] Dependabot, release-please
- [ ] `openapi-typescript` type generation for the frontend

Done when: CI green on every push, tags deploy automatically, `/docs` live, fast-mode response < 2 s.

---

## Phase 5 — Weeks 8–10: Frontend, launch

- [ ] Next.js (App Router) + TypeScript + Tailwind + shadcn/ui; Explorer page first
- [ ] Heatmap (D3/visx, colourblind-safe diverging) linked to 3D structure (3Dmol.js)
- [ ] Pages: Landing, Explorer, Benchmarks, How it works (pipeline, model card, limitations), About
- [ ] Deploy frontend (Vercel / Cloudflare Pages) on a subdomain
- [ ] README, 2-minute demo video, write-up

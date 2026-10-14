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
- [ ] Optuna hyperparameter search inside training folds only (nested CV); scalers fit on train folds only
      (ridge: α by inner CV over the training folds, split like the outer scheme; scaling on the
      fit rows only — ADR 0021)

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
- [ ] Precomputed landscapes for all ProteinGym proteins → compressed JSON/Arrow (served statically by the frontend).
      Reads the full landscapes from the cache (ADR 0018); run `mutascope score check-landscapes` first
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
- [ ] Heatmap (D3/visx, colourblind-safe diverging) linked to 3D structure (3Dmol.js); label the
      5 proteins whose benchmark scores use the MSA region (ADR 0012)
- [ ] Pages: Landing, Explorer, Benchmarks, How it works (pipeline, model card, limitations), About
- [ ] Deploy frontend (Vercel / Cloudflare Pages) on a subdomain
- [ ] README, 2-minute demo video, write-up

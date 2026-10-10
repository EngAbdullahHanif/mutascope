# CLAUDE.md — MutaScope

Instructions for Claude Code working in this repository. Read this fully before making changes.

## What this project is

MutaScope predicts the functional effect of every single amino-acid substitution in a protein and
presents it as an interactive mutation landscape (heatmap + 3D structure) with an honest benchmark.

- **Data:** ProteinGym DMS substitution benchmark (single mutants only in v1).
- **Zero-shot models:** ESM-2 (35M / 150M / 650M), SaProt 650M (AF2, structure-aware via Foldseek 3Di tokens). Stretch: ESM-1v, ESM C 300M.
- **Supervised models:** per-assay models following the ProteinGym supervised protocol (random / modulo / contiguous 5-fold CV): augmented ridge → LightGBM → MLP head. Uncertainty via fold ensembles + conformal prediction (MAPIE).
- **Serving:** FastAPI backend (ONNX-quantized ESM-2 35M/150M for live scoring), Next.js frontend; precomputed landscapes for all ProteinGym proteins served statically.
- **Pipeline:** Nextflow DSL2 orchestrating the `mutascope` Python CLI.

This is a public portfolio project. Code quality, reproducibility and honest evaluation matter more than
squeezing out leaderboard points. It must not contain any thesis code or data.

## Repository layout

```
mutascope/
├── CLAUDE.md
├── pyproject.toml            # uv-managed; single source of Python deps
├── Makefile                  # canonical commands (see below)
├── conf/                     # Hydra configs (data/, model/, train/, eval/)
├── src/mutascope/
│   ├── cli.py                # Typer entry point: `mutascope <group> <cmd>`
│   ├── data/                 # download, parse, validate (pandera), clean, structures, 3Di
│   ├── scoring/              # Scorer protocol, registry, esm2.py, saprot.py, ensemble.py, cache.py
│   ├── features/             # per-variant feature builders
│   ├── models/               # ridge, lgbm, mlp (Lightning), conformal, ensemble
│   ├── eval/                 # metrics, ProteinGym-style aggregation, bootstrap CIs, figures
│   ├── export/               # landscapes → JSON/Arrow, ONNX export + quantization
│   └── utils/                # hashing, io, logging, seeding
├── pipeline/                 # Nextflow: main.nf, modules/, nextflow.config (profiles: local, docker, test)
├── api/                      # FastAPI app (app/, tests/), Dockerfile
├── web/                      # Next.js + TypeScript + Tailwind + shadcn/ui
├── notebooks/                # marimo notebooks — exploration only
├── tests/                    # pytest for src/mutascope
├── docs/adr/                 # architecture decision records
└── data/                     # gitignored: raw/, interim/, processed/, cache/
```

## Commands

Always use these; do not invent alternatives.

```bash
make setup        # uv sync --all-extras && pre-commit install
make lint         # ruff check . && ruff format --check . && pyright
make fmt          # ruff format . && ruff check --fix .
make test         # pytest -q (uses esm2_t6_8M; must stay fast, < 2 min)
make test-slow    # pytest -m slow (real 650M models, MPS; run locally only)
make pipeline     # nextflow run pipeline/main.nf -profile local
make pipeline-test# nextflow run pipeline/main.nf -profile local,test (docker,test once the image exists, Phase 4)
make api          # uvicorn api.app.main:app --reload --port 7860
make web          # cd web && pnpm dev
make types        # regenerate web/src/lib/api-types.ts from OpenAPI (openapi-typescript)
```

Run `make lint test` before declaring any task done.

## Environment facts

- Dev machine: Apple M4 Pro, 24 GB RAM. Use `torch.device("mps")` when available, else CPU.
  Set `PYTORCH_ENABLE_MPS_FALLBACK=1`. Use float32 (no fp16/bf16 on MPS).
- Docker on macOS cannot access MPS. GPU/MPS work runs in `-profile local` (uv env); Docker is CPU-only.
- Deployment target (Hugging Face Spaces) is linux/amd64. Build images with
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
- Sequences > 1022 residues: overlapping windows; each position is scored from the window where it is most central.
- SaProt: combined tokens (uppercase AA + lowercase 3Di); 3Di replaced by `#` where pLDDT < 70.
  Port the scoring logic from the official SaProt repository and cite it in the docstring.
- Cache logits on disk keyed by `sha256(model_id + revision + sequence + struct_tokens)`.
- Pin every Hugging Face model by `revision` in config.

## Evaluation rules (non-negotiable)

- Metrics per assay: Spearman (primary), AUC, MCC, NDCG@10%, top-10% recall.
- Aggregate ProteinGym-style: per UniProt ID → per function type → overall. Never report a plain mean over assays as the headline.
- 95% CIs by bootstrap over assays.
- **Reproduction gate:** our ESM-2 650M zero-shot average Spearman must match ProteinGym's published value
  within ±0.01 (`tests/test_reproduction.py`, marked `slow`). If it fails, fix the bug before any new modelling work.
- Supervised: use ProteinGym's fold assignments for random, modulo and contiguous splits. Report all three.
- **No leakage:** scalers, feature selection and hyperparameter search (Optuna) are fit on training folds only
  (nested CV). Test folds are touched exactly once per final evaluation.
- Results go to `results/*.parquet` in long format (`model, assay_id, split, fold, metric, value`);
  figures are generated from these files only.
- Never present zero-shot predictions as supervised, or either as clinical. Variants are "functional effect in DMS assays", not "pathogenic".

## API (`api/`)

- FastAPI + Pydantic v2 + pydantic-settings. Models loaded once in the `lifespan` hook.
- Versioned routes under `/v1`: `proteins`, `proteins/{id}/landscape`, `score` (fast mode),
  `jobs` + `jobs/{id}/stream` (precise mode via Server-Sent Events), `benchmarks`; plus `/health`, `/metrics`.
- Input validation: only canonical 20 amino acids, length 1–1022, max 50 mutations per `score` request.
- Live inference uses ONNX Runtime (int8-quantized ESM-2 35M/150M). SaProt is not served live.
- Caching with `diskcache`; rate limiting with `slowapi`; CORS restricted to configured origins.
- Logs: structlog JSON. Metrics: prometheus-fastapi-instrumentator.
- Tests: pytest + httpx AsyncClient, golden-value tests against pipeline outputs, schemathesis against OpenAPI.
- After changing any response schema, run `make types` and commit the regenerated TypeScript types.

## Docker

- Multi-stage, `python:3.11-slim`, uv, CPU-only torch/onnxruntime wheels, non-root user, `HEALTHCHECK`, port 7860.
- Model weights fetched from the Hugging Face Hub at pinned revisions.
- Never copy `data/` or secrets into images.

## Frontend (`web/`)

- Next.js (App Router) + TypeScript strict + Tailwind + shadcn/ui; 3Dmol.js for structures; D3/visx for the heatmap.
- API types come only from the generated `api-types.ts`; never hand-write response types.
- Precomputed ProteinGym landscapes are static assets; the site must work with the backend asleep.
- Heatmap colour scale: colourblind-safe diverging (blue–white–red), symmetric around 0.
- Linked views: hovering a heatmap column highlights the residue in 3D and vice versa.
- Always label results as "Supervised (n = …)" or "Zero-shot".

## CI (GitHub Actions)

lint → typecheck → tests (+ coverage) → Nextflow `-profile local,test` smoke run (`docker,test` from Phase 4, ADR 0007) → build & push image to GHCR
(amd64) → deploy to Hugging Face Space on version tags. CI must stay green on `main`.

## How to work in this repo

- For any non-trivial task: propose a short plan first, then implement.
- Write or update tests before or alongside the code. Bug fix → add a regression test first.
- Keep changes small and focused; one concern per commit. Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`…).
- When a design decision is made (tool, model, protocol change), add an ADR in `docs/adr/NNNN-title.md`.
- If a metric looks surprisingly good, assume leakage or an indexing bug and investigate before reporting it.
- Do not modify `conf/data/proteingym.yaml` pins, the reproduction test tolerance or the evaluation
  protocol without explicitly asking.
- Explain non-obvious code in plain language in the PR/commit description — the author must be able to defend every line in an interview.

## Current phase

Weeks 3–4: zero-shot model zoo and benchmark harness (Phase 2). Phase 1 delivered a pinned,
checksum-verified ProteinGym v1.3 pipeline producing 696,311 validated single mutants across 217 assays,
structure features and 3Di for 213 of them, an EDA notebook and a data card (ADR 0009).
Update this section at the start of each phase.
The per-phase task checklist lives in `docs/ROADMAP.md`; mark items `[x]` there as they land.

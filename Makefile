# Canonical commands — see CLAUDE.md "Commands". Python tools run inside the uv env via `uv run`.

export PYTORCH_ENABLE_MPS_FALLBACK := 1

.PHONY: setup lint fmt test test-slow pipeline pipeline-test mlflow api web types

setup:
	uv sync --all-extras
	uv run pre-commit install

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright

fmt:
	uv run ruff format .
	uv run ruff check --fix .

# Extra pytest flags, e.g. CI's coverage: make test PYTEST_ARGS="--cov=mutascope"
PYTEST_ARGS ?=

test:
	uv run pytest -q $(PYTEST_ARGS)

test-slow:
	uv run pytest -m slow

pipeline:
	nextflow run pipeline/main.nf -profile local

# Native uv env until the Docker image exists (Phase 4); see docs/adr/0007. The smoke test's
# log-probability cache is deleted first, so every run scores with the model (ADR 0018).
pipeline-test:
	rm -rf pipeline/results/logprob_cache
	nextflow run pipeline/main.nf -profile local,test

# Local MLflow UI over the tracking store (conf/tracking; ADR 0014). 5001: macOS uses 5000.
mlflow:
	uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db --port 5001

api:
	uv run uvicorn api.app.main:app --reload --port 7860

web: require-web
	cd web && pnpm dev

types: require-web
	uv run python -m api.app.openapi web/openapi.json
	cd web && pnpm exec openapi-typescript openapi.json -o src/lib/api-types.ts

.PHONY: require-web
require-web:
	@test -d web || { echo "web/ is not scaffolded yet (Phase 5)." >&2; exit 1; }

# Canonical commands — see CLAUDE.md "Commands". Python tools run inside the uv env via `uv run`.

export PYTORCH_ENABLE_MPS_FALLBACK := 1

.PHONY: setup lint fmt test test-slow pipeline pipeline-test api web types

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

test:
	uv run pytest -q

test-slow:
	uv run pytest -m slow

pipeline:
	nextflow run pipeline/main.nf -profile local

pipeline-test:
	nextflow run pipeline/main.nf -profile docker,test

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

# Canonical commands — see CLAUDE.md "Commands". Python tools run inside the uv env via `uv run`.

export PYTORCH_ENABLE_MPS_FALLBACK := 1

.PHONY: setup lint fmt test test-slow pipeline pipeline-test pipeline-test-docker mlflow api web types
.PHONY: api-image pipeline-image images test-container web-check web-e2e web-quality

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

# LightGBM tests run in a second process, from files that never import torch: on macOS the two
# libraries' OpenMP runtimes crash when loaded together (ADR 0022). Coverage appends across both.
LIGHTGBM_TESTS = tests/models/test_lgbm.py tests/models/test_cli_train.py
test:
	uv run pytest -q $(PYTEST_ARGS)
	uv run pytest -q -m "lightgbm and not slow" $(PYTEST_ARGS) \
		$(if $(findstring --cov,$(PYTEST_ARGS)),--cov-append) $(LIGHTGBM_TESTS)

test-slow:
	uv run pytest -m slow

pipeline:
	nextflow run pipeline/main.nf -profile local

# Native uv env until the Docker image exists (Phase 4); see docs/adr/0007. The smoke test's
# log-probability and embedding caches are deleted first, so every run uses the model (ADR 0018).
pipeline-test:
	rm -rf pipeline/results/logprob_cache pipeline/results/embedding_cache
	nextflow run pipeline/main.nf -profile local,test

# The same smoke test in the pipeline image (ADR 0033): CPU only, data_dir and the output and
# cache directories mounted at their host paths. Needs `make pipeline-image` first.
pipeline-test-docker:
	rm -rf pipeline/results/logprob_cache pipeline/results/embedding_cache
	nextflow run pipeline/main.nf -profile docker,test

# Images (ADR 0033), for this machine's architecture unless PLATFORM is set, e.g.
# make api-image PLATFORM=linux/amd64 (the Space's). The API image fetches and verifies the
# bundle and the fp32 ONNX files at the pinned revisions, then runs its self-check.
PLATFORM ?= linux/$(shell uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')
VCS_REF := $(shell git rev-parse --short HEAD)
api-image:
	docker buildx build -f api/Dockerfile --platform $(PLATFORM) --build-arg VCS_REF=$(VCS_REF) \
		-t mutascope-api:dev --load .
pipeline-image:
	docker buildx build -f pipeline/Dockerfile --platform $(PLATFORM) --build-arg VCS_REF=$(VCS_REF) \
		-t mutascope-pipeline:dev --load .
images: api-image pipeline-image

# The API image under the Space's limits (2 CPUs, 16 GB): health, readiness, one fast score and
# one precise job end to end, user, write paths, no torch. Needs `make api-image`.
test-container:
	uv run pytest -m container api/tests/test_container.py

# Local MLflow UI over the tracking store (conf/tracking; ADR 0014). 5001: macOS uses 5000.
mlflow:
	uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db --port 5001

# Development server (one process; --reload restarts it). Production runs `python -m api.app.serve`
# (ADR 0032). Like it, this leaves the client address to the API: uvicorn must not rewrite it
# from X-Forwarded-For, and the API writes its own JSON access line.
api:
	uv run uvicorn api.app.main:app --reload --port 7860 --no-proxy-headers --no-access-log

web: require-web
	cd web && pnpm dev

# What CI's web job runs: generated validators up to date, lint (ESLint + Prettier), typecheck,
# unit tests, then the build, which downloads the pinned bundle and verifies it (ADR 0037).
web-check: require-web
	cd web && pnpm schemas && git diff --exit-code src/lib/bundle/schemas.generated.ts
	cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm build

# Playwright against the production build (`make web-check` first; `pnpm exec playwright install
# chromium` once).
web-e2e: require-web
	cd web && pnpm test:e2e

# What CI's web-quality job runs after the build (`make web-check` first): the JavaScript size
# budget, Playwright (axe, 375 px, end-to-end), then Lighthouse, mobile and desktop, median of 3.
# Lighthouse needs Chrome; set CHROME_PATH (e.g. Playwright's Chromium) if it is not installed.
web-quality: require-web
	cd web && pnpm size && pnpm test:e2e
	cd web && pnpm lighthouse; m=$$?; \
	  pnpm lighthouse --collect.settings.preset=desktop --upload.outputDir=.lighthouseci/desktop; d=$$?; \
	  node scripts/lighthouse-summary.ts; test $$m -eq 0 && test $$d -eq 0

# API types for the frontend, from the OpenAPI schema; committed, and CI fails when they are stale.
# Needs `pnpm install` in web/ once (pinned openapi-typescript).
types: require-web
	uv run python -m api.app.openapi web/openapi.json
	cd web && pnpm exec openapi-typescript openapi.json -o src/lib/api-types.ts

.PHONY: require-web
require-web:
	@test -d web || { echo "web/ is missing." >&2; exit 1; }

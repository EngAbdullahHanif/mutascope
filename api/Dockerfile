# The API image (ADR 0033): FastAPI + fp32 ONNX ESM-2 35M / 150M, no torch, no transformers.
#
#   docker buildx build -f api/Dockerfile --platform linux/amd64 -t mutascope-api:dev --load .
#
# Build context: the repository root (.dockerignore keeps data, outputs and secrets out).
# Stages:
#   builder  the serving environment from uv.lock: the package with its `api` and `onnx` extras
#            only (the `pipeline` extra holds torch and transformers, and is not installed)
#   weights  the website bundle and the fp32 ONNX files at the pinned commits, checked against
#            the SHA256SUMS of those commits (python -m api.app.fetch)
#   runtime  python:3.11-slim, UID 1000 (Hugging Face Spaces runs containers as that user),
#            offline, one process; its last build step starts the API and scores with it
#            (python -m api.app.selfcheck), so an image that cannot serve is never produced.

# Base images are literal FROM lines (tag and digest), so Dependabot can read and bump them.

FROM ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a AS uv

# --- builder: /opt/venv ------------------------------------------------------------------------
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
# Dependencies first (cached until the lock changes), then the package itself, not editable.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project --extra api --extra onnx
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra api --extra onnx \
    && ! /opt/venv/bin/python -c "import torch" 2>/dev/null \
    && ! /opt/venv/bin/python -c "import transformers" 2>/dev/null

# --- weights: /opt/mutascope/hf ----------------------------------------------------------------
# Only the modules the fetch imports, so a change elsewhere in the API does not download
# the 760 MB again.
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS weights
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY conf ./conf
COPY api/__init__.py ./api/
COPY api/app/__init__.py api/app/fetch.py api/app/bundle.py api/app/scorers.py \
     api/app/settings.py api/app/logs.py ./api/app/
ENV PATH=/opt/venv/bin:$PATH \
    HF_HOME=/opt/mutascope/hf \
    HF_HUB_DISABLE_TELEMETRY=1
RUN python -m api.app.fetch \
    && rm -rf /opt/mutascope/hf/xet /opt/mutascope/hf/hub/.locks

# --- runtime -----------------------------------------------------------------------------------
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="mutascope-api" \
      org.opencontainers.image.description="MutaScope API: precomputed landscapes and live zero-shot ESM-2 scoring (ONNX, CPU)" \
      org.opencontainers.image.source="https://github.com/EngAbdullahHanif/mutascope" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${VCS_REF}"

# The base image's own pip, setuptools and wheel are not used (the server runs from /opt/venv,
# which does not see the system site-packages) and carry CVEs in setuptools' vendored copies:
# removed. Every directory on the write paths is the user's (`install -d -o` sets only the last).
RUN python -m pip uninstall --yes --no-cache-dir pip setuptools wheel \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin user \
    && install -d -o user -g user -m 755 \
       /home/user/.cache /home/user/.cache/mutascope /home/user/.cache/mutascope/scores

# Code and files are root-owned: the server's user can read them, not change them.
COPY --from=builder /opt/venv /opt/venv
COPY --from=weights /opt/mutascope/hf /opt/mutascope/hf
WORKDIR /app
COPY conf ./conf
COPY api/__init__.py ./api/
COPY api/app ./api/app

# Offline: everything served was fetched and verified at build time, at the pinned commits.
# BLAS/OpenMP at one thread (ADR 0026's determinism rule); ONNX Runtime's own thread pool is
# MUTASCOPE_API_ONNX_THREADS (default 2). The two write paths are the user's.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/user \
    HF_HOME=/opt/mutascope/hf \
    HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    BLIS_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    MUTASCOPE_API_CONFIG_DIR=/app/conf \
    MUTASCOPE_API_SCORE_CACHE_DIR=/home/user/.cache/mutascope/scores \
    MUTASCOPE_API_LOCK_FILE=/home/user/.cache/mutascope/api.lock

USER 1000:1000

# The shipped image starts from its own files, offline, as its user, on its real write paths,
# scores with both models and has loaded neither torch nor transformers (the check of
# api/tests/test_no_torch.py). What it wrote there is removed in the same layer.
RUN python -m api.app.selfcheck \
    && rm -rf "$MUTASCOPE_API_LOCK_FILE" "$MUTASCOPE_API_SCORE_CACHE_DIR"/*

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --start-interval=5s --retries=3 \
    CMD ["python", "-m", "api.app.healthcheck"]
# One process, one worker (ADR 0032): the app object is handed to uvicorn, WEB_CONCURRENCY > 1
# is refused, and the lifespan holds MUTASCOPE_API_LOCK_FILE.
CMD ["python", "-m", "api.app.serve"]

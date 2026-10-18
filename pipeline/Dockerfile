# The pipeline image (ADR 0033): the `mutascope` CLI with CPU-only PyTorch and the pinned
# Foldseek build, for Nextflow's `docker` profile. Separate from the API image, which has no torch.
#
#   docker buildx build -f pipeline/Dockerfile --platform linux/arm64 -t mutascope-pipeline:dev --load .
#   make pipeline-docker-test     # nextflow run pipeline/main.nf -profile docker,test
#
# Build context: the repository root. No data: Nextflow mounts data_dir and the output and
# cache directories at run time (pipeline/nextflow.config, profile `docker`).

# Base images are literal FROM lines (tag and digest), so Dependabot can read and bump them.
# Set by buildx for each --platform; picks the Foldseek build below.
ARG TARGETARCH

FROM ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a AS uv

# --- Foldseek 10-941cd33 (ADR 0006), one build per architecture, checksum-verified -------------
# amd64: the linux-avx2 build CI uses (same sha256). arm64: the release's linux-arm64 build.
# `mutascope data foldseek-3di` refuses any binary whose `foldseek version` is not the pin.
FROM scratch AS foldseek-amd64
ADD --checksum=sha256:e6a3e3860e76b0b7b858736f627fe680d4d7f0fc7298301d8a92b9f711d74ac7 \
    https://github.com/steineggerlab/foldseek/releases/download/10-941cd33/foldseek-linux-avx2.tar.gz \
    /foldseek.tar.gz

FROM scratch AS foldseek-arm64
ADD --checksum=sha256:c71907a7ea06cb6e93d8eb000b0c3689e5e25f5edeb3969e2553cdb8f42982c1 \
    https://github.com/steineggerlab/foldseek/releases/download/10-941cd33/foldseek-linux-arm64.tar.gz \
    /foldseek.tar.gz

FROM foldseek-${TARGETARCH} AS foldseek

# --- builder: /opt/venv ------------------------------------------------------------------------
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS builder
# A compiler for this stage only: biotraj 1.2.2 (biotite's dependency) has no linux/arm64 wheel
# and is built from source there. The runtime stage has no compiler.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
# `pipeline` (torch from the CPU index on Linux, per uv.lock) and `tracking` (MLflow: every
# scoring and training step logs a run). No api, onnx, notebooks or figures.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-project --extra pipeline --extra tracking
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra pipeline --extra tracking

# --- runtime -----------------------------------------------------------------------------------
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="mutascope-pipeline" \
      org.opencontainers.image.description="MutaScope pipeline CLI (CPU-only PyTorch, Foldseek) for Nextflow's docker profile" \
      org.opencontainers.image.source="https://github.com/EngAbdullahHanif/mutascope" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${VCS_REF}"

# procps: Nextflow reads task resource usage with `ps` inside the container. The base image's
# pip, setuptools and wheel are unused (tasks run /opt/venv) and carry CVEs: removed.
RUN python -m pip uninstall --yes --no-cache-dir pip setuptools wheel \
    && apt-get update \
    && apt-get install --yes --no-install-recommends procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin user
RUN --mount=type=bind,from=foldseek,target=/fs \
    tar xzf /fs/foldseek.tar.gz -C /tmp \
    && install -m 755 /tmp/foldseek/bin/foldseek /usr/local/bin/foldseek \
    && rm -rf /tmp/foldseek
COPY --from=builder /opt/venv /opt/venv
# The configs this code was built with: the docker profile points params.conf_dir here.
COPY conf /opt/mutascope/conf

# Nextflow runs tasks as the host user (-u uid:gid), who has no home in the image: caches go
# to /tmp unless the profile sets them (HF_HOME is under data_dir). BLAS threads are capped in
# code (utils.parallel, ADR 0026), not here: torch scoring uses the cores it is given.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib \
    HF_HUB_DISABLE_TELEMETRY=1

USER 1000:1000
# CPU-only torch, the pinned Foldseek build, and a CLI that loads.
RUN python -c "import torch; assert torch.version.cuda is None, torch.version.cuda" \
    && foldseek version | grep -qx 941cd33ff0771cd2e3f144e3293e22a2b87e9fda \
    && mutascope --help > /dev/null

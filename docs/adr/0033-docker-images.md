# 0033 — Docker images: a torch-free API image with baked-in, verified files, and a pipeline image

- Status: accepted
- Date: 2026-10-18
- Builds on: ADR 0007 (Nextflow profiles), ADR 0026 (determinism, pinned Hub revisions), ADR 0027
  (Phase 3 in Nextflow), ADR 0029 (fp32 ONNX, image-size target), ADR 0030 (settings as pins),
  ADR 0031 (live scoring), ADR 0032 (hardening, one process)

## Context

The API is to run on a free Hugging Face Space: a linux/amd64 container with 2 vCPUs and 16 GB,
run as UID 1000, rebuilt from a Dockerfile. It must not contain torch or transformers (ADR
0029), must serve exactly the pinned bundle and fp32 ONNX files (ADR 0030), and must run as one
process (ADR 0032). The pipeline needs a second image, with CPU torch and Foldseek, for
Nextflow's `docker` profile (ADR 0007), so that CI can run the smoke test in a container. Until
now `torch` and `transformers` were core dependencies of the package, so any install of
`mutascope` pulled them in.

## Decision

### 1. The package's dependencies are split: a light base and a `pipeline` extra

The core `dependencies` are now what the API imports from the package: `httpx`,
`huggingface-hub`, `hydra-core`, `numpy` (now explicit; it was a transitive dependency), `pydantic`
and `structlog`. Everything only the pipeline and the CLI import (biotite, duckdb, lightgbm,
optuna, pandas, pandera, scikit-learn, scipy, threadpoolctl, torch, transformers, typer) is the new
`pipeline` extra. `uv lock` changed metadata only. No version moved: the lock's packages and
pins are the same, and `make setup` (`--all-extras`) installs what it did before.

The API image installs `--extra api --extra onnx` from the lock: 54 packages, no torch, no
transformers, no pandas. The list of what the API loads was measured, not guessed: after
importing the app, the start command and a scorer config, `sys.modules` held no module of the
`pipeline` extra.

Alternative not taken: keep the dependencies and skip packages at install time
(`uv sync --no-install-package torch …`). uv still installs their own dependencies (sympy,
networkx, …), the list must name every heavy package by hand, and nothing stops a new one from
slipping in. With the extra, the lock decides, and a test (below) fails if torch appears.

### 2. The API image (`api/Dockerfile`)

Three stages on `python:3.11.17-slim-trixie` and uv 0.12.24, both pinned by tag and digest:

1. **builder:** `uv sync --frozen --no-dev --no-editable --extra api --extra onnx` into
   `/opt/venv` (dependencies first, so a code change does not reinstall them). The step fails if
   `import torch` or `import transformers` succeeds.
2. **weights:** `python -m api.app.fetch` (new) downloads, at the pinned commits:
   - the bundle (`open_bundle`: all 223 files in its `SHA256SUMS`, every protein file in the
     manifest, the schema version: the same checks as at startup);
   - each served model's fp32 graph and `tokenizer.json`, from the served configs (only fp32 can
     pass `model_config`), each checked against the ONNX repo's `SHA256SUMS` of commit
     `82e1528`. A file missing from the list fails like a wrong digest.

   Both lists are read from the pinned commits, so the commit hash fixes the files and the
   digests. This stage copies only the modules the fetch imports, so changing other API code
   does not download the 760 MB again.
3. **runtime:**
   - **User and permissions.** UID 1000 (`user`). Code, venv and weights are root-owned and
     read-only to that user. Its only write paths are the settings `score_cache_dir` and
     `lock_file`, under `/home/user/.cache/mutascope`, each directory owned by the user.
   - **Offline.** `HF_HUB_OFFLINE=1`: startup finds the bundle and the models in the baked-in
     cache by the same revisions and never reaches the network.
   - **Threads.** `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
     `BLIS_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS` and `NUMEXPR_NUM_THREADS` are 1 (ADR 0026).
     ONNX Runtime's intra-op pool is the setting `onnx_threads` (2).
   - **Start, port and health.** `CMD python -m api.app.serve` (one process, ADR 0032), port
     7860. `HEALTHCHECK` runs `python -m api.app.healthcheck` (standard library, reads
     `MUTASCOPE_API_PORT`) against `/health` every 30 s, with a 120 s start period probed every
     5 s.
   - **Removed.** The base image's own pip, setuptools and wheel: unused, and Trivy flags
     setuptools' vendored `jaraco.context` 5.3.0 and `wheel` 0.45.1 (HIGH, both fixed upstream).

**The last build step is the self-check.** `python -m api.app.selfcheck` (new) starts the app
in-process, as the image's user, offline, on the real write paths. It runs the whole lifespan,
then `/ready`, one bundle landscape and a fast ubiquitin landscape from each scorer, and fails
if torch or transformers is in `sys.modules`. `api/tests/test_no_torch.py` now runs the same
module in a fresh interpreter of the development environment, so the image and the test suite
share one check. An image that cannot start offline, cannot write where it must, or imports
torch is never produced.

No secret is in the image: `.env.example` lists every `MUTASCOPE_API_*` variable, commented
out with its default (a test fails if a setting is missing from it or set in it). `.env` is
ignored by git and by `.dockerignore`, which also excludes `data/`, `build/`, `mlruns/`,
`results/`, Nextflow's directories, environments and caches. `docker-compose.yml` runs the
image with the Space's limits, reads `.env` if present, and keeps the score cache in a named
volume.

### 3. The pipeline image (`pipeline/Dockerfile`) and the `docker` profile

- **Image.** The same base and uv. `--extra pipeline --extra tracking` (torch `2.14.1+cpu` from
  the CPU index, as the lock says for Linux; MLflow, because every scoring and training step
  logs a run). Foldseek 10-941cd33 is added with `ADD --checksum`, per architecture: amd64 is the
  `linux-avx2` build, with the same sha256 CI already pins; arm64 is the release's `linux-arm64`
  build, hashed when first downloaded. `procps` is there because Nextflow reads task usage with
  `ps`. biotraj 1.2.2 (biotite's dependency) has no linux/arm64 wheel, so the builder stage has
  a compiler and the runtime stage does not. The build fails unless torch has no CUDA, `foldseek
  version` prints the pinned commit, and the CLI loads.
- **Profile.** `-profile docker` runs every task in `mutascope-pipeline:dev`, as the host user
  (`-u $(id -u):$(id -g)`), for the host's architecture. The old `--platform linux/amd64` is
  gone: on the Mac it meant emulation, and CI runners are amd64 anyway.
- **Mounts.** The task commands name some directories by host path: the data cache (the
  release, the manifest, the default caches), the log-probability and embedding caches, the
  MLflow store, and the released outputs that `train check-release` reads. Each is mounted at
  that same path through `process.containerOptions`. That is a closure, so it is evaluated per
  task after every profile's params are applied, and `docker,test` mounts the test profile's
  directories. `main.nf` creates them first, so the Docker daemon does not create them as root.
- **Configs and cache.** `params.conf_dir` points to the configs in the image, the ones its code
  was built with. `HF_HOME` is under the mounted data cache.

## Evidence

- **Sizes** (`du -sxm /` in the container; compressed is the pull size):

  | image | arm64 | amd64 |
  |---|---|---|
  | API, unpacked | 1,138 MiB | 1,124 MiB |
  | API, compressed | 597 MB | 600 MB |
  | pipeline, unpacked | 2,165 MiB | 2,256 MiB |
  | pipeline, compressed | 642 MB | 687 MB |

  Of the API image, 762 MB is the baked-in Hub cache (the fp32 graphs, 135 MB + 594 MB, and the
  bundle) and 276 MB the venv. The ~1.5 GB target of ADR 0029 is met. Docker Desktop's "disk
  usage" column (1.8 GB for arm64) counts the compressed and unpacked copies together.
- **Build:** the fetch verified 223 bundle files and 4 ONNX files on both architectures. The
  self-check passed on both (amd64 under emulation).
- **Pipeline in Docker:** `make pipeline-test-docker` (`-profile docker,test`, arm64 image)
  completed all 20 tasks, `DOWNLOAD` through `CHECK_RELEASE` and the Phase 3 ridge path, in
  62 s. Compared value by value with the native `make pipeline-test` on the same 2 assays:
  - variants, folds, structure features and 3Di tokens are identical (the Linux arm64 Foldseek
    build gives the macOS build's tokens);
  - zero-shot scores differ by at most 2.2e-5 and embeddings by 1.3e-5 (CPU against MPS,
    float32);
  - the ridge outputs follow at the same relative size. B2L11's DMS scores are on a 2 × 10⁷
    scale, so its absolute differences look large (intercept 40, predictions 5.3) but are
    3.4e-6 and 2.5e-7 of the scale.

  `train check-release` therefore stays a same-machine check (ADR 0027). A release rebuilt in
  the container would differ from the MPS-built one in the last digits.
- **Container tests** (`make test-container`, `api/tests/test_container.py`, 10 checks, arm64
  image, `--cpus=2 --memory=16g`): Docker reports the container healthy; `/ready` names the
  pinned bundle, 217 proteins and both fp32 models; a fast score and a precise job complete end
  to end, labelled Zero-shot; UID 1000 can write its cache and lock and nothing else; the thread
  variables are 1 and the Hub is offline; torch and transformers cannot be imported; `/metrics`
  needs the token; the logs hold the sequence's hash, not the sequence; a second server in the
  container and `WEB_CONCURRENCY=2` are both refused.
- **Load and memory** (`docs/load-test.md`, section 5; local container, not the Space):
  - **Setup.** `--cpus=2 --memory=16g`, but Docker Desktop's VM had 7.75 GiB, so that was the
    real memory ceiling. Nothing came near it: the highest reading was 3.2 GB.
  - **Limits.** Every limit accepted exactly its value: 120 reads, 20 scores, 120 scores over
    10 clients, 2 jobs, 4 streams.
  - **Errors.** No 5xx, failure or error line at 5 users (418 requests), at 20 users (1,921),
    or in the soak (3,141).
  - **Soak.** Anon memory levelled off at 2,472–2,503 MB from minute 7 to minute 20 (5 users,
    20 minutes), so no leak to fix.
  - **CPU.** At 20 users, 2 CPUs saturate: fast-mode median 5.6 s, p95 46 s, and precise
    jobs wait minutes. The global score limit (120 a minute) was never reached; the server
    managed 46 a minute. The likely cause is unbounded concurrent ONNX runs, each with 2
    threads (inferred, not measured). Left to Task 7, with the limits (ADR 0032): bound
    concurrent forward passes, lower `rate_limit_score_global`, or default to 35M.
- **Trivy 0.76.0** (HIGH and CRITICAL): no critical. After removing the base image's
  pip and setuptools, no Python package is flagged. 8 HIGH CVEs remain in both images, all in
  Debian 13 base packages with no fixed version (util-linux CVE-2026-76642, -78408, -78409,
  -78410; ncurses CVE-2025-69720; systemd's libraries CVE-2026-16742; libacl1 CVE-2026-54369;
  perl-base CVE-2026-9538, fix deferred). Rebuilding on a newer base digest picks up fixes
  when Debian ships them.

## Consequences

- New: `api/Dockerfile`, `pipeline/Dockerfile`, `.dockerignore`, `.env.example`,
  `docker-compose.yml`, `api/app/{fetch,selfcheck,healthcheck}.py`, `api/tests/test_fetch.py`,
  `api/tests/test_container.py` (marker `container`, outside `make test`). Make targets:
  `api-image`, `pipeline-image`, `images`, `test-container`, `pipeline-test-docker`.
- `pip install mutascope` alone no longer gives a working CLI; `mutascope[pipeline]` does.
- `tools/loadtest/run.py --image <image>` runs the scenarios against a container (the Docker
  bridge's gateway is the trusted proxy) and reports cgroup memory. A `soak5` scenario was added.
- CI still runs `make pipeline-test` natively. Moving it to `docker,test`, building the images
  for linux/amd64 and pushing to GHCR is the CI task. Under emulation on the Mac, the amd64
  pipeline build's first attempt failed on a stalled wheel download (duckdb, after about 2 h);
  the retry passed. CI builds it natively.
- The bundle and models are in the image, so a new pin means a new image. That is intended:
  an image is one release.

## Alternatives considered

- **Fetch the files at startup instead of build time:** a smaller image, but every cold start
  on the Space would download 760 MB and depend on the Hub being up. Checksums would also be
  verified on the server rather than once, before anything ships.
- **One image for the API and the pipeline:** torch would be in the API image, which ADR 0029
  rules out, and the image would be about 3 GB.
- **`env.HF_HOME` in the docker profile:** Nextflow passes the `env` scope into the container
  through an unquoted `eval $(nxf_container_env)`, which splits a value at its spaces. This
  repository's path has one, so it is `-e` in `containerOptions`.
- **A distroless or Alpine base:** fewer OS packages to patch, but onnxruntime and tokenizers
  ship manylinux (glibc) wheels, and `python:3.11-slim` is what CLAUDE.md specifies.

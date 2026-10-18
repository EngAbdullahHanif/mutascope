# 0034 — CI/CD: required checks, release-please, and deployment to Google Cloud Run

- Status: accepted (first deploy and live measurement pending; section 6)
- Date: 2026-10-18
- Builds on: ADR 0007 (Nextflow profiles), ADR 0029 and 0031 (the 2-second rule, the provisional
  precise limit), ADR 0032 (trusted proxies, limits), ADR 0033 (the images)

## Context

`main` was red twice in Phase 3 although CI ran on every PR. The repository has an active
ruleset, "protect main", with a required-status-checks rule whose list of checks is **empty**,
so no check was ever required, and "require branches to be up to date" is off. CI itself pinned
actions by moving major tags (`actions/checkout@v7`), which CLAUDE.md forbids. Nothing built or
pushed an image, and nothing deployed.

The API image is ADR 0033's: linux/amd64, ~600 MB compressed, the bundle and fp32 ONNX files
baked in, a self-check as its last build step. Every ADR so far assumed a free Hugging Face
Docker Space (2 vCPUs, 16 GB). **Docker Spaces now need a paid Hugging Face plan** (the
create-Space form offers only Static Spaces for free), so the API needs another host. What it
needs, measured (`docs/load-test-container-summary.json`): about 1.2 GB of memory once the
models are loaded, 2.0–2.5 GB with 5 users, 3.2 GB at the 20-user peak, and 2 vCPUs for ADR
0031's latency rule to be met.

## Decision

### 1. CI on every PR and every push to main (`.github/workflows/ci.yml`)

Five jobs, all in parallel; each job's name is the check name branch protection requires:

| check | does |
|---|---|
| `lint + typecheck (ruff, pyright)` | `make lint` |
| `pytest + coverage` | `make test` with `--cov=mutascope --cov=api`, a coverage table in the job summary, `coverage.xml` uploaded |
| `API types up to date` | `make types`, then `git diff --exit-code web/src/lib/api-types.ts` |
| `Nextflow smoke test (docker,test, 2 assays)` | builds the pipeline image (linux/amd64), `make pipeline-test-docker` |
| `API image (build + container tests)` | builds the API image (linux/amd64, self-check included), `make test-container` |

The smoke test now runs in the pipeline image (`-profile docker,test`), as ADR 0007 planned for
Phase 4, so CI no longer installs Foldseek itself: the image's pinned, checksum-verified build is
the one tested. Both builds use the GitHub Actions layer cache, so a PR that does not touch the
dependencies or the pins rebuilds only the code layers. The API image built on a PR is the same
recipe the release pushes: a PR cannot merge if the image would fail its self-check or its
container tests.

### 2. Every action pinned by exact release tag; Dependabot moves the pins

Each `uses:` names an exact release (`actions/checkout@v7.0.1`), each checked to exist on the
action's releases page before pinning. Dependabot (`.github/dependabot.yml`) opens grouped,
weekly PRs for:

- **Python** with the `uv` ecosystem, which updates `uv.lock` (the `pip` ecosystem would edit
  `pyproject.toml` only, and CI's `uv sync --locked` would fail on every such PR); the tool
  environments monthly, without majors;
- **GitHub Actions**;
- **Docker base images** in both Dockerfiles. Their `FROM` lines are now literal (tag and
  digest) instead of `ARG`s: Dependabot does not resolve an `ARG` in `FROM`. Python stays on
  3.11 (patches and digest refreshes only).

Each such PR passes the same required checks as any other.

### 3. Versions and changelog: release-please, with a token that starts workflows

`release-please.yml` keeps one release PR open on main, built from the Conventional Commits:
it bumps `pyproject.toml`, `uv.lock` and `.release-please-manifest.json` and writes
`CHANGELOG.md`. Merging it tags `vX.Y.Z` and makes the GitHub release.

- **`uv.lock`.** release-please has no uv support. Its generic TOML updater finds the project's
  entry with `$.package[?(@.name.value=='mutascope')].version`: its TOML parser wraps every
  string in an object, hence `.value` (googleapis/release-please#2455). Checked locally with
  release-please 17.11.2 on this `uv.lock`: exactly one line changes (mutascope's version), and
  `uv lock --check` passes on the bumped pair. If that ever breaks, the release PR fails CI's
  `uv sync --locked`, so it cannot ship a stale lock.
- **Token.** GitHub starts no workflow for events made with `GITHUB_TOKEN`. With it, the release
  PR would never get its required checks (unmergeable) and its tag would never start the
  release. release-please therefore runs with a fine-grained token (`RELEASE_PLEASE_TOKEN`)
  limited to this repository.
- `bump-minor-pre-major`: until 1.0.0, a breaking change bumps the minor version.

### 4. Release: one image, pushed once, deployed by digest to Cloud Run (`release.yml`)

**Host: Google Cloud Run**, one service in `europe-west1`: 2 vCPUs, 4 GiB, at most one
instance, scaled to zero when idle. The free tier (180,000 vCPU-seconds and 360,000
GiB-seconds a month, request-based billing) covers a portfolio's traffic; the account needs a
billing account, and a budget alert guards it. Render's free instance (512 MB, 0.1 CPU) was
considered and rejected: the API needs 1.2 GB before its first request.

On a tag `v*.*.*`:

1. **Check:** the tag equals `v` + the package version (`uv version --short`). `/health`
   reports the package version and the deploy waits for it, so a mismatched tag fails here,
   before the build, not after the wait.
2. **Image:** build `api/Dockerfile` for linux/amd64 once and push it to Artifact Registry
   (`europe-west1-docker.pkg.dev/<project>/mutascope/mutascope-api:<tag>`, which Cloud Run
   pulls; it cannot pull from GHCR) and to GHCR (`ghcr.io/engabdullahhanif/mutascope-api`, the
   public copy). The digest is a job output.
3. **Deploy:** `python -m api.deploy.cloudrun render` writes the whole service from
   `conf/deploy/cloudrun.yaml`, and `gcloud run services replace` makes Cloud Run's service
   exactly that. Nothing is set in the console, and a hand-made change is undone by the next
   deploy. The service:
   - runs the image **by digest**, with the tag as a revision label, so the revision is the
     bytes CI built and tested even if a tag moves;
   - has a **startup probe on `/ready`**: a revision gets no traffic until the bundle and both
     models are loaded, and one that never gets ready never takes traffic, so a broken release
     leaves the previous one serving (`replace` then fails, and so does the workflow);
   - has a liveness probe on `/health` (Cloud Run ignores the image's Docker `HEALTHCHECK`);
   - has **at most one instance**: the queue, rate-limit counters and stream slots are in one
     process's memory (ADR 0032);
   - is **gen2** (full Linux, for the lock file), with startup CPU boost and request timeout
     900 s (a precise job's stream);
   - runs as a service account with **no roles**: the API reads nothing from Google Cloud;
   - takes its environment only from the config's `env` (and Secret Manager via `secrets`).
   Then `allUsers` gets `roles/run.invoker`: the API is public, as the Space would have been.
4. **Wait** (`python -m api.deploy.cloudrun wait`): `/health` at the public URL must report
   the tag's version and `/ready` must answer 200, or the workflow fails after 15 minutes.

**Keyless access.** CI never holds a Google key. Workload Identity Federation trusts GitHub's
OIDC tokens only when the repository is `EngAbdullahHanif/mutascope`, the workflow is
`release.yml`, and the ref is a `v` tag or main; then it may act as the deployer service
account (Cloud Run admin, write to the one Artifact Registry repository, run services as the
runtime account). `tools/deploy/setup-gcp.sh` creates all of it once; its output (project id
and number) goes into `conf/deploy/cloudrun.yaml`. A PR, a fork or another workflow cannot get
a token.

`workflow_dispatch` (from main) with a tag redeploys an image already in Artifact Registry
(its digest is looked up, nothing is rebuilt): the way to apply a change to
`conf/deploy/cloudrun.yaml` without a release.

**Billing shape and precise mode.** With request-based billing an instance has CPU only while
a request is in flight. Fast mode is a request, so it is unaffected. A precise job runs in the
background (ADR 0031), so it advances while a client holds its SSE stream open, which is how
the website follows a job; a client that only polls `GET /v1/jobs/{id}` gets a job that mostly
waits. Instance-based billing would remove this, but it bills idle instances until they scale
down, and the free tier is meant for request-based billing. Scale-to-zero also means a first
visitor after an idle period waits for a cold start (image pull and model loading), and the
score cache and finished jobs do not survive it. The website must work with the API asleep
anyway (CLAUDE.md), so this is accepted.

### 5. Branch protection

The existing ruleset is kept and completed (instructions in the PR): the five checks of section 1
are required, from GitHub Actions, and branches must be up to date with main before merging.
Release-please's and Dependabot's PRs go through the same rule.

### 6. After the first deploy (pending)

`tools/loadtest/live.py` measures the live service in about 10 minutes and ~250 requests: the
cold start (first `/ready`), `/health` round trips (network baseline), fast mode at 300 and
1,000 residues for both models (20 cache misses per cell, one request at a time, under the
per-client limit), one precise job of 600 residues followed on its stream, and Locust's
`Visitor` mix at 5 users for 2 minutes. Through Google's front end the five users are one
client, so their 429s are counted as the limiter working. The script applies ADR 0031's rule
(the rule was written for "the Space"; it applies to the service that serves the API, now
Cloud Run with the same 2 vCPUs) and prints the default model. The results, the decision and
the address the proxy connects from (for `trusted_proxies`, read from the API's access lines
in Cloud Logging) are added to this ADR and to `docs/load-test.md`, and any change goes into
`conf/deploy/cloudrun.yaml`.

Until then `env` is empty: the service runs the image's defaults, including an empty
`trusted_proxies`. Every visitor is then the front end's address, so all users share one
client's quota and one heavy user can make everyone wait (`docs/threat-model.md`). The limits
still hold, and the measurement sets the range before the site links to the API. Where earlier
ADRs and the threat model say "the Space", read "the deployed service".

## Setup and secrets

Done once by the author (exact steps in the PR):

| where | what |
|---|---|
| Google Cloud | a project with billing linked, a budget alert; `tools/deploy/setup-gcp.sh <project-id>` in Cloud Shell; its two output lines into `conf/deploy/cloudrun.yaml` |
| GitHub, repository secret | `RELEASE_PLEASE_TOKEN`: fine-grained PAT, this repository only, Contents, Pull requests and Issues read and write |

No Google secret is stored in GitHub (Workload Identity Federation), and GHCR needs none
(`GITHUB_TOKEN` pushes). Project id and number are identifiers, not secrets, and live in the
config.

## Consequences

- New: `ci.yml` (rewritten), `release.yml`, `release-please.yml`, `.github/dependabot.yml`,
  `release-please-config.json`, `.release-please-manifest.json`, `conf/deploy/cloudrun.yaml`,
  `api/deploy/cloudrun.py` (+ `api/tests/test_deploy.py`), `tools/deploy/setup-gcp.sh`,
  `tools/loadtest/live.py`.
- Both Dockerfiles have literal `FROM` lines; the images are unchanged. The image needed no
  change for Cloud Run: it already listens on `0.0.0.0:7860`, runs as UID 1000 and needs no
  network.
- A release is a merge of the release PR; there is no manual deploy step and no deploy from a
  laptop.
- Costs: inside the free tier for expected traffic; Artifact Registry keeps the 3 newest
  images (~0.6 GB each, layers shared), a few cents a month above its 0.5 GB free storage.
  With one instance at most, the worst case (the instance busy all month) is bounded; the
  budget alert says when spending starts.
- CI takes longer (two image builds), but the jobs run in parallel and the layer cache makes
  most rebuilds partial.

## Alternatives considered

- **Hugging Face Docker Space** (the plan of ADR 0029–0033): needs a paid plan now. It would
  have been a one-line `FROM` Dockerfile pulling the GHCR image. The frontend can still use a
  free Static Space (Phase 5).
- **Render, free instance:** 512 MB and 0.1 CPU; the API needs 1.2 GB idle. A 2 GB Render plan
  fits but is paid.
- **Oracle Cloud Always Free VM** (4 ARM cores, 24 GB): the most hardware for free, but a
  server to run (OS updates, TLS, a proxy) and an arm64 image to release.
- **Cut the API to fit 512 MB** (35M only, no precise mode): reverses ADR 0029 and 0031, and
  0.1 CPU would still miss the 2-second rule.
- **Instance-based billing (`cpu-throttling: false`):** precise jobs would run without a client
  connected, but idle instances are billed (section 4).
- **A JSON key for a service account in a GitHub secret:** a long-lived credential to leak and
  rotate; Workload Identity Federation needs none.
- **Pin actions by commit SHA:** stronger against a moved tag, but CLAUDE.md asks for exact
  versions, and Dependabot updates either form.
- **A deploy inside the release-please workflow (`release_created`):** works with
  `GITHUB_TOKEN`, but a hand-made tag would not deploy, and release PRs would still get no CI.
- **`pip` ecosystem for Dependabot:** leaves `uv.lock` stale (section 2).

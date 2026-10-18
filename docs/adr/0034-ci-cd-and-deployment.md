# 0034 — CI/CD: required checks, release-please, GHCR, and a Space that only pulls

- Status: accepted (deploy and Space measurement pending the first release; section 6)
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
baked in, a self-check as its last build step. The target is a free Hugging Face Docker Space
(2 vCPUs, 16 GB).

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

### 4. Release: one image, pushed once, pulled by the Space (`release.yml`)

On a tag `v*.*.*`:

1. **Check:** the tag equals `v` + the package version (`uv version --short`). `/health`
   reports the package version and the deploy waits for it, so a mismatched tag fails here,
   before the build, not after a 20-minute wait.
2. **Image:** build `api/Dockerfile` for linux/amd64 and push
   `ghcr.io/engabdullahhanif/mutascope-api:<tag>` and `:sha-<commit>` with `GITHUB_TOKEN`
   (`packages: write`); the digest is a job output.
3. **Deploy** (`python -m api.deploy.space deploy`), in the `huggingface-space` environment:
   - the Space's **variables** become exactly those in `conf/deploy/space.yaml` (others are
     deleted), and the Space **secrets** that CI holds are set (`SPACE_METRICS_TOKEN` →
     `MUTASCOPE_API_METRICS_TOKEN`; never deleted, never logged);
   - one commit writes the Space's README (Docker SDK, port 7860) and its whole Dockerfile:
     `FROM ghcr.io/engabdullahhanif/mutascope-api:<tag>@sha256:<digest>`. The tag says which
     release runs; the digest makes the line immutable, so retagging in GHCR cannot change
     what the Space pulls. A rerun with identical files restarts the Space instead of
     committing nothing.
4. **Wait** (`python -m api.deploy.space wait`): passes only when, at the same time, the Space
   runs its latest commit (the previous container keeps answering `/ready` while the new one
   builds, so `/ready` alone would pass on the old release), `/health` reports the tag's
   version, and `/ready` is 200. `BUILD_ERROR`, `RUNTIME_ERROR` or `CONFIG_ERROR` fails at
   once and prints the end of the Space's build or run log. Otherwise it fails after 25
   minutes.

`workflow_dispatch` with a tag redeploys an image that is already in GHCR (its digest is looked
up, nothing is rebuilt): the way to apply a change to `conf/deploy/space.yaml` without a release.

**Why the Space only pulls.** The Space could build `api/Dockerfile` itself, but then it would
run an image nobody tested: the CI build, its self-check and its container tests would be of
different bytes, and the Space would download the 760 MB of weights on every build. The
one-line Dockerfile makes the Space a pull of the exact image the release built.

**Config as code.** `conf/deploy/space.yaml` is the Space's whole non-secret configuration
(`MUTASCOPE_API_*` variables). ADR 0031's 2-second rule, a measured `trusted_proxies` or a new
precise limit is therefore a reviewed one-line change, applied by the next deploy.

### 5. Branch protection

The existing ruleset is kept and completed (instructions in the PR): the five checks of section 1
are required, from GitHub Actions, and branches must be up to date with main before merging.
Release-please's and Dependabot's PRs go through the same rule.

### 6. After the first deploy (pending)

`tools/loadtest/space.py` measures the live Space in about 10 minutes and ~250 requests:
`/health` round trips (network baseline), fast mode at 300 and 1,000 residues for both models
(20 cache misses per cell, one request at a time, under the per-client limit), one precise job
of 600 residues, and Locust's `Visitor` mix at 5 users for 2 minutes. Through the Space's proxy
the five users are one client, so their 429s are counted as the limiter working. The script
applies ADR 0031's rule and prints the default model. The results, the decision and the
measured proxy range (`trusted_proxies`, read from the Space's run log) are added to this ADR
and to `docs/load-test.md`, and any change goes into `conf/deploy/space.yaml`.

Until then `variables` is empty: the Space runs the image's defaults, including an empty
`trusted_proxies`: every visitor is the proxy's address, so all users share one client's quota
and one heavy user can make everyone wait (`docs/threat-model.md`). The limits still hold, and
the measurement sets the range before the site links to the API.

## Secrets

| where | name | what |
|---|---|---|
| GitHub, repository secret | `HF_TOKEN` | Hugging Face fine-grained token, write access to the Space repo only |
| GitHub, repository secret | `RELEASE_PLEASE_TOKEN` | GitHub fine-grained PAT, this repository only: Contents, Pull requests, Issues read and write |
| GitHub, repository secret (optional) | `SPACE_METRICS_TOKEN` | ≥ 32 characters; set on the Space as `MUTASCOPE_API_METRICS_TOKEN` |

GHCR needs no secret: `GITHUB_TOKEN` pushes. The package must be public, because a Space
cannot pull from a private registry.

## Consequences

- New: `ci.yml` (rewritten), `release.yml`, `release-please.yml`, `.github/dependabot.yml`,
  `release-please-config.json`, `.release-please-manifest.json`, `conf/deploy/space.yaml`,
  `api/deploy/space.py` (+ `api/tests/test_deploy.py`), `tools/loadtest/space.py`.
- Both Dockerfiles have literal `FROM` lines; the images are unchanged.
- A release is a merge of the release PR; there is no manual deploy step and no deploy from a
  laptop.
- CI takes longer (two image builds), but the jobs run in parallel and the layer cache makes
  most rebuilds partial.

## Alternatives considered

- **Pin actions by commit SHA:** stronger against a moved tag, but CLAUDE.md asks for exact
  versions, and Dependabot updates either form. Not taken for now.
- **Build on the Space from the repository:** an untested image, 760 MB per build (above).
- **A tag-triggered deploy inside the release-please workflow (`release_created`):** works with
  `GITHUB_TOKEN`, but a hand-made tag would not deploy, and release PRs would still get no CI.
- **`pip` ecosystem for Dependabot:** leaves `uv.lock` stale (section 2).

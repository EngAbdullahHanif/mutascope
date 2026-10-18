# 0035 — Phase 4 close: API, Docker, CI/CD

- Status: accepted
- Date: 2026-10-18
- Changes: no code. Ticks the Phase 1 `docker` profile item (done by ADR 0033), carries one
  open Phase 4 item and four follow-ups into Phase 5 (see "Open and weak")

## Context

Phase 4 (week 7) had one goal: a tested, observable service deployed automatically. Its
done-when line has four parts: CI green on every push, tags deploy automatically, `/docs` live,
fast-mode response < 2 s. This record checks each against the repository, GitHub and the live
service on 2026-10-18, lists what is open or weak, and says what Phase 5 builds on. Details are
in ADRs 0028–0034 and build-log entries 28–35.

## The done-when line, checked

| Part | Verdict | Evidence |
|---|---|---|
| CI green on every push | **met since `9533655`; red once** | Every `ci.yml` run on main is green except the merge of #35 (`9de9434`, 12:37 UTC): a container test that depended on test order (build-log entry 35, problem 1). Fixed by #39 and green from `9533655` (13:23 UTC) on, including `24d5adf`, main's head. The five required checks pass on all three open PRs (#37, #38, #41). |
| Tags deploy automatically | **met** | `release.yml` on tag `v0.2.0`: built, pushed to Artifact Registry and GHCR (digest `sha256:4692a941…`), deployed, waited for `/health` = 0.2.0 and `/ready`. The manual redeploy with main's config (`workflow_dispatch`, 15:00 UTC) also succeeded. |
| `/docs` live | **met** | `GET /docs` and `/openapi.json` answer 200 on the service URL. |
| Fast-mode response < 2 s | **met at 300 residues, not at 1,000, not under load** | p95 with the network (~0.2 s round trip): 35M 0.92 s at 300 residues, 2.09 s at 1,000; 150M 1.85 s / 5.72 s. The default is 35M (ADR 0031's rule). With 5 users, `POST /v1/score` p95 was 10 s. |

The live service reports what the redeploy set: `/ready` gives `score_default_model:
esm2_35m`, `precise_max_length: 400`, bundle revision `3ae76721…`, 217 proteins, both fp32
models. This was the open check of build-log entry 35 (design choice 3, problem 3).

## What Phase 4 produced

All values were rechecked on 2026-10-18 from the source named in the last column.

| What | Value | Source |
|---|---|---|
| Live API | https://mutascope-api-2lvmztrsha-ew.a.run.app (version 0.2.0; Cloud Run `europe-west1`, 2 vCPU, 4 GiB, 0–1 instance) | `/health`, `/ready`, `conf/deploy/cloudrun.yaml` |
| API image (linux/amd64) | 600 MB compressed (600,190,175 bytes, 12 layers), 1,124 MiB unpacked | rebuild of the `v0.2.0` commit: OCI manifest layer sizes; `du -sxm /` in the container. Same as ADR 0033 |
| Fast mode, p95 (network included) | 35M: 0.92 s (300 aa), 2.09 s (1,000 aa); 150M: 1.85 s, 5.72 s | `docs/live-measure-summary.json`, 20 cache misses per cell |
| Precise mode (35M) | 400 aa in 163 s, 600 aa in 406 s → limit 400 | same file |
| Cold start | 20.5 s to the first `/ready` after 21 idle minutes | same file |
| int8 vs fp32 PyTorch (fast mode, 217 assays) | aggregate Spearman −0.0005 [−0.0013, +0.0002] (35M), −0.0006 [−0.0012, +0.0002] (150M); per-assay agreement < 0.99 on 3 / 1 assays (min 0.9780 / 0.9819) → **int8 rejected, fp32 served** | `results/zero_shot/comparisons.parquet` (paired bootstrap), `results/onnx/score_parity_*_int8_wt.parquet` |
| fp32 ONNX vs PyTorch | logits within 9.0e-5 (atol 1e-4); per-assay Spearman 1.0000 on 217 / 217; live routes vs golden landscapes 0.99999997 | `results/onnx/`, ADR 0029, 0031 |
| Website bundle | 217 protein files, 29.2 MB (29,218,051 bytes); 29.7 MB with manifest, benchmarks, schemas; largest protein 1.15 MB (budget 2 MB); `SHA256SUMS` all OK | `build/bundle/`, tag `bundle-v1.0.0` = `3ae76721…` |
| Bundle fidelity | no Spearman moved by more than 6.6e-4, no coverage moved | ADR 0028, `results/export/bundle_fidelity.parquet` |

One detail ADR 0029 does not give: within function types, int8 is lower with an interval that
excludes 0 for Organismal Fitness (35M −0.0014, 150M −0.0015, 77 assays), Stability (35M only,
−0.0022) and Activity (150M only, −0.0011). int8 is rejected anyway, so nothing changes.

## Checklist audit

Every Phase 4 item but one is ticked, and each ticked item was checked against its evidence:
the Hub pins (test requires 40-character commits), the bundle (`build/bundle`, checksums), ONNX
(`results/onnx/`), the API routes and tests (`api/tests/`), the images (rebuilt above), CI
(GitHub run history), Dependabot and release-please (open PRs #37, #38, #41 with green checks),
the ruleset (five required checks; set by the author), the deploy (run history and live
probes), the Nextflow wiring (ADR 0027) and the type generation (CI job `API types up to
date`).

The Phase 1 item "Profiles: `local`, `docker`, `test`" was still open, waiting for "the Phase 4
image". ADR 0033 built that image and CI's `Nextflow smoke test (docker,test, 2 assays)` runs
the profile on every PR. It is now ticked.

## Open and weak

### Open (carried into Phase 5)

1. **Bound concurrent forward passes.** At 5 users, reads wait behind CPU-bound scoring
   (`GET /v1/proteins/{id}` p99 9.9 s), and Cloud Run refused 2 of 226 requests with "no
   available instance". ADR 0033 inferred the cause (unbounded concurrent ONNX runs, each with
   2 threads; not measured) and listed three levers: bound concurrent forward passes, lower
   `rate_limit_score_global`, default to 35M. Only the third is done. Raising `max_instances`
   is not an option (ADR 0032).

### Weak

2. **The 2-second rule holds at 300 residues, not at 1,000.** 35M's p95 at 1,000 residues is
   2.09 s, about 1.9 s without the network. Under 5 users, scoring p95 was 10 s. The rule chose
   the faster model; nothing makes long sequences meet 2 s. Item 1 is what would help under
   load.
3. **The deployed service is not observable through the API's own metrics.** `secrets: {}` in
   `conf/deploy/cloudrun.yaml`: no `metrics_token`, no `sentry_dsn`. Live `/metrics` answers
   404, and no error reaches Sentry. What exists live is Cloud Run's request metrics and the
   structlog JSON lines in Cloud Logging. The code, tests and secret wiring are done (ADR 0032,
   0034); the secrets were never created.
4. **The GHCR image is probably not public.** ADR 0034 and the config call GHCR "the public
   copy", and CI pushed `ghcr.io/engabdullahhanif/mutascope-api:v0.2.0`. An anonymous pull
   token for it is refused (401). New GHCR packages are private by default, so the package
   visibility likely needs to be set by the author. Not confirmed: this machine's `gh` token
   lacks `read:packages`.
5. **The trusted proxy is set but not yet seen working.** Entry 35 asked for a check that the
   access lines show visitors' addresses after the redeploy. That needs Cloud Logging, which
   this machine cannot query (no `gcloud`).
6. **Memory was measured with more headroom than the service has.** The container tests run
   with 16 GB, the local load test peaked at 3.2 GB at 20 users, and the service has 4 GiB,
   whose in-memory disk also holds the score cache (up to 256 MB). Twenty users on Cloud Run
   were not measured.
7. **Scale-to-zero costs.** A first visitor waits 20.5 s, the score cache and finished jobs do
   not survive an idle period, and a precise job advances only while its SSE stream is open
   (request-based billing, ADR 0034). The website must follow jobs on the stream and work with
   the API asleep.
8. **The live numbers are thin.** One laptop in Germany, one client address, 20 requests per
   latency cell, one precise job per length, one cold start.
9. **The image size is a rebuild, not the registry's bytes.** Same commit and recipe; the
   pushed manifest could not be read without registry credentials. It equals ADR 0033's
   figure for the earlier build.
10. **Earlier ADRs say "the Space"** (ADR 0029–0033, the threat model). ADR 0034 says to read it
    as "the deployed service"; the documents were not rewritten.

Not weak, recorded so it is not rechecked: the deployed image's code equals main's (`git diff
v0.2.0 origin/main` on `api/app`, `src`, the Dockerfile and the lock is empty). Release PR #41
(0.3.0) carries the release workflow fix and the deploy config, not an API change.

## Decisions that mattered most

1. **Refuse to run anything unverified (ADR 0028, 0030, 0033, 0034).** The bundle and the ONNX
   files are read by commit and checked against their `SHA256SUMS`, at image build and again at
   startup. The API refuses an unknown schema version or a non-fp32 model. The image's last
   build step is the same self-check as the no-torch test. Cloud Run runs the image by digest
   and gives it no traffic until `/ready` passes. A bad release fails its own workflow and leaves
   the previous one serving.
2. **Accuracy gates fixed before results decide what is served (ADR 0028, 0029).** int8 had a
   non-significant aggregate change and the smaller, faster-looking files, and it was rejected
   because 3 + 1 assays fell below the per-assay agreement set beforehand. On this CPU it was
   not faster either. The bundle's fixed-point encoding has to pass a fidelity check on the
   written bytes before it ships.
3. **Measure on the real host and change behaviour by configuration (ADR 0031, 0034).** The
   default model, the precise limit and the trusted proxy were settings from the start. One
   measured deploy changed all three in `conf/deploy/cloudrun.yaml`, with each measurement
   beside its line, and no code changed. The host itself changed mid-task (Docker Spaces became
   paid) without touching the image.

## What Phase 5 inherits

- **The API:** https://mutascope-api-2lvmztrsha-ew.a.run.app, `/docs` for the OpenAPI UI. Routes
  under `/v1` (ADR 0030, 0031). Response types only from `web/src/lib/api-types.ts` (`make
  types`).
- **CORS:** `cors_origins` is `http://localhost:3000` only, and the live service sends no
  `Access-Control-Allow-Origin` for any other origin. The frontend's origin goes into
  `MUTASCOPE_API_CORS_ORIGINS` in `conf/deploy/cloudrun.yaml` when its domain exists.
- **Static data:** the bundle at `bundle-v1.0.0` (`export.publish.revision`), read straight from
  the Hub dataset repo's `bundle/`, so the site works with the API asleep. Schemas in
  `docs/schema/bundle/`.
- **Labels the site must carry:** "Supervised (n = …)" for the 198 ridge assays, "Zero-shot"
  for the rest and for every live result; intervals with their level; imputed-structure rows
  flagged; the five MSA-region assays labelled (ADR 0012, 0026).
- **Live-scoring behaviour the UI must handle:** cold start (~20 s), 429 with `Retry-After`,
  precise mode only via the SSE stream (resume with `Last-Event-ID`), limit 400 residues, 35M
  by default.
- **Open items 1, 3, 4, 5** above, as follow-ups in the Phase 5 roadmap.

# 0036 — Bounded live inference, and the deployed service's secrets

- Status: accepted; measured in a local container (section 5); the live measurement on Cloud
  Run follows the deploy (section 6)
- Date: 2026-10-18
- Builds on: ADR 0031 (live scoring, jobs), ADR 0032 (limits, `/metrics`, Sentry), ADR 0033
  (the image, the 20-user container run), ADR 0034 (Cloud Run, `secrets`), ADR 0035 (items 1
  and 3)

## Context

ADR 0035 carried two items into Phase 5.

**Item 1, concurrent forward passes.** At 5 users on Cloud Run, `GET /v1/proteins/{id}` had a
p99 of 9.9 s, `POST /v1/score` a p95 of 10 s, and Cloud Run refused 2 of 226 requests with "no
available instance". Every fast-mode request ran its forward pass in its own
`asyncio.to_thread` call, and so did every precise-mode batch. Nothing bounded how many ran at
once, and each ONNX session uses 2 intra-op threads (`onnx_threads`), so on 2 vCPUs N requests
meant 2N compute threads, plus the event loop and the read routes. ADR 0033 inferred this
cause from the 20-user container run, where fast mode had a median of 5.6 s and a p95 of 46 s,
but nobody measured it.

**Item 3, observability.** `secrets: {}` in `conf/deploy/cloudrun.yaml`: live `/metrics`
answered 404 and nothing reached Sentry. The author has since created two Secret Manager
secrets, `MUTASCOPE_API_METRICS_TOKEN` and `MUTASCOPE_API_SENTRY_DSN`.

## Decision

### 1. One inference pool for every live forward pass (`api.app.inference`)

`Inference` is a thread pool of `inference_concurrency` workers (default **1**) behind an
`asyncio.Semaphore` of the same size. Fast mode (`POST /v1/score`) and every precise-mode
batch (the job worker) call `Inference.run`, and nothing else in the API runs a scorer.

- **Admission before the thread.** A call takes a slot first and only then goes to the pool,
  so the pool never holds a backlog and the waiting is on the event loop, where it costs
  nothing.
- **Fast mode waits at most `inference_wait_seconds` (10 s)**, then gets a **503**
  `urn:mutascope:problem:scoring-busy` with `Retry-After: 10`. It is a 503, not a 429, because
  the server is busy; the client has not exceeded any limit of its own.
- **Precise batches wait without a limit.** The job is already accepted and queued. asyncio's
  semaphore wakes waiters in order, so a job's batches take turns with fast requests instead
  of either starving the other.
- **A slot is freed when its thread finishes**, in the future's done-callback, not when the
  awaiting request goes away. A forward pass cannot be interrupted, so freeing the slot on
  cancellation would let a second pass share the CPU with it.
- **Cache hits take no slot.** The fast path is split into `cached_fast` (a disk read, in a
  plain thread) and `compute_fast` (the pass and the cache write, in the pool).
- The calling task's context goes with the call (`contextvars.copy_context`, as
  `asyncio.to_thread` does), so the scorer's log lines keep the `request_id`.

**Why 1.** One pass already uses both vCPUs (`onnx_threads = 2`). A second pass at the same
time does not finish two requests sooner. It splits the same CPU, and every read waits for
CPU along with it. The setting exists so that a larger instance can be measured without a code
change.

**Why 10 s.** On Cloud Run a 35M fast pass at 1,000 residues took 2.09 s at p95, and a precise
batch of 16 residues at the 400-residue limit takes about 6.5 s (163.4 s for 25 batches, ADR
0034). With 10 s, a request behind one batch and one long fast pass still runs.

**Alternatives.**
- *A lower `rate_limit_score_global`* caps requests a minute, not passes running at once, so
  a burst still stacks up.
- *A separate pool for precise mode* puts two passes back on 2 vCPUs.
- *A priority queue that puts fast mode ahead of jobs* is more code and can starve a job under
  load. Taking turns in order was enough here (section 5).
- *More instances or workers* are ruled out by ADR 0032: the queue, the counters and the slots
  live in one process.

### 2. What the pool and the memory look like from outside

`/metrics` gains `mutascope_inference_running`, `mutascope_inference_waiting`,
`mutascope_score_cache_bytes` (diskcache's volume: Cloud Run's disk is memory, so the cache
counts against the 4 GiB) and the `process_*` series (`ProcessCollector`: resident memory, CPU
seconds; Linux only). Resident memory plus the cache is what the live measurement compares
with the limit.

### 3. Secrets from Secret Manager

`conf/deploy/cloudrun.yaml`:

```yaml
secrets:
  MUTASCOPE_API_METRICS_TOKEN: MUTASCOPE_API_METRICS_TOKEN
  MUTASCOPE_API_SENTRY_DSN: MUTASCOPE_API_SENTRY_DSN
```

and `env` gains `MUTASCOPE_API_SENTRY_ENVIRONMENT: production`. The runtime account
(`mutascope-api-runtime`) still has no project role. It gets
`roles/secretmanager.secretAccessor` on **these two secrets only**, granted per secret by
`tools/deploy/setup-gcp.sh`, which now also enables `secretmanager.googleapis.com`. Without
the grant, `gcloud run services replace` fails and the previous revision keeps serving.

Two details the settings now handle:
- **A trailing newline is stripped** from both secrets. A secret created with `echo … | gcloud
  secrets create --data-file=-` stores the newline. The token check strips the client's
  header, so the server's stored token must be stripped too, or every request would get a 401.
- **The token still needs 32 or more characters** (`min_length`, ADR 0032). A shorter one
  stops the API at startup, so the deploy's `/ready` wait fails.

### 4. The load tools know the pool

- `locustfile.py` counts a `scoring-busy` 503 with `Retry-After` as an answer (`… [503]`), as
  it does a 429.
- `run.py` takes `--limits` (for example Cloud Run's `--memory=4g`), has a `load20_2m`
  scenario, and samples the inference gauges.
- `live.py` adds:
  - reads at one user (protein, landscape, benchmarks; p95 and p99);
  - load at several user counts (`--load-users 5 20`, a minute apart, so each starts with an
    empty rate-limit window);
  - `/metrics` sampled during the load (memory and gauges). The token comes from
    `MUTASCOPE_API_METRICS_TOKEN` in the environment, never from the command line;
  - the 401 / 200 check of `/metrics`.

  The default precise length is now the deployed limit, 400.

## 5. Evidence: local container, before and after

Same image recipe, before (`main`) and after (this branch), each under
`--cpus=2 --cpuset-cpus=0,1 --memory=4g` (two cores, Cloud Run's memory limit), with Locust's
`Visitor` mix (`tools/loadtest/run.py`), a fresh container per run, and the cores of an M4 Pro,
not Cloud Run's. Milliseconds; memory is the container's anonymous memory (cgroup).

| | 5 users, 180 s: before | after | 20 users, 120 s: before | after |
|---|---|---|---|---|
| `GET /v1/proteins/{id}` p95 / p99 | 38 / 58 | 25 / 34 | 140 / 200 | 55 / 150 |
| `GET /v1/proteins/{id}/landscape` p95 / p99 / max | 640 / 1,200 / 1,231 | 370 / 560 / 565 | 810 / 2,900 / 4,770 | 540 / 1,600 / 1,733 |
| `GET /v1/benchmarks` p99 | 36 | 44 | 390 | 28 |
| `POST /v1/score` p50 / p95 / p99 | 530 / 3,800 / 6,600 | 530 / 2,200 / 2,800 | 2,400 / 16,000 / 21,000 | 3,400 / 7,600 / 9,700 |
| scores accepted | 96 | 103 | 145 | 163 |
| precise job, whole stream, p50 | 3,200 | 2,800 | 65,000 | 39,000 |
| inference running / waiting, max | (no gauge) | 1 / 2 | (no gauge) | 1 / 11 |
| memory, max (MB) | 2,077 | 1,860 | 2,584 | 1,989 |
| 503 `scoring-busy` | — | 0 | — | 0 |

What this shows:
- **The tails shrink.** Fast mode's p95 at 20 users halves (16 s → 7.6 s), and the landscape
  p99 falls from 2.9 s to 1.6 s.
- **The pool holds at one pass**, with up to 11 calls waiting, and more scores and jobs finish
  in the same time. Fast mode's median at 20 users rises (2.4 s → 3.4 s): requests now wait
  their turn instead of sharing the CPU.
- **Memory falls by about 600 MB** at 20 users. One ONNX run at a time means one run's working
  memory, not several.
- **No request waited 10 s**, so the 503 path was not exercised here; the tests exercise it.

What this does not show: locally, before the change, reads were already fast at 5 users (p99
58 ms), while Cloud Run had 9.9 s. Cloud Run's 2 vCPUs are slower than two M4 Pro cores, so the
local run cannot answer whether reads stay under 1 s live. Section 6 does.

Tests:
- `api/tests/test_inference.py`, in-process with the stand-in graphs:
  - the bound at concurrency 1 and 2;
  - the 503 with `Retry-After`;
  - a slot held until the pass ends, even when its caller is cancelled;
  - context propagation;
  - a failing call freeing its slot;
  - through the routes, one pass with four waiting while reads answer within 0.5 s;
  - jobs and fast mode taking turns;
  - cache hits needing no slot;
  - the gauges.
- `api/tests/test_container.py`, in the image under `--cpus=2`, with real models: four
  1,000-residue fast requests at once. The gauges must show one pass running and others waiting,
  never two running, and every read must answer under 1 s. The image built from `main` fails it
  (no inference gauges).
- The 20-user run found one bug, fixed with a regression test: at shutdown, a pass still running
  after the event loop had closed raised `RuntimeError: Event loop is closed` in its
  done-callback (one error line in the server log). The release now ignores a closed loop.

## 6. Live measurement on Cloud Run

To be added after the deploy, with `tools/loadtest/live.py` as in ADR 0034: p95 of the read
routes and of `POST /v1/score` at 1 and 5 users, 20 users for at most 2 minutes, memory against
the 4 GiB limit, read p99 at 5 users against 1 s, and `/metrics` 401 without the token and 200
with it.

## Consequences

- Under load, a fast-mode request waits its turn, behind at most a precise batch (about 6.5 s
  live at 400 residues) and the fast requests ahead of it. It no longer competes for CPU. A
  client sees either the answer or, after 10 s, a 503 with `Retry-After`; the website must
  handle that like a 429.
- `inference_concurrency` and `inference_wait_seconds` are settings (`.env.example`), so a
  different instance shape is a config change, measured first.
- The API's secrets live only in Secret Manager. The repository names them but never holds them,
  and the one GitHub secret is still `RELEASE_PLEASE_TOKEN`.

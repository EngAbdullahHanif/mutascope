# 0036 — Bounded live inference, and the deployed service's secrets

- Status: accepted. The pool alone did not make reads fast on Cloud Run (section 6: read p99
  8.5 s at 5 users). With **one ONNX thread** (section 7, deployed 2026-10-18) read p99 at 5 users
  is 140 ms; precise jobs and scoring capacity under load pay for it
- Date: 2026-10-18
- Builds on: ADR 0031 (live scoring, jobs), ADR 0032 (limits, `/metrics`, Sentry), ADR 0033
  (the image, the 20-user container run), ADR 0034 (Cloud Run, `secrets`), ADR 0035 (items 1
  and 3)
- Addendum (2026-10-20): the precise limit on Cloud Run is lowered from 400 to 300 residues
  (see "Addendum" at the end)

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
```

and `env` gains `MUTASCOPE_API_SENTRY_ENVIRONMENT: production`. The Sentry DSN was listed too,
but there is no Sentry project yet. The secret held a value that is not a DSN, and `sentry_sdk`
raises `BadDsn` at startup, so v0.4.0's first deploy failed its startup probe and v0.3.0 kept
serving. PR #46 removed the line, and v0.4.0 was redeployed from main. The DSN goes back when a
Sentry project exists. The runtime account
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
- **A rejected setting is never echoed** (`hide_input_in_errors`). Reproducing the failed
  deploy showed that pydantic's message printed the rejected value in full
  (`input_value='…'`), which would have put a too-short token into Cloud Run's logs. The error
  now names the field and the reason only. The live token is 64 characters and was never
  rejected, so nothing leaked.
- **CORS** (`env`): `MUTASCOPE_API_CORS_ORIGINS` is now the website's two origins,
  `https://mutascope.abdullahhanif.me` and `https://mutascope-web.vercel.app` (Vercel). It takes
  effect with the next deploy; v0.4.0 still allows only `http://localhost:3000`.

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

## 6. Live measurement on Cloud Run (v0.4.0, 2026-10-18)

`tools/loadtest/live.py --load-users 5 20 --load-seconds 120 --seed 36`, from one laptop in
Germany (network round trip p95 0.21 s), with the same method as ADR 0034. Results are in
`docs/live-measure-0036-summary.json`. The earlier v0.2.0 numbers (ADR 0034, section 6) are
given for comparison; they come from a different day and possibly a different host.

**`/metrics`:** 401 without the token, 401 with a wrong one, 200 with the right one, and the
inference gauges are present. `/health`: 0.4.0.

**One user, one request at a time** (20 each; seconds, network included):

| | p50 | p95 | p99 / max | v0.2.0 p95 |
|---|---|---|---|---|
| `GET /v1/proteins/{id}` | 0.16 | 0.25 | 0.26 | — |
| `GET /v1/proteins/{id}/landscape` | 0.23 | 0.33 | 1.26 | — |
| `GET /v1/benchmarks` | 0.23 | 0.33 | 0.37 | — |
| `POST /v1/score` 35M, 300 aa | 0.56 | 0.64 | 0.89 | 0.92 |
| `POST /v1/score` 35M, 1,000 aa | 2.16 | 2.48 | 2.58 | 2.09 |
| `POST /v1/score` 150M, 300 aa | 1.54 | 1.91 | 2.51 | 1.85 |
| `POST /v1/score` 150M, 1,000 aa | 6.30 | 7.22 | 7.71 | 5.72 |
| precise job, 400 aa (whole) | 203 s | | | 163 s |

Every long computation is 19–26% slower than on v0.2.0 (35M at 1,000 aa ×1.19, 150M at 1,000
aa ×1.26, the precise job ×1.25), with one user and so nothing to queue behind. The code of a
pass did not change, only the thread it runs on. The likely cause is a slower host, but that is
not verified. The precise job now takes 203 s, over ADR 0029's three-minute target at the
400-residue limit. The limit is not changed here; that needs a second measurement.

**Load** (Locust `Visitor` mix, 120 s each, a minute apart; ms; one client address, so all users
share one client's limits):

| | 5 users: p50 / p95 / p99 | v0.2.0, 5 users: p50 / p95 / p99 | 20 users: p50 / p95 / p99 |
|---|---|---|---|
| `GET /v1/proteins/{id}` | 120 / 4,200 / **8,500** (n = 89) | 98 / 2,500 / 9,900 | 3,200 / 24,000 / 29,000 (n = 110) |
| `GET /v1/proteins/{id}/landscape` | 190 / 1,300 / 3,200 | 210 / 1,300 / 1,900 | 4,700 / 16,000 / 21,000 |
| `GET /v1/benchmarks` | 110 / 4,900 / 4,900 | 120 / 200 / 200 | 2,100 / 24,000 / 29,000 |
| `POST /v1/score` (200s) | 2,600 / 6,700 / 8,600 | 1,400 / 10,000 / 11,000 | 9,500 / 30,000 / 34,000 |
| `POST /v1/jobs` refused (429) | 120 / 5,200 / 5,200 | 73 / 11,000 / 11,000 | 400 / 27,000 / 28,000 |
| statuses | 179 × 200, 4 × 202, 26 × 429 | 191 / 4 / 31 | 232 × 200, 3 × 202, 153 × 429, 1 × 503 |
| memory, max (resident + cache) | 2,565 MiB (63% of 4 GiB) | — | 2,592 MiB (63%) |
| inference running, max | 1 | — | 1 |

**Verdict: read p99 at 5 users is 8.5 s, not under 1 s.** Bounding forward passes did not fix
slow reads on Cloud Run. At 5 users, every row is within the noise of a 2-minute run with n = 19
to 89, and `/v1/benchmarks` got worse. At 20 users the service is far past its capacity.

What the numbers say about the cause:
- **The pool held.** No scrape ever saw two passes running, and the one 503 came after 10.5 s,
  as designed. Ten-second scrapes are too sparse to see the queue, so the waiting gauge's
  maximum of 1 says little.
- **The time is spent outside the pool.** Two observations show it:
  - A 429 for a job takes no model work at all: a rate-limit check, then an answer. At 20 users
    those answers took up to 28 s.
  - A successful score has a wait of at most 10 s plus a pass of at most about 7 s, yet took
    up to 34 s.

  Requests waited for the event loop (or the GIL), not for a slot. On 2 vCPUs, one pass with 2
  ONNX threads still keeps both cores busy. Every Python step that needs the GIL competes with
  it:
  - decoding protein files (`get_protein` and `get_landscape` are sync routes);
  - building and serialising 20,000-cell responses;
  - computing ETags and gzipping on the loop.

  Locally (section 5) the same mix stayed fast, because two M4 Pro cores are much faster per
  operation.
- **Memory is not a constraint**: 63% of the limit at 20 users, flat during each run.

What would show the cause, as a follow-up (not done here):
- a live run with `MUTASCOPE_API_ONNX_THREADS=1`, which leaves one vCPU to the loop and the
  reads;
- a profile of the loop under load (where the 28-second 429s spend their time);
- moving gzip and ETag hashing of large bodies off the loop.

Each is a setting or a small change, and each should be measured live before it is adopted.

## 7. One ONNX thread on Cloud Run (experiment 1, kept)

Section 6 found requests waiting for the event loop, not for a slot. With `onnx_threads = 2`, one
pass keeps both vCPUs busy, and every Python step of every other request competes with it. PR #50
set `MUTASCOPE_API_ONNX_THREADS: "1"` in `env`, leaving one vCPU to the event loop and the reads.
Nothing else changed: same image (v0.4.0, redeployed from main), same pool (one pass at a time,
10 s wait), same method (`live.py --load-users 5 20 --load-seconds 120 --seed 50`). Results are in
`docs/live-measure-0036-t1-summary.json`. That redeploy also made the CORS origins live: both
website origins get `Access-Control-Allow-Origin`, and another origin gets none.

**Reads under load** (ms, p50 / p95 / p99):

| | 5 users, 2 threads | 5 users, 1 thread | 20 users, 2 threads | 20 users, 1 thread |
|---|---|---|---|---|
| `GET /v1/proteins/{id}` | 120 / 4,200 / 8,500 | **69 / 120 / 140** | 3,200 / 24,000 / 29,000 | **72 / 510 / 560** |
| `GET /v1/proteins/{id}/landscape` | 190 / 1,300 / 3,200 | **160 / 560 / 770** | 4,700 / 16,000 / 21,000 | 140 / 1,300 / 2,000 |
| `GET /v1/benchmarks` | 110 / 4,900 / 4,900 | **97 / 130 / 150** | 2,100 / 24,000 / 29,000 | **100 / 410 / 520** |
| `POST /v1/jobs` refused (429) | 120 / 5,200 / 5,200 | 65 / 79 / 100 | 400 / 27,000 / 28,000 | 54 / 71 / 330 |

**Scoring:**

| | 2 threads | 1 thread |
|---|---|---|
| 35M, 1,000 aa, one user, p95 | 2.48 s | 2.35 s |
| 150M, 300 aa, one user, p95 | 1.91 s | 1.75 s |
| 150M, 1,000 aa, one user, p95 | 7.22 s | 8.10 s |
| precise job, 400 aa | 203 s | **293 s** |
| `POST /v1/score`, 5 users, p50 / p95 / p99 | 2.6 / 6.7 / 8.6 s | 3.4 / 10 / 11 s |
| `POST /v1/score`, 20 users: answered / 503 | 39 / 1 | 19 / 21 |
| `POST /v1/score`, 20 users, slowest answer | 34 s | 14 s |
| memory max, 20 users | 2,592 MiB (63%) | 2,614 MiB (64%) |

**Decision: keep one thread** (the author's call on these numbers, 2026-10-18).
- **Reads now meet the goal.** Read p99 at 5 users is 140 ms for a protein, 770 ms for a
  landscape and 150 ms for benchmarks, all under 1 s.
- **Every answer is prompt again.** A refused job now takes 330 ms at 20 users, not 28 s, and
  the same holds for status polls, SSE events and errors.
- **Single-sequence fast mode barely changed.** 35M at 1,000 aa is as fast as with two threads,
  and 150M is up to 12% slower. A single-request pass gains little from the second thread here.
  Batched passes do gain from it: a precise job takes 44% longer.
- **The cost is capacity under heavy load.** At 20 users, half as many scores were answered,
  and 21 got a 503 `scoring-busy` after 10 s. That is the pool's designed answer: quick to
  read, with `Retry-After`.

Not changed by this, and still open:
- **The precise limit.** 400 residues now take 293 s, over ADR 0029's three-minute target. On
  these numbers about 250–300 residues would fit. The limit changes only after a measurement at
  the new value, as ADR 0031's rule asks.
- **Profiling the loop and moving gzip / ETag off it** (experiments 2 and 3 of section 6). They
  are no longer needed for the read goal, but they would still help under heavier load.

One request in this run, during the 35M 300-residue cell, failed its TLS handshake and was
counted as `no_answer`. It is a network event; it does not affect the percentiles.

## Consequences

- Under load, a fast-mode request waits its turn for a slot, behind at most a precise batch
  (about 6.5 s live at 400 residues, now about 8 s) and the fast requests ahead of it. A
  client sees either the answer or, after 10 s, a 503 with `Retry-After`; the website must
  handle that like a 429.
- `inference_concurrency` and `inference_wait_seconds` are settings (`.env.example`), so a
  different instance shape is a config change, measured first.
- On Cloud Run, ONNX Runtime runs with one intra-op thread (`MUTASCOPE_API_ONNX_THREADS=1`,
  section 7), and the other vCPU is the event loop's. Read p99 at 5 users is under 1 s. ADR
  0035's item 1 is closed.
- Precise jobs are slower (400 residues: 293 s), so the precise limit must be measured again and
  probably lowered. Scoring capacity under heavy load is about half, and the excess gets a 503
  within 10 s.
- The API's secrets live only in Secret Manager. The repository names them but never holds them,
  and the one GitHub secret is still `RELEASE_PLEASE_TOKEN`.

## Addendum (2026-10-20): precise limit 300

With one ONNX thread (section 7), a 400-residue precise job took 293 s on the live service: over
ADR 0029's target of about three minutes, and within 7 s of 300 s, Cloud Run's default request
timeout. This service sets `timeout_seconds: 900`, so the job did not fail, but the limit left no
margin against the target. `conf/deploy/cloudrun.yaml` now sets
`MUTASCOPE_API_PRECISE_MAX_LENGTH` to **300**. The image's default (600, `.env.example`) is
unchanged; it applies outside Cloud Run.

How long 300 residues take is **not measured**. Section 7's numbers suggest about 250–300
residues would fit, but that is an estimate, not a result. ADR 0031's rule asks for a measurement
at the new value: after the next deploy, run one 300-residue job on its SSE stream
(`tools/loadtest/live.py`) and record the time in `docs/load-test.md`, section 6. If it is still
over about three minutes, the limit goes lower.

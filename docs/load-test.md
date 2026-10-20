# Local load test of the API (Phase 4 Tasks 5 and 6)

> **The latencies here are M4 Pro numbers, not representative of the Space.** The Space has
> 2 shared vCPUs. The M4 Pro has 12 fast cores, so requests that ran in parallel here will queue
> there. Real latency is measured on the Space in Task 7. This run tests behaviour: the limits,
> errors and memory.

- **Date:** 2026-10-17, on branch `feat/api-hardening` (the code of the PR that adds this file)
- **Machine:** Apple M4 Pro, 24 GB RAM, macOS; nothing else running
- **Server:** the production start command, `python -m api.app.serve`: one process, one
  worker, `MUTASCOPE_API_ONNX_THREADS=2`, the real fp32 ONNX models (Hub `82e1528`) and the
  bundle at its pinned revision, `trusted_proxies = 127.0.0.1`, default limits (ADR 0032), an
  empty score cache. Each scenario gets a **fresh process**, so no scenario sees another's
  counters or cache.
- **Load:** Locust 2.46.7 (`tools/loadtest/`). Each user sends its own `X-Forwarded-For`, as
  clients will reach the Space through its proxy, so each user is a separate client.
- **Raw results:** `docs/load-test-summary.json` (status counts per client, Locust percentiles,
  memory and gauges once a second).
- **Reproduce:** `uv run --project tools/loadtest python tools/loadtest/run.py --out <dir>`
  (about 20 minutes).

## 1. The 429s start at the configured limits

Each scenario sends as fast as it can for 50 s. That is inside one 60 s moving window, so a
client's accepted count must equal its limit **exactly**.

| Scenario | Limit (default) | Users | Accepted | Refused (429) | Expected |
|---|---|---|---|---|---|
| `read`: `GET /v1/proteins/{id}` | `rate_limit_read` 120/min | 1 | **120** | 191,413 | 120 |
| `score`: `POST /v1/score` | `rate_limit_score` 20/min | 1 | **20** | 182,489 | 20 |
| `score_global`: `POST /v1/score` | `rate_limit_score_global` 120/min | 10 addresses | **120** in total (10–13 each) | 246,403 | 120, none over 20 |
| `jobs`: `POST /v1/jobs`, one a second | `rate_limit_jobs` 2/min | 1 | **2** | 48 | 2 |
| `streams`: 6 users, one address, one 400-residue job | `max_streams_per_client` 4 | 6 | at most **4** open (gauge max 4) | 22 | 4 |

Every 429 was checked by the Locust user to be an `application/problem+json` body, with a
numeric `Retry-After` for rate limits. None failed that check. In `streams`, the refused users
retry every 2 s, and once the job finishes every stream replays its events and closes (72
accepted in all). The refused ones answer in 1 ms (median), and the four held streams lasted
the job's 21 s.

## 2. No 5xx at 5 and 20 users

Mixed traffic (`Visitor`): think time 1–3 s; browsing protein details, landscapes and the
benchmarks; fast scores of 50–1,000 residues on 35M or 150M (half of them a repeat of the
user's own sequences, so cache hits); now and then a precise job of 30–120 residues followed
on its stream.

| Run | Duration | Requests | 2xx | 429 | 5xx | Failures | Server error lines |
|---|---|---|---|---|---|---|---|
| 5 users | 180 s | 524 | 501 | 23 | **0** | **0** | 0 |
| 20 users | 600 s | 6,922 | 6,249 | 673 | **0** | **0** | 0 |

Counts are the users' own tally of every response. Locust's request statistics, which the
latency table uses, count a few fewer (6,917 at 20 users).

The 429s at 20 users are the limits working as intended. 360 refusals on `POST /v1/score`:
the accepted rate was 1.97 a second (118 a minute), against the global fast-mode limit of 120 a
minute. 313 on `POST /v1/jobs`: one job runs at a time, so jobs from 20 users meet the queue, the
one-active-job rule and the job rate limit. The job queue's gauge peaked at 3 of 4. No request
failed for any other reason, and the server logged no error and no traceback in any scenario.

## 3. Memory stays bounded

Resident memory of the server process, once a second. The two ONNX models and the bundle
index take about 0.9 GB at startup.

| Run | Start | Mean by quarter of the run (MB) | Max | End |
|---|---|---|---|---|
| 5 users, 180 s | 900 | 1,264 → 1,543 → 1,789 → 1,998 | 2,022 | 2,021 |
| 20 users, 600 s | 906 | 2,406 → 2,471 → 2,503 → 2,523 | 2,560 | 2,529 |

At 20 users memory reaches a plateau. The rise between quarters falls (+65, +32, +20 MB), and
over the last half of the run it is 0.8%. At 5 users it was still rising after 3 minutes, but
below the 20-user level. The growth follows the largest forward passes run at the same time,
not the number of requests: 20 users made 13 times as many requests in the 10 minutes as 5
users made in 3, and memory ended only 25% higher. That is how ONNX Runtime's memory arena
behaves (it keeps the buffers of the largest inputs it has seen, here 150M at up to 1,000
residues, several at once in the thread pool). It is not a per-request leak, and the job store,
rate-limit counters and stream slots are all bounded (ADR 0032). This explanation is an
inference from the shape of the curves, not a measurement of the arena. The Space has 16 GB.
Task 7 should watch the process's memory there over a longer run.

One reading is unexplained. In the `jobs` scenario, where the server was nearly idle (one
request a second), resident memory fell from 939 MB to 281 MB within 5 s and stayed there. That
is most likely macOS compressing idle pages, which leaves the resident figure without them. It
is not memory the server freed. It does not affect the conclusions above, which come from busy
servers.

## 4. Latency (M4 Pro, not representative of the Space)

Milliseconds, as Locust measured them (whole request, from the client).

| Route | 5 users: median / p95 / p99 | 20 users: median / p95 / p99 | Max (20 users) |
|---|---|---|---|
| `POST /v1/score` (accepted) | 94 / 390 / 640 | 72 / 520 / 680 | 1,001 |
| `GET /v1/proteins/{id}` | 7 / 20 / 33 | 6 / 21 / 38 | 71 |
| `GET /v1/proteins/{id}/landscape` | 49 / 300 / 330 | 36 / 230 / 350 | 781 |
| `GET /v1/benchmarks` | 18 / 23 / 36 | 11 / 22 / 36 | 58 |
| `POST /v1/jobs` (accepted) | 4 / 5 / 7 | 1 / 8 / 19 | 32 |
| `GET /v1/jobs/{id}/stream` (whole job) | 730 / 1,300 / 1,700 | 910 / 2,000 / 2,600 | 2,898 |
| Any 429 (by route) | median 3, p95 4 | median 1, p95 ≤ 5 | 31 |

A stream's time is the whole job: waiting in the queue and scoring 30–120 residues. Fast mode
includes 150M at up to 1,000 residues, and its maximum (1.0 s) is close to the bare 150M pass
on this machine (0.6 s at 1,000 residues, ADR 0029) plus queueing behind parallel requests.
A refusal costs about a millisecond, which is the point: an abuser at the limit costs almost
nothing.

## 5. The same tests against the container (Task 6, ADR 0033)

> **All latencies in this section are from a local container, not the Space.** The CPU limit
> is the Space's (2 CPUs), but the cores are the M4 Pro's, and the memory limit was not the
> Space's (below).

- **Date:** 2026-10-18, branch `feat/docker`
- **Server:** the API image `mutascope-api:dev` (linux/arm64, native), one fresh container per
  scenario, `docker run --cpus=2 --memory=16g`, its own start command (`python -m
  api.app.serve`, ONNX threads 2, BLAS at 1, offline, baked-in fp32 models and bundle). Requests
  reach the container from the Docker bridge's gateway (172.17.0.1), which is the trusted
  proxy, so each Locust user's `X-Forwarded-For` is its own client, as behind the Space's proxy.
- **Memory limit:** Docker Desktop's VM had 7.75 GiB. `--memory=16g` was set, but the
  effective ceiling was the VM's 7.75 GiB, not 16 GB. Nothing came close to either: the
  highest reading in any scenario was 3.2 GB.
- **Memory measure:** the container's cgroup, once a second. `anon` is heap and other
  anonymous memory, which a leak would grow. `usage` is what `docker stats` shows (it also
  counts active page cache) and was 10–50 MB above `anon` throughout. This is not the
  process-tree RSS of sections 3 and 4, so the two are not directly comparable.
- **Raw results:** `docs/load-test-container-summary.json`.
- **Reproduce:** `make api-image`, then
  `uv run --project tools/loadtest python tools/loadtest/run.py --image mutascope-api:dev --out <dir> read score score_global jobs streams load5 load20 soak5`
  (about 40 minutes).

### 5.1 Limits: identical to the local process

| Scenario | Limit | Accepted | Refused (429) | Expected |
|---|---|---|---|---|
| `read` | 120/min | **120** | 78,778 | 120 |
| `score` | 20/min | **20** | 65,743 | 20 |
| `score_global`, 10 addresses | 120/min | **120** in total (11–13 each) | 150,059 | 120, none over 20 |
| `jobs` | 2/min | **2** | 48 | 2 |
| `streams`, 6 users, one address | 4 open | at most **4** open (gauge max 4) | 46 | 4 |

In `streams`, the 400-residue precise job ran longer on 2 CPUs than the 45 s window (21 s in
the local run), so the four held streams were still open when Locust stopped. No stream
therefore completed inside the window, and the cap is shown by the gauge and the refusals.

### 5.2 No 5xx at 5 and 20 users

| Run | Duration | Requests | 2xx | 429 | 5xx | Failures | Server error lines / tracebacks |
|---|---|---|---|---|---|---|---|
| 5 users | 180 s | 418 | 408 | 10 | **0** | **0** | 0 / 0 |
| 20 users | 600 s | 1,921 | 1,803 | 118 | **0** | **0** | 0 / 0 |
| 5 users, soak | 1,200 s | 3,141 | 2,942 | 199 | **0** | **0** | 0 / 0 |

### 5.3 Memory: levels off (soak, 5 users, 20 minutes)

Container `anon` memory in MB, at each minute (the sample nearest it):

| minute | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MB | 1,222 | 2,132 | 2,285 | 2,295 | 2,375 | 2,375 | 2,473 | 2,503 | 2,477 | 2,485 | 2,485 |

| minute | 11 | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 |
|---|---|---|---|---|---|---|---|---|---|---|
| MB | 2,472 | 2,481 | 2,490 | 2,496 | 2,477 | 2,484 | 2,484 | 2,484 | 2,484 | 2,484 |

Means by quarter: 2,248 → 2,473 → 2,488 → 2,485 MB. Memory rises in steps for about 7 minutes
and then stays between 2,472 and 2,503 MB for the remaining 13, while traffic continued at the
same rate (3,141 requests over the 20 minutes). Memory does not keep rising, so there was
nothing to fix. The job store, counters and slots are bounded (ADR 0032). Finished jobs expire
after an hour, longer than the soak, so the 50 jobs it ran were all still held at the end
without a visible effect on the curve.

The steps fit ONNX Runtime's arena keeping the buffers of the largest passes run so far: each
step is a new, larger set of concurrent forward passes. That is the same inference as in
section 3, from the shape of the curve, not a measurement of the arena. At 20 users memory
levelled off higher, as the local run did: by minute 6 it was 3,197 MB, then 3,193, 3,191 and
3,186 MB at minutes 7–9 (quarter means 2,634 → 2,989 → 3,140 → 3,194). The Space has 16 GB.

### 5.4 Latency (local container, not the Space): 2 CPUs saturate at 20 users

Milliseconds, whole request, as Locust measured them.

| Route | 5 users (180 s): median / p95 / max | soak, 5 users (20 min): median / p95 / max | 20 users (600 s): median / p95 / max |
|---|---|---|---|
| `POST /v1/score` (accepted) | 560 / 5,200 / 5,846 | 150 / 2,900 / 7,664 | 5,600 / 46,000 / 58,458 |
| `GET /v1/proteins/{id}` | 11 / 45 / 288 | 9 / 28 / 103 | 94 / 580 / 2,018 |
| `GET /v1/proteins/{id}/landscape` | 74 / 480 / 2,033 | 41 / 260 / 1,600 | 420 / 3,400 / 11,120 |
| `GET /v1/benchmarks` | 12 / 23 / 24 | 18 / 27 / 71 | 150 / 470 / 918 |
| `GET /v1/jobs/{id}/stream` (whole job) | 3,200 / 12,000 / 12,847 | 3,400 / 9,900 / 11,209 | 210,000 / 325,000 / 325,409 |

**This is the main finding of the container run.** With 2 CPUs, 20 users overload fast mode:

- a median of 5.6 s and a p95 of 46 s, against 72 ms and 520 ms for the unconstrained process
  in section 4;
- precise jobs that wait minutes, because they share the 2 CPUs with fast mode;
- 459 fast scores accepted in 600 s (46 a minute). The global limit of 120 a minute was never
  reached, so the server's own speed, not the limit, held the rate down. The 20 users made 1,921
  requests here against 6,922 locally, because each waits for its answer before the next.

The likely cause is that every fast request is its own ONNX Runtime run with 2 intra-op
threads, and nothing bounds how many run at once. With many concurrent requests, more threads
compete for the 2-CPU quota than it has, and each request takes longer than it would in turn.
That is an inference: thread counts and CPU throttling were not measured. The free Space has the
same 2-CPU limit, so this probably holds there.

Not changed in this task: ADR 0032 leaves the limits to Task 7, which measures the Space. The
options are to bound concurrent forward passes (a semaphore before the scorer), to lower
`rate_limit_score_global`, or to default to 35M (ADR 0029's rule). At 5 users the median score
is 150–560 ms, and the p95 (2.9–5.2 s) is already above ADR 0029's 2 s rule. That rule is for
a single 1,000-residue request, but under load the Space will likely miss it.

## 6. The deployed service: Google Cloud Run (Task 7, ADR 0034)

> **These are live numbers**, measured on 2026-10-18 against `v0.2.0` on Cloud Run
> (`europe-west1`, 2 vCPU, 4 GiB, one instance) from a laptop in Germany with
> `tools/loadtest/live.py`. Latency is the client's wall time for the whole request, network
> included (round trip to the service: `/health` p50 0.20 s, p95 0.28 s, n = 20). Raw results:
> `docs/live-measure-summary.json`.

### 6.1 Fast mode, one request at a time

Twenty requests per cell, each a new random sequence (every one a cache miss, checked by the
`X-MutaScope-Cache: miss` header), 3.5 s apart, after one uncounted warm-up per model.

| model | residues | p50 (s) | p95 (s) | max (s) |
|---|---|---|---|---|
| ESM-2 35M | 300 | 0.59 | 0.92 | 1.08 |
| ESM-2 35M | 1,000 | 1.83 | 2.09 | 2.25 |
| ESM-2 150M | 300 | 1.56 | 1.85 | 1.89 |
| ESM-2 150M | 1,000 | 4.80 | 5.72 | 5.74 |

**ADR 0031's rule applies: the default model is now 35M.** 150M's p95 at 1,000 residues is
5.72 s, above 2 s. The live server is slower than the M4 Pro at the same 2 threads, where 150M
took 0.53 s at 1,000 residues (ADR 0031). 35M itself is just over 2 s at 1,000 residues (p95 2.09
s, of which ~0.2 s is the network), so the rule's choice is the faster model, not one that meets
2 s at every length. 150M stays available with `"model": "esm2_150m"`.

### 6.2 Precise mode and the length limit

One job at a time, followed on its SSE stream (as the website does; on Cloud Run an instance
has CPU only while a request is open):

| residues | time to `succeeded` |
|---|---|
| 600 (the provisional limit) | 406 s |
| 400 | 163 s |

600 residues took 6.3× the M4 Pro's 64 s, more than twice ADR 0029's target of about 3
minutes. **The limit is now 400** (`MUTASCOPE_API_PRECISE_MAX_LENGTH`). Between the two
measurements time grows as about length^2.24, so 450 residues would take about 210 s, over the
target. 400 was measured, not extrapolated. (With one ONNX thread, 400 took 293 s and the limit
is now 300, not yet measured: ADR 0036, addendum.)

### 6.3 Cold start

After 21 idle minutes (the service scales to zero), the first `GET /ready` took **20.5 s**; the
next took 0.22 s. That is what the first visitor after a quiet period waits. The website must
work with the API asleep (CLAUDE.md), so only live scoring waits for it.

### 6.4 Five users for two minutes

Locust's `Visitor` mix, 5 users, 120 s. Through Cloud Run's front end the five are one client
(this laptop's address), so they share one client's limits.

- **Statuses:** 191 × 200, 4 × 202, 31 × 429 (226 requests). 29 of the 429s are the API's own
  limits (17 job submissions, refused by the job rate limit or the one-active-job rule, and 12
  scores over 20 a minute), each a problem body.
- **Two 429s were Cloud Run's, not the API's:** "The request was aborted because there was no
  available instance" (14:10:36 UTC, Cloud Logging), both job submissions. With at most one
  instance, the platform turned two requests away while that instance was busy scoring. Locust
  counted them as failures because they carry no problem body; the client waited up to 11 s.
- **Latency of the 200s** (p50 / p95 / p99, ms): `POST /v1/score` 1,400 / 10,000 / 11,000;
  `GET /v1/proteins/{id}` 98 / 2,500 / 9,900; landscapes 210 / 1,300 / 1,900; benchmarks
  120 / 200 / 200. Reads wait behind CPU-bound scoring: the same saturation as the 20-user
  container run (section 5.4), reached here at 5 users because the CPUs are slower.

### 6.5 What changed from this

In `conf/deploy/cloudrun.yaml` (`env`), applied by the next deploy:

- `MUTASCOPE_API_SCORE_DEFAULT_MODEL=esm2_35m` (6.1);
- `MUTASCOPE_API_PRECISE_MAX_LENGTH=400` (6.2);
- `MUTASCOPE_API_TRUSTED_PROXIES=169.254.169.126/32`: all 413 requests of the first deploy came
  from this address (Cloud Logging, `jsonPayload.client`), so until now every visitor shared one
  quota. With it, the client is read from `X-Forwarded-For` from the right, as ADR 0032 says.
  To check after the redeploy: the access lines show visitors' addresses, not this one.

Not changed: the rate limits, and the open question of bounding concurrent forward passes
(ADR 0033). At 5 users the platform, not the API, refused 2 of 226 requests.

## 7. With the inference pool: before and after locally, then live (Phase 5 Task 0, ADR 0036)

One forward pass at a time (`inference_concurrency = 1`). Details and every number are in ADR
0036, sections 5 and 6. The live numbers are in `docs/live-measure-0036-summary.json`.

- **Local container** (`run.py --limits "--cpus=2 --cpuset-cpus=0,1 --memory=4g"`, `load5` and
  `load20_2m`, old and new image): at 20 users fast mode's p95 fell from 16 s to 7.6 s, the
  landscape p99 from 2.9 s to 1.6 s, and memory from 2,584 to 1,989 MB. Fast mode's median rose
  from 2.4 s to 3.4 s, because requests now wait their turn.
- **Cloud Run, v0.4.0** (`live.py --load-users 5 20`): the pool held at one pass, and memory
  peaked at 2,592 MiB (63% of 4 GiB). Reads did **not** get fast. At 5 users
  `GET /v1/proteins/{id}` had a p99 of 8.5 s, against 9.9 s before, the same within noise. At 20
  users reads, scores and even 429 answers took up to 28–34 s. The waiting is on the event loop
  (or the GIL), not on inference slots. Every long computation was also 19–26% slower than on
  v0.2.0 with one user (precise, 400 aa: 203 s against 163 s).
- **Cloud Run, one ONNX thread** (`MUTASCOPE_API_ONNX_THREADS=1`, ADR 0036 section 7; summary
  in `docs/live-measure-0036-t1-summary.json`): read p99 at 5 users is 140 ms (protein), 770 ms
  (landscape) and 150 ms (benchmarks); at 20 users 560 / 2,000 / 520 ms, and refused requests
  answer within 330 ms. Costs: the 400-residue precise job takes 293 s (203 s with two threads),
  and at 20 users 19 scores were answered and 21 got a 503 after 10 s (two threads: 39 and 1).
  Kept.
- **Running `live.py` twice on a warm instance needs a new `--seed`.** The second run otherwise
  sends the same sequences, which are cache hits, and the script stops rather than time them.
  A connection closed without an answer is now counted (`no_answer`) instead of ending the run:
  the first attempt lost one 150M request that way, with the instance still up (its counters
  continued).

## 8. The precise limit with one ONNX thread (2026-10-20)

Precise jobs on the live service (v0.11.1, one ONNX thread, `precise_max_length` 400 at the
time), one job per length, each followed on its SSE stream (`live.py --precise-only`;
`docs/live-measure-precise-summary.json`):

| residues | time to `succeeded` |
|---|---|
| 250 | 101.0 s |
| 300 | 147.8 s |
| 350 | 205.9 s |
| 400 (section 7) | 293 s |

300 residues stay under ADR 0029's target of about 3 minutes; 350 do not. One job per length,
from one client on a mobile connection, so these times are a single sample each. Twice the
stream was cut before the job finished: once with no data for 600 s (300, repeated), once ended
early (350, cancelled and repeated). The server ends a stream only when its job has finished,
and a job advances only while a stream is open, so `live.py` now resumes a cut stream with
`Last-Event-ID`, as the website does. The repeated 350 run needed no resume.

## What this does not show

- Latency from other places than a laptop in Germany, or with more than one client address
  (section 6 is one address, so the per-client limits shape the 5-user run).
- Many-address abuse beyond the global limits (see `docs/threat-model.md`).
- Memory over hours (the soak was 20 minutes, on Linux in a container), and a 16 GB limit
  (the Docker VM had 7.75 GiB).

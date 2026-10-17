# Local load test of the API (Phase 4 Task 5)

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

## What this does not show

- Throughput or latency on 2 vCPUs (Task 7).
- Many-address abuse beyond the global limits (see `docs/threat-model.md`).
- Memory over hours, and on Linux.

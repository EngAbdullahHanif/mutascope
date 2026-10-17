# 0031 — Live scoring: fast mode, precise-mode jobs with SSE, the score cache

- Status: accepted
- Date: 2026-10-17
- Builds on: ADR 0029 (fp32 ONNX ESM-2 35M / 150M, section 6: deployment rule, precise mode on
  35M under about 3 minutes), ADR 0030 (API core, problem details, settings as pins)

## Context

The API loads two fp32 ONNX scorers at startup (ADR 0030) but does not use them yet. A user
with a sequence that is not in ProteinGym should be able to get its landscape:

- **Fast mode** (wild-type marginal) is one forward pass: 0.25 s (35M) and 0.53 s (150M) at
  1,000 residues on the M4 Pro. It can be answered inside the request.
- **Precise mode** (masked marginal) needs one pass per residue: 64 s at 600 residues and
  241 s at 1,000 (35M). It has to run in the background, with progress.

The free Space has 2 vCPUs, so the server can run only one precise job at a time. And every
result is zero-shot, so every response must say so.

## Decision

### 1. Routes

| Route | Does |
|---|---|
| `POST /v1/score` | fast mode: `{sequence, model?, mutations?}` → the full 19 × L landscape, or the scores of up to 50 mutations |
| `POST /v1/jobs` | precise mode (35M only): `{sequence}` → 202, the job, `Location: /v1/jobs/{id}` |
| `GET /v1/jobs/{id}` | status, progress (`done` / `total` residues) and, once `succeeded`, the landscape |
| `GET /v1/jobs/{id}/stream` | Server-Sent Events (below) |
| `DELETE /v1/jobs/{id}` | cancel a queued or running job; 409 if it has finished |

`model` is `esm2_35m` or `esm2_150m`. Without it, the request gets `score_default_model`
(`esm2_150m`). ADR 0029's rule (35M if 150M's p95 on the Space exceeds 2 s at 1,000 residues)
is therefore one environment variable, not a code change. `/ready` reports the default and
the precise-mode limit.

Every live response has `kind: "zero_shot"` and `label: "Zero-shot"` (both `Literal`s in the
schema), the method (`wt_marginal` / `masked_marginal`), the served config
(`esm2_150m_onnx_fp32`), its Hub id, file and revision, and the version stamp. Grids have
the bundle's layout: `(L, 20)`, row-major, `AA_ORDER`, 0-indexed residues, wild type 0. Scores
are rounded to 4 decimals (1e-4 nats; a landscape's SD is about 3, and fp32-vs-PyTorch logits
differ by less than 1e-4). `X-MutaScope-Cache: hit|miss` says whether a fast landscape came from
the cache. The body is the same either way.

### 2. Validation before any model runs

- **Sequence:** 1–1,022 characters (ESM-2's window without tiling), only the 20 canonical
  amino acids in uppercase. The message names the first bad character and its 1-indexed
  position, and how many others there are: `found 'X' at position 3 (and 2 more)`. The
  OpenAPI schema carries the pattern, so generated clients and the fuzzer know it.
- **Mutations:** 1–50 items, each parsed by `data.parse.parse_mutation` (the only 1→0-indexed
  conversion), so the format and the "mutant ≠ wild type" rules are the pipeline's. Every
  mutation whose position is past the end or whose wild type does not match is reported, each
  with its own `loc` (`["body", "mutations", 2]`), not just the first.
- **Unknown fields** are refused (`extra="forbid"`), so a typo like `mutation` is a 422, not a
  silently ignored field.
- **Precise mode:** longer than `precise_max_length` → 422
  `urn:mutascope:problem:sequence-too-long`, with the limit in the detail.

All of these are RFC 9457 problems (ADR 0030). A body that is not valid UTF-8 is FastAPI's 400,
now documented on both POST routes.

### 3. Jobs: one at a time, a bounded queue, one hour

`api.app.jobs.JobManager` lives on the event loop. One worker task takes queued jobs in order
and runs each forward batch in a thread (`asyncio.to_thread`), so the API keeps serving while a
job scores. All job state changes on the loop, between batches, so no lock is needed.

- **Batches are the scorer's own.** The job asks the scorer how it would batch the masked passes
  (`masked_batches`, 16 residues per forward call) and calls `landscape(seq, positions=batch)`
  once per batch. A progress event is therefore exactly one forward call, and the numbers are
  those of the scorer the rest of the code uses.
- **Queue:** at most `job_queue_size` (4) jobs wait behind the running one. Another submission
  is 429 `urn:mutascope:problem:queue-full`. Fast mode never queues.
- **Cancellation:** a queued job leaves the queue (its slot is free at once). A running job is
  marked `cancelled` at once, and the worker drops the batch in flight when it returns. A
  cancelled or failed job's partial landscape is never cached.
- **Expiry:** a finished job (succeeded, failed, cancelled) is deleted `job_ttl_seconds` (3,600)
  after it finished, and then answers 404. `expires_at` says when.
- **Failure:** the exception is logged, and the job says only "Scoring failed; the error was
  logged."

### 4. Server-Sent Events: a replayable log per job

Each job keeps an append-only list of events: `queued`, `running`, one `progress` per forward
batch, then `succeeded`, `failed` or `cancelled`. A `progress` event (`JobProgress`, in the
OpenAPI components) carries `done`, `total` and that batch's landscape rows (`position`, 20
scores). A status event carries the job as `GET /v1/jobs/{id}` returns it, so `succeeded`
includes the landscape. An event's `id` is its index in the list. A stream replays the list
(after `Last-Event-ID` when the client sends one), then waits for new events, and ends when
the job has finished. A client that connects late or reconnects therefore sees every event
exactly once and in order. FastAPI 0.143's `EventSourceResponse` frames the events and sends a
keep-alive comment every 15 s. Starlette's gzip middleware leaves `text/event-stream` alone.

### 5. The score cache (diskcache)

A key is the sha256 of the JSON list `[model id, revision, method, sha256(sequence)]`. The model
id is `hf_id/file` (`cache_model_id`, the pipeline's), the revision the Hub commit, and the
method `wt` or `masked`, as in the pipeline cache's keys. The value is the `(L, 20)` float32
landscape as bytes. A value of the wrong length is treated as a miss. The cache is
size-bounded (`score_cache_bytes`, 256 MB, LRU eviction), and its entries never expire: a
pinned model gives the same scores for the same sequence.

Fast mode always computes the full landscape, so a request for a few mutations and a request
for the full landscape of the same sequence share one entry. An identical precise job returns
a job that has already `succeeded` (`cached: true`), without entering the queue.

Files read from a local export (`onnx_local_dir`: tests, unreleased files) are keyed by
`revision = "local:<dir>"`, never by the Hub commit. Stand-in or unreleased files can therefore
never fill entries that a released model will read. This is the same rule as
`model.local_dir` turning the pipeline cache off (ADR 0029).

### 6. Precise-mode length limit: 600 residues, provisional

ADR 0029 says to set the limit from a measurement on the Space so that a job stays under about
3 minutes. The Space does not exist yet (Task 6). The author chose 600 residues for now
(`precise_max_length`, environment `MUTASCOPE_API_PRECISE_MAX_LENGTH`). That is 64 s on the M4
Pro at 2 threads, so the limit holds unless the Space is more than 2.8× slower. After the
first deploy, the Space measurement sets the value.

### 7. Agreement with our PyTorch scores, not with the bundle

The bundle's zero-shot landscapes are ESM-2 650M and SaProt only
(`export.zero_shot_models`). The live models are 35M and 150M, so comparing the two would
compare different models. The author chose this reference instead:
`api/tests/golden/live_TCRG1_MOUSE_Tsuboyama_2023_1E0L.json`, our PyTorch landscapes of the
bundle's TCRG1 target (37 residues): 35M and 150M wild-type marginal, and 35M masked marginal.
`python -m api.tests.make_live_golden` writes it from the pipeline cache only, and a missing
or partial entry is an error. The slow test starts the API with the real fp32 files from the
pinned Hub revision, scores the target through `POST /v1/score` and a job's stream, and
requires a Spearman of at least 0.99 per landscape. It leaves out the wild-type cells, which
are 0 in both and would only inflate the agreement. 0.99 is the model gate's per-assay
threshold.

### 8. Schemathesis

`api/tests/test_schemathesis.py` runs the API in a real uvicorn server (with its lifespan, the
bundle fixture, the stand-in graphs and the job worker) and fuzzes every operation from the
OpenAPI schema. The checks: no 5xx, and status code, content type, headers and body as
documented. A request that violates the schema must get a 4xx. Left out:
`positive_data_acceptance`, because some rules cannot be written in OpenAPI (wild type
matching the sequence, the length limit from settings, unknown job ids). Those requests are
valid by schema and correctly 404 or 422, and `test_live.py` covers them.

## Evidence

- **Agreement (slow, real fp32 files at `82e1528`):** Spearman 0.99999997 (35M fast),
  0.99999997 (150M fast) and 0.99999997 (35M precise). Each equals the Spearman of the golden
  landscape with its own 4-decimal rounding (3 or 4 ties created by the rounding), so the
  ranks agree exactly up to that rounding. The test can tell the methods apart: the golden
  35M wild-type and masked landscapes differ by up to 4.64 nats, with a Spearman of 0.977
  between them.
- **Cost through the API** (M4 Pro, real fp32 files, 2 threads, random sequences): a
  600-residue precise job took 63.8 s from `POST` to the last stream event, with 38 progress
  events (one per 16 residues). The bare scorer takes 63.9 s (ADR 0029), so splitting the job
  into per-batch calls adds no measurable time. Fast mode at 1,000 residues, the whole request
  with JSON: 0.29 s (35M) and 0.60 s (150M) on a miss, 0.037 s on a cache hit.
- **Fuzzing:** about 100–156 generated requests per operation over the 11 operations,
  including 31 successful fast scores and 57 accepted jobs. Two findings, fixed (BUILD_LOG
  entry 31): an undocumented 400 for a body that is not UTF-8, and a 405 whose `Allow` header
  named only `GET` for `/v1/jobs/{id}`, which also serves `DELETE`. The 405 handler now lists
  every method of every route that matches the path.
- **Tests with stand-in graphs** (`test_live.py`, 28): the validation cases, fast scores
  equal to the scorer's own landscape, mutation lookup, cache hits (same bytes, no new forward
  passes, also after a restart), event order and ids, `Last-Event-ID` resume, cached jobs,
  queue full (429), cancellation (queued and running), 404s, 409s, expiry at exactly one hour,
  a failed job without internals, and a stream that waits for events.

## Consequences

- New: `api/app/{live,live_routes,jobs,score_cache}.py`. `load_scorers` returns the concrete
  `OnnxEsm2Scorer` (the live code reads its Hub id and file). `AppState` gains `live`,
  `score_cache`, `jobs` and `settings`. `lifespan` starts the job worker and stops it at
  shutdown.
- New settings: `score_default_model`, `precise_max_length`, `job_queue_size`,
  `job_ttl_seconds`, `score_cache_dir` (default under the system temp directory),
  `score_cache_bytes`.
- Dependencies: `diskcache` 5.6.3 (`api` extra), `schemathesis` 4.31.1 (dev). The fastapi
  lower bound moves to 0.143 for `fastapi.sse`.
- Jobs live in one process's memory. A restart loses queued and running jobs (finished results
  stay in the score cache). With more than one worker process the queue would no longer be
  one queue. The Space runs one process, and this is acceptable there.
- Still to do in Phase 4: `/metrics`, slowapi rate limiting (the job queue bounds precise mode;
  nothing bounds fast mode yet), CORS, Sentry, and measuring the Space to set the 35M/150M
  default and the precise limit.

## Alternatives considered

- **A task queue (Celery, RQ, arq) or a separate worker process:** survives restarts and
  scales out, but needs a broker (Redis) that the free Space does not have, for one job at a
  time. The in-process worker has the same API, and a broker can replace it later.
- **sse-starlette:** FastAPI 0.143 has `EventSourceResponse` built in, with keep-alive pings.
- **Polling only (no SSE):** simpler, but a 3-minute job would need many requests and could
  not stream rows.
- **Compare the live models against the bundle's ESM-2 650M:** passes only with a far lower
  threshold, and it would test model similarity, not serving correctness.
- **Cache only full requests, or key by the mutation list:** fast mode costs the same for one
  mutation as for the full landscape. One entry per sequence makes every later request free.

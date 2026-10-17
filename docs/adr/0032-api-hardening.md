# 0032 — API hardening and observability: limits, client identity, logs, metrics, one process

- Status: accepted
- Date: 2026-10-17
- Builds on: ADR 0030 (API core, problem details, settings as pins), ADR 0031 (live scoring,
  in-process job queue)

## Context

ADR 0031 made the API score sequences live on a free Space (2 vCPUs, one container). Nothing
bounded fast mode, one client could fill the job queue, an event stream could be held open
forever, any body size was read, and the logs were not tied to requests. The API must stay usable
for everyone while one abuser is active, must never leak a submitted sequence (it may be
unpublished), and must be observable without a log search service. The threats are listed one
by one, with the limit that addresses each, in `docs/threat-model.md`.

## Decision

Every limit is a setting (`api.app.settings`, environment `MUTASCOPE_API_*`). The defaults are
provisional: Task 7 measures the Space and revisits them.

### 1. Who the client is

Rate limits are per client, and the client is an IP address it cannot choose
(`api.app.clients.client_address`):

- The socket address, unless the connection comes from one of `trusted_proxies` (addresses or
  networks; comma-separated in the environment; empty by default).
- From a trusted proxy, `X-Forwarded-For` is read **from the right**. Each proxy appends the
  address it received the request from, so trusted hops are skipped and the first address that
  is not one is the client. Anything the client wrote into the header is further left and never
  used. A malformed entry ends the walk at the last trusted hop. IPv6 is normalised.
- uvicorn's own `--proxy-headers` is off in both start commands. By default it believes
  `X-Forwarded-For` from 127.0.0.1, which would make the rule above moot behind a local proxy.

On the Space, `trusted_proxies` must be the Space proxy's range (measured at deploy, Task 6).
If it is empty, all users share one quota. If it is too broad, spoofing works again.

### 2. Rate limits (slowapi)

| Group | Routes | Default | Key |
|---|---|---|---|
| `read` | `/v1/proteins*`, `/v1/benchmarks`, `GET` / `DELETE /v1/jobs/{id}`, opening a stream, `/metrics` | 120/min | client |
| `score` | `POST /v1/score` | 20/min | client |
| `score_global` | `POST /v1/score` | 120/min | everyone |
| `jobs` | `POST /v1/jobs` | 2/min and 10/hour | client |

`/health`, `/ready`, `/docs` and `/openapi.json` are not limited. Limits are **moving windows**
(at most N in any 60 s), so there is no double burst at a window boundary. A refusal is a 429
problem `urn:mutascope:problem:rate-limited` with `Retry-After` (seconds until the window has
room). Limits are checked as dependencies, before the body's content is validated, so an
over-limit client gets 429 even for an invalid body. A refused request still counts. Cache hits
count too: the limit is on requests, not on CPU.

`score_global` is about what 2 vCPUs can answer at 1,000 residues with 150M (0.6 s each on the
M4 Pro). Per-client limits alone let many addresses add up to more than the server can do.

**slowapi without its decorators.** slowapi's `@limiter.limit("…")` registers a limit string
on one module-level `Limiter` when the routes are imported. Our limits come from each app's
settings, and the tests build many apps with different limits in one process. Each app
therefore builds its own slowapi `Limiter` (memory storage, `moving-window`) in `create_app`,
and the routes depend on `api.app.ratelimit.rate_limit(...)`, which calls the limiter's
strategy (`Limiter.limiter.hit` / `get_window_stats`) with the client key. slowapi provides the
storage and the strategy. The FastAPI dependency replaces only the decorator.

Beyond request rates:

- **Active jobs per client:** `max_active_jobs_per_client` (1) queued or running jobs, then 429
  `too-many-jobs`. Without it, one client could fill the queue of 4 within the job rate
  limit, and everyone else would get `queue-full`. A cached result never counts as active.
- **Open streams:** `max_streams_per_client` (4) and `max_streams_total` (64), then 429
  `too-many-streams`. The slot is a `yield` dependency. FastAPI runs its exit after the
  response is sent, which for an event stream is when the stream ends or the client
  disconnects. A test cancels a reader and sees the slot come back.
- **Connections:** `max_connections` (200, uvicorn's `limit_concurrency`; 503 beyond).

### 3. CORS

Only `cors_origins` (default `http://localhost:3000`; the production origin is set at deploy,
since the site does not exist yet), methods `GET`, `POST`, `DELETE`, no credentials. The headers
a client needs are exposed: `ETag`, `Location`, `Retry-After`, `X-Request-ID`,
`X-MutaScope-Cache`. CORS restricts browsers only. The limits are what stop scripts.

### 4. Body size

`max_body_bytes` (16 KiB; the largest valid body, 1,022 residues and 50 mutations, is 1,787
bytes pretty-printed). A declared `Content-Length` over it is refused by middleware before
anything reads the body. A chunked body is counted as it is received, and reading past the
limit raises `problems.BodyTooLarge`. That class is a Starlette `HTTPException` because FastAPI
re-raises those from its body reader, and turns any other exception into a 400. Both cases
give a 413 problem `urn:mutascope:problem:body-too-large`.

### 5. Request ids and logs

- Every request gets an id: the incoming `X-Request-ID` if it is 8–64 characters of
  `[A-Za-z0-9._-]`, otherwise a fresh UUID (32 hex digits). The id is returned as
  `X-Request-ID` and bound into the structlog context, so every line the request logs has it.
  The access line (`event: request`: method, path, status, ms, client) is written by our
  middleware, and uvicorn's access log is off. A job's worker lines carry the id of the request
  that submitted it, and the job id. A 500 is formatted outside every middleware, so its
  handler reads the id from the request state.
- One JSON object per line. The standard library's loggers (uvicorn, httpx) go through the same
  processors (`structlog.stdlib.ProcessorFormatter`), so they are JSON with the request id too.
- **No sequence, no mutation list.** The API logs a sequence's sha256 and length
  (`api.app.logs.sequence_fields`). As a safety net, every string in every log event (exception
  text and library messages included) is scanned. Runs of 10 or more amino-acid letters and
  mutation-shaped tokens (`A123G`) are replaced. No event name, id, hash or timestamp matches
  either pattern (they are lowercase, or digits and punctuation). The test submits a 119-residue
  sequence through every live route, with mutations, a 422, a 500 and a failed job whose
  exception text holds the sequence and the mutations. It fails if the sequence, a 30-residue
  piece of it or a mutation appears in any log output, and it also checks that the hash does
  appear.

### 6. Metrics (`GET /metrics`)

prometheus-fastapi-instrumentator: request count and latency by route template, method and
status (buckets up to 5 s, with 2 s, ADR 0029's fast-mode p95 rule). An event stream's time is
to its first byte, not its whole life. Probes are left out. The API adds:
`mutascope_inference_seconds{model, method}` (one forward call: a fast landscape, or one
precise batch of up to 16 residues), `mutascope_score_cache_requests_total{method, result}`
(hit rate = hit / (hit + miss)), `mutascope_job_queue_length`, `mutascope_jobs_running` and
`mutascope_sse_streams_open`.

Each app has its own `CollectorRegistry` (the global one would refuse a second app's metrics in
the tests). The route needs `Authorization: Bearer <metrics_token>`. The token is at least 32
characters and is compared with `hmac.compare_digest`. A missing or wrong token gets 401 with
`WWW-Authenticate: Bearer`. With no token configured, the route answers 404. It is under the
read limit, and it is not in the OpenAPI schema (`include_in_schema=False`; the generated
frontend types do not mention it).

### 7. Sentry (optional)

Off unless `sentry_dsn` is set. When on, it collects no request bodies (`http_bodies` empty and
`max_request_body_size="never"`), no stack-frame variables (a scoring frame holds the
sequence), no cookies, no query strings and no user identity (`data_collection`; the older
`send_default_pii` is deprecated in sentry-sdk 2.72). `before_send` also drops the body,
cookies and `Authorization` / `X-Forwarded-For` headers, and applies the log redaction to every
string. The Starlette integration reports a 500 by itself. An explicit `capture_exception` in
the 500 handler produced no second event (Sentry deduplicates the same exception), so the
handler does not call it. A failed job is not a request error, so the worker reports it
explicitly. The test, with a capturing transport: one 500 and one failed job give exactly 2
events, each without a body, without the sequence, and with the redaction marker.

### 8. One process, enforced

The job queue (ADR 0031), the rate-limit counters and the stream slots live in one process's
memory. With two worker processes each would have its own: every limit would silently double
(or more), two precise jobs could run at once on 2 vCPUs, and a client could see `404` for its
own job when the next request went to the other worker. Therefore:

- **The start command** is `python -m api.app.serve` (the Docker image's command in Task 6). It
  passes uvicorn the app **object**, with `workers=1`. uvicorn can fork workers only from an
  import string, so no flag can add one. `WEB_CONCURRENCY` (what hosts set to ask for more
  workers) other than 1 is refused with an explanation. The command also turns off proxy
  headers and uvicorn's access log, and sets `limit_concurrency`.
- **A host-wide lock** (`api.app.process.ProcessLock`, `flock` on `lock_file`, taken in the
  lifespan before anything loads) makes any other launch fail at startup with
  `SecondProcessError`: `uvicorn --workers 4 api.app.main:app`, gunicorn, or a second server
  started by mistake. The kernel releases the lock when the process dies, even after a crash.
- `make api` (development, `--reload`) is one process too and uses the same flags.

Scaling out later means moving the queue and the counters to a shared store (Redis). slowapi
supports that storage, and the job manager's interface would not change.

### 9. Local load test

`tools/loadtest/` (its own locked uv environment: Locust 2.46.7 brings gevent and Flask pins
that nothing else needs) runs each scenario against a fresh server started with the production
command: one worker, ONNX threads = 2, the real fp32 models and bundle at their pinned
revisions, `trusted_proxies = 127.0.0.1`. Each Locust user sends its own `X-Forwarded-For`,
as clients will reach the Space through its proxy. The test checks behaviour: the 429s start at
the configured limits, there are no 5xx at 5 and 20 users, and memory stays bounded. The
latencies are recorded but are **M4 Pro numbers, not representative of the Space**. Real
latency is measured on the Space in Task 7. Report: `docs/load-test.md`.

## Evidence

- **Load test** (`docs/load-test.md`, raw numbers in `docs/load-test-summary.json`): each
  limit accepted exactly its value inside one window: 120 reads, 20 scores for one client,
  120 scores over 10 clients (10–13 each), 2 jobs, at most 4 open streams. Every refusal was a
  problem body, and every rate-limit refusal had `Retry-After`. At 5 users (180 s, 524 requests) and 20 users (600 s, 6,922
  requests): 0 failures, 0 5xx, no error log line. Memory at 20 users levelled off at about
  2.5 GB (quarter means 2,406 → 2,471 → 2,503 → 2,523 MB).
- **Tests** (`api/tests/test_hardening.py`, 23): client identity with and without a trusted
  proxy, including a spoofed header from an untrusted client; every limit and its
  `Retry-After`; slots released on disconnect; CORS for allowed and refused origins; 413 for
  declared and chunked bodies; request ids (kept, replaced, present in log lines and on
  errors); no sequence in any log; `/metrics` auth and content; Sentry scrubbing; the lock and
  the start command.

## Consequences

- New: `api/app/{clients,middleware,ratelimit,metrics,logs,sentry,process,serve}.py`,
  `tools/loadtest/`, `docs/threat-model.md`, `docs/load-test.md`.
- New dependencies (`api` extra): slowapi 0.1.10 (and limits 5.8.0, used directly for parsing
  limit strings), prometheus-client 0.26.0, prometheus-fastapi-instrumentator 8.1.0,
  sentry-sdk 2.72.0.
- `mutascope.utils.logging.configure_logging` takes extra processors, and its stderr wrapper is
  public (`CurrentStderr`) so the API's standard-library handler also writes to the current
  stream.
- Problem types added: `rate-limited`, `too-many-jobs`, `too-many-streams`, `body-too-large`,
  `unauthorized`. The OpenAPI schema documents 413 and 429 where they can happen. The frontend
  types were regenerated.
- The test fixtures run with loose limits (`LOOSE_LIMITS`), because every in-process client is
  the same address. `test_hardening.py` sets the limits explicitly.
- Deploy (Task 6) must set `trusted_proxies`, `cors_origins` (the production origin) and
  `metrics_token`, and optionally `sentry_dsn`.

## Alternatives considered

- **slowapi decorators with a module-level limiter:** the limits would be fixed at import, and
  tests would share counters between apps.
- **Fixed windows:** simpler, but allow up to 2N requests across a window boundary.
- **uvicorn's `--proxy-headers` with `--forwarded-allow-ips`:** it rewrites the socket address
  before the app sees it, so the rule would live in a server flag rather than in code with
  tests, and a launch without the flag would silently change who the client is. Ours is in
  one tested function and needs no server flag except turning uvicorn's off.
- **Only a global fast-mode limit:** one client could use the whole budget. **Only per-client
  limits:** many clients could exceed the CPU. Both are needed.
- **Logging nothing about requests:** safe but undebuggable. Logging hashes makes a user's
  report ("my sequence failed") traceable without the sequence.
- **Several workers with Redis now:** a broker the free Space does not have, for one 2-vCPU
  container.

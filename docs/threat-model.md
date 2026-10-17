# API threat note

What someone could do to the public MutaScope API (`api/`, on a free Hugging Face Space: 2 vCPUs,
one process), which limit addresses each threat, and what is left. The limits and their
defaults are in `api/app/settings.py` (environment `MUTASCOPE_API_*`) and ADR 0032. All
defaults are provisional until the Space is measured (Phase 4 Task 7).

## What is worth protecting

- **CPU.** Every live score is an ONNX forward pass on 2 vCPUs. Fast mode takes up to 0.6 s
  (150M, 1,022 residues; M4 Pro). A precise job takes up to about a minute (35M, 600 residues).
  One abuser who keeps the CPU busy makes the API useless for everyone.
- **Memory and connections.** One process holds the two models (about 1 GB resident), the job
  store, the rate-limit counters and every open connection.
- **Submitted sequences.** A user's sequence may be unpublished. It must not leak into logs,
  metrics or error reports.
- **Not worth protecting:** the precomputed data. The bundle is public on the Hub and the static
  site serves it without the API, so scraping the read routes gains nothing.

## Threats and the limits that address them

A "client" is an IP address: the socket address, or the `X-Forwarded-For` address written by a
trusted proxy (see *Spoofed IPs*). Limits are moving windows: at most N in any 60 s (or hour),
with no burst at a window boundary. Every refusal is an RFC 9457 problem (`application/problem+json`),
and a rate-limit refusal carries `Retry-After`.

| Threat | What the abuser could do | What stops it (default) |
|---|---|---|
| **Flooding fast mode** | Send `POST /v1/score` in a loop, with new sequences so the cache never helps, to keep both vCPUs busy. | Per client, `rate_limit_score` (20/min). Over all clients, `rate_limit_score_global` (120/min, about what 2 vCPUs score at 1,000 residues with 150M), so many addresses cannot add up to more. Validation (alphabet, length ≤ 1,022, ≤ 50 mutations, wild types) runs before any model, and a bad request costs microseconds. Cache hits count against the limit too. |
| **Filling the job queue** | Submit precise-mode jobs (about a minute each) until the queue is full, so nobody else can start one. | Per client: one queued or running job (`max_active_jobs_per_client`), then `rate_limit_jobs` (2/min and 10/hour). Over all clients: one job runs at a time, 4 wait (`job_queue_size`), then 429 `queue-full`. Jobs are capped at 600 residues (`precise_max_length`). Cancelling (`DELETE`) frees a slot at once, and finished jobs expire after an hour. |
| **Holding SSE streams open** | Open many `GET /v1/jobs/{id}/stream` connections and never close them, to use up sockets and memory. | Per client, `max_streams_per_client` (4). Over all clients, `max_streams_total` (64). A slot is released when the stream ends or the client disconnects (tested). A stream ends when its job finishes, so with one running job and four queued it lasts minutes at most. A stream of a finished job replays its events and closes. Opening a stream counts as a read (`rate_limit_read`). |
| **Huge bodies** | Send multi-megabyte JSON to use memory and parsing time. | `max_body_bytes` (16 KiB; the largest valid body is 1.8 kB). A declared `Content-Length` over the limit is refused before the body is read. A chunked body is counted as it arrives and refused at the limit. Both are a 413 `body-too-large`. |
| **Many connections** (including slow uploads) | Open hundreds of idle or slow connections. | `max_connections` (200, uvicorn's `limit_concurrency`): beyond it, uvicorn answers 503 at once. |
| **Spoofed IPs** | Send a new `X-Forwarded-For` with each request, to get a fresh quota each time or to be counted as someone else. | `X-Forwarded-For` is read only when the connection comes from `trusted_proxies`, and then from the right, so only the address the proxy itself wrote counts. Entries the client wrote further left are ignored. uvicorn's own proxy-header rewriting is off in the start command. Tested with a spoofed header from an untrusted client (one quota, however many headers) and a prepended fake behind the proxy (counted as the real address). Spoofing the TCP source address is not possible for HTTP: the handshake would fail. |
| **Scraping** | Download every protein and landscape through the API. | `rate_limit_read` (120/min per client) on every read route. ETags let honest clients revalidate for free. The data is public on the Hub anyway, so the limit protects the CPU, not the data. |
| **Guessing the metrics token** | Try tokens on `GET /metrics` to read the server's internals. | The token is at least 32 characters (settings refuse shorter) and compared in constant time (`hmac.compare_digest`). Attempts count against the read limit. Without a configured token the route answers 404. It is not in the public OpenAPI schema. |
| **Using a victim's browser** (cross-site requests) | Make a visitor's browser call the API from another site. | CORS allows only `cors_origins` (the production site and `http://localhost:3000`). CORS binds browsers only, not scripts, so it is not an abuse control: the limits above are. The API has no cookies or accounts, so a cross-site request gains nothing. |
| **Reading someone's sequence** | Find a submitted sequence in logs, metrics or error reports. | Logs carry a sequence's sha256 and length only. A redaction pass replaces any run of 10+ amino-acid letters, and any mutation-shaped token (`A123G`), in every log string, including exception text and library log lines. A test submits a sequence through every live route, including failures, and fails if it appears in any log output. Sentry (off unless a DSN is set) collects no request bodies, no stack-frame variables, no cookies and no query strings, and the same redaction runs on every event. Metrics have no sequence-derived labels. Job ids are random UUIDs, so a job (and its sequence) cannot be guessed. |
| **Log injection** | Put newlines or quotes into a logged value to forge log lines. | Logs are JSON, so values are escaped. An incoming `X-Request-ID` is kept only if it is 8–64 characters of `[A-Za-z0-9._-]`, otherwise it is replaced. |

## What is left

- **Many addresses.** Per-client limits stop one address. An abuser with many addresses (a
  botnet, or an IPv6 range: every address in a /64 is a separate client today) is held only by
  the global bounds: the global fast-mode limit, the job queue, the total stream limit and the
  connection limit. They keep the server up, but such an abuser can still use up the global
  budget and make legitimate users wait or get 429s. The read routes have no global limit. A
  free Space has no upstream WAF we control. Keying IPv6 clients by /64 is a possible next step.
- **The proxy setting is load-bearing.** If `trusted_proxies` is empty on the Space, every
  request appears to come from the proxy: the limits still hold, but all users share one
  quota, so one abuser blocks everyone. If it is too broad (e.g. `0.0.0.0/0`), clients can
  spoof `X-Forwarded-For` again. It must be set to the Space proxy's address range, measured
  at deploy (Task 6).
- **Counters do not survive a restart** and are per process. That is acceptable only because
  the server runs as one process, which the start command and a host-wide lock enforce
  (ADR 0032). A restart gives every client a fresh quota.
- **Rate-limit memory grows with the number of distinct clients** in the window: each counter
  keeps at most N timestamps, which expire with the window. Memory is therefore bounded by the
  request rate, not by the number of addresses ever seen.
- **`/docs` and `/openapi.json` are not rate-limited.** They are static and cheap, and the
  connection limit bounds them.

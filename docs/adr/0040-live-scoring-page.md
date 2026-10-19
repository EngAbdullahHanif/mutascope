# 0040 — Live scoring page: `/score` over fetch-streamed SSE, waking the API on load

- Status: accepted
- Date: 2026-10-19
- Builds on: ADR 0031 (live scoring routes, SSE job log), ADR 0032 (rate limits, CORS), ADR 0034
  (Cloud Run: scale to zero, CPU only during a request), ADR 0036 (one forward pass at a time,
  503 `scoring-busy`), ADR 0037 (site works with the API asleep), ADR 0038 (canvas heatmap)

## Context

Phase 5 Task 4 adds the one page that calls the API: score any pasted sequence, fast mode
(`POST /v1/score`, wild-type marginal) or precise mode (`POST /v1/jobs` and its event stream,
masked marginal). Three facts of the deployment shape it:

- The service scales to zero. The first request after a quiet spell waits for an instance:
  `/health` took 20.4 s from cold on 2026-10-19. Cloud Run holds the request rather than failing
  it.
- An instance has CPU only while a request is open, so a precise job advances only while its
  stream is open (ADR 0034). A dropped stream must be reopened, not reported and abandoned.
- Precise mode's length limit is a deployment setting (`MUTASCOPE_API_PRECISE_MAX_LENGTH`, 400
  on Cloud Run, 600 in the image) and may change after a measurement (ROADMAP). `/ready` reports
  it as `precise_max_length`.

The CORS configuration needed no change: `conf/deploy/cloudrun.yaml` already lists
`https://mutascope.abdullahhanif.me` and `https://mutascope-web.vercel.app`, and the live
service's preflight for `GET /v1/jobs/{id}/stream` with `Last-Event-ID` from the custom domain
returns 200 with that origin allowed.

## Decision

### 1. Read job streams with `fetch`, not `EventSource`

`EventSource` cannot set `Last-Event-ID` on a connection it did not open itself, never exposes
the status code or `Retry-After` of a refused stream (429 `too-many-streams`, 503), and retries
forever on its own schedule. `followJob` (`web/src/lib/live/follow.ts`) opens the stream with
`fetch`, parses it with a small spec-conforming parser (`sse.ts`), and on a stream that ends
without a final status, or a network error, reconnects after 1, 2, 4, 8, 8… s (or the server's
`Retry-After`), sending the last event id it saw. The server replays every event after that id,
so no row is lost or applied twice. The page shows "Connection lost. Reconnecting (attempt n)".
It gives up on a 404 (a new instance has no record of the job), on any other 4xx, or after 8
reconnects in a row with no event in between.

### 2. Wake the API when the page loads, and read its limits from `/ready`

On mount the page calls `/health`; if no answer comes within 1.5 s it shows "Waking up the
server…" (not an error), and retries a failed ping every 3 s for up to 90 s. Then `/ready`
gives `precise_max_length` and the default model. Nothing in the web code assumes either. The
submit button stays disabled, and says why, until the server is ready. A fast request that is
still unanswered after 3 s adds "the server may be waking up", for the case where the instance
went back to sleep after the page loaded.

### 3. A live result is an Explorer landscape with `source: "live"`

The live grid becomes the Explorer's `Landscape` (one zero-shot model, no measurements), so the
same canvas heatmap, top-10 lists, CSV and table draw it. `source: "live"` turns off everything
about measurements: the tooltip's "Measured" row and residue status, the screen-reader text's,
the CSV's `measured_dms_score` column. Rows a running job has not reached are NaN, drawn grey
with a "Not scored yet" legend swatch, never white. Every result is labelled `Zero-shot, <model>`
from the response's `model` field (`esm2_35m_onnx_fp32` → "ESM-2 35M"), not from what the page
asked for. No 3D view and no supervised model: neither exists for an arbitrary sequence.

### 4. The sequence never leaves the request body

The textarea has no `name` (a form submitted without JavaScript would carry nothing), the
sequence is never put in the URL, storage or an error message, requests omit credentials and
skip the HTTP cache, and the site has no analytics. A Playwright test checks that no request URL
and not the page URL contain the sequence. The API already never logs a sequence (ADR 0032).

### 5. Errors in the reader's words, validation at the input

Client-side checks mirror the API's (`schemas._canonical`): after removing a FASTA header and
whitespace and uppercasing, only the 20 canonical amino acids, 1–1,022 residues, and the first bad
residue named with its 1-indexed position and how many more there are. A 422 from the API also
goes to the input. Every other problem gets a sentence per slug (`rate-limited`, `scoring-busy`,
`queue-full`, `too-many-jobs`, `too-many-streams`), with `Retry-After` shown as a time and the
button counting down instead of sending a request the server would refuse.

### 6. Leaving the page cancels a running job

A job left without a stream holds the client's one job slot (ADR 0032) and does not advance. On
`pagehide` and unmount the page sends `DELETE /v1/jobs/{id}` with `keepalive`. This is best
effort. The server's own expiry still applies.

## Tests

- Vitest (`src/lib/live/live.test.ts`, 24 tests): sequence rules, SSE parsing at every chunk
  size, problems and `Retry-After`, the live landscape's text and CSV, `followJob` (full stream,
  drop and resume with `Last-Event-ID`, Retry-After, 404, give-up, abort), `wake`.
- Playwright with a mocked API (`e2e/score.spec.ts`, 13 tests): fast success, validation at the
  input (client and 422), 429 and 503 with `Retry-After`, cold start, precise limit from `/ready`,
  a full SSE stream, a dropped stream resumed with `Last-Event-ID: 1`, reconnecting while the
  connection is down, cancel (`DELETE`), a cached job, examples.
- `e2e/score.live.spec.ts`: one fast-mode smoke test against the live API, skipped unless
  `MUTASCOPE_LIVE_API=1` (and always in CI). The API's CORS refuses the test server's origin, so
  Playwright relays the browser's API calls (`route.fetch`): the real requests and responses,
  with only `Access-Control-Allow-Origin` added.

## Consequences

- `/score` is the only page that depends on the API. All others still read the bundle alone.
- The 99th-percentile scale of a running job is recomputed per batch, so colours settle when the
  last rows arrive (the legend says so).
- The API URL is `SITE.api` (`NEXT_PUBLIC_MUTASCOPE_API_URL` at build time, default the Cloud
  Run URL). Vercel previews cannot use the live API: CORS allows production origins only.

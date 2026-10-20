/**
 * A mocked live-scoring API for the `/score` tests: every route the page calls, answered by
 * Playwright, with the CORS headers the real API sends (the API is another origin).
 */
import type { Page, Request, Route } from "@playwright/test";

export const AA_ORDER = "ACDEFGHIKLMNPQRSTVWY";
export const SEQ = "MKTAYIAK";
export const MODEL = "esm2_35m_onnx_fp32";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE",
  "Access-Control-Allow-Headers": "Content-Type, Last-Event-ID, X-Request-ID",
  "Access-Control-Expose-Headers": "Retry-After, Location, X-Request-ID",
};

/** Wild type 0, every substitution a distinct negative score. */
export function grid(seq = SEQ): number[] {
  return Array.from(seq, (wt, pos) =>
    Array.from(AA_ORDER, (aa, i) => (aa === wt ? 0 : -((pos + 1) * 0.1 + i * 0.01))),
  ).flat();
}

export function rows(from: number, to: number, seq = SEQ) {
  const g = grid(seq);
  return Array.from({ length: to - from }, (_, k) => ({
    position: from + k,
    scores: g.slice((from + k) * 20, (from + k + 1) * 20),
  }));
}

const live = (seq: string) => ({
  kind: "zero_shot",
  label: "Zero-shot",
  model: MODEL,
  hf_id: "example/onnx",
  file: "esm2_35m/model.onnx",
  revision: "82e1528",
  target_seq: seq,
  length: seq.length,
  aa_order: AA_ORDER,
  version: { api_version: "0.6.0", bundle_revision: null, bundle: {} },
});

export function job(status: string, done = 0, seq = SEQ, scores: number[] | null = null) {
  return {
    ...live(seq),
    id: "job-1",
    status,
    method: "masked_marginal",
    done,
    total: seq.length,
    cached: false,
    created_at: "2026-10-19T10:00:00Z",
    started_at: null,
    finished_at: null,
    expires_at: null,
    error: null,
    scores,
  };
}

export function scoreResponse(seq = SEQ) {
  return { ...live(seq), method: "wt_marginal", scores: grid(seq), mutations: null };
}

export const event = (id: number, name: string, data: unknown) =>
  `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

export function progress(done: number, from: number, to: number) {
  return {
    id: "job-1",
    kind: "zero_shot",
    label: "Zero-shot",
    done,
    total: SEQ.length,
    rows: rows(from, to),
  };
}

export function json(
  route: Route,
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
) {
  return route.fulfill({
    status,
    headers: { ...CORS, "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

export function problem(
  route: Route,
  status: number,
  slug: string,
  detail: string,
  headers: Record<string, string> = {},
) {
  return route.fulfill({
    status,
    headers: { ...CORS, "Content-Type": "application/problem+json", ...headers },
    body: JSON.stringify({
      type: `urn:mutascope:problem:${slug}`,
      title: slug,
      status,
      detail,
      instance: new URL(route.request().url()).pathname,
      errors: null,
    }),
  });
}

export function sse(route: Route, text: string) {
  return route.fulfill({
    status: 200,
    headers: { ...CORS, "Content-Type": "text/event-stream", "Cache-Control": "no-store" },
    body: text,
  });
}

/** A promise the test resolves when it wants a held response to go out. */
export function gate() {
  let open!: () => void;
  const opened = new Promise<void>((resolve) => (open = resolve));
  return { open, opened };
}

type Handler = (route: Route, request: Request) => Promise<void> | void;

export type MockOptions = {
  health?: Handler;
  ready?: Partial<{ precise_max_length: number; score_default_model: string }>;
  score?: Handler;
  createJob?: Handler;
  /** Answers stream requests in order; the last one repeats. */
  streams?: Handler[];
  cancel?: Handler;
};

/** Install the mock; returns every API request the page made, in order. */
export async function mockApi(page: Page, opts: MockOptions = {}): Promise<Request[]> {
  const requests: Request[] = [];
  const streams = [...(opts.streams ?? [])];
  const handle =
    (handler: Handler | undefined, fallback: Handler): Handler =>
    (route, request) => {
      if (request.method() === "OPTIONS") {
        return route.fulfill({
          status: 204,
          headers: { ...CORS, "Access-Control-Max-Age": "600" },
        });
      }
      requests.push(request);
      return (handler ?? fallback)(route, request);
    };
  const notMocked: Handler = (route) => problem(route, 500, "not-mocked", route.request().url());

  await page.route(
    "**/health",
    handle(opts.health, (r) => json(r, { status: "ok", version: "0.6.0" })),
  );
  await page.route(
    "**/ready",
    handle(undefined, (r) =>
      json(r, {
        status: "ready",
        schema_version: "1.1.0",
        bundle_revision: null,
        proteins: 217,
        models: ["esm2_150m_onnx_fp32", MODEL],
        score_default_model: "esm2_35m",
        precise_max_length: 300,
        ...opts.ready,
      }),
    ),
  );
  await page.route(
    "**/v1/score",
    handle(opts.score, (r) => json(r, scoreResponse())),
  );
  await page.route(
    "**/v1/jobs",
    handle(opts.createJob, (r) => json(r, job("queued"), 202)),
  );
  await page.route(
    /\/v1\/jobs\/[^/]+\/stream$/,
    handle(undefined, (route, request) => {
      const next = streams.length > 1 ? streams.shift()! : streams[0];
      return (next ?? notMocked)(route, request);
    }),
  );
  await page.route(
    /\/v1\/jobs\/[^/]+$/,
    handle(opts.cancel, (r) => json(r, job("cancelled"))),
  );
  return requests;
}

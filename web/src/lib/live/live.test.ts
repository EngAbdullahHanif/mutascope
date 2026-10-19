import { describe, expect, it, vi } from "vitest";

import {
  AA_ORDER,
  N_AA,
  buildView,
  cellDetail,
  describeCell,
  viewCsv,
} from "@/lib/explorer/landscape";

import { ApiError, LiveClient, errorFrom, parseRetryAfter, type JobResponse } from "./client";
import { followJob, type Connection } from "./follow";
import {
  applyRows,
  emptyGrid,
  gridFrom,
  liveLandscape,
  liveModelName,
  scoredResidues,
} from "./landscape";
import { formatWait, friendlyError } from "./messages";
import {
  EXAMPLES,
  MAX_LENGTH,
  checkSequence,
  normalizeSequence,
  preciseLengthError,
} from "./sequence";
import { SseParser } from "./sse";
import { wake } from "./wake";

const BASE = "https://api.test";

function problem(
  status: number,
  slug: string,
  detail: string,
  headers: Record<string, string> = {},
) {
  return new Response(
    JSON.stringify({
      type: `urn:mutascope:problem:${slug}`,
      title: slug,
      status,
      detail,
      instance: "/x",
      errors: null,
    }),
    { status, headers: { "Content-Type": "application/problem+json", ...headers } },
  );
}

describe("sequence checks (the API's rules)", () => {
  it("tidies pasted text: FASTA header, whitespace, case", () => {
    expect(normalizeSequence(">sp|P0CG63|UBI\nmqif vk\r\nTLTG\n")).toBe("MQIFVKTLTG");
    expect(checkSequence("  mk  ")).toEqual({ ok: true, sequence: "MK" });
  });

  it("names the first bad residue, 1-indexed, and counts the rest", () => {
    const r = checkSequence("MKXAB*");
    expect(r.ok).toBe(false);
    expect(!r.ok && r.message).toBe(
      `“X” at position 3 is not one of the 20 canonical amino acids (${AA_ORDER}) (and 2 more).`,
    );
    expect(
      !checkSequence("MKU").ok && (checkSequence("MKU") as { message: string }).message,
    ).toMatch(/^“U” at position 3 .*\.$/);
  });

  it("refuses empty and over-long input; 1,022 is fine", () => {
    expect(checkSequence(" \n").ok).toBe(false);
    expect(checkSequence("A".repeat(MAX_LENGTH)).ok).toBe(true);
    const long = checkSequence("A".repeat(MAX_LENGTH + 1));
    expect(!long.ok && long.message).toMatch(/^1,023 residues: the limit is 1,022/);
  });

  it("applies precise mode's limit as given by the server", () => {
    expect(preciseLengthError(400, 400)).toBeNull();
    expect(preciseLengthError(401, 400)).toMatch(/at most 400 residues .* has 401\)/);
  });

  it("ships only valid examples", () => {
    for (const ex of EXAMPLES)
      expect(checkSequence(ex.sequence)).toEqual({ ok: true, sequence: ex.sequence });
  });
});

describe("SSE parser", () => {
  it("reads events split anywhere, with CRLF, comments and multi-line data", () => {
    const text =
      ': keep-alive\r\nevent: progress\r\nid: 3\r\ndata: {"a":\r\ndata: 1}\r\n\r\nid: 4\nevent: succeeded\ndata:x\n\n';
    for (const size of [1, 2, 5, text.length]) {
      const p = new SseParser();
      const events = [];
      for (let i = 0; i < text.length; i += size) events.push(...p.push(text.slice(i, i + size)));
      expect(events).toEqual([
        { event: "progress", id: "3", data: '{"a":\n1}' },
        { event: "succeeded", id: "4", data: "x" },
      ]);
    }
  });

  it("dispatches nothing for an event without data, and defaults the name", () => {
    const p = new SseParser();
    expect(p.push("event: ping\n\ndata: hi\n\n")).toEqual([
      { event: "message", id: null, data: "hi" },
    ]);
  });
});

describe("API errors", () => {
  it("parses Retry-After as seconds or a date", () => {
    expect(parseRetryAfter("30")).toBe(30);
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter("soon")).toBeNull();
    expect(parseRetryAfter(new Date(10_000).toUTCString(), 0)).toBe(10);
  });

  it("reads a problem: slug, detail, field errors, Retry-After", async () => {
    const e = await errorFrom(
      problem(429, "rate-limited", "Retry after 30 s.", { "Retry-After": "30" }),
    );
    expect(e).toMatchObject({ kind: "http", status: 429, slug: "rate-limited", retryAfter: 30 });
    expect(friendlyError(e).message).toBe(
      "Too many requests from your address. Try again in 30 seconds.",
    );
  });

  it("copes with a body that is not a problem (Cloud Run's front end)", async () => {
    const e = await errorFrom(new Response("<html>Service Unavailable</html>", { status: 503 }));
    expect(e).toMatchObject({ status: 503, slug: null, message: "HTTP 503" });
    expect(friendlyError(e).message).toMatch(/unavailable right now\. Try again in a minute\./);
  });

  it("sends a 422 to the input, in the API's words without pydantic's prefix", async () => {
    const res = new Response(
      JSON.stringify({
        type: "urn:mutascope:problem:validation-error",
        title: "Validation error",
        status: 422,
        detail: "body.sequence",
        instance: "/v1/score",
        errors: [
          {
            loc: ["body", "sequence"],
            msg: "Value error, only the 20 canonical amino acids",
            type: "value_error",
          },
        ],
      }),
      { status: 422 },
    );
    expect(friendlyError(await errorFrom(res))).toEqual({
      message: "",
      retryAfter: null,
      inputError: "Only the 20 canonical amino acids",
    });
  });

  it("words the waits", () => {
    expect(formatWait(1)).toBe("in a second");
    expect(formatWait(45)).toBe("in 45 seconds");
    expect(formatWait(150)).toBe("in about 3 minutes");
  });

  it("turns no answer into a network error, but lets an abort through", async () => {
    const offline = new LiveClient({
      base: BASE,
      fetch: () => Promise.reject(new TypeError("Failed to fetch")),
    });
    await expect(offline.health()).rejects.toMatchObject({ kind: "network" });
    const aborted = new LiveClient({
      base: BASE,
      fetch: () => Promise.reject(new DOMException("aborted", "AbortError")),
    });
    await expect(aborted.health()).rejects.toBeInstanceOf(DOMException);
  });
});

describe("live landscape", () => {
  const seq = "MK";

  it("names served models", () => {
    expect(liveModelName("esm2_35m_onnx_fp32")).toBe("ESM-2 35M");
    expect(liveModelName("esm2_150m")).toBe("ESM-2 150M");
    expect(liveModelName("other")).toBe("other");
  });

  it("fills rows as they arrive and counts them", () => {
    let g = emptyGrid(2);
    expect(scoredResidues(g)).toBe(0);
    g = applyRows(g, [{ position: 1, scores: Array.from({ length: N_AA }, (_, i) => -i) }]);
    expect(scoredResidues(g)).toBe(1);
    expect(g[N_AA + 3]).toBe(-3);
    expect(Number.isNaN(g[0]!)).toBe(true);
    expect(() => applyRows(g, [{ position: 2, scores: Array(N_AA).fill(0) }])).toThrow(
      /out of range/,
    );
    expect(() => gridFrom([1, 2], 2)).toThrow(/expected 40/);
  });

  it("is labelled zero-shot and says nothing about measurements", () => {
    const grid = gridFrom(
      Array.from({ length: 2 * N_AA }, (_, i) => (i % N_AA) / -10),
      2,
    );
    const l = liveLandscape(seq, "esm2_35m_onnx_fp32", grid);
    expect(l.options).toEqual([
      {
        model: "esm2_35m_onnx_fp32",
        name: "ESM-2 35M",
        kind: "zero_shot",
        label: "Zero-shot",
        isDefault: true,
      },
    ]);
    const view = buildView(l, {
      model: "esm2_35m_onnx_fp32",
      mode: "predicted",
      maskUnmeasured: false,
    });
    expect(view.label).toBe("Zero-shot");
    expect(view.center).toBe(0);
    const d = cellDetail(l, "esm2_35m_onnx_fp32", 0, AA_ORDER.indexOf("A"));
    expect(describeCell(d, 2)).toBe("M1A. Zero-shot ESM-2 35M: 0.00.");
    const csv = viewCsv(l, view, "predicted").trim().split("\n");
    expect(csv[0]).toBe("mutation,position,wt,mt,value,quantity,model,label");
    expect(csv).toContain('M1C,1,M,C,-0.1,log_ratio,esm2_35m_onnx_fp32,"Zero-shot"');
    expect(csv).toHaveLength(1 + 2 * 19);
  });

  it("says a residue is not scored yet while a job runs", () => {
    const l = liveLandscape(seq, "esm2_35m_onnx_fp32", emptyGrid(2));
    expect(describeCell(cellDetail(l, "esm2_35m_onnx_fp32", 1, 0), 2)).toBe(
      "K2A. Zero-shot ESM-2 35M: not scored yet.",
    );
  });
});

// A job stream, as the API writes it.
function job(status: JobResponse["status"], extra: Partial<JobResponse> = {}): JobResponse {
  return { id: "j1", status, done: 0, total: 2, scores: null, ...extra } as JobResponse;
}
const ev = (id: number, event: string, data: unknown) =>
  `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const row = (position: number) => ({ position, scores: Array(N_AA).fill(-position) });

function sseResponse(text: string) {
  return new Response(text, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

/** A client whose stream requests are answered from `answers` in order; records requests. */
function streamClient(answers: (() => Response | Promise<Response>)[]) {
  const requests: { url: string; lastEventId: string | null }[] = [];
  const fetchFn = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    requests.push({ url: String(input), lastEventId: headers.get("Last-Event-ID") });
    const next = answers.shift();
    if (!next) throw new Error("no more answers");
    return Promise.resolve(next());
  });
  return { client: new LiveClient({ base: BASE, fetch: fetchFn as typeof fetch }), requests };
}

const noSleep = () => Promise.resolve();

describe("following a job", () => {
  it("applies status and progress events and resolves with the final job", async () => {
    const { client, requests } = streamClient([
      () =>
        sseResponse(
          ev(0, "queued", job("queued")) +
            ev(1, "running", job("running")) +
            ev(2, "progress", { id: "j1", done: 1, total: 2, rows: [row(0)] }) +
            ev(3, "progress", { id: "j1", done: 2, total: 2, rows: [row(1)] }) +
            ev(4, "succeeded", job("succeeded", { done: 2 })),
        ),
    ]);
    const statuses: string[] = [];
    const done: number[] = [];
    const final = await followJob(
      client,
      "j1",
      { onStatus: (j) => statuses.push(j.status), onProgress: (p) => done.push(p.done) },
      new AbortController().signal,
      { sleep: noSleep },
    );
    expect(final.status).toBe("succeeded");
    expect(statuses).toEqual(["queued", "running", "succeeded"]);
    expect(done).toEqual([1, 2]);
    expect(requests).toEqual([{ url: `${BASE}/v1/jobs/j1/stream`, lastEventId: null }]);
  });

  it("reconnects after a dropped stream with Last-Event-ID, and never repeats a row", async () => {
    const { client, requests } = streamClient([
      // Cut after the first progress event, without a final status.
      () =>
        sseResponse(
          ev(0, "running", job("running")) +
            ev(1, "progress", { id: "j1", done: 1, total: 2, rows: [row(0)] }),
        ),
      () => Promise.reject(new TypeError("network error")),
      () =>
        sseResponse(
          ev(2, "progress", { id: "j1", done: 2, total: 2, rows: [row(1)] }) +
            ev(3, "succeeded", job("succeeded")),
        ),
    ]);
    const connections: Connection[] = [];
    const positions: number[] = [];
    const sleeps: number[] = [];
    const final = await followJob(
      client,
      "j1",
      {
        onStatus: () => undefined,
        onProgress: (p) => positions.push(...p.rows.map((r) => r.position)),
        onConnection: (c) => connections.push(c),
      },
      new AbortController().signal,
      { sleep: (ms) => (sleeps.push(ms), Promise.resolve()) },
    );
    expect(final.status).toBe("succeeded");
    expect(positions).toEqual([0, 1]);
    expect(requests.map((r) => r.lastEventId)).toEqual([null, "1", "1"]);
    expect(sleeps).toEqual([1000, 2000]);
    expect(connections.filter((c) => c.state === "reconnecting")).toEqual([
      { state: "reconnecting", attempt: 1, delayMs: 1000 },
      { state: "reconnecting", attempt: 2, delayMs: 2000 },
    ]);
  });

  it("waits Retry-After on a refused stream, and gives up when the job is gone", async () => {
    const { client } = streamClient([
      () => problem(503, "scoring-busy", "busy", { "Retry-After": "7" }),
      () => problem(404, "job-not-found", "No job 'j1'."),
    ]);
    const sleeps: number[] = [];
    await expect(
      followJob(
        client,
        "j1",
        { onStatus: () => undefined, onProgress: () => undefined },
        new AbortController().signal,
        {
          sleep: (ms) => (sleeps.push(ms), Promise.resolve()),
        },
      ),
    ).rejects.toMatchObject({
      status: 404,
      message: expect.stringMatching(/no longer has this job/),
    });
    expect(sleeps).toEqual([7000]);
  });

  it("stops after maxAttempts reconnects in a row", async () => {
    const { client, requests } = streamClient(
      Array.from({ length: 4 }, () => () => sseResponse("")),
    );
    await expect(
      followJob(
        client,
        "j1",
        { onStatus: () => undefined, onProgress: () => undefined },
        new AbortController().signal,
        {
          maxAttempts: 3,
          sleep: noSleep,
        },
      ),
    ).rejects.toMatchObject({
      kind: "network",
      message: expect.stringMatching(/after 3 attempts/),
    });
    expect(requests).toHaveLength(4);
  });

  it("ends with an abort when cancelled", async () => {
    const ctl = new AbortController();
    const { client } = streamClient([
      () => {
        ctl.abort();
        return Promise.reject(new DOMException("aborted", "AbortError"));
      },
    ]);
    await expect(
      followJob(
        client,
        "j1",
        { onStatus: () => undefined, onProgress: () => undefined },
        ctl.signal,
      ),
    ).rejects.toBeInstanceOf(DOMException);
  });
});

describe("waking the server", () => {
  const ready = { status: "ready", precise_max_length: 400, score_default_model: "esm2_35m" };

  it("pings /health, then reads the limits from /ready", async () => {
    const urls: string[] = [];
    const client = new LiveClient({
      base: BASE,
      fetch: ((input: RequestInfo | URL) => {
        urls.push(String(input));
        return Promise.resolve(
          Response.json(String(input).endsWith("/health") ? { status: "ok" } : ready),
        );
      }) as typeof fetch,
    });
    const onWaking = vi.fn();
    await expect(wake(client, new AbortController().signal, { onWaking })).resolves.toMatchObject({
      precise_max_length: 400,
    });
    expect(urls).toEqual([`${BASE}/health`, `${BASE}/ready`]);
    expect(onWaking).not.toHaveBeenCalled();
  });

  it("says it is waking when a ping fails, retries, and gives up at the deadline", async () => {
    let t = 0;
    const client = new LiveClient({
      base: BASE,
      fetch: () => Promise.reject(new TypeError("down")),
    });
    const onWaking = vi.fn();
    await expect(
      wake(client, new AbortController().signal, {
        onWaking,
        deadlineMs: 10_000,
        retryMs: 3000,
        now: () => t,
        sleep: (ms) => ((t += ms), Promise.resolve()),
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(onWaking).toHaveBeenCalledTimes(1);
    // Pings at 0, 3, 6 and 9 s; one more would end past the 10 s deadline.
    expect(t).toBe(9000);
  });
});

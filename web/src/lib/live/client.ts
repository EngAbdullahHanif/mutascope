/**
 * The live-scoring API, from the browser (ADR 0031, 0040).
 *
 * Only `/score` calls the API; every other page reads the bundle and works with the API asleep.
 * Request and response types come from the generated `api-types.ts`. Every failure becomes an
 * {@link ApiError}: an RFC 9457 problem from the API, a response that is not one (Cloud Run's
 * own front end), or no response at all. A sequence travels only in a request body: never in a
 * URL, a log line or an error message.
 */
import type { components } from "@/lib/api-types";
import { SITE } from "@/lib/site";

export type Schemas = components["schemas"];
export type Ready = Schemas["Ready"];
export type ScoreResponse = Schemas["ScoreResponse"];
export type JobResponse = Schemas["JobResponse"];
export type JobProgress = Schemas["JobProgress"];
export type Problem = Schemas["Problem"];
export type FieldError = Schemas["FieldError"];

export type Fetch = typeof fetch;

export type ApiErrorKind =
  /** The API answered with an error status. */
  | "http"
  /** No answer: offline, DNS, CORS refused, or the connection dropped. */
  | "network";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** The problem's `type` slug (`urn:mutascope:problem:<slug>`), if it was one of ours. */
  readonly slug: string | null;
  readonly fieldErrors: FieldError[];
  /** Seconds, from `Retry-After` (429, 503). */
  readonly retryAfter: number | null;

  constructor(opts: {
    kind: ApiErrorKind;
    message: string;
    status?: number | null;
    slug?: string | null;
    fieldErrors?: FieldError[];
    retryAfter?: number | null;
  }) {
    super(opts.message);
    this.name = "ApiError";
    this.kind = opts.kind;
    this.status = opts.status ?? null;
    this.slug = opts.slug ?? null;
    this.fieldErrors = opts.fieldErrors ?? [];
    this.retryAfter = opts.retryAfter ?? null;
  }
}

const PROBLEM_URN = "urn:mutascope:problem:";

/** `Retry-After` in seconds: delta-seconds or an HTTP date (RFC 9110); null if absent or bad. */
export function parseRetryAfter(value: string | null, now = Date.now()): number | null {
  if (value === null || value.trim() === "") return null;
  if (/^\d+$/.test(value.trim())) return Number(value.trim());
  const at = Date.parse(value);
  return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - now) / 1000));
}

/** An error response as an {@link ApiError}, whether or not its body is a problem. */
export async function errorFrom(res: Response): Promise<ApiError> {
  const retryAfter = parseRetryAfter(res.headers.get("Retry-After"));
  let problem: Partial<Problem> | null = null;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === "object") problem = body as Partial<Problem>;
  } catch {
    // Not JSON: Cloud Run's front end (a 503 while no instance can start) or a proxy.
  }
  const type = typeof problem?.type === "string" ? problem.type : null;
  return new ApiError({
    kind: "http",
    status: res.status,
    slug: type?.startsWith(PROBLEM_URN) ? type.slice(PROBLEM_URN.length) : null,
    message:
      (typeof problem?.detail === "string" && problem.detail) ||
      (typeof problem?.title === "string" && problem.title) ||
      `HTTP ${res.status}`,
    fieldErrors: Array.isArray(problem?.errors) ? problem.errors : [],
    retryAfter,
  });
}

export type ClientOptions = { base?: string; fetch?: Fetch };

/** Thin typed wrapper over the routes the scorer uses. */
export class LiveClient {
  readonly base: string;
  private readonly fetchFn: Fetch;

  constructor({ base = SITE.api, fetch: fetchFn }: ClientOptions = {}) {
    this.base = base.replace(/\/$/, "");
    // Bound, so `this` is never the client when the browser's fetch is called.
    this.fetchFn = fetchFn ?? ((input, init) => globalThis.fetch(input, init));
  }

  /** `fetch`, with "no answer at all" turned into an {@link ApiError} of kind `network`. */
  async request(path: string, init: RequestInit = {}): Promise<Response> {
    try {
      return await this.fetchFn(`${this.base}${path}`, {
        ...init,
        // No cookies or credentials go to the API, and nothing is cached by the browser.
        credentials: "omit",
        cache: "no-store",
      });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      throw new ApiError({ kind: "network", message: "Could not reach the scoring server." });
    }
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const res = await this.request(path, init);
    if (!res.ok) throw await errorFrom(res);
    return (await res.json()) as T;
  }

  private post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    return this.json<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  }

  health(signal?: AbortSignal): Promise<Schemas["Health"]> {
    return this.json("/health", { signal });
  }

  ready(signal?: AbortSignal): Promise<Ready> {
    return this.json("/ready", { signal });
  }

  /** Fast mode, the full landscape: no `mutations`, and the server's default model. */
  score(sequence: string, signal?: AbortSignal): Promise<ScoreResponse> {
    const body: Schemas["ScoreRequest"] = { sequence };
    return this.post("/v1/score", body, signal);
  }

  createJob(sequence: string, signal?: AbortSignal): Promise<JobResponse> {
    // Precise mode runs on ESM-2 35M only (ADR 0031); the field is sent, not left to a default.
    const body: Schemas["JobRequest"] = { sequence, model: "esm2_35m" };
    return this.post("/v1/jobs", body, signal);
  }

  cancelJob(id: string, opts: { keepalive?: boolean } = {}): Promise<JobResponse> {
    return this.json(`/v1/jobs/${encodeURIComponent(id)}`, {
      method: "DELETE",
      keepalive: opts.keepalive,
    });
  }

  /** Open a job's event stream, resuming after `lastEventId` when given. */
  async openStream(id: string, lastEventId: string | null, signal: AbortSignal) {
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (lastEventId !== null) headers["Last-Event-ID"] = lastEventId;
    const res = await this.request(`/v1/jobs/${encodeURIComponent(id)}/stream`, {
      headers,
      signal,
    });
    if (!res.ok) throw await errorFrom(res);
    if (!res.body) throw new ApiError({ kind: "network", message: "The stream had no body." });
    return res.body;
  }
}

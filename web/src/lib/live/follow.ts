/**
 * Follow a precise-mode job's event stream to the end, reconnecting when it drops (ADR 0040).
 *
 * On Cloud Run an instance has CPU only during a request, so a job advances only while a
 * stream is open (ADR 0034): a dropped connection must be reopened, not reported and left. The
 * server numbers its events and replays everything after `Last-Event-ID` (ADR 0031), so a
 * reconnect sends the last id seen and no row is lost or applied twice.
 *
 * Gives up, with an {@link ApiError}, when the job is gone (404: a new instance has no record
 * of it), on any other refusal that waiting cannot fix, or after `maxAttempts` reconnects in a
 * row with no event between them.
 */
import { ApiError, type JobProgress, type JobResponse, type LiveClient } from "./client";
import { SseParser, type SseEvent } from "./sse";

export const TERMINAL = ["succeeded", "failed", "cancelled"] as const;
export type Terminal = (typeof TERMINAL)[number];

export type Connection =
  | { state: "connecting" }
  | { state: "open" }
  | { state: "reconnecting"; attempt: number; delayMs: number };

export type FollowHandlers = {
  /** `queued`, `running` and the final status, each with the job as the server sees it. */
  onStatus: (job: JobResponse) => void;
  /** One finished forward batch: its rows of the landscape. */
  onProgress: (progress: JobProgress) => void;
  onConnection?: (connection: Connection) => void;
};

export type FollowOptions = {
  maxAttempts?: number;
  /** Delay before reconnect `attempt` (1-based) when the server gave no `Retry-After`. */
  backoffMs?: (attempt: number) => number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
};

export const defaultBackoff = (attempt: number) => Math.min(1000 * 2 ** (attempt - 1), 8000);

export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason as Error);
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason as Error);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function isTerminal(name: string): name is Terminal {
  return (TERMINAL as readonly string[]).includes(name);
}

/** Resolves with the job's final state (`succeeded`, `failed` or `cancelled`). */
export async function followJob(
  client: LiveClient,
  jobId: string,
  handlers: FollowHandlers,
  signal: AbortSignal,
  { maxAttempts = 8, backoffMs = defaultBackoff, sleep = abortableSleep }: FollowOptions = {},
): Promise<JobResponse> {
  let lastId: string | null = null;
  let attempt = 0;
  handlers.onConnection?.({ state: "connecting" });
  for (;;) {
    let retryAfter: number | null = null;
    try {
      const body = await client.openStream(jobId, lastId, signal);
      handlers.onConnection?.({ state: "open" });
      const reader = body.getReader();
      const decoder = new TextDecoder();
      const parser = new SseParser();
      try {
        for (;;) {
          const { done, value } = await reader.read();
          const events = parser.push(
            done ? decoder.decode() : decoder.decode(value, { stream: true }),
          );
          for (const event of events) {
            if (event.id !== null) lastId = event.id;
            attempt = 0;
            const final = handle(event, handlers);
            if (final) return final;
          }
          if (done) break;
        }
      } finally {
        reader.releaseLock();
      }
      // The stream ended without a final status: the connection was cut (a proxy, a laptop
      // lid, a request timeout). The job is still there; reopen it.
    } catch (error) {
      if (isAbort(error) || signal.aborted) throw error;
      if (error instanceof ApiError && error.kind === "http") {
        if (error.status === 404) {
          throw new ApiError({
            kind: "http",
            status: 404,
            slug: error.slug,
            message: "The server no longer has this job (it may have restarted). Start it again.",
          });
        }
        // Too many streams open (429) or no instance free (503): wait as told, then retry.
        if (error.status !== 429 && (error.status ?? 0) < 500) throw error;
        retryAfter = error.retryAfter;
      } else if (!(error instanceof ApiError) && !(error instanceof TypeError)) {
        throw error;
      }
    }
    attempt += 1;
    if (attempt > maxAttempts) {
      throw new ApiError({
        kind: "network",
        message: `Lost the connection to the server and could not reconnect after ${maxAttempts} attempts.`,
      });
    }
    const delayMs = retryAfter !== null ? retryAfter * 1000 : backoffMs(attempt);
    handlers.onConnection?.({ state: "reconnecting", attempt, delayMs });
    await sleep(delayMs, signal);
  }
}

/** Apply one event; the job if it was the final one. */
function handle(event: SseEvent, handlers: FollowHandlers): JobResponse | null {
  if (event.event === "progress") {
    handlers.onProgress(JSON.parse(event.data) as JobProgress);
    return null;
  }
  if (event.event === "queued" || event.event === "running" || isTerminal(event.event)) {
    const job = JSON.parse(event.data) as JobResponse;
    handlers.onStatus(job);
    return isTerminal(event.event) ? job : null;
  }
  return null;
}

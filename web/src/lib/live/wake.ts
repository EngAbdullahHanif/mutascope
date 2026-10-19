/**
 * Wake the API and read its live limits (ADR 0040).
 *
 * The service scales to zero (ADR 0034): the first request after a quiet spell starts an
 * instance, about 20 s (20.4 s measured on 2026-10-19), and Cloud Run holds the request until
 * it is up rather than failing it. So the scorer pings `/health` when the page loads, says
 * "waking up" if the answer is slow, and retries a failed ping until `deadlineMs`. Then
 * `/ready` gives the precise-mode length limit and the default model; nothing here assumes them.
 */
import { abortableSleep } from "./follow";
import { ApiError, type LiveClient, type Ready } from "./client";

/** A healthy instance answers `/health` in well under a second; slower means it is starting. */
export const WAKE_HINT_MS = 1500;

export type WakeOptions = {
  /** Called once, when the server has not answered after `hintMs` (or a ping failed). */
  onWaking?: () => void;
  hintMs?: number;
  /** Give up after this long (a cold start is ~20 s; a failed start would never answer). */
  deadlineMs?: number;
  retryMs?: number;
  now?: () => number;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
};

export async function wake(
  client: LiveClient,
  signal: AbortSignal,
  {
    onWaking,
    hintMs = WAKE_HINT_MS,
    deadlineMs = 90_000,
    retryMs = 3000,
    now = () => Date.now(),
    sleep = abortableSleep,
  }: WakeOptions = {},
): Promise<Ready> {
  const start = now();
  let hinted = false;
  const hint = () => {
    if (!hinted) {
      hinted = true;
      onWaking?.();
    }
  };
  const timer = setTimeout(hint, hintMs);
  try {
    await retrying(() => client.health(signal));
    // Cloud Run routes only to an instance whose startup probe (/ready) passed, so this is quick.
    return await retrying(() => client.ready(signal));
  } finally {
    clearTimeout(timer);
  }

  async function retrying<T>(call: () => Promise<T>): Promise<T> {
    for (;;) {
      try {
        return await call();
      } catch (error) {
        if (signal.aborted || !(error instanceof ApiError)) throw error;
        // 4xx other than 429 will not change by waiting (CORS refusals arrive as "network").
        if (error.kind === "http" && error.status! < 500 && error.status !== 429) throw error;
        if (now() - start + retryMs > deadlineMs) throw error;
        hint();
        await sleep(error.retryAfter !== null ? error.retryAfter * 1000 : retryMs, signal);
      }
    }
  }
}

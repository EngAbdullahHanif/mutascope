/**
 * API errors in a reader's words. The API's own `detail` is written for developers (it names
 * routes); here each problem slug gets a sentence that says what to do, and `Retry-After`
 * becomes a time. Validation errors (422) are returned separately, to be shown at the input.
 */
import type { ApiError } from "./client";

export type Friendly = {
  message: string;
  /** Seconds to wait before trying again, when the server said. */
  retryAfter: number | null;
  /** For the sequence field instead of the page: a 422 from the API. */
  inputError: string | null;
};

/** `in 30 seconds`, `in about 2 minutes`. */
export function formatWait(seconds: number): string {
  if (seconds <= 1) return "in a second";
  if (seconds < 90) return `in ${seconds} seconds`;
  return `in about ${Math.round(seconds / 60)} minutes`;
}

/** The API's 422 messages, without pydantic's "Value error, " prefix. */
function validationText(error: ApiError): string {
  const field = error.fieldErrors.find((e) => e.loc.includes("sequence")) ?? error.fieldErrors[0];
  const text = field ? field.msg.replace(/^Value error, /, "") : error.message;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function friendlyError(error: ApiError): Friendly {
  const wait = error.retryAfter;
  const after = (fallback: string) => (wait !== null ? `Try again ${formatWait(wait)}.` : fallback);
  const out = (message: string): Friendly => ({ message, retryAfter: wait, inputError: null });

  if (error.kind === "network") {
    return out(
      "Could not reach the scoring server. Check your connection and try again; the rest of the site works without it.",
    );
  }
  switch (error.slug) {
    case "rate-limited":
      return out(`Too many requests from your address. ${after("Try again in a minute.")}`);
    case "scoring-busy":
      return out(`The server is busy scoring another sequence. ${after("Try again shortly.")}`);
    case "queue-full":
      return out(
        `The precise-mode queue is full. ${after("Try again in a few minutes,")} or use fast mode.`,
      );
    case "too-many-jobs":
      return out(
        "You already have a precise-mode job queued or running (perhaps in another tab). Cancel it, or wait for it to finish.",
      );
    case "too-many-streams":
      return out(
        "Too many progress streams are open from your address. Close other MutaScope tabs and try again.",
      );
    case "job-not-found":
      return out(error.message);
  }
  if (error.status === 422) {
    return { message: "", retryAfter: null, inputError: validationText(error) };
  }
  if (error.status === 413) return out("The request is too large for the server.");
  if (error.status === 429) return out(`Too many requests. ${after("Try again in a minute.")}`);
  if (error.status !== null && error.status >= 500) {
    return out(`The scoring server is unavailable right now. ${after("Try again in a minute.")}`);
  }
  return out(`Scoring failed: ${error.message}`);
}

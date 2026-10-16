/**
 * Plain-language wording for a failed API call, shared by every feature page so the same failure reads the same
 * everywhere. The page decides what to show next to it (a Try again button, a sign-in prompt).
 */

import { ApiError, errorMessages, isOfflineError } from "@/lib/api";

export type ProblemKind = "offline" | "signed_out" | "forbidden" | "not_found" | "busy" | "timeout" | "conflict" | "invalid" | "server";

export interface Problem {
  kind: ProblemKind;
  title: string;
  /** What the server said, or what the user can do. */
  messages: string[];
  /** Seconds to wait before trying again (a 429's Retry-After), when known. */
  retryAfterS: number | null;
}

/** "Try again in 30 seconds." style text for a Retry-After value. */
export function waitText(seconds: number | null): string | null {
  if (seconds === null) {
    return null;
  }
  if (seconds <= 1) {
    return "Try again in a moment.";
  }
  if (seconds < 90) {
    return `Try again in ${seconds} seconds.`;
  }
  return `Try again in about ${Math.round(seconds / 60)} minutes.`;
}

/** Classify any error thrown by `request` (or anything else) into a Problem the page can show. */
export function describeProblem(error: unknown, action = "This request"): Problem {
  if (isOfflineError(error)) {
    return {
      kind: "offline",
      title: "The server is not reachable right now",
      messages: ["It may be starting up (a sleeping Hugging Face Space needs a moment). Try again shortly."],
      retryAfterS: null,
    };
  }
  if (!(error instanceof ApiError)) {
    return { kind: "server", title: `${action} did not work`, messages: ["Something unexpected went wrong."], retryAfterS: null };
  }
  const messages = errorMessages(error.detail);
  const said = messages.length > 0 ? messages : [error.message];
  switch (error.status) {
    case 401:
      return { kind: "signed_out", title: "You are signed out", messages: ["Sign in again to continue."], retryAfterS: null };
    case 403:
      return { kind: "forbidden", title: "Not allowed for your account", messages: said, retryAfterS: null };
    case 404:
      return { kind: "not_found", title: "Not found", messages: said, retryAfterS: null };
    case 409:
      return { kind: "conflict", title: `${action} is not available`, messages: said, retryAfterS: null };
    case 413:
    case 422:
      return { kind: "invalid", title: "Some input was not accepted", messages: said, retryAfterS: null };
    case 429:
      return {
        kind: "busy",
        title: "Too many requests right now",
        // The global rate limit sends a generic sentence; a feature limit (address lookups) says what to wait for.
        messages:
          messages.length > 0 && !messages[0]?.startsWith("rate limit exceeded")
            ? messages
            : ["The server limits how many requests one connection or account can send per minute."],
        retryAfterS: error.retryAfterS,
      };
    case 503:
      return {
        kind: "timeout",
        title: "The server is busy or this took too long",
        messages: [...said, "Nothing was lost. Try again in a moment."],
        retryAfterS: error.retryAfterS,
      };
    default:
      return {
        kind: "server",
        title: `${action} did not work (HTTP ${error.status})`,
        messages: said,
        retryAfterS: error.retryAfterS,
      };
  }
}

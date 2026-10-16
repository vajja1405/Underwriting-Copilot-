import { HF_LOGIN_PATH } from "@/lib/config";

/**
 * Sign-in links and messages.
 *
 * - `next`: where to return after sign-in. The server checks it again (web/auth.py safe_next_path) and falls back
 *   to "/"; the same rule is applied here so the browser never builds a link that leaves this site.
 * - Error codes: the server's OAuth callback redirects to /signin?error=<code> (docs/web/auth.md, "Failures").
 */

const MAX_NEXT_PATH_CHARS = 512;
const SIGN_IN_PAGE = "/signin";

/** A local path such as "/studio" or "/lender/file?id=3", or null for anything that could leave this site. */
export function safeNextPath(value: string | null | undefined): string | null {
  if (!value || value.length > MAX_NEXT_PATH_CHARS) {
    return null;
  }
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("\\")) {
    return null;
  }
  for (const char of value) {
    const code = char.charCodeAt(0);
    if (code < 32 || code === 127) {
      return null;
    }
  }
  return value;
}

/** True for the sign-in page itself (returning there after sign-in would be pointless). */
function isSignInPage(path: string): boolean {
  return path === SIGN_IN_PAGE || path.startsWith(`${SIGN_IN_PAGE}/`) || path.startsWith(`${SIGN_IN_PAGE}?`);
}

/** The sign-in page, remembering where to come back to. */
export function signInPageHref(next: string | null | undefined): string {
  const safe = safeNextPath(next);
  if (!safe || safe === "/" || isSignInPage(safe)) {
    return SIGN_IN_PAGE;
  }
  return `${SIGN_IN_PAGE}?next=${encodeURIComponent(safe)}`;
}

/** The server endpoint that starts "Sign in with Hugging Face", remembering where to come back to. */
export function hfLoginHref(next: string | null | undefined): string {
  const safe = safeNextPath(next);
  if (!safe || isSignInPage(safe)) {
    return HF_LOGIN_PATH;
  }
  return `${HF_LOGIN_PATH}?next=${encodeURIComponent(safe)}`;
}

export type SignInErrorCode = "access_denied" | "state_mismatch" | "provider_error" | "oauth_not_configured";

export interface SignInErrorMessage {
  title: string;
  body: string;
  /** Show the "Open in a new tab" link (the iframe may have blocked the sign-in cookie). */
  offerNewTab: boolean;
}

/** What the sign-in page says for each error code the server sends (wording from docs/web/auth.md). */
export const SIGN_IN_ERRORS: Record<SignInErrorCode, SignInErrorMessage> = {
  access_denied: {
    title: "Sign-in was cancelled.",
    body: "Nothing was shared. You can sign in again at any time.",
    offerNewTab: false,
  },
  state_mismatch: {
    title: "Sign-in did not finish.",
    body: "It took longer than 10 minutes, or the browser blocked the sign-in cookie inside the Hugging Face page. Open the app in a new tab and sign in there.",
    offerNewTab: true,
  },
  provider_error: {
    title: "Hugging Face did not answer as expected.",
    body: "Try again later.",
    offerNewTab: false,
  },
  oauth_not_configured: {
    title: "Hugging Face sign-in is not set up on this server.",
    body: "It is available only when the app runs as a Hugging Face Space with sign-in turned on.",
    offerNewTab: false,
  },
};

/** The message for an error code from the URL, or a generic one for an unknown code; null when there is none. */
export function signInErrorMessage(code: string | null): SignInErrorMessage | null {
  if (!code) {
    return null;
  }
  if (code in SIGN_IN_ERRORS) {
    return SIGN_IN_ERRORS[code as SignInErrorCode];
  }
  return { title: "Sign-in did not work.", body: "Try again.", offerNewTab: false };
}

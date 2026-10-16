/**
 * Product-wide constants for the frontend.
 *
 * Rename the product HERE (and in src/uwcopilot/web/settings.py PRODUCT_NAME on the server).
 * At runtime the server's /api/config wins for PRODUCT_NAME and DISCLAIMER (see lib/auth.tsx);
 * these values are the fallback used for the static build and when the API is unreachable.
 */

/** Working product name (final name pending the owners' decision). */
export const PRODUCT_NAME = "Fundproof";

/** One-line description used in the header, page titles and the landing hero. */
export const TAGLINE = "Every number in a small-business loan file, with its evidence attached.";

/** Shown in the footer of EVERY page (Studio addendum rule C). Keep in sync with the server's DISCLAIMER. */
export const DISCLAIMER =
  "Educational planning tool. Not financial, legal or lending advice. Lenders make their own decisions.";

/** Public source code (the repository this app is built from). */
export const REPO_URL = "https://github.com/vajja1405/Underwriting-Copilot-";

/** The two owners of the project (shown on the About page). */
export const OWNERS = ["Bhavani Adula", "Rahul Vajja"] as const;

/** Where the "Sign in with Hugging Face" button sends the browser (a full-page redirect, not a fetch). */
export const HF_LOGIN_PATH = "/api/auth/login";

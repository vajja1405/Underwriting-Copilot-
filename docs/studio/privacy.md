# Founder Studio and Lender Desk: privacy notice for the v0.1 web app

> **Status: draft, written 2026-10-13 from the v0.1 code, for the owners (Bhavani Adula and Rahul Vajja) to review
> before the first public deploy.** Studio addendum rule B says the deployed app may take real founder input only
> under this document. Everything below describes what the code in this repository does today, with the file that
> does it; what is not built yet is listed at the end. This is a demonstration prototype, not a compliance program:
> whether any privacy law applies depends on who runs it and how, and this page does not claim compliance with any.

The app shows the short version of this notice on its About page (section "Your information"). Until the owners
approve this document, the Founder Studio pages also show a "Prototype: please use a made-up plan" notice
(`frontend/components/studio/prototype-data-notice.tsx`) asking visitors to use invented figures and public addresses
only; remove that notice when this document is approved.

## 1. What the app asks for, and what it refuses

| Input | Where | Kept? |
|---|---|---|
| Hugging Face sign-in | every signed-in page | The account id, username and display name in a signed session cookie (8 hours by default, `UWC_SESSION_MAX_AGE_S`). Only the `openid profile` scopes are requested (`web/settings.py`, `OAUTH_SCOPES`); no e-mail address. |
| A plan: city, business type, idea text (up to 2,000 characters), money figures, opening date, 1 to 3 sites (a map pin, or for a real city a street address) | Founder Studio wizard | Yes, as one encrypted object per plan (section 3). |
| A DEMO decision on a SYNTHETIC file: action and rationale | Lender Desk | Yes, as one encrypted record per user and file. |

**Refused, never stored** (`web/services/text_checks.py`, 422 with a plain message that does not repeat the text):
an SSN-shaped number (`123-45-6789`, also with spaces), a labeled account number (`acct 12345678`) or any run of 8
to 17 digits, in the plan name, the idea, a site name, a site address and a decision rationale. A site address is
checked **before** it is sent anywhere. The metro's own 11-digit census tract ids are allowed in site names (the
wizard writes them; a public tract id is not personal data). There are **no uploads** in v0.1, so no tax returns,
bank statements or other documents can be sent.

Requests are small by design: a JSON body over 64 KiB is refused (413) before it is read further
(`web/security.py`, `API_MAX_BODY_BYTES`).

## 2. Who else sees what you type

- **The US Census Geocoder** (a public service of the US Census Bureau at geocoding.geo.census.gov). When you type a
  street address for a real city, the server sends that address to it over HTTPS to find the location and census
  tract (`web/services/geocoding.py`). Nothing else from your plan is sent. The answer is not cached anywhere on the
  server (not on disk and not in a shared memory cache); the match is stored inside your encrypted plan, and a later
  change to the plan reuses it instead of asking again. Its terms of use are listed as "to verify" in
  [data_sources.md](data_sources.md).
- **No language model** reads your idea text, your numbers or your rationale in v0.1. The idea text is screened for
  instruction-like text (`agent/injection.detect` after normalization) and, when flagged, marked as such; it is
  shown back only to you. The Lender Desk demo writes its memo with a fixed template (no model).
- **Nobody else signed in** can read your plans or records: every object is stored under your account id, and
  another user's id answers "not found" (tests: `tests/web/test_web_studio.py`, `tests/web/test_web_lender.py`).
- **Hugging Face** hosts the app (the Space) and, if the owners turn it on, a private dataset that mirrors the
  encrypted store (section 3). The app has no code that passes your data to anyone else, and no analytics or tracking
  script (the Content-Security-Policy allows scripts and connections only to the app's own origin). Whether the
  owners commit to "never sold" in a policy is their decision (section 6).

## 3. How it is stored

- **Encrypted at rest** with Fernet (`web/crypto.py`, `web/store.py`): plans and decision records are encrypted
  before they are written, both on the container disk and in the optional private Hugging Face dataset
  (`UWC_STORE=hf_dataset`, `web/hf_store.py`, which refuses a public dataset).
- **Per account**: objects live under a digest of the account id (`store.owner_digest`).
- **Results** (site scores, simulations, path searches, lender file runs) are kept in the server's memory only, per
  plan version, and dropped when the plan changes or is deleted, and when the server restarts
  (`web/services/compute.py`).
- **Address lookups** run in their own small worker pool with per-user limits (one batch at a time, a small budget
  of addresses per minute), so they cannot be used to flood the geocoder from this server.

## 4. Deletion and retention

- **On request**: "Delete plan" on a plan's page deletes the plan and drops its results from memory; a DEMO decision
  record can be deleted from its file (`DELETE /api/lender/files/{id}/record`). With the dataset store, the deleted
  files are removed from the dataset at once and its history is squashed at the next flush so earlier revisions do
  not keep them (`web/hf_store.py`); if the Hub cannot be reached the app says the deletion is pending instead of
  claiming it is done.
- **Automatically**: every stored object expires after the retention period (`UWC_RETENTION_DAYS`, 30 days by
  default, a design choice) and is deleted at startup and then every hour (`UWC_PURGE_EVERY_S`). A plan's page
  shows its own deletion date.
- Without the dataset store, everything also disappears when the Space restarts or rebuilds.

## 5. Logs

- One line per request with the method, the path with personal patterns masked, the **names** of query parameters
  (never their values), the status and the time (`web/access_log.py`). No bodies, cookies, headers, IP addresses or
  usernames are logged; uvicorn's own access log is off (`--no-access-log` in the Dockerfile).
- A failed address lookup is logged with the geocoder's URL without its query and the error, not the address.
- A failing Lender Desk tool is logged in full on the server only; the page and the DEMO record show a plain
  sentence (`web/services/lender_demo.py`, `SafeTool`).

## 6. Not built yet (Studio Prompt S8)

- A Terms of Use page and a privacy policy page inside the app (the About page carries the short notice), with the
  owners' own commitments (for example that data is never sold).
- A single "delete all my data" button (the store has `delete_owner`; no route uses it yet).
- An audit log of every access to a founder's package, signed expiring share links, and a STRIDE threat model.
- Document uploads, and their screening for tax returns and account numbers (there are no uploads in v0.1).

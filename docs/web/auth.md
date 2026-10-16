# Sign-in, sessions and roles in the web app

This page explains how people sign in to the Fundproof web app (working name), what the cookies hold, why the
cookies look the way they do inside a Hugging Face Space, and how roles are given. Code: `src/uwcopilot/web/`
(`auth.py`, `cookies.py`, `security.py`, `routers/auth.py`). Tests: `tests/web/test_web_auth.py`,
`tests/web/test_web_security.py`.

Status: the flow is tested against a mock OpenID provider (`httpx.MockTransport`). It has **not yet run against
the real Hugging Face sign-in**, because no Space exists yet. Treat everything below about the live Space as the
design, to be checked on the first deploy.

## Two ways to sign in

| Where | Method | How it is turned on |
|---|---|---|
| Hugging Face Space | "Sign in with Hugging Face" (OpenID Connect, authorization code + PKCE) | `hf_oauth: true` in the Space README; the Space then injects `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_SCOPES`, `OPENID_PROVIDER_URL` |
| Local development | DEV LOGIN: type any username and pick a role | `UWC_DEV_AUTH=1` |

The dev login lets anyone choose any role, so it can never run in a Space: when `SPACE_ID` is set (Hugging Face
sets it in every Space) and `UWC_DEV_AUTH=1`, the app refuses to start with a clear error. The endpoint checks again
on every call. A dev session cookie is also ignored as soon as the dev login is turned off, and a Hugging Face
session cookie is ignored when OAuth is not configured.

Outside a Space, Hugging Face sign-in works only if you register your own OAuth app and set the three `OAUTH_*`
variables plus `UWC_PUBLIC_URL` (the callback is `<UWC_PUBLIC_URL>/api/auth/callback`). Without them,
`GET /api/auth/login` sends the browser to `/signin?error=oauth_not_configured`.

## Why not huggingface_hub's `attach_huggingface_oauth`

The task asked us to use `attach_huggingface_oauth` / `parse_huggingface_oauth` from the installed
huggingface_hub (1.33.0, file `huggingface_hub/_oauth.py`) if they fit. We read the source. They do not fit:

1. The session cookie is set by Starlette's `SessionMiddleware` with `SameSite=None; Secure` but **without
   `Partitioned`**, so browsers that block third-party cookies drop it inside the Space iframe (see below).
2. The whole token response, including the Hugging Face **access token**, is stored in that cookie. It is signed,
   not encrypted, so anyone holding the cookie can read the token. We do not want to hold a user's token at all.
3. The `_target_url` query parameter is used as the redirect after login and logout without checking it: an
   **open redirect** (`/oauth/huggingface/login?_target_url=https://evil.example`).
4. Logout is a `GET`, so another site can log a user out with an image tag (no CSRF protection).
5. Outside a Space it replaces sign-in with a mock that calls `whoami()` with the developer machine's own token,
   which needs network access and a logged-in machine; our tests must run offline.
6. It needs the extra `authlib` package and is marked `@experimental`.

So `auth.py` implements the small flow the Hugging Face documentation describes
(https://huggingface.co/docs/hub/spaces-oauth, read 2026-10-12), using `httpx`, which the project already has.

## The flow

1. **`GET /api/auth/login?next=/studio`.** The app creates a random `state` and a PKCE verifier/challenge
   (RFC 7636, S256), stores `{state, verifier, next}` in a signed cookie `uwc_oauth` that lives 10 minutes, and
   redirects to the provider's authorization endpoint. The endpoints come from OpenID discovery at
   `{OPENID_PROVIDER_URL}/.well-known/openid-configuration`. On 2026-10-12 that document for
   `https://huggingface.co` listed `authorization_endpoint` `https://huggingface.co/oauth/authorize`,
   `token_endpoint` `https://huggingface.co/oauth/token`, `userinfo_endpoint` `https://huggingface.co/oauth/userinfo`,
   `code_challenge_methods_supported: ["S256"]` and `token_endpoint_auth_methods_supported:
   ["client_secret_basic", "client_secret_post"]`.
   `next` must be a local path (`/studio`, `/lender/file?id=3`); anything else (`https://...`, `//host`, `/\host`,
   control characters) becomes `/`. Only the scopes `openid profile` are requested (data minimization), whatever
   the Space allows.
2. **Hugging Face** asks the user to approve, then sends the browser to
   `https://<SPACE_HOST>/api/auth/callback?code=...&state=...`. The docs say any redirect URL on the Space's own
   host is accepted.
3. **`GET /api/auth/callback`** checks that `state` equals the one in the `uwc_oauth` cookie, exchanges the code
   at the token endpoint (client id and secret with HTTP Basic auth, plus the PKCE verifier), reads
   `sub`, `preferred_username` and `name` from the userinfo endpoint with the new access token, and then
   **drops the access token**. The ID token is not used, so its signature does not need checking; identity comes
   from the userinfo call over TLS. The app sets the session cookie and redirects to `next`.

Failures redirect to `/signin?error=<code>`:

| code | meaning | what the sign-in page says (`lib/sign-in.ts`, shown in a `role="alert"` box) |
|---|---|---|
| `access_denied` | the user pressed cancel | "Sign-in was cancelled." |
| `state_mismatch` | the `uwc_oauth` cookie was missing or did not match: sign-in took over 10 minutes, or the browser blocked the cookie in the iframe | offer "Open in a new tab" |
| `provider_error` | Hugging Face did not answer as expected | "Try again later." |
| `oauth_not_configured` | no OAuth settings (local development without your own OAuth app) | show the dev login if it is on |

## Cookies

| Cookie | Holds | Lifetime | HttpOnly |
|---|---|---|---|
| `uwc_session` | signed JSON: auth mode, stable subject id, username, display name, the dev role (dev login only), issue time | 8 hours (`UWC_SESSION_MAX_AGE_S`) | yes |
| `uwc_oauth` | signed JSON: state, PKCE verifier, `next` | 10 minutes | yes |
| `uwc_csrf` | random token (32 bytes, URL-safe base64) | browser session | no (the page may read it) |

Values are signed with HMAC-SHA256 using `UWC_SESSION_SECRET` and a different salt per cookie, so a cookie of one
type cannot be replayed as another. They are signed, **not encrypted**: they hold no secret and no token.
Roles are not stored in the cookie (except the dev role); they are recomputed from `UWC_ROLE_ALLOWLIST` on every
request, so removing someone from the allowlist takes effect immediately.

Attributes:

| Environment | Attributes |
|---|---|
| Space (`SPACE_ID` set) | `Path=/; Secure; SameSite=None; Partitioned` |
| Local development | `Path=/; SameSite=Lax` (no `Secure`, because local development is plain `http://localhost`) |

No cookie sets `Domain`, so cookies stay on the Space's own host.

Starlette's `Response.set_cookie` writes `Partitioned` only on Python 3.14 and later (it raises on older
versions), and this project runs Python 3.11, so `cookies.build_set_cookie()` writes the header text itself.

### Why `SameSite=None; Partitioned` in a Space (the iframe problem)

A Space page on huggingface.co shows the app in an **iframe** whose address is `https://<SPACE_HOST>` (a
`*.hf.space` host). For the browser, cookies set inside that iframe are **third-party cookies**:

- they are only sent at all with `SameSite=None`, which browsers accept only together with `Secure`;
- browsers that block third-party cookies (Safari by default, Chrome and Firefox in some settings) still accept
  a cookie marked `Partitioned` ("CHIPS"): it is stored in a separate jar keyed by the top-level site
  (huggingface.co), so it cannot be used to track the user across sites.

Two consequences:

1. **Sign-in may still fail inside the iframe** on some browsers. The Hugging Face docs say: "You should use
   target=_blank on the button to open the sign-in page in a new tab, unless you run the space outside its
   iframe. Otherwise, you might encounter issues with cookies on some browsers." So when the page is framed the
   sign-in button opens `/api/auth/login?next=...` with `target="_blank"`, and `/signin` shows an "Open in a new
   tab" link that goes to the Space's own address (`space_url` in `GET /api/config`, `https://<SPACE_HOST>`); the
   same link appears in the `state_mismatch` error message. Both were checked only in a simulated iframe (a local
   parent page, with `frame-ancestors` relaxed in the test); a real Space has not been tried.
2. **The iframe and the new tab have separate cookie jars.** Signing in in a new tab signs you in in that tab
   (top-level site `hf.space`), not in the iframe on huggingface.co. The user should keep using the new tab.

Logout removes the session cookie with a `Set-Cookie` that repeats `Partitioned` (a partitioned cookie can only be
removed that way).

## CSRF protection

Every `POST`, `PUT`, `PATCH` and `DELETE` under `/api` needs the header `X-CSRF-Token` equal to the `uwc_csrf`
cookie (double-submit). The frontend calls `GET /api/auth/csrf` (body `{"token": "..."}`), keeps the token in
memory and sends it with every state-changing call. A cross-site page cannot read our cookie, and it cannot add a
custom header to a cross-site request without a CORS preflight, which this app never approves (there is no CORS
middleware except the local-development rule below). The dev login is a `POST`, so it is protected the same way.
The Hugging Face sign-in is protected by the `state` check instead: a callback whose `state` does not match this
browser's `uwc_oauth` cookie is refused, so another site cannot plant a sign-in to an account of its choosing.

CORS: only when the dev login is on, `http://localhost:3000` (the `next dev` server) is allowed. The frontend's
`next dev` setup proxies `/api` to the FastAPI server, so normally even development is same-origin.

## Roles

| Role | Who has it |
|---|---|
| `founder` | every signed-in user |
| `lender_reviewer` | users listed in `UWC_ROLE_ALLOWLIST`; also everyone with `lender_senior` |
| `lender_senior` | users listed in `UWC_ROLE_ALLOWLIST` (for example, needed to decline) |
| `admin` | users listed in `UWC_ROLE_ALLOWLIST`; operating the app, deliberately **not** a lender role |

`UWC_ROLE_ALLOWLIST="astra6@<id>:admin,astra6@<id>:lender_senior,someone@<id>:lender_reviewer"`: list a user
once per role. Usernames are compared without case, ids exactly. Unknown roles stop the app at startup.

Usernames can be renamed on Hugging Face, and a freed name can be taken by someone else. So an entry should be
**pinned to the account id**: `hfuser@<id>:role` grants the role only to a session whose subject (the `sub` the
Hugging Face sign-in returns) is `<id>`; whoever later registers a freed `hfuser` has another id and gets nothing.
To find your id: sign in once, open `/api/me`, copy `subject`. The plain form `hfuser:role` still works (handy for
local development), but in a Space the app logs a startup warning for every unpinned entry. Stored data is keyed
by the same stable subject id (`hf_oauth:<sub>`), never by the username.

Feature routers protect endpoints with one dependency. The role logic still lives in `web/auth.py`:
`src/uwcopilot/security/rbac.py` now exists (Phase 5 B), but the web has not adopted it yet; the adoption steps are
in docs/security.md ("Web adoption"):

```python
from typing import Annotated
from fastapi import Depends
from uwcopilot.web.auth import Principal, require_role

SeniorLender = Annotated[Principal, Depends(require_role("lender_senior"))]


@router.post("/api/lender/files/{file_id}/decision")
def decide(file_id: str, principal: SeniorLender) -> ...: ...
```

`require_role()` with no roles means "any signed-in user". Signed out gives 401; signed in without the role
gives 403. Remember: the app never decides; a decision endpoint records a person's decision.

## Settings and Space secrets for sign-in

| Name | Where | Notes |
|---|---|---|
| `UWC_SESSION_SECRET` | Space secret (required in a Space) | at least 32 random characters, for example `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Changing it signs everyone out |
| `UWC_ROLE_ALLOWLIST` | Space secret or variable | see Roles |
| `UWC_SESSION_MAX_AGE_S` | optional | default 28800 (8 hours, the default Hugging Face OAuth token lifetime per the docs) |
| `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET`, `OAUTH_SCOPES`, `OPENID_PROVIDER_URL` | injected by Hugging Face | never set them by hand in a Space |
| `UWC_DEV_AUTH` | local `.env` only | `1` turns on the dev login; refused in a Space |
| `UWC_PUBLIC_URL` | local only | for testing your own OAuth app outside a Space |

## Known limits

- Sessions are stateless signed cookies. Logout removes the cookie from the browser, but a copy of the cookie
  taken before logout stays valid until it expires (at most 8 hours). Rotating `UWC_SESSION_SECRET` ends every
  session. A server-side session list would fix this; it is not built.
- Rate limits are per process and in memory (`security.py`). The client IP comes from `X-Forwarded-For`, trusting
  `UWC_TRUSTED_PROXY_HOPS` proxies (default 1 in a Space). How many proxies Hugging Face puts in front of a Space
  has not been checked; verify it on the first deploy.
- Run uvicorn with `--no-access-log`: the app writes its own access line with query values removed and personal
  data masked; uvicorn's line would add the client IP and the raw query string.

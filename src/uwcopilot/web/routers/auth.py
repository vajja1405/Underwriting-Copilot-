"""Sign-in endpoints. JSON shapes (the frontend builds against these):

GET /api/me -> 200
    {"signed_in": false, "username": null, "display_name": null, "subject": null, "roles": [], "auth_mode": "none"}
    {"signed_in": true, "username": "astra6", "display_name": "Rahul", "subject": "6123abc...",
     "roles": ["founder", "admin"], "auth_mode": "hf_oauth" | "dev"}
    auth_mode says how THIS session signed in ("none" when signed out). Which sign-in methods the server offers is
    in GET /api/config -> hf_oauth and dev_auth. `subject` is the user's own stable account id (Hugging Face "sub";
    the username for a dev session); an owner pins an allowlist entry to it (UWC_ROLE_ALLOWLIST "hfuser@id:role").

GET /api/auth/csrf -> 200 {"token": "<43 URL-safe characters>"}
    Also sets cookie uwc_csrf (not HttpOnly). Send the token back in the X-CSRF-Token header on every POST, PUT,
    PATCH and DELETE under /api. Keep the token from this JSON body in memory; do not parse document.cookie.
    The same token is returned while the cookie lasts (one per browser session).

GET /api/auth/login?next=/studio -> 302 to the Hugging Face sign-in page.
    `next` must be a local path; anything else becomes "/". Open it as a top-level page: inside the Space iframe
    use target="_blank" (the Hugging Face docs recommend this; some browsers block cookies in the iframe).
    If sign-in is not configured -> 302 to /signin?error=oauth_not_configured.

GET /api/auth/callback?code=...&state=... (called by Hugging Face, not by the frontend)
    Success -> session cookie set, 302 to `next`.
    Failure -> 302 to /signin?error=<code> with code one of:
      access_denied (the user cancelled), state_mismatch (sign-in took too long or the browser blocked cookies in
      the iframe: offer "open in a new tab"), provider_error (Hugging Face did not answer as expected),
      oauth_not_configured.

POST /api/auth/logout (X-CSRF-Token required) -> 200 {"signed_in": false}; the session cookie is removed.

POST /api/auth/dev-login (X-CSRF-Token required; only when UWC_DEV_AUTH=1, which a Space refuses)
    body {"username": "demo_lender", "role": "founder" | "lender_reviewer" | "lender_senior" | "admin"}
    -> 200 with the /api/me body for the new session (auth_mode "dev"); 403 when the dev login is off;
    422 for a bad username or role. The UI must label it "DEV LOGIN".
"""

import logging
import re
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from uwcopilot.web.auth import (
    OAUTH_STATE_MAX_AGE_S,
    HfOAuthClient,
    OAuthError,
    Principal,
    new_pkce_pair,
    oauth_state_serializer,
    principal_from_payload,
    read_principal,
    safe_next_path,
    session_payload,
)
from uwcopilot.web.cookies import (
    CSRF_COOKIE,
    OAUTH_STATE_COOKIE,
    SESSION_COOKIE,
    CookiePolicy,
    SignedSerializer,
    clear_cookie,
    set_cookie,
)
from uwcopilot.web.settings import USERNAME_PATTERN, WebSettings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["auth"])

CSRF_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
SIGNIN_PAGE = "/signin"
RoleName = Literal["founder", "lender_reviewer", "lender_senior", "admin"]


class MeResponse(BaseModel):
    """Who is signed in."""

    signed_in: bool
    username: str | None
    display_name: str | None
    subject: str | None
    roles: list[str]
    auth_mode: Literal["hf_oauth", "dev", "none"]


class CsrfResponse(BaseModel):
    """The CSRF token to echo in X-CSRF-Token."""

    token: str


class LogoutResponse(BaseModel):
    """Always signed out after logout."""

    signed_in: Literal[False]


class DevLoginRequest(BaseModel):
    """A dev-only sign-in as any username with one role."""

    username: str = Field(min_length=1, max_length=64, pattern=USERNAME_PATTERN.pattern)
    role: RoleName


def me_body(principal: Principal | None) -> MeResponse:
    """The /api/me body for a principal (or for nobody)."""
    if principal is None:
        return MeResponse(signed_in=False, username=None, display_name=None, subject=None, roles=[], auth_mode="none")
    return MeResponse(
        signed_in=True,
        username=principal.username,
        display_name=principal.display_name,
        subject=principal.subject,
        roles=list(principal.roles),
        auth_mode=principal.auth_mode,
    )


def signin_error(error: str) -> RedirectResponse:
    """302 to the sign-in page with an error code the UI explains."""
    return RedirectResponse(f"{SIGNIN_PAGE}?error={error}", status_code=status.HTTP_302_FOUND)


def app_settings(request: Request) -> WebSettings:
    """The settings stored on the app."""
    return request.app.state.settings


def cookie_policy(request: Request) -> CookiePolicy:
    """The cookie attributes for this environment."""
    return request.app.state.cookie_policy


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    """The signed-in user, or signed_in false."""
    return me_body(read_principal(request))


@router.get("/auth/csrf", response_model=CsrfResponse)
def csrf_token(request: Request) -> JSONResponse:
    """Issue (or repeat) the double-submit CSRF token."""
    existing = request.cookies.get(CSRF_COOKIE, "")
    token = existing if CSRF_TOKEN_PATTERN.match(existing) else secrets.token_urlsafe(32)
    response = JSONResponse(CsrfResponse(token=token).model_dump(), headers={"Cache-Control": "no-store"})
    set_cookie(response, CSRF_COOKIE, token, cookie_policy(request), httponly=False)
    return response


@router.get("/auth/login")
def login(request: Request, next_path: str | None = Query(default=None, alias="next")) -> RedirectResponse:
    """Start the Hugging Face sign-in (OpenID Connect authorization code + PKCE)."""
    client: HfOAuthClient | None = request.app.state.oauth_client
    if client is None:
        return signin_error("oauth_not_configured")
    state = secrets.token_urlsafe(32)
    verifier, challenge = new_pkce_pair()
    try:
        target = client.authorization_url(state, challenge)
    except OAuthError as error:
        logger.warning("sign-in could not start: %s", error)
        return signin_error("provider_error")
    serializer: SignedSerializer = oauth_state_serializer(app_settings(request))
    state_cookie = serializer.dumps({"st": state, "v": verifier, "nx": safe_next_path(next_path)})
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    set_cookie(response, OAUTH_STATE_COOKIE, state_cookie, cookie_policy(request), max_age=OAUTH_STATE_MAX_AGE_S)
    return response


@router.get("/auth/callback", include_in_schema=False)
def callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    """Finish the sign-in: check state, exchange the code, read the profile, set the session cookie."""
    settings = app_settings(request)
    client: HfOAuthClient | None = request.app.state.oauth_client
    if client is None:
        return signin_error("oauth_not_configured")
    if error:
        return signin_error("access_denied" if error == "access_denied" else "provider_error")
    saved = oauth_state_serializer(settings).loads(request.cookies.get(OAUTH_STATE_COOKIE), OAUTH_STATE_MAX_AGE_S)
    if saved is None or not state or not code or not secrets.compare_digest(str(saved.get("st", "")), state):
        return signin_error("state_mismatch")
    try:
        identity = client.fetch_identity(client.exchange_code(code, str(saved.get("v", ""))))
    except OAuthError as failure:
        logger.warning("sign-in callback failed: %s", failure)
        return signin_error("provider_error")

    payload = session_payload("hf_oauth", identity.subject, identity.username, identity.display_name)
    session = request.app.state.session_serializer.dumps(payload)
    response = RedirectResponse(safe_next_path(str(saved.get("nx", "/"))), status_code=status.HTTP_302_FOUND)
    set_cookie(response, SESSION_COOKIE, session, cookie_policy(request), max_age=settings.session_max_age_s)
    clear_cookie(response, OAUTH_STATE_COOKIE, cookie_policy(request))
    return response


@router.post("/auth/logout", response_model=LogoutResponse)
def logout(request: Request) -> JSONResponse:
    """End the session (the session lives only in the cookie, so removing it is enough)."""
    response = JSONResponse(LogoutResponse(signed_in=False).model_dump())
    clear_cookie(response, SESSION_COOKIE, cookie_policy(request))
    return response


@router.post("/auth/dev-login", response_model=MeResponse)
def dev_login(request: Request, body: DevLoginRequest) -> JSONResponse:
    """DEV LOGIN: sign in as any username with one role. Only when UWC_DEV_AUTH=1 (never in a Space)."""
    settings = app_settings(request)
    if not settings.dev_auth or settings.is_space:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="dev login is disabled")
    payload = session_payload("dev", body.username, body.username, body.username, dev_role=body.role)
    session = request.app.state.session_serializer.dumps(payload)
    # The cookie takes effect on the next request; answer with the user that request will see.
    response = JSONResponse(me_body(principal_from_payload(payload, settings)).model_dump())
    set_cookie(response, SESSION_COOKIE, session, cookie_policy(request), max_age=settings.session_max_age_s)
    return response

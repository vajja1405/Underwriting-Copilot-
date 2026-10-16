"""Who is signed in, which roles they have, and the Hugging Face sign-in (OpenID Connect + PKCE).

Why not huggingface_hub's attach_huggingface_oauth (huggingface_hub 1.33.0, _oauth.py)? We read its source and it
does not fit this app (details in docs/web/auth.md): it is marked experimental; it needs authlib; its session cookie
is not Partitioned (so it breaks in the Space iframe when third-party cookies are blocked); it keeps the HF access
token in a readable (signed, not encrypted) cookie; its `_target_url` parameter is an open redirect; logout is a
GET without CSRF protection; and outside a Space it calls whoami() with the machine's own token. So this module
implements the small flow the Hugging Face docs describe (huggingface.co/docs/hub/spaces-oauth, read 2026-10-12):

1. GET /api/auth/login: make `state` and a PKCE verifier, keep both in a short-lived signed cookie, redirect to
   the provider's authorization endpoint (found by OpenID discovery at OPENID_PROVIDER_URL).
2. GET /api/auth/callback: check `state` against the cookie, exchange the code (client secret via HTTP Basic +
   PKCE verifier), read the profile from the userinfo endpoint with the new access token, then drop the token.
   The ID token is not used, so its signature does not need checking: identity comes from userinfo over TLS.
3. The session cookie holds only: auth mode, the stable subject id, username, display name (signed, 8 hours).

Roles: founder for every signed-in user; lender_reviewer, lender_senior and admin only from UWC_ROLE_ALLOWLIST.
lender_senior also counts as lender_reviewer (a senior reviewer can do what a reviewer can). admin implies no
lender role: operating the app is not a credit role. Roles are recomputed from the allowlist on every request, so
removing someone from the allowlist takes effect at once. An allowlist entry pinned to an account id
("hfuser@id:role") matches only a session whose subject is that id, so a renamed and re-registered username does
not inherit the role (settings.parse_role_allowlist). There is no src/uwcopilot/security/rbac.py in the repo
yet, so the role logic lives here behind require_role(); move it there if the Phase 5 RBAC module appears.
"""

import base64
import hashlib
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request, status

from uwcopilot.common.settings import is_http_url
from uwcopilot.web.cookies import SESSION_COOKIE, SignedSerializer
from uwcopilot.web.settings import ROLES, USERNAME_PATTERN, OAuthSettings, WebSettings, allowlist_key

AuthMode = Literal["hf_oauth", "dev"]
# A role on the left also grants the roles on the right.
ROLE_IMPLIES: dict[str, tuple[str, ...]] = {"lender_senior": ("lender_reviewer",)}
SESSION_SALT = "uwc-session-v1"
OAUTH_STATE_SALT = "uwc-oauth-state-v1"
OAUTH_STATE_MAX_AGE_S = 10 * 60
MAX_NEXT_PATH_CHARS = 512
DISPLAY_NAME_MAX_CHARS = 100


@dataclass(frozen=True)
class Principal:
    """The signed-in user as the API sees them."""

    subject: str
    username: str
    display_name: str
    auth_mode: AuthMode
    roles: tuple[str, ...]

    @property
    def owner_id(self) -> str:
        """Stable id for everything this user stores (HF usernames can be renamed; the subject cannot)."""
        return f"{self.auth_mode}:{self.subject}"

    def has_any_role(self, roles: Iterable[str]) -> bool:
        """True if the user holds at least one of roles."""
        return any(role in self.roles for role in roles)


def effective_roles(
    username: str, allowlist: dict[str, tuple[str, ...]], extra_role: str | None = None, subject: str | None = None
) -> tuple[str, ...]:
    """founder + allowlisted roles + extra_role (dev login) + implied roles, in the canonical order of ROLES.

    Allowlisted roles come from the username-only entry and, when subject is known, from the entry pinned to
    username AND subject (an entry pinned to another id does not match).
    """
    granted = {"founder", *allowlist.get(allowlist_key(username), ())}
    if subject:
        granted.update(allowlist.get(allowlist_key(username, subject), ()))
    if extra_role:
        granted.add(extra_role)
    for role in list(granted):
        granted.update(ROLE_IMPLIES.get(role, ()))
    return tuple(role for role in ROLES if role in granted)


def session_serializer(settings: WebSettings) -> SignedSerializer:
    """The signer for session cookies."""
    return SignedSerializer(settings.session_secret.get_secret_value(), SESSION_SALT)


def oauth_state_serializer(settings: WebSettings) -> SignedSerializer:
    """The signer for the short-lived OAuth state cookie (a different salt, so it cannot pass as a session)."""
    return SignedSerializer(settings.session_secret.get_secret_value(), OAUTH_STATE_SALT)


def session_payload(
    auth_mode: AuthMode, subject: str, username: str, display_name: str, dev_role: str | None = None
) -> dict[str, Any]:
    """What goes into the signed session cookie: identity only, never a token."""
    return {"m": auth_mode, "s": subject, "u": username, "n": display_name[:DISPLAY_NAME_MAX_CHARS], "r": dev_role}


def principal_from_payload(payload: dict[str, Any] | None, settings: WebSettings) -> Principal | None:
    """The Principal for a verified session payload, or None if it is missing or no longer allowed.

    A dev session is accepted only while the dev login is on, and an HF session only while OAuth is configured,
    so turning a sign-in method off also ends its sessions.
    """
    if payload is None:
        return None
    mode, subject, username = payload.get("m"), payload.get("s"), payload.get("u")
    if mode == "dev" and not settings.dev_auth:
        return None
    if mode == "hf_oauth" and settings.oauth is None:
        return None
    if mode not in ("dev", "hf_oauth") or not isinstance(subject, str) or not subject:
        return None
    if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
        return None
    dev_role = payload.get("r") if mode == "dev" and payload.get("r") in ROLES else None
    display_name = payload.get("n") if isinstance(payload.get("n"), str) else username
    return Principal(
        subject=subject,
        username=username,
        display_name=display_name or username,
        auth_mode=mode,
        roles=effective_roles(username, settings.role_allowlist, dev_role, subject=subject),
    )


def read_principal(request: Request) -> Principal | None:
    """The signed-in user for this request (decoded once, then cached on request.state)."""
    if hasattr(request.state, "principal"):
        return request.state.principal
    settings: WebSettings = request.app.state.settings
    serializer: SignedSerializer = request.app.state.session_serializer
    payload = serializer.loads(request.cookies.get(SESSION_COOKIE), settings.session_max_age_s)
    principal = principal_from_payload(payload, settings)
    request.state.principal = principal
    return principal


def rate_limit_user_key(request: Request) -> str | None:
    """The per-user rate-limit key: the owner id of the signed-in user, or None when signed out."""
    principal = read_principal(request)
    if principal is None:
        return None
    return principal.owner_id


def require_role(*roles: str) -> Callable[[Request], Principal]:
    """A FastAPI dependency: 401 when signed out, 403 when the user holds none of roles (no roles = any user).

    SeniorLender = Annotated[Principal, Depends(require_role("lender_senior"))]

    @router.post("/api/lender/files/{file_id}/decision")
    def decide(file_id: str, principal: SeniorLender) -> ...
    """
    unknown = [role for role in roles if role not in ROLES]
    if unknown:
        raise ValueError(f"unknown roles {unknown}; known roles are {list(ROLES)}")

    def dependency(request: Request) -> Principal:
        """Checks the session cookie and the roles for this request."""
        principal = read_principal(request)
        if principal is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="sign in required")
        if roles and not principal.has_any_role(roles):
            detail = f"requires one of the roles {list(roles)}"
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
        return principal

    return dependency


def safe_next_path(value: str | None, default: str = "/") -> str:
    """A local path to return to after sign-in. Anything that could leave this site falls back to default.

    Accepted: "/studio", "/lender/file?id=3". Refused: "https://evil.example", "//evil.example", "/\\evil".
    """
    if not value or len(value) > MAX_NEXT_PATH_CHARS:
        return default
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return default
    return value


def new_pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) for PKCE with S256 (RFC 7636)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class OAuthError(Exception):
    """The provider refused a request or answered with something unusable."""


@dataclass(frozen=True)
class OidcEndpoints:
    """The three provider endpoints the flow uses, from OpenID discovery."""

    authorization: str
    token: str
    userinfo: str


@dataclass(frozen=True)
class HfIdentity:
    """The profile fields kept from userinfo."""

    subject: str
    username: str
    display_name: str


class HfOAuthClient:
    """Talks to the Hugging Face OpenID provider: discovery, code exchange and userinfo."""

    def __init__(
        self, settings: OAuthSettings, transport: httpx.BaseTransport | None = None, timeout_s: float = 10.0
    ) -> None:
        """transport: an httpx transport for tests (httpx.MockTransport); None uses the network."""
        self.settings = settings
        self._transport = transport
        self._timeout_s = timeout_s
        self._endpoints: OidcEndpoints | None = None

    def _request_json(self, method: str, url: str, what: str, **kwargs: Any) -> dict[str, Any]:
        """Send one request and return its JSON object. Network failures, HTTP errors and non-JSON answers all
        become OAuthError, so callers handle a single exception type."""
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout_s) as client:
                response = client.request(method, url, **kwargs)
        except httpx.HTTPError as error:
            raise OAuthError(f"{what} failed: {type(error).__name__}") from error
        if response.status_code != httpx.codes.OK:
            raise OAuthError(f"{what} failed with HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as error:
            raise OAuthError(f"{what} did not return JSON") from error
        if not isinstance(data, dict):
            raise OAuthError(f"{what} did not return a JSON object")
        return data

    def endpoints(self) -> OidcEndpoints:
        """The provider endpoints, read once from {OPENID_PROVIDER_URL}/.well-known/openid-configuration."""
        if self._endpoints is None:
            url = f"{self.settings.provider_url}/.well-known/openid-configuration"
            data = self._request_json("GET", url, "OpenID discovery")
            values = [data.get(name) for name in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint")]
            if not all(isinstance(value, str) and is_http_url(value) for value in values):
                raise OAuthError("OpenID discovery is missing the authorization, token or userinfo endpoint")
            self._endpoints = OidcEndpoints(authorization=values[0], token=values[1], userinfo=values[2])
        return self._endpoints

    def authorization_url(self, state: str, code_challenge: str) -> str:
        """Where to send the browser to sign in."""
        query = {
            "response_type": "code",
            "client_id": self.settings.client_id,
            "redirect_uri": self.settings.redirect_uri,
            "scope": self.settings.scopes,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.endpoints().authorization}?{urlencode(query)}"

    def exchange_code(self, code: str, code_verifier: str) -> str:
        """Trade the authorization code for an access token (client secret sent with HTTP Basic auth)."""
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.redirect_uri,
            "client_id": self.settings.client_id,
            "code_verifier": code_verifier,
        }
        auth = (self.settings.client_id, self.settings.client_secret.get_secret_value())
        data = self._request_json("POST", self.endpoints().token, "token exchange", data=form, auth=auth)
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise OAuthError("token exchange returned no access_token")
        return token

    def fetch_identity(self, access_token: str) -> HfIdentity:
        """The user's subject id, username and display name from the userinfo endpoint."""
        headers = {"Authorization": f"Bearer {access_token}"}
        data = self._request_json("GET", self.endpoints().userinfo, "userinfo", headers=headers)
        subject, username = data.get("sub"), data.get("preferred_username")
        if not isinstance(subject, str) or not subject:
            raise OAuthError("userinfo has no subject")
        if not isinstance(username, str) or not USERNAME_PATTERN.match(username):
            raise OAuthError("userinfo has no usable preferred_username")
        name = data.get("name")
        display_name = name.strip() if isinstance(name, str) and name.strip() else username
        return HfIdentity(subject=subject, username=username, display_name=display_name[:DISPLAY_NAME_MAX_CHARS])

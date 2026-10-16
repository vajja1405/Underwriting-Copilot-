"""Tests for sign-in: /api/me, the dev login, roles, require_role, cookies and the Hugging Face OAuth flow."""

import base64
import hashlib
import json
from typing import Annotated
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from web_helpers import csrf_headers, dev_login

from uwcopilot.web.app import create_app
from uwcopilot.web.auth import Principal, effective_roles, require_role, safe_next_path
from uwcopilot.web.cookies import CookiePolicy, SignedSerializer, build_set_cookie

SPACE_ENV = {
    "SPACE_ID": "astra6/fundproof",
    "SPACE_HOST": "astra6-fundproof.hf.space",
    "OAUTH_CLIENT_ID": "test-client",
    "OAUTH_CLIENT_SECRET": "test-client-secret",
    "OPENID_PROVIDER_URL": "https://huggingface.co",
}


# The way feature routers declare who may call them.
SignedInUser = Annotated[Principal, Depends(require_role())]
LenderReviewer = Annotated[Principal, Depends(require_role("lender_reviewer"))]
Admin = Annotated[Principal, Depends(require_role("admin"))]


def role_probe_router() -> APIRouter:
    """Endpoints guarded by require_role, for the role tests."""
    router = APIRouter(prefix="/api/probe")

    @router.get("/any")
    def any_user(principal: SignedInUser) -> dict[str, str]:
        return {"owner": principal.owner_id}

    @router.get("/reviewer")
    def reviewer(principal: LenderReviewer) -> dict[str, str]:
        return {"user": principal.username}

    @router.get("/admin")
    def admin(principal: Admin) -> dict[str, str]:
        return {"user": principal.username}

    return router


# ---- /api/me and the dev login ----


def test_me_signed_out(client: TestClient) -> None:
    assert client.get("/api/me").json() == {
        "signed_in": False,
        "username": None,
        "display_name": None,
        "subject": None,
        "roles": [],
        "auth_mode": "none",
    }


def test_csrf_token_is_stable_for_the_browser_session(client: TestClient) -> None:
    first = client.get("/api/auth/csrf")
    token = first.json()["token"]
    assert len(token) == 43 and first.headers["cache-control"] == "no-store"
    assert "HttpOnly" not in first.headers["set-cookie"]  # the frontend may read it
    assert client.get("/api/auth/csrf").json()["token"] == token


def test_dev_login_signs_in_with_the_chosen_role(dev_client: TestClient) -> None:
    body = dev_login(dev_client, "demo_lender", "lender_senior")
    assert body["signed_in"] and body["auth_mode"] == "dev"
    assert body["roles"] == ["founder", "lender_reviewer", "lender_senior"]  # senior implies reviewer
    assert dev_client.get("/api/me").json() == body


def test_dev_login_needs_csrf(dev_client: TestClient) -> None:
    response = dev_client.post("/api/auth/dev-login", json={"username": "demo", "role": "founder"})
    assert response.status_code == 403 and "CSRF" in response.json()["detail"]


def test_dev_login_is_refused_when_off(client: TestClient) -> None:
    response = client.post(
        "/api/auth/dev-login", json={"username": "demo", "role": "admin"}, headers=csrf_headers(client)
    )
    assert response.status_code == 403
    assert client.get("/api/me").json()["signed_in"] is False


def test_dev_login_endpoint_refuses_inside_a_space_even_if_settings_were_forced(make_settings) -> None:
    # load_web_settings already refuses UWC_DEV_AUTH=1 with SPACE_ID; the endpoint checks again (defense in depth).
    forced = make_settings(UWC_DEV_AUTH="1").model_copy(update={"is_space": True})
    with TestClient(create_app(forced), base_url="https://testserver") as test_client:
        body = {"username": "demo", "role": "admin"}
        response = test_client.post("/api/auth/dev-login", json=body, headers=csrf_headers(test_client))
        assert response.status_code == 403


@pytest.mark.parametrize(
    "body", [{"username": "demo", "role": "superuser"}, {"username": "../evil", "role": "founder"}, {"role": "founder"}]
)
def test_dev_login_validates_the_body(dev_client: TestClient, body: dict[str, str]) -> None:
    response = dev_client.post("/api/auth/dev-login", json=body, headers=csrf_headers(dev_client))
    assert response.status_code == 422


def test_dev_session_ends_when_the_dev_login_is_turned_off(make_settings) -> None:
    with TestClient(create_app(make_settings(UWC_DEV_AUTH="1"))) as dev_app_client:
        dev_login(dev_app_client, "demo", "admin")
        session_cookie = dev_app_client.cookies["uwc_session"]
    # Same secret, dev login now off: the signed dev cookie must no longer count.
    with TestClient(create_app(make_settings())) as plain_client:
        plain_client.cookies.set("uwc_session", session_cookie)
        assert plain_client.get("/api/me").json()["signed_in"] is False


def test_tampered_session_cookie_is_ignored(dev_client: TestClient) -> None:
    dev_login(dev_client, "demo", "founder")
    cookie = dev_client.cookies["uwc_session"]
    body, signature = cookie.split(".")
    forged = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    forged["r"] = "admin"
    forged_body = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode().rstrip("=")
    dev_client.cookies.set("uwc_session", f"{forged_body}.{signature}")
    assert dev_client.get("/api/me").json()["signed_in"] is False


def test_logout_needs_csrf_and_clears_the_session(dev_client: TestClient) -> None:
    dev_login(dev_client)
    assert dev_client.post("/api/auth/logout").status_code == 403
    response = dev_client.post("/api/auth/logout", headers=csrf_headers(dev_client))
    assert response.status_code == 200 and response.json() == {"signed_in": False}
    assert "uwc_session=;" in response.headers["set-cookie"] and "Max-Age=0" in response.headers["set-cookie"]
    assert dev_client.get("/api/me").json()["signed_in"] is False


# ---- roles ----


def test_effective_roles() -> None:
    allowlist = {"alice": ("lender_senior",), "bob": ("admin",)}
    assert effective_roles("Alice", allowlist) == ("founder", "lender_reviewer", "lender_senior")
    assert effective_roles("bob", allowlist) == ("founder", "admin")  # admin is not a credit role
    assert effective_roles("carol", allowlist) == ("founder",)
    assert effective_roles("carol", allowlist, extra_role="lender_reviewer") == ("founder", "lender_reviewer")


def test_effective_roles_with_entries_pinned_to_an_account_id() -> None:
    allowlist = {"alice@id-1": ("lender_senior",), "bob": ("admin",)}
    assert effective_roles("Alice", allowlist, subject="id-1") == ("founder", "lender_reviewer", "lender_senior")
    assert effective_roles("alice", allowlist, subject="id-2") == ("founder",)  # someone else now named alice
    assert effective_roles("alice", allowlist) == ("founder",)  # no subject known: a pinned entry never matches
    assert effective_roles("bob", allowlist, subject="any") == ("founder", "admin")  # unpinned entries still work


def test_require_role_rejects_unknown_roles() -> None:
    with pytest.raises(ValueError, match="unknown roles"):
        require_role("superuser")


def test_require_role_on_routes(make_settings) -> None:
    settings = make_settings(UWC_DEV_AUTH="1", UWC_ROLE_ALLOWLIST="boss:admin")
    with TestClient(create_app(settings, extra_routers=[role_probe_router()])) as test_client:
        assert test_client.get("/api/probe/any").status_code == 401
        dev_login(test_client, "founder1", "founder")
        assert test_client.get("/api/probe/any").json() == {"owner": "dev:founder1"}
        assert test_client.get("/api/probe/reviewer").status_code == 403
        dev_login(test_client, "senior1", "lender_senior")
        assert test_client.get("/api/probe/reviewer").json() == {"user": "senior1"}
        assert test_client.get("/api/probe/admin").status_code == 403
        dev_login(test_client, "boss", "founder")  # admin comes from the allowlist, not from the dev login
        assert test_client.get("/api/probe/admin").json() == {"user": "boss"}


# ---- helpers ----


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/studio", "/studio"),
        ("/lender/file?id=3", "/lender/file?id=3"),
        (None, "/"),
        ("https://evil.example/", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("studio", "/"),
        ("/a\nb", "/"),
    ],
)
def test_safe_next_path(value: str | None, expected: str) -> None:
    assert safe_next_path(value) == expected


def test_signed_serializer_checks_signature_salt_and_age() -> None:
    now = [1_000_000.0]
    signer = SignedSerializer("secret-" * 6, "salt-a", clock=lambda: now[0])
    token = signer.dumps({"u": "alice"})
    assert signer.loads(token, max_age_s=60)["u"] == "alice"
    assert SignedSerializer("secret-" * 6, "salt-b").loads(token, max_age_s=60) is None
    assert SignedSerializer("other-" * 6, "salt-a", clock=lambda: now[0]).loads(token, max_age_s=60) is None
    assert signer.loads(token[:-2] + "xx", max_age_s=60) is None
    assert signer.loads("garbage", max_age_s=60) is None
    now[0] += 61
    assert signer.loads(token, max_age_s=60) is None


def test_cookie_attributes_per_environment() -> None:
    space = build_set_cookie("uwc_session", "abc", CookiePolicy.for_environment(is_space=True), max_age=10)
    assert space == "uwc_session=abc; Path=/; Max-Age=10; Secure; HttpOnly; SameSite=None; Partitioned"
    local = build_set_cookie("uwc_csrf", "abc", CookiePolicy.for_environment(is_space=False), httponly=False)
    assert local == "uwc_csrf=abc; Path=/; SameSite=Lax"
    with pytest.raises(ValueError):
        build_set_cookie("x", "a;b", CookiePolicy.for_environment(is_space=False))


# ---- Hugging Face OAuth (OpenID Connect + PKCE), with a mock provider ----


class FakeProvider:
    """A mock OpenID provider: discovery, token and userinfo endpoints; records what it receives."""

    def __init__(self, token_status: int = 200) -> None:
        self.token_status = token_status
        self.token_requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://huggingface.co",
                    "authorization_endpoint": "https://huggingface.co/oauth/authorize",
                    "token_endpoint": "https://huggingface.co/oauth/token",
                    "userinfo_endpoint": "https://huggingface.co/oauth/userinfo",
                },
            )
        if path == "/oauth/token":
            self.token_requests.append(request)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "hf_oauth_access", "token_type": "bearer"})
        if path == "/oauth/userinfo":
            assert request.headers["authorization"] == "Bearer hf_oauth_access"
            return httpx.Response(200, json={"sub": "6123abc", "preferred_username": "astra6", "name": "Rahul V"})
        return httpx.Response(404)


def space_client(make_settings, provider: FakeProvider, **env: str) -> TestClient:
    """A client for an app in Space mode with OAuth configured (https base URL, so Secure cookies are sent)."""
    settings = make_settings(**SPACE_ENV, **env)
    app = create_app(settings, oauth_transport=httpx.MockTransport(provider.handler))
    return TestClient(app, base_url="https://astra6-fundproof.hf.space", follow_redirects=False)


def start_login(test_client: TestClient, next_path: str = "/lender") -> dict[str, list[str]]:
    """GET /api/auth/login and return the query of the provider URL it redirects to."""
    response = test_client.get("/api/auth/login", params={"next": next_path})
    assert response.status_code == 302
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == "https://huggingface.co/oauth/authorize"
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith("uwc_oauth=") and "Partitioned" in set_cookie and "SameSite=None" in set_cookie
    return parse_qs(target.query)


def test_oauth_full_flow(make_settings) -> None:
    provider = FakeProvider()
    with space_client(make_settings, provider, UWC_ROLE_ALLOWLIST="astra6:admin") as test_client:
        query = start_login(test_client)
        assert query["client_id"] == ["test-client"] and query["scope"] == ["openid profile"]
        assert query["redirect_uri"] == ["https://astra6-fundproof.hf.space/api/auth/callback"]
        assert query["code_challenge_method"] == ["S256"]

        response = test_client.get("/api/auth/callback", params={"code": "the-code", "state": query["state"][0]})
        assert response.status_code == 302 and response.headers["location"] == "/lender"
        cookies = response.headers.get_list("set-cookie")
        assert any(c.startswith("uwc_session=") and "Partitioned" in c and "Secure" in c for c in cookies)

        me = test_client.get("/api/me").json()
        assert me == {
            "signed_in": True,
            "username": "astra6",
            "display_name": "Rahul V",
            "subject": "6123abc",
            "roles": ["founder", "admin"],
            "auth_mode": "hf_oauth",
        }

    token_request = provider.token_requests[0]
    form = parse_qs(token_request.content.decode())
    verifier = form["code_verifier"][0]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == query["code_challenge"][0]  # PKCE: the verifier matches the challenge sent at login
    expected_basic = base64.b64encode(b"test-client:test-client-secret").decode()
    assert token_request.headers["authorization"] == f"Basic {expected_basic}"


@pytest.mark.parametrize(
    ("allowlist", "expected_roles"),
    [
        ("astra6@6123abc:admin", ["founder", "admin"]),  # pinned to this account id
        ("astra6@0ther1d:admin", ["founder"]),  # same username, other account: a renamed name was re-registered
    ],
)
def test_oauth_roles_from_an_entry_pinned_to_the_account_id(
    make_settings, allowlist: str, expected_roles: list[str]
) -> None:
    with space_client(make_settings, FakeProvider(), UWC_ROLE_ALLOWLIST=allowlist) as test_client:
        query = start_login(test_client)
        test_client.get("/api/auth/callback", params={"code": "the-code", "state": query["state"][0]})
        assert test_client.get("/api/me").json()["roles"] == expected_roles


def test_oauth_state_mismatch_is_refused(make_settings) -> None:
    with space_client(make_settings, FakeProvider()) as test_client:
        start_login(test_client)
        response = test_client.get("/api/auth/callback", params={"code": "c", "state": "forged"})
        assert response.headers["location"] == "/signin?error=state_mismatch"
        assert test_client.get("/api/me").json()["signed_in"] is False


def test_oauth_callback_without_the_state_cookie_is_refused(make_settings) -> None:
    with space_client(make_settings, FakeProvider()) as test_client:
        query = start_login(test_client)
        test_client.cookies.clear()  # what a browser that blocks the iframe cookie looks like
        response = test_client.get("/api/auth/callback", params={"code": "c", "state": query["state"][0]})
        assert response.headers["location"] == "/signin?error=state_mismatch"


def test_oauth_cancel_and_provider_errors(make_settings) -> None:
    with space_client(make_settings, FakeProvider(token_status=400)) as test_client:
        response = test_client.get("/api/auth/callback", params={"error": "access_denied"})
        assert response.headers["location"] == "/signin?error=access_denied"
        query = start_login(test_client)
        response = test_client.get("/api/auth/callback", params={"code": "c", "state": query["state"][0]})
        assert response.headers["location"] == "/signin?error=provider_error"


def test_oauth_provider_unreachable_is_a_provider_error(make_settings) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    settings = make_settings(**SPACE_ENV)
    app = create_app(settings, oauth_transport=httpx.MockTransport(unreachable))
    with TestClient(app, base_url="https://astra6-fundproof.hf.space", follow_redirects=False) as test_client:
        response = test_client.get("/api/auth/login")
        assert response.status_code == 302 and response.headers["location"] == "/signin?error=provider_error"


def test_oauth_never_redirects_off_site(make_settings) -> None:
    with space_client(make_settings, FakeProvider()) as test_client:
        query = start_login(test_client, next_path="https://evil.example/steal")
        response = test_client.get("/api/auth/callback", params={"code": "c", "state": query["state"][0]})
        assert response.headers["location"] == "/"


def test_login_without_oauth_configured(client: TestClient) -> None:
    response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/signin?error=oauth_not_configured"

"""Helpers shared by the web tests (imported as `web_helpers`; pytest puts tests/web on sys.path)."""

from typing import Any

from fastapi.testclient import TestClient


def csrf_headers(client: TestClient) -> dict[str, str]:
    """Fetch a CSRF token (sets the cookie on the client) and return the header that echoes it."""
    token = client.get("/api/auth/csrf").json()["token"]
    return {"X-CSRF-Token": token}


def dev_login(client: TestClient, username: str = "demo_user", role: str = "founder") -> dict[str, Any]:
    """Sign in through the dev login and return the /api/me body it answers with."""
    body = {"username": username, "role": role}
    response = client.post("/api/auth/dev-login", json=body, headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    return response.json()

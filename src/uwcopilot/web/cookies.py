"""Cookies: the attributes they need inside a Hugging Face Space iframe, and HMAC-signed cookie values.

Why the attributes differ by environment (details in docs/web/auth.md):
- Inside a Space the app runs in an iframe on huggingface.co, so its cookies are third-party cookies. Browsers
  send them only with `Secure; SameSite=None`, and browsers that block third-party cookies still accept them when
  they are `Partitioned` (CHIPS: the cookie is stored per top-level site). So in a Space every cookie is
  `Secure; SameSite=None; Partitioned`.
- In local development the app runs on http://localhost, where `Secure` cookies would be dropped, so cookies are
  `SameSite=Lax` without `Secure` or `Partitioned`.

Starlette's Response.set_cookie only writes `Partitioned` on Python 3.14+, and this project runs Python 3.11, so
build_set_cookie() writes the header text itself.

SignedSerializer turns a small JSON dict into "<base64 payload>.<base64 HMAC-SHA256>" and back. The value is
signed, not encrypted: never put a secret in it. Each cookie type uses its own salt, so a value signed for one
cookie (for example the OAuth state) is rejected when presented as another (the session).
"""

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from starlette.responses import Response

SESSION_COOKIE = "uwc_session"
CSRF_COOKIE = "uwc_csrf"
OAUTH_STATE_COOKIE = "uwc_oauth"
# Cookie values we write are URL-safe base64 plus '.', so they never need quoting.
SAFE_COOKIE_VALUE = re.compile(r"^[A-Za-z0-9_.=-]*$")
# A value "issued in the future" by more than this is rejected (the clock went backwards a lot, or a forgery).
MAX_CLOCK_SKEW_S = 60


@dataclass(frozen=True)
class CookiePolicy:
    """The Secure / SameSite / Partitioned attributes used for every cookie the app sets."""

    secure: bool
    samesite: Literal["lax", "strict", "none"]
    partitioned: bool

    @classmethod
    def for_environment(cls, is_space: bool) -> "CookiePolicy":
        """Space (HTTPS, inside a cross-site iframe) or local development (plain HTTP on localhost)."""
        if is_space:
            return cls(secure=True, samesite="none", partitioned=True)
        return cls(secure=False, samesite="lax", partitioned=False)


def build_set_cookie(
    name: str, value: str, policy: CookiePolicy, *, max_age: int | None = None, httponly: bool = True
) -> str:
    """The text of one Set-Cookie header (Path=/, never a Domain, so the cookie stays on this host)."""
    if not SAFE_COOKIE_VALUE.match(value):
        raise ValueError(f"cookie {name} has characters that would need quoting")
    parts = [f"{name}={value}", "Path=/"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if policy.secure:
        parts.append("Secure")
    if httponly:
        parts.append("HttpOnly")
    parts.append(f"SameSite={policy.samesite.capitalize()}")
    if policy.partitioned:
        parts.append("Partitioned")
    return "; ".join(parts)


def set_cookie(
    response: Response,
    name: str,
    value: str,
    policy: CookiePolicy,
    *,
    max_age: int | None = None,
    httponly: bool = True,
) -> None:
    """Add a Set-Cookie header to response (several cookies can be set on one response)."""
    response.headers.append("set-cookie", build_set_cookie(name, value, policy, max_age=max_age, httponly=httponly))


def clear_cookie(response: Response, name: str, policy: CookiePolicy) -> None:
    """Expire a cookie. A partitioned cookie can only be removed by a Set-Cookie that is also Partitioned."""
    set_cookie(response, name, "", policy, max_age=0)


def b64encode(data: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64decode(text: str) -> bytes:
    """Inverse of b64encode (adds the padding back)."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SignedSerializer:
    """Signs small JSON payloads with HMAC-SHA256 and checks signature and age when reading them back."""

    def __init__(self, secret: str, salt: str, clock: Callable[[], float] = time.time) -> None:
        """secret: the session secret; salt: a different fixed string per cookie type."""
        self._key = hashlib.sha256(f"{salt}|{secret}".encode()).digest()
        self._clock = clock

    def _signature(self, body: str) -> str:
        """The HMAC of the encoded payload."""
        return b64encode(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())

    def dumps(self, payload: dict[str, Any]) -> str:
        """Signed text for payload; the issue time is added as "iat"."""
        data = dict(payload, iat=int(self._clock()))
        body = b64encode(json.dumps(data, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{self._signature(body)}"

    def loads(self, token: str | None, max_age_s: int) -> dict[str, Any] | None:
        """The payload if the signature is valid and it is at most max_age_s old; otherwise None."""
        if not token or token.count(".") != 1:
            return None
        body, signature = token.split(".")
        if not hmac.compare_digest(signature, self._signature(body)):
            return None
        try:
            data = json.loads(b64decode(body))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("iat"), int):
            return None
        age = self._clock() - data["iat"]
        if age < -MAX_CLOCK_SKEW_S or age > max_age_s:
            return None
        return data

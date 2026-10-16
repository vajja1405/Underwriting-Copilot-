"""Request-level protections, written as small ASGI middlewares so every route gets them.

Order (outermost first, set in app.py): access log -> security headers -> (dev-only CORS) -> rate limit ->
body size limit -> CSRF -> the routes. So a 429, 413 or 403 still gets the security headers and a log line.

- SecurityHeadersMiddleware: CSP and the other headers on every response. A route may set its own CSP (the static
  site sends one per HTML page with the hashes of that page's inline scripts; /api/docs allows its CDN); the
  middleware never overwrites it.
- RateLimitMiddleware: token buckets per client IP and per signed-in user on /api (except /api/health). The
  buckets live in this process's memory: with several worker processes each one counts separately, and a
  restart forgets them. That is enough for a single-container prototype.
- BodySizeLimitMiddleware: refuses bodies over max_body_bytes with 413, whether or not Content-Length is sent.
  The JSON API (/api/*) has a much smaller cap (API_MAX_BODY_BYTES, 64 KiB): its largest real body (a plan with a
  2,000-character idea and three addresses) is a few KiB, and a big body is expensive to validate and to answer
  (a 9 MB body of list items froze the event loop for over 30 s before this cap). The global limit stays for
  future upload routes, which must then be given their own prefix limit here.
- CsrfMiddleware: double-submit token. GET /api/auth/csrf sets cookie `uwc_csrf` and returns the same value; every
  POST/PUT/PATCH/DELETE under /api must echo it in the `X-CSRF-Token` header. A cross-site page cannot read our
  cookie and cannot send a custom header without a CORS preflight, which this app never approves.
"""

import base64
import hashlib
import hmac
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from uwcopilot.web.cookies import CSRF_COOKIE

CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
RATE_LIMIT_EXEMPT_PATHS = frozenset({"/api/health"})
# Largest JSON body the /api routes accept (a design choice: about 10x the largest real request).
API_MAX_BODY_BYTES = 64 * 1024
FRAME_ANCESTORS = "'self' https://huggingface.co"
# Turn off powerful browser features the app does not use. Revisit when a feature needs one (for example voice).
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


def build_csp(
    script_hashes: Iterable[str] = (),
    extra_script_src: Iterable[str] = (),
    extra_style_src: Iterable[str] = (),
    extra_img_src: Iterable[str] = (),
) -> str:
    """The Content-Security-Policy text.

    script_hashes are "sha256-..." values of inline scripts that may run; nothing else inline may run.
    style-src allows inline styles because Tailwind and Recharts set style attributes.
    worker-src allows blob: because MapLibre starts its workers from blob URLs.
    form-action allows huggingface.co because a sign-in form may redirect there.
    """
    script_src = ["'self'", *extra_script_src, *(f"'{value}'" for value in script_hashes)]
    directives = [
        "default-src 'self'",
        "script-src " + " ".join(script_src),
        "style-src " + " ".join(["'self'", "'unsafe-inline'", *extra_style_src]),
        "img-src " + " ".join(["'self'", "data:", "blob:", *extra_img_src]),
        "font-src 'self'",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self' https://huggingface.co",
        f"frame-ancestors {FRAME_ANCESTORS}",
    ]
    return "; ".join(directives)


DEFAULT_CSP = build_csp()


class InlineScriptCollector(HTMLParser):
    """Collects the text of every <script> element without a src attribute."""

    def __init__(self) -> None:
        """Start with no scripts."""
        super().__init__(convert_charrefs=False)
        self.scripts: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Start collecting at an inline <script>."""
        if tag == "script" and not any(name == "src" for name, _ in attrs):
            self._current = []

    def handle_data(self, data: str) -> None:
        """Script text may arrive in several pieces."""
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        """Finish the current inline script."""
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def inline_script_hashes(html: str) -> list[str]:
    """The CSP hash source ("sha256-<base64>") of each distinct inline script in an HTML document, in order.

    Browsers hash the exact text between <script> and </script>, so the text is hashed as found.
    """
    collector = InlineScriptCollector()
    collector.feed(html)
    collector.close()
    hashes: list[str] = []
    for script in collector.scripts:
        digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
        value = f"sha256-{digest}"
        if value not in hashes:
            hashes.append(value)
    return hashes


def json_error(status_code: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    """The error shape FastAPI uses ({"detail": ...}), for responses sent by middleware."""
    return JSONResponse({"detail": detail}, status_code=status_code, headers=headers)


class SecurityHeadersMiddleware:
    """Adds CSP, nosniff, Referrer-Policy and Permissions-Policy to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap app."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Add the headers when the response starts; keep a CSP the route already set."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            """Edit the header list of the response start message."""
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                defaults = {
                    b"content-security-policy": DEFAULT_CSP.encode(),
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"strict-origin-when-cross-origin",
                    b"permissions-policy": PERMISSIONS_POLICY.encode(),
                }
                for name, value in defaults.items():
                    if name not in present:
                        headers.append((name, value))
                message = dict(message, headers=headers)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    """Refuses request bodies larger than max_bytes with 413 before the route sees them.

    A declared Content-Length over the limit is refused at once. Otherwise the body is read here (at most
    max_bytes plus one chunk) and replayed to the route, so a chunked upload without Content-Length cannot get past.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, prefix_limits: Iterable[tuple[str, int]] = ()) -> None:
        """Wrap app with a limit in bytes. prefix_limits: (path prefix, smaller limit) pairs; the longest matching
        prefix applies, and a prefix limit never raises the global one."""
        self.app = app
        self.max_bytes = max_bytes
        self.prefix_limits = sorted(prefix_limits, key=lambda item: len(item[0]), reverse=True)

    def limit_for(self, path: str) -> int:
        """The body limit of one request path."""
        for prefix, limit in self.prefix_limits:
            if path.startswith(prefix):
                return min(limit, self.max_bytes)
        return self.max_bytes

    @staticmethod
    def _too_large(limit: int) -> JSONResponse:
        """The 413 response."""
        return json_error(413, f"request body is larger than {limit} bytes")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check the declared length, then the real length, then call the app with the buffered body."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope.get("path", ""))
        declared = Request(scope).headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._too_large(limit)(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        more_body = scope["method"] not in ("GET", "HEAD", "OPTIONS")
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await self._too_large(limit)(scope, receive, send)
                return
            chunks.append(chunk)
            more_body = message.get("more_body", False)

        body = b"".join(chunks)
        body_sent = False

        async def replay() -> Message:
            """Hand the buffered body to the app once, then pass through (for disconnect messages)."""
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


@dataclass
class TokenBucket:
    """Holds up to capacity tokens and refills at refill_per_s tokens per second; each request takes one."""

    capacity: float
    refill_per_s: float
    tokens: float
    updated_at: float

    def refill(self, now: float) -> None:
        """Add the tokens earned since the last update."""
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_s)
        self.updated_at = now

    def take(self, now: float) -> float:
        """Take one token. Returns 0.0 on success, else the seconds until a token is available."""
        self.refill(now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        return (1.0 - self.tokens) / self.refill_per_s


class RateLimiter:
    """Token buckets by key ("ip:..." or "user:..."), kept in memory; idle full buckets are dropped."""

    def __init__(
        self, per_minute: int, burst: int, clock: Callable[[], float] = time.monotonic, max_keys: int = 10_000
    ) -> None:
        """per_minute: steady rate; burst: how many requests may arrive at once."""
        self.capacity = float(burst)
        self.refill_per_s = per_minute / 60.0
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: dict[str, TokenBucket] = {}

    def check(self, key: str) -> float:
        """Take a token for key. Returns 0.0 when allowed, else the seconds to wait."""
        now = self.clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            if len(self._buckets) >= self.max_keys:
                self._prune(now)
            bucket = TokenBucket(self.capacity, self.refill_per_s, self.capacity, now)
            self._buckets[key] = bucket
        return bucket.take(now)

    def _prune(self, now: float) -> None:
        """Forget buckets that have refilled completely; if still too many, forget the least recently used half."""
        for key, bucket in list(self._buckets.items()):
            bucket.refill(now)
            if bucket.tokens >= bucket.capacity:
                del self._buckets[key]
        if len(self._buckets) >= self.max_keys:
            oldest_first = sorted(self._buckets, key=lambda name: self._buckets[name].updated_at)
            for key in oldest_first[: len(oldest_first) // 2]:
                del self._buckets[key]

    def __len__(self) -> int:
        """How many buckets are kept right now."""
        return len(self._buckets)


def client_ip(scope: Scope, trusted_proxy_hops: int) -> str:
    """The caller's IP address.

    With trusted_proxy_hops = N > 0 the app sits behind N proxies that each APPEND to X-Forwarded-For, so the
    N-th entry from the right is the address the first trusted proxy saw. Entries further left are written by the
    client and cannot be trusted. With N = 0 (local development) the socket peer address is used.

    A request may carry several X-Forwarded-For header lines (a proxy may add its own line instead of extending the
    client's). They are read in order and joined, so a client-written first line cannot hide the proxy's entry.
    """
    if trusted_proxy_hops > 0:
        forwarded = ", ".join(Request(scope).headers.getlist("x-forwarded-for"))
        entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
        if len(entries) >= trusted_proxy_hops:
            return entries[-trusted_proxy_hops]
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    """429 with Retry-After when a client IP or a signed-in user exceeds its token bucket on /api."""

    def __init__(
        self,
        app: ASGIApp,
        ip_limiter: RateLimiter,
        user_limiter: RateLimiter,
        user_key: Callable[[Request], str | None],
        trusted_proxy_hops: int = 0,
    ) -> None:
        """user_key returns a stable id for the signed-in user of a request, or None."""
        self.app = app
        self.ip_limiter = ip_limiter
        self.user_limiter = user_limiter
        self.user_key = user_key
        self.trusted_proxy_hops = trusted_proxy_hops

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check the IP bucket, then the user bucket; refuse when either is empty."""
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path in RATE_LIMIT_EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        wait_s = self.ip_limiter.check("ip:" + client_ip(scope, self.trusted_proxy_hops))
        if wait_s == 0.0:
            user = self.user_key(Request(scope))
            if user is not None:
                wait_s = self.user_limiter.check("user:" + user)
        if wait_s > 0.0:
            retry_after = str(max(1, math.ceil(wait_s)))
            response = json_error(429, "rate limit exceeded; try again later", {"Retry-After": retry_after})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class CsrfMiddleware:
    """403 for a state-changing /api request whose X-CSRF-Token header does not match the uwc_csrf cookie."""

    def __init__(self, app: ASGIApp, exempt_paths: Iterable[str] = ()) -> None:
        """exempt_paths: exact paths that skip the check (none today)."""
        self.app = app
        self.exempt_paths = frozenset(exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Compare header and cookie in constant time."""
        if (
            scope["type"] != "http"
            or scope["method"] not in UNSAFE_METHODS
            or not scope.get("path", "").startswith("/api/")
            or scope.get("path") in self.exempt_paths
        ):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        header_token = request.headers.get(CSRF_HEADER, "")
        if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
            detail = "CSRF token missing or invalid: GET /api/auth/csrf, then send it in the X-CSRF-Token header"
            await json_error(403, detail)(scope, receive, send)
            return
        await self.app(scope, receive, send)

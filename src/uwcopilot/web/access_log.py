"""One log line per request, with personal data masked.

The line holds the method, the path, the NAMES of the query parameters (their values are replaced by "*"), the
status code and the time taken. It never holds cookies, headers, bodies, query values, IP addresses or usernames.
The path goes through the same PiiRedactor that cleans traces (uwcopilot.observability.redaction), so an e-mail
address, phone number or SSN-shaped number in a URL path is masked before it reaches the log.

The path is used as the server already decoded it (uvicorn percent-decodes it once); it is NOT decoded a second
time. Every character that is not printable (CR, LF, tab, other control characters, Unicode line separators) is
written as an escape such as \\n or \\x1b, so a request can never start a new, forged log line.
"""

import logging
import time
from urllib.parse import parse_qsl

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from uwcopilot.observability.redaction import PiiRedactor

logger = logging.getLogger("uwcopilot.web.access")
MAX_LOGGED_PATH_CHARS = 300


def escape_unprintable(text: str) -> str:
    """text with every non-printable character written as a Python escape ("\\n", "\\x1b", "\\u2028")."""
    pieces = []
    for char in text:
        if char.isprintable():
            pieces.append(char)
        else:
            pieces.append(char.encode("unicode_escape").decode("ascii"))
    return "".join(pieces)


def redacted_target(scope: Scope, redactor: PiiRedactor) -> str:
    """The path with identifiers masked, plus the query parameter names only ("?id=*&q=*"), length capped.

    scope["path"] is already percent-decoded by the server. The raw query string is decoded once by parse_qsl.
    """
    path = escape_unprintable(redactor.redact_text(scope.get("path", "")))
    query = scope.get("query_string", b"").decode("latin-1")
    names = [escape_unprintable(redactor.redact_text(name)) for name, _ in parse_qsl(query, keep_blank_values=True)]
    target = path + ("?" + "&".join(f"{name}=*" for name in names) if names else "")
    return target[:MAX_LOGGED_PATH_CHARS]


class AccessLogMiddleware:
    """Logs "METHOD /path STATUS 12.3ms" at INFO for every HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap app."""
        self.app = app
        self.redactor = PiiRedactor()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Remember the status code when the response starts; log when the app returns."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status_code = 500

        async def capture_status(message: Message) -> None:
            """Read the status from the response start message."""
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            target = redacted_target(scope, self.redactor)
            logger.info("%s %s %d %.1fms", scope["method"], target, status_code, elapsed_ms)


def configure_web_logging() -> None:
    """Make INFO lines of the uwcopilot.web loggers visible (uvicorn only configures its own loggers).

    Adds one stderr handler to the "uwcopilot.web" logger unless it already has one; records still propagate, so
    pytest's caplog sees them too. Run uvicorn with --no-access-log: its own access log would repeat each request
    with the client IP and the unredacted query string.
    """
    web_logger = logging.getLogger("uwcopilot.web")
    web_logger.setLevel(logging.INFO)
    if not web_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        web_logger.addHandler(handler)

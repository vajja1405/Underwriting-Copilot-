"""Serving the Next.js static export (frontend/out) from FastAPI.

Contract with the frontend (frontend/next.config.ts: `output: "export"`, `trailingSlash: true`): a route such as
/studio is exported as out/studio/index.html, / as out/index.html, and links are written as /studio/. A request
is answered with the first file that exists, in this order:

1. the exact file (/_next/static/chunks/app.js, /icon.svg, /studio/index.txt, ...);
2. for a path without an extension: <path>/index.html (trailingSlash: true), then <path>.html (so an export
   built with trailingSlash: false also works); a trailing slash in the URL makes no difference;
3. otherwise out/404.html with status 404 (or a plain 404 if the export has no 404.html).

Paths with '..', '.'-prefixed segments, backslashes or NUL are refused, and the resolved file must stay inside
the export folder (no symlink escape). There is no catch-all to index.html: client routes are real exported
pages, and record pages use query strings (/lender/file?id=...).

Caching: files under /_next/static/ have content hashes in their names, so they are cached for a year
(immutable). Everything else (HTML, the .txt route payloads, icons) is "no-cache": the browser keeps it but asks
again each time and gets 304 Not Modified when the ETag still matches.

CSP: Next's static HTML contains inline bootstrap scripts. Instead of allowing every inline script
('unsafe-inline'), each HTML file is sent with a CSP that lists the SHA-256 hashes of its own inline scripts.
The hashes are computed when a file is first served and cached until the file changes.

If the export folder does not exist, every page answers with a short HTML note that the frontend is not built
(status 200 for /, 404 elsewhere); the API keeps working.
"""

import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from uwcopilot.web.security import build_csp, inline_script_hashes

IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
REVALIDATE_CACHE = "no-cache"
HASHED_ASSET_PREFIX = "_next/static/"
# Explicit types for what a Next export contains, so the answer does not depend on the machine's mime.types.
MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
    ".xml": "application/xml",
    ".pdf": "application/pdf",
}
NOT_BUILT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{product} - frontend not built</title></head>
<body><main><h1>{product}</h1>
<p>The API is running, but the frontend has not been built. Run <code>make web-build</code> (static export to
frontend/out), then reload. API documentation: <a href="/api/docs">/api/docs</a>.</p>
<p>{disclaimer}</p></main></body></html>
"""


def media_type_for(path: Path) -> str:
    """The Content-Type for a file name."""
    known = MIME_TYPES.get(path.suffix.lower())
    if known:
        return known
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def safe_segments(url_path: str) -> list[str] | None:
    """The path split into segments, or None if any segment could reach outside the export folder."""
    segments = [segment for segment in url_path.split("/") if segment]
    for segment in segments:
        if segment.startswith(".") or "\\" in segment or "\x00" in segment:
            return None
    return segments


@dataclass(frozen=True)
class CspCacheEntry:
    """A computed CSP and the file version it belongs to."""

    mtime_ns: int
    size: int
    csp: str


class StaticSite:
    """Finds and serves files of the static export."""

    def __init__(self, root: Path, product_name: str, disclaimer: str) -> None:
        """root: the export folder (frontend/out)."""
        self.root = root.resolve()
        self.product_name = product_name
        self.disclaimer = disclaimer
        self._csp_cache: dict[Path, CspCacheEntry] = {}

    @property
    def available(self) -> bool:
        """True when the export folder exists."""
        return self.root.is_dir()

    def _file(self, segments: list[str]) -> Path | None:
        """root/segments if it is a regular file inside root."""
        candidate = self.root.joinpath(*segments).resolve()
        if candidate.is_file() and candidate.is_relative_to(self.root):
            return candidate
        return None

    def resolve(self, url_path: str) -> Path | None:
        """The file for a URL path (see the module docstring for the order), or None."""
        segments = safe_segments(url_path)
        if segments is None:
            return None
        if not segments:
            return self._file(["index.html"])
        exact = self._file(segments)
        if exact is not None:
            return exact
        if PurePosixPath(segments[-1]).suffix:
            return None
        index_page = self._file([*segments, "index.html"])
        return index_page if index_page is not None else self._file([*segments[:-1], segments[-1] + ".html"])

    def csp_for(self, path: Path, stat_result: os.stat_result) -> str:
        """The CSP for an HTML file: the base policy plus the hashes of its inline scripts."""
        cached = self._csp_cache.get(path)
        if cached and cached.mtime_ns == stat_result.st_mtime_ns and cached.size == stat_result.st_size:
            return cached.csp
        hashes = inline_script_hashes(path.read_text(encoding="utf-8", errors="replace"))
        csp = build_csp(script_hashes=hashes)
        self._csp_cache[path] = CspCacheEntry(stat_result.st_mtime_ns, stat_result.st_size, csp)
        return csp

    def file_response(self, request: Request, path: Path, status_code: int = 200) -> Response:
        """A FileResponse with type, cache and CSP headers, or 304 when the browser's copy is current."""
        stat_result = path.stat()
        relative = path.relative_to(self.root).as_posix()
        headers = {"Cache-Control": IMMUTABLE_CACHE if relative.startswith(HASHED_ASSET_PREFIX) else REVALIDATE_CACHE}
        media_type = media_type_for(path)
        if media_type.startswith("text/html"):
            headers["Content-Security-Policy"] = self.csp_for(path, stat_result)
        response = FileResponse(
            path, status_code=status_code, headers=headers, media_type=media_type, stat_result=stat_result
        )
        if status_code == 200 and request.headers.get("if-none-match") == response.headers.get("etag"):
            return Response(status_code=304, headers={"ETag": response.headers["etag"], **headers})
        return response

    def not_built(self, url_path: str) -> HTMLResponse:
        """The note shown when frontend/out does not exist."""
        body = NOT_BUILT_PAGE.format(product=self.product_name, disclaimer=self.disclaimer)
        status_code = 200 if url_path.strip("/") == "" else 404
        return HTMLResponse(body, status_code=status_code, headers={"Cache-Control": REVALIDATE_CACHE})

    def respond(self, request: Request, url_path: str) -> Response:
        """The response for a GET/HEAD of a non-API path."""
        if not self.available:
            return self.not_built(url_path)
        path = self.resolve(url_path)
        if path is not None:
            return self.file_response(request, path)
        not_found_page = self._file(["404.html"])
        if not_found_page is not None:
            return self.file_response(request, not_found_page, status_code=404)
        return PlainTextResponse("Not Found", status_code=404, headers={"Cache-Control": REVALIDATE_CACHE})

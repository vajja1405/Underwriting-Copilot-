"""On-disk cache of HTTP responses, keyed by the SHA-256 of the public request, with the body's SHA-256.

Layout (under data/studio/cache, gitignored):

    <key[:2]>/<key>.body   the response body, byte for byte
    <key[:2]>/<key>.json   metadata: url, public params, request date (UTC), body SHA-256, status, content type

Why: public APIs ask callers not to repeat identical requests (Studio addendum rule F), and a cached response with
its checksum and request date is what makes a snapshot reproducible. The key is computed from the method, the URL
and the PUBLIC parameters only; API keys are removed before the key is computed, so they never reach the disk.

Reading an entry recomputes the body's SHA-256. A mismatch raises CacheCorrupted (it is never silently used).
"""

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from uwcopilot.studio.data.errors import CacheCorrupted
from uwcopilot.studio.data.paths import CACHE_DIR


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest of a byte string."""
    return hashlib.sha256(data).hexdigest()


def request_key(method: str, url: str, params: Mapping[str, str] | None = None, body: bytes | None = None) -> str:
    """The cache key: SHA-256 of a canonical JSON description of the (public) request."""
    canonical = {
        "method": method.upper(),
        "url": url,
        "params": sorted((str(name), str(value)) for name, value in (params or {}).items()),
        "body_sha256": sha256_bytes(body) if body else None,
    }
    return sha256_bytes(json.dumps(canonical, sort_keys=True).encode("utf-8"))


@dataclass(frozen=True)
class CachedResponse:
    """A response body and what is known about the request that produced it."""

    body: bytes
    sha256: str
    retrieved_at: datetime
    url: str
    params: dict[str, str]
    status_code: int
    content_type: str
    final_url: str = ""  # the URL that answered after redirects, without its query string ("" = not recorded)


class ResponseCache:
    """A folder of cached responses. Safe to share between runs; writes are atomic (temporary file, then rename)."""

    def __init__(self, root: Path = CACHE_DIR) -> None:
        self.root = root

    def paths_for(self, key: str) -> tuple[Path, Path]:
        """(body path, metadata path) for a key."""
        folder = self.root / key[:2]
        return folder / f"{key}.body", folder / f"{key}.json"

    def get(self, key: str) -> CachedResponse | None:
        """The cached response, or None when there is no entry. Raises CacheCorrupted on a checksum mismatch."""
        body_path, meta_path = self.paths_for(key)
        if not (body_path.is_file() and meta_path.is_file()):
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        body = body_path.read_bytes()
        if sha256_bytes(body) != meta["sha256"]:
            raise CacheCorrupted(
                f"cached body {body_path.name} does not match its recorded SHA-256; delete it to refetch"
            )
        return CachedResponse(
            body=body,
            sha256=meta["sha256"],
            retrieved_at=datetime.fromisoformat(meta["retrieved_at"]),
            url=meta["url"],
            params=dict(meta["params"]),
            status_code=int(meta["status_code"]),
            content_type=str(meta["content_type"]),
            final_url=str(meta.get("final_url", "")),
        )

    def put(self, key: str, response: CachedResponse) -> None:
        """Store a response (overwrites an existing entry for the same key)."""
        body_path, meta_path = self.paths_for(key)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        meta = {
            "url": response.url,
            "params": response.params,
            "retrieved_at": response.retrieved_at.isoformat(timespec="seconds"),
            "sha256": response.sha256,
            "status_code": response.status_code,
            "content_type": response.content_type,
            "final_url": response.final_url,
        }
        write_atomic(body_path, response.body)
        write_atomic(meta_path, json.dumps(meta, indent=2, sort_keys=True).encode("utf-8"))

    def delete(self, key: str) -> bool:
        """Remove one entry (body and metadata). True when something was removed."""
        removed = False
        for path in self.paths_for(key):
            if path.is_file():
                path.unlink()
                removed = True
        return removed


def write_atomic(path: Path, data: bytes) -> None:
    """Write bytes to a temporary file next to path, then rename it over path (no half-written files)."""
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(data)
    os.replace(tmp_path, path)

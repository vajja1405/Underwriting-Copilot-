"""On-disk JSON cache for LLM responses, so rerunning a script costs nothing.

The cache key is the SHA-256 of a canonical JSON document holding the provider, model, system prompt,
messages, request parameters and the client's request fingerprint (its base URL and, for Anthropic, the effort
level). Any change to any of them (even one character of the prompt, or pointing UWC_ANTHROPIC_BASE_URL at a
different gateway) is a new key, so a response is never served for an endpoint or setting that did not produce it.
Each entry is one JSON file under <cache_dir>/<first two hex chars>/<key>.json, holding the request and the
response, so a reviewer can open a file and see exactly what was asked and answered.

Incomplete answers (cut off at the token limit, or refused) are cached too, with their stop reason. A later hit
raises IncompleteLLMResponse again, exactly like the original call, so the partial text is still never used as an
answer, but a rerun or a resumed run does not pay (or wait) for the same cut-off answer twice.
"""

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uwcopilot.llm.base import IncompleteLLMResponse, LLMClient, LLMMessage, LLMResponse, raise_if_incomplete

CACHE_FORMAT_VERSION = 2


def build_cache_request(
    provider: str,
    model: str,
    system: str,
    messages: list[LLMMessage],
    params: dict[str, Any],
) -> dict[str, Any]:
    """The request description that is hashed and stored with each cache entry."""
    return {
        "format_version": CACHE_FORMAT_VERSION,
        "provider": provider,
        "model": model,
        "system": system,
        "messages": [message.model_dump() for message in messages],
        "params": params,
    }


def cache_key(provider: str, model: str, system: str, messages: list[LLMMessage], params: dict[str, Any]) -> str:
    """SHA-256 hex digest of the canonical JSON form of the request (sorted keys, no extra whitespace)."""
    request = build_cache_request(provider, model, system, messages, params)
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LLMCache:
    """Read and write cached responses in a folder."""

    def __init__(self, cache_dir: Path) -> None:
        """cache_dir is created on the first write."""
        self.cache_dir = cache_dir

    def path_for(self, key: str) -> Path:
        """Where the entry for a key lives."""
        return self.cache_dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> LLMResponse | None:
        """Return the stored response for a key, or None on a miss."""
        path = self.path_for(key)
        if not path.exists():
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        return LLMResponse.model_validate(entry["response"])

    def put(self, key: str, request: dict[str, Any], response: LLMResponse) -> Path:
        """Store a response. Writes to a temporary file first so a crash never leaves half a JSON file."""
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "format_version": CACHE_FORMAT_VERSION,
            "key": key,
            "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "request": request,
            "response": response.model_dump(),
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(entry, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return path


class CachedLLMClient:
    """Wraps any LLMClient: returns a stored response when the same request was made before.

    On a hit the inner client is not called, so no money is spent and no budget is used. The returned
    response has cached=True and cost_usd=0.0 (what this call cost); the stored file keeps the original cost.
    A stored incomplete answer raises IncompleteLLMResponse on a hit, as the original call did.
    """

    def __init__(self, inner: LLMClient, cache: LLMCache) -> None:
        """inner does the real work on a cache miss."""
        self.inner = inner
        self.cache = cache
        self.provider = inner.provider
        self.model = inner.model
        self.hits = 0
        self.misses = 0

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        max_tokens: int,
        temperature: float = 0,
    ) -> LLMResponse:
        """Return the cached response for this exact request, or call the inner client and store the result.

        Raises IncompleteLLMResponse for an incomplete answer, whether it came from the inner client or the cache.
        """
        params: dict[str, Any] = {
            "max_tokens": max_tokens,
            "temperature": temperature,
            "client": dict(getattr(self.inner, "request_fingerprint", {})),
        }
        key = cache_key(self.provider, self.model, system, messages, params)
        started = time.perf_counter()
        stored = self.cache.get(key)
        if stored is not None:
            self.hits += 1
            lookup_s = time.perf_counter() - started
            hit = stored.model_copy(update={"cached": True, "cost_usd": 0.0, "latency_s": lookup_s})
            return raise_if_incomplete(hit)
        self.misses += 1
        request = build_cache_request(self.provider, self.model, system, messages, params)
        try:
            response = self.inner.complete(system, messages, max_tokens, temperature)
        except IncompleteLLMResponse as error:
            self.cache.put(key, request, error.response)
            raise
        self.cache.put(key, request, response)
        return response

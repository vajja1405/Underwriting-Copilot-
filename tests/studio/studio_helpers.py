"""Shared helpers for the Studio tests: fixture loading and a fake network (httpx.MockTransport).

Tests never touch the network: every request goes to a MockTransport that answers from tests/studio/fixtures/ and
fails loudly for any URL it does not know.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from uwcopilot.studio.data.cache import ResponseCache
from uwcopilot.studio.data.http import HostPolicy, PoliteClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FAST_POLICY = HostPolicy(min_interval_s=0.0, max_retries=2, timeout_s=5.0, backoff_base_s=1.0)


def load_fixture(name: str) -> dict[str, Any]:
    """The fixture record (with fixture_origin, url, params, body ...)."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_body(name: str) -> str:
    """Only the stored response body of a fixture."""
    return load_fixture(name)["body"]


@dataclass
class FakeNetwork:
    """A MockTransport answering by URL prefix. requests keeps every request for assertions."""

    routes: list[tuple[str, Callable[[httpx.Request], httpx.Response]]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, url_prefix: str, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        """Answer requests whose URL (without the query string) starts with url_prefix."""
        self.routes.append((url_prefix, handler))

    def add_fixture(self, url_prefix: str, name: str, status: int = 200) -> None:
        """Answer with a fixture's body and content type."""
        record = load_fixture(name)
        body = record["body"].encode("utf-8")
        headers = {"content-type": record.get("content_type") or "application/json"}
        self.add(url_prefix, lambda request: httpx.Response(status, content=body, headers=headers))

    def handle(self, request: httpx.Request) -> httpx.Response:
        """Dispatch to the first matching route (longest prefix first)."""
        self.requests.append(request)
        address = str(request.url).split("?", 1)[0]
        for prefix, handler in sorted(self.routes, key=lambda item: len(item[0]), reverse=True):
            if address.startswith(prefix):
                return handler(request)
        raise AssertionError(f"unexpected request in a test: {address}")

    def transport(self) -> httpx.MockTransport:
        """The httpx transport for a PoliteClient."""
        return httpx.MockTransport(self.handle)


def make_client(tmp_path: Path, network: FakeNetwork, **kwargs: Any) -> tuple[PoliteClient, list[float]]:
    """A PoliteClient with an empty cache under tmp_path, the fake network and a recorded (never waiting) sleep."""
    sleeps: list[float] = []
    client = PoliteClient(
        cache=ResponseCache(tmp_path / "cache"),
        transport=network.transport(),
        policies=kwargs.pop("policies", {}),
        sleep=sleeps.append,
        **kwargs,
    )
    return client, sleeps


def fast_policies(*hosts: str) -> dict[str, HostPolicy]:
    """No waiting between requests for the given hosts (sleep is recorded, not real, anyway)."""
    return {host: FAST_POLICY for host in hosts}

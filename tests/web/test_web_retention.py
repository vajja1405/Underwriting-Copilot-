"""Tests for the retention purge: it runs at startup and on a timer, and its failures never stop the app."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from uwcopilot.web.app import create_app, purge_expired_objects, purge_periodically
from uwcopilot.web.crypto import Encryptor, generate_key
from uwcopilot.web.store import LocalFileStore, ObjectMeta, owner_digest

OWNER = "hf_oauth:6123abc"


class Clock:
    """A settable clock."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class CountingStore:
    """A store stand-in that counts purge calls; the first one can be made to fail."""

    def __init__(self, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    def purge_expired(self) -> list[ObjectMeta]:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise OSError("disk unavailable")
        return []


def test_startup_purges_expired_objects(make_settings, tmp_path: Path) -> None:
    clock = Clock()
    store = LocalFileStore(tmp_path / "store", Encryptor([generate_key().encode()]), clock=clock)
    store.put_json(OWNER, "projects", "short", {"name": "SYNTHETIC plan"}, retention_days=1)
    store.put_json(OWNER, "projects", "long", {"name": "SYNTHETIC plan"}, retention_days=30)
    clock.now += timedelta(days=2)
    folder = tmp_path / "store" / owner_digest(OWNER) / "projects"
    assert (folder / "short.enc").exists()  # expired, but still on disk until a purge runs
    with TestClient(create_app(make_settings(), store=store)):
        remaining = sorted(path.name for path in folder.iterdir())
    assert remaining == ["long.enc", "long.meta.json"]


def test_a_failing_purge_is_logged_not_raised() -> None:
    store = CountingStore(fail_first=True)
    assert purge_expired_objects(store) == 0
    assert purge_expired_objects(store) == 0
    assert store.calls == 2


def test_purge_runs_again_on_its_interval() -> None:
    store = CountingStore(fail_first=True)  # a failed run does not stop the timer

    async def run_for_a_moment() -> None:
        task = asyncio.create_task(purge_periodically(store, every_s=0.01))
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(run_for_a_moment())
    assert store.calls >= 2


def test_the_purge_interval_is_a_setting(make_settings) -> None:
    assert make_settings().purge_every_s == 3600
    assert make_settings(UWC_PURGE_EVERY_S="60").purge_every_s == 60

"""Tests for encryption at rest and the local file store: round trips, ciphertext on disk, retention, deletion."""

import os
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from uwcopilot.web.crypto import (
    DEV_KEY_FILE_NAME,
    CryptoError,
    Encryptor,
    encryptor_for_settings,
    generate_key,
    parse_fernet_keys,
)
from uwcopilot.web.store import LocalFileStore, ObjectNotFound, StoreError, owner_digest

OWNER = "hf_oauth:6123abc"
OTHER = "hf_oauth:999zzz"
MARKER = "SYNTHETIC-founder-plan-Marker-7731"


class Clock:
    """A settable clock for retention tests."""

    def __init__(self) -> None:
        self.now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> LocalFileStore:
    encryptor = Encryptor([generate_key().encode()])
    return LocalFileStore(tmp_path / "store", encryptor, default_retention_days=30, clock=clock)


def all_bytes_on_disk(root: Path) -> bytes:
    """Every file under root, concatenated."""
    return b"".join(path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file())


def test_json_and_bytes_round_trip(store: LocalFileStore, clock: Clock) -> None:
    value = {"business": "car wash", "note": MARKER, "budget": [1, 2, 3]}
    meta = store.put_json(OWNER, "projects", "p1", value)
    assert store.get_json(OWNER, "projects", "p1") == value
    assert meta.kind == "json" and meta.created_at == clock.now and meta.retention_days == 30
    store.put_bytes(OWNER, "uploads", "quote.pdf", b"%PDF-1.7 " + MARKER.encode(), retention_days=7)
    assert store.get_bytes(OWNER, "uploads", "quote.pdf") == b"%PDF-1.7 " + MARKER.encode()
    assert [item.key for item in store.list(OWNER, "projects")] == ["p1"]
    assert store.list(OTHER, "projects") == []


def test_nothing_on_disk_is_plaintext(store: LocalFileStore) -> None:
    store.put_json(OWNER, "projects", "p1", {"note": MARKER})
    store.put_bytes(OWNER, "uploads", "u1", MARKER.encode())
    on_disk = all_bytes_on_disk(store.root)
    assert MARKER.encode() not in on_disk
    assert OWNER.encode() not in on_disk  # folders are named by a digest of the owner id
    assert (store.root / owner_digest(OWNER)).is_dir()


def test_wrong_kind_and_missing_objects(store: LocalFileStore) -> None:
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    with pytest.raises(StoreError, match="holds json"):
        store.get_bytes(OWNER, "projects", "p1")
    with pytest.raises(ObjectNotFound):
        store.get_json(OWNER, "projects", "nope")
    with pytest.raises(ObjectNotFound):
        store.get_json(OTHER, "projects", "p1")


def test_a_file_copied_to_another_owner_is_refused(store: LocalFileStore) -> None:
    store.put_json(OWNER, "projects", "p1", {"secret": MARKER})
    store.put_json(OTHER, "projects", "p1", {"mine": True})
    for relative_owner, relative_other in zip(
        store.relative_paths(OWNER, "projects", "p1"), store.relative_paths(OTHER, "projects", "p1"), strict=True
    ):
        shutil.copyfile(store.root / relative_owner, store.root / relative_other)
    with pytest.raises(StoreError, match="different owner"):
        store.get_json(OTHER, "projects", "p1")


def test_another_key_cannot_decrypt(store: LocalFileStore, tmp_path: Path) -> None:
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    stranger = LocalFileStore(store.root, Encryptor([generate_key().encode()]))
    with pytest.raises(CryptoError):
        stranger.get_json(OWNER, "projects", "p1")


@pytest.mark.parametrize("name", ["../escape", ".hidden", "a/b", "", "x" * 129, "a..b"])
def test_bad_names_are_refused(store: LocalFileStore, name: str) -> None:
    with pytest.raises(StoreError):
        store.put_json(OWNER, name, "k", {})
    with pytest.raises(StoreError):
        store.put_json(OWNER, "projects", name, {})


def test_delete_and_delete_owner(store: LocalFileStore) -> None:
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    store.put_json(OWNER, "projects", "p2", {"a": 2})
    store.put_bytes(OWNER, "uploads", "u1", b"x")
    store.put_json(OTHER, "projects", "p1", {"b": 1})
    assert store.delete(OWNER, "projects", "p1") is True
    assert store.delete(OWNER, "projects", "p1") is False
    with pytest.raises(ObjectNotFound):
        store.get_json(OWNER, "projects", "p1")
    assert store.delete_owner(OWNER) == 2
    assert not (store.root / owner_digest(OWNER)).exists()
    assert store.delete_owner(OWNER) == 0
    assert store.get_json(OTHER, "projects", "p1") == {"b": 1}  # other owners are untouched


def test_retention_hides_and_purges_expired_objects(store: LocalFileStore, clock: Clock) -> None:
    store.put_json(OWNER, "projects", "short", {"a": 1}, retention_days=1)
    store.put_json(OWNER, "projects", "long", {"a": 2})  # default 30 days
    clock.now += timedelta(days=2)
    with pytest.raises(ObjectNotFound):
        store.get_json(OWNER, "projects", "short")
    assert [item.key for item in store.list(OWNER, "projects")] == ["long"]
    purged = store.purge_expired()
    assert [item.key for item in purged] == ["short"]
    remaining = sorted(path.name for path in (store.root / owner_digest(OWNER) / "projects").iterdir())
    assert remaining == ["long.enc", "long.meta.json"]
    with pytest.raises(StoreError):
        store.put_json(OWNER, "projects", "bad", {}, retention_days=0)


def test_key_rotation_reads_old_data(tmp_path: Path) -> None:
    old_key, new_key = generate_key(), generate_key()
    old = LocalFileStore(tmp_path, Encryptor(parse_fernet_keys(old_key)))
    old.put_json(OWNER, "projects", "p1", {"a": 1})
    rotated = LocalFileStore(tmp_path, Encryptor(parse_fernet_keys(f"{new_key},{old_key}")))
    assert rotated.get_json(OWNER, "projects", "p1") == {"a": 1}


def test_dev_key_fallback_is_created_once_with_private_permissions(tmp_path: Path) -> None:
    first = encryptor_for_settings([], is_space=False, store_dir=tmp_path)
    key_file = tmp_path / DEV_KEY_FILE_NAME
    assert stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    second = encryptor_for_settings([], is_space=False, store_dir=tmp_path)
    assert second.decrypt(first.encrypt(b"same key")) == b"same key"


def test_space_fallback_key_lives_in_memory_only(tmp_path: Path) -> None:
    encryptor_for_settings([], is_space=True, store_dir=tmp_path)
    assert not (tmp_path / DEV_KEY_FILE_NAME).exists()

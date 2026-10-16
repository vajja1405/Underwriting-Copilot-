"""Tests for the Hugging Face dataset store with a fake Hub API (no network)."""

import hashlib
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from uwcopilot.web.app import create_app
from uwcopilot.web.crypto import Encryptor, generate_key
from uwcopilot.web.hf_store import PENDING_DELETES_FILE, HFDatasetStore
from uwcopilot.web.store import DeletionPending, LocalFileStore, ObjectNotFound, StoreError, owner_digest

OWNER = "hf_oauth:6123abc"
MARKER = "SYNTHETIC-lease-quote-Marker-4410"
# The fake stores files of at least this many bytes as LFS objects (the real Hub has its own rules).
FAKE_LFS_MIN_BYTES = 1000


class FakeHubApi:
    """An in-memory dataset repo with the HfApi methods HFDatasetStore calls; records every call.

    Like a git repo it keeps every revision (`history`, one snapshot per commit) and every LFS object ever pushed
    (`lfs_objects`), so tests can check that deleted data is gone from the history too, not only from the head.
    """

    def __init__(self, private: bool = True) -> None:
        self.private = private
        self.files: dict[str, bytes] = {".gitattributes": b"*.enc binary\n"}
        self.history: list[dict[str, bytes]] = [dict(self.files)]
        self.lfs_objects: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.fail_deletes = False
        self.fail_squash = False

    def everything_stored(self) -> list[bytes]:
        """Every file of every revision plus every LFS object: what someone with the token could recover."""
        stored = [data for revision in self.history for data in revision.values()]
        return stored + list(self.lfs_objects.values())

    def create_repo(self, repo_id: str, *, repo_type: str, private: bool, exist_ok: bool) -> None:
        assert repo_type == "dataset" and private is True and exist_ok is True
        self.calls.append("create_repo")

    def repo_info(self, repo_id: str, *, repo_type: str) -> Any:
        return SimpleNamespace(private=self.private)

    def snapshot_download(self, repo_id: str, *, repo_type: str, local_dir: str) -> str:
        self.calls.append("snapshot_download")
        for path, data in self.files.items():
            target = Path(local_dir) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return local_dir

    def list_repo_files(self, repo_id: str, *, repo_type: str) -> list[str]:
        return sorted(self.files)

    def create_commit(self, repo_id: str, operations: list[Any], *, commit_message: str, repo_type: str) -> None:
        self.calls.append(f"commit:{len(operations)}")
        for operation in operations:
            data = operation.path_or_fileobj
            self.files[operation.path_in_repo] = data
            if len(data) >= FAKE_LFS_MIN_BYTES:
                self.lfs_objects[hashlib.sha256(data).hexdigest()] = data
        self.history.append(dict(self.files))

    def delete_files(self, repo_id: str, delete_patterns: list[str], *, repo_type: str, commit_message: str) -> None:
        if self.fail_deletes:
            raise OSError("Hub unavailable")
        self.calls.append(f"delete:{len(delete_patterns)}")
        for pattern in delete_patterns:
            for path in list(self.files):
                if path == pattern or (pattern.endswith("/") and path.startswith(pattern)):
                    del self.files[path]
        self.history.append(dict(self.files))

    def super_squash_history(self, repo_id: str, *, repo_type: str, commit_message: str) -> None:
        if self.fail_squash:
            raise OSError("Hub unavailable")
        self.calls.append("squash")
        self.history = [dict(self.files)]

    def list_repo_tree(self, repo_id: str, *, recursive: bool, repo_type: str) -> list[Any]:
        entries = []
        for path, data in self.files.items():
            is_lfs = len(data) >= FAKE_LFS_MIN_BYTES
            lfs = SimpleNamespace(sha256=hashlib.sha256(data).hexdigest()) if is_lfs else None
            entries.append(SimpleNamespace(path=path, lfs=lfs))
        return entries

    def list_lfs_files(self, repo_id: str, *, repo_type: str) -> list[Any]:
        return [SimpleNamespace(file_oid=oid) for oid in self.lfs_objects]

    def permanently_delete_lfs_files(self, repo_id: str, lfs_files: Any, *, repo_type: str) -> None:
        oids = [item.file_oid for item in lfs_files]
        self.calls.append(f"lfs_delete:{len(oids)}")
        for oid in oids:
            del self.lfs_objects[oid]


@pytest.fixture
def key() -> bytes:
    return generate_key().encode()


def make_store(root: Path, api: FakeHubApi, key: bytes) -> HFDatasetStore:
    """A restored dataset store whose working copy is root."""
    store = HFDatasetStore(LocalFileStore(root, Encryptor([key])), api, "astra6/fundproof-data", flush_every_s=3600)
    store.restore()
    return store


def remote_object_paths(api: FakeHubApi) -> list[str]:
    """Remote files other than .gitattributes."""
    return sorted(path for path in api.files if path != ".gitattributes")


def test_restore_creates_a_private_repo_and_refuses_a_public_one(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    make_store(tmp_path / "a", api, key)
    assert api.calls[:2] == ["create_repo", "snapshot_download"]
    with pytest.raises(StoreError, match="public"):
        make_store(tmp_path / "b", FakeHubApi(private=False), key)


def test_writes_are_batched_until_flush_and_uploaded_encrypted(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.put_json(OWNER, "projects", "p1", {"note": MARKER})
    store.put_bytes(OWNER, "uploads", "quote.pdf", MARKER.encode())
    assert remote_object_paths(api) == []  # nothing is sent before the flush
    assert store.flush() == 4  # two objects, payload + metadata each, in ONE commit
    assert [call for call in api.calls if call.startswith("commit")] == ["commit:4"]
    digest = owner_digest(OWNER)
    assert f"{digest}/projects/p1.enc" in api.files
    assert all(MARKER.encode() not in data for data in api.files.values())
    assert store.flush() == 0  # nothing new


def test_a_new_process_restores_the_data(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    first = make_store(tmp_path / "first", api, key)
    first.put_json(OWNER, "projects", "p1", {"note": MARKER})
    first.close()  # close() flushes the last batch
    second = make_store(tmp_path / "second", api, key)
    assert second.get_json(OWNER, "projects", "p1") == {"note": MARKER}


def test_deletes_reach_the_hub_at_once(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    store.put_json(OWNER, "projects", "p2", {"a": 2})
    store.flush()
    assert store.delete(OWNER, "projects", "p1") is True
    assert not any("/p1." in path for path in api.files)  # gone remotely without waiting for a flush
    store.put_json(OWNER, "projects", "draft", {"a": 3})
    calls_before = len(api.calls)
    store.delete(OWNER, "projects", "draft")  # never uploaded: no Hub call needed
    assert len(api.calls) == calls_before
    assert store.flush() == 0  # and the deleted draft is not uploaded later


def test_delete_owner_removes_the_remote_folder(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    store.put_json("hf_oauth:other", "projects", "p1", {"b": 1})
    store.flush()
    assert store.delete_owner(OWNER) == 1
    assert not any(path.startswith(owner_digest(OWNER)) for path in api.files)
    assert any(path.startswith(owner_digest("hf_oauth:other")) for path in api.files)


def test_purge_expired_deletes_remotely(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.put_json(OWNER, "projects", "p1", {"a": 1}, retention_days=1)
    store.flush()
    real_clock = store.local.clock
    store.local.clock = lambda: real_clock() + timedelta(days=2)
    assert [meta.key for meta in store.purge_expired()] == ["p1"]
    assert remote_object_paths(api) == []


def test_deleted_data_is_erased_from_the_hub_history(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.put_bytes(OWNER, "uploads", "big", b"x" * 4000)  # its encrypted file is stored as LFS in the fake
    store.put_json(OWNER, "projects", "small", {"note": MARKER})
    store.put_json(OWNER, "projects", "kept", {"a": 1})
    store.flush()
    digest = owner_digest(OWNER)
    deleted = [api.files[f"{digest}/uploads/big.enc"], api.files[f"{digest}/projects/small.enc"]]
    kept = api.files[f"{digest}/projects/kept.enc"]
    store.delete(OWNER, "uploads", "big")
    store.delete(OWNER, "projects", "small")
    assert any(data in api.everything_stored() for data in deleted)  # a plain delete leaves it in the history
    store.flush()
    assert not any(data in api.everything_stored() for data in deleted)
    assert kept in api.everything_stored() and len(api.history) == 1
    assert "squash" in api.calls and "lfs_delete:1" in api.calls
    calls_before = len(api.calls)
    store.flush()  # nothing deleted since the last squash: no new squash
    assert len(api.calls) == calls_before


def test_failed_remote_delete_is_reported_and_retried_at_the_next_flush(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    store.flush()
    api.fail_deletes = True
    with pytest.raises(DeletionPending):  # the caller must not tell the user the data is gone
        store.delete(OWNER, "projects", "p1")
    with pytest.raises(ObjectNotFound):
        store.get_json(OWNER, "projects", "p1")  # the local copy is gone already
    assert remote_object_paths(api) != []
    api.fail_deletes = False
    store.flush()
    assert remote_object_paths(api) == []
    assert not (store.local.root / PENDING_DELETES_FILE).exists()


def test_pending_deletes_survive_a_process_restart_on_the_same_disk(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    first = make_store(tmp_path / "work", api, key)
    first.put_json(OWNER, "projects", "p1", {"name": "SYNTHETIC founder plan"})
    first.flush()
    api.fail_deletes = True
    with pytest.raises(DeletionPending):
        first.delete(OWNER, "projects", "p1")
    assert (tmp_path / "work" / PENDING_DELETES_FILE).is_file()
    second = make_store(tmp_path / "work", api, key)  # the snapshot download brings the files back ...
    with pytest.raises(ObjectNotFound):
        second.get_json(OWNER, "projects", "p1")  # ... and the saved pending delete removes them again
    api.fail_deletes = False
    second.flush()
    assert remote_object_paths(api) == []
    assert not any(b"p1" in path.encode() for path in api.files)


def test_delete_owner_reports_a_failed_remote_delete(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    store.flush()
    api.fail_deletes = True
    with pytest.raises(DeletionPending):
        store.delete_owner(OWNER)
    api.fail_deletes = False
    store.flush()
    assert remote_object_paths(api) == []


def test_a_failed_squash_is_retried_at_the_next_flush(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.put_json(OWNER, "projects", "p1", {"note": MARKER})
    store.flush()
    deleted = api.files[f"{owner_digest(OWNER)}/projects/p1.enc"]
    store.delete(OWNER, "projects", "p1")
    api.fail_squash = True
    store.flush()  # logged, not raised
    assert deleted in api.everything_stored()
    api.fail_squash = False
    store.flush()
    assert deleted not in api.everything_stored()


def test_background_thread_starts_and_close_flushes(tmp_path: Path, key: bytes) -> None:
    api = FakeHubApi()
    store = make_store(tmp_path / "work", api, key)
    store.start()
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    store.close()
    assert len(remote_object_paths(api)) == 2


def test_app_opens_and_closes_the_dataset_store(make_settings, key: bytes) -> None:
    api = FakeHubApi()
    settings = make_settings(
        UWC_STORE="hf_dataset",
        UWC_HF_DATASET_REPO="astra6/fundproof-data",
        HF_TOKEN="hf_test_token",
        UWC_FERNET_KEY=key.decode(),
    )
    with TestClient(create_app(settings, hub_api=api)) as test_client:
        store = test_client.app.state.store
        assert isinstance(store, HFDatasetStore)
        store.put_json(OWNER, "projects", "p1", {"a": 1})
        assert remote_object_paths(api) == []
    # leaving the client runs the shutdown, which flushes the last batch
    assert len(remote_object_paths(api)) == 2


class SlowHubApi(FakeHubApi):
    """A Hub whose commits take a while (a slow network), to check that writes never wait for them."""

    def __init__(self, commit_s: float) -> None:
        super().__init__()
        self.commit_s = commit_s
        self.commit_started = threading.Event()

    def create_commit(self, repo_id: str, operations: list[Any], *, commit_message: str, repo_type: str) -> None:
        self.commit_started.set()
        time.sleep(self.commit_s)
        super().create_commit(repo_id, operations, commit_message=commit_message, repo_type=repo_type)


def test_writes_and_requests_do_not_wait_for_a_slow_commit(make_settings, key: bytes) -> None:
    api = SlowHubApi(commit_s=1.5)
    settings = make_settings(
        UWC_STORE="hf_dataset",
        UWC_HF_DATASET_REPO="astra6/fundproof-data",
        HF_TOKEN="hf_test_token",
        UWC_FERNET_KEY=key.decode(),
    )
    with TestClient(create_app(settings, hub_api=api)) as test_client:
        store = test_client.app.state.store
        store.put_json(OWNER, "projects", "p1", {"a": 1})
        flushing = threading.Thread(target=store.flush)
        flushing.start()
        assert api.commit_started.wait(5)
        started = time.perf_counter()
        store.put_json(OWNER, "projects", "p2", {"b": 2})  # a write while the commit is in flight
        assert test_client.get("/api/health").status_code == 200
        waited = time.perf_counter() - started
        flushing.join()
        assert waited < 0.75, f"a write waited {waited:.2f} s for the Hub commit"
        assert store.flush() == 2  # the second object is uploaded at the next flush, not lost
    assert len(remote_object_paths(api)) == 4


def test_a_delete_during_a_commit_still_reaches_the_hub(tmp_path: Path, key: bytes) -> None:
    api = SlowHubApi(commit_s=0.5)
    store = make_store(tmp_path / "work", api, key)
    store.put_json(OWNER, "projects", "p1", {"a": 1})
    flushing = threading.Thread(target=store.flush)
    flushing.start()
    assert api.commit_started.wait(5)
    store.delete(OWNER, "projects", "p1")  # waits for the commit, then deletes what it uploaded
    flushing.join()
    assert remote_object_paths(api) == []

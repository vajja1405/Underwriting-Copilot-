"""Where the web app keeps user data: one Store interface, two implementations.

    store = LocalFileStore(Path("data/web/store"), encryptor, default_retention_days=30)
    store.put_json("hf_oauth:123", "projects", "p1", {"name": "Car wash"})
    store.get_json("hf_oauth:123", "projects", "p1")
    store.list("hf_oauth:123", "projects")      # [ObjectMeta(...)]
    store.delete("hf_oauth:123", "projects", "p1")
    store.delete_owner("hf_oauth:123")           # "delete my data"
    store.purge_expired()                        # retention clean-up

Every object is encrypted before it touches disk (crypto.Encryptor, Fernet). Inside the encrypted bytes a short
header names the owner, namespace and key, and reading checks it, so an encrypted file copied into another user's
folder is refused instead of shown to them.

On disk (and in the dataset repo) an object is two files:
    <owner digest>/<namespace>/<key>.enc        the encrypted payload
    <owner digest>/<namespace>/<key>.meta.json  plaintext metadata: kind, created_at, retention_days, size
The owner digest is the first 32 hex characters of SHA-256(owner id), so folder names do not show who owns them.
The metadata file is written last and deleted first: an object without metadata does not exist.

Retention: every object gets retention_days (the store default unless given). An expired object is not returned
by get or list, and purge_expired() deletes it. A new put of the same key starts a new retention period.

The Hugging Face dataset store (hf_store.py) wraps a LocalFileStore as its working copy.
"""

# Postponed annotations: the Store classes have a method named `list`, which would shadow the built-in list type
# inside the class bodies if annotations were evaluated.
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from uwcopilot.web.crypto import Encryptor

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DATA_SUFFIX = ".enc"
META_SUFFIX = ".meta.json"
OWNER_DIGEST_CHARS = 32
ObjectKind = Literal["json", "bytes"]


class StoreError(ValueError):
    """A bad name, or stored data that does not belong where it was found."""


class ObjectNotFound(LookupError):
    """No such object (or it has expired)."""


class DeletionPending(RuntimeError):
    """The local copy is deleted, but the remote copy (Hugging Face dataset) could not be deleted yet.

    The deletion is recorded and retried; the caller must not tell the user that the data is gone.
    """


def utc_now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)


def owner_digest(owner: str) -> str:
    """The folder name for an owner id."""
    if not owner:
        raise StoreError("owner id is empty")
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:OWNER_DIGEST_CHARS]


def check_name(value: str, what: str) -> str:
    """Namespaces and keys are 1-128 characters of letters, digits, '_', '.', '-' and never start with '.'."""
    if not NAME_PATTERN.match(value) or ".." in value:
        raise StoreError(f"{what} {value!r} must match {NAME_PATTERN.pattern} without '..'")
    return value


def object_paths(digest: str, namespace: str, key: str) -> tuple[str, str]:
    """(data path, metadata path), relative to the store root, with '/' separators (also the dataset paths)."""
    folder = f"{digest}/{namespace}"
    return f"{folder}/{key}{DATA_SUFFIX}", f"{folder}/{key}{META_SUFFIX}"


class ObjectMeta(BaseModel):
    """Plaintext facts about one stored object."""

    model_config = ConfigDict(frozen=True)

    owner_digest: str
    namespace: str
    key: str
    kind: ObjectKind
    created_at: datetime
    retention_days: int
    size_bytes: int

    @property
    def expires_at(self) -> datetime:
        """When retention ends."""
        return self.created_at + timedelta(days=self.retention_days)

    def is_expired(self, now: datetime) -> bool:
        """True once retention has ended."""
        return now >= self.expires_at

    @property
    def paths(self) -> tuple[str, str]:
        """(data path, metadata path) relative to the store root."""
        return object_paths(self.owner_digest, self.namespace, self.key)


class Store(Protocol):
    """What the rest of the web app may do with stored data."""

    def put_json(
        self, owner: str, namespace: str, key: str, value: Any, *, retention_days: int | None = None
    ) -> ObjectMeta:
        """Store a JSON-serializable value."""
        ...

    def get_json(self, owner: str, namespace: str, key: str) -> Any:
        """Read a JSON value; ObjectNotFound if missing or expired."""
        ...

    def put_bytes(
        self, owner: str, namespace: str, key: str, data: bytes, *, retention_days: int | None = None
    ) -> ObjectMeta:
        """Store raw bytes (for example an uploaded PDF)."""
        ...

    def get_bytes(self, owner: str, namespace: str, key: str) -> bytes:
        """Read raw bytes; ObjectNotFound if missing or expired."""
        ...

    def list(self, owner: str, namespace: str) -> list[ObjectMeta]:
        """Metadata of the owner's unexpired objects in a namespace, sorted by key."""
        ...

    def delete(self, owner: str, namespace: str, key: str) -> bool:
        """Delete one object; False if it did not exist. DeletionPending if a remote copy could not be deleted."""
        ...

    def delete_owner(self, owner: str) -> int:
        """Delete everything an owner stored; returns the number of objects deleted. DeletionPending as above."""
        ...

    def purge_expired(self) -> list[ObjectMeta]:
        """Delete every expired object (all owners); returns their metadata."""
        ...


def write_atomically(path: Path, data: bytes) -> None:
    """Write to a temporary file in the same folder, then rename it over path (readers never see half a file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


class LocalFileStore:
    """Encrypted objects in a local folder (development, tests, and the working copy of the dataset store)."""

    def __init__(
        self,
        root: Path,
        encryptor: Encryptor,
        default_retention_days: int = 30,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """root is created on the first write."""
        self.root = root
        self.encryptor = encryptor
        self.default_retention_days = default_retention_days
        self.clock = clock

    def relative_paths(self, owner: str, namespace: str, key: str) -> tuple[str, str]:
        """(data path, metadata path) of an object, relative to root; checks the names."""
        return object_paths(owner_digest(owner), check_name(namespace, "namespace"), check_name(key, "key"))

    def _envelope_header(self, owner: str, namespace: str, key: str, kind: ObjectKind) -> bytes:
        """The first line inside the encrypted bytes: where this object belongs."""
        header = {"o": owner_digest(owner), "n": namespace, "k": key, "t": kind}
        return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _put(
        self, owner: str, namespace: str, key: str, kind: ObjectKind, data: bytes, retention_days: int | None
    ) -> ObjectMeta:
        """Encrypt and write the payload, then the metadata."""
        data_path, meta_path = self.relative_paths(owner, namespace, key)
        retention = self.default_retention_days if retention_days is None else retention_days
        if retention <= 0:
            raise StoreError("retention_days must be positive")
        meta = ObjectMeta(
            owner_digest=owner_digest(owner),
            namespace=namespace,
            key=key,
            kind=kind,
            created_at=self.clock(),
            retention_days=retention,
            size_bytes=len(data),
        )
        plaintext = self._envelope_header(owner, namespace, key, kind) + b"\n" + data
        write_atomically(self.root / data_path, self.encryptor.encrypt(plaintext))
        write_atomically(self.root / meta_path, meta.model_dump_json().encode("utf-8"))
        return meta

    def _read_meta(self, path: Path) -> ObjectMeta | None:
        """Parse a metadata file; None if it is missing or unreadable."""
        try:
            return ObjectMeta.model_validate_json(path.read_bytes())
        except (OSError, ValidationError):
            return None

    def _get(self, owner: str, namespace: str, key: str, kind: ObjectKind) -> bytes:
        """Decrypt an object after checking it exists, has not expired, and belongs to this owner and key."""
        data_path, meta_path = self.relative_paths(owner, namespace, key)
        meta = self._read_meta(self.root / meta_path)
        if meta is None or meta.is_expired(self.clock()) or not (self.root / data_path).is_file():
            raise ObjectNotFound(f"{namespace}/{key}")
        if meta.kind != kind:
            raise StoreError(f"{namespace}/{key} holds {meta.kind}, not {kind}")
        plaintext = self.encryptor.decrypt((self.root / data_path).read_bytes())
        header, separator, data = plaintext.partition(b"\n")
        if not separator or header != self._envelope_header(owner, namespace, key, kind):
            raise StoreError(f"{namespace}/{key}: the encrypted header names a different owner or key")
        return data

    def put_json(
        self, owner: str, namespace: str, key: str, value: Any, *, retention_days: int | None = None
    ) -> ObjectMeta:
        """Store a JSON-serializable value."""
        data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self._put(owner, namespace, key, "json", data, retention_days)

    def get_json(self, owner: str, namespace: str, key: str) -> Any:
        """Read a JSON value."""
        return json.loads(self._get(owner, namespace, key, "json"))

    def put_bytes(
        self, owner: str, namespace: str, key: str, data: bytes, *, retention_days: int | None = None
    ) -> ObjectMeta:
        """Store raw bytes."""
        return self._put(owner, namespace, key, "bytes", data, retention_days)

    def get_bytes(self, owner: str, namespace: str, key: str) -> bytes:
        """Read raw bytes."""
        return self._get(owner, namespace, key, "bytes")

    def list(self, owner: str, namespace: str) -> list[ObjectMeta]:
        """Unexpired objects of an owner in a namespace, sorted by key."""
        folder = self.root / owner_digest(owner) / check_name(namespace, "namespace")
        now = self.clock()
        metas = [self._read_meta(path) for path in folder.glob(f"*{META_SUFFIX}")]
        return sorted((meta for meta in metas if meta and not meta.is_expired(now)), key=lambda meta: meta.key)

    def delete(self, owner: str, namespace: str, key: str) -> bool:
        """Delete the metadata, then the payload. False if neither existed."""
        data_path, meta_path = self.relative_paths(owner, namespace, key)
        found = False
        for relative in (meta_path, data_path):
            path = self.root / relative
            if path.exists():
                path.unlink()
                found = True
        return found

    def delete_owner(self, owner: str) -> int:
        """Remove the owner's whole folder; returns how many objects it held."""
        folder = self.root / owner_digest(owner)
        if not folder.is_dir():
            return 0
        count = sum(1 for _ in folder.glob(f"*/*{META_SUFFIX}"))
        shutil.rmtree(folder)
        return count

    def all_metas(self) -> list[ObjectMeta]:
        """Metadata of every object of every owner (hidden folders such as .cache are skipped)."""
        if not self.root.is_dir():
            return []
        metas = []
        for path in sorted(self.root.glob(f"*/*/*{META_SUFFIX}")):
            if path.parts[-3].startswith("."):
                continue
            meta = self._read_meta(path)
            if meta is not None:
                metas.append(meta)
        return metas

    def purge_expired(self) -> list[ObjectMeta]:
        """Delete every expired object; returns their metadata."""
        now = self.clock()
        purged = []
        for meta in self.all_metas():
            if meta.is_expired(now):
                for relative in reversed(meta.paths):  # metadata first
                    (self.root / relative).unlink(missing_ok=True)
                purged.append(meta)
        return purged

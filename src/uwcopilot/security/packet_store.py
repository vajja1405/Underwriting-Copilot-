"""The encrypted packet store: uploaded packet PDFs are encrypted (Fernet) before they touch disk.

    store = EncryptedPacketStore(Path("data/processed/security/packets"), encryptor_from_env())
    meta = store.put("PKT-C-024", pdf_bytes)        # refuses a second put of the same id (packets are evidence)
    store.get("PKT-C-024")                           # the PDF bytes, checked against the stored SHA-256
    with store.decrypted_file("PKT-C-024") as pdf:   # a private temporary copy for docintel.pipeline.run_packet
        ...                                          # the copy is deleted when the block ends

On disk, per packet:
    <root>/<packet_id>/packet.pdf.enc    Fernet token of HEADER + the PDF bytes
    <root>/<packet_id>/packet.meta.json  plaintext metadata: packet_id, sha256 and size of the PDF, stored_at
The header ("UWC-PACKET-1 <packet_id>" and a newline) is inside the encrypted bytes, and get() checks it, so an
encrypted file copied into another packet's folder is refused instead of being read as that packet.

The metadata stays in clear on purpose: the packet SHA-256 is what decision records cite (packet_sha256), and
checking it must not need the key. A SHA-256 of a whole PDF does not reveal its content.

This module stores and reads bytes only. WHO may read a packet is decided by security/rbac.py (view_documents and
the packet access list); security/document_access.py combines the two and writes the audit event.
"""

import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from uwcopilot.security.config import PACKET_ID_PATTERN, is_plain_packet_id
from uwcopilot.security.crypto import Encryptor
from uwcopilot.security.fileio import write_private_file

PDF_MAGIC = b"%PDF-"
DATA_FILE = "packet.pdf.enc"
META_FILE = "packet.meta.json"
HEADER_PREFIX = b"UWC-PACKET-1 "


class PacketStoreError(ValueError):
    """A bad packet id, a missing or duplicate packet, or stored bytes that do not match their metadata."""


class PacketMeta(BaseModel):
    """What is known about a stored packet without decrypting it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    packet_id: str
    sha256: str
    size_bytes: int
    stored_at: str
    encryption: str = "fernet"


def utc_now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)


def check_packet_id(packet_id: str) -> str:
    """A packet id is a plain folder name (no slashes, no '..'); PacketStoreError otherwise."""
    if not is_plain_packet_id(packet_id):
        raise PacketStoreError(f"packet id must match {PACKET_ID_PATTERN} and must not contain '..'")
    return packet_id


def header_for(packet_id: str) -> bytes:
    """The header encrypted together with the PDF, naming the packet it belongs to."""
    return HEADER_PREFIX + packet_id.encode("ascii") + b"\n"


class EncryptedPacketStore:
    """Packet PDFs encrypted at rest, one folder per packet id."""

    def __init__(self, root: Path, encryptor: Encryptor, clock: Callable[[], datetime] = utc_now) -> None:
        """root is created on the first put."""
        self.root = root
        self._encryptor = encryptor
        self._clock = clock

    def folder(self, packet_id: str) -> Path:
        """The folder of one packet (the id is checked first)."""
        return self.root / check_packet_id(packet_id)

    def has(self, packet_id: str) -> bool:
        """True when the packet is stored (its metadata file exists)."""
        return (self.folder(packet_id) / META_FILE).is_file()

    def put(self, packet_id: str, pdf_bytes: bytes) -> PacketMeta:
        """Encrypt and store one PDF; refuses non-PDF bytes and an id that is already stored."""
        folder = self.folder(packet_id)
        if not pdf_bytes.startswith(PDF_MAGIC):
            raise PacketStoreError("only PDF files can be stored (the bytes do not start with %PDF-)")
        if self.has(packet_id):
            raise PacketStoreError(f"packet {packet_id} is already stored; packets are never overwritten")
        meta = PacketMeta(
            packet_id=packet_id,
            sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            size_bytes=len(pdf_bytes),
            stored_at=self._clock().isoformat(),
        )
        folder.mkdir(parents=True, exist_ok=True)
        write_private_file(folder / DATA_FILE, self._encryptor.encrypt(header_for(packet_id) + pdf_bytes))
        # The metadata is written last: a packet without metadata does not exist.
        write_private_file(folder / META_FILE, meta.model_dump_json(indent=2).encode("utf-8"))
        return meta

    def meta(self, packet_id: str) -> PacketMeta:
        """The stored metadata (no key needed); PacketStoreError when the packet is not stored."""
        path = self.folder(packet_id) / META_FILE
        if not path.is_file():
            raise PacketStoreError(f"packet {packet_id} is not stored")
        return PacketMeta.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def get(self, packet_id: str) -> bytes:
        """The decrypted PDF; refuses a file that belongs to another packet or does not match its SHA-256."""
        meta = self.meta(packet_id)
        plaintext = self._encryptor.decrypt((self.folder(packet_id) / DATA_FILE).read_bytes())
        header = header_for(packet_id)
        if not plaintext.startswith(header):
            raise PacketStoreError(f"the encrypted file in packet {packet_id}'s folder belongs to another packet")
        pdf_bytes = plaintext[len(header) :]
        if hashlib.sha256(pdf_bytes).hexdigest() != meta.sha256:
            raise PacketStoreError(f"packet {packet_id} does not match its recorded SHA-256")
        return pdf_bytes

    @contextmanager
    def decrypted_file(self, packet_id: str, temp_dir: Path | None = None) -> Iterator[Path]:
        """A private temporary copy of the PDF (folder mode 0700, file mode 0600), deleted when the block ends.

        temp_dir: where the private folder is made (default: the system temporary folder).
        """
        pdf_bytes = self.get(packet_id)
        folder = Path(tempfile.mkdtemp(prefix="uwc-packet-", dir=temp_dir))
        try:
            path = folder / "packet.pdf"
            write_private_file(path, pdf_bytes)
            yield path
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def delete(self, packet_id: str) -> bool:
        """Delete a packet (metadata first, so a half-deleted packet no longer exists); False if absent."""
        folder = self.folder(packet_id)
        if not folder.is_dir():
            return False
        (folder / META_FILE).unlink(missing_ok=True)
        shutil.rmtree(folder)
        return True

    def packet_ids(self) -> list[str]:
        """Every stored packet id, sorted."""
        if not self.root.is_dir():
            return []
        return sorted(path.parent.name for path in self.root.glob(f"*/{META_FILE}"))

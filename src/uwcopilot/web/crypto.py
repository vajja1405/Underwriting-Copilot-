"""Encryption at rest for everything a user stores (Fernet: AES-128-CBC + HMAC-SHA256, from `cryptography`).

    encryptor = Encryptor(parse_fernet_keys(os.environ["UWC_FERNET_KEY"]))
    token = encryptor.encrypt(b"plaintext")
    encryptor.decrypt(token) == b"plaintext"

UWC_FERNET_KEY holds one key, or several separated by commas for key rotation: the FIRST key encrypts, every key
can decrypt (cryptography's MultiFernet). Make a key with `uv run python -c "from cryptography.fernet import
Fernet; print(Fernet.generate_key().decode())"`.

When no key is configured (development only), encryptor_for_settings() picks a fallback and logs a warning:
- outside a Space: a dev key kept in a file next to the local store, so data survives restarts. A key stored next
  to the data protects nothing; it only keeps the code path identical to production.
- inside a Space with the local store: a random key kept in memory only. The Space disk is not persistent, so the
  data and the key disappear together on restart.
The Hugging Face dataset store never runs without a configured key (settings.py refuses to start).
"""

import logging
import os
from collections.abc import Sequence
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

logger = logging.getLogger(__name__)

DEV_KEY_FILE_NAME = ".dev_fernet_key"


class CryptoError(ValueError):
    """A key is malformed, or a token cannot be decrypted with any configured key."""


def generate_key() -> str:
    """A new random Fernet key (URL-safe base64 text)."""
    return Fernet.generate_key().decode("ascii")


def parse_fernet_keys(raw: str) -> list[bytes]:
    """Split a comma-separated key list and check that each one is a valid Fernet key."""
    keys = [part.strip().encode("ascii") for part in raw.split(",") if part.strip()]
    if not keys:
        raise CryptoError("UWC_FERNET_KEY is empty")
    for index, key in enumerate(keys):
        try:
            Fernet(key)
        except ValueError as error:
            raise CryptoError(f"UWC_FERNET_KEY entry {index + 1} is not a valid Fernet key") from error
    return keys


class Encryptor:
    """Encrypts and decrypts bytes; the first key encrypts and every key decrypts."""

    def __init__(self, keys: Sequence[bytes]) -> None:
        """keys: one or more Fernet keys, newest first."""
        if not keys:
            raise CryptoError("at least one Fernet key is needed")
        self._fernet = MultiFernet([Fernet(key) for key in keys])

    def encrypt(self, data: bytes) -> bytes:
        """The Fernet token for data (it embeds a timestamp and is authenticated)."""
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        """The plaintext for a token; raises CryptoError if it was tampered with or no key fits."""
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as error:
            raise CryptoError("cannot decrypt: wrong key or tampered data") from error


def load_or_create_dev_key(folder: Path) -> bytes:
    """The development key stored in folder/.dev_fernet_key, created (mode 0600) on first use."""
    key_file = folder / DEV_KEY_FILE_NAME
    if key_file.is_file():
        return key_file.read_bytes().strip()
    folder.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    descriptor = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(key)
    return key


def encryptor_for_settings(fernet_keys: Sequence[bytes], is_space: bool, store_dir: Path) -> Encryptor:
    """The Encryptor for the configured keys, or the documented development fallback (with a warning)."""
    if fernet_keys:
        return Encryptor(fernet_keys)
    if is_space:
        logger.warning(
            "UWC_FERNET_KEY is not set: using a random in-memory key. Stored data will be unreadable after a restart."
        )
        return Encryptor([Fernet.generate_key()])
    logger.warning(
        "UWC_FERNET_KEY is not set: using the DEV key in %s. Never use this outside local development.",
        store_dir / DEV_KEY_FILE_NAME,
    )
    return Encryptor([load_or_create_dev_key(store_dir)])

"""Encryption at rest for the agent side: ONE Fernet implementation, shared with the website.

    encryptor = encryptor_from_env()               # reads UWC_FERNET_KEY from .env or the environment
    token = encryptor.encrypt(b"plaintext")
    encryptor.decrypt(token) == b"plaintext"

Single source. The website already has the Encryptor (src/uwcopilot/web/crypto.py: Fernet = AES-128-CBC +
HMAC-SHA256 from `cryptography`, key rotation with MultiFernet: the first key encrypts, every key decrypts). This
module re-exports it instead of writing a second copy, so the web store, the encrypted decision log and the packet
store all use the same code and the same key variable. The dependency points from security/ to web/ for now only
because web/ may not be edited in this phase; the planned fix (docs/security.md, "Web adoption") moves the class
here and turns web/crypto.py into the re-export.

The difference from the website: the website falls back to a development key when UWC_FERNET_KEY is unset
(web/crypto.encryptor_for_settings). The agent side has NO fallback: encryptor_from_env() refuses to run without a
configured key, because a decision log encrypted with a throwaway key would be unreadable later.

Keys: UWC_FERNET_KEY holds one key, or several separated by commas (newest first). Never commit a key; tests
generate their own with generate_key(). Cloud KMS (envelope encryption, key rotation by the platform) is Phase 7 and
is not built.
"""

from collections.abc import Mapping
from pathlib import Path

from uwcopilot.common.paths import ENV_FILE
from uwcopilot.common.settings import env_reader
from uwcopilot.web.crypto import CryptoError, Encryptor, generate_key, parse_fernet_keys

FERNET_KEY_VARIABLE = "UWC_FERNET_KEY"

__all__ = [
    "FERNET_KEY_VARIABLE",
    "CryptoError",
    "Encryptor",
    "encryptor_from_env",
    "generate_key",
    "parse_fernet_keys",
]


def encryptor_from_env(env_file: Path | None = ENV_FILE, environ: Mapping[str, str] | None = None) -> Encryptor:
    """The Encryptor for UWC_FERNET_KEY (.env wins over the environment); CryptoError when it is unset or invalid."""
    raw = env_reader(env_file, environ)(FERNET_KEY_VARIABLE)
    if raw is None:
        raise CryptoError(
            f'{FERNET_KEY_VARIABLE} is not set. Generate a key with `uv run python -c "from cryptography.fernet '
            f'import Fernet; print(Fernet.generate_key().decode())"` and put it in .env (never commit it).'
        )
    return Encryptor(parse_fernet_keys(raw))

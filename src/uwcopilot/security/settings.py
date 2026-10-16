"""Where the security components keep their files, read from .env and the environment (the .env file wins).

Only these names are read:
- UWC_AUDIT_LOG: the audit event log (default data/processed/security/audit_events.jsonl, gitignored); its head
  anchor is written next to it (audit_events.head.json);
- UWC_PACKET_STORE: the folder of the encrypted packet store (default data/processed/security/packets, gitignored).
The Fernet key (UWC_FERNET_KEY) is read by security.crypto.encryptor_from_env, never stored in a settings object.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from uwcopilot.common.paths import DATA_DIR, ENV_FILE
from uwcopilot.common.settings import env_reader

DEFAULT_AUDIT_LOG = DATA_DIR / "processed" / "security" / "audit_events.jsonl"
DEFAULT_PACKET_STORE = DATA_DIR / "processed" / "security" / "packets"


@dataclass(frozen=True)
class SecuritySettings:
    """File locations of the audit log and the encrypted packet store."""

    audit_log: Path = DEFAULT_AUDIT_LOG
    packet_store: Path = DEFAULT_PACKET_STORE


def load_security_settings(
    env_file: Path | None = ENV_FILE, environ: Mapping[str, str] | None = None
) -> SecuritySettings:
    """Build SecuritySettings from the .env file and the process environment. Pass env_file=None to skip .env."""
    read = env_reader(env_file, environ)
    audit_log = read("UWC_AUDIT_LOG")
    packet_store = read("UWC_PACKET_STORE")
    return SecuritySettings(
        audit_log=Path(audit_log) if audit_log else DEFAULT_AUDIT_LOG,
        packet_store=Path(packet_store) if packet_store else DEFAULT_PACKET_STORE,
    )

"""The security policy file (config/security.yaml): the senior approval threshold and the packet access lists.

    config = load_security_config()                 # config/security.yaml
    config.senior_approval_above["credit"]          # 150000.0
    config.packet_acl.entry_for("PKT-C-024")        # that packet's entry, or None (the default roles apply)

The file is checked when it is loaded, so a mistake fails loudly instead of quietly granting access:
- unknown keys are refused at every level;
- the approval thresholds must be finite numbers, zero or more, one per business line (YAML .nan or .inf is refused:
  every comparison with NaN is False, so a NaN threshold would let any amount through);
- a packet access list may only name document-viewing roles (underwriter, senior_underwriter): granting documents
  to a model_validator or an admin is refused, because security/rbac.py would never honour it anyway;
- principal ids must look like the reviewer ids the checkpoint accepts;
- packet ids must be plain (PACKET_ID_PATTERN, no '..'), and two entries may not differ only in case.
"""

import math
import re
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from uwcopilot.agent.vocab import ACCESS_ROLES, REVIEWER_ROLES
from uwcopilot.common.paths import CONFIG_DIR

DEFAULT_SECURITY_CONFIG = CONFIG_DIR / "security.yaml"
SECURITY_CONFIG_VERSION = 1
# Only the reviewer roles may ever see packet documents (security/rbac.py, action view_documents).
DOCUMENT_ROLES: tuple[str, ...] = REVIEWER_ROLES
PRINCIPAL_ID_PATTERN = r"^[A-Za-z0-9_.@:-]{1,128}$"
PACKET_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


def is_plain_packet_id(packet_id: object) -> bool:
    """True for a plain packet id: a string matching PACKET_ID_PATTERN with no '..' (so it is a safe folder name).

    Used by the config, the access check (security/rbac.py) and the packet store, so all three agree on what a packet
    id is. Anything else (a trailing space or newline, a slash, '..') is not a packet id and is refused.
    """
    if not isinstance(packet_id, str) or ".." in packet_id:
        return False
    return re.fullmatch(PACKET_ID_PATTERN, packet_id) is not None


class SecurityConfigError(ValueError):
    """config/security.yaml is missing, malformed, or grants something the access model does not allow."""


def check_document_roles(roles: list[str]) -> list[str]:
    """Refuse roles that are unknown or that may never view documents."""
    for role in roles:
        if role not in ACCESS_ROLES:
            raise ValueError(f"unknown role {role!r}; known roles are {list(ACCESS_ROLES)}")
        if role not in DOCUMENT_ROLES:
            raise ValueError(f"role {role!r} may never view packet documents; only {list(DOCUMENT_ROLES)} may")
    return roles


class PacketAclEntry(BaseModel):
    """Who may view one packet's documents: any of these roles, or any of these principal ids."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    roles: list[str] = Field(default_factory=list)
    principals: list[str] = Field(default_factory=list)

    @field_validator("roles")
    @classmethod
    def roles_may_view_documents(cls, roles: list[str]) -> list[str]:
        """Only document-viewing roles can be granted."""
        return check_document_roles(roles)

    @field_validator("principals")
    @classmethod
    def principal_ids_are_plain(cls, principals: list[str]) -> list[str]:
        """Principal ids use the same characters as reviewer ids (no spaces, no wildcards)."""
        for principal in principals:
            if re.fullmatch(PRINCIPAL_ID_PATTERN, principal) is None:
                raise ValueError(f"principal id {principal!r} must match {PRINCIPAL_ID_PATTERN}")
        return principals


class PacketAcl(BaseModel):
    """The default document roles and the per-packet entries that replace the default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    default_roles: list[str] = Field(default_factory=list)
    packets: dict[str, PacketAclEntry] = Field(default_factory=dict)

    @field_validator("default_roles")
    @classmethod
    def default_roles_may_view_documents(cls, roles: list[str]) -> list[str]:
        """Only document-viewing roles can be the default."""
        return check_document_roles(roles)

    @field_validator("packets")
    @classmethod
    def packet_ids_are_plain(cls, packets: dict[str, PacketAclEntry]) -> dict[str, PacketAclEntry]:
        """Packet ids are plain names (letters, digits, '_', '.', '-'; no '..'), and no two differ only in case."""
        seen: dict[str, str] = {}
        for packet_id in packets:
            if not is_plain_packet_id(packet_id):
                raise ValueError(f"packet id {packet_id!r} must match {PACKET_ID_PATTERN} and must not contain '..'")
            folded = packet_id.casefold()
            if folded in seen:
                raise ValueError(f"packet ids {seen[folded]!r} and {packet_id!r} differ only in case")
            seen[folded] = packet_id
        return packets

    def entry_for(self, packet_id: str) -> PacketAclEntry | None:
        """The packet's own entry (exact id), or None when the default roles apply."""
        return self.packets.get(packet_id)

    def case_variant_of(self, packet_id: str) -> str | None:
        """The listed packet id that equals packet_id except for case ('pkt-c-024' for 'PKT-C-024'), or None.

        security/rbac.py refuses such a variant instead of applying the default roles to it, so changing the case
        of a restricted packet's id never escapes its access list.
        """
        folded = packet_id.casefold()
        for listed in self.packets:
            if listed != packet_id and listed.casefold() == folded:
                return listed
        return None


class SecurityConfig(BaseModel):
    """Everything config/security.yaml holds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    senior_approval_above: dict[str, float]
    packet_acl: PacketAcl = Field(default_factory=PacketAcl)

    @field_validator("version")
    @classmethod
    def version_is_known(cls, version: int) -> int:
        """Only the version this code understands."""
        if version != SECURITY_CONFIG_VERSION:
            raise ValueError(f"version must be {SECURITY_CONFIG_VERSION}")
        return version

    @field_validator("senior_approval_above")
    @classmethod
    def thresholds_are_not_negative(cls, thresholds: dict[str, float]) -> dict[str, float]:
        """A threshold is a finite amount in USD, zero or more (NaN and infinity are refused)."""
        if not thresholds:
            raise ValueError("at least one business line needs a threshold")
        for line, amount in thresholds.items():
            if not math.isfinite(amount):
                raise ValueError(f"the threshold for {line!r} must be a finite number, not {amount}")
            if amount < 0:
                raise ValueError(f"the threshold for {line!r} must be zero or more")
        return thresholds


def parse_security_config(document: object) -> SecurityConfig:
    """Validate an already-parsed YAML document; raises SecurityConfigError with the reasons."""
    if not isinstance(document, dict):
        raise SecurityConfigError("the security config must be a YAML mapping")
    try:
        return SecurityConfig.model_validate(document)
    except ValueError as error:
        raise SecurityConfigError(f"invalid security config: {error}") from error


def load_security_config(path: Path = DEFAULT_SECURITY_CONFIG) -> SecurityConfig:
    """Read and validate a security config file."""
    if not path.is_file():
        raise SecurityConfigError(f"security config not found: {path}")
    return parse_security_config(yaml.safe_load(path.read_text(encoding="utf-8")))


@cache
def default_security_config() -> SecurityConfig:
    """config/security.yaml, loaded once per process (tests pass their own SecurityConfig instead)."""
    return load_security_config(DEFAULT_SECURITY_CONFIG)

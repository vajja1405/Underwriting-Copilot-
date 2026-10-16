"""Role-based access control: one policy function, authorize(principal, action, resource) -> Decision.

ROLES ARE CLAIMED IN THIS DEMO. The command line and the agent have no identity provider: a reviewer's role is a
string in the resume payload (--role). authorize() enforces the table below against that role, which shows how
authority is enforced once a role is known; it does not prove who someone is. Principal.role_source records where
the role came from ("claimed" by default) so an audit event never presents a claimed role as a verified one.

The authorization table (docs/security.md has the same table with the reasons):

| Action                       | underwriter            | senior_underwriter | model_validator | admin |
|------------------------------|------------------------|--------------------|-----------------|-------|
| view_file                    | yes                    | yes                | no              | no    |
| view_documents (packet ACL)  | if the ACL allows      | if the ACL allows  | no              | no    |
| run_agent                    | yes                    | yes                | no              | no    |
| approve_file                 | up to the threshold    | yes                | no              | no    |
| decline_file                 | no                     | yes                | no              | no    |
| refer_file                   | yes                    | yes                | no              | no    |
| request_more_information     | yes                    | yes                | no              | no    |
| promote_model                | no                     | no                 | yes             | no    |
| sign_migration_certificate   | no                     | no                 | yes             | no    |
| manage_permissions           | no                     | no                 | no              | yes   |
| view_audit_log               | no                     | no                 | no              | yes   |

- The threshold is config/security.yaml senior_approval_above[line]: above it only a senior_underwriter may approve.
  The amount must be a positive, finite number (NaN is denied: every comparison with NaN is False).
- view_documents needs a plain packet id (config.is_plain_packet_id). A packet with its own access list is looked
  up by its exact id; an id that differs from a listed one only in case is denied, never given the default roles.
- A file decision that authorize() allows is still checked at the human checkpoint by agent.hitl.validate_decision
  (blocking referrals, overrides, the senior underwriter's own ceiling, startup rules). authorize() is the coarse
  "may this role act at all" gate; validate_decision() is the guideline gate. Both must say yes.
- An admin manages permissions and reads audit logs but can never approve, decline or promote anything.
- The agent itself ("system" role) is never authorized for any action here: it calls tools only through its own
  allow-list (agent/tool_runner.py) and can never decide a file. Neither is an "unknown" actor.
- Anything not in the table (an unknown action or role) is denied.
"""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from uwcopilot.agent.vocab import ACCESS_ROLES, REVIEWER_ROLES
from uwcopilot.security.config import (
    PACKET_ID_PATTERN,
    PRINCIPAL_ID_PATTERN,
    SecurityConfig,
    default_security_config,
    is_plain_packet_id,
)

# Two roles that are not access roles and are never authorized for anything: "system" is the agent process itself,
# "unknown" an actor whose claimed role was not recognised (kept only so an audit event can name the attempt).
SYSTEM_ROLE = "system"
UNKNOWN_ROLE = "unknown"
NON_ACCESS_ROLES: tuple[str, ...] = (SYSTEM_ROLE, UNKNOWN_ROLE)
RoleSource = Literal["claimed", "session_allowlist", "dev_login", "service"]
ROLE_SOURCES: tuple[str, ...] = ("claimed", "session_allowlist", "dev_login", "service")

Action = Literal[
    "view_file",
    "view_documents",
    "run_agent",
    "approve_file",
    "decline_file",
    "refer_file",
    "request_more_information",
    "promote_model",
    "sign_migration_certificate",
    "manage_permissions",
    "view_audit_log",
]

# Which roles may take each action (before the threshold and packet ACL checks).
PERMISSIONS: dict[str, frozenset[str]] = {
    "view_file": frozenset({"underwriter", "senior_underwriter"}),
    "view_documents": frozenset({"underwriter", "senior_underwriter"}),
    "run_agent": frozenset({"underwriter", "senior_underwriter"}),
    "approve_file": frozenset({"underwriter", "senior_underwriter"}),
    "decline_file": frozenset({"senior_underwriter"}),
    "refer_file": frozenset({"underwriter", "senior_underwriter"}),
    "request_more_information": frozenset({"underwriter", "senior_underwriter"}),
    "promote_model": frozenset({"model_validator"}),
    "sign_migration_certificate": frozenset({"model_validator"}),
    "manage_permissions": frozenset({"admin"}),
    "view_audit_log": frozenset({"admin"}),
}
ACTIONS: tuple[str, ...] = tuple(PERMISSIONS)

# The authorization action for each reviewer action at the human checkpoint.
CHECKPOINT_ACTIONS: dict[str, str] = {
    "approve": "approve_file",
    "decline": "decline_file",
    "refer": "refer_file",
    "request_more_information": "request_more_information",
}
# Roles that exist but may never decide a file (agent.hitl.validate_decision refuses them first).
NON_DECIDING_ROLES: tuple[str, ...] = tuple(role for role in ACCESS_ROLES if role not in REVIEWER_ROLES)

# How the website's roles (src/uwcopilot/web/settings.py ROLES) map onto access roles when the web adopts this
# module (docs/security.md, "Web adoption"). founder has no access role: founders never act on lender files.
WEB_ROLE_TO_ACCESS_ROLE: dict[str, str] = {
    "lender_senior": "senior_underwriter",
    "lender_reviewer": "underwriter",
    "admin": "admin",
}


@dataclass(frozen=True)
class Principal:
    """Who is acting: an id, ONE acting role, and where that role came from."""

    principal_id: str
    role: str
    role_source: RoleSource = "claimed"

    def __post_init__(self) -> None:
        """Refuse empty or odd ids, unknown roles and unknown role sources."""
        if re.fullmatch(PRINCIPAL_ID_PATTERN, self.principal_id) is None:
            raise ValueError(f"principal id must match {PRINCIPAL_ID_PATTERN}")
        if self.role not in (*ACCESS_ROLES, *NON_ACCESS_ROLES):
            raise ValueError(f"unknown role {self.role!r}; known roles are {[*ACCESS_ROLES, *NON_ACCESS_ROLES]}")
        if self.role_source not in ROLE_SOURCES:
            raise ValueError(f"unknown role source {self.role_source!r}; known sources are {list(ROLE_SOURCES)}")


# The agent process itself, for audit events of tool calls and recommendations. It is never authorized to act.
AGENT_PRINCIPAL = Principal("uwcopilot-agent", SYSTEM_ROLE, "service")


@dataclass(frozen=True)
class Resource:
    """What is acted on. line and amount are needed for approve_file; a packet id for view_documents."""

    kind: str
    resource_id: str
    line: str | None = None
    amount: float | None = None


@dataclass(frozen=True)
class Decision:
    """The answer of authorize(): allowed or not, and a plain-English reason either way."""

    allowed: bool
    reason: str


def allow(reason: str) -> Decision:
    """An allowing decision."""
    return Decision(allowed=True, reason=reason)


def deny(reason: str) -> Decision:
    """A denying decision."""
    return Decision(allowed=False, reason=reason)


def usd(amount: float) -> str:
    """$150,000.00 style."""
    return f"${amount:,.2f}"


def roles_text(roles: Iterable[str]) -> str:
    """Roles in the canonical order of ACCESS_ROLES, joined for a message."""
    ordered = [role for role in ACCESS_ROLES if role in set(roles)]
    return ", ".join(ordered) if ordered else "no role"


def approval_decision(principal: Principal, resource: Resource | None, config: SecurityConfig) -> Decision:
    """approve_file for a role that may approve: the senior threshold by business line."""
    if resource is None or resource.line is None or resource.amount is None:
        return deny("approve_file needs the file's business line and amount")
    thresholds = config.senior_approval_above
    if resource.line not in thresholds:
        return deny(f"no approval threshold is configured for line {resource.line!r}")
    if not math.isfinite(resource.amount) or resource.amount <= 0:
        return deny("approve_file needs a positive, finite amount")
    if principal.role == "senior_underwriter":
        return allow(
            "senior_underwriter may approve_file; the guideline limits are still checked at the human checkpoint"
        )
    threshold = thresholds[resource.line]
    if resource.amount > threshold:
        return deny(
            f"above {usd(threshold)} on the {resource.line} line only a senior_underwriter may approve; "
            f"this file is {usd(resource.amount)}"
        )
    return allow(f"{principal.role} may approve_file up to {usd(threshold)} on the {resource.line} line")


def document_decision(principal: Principal, resource: Resource | None, config: SecurityConfig) -> Decision:
    """view_documents for a role that may view documents: the packet's access list, else the default roles."""
    if resource is None or resource.kind != "packet" or not resource.resource_id:
        return deny("view_documents needs a packet")
    if not is_plain_packet_id(resource.resource_id):
        return deny(f"view_documents needs a plain packet id ({PACKET_ID_PATTERN}, no '..')")
    acl = config.packet_acl
    entry = acl.entry_for(resource.resource_id)
    if entry is None and acl.case_variant_of(resource.resource_id) is not None:
        return deny("the packet id differs only in case from a packet with its own access list; use the exact id")
    if entry is None:
        if principal.role in acl.default_roles:
            return allow(f"{principal.role} may view the documents of packets without their own access list")
        return deny(f"{principal.role} is not in the default document roles ({roles_text(acl.default_roles)})")
    if principal.role in entry.roles:
        return allow(f"{principal.role} is on the access list of packet {resource.resource_id}")
    if principal.principal_id in entry.principals:
        return allow(f"this principal is on the access list of packet {resource.resource_id}")
    return deny(f"not on the access list of packet {resource.resource_id}")


def authorize(
    principal: Principal, action: str, resource: Resource | None = None, config: SecurityConfig | None = None
) -> Decision:
    """The one policy function: may this principal take this action on this resource?

    config defaults to config/security.yaml. The reason never echoes the principal id or resource text.
    """
    allowed_roles = PERMISSIONS.get(action)
    if allowed_roles is None:
        return deny(f"unknown action {action!r}")
    if principal.role not in allowed_roles:
        return deny(f"{principal.role} may not {action}; only {roles_text(allowed_roles)} may")
    settings = config if config is not None else default_security_config()
    if action == "approve_file":
        return approval_decision(principal, resource, settings)
    if action == "view_documents":
        return document_decision(principal, resource, settings)
    return allow(f"{principal.role} may {action}")


def checkpoint_role_refusal(raw: Any) -> str | None:
    """Why a resume value may not decide a file because of its role, or None (agent.hitl.validate_decision).

    Only roles that exist but never decide files (model_validator, admin) are refused here, with the reason from
    authorize(). Unknown roles, bad ids and other schema problems are left to the HumanDecision schema.
    """
    if not isinstance(raw, Mapping):
        return None
    role = raw.get("role")
    if not isinstance(role, str) or role.strip().lower() not in NON_DECIDING_ROLES:
        return None
    action = CHECKPOINT_ACTIONS.get(str(raw.get("action")), "approve_file")
    decision = authorize(Principal("checkpoint-reviewer", role.strip().lower()), action)
    return f"role refused: {decision.reason} (only underwriter and senior_underwriter decide files)"


def access_roles_for_web_roles(web_roles: Iterable[str]) -> tuple[str, ...]:
    """The access roles a website user holds (canonical order); founders hold none. The web picks the acting one."""
    mapped = {WEB_ROLE_TO_ACCESS_ROLE[role] for role in web_roles if role in WEB_ROLE_TO_ACCESS_ROLE}
    return tuple(role for role in ACCESS_ROLES if role in mapped)


def acting_reviewer_role(access_roles: Iterable[str]) -> str | None:
    """The reviewer role a user acts with on a file: the highest reviewer role they hold, or None."""
    held = set(access_roles)
    for role in reversed(REVIEWER_ROLES):
        if role in held:
            return role
    return None

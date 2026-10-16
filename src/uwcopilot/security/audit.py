"""Audit events: an append-only, hash-chained JSON Lines log of every view, tool call, recommendation and decision.

    log = AuditLog(Path("data/processed/security/audit_events.jsonl"))
    log.append("view", principal, "view_file", Resource("file", "APP-C-001"), outcome="allowed", reason="...")
    record_access(log, principal, "view_documents", Resource("packet", "PKT-C-024"), decision)
    audit_decision_record(log, stored_record)     # tool calls, recommendation, refused attempts, decision, override
    log.verify()                                   # [] when intact; scripts/verify_audit.py prints the same

One event per line:
    {"schema_version": 1, "seq": 0, "event_id": "AE-00000000", "occurred_at": "...", "event_type": "view",
     "actor": {"principal_id": "uw.1", "role": "underwriter", "role_source": "claimed"},
     "action": "view_file", "resource": {"kind": "file", "id": "APP-C-001"}, "outcome": "allowed",
     "reason": "...", "details": {...}, "data_label": "SYNTHETIC", "prev_hash": "000...", "record_hash": "..."}

Integrity. record_hash is the SHA-256 of the event's canonical JSON without record_hash; prev_hash is the record_hash
of the event before it (64 zeros for the first); seq counts from 0. After every append the log's "head anchor"
(<name>.head.json: the event count and the last record_hash) is rewritten. verify() detects:
- an EDITED event (its record_hash no longer matches its content, or the line is no longer valid JSON);
- a DELETED event in the middle or at the start (seq gap, prev_hash mismatch);
- REORDERED events (seq and prev_hash mismatch);
- a TRUNCATED tail (fewer events, or another last hash, than the head anchor says; or a cut-off last line).
Limit: someone who can rewrite BOTH the log and its head file can rebuild a consistent chain. Copy the head
(count + hash, printed by scripts/verify_audit.py) somewhere they cannot write, and pass it back later with
--expect-count / --expect-head. That copy is checked as a PREFIX: the log may have grown since, but it must still
hold at least that many events and the event at that position must still have that record_hash, so history up to
the copy cannot be rewritten unseen. Events appended after the copy are protected only by the stored head (refresh
the copy regularly). A signed or write-once anchor (Cloud KMS, object lock) is Phase 7, not built.

Lines. Each event is written as ASCII-only JSON (non-ASCII characters escaped) and the file is split on "\n" only
(security.fileio.jsonl_lines), so a reviewer-typed U+2028 or U+0085 can never split one event into two lines.

Privacy. Every reason and every details value goes through observability.redaction (a PiiRedactor: names you give
it, FEINs, SSNs, e-mails, phones, account numbers, and whole values under keys such as rationale, notes, excerpt).
Build the AuditLog (or pass a redactor per call) with redactor_for_application(...) to mask the applicant's names.
The actor id and the resource id are kept: an audit trail must say who did what to which file. Use opaque ids
(reviewer ids are staff identifiers; applicant data never goes into an id). The same holds for two details keys,
decision_record_id and event_key (KEPT_ID_PATTERNS): they are kept as written when they have their id shape,
because a record id's 24 hex characters can hold a digit run the redactor would mask as an account number.

Roles are CLAIMED in this demo: actor.role_source says so on every event ("claimed" unless the caller knows better).
"""

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from uwcopilot.agent.applications import canonical_json
from uwcopilot.agent.vocab import ACCESS_ROLES, SYNTHETIC_LABEL
from uwcopilot.observability.redaction import PiiRedactor
from uwcopilot.security.config import PRINCIPAL_ID_PATTERN
from uwcopilot.security.fileio import jsonl_lines, write_private_file
from uwcopilot.security.rbac import AGENT_PRINCIPAL, UNKNOWN_ROLE, Decision, Principal, Resource

GENESIS_HASH = "0" * 64
AUDIT_SCHEMA_VERSION = 1
MAX_SHORT_TEXT = 200

EVENT_TYPES: tuple[str, ...] = (
    "view",  # a file, a packet's documents or the audit log was shown
    "access_denied",  # authorize() said no
    "run_started",  # an agent run was started for a file
    "tool_call",  # the agent called a tool
    "recommendation",  # the reference policy's (and memo's) recommendation reached the human checkpoint
    "approval",  # a human decision accepted by the checkpoint ...
    "decline",
    "referral",
    "information_request",
    "override",  # ... that differs from the recommendation (logged in addition to the decision)
    "decision_refused",  # the checkpoint refused a submitted decision
    "permission_change",  # admin and validator actions (authorized by rbac; no code path performs them yet)
    "model_promotion",
    "migration_certificate",
)
OUTCOMES: tuple[str, ...] = ("allowed", "denied", "ok", "error", "refused")
# The event logged by record_access() for an action that authorize() allowed.
ACCESS_EVENT_TYPES: dict[str, str] = {
    "view_file": "view",
    "view_documents": "view",
    "view_audit_log": "view",
    "run_agent": "run_started",
    "manage_permissions": "permission_change",
    "promote_model": "model_promotion",
    "sign_migration_certificate": "migration_certificate",
}
# The event for each reviewer action the checkpoint accepted.
DECISION_EVENT_TYPES: dict[str, str] = {
    "approve": "approval",
    "decline": "decline",
    "refer": "referral",
    "request_more_information": "information_request",
}
# Details keys kept out of the PII redactor when the value has this id shape (see "Privacy" above).
RECORD_ID_PATTERN = r"^(DR|EV)-[0-9a-f]{1,64}$"  # DR-<24 hex> (agent), EV-<24 hex> (evidence records)
EVENT_KEY_PATTERN = r"^[a-z_]{1,40}(:[0-9]{1,6})?$"  # tool_call:0, recommendation, decision, override, ...
KEPT_ID_PATTERNS: dict[str, str] = {"decision_record_id": RECORD_ID_PATTERN, "event_key": EVENT_KEY_PATTERN}
REQUIRED_FIELDS: tuple[str, ...] = (
    "schema_version",
    "seq",
    "event_id",
    "occurred_at",
    "event_type",
    "actor",
    "action",
    "outcome",
    "prev_hash",
    "record_hash",
)


class AuditLogError(RuntimeError):
    """The audit log cannot be appended to safely (for example its last line is cut off)."""


class AuditHead(BaseModel):
    """The head anchor: how many events the log should hold and the record_hash of the last one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int
    head_hash: str
    updated_at: str | None = None


def utc_now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)


def event_hash(event: Mapping[str, Any]) -> str:
    """SHA-256 of the event's canonical JSON, without its own record_hash field."""
    body = {key: value for key, value in event.items() if key != "record_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def event_line(event: Mapping[str, Any]) -> str:
    """The stored line of one event: ASCII-only JSON with sorted keys (non-ASCII characters are escaped).

    record_hash is computed with canonical_json over the parsed event, which gives the same text for this line and
    for the line read back, so escaping changes nothing in the chain.
    """
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def redact_details(redactor: PiiRedactor, details: Mapping[str, Any] | None) -> dict[str, Any]:
    """details through the PII redactor, except the KEPT_ID_PATTERNS keys whose values have their id shape."""
    original = dict(details or {})
    cleaned = redactor.redact(original)
    for key, pattern in KEPT_ID_PATTERNS.items():
        value = original.get(key)
        if isinstance(value, str) and re.fullmatch(pattern, value) is not None:
            cleaned[key] = value
    return cleaned


def head_path_for(log_path: Path) -> Path:
    """Where the head anchor of a log lives: audit_events.jsonl -> audit_events.head.json."""
    return log_path.with_name(log_path.stem + ".head.json")


def read_head(path: Path) -> AuditHead | None:
    """The head anchor stored at path, or None when there is none."""
    if not path.is_file():
        return None
    return AuditHead.model_validate(json.loads(path.read_text(encoding="utf-8")))


def actor_of(principal: Principal) -> dict[str, str]:
    """The actor fields of an event."""
    return {"principal_id": principal.principal_id, "role": principal.role, "role_source": principal.role_source}


def resource_of(resource: Resource | None) -> dict[str, Any] | None:
    """The resource fields of an event (line and amount only when known)."""
    if resource is None:
        return None
    fields: dict[str, Any] = {"kind": resource.kind, "id": resource.resource_id}
    if resource.line is not None:
        fields["line"] = resource.line
    if resource.amount is not None:
        fields["amount"] = resource.amount
    return fields


class AuditLog:
    """One JSONL file of audit events plus its head anchor. Events are only ever appended."""

    def __init__(
        self, path: Path, clock: Callable[[], datetime] = utc_now, redactor: PiiRedactor | None = None
    ) -> None:
        """path is created (with its folder) on the first append; redactor defaults to the pattern-only redactor."""
        self.path = path
        self.head_path = head_path_for(path)
        self._clock = clock
        self._redactor = redactor if redactor is not None else PiiRedactor()

    def events(self) -> list[dict[str, Any]]:
        """Every event in file order (an empty list if the file does not exist); AuditLogError on a damaged line."""
        if not self.path.exists():
            return []
        events = []
        for number, line in enumerate(jsonl_lines(self.path.read_text(encoding="utf-8")), start=1):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise AuditLogError(f"line {number} of the audit log is not valid JSON") from error
        return events

    def head(self) -> AuditHead | None:
        """The stored head anchor, or None."""
        return read_head(self.head_path)

    def append(
        self,
        event_type: str,
        actor: Principal,
        action: str,
        resource: Resource | None = None,
        outcome: str = "ok",
        reason: str | None = None,
        details: Mapping[str, Any] | None = None,
        redactor: PiiRedactor | None = None,
        data_label: str = SYNTHETIC_LABEL,
    ) -> dict[str, Any]:
        """Chain and append one event (reason and details are PII-redacted first); return the stored event.

        redactor overrides the log's redactor for this event only.
        """
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}; known types are {list(EVENT_TYPES)}")
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}; known outcomes are {list(OUTCOMES)}")
        scrub = redactor if redactor is not None else self._redactor
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One writer at a time (POSIX file lock): read the tail, chain, append, update the head, then unlock.
        with self.path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                existing = handle.read()
                seq, previous = next_position(existing)
                event: dict[str, Any] = {
                    "schema_version": AUDIT_SCHEMA_VERSION,
                    "seq": seq,
                    "event_id": f"AE-{seq:08d}",
                    "occurred_at": self._clock().isoformat(),
                    "event_type": event_type,
                    "actor": actor_of(actor),
                    "action": action,
                    "resource": resource_of(resource),
                    "outcome": outcome,
                    "reason": scrub.redact_text(reason) if reason else None,
                    "details": redact_details(scrub, details),
                    "data_label": data_label,
                    "prev_hash": previous,
                }
                event["record_hash"] = event_hash(event)
                handle.write((event_line(event) + "\n").encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
                head = AuditHead(count=seq + 1, head_hash=event["record_hash"], updated_at=event["occurred_at"])
                write_private_file(self.head_path, head.model_dump_json(indent=2).encode("utf-8"))
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return event

    def verify(self, expected_head: AuditHead | None = None) -> list[str]:
        """Problems found in the chain, against the stored head and against an anchor kept elsewhere ([] = intact).

        The stored head file must match the log exactly (it is rewritten on every append). expected_head is a copy
        of an earlier head kept where the log's writer cannot change it; the log may have grown since, so it is
        checked as a prefix (anchor_problems).
        """
        return verify_audit_file(self.path, self.head(), expected_head)


def next_position(existing: bytes) -> tuple[int, str]:
    """(seq, prev_hash) for the next event, from the current file bytes; AuditLogError if the tail is damaged."""
    if not existing.strip():
        return 0, GENESIS_HASH
    if not existing.endswith(b"\n"):
        raise AuditLogError("the audit log's last line is cut off; run scripts/verify_audit.py before writing")
    last_line = existing.rstrip(b"\n").rsplit(b"\n", 1)[-1]
    try:
        last = json.loads(last_line)
        return int(last["seq"]) + 1, str(last["record_hash"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise AuditLogError("the audit log's last event is damaged; run scripts/verify_audit.py") from error


def verify_lines(lines: list[str]) -> list[str]:
    """Chain problems in a list of JSON lines (seq, prev_hash and record_hash of every event)."""
    problems = []
    previous = GENESIS_HASH
    for index, line in enumerate(lines):
        number = index + 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {number}: not valid JSON (edited or cut off)")
            previous = ""
            continue
        missing = [field for field in REQUIRED_FIELDS if not isinstance(event, dict) or field not in event]
        if missing:
            problems.append(f"line {number}: missing fields {missing} (edited)")
            previous = ""
            continue
        if event["seq"] != index:
            problems.append(f"line {number}: seq is {event['seq']}, expected {index} (an event was deleted or moved)")
        if event["prev_hash"] != previous:
            problems.append(f"line {number}: prev_hash does not match the event before it (deleted or reordered)")
        if event["record_hash"] != event_hash(event):
            problems.append(f"line {number}: record_hash does not match its content (edited)")
        previous = event["record_hash"]
    return problems


def verify_audit_file(path: Path, head: AuditHead | None, anchor: AuditHead | None = None) -> list[str]:
    """Chain problems, stored-head problems and (when given) problems against an anchor kept elsewhere."""
    if not path.is_file():
        return [f"no audit log at {path}"]
    text = path.read_text(encoding="utf-8")
    lines = jsonl_lines(text)
    problems = []
    if text and not text.endswith("\n"):
        problems.append(f"line {len(lines)}: the last line is cut off (truncated while or after writing)")
    problems.extend(verify_lines(lines))
    problems.extend(head_problems(lines, head))
    if anchor is not None:
        problems.extend(anchor_problems(lines, anchor))
    return problems


def head_problems(lines: list[str], head: AuditHead | None) -> list[str]:
    """The stored head file must match the log EXACTLY: the same count and the same last record_hash."""
    if head is None:
        return ["no head anchor: a truncated tail cannot be ruled out"] if lines else []
    problems = []
    if head.count != len(lines):
        problems.append(f"the log holds {len(lines)} events but the head anchor says {head.count} (truncated?)")
    if head.count and record_hash_at(lines, len(lines) - 1) != head.head_hash:
        problems.append("the last event's record_hash does not match the head anchor (truncated or rewritten)")
    return problems


def anchor_problems(lines: list[str], anchor: AuditHead) -> list[str]:
    """An anchor kept elsewhere is a PREFIX check: the log may have grown since the copy was taken.

    The log must still hold at least anchor.count events, and event number anchor.count must still have the
    anchor's record_hash. Because every record_hash covers the prev_hash before it, that pins every event up to the
    anchor; verify_lines checks the events appended after it.
    """
    if anchor.count < 0:
        return ["the anchor kept elsewhere has a negative event count"]
    if anchor.count > len(lines):
        return [
            f"the log holds {len(lines)} events but the anchor kept elsewhere says at least {anchor.count} (truncated?)"
        ]
    if anchor.count and record_hash_at(lines, anchor.count - 1) != anchor.head_hash:
        return [
            f"line {anchor.count}: record_hash does not match the anchor kept elsewhere "
            "(the history up to the anchor was rewritten)"
        ]
    return []


def record_hash_at(lines: list[str], index: int) -> str | None:
    """The record_hash of lines[index], or None when there is no such line or it cannot be read."""
    if not 0 <= index < len(lines):
        return None
    try:
        value = json.loads(lines[index]).get("record_hash")
    except (json.JSONDecodeError, AttributeError):
        return None
    return value if isinstance(value, str) else None


def record_access(
    log: AuditLog,
    principal: Principal,
    action: str,
    resource: Resource | None,
    decision: Decision,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Log one authorization outcome: access_denied when refused, else the action's own event (view, ...).

    File decisions are not logged here: the human checkpoint may still refuse them, so they are logged from the
    decision record (audit_decision_record).
    """
    if action not in ACCESS_EVENT_TYPES:
        raise ValueError(f"{action!r} is not logged by record_access; file decisions come from the decision record")
    if not decision.allowed:
        return log.append("access_denied", principal, action, resource, "denied", decision.reason, details)
    return log.append(ACCESS_EVENT_TYPES[action], principal, action, resource, "allowed", decision.reason, details)


def claimed_principal(raw: Mapping[str, Any]) -> Principal:
    """The actor a submitted decision claims to be (role_source "claimed"); 'unknown' when it is not recognisable."""
    reviewer_id = raw.get("reviewer_id")
    role = raw.get("role")
    plain_id = isinstance(reviewer_id, str) and re.fullmatch(PRINCIPAL_ID_PATTERN, reviewer_id) is not None
    principal_id = reviewer_id if plain_id else "unidentified"
    known_role = role if isinstance(role, str) and role in ACCESS_ROLES else UNKNOWN_ROLE
    return Principal(principal_id, known_role, "claimed")


@dataclass(frozen=True)
class PlannedEvent:
    """One event that a decision record holds, before it is chained. event_key is stable for the record."""

    event_key: str
    event_type: str
    actor: Principal
    action: str
    outcome: str
    reason: str | None
    details: dict[str, Any]


def audited_event_keys(log: AuditLog, record_id: str) -> set[str]:
    """The event_key of every event already in the log for this decision record."""
    keys = set()
    for event in log.events():
        details = event.get("details") or {}
        if details.get("decision_record_id") == record_id and isinstance(details.get("event_key"), str):
            keys.add(details["event_key"])
    return keys


def tool_call_events(record: Mapping[str, Any]) -> list[PlannedEvent]:
    """One tool_call event per tool call (actor: the agent)."""
    events = []
    for index, call in enumerate(record.get("tool_calls") or []):
        details = {key: call.get(key) for key in ("result_id", "tool", "purpose", "status")}
        outcome = "ok" if call.get("status") == "ok" else "error"
        key = f"tool_call:{index}"
        events.append(PlannedEvent(key, "tool_call", AGENT_PRINCIPAL, str(call.get("tool")), outcome, None, details))
    return events


def recommendation_event(record: Mapping[str, Any]) -> PlannedEvent:
    """The recommendation that reached the human checkpoint (actor: the agent)."""
    memo = record.get("memo") or {}
    details = {
        "reference_recommendation": record.get("reference_recommendation"),
        "memo_recommendation": memo.get("recommendation") if isinstance(memo, dict) else None,
        "memo_status": record.get("memo_status"),
        "fake_llm": record.get("fake_llm"),
    }
    return PlannedEvent("recommendation", "recommendation", AGENT_PRINCIPAL, "recommend", "ok", None, details)


def refused_attempt_events(record: Mapping[str, Any]) -> list[PlannedEvent]:
    """One decision_refused event per refused submission (actor: whoever the submission claimed to be)."""
    events = []
    for index, attempt in enumerate(record.get("rejected_reviewer_attempts") or []):
        submitted = attempt.get("submitted") if isinstance(attempt.get("submitted"), dict) else {}
        details = {"submitted_action": str(submitted.get("action"))[:MAX_SHORT_TEXT]}
        reason = "; ".join(str(error) for error in attempt.get("errors") or [])[: MAX_SHORT_TEXT * 5]
        actor = claimed_principal(submitted)
        key = f"decision_refused:{index}"
        events.append(PlannedEvent(key, "decision_refused", actor, "decide_file", "refused", reason, details))
    return events


def decision_events(record: Mapping[str, Any]) -> list[PlannedEvent]:
    """The accepted decision and, when it differs from the recommendation, an override (actor: the reviewer)."""
    reviewer = record.get("reviewer") or {}
    action = str(reviewer.get("action"))
    actor = claimed_principal(reviewer)
    events = []
    event_type = DECISION_EVENT_TYPES.get(action)
    if event_type is not None:
        details = {
            "final_status": record.get("final_status"),
            "reference_recommendation": record.get("reference_recommendation"),
            "is_override": bool(reviewer.get("is_override")),
            "rationale": reviewer.get("rationale"),
        }
        events.append(PlannedEvent("decision", event_type, actor, action, "ok", None, details))
    if reviewer.get("is_override"):
        details = {
            "action": action,
            "override_reason": reviewer.get("override_reason"),
            "reference_recommendation": record.get("reference_recommendation"),
        }
        events.append(PlannedEvent("override", "override", actor, action, "ok", None, details))
    return events


def decision_record_events(record: Mapping[str, Any]) -> list[PlannedEvent]:
    """Every event a stored decision record holds, in the order they are logged."""
    return [
        *tool_call_events(record),
        recommendation_event(record),
        *refused_attempt_events(record),
        *decision_events(record),
    ]


def audit_decision_record(
    log: AuditLog, record: Mapping[str, Any], redactor: PiiRedactor | None = None
) -> list[dict[str, Any]]:
    """Log the events a stored agent decision record holds; idempotent PER EVENT. Returns the new events.

    In order: one tool_call per tool call (actor: the agent), the recommendation (the agent), each refused decision
    attempt (the claimed reviewer), the accepted decision (the claimed reviewer), and an override when the decision
    differs from the recommendation. The record itself is not copied: events hold ids, statuses and redacted text.

    Every event carries details.decision_record_id and a stable details.event_key ("tool_call:0", "recommendation",
    "decision_refused:0", "decision", "override"). A replay writes only the events whose key is not in the log yet,
    so a run interrupted after its first events is completed, not skipped. Call it from the one process that wrote
    the record: two processes replaying the same record at the same moment could both write a missing event.

    redactor: pass observability.redaction.redactor_for_application(application) so the applicant's and owners'
    names are masked too; without it only patterns (FEIN, SSN, e-mail, phone, account numbers) and sensitive keys
    (rationale, notes, ...) are masked, because names cannot be found by pattern.
    """
    record_id = str(record["record_id"])
    resource = Resource("file", str(record.get("application_id") or "unknown"))
    logged = audited_event_keys(log, record_id)
    written = []
    for planned in decision_record_events(record):
        if planned.event_key in logged:
            continue
        details = {"decision_record_id": record_id, "event_key": planned.event_key, **planned.details}
        event = log.append(
            planned.event_type,
            planned.actor,
            planned.action,
            resource,
            outcome=planned.outcome,
            reason=planned.reason,
            details=details,
            redactor=redactor,
        )
        written.append(event)
    return written

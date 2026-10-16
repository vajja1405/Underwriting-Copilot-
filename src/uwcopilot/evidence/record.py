"""Evidence decision records (Phase 4 E): one append-only, hash-chained record per engine recommendation.

A record holds everything needed to explain the engine's output later: the packet hash, the application inputs hash,
the evidence ledger, the contradiction graph, the calculations (and the display-only supplementary calculations of
evidence/supplementary.py), the scenarios and decision stability, the open items and the next-best-evidence
ranking, model versions ("not evaluated": no Phase 1 model applies), prompt versions (none: no LLM runs in this
phase), the policy, engine, catalog and template versions, the reference recommendation, the reviewer action fields
(None until a person acts), timestamps, and supersedes_record_id.

Records are appended with agent.decision_record.DecisionLog (unchanged): JSON Lines, every record chained to the one
before by SHA-256, so an edit or a deletion is detected by verify_chain(). A record is never edited after it is
written. A correction, or a reviewer's action, is a NEW record (supersede) that names the record it replaces in
supersedes_record_id, with the reason. The correction contract is enforced: a record that supersedes another must
give a non-empty correction_reason (build_decision_record), the superseded record must already be in the log it is
appended to (append_record), and supersede() refuses updates to identity fields (record id, case id, record type,
supersedes_record_id, correction_reason, recorded_at and the chain fields). The default log is
data/processed/evidence/decision_records.jsonl, which is gitignored (data/ is never committed).

record_id = "EV-" + the first 24 hex characters of SHA-256 over the record's canonical JSON without its timestamp,
so writing the same engine output twice is a no-op (DecisionLog.append is idempotent by record_id), while a
correction or a reviewer action always gets a new id.

The rendered views (record_views.render_markdown / render_html, re-exported here) escape every value, because
contradiction messages, evidence values and request params can carry document text, which is untrusted data.
Everything in a record is SYNTHETIC.
"""

import copy
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uwcopilot.agent.applications import canonical_json
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.common.paths import DATA_DIR
from uwcopilot.evidence.record_views import render_html, render_markdown

ENGINE_VERSION = "evidence-engine-v1.1"
RECORD_TYPE = "evidence_engine_recommendation"
DEFAULT_RECORDS_PATH = DATA_DIR / "processed" / "evidence" / "decision_records.jsonl"
DEFAULT_VIEWS_DIR = DATA_DIR / "processed" / "evidence" / "records"
RECORD_ID_PREFIX = "EV-"
RECORD_ID_HEX = 24
NOTICE = (
    "Decision support only: the reference recommendation is not a decision, and a person makes every underwriting "
    "decision. SYNTHETIC data; Northwind Business Credit and Harborline Mutual are fictional."
)
# Fields DecisionLog adds when it appends; a superseding record starts without them.
CHAIN_FIELDS: tuple[str, ...] = ("schema_version", "previous_record_sha256", "record_sha256")
TIMESTAMP_FIELD = "recorded_at"
# Fields a correction may never change through supersede(updates=...): they say WHICH record this is.
IDENTITY_FIELDS: tuple[str, ...] = (
    "record_id",
    "record_type",
    "case_id",
    "supersedes_record_id",
    "correction_reason",
    TIMESTAMP_FIELD,
    *CHAIN_FIELDS,
)
EMPTY_REVIEWER: dict[str, Any] = {
    "action": None,
    "reviewer_id": None,
    "role": None,
    "rationale": None,
    "override_reason": None,
    "decided_at": None,
}

__all__ = [
    "DEFAULT_RECORDS_PATH",
    "DEFAULT_VIEWS_DIR",
    "ENGINE_VERSION",
    "append_record",
    "build_decision_record",
    "record_id_for",
    "render_html",
    "render_markdown",
    "supersede",
    "utc_now",
]


def utc_now() -> datetime:
    """The current time in UTC (the default clock)."""
    return datetime.now(UTC)


def record_id_for(record: dict[str, Any]) -> str:
    """ "EV-" + SHA-256 of the canonical record without its id, chain fields and timestamp (24 hex characters)."""
    body = {key: value for key, value in record.items() if key not in ("record_id", TIMESTAMP_FIELD, *CHAIN_FIELDS)}
    digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return RECORD_ID_PREFIX + digest[:RECORD_ID_HEX]


def build_decision_record(
    *,
    case_id: str,
    line: str | None,
    data_label: str,
    packet_sha256: dict[str, str | None],
    inputs_sha256: str | None,
    evidence_ledger: dict[str, Any] | None,
    contradictions: list[dict[str, Any]],
    contradiction_graph: dict[str, Any] | None,
    policy_result: dict[str, Any] | None,
    scenarios: list[dict[str, Any]],
    decision_stability: dict[str, Any] | None,
    open_items: list[dict[str, Any]],
    next_best_evidence: list[dict[str, Any]],
    versions: dict[str, Any],
    clock: Callable[[], datetime] = utc_now,
    supplementary_calculations: dict[str, Any] | None = None,
    not_evaluable_reason: str | None = None,
    supersedes_record_id: str | None = None,
    correction_reason: str | None = None,
) -> dict[str, Any]:
    """One decision record as plain JSON (see the module docstring); every part is passed already dumped.

    Raises ValueError when supersedes_record_id is set without a non-empty correction_reason.
    """
    if supersedes_record_id is not None and not (correction_reason or "").strip():
        raise ValueError("a record that supersedes another needs a correction reason")
    record: dict[str, Any] = {
        "record_type": RECORD_TYPE,
        "data_label": data_label,
        "notice": NOTICE,
        "case_id": case_id,
        "line": line,
        "packet_sha256": dict(packet_sha256),
        "inputs_sha256": inputs_sha256,
        "recommendation": None if policy_result is None else policy_result["recommendation"],
        "referral_targets": [] if policy_result is None else list(policy_result["referral_targets"]),
        "blocking_open": None if policy_result is None else policy_result["blocking_open"],
        "not_evaluable_reason": not_evaluable_reason,
        "policy_result": policy_result,
        "calculations": [] if policy_result is None else list(policy_result["calculations"]),
        "supplementary_calculations": dict(supplementary_calculations or {}),
        "evidence_ledger": evidence_ledger,
        "contradictions": contradictions,
        "contradiction_graph": contradiction_graph,
        "scenarios": scenarios,
        "decision_stability": decision_stability,
        "open_items": open_items,
        "next_best_evidence": next_best_evidence,
        "versions": versions,
        "reviewer": dict(EMPTY_REVIEWER),
        "supersedes_record_id": supersedes_record_id,
        "correction_reason": correction_reason,
        TIMESTAMP_FIELD: clock().isoformat(),
    }
    record["record_id"] = record_id_for(record)
    return record


def supersede(
    record: dict[str, Any],
    reason: str,
    updates: dict[str, Any] | None = None,
    reviewer: dict[str, Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    """A NEW record that replaces `record`: a copy with the updates (and reviewer action) applied, the old record's
    id in supersedes_record_id, the reason, a new timestamp and a new id. The old record is not touched."""
    if not reason.strip():
        raise ValueError("a correction needs a reason")
    refused = sorted(set(updates or {}) & set(IDENTITY_FIELDS))
    if refused:
        raise ValueError(f"a correction cannot change identity fields: {', '.join(refused)}")
    replacement = {key: copy.deepcopy(value) for key, value in record.items() if key not in CHAIN_FIELDS}
    replacement.update(copy.deepcopy(updates or {}))
    if reviewer is not None:
        replacement["reviewer"] = {**EMPTY_REVIEWER, **reviewer}
    replacement["supersedes_record_id"] = record["record_id"]
    replacement["correction_reason"] = reason
    replacement[TIMESTAMP_FIELD] = clock().isoformat()
    replacement["record_id"] = record_id_for(replacement)
    return replacement


def append_record(record: dict[str, Any], log: DecisionLog | Path | None = None) -> dict[str, Any]:
    """Append to the hash-chained log (default DEFAULT_RECORDS_PATH); returns the stored record.

    Raises ValueError when the record supersedes a record that is not in this log.
    """
    target = log if isinstance(log, DecisionLog) else DecisionLog(Path(log) if log else DEFAULT_RECORDS_PATH)
    superseded = record.get("supersedes_record_id")
    if superseded is not None and target.find(str(superseded)) is None:
        raise ValueError(f"record {superseded} is not in the log, so it cannot be superseded")
    return target.append(record)

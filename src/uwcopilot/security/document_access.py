"""Document-level permissions: what a principal may see of a file's review payload and of a packet's PDF.

    review = review_for_principal(review, principal, audit=log, store=store)  # needs view_file
    pdf = read_packet_for(principal, "PKT-C-024", store, audit=log)          # needs view_documents

Two permissions (security/rbac.py):
- view_file: the review payload at the human checkpoint (recommendation, findings, limits, tool statuses).
  Without it, nothing is shown: AccessDenied.
- view_documents: the packet's documents and extraction (page text, extracted fields, document checks,
  contradictions, field provenance, quoted excerpts). It needs a document-viewing role AND the packet access list
  (config/security.yaml). Without it the review payload is still shown, with every document part replaced by
  WITHHELD, and document_access says why and which keys were withheld.

What may be shown WITHOUT document permission is listed here, in one place, and everything else is withheld
(fail closed):
- REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT: the top-level keys agent.hitl.build_review_request returns today (ids, rule
  codes, numbers, statuses, the memo, limits) plus packet_id and packet_sha256. They stay visible because the
  reviewer needs them to act;
- DOCUMENT_TEXT_KEYS: keys that quote document or applicant text wherever they appear inside those visible parts
  (for example the excerpt of an injection hit). Withheld at any depth;
- any OTHER top-level key is withheld, including the document keys the packet intake will add (Phase 5 A, not
  built yet: extraction, document_checks, contradictions, field_provenance, page texts). A new key the reviewer
  needs without document permission must be added to REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT on purpose;
  tests/security/test_document_access.py runs a real agent and fails if build_review_request gains a key that is
  in neither list.

Which packet's access list applies (fail closed):
- only the review's OWN packet_id (set by whoever built the review from the packet) decides it. A packet_id passed
  by the caller (for example taken from a URL) can only confirm it: if the two differ, or the caller names a packet
  and the review names none, every document part is withheld;
- the id must be a plain packet id, and an id that differs from a listed packet only in case is refused
  (security/rbac.py), so a different spelling never falls back to the default roles;
- with store= given, the packet must be in the encrypted packet store and the review's packet_sha256 must equal the
  stored SHA-256, so the label is tied to the bytes the documents came from. The packet intake should pass it.

Every call writes an audit event when an AuditLog is given: view, or access_denied.
"""

from typing import Any

from uwcopilot.security.audit import AuditLog, record_access
from uwcopilot.security.config import SecurityConfig, is_plain_packet_id
from uwcopilot.security.packet_store import EncryptedPacketStore, PacketStoreError
from uwcopilot.security.rbac import Decision, Principal, Resource, authorize

WITHHELD = "[WITHHELD: no document permission for this packet]"
# What an audit event names instead of a packet id that is not a plain id (the text could be anything).
NOT_A_PACKET_ID = "not-a-plain-packet-id"
# The review keys of agent.hitl.build_review_request that hold no document content (ids, codes, numbers, statuses),
# plus the packet's id and SHA-256 (a hash reveals nothing of the PDF).
REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT: frozenset[str] = frozenset(
    {
        "application_id",
        "line",
        "data_label",
        "reference_recommendation",
        "memo_recommendation",
        "memo",
        "memo_status",
        "memo_check",
        "escalations",
        "referral_targets",
        "review_queue",
        "blocking_open",
        "required_reviewer_role",
        "authority_exposure",
        "approval_limits",
        "allowed_actions",
        "tool_results",
        "tool_rejections",
        "injection_hits",
        "not_evaluated",
        "llm",
        "stamp",
        "previous_rejections",
        "instructions",
        "packet_id",
        "packet_sha256",
    }
)
# The document keys the packet intake is specified to add (Phase 5 A). Listed so the docs and tests can name them;
# they are withheld because they are not in REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT, not because they are listed here.
DOCUMENT_CONTENT_KEYS: frozenset[str] = frozenset(
    {"extraction", "document_checks", "contradictions", "field_provenance", "page_texts", "documents"}
)
DOCUMENT_TEXT_KEYS: frozenset[str] = frozenset({"excerpt", "quote", "snippet", "page_text", "raw_text", "source_text"})


class AccessDenied(PermissionError):
    """authorize() refused; .decision holds the reason."""

    def __init__(self, decision: Decision) -> None:
        """Keep the decision so callers can show its reason."""
        super().__init__(decision.reason)
        self.decision = decision


def withhold_text(value: Any) -> Any:
    """A copy of value with every DOCUMENT_TEXT_KEYS entry replaced by WITHHELD, at any depth."""
    if isinstance(value, dict):
        return {
            key: (WITHHELD if key in DOCUMENT_TEXT_KEYS and item not in (None, "") else withhold_text(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [withhold_text(item) for item in value]
    return value


def document_keys_in(review: dict[str, Any]) -> list[str]:
    """The top-level keys of a review payload that may hold document content (every key not known to be safe)."""
    return sorted(key for key in review if key not in REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT)


def hide_document_content(review: dict[str, Any], decision: Decision, packet_id: str | None) -> dict[str, Any]:
    """The review payload with document content withheld unless decision allows it; adds document_access."""
    if decision.allowed:
        access = {"packet_id": packet_id, "allowed": True, "reason": decision.reason, "withheld": []}
        return {**review, "document_access": access}
    withheld = document_keys_in(review)
    hidden = {key: (WITHHELD if key in withheld else value) for key, value in review.items()}
    access = {"packet_id": packet_id, "allowed": False, "reason": decision.reason, "withheld": withheld}
    return {**withhold_text(hidden), "document_access": access}


def packet_resource_for(packet_id: object) -> Resource:
    """The resource an AUDIT EVENT names for a packet: the id when it is plain, else NOT_A_PACKET_ID.

    Only for the audit log (an id that is not plain could hold any text). Authorize on the real id, never on this.
    """
    if is_plain_packet_id(packet_id):
        return Resource("packet", str(packet_id))
    return Resource("packet", NOT_A_PACKET_ID)


def store_binding_problem(review: dict[str, Any], packet_id: str, store: EncryptedPacketStore) -> str | None:
    """Why the review's packet label does not match the packet store, or None when it does."""
    try:
        if not store.has(packet_id):
            return "the review's packet is not in the packet store; withheld"
        stored_sha256 = store.meta(packet_id).sha256
    except PacketStoreError as error:
        return f"the review's packet cannot be checked against the packet store ({error}); withheld"
    if review.get("packet_sha256") != stored_sha256:
        return "the review's packet_sha256 does not match the stored packet; withheld"
    return None


def review_for_principal(
    review: dict[str, Any],
    principal: Principal,
    packet_id: str | None = None,
    audit: AuditLog | None = None,
    config: SecurityConfig | None = None,
    store: EncryptedPacketStore | None = None,
) -> dict[str, Any]:
    """The review payload this principal may see. AccessDenied without view_file. The input is never changed.

    The packet whose access list applies is the review's own "packet_id". packet_id (optional) is the packet the
    caller expects: when it differs from the review's, or the review names none, document content is withheld.
    A review with no packet_id is a structured application: nothing is withheld and document_access.allowed is None
    (not applicable), unless it holds document keys, which are then withheld (no packet, no access list to grant
    them). store (optional): also require the packet to be stored with the review's packet_sha256.
    """
    file_id = str(review.get("application_id") or "unknown")
    file_resource = Resource("file", file_id)
    file_decision = authorize(principal, "view_file", file_resource, config)
    if audit is not None:
        record_access(audit, principal, "view_file", file_resource, file_decision)
    if not file_decision.allowed:
        raise AccessDenied(file_decision)
    named = review.get("packet_id")
    if packet_id is not None and named != packet_id:
        mismatch = Decision(False, "the packet id given does not match the review's own packet_id; withheld")
        if audit is not None:
            record_access(audit, principal, "view_documents", packet_resource_for(packet_id), mismatch)
        return hide_document_content(review, mismatch, None if named is None else str(named))
    if named is None:
        if document_keys_in(review):
            no_packet = Decision(False, "the review holds document content but names no packet; withheld")
            return hide_document_content(review, no_packet, None)
        not_applicable = {
            "packet_id": None,
            "allowed": None,
            "reason": "structured application: no packet documents",
            "withheld": [],
        }
        return {**review, "document_access": not_applicable}
    packet_resource = packet_resource_for(named)
    document_decision = authorize(principal, "view_documents", Resource("packet", str(named)), config)
    if document_decision.allowed and store is not None:
        problem = store_binding_problem(review, str(named), store)
        if problem is not None:
            document_decision = Decision(False, problem)
    if audit is not None:
        record_access(audit, principal, "view_documents", packet_resource, document_decision)
    return hide_document_content(review, document_decision, str(named))


def read_packet_for(
    principal: Principal,
    packet_id: str,
    store: EncryptedPacketStore,
    audit: AuditLog | None = None,
    config: SecurityConfig | None = None,
) -> bytes:
    """The decrypted packet PDF, only for a principal allowed to view its documents (AccessDenied otherwise).

    The store is not touched (nothing is decrypted) when access is denied.
    """
    decision = authorize(principal, "view_documents", Resource("packet", packet_id), config)
    if audit is not None:
        audited = packet_resource_for(packet_id)
        record_access(audit, principal, "view_documents", audited, decision, {"what": "packet_pdf"})
    if not decision.allowed:
        raise AccessDenied(decision)
    return store.get(packet_id)


def audit_events_for(
    principal: Principal, audit: AuditLog, config: SecurityConfig | None = None
) -> list[dict[str, Any]]:
    """The audit events, only for a principal allowed to view the audit log; reading them is itself logged."""
    resource = Resource("audit_log", audit.path.name)
    decision = authorize(principal, "view_audit_log", resource, config)
    record_access(audit, principal, "view_audit_log", resource, decision)
    if not decision.allowed:
        raise AccessDenied(decision)
    return audit.events()

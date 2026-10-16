"""Tests for packet access lists (config/security.yaml) and document-level permissions (security/document_access.py).

Packets and applications are SYNTHETIC. The page text below is made up for the test. Roles are CLAIMED in this
demo; these tests show the rules, not identity.
"""

import hashlib
from pathlib import Path

import pytest

from agent.agent_helpers import committed_by_scenario, make_deps, memory_agent
from uwcopilot.agent.runner import start_run
from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.security.audit import AuditLog
from uwcopilot.security.config import (
    DEFAULT_SECURITY_CONFIG,
    SecurityConfig,
    SecurityConfigError,
    load_security_config,
    parse_security_config,
)
from uwcopilot.security.crypto import Encryptor, generate_key
from uwcopilot.security.document_access import (
    DOCUMENT_CONTENT_KEYS,
    NOT_A_PACKET_ID,
    REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT,
    WITHHELD,
    AccessDenied,
    audit_events_for,
    read_packet_for,
    review_for_principal,
)
from uwcopilot.security.packet_store import EncryptedPacketStore
from uwcopilot.security.rbac import Principal, Resource, authorize

UW, SENIOR, VALIDATOR, ADMIN = "underwriter", "senior_underwriter", "model_validator", "admin"
RESTRICTED = "PKT-C-024"  # SYNTHETIC packet ids
NAMED_ONLY = "PKT-I-007"
OPEN_PACKET = "PKT-C-001"
PAGE_TEXT = "SYNTHETIC bank statement page 3: Northwind Imaginary Bakery LLC, ending balance 41,250.00"
PDF = b"%PDF-1.4\n% SYNTHETIC test packet\n%%EOF\n"
PDF_SHA256 = hashlib.sha256(PDF).hexdigest()


def acl_config() -> SecurityConfig:
    """Default roles for most packets; one packet for senior underwriters (plus one named underwriter); one packet
    for one named principal only."""
    return SecurityConfig.model_validate(
        {
            "version": 1,
            "senior_approval_above": {"credit": 150_000, "insurance": 5_000_000, "startup_credit": 0},
            "packet_acl": {
                "default_roles": [UW, SENIOR],
                "packets": {
                    RESTRICTED: {"roles": [SENIOR], "principals": ["uw.named"]},
                    NAMED_ONLY: {"principals": ["uw.named"]},
                },
            },
        }
    )


def can_view(principal_id: str, role: str, packet_id: str) -> bool:
    """authorize(view_documents) on a packet with the ACL config."""
    return authorize(
        Principal(principal_id, role), "view_documents", Resource("packet", packet_id), acl_config()
    ).allowed


def packet_review(packet_id: str | None = RESTRICTED) -> dict:
    """A review payload as the packet intake (Phase 5 A, not built) is specified to extend it. SYNTHETIC.

    It names its own packet (packet_id, packet_sha256), as the intake will; packet_id=None leaves both out."""
    packet = {} if packet_id is None else {"packet_id": packet_id, "packet_sha256": PDF_SHA256}
    return {
        **packet,
        "application_id": "APP-C-924",
        "line": "credit",
        "data_label": "SYNTHETIC",
        "reference_recommendation": "refer",
        "allowed_actions": ["refer", "request_more_information"],
        "escalations": [{"rule": "R-NBC-9", "message": "embedded instruction in a document"}],
        "injection_hits": [{"field": "doc:bank:p3", "patterns": ["approval_claim"], "excerpt": "already approved"}],
        "extraction": {"fields": {"ending_balance": {"value": 41250.0, "page_text": PAGE_TEXT}}},
        "document_checks": [{"check": "totals_reconcile", "status": "fail", "quote": PAGE_TEXT}],
        "contradictions": [{"between": ["bank:p3", "tax:p1"], "snippet": PAGE_TEXT}],
        "field_provenance": {"ending_balance": {"doc_id": "bank", "page": 3}},
        "page_texts": {"bank:p3": PAGE_TEXT},
    }


# ---------------------------------------------------------------------------------------------------------------
# config/security.yaml
# ---------------------------------------------------------------------------------------------------------------


def test_the_committed_security_config_loads() -> None:
    assert DEFAULT_SECURITY_CONFIG == CONFIG_DIR / "security.yaml"
    config = load_security_config()
    assert config.packet_acl.default_roles == [UW, SENIOR]
    assert config.packet_acl.packets == {}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"packet_acl": {"default_roles": [UW, VALIDATOR]}}, "may never view packet documents"),
        ({"packet_acl": {"default_roles": [ADMIN]}}, "may never view packet documents"),
        ({"packet_acl": {"packets": {RESTRICTED: {"roles": [ADMIN]}}}}, "may never view packet documents"),
        ({"packet_acl": {"packets": {RESTRICTED: {"roles": ["superuser"]}}}}, "unknown role"),
        ({"packet_acl": {"packets": {"../escape": {"roles": [UW]}}}}, "packet id"),
        ({"packet_acl": {"packets": {"PKT..C": {"roles": [UW]}}}}, "must not contain '..'"),
        ({"packet_acl": {"packets": {RESTRICTED: {"roles": [SENIOR]}, "pkt-c-024": {"roles": [UW]}}}}, "only in case"),
        ({"packet_acl": {"packets": {RESTRICTED: {"principals": ["has space"]}}}}, "principal id"),
        ({"packet_acl": {"packets": {RESTRICTED: {"principals": ["*"]}}}}, "principal id"),
        ({"packet_acl": {"packets": {RESTRICTED: {"everyone": True}}}}, "Extra inputs"),
        ({"packet_acl": {"public": True}}, "Extra inputs"),
        ({"allow_all": True}, "Extra inputs"),
        ({"senior_approval_above": {"credit": -1}}, "zero or more"),
        ({"senior_approval_above": {"credit": float("nan")}}, "finite number"),
        ({"senior_approval_above": {"credit": float("inf")}}, "finite number"),
        ({"senior_approval_above": {}}, "at least one business line"),
        ({"version": 2}, "version must be 1"),
    ],
)
def test_a_config_mistake_fails_loudly(change: dict, message: str) -> None:
    document = {"version": 1, "senior_approval_above": {"credit": 150_000}, **change}
    with pytest.raises(SecurityConfigError, match=message):
        parse_security_config(document)


@pytest.mark.parametrize("value", [".nan", ".inf", ".NaN"])
def test_a_yaml_nan_or_inf_threshold_is_refused(tmp_path: Path, value: str) -> None:
    """YAML spells NaN and infinity .nan / .inf; with NaN every comparison is False, so it would allow any amount."""
    path = tmp_path / "security.yaml"
    path.write_text(f"version: 1\nsenior_approval_above:\n  credit: {value}\n", encoding="utf-8")
    with pytest.raises(SecurityConfigError, match="finite number"):
        load_security_config(path)


def test_a_missing_or_non_mapping_config_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SecurityConfigError, match="not found"):
        load_security_config(tmp_path / "missing.yaml")
    listed = tmp_path / "list.yaml"
    listed.write_text("- underwriter\n", encoding="utf-8")
    with pytest.raises(SecurityConfigError, match="YAML mapping"):
        load_security_config(listed)


# ---------------------------------------------------------------------------------------------------------------
# Packet access lists (authorize view_documents)
# ---------------------------------------------------------------------------------------------------------------


def test_packets_without_an_entry_use_the_default_roles() -> None:
    assert can_view("uw.any", UW, OPEN_PACKET)
    assert can_view("senior.any", SENIOR, OPEN_PACKET)
    assert not can_view("val.any", VALIDATOR, OPEN_PACKET)
    assert not can_view("adm.any", ADMIN, OPEN_PACKET)


def test_a_packet_entry_replaces_the_default() -> None:
    assert not can_view("uw.any", UW, RESTRICTED)  # the default would allow an underwriter; the entry does not
    assert can_view("senior.any", SENIOR, RESTRICTED)  # granted by role
    assert can_view("uw.named", UW, RESTRICTED)  # granted by principal id


def test_a_packet_for_named_principals_only() -> None:
    assert can_view("uw.named", UW, NAMED_ONLY)
    assert can_view("uw.named", SENIOR, NAMED_ONLY)
    assert not can_view("senior.any", SENIOR, NAMED_ONLY)
    assert not can_view("uw.other", UW, NAMED_ONLY)


@pytest.mark.parametrize("role", [VALIDATOR, ADMIN])
def test_a_named_principal_still_needs_a_document_role(role: str) -> None:
    """Being on a packet's list does not lift the role check: a validator or admin never sees documents."""
    assert not can_view("uw.named", role, NAMED_ONLY)
    assert not can_view("uw.named", role, RESTRICTED)


@pytest.mark.parametrize(
    "spelling",
    ["pkt-c-024", "Pkt-C-024", "PKT-C-024 ", " PKT-C-024", "PKT-C-024\n", "PKT-C-024/../PKT-C-024", "PKT-C-024/x"],
)
@pytest.mark.parametrize("role", [UW, SENIOR])
def test_another_spelling_of_a_restricted_packet_never_gets_the_default_roles(spelling: str, role: str) -> None:
    """Fail closed: a case variant or an id that is not plain is denied, not treated as an unlisted packet."""
    decision = authorize(Principal("someone", role), "view_documents", Resource("packet", spelling), acl_config())
    assert not decision.allowed
    assert "plain packet id" in decision.reason or "only in case" in decision.reason


def test_view_documents_needs_a_packet_resource() -> None:
    senior = Principal("senior.any", SENIOR)
    assert not authorize(senior, "view_documents", None, acl_config()).allowed
    assert not authorize(senior, "view_documents", Resource("file", OPEN_PACKET), acl_config()).allowed
    assert not authorize(senior, "view_documents", Resource("packet", ""), acl_config()).allowed


def test_denial_reasons_do_not_echo_the_principal_id() -> None:
    decision = authorize(Principal("uw.secret-id", UW), "view_documents", Resource("packet", RESTRICTED), acl_config())
    assert not decision.allowed and "uw.secret-id" not in decision.reason


# ---------------------------------------------------------------------------------------------------------------
# The review payload hides document content from principals without permission
# ---------------------------------------------------------------------------------------------------------------


def test_a_permitted_reviewer_sees_the_document_content() -> None:
    review = packet_review()
    shown = review_for_principal(review, Principal("senior.any", SENIOR), RESTRICTED, config=acl_config())
    assert shown["page_texts"] == review["page_texts"] and shown["extraction"] == review["extraction"]
    assert shown["injection_hits"][0]["excerpt"] == "already approved"
    assert shown["document_access"]["allowed"] is True and shown["document_access"]["withheld"] == []


def test_a_reviewer_without_permission_sees_the_file_but_not_the_documents() -> None:
    review = packet_review()
    shown = review_for_principal(review, Principal("uw.other", UW), RESTRICTED, config=acl_config())
    assert PAGE_TEXT not in repr(shown) and "already approved" not in repr(shown)
    for key in ("extraction", "document_checks", "contradictions", "field_provenance", "page_texts"):
        assert shown[key] == WITHHELD
    assert shown["injection_hits"] == [{"field": "doc:bank:p3", "patterns": ["approval_claim"], "excerpt": WITHHELD}]
    # what the reviewer needs to act stays visible
    for key in ("application_id", "reference_recommendation", "allowed_actions", "escalations"):
        assert shown[key] == review[key]
    access = shown["document_access"]
    assert access["allowed"] is False and access["packet_id"] == RESTRICTED
    assert access["withheld"] == sorted(
        ["contradictions", "document_checks", "extraction", "field_provenance", "page_texts"]
    )
    assert review == packet_review()  # the input is not changed


def test_unknown_review_keys_are_withheld_by_default() -> None:
    review = {**packet_review(), "brand_new_key": PAGE_TEXT}
    shown = review_for_principal(review, Principal("uw.other", UW), RESTRICTED, config=acl_config())
    assert shown["brand_new_key"] == WITHHELD and "brand_new_key" in shown["document_access"]["withheld"]


def test_the_access_list_comes_from_the_reviews_own_packet_id() -> None:
    review = packet_review()
    shown = review_for_principal(review, Principal("uw.other", UW), config=acl_config())
    assert shown["page_texts"] == WITHHELD and shown["document_access"]["packet_id"] == RESTRICTED
    assert shown["packet_id"] == RESTRICTED and shown["packet_sha256"] == PDF_SHA256  # ids and hashes stay visible
    permitted = review_for_principal(review, Principal("senior.any", SENIOR), config=acl_config())
    assert permitted["page_texts"] == review["page_texts"]


def test_a_caller_packet_id_cannot_replace_the_reviews_own(tmp_path: Path) -> None:
    """A packet id from the caller (say, a URL) only confirms the review's; naming an open packet unlocks nothing."""
    log = AuditLog(tmp_path / "audit.jsonl")
    for principal in (Principal("uw.other", UW), Principal("senior.any", SENIOR)):
        shown = review_for_principal(packet_review(), principal, OPEN_PACKET, log, acl_config())
        assert PAGE_TEXT not in repr(shown) and "already approved" not in repr(shown)
        assert shown["document_access"]["allowed"] is False
        assert "does not match the review's own packet_id" in shown["document_access"]["reason"]
        assert shown["document_access"]["packet_id"] == RESTRICTED  # the packet the content belongs to
    denials = [event for event in log.events() if event["action"] == "view_documents"]
    assert [(event["event_type"], event["resource"]["id"]) for event in denials] == [
        ("access_denied", OPEN_PACKET),
        ("access_denied", OPEN_PACKET),
    ]


def test_a_caller_packet_id_for_a_review_that_names_none_is_withheld() -> None:
    shown = review_for_principal(packet_review(None), Principal("senior.any", SENIOR), RESTRICTED, config=acl_config())
    assert shown["page_texts"] == WITHHELD and shown["document_access"]["allowed"] is False


def test_a_review_labelled_with_another_spelling_is_withheld(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    for spelling in ("pkt-c-024", "PKT-C-024\n"):
        shown = review_for_principal(packet_review(spelling), Principal("uw.other", UW), audit=log, config=acl_config())
        assert PAGE_TEXT not in repr(shown) and shown["document_access"]["allowed"] is False
    resources = [event["resource"]["id"] for event in log.events() if event["action"] == "view_documents"]
    assert resources == ["pkt-c-024", NOT_A_PACKET_ID]  # an id that is not plain is never copied into the log


def test_document_content_without_a_packet_id_is_withheld() -> None:
    """Fail closed: with no packet there is no access list that could grant the documents."""
    shown = review_for_principal(packet_review(None), Principal("senior.any", SENIOR), config=acl_config())
    assert shown["page_texts"] == WITHHELD and shown["document_access"]["allowed"] is False
    assert "names no packet" in shown["document_access"]["reason"]


def test_with_the_store_the_review_must_cite_the_stored_packet(tmp_path: Path) -> None:
    """store=: the review's packet must be stored, with the packet_sha256 the review cites."""
    store = EncryptedPacketStore(tmp_path / "packets", Encryptor([generate_key().encode("ascii")]))
    senior = Principal("senior.any", SENIOR)
    missing = review_for_principal(packet_review(), senior, store=store, config=acl_config())
    assert missing["page_texts"] == WITHHELD and "not in the packet store" in missing["document_access"]["reason"]
    store.put(RESTRICTED, PDF)
    shown = review_for_principal(packet_review(), senior, store=store, config=acl_config())
    assert shown["document_access"]["allowed"] is True and shown["page_texts"] == packet_review()["page_texts"]
    other = {**packet_review(), "packet_sha256": "0" * 64}
    mismatched = review_for_principal(other, senior, store=store, config=acl_config())
    assert mismatched["page_texts"] == WITHHELD
    assert "packet_sha256 does not match" in mismatched["document_access"]["reason"]
    denied = review_for_principal(packet_review(), Principal("uw.other", UW), store=store, config=acl_config())
    assert "not on the access list" in denied["document_access"]["reason"]  # the ACL is still checked first


@pytest.mark.parametrize("role", [VALIDATOR, ADMIN])
def test_roles_without_view_file_see_nothing(role: str) -> None:
    with pytest.raises(AccessDenied, match=f"{role} may not view_file"):
        review_for_principal(packet_review(), Principal("someone", role), RESTRICTED, config=acl_config())


def test_document_keys_named_by_the_spec_are_never_shown_without_permission() -> None:
    assert not DOCUMENT_CONTENT_KEYS & REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT


def test_a_real_review_payload_is_fully_classified(tmp_path: Path) -> None:
    """build_review_request's keys are all known, so a new key (e.g. from the packet intake) fails this test until
    someone decides whether it holds document content."""
    application = committed_by_scenario("injection_already_approved")
    outcome = start_run(memory_agent(make_deps(tmp_path)), application.model_dump(mode="json"), "t-docs")
    review = outcome.review_request
    assert review is not None and review["injection_hits"]
    assert set(review) <= REVIEW_KEYS_WITHOUT_DOCUMENT_CONTENT
    structured = review_for_principal(review, Principal("uw.any", UW), config=acl_config())
    assert structured["document_access"]["allowed"] is None  # structured application: not applicable
    assert structured["injection_hits"] == review["injection_hits"]
    # the same payload, if it came from a packet the reviewer may not open, keeps everything but the excerpts
    from_packet = {**review, "packet_id": RESTRICTED}
    hidden = review_for_principal(from_packet, Principal("uw.other", UW), config=acl_config())
    assert hidden["document_access"]["withheld"] == []
    assert all(hit["excerpt"] == WITHHELD for hit in hidden["injection_hits"])
    assert hidden["reference_recommendation"] == review["reference_recommendation"]


def test_review_access_is_audited(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    review_for_principal(packet_review(), Principal("senior.any", SENIOR), RESTRICTED, log, acl_config())
    review_for_principal(packet_review(), Principal("uw.other", UW), RESTRICTED, log, acl_config())
    with pytest.raises(AccessDenied):
        review_for_principal(packet_review(), Principal("adm.1", ADMIN), RESTRICTED, log, acl_config())
    summary = [(event["actor"]["principal_id"], event["action"], event["event_type"]) for event in log.events()]
    assert summary == [
        ("senior.any", "view_file", "view"),
        ("senior.any", "view_documents", "view"),
        ("uw.other", "view_file", "view"),
        ("uw.other", "view_documents", "access_denied"),
        ("adm.1", "view_file", "access_denied"),
    ]
    assert PAGE_TEXT not in log.path.read_text(encoding="utf-8") and log.verify() == []


# ---------------------------------------------------------------------------------------------------------------
# Packet PDFs and the audit log itself
# ---------------------------------------------------------------------------------------------------------------


def test_packet_pdfs_are_decrypted_only_for_permitted_principals(tmp_path: Path) -> None:
    store = EncryptedPacketStore(tmp_path / "packets", Encryptor([generate_key().encode("ascii")]))
    store.put(RESTRICTED, PDF)
    log = AuditLog(tmp_path / "audit.jsonl")
    assert read_packet_for(Principal("senior.any", SENIOR), RESTRICTED, store, log, acl_config()) == PDF
    with pytest.raises(AccessDenied, match="not on the access list"):
        read_packet_for(Principal("uw.other", UW), RESTRICTED, store, log, acl_config())
    with pytest.raises(AccessDenied):  # denied before the store is read: the packet does not even exist
        read_packet_for(Principal("val.1", VALIDATOR), "PKT-C-404", store, log, acl_config())
    with pytest.raises(AccessDenied, match="only in case"):
        read_packet_for(Principal("uw.other", UW), "pkt-c-024", store, log, acl_config())
    with pytest.raises(AccessDenied, match="plain packet id"):
        read_packet_for(Principal("senior.any", SENIOR), "PKT-C-024\n", store, log, acl_config())
    events = log.events()
    assert [event["event_type"] for event in events] == ["view"] + ["access_denied"] * 4
    assert all(event["details"] == {"what": "packet_pdf"} for event in events)
    assert events[-1]["resource"]["id"] == NOT_A_PACKET_ID


def test_only_an_admin_reads_the_audit_log_and_the_read_is_logged(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    with pytest.raises(AccessDenied, match="only admin may"):
        audit_events_for(Principal("senior.any", SENIOR), log, acl_config())
    events = audit_events_for(Principal("adm.1", ADMIN), log, acl_config())
    assert [event["event_type"] for event in events] == ["access_denied", "view"]
    assert events[-1]["action"] == "view_audit_log" and log.verify() == []

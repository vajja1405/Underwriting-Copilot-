"""Tests for the authorization module (security/rbac.py) and the checkpoint's role refusal (agent/hitl.py).

The expected table below is written out by hand, independently of rbac.PERMISSIONS, so a change to the policy
must change both. Roles are CLAIMED in this demo; these tests show the rules, not identity.
"""

from pathlib import Path

import pytest

from agent.agent_helpers import SENIOR_REFER, committed_by_scenario, credit_app, make_deps, memory_agent
from uwcopilot.agent.applications import screened_parties
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.hitl import APPROVAL_LIMITS, validate_decision
from uwcopilot.agent.policy import PolicyInputs, ScreeningOutcome, evaluate_policy
from uwcopilot.agent.runner import resume_run, start_run
from uwcopilot.agent.vocab import ACCESS_ROLES, REVIEWER_ACTIONS
from uwcopilot.security.config import SecurityConfig, default_security_config
from uwcopilot.security.rbac import (
    ACTIONS,
    AGENT_PRINCIPAL,
    NON_DECIDING_ROLES,
    PERMISSIONS,
    SYSTEM_ROLE,
    UNKNOWN_ROLE,
    WEB_ROLE_TO_ACCESS_ROLE,
    Principal,
    Resource,
    access_roles_for_web_roles,
    acting_reviewer_role,
    authorize,
    checkpoint_role_refusal,
)
from uwcopilot.web.settings import ROLES as WEB_ROLES

UW, SENIOR, VALIDATOR, ADMIN = "underwriter", "senior_underwriter", "model_validator", "admin"
# The authorization table of docs/security.md. approve_file is checked with a SMALL credit file (within the
# underwriter threshold) and view_documents with a packet that has no entry of its own (default roles apply).
EXPECTED_TABLE: dict[str, set[str]] = {
    "view_file": {UW, SENIOR},
    "view_documents": {UW, SENIOR},
    "run_agent": {UW, SENIOR},
    "approve_file": {UW, SENIOR},
    "decline_file": {SENIOR},
    "refer_file": {UW, SENIOR},
    "request_more_information": {UW, SENIOR},
    "promote_model": {VALIDATOR},
    "sign_migration_certificate": {VALIDATOR},
    "manage_permissions": {ADMIN},
    "view_audit_log": {ADMIN},
}
SMALL_CREDIT_FILE = Resource("file", "APP-C-001", line="credit", amount=50_000)
UNLISTED_PACKET = Resource("packet", "PKT-C-001")


def config() -> SecurityConfig:
    """A config like the committed one, built in the test so the file's later edits do not change these tests."""
    return SecurityConfig.model_validate(
        {
            "version": 1,
            "senior_approval_above": {"credit": 150_000, "insurance": 5_000_000, "startup_credit": 0},
            "packet_acl": {"default_roles": [UW, SENIOR], "packets": {}},
        }
    )


def resource_for(action: str) -> Resource:
    """The resource each action of the matrix is checked against."""
    if action == "approve_file":
        return SMALL_CREDIT_FILE
    if action == "view_documents":
        return UNLISTED_PACKET
    return Resource("file", "APP-C-001")


def principal(role: str, principal_id: str = "person.1") -> Principal:
    """A claimed principal."""
    return Principal(principal_id, role)


def test_the_expected_table_covers_every_action() -> None:
    assert set(EXPECTED_TABLE) == set(ACTIONS) == set(PERMISSIONS)


@pytest.mark.parametrize("action", sorted(EXPECTED_TABLE))
@pytest.mark.parametrize("role", ACCESS_ROLES)
def test_authorization_matrix(role: str, action: str) -> None:
    decision = authorize(principal(role), action, resource_for(action), config())
    assert decision.allowed is (role in EXPECTED_TABLE[action]), decision.reason
    assert decision.reason


@pytest.mark.parametrize("action", sorted(EXPECTED_TABLE))
@pytest.mark.parametrize("role", [SYSTEM_ROLE, UNKNOWN_ROLE])
def test_the_agent_and_unknown_actors_are_never_authorized(role: str, action: str) -> None:
    assert not authorize(principal(role), action, resource_for(action), config()).allowed
    assert not authorize(AGENT_PRINCIPAL, action, resource_for(action), config()).allowed


@pytest.mark.parametrize("action", ["approve_file", "decline_file", "refer_file", "promote_model"])
def test_admin_can_never_approve_decline_or_promote(action: str) -> None:
    for amount in (1, 50_000, 10_000_000):
        resource = Resource("file", "APP-C-001", line="credit", amount=amount)
        decision = authorize(principal(ADMIN), action, resource, config())
        assert not decision.allowed and "admin may not" in decision.reason


def test_model_validator_promotes_but_never_decides_files() -> None:
    validator = principal(VALIDATOR)
    assert authorize(validator, "promote_model", Resource("model", "insurance_frequency_lgbm"), config()).allowed
    assert authorize(validator, "sign_migration_certificate", Resource("certificate", "a_vs_b"), config()).allowed
    for action in ("approve_file", "decline_file", "refer_file", "request_more_information", "view_documents"):
        assert not authorize(validator, action, resource_for(action), config()).allowed


def test_unknown_actions_are_denied() -> None:
    decision = authorize(principal(SENIOR), "delete_everything", None, config())
    assert not decision.allowed and "unknown action" in decision.reason


@pytest.mark.parametrize(
    ("line", "amount", "underwriter_allowed"),
    [
        ("credit", 150_000, True),  # exactly at the threshold: allowed (hitl refuses only above the limit)
        ("credit", 150_000.01, False),
        ("insurance", 5_000_000, True),
        ("insurance", 5_000_001, False),
        ("startup_credit", 10_000, False),  # NSP-9.1: only a senior underwriter approves startup files
    ],
)
def test_only_a_senior_underwriter_approves_above_the_configured_amount(
    line: str, amount: float, underwriter_allowed: bool
) -> None:
    resource = Resource("file", "APP-X", line=line, amount=amount)
    underwriter = authorize(principal(UW), "approve_file", resource, config())
    assert underwriter.allowed is underwriter_allowed, underwriter.reason
    if not underwriter_allowed:
        assert "only a senior_underwriter may approve" in underwriter.reason
    assert authorize(principal(SENIOR), "approve_file", resource, config()).allowed


def test_approval_needs_a_known_line_and_a_positive_amount() -> None:
    senior = principal(SENIOR)
    assert not authorize(senior, "approve_file", None, config()).allowed
    assert not authorize(senior, "approve_file", Resource("file", "A", line="credit"), config()).allowed
    assert not authorize(senior, "approve_file", Resource("file", "A", line="auto", amount=10), config()).allowed
    assert not authorize(senior, "approve_file", Resource("file", "A", line="credit", amount=0), config()).allowed


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("role", [UW, SENIOR])
def test_approval_needs_a_finite_amount(role: str, amount: float) -> None:
    """NaN fails both '<= 0' and '> threshold', so without an explicit check it would be allowed."""
    decision = authorize(principal(role), "approve_file", Resource("file", "A", line="credit", amount=amount), config())
    assert not decision.allowed and "finite" in decision.reason


def test_the_threshold_comes_from_the_config() -> None:
    stricter = config().model_copy(update={"senior_approval_above": {"credit": 10_000.0}})
    resource = Resource("file", "A", line="credit", amount=50_000)
    assert authorize(principal(UW), "approve_file", resource, config()).allowed
    assert not authorize(principal(UW), "approve_file", resource, stricter).allowed


def test_committed_thresholds_mirror_the_checkpoint_limits() -> None:
    committed = default_security_config().senior_approval_above
    assert set(committed) == set(APPROVAL_LIMITS)
    for line, limits in APPROVAL_LIMITS.items():
        assert committed[line] == limits["underwriter"], line


def test_principals_are_validated() -> None:
    with pytest.raises(ValueError, match="principal id"):
        Principal("", UW)
    with pytest.raises(ValueError, match="principal id"):
        Principal("has space", UW)
    with pytest.raises(ValueError, match="unknown role"):
        Principal("p.1", "superuser")
    with pytest.raises(ValueError, match="unknown role source"):
        Principal("p.1", UW, "sso")  # type: ignore[arg-type]
    assert Principal("p.1", UW).role_source == "claimed"  # the honest default: roles are claimed in this demo


# ---------------------------------------------------------------------------------------------------------------
# The human checkpoint refuses decisions from model_validator and admin
# ---------------------------------------------------------------------------------------------------------------


def clean_policy(application):
    """The reference policy result with clear screening."""
    screening = [ScreeningOutcome(party_id=party.party_id, status="clear") for party in screened_parties(application)]
    return evaluate_policy(application, PolicyInputs(screening=screening))


def raw_decision(action: str, role: str) -> dict:
    """A resume value."""
    return {"action": action, "reviewer_id": "person.1", "role": role, "rationale": "Reviewed the whole file."}


def test_checkpoint_accepts_an_underwriter_on_the_same_file() -> None:
    application = credit_app()
    accepted, errors = validate_decision(raw_decision("approve", UW), clean_policy(application), application)
    assert errors == [] and accepted is not None


@pytest.mark.parametrize("action", REVIEWER_ACTIONS)
@pytest.mark.parametrize("role", NON_DECIDING_ROLES)
def test_checkpoint_refuses_model_validator_and_admin_decisions(role: str, action: str) -> None:
    application = credit_app()
    accepted, errors = validate_decision(raw_decision(action, role), clean_policy(application), application)
    assert accepted is None
    assert len(errors) == 1 and errors[0].startswith("role refused:") and f"{role} may not" in errors[0]


@pytest.mark.parametrize("role", [" admin ", "ADMIN", "Model_Validator"])
def test_checkpoint_refusal_ignores_case_and_spaces(role: str) -> None:
    application = credit_app()
    accepted, errors = validate_decision(raw_decision("refer", role), clean_policy(application), application)
    assert accepted is None and errors[0].startswith("role refused:")


def test_role_refusal_leaves_other_values_to_the_schema() -> None:
    assert checkpoint_role_refusal("approve") is None
    assert checkpoint_role_refusal({"role": "underwriter"}) is None
    assert checkpoint_role_refusal({"role": "superuser"}) is None  # unknown: the schema refuses it
    assert checkpoint_role_refusal({"role": ["admin"]}) is None


def test_the_graph_refuses_an_admin_and_records_nothing_until_a_reviewer_decides(tmp_path: Path) -> None:
    application = committed_by_scenario("clean_term_dental")
    agent = memory_agent(make_deps(tmp_path))
    start_run(agent, application.model_dump(mode="json"), "t-admin")
    admin_approve = {**raw_decision("approve", ADMIN), "reviewer_id": "adm.1"}
    waiting = resume_run(agent, "t-admin", admin_approve)
    assert waiting.status == "awaiting_review"
    [rejected] = waiting.state["rejected_decisions"]
    assert rejected["errors"][0].startswith("role refused:")
    assert DecisionLog(tmp_path / "decision_records.jsonl").records() == []
    done = resume_run(agent, "t-admin", SENIOR_REFER)
    assert done.state["status"] == "referred_by_human"


# ---------------------------------------------------------------------------------------------------------------
# Web adoption helpers (the web does not use them yet; docs/security.md "Web adoption")
# ---------------------------------------------------------------------------------------------------------------


def test_every_web_role_is_mapped_or_is_founder() -> None:
    assert set(WEB_ROLES) == {*WEB_ROLE_TO_ACCESS_ROLE, "founder"}


def test_web_roles_map_onto_access_roles() -> None:
    assert access_roles_for_web_roles(["founder"]) == ()
    senior_roles = access_roles_for_web_roles(["founder", "lender_reviewer", "lender_senior"])
    assert senior_roles == (UW, SENIOR) and acting_reviewer_role(senior_roles) == SENIOR
    assert access_roles_for_web_roles(["lender_reviewer"]) == (UW,)
    admin_roles = access_roles_for_web_roles(["founder", "admin"])
    assert admin_roles == (ADMIN,) and acting_reviewer_role(admin_roles) is None

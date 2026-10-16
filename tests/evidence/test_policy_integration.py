"""Phase 2 document checks inside the reference policy (agent/policy.py PolicyInputs.document_checks).

1. Regression: with the default (no document checks) the policy output for every committed SYNTHETIC application
   (the 65 eval applications and the startup examples) is byte-identical to the snapshot recorded BEFORE the field
   existed (fixtures/policy_snapshot_before_document_checks.json.gz, see evidence_snapshot_helpers.py).
2. Each confirmed document check raises its finding: the trigger, sections, reviewers and blocking flag of
   agent.policy.DOCUMENT_CHECK_RULES. A resolved flag raises nothing; other check ids are ignored.
"""

import pytest
from evidence_snapshot_helpers import (
    committed_applications,
    policy_outputs,
    read_snapshot,
    variant_inputs,
)

from uwcopilot.agent.applications import AnyApplication, canonical_json, inputs_sha256, parse_application
from uwcopilot.agent.policy import (
    NAMES_DIFFER_BETWEEN_STATEMENTS,
    DocumentCheckFlag,
    InjectionFlag,
    PolicyInputs,
    PolicyResult,
    evaluate_policy,
)
from uwcopilot.agent.vocab import FRAUD_OPERATIONS, KYC_OPERATIONS, SENIOR_UNDERWRITER, SPECIAL_INVESTIGATIONS
from uwcopilot.agent.vocab import UNDERWRITING_MANAGER as UW_MANAGER
from uwcopilot.evidence.state import evaluate, from_application

# Applications the reference policy approves with designed screening (checked in baseline()).
CREDIT_APPROVE = "APP-C-001"
INSURANCE_APPROVE = "APP-I-001"
STARTUP_APPROVE = "APP-S-001"


def application(application_id: str) -> AnyApplication:
    """A committed application, validated."""
    for item in committed_applications():
        if item.application_id == application_id:
            return item
    raise KeyError(application_id)


def baseline(application_id: str) -> tuple[AnyApplication, PolicyInputs]:
    """An application the policy approves and its designed inputs (no document checks)."""
    item = application(application_id)
    inputs = variant_inputs(item)["designed"]
    assert evaluate_policy(item, inputs).recommendation == "approve"
    return item, inputs


def with_checks(inputs: PolicyInputs, *flags: DocumentCheckFlag) -> PolicyInputs:
    """The same inputs plus document check flags."""
    return inputs.model_copy(update={"document_checks": list(flags)})


def finding(result: PolicyResult, rule: str):
    """The single finding with this rule name."""
    found = [item for item in result.findings if item.rule == rule]
    assert len(found) == 1, (rule, [item.rule for item in result.findings])
    return found[0]


# --------------------------------------------------------------------------------------------------------------
# 1. Regression: the default leaves every committed application byte-identical
# --------------------------------------------------------------------------------------------------------------


def test_snapshot_covers_every_committed_application() -> None:
    snapshot = read_snapshot()
    applications = committed_applications()
    ids = [item.application_id for item in applications]
    assert sorted(snapshot) == sorted(ids)
    assert sum(1 for item in ids if item.startswith(("APP-C-", "APP-I-"))) == 65
    assert sum(1 for item in ids if item.startswith("APP-S-")) >= 6


def test_default_document_checks_leave_policy_output_byte_identical() -> None:
    snapshot = read_snapshot()
    for item in committed_applications():
        recorded = snapshot[item.application_id]
        assert recorded["inputs_sha256"] == inputs_sha256(item), (
            f"{item.application_id} changed since the snapshot was recorded; re-record it on purpose"
        )
        assert policy_outputs(item) == recorded["outputs"], item.application_id


def test_explicit_empty_document_checks_are_identical_to_the_default() -> None:
    snapshot = read_snapshot()
    for item in committed_applications():
        for name, inputs in variant_inputs(item).items():
            explicit = with_checks(inputs)
            assert explicit.document_checks == []
            output = canonical_json(evaluate_policy(item, explicit).model_dump(mode="json"))
            assert output == snapshot[item.application_id]["outputs"][name], (item.application_id, name)


# --------------------------------------------------------------------------------------------------------------
# 2. Each confirmed document check raises its finding
# --------------------------------------------------------------------------------------------------------------

CREDIT_EXPECTED = [
    ("balance_continuity", "R-NBC-13", ["NBC-9.1", "NBC-7.3"], [FRAUD_OPERATIONS]),
    ("tax_return_vs_pnl", "R-NBC-13", ["NBC-9.1", "NBC-7.3"], [FRAUD_OPERATIONS]),
    ("name_consistency", "R-NBC-09", ["NBC-6.2", "NBC-7.3"], [KYC_OPERATIONS]),
    ("fein_consistency", "R-NBC-09", ["NBC-6.2", "NBC-7.3"], [KYC_OPERATIONS]),
    ("owner_consistency", "R-NBC-08", ["NBC-6.1", "NBC-7.3"], [SENIOR_UNDERWRITER, KYC_OPERATIONS]),
]


@pytest.mark.parametrize(("check_id", "trigger", "sections", "refer_to"), CREDIT_EXPECTED)
def test_confirmed_credit_check_raises_its_blocking_finding(
    check_id: str, trigger: str, sections: list[str], refer_to: list[str]
) -> None:
    item, inputs = baseline(CREDIT_APPROVE)
    flag = DocumentCheckFlag(check_id=check_id, status="confirmed", detail="1 issue(s).", evidence_ids=["CT-01"])
    result = evaluate_policy(item, with_checks(inputs, flag))
    raised = finding(result, f"document_{check_id}")
    assert raised.trigger_ids == [trigger]
    assert raised.section_ids == sections
    assert raised.refer_to == refer_to
    assert raised.blocking is True
    assert raised.evidence_ids == ["CT-01"]
    assert raised.detail == f"Document check {check_id} is confirmed: 1 issue(s)."
    assert result.recommendation == "refer"
    assert result.blocking_open is True
    assert result.required_reviewer_role == "senior_underwriter"


@pytest.mark.parametrize("check_id", ["balance_continuity", "name_consistency", "owner_consistency"])
def test_startup_files_use_the_northwind_document_rules(check_id: str) -> None:
    item, inputs = baseline(STARTUP_APPROVE)
    result = evaluate_policy(item, with_checks(inputs, DocumentCheckFlag(check_id=check_id, status="confirmed")))
    expected = {row[0]: row[1] for row in CREDIT_EXPECTED}[check_id]
    assert finding(result, f"document_{check_id}").trigger_ids == [expected]
    assert result.recommendation == "refer"


def test_confirmed_loss_run_totals_is_a_blocking_siu_referral() -> None:
    item, inputs = baseline(INSURANCE_APPROVE)
    flag = DocumentCheckFlag(check_id="loss_run_totals", status="confirmed", evidence_ids=["CT-02"])
    result = evaluate_policy(item, with_checks(inputs, flag))
    raised = finding(result, "document_loss_run_totals")
    assert raised.trigger_ids == ["R-HM-16"]
    assert raised.section_ids == ["HM-11.1", "HM-9.3"]
    assert raised.refer_to == [SPECIAL_INVESTIGATIONS]
    assert raised.blocking is True
    assert raised.detail == "Document check loss_run_totals is confirmed."
    assert result.recommendation == "refer"
    assert result.review_queue == SPECIAL_INVESTIGATIONS


@pytest.mark.parametrize("check_id", ["name_consistency", "fein_consistency", "owner_consistency"])
def test_confirmed_identity_check_on_insurance_refers_to_the_underwriting_manager(check_id: str) -> None:
    item, inputs = baseline(INSURANCE_APPROVE)
    result = evaluate_policy(item, with_checks(inputs, DocumentCheckFlag(check_id=check_id, status="confirmed")))
    raised = finding(result, f"document_{check_id}")
    assert raised.trigger_ids == ["R-HM-12"]
    assert raised.section_ids == ["HM-8.1", "HM-9.3"]
    assert raised.refer_to == [UW_MANAGER]
    assert raised.blocking is False
    assert result.recommendation == "refer"
    assert result.blocking_open is False
    assert result.review_queue == UW_MANAGER


def test_resolved_flag_raises_no_finding_and_records_a_passed_check() -> None:
    item, inputs = baseline(CREDIT_APPROVE)
    flag = DocumentCheckFlag(check_id="name_consistency", status="resolved", detail="corrected application.")
    result = evaluate_policy(item, with_checks(inputs, flag))
    assert result.recommendation == "approve"
    assert not [entry for entry in result.findings if entry.rule.startswith("document_")]
    passed = [entry for entry in result.checks_passed if entry.rule == "document_name_consistency"]
    assert len(passed) == 1
    assert passed[0].section_ids == ["NBC-6.2"]
    assert passed[0].detail == "Document check name_consistency was resolved: corrected application."


@pytest.mark.parametrize(
    "check_id",
    ["revenue_vs_deposits", "required_documents", "statement_months_complete", "debt_payment_consistency",
     "embedded_instructions", "tiv_consistency", "not_a_check"],
)  # fmt: skip
def test_checks_the_policy_evaluates_from_the_application_are_not_duplicated(check_id: str) -> None:
    item, inputs = baseline(CREDIT_APPROVE)
    plain = canonical_json(evaluate_policy(item, inputs).model_dump(mode="json"))
    flagged = evaluate_policy(item, with_checks(inputs, DocumentCheckFlag(check_id=check_id, status="confirmed")))
    assert canonical_json(flagged.model_dump(mode="json")) == plain


def test_duplicate_flags_merge_and_confirmed_wins() -> None:
    item, inputs = baseline(CREDIT_APPROVE)
    flags = [
        DocumentCheckFlag(check_id="fein_consistency", status="resolved", detail="first.", evidence_ids=["CT-01"]),
        DocumentCheckFlag(check_id="fein_consistency", status="confirmed", detail="second.", evidence_ids=["CT-03"]),
    ]
    result = evaluate_policy(item, with_checks(inputs, *flags))
    raised = finding(result, "document_fein_consistency")
    assert raised.evidence_ids == ["CT-01", "CT-03"]
    assert raised.detail == "Document check fein_consistency is confirmed: first. second."
    assert not [entry for entry in result.checks_passed if entry.rule == "document_fein_consistency"]


def test_document_injection_enters_through_the_existing_injection_input() -> None:
    item, inputs = baseline(CREDIT_APPROVE)
    flagged = inputs.model_copy(
        update={"injection_flags": [InjectionFlag(field="doc:D03:p7", patterns=["approval_claim"])]}
    )
    result = evaluate_policy(item, flagged)
    raised = finding(result, "embedded_instruction")
    assert raised.trigger_ids == ["R-NBC-13"]
    assert "doc:D03:p7" in raised.detail
    assert result.recommendation == "refer"


def test_case_state_passes_document_checks_to_the_policy() -> None:
    item, _ = baseline(CREDIT_APPROVE)
    data = item.model_dump(mode="json", exclude={"ground_truth"})
    state = from_application(
        data, document_checks=[DocumentCheckFlag(check_id="balance_continuity", status="confirmed")]
    )
    result = evaluate(state)
    assert finding(result, "document_balance_continuity").trigger_ids == ["R-NBC-13"]
    assert parse_application(state.application).application_id == CREDIT_APPROVE


# --------------------------------------------------------------------------------------------------------------
# Named indicators (review finding): a name that differs between two statements is also an NBC-9.1 indicator
# --------------------------------------------------------------------------------------------------------------


def test_names_differing_between_statements_also_refer_to_fraud_operations() -> None:
    item, inputs = baseline(CREDIT_APPROVE)
    flag = DocumentCheckFlag(
        check_id="name_consistency",
        status="confirmed",
        evidence_ids=["CT-01"],
        indicators=[NAMES_DIFFER_BETWEEN_STATEMENTS],
    )
    result = evaluate_policy(item, with_checks(inputs, flag))
    # NBC-6.2 still applies (identity, KYC Operations), and NBC-9.1 adds the integrity referral.
    assert finding(result, "document_name_consistency").trigger_ids == ["R-NBC-09"]
    integrity = finding(result, "document_name_consistency_names_differ_between_statements")
    assert integrity.trigger_ids == ["R-NBC-13"] and integrity.section_ids == ["NBC-9.1", "NBC-7.3"]
    assert integrity.refer_to == [FRAUD_OPERATIONS] and integrity.blocking and integrity.evidence_ids == ["CT-01"]
    assert result.recommendation == "refer"
    # Without the indicator only the identity rule fires, exactly as before.
    plain = evaluate_policy(item, with_checks(inputs, flag.model_copy(update={"indicators": []})))
    assert [f.rule for f in plain.findings if f.rule.startswith("document_")] == ["document_name_consistency"]


def test_insurance_has_no_statement_indicator_and_a_resolved_flag_raises_nothing() -> None:
    item, inputs = baseline(INSURANCE_APPROVE)
    flag = DocumentCheckFlag(
        check_id="name_consistency", status="confirmed", indicators=[NAMES_DIFFER_BETWEEN_STATEMENTS]
    )
    rules = [f.rule for f in evaluate_policy(item, with_checks(inputs, flag)).findings]
    assert rules == ["document_name_consistency"]
    credit, credit_inputs = baseline(CREDIT_APPROVE)
    resolved = flag.model_copy(update={"status": "resolved"})
    assert evaluate_policy(credit, with_checks(credit_inputs, resolved)).findings == []

"""evidence/ledger.py: claim statuses on hand-built and generated SYNTHETIC cases, the builder API used by Founder
Studio, the validation rules of each status, and the renderer protocol (provenance/renderer.py)."""

import hashlib
import json
import re

import pytest
from evidence_case_helpers import (
    CASE_NUMBERS,
    application_dict,
    generated_truth,
    oracle_case,
    single_problem_packet,
)
from pydantic import ValidationError

from uwcopilot.agent.applications import AnyApplication, parse_application, screened_parties
from uwcopilot.agent.policy import ScreeningOutcome
from uwcopilot.agent.policy_insurance import coverage_lines
from uwcopilot.docintel.check_models import Evidence
from uwcopilot.docintel.checks import run_checks
from uwcopilot.docintel.contradictions import Contradiction, build_contradictions
from uwcopilot.docintel.schemas import Cited, InsuranceSubmission, parse_extraction
from uwcopilot.docintel.synthetic.problems import CREDIT_RATES, INSURANCE_RATES
from uwcopilot.evidence import ledger_tables as tables
from uwcopilot.evidence.ledger import (
    CLAIM_STATUSES,
    IMPUTED_DEFAULT_METHOD,
    MODEL_EXPECTED_LOSS_CLAIM,
    MODEL_PD_CLAIM,
    SCREENING_ASSUMPTION_CLAIM,
    Citation,
    Claim,
    Ledger,
    ModelEstimate,
    Provenance,
    assumption_claim,
    build_ledger,
    unknown_claim,
    user_stated_claim,
)
from uwcopilot.evidence.ledger_facts import CHECK_UNKNOWN_REASON, UNCITED_REASON, outcome_claim
from uwcopilot.evidence.state import MODEL_NOT_EVALUATED_REASON, CaseState, apply, from_application, from_packet
from uwcopilot.provenance.renderer import STATUS_SUFFIX, render, round_trip_audit


def state_with(application_id: str, **changes: object) -> CaseState:
    """A state for a committed application with some CaseState fields replaced."""
    state = from_application(application_dict(application_id))
    return CaseState.model_validate({**state.model_dump(), **changes})


def claim(ledger: Ledger, claim_id: str) -> Claim:
    """A claim that must exist."""
    found = ledger.claim(claim_id)
    assert found is not None, claim_id
    return found


def slot(kind: str, slot_id: str) -> str:
    """A memo slot: {{claim:ID}} or {{assert:ID}}."""
    return "{{" + kind + ":" + slot_id + "}}"


def full_memo(ledger: Ledger) -> str:
    """A memo that uses every claim and every assertion once (for the round-trip property)."""
    claims = " ".join(slot("claim", item.id) for item in ledger.claims)
    assertions = " ".join(slot("assert", item.id) for item in ledger.assertions)
    return f"{claims}. {assertions}."


# --------------------------------------------------------------------------------------------------------------
# Hand-built states
# --------------------------------------------------------------------------------------------------------------


def test_an_application_without_documents_gives_user_stated_facts_and_verified_calculations() -> None:
    ledger = build_ledger(state_with("APP-C-001"))
    facts = [item for item in ledger.claims if item.id.startswith("APP-")]
    assert facts and {item.status for item in facts} == {"USER_STATED"}
    assert {item.provenance.kind for item in facts} == {"application"}
    calcs = [item for item in ledger.claims if item.id.startswith("CALC-")]
    assert calcs and {item.status for item in calcs} == {"VERIFIED"}
    assert claim(ledger, "CALC-dscr").provenance.kind == "calculation"
    assert claim(ledger, "APP-requested_amount").value == application_dict("APP-C-001")["requested_amount"]
    assert ledger.subject_id == "APP-C-001" and ledger.line == "credit" and ledger.data_label == "SYNTHETIC"
    assert sum(ledger.status_counts().values()) == len(ledger.claims)
    assert set(ledger.status_counts()) == set(CLAIM_STATUSES)


def test_model_pd_is_unknown_with_the_honesty_reason() -> None:
    credit = build_ledger(state_with("APP-C-001"))
    pd = claim(credit, MODEL_PD_CLAIM)
    assert pd.status == "UNKNOWN" and pd.value is None and pd.reason == MODEL_NOT_EVALUATED_REASON
    insurance = build_ledger(state_with("APP-I-001"))
    expected_loss = claim(insurance, MODEL_EXPECTED_LOSS_CLAIM)
    assert expected_loss.status == "UNKNOWN" and expected_loss.reason == MODEL_NOT_EVALUATED_REASON
    assert insurance.claim(MODEL_PD_CLAIM) is None


def test_clean_screening_assumption_is_an_assumption_claim() -> None:
    ledger = build_ledger(state_with("APP-C-001"))
    assumed = claim(ledger, SCREENING_ASSUMPTION_CLAIM)
    assert assumed.status == "ASSUMPTION" and assumed.value == "clear"
    assert assumed.provenance.source == "unsourced"
    application = parse_application(application_dict("APP-C-001"))
    screened = [ScreeningOutcome(party_id=p.party_id, status="clear") for p in screened_parties(application)]
    assert build_ledger(state_with("APP-C-001", screening=screened)).claim(SCREENING_ASSUMPTION_CLAIM) is None


def revenue_contradiction() -> Contradiction:
    """A hand-built revenue_vs_deposits contradiction: application revenue against the bank deposits."""
    return Contradiction(
        contradiction_id="CT-01",
        type="revenue_gap",
        severity="medium",
        check_id="revenue_vs_deposits",
        issue="revenue_gap",
        entity="business",
        fields=["application.stated_annual_revenue"],
        doc_ids=["D01", "D02"],
        sides=[
            Evidence(doc_id="D01", page=1, line=7, field="application.stated_annual_revenue", value=1_000_000.0),
            Evidence(doc_id="D02", page=3, line=40, field="statement.total_deposits", value=60_000.0),
        ],
        side_a_count=1,
        explanation="unsupported",
        explanation_code="unexplained",
        explanation_detail="hand-built for the test",
        guideline_refs=["NBC-3.4"],
        message="hand-built",
    )


def cited_everywhere(line: str, except_paths: tuple[str, ...] = ()) -> dict[str, list[dict[str, object]]]:
    """Document provenance for every fact path of a line (a packet whose facts all have a citation), on made-up
    lines of D09 page 9, except the listed paths (a value the mapping read but did not cite)."""
    specs = (*tables.FACTS_BY_LINE[line], *tables.COUNT_FACTS_BY_LINE.get(line, ()))
    return {
        spec.path: [{"doc_id": "D09", "page": 9, "line": number}]
        for number, spec in enumerate(specs, start=1)
        if spec.path not in except_paths
    }


def conflicting_state() -> CaseState:
    """APP-C-001 with document provenance for every fact (the stated revenue on D01 p1 L7) and an open revenue
    contradiction."""
    provenance = cited_everywhere("credit")
    provenance["stated_annual_revenue"] = [{"doc_id": "D01", "page": 1, "line": 7}]
    return state_with(
        "APP-C-001",
        field_provenance=provenance,
        check_status={"revenue_vs_deposits": "fail", "name_consistency": "pass"},
    )


def test_a_contradiction_makes_its_facts_conflicting_and_marks_dependent_calculations() -> None:
    ledger = build_ledger(conflicting_state(), contradictions=[revenue_contradiction()])
    revenue = claim(ledger, "APP-stated_annual_revenue")
    assert revenue.status == "CONFLICTING" and revenue.conflict_ids == ("CT-01",)
    assert revenue.provenance.citations == (Citation(doc_id="D01", page=1, line=7),)
    check = claim(ledger, "CHK-revenue_vs_deposits")
    assert check.status == "CONFLICTING" and check.conflict_ids == ("CT-01",)
    assert claim(ledger, "CHK-name_consistency").status == "VERIFIED"
    # The arithmetic stays VERIFIED; a calculation over the conflicting figure says so.
    gap = claim(ledger, "CALC-revenue_gap_pct")
    assert gap.status == "VERIFIED" and gap.depends_on_conflicting == ("APP-stated_annual_revenue",)
    assert claim(ledger, "CALC-dscr_stated_revenue").depends_on_conflicting == ("APP-stated_annual_revenue",)
    assert claim(ledger, "CALC-dscr").depends_on_conflicting == ()
    # A document check's claim is a rule's outcome, not a printed value: provenance kind "calculation".
    assert claim(ledger, "CHK-name_consistency").provenance.kind == "calculation"


def test_a_packet_value_without_a_citation_is_never_verified() -> None:
    # The mapping read term_months (the state has document provenance) but recorded no citation for it.
    state = state_with("APP-C-001", field_provenance=cited_everywhere("credit", except_paths=("term_months",)))
    ledger = build_ledger(state)
    term = claim(ledger, "APP-term_months")
    assert term.status == "UNKNOWN" and term.value is None and not term.imputed
    assert term.reason == UNCITED_REASON and term.provenance.kind == "document"
    assert term.provenance.details == {"uncited_value": application_dict("APP-C-001")["term_months"]}
    assert "not available" in render("Term {{claim:APP-term_months}}.", ledger).body
    # The DSCR reads the term (through the annual debt service), so it says it rests on an unknown value.
    assert "APP-term_months" in claim(ledger, "CALC-dscr").depends_on_unknown
    # Every other fact has its citation and is VERIFIED.
    others = [item for item in ledger.claims if item.id.startswith("APP-") and item.id != "APP-term_months"]
    assert others and {item.status for item in others} == {"VERIFIED"}


def test_a_check_outcome_over_an_unread_value_is_unknown_not_verified() -> None:
    # The revenue check passed, but statement month 1 was read and marked unreliable (its rows do not add up): the
    # pass rests on that month, so it is UNKNOWN, like a calculation over it, and its outcome is kept in details.
    state = state_with(
        "APP-C-001",
        field_provenance=cited_everywhere("credit"),
        check_status={"revenue_vs_deposits": "pass", "name_consistency": "pass"},
        unknown_paths=["bank_statements.months[1]"],
    )
    ledger = build_ledger(state)
    check = claim(ledger, "CHK-revenue_vs_deposits")
    assert check.status == "UNKNOWN" and check.value is None and not check.imputed
    assert check.depends_on_unknown == ("APP-bank_statements.months.1",)
    assert check.reason == CHECK_UNKNOWN_REASON.format(ids="APP-bank_statements.months.1")
    assert check.provenance.kind == "calculation" and check.provenance.details == {"outcome_with_unread_inputs": "pass"}
    assert "not available" in render("Revenue check {{claim:CHK-revenue_vs_deposits}}.", ledger).body
    # A check that compares other values keeps its VERIFIED outcome.
    name = claim(ledger, "CHK-name_consistency")
    assert name.status == "VERIFIED" and name.value == "pass" and name.depends_on_unknown == ()


def test_a_check_outcome_lists_the_conflicting_values_it_compares() -> None:
    stated = Claim(
        id="APP-debt.existing_monthly_payment_schedule",
        label="Existing monthly debt payments (debt schedule)",
        value=1000.0,
        unit="USD",
        display="usd2",
        status="CONFLICTING",
        provenance=Provenance(kind="document", ref="debt.existing_monthly_payment_schedule"),
        conflict_ids=("CT-09",),
    )
    unrelated = stated.model_copy(update={"id": "APP-stated_annual_revenue"})
    found = outcome_claim("debt_payment_consistency", "pass", [stated, unrelated])
    assert found.status == "VERIFIED" and found.value == "pass"
    assert found.depends_on_conflicting == ("APP-debt.existing_monthly_payment_schedule",)


def test_a_generated_claims_history_check_over_an_unreliable_loss_run_is_unknown() -> None:
    # A loss run whose summary differs from its claim lines: the claims-history check still passes against one of
    # the two readings, but the policy year it read is UNKNOWN, so the pass is not shown as verified.
    case = oracle_case(single_problem_packet("insurance", "loss_run_total_mismatch"))
    ledger = build_ledger(case.state, contradictions=case.report)
    check = claim(ledger, "CHK-claims_history_consistency")
    assert check.status == "UNKNOWN" and check.depends_on_unknown
    assert all(item.startswith("APP-loss_runs.years.") for item in check.depends_on_unknown)
    assert claim(ledger, "CHK-loss_run_totals").status == "CONFLICTING"
    assert round_trip_audit(render(full_memo(ledger), ledger), ledger) == []


@pytest.mark.parametrize(
    ("line", "problem_type"),
    [("credit", problem_type) for problem_type in CREDIT_RATES]
    + [("insurance", problem_type) for problem_type in INSURANCE_RATES],
)
def test_every_verified_document_claim_has_a_citation(line: str, problem_type: str) -> None:
    # On a generated SYNTHETIC packet of every problem family the generator can plant on the line (the keys of its
    # draw rates: 9 credit and 8 insurance families, missing_document included).
    case = oracle_case(single_problem_packet(line, problem_type))
    assert problem_type in {problem.problem_type for problem in case.truth.problems}
    ledger = build_ledger(case.state, extraction=case.extraction, contradictions=case.report)
    uncited = [
        item.id
        for item in ledger.claims
        if item.status == "VERIFIED" and item.provenance.kind == "document" and not item.provenance.citations
    ]
    assert uncited == []
    assert any(item.status == "VERIFIED" and item.provenance.kind == "document" for item in ledger.claims)


def test_a_resolution_closes_the_contradiction() -> None:
    resolved = apply(conflicting_state(), {"check:revenue_vs_deposits": "resolved"})
    ledger = build_ledger(resolved, contradictions=[revenue_contradiction()])
    # The document value stands and its disagreement is closed; the closing outcome came from a lever, not a page.
    assert claim(ledger, "APP-stated_annual_revenue").status == "VERIFIED"
    check = claim(ledger, "CHK-revenue_vs_deposits")
    assert check.status == "ASSUMPTION" and check.value == "resolved"
    assert check.provenance.kind == "assumption" and "check:revenue_vs_deposits" in (check.provenance.source or "")
    assert claim(ledger, "CALC-revenue_gap_pct").depends_on_conflicting == ()


def without_last_statement_month(application_id: str) -> CaseState:
    """A committed credit application with its most recent statement month removed (NBC-3.2 fails)."""
    data = application_dict(application_id)
    data["bank_statements"]["months"] = sorted(data["bank_statements"]["months"], key=lambda m: m["month"])[:-1]
    return from_application(data)


def test_a_missing_statement_month_makes_revenue_and_dscr_unknown() -> None:
    ledger = build_ledger(without_last_statement_month("APP-C-004"))
    for calc_id in ("CALC-verified_annual_revenue", "CALC-revenue_gap_pct", "CALC-noi", "CALC-dscr", "CALC-leverage"):
        unknown = claim(ledger, calc_id)
        assert unknown.status == "UNKNOWN" and unknown.value is None
        assert "12 consecutive months" in (unknown.reason or "")
    # APP-C-004 is a variable-rate line of credit, so both NBC-4.5 stress tests were required.
    assert claim(ledger, "CALC-stressed_dscr_revenue").status == "UNKNOWN"
    assert claim(ledger, "CALC-stressed_dscr_rate").status == "UNKNOWN"
    incomplete = ledger.assertion("A-bank_statements_incomplete")
    assert incomplete is not None and incomplete.result is False and incomplete.rule_ref == "R-NBC-03"
    dscr = ledger.assertion("A-dscr")
    assert dscr is not None and dscr.result is None and dscr.phrase_unknown == "could not be evaluated"


def test_stress_claims_are_not_required_when_the_stress_test_does_not_apply() -> None:
    ledger = build_ledger(without_last_statement_month("APP-C-001"))  # a fixed-rate term loan of $90,000
    assert ledger.claim("CALC-stressed_dscr_revenue") is None
    assert ledger.claim("CALC-stressed_dscr_rate") is None


def test_an_unread_value_with_a_default_is_unknown_and_marked_imputed() -> None:
    data = application_dict("APP-C-001")
    data["debt"]["existing_total_debt_balance"] = 0.0
    state = CaseState(
        application=data,
        field_provenance=cited_everywhere("credit", except_paths=("debt.existing_total_debt_balance",)),
        unknown_paths=["debt.existing_total_debt_balance"],
    )
    ledger = build_ledger(state)
    unread = claim(ledger, "APP-debt.existing_total_debt_balance")
    # The default the policy used is kept OUT of the value (a memo prints "not available"), but it is recorded.
    assert unread.status == "UNKNOWN" and unread.value is None
    assert unread.provenance.details == {"imputed_value": 0.0} and unread.inputs["imputed_value"] == 0.0
    assert unread.imputed and unread.imputation_method == IMPUTED_DEFAULT_METHOD
    assert "could not read" in (unread.reason or "")
    assert "not available[^c1]" in render("Debt {{claim:APP-debt.existing_total_debt_balance}}.", ledger).body
    # Leverage reads that balance: the policy computed it on the default, so it is UNKNOWN, never VERIFIED.
    leverage = claim(ledger, "CALC-leverage")
    assert leverage.status == "UNKNOWN" and leverage.value is None and leverage.imputed
    assert leverage.depends_on_unknown == ("APP-debt.existing_total_debt_balance",)
    assert leverage.provenance.details["value_with_unread_inputs"] is not None


def test_a_missing_required_document_is_an_unknown_doc_claim() -> None:
    data = application_dict("APP-C-001")
    data["documents_provided"] = [code for code in data["documents_provided"] if code != "debt_schedule"]
    ledger = build_ledger(from_application(data))
    missing = claim(ledger, "DOC-debt_schedule")
    assert missing.status == "UNKNOWN" and missing.provenance.kind == "policy"
    assert ledger.assertion("A-missing_documents") is not None


def test_insurance_without_loss_runs_has_unknown_loss_ratios_for_the_quoted_lines() -> None:
    data = application_dict("APP-I-001")
    data["documents_provided"] = [code for code in data["documents_provided"] if code != "loss_runs"]
    ledger = build_ledger(from_application(data))
    application: AnyApplication = parse_application(data)
    for line in coverage_lines(application):
        assert claim(ledger, f"CALC-loss_ratio_5yr_{line}").status == "UNKNOWN"
    assert claim(ledger, "CALC-claim_frequency").status == "UNKNOWN"
    assert claim(ledger, "DOC-loss_runs").status == "UNKNOWN"


def test_startup_application_ledger() -> None:
    ledger = build_ledger(state_with("APP-S-001"))
    assert claim(ledger, "APP-loan_request.amount").status == "USER_STATED"
    assert claim(ledger, MODEL_PD_CLAIM).status == "UNKNOWN"
    assert claim(ledger, "CALC-startup_projected_dscr").status == "VERIFIED"


# --------------------------------------------------------------------------------------------------------------
# Assertions
# --------------------------------------------------------------------------------------------------------------


def test_assertions_follow_the_policy_result() -> None:
    ledger = build_ledger(state_with("APP-C-001", screening=[]))  # nobody screened: one finding per party
    application = parse_application(application_dict("APP-C-001"))
    count = len(screened_parties(application))
    ids = [item.id for item in ledger.assertions if item.rule == "screening_not_completed"]
    assert ids == ["A-screening_not_completed"] + [f"A-screening_not_completed-{n}" for n in range(2, count + 1)]
    first = ledger.assertion("A-screening_not_completed")
    assert first is not None and first.result is False and first.blocking and first.section_id == "NBC-6.3"
    dscr = ledger.assertion("A-dscr")
    assert dscr is not None and dscr.result is True and dscr.phrase_true == "meets the product minimum"
    pd = ledger.assertion("A-risk_model_pd")
    assert pd is not None and pd.result is None
    model = build_ledger(without_last_statement_month("APP-C-001"))
    finding = model.assertion("A-bank_statements_incomplete")
    assert finding is not None and finding.rule_ref == "R-NBC-03" and finding.section_ids == ("NBC-3.2", "NBC-3.5")


def test_assertion_phrases_contain_no_numbers() -> None:
    digit_or_symbol = re.compile(r"[\d$%]")
    phrases = [phrase for row in tables.TOPIC_PHRASES.values() for phrase in row[1:]] + list(tables.DEFAULT_PHRASES)
    assert phrases and not [phrase for phrase in phrases if digit_or_symbol.search(phrase)]
    assert set(tables.RULE_TOPIC.values()) <= set(tables.TOPIC_PHRASES)


# --------------------------------------------------------------------------------------------------------------
# Generated SYNTHETIC packets: each problem type opens the expected claim
# --------------------------------------------------------------------------------------------------------------

PROBLEM_CLAIMS = [
    ("credit", "revenue_gap", "APP-stated_annual_revenue", "CONFLICTING"),
    ("credit", "debt_payment_mismatch", "APP-debt.existing_monthly_payment_schedule", "CONFLICTING"),
    ("credit", "fein_mismatch", "CHK-fein_consistency", "CONFLICTING"),
    ("credit", "balance_discontinuity", "CHK-balance_continuity", "CONFLICTING"),
    ("credit", "missing_pages", "CALC-verified_annual_revenue", "UNKNOWN"),
    ("insurance", "tiv_mismatch", "APP-stated_tiv", "CONFLICTING"),
    ("insurance", "claims_history_mismatch", "APP-stated_claim_count", "CONFLICTING"),
    ("insurance", "loss_run_total_mismatch", "CHK-loss_run_totals", "CONFLICTING"),
    ("insurance", "paid_reserve_mismatch", "CHK-loss_run_line_balance", "CONFLICTING"),
    ("insurance", "name_mismatch", "CHK-name_consistency", "CONFLICTING"),
    ("insurance", "owner_mismatch", "CHK-owner_consistency", "CONFLICTING"),
    ("insurance", "prompt_injection", "CHK-embedded_instructions", "CONFLICTING"),
]


@pytest.mark.parametrize(("line", "problem_type", "claim_id", "status"), PROBLEM_CLAIMS)
def test_generated_problem_opens_its_claim(line: str, problem_type: str, claim_id: str, status: str) -> None:
    case = oracle_case(single_problem_packet(line, problem_type))
    ledger = build_ledger(case.state, extraction=case.extraction, contradictions=case.report)
    opened = claim(ledger, claim_id)
    assert opened.status == status
    if status == "CONFLICTING":
        ids = {item.contradiction_id for item in case.report.contradictions}
        assert set(opened.conflict_ids) <= ids and opened.conflict_ids
    assert round_trip_audit(render(full_memo(ledger), ledger), ledger) == []


@pytest.mark.parametrize("line", ["credit", "insurance"])
def test_generated_missing_document_is_an_unknown_doc_claim(line: str) -> None:
    case = oracle_case(single_problem_packet(line, "missing_document"))
    ledger = build_ledger(case.state, contradictions=case.report)
    missing = [item for item in ledger.claims if item.id.startswith("DOC-")]
    assert missing and {item.status for item in missing} == {"UNKNOWN"}
    assert {item.id for item in missing} == {f"DOC-{code}" for code in case.truth.missing_required_documents}


def test_generated_packet_facts_carry_their_document_citations() -> None:
    case = oracle_case(single_problem_packet("insurance", "tiv_mismatch"))
    ledger = build_ledger(case.state, contradictions=case.report)
    tiv = claim(ledger, "APP-stated_tiv")
    assert tiv.provenance.kind == "document"
    assert tiv.provenance.citations and all(item.doc_id and item.page for item in tiv.provenance.citations)


# --------------------------------------------------------------------------------------------------------------
# The renderer protocol
# --------------------------------------------------------------------------------------------------------------


def test_the_ledger_fills_memo_slots_with_status_suffixes_and_footnotes() -> None:
    ledger = build_ledger(conflicting_state(), contradictions=[revenue_contradiction()])
    memo = render(
        "Stated revenue is {{claim:APP-stated_annual_revenue}}. The decision DSCR {{claim:CALC-dscr}} "
        "{{assert:A-dscr}}.",
        ledger,
    )
    revenue = claim(ledger, "APP-stated_annual_revenue")
    assert f"${revenue.value:,.2f}{STATUS_SUFFIX['CONFLICTING']}[^c1]" in memo.body
    assert "meets the product minimum" in memo.body
    assert len(memo.footnotes) == 2
    assert "inputs sha256" in memo.footnotes[0] and "D01 p1 L7" in memo.footnotes[0]
    assert "conflicts: CT-01" in memo.footnotes[0]
    assert round_trip_audit(memo, ledger) == []


def test_every_claim_of_a_case_renders_and_round_trips() -> None:
    ledger = build_ledger(without_last_statement_month("APP-C-004"))
    assert round_trip_audit(render(full_memo(ledger), ledger), ledger) == []


# --------------------------------------------------------------------------------------------------------------
# Builder API (Founder Studio) and the two Studio statuses
# --------------------------------------------------------------------------------------------------------------


def studio_ledger() -> Ledger:
    """A founder package ledger built with the builder API (values are SYNTHETIC examples)."""
    return (
        Ledger.builder(subject_id="PKG-TEST-1", line="startup_credit")
        .add_claim(
            user_stated_claim(
                "rent_quote", "Monthly rent quote", 6_500.0, "USD", "usd0", "plan.rent", recorded_at="2026-10-01"
            )
        )
        .add_claim(
            assumption_claim(
                "ticket_size",
                "Average ticket",
                14.0,
                "USD",
                "usd2",
                source="unsourced",
                ref="profile:coffee_shop",
                low=9.0,
                high=18.0,
                metro_id="austin_tx",
                snapshot_date="2026-10-01",
            )
        )
        .add_claim(unknown_claim("foot_traffic", "Foot traffic", "COUNT", "int", "no data for this site"))
        .add_claim(
            id="opening_capex",
            label="Build-out budget",
            value=180_000.0,
            unit="USD",
            display="usd0",
            status="VERIFIED",
            provenance={"kind": "document", "ref": "quote", "citations": [{"doc_id": "U1", "page": 1, "line": 4}]},
        )
        .add_assertion(
            id="A-reserve",
            rule="working_capital_reserve",
            expression="reserve covers the fixed costs",
            result=True,
            phrase_true="covers the required reserve",
            phrase_false="falls short of the required reserve",
            phrase_unknown="could not be evaluated",
        )
        .build()
    )


def test_builder_api_builds_a_studio_ledger() -> None:
    ledger = studio_ledger()
    assert [item.id for item in ledger.claim_values()] == ["rent_quote", "ticket_size", "foot_traffic", "opening_capex"]
    assert ledger.status_counts()["USER_STATED"] == 1 and ledger.status_counts()["ASSUMPTION"] == 1
    assert [item.id for item in ledger.open_claims()] == ["foot_traffic"]
    ticket = claim(ledger, "ticket_size")
    assert ticket.value_range is not None and (ticket.value_range.low, ticket.value_range.high) == (9.0, 18.0)
    assert ticket.provenance.metro_id == "austin_tx" and ticket.provenance.snapshot_date == "2026-10-01"
    note = ticket.provenance_note()
    assert "source: unsourced" in note and "range 9 to 18" in note and "metro austin_tx" in note
    assert claim(ledger, "rent_quote").provenance.details == {"recorded_at": "2026-10-01"}
    memo = render("Rent {{claim:rent_quote}}, ticket {{claim:ticket_size}} {{assert:A-reserve}}.", ledger)
    assert "$6,500 (stated by the applicant)[^c1]" in memo.body
    assert "$14.00 (assumption)[^c2]" in memo.body
    assert round_trip_audit(memo, ledger) == []


def test_builder_refuses_duplicate_ids() -> None:
    builder = Ledger.builder().add_claim(unknown_claim("x", "X", "NONE", "text", "absent"))
    with pytest.raises(ValueError, match="duplicate claim id"):
        builder.add_claim(unknown_claim("x", "X again", "NONE", "text", "absent"))
    builder.add_assertion(
        id="A-x", rule="x", expression="x", result=None, phrase_true="a", phrase_false="b", phrase_unknown="c"
    )
    with pytest.raises(ValueError, match="duplicate assertion id"):
        builder.add_assertion(
            id="A-x", rule="x", expression="x", result=None, phrase_true="a", phrase_false="b", phrase_unknown="c"
        )


def test_ledger_and_claims_are_immutable() -> None:
    ledger = studio_ledger()
    with pytest.raises(ValidationError):
        ledger.subject_id = "other"
    with pytest.raises(ValidationError):
        ledger.claims[0].value = 1.0


def test_ledger_checks_ids_and_conflict_links() -> None:
    verified = Claim(
        id="a", label="A", value=1.0, unit="USD", display="usd2", status="VERIFIED",
        provenance=Provenance(kind="calculation", ref="a"),
    )  # fmt: skip
    with pytest.raises(ValidationError, match="unique"):
        Ledger(claims=(verified, verified))
    dependent = verified.model_copy(update={"id": "b", "depends_on_conflicting": ("a",)})
    with pytest.raises(ValidationError, match="non-CONFLICTING"):
        Ledger(claims=(verified, dependent))


def test_inputs_hash_is_the_sha256_of_the_canonical_inputs() -> None:
    inputs = {"noi": 120_000.0, "annual_debt_service": 96_000.0}
    made = Claim(
        id="CALC-x", label="x", value=1.25, unit="RATIO", display="ratio2", status="VERIFIED",
        provenance=Provenance(kind="calculation", ref="CALC-x"), inputs=inputs,
    )  # fmt: skip
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert made.inputs_sha256 == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    with pytest.raises(ValidationError, match="does not match"):
        Claim.model_validate({**made.model_dump(), "inputs_sha256": "0" * 64})


BASE = {"id": "c1", "label": "C", "unit": "USD", "display": "usd2", "provenance": {"kind": "policy", "ref": "c1"}}
INVALID_CLAIMS = [
    ({"value": None, "status": "UNKNOWN"}, "needs a reason"),
    ({"value": 5.0, "status": "UNKNOWN", "reason": "absent"}, "marked imputed"),
    ({"value": 5.0, "status": "UNKNOWN", "reason": "absent", "imputed": True}, "imputation method"),
    ({"value": 5.0, "status": "VERIFIED", "imputed": True, "imputation_method": "mean"}, "only an UNKNOWN"),
    ({"value": None, "status": "VERIFIED"}, "needs a value"),
    ({"value": None, "status": "USER_STATED"}, "needs a value"),
    ({"value": 5.0, "status": "CONFLICTING"}, "link its conflicts"),
    ({"value": 5.0, "status": "MODEL_ESTIMATE", "uncertainty": "band C"}, "model name and version"),
    (
        {
            "value": 5.0,
            "status": "MODEL_ESTIMATE",
            "provenance": {"kind": "model", "ref": "m", "model_name": "m", "model_version": "1"},
        },
        "uncertainty",
    ),
    ({"value": "x", "status": "ASSUMPTION"}, "source"),
    ({"value": 5.0, "status": "ASSUMPTION", "provenance": {"kind": "assumption", "ref": "p", "source": "s"}}, "range"),
    ({"value": 5.0, "status": "VERIFIED", "display": "usd9"}, "display"),
    ({"value": 5.0, "status": "VERIFIED", "id": "1bad id"}, "pattern"),
    ({"value": 5.0, "status": "VERIFIED", "unit": "usd"}, "pattern"),
    ({"value": 5.0, "status": "MAYBE"}, "status"),
]


@pytest.mark.parametrize(("changes", "message"), INVALID_CLAIMS)
def test_each_status_carries_what_it_needs(changes: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Claim.model_validate({**BASE, **changes})


def test_valid_claims_of_every_status() -> None:
    for status, extra in [
        ("VERIFIED", {"value": 1.0}),
        ("USER_STATED", {"value": 1.0, "provenance": {"kind": "application", "ref": "c1"}}),
        ("CONFLICTING", {"value": 1.0, "conflict_ids": ["CT-01"]}),
        ("UNKNOWN", {"value": None, "reason": "absent"}),
        ("UNKNOWN", {"value": 2.0, "reason": "absent", "imputed": True, "imputation_method": "median"}),
        (
            "ASSUMPTION",
            {
                "value": "clear",
                "display": "text",
                "unit": "TEXT",
                "provenance": {"kind": "assumption", "ref": "p", "source": "unsourced"},
            },
        ),
        (
            "MODEL_ESTIMATE",
            {
                "value": 0.04,
                "uncertainty": "not measured",
                "provenance": {"kind": "model", "ref": "m", "model_name": "m", "model_version": "1"},
            },
        ),
    ]:
        assert Claim.model_validate({**BASE, "status": status, **extra}).status == status


# --------------------------------------------------------------------------------------------------------------
# The model-estimator path (tested with a fake estimator, labeled fake)
# --------------------------------------------------------------------------------------------------------------


class FakeEstimator:
    """A FAKE estimator for tests only: it returns a fixed number and is labeled fake everywhere."""

    def estimate(self, application: AnyApplication) -> list[ModelEstimate]:
        """One fixed FAKE PD estimate with one missing and one imputed feature."""
        del application
        return [
            ModelEstimate(
                claim_id=MODEL_PD_CLAIM,
                label="Model probability of default",
                value=0.04,
                unit="PROBABILITY",
                display="ratio2",
                model_name="FAKE-test-estimator",
                model_version="fake-0",
                uncertainty="not measured (fake estimator)",
                unknown_features=("annual_inc",),
                imputed_features={"dti": "median of a fake training set"},
                is_fake=True,
            )
        ]


def test_a_model_estimator_replaces_the_unknown_model_claim() -> None:
    ledger = build_ledger(state_with("APP-C-001"), estimator=FakeEstimator())
    pd = claim(ledger, MODEL_PD_CLAIM)
    assert pd.status == "MODEL_ESTIMATE" and pd.value == 0.04
    assert pd.provenance.model_name == "FAKE-test-estimator" and pd.provenance.model_version == "fake-0"
    assert "FAKE" in pd.label and pd.provenance.details == {"fake": True}
    missing = claim(ledger, "FEATURE-annual_inc")
    assert missing.status == "UNKNOWN" and not missing.imputed and "not imputed" in (missing.reason or "")
    imputed = claim(ledger, "FEATURE-dti")
    assert (
        imputed.status == "UNKNOWN" and imputed.imputed and imputed.imputation_method == "median of a fake training set"
    )
    assert STATUS_SUFFIX["MODEL_ESTIMATE"] in render("PD {{claim:MODEL-pd}}.", ledger).body


# --------------------------------------------------------------------------------------------------------------
# Values no document provided (review findings): imputed defaults, unread paths, undocumented location fields
# --------------------------------------------------------------------------------------------------------------


def oracle_state_without(packet_id: str, line: str, section: str) -> CaseState:
    """The oracle state of a seed-4040 packet with one extracted section removed (as if the document were absent)."""
    raw = dict(generated_truth(packet_id, line).oracle_extraction)
    raw[section] = None
    extraction = parse_extraction(raw)
    checks = run_checks(extraction)
    return from_packet(extraction, checks, build_contradictions(extraction, checks))


def test_calculations_over_an_imputed_debt_schedule_are_unknown_not_verified() -> None:
    # PKT-C-413 without its debt schedule: docintel writes 0.0 for the scheduled payment and the total balance.
    # Leverage = (balance + requested) / NOI and DSCR = NOI / (12 x max(scheduled, observed) + proposed) were computed
    # on those zeros, so neither may print as a VERIFIED figure.
    state = oracle_state_without("PKT-C-413", "credit", "debt_schedule")
    ledger = build_ledger(state)
    for calc_id in ("CALC-annual_debt_service", "CALC-dscr", "CALC-leverage"):
        found = claim(ledger, calc_id)
        assert found.status == "UNKNOWN" and found.value is None and found.imputed
        assert found.depends_on_unknown and all(item.startswith("APP-debt.") for item in found.depends_on_unknown)
        assert found.provenance.details["value_with_unread_inputs"] is not None
    assert "APP-debt.existing_total_debt_balance" in claim(ledger, "CALC-leverage").depends_on_unknown
    assert "not available" in render("Leverage {{claim:CALC-leverage}}.", ledger).body
    assert round_trip_audit(render(full_memo(ledger), ledger), ledger) == []


def test_every_unread_path_has_a_ledger_claim() -> None:
    # PKT-C-409 without its credit report: credit scores (None) and bankruptcy flags (default False) are unread.
    state = oracle_state_without("PKT-C-409", "credit", "credit_report")
    assert state.unknown_paths
    ledger = build_ledger(state)
    by_ref = {item.provenance.ref: item for item in ledger.claims if item.status == "UNKNOWN"}
    for path in state.unknown_paths:
        assert path in by_ref, path
    flag = claim(ledger, "APP-owners.0.bankruptcy_within_7_years")
    assert flag.status == "UNKNOWN" and flag.value is None and flag.imputed
    assert flag.provenance.details == {"imputed_value": False}
    score = claim(ledger, "APP-owners.0.credit_score")
    assert score.status == "UNKNOWN" and not score.imputed
    missing = claim(ledger, "CALC-min_principal_credit_score")
    assert missing.status == "UNKNOWN" and "no credit score" in (missing.reason or "")


def test_an_unread_gl_limit_on_a_gl_request_is_unknown_and_the_capacity_pass_is_not_shown() -> None:
    # A seed-4040 packet whose form asks for GL, with both GL limits unread (as if OCR missed them): the policy counts
    # an unread limit as 0, so the HM-1.1 capacity pass rests on it and is shown as not evaluated.
    truth = next(
        generated_truth(f"PKT-I-{number}", "insurance")
        for number in CASE_NUMBERS
        if generated_truth(f"PKT-I-{number}", "insurance").application_as_documented["gl_limit_per_occurrence"]
    )
    extraction = parse_extraction(truth.oracle_extraction)
    assert isinstance(extraction, InsuranceSubmission) and extraction.application is not None
    unread = extraction.application.model_copy(
        update={"gl_limit_per_occurrence": Cited(), "gl_limit_aggregate": Cited()}
    )
    extraction = extraction.model_copy(update={"application": unread})
    checks = run_checks(extraction)
    state = from_packet(extraction, checks, build_contradictions(extraction, checks))
    ledger = build_ledger(state)
    for claim_id in ("APP-gl_limit_per_occurrence", "APP-gl_limit_aggregate"):
        assert claim(ledger, claim_id).status == "UNKNOWN"
    capacity = ledger.assertion("A-capacity")
    assert capacity is not None and capacity.result is None
    assert set(capacity.depends_on_unknown) == {"APP-gl_limit_per_occurrence", "APP-gl_limit_aggregate"}


def test_undocumented_location_defaults_are_unknown_and_passes_resting_on_them_are_not_shown() -> None:
    case = oracle_case(single_problem_packet("insurance", "tiv_mismatch"))
    ledger = build_ledger(case.state, contradictions=case.report)
    wind = claim(ledger, "APP-locations.0.wind_tier")
    assert wind.status == "UNKNOWN" and wind.value is None and wind.imputed
    assert wind.provenance.details == {"imputed_value": 0}
    for name in tables.UNDOCUMENTED_LOCATION_FIELDS:
        assert ledger.claim(f"APP-locations.0.{name}") is not None, name
    catastrophe = ledger.assertion("A-catastrophe_exposure")
    assert catastrophe is not None and catastrophe.result is None
    assert "APP-locations.0.wind_tier" in catastrophe.depends_on_unknown
    assert "not evaluated" in catastrophe.detail
    assert "could not be evaluated" in render("Exposure {{assert:A-catastrophe_exposure}}.", ledger).body
    # An application the applicant typed states these fields itself: no such claims, and the pass stands.
    typed = build_ledger(state_with("APP-I-001"))
    assert typed.claim("APP-locations.0.wind_tier") is None
    passed = typed.assertion("A-catastrophe_exposure")
    assert passed is not None and passed.result is True and passed.depends_on_unknown == ()


def test_an_annual_debt_service_left_out_with_the_dscr_rule_says_why() -> None:
    ledger = build_ledger(without_last_statement_month("APP-C-004"))
    service = claim(ledger, "CALC-annual_debt_service")
    assert service.status == "UNKNOWN"
    assert "only inside its DSCR rule" in (service.reason or "") and "does not need verified revenue" in (
        service.reason or ""
    )


# --------------------------------------------------------------------------------------------------------------
# Values a resolution substituted (review findings): never VERIFIED, never cited to the old page
# --------------------------------------------------------------------------------------------------------------


def test_a_substituted_value_is_an_assumption_without_the_old_citation() -> None:
    case = oracle_case(single_problem_packet("insurance", "tiv_mismatch"))
    before = claim(build_ledger(case.state, contradictions=case.report), "APP-stated_tiv")
    assert before.status == "CONFLICTING" and before.provenance.citations
    changed = apply(case.state, {"stated_tiv": 1_234_567.0})
    assert "stated_tiv" not in changed.field_provenance and changed.substituted_paths == {"stated_tiv": "stated_tiv"}
    ledger = build_ledger(changed, contradictions=case.report)
    after = claim(ledger, "APP-stated_tiv")
    assert after.status == "ASSUMPTION" and after.value == 1_234_567.0
    assert after.provenance.kind == "assumption" and after.provenance.citations == ()
    assert "substituted" in (after.provenance.source or "") and after.inputs_sha256 != before.inputs_sha256
    # The TIV contradiction compared the substituted value, so it is superseded (closed) and says why.
    check = claim(ledger, "CHK-tiv_consistency")
    assert check.status == "ASSUMPTION" and check.value == "superseded by a substituted value"
    # The TIV difference reads the substituted TIV and the schedule TIV (which rests on the superseded check).
    assert claim(ledger, "CALC-tiv_difference_pct").depends_on_assumption == ("APP-stated_tiv", "CHK-tiv_consistency")
    assert "(assumption)" in render("TIV {{claim:APP-stated_tiv}}.", ledger).body


def test_a_value_the_extraction_could_not_read_and_a_lever_filled_is_not_verified() -> None:
    data = application_dict("APP-C-001")
    data["debt"]["existing_total_debt_balance"] = 0.0
    state = CaseState(
        application=data,
        field_provenance={"requested_amount": [{"doc_id": "D01", "page": 1, "line": 3}]},
        unknown_paths=["debt.existing_total_debt_balance"],
    )
    filled = claim(
        build_ledger(apply(state, {"debt.existing_total_debt_balance": 250_000.0})),
        "APP-debt.existing_total_debt_balance",
    )
    assert filled.status == "ASSUMPTION" and filled.value == 250_000.0 and filled.provenance.kind == "assumption"
    assert filled.value_range is not None and filled.value_range.low == filled.value_range.high == 250_000.0


def test_fact_inputs_pin_the_value() -> None:
    first = claim(build_ledger(state_with("APP-C-001")), "APP-requested_amount")
    data = application_dict("APP-C-001")
    data["requested_amount"] = first.value + 1_000.0
    second = claim(build_ledger(from_application(data)), "APP-requested_amount")
    assert first.inputs["value"] == first.value and first.inputs_sha256 != second.inputs_sha256


# --------------------------------------------------------------------------------------------------------------
# Status, provenance and value rules (review findings): an assumption can never be presented as data
# --------------------------------------------------------------------------------------------------------------

STRICTER_INVALID_CLAIMS = [
    ({"value": 5.0, "status": "VERIFIED", "provenance": {"kind": "assumption", "ref": "p", "source": "s"}}, "kind"),
    ({"value": 5.0, "status": "VERIFIED", "provenance": {"kind": "user_input", "ref": "p"}}, "kind"),
    ({"value": 5.0, "status": "VERIFIED", "provenance": {"kind": "public_data", "ref": "acs"}}, "needs its source"),
    ({"value": 5.0, "status": "USER_STATED", "provenance": {"kind": "model", "ref": "m"}}, "kind"),
    ({"value": "6,500", "status": "VERIFIED", "display": "usd0"}, "needs a number"),
    ({"value": True, "status": "VERIFIED", "display": "int"}, "needs a number"),
    ({"value": float("nan"), "status": "VERIFIED"}, "finite"),
    ({"value": float("inf"), "status": "VERIFIED"}, "finite"),
    ({"value": 0.62, "status": "VERIFIED", "unit": "PROBABILITY", "display": "pct1"}, "PROBABILITY"),
    (
        {
            "value": 50.0,
            "status": "ASSUMPTION",
            "provenance": {"kind": "assumption", "ref": "p", "source": "unsourced"},
            "value_range": {"low": 9.0, "high": 18.0},
        },
        "outside its own range",
    ),
]


@pytest.mark.parametrize(("changes", "message"), STRICTER_INVALID_CLAIMS)
def test_status_provenance_and_value_must_agree(changes: dict, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Claim.model_validate({**BASE, **changes})


def test_studio_helpers_refuse_text_figures_at_build_time() -> None:
    with pytest.raises(ValidationError, match="needs a number"):
        user_stated_claim("rent", "Rent quote", "6,500", "USD", "usd0", "plan.rent")
    with pytest.raises(ValidationError, match="outside its own range"):
        assumption_claim("ticket", "Ticket", 50.0, "USD", "usd2", source="unsourced", ref="p", low=9.0, high=18.0)
    public = Claim.model_validate(
        {**BASE, "value": 5.0, "status": "VERIFIED", "provenance": {"kind": "public_data", "ref": "acs", "source": "x"}}
    )
    assert public.status == "VERIFIED"


def test_the_renderer_marks_an_imputed_value_another_ledger_prints() -> None:
    imputed = Claim.model_validate(
        {**BASE, "value": 2.0, "status": "UNKNOWN", "reason": "absent", "imputed": True, "imputation_method": "median"}
    )
    ledger = Ledger(claims=(imputed,))
    body = render("Value {{claim:c1}}.", ledger).body
    assert "$2.00 (imputed, not read from the documents)[^c1]" in body
    assert round_trip_audit(render("Value {{claim:c1}}.", ledger), ledger) == []


def test_a_ledger_checks_unknown_and_assumption_dependencies() -> None:
    verified = Claim(
        id="a", label="A", value=1.0, unit="USD", display="usd2", status="VERIFIED",
        provenance=Provenance(kind="calculation", ref="a"),
    )  # fmt: skip
    with pytest.raises(ValidationError, match="non-UNKNOWN"):
        Ledger(claims=(verified, verified.model_copy(update={"id": "b", "depends_on_unknown": ("a",)})))
    with pytest.raises(ValidationError, match="non-ASSUMPTION"):
        Ledger(claims=(verified, verified.model_copy(update={"id": "b", "depends_on_assumption": ("a",)})))

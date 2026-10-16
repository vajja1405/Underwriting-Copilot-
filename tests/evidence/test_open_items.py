"""evidence/open_items.py (+ open_items_insurance.py, open_item_params.py, requests.py): open problems detected from
the current case state, the request and template each one maps to, the case values that fill the template, and how
items close. Cases are committed SYNTHETIC applications and JSON-only generated packets (seed 4040)."""

import statistics

import pytest
from evidence_case_helpers import application_dict, oracle_case, single_problem_packet

from uwcopilot.agent.applications import MonthlyDeposits
from uwcopilot.agent.credit_calculations import month_eligibility
from uwcopilot.docintel.contradictions import Contradiction
from uwcopilot.evidence import open_item_params
from uwcopilot.evidence.open_item_models import PacketContext
from uwcopilot.evidence.open_items import detect_open_items, packet_context
from uwcopilot.evidence.requests import instantiate_options, request_catalog, templates
from uwcopilot.evidence.state import CaseState, apply, evaluate, from_application
from uwcopilot.evidence.voi import resolution_choices

CREDIT_TYPES = (
    "revenue_gap",
    "missing_pages",
    "missing_document",
    "name_mismatch",
    "fein_mismatch",
    "owner_mismatch",
    "balance_discontinuity",
    "debt_payment_mismatch",
)
INSURANCE_TYPES = (
    "loss_run_total_mismatch",
    "paid_reserve_mismatch",
    "tiv_mismatch",
    "claims_history_mismatch",
    "missing_document",
    "name_mismatch",
    "owner_mismatch",
)


def items_of(state: CaseState, context: PacketContext | None = None, answered: tuple[str, ...] = ()) -> dict:
    """Open items by item id."""
    return {item.item_id: item for item in detect_open_items(state, context, answered=answered)}


def without_documents(application_id: str, *codes: str) -> CaseState:
    """A committed application with some checklist codes removed from documents_provided."""
    application = application_dict(application_id)
    application["documents_provided"] = [code for code in application["documents_provided"] if code not in codes]
    return from_application(application)


def without_month(application_id: str, index: int) -> CaseState:
    """A committed credit application with one statement month removed."""
    application = application_dict(application_id)
    del application["bank_statements"]["months"][index]
    return from_application(application)


# --------------------------------------------------------------------------------------------------------------
# Every problem type of the generator is detected and mapped to its catalog request
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "problem_type"), [("credit", t) for t in CREDIT_TYPES] + [("insurance", t) for t in INSURANCE_TYPES]
)
def test_each_problem_type_is_detected_with_its_catalog_request(line: str, problem_type: str) -> None:
    truth = single_problem_packet(line, problem_type)
    case = oracle_case(truth)
    items = detect_open_items(case.state, packet_context(case.extraction, case.checks, case.report))
    problem = next(item for item in truth.problems if item.problem_type == problem_type)
    expected_type = "missing_loss_run" if problem.resolving_request == "missing_loss_run" else problem_type
    found = [item for item in items if item.problem_type == expected_type]
    assert found, [item.item_id for item in items]
    assert {item.request_id for item in found} == {problem.resolving_request}
    for item in found:
        assert item.request_id in request_catalog()
        assert problem_type in request_catalog()[item.request_id].resolves or expected_type == "missing_loss_run"
        assert item.template_key in templates()
        choices, dropped = resolution_choices(item)
        # missing_document's provided_inconsistent option names a check no policy rule reads, so it is left out
        # (evidence/requests.py); every other template keeps all its options.
        assert choices and set(dropped) <= {"provided_inconsistent"}
        assert (item.template_key == "missing_document") == bool(dropped)
        assert sum(choice.prior for choice in choices) == pytest.approx(1.0, abs=1e-5)


def test_item_details_carry_ids_and_numbers_but_never_document_text() -> None:
    truth = single_problem_packet("credit", "name_mismatch")
    case = oracle_case(truth)
    items = detect_open_items(case.state, packet_context(case.extraction, case.checks, case.report))
    names = [str(value) for problem in truth.problems for value in problem.values_by_doc.values() if value]
    for item in items:
        for name in names:
            assert name not in item.detail and name not in item.title


# --------------------------------------------------------------------------------------------------------------
# Request rules
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "request_id"),
    [
        ("non_revenue_deposits_counted", "revenue_breakdown_by_account"),
        ("unidentified_deposits", "revenue_breakdown_by_account"),
        ("fiscal_year_timing", "fiscal_year_revenue_reconciliation"),
        ("no_supporting_document", "revenue_source_documentation"),
        ("insufficient_evidence", "revenue_source_documentation"),
    ],
)
def test_the_revenue_gap_request_follows_the_contradiction_explanation(code: str, request_id: str) -> None:
    case = oracle_case(single_problem_packet("credit", "revenue_gap"))
    contradictions = [
        item.model_copy(update={"explanation_code": code}) if item.check_id == "revenue_vs_deposits" else item
        for item in case.report.contradictions
    ]
    context = packet_context(case.extraction, case.checks, contradictions)
    gap = next(item for item in detect_open_items(case.state, context) if item.problem_type == "revenue_gap")
    assert gap.request_id == request_id and gap.explanation_code == code


def test_a_revenue_gap_without_a_packet_asks_for_revenue_source_documentation() -> None:
    application = application_dict("APP-C-001")
    application["stated_annual_revenue"] = 1_200_000.0  # verified 900,000.00: a gap of 25.0%
    items = items_of(from_application(application))
    gap = items["OI-revenue_gap"]
    assert gap.request_id == "revenue_source_documentation"
    assert gap.placeholders == {"verified": 900_000.0, "stated": 1_200_000.0, "midpoint": 1_050_000.0}


@pytest.mark.parametrize(
    ("codes", "request_id"),
    [
        (["transcription_error"], "corrected_application"),
        (["transcription_error", "no_supporting_document"], "identity_reverification"),
    ],
)
def test_name_and_fein_requests_follow_the_explanations(codes: list[str], request_id: str) -> None:
    case = oracle_case(single_problem_packet("credit", "fein_mismatch"))
    template = next(item for item in case.report.contradictions if item.check_id == "fein_consistency")
    contradictions: list[Contradiction] = [
        template.model_copy(update={"explanation_code": code, "contradiction_id": f"CT-{90 + index}"})
        for index, code in enumerate(codes)
    ]
    items = items_of(case.state, packet_context(case.extraction, case.checks, contradictions))
    assert items["OI-fein_mismatch"].request_id == request_id


# --------------------------------------------------------------------------------------------------------------
# Case values for the templates (hand-checked)
# --------------------------------------------------------------------------------------------------------------


def test_a_missing_statement_month_is_valued_at_the_median_of_the_other_months() -> None:
    state = without_month("APP-C-001", 5)
    application = application_dict("APP-C-001")
    month = application["bank_statements"]["months"][5]["month"]
    items = items_of(state)
    item = items[f"OI-missing_pages-{month}"]
    assert item.request_id == "missing_statement_month" and item.request_params == {"month": month}
    remaining = state.application["bank_statements"]["months"]
    median = statistics.median(
        month_eligibility(MonthlyDeposits.model_validate(row), False).eligible for row in remaining
    )
    assert item.placeholders["month_at_median"]["total_deposits"] == round(median, 2)
    assert item.placeholders["month_at_half_median"]["total_deposits"] == round(0.5 * median, 2)
    assert item.placeholders["month_at_1_5_median"]["total_deposits"] == round(1.5 * median, 2)
    # Every option also brings the 12-month statement checklist code (the month completes the window).
    assert item.extra_substitutions == {"documents_provided+": "bank_statements_12_months"}
    assert "OI-missing_document-bank_statements_12_months" not in items


def test_formation_documents_are_one_item_and_all_missing_documents_share_one_request() -> None:
    state = without_documents(
        "APP-C-001", "formation_documents", "beneficial_ownership_certification", "principal_photo_ids"
    )
    items = [item for item in detect_open_items(state) if item.problem_type == "missing_document"]
    # Checklist order (NBC-3.1): photo IDs come before the formation documents.
    assert [item.item_id for item in items] == [
        "OI-missing_document-principal_photo_ids",
        "OI-missing_document-formation_documents",
    ]
    formation = next(item for item in items if item.item_id.endswith("formation_documents"))
    assert formation.extra_substitutions == {
        "documents_provided+": ["formation_documents", "beneficial_ownership_certification"]
    }
    assert {item.request_params["document"] for item in items} == {"principal_photo_ids, formation_documents"}


def test_owner_options_rewrite_the_owners_to_the_other_documents_percentages() -> None:
    case = oracle_case(single_problem_packet("credit", "owner_mismatch"))
    owners = open_item_params.owners_per_other_document(case.state.application, case.extraction)
    assert owners is not None and case.extraction.formation is not None
    members = {round(float(member.ownership_pct.value), 4) for member in case.extraction.formation.members}
    assert {round(owner["ownership_pct"], 4) for owner in owners} == members
    assert all(set(owner) == set(case.state.application["owners"][0]) for owner in owners)


def test_a_loss_run_missing_claim_is_sized_from_the_printed_summary() -> None:
    case = oracle_case(single_problem_packet("insurance", "loss_run_total_mismatch"))
    contradiction = next(item for item in case.report.contradictions if item.check_id == "loss_run_totals")
    values = open_item_params.missing_claim_values(case.state.application, case.extraction, contradiction)
    printed, lines = contradiction.values["differences"]["total_incurred"]
    claim = values["missing_claim"]
    assert claim["incurred"] == round(printed - lines, 2)
    assert round(claim["paid"] + claim["reserve"], 2) == claim["incurred"]
    year = case.state.application["loss_runs"]["years"][values["year_index"]]
    assert year["policy_year_start"] < claim["date_of_loss"]


def test_a_missing_loss_run_uses_the_prior_carrier_rows_of_the_application() -> None:
    case = oracle_case(single_problem_packet("insurance", "missing_document"))
    item = next(
        item
        for item in detect_open_items(case.state, packet_context(case.extraction, case.checks, case.report))
        if item.problem_type == "missing_loss_run"
    )
    clean = item.placeholders["years_without_claims"]
    twice = item.placeholders["years_at_twice_observed_rate"]
    assert clean and all(year["claims"] == [] for year in clean)
    rates = open_item_params.observed_loss_rates(case.state.application)
    for year in twice:
        for claim in year["claims"]:
            premium = year["property_earned_premium"] if claim["line"] == "property" else year["gl_earned_premium"]
            assert claim["incurred"] == round(premium * rates[claim["line"]] * 2.0, 2)
    assert item.extra_substitutions == {"documents_provided+": "loss_runs"}
    assert "OI-missing_document-loss_runs" not in {
        other.item_id
        for other in detect_open_items(case.state, packet_context(case.extraction, case.checks, case.report))
    }


def test_an_option_whose_case_value_is_missing_is_dropped_and_the_priors_renormalized() -> None:
    filled = instantiate_options("owner_mismatch", {"owners_per_other_document": None})
    assert filled.dropped_option_ids == ("other_document_correct",)
    priors = {choice.option_id: choice.prior for choice in filled.options}
    # documented priors 0.5 / 0.3 / 0.2: without the 0.3 option the others become 0.5 / 0.7 and 0.2 / 0.7.
    assert priors == pytest.approx({"application_correct": 0.5 / 0.7, "unresolved": 0.2 / 0.7}, abs=1e-6)


# --------------------------------------------------------------------------------------------------------------
# Items close when their cause is gone, or when answered
# --------------------------------------------------------------------------------------------------------------


def test_items_close_when_a_resolution_removes_their_cause() -> None:
    state = without_month("APP-C-001", 5)
    item = next(item for item in detect_open_items(state) if item.problem_type == "missing_pages")
    typical = next(choice for choice in resolution_choices(item)[0] if choice.option_id == "month_typical")
    resolved = apply(state, typical.substitutions)
    assert not [
        other for other in detect_open_items(resolved) if other.problem_type in ("missing_pages", "missing_document")
    ]
    assert evaluate(resolved).recommendation != "request_more_information"


def test_document_items_stay_open_until_answered_and_answered_items_are_left_out() -> None:
    case = oracle_case(single_problem_packet("credit", "debt_payment_mismatch"))
    context = packet_context(case.extraction, case.checks, case.report)
    assert "OI-debt_payment_mismatch" in items_of(case.state, context)
    kept = apply(
        case.state,
        {"debt.existing_monthly_payment_schedule": case.state.application["debt"]["existing_monthly_payment_schedule"]},
    )
    assert "OI-debt_payment_mismatch" in items_of(kept, context)
    assert "OI-debt_payment_mismatch" not in items_of(kept, context, answered=("OI-debt_payment_mismatch",))


def test_a_resolved_check_closes_its_identity_item() -> None:
    case = oracle_case(single_problem_packet("credit", "fein_mismatch"))
    context = packet_context(case.extraction, case.checks, case.report)
    assert "OI-fein_mismatch" in items_of(case.state, context)
    resolved = apply(case.state, {"check:fein_consistency": "resolved"})
    assert "OI-fein_mismatch" not in items_of(resolved, context)


def test_item_ids_stay_the_same_after_another_item_is_revealed() -> None:
    truth = single_problem_packet("insurance", "paid_reserve_mismatch")  # with a TIV mismatch too
    case = oracle_case(truth)
    context = packet_context(case.extraction, case.checks, case.report)
    before = items_of(case.state, context)
    tiv = next(problem for problem in truth.problems if problem.problem_type == "tiv_mismatch")
    after = items_of(apply(case.state, tiv.latent_substitutions), context)
    assert "OI-tiv_mismatch" in before and "OI-tiv_mismatch" not in after
    assert set(after) == set(before) - {"OI-tiv_mismatch"}


def test_embedded_instructions_and_tax_return_differences_are_items_one_without_a_request() -> None:
    state = apply(
        from_application(application_dict("APP-C-001")),
        {"check:tax_return_vs_pnl": "confirmed", "check:embedded_instructions": "confirmed"},
    )
    items = items_of(state)
    assert items["OI-prompt_injection"].request_id == "fraud_operations_review"
    assert items["OI-tax_return_vs_pnl"].request_id is None and items["OI-tax_return_vs_pnl"].template_key is None
    order = list(items)
    assert order.index("OI-prompt_injection") < order.index("OI-tax_return_vs_pnl")


def test_a_clean_application_has_no_open_items() -> None:
    assert detect_open_items(from_application(application_dict("APP-C-001"))) == []

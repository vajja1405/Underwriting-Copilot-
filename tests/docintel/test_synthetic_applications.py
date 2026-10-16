"""Tests for the SYNTHETIC packets' applications and problem records (JSON-only packets, no drawing)."""

import pytest

from uwcopilot.agent.applications import parse_application, screened_parties
from uwcopilot.agent.insurance_calculations import location_tiv
from uwcopilot.agent.policy import PolicyInputs, ScreeningOutcome, evaluate_policy
from uwcopilot.agent.policy_credit import CPA_FINANCIALS_ABOVE
from uwcopilot.agent.policy_credit import required_documents as credit_required_documents
from uwcopilot.agent.policy_insurance import INSPECTION_REPORT_ABOVE_TIV
from uwcopilot.agent.policy_insurance import required_documents as insurance_required_documents
from uwcopilot.docintel.deposits import (
    average_daily_balance,
    average_monthly_loan_payments,
    average_monthly_mca_remittance,
    classify_deposit,
    classify_withdrawal,
    last_period_end,
    monthly_deposits,
    negative_balance_months,
    new_loans_last_90_days,
    nsf_events,
)
from uwcopilot.docintel.levers import apply_substitutions
from uwcopilot.docintel.schemas import LoanPacket, parse_extraction
from uwcopilot.docintel.synthetic import build_packet
from uwcopilot.docintel.synthetic.common import packet_seed
from uwcopilot.docintel.synthetic.problems import evidence_request_ids
from uwcopilot.docintel.truth import PacketTruth

COUNT = 40


def json_only(line: str, count: int = COUNT) -> list[PacketTruth]:
    """JSON-only truths PKT-x-001.. for a line, seed 42."""
    letter = "C" if line == "credit" else "I"
    truths = []
    for number in range(1, count + 1):
        packet_id = f"PKT-{letter}-{number:03d}"
        pdf, truth = build_packet(packet_id, line, packet_seed(42, packet_id), "dev", draw=False)
        assert pdf is None
        truths.append(truth)
    return truths


@pytest.fixture(scope="module")
def credit_truths() -> list[PacketTruth]:
    """40 JSON-only loan packets."""
    return json_only("credit")


@pytest.fixture(scope="module")
def insurance_truths() -> list[PacketTruth]:
    """40 JSON-only insurance packets."""
    return json_only("insurance")


def clean_screening(application: dict) -> PolicyInputs:
    """Policy inputs with every party screened clear."""
    parsed = parse_application(application)
    return PolicyInputs(
        screening=[ScreeningOutcome(party_id=party.party_id, status="clear") for party in screened_parties(parsed)]
    )


@pytest.mark.parametrize("line", ["credit", "insurance"])
def test_applications_validate_and_the_policy_runs(line: str, credit_truths: list, insurance_truths: list) -> None:
    truths = credit_truths if line == "credit" else insurance_truths
    for truth in truths:
        documented = parse_application(truth.application_as_documented)
        latent = parse_application(truth.application_latent)
        assert documented.application_id == latent.application_id == truth.packet_id.replace("PKT", "APP")
        assert documented.ground_truth is None and documented.data_label == "SYNTHETIC"
        result = evaluate_policy(documented, clean_screening(truth.application_as_documented))
        assert result.recommendation in {"approve", "refer", "decline_for_review", "request_more_information"}
        missing = set(truth.missing_required_documents)
        assert missing.isdisjoint(truth.application_as_documented["documents_provided"])


@pytest.mark.parametrize("line", ["credit", "insurance"])
def test_problem_records_are_consistent(line: str, credit_truths: list, insurance_truths: list) -> None:
    truths = credit_truths if line == "credit" else insurance_truths
    requests = set(evidence_request_ids())
    for truth in truths:
        types = list(dict.fromkeys(problem.problem_type for problem in truth.problems))
        assert truth.scenario == ("clean" if not types else "+".join(types))
        doc_ids = {document.doc_id for document in truth.documents}
        for problem in truth.problems:
            assert problem.resolving_request in requests
            assert set(problem.values_by_doc) <= doc_ids and set(problem.doc_ids) <= doc_ids
            for option in problem.resolution_options:
                parse_application(apply_substitutions(truth.application_as_documented, option.substitutions))
            parse_application(apply_substitutions(truth.application_as_documented, problem.latent_substitutions))


@pytest.mark.parametrize("line", ["credit", "insurance"])
def test_injections_point_at_the_printed_text(line: str, credit_truths: list, insurance_truths: list) -> None:
    truths = credit_truths if line == "credit" else insurance_truths
    count = 0
    for truth in truths:
        pages = {page.page_number: " ".join(page.text_lines) for page in truth.pages}
        assert len(truth.injections) == sum(
            1 for problem in truth.problems if problem.problem_type == "prompt_injection"
        )
        for injection in truth.injections:
            count += 1
            assert " ".join(injection.text.split()[:5]) in pages[injection.page_number]
    assert count > 0


def test_json_only_truth_matches_the_drawn_truth() -> None:
    for packet_id, line in (("PKT-C-003", "credit"), ("PKT-I-003", "insurance")):
        _, drawn = build_packet(packet_id, line, packet_seed(42, packet_id), "test", draw=True)
        _, layout = build_packet(packet_id, line, packet_seed(42, packet_id), "test", draw=False)
        assert layout.pdf_file == "" and layout.pdf_sha256 == ""
        assert layout.model_dump(exclude={"pdf_file", "pdf_sha256"}) == drawn.model_dump(
            exclude={"pdf_file", "pdf_sha256"}
        )


def test_rows_classify_as_their_documented_category(credit_truths: list) -> None:
    for truth in credit_truths:
        packet = parse_extraction(truth.oracle_extraction)
        assert isinstance(packet, LoanPacket)
        for statement in packet.statements:
            for row in statement.transactions:
                if row.deposit.found:
                    assert classify_deposit(row.description.value) in {
                        "revenue", "cash_revenue", "transfer", "refund", "loan_proceeds", "owner_contribution",
                        "one_time", "unidentified",
                    }  # fmt: skip
                else:
                    assert classify_withdrawal(row.description.value) in {
                        "loan_payment",
                        "mca_remittance",
                        "nsf_fee",
                        "other",
                    }


def test_as_documented_account_figures_match_deposits_rules(credit_truths: list) -> None:
    """The generator's own computation agrees with docintel/deposits.py run on the oracle extraction."""
    for truth in credit_truths:
        packet = parse_extraction(truth.oracle_extraction)
        statements = packet.statements
        application = truth.application_as_documented
        summary = application["bank_statements"]
        assert [monthly_deposits(statement) for statement in statements] == summary["months"], truth.packet_id
        assert nsf_events(statements) == summary["nsf_events_last_6_months"]
        assert negative_balance_months(statements) == summary["negative_balance_months_last_12"]
        assert average_daily_balance(statements) == summary["average_daily_balance_last_3_months"], truth.packet_id
        debt = application["debt"]
        assert average_monthly_loan_payments(statements) == debt["existing_monthly_payment_observed"], truth.packet_id
        assert average_monthly_mca_remittance(statements) == debt["mca_monthly_remittance"], truth.packet_id
        as_of = last_period_end(statements)  # NBC-4.4: the most recent 90 days of statements
        assert new_loans_last_90_days(statements, as_of) == application["new_loans_last_90_days"], truth.packet_id


def test_latent_application_applies_every_latent_substitution(credit_truths: list, insurance_truths: list) -> None:
    for truth in [*credit_truths, *insurance_truths]:
        if not truth.problems:
            assert truth.application_latent == truth.application_as_documented


@pytest.mark.parametrize("line", ["credit", "insurance"])
def test_latent_world_has_every_required_document(line: str, credit_truths: list, insurance_truths: list) -> None:
    """Each required document the packet lacks has a problem whose latent resolution provides it."""
    truths = credit_truths if line == "credit" else insurance_truths
    required_for = credit_required_documents if line == "credit" else insurance_required_documents
    for truth in truths:
        latent = parse_application(truth.application_latent)
        missing = [code for code in required_for(latent) if code not in latent.documents_provided]
        assert missing == [], truth.packet_id


def test_cpa_financials_problem_exactly_when_the_request_is_above_500k(credit_truths: list) -> None:
    above_seen = 0
    for truth in credit_truths:
        above = truth.application_as_documented["requested_amount"] > CPA_FINANCIALS_ABOVE
        above_seen += above
        cpa = [p for p in truth.problems if p.resolving_request_params.get("document") == "cpa_reviewed_financials"]
        assert len(cpa) == (1 if above else 0), truth.packet_id
        if cpa:
            assert cpa[0].problem_type == "missing_document" and cpa[0].latent_option_id == "provided_consistent"
            assert "cpa_reviewed_financials" in truth.missing_required_documents
    assert above_seen > 0


def test_missing_month_latent_has_twelve_months_and_the_checklist_code(credit_truths: list) -> None:
    seen = 0
    for truth in credit_truths:
        if not any(problem.problem_type == "missing_pages" for problem in truth.problems):
            continue
        seen += 1
        documented = truth.application_as_documented
        latent = truth.application_latent
        assert len(documented["bank_statements"]["months"]) == 11
        months = [month["month"] for month in latent["bank_statements"]["months"]]
        assert len(months) == 12 and months == sorted(months)
        assert "bank_statements_12_months" not in documented["documents_provided"]
        assert "bank_statements_12_months" in latent["documents_provided"]
    assert seen > 0


def test_inspection_reports_cover_every_latent_large_location(insurance_truths: list) -> None:
    for truth in insurance_truths:
        latent = parse_application(truth.application_latent)
        large = [location for location in latent.locations if location_tiv(location) > INSPECTION_REPORT_ABOVE_TIV]
        reports = [document for document in truth.documents if document.doc_type == "inspection_report"]
        assert len(reports) >= len(large), truth.packet_id

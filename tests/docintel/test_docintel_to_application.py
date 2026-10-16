"""Tests for to_application (docintel/to_application*.py): extraction -> agent application JSON with provenance.

The contract test maps the generator's perfect (oracle) extraction of 20 JSON-only packets per line and compares
the result with the generator's application_as_documented, field by field with a $0.01 tolerance. The packets use
ids and a seed outside the evaluation set (PKT-*-201 to 220, seed 2026), so nothing here touches the test split.
"""

from functools import cache
from typing import Any

import pytest

from uwcopilot.agent.applications import parse_application
from uwcopilot.docintel.checks import run_checks
from uwcopilot.docintel.schemas import Cited, InsuranceSubmission, LoanPacket, parse_extraction
from uwcopilot.docintel.synthetic import build_packet
from uwcopilot.docintel.synthetic.common import packet_seed
from uwcopilot.docintel.to_application import to_application
from uwcopilot.docintel.truth import PacketTruth

CONTRACT_SEED = 2026
CONTRACT_IDS = range(201, 221)
TOLERANCE = 0.01


@cache
def json_only_truth(packet_id: str, line: str) -> PacketTruth:
    """The truth of one JSON-only packet (no PDF is drawn)."""
    _, truth = build_packet(packet_id, line, packet_seed(CONTRACT_SEED, packet_id), "dev", draw=False)
    return truth


def differences(first: Any, second: Any, path: str = "") -> list[str]:
    """Paths where two JSON values differ (numbers within TOLERANCE are equal; booleans are compared exactly)."""
    if isinstance(first, dict) and isinstance(second, dict):
        found = []
        for key in sorted(set(first) | set(second)):
            if key not in first or key not in second:
                found.append(f"{path}.{key} (missing on one side)")
            else:
                found.extend(differences(first[key], second[key], f"{path}.{key}"))
        return found
    if isinstance(first, list) and isinstance(second, list):
        if len(first) != len(second):
            return [f"{path} (length {len(first)} != {len(second)})"]
        found = []
        for index, (left, right) in enumerate(zip(first, second, strict=True)):
            found.extend(differences(left, right, f"{path}[{index}]"))
        return found
    if isinstance(first, bool) or isinstance(second, bool):
        return [] if first is second else [f"{path} ({first!r} != {second!r})"]
    if isinstance(first, int | float) and isinstance(second, int | float):
        return [] if abs(first - second) <= TOLERANCE else [f"{path} ({first} != {second})"]
    return [] if first == second else [f"{path} ({first!r} != {second!r})"]


DEBT_SCHEDULE_PATHS = {
    "debt.existing_monthly_payment_schedule",
    "debt.refinanced_monthly_payment",
    "debt.existing_total_debt_balance",
}


def allowed_unknown(truth: PacketTruth, path: str) -> bool:
    """True when an injected problem explains why the oracle mapping lists this path in unknowns.

    A missing debt schedule leaves the schedule figures undocumented; a statement whose printed total deposits
    were changed (balance_discontinuity) disagrees with its rows; a loss run whose summary disagrees with its claim
    rows (loss_run_total_mismatch) makes its years uncertain.
    """
    types = {problem.problem_type for problem in truth.problems}
    if path in DEBT_SCHEDULE_PATHS:
        return "debt_schedule" in truth.missing_required_documents
    if path.startswith("bank_statements.months["):
        return "balance_discontinuity" in types
    if path.startswith("loss_runs.years[") or path == "loss_runs.current_term_claims":
        return "loss_run_total_mismatch" in types
    return False


@pytest.mark.parametrize("line", ["credit", "insurance"])
def test_oracle_extraction_maps_to_the_as_documented_application(line: str) -> None:
    letter = "C" if line == "credit" else "I"
    for number in CONTRACT_IDS:
        truth = json_only_truth(f"PKT-{letter}-{number:03d}", line)
        application, provenance, unknowns = to_application(parse_extraction(truth.oracle_extraction))
        assert differences(application, truth.application_as_documented) == [], truth.packet_id
        assert [path for path in unknowns if not allowed_unknown(truth, path)] == [], truth.packet_id
        if any(problem.problem_type == "loss_run_total_mismatch" for problem in truth.problems):
            assert any(path.startswith("loss_runs.years[") for path in unknowns), truth.packet_id
        if "debt_schedule" in truth.missing_required_documents:
            assert DEBT_SCHEDULE_PATHS <= set(unknowns), truth.packet_id
        parse_application(application)
        assert provenance["application_date"][0]["doc_id"] == "D01"


def test_credit_documents_can_come_from_the_required_documents_check() -> None:
    truth = json_only_truth("PKT-C-201", "credit")
    extraction = parse_extraction(truth.oracle_extraction)
    with_checks, _, _ = to_application(extraction, run_checks(extraction))
    without_checks, _, _ = to_application(extraction)
    assert with_checks["documents_provided"] == without_checks["documents_provided"]
    assert with_checks["documents_provided"] == truth.application_as_documented["documents_provided"]


def test_provenance_cites_the_page_and_line_of_each_value() -> None:
    truth = json_only_truth("PKT-C-202", "credit")
    extraction = parse_extraction(truth.oracle_extraction)
    _, provenance, _ = to_application(extraction)
    cited = extraction.application.requested_amount
    assert provenance["requested_amount"] == [{"doc_id": cited.doc_id, "page": cited.page, "line": cited.line}]
    assert len(provenance["bank_statements.months[0]"]) == 1
    statement_docs = {source["doc_id"] for source in provenance["bank_statements.nsf_events_last_6_months"]}
    assert statement_docs == {statement.doc_id for statement in extraction.statements}


def test_missing_values_are_none_and_listed_as_unknown() -> None:
    truth = json_only_truth("PKT-C-203", "credit")
    extraction = parse_extraction(truth.oracle_extraction)
    assert isinstance(extraction, LoanPacket)
    extraction.application.fein = Cited()
    extraction.profit_and_loss = None
    application, provenance, unknowns = to_application(extraction)
    assert application["applicant"]["fein"] is None
    assert application["debt"]["ttm_ebitda"] is None
    assert {"applicant.fein", "debt.ttm_ebitda", "debt.ttm_pnl_revenue"} <= set(unknowns)
    assert "applicant.fein" not in provenance
    empty, _, empty_unknowns = to_application(LoanPacket(packet_id="PKT-C-999"))
    assert empty["application_id"] == "APP-C-999"
    assert empty["bank_statements"]["average_daily_balance_last_3_months"] is None
    assert {"application_form", "owners", "bank_statements.account_number"} <= set(empty_unknowns)


def test_loss_runs_count_as_provided_only_when_every_prior_carrier_has_one() -> None:
    truth = json_only_truth("PKT-I-201", "insurance")
    extraction = parse_extraction(truth.oracle_extraction)
    assert isinstance(extraction, InsuranceSubmission)
    application, _, _ = to_application(extraction)
    carriers = {row.carrier.value for row in extraction.application.prior_carriers}
    runs = {run.carrier.value for run in extraction.loss_runs}
    assert ("loss_runs" in application["documents_provided"]) == (carriers <= runs)
    extraction.loss_runs = extraction.loss_runs[1:]
    reduced, _, _ = to_application(extraction)
    assert "loss_runs" not in reduced["documents_provided"]
    assert reduced["documents_provided"][0] == "signed_application"


def test_insurance_application_id_and_defaults() -> None:
    truth = json_only_truth("PKT-I-202", "insurance")
    application, _, _ = to_application(parse_extraction(truth.oracle_extraction))
    assert application["application_id"] == "APP-I-202"
    assert application["applicant"]["prior_experience_years"] == 0
    assert all(location["flood_zone"] == "X" for location in application["locations"])
    assert all(owner["credit_score"] is None for owner in application["owners"])


def gl_and_no_gl_truths() -> tuple[PacketTruth, PacketTruth]:
    """A contract packet whose form prints GL limits and one whose form says "Not requested"."""
    truths = [json_only_truth(f"PKT-I-{number:03d}", "insurance") for number in CONTRACT_IDS]
    with_gl = [truth for truth in truths if truth.application_as_documented["gl_limit_per_occurrence"] is not None]
    without_gl = [truth for truth in truths if truth.application_as_documented["gl_limit_per_occurrence"] is None]
    assert with_gl and without_gl
    return with_gl[0], without_gl[0]


def test_gl_limits_record_their_source_and_not_requested_limits_are_not_unknown() -> None:
    with_gl, without_gl = gl_and_no_gl_truths()
    extraction = parse_extraction(with_gl.oracle_extraction)
    assert isinstance(extraction, InsuranceSubmission) and extraction.application is not None
    application, provenance, unknowns = to_application(extraction)
    for name in ("gl_limit_per_occurrence", "gl_limit_aggregate"):
        cited = getattr(extraction.application, name)
        assert application[name] == cited.value
        assert provenance[name] == [{"doc_id": cited.doc_id, "page": cited.page, "line": cited.line}]
        assert name not in unknowns
    # "Not requested": None, no source, and NOT an unknown (a property-only quote has no GL limits).
    application, provenance, unknowns = to_application(parse_extraction(without_gl.oracle_extraction))
    for name in ("gl_limit_per_occurrence", "gl_limit_aggregate"):
        assert application[name] is None and name not in provenance and name not in unknowns


def test_an_unread_gl_limit_on_a_gl_request_is_unknown() -> None:
    # The form asks for GL (or a package), but OCR did not read its limits: they are unknown, never "Not requested".
    with_gl, without_gl = gl_and_no_gl_truths()
    extraction = parse_extraction(with_gl.oracle_extraction)
    assert isinstance(extraction, InsuranceSubmission) and extraction.application is not None
    assert extraction.application.coverage.value in ("general_liability", "package")
    unread = extraction.application.model_copy(
        update={"gl_limit_per_occurrence": Cited(), "gl_limit_aggregate": Cited()}
    )
    application, provenance, unknowns = to_application(extraction.model_copy(update={"application": unread}))
    for name in ("gl_limit_per_occurrence", "gl_limit_aggregate"):
        assert application[name] is None and name not in provenance and name in unknowns
    # The same empty limits on a form whose coverage was not read: unknown too (GL may have been requested).
    other = parse_extraction(without_gl.oracle_extraction)
    assert isinstance(other, InsuranceSubmission) and other.application is not None
    no_coverage = other.application.model_copy(update={"coverage": Cited()})
    _, _, unknowns = to_application(other.model_copy(update={"application": no_coverage}))
    assert {"coverage", "gl_limit_per_occurrence", "gl_limit_aggregate"} <= set(unknowns)


def test_a_debt_schedule_without_rows_cites_its_total_monthly_payment_line() -> None:
    truths = [json_only_truth(f"PKT-C-{number:03d}", "credit") for number in CONTRACT_IDS]
    extractions = [parse_extraction(truth.oracle_extraction) for truth in truths]
    empty = [
        item
        for item in extractions
        if isinstance(item, LoanPacket) and item.debt_schedule is not None and not item.debt_schedule.items
    ]
    assert empty, "no contract packet has a debt schedule without rows"
    schedule = empty[0].debt_schedule
    assert schedule is not None and schedule.total_monthly_payment.found
    application, provenance, unknowns = to_application(empty[0])
    total_line = {
        "doc_id": schedule.total_monthly_payment.doc_id,
        "page": schedule.total_monthly_payment.page,
        "line": schedule.total_monthly_payment.line,
    }
    for path in ("debt.existing_monthly_payment_schedule", "debt.refinanced_monthly_payment"):
        assert provenance[path] == [total_line] and path not in unknowns
    assert application["debt"]["existing_monthly_payment_schedule"] == 0.0

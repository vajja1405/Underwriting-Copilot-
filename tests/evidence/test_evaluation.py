"""evidence/evaluation*.py: the Phase 4 F evaluation on hand-built cases (SYNTHETIC).

Hand-built truths use the real docintel.truth.InjectedProblem model; generated packets come from the Phase 2
generator with draw=False, seed 4040 and ids 401-440 (evidence_case_helpers), outside the evaluation set.
"""

import json
from types import SimpleNamespace

import pytest
from evidence_case_helpers import application_dict, single_problem_packet

from uwcopilot.agent.policy import Calculation
from uwcopilot.docintel.checks import run_checks
from uwcopilot.docintel.contradictions import build_contradictions
from uwcopilot.docintel.pipeline import PacketResult
from uwcopilot.docintel.schemas import parse_extraction
from uwcopilot.docintel.truth import InjectedProblem
from uwcopilot.evidence.evaluation import (
    ABSENT,
    CLAIM_STATUS_LABEL,
    CONFLICTING,
    LOCATION_DEFAULTS_KEY,
    UNKNOWN,
    VERIFIED,
    claim_counts,
    expected_claims,
    expected_patterns,
    expected_table,
    expected_table_sha256,
    merge_expectation,
    problem_claim_counts,
    score_claims,
    unobservable_problems,
)
from uwcopilot.evidence.evaluation_calcs import calc_counts, calc_rows, compare_one, within_tolerance
from uwcopilot.evidence.evaluation_packet import (
    ORACLE,
    PIPELINE,
    evaluate_case,
    oracle_case,
    pipeline_case,
    reference_result,
)
from uwcopilot.evidence.evaluation_requests import (
    document_id_map,
    nbe_counts,
    nbe_rows,
    request_agrees,
    top_request_reveal,
    translate_items,
)
from uwcopilot.evidence.evaluation_summary import (
    counter_group,
    nested_counts,
    paired_ratio_difference,
    summarize_mode,
)
from uwcopilot.evidence.open_item_models import OpenItem
from uwcopilot.evidence.simulator_cases import SimCase, page_hits
from uwcopilot.evidence.state import from_application
from uwcopilot.evidence.voi_models import RankedRequest

# ----------------------------------------------------------------------------------------------------------------
# Hand-built pieces
# ----------------------------------------------------------------------------------------------------------------

CAUSE_FOR_CLASS = {"benign": "benign_typo", "unsupported": "unsupported_unexplained", "missing": "missing"}


def problem(
    problem_id: str,
    problem_type: str,
    request: str,
    params: dict | None = None,
    latent: dict | None = None,
    classification: str = "unsupported",
    doc_ids: tuple[str, ...] = ("D01",),
) -> InjectedProblem:
    """A planted problem with no resolution options (the evaluation reads only the fields set here)."""
    return InjectedProblem(
        problem_id=problem_id,
        problem_type=problem_type,
        cause=CAUSE_FOR_CLASS[classification],
        classification=classification,
        doc_ids=list(doc_ids),
        fields=[],
        values_by_doc={},
        description="SYNTHETIC test problem",
        resolving_request=request,
        resolving_request_params=params or {},
        resolution_options=[],
        latent_option_id="none",
        latent_substitutions=latent or {},
    )


def truth(problems: list, missing: list[str] | None = None) -> SimpleNamespace:
    """A truth stand-in: the evaluation reads problems and missing_required_documents by attribute."""
    return SimpleNamespace(problems=problems, missing_required_documents=missing or [])


def item(item_id: str, problem_type: str, request_id: str, params: dict | None = None, **fields) -> OpenItem:
    """An open item."""
    return OpenItem(
        item_id=item_id,
        problem_type=problem_type,
        status="CONFLICTING",
        title="SYNTHETIC test item",
        template_key=None,
        request_id=request_id,
        request_params=params or {},
        **fields,
    )


def ranked(rank: int, request_id: str, item_ids: tuple[str, ...], voi: float, params: dict | None = None):
    """A ranked request."""
    return RankedRequest(
        rank=rank,
        request_id=request_id,
        title=request_id,
        params=params or {},
        item_ids=item_ids,
        voi=voi,
        p_change=1.0 if voi > 0 else 0.0,
        cost_weight=1.0,
        options_summary=(),
        reason="SYNTHETIC test reason",
    )


def calc(calc_id: str, unit: str, value: float | None) -> Calculation:
    """A policy calculation."""
    return Calculation(calc_id=calc_id, label=calc_id, value=value, unit=unit)


REVENUE_GAP = problem("PB-01", "revenue_gap", "revenue_source_documentation")


# ----------------------------------------------------------------------------------------------------------------
# evaluation.py: the expected-status table and claim scoring
# ----------------------------------------------------------------------------------------------------------------


def test_contradiction_problem_expects_its_claims_conflicting_and_required():
    table = expected_claims(truth([REVENUE_GAP]))
    assert table["CHK-revenue_vs_deposits"].status == CONFLICTING
    assert table["APP-stated_annual_revenue"].status == CONFLICTING
    assert table["CHK-revenue_vs_deposits"].required
    assert "PB-01 revenue_gap" in table["APP-stated_annual_revenue"].sources


def test_missing_statements_make_the_revenue_gap_unobservable_and_revenue_unknown():
    case = truth([REVENUE_GAP], missing=["bank_statements_12_months"])
    table = expected_claims(case)
    assert "CHK-revenue_vs_deposits" not in table
    assert "APP-stated_annual_revenue" not in table
    assert table["DOC-bank_statements_12_months"].status == UNKNOWN
    assert table["DOC-bank_statements_12_months"].required
    assert table["CHK-required_documents"].required
    assert table["CHK-statement_months_complete"].required
    # NBC-3.2: no verified revenue, so the DSCR is UNKNOWN when the ledger has it (conditional, not required).
    assert table["CALC-dscr"].status == UNKNOWN and not table["CALC-dscr"].required
    assert unobservable_problems(case) == ["PB-01"]


def test_unknown_wins_over_conflicting_and_required_is_sticky():
    table = {}
    merge_expectation(table, "APP-x", CONFLICTING, True, "problem")
    merge_expectation(table, "APP-x", UNKNOWN, False, "missing document")
    assert table["APP-x"].status == UNKNOWN
    assert table["APP-x"].required
    assert table["APP-x"].sources == ("problem", "missing document")


def test_a_calculation_the_truth_cannot_compute_is_expected_unknown():
    reference = [calc("CALC-incurred_difference_pct", "pct", None), calc("CALC-dscr", "ratio", 1.4)]
    table = expected_claims(truth([]), reference)
    assert table["CALC-incurred_difference_pct"].status == UNKNOWN
    assert not table["CALC-incurred_difference_pct"].required
    assert "CALC-dscr" not in table  # computable: expected VERIFIED by default


def test_score_claims_absent_conditional_and_by_design():
    table = expected_claims(truth([REVENUE_GAP]), [calc("CALC-revenue_gap_pct", "pct", None)])
    predicted = {
        "CHK-revenue_vs_deposits": CONFLICTING,  # right
        "CALC-dscr": VERIFIED,  # not in the table: expected VERIFIED, right
        "APP-requested_amount": UNKNOWN,  # expected VERIFIED: wrong
        "MODEL-pd": UNKNOWN,  # excluded by design
        "ASSUME-screening_clear": "ASSUMPTION",  # excluded by design
    }
    scores, excluded = score_claims(predicted, table)
    by_id = {score.claim_id: score for score in scores}
    assert by_id["APP-stated_annual_revenue"].predicted == ABSENT  # required but missing from the ledger
    assert "CALC-revenue_gap_pct" not in by_id  # conditional and missing: not scored
    assert "MODEL-pd" not in by_id
    assert excluded == {"MODEL-": 1, "ASSUME-": 1}
    counts = claim_counts(scores)
    assert counts["claims.total"] == 4
    assert counts["claims.correct"] == 2
    assert counts["claims.open_expected.total"] == 2
    assert counts["claims.open_expected.correct"] == 1
    assert counts["claims.confusion.CONFLICTING.ABSENT"] == 1
    assert counts["claims.confusion.VERIFIED.UNKNOWN"] == 1


def test_problem_claim_counts_per_type_and_unobservable():
    case = truth(
        [REVENUE_GAP, problem("PB-02", "missing_document", "missing_document", {"document": "debt_schedule"})],
        missing=["debt_schedule"],
    )
    scores, _ = score_claims(
        {"CHK-revenue_vs_deposits": CONFLICTING, "APP-stated_annual_revenue": VERIFIED, "DOC-debt_schedule": UNKNOWN},
        expected_claims(case),
    )
    counts = problem_claim_counts(case, scores)
    assert counts["claims.by_problem.revenue_gap.total"] == 2
    assert counts["claims.by_problem.revenue_gap.correct"] == 1
    assert counts["claims.by_problem.missing_document.correct"] == 1
    blocked = truth([problem("PB-01", "debt_payment_mismatch", "debt_schedule_update")], missing=["debt_schedule"])
    assert problem_claim_counts(blocked, [])["claims.by_problem.debt_payment_mismatch.unobservable"] == 1


def test_expected_table_hash_is_the_hash_of_its_json():
    import hashlib

    text = json.dumps(expected_table(), sort_keys=True, separators=(",", ":"))
    assert expected_table_sha256() == hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------------------------------------------
# evaluation_calcs.py
# ----------------------------------------------------------------------------------------------------------------


def test_money_tolerance_is_one_dollar_or_half_a_percent():
    assert within_tolerance("usd", 100.00, 101.00)  # $1.00 off: allowed
    assert not within_tolerance("usd", 100.00, 101.02)  # 0.5% of $100 is $0.50, so $1.00 applies
    assert within_tolerance("usd", 1_000_000.00, 1_004_900.00)  # 0.49% off
    assert not within_tolerance("usd", 1_000_000.00, 1_006_000.00)  # 0.6% off


def test_ratio_percent_and_exact_tolerances():
    assert within_tolerance("ratio", 1.25, 1.26)  # exactly 0.01 (floating-point noise absorbed)
    assert not within_tolerance("ratio", 1.25, 1.27)
    assert within_tolerance("pct", 12.3, 12.4)
    assert not within_tolerance("pct", 12.3, 12.5)
    assert within_tolerance("count", 3, 3) and not within_tolerance("count", 3, 4)


def test_compare_one_outcomes_and_counts():
    assert compare_one(calc("CALC-a", "pct", None), calc("CALC-a", "pct", None)) == "agree"
    assert compare_one(calc("CALC-a", "pct", None), calc("CALC-a", "pct", 2.0)) == "disagree"
    assert compare_one(calc("CALC-a", "pct", 2.0), None) == "missing"
    assert compare_one(None, calc("CALC-a", "pct", 2.0)) == "extra"
    rows = calc_rows(
        [calc("CALC-dscr", "ratio", 1.30), calc("CALC-noi", "usd", 500_000.0)],
        [calc("CALC-dscr", "ratio", 1.31), calc("CALC-leverage", "ratio", 2.0)],
    )
    assert [row["outcome"] for row in rows] == ["agree", "extra", "missing"]  # sorted by calc id
    counts = calc_counts(rows)
    assert counts["calcs.all.total"] == 3 and counts["calcs.all.agree"] == 1
    assert counts["calcs.unit.ratio.total"] == 2
    assert counts["calcs.outcome.missing"] == 1


# ----------------------------------------------------------------------------------------------------------------
# evaluation_requests.py
# ----------------------------------------------------------------------------------------------------------------


def test_request_agreement_rules():
    month = problem("PB-01", "missing_pages", "missing_statement_month", {"month": "2026-03"})
    assert request_agrees(month, "missing_statement_month", {"month": "2026-03"})
    assert not request_agrees(month, "missing_statement_month", {"month": "2026-04"})
    assert not request_agrees(month, "missing_statement_month", {})
    document = problem("PB-02", "missing_document", "missing_document", {"document": "debt_schedule"})
    assert request_agrees(document, "missing_document", {"document": "cpa_reviewed_financials, debt_schedule"})
    assert not request_agrees(document, "missing_document", {"document": "debt_schedule_2"})
    carrier = problem("PB-03", "loss_run_total_mismatch", "corrected_loss_run", {"carrier": "Example Mutual"})
    assert request_agrees(carrier, "corrected_loss_run", {"carrier": " example  MUTUAL"})
    assert not request_agrees(carrier, "missing_loss_run", {"carrier": "Example Mutual"})


def test_nbe_top_k_counts_only_problems_with_a_nonzero_voi_item():
    planted = [
        REVENUE_GAP,
        problem("PB-02", "fein_mismatch", "corrected_application", classification="benign"),
        problem("PB-03", "debt_payment_mismatch", "debt_schedule_update", classification="benign"),
        problem("PB-04", "balance_discontinuity", "bank_verified_statement"),
    ]
    items = [
        item("OI-fein_mismatch", "fein_mismatch", "identity_reverification"),  # wrong request for the truth
        item("OI-revenue_gap", "revenue_gap", "revenue_source_documentation"),
        item("OI-debt_payment_mismatch", "debt_payment_mismatch", "debt_schedule_update"),
    ]
    requests = [
        ranked(1, "identity_reverification", ("OI-fein_mismatch",), 0.7),
        ranked(2, "revenue_source_documentation", ("OI-revenue_gap",), 0.4),
        ranked(3, "debt_schedule_update", ("OI-debt_payment_mismatch",), 0.0),
    ]
    rows = nbe_rows(planted, items, requests)
    by_id = {row["problem_id"]: row for row in rows}
    assert by_id["PB-01"]["truth_request_rank"] == 2 and by_id["PB-01"]["nonzero_voi"]
    assert by_id["PB-02"]["truth_request_rank"] is None and not by_id["PB-02"]["mapping_agrees"]
    assert not by_id["PB-03"]["nonzero_voi"]  # VOI 0: not eligible
    assert by_id["PB-04"]["item_ids"] == []  # not detected
    counts = nbe_counts(rows)
    assert counts["nbe.problems"] == 4
    assert counts["nbe.with_item"] == 3
    assert counts["nbe.mapping_agrees"] == 2
    assert counts["nbe.eligible"] == 2  # PB-01 and PB-02
    assert counts["nbe.top_1"] == 0
    assert counts["nbe.top_3"] == 1  # PB-01 at rank 2


def test_document_ids_are_translated_to_the_truths():
    extraction = SimpleNamespace(
        documents=[SimpleNamespace(doc_id="D01", pages=[1, 2]), SimpleNamespace(doc_id="D02", pages=[3])]
    )
    mapping = document_id_map(extraction, {1: "D01", 2: "D01", 3: "D04"})
    assert mapping == {"D01": "D01", "D02": "D04"}
    translated = translate_items(
        [item("OI-x", "loss_run_total_mismatch", "corrected_loss_run", doc_ids=("D02",))], mapping
    )
    assert translated[0].doc_ids == ("D04",)


@pytest.fixture
def approve_case() -> SimCase:
    """APP-C-001 (approve under the clean screening assumption) with one planted revenue-gap problem whose latent
    truth is a stated revenue of $3,000,000 (a gap of 70.0%: R-NBC-02, the recommendation becomes refer)."""
    state = from_application(application_dict("APP-C-001"), document_checks=[], injection_flags=[])
    planted = problem("PB-01", "revenue_gap", "revenue_source_documentation", latent={"stated_annual_revenue": 3e6})
    return SimCase(truth=SimpleNamespace(problems=[planted]), extraction=None, context=None, initial=state)


def test_top_request_reveal_changes_the_recommendation(approve_case):
    items = [item("OI-revenue_gap", "revenue_gap", "revenue_source_documentation")]
    row, counts = top_request_reveal(
        approve_case, items, [ranked(1, "revenue_source_documentation", ("OI-revenue_gap",), 0.5)], "approve"
    )
    assert row["outcome"] == "revealed" and row["changed"] and row["recommendation_after"] == "refer"
    assert counts["reveal.all.revealed"] == 1 and counts["reveal.all.changed"] == 1
    assert counts["reveal.voi_positive.changed"] == 1


def test_top_request_reveal_edge_outcomes(approve_case):
    items = [item("OI-revenue_gap", "revenue_gap", "revenue_source_documentation")]
    top = [ranked(1, "revenue_source_documentation", ("OI-revenue_gap",), 0.0)]
    assert top_request_reveal(approve_case, items, [], "approve")[0]["outcome"] == "no_request"
    unmatched = [item("OI-fein_mismatch", "fein_mismatch", "corrected_application")]
    other = [ranked(1, "corrected_application", ("OI-fein_mismatch",), 0.0)]
    assert top_request_reveal(approve_case, unmatched, other, "approve")[0]["outcome"] == "no_truth_problem"
    broken = SimCase(
        truth=SimpleNamespace(
            problems=[problem("PB-01", "revenue_gap", "revenue_source_documentation", latent={"no.such.path": 1})]
        ),
        extraction=None,
        context=None,
        initial=approve_case.initial,
    )
    row, counts = top_request_reveal(broken, items, top, "approve")
    assert row["outcome"] == "failed" and counts["reveal.failed"] == 1
    assert counts["reveal.all.revealed"] == 0  # a failed reveal is not in the denominator


# ----------------------------------------------------------------------------------------------------------------
# evaluation_packet.py on generated JSON-only packets (oracle extraction)
# ----------------------------------------------------------------------------------------------------------------


def as_pipeline_result(truth_model) -> PacketResult:
    """A PacketResult built from the oracle extraction (what a perfect pipeline would return)."""
    extraction = parse_extraction(truth_model.oracle_extraction)
    hits = page_hits(truth_model)
    checks = run_checks(extraction, hits)
    return PacketResult(
        packet_id=truth_model.packet_id,
        line=truth_model.line,
        extractor="rules",
        ocr_mode="auto",
        extraction=extraction,
        checks=checks,
        contradictions=build_contradictions(extraction, checks),
        injection_hits=hits,
        page_texts=[],
        page_types=[],
        timings_ms={},
        ocr_pages=[],
        tesseract_version=None,
        llm_usage={},
    )


def test_oracle_record_on_a_revenue_gap_packet():
    packet = single_problem_packet("credit", "revenue_gap")
    record = evaluate_case(oracle_case(packet), ORACLE, reference_result(packet))
    json.dumps(record, allow_nan=False)  # JSON-simple
    counts = record["counts"]
    assert record["evaluable"] and counts["packets.evaluable"] == 1
    assert counts["claims.by_problem.revenue_gap.total"] == 2
    assert counts["claims.by_problem.revenue_gap.correct"] == 2
    assert counts["calcs.all.agree"] == counts["calcs.all.total"] > 0  # oracle extraction: calcs as the truth's
    assert counts["nbe.problems"] == len(packet.problems)
    assert record["stability"] is not None


def test_a_perfect_pipeline_result_scores_like_the_oracle():
    packet = single_problem_packet("insurance", "tiv_mismatch")
    reference = reference_result(packet)
    oracle = evaluate_case(oracle_case(packet), ORACLE, reference)
    pipeline = evaluate_case(pipeline_case(packet, as_pipeline_result(packet)), PIPELINE, reference)
    assert pipeline["mode"] == PIPELINE
    assert pipeline["counts"] == oracle["counts"]


def test_an_application_that_does_not_validate_is_counted_not_guessed():
    packet = single_problem_packet("credit", "revenue_gap")
    case = oracle_case(packet)
    broken = dict(case.initial.application)
    broken["requested_amount"] = None
    state = case.initial.model_copy(update={"application": broken})
    record = evaluate_case(SimCase(packet, case.extraction, case.context, state), ORACLE, reference_result(packet))
    counts = record["counts"]
    assert not record["evaluable"] and record["not_evaluable_reason"]
    assert counts["claims.confusion.CONFLICTING.ABSENT"] == 2  # the required claims are absent
    assert counts["reveal.not_evaluable"] == 1
    assert counts["calcs.outcome.missing"] == counts["calcs.all.total"]


# ----------------------------------------------------------------------------------------------------------------
# evaluation_summary.py
# ----------------------------------------------------------------------------------------------------------------


def record(packet_id: str, line: str, counts: dict) -> dict:
    """A minimal per-packet record."""
    return {"packet_id": packet_id, "line": line, "counts": counts, "excluded_by_design": {"MODEL-": 1}}


def test_summary_sums_counts_over_packets():
    records = [
        record("P1", "credit", {"packets.total": 1, "packets.evaluable": 1, "claims.total": 10, "claims.correct": 9}),
        record("P2", "insurance", {"packets.total": 1, "packets.evaluable": 0, "claims.total": 2, "claims.correct": 0}),
    ]
    summary = summarize_mode(records)
    assert summary["n_packets"] == 2
    assert summary["packets_by_line"] == {"credit": 1, "insurance": 1}
    accuracy = summary["claim_status"]["accuracy"]
    assert (accuracy["numerator"], accuracy["denominator"], accuracy["value"]) == (9, 12, 0.75)
    assert accuracy["ci_low"] <= 0.75 <= accuracy["ci_high"]
    assert summary["evaluable"]["value"] == 0.5
    assert summary["claim_status"]["excluded_by_design"]["MODEL-"]["claims"] == 2


def test_counter_helpers_and_paired_difference():
    records = [record("P1", "credit", {"calcs.outcome.agree": 3, "calcs.outcome.missing": 1})]
    assert counter_group(records, "calcs.outcome.") == {"agree": 3, "missing": 1}
    assert nested_counts({"a.b": 1, "a.c": 2, "d.e": 3}) == {"a": {"b": 1, "c": 2}, "d": {"e": 3}}
    first = [record("P1", "credit", {"x": 1, "n": 2}), record("P2", "credit", {"x": 2, "n": 2})]
    second = [record("P1", "credit", {"x": 2, "n": 2}), record("P2", "credit", {"x": 2, "n": 2})]
    difference = paired_ratio_difference(first, second, "x", "n")
    assert difference["value"] == -0.25  # 3/4 - 4/4
    assert difference["ci_low"] <= -0.25 <= difference["ci_high"] <= 0.0


# ----------------------------------------------------------------------------------------------------------------
# Table v2 (review finding): dependency-aware expectations and the same-author label
# ----------------------------------------------------------------------------------------------------------------


def test_a_missing_debt_schedule_makes_the_debt_service_ratios_unknown():
    table = expected_claims(truth([], missing=["debt_schedule"]))
    for claim_id in ("CALC-annual_debt_service", "CALC-dscr", "CALC-leverage", "CALC-stressed_dscr_revenue"):
        assert table[claim_id].status == UNKNOWN and not table[claim_id].required, claim_id


def test_missing_credit_report_and_loss_run_totals_expect_unknown_by_pattern():
    case = truth([problem("PB-01", "loss_run_total_mismatch", "corrected_loss_run")], missing=["credit_report"])
    table = expected_claims(case)
    assert table["CALC-min_principal_credit_score"].status == UNKNOWN
    assert table["CALC-loss_ratio_5yr_property"].status == UNKNOWN
    patterns = expected_patterns(case)
    predicted = {
        "APP-owners.0.credit_score": UNKNOWN,
        "APP-owners.1.bankruptcy_within_7_years": UNKNOWN,
        "APP-loss_runs.years.2023-11-01": UNKNOWN,
        "APP-owners.0.is_control_person": UNKNOWN,  # no rule expects it: VERIFIED expected, so wrong
    }
    scores, _ = score_claims(predicted, table, patterns)
    by_id = {score.claim_id: score for score in scores}
    assert by_id["APP-owners.0.credit_score"].correct and by_id["APP-owners.1.bankruptcy_within_7_years"].correct
    assert by_id["APP-loss_runs.years.2023-11-01"].correct and not by_id["APP-owners.0.is_control_person"].correct


def test_undocumented_location_fields_are_excluded_by_design_and_the_table_carries_its_label():
    scores, excluded = score_claims({"APP-locations.0.wind_tier": UNKNOWN, "APP-locations.1.flood_zone": UNKNOWN}, {})
    assert scores == [] and excluded == {LOCATION_DEFAULTS_KEY: 2}
    assert expected_table()["version"] == "evidence-claim-status-table-v2"
    assert "same author" in expected_table()["label"] and "same author" in CLAIM_STATUS_LABEL

"""evidence/voi.py: the rules-based, scenario-based value-of-information score, hand-checked on SYNTHETIC cases.

VOI(request) = sum_r p(r) x 1[rec(r) != rec now] x size(r) / cost weight, size(r) = 1 + min(|delta metric|, 1.0)."""

import pytest
from evidence_case_helpers import (
    CASE_NUMBERS,
    LINE_PREFIX,
    application_dict,
    generated_truth,
    oracle_case,
    single_problem_packet,
)

from uwcopilot.evidence.open_item_models import OpenItem, make_item
from uwcopilot.evidence.open_items import detect_open_items, packet_context
from uwcopilot.evidence.requests import ResolutionChoice, cost_weight
from uwcopilot.evidence.scenarios import decision_metric
from uwcopilot.evidence.state import CaseState, apply, evaluate, from_application
from uwcopilot.evidence.voi import nonzero_requests, options, rank_requests, request_value, voi


def debt_item(state: CaseState) -> OpenItem:
    """The debt_payment_mismatch item for a state (as open_items builds it from a debt contradiction)."""
    debt = state.application["debt"]
    return make_item(
        "debt_payment_mismatch",
        "CONFLICTING",
        template_key="debt_payment_mismatch",
        request_id="debt_schedule_update",
        placeholders={
            "observed_payment": debt["existing_monthly_payment_observed"],
            "scheduled_payment": debt["existing_monthly_payment_schedule"],
        },
    )


def with_debt(application_id: str, observed: float) -> CaseState:
    """A committed application whose observed monthly debt payment is replaced."""
    application = application_dict(application_id)
    application["debt"]["existing_monthly_payment_observed"] = observed
    return from_application(application)


def fein_flagged(application_id: str) -> CaseState:
    """A committed application with a confirmed FEIN difference between documents (blocking R-NBC-09)."""
    return apply(from_application(application_dict(application_id)), {"check:fein_consistency": "confirmed"})


def fein_item(request_id: str) -> OpenItem:
    """The fein_mismatch item with a given request (corrected_application or identity_reverification)."""
    return make_item("fein_mismatch", "CONFLICTING", template_key="fein_mismatch", request_id=request_id)


# --------------------------------------------------------------------------------------------------------------
# Zero value when no resolution changes the recommendation
# --------------------------------------------------------------------------------------------------------------


def test_debt_payment_mismatch_has_exactly_zero_value_when_the_observed_payment_is_the_higher() -> None:
    # NBC-4.1 already uses max(scheduled, observed). Observed 1,800.00 > scheduled 1,500.00: setting the schedule to
    # either figure leaves max(...) = 1,800.00, so the decision DSCR and the recommendation cannot move.
    state = with_debt("APP-C-001", 1_800.0)
    item = debt_item(state)
    outcomes = options(item, state)
    assert [outcome.option_id for outcome in outcomes] == ["observed_is_current", "schedule_is_current"]
    baseline = decision_metric(evaluate(state)).value
    assert [outcome.metric for outcome in outcomes] == [baseline, baseline]
    assert all(not outcome.changes_recommendation for outcome in outcomes)
    assert voi(item, state) == 0.0
    ranked = rank_requests(state, [item])
    assert ranked[0].voi == 0.0 and ranked[0].p_change == 0.0
    assert "zero decision value" in ranked[0].reason and nonzero_requests(ranked) == []


def test_the_generated_debt_payment_mismatch_packet_has_zero_value() -> None:
    case = oracle_case(single_problem_packet("credit", "debt_payment_mismatch"))
    debt = case.state.application["debt"]
    assert debt["existing_monthly_payment_observed"] >= debt["existing_monthly_payment_schedule"]
    items = detect_open_items(case.state, packet_context(case.extraction, case.checks, case.report))
    item = next(item for item in items if item.problem_type == "debt_payment_mismatch")
    assert voi(item, case.state) == 0.0


def test_a_lower_observed_payment_can_carry_value_so_zero_is_not_universal() -> None:
    # APP-C-009 (term loan, minimum DSCR 1.25, floor 1.10): NOI 36,355.00, existing service 12 x 1,500.00 =
    # 18,000.00 (the schedule is the higher figure), proposed 17,641.56, DSCR 36,355.00 / 35,641.56 = 1.02 ->
    # decline_for_review. With an observed payment of 750.00, "observed is current" (prior 0.7) sets the schedule to
    # 750.00: 12 x 750 = 9,000 + 17,641.56 = 26,641.56 and DSCR 36,355.00 / 26,641.56 = 1.36 -> approve.
    # VOI = 0.7 x (1 + |1.36 - 1.02|) / 1.0 = 0.7 x 1.34 = 0.938.
    state = with_debt("APP-C-009", 750.0)
    assert evaluate(state).recommendation == "decline_for_review"
    value = request_value(state, [debt_item(state)])
    observed = next(outcome for outcome in value.outcomes if outcome.option_id == "observed_is_current")
    assert observed.recommendation == "approve" and observed.metric == 1.36
    assert value.voi == pytest.approx(0.938, abs=1e-6)


# --------------------------------------------------------------------------------------------------------------
# Hand-checked values
# --------------------------------------------------------------------------------------------------------------


def test_voi_divides_the_changed_prior_mass_by_the_cost_weight() -> None:
    # A confirmed FEIN difference makes APP-C-001 "refer" (blocking). typo_confirmed (prior 0.7) clears it -> approve
    # with the same DSCR 2.20, so size = 1 + 0 = 1; identity_issue (0.3) keeps refer. VOI = 0.7 x 1 / cost weight.
    state = fein_flagged("APP-C-001")
    assert evaluate(state).recommendation == "refer"
    assert voi(fein_item("corrected_application"), state) == pytest.approx(0.7 / 1.0)
    assert voi(fein_item("identity_reverification"), state) == pytest.approx(0.7 / 2.5)
    value = request_value(state, [fein_item("corrected_application")])
    assert value.p_change == pytest.approx(0.7)


def test_the_change_size_grows_with_the_decision_metric_move_capped_at_one() -> None:
    # APP-C-001 with stated revenue 1,200,000.00 vs verified 900,000.00: a 25.0% gap -> refer (R-NBC-02).
    # verified_is_right (0.6): gap 0, DSCR stays 2.20 -> approve, size 1.00.
    # stated_is_right and midpoint (0.2 each): verified revenue rises, NOI = EBITDA 117,336.00 (no shortfall),
    # DSCR 117,336.00 / 40,682.01 = 2.88 -> approve, size 1 + 0.68 = 1.68.
    # VOI = (0.6 x 1.00 + 0.2 x 1.68 + 0.2 x 1.68) / 3.0 (revenue_source_documentation) = 1.272 / 3.0 = 0.424.
    application = application_dict("APP-C-001")
    application["stated_annual_revenue"] = 1_200_000.0
    state = from_application(application)
    items = detect_open_items(state)
    value = request_value(state, items)
    sizes = {outcome.option_id: outcome.size for outcome in value.outcomes}
    assert sizes == pytest.approx({"verified_is_right": 1.0, "stated_is_right": 1.68, "midpoint": 1.68})
    assert value.voi == pytest.approx(0.424, abs=1e-6)
    assert cost_weight("revenue_source_documentation") == 3.0


def custom_item(request_id: str) -> OpenItem:
    """An item whose resolutions the test supplies (choices_by_item)."""
    return make_item("revenue_gap", "CONFLICTING", request_id, template_key="revenue_gap", request_id=request_id)


def test_the_reason_quotes_the_metric_move_and_the_probability() -> None:
    state = from_application(application_dict("APP-C-001"))
    item = custom_item("revenue_source_documentation")
    # EBITDA cut so NOI = 117,336 - 60,000 - 27,835 = 29,501.00 and DSCR = 29,501.00 / 40,682.01 = 0.73 -> decline.
    choices = {
        item.item_id: [
            ResolutionChoice(
                option_id="lower", description="lower EBITDA", prior=0.4, substitutions={"debt.ttm_ebitda": 57_336.0}
            ),
            ResolutionChoice(option_id="same", description="no change", prior=0.6, substitutions={}),
        ]
    }
    ranked = rank_requests(state, [item], choices_by_item=choices)
    assert ranked[0].reason == (
        'Requesting "Documentation for the stated revenue" could move the decision DSCR from 2.20 to 0.73, which '
        "changes approve -> decline_for_review (probability 0.40 under the documented priors)."
    )
    # size = 1 + min(|0.73 - 2.20|, 1.0) = 2.0; VOI = 0.4 x 2.0 / 3.0
    assert ranked[0].voi == pytest.approx(0.4 * 2.0 / 3.0, abs=1e-6)


def test_a_resolution_that_cannot_be_applied_is_listed_with_its_error_and_counts_as_no_change() -> None:
    state = from_application(application_dict("APP-C-001"))
    item = custom_item("revenue_source_documentation")
    choices = {
        item.item_id: [
            ResolutionChoice(option_id="broken", description="bad lever", prior=1.0, substitutions={"no.such.path": 1})
        ]
    }
    value = request_value(state, [item], choices_by_item=choices)
    assert value.outcomes[0].error == "LeverError" and not value.outcomes[0].changes_recommendation
    assert value.voi == 0.0


# --------------------------------------------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------------------------------------------


def two_request_case() -> tuple[CaseState, list[OpenItem], dict[str, list[ResolutionChoice]]]:
    """APP-C-001 with two supplied items that can each change approve -> decline_for_review."""
    state = from_application(application_dict("APP-C-001"))
    first, second = custom_item("revenue_source_documentation"), custom_item("debt_schedule_update")
    lower = {"debt.ttm_ebitda": 57_336.0}
    choices = {
        first.item_id: [
            ResolutionChoice(option_id="lower", description="", prior=0.5, substitutions=lower),
            ResolutionChoice(option_id="same", description="", prior=0.5, substitutions={}),
        ],
        second.item_id: [
            ResolutionChoice(option_id="lower", description="", prior=0.3, substitutions=lower),
            ResolutionChoice(option_id="same", description="", prior=0.7, substitutions={}),
        ],
    }
    return state, [first, second], choices


@pytest.mark.parametrize("other_weight", [1.0, 3.0])
def test_ranking_is_monotone_in_the_cost_weight(other_weight: float) -> None:
    state, items, choices = two_request_case()
    ranks, scaled = [], []
    for weight in (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0):
        weights = {"revenue_source_documentation": weight, "debt_schedule_update": other_weight}
        ranked = rank_requests(state, items, choices_by_item=choices, cost_weights=weights)
        mine = next(request for request in ranked if request.request_id == "revenue_source_documentation")
        ranks.append(mine.rank)
        scaled.append(round(mine.voi * weight, 6))
    assert ranks == sorted(ranks)  # a higher cost weight never improves the rank
    assert len(set(scaled)) == 1  # VOI x cost weight is constant: the score divides by the weight
    assert ranks[0] == 1 and ranks[-1] == 2


def test_missing_documents_share_one_request_and_are_valued_together() -> None:
    # Two documents missing: each one alone leaves the file incomplete (request more information), so each scores 0
    # alone; one checklist request that brings both can complete the file.
    application = application_dict("APP-C-001")
    missing = {"principal_photo_ids", "business_tax_returns_2_years"}
    application["documents_provided"] = [code for code in application["documents_provided"] if code not in missing]
    state = from_application(application)
    items = [item for item in detect_open_items(state) if item.problem_type == "missing_document"]
    assert len(items) == 2
    assert all(voi(item, state) == 0.0 for item in items)
    ranked = rank_requests(state, items)
    assert len(ranked) == 1 and set(ranked[0].item_ids) == {item.item_id for item in items}
    # One option per document (provided_consistent; provided_inconsistent is left out because no policy rule reads
    # its check lever), so 1 x 1 combination.
    assert len(ranked[0].options_summary) == 1
    assert ranked[0].p_change == pytest.approx(1.0) and ranked[0].voi == pytest.approx(1.0 / 1.5)


def test_the_lookahead_orders_and_explains_zero_value_requests() -> None:
    # A blocking FEIN difference keeps "refer" whatever the missing photo ID shows, so the ID request scores 0 alone.
    # With the FEIN difference resolved as most likely (a typo), the ID decides between "more information" and approve.
    application = application_dict("APP-C-001")
    application["documents_provided"] = [c for c in application["documents_provided"] if c != "principal_photo_ids"]
    state = apply(from_application(application), {"check:fein_consistency": "confirmed"})
    items = detect_open_items(state)
    ranked = rank_requests(state, items)
    by_request = {request.request_id: request for request in ranked}
    # No contradiction explains the FEIN difference as a typo, so the request is the re-verification.
    assert by_request["identity_reverification"].voi > 0
    document = by_request["missing_document"]
    assert document.voi == 0.0 and document.voi_given_others is not None and document.voi_given_others > 0
    assert document.recommendation_given_others == "request_more_information"
    assert "zero decision value on its own" in document.reason
    assert [request.request_id for request in nonzero_requests(ranked)] != ["missing_document"]


def test_scores_are_bounded_on_generated_packets() -> None:
    for line in ("credit", "insurance"):
        for number in list(CASE_NUMBERS)[:12]:
            case = oracle_case(generated_truth(f"PKT-{LINE_PREFIX[line]}-{number}", line))
            items = detect_open_items(case.state, packet_context(case.extraction, case.checks, case.report))
            for request in rank_requests(case.state, items):
                assert request.voi >= 0.0 and 0.0 <= request.p_change <= 1.0 + 1e-6
                assert (request.voi == 0.0) == (request.p_change == 0.0)
                assert sum(option["prior"] for option in request.options_summary) == pytest.approx(1.0, abs=1e-4)


# --------------------------------------------------------------------------------------------------------------
# Review findings: an insurance value by hand, and a request with too many combinations degrades instead of failing
# --------------------------------------------------------------------------------------------------------------


def test_insurance_voi_uses_the_loss_ratio_as_a_fraction() -> None:
    # APP-I-008 (package; property loss ratio decides): property earned premium 11,862 + 12,120 + 12,378 + 12,636 +
    # 12,893 = 61,889; property incurred 11,625 + 11,878 + 12,130 = 35,633; 35,633 / 61,889 = 57.6% > 55.0%: refer.
    # Option "lower" (prior 0.5) corrects the 2023-02-09 claim to 2,130: 25,633 / 61,889 = 41.4% -> approve.
    # Option "as_shown" (prior 0.5) keeps it -> refer, no change.
    # size = 1 + |41.4 - 57.6| / 100 = 1 + 0.162 = 1.162 (the percent difference as a FRACTION, capped at 1.0);
    # VOI = 0.5 x 1.162 / 2.0 (corrected_loss_run) = 0.2905.
    state = from_application(application_dict("APP-I-008"))
    assert evaluate(state).recommendation == "refer"
    item = make_item(
        "paid_reserve_mismatch", "CONFLICTING", template_key="paid_reserve_mismatch", request_id="corrected_loss_run"
    )
    lower = {
        "loss_runs.years[2].claims[0].paid": 2_130.0,
        "loss_runs.years[2].claims[0].incurred": 2_130.0,
        "stated_total_incurred": 29_863.0,
    }
    replacement = {
        item.item_id: [
            ResolutionChoice(option_id="lower", description="lower", prior=0.5, substitutions=lower),
            ResolutionChoice(option_id="as_shown", description="as shown", prior=0.5, substitutions={}),
        ]
    }
    value = request_value(state, [item], choices_by_item=replacement)
    outcomes = {outcome.option_id: outcome for outcome in value.outcomes}
    assert value.current_metric == 57.6 and outcomes["lower"].metric == 41.4
    assert outcomes["lower"].recommendation == "approve" and outcomes["lower"].size == pytest.approx(1.162)
    assert not outcomes["as_shown"].changes_recommendation
    assert cost_weight("corrected_loss_run") == 2.0
    assert value.voi == pytest.approx(0.2905, abs=1e-6)


def test_a_request_with_too_many_combinations_is_not_scored_and_does_not_fail() -> None:
    # Ten out-of-balance claim lines on one loss run: 2^10 = 1,024 combinations of the paid/reserve options exceed
    # the cap of 729. Document amounts are untrusted input, so the engine must degrade, not raise.
    application = application_dict("APP-I-008")
    claims = application["loss_runs"]["years"][4]["claims"]
    template = dict(application["loss_runs"]["years"][2]["claims"][0])
    while sum(len(year["claims"]) for year in application["loss_runs"]["years"]) < 10:
        claims.append(dict(template, date_of_loss="2025-02-09"))
    for year in application["loss_runs"]["years"]:
        for line in year["claims"]:
            line["reserve"] = round(line["reserve"] + 100.0, 2)
    state = from_application(application, injection_flags=[])
    items = [item for item in detect_open_items(state) if item.problem_type == "paid_reserve_mismatch"]
    assert len(items) >= 10
    value = request_value(state, items)
    assert not value.computable and value.voi == 0.0 and "exceed the cap of 729" in str(value.not_computable_reason)
    ranked = rank_requests(state, items)
    assert ranked[0].voi == 0.0 and "is not scored: 1024 combinations" in ranked[0].reason

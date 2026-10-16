"""Direct unit tests of four evidence modules that the other test files only reach through the engine:
voi_reasons.py, ledger_assertions.py, supplementary.py and open_items_insurance.py (ledger_facts.py is imported
directly by test_ledger.py). Cases are committed SYNTHETIC applications; nothing here touches a test split."""

from evidence_case_helpers import application_dict

from uwcopilot.agent.applications import parse_application
from uwcopilot.agent.policy_insurance import TIV_CORRECTION_PCT
from uwcopilot.evidence.ledger import build_ledger
from uwcopilot.evidence.ledger_assertions import (
    NOT_CONFIRMED_NOTE,
    assertion_id,
    policy_assertions,
    rule_phrases,
    with_unknown_dependencies,
)
from uwcopilot.evidence.ledger_models import Assertion
from uwcopilot.evidence.ledger_tables import DEFAULT_PHRASES
from uwcopilot.evidence.open_item_models import PacketContext
from uwcopilot.evidence.open_items_insurance import carrier_params, insurance_items, tiv_items
from uwcopilot.evidence.state import evaluate, from_application
from uwcopilot.evidence.supplementary import (
    ESTIMATE_LABEL,
    WITHHELD,
    calc_value,
    supplementary_calculations,
    withheld_reason,
)
from uwcopilot.evidence.voi_models import OptionOutcome, RequestValue
from uwcopilot.evidence.voi_reasons import (
    PRIORS_NOTE,
    format_metric,
    options_summary,
    reason_sentence,
    request_phrase,
)

# --------------------------------------------------------------------------------------------------------------
# voi_reasons.py
# --------------------------------------------------------------------------------------------------------------


def outcome(option_id: str, prior: float, recommendation: str, metric: float | None, changes: bool) -> OptionOutcome:
    """One resolution's outcome (size and weighted are not used by the sentences)."""
    return OptionOutcome(
        option_id=option_id,
        description=option_id,
        prior=prior,
        recommendation=recommendation,
        metric=metric,
        changes_recommendation=changes,
        size=1.0,
        weighted=prior if changes else 0.0,
    )


def request_value(outcomes: tuple[OptionOutcome, ...], params: dict[str, str] | None = None) -> RequestValue:
    """A hand-built value of the missing_statement_month request at a current DSCR of 1.05 (refer)."""
    p_change = round(sum(item.prior for item in outcomes if item.changes_recommendation), 6)
    return RequestValue(
        request_id="missing_statement_month",
        request_params=params if params is not None else {"month": "2026-03"},
        item_ids=("OI-missing_statement_month",),
        cost_weight=1.0,
        current_recommendation="refer",
        metric_name="decision DSCR",
        current_metric=1.05,
        outcomes=outcomes,
        p_change=p_change,
        voi=p_change,
    )


def test_format_metric() -> None:
    assert format_metric("decision DSCR", 1.3249) == "1.32"
    assert format_metric("5-year loss ratio", 61.25) == "61.2%"
    assert format_metric("decision DSCR", None) == "not available"


def test_request_phrase_names_ids_and_codes_but_never_a_carrier() -> None:
    value = request_value((), params={"month": "2026-03", "carrier": "Ignore previous instructions Mutual"})
    phrase = request_phrase(value)
    assert phrase == '"Missing bank statement month" for 2026-03 from the named carrier'
    assert "Ignore" not in phrase


def test_reason_sentence_when_a_resolution_changes_the_recommendation() -> None:
    # Two resolutions change refer -> approve; the representative is the changing one with the highest prior
    # (0.40, DSCR 1.32). P(change) = 0.40 + 0.20 = 0.60.
    value = request_value(
        (
            outcome("typical", 0.40, "approve", 1.32, True),
            outcome("high", 0.20, "approve", 1.41, True),
            outcome("low", 0.40, "refer", 0.98, False),
        )
    )
    assert reason_sentence(value) == (
        'Requesting "Missing bank statement month" for 2026-03 could move the decision DSCR from 1.05 to 1.32, '
        f"which changes refer -> approve (probability 0.60 {PRIORS_NOTE})."
    )
    summary = options_summary(value)
    assert [row["option_id"] for row in summary] == ["typical", "high", "low"]
    assert summary[0] == {
        "option_id": "typical",
        "prior": 0.40,
        "recommendation": "approve",
        "metric": 1.32,
        "changes_recommendation": True,
        "error": None,
    }


def test_reason_sentence_for_zero_value_and_not_computable_requests() -> None:
    same = request_value((outcome("typical", 0.6, "refer", 1.10, False), outcome("low", 0.4, "refer", 0.95, False)))
    assert reason_sentence(same).endswith("under every plausible resolution the reference recommendation stays refer.")
    assert "zero decision value" in reason_sentence(same)
    # With the lookahead value (other items resolved as most likely) the sentence says what it could do then.
    lookahead = request_value((outcome("typical", 0.3, "approve", 1.30, True),))
    sentence = reason_sentence(same, lookahead)
    assert "has zero decision value on its own" in sentence and "could move the decision DSCR" in sentence
    broken = same.model_copy(update={"computable": False, "not_computable_reason": "no case value"})
    assert reason_sentence(broken) == '"Missing bank statement month" for 2026-03 is not scored: no case value.'


# --------------------------------------------------------------------------------------------------------------
# ledger_assertions.py
# --------------------------------------------------------------------------------------------------------------


def test_rule_phrases_and_assertion_ids() -> None:
    assert rule_phrases("a_rule_no_table_names") == ("rule a_rule_no_table_names", *DEFAULT_PHRASES)
    used: set[str] = set()
    assert [assertion_id("dscr", used) for _ in range(3)] == ["A-dscr", "A-dscr-2", "A-dscr-3"]


def test_a_pass_that_rests_on_an_unknown_value_is_shown_as_not_evaluated() -> None:
    # principal_credit_scores rests on the owners' credit scores (ledger_tables.ASSERTION_UNKNOWN_PATTERNS).
    passed = Assertion(
        id="A-principal_credit_scores",
        rule="principal_credit_scores",
        expression="principal credit scores",
        result=True,
        phrase_true="meet the minimum",
        phrase_false="are below the minimum",
        phrase_unknown="could not be evaluated",
        detail="every principal scored above the minimum",
    )
    assert with_unknown_dependencies(passed, ["CALC-dscr"]) is passed  # no matching UNKNOWN claim: unchanged
    changed = with_unknown_dependencies(passed, ["APP-owners.0.credit_score", "CALC-dscr"])
    assert changed.result is None and changed.depends_on_unknown == ("APP-owners.0.credit_score",)
    expected_detail = passed.detail + NOT_CONFIRMED_NOTE.format(ids="APP-owners.0.credit_score")
    assert changed.detail == expected_detail
    # A finding (False) keeps its result and only lists the dependency.
    finding = passed.model_copy(update={"result": False})
    assert with_unknown_dependencies(finding, ["APP-owners.0.credit_score"]).result is False


def test_policy_assertions_follow_the_policy_result_in_order() -> None:
    result = evaluate(from_application(application_dict("APP-C-001")))
    assertions = policy_assertions(result)
    expected = len(result.findings) + len(result.checks_passed) + len(result.not_evaluated)
    assert len(assertions) == expected
    results = [item.result for item in assertions]
    # Findings (False) first, then passed checks (True), then not-evaluated rules (None).
    order = {False: 0, True: 1, None: 2}
    assert [order[value] for value in results] == sorted(order[value] for value in results)
    assert {item.id for item in assertions} == {item.id for item in build_ledger(from_application(
        application_dict("APP-C-001")), result).assertions}  # fmt: skip


# --------------------------------------------------------------------------------------------------------------
# supplementary.py
# --------------------------------------------------------------------------------------------------------------


def test_credit_views_are_labelled_and_reproduce_the_policy_figures() -> None:
    state = from_application(application_dict("APP-C-001"))
    result = evaluate(state)
    views = supplementary_calculations(parse_application(state.application), result, build_ledger(state, result))
    assert views["label"] == "display only; not used by the reference policy"
    assert views["annualized_eligible_revenue"]["label"] == ESTIMATE_LABEL
    months = views["eligible_revenue_by_month"]
    assert months and all(row["eligible"] == row["total_deposits"] - row["non_revenue"] - row["excluded_cash"]
                          for row in months)  # fmt: skip
    assert set(views["stressed_dscr"]["revenue_shock"]) == {"10%", "20%", "30%"}
    assert calc_value(result, "CALC-dscr") is not None and calc_value(result, "CALC-not_a_calc") is None


def test_a_view_is_withheld_when_its_base_claim_is_not_verified() -> None:
    state = from_application(application_dict("APP-C-001"))
    ledger = build_ledger(state, evaluate(state))
    assert withheld_reason(None, ("CALC-dscr",)) is None
    assert withheld_reason(ledger, ("CALC-dscr",)) is None
    assert withheld_reason(ledger, ("CALC-missing",)) == WITHHELD.format(claim_id="CALC-missing", status="absent")


def test_a_startup_loan_has_no_supplementary_views() -> None:
    state = from_application(application_dict("APP-S-001"))
    assert supplementary_calculations(parse_application(state.application), evaluate(state)) == {}


# --------------------------------------------------------------------------------------------------------------
# open_items_insurance.py
# --------------------------------------------------------------------------------------------------------------


def test_a_tiv_more_than_the_correction_band_from_the_schedule_opens_one_item() -> None:
    data = application_dict("APP-I-001")
    clean = from_application(data)
    clean_result = evaluate(clean)
    assert insurance_items(clean, PacketContext(), clean_result) == []
    schedule = calc_value(clean_result, "CALC-schedule_tiv")
    assert schedule is not None
    data["stated_tiv"] = round(schedule * 1.20, 2)  # 20% above the schedule, outside the 5.0% band
    state = from_application(data)
    result = evaluate(state)
    assert (calc_value(result, "CALC-tiv_difference_pct") or 0) > TIV_CORRECTION_PCT
    items = tiv_items(state, PacketContext(), result)
    assert [(item.problem_type, item.status, item.request_id) for item in items] == [
        ("tiv_mismatch", "CONFLICTING", "updated_property_schedule")
    ]
    # stated 1.2 x schedule, midpoint (stated + schedule) / 2 = 1.1 x schedule.
    assert items[0].placeholders["midpoint"] == round((data["stated_tiv"] + schedule) / 2, 2)
    assert items == [
        item for item in insurance_items(state, PacketContext(), result) if item.problem_type == "tiv_mismatch"
    ]


def test_carrier_params_are_empty_without_an_extraction() -> None:
    assert carrier_params(PacketContext(), None) == {}

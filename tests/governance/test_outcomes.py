"""governance/outcomes.py: joining SYNTHETIC outcomes to SIMULATED decisions, rates by decision, PD calibration."""

import numpy as np
import pandas as pd
import pytest

from uwcopilot.agent.vocab import RECOMMENDATIONS
from uwcopilot.governance.outcomes import (
    SimulatedDecisionRule,
    decisions_frame,
    join_outcomes,
    outcomes_frame,
    summarize_outcomes,
)
from uwcopilot.risk.bands import band_cutoffs
from uwcopilot.risk.bootstrap import bootstrap_ci
from uwcopilot.risk.metrics import brier_score, expected_calibration_error

N_RESAMPLES = 50


def decision(application_id: str, choice: str, pd_value: float | None = 0.1) -> dict:
    """One SYNTHETIC decision record."""
    return {"application_id": application_id, "decision": choice, "recorded_pd": pd_value, "model_id": "m-1"}


def outcome(application_id: str, defaulted: int) -> dict:
    """One SYNTHETIC outcome record."""
    return {"application_id": application_id, "defaulted": defaulted}


def small_log() -> tuple[list[dict], list[dict]]:
    """Six decisions, four outcomes (one for an application that was never decided)."""
    decisions = [
        decision("A1", "approve", 0.05),
        decision("A2", "approve", 0.10),
        decision("A3", "approve", 0.20),
        decision("R1", "refer", 0.40),
        decision("D1", "decline_for_review", 0.70),
        decision("I1", "request_more_information", 0.15),
    ]
    outcomes = [outcome("A1", 0), outcome("A2", 1), outcome("R1", 1), outcome("X9", 1), outcome("D1", 0)]
    return decisions, outcomes


def test_the_join_counts_pending_decisions_and_unmatched_outcomes() -> None:
    decisions, outcomes = small_log()
    joined = join_outcomes(decisions, outcomes)
    assert (joined.n_decisions, joined.n_outcomes, joined.n_unmatched_outcomes) == (6, 5, 1)
    arrived = dict(zip(joined.joined["application_id"], joined.joined["outcome_arrived"], strict=True))
    assert arrived == {"A1": True, "A2": True, "A3": False, "R1": True, "D1": True, "I1": False}
    assert list(joined.with_outcome["application_id"]) == ["A1", "A2", "R1", "D1"]


def test_default_rate_by_decision_lists_every_decision_and_says_not_measured_without_outcomes() -> None:
    decisions, outcomes = small_log()
    summary = summarize_outcomes(decisions, outcomes, n_resamples=N_RESAMPLES)
    by_decision = summary["default_rate_by_decision"]
    assert list(by_decision) == list(RECOMMENDATIONS)
    approve = by_decision["approve"]
    assert (approve["n_decisions"], approve["n_with_outcome"], approve["n_pending"], approve["n_defaulted"]) == (
        3,
        2,
        1,
        1,
    )
    assert approve["default_rate"]["estimate"] == pytest.approx(0.5)
    assert approve["default_rate"]["n_resamples"] == N_RESAMPLES and approve["default_rate"]["seed"] == 42
    assert approve["mean_recorded_pd_with_outcome"] == pytest.approx(0.075)
    assert by_decision["request_more_information"]["default_rate"]["status"] == "not measured"
    assert summary["join"] == {
        "key": "application_id",
        "n_decisions": 6,
        "n_outcomes_received": 5,
        "n_decisions_with_outcome": 4,
        "n_decisions_pending": 2,
        "n_outcomes_without_decision": 1,
    }
    assert summary["decisions"] == {
        "approve": 3,
        "refer": 1,
        "decline_for_review": 1,
        "request_more_information": 1,
    }


def test_approved_and_defaulted() -> None:
    decisions, outcomes = small_log()
    result = summarize_outcomes(decisions, outcomes, n_resamples=N_RESAMPLES)["approved_and_defaulted"]
    assert result == {
        "n_approved": 3,
        "n_approved_with_outcome": 2,
        "n_approved_and_defaulted": 1,
        "share_of_approved_with_outcome": 0.5,
    }


def test_calibration_uses_only_decisions_with_an_outcome_and_a_recorded_pd() -> None:
    rng = np.random.default_rng(0)
    pds = rng.uniform(0.02, 0.6, 400)
    defaulted = (rng.random(400) < pds).astype(int)
    decisions = [decision(f"S{i}", "approve", float(p)) for i, p in enumerate(pds)]
    decisions.append(decision("NOPD", "refer", None))
    outcomes = [outcome(f"S{i}", int(d)) for i, d in enumerate(defaulted) if i % 4 != 0]
    outcomes.append(outcome("NOPD", 1))
    calibration = summarize_outcomes(decisions, outcomes, n_resamples=N_RESAMPLES)["recorded_pd_calibration"]
    kept = np.array([i % 4 != 0 for i in range(400)])
    observed, predicted = defaulted[kept].astype(float), pds[kept]
    assert calibration["n_rows"] == int(kept.sum())
    assert calibration["decisions_without_recorded_pd"] == 1
    expected_ece = expected_calibration_error(observed, predicted, n_bins=10, strategy="uniform")
    assert calibration["ece"]["estimate"] == pytest.approx(expected_ece)
    assert calibration["brier"]["estimate"] == pytest.approx(brier_score(observed, predicted))
    reference = bootstrap_ci(brier_score, observed, predicted, n_resamples=N_RESAMPLES, seed=42)
    assert calibration["brier"]["ci_low"] == pytest.approx(reference["ci_low"])
    assert calibration["mean_recorded_pd"] == pytest.approx(predicted.mean())
    assert calibration["observed_over_expected"] == pytest.approx(observed.mean() / predicted.mean())
    assert len(calibration["calibration_by_recorded_pd_decile"]) == 10


def test_calibration_is_not_measured_without_outcomes() -> None:
    summary = summarize_outcomes([decision("A1", "approve")], [], n_resamples=N_RESAMPLES)
    assert summary["recorded_pd_calibration"]["status"] == "not measured"
    assert summary["approved_and_defaulted"]["share_of_approved_with_outcome"] is None


@pytest.mark.parametrize(
    ("records", "message"),
    [
        ([decision("A1", "approve"), decision("A1", "refer")], "more than one decision"),
        ([decision("A1", "approve_now")], "unknown decisions"),
        ([decision("A1", "approve", 1.5)], "between 0 and 1"),
        ([decision("", "approve")], "needs an application_id"),
    ],
)
def test_bad_decision_records_are_refused(records: list[dict], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decisions_frame(records)


@pytest.mark.parametrize("value", [2, 0.5, None, float("nan"), "1"])
def test_an_outcome_must_be_zero_or_one(value: object) -> None:
    with pytest.raises(ValueError, match="defaulted must be 0 or 1"):
        outcomes_frame([outcome("A1", value)])  # type: ignore[arg-type]


def test_repeated_outcomes_are_refused() -> None:
    with pytest.raises(ValueError, match="more than one outcome"):
        outcomes_frame([outcome("A1", 0), outcome("A1", 1)])


def rule() -> SimulatedDecisionRule:
    """Refer from PD 0.3, decline from PD 0.6, verify income above loan_to_income 0.3."""
    return SimulatedDecisionRule(refer_from_pd=0.3, decline_from_pd=0.6, max_unverified_loan_to_income=0.3)


def test_the_simulated_rule_follows_its_stated_order() -> None:
    loans = pd.DataFrame(
        {
            "verification_status": [
                "Verified",
                "Not Verified",
                "Not Verified",
                "Source Verified",
                "Not Verified",
                "Verified",
                "Not Verified",
            ],
            "loan_to_income": [0.5, 0.5, 0.5, 0.5, 0.1, 0.2, np.nan],
        }
    )
    pds = np.array([0.6, 0.6, 0.1, 0.3, 0.3, 0.29, 0.1])
    assert list(rule().decide(loans, pds)) == [
        "decline_for_review",  # a PD at the cut-off goes to the riskier side
        "decline_for_review",  # declining comes before asking for more information
        "request_more_information",
        "refer",
        "refer",
        "approve",
        "request_more_information",  # a missing loan_to_income cannot be checked
    ]


def test_the_simulated_rule_takes_its_cutoffs_from_the_risk_bands() -> None:
    bands = band_cutoffs(np.linspace(0.0, 1.0, 101), "probability of default", "SYNTHETIC")
    from_bands = SimulatedDecisionRule.from_bands(bands)
    assert from_bands.refer_from_pd == pytest.approx(0.80)
    assert from_bands.decline_from_pd == pytest.approx(0.95)
    assert from_bands.describe()["note"].startswith("SIMULATED")


def test_the_simulated_rule_needs_one_pd_per_row() -> None:
    loans = pd.DataFrame({"verification_status": ["Verified"], "loan_to_income": [0.1]})
    with pytest.raises(ValueError, match="one PD"):
        rule().decide(loans, np.array([0.1, 0.2]))
    with pytest.raises(ValueError, match="one PD"):
        rule().decide(loans, np.array([np.nan]))

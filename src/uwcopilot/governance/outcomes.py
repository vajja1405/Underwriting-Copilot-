"""Outcome analysis (Phase 6 C): when loan outcomes arrive, join them to the decisions made earlier.

A decision is recorded when an application comes in; whether the loan defaulted is known months or years later. When
outcomes arrive they are joined to the decision records by application id, and three questions are answered:

1. Default rate by decision: of the applications that got each decision (approve, refer, decline_for_review,
   request_more_information), what share defaulted? Percentile bootstrap 95% CI, 1,000 resamples, seed 42; the unit
   is one application with an outcome.
2. Approved and defaulted: how many approved applications defaulted (the losses the approvals let through)?
3. Calibration of the recorded PD: on the decisions that have an outcome, does the PD recorded at decision time match
   the observed default rate? ECE (10 equal-width bins, as in risk/credit.py), Brier score (both with bootstrap CIs),
   mean PD against the observed rate, and a table by predicted-PD decile.

Record shapes (plain mappings, so a decision log or a table can feed them)
    decision: {"application_id": str, "decision": one of agent/vocab.py RECOMMENDATIONS,
               "recorded_pd": a probability in [0, 1] or None, "model_id": str or None}
    outcome:  {"application_id": str, "defaulted": 0 or 1}
    An application id may appear only once in each list; anything else raises ValueError, never a silent choice.
    A decision without an outcome is "pending" (the loan has not finished yet); an outcome without a decision is
    counted as "unmatched" and left out of every rate.

Selective labels: in a real portfolio only booked loans produce outcomes. A declined application never defaults or
repays, so default rates for refer, decline_for_review and request_more_information exist only for files that were
later booked. The SYNTHETIC demonstration (scripts/champion_challenger.py) has outcomes for every decision only
because its rows are all issued loans.

SimulatedDecisionRule is the stated rule that makes the demonstration's SIMULATED decisions. It is not the agent's
reference policy and not a lending policy, and it decides nothing about a real application.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from uwcopilot.agent.vocab import RECOMMENDATIONS
from uwcopilot.risk.bootstrap import bootstrap_ci
from uwcopilot.risk.metrics import brier_score, calibration_table, expected_calibration_error

BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 42
ECE_BINS = 10
NOT_MEASURED = "not measured"
VERIFIED_INCOME = ("Verified", "Source Verified")


def decisions_frame(records: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Decision records as a table (application_id, decision, recorded_pd, model_id), checked.

    ValueError for a missing or repeated application id, a decision outside RECOMMENDATIONS, or a recorded PD
    outside [0, 1]. A missing PD (None) is allowed: the model may not have answered.
    """
    rows = []
    for record in records:
        rows.append(
            {
                "application_id": str(record.get("application_id") or ""),
                "decision": str(record.get("decision")),
                "recorded_pd": np.nan if record.get("recorded_pd") is None else float(record["recorded_pd"]),
                "model_id": record.get("model_id"),
            }
        )
    frame = pd.DataFrame(rows, columns=["application_id", "decision", "recorded_pd", "model_id"])
    _check_ids(frame, "decision")
    unknown = sorted(set(frame["decision"]) - set(RECOMMENDATIONS))
    if unknown:
        raise ValueError(f"unknown decisions {unknown}; expected one of {list(RECOMMENDATIONS)}")
    pds = frame["recorded_pd"].dropna()
    if ((pds < 0) | (pds > 1)).any():
        raise ValueError("recorded_pd must be between 0 and 1")
    return frame


def outcomes_frame(records: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Outcome records as a table (application_id, defaulted), checked: unique ids and defaulted 0 or 1."""
    rows = []
    for record in records:
        defaulted = record.get("defaulted")
        if defaulted not in (0, 1):  # also refuses None, NaN and strings
            raise ValueError(f"defaulted must be 0 or 1, got {defaulted!r} for {record.get('application_id')!r}")
        rows.append({"application_id": str(record.get("application_id") or ""), "defaulted": int(defaulted)})
    frame = pd.DataFrame(rows, columns=["application_id", "defaulted"])
    _check_ids(frame, "outcome")
    return frame


def _check_ids(frame: pd.DataFrame, what: str) -> None:
    """ValueError for an empty or repeated application id."""
    if (frame["application_id"] == "").any():
        raise ValueError(f"every {what} record needs an application_id")
    repeated = frame["application_id"][frame["application_id"].duplicated()].unique().tolist()
    if repeated:
        raise ValueError(f"{len(repeated)} application ids appear in more than one {what} record, e.g. {repeated[:3]}")


@dataclass(frozen=True)
class JoinedOutcomes:
    """Every decision with its outcome when it has arrived (defaulted is NaN while pending)."""

    joined: pd.DataFrame
    n_decisions: int
    n_outcomes: int
    n_unmatched_outcomes: int

    @property
    def with_outcome(self) -> pd.DataFrame:
        """The decisions whose outcome has arrived."""
        return self.joined.loc[self.joined["outcome_arrived"]]


def join_outcomes(decisions: Iterable[Mapping[str, Any]], outcomes: Iterable[Mapping[str, Any]]) -> JoinedOutcomes:
    """Left-join outcomes to decisions by application id (module docstring)."""
    decision_table = decisions_frame(decisions)
    outcome_table = outcomes_frame(outcomes)
    joined = decision_table.merge(outcome_table, on="application_id", how="left", validate="one_to_one")
    joined["outcome_arrived"] = joined["defaulted"].notna()
    unmatched = int((~outcome_table["application_id"].isin(decision_table["application_id"])).sum())
    return JoinedOutcomes(
        joined=joined,
        n_decisions=int(len(decision_table)),
        n_outcomes=int(len(outcome_table)),
        n_unmatched_outcomes=unmatched,
    )


def rate_interval(indicators: np.ndarray, n_resamples: int, seed: int) -> dict[str, Any]:
    """Share of ones with its percentile bootstrap 95% CI, or "not measured" when there are no rows."""
    if indicators.size == 0:
        return {"status": NOT_MEASURED, "reason": "no outcomes have arrived for these decisions"}
    interval = bootstrap_ci(np.mean, indicators.astype(float), n_resamples=n_resamples, seed=seed)
    return {**interval, "k": int(indicators.sum()), "n": int(indicators.size)}


def default_rate_by_decision(joined: JoinedOutcomes, n_resamples: int, seed: int) -> dict[str, Any]:
    """For each decision: counts, the default rate with its CI, and the mean recorded PD of the same rows."""
    table = joined.joined
    result: dict[str, Any] = {}
    for decision in RECOMMENDATIONS:
        rows = table.loc[table["decision"] == decision]
        arrived = rows.loc[rows["outcome_arrived"]]
        recorded = arrived["recorded_pd"].dropna()
        result[decision] = {
            "n_decisions": int(len(rows)),
            "n_with_outcome": int(len(arrived)),
            "n_pending": int(len(rows) - len(arrived)),
            "n_defaulted": int(arrived["defaulted"].sum()),
            "default_rate": rate_interval(arrived["defaulted"].to_numpy(), n_resamples, seed),
            "mean_recorded_pd_with_outcome": float(recorded.mean()) if len(recorded) else None,
        }
    return result


def approved_and_defaulted(joined: JoinedOutcomes) -> dict[str, Any]:
    """How many approved applications have defaulted, out of the approved ones whose outcome has arrived."""
    approved = joined.joined.loc[joined.joined["decision"] == "approve"]
    arrived = approved.loc[approved["outcome_arrived"]]
    count = int(arrived["defaulted"].sum())
    return {
        "n_approved": int(len(approved)),
        "n_approved_with_outcome": int(len(arrived)),
        "n_approved_and_defaulted": count,
        "share_of_approved_with_outcome": count / len(arrived) if len(arrived) else None,
    }


def ece_10_bins(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    """ECE with 10 equal-width bins (the definition risk/credit.py uses)."""
    return expected_calibration_error(y_true, y_prob, n_bins=ECE_BINS, strategy="uniform")


def pd_calibration(joined: JoinedOutcomes, n_resamples: int, seed: int) -> dict[str, Any]:
    """Calibration of the recorded PD on the decisions that have an outcome and a recorded PD."""
    rows = joined.with_outcome.dropna(subset=["recorded_pd"])
    if rows.empty:
        return {"status": NOT_MEASURED, "reason": "no decision has both a recorded PD and an outcome"}
    observed = rows["defaulted"].to_numpy(dtype=float)
    predicted = rows["recorded_pd"].to_numpy(dtype=float)
    mean_pd = float(predicted.mean())
    return {
        "n_rows": int(len(rows)),
        "observed_default_rate": float(observed.mean()),
        "mean_recorded_pd": mean_pd,
        "observed_over_expected": float(observed.mean()) / mean_pd if mean_pd > 0 else None,
        "ece": bootstrap_ci(ece_10_bins, observed, predicted, n_resamples=n_resamples, seed=seed),
        "ece_bins": ECE_BINS,
        "brier": bootstrap_ci(brier_score, observed, predicted, n_resamples=n_resamples, seed=seed),
        "calibration_by_recorded_pd_decile": calibration_table(observed, predicted, n_bins=10).to_dict(
            orient="records"
        ),
        "decisions_without_recorded_pd": int(joined.with_outcome["recorded_pd"].isna().sum()),
    }


def summarize_outcomes(
    decisions: Iterable[Mapping[str, Any]],
    outcomes: Iterable[Mapping[str, Any]],
    n_resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Join and answer the three questions of the module docstring."""
    joined = join_outcomes(decisions, outcomes)
    n_arrived = int(joined.joined["outcome_arrived"].sum())
    return {
        "join": {
            "key": "application_id",
            "n_decisions": joined.n_decisions,
            "n_outcomes_received": joined.n_outcomes,
            "n_decisions_with_outcome": n_arrived,
            "n_decisions_pending": joined.n_decisions - n_arrived,
            "n_outcomes_without_decision": joined.n_unmatched_outcomes,
        },
        "decisions": {decision: int((joined.joined["decision"] == decision).sum()) for decision in RECOMMENDATIONS},
        "default_rate_by_decision": default_rate_by_decision(joined, n_resamples, seed),
        "approved_and_defaulted": approved_and_defaulted(joined),
        "recorded_pd_calibration": pd_calibration(joined, n_resamples, seed),
        "bootstrap": {"n_resamples": n_resamples, "seed": seed, "method": "percentile", "unit": "application"},
    }


# ---------------------------------------------------------------------------
# The SIMULATED decisions of the demonstration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimulatedDecisionRule:
    """The stated rule behind the demonstration's SIMULATED decisions (not a lending policy).

    Checked in this order, the same precedence as agent/policy.py decide_recommendation:
      1. decline_for_review        if PD >= decline_from_pd (the "very high" risk band)
      2. request_more_information  if income is not verified and loan_to_income > max_unverified_loan_to_income
                                   (or loan_to_income is missing): verify income before deciding
      3. refer                     if PD >= refer_from_pd (the "high" risk band)
      4. approve                   otherwise
    A PD equal to a cut-off goes to the riskier side, as in risk/bands.py.
    """

    refer_from_pd: float
    decline_from_pd: float
    max_unverified_loan_to_income: float = 0.3

    @classmethod
    def from_bands(
        cls, bands: Mapping[str, Any], max_unverified_loan_to_income: float = 0.3
    ) -> "SimulatedDecisionRule":
        """Cut-offs from risk/bands.py band_cutoffs: refer from the "high" band, decline from "very high"."""
        labels = list(bands["labels"])
        cutoffs = list(bands["cutoffs"])
        return cls(
            refer_from_pd=float(cutoffs[labels.index("high") - 1]),
            decline_from_pd=float(cutoffs[labels.index("very high") - 1]),
            max_unverified_loan_to_income=max_unverified_loan_to_income,
        )

    def describe(self) -> dict[str, Any]:
        """The rule in words, with its numbers."""
        return {
            "order": [
                f"decline_for_review if PD >= {self.decline_from_pd:.6f} (the 'very high' band)",
                "request_more_information if income is not verified and loan_to_income > "
                f"{self.max_unverified_loan_to_income} (or loan_to_income is missing)",
                f"refer if PD >= {self.refer_from_pd:.6f} (the 'high' band)",
                "approve otherwise",
            ],
            "refer_from_pd": self.refer_from_pd,
            "decline_from_pd": self.decline_from_pd,
            "max_unverified_loan_to_income": self.max_unverified_loan_to_income,
            "verified_income_values": list(VERIFIED_INCOME),
            "note": "SIMULATED: a stated demonstration rule, not the agent's reference policy and not a lending policy",
        }

    def decide(self, loans: pd.DataFrame, pds: np.ndarray) -> np.ndarray:
        """One decision per row of loans (needs verification_status and loan_to_income) given its PD."""
        pds = np.asarray(pds, dtype=float)
        if pds.shape[0] != len(loans) or np.isnan(pds).any():
            raise ValueError("pass one PD (not NaN) per row of loans")
        unverified = ~loans["verification_status"].isin(VERIFIED_INCOME).to_numpy()
        loan_to_income = loans["loan_to_income"].to_numpy(dtype=float)
        high_or_missing = np.isnan(loan_to_income) | (loan_to_income > self.max_unverified_loan_to_income)
        conditions = [pds >= self.decline_from_pd, unverified & high_or_missing, pds >= self.refer_from_pd]
        choices = ["decline_for_review", "request_more_information", "refer"]
        return np.select(conditions, choices, default="approve")

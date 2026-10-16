"""How each stress scenario and counterfactual changes an application (Phase 4 C): the exact mechanics.

Every scenario is a set of lever substitutions (docintel/levers.py vocabulary) applied with evidence.state.apply, so
the changed application is validated by parse_application and judged by the SAME reference policy with the SAME
PolicyInputs as the baseline. Nothing here decides anything; scenarios.py runs the policy and compares.

Loan (Northwind, credit line):
- revenue -10/-20/-30%: the shock removes d = shock x verified annual revenue (to cents). Verified revenue (the
  NBC-3.3 eligible deposits of the 12 statement months, "@verified_annual_revenue" lever), TTM P&L revenue and TTM
  EBITDA all fall by the same d dollars (costs unchanged), so NOI = EBITDA - max(0, P&L revenue - verified) falls
  by exactly d: "verified revenue and NOI reduced by the same dollars", the NBC-4.5 stress definition. The stated
  revenue is scaled by the same share, so the NBC-3.4 revenue gap percent is unchanged and the scenario isolates
  repayment capacity. Not applicable when verified revenue is unknown (fewer than 12 consecutive months) or when
  the P&L revenue is smaller than d;
- rate +200/+400 bps: the note rate of the requested loan rises by 2.00 / 4.00 percentage points (existing debt is
  unchanged). The policy's own NBC-4.5 rate stress (variable rate) then runs on top of the raised rate;
- stated revenue := verified (counterfactual, not a shock): the application's stated revenue is set to the
  verified figure.
Startup (startup_credit line): only the rate family (loan_request.annual_rate_pct). The startup projections are
Founder Studio simulation outputs; the policy already runs its own NSP revenue stress on them.

Insurance (Harborline):
- severity +10/+25%: every claim of the experience period (loss_runs.years[*].claims) has paid and incurred
  multiplied by (1 + shock), to cents. A line that balanced (|paid + reserve - incurred| <= $1.00) gets reserve =
  incurred - paid so it still balances; an out-of-balance line has all three amounts scaled. The HM-4.1 $250,000
  per-claim cap is applied afterwards by the policy itself;
- frequency +10/+25%: k = round-half-up(shock x N) whole claims are added, where N is the number of experience-period
  claims. The added claims copy the k most recent claims (latest date of loss first, then the largest incurred),
  each in its own policy year with the same date and amounts, so the loss-ratio, rolling-window and trend rules
  all see them. With few claims k can be 0 (for example 0.10 x 3 = 0.3 rounds to 0): the scenario then equals
  the baseline and says so; the effective shock k / N is reported;
- in both, the application's stated loss history moves with the loss runs (stated claim count + k; stated total
  incurred x (new loss-run total / old loss-run total)), so the HM-3.3 reconciliation compares like with like.
  Current-term claims are not changed (they are outside the experience period);
- stated TIV := schedule TIV (counterfactual).
"""

import copy
from dataclasses import dataclass, field
from typing import Any

from uwcopilot.agent.applications import Claim as LossClaim
from uwcopilot.agent.insurance_calculations import claim_out_of_balance

BPS_PER_POINT = 100.0


class ScenarioNotApplicable(ValueError):
    """The scenario cannot be built for this case (the reason is the message)."""


@dataclass(frozen=True)
class ScenarioPlan:
    """The lever substitutions of one scenario and notes about how they were built."""

    substitutions: dict[str, Any]
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------------------------------
# Loan
# --------------------------------------------------------------------------------------------------------------


def revenue_shock_plan(application: dict, verified: float | None, shock: float) -> ScenarioPlan:
    """Verified revenue, P&L revenue and EBITDA down by the same dollars; stated revenue down by the same share."""
    if verified is None:
        raise ScenarioNotApplicable("verified annual revenue is unknown (NBC-3.2 needs 12 consecutive months)")
    dollars = round(shock * verified, 2)
    debt = application["debt"]
    pnl_revenue = round(float(debt["ttm_pnl_revenue"]) - dollars, 2)
    if pnl_revenue < 0:
        raise ScenarioNotApplicable("the TTM P&L revenue is smaller than the revenue shock in dollars")
    return ScenarioPlan(
        substitutions={
            "@verified_annual_revenue": round(verified - dollars, 2),
            "debt.ttm_pnl_revenue": pnl_revenue,
            "debt.ttm_ebitda": round(float(debt["ttm_ebitda"]) - dollars, 2),
            "stated_annual_revenue": round(float(application["stated_annual_revenue"]) * (1.0 - shock), 2),
        },
        notes=[f"revenue and NOI reduced by {dollars:,.2f} USD"],
    )


def rate_shock_plan(application: dict, add_bps: float) -> ScenarioPlan:
    """The requested loan's note rate raised by add_bps basis points (credit or startup_credit)."""
    if application.get("line") == "startup_credit":
        path = "loan_request.annual_rate_pct"
        rate = float(application["loan_request"]["annual_rate_pct"])
    else:
        path = "note_rate_pct"
        rate = float(application["note_rate_pct"])
    return ScenarioPlan(substitutions={path: round(rate + add_bps / BPS_PER_POINT, 4)})


def stated_revenue_plan(verified: float | None) -> ScenarioPlan:
    """Counterfactual: stated annual revenue := verified annual revenue."""
    if verified is None:
        raise ScenarioNotApplicable("verified annual revenue is unknown (NBC-3.2 needs 12 consecutive months)")
    return ScenarioPlan(substitutions={"stated_annual_revenue": verified})


# --------------------------------------------------------------------------------------------------------------
# Insurance
# --------------------------------------------------------------------------------------------------------------


def experience_claims(application: dict) -> list[tuple[int, dict]]:
    """(policy year index, claim dict) for every claim of the experience period, in file order."""
    return [
        (index, claim)
        for index, year in enumerate(application["loss_runs"]["years"])
        for claim in year.get("claims", [])
    ]


def loss_run_total(application: dict) -> float:
    """Total incurred over every claim the loss runs show (experience years and current term)."""
    runs = application["loss_runs"]
    claims = [claim for _, claim in experience_claims(application)] + list(runs.get("current_term_claims", []))
    return round(sum(float(claim["incurred"]) for claim in claims), 2)


def stated_history(application: dict, added_claims: int, total_before: float, total_after: float) -> dict[str, Any]:
    """The application's stated claim count and total incurred moved in step with the loss runs."""
    stated_total = float(application["stated_total_incurred"])
    if total_before > 0:
        stated_total = round(stated_total * total_after / total_before, 2)
    return {
        "stated_claim_count": int(application["stated_claim_count"]) + added_claims,
        "stated_total_incurred": stated_total,
    }


def scaled_claim(claim: dict, factor: float) -> dict:
    """A claim with its amounts scaled; a balanced line stays balanced (reserve = incurred - paid)."""
    balanced = not claim_out_of_balance(LossClaim.model_validate(claim))
    paid = round(float(claim["paid"]) * factor, 2)
    incurred = round(float(claim["incurred"]) * factor, 2)
    reserve = max(0.0, round(incurred - paid, 2)) if balanced else round(float(claim["reserve"]) * factor, 2)
    return {**claim, "paid": paid, "reserve": reserve, "incurred": incurred}


def severity_plan(application: dict, shock: float) -> ScenarioPlan:
    """Every experience-period claim's amounts x (1 + shock); stated history scaled with the loss runs."""
    claims = experience_claims(application)
    if not claims:
        return ScenarioPlan(substitutions={}, notes=["no claims in the experience period: nothing to scale"])
    substitutions: dict[str, Any] = {}
    changed = copy.deepcopy(application)
    for index, year in enumerate(changed["loss_runs"]["years"]):
        if year.get("claims"):
            year["claims"] = [scaled_claim(claim, 1.0 + shock) for claim in year["claims"]]
            substitutions[f"loss_runs.years[{index}].claims"] = year["claims"]
    substitutions.update(stated_history(application, 0, loss_run_total(application), loss_run_total(changed)))
    return ScenarioPlan(substitutions=substitutions, notes=[f"{len(claims)} claim(s) scaled"])


def added_claim_count(claim_count: int, shock: float) -> int:
    """round-half-up(shock x claim count): the whole claims a frequency shock adds."""
    return int(shock * claim_count + 0.5)


def frequency_plan(application: dict, shock: float) -> ScenarioPlan:
    """k = round-half-up(shock x N) copies of the most recent claims; stated history moved with the loss runs."""
    claims = experience_claims(application)
    added = added_claim_count(len(claims), shock)
    if added == 0:
        note = f"{shock:g} x {len(claims)} claim(s) rounds to 0 whole claims: no claim added (same as the baseline)"
        return ScenarioPlan(substitutions={}, notes=[note])
    recent_first = sorted(claims, key=lambda item: (item[1]["date_of_loss"], float(item[1]["incurred"])), reverse=True)
    changed = copy.deepcopy(application)
    years = changed["loss_runs"]["years"]
    for index, claim in recent_first[:added]:
        years[index]["claims"].append(copy.deepcopy(claim))
    touched = sorted({index for index, _ in recent_first[:added]})
    substitutions: dict[str, Any] = {f"loss_runs.years[{index}].claims": years[index]["claims"] for index in touched}
    substitutions.update(stated_history(application, added, loss_run_total(application), loss_run_total(changed)))
    effective = added / len(claims)
    return ScenarioPlan(
        substitutions=substitutions,
        notes=[f"{added} claim(s) added to {len(claims)} (effective frequency shock {effective:.0%})"],
    )


def stated_tiv_plan(schedule_total: float | None) -> ScenarioPlan:
    """Counterfactual: application TIV := property schedule TIV."""
    if schedule_total is None:
        raise ScenarioNotApplicable("the property schedule TIV is unknown")
    return ScenarioPlan(substitutions={"stated_tiv": schedule_total})

"""Supplementary calculations for the decision record (Phase 4): deterministic views the reviewer sees next to the
reference policy's own figures. The policy never uses them, and they are not ledger claims, so a memo cannot cite
them (the policy's CALC-* claims are the decision figures).

Every value comes from a helper of evidence/calculations.py:
- credit: NBC-3.3 eligible deposits per statement month (verified_monthly_revenue); the annualized eligible revenue,
  an ESTIMATE for display that is never NBC-3.2 verified revenue (annualized_eligible_revenue); the decision DSCR
  after a 10 / 20 / 30% revenue shock (stressed_dscr) and after +200 / +400 bps on the requested loan
  (rate_stressed_service, rate_stressed_dscr). At 20% and +200 bps they equal the policy's CALC-stressed_dscr_revenue
  and CALC-stressed_dscr_rate. Stated-versus-verified DSCR is not repeated here: the policy already reports it
  (CALC-dscr_stated_revenue next to CALC-dscr);
- insurance: the HM-4.1 annual loss ratio per policy year of each quoted line (loss_ratio_by_year); claim count,
  incurred and mean severity per policy year (experience_by_year) with their least-squares trend per year
  (linear_trend); and the STRESS MULTIPLIER view of each quoted line's 5-year loss ratio under the frequency and
  severity shocks the scenarios use (stress_multiplier, apply_stress_multiplier). It is labeled: a stress
  multiplier, not a model, and it ignores the HM-4.1 $250,000 per-claim cap that the claim-level scenarios respect.

A view is shown only when the ledger shows the figure it builds on as VERIFIED (a DSCR over imputed debt figures, or
a loss ratio over a loss run whose summary disagrees with its lines, is UNKNOWN there); otherwise it is withheld with
the reason. Everything is SYNTHETIC.
"""

from dataclasses import asdict
from typing import Any

from uwcopilot.agent.applications import AnyApplication, CreditApplication, InsuranceApplication
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.agent.policy_insurance import coverage_lines
from uwcopilot.evidence import calculations
from uwcopilot.evidence.ledger_models import Ledger

REVENUE_SHOCKS: tuple[float, ...] = (0.10, 0.20, 0.30)
RATE_SHOCKS_BPS: tuple[float, ...] = (200.0, 400.0)
# (frequency shock, severity shock) pairs: the four shocks of the insurance scenarios (scenarios.py).
LOSS_STRESSES: tuple[tuple[float, float], ...] = ((0.10, 0.0), (0.25, 0.0), (0.0, 0.10), (0.0, 0.25))
STRESS_MULTIPLIER_LABEL = "STRESS MULTIPLIER, not a model: (1 + frequency shock) x (1 + severity shock)"
ESTIMATE_LABEL = "ESTIMATE for display (eligible deposits x 12 / months observed); NOT NBC-3.2 verified revenue"
WITHHELD = "withheld: the ledger shows {claim_id} as {status}, not VERIFIED"


def calc_value(result: PolicyResult, calc_id: str) -> float | None:
    """The value of one policy calculation (None when absent)."""
    return next((item.value for item in result.calculations if item.calc_id == calc_id), None)


def calc_input(result: PolicyResult, calc_id: str, name: str) -> Any:
    """One input of a policy calculation (None when absent)."""
    found = next((item for item in result.calculations if item.calc_id == calc_id), None)
    return None if found is None else found.inputs.get(name)


def withheld_reason(ledger: Ledger | None, claim_ids: tuple[str, ...]) -> str | None:
    """Why a view is withheld (the first base claim that is not VERIFIED), or None when it may be shown."""
    if ledger is None:
        return None
    for claim_id in claim_ids:
        claim = ledger.claim(claim_id)
        status = "absent" if claim is None else claim.status
        if status != "VERIFIED":
            return WITHHELD.format(claim_id=claim_id, status=status)
    return None


def credit_views(application: CreditApplication, result: PolicyResult, ledger: Ledger | None) -> dict[str, Any]:
    """Monthly eligible revenue, the annualized estimate, and the revenue and rate stress grids."""
    months = [asdict(row) for row in calculations.verified_monthly_revenue(application.bank_statements)]
    views: dict[str, Any] = {
        "eligible_revenue_by_month": months,
        "annualized_eligible_revenue": {
            "value": calculations.annualized_eligible_revenue(application.bank_statements),
            "label": ESTIMATE_LABEL,
        },
    }
    base = ("CALC-dscr", "CALC-noi", "CALC-verified_annual_revenue", "CALC-annual_debt_service")
    reason = withheld_reason(ledger, base)
    noi = calc_value(result, "CALC-noi")
    verified = calc_value(result, "CALC-verified_annual_revenue")
    service = calc_value(result, "CALC-annual_debt_service")
    existing = calc_input(result, "CALC-annual_debt_service", "existing_annual_debt_service")
    if reason is None and None in (noi, verified, service, existing):
        reason = "not computed: the policy did not compute NOI, verified revenue and annual debt service"
    if reason is not None:
        views["stressed_dscr"] = {"withheld": reason}
        return views
    views["stressed_dscr"] = {
        "revenue_shock": {
            f"{shock:.0%}": calculations.stressed_dscr(noi, verified, service, shock) for shock in REVENUE_SHOCKS
        },
        "rate_shock": {
            f"+{bps:.0f} bps": calculations.rate_stressed_dscr(
                noi,
                calculations.rate_stressed_service(
                    float(existing),
                    application.product,
                    application.requested_amount,
                    application.note_rate_pct,
                    application.term_months,
                    bps,
                ),
            )
            for bps in RATE_SHOCKS_BPS
        },
    }
    return views


def experience_rows(application: InsuranceApplication) -> tuple[list[dict[str, Any]], dict[str, float | None]]:
    """Claims per policy year (count, incurred, capped incurred, mean severity) and the trends per year."""
    rows = calculations.experience_by_year(application.loss_runs)
    table = [
        {
            "policy_year_start": row.policy_year_start.isoformat(),
            "claim_count": row.claim_count,
            "incurred": row.incurred,
            "capped_incurred": row.capped_incurred,
            "mean_severity": row.mean_severity,
        }
        for row in rows
    ]
    trends = {
        "claim_count_per_year": calculations.linear_trend([float(row.claim_count) for row in rows]),
        "mean_severity_per_year": calculations.linear_trend([row.mean_severity for row in rows]),
    }
    return table, trends


def insurance_views(application: InsuranceApplication, result: PolicyResult, ledger: Ledger | None) -> dict[str, Any]:
    """Annual loss ratios per quoted line, experience by year with trends, and the stress-multiplier view."""
    views: dict[str, Any] = {"by_line": {}}
    for line in coverage_lines(application):
        calc_id = f"CALC-loss_ratio_5yr_{line}"
        reason = withheld_reason(ledger, (calc_id,))
        five_year = calc_value(result, calc_id)
        if reason is None and five_year is None:
            reason = f"not computed: the policy did not compute {calc_id}"
        if reason is not None:
            views["by_line"][line] = {"withheld": reason}
            continue
        stressed = {}
        for frequency, severity in LOSS_STRESSES:
            multiplier = calculations.stress_multiplier(frequency, severity)
            key = f"frequency +{frequency:.0%}, severity +{severity:.0%}"
            stressed[key] = calculations.apply_stress_multiplier(five_year, multiplier)
        views["by_line"][line] = {
            "loss_ratio_by_year_pct": calculations.loss_ratio_by_year(application.loss_runs, line),
            "five_year_loss_ratio_pct": five_year,
            "stress_multiplier_view_pct": stressed,
            "stress_multiplier_label": STRESS_MULTIPLIER_LABEL,
        }
    if all("withheld" in entry for entry in views["by_line"].values()):
        views["experience_by_year"] = {"withheld": "every quoted line's loss ratio is withheld"}
        return views
    table, trends = experience_rows(application)
    views["experience_by_year"] = {"years": table, "trend_per_year": trends}
    return views


def supplementary_calculations(
    application: AnyApplication, result: PolicyResult, ledger: Ledger | None = None
) -> dict[str, Any]:
    """The supplementary views of one evaluated case (module docstring); {} for a startup loan."""
    if isinstance(application, CreditApplication):
        return {"label": "display only; not used by the reference policy", **credit_views(application, result, ledger)}
    if isinstance(application, InsuranceApplication):
        views = insurance_views(application, result, ledger)
        return {"label": "display only; not used by the reference policy", **views}
    return {}

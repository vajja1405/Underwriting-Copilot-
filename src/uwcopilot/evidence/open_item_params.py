"""Case values that fill the resolution templates of open items (config/resolution_templates.yaml placeholders).

Every value is computed from what the engine can see: the current application, the policy's calculations and the
packet extraction. None of it reads the generator's hidden truth. A value that cannot be computed is returned as
None; the template option that needs it is then left out (evidence/requests.py renormalizes the priors).

The rules mirror the ones the SYNTHETIC packet generator documents for the same placeholders, so the engine's
options describe the same plausible worlds:
- a missing statement month at the median, half the median and 1.5 x the median of the other months' NBC-3.3
  eligible deposits (all of it non-cash revenue);
- the owners table rewritten to the other ownership document's percentages (owners paired by canonical name);
- a claim line missing from a loss run, sized from the printed summary minus the claim lines, placed half a year
  into the carrier's latest policy year;
- the years of a missing prior-carrier loss run with the premiums the application's prior-carriers table prints,
  and no claims, claims at the observed loss rate of the loss runs on file, or at twice that rate;
- the inspection report code when re-scaling the schedule would put a location above $5,000,000 and the packet
  already holds a current inspection report for every such location.
"""

import statistics
from datetime import date, timedelta
from typing import Any

from uwcopilot.agent.applications import MonthlyDeposits
from uwcopilot.agent.credit_calculations import MONTHS_REQUIRED, month_eligibility, month_index
from uwcopilot.agent.policy_insurance import INSPECTION_REPORT_ABOVE_TIV
from uwcopilot.docintel.checks_insurance import current_inspections
from uwcopilot.docintel.contradictions import Contradiction
from uwcopilot.docintel.levers import SCHEDULE_TIV_LEVER, apply_substitutions, location_total
from uwcopilot.docintel.normalize import canonical_name
from uwcopilot.docintel.schemas import ExtractedOwner, InsuranceSubmission, LoanPacket, PacketExtraction

ZERO_MONTH: dict[str, float] = {
    "transfers_between_accounts": 0.0,
    "refunds_and_reversals": 0.0,
    "loan_proceeds": 0.0,
    "owner_contributions": 0.0,
    "one_time_receipts": 0.0,
    "unidentified_deposits": 0.0,
    "cash_deposits": 0.0,
}
MONTH_SHARES: dict[str, float] = {
    "month_at_median": 1.0,
    "month_at_half_median": 0.5,
    "month_at_1_5_median": 1.5,
}
LOSS_RATE_FACTORS: dict[str, float] = {
    "years_without_claims": 0.0,
    "years_at_observed_rate": 1.0,
    "years_at_twice_observed_rate": 2.0,
}
LINE_PREMIUMS: tuple[tuple[str, str], ...] = (
    ("property", "property_earned_premium"),
    ("general_liability", "gl_earned_premium"),
)
MID_YEAR_DAYS = 180
CENT = 0.005


# --------------------------------------------------------------------------------------------------------------
# Statement months
# --------------------------------------------------------------------------------------------------------------


def month_text(index: int) -> str:
    """The inverse of credit_calculations.month_index: a running month number -> "YYYY-MM"."""
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def missing_months(application: dict) -> list[str]:
    """Months absent from the 12-month window that ends at the latest statement month (oldest first)."""
    months = [item["month"] for item in application.get("bank_statements", {}).get("months", [])]
    if not months:
        return []
    present = {month_index(month) for month in months}
    last = max(present)
    window = range(last - MONTHS_REQUIRED + 1, last + 1)
    return [month_text(index) for index in window if index not in present]


def eligible_deposits(month: dict, cash_intensive: bool) -> float:
    """NBC-3.3 eligible deposits of one MonthlyDeposits dict."""
    return month_eligibility(MonthlyDeposits.model_validate(month), cash_intensive).eligible


def missing_month_values(application: dict, month: str) -> dict[str, dict[str, Any]]:
    """The three template months for a missing month: median, half and 1.5 x the median eligible deposits."""
    statements = application["bank_statements"]
    cash_intensive = bool(statements.get("cash_intensive_approved", False))
    median = statistics.median(eligible_deposits(item, cash_intensive) for item in statements["months"])
    return {
        name: {"month": month, "total_deposits": round(share * median, 2), **ZERO_MONTH}
        for name, share in MONTH_SHARES.items()
    }


# --------------------------------------------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------------------------------------------


def other_ownership_document(extraction: PacketExtraction | None) -> list[ExtractedOwner]:
    """The owners on the other ownership document: formation members (credit) or the ownership statement."""
    if isinstance(extraction, LoanPacket) and extraction.formation is not None:
        return list(extraction.formation.members)
    if isinstance(extraction, InsuranceSubmission) and extraction.ownership_statement is not None:
        return list(extraction.ownership_statement.owners)
    return []


def owners_per_other_document(application: dict, extraction: PacketExtraction | None) -> list[dict] | None:
    """The application's owners rewritten to the other document's members and percentages (None if unpaired)."""
    members = other_ownership_document(extraction)
    if not members:
        return None
    by_name = {canonical_name(owner["name"]): owner for owner in application.get("owners", [])}
    rewritten = []
    for member in members:
        owner = by_name.get(canonical_name(member.name.value)) if member.name.found else None
        if owner is None or member.ownership_pct.value is None:
            return None
        rewritten.append({**owner, "ownership_pct": float(member.ownership_pct.value)})
    return rewritten


# --------------------------------------------------------------------------------------------------------------
# Loss runs
# --------------------------------------------------------------------------------------------------------------


def loss_run_by_doc(extraction: PacketExtraction | None, doc_id: str | None) -> Any:
    """The extracted loss run with this document id (None when there is none)."""
    if not isinstance(extraction, InsuranceSubmission):
        return None
    return next((item for item in extraction.loss_runs if item.doc_id == doc_id), None)


def carrier_of(extraction: PacketExtraction | None, doc_id: str | None) -> str | None:
    """The carrier printed on a loss run document (untrusted document text; used only as a request parameter)."""
    loss_run = loss_run_by_doc(extraction, doc_id)
    if loss_run is None or not loss_run.carrier.found:
        return None
    return str(loss_run.carrier.value)


def summary_gap(contradiction: Contradiction, name: str) -> float | None:
    """Printed summary minus the claim lines for one summary item (None when that item agrees)."""
    pair = contradiction.values.get("differences", {}).get(name)
    if not pair:
        return None
    return round(float(pair[0]) - float(pair[1]), 2)


def latest_year_index(application: dict, loss_run: Any) -> int | None:
    """Index in the application's loss-run years of the carrier's latest printed policy year."""
    starts = [year.policy_year_start.value for year in loss_run.years if year.policy_year_start.value is not None]
    if not starts:
        return None
    latest = max(starts).isoformat()
    for index, year in enumerate(application["loss_runs"]["years"]):
        if year["policy_year_start"] == latest:
            return index
    return None


def missing_claim_values(
    application: dict, extraction: PacketExtraction | None, contradiction: Contradiction
) -> dict[str, Any]:
    """{"year_index", "missing_claim"} for a loss run whose summary shows more incurred than its claim lines."""
    incurred = summary_gap(contradiction, "total_incurred")
    loss_run = loss_run_by_doc(extraction, contradiction.doc_ids[0] if contradiction.doc_ids else None)
    index = latest_year_index(application, loss_run) if loss_run is not None else None
    if incurred is None or incurred <= CENT or index is None:
        return {"year_index": None, "missing_claim": None}
    paid = max(0.0, summary_gap(contradiction, "total_paid") or 0.0)
    paid = min(paid, incurred)
    year = application["loss_runs"]["years"][index]
    when = date.fromisoformat(year["policy_year_start"]) + timedelta(days=MID_YEAR_DAYS)
    line = "property" if year.get("property_earned_premium") is not None else "general_liability"
    claim = {
        "date_of_loss": when.isoformat(),
        "line": line,
        "paid": round(paid, 2),
        "reserve": round(incurred - paid, 2),
        "incurred": incurred,
    }
    return {"year_index": index, "missing_claim": claim}


def observed_loss_rates(application: dict) -> dict[str, float]:
    """Incurred / earned premium per line over the loss-run years on file (0 when a line has no premium)."""
    years = application["loss_runs"]["years"]
    rates = {}
    for line, premium_key in LINE_PREMIUMS:
        premium = sum(float(year.get(premium_key) or 0.0) for year in years)
        losses = sum(
            float(claim["incurred"]) for year in years for claim in year.get("claims", []) if claim["line"] == line
        )
        rates[line] = losses / premium if premium > 0 else 0.0
    return rates


def prior_carrier_years(extraction: PacketExtraction | None, carrier: str) -> list[dict]:
    """Year dicts (start and premiums, no claims) of the prior-carriers rows of one carrier, oldest first."""
    if not isinstance(extraction, InsuranceSubmission) or extraction.application is None:
        return []
    key = canonical_name(carrier)
    years = []
    for row in extraction.application.prior_carriers:
        if not row.carrier.found or canonical_name(row.carrier.value) != key:
            continue
        if row.policy_year_start.value is None:
            continue
        years.append(
            {
                "policy_year_start": row.policy_year_start.value.isoformat(),
                "property_earned_premium": row.property_premium.value,
                "gl_earned_premium": row.gl_premium.value,
                "claims": [],
            }
        )
    return sorted(years, key=lambda year: year["policy_year_start"])


def years_at_rate(years: list[dict], rates: dict[str, float], factor: float) -> list[dict]:
    """The missing years with one claim per written line at factor x the observed loss rate of that line."""
    result = []
    for year in years:
        claims = []
        for line, premium_key in LINE_PREMIUMS:
            incurred = round(float(year.get(premium_key) or 0.0) * rates[line] * factor, 2)
            if incurred > 0:
                when = date.fromisoformat(year["policy_year_start"]) + timedelta(days=MID_YEAR_DAYS)
                claims.append(
                    {
                        "date_of_loss": when.isoformat(),
                        "line": line,
                        "paid": incurred,
                        "reserve": 0.0,
                        "incurred": incurred,
                    }
                )
        result.append({**year, "claims": claims})
    return result


def missing_loss_run_values(application: dict, extraction: PacketExtraction | None, carrier: str) -> dict[str, Any]:
    """The three template year lists of a missing prior-carrier loss run (None when the years are unknown)."""
    years = prior_carrier_years(extraction, carrier)
    if not years:
        return {name: None for name in LOSS_RATE_FACTORS}
    rates = observed_loss_rates(application)
    return {name: years_at_rate(years, rates, factor) for name, factor in LOSS_RATE_FACTORS.items()}


# --------------------------------------------------------------------------------------------------------------
# Property schedule
# --------------------------------------------------------------------------------------------------------------


def inspection_extra(application: dict, extraction: PacketExtraction | None, target_tiv: float) -> dict[str, Any]:
    """{"documents_provided+": "inspection_report"} when scaling the schedule to target_tiv needs inspection reports
    the packet already holds (every location above $5,000,000 has a current report); else {}."""
    if not isinstance(extraction, InsuranceSubmission) or "inspection_report" in application["documents_provided"]:
        return {}
    scaled = apply_substitutions(application, {SCHEDULE_TIV_LEVER: target_tiv})
    large = [item["location_id"] for item in scaled["locations"] if location_total(item) > INSPECTION_REPORT_ABOVE_TIV]
    inspected = set(current_inspections(extraction))
    if large and all(location in inspected for location in large):
        return {"documents_provided+": "inspection_report"}
    return {}

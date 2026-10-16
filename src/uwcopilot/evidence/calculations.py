"""Financial calculations for the Evidence-to-Action Engine (Phase 4): deterministic, plain Python, no thresholds.

Most formulas already exist and are RE-EXPORTED here unchanged, so the engine, the reference policy and the
document checks always compute a figure the same way:
- credit (agent/credit_calculations.py): verify_revenue, month_eligibility, revenue_gap_pct, net_operating_income,
  existing_annual_debt_service, proposed_annual_debt_service, amortizing_monthly_payment, coverage_ratio,
  collateral_coverage, months_between, round_ratio, round_pct;
- insurance (agent/insurance_calculations.py): schedule_tiv, location_tiv, tiv_difference_pct, loss_ratios,
  capped_incurred, claim_out_of_balance, max_claims_in_rolling_window, claims_trend, incurred_difference_pct,
  loss_run_age_days, all_claims;
- statement transactions (docintel/deposits.py): average_daily_balance, negative_balance_months, nsf_events,
  monthly_deposits, average_monthly_loan_payments, average_monthly_mca_remittance.

Added here (only what was missing):
- stressed_noi / stressed_dscr: the NBC-4.5 revenue stress with any shock size. Verified revenue and NOI fall by
  the same dollars. At revenue_shock=0.20 it is numerically identical to the policy's CALC-stressed_dscr_revenue;
- rate_stressed_service / rate_stressed_dscr: annual debt service with the note rate raised by some basis points.
  At +200 bps it is identical to the policy's CALC-stressed_dscr_rate;
- verified_monthly_revenue and annualized_eligible_revenue: the monthly view of NBC-3.3 eligible deposits. The
  annualized figure is an ESTIMATE for display only; the policy keeps NBC-3.2 (12 consecutive months, else no
  verified revenue);
- dscr_stated_vs_verified: DSCR with stated revenue next to DSCR with verified revenue;
- insurance: loss_ratio_by_year, experience_by_year and linear_trend (frequency and severity trend by policy
  year), tiv_reconciliation, and stress_multiplier / apply_stress_multiplier. The stress multiplier is a LABELED
  STRESS MULTIPLIER on the experience loss ratio, not a model output: no Phase 1 model is trained on these
  SYNTHETIC commercial submissions.

Why deterministic: SR 26-2 / OCC Bulletin 2026-13 ("Revised Guidance on Model Risk Management", Federal Reserve,
OCC and FDIC, April 17, 2026) excludes "simple arithmetic calculations" and "deterministic rule-based processes"
from its model definition. Keeping these calculations plain arithmetic is one reason they stay outside model risk
management. Applicability depends on the institution and its risk profile; this project is a demonstration, not
a compliance program.

Rounding follows the guideline conventions of the agent modules: ratios to 2 decimals, percentages to 1 decimal,
dollars to cents.
"""

from dataclasses import dataclass
from datetime import date

from uwcopilot.agent.applications import BankStatementSummary, DebtServiceInputs, InsuranceApplication, LossRuns
from uwcopilot.agent.credit_calculations import (
    ADVANCE_RATES,
    MONTHS_REQUIRED,
    amortizing_monthly_payment,
    collateral_coverage,
    coverage_ratio,
    existing_annual_debt_service,
    month_eligibility,
    months_between,
    net_operating_income,
    proposed_annual_debt_service,
    revenue_gap_pct,
    round_pct,
    round_ratio,
    verify_revenue,
)
from uwcopilot.agent.insurance_calculations import (
    LARGE_LOSS_USD,
    all_claims,
    capped_incurred,
    claim_out_of_balance,
    claims_trend,
    incurred_difference_pct,
    location_tiv,
    loss_ratios,
    loss_run_age_days,
    max_claims_in_rolling_window,
    schedule_tiv,
    tiv_difference_pct,
)
from uwcopilot.docintel.deposits import (
    average_daily_balance,
    average_monthly_loan_payments,
    average_monthly_mca_remittance,
    monthly_deposits,
    negative_balance_months,
    nsf_events,
)

BPS_PER_PERCENTAGE_POINT = 100.0
MONTHS_PER_YEAR = 12

__all__ = [
    "ADVANCE_RATES",
    "LARGE_LOSS_USD",
    "MONTHS_REQUIRED",
    "DscrComparison",
    "MonthlyRevenue",
    "TivReconciliation",
    "YearExperience",
    "all_claims",
    "amortizing_monthly_payment",
    "annualized_eligible_revenue",
    "apply_stress_multiplier",
    "average_daily_balance",
    "average_monthly_loan_payments",
    "average_monthly_mca_remittance",
    "capped_incurred",
    "claim_out_of_balance",
    "claims_trend",
    "collateral_coverage",
    "coverage_ratio",
    "dscr_stated_vs_verified",
    "existing_annual_debt_service",
    "experience_by_year",
    "incurred_difference_pct",
    "linear_trend",
    "location_tiv",
    "loss_ratio_by_year",
    "loss_ratios",
    "loss_run_age_days",
    "max_claims_in_rolling_window",
    "month_eligibility",
    "monthly_deposits",
    "months_between",
    "negative_balance_months",
    "net_operating_income",
    "nsf_events",
    "proposed_annual_debt_service",
    "rate_stressed_dscr",
    "rate_stressed_service",
    "revenue_gap_pct",
    "round_pct",
    "round_ratio",
    "schedule_tiv",
    "stress_multiplier",
    "stressed_dscr",
    "stressed_noi",
    "tiv_difference_pct",
    "tiv_reconciliation",
    "verified_monthly_revenue",
    "verify_revenue",
]


# --------------------------------------------------------------------------------------------------------------
# Credit: stress tests with any shock size (NBC-4.5)
# --------------------------------------------------------------------------------------------------------------


def stressed_noi(noi: float, verified_revenue: float, revenue_shock: float) -> float:
    """NOI after verified revenue falls by revenue_shock (0.20 = 20%): NOI loses the same dollars, to cents."""
    return round(noi - revenue_shock * verified_revenue, 2)


def stressed_dscr(noi: float, verified_revenue: float, annual_service: float, revenue_shock: float) -> float | None:
    """DSCR after a revenue shock: stressed NOI / annual debt service (None when debt service is not positive).

    Same arithmetic as policy_credit.check_stress, so stressed_dscr(noi, verified, service, 0.20) equals the
    policy's CALC-stressed_dscr_revenue.
    """
    return coverage_ratio(stressed_noi(noi, verified_revenue, revenue_shock), annual_service)


def rate_stressed_service(
    existing_service: float, product: str, amount: float, annual_rate_pct: float, term_months: int, add_bps: float
) -> float:
    """Annual debt service with the note rate raised by add_bps basis points (200 bps = 2.00 points), to cents.

    Existing debt service is unchanged; only the proposed loan is repriced (NBC-4.5 rate stress).
    """
    stressed_rate = annual_rate_pct + add_bps / BPS_PER_PERCENTAGE_POINT
    proposed = proposed_annual_debt_service(product, amount, stressed_rate, term_months)
    return round(existing_service + proposed, 2)


def rate_stressed_dscr(noi: float, stressed_service: float) -> float | None:
    """DSCR with the rate-stressed debt service (equals CALC-stressed_dscr_rate at +200 bps)."""
    return coverage_ratio(noi, stressed_service)


# --------------------------------------------------------------------------------------------------------------
# Credit: monthly revenue view and stated versus verified DSCR
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthlyRevenue:
    """One statement month: total deposits, NBC-3.3 non-revenue deposits, cash removed by the cap, eligible."""

    month: str
    total_deposits: float
    non_revenue: float
    excluded_cash: float
    eligible: float


def verified_monthly_revenue(statements: BankStatementSummary) -> list[MonthlyRevenue]:
    """NBC-3.3 eligible deposits for every statement month, oldest first (after transfers, refunds, loans ...)."""
    rows = []
    for month in sorted(statements.months, key=lambda item: item.month):
        result = month_eligibility(month, statements.cash_intensive_approved)
        rows.append(
            MonthlyRevenue(
                month=month.month,
                total_deposits=round(month.total_deposits, 2),
                non_revenue=round(result.non_revenue, 2),
                excluded_cash=round(result.excluded_cash, 2),
                eligible=round(result.eligible, 2),
            )
        )
    return rows


def annualized_eligible_revenue(statements: BankStatementSummary) -> float | None:
    """ESTIMATE: eligible deposits x 12 / months observed (None with no months). Not NBC-3.2 verified revenue.

    The reference policy verifies revenue only from 12 consecutive months (verify_revenue). This figure is for
    display, labeled as an estimate, and is never fed to the policy.
    """
    rows = verified_monthly_revenue(statements)
    if not rows:
        return None
    total = sum(row.eligible for row in rows)
    return round(total * MONTHS_PER_YEAR / len(rows), 2)


@dataclass(frozen=True)
class DscrComparison:
    """DSCR with stated revenue next to DSCR with verified revenue (NBC-4.1 NOI rule for both)."""

    noi_stated: float
    noi_verified: float
    dscr_stated: float | None
    dscr_verified: float | None
    difference: float | None  # dscr_stated - dscr_verified, 2 decimals; None when either is None


def dscr_stated_vs_verified(
    debt: DebtServiceInputs, stated_revenue: float, verified_revenue: float, annual_service: float
) -> DscrComparison:
    """NOI and DSCR computed once with the stated and once with the verified revenue (same debt service)."""
    noi_stated = net_operating_income(debt, stated_revenue)
    noi_verified = net_operating_income(debt, verified_revenue)
    dscr_stated = coverage_ratio(noi_stated, annual_service)
    dscr_verified = coverage_ratio(noi_verified, annual_service)
    difference = None
    if dscr_stated is not None and dscr_verified is not None:
        difference = round_ratio(dscr_stated - dscr_verified)
    return DscrComparison(
        noi_stated=noi_stated,
        noi_verified=noi_verified,
        dscr_stated=dscr_stated,
        dscr_verified=dscr_verified,
        difference=difference,
    )


# --------------------------------------------------------------------------------------------------------------
# Insurance: loss ratio, frequency and severity by policy year; TIV reconciliation
# --------------------------------------------------------------------------------------------------------------


def loss_ratio_by_year(loss_runs: LossRuns, line: str) -> dict[str, float | None]:
    """HM-4.1 annual loss ratio (percent) of one line, keyed by policy year start "YYYY-MM-DD".

    The values are agent.insurance_calculations.loss_ratios(...).annual_pct (None for a year without premium).
    """
    summary = loss_ratios(loss_runs, line)
    starts = [year.policy_year_start.isoformat() for year in loss_runs.years]
    return dict(zip(starts, summary.annual_pct, strict=True))


@dataclass(frozen=True)
class YearExperience:
    """Claims of one policy year (both lines): count, incurred, capped incurred and mean severity."""

    policy_year_start: date
    claim_count: int
    incurred: float
    capped_incurred: float
    mean_severity: float | None  # incurred / claim_count, to cents; None in a year without claims


def experience_by_year(loss_runs: LossRuns) -> list[YearExperience]:
    """Frequency (claim count) and severity (mean incurred per claim) for every policy year, oldest first."""
    rows = []
    for year in loss_runs.years:
        count = len(year.claims)
        incurred = round(sum(claim.incurred for claim in year.claims), 2)
        capped = round(sum(capped_incurred(claim) for claim in year.claims), 2)
        severity = round(incurred / count, 2) if count else None
        rows.append(
            YearExperience(
                policy_year_start=year.policy_year_start,
                claim_count=count,
                incurred=incurred,
                capped_incurred=capped,
                mean_severity=severity,
            )
        )
    return rows


def linear_trend(values: list[float | None]) -> float | None:
    """Least-squares slope per year of a yearly series; None entries are skipped (None with < 2 points).

    x is the year position (0, 1, 2, ...), so a missing year keeps its place. slope = sum((x - mean x)(y - mean y))
    / sum((x - mean x)^2), rounded to 2 decimals.
    """
    points = [(float(index), float(value)) for index, value in enumerate(values) if value is not None]
    if len(points) < 2:
        return None
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    spread = sum((x - mean_x) ** 2 for x, _ in points)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return round(covariance / spread, 2)


@dataclass(frozen=True)
class TivReconciliation:
    """HM-5.2: application TIV versus the property schedule, in total and per location."""

    stated_tiv: float
    schedule_tiv: float
    difference_usd: float  # stated - schedule, to cents
    difference_pct: float  # |stated - schedule| / schedule, percent, 1 decimal
    by_location: dict[str, float]  # location id -> building + contents + business income


def tiv_reconciliation(application: InsuranceApplication) -> TivReconciliation:
    """The application's stated TIV reconciled to the schedule (schedule_tiv and tiv_difference_pct)."""
    schedule_total = schedule_tiv(application)
    return TivReconciliation(
        stated_tiv=application.stated_tiv,
        schedule_tiv=schedule_total,
        difference_usd=round(application.stated_tiv - schedule_total, 2),
        difference_pct=tiv_difference_pct(application.stated_tiv, schedule_total),
        by_location={location.location_id: round(location_tiv(location), 2) for location in application.locations},
    )


# --------------------------------------------------------------------------------------------------------------
# Insurance: stress multiplier (LABELED: a stress multiplier, not a model)
# --------------------------------------------------------------------------------------------------------------


def stress_multiplier(frequency_shock: float, severity_shock: float) -> float:
    """STRESS MULTIPLIER, not a model: (1 + frequency shock) x (1 + severity shock), e.g. +10% and +25%.

    Expected losses are frequency x severity, so shocking each by a share multiplies losses by this factor. It
    describes a what-if, not a prediction.
    """
    return (1.0 + frequency_shock) * (1.0 + severity_shock)


def apply_stress_multiplier(loss_ratio_pct: float | None, multiplier: float) -> float | None:
    """The experience loss ratio (percent) times the STRESS MULTIPLIER, 1 decimal (None stays None).

    Caveat: this scales the ratio as a whole and ignores the HM-4.1 per-claim cap at $250,000; scenarios that must
    respect the cap scale the claim amounts in the application and let the policy recompute the ratio.
    """
    if loss_ratio_pct is None:
        return None
    return round_pct(loss_ratio_pct * multiplier)

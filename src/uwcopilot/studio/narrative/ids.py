"""Claim and assertion ids of the founder ledger, in one place so the template, the prompts and the package agree.

Every id fits the memo slot grammar ({{claim:ID}}): letters, digits, "_", "." and "-", starting with a letter.
Prefixes say where a value comes from:
- PLAN.*     what the plan is about (business type, metro, opening date, rate type): text claims;
- FOUNDER.*  figures the founder typed (USER_STATED; VERIFIED or CONFLICTING when a document backs or contradicts it);
- DOC.*      a document value that disagrees with the founder's figure (CONFLICTING);
- ASSUME.*   simulator inputs from a profile or metro range (ASSUMPTION), public data (VERIFIED), or a range the
             founder edited (USER_STATED, Plan.assumption_overrides);
- RATE.*     the note rate the simulation used;
- SITE.*     SiteIQ outputs and location factors;
- SIM.*      simulation outputs (MODEL_ESTIMATE);
- TORNADO.*  the sensitivity bars (MODEL_ESTIMATE);
- STRESS.*   the lender stress tests run on every simulated path (MODEL_ESTIMATE);
- PATH.*     the path-to-targets search (MODEL_ESTIMATE);
- PROGRAM.*  thresholds of the SYNTHETIC startup program (VERIFIED, kind policy);
- TARGET.*   the Studio's simulated targets (VERIFIED, kind policy: editable design choices, not lender rules);
- CALC.*     arithmetic over other claims; its status is the weakest of its inputs (derive.py).
Assertions start with "A-" (A-NSP-4.1, A-TARGET-dscr, ...).
"""

import re

UNSAFE = re.compile(r"[^A-Za-z0-9_.\-]")

# PLAN
PLAN_BUSINESS_TYPE = "PLAN.business_type"
PLAN_METRO = "PLAN.metro_id"
PLAN_OPENING_DATE = "PLAN.opening_date"
PLAN_DEMAND_UNIT = "PLAN.demand_unit"
PLAN_RATE_TYPE = "PLAN.rate_type"
# FOUNDER
FOUNDER_EQUITY = "FOUNDER.owner_equity"
FOUNDER_LOAN = "FOUNDER.loan_amount"
FOUNDER_TERM = "FOUNDER.loan_term_months"
FOUNDER_RATE = "FOUNDER.annual_rate_pct"
FOUNDER_SPREAD = "FOUNDER.rate_spread_pct"
FOUNDER_CAPEX = "FOUNDER.capex"
FOUNDER_OWNER_DRAW = "FOUNDER.owner_draw_per_month"
FOUNDER_CREDIT_SCORE = "FOUNDER.credit_score"
FOUNDER_EXPERIENCE = "FOUNDER.experience_months"
# RATE and capex used by the simulation
RATE_NOTE = "RATE.note_rate_pct"
ASSUME_CAPEX = "ASSUME.capex"
# SITE
SITE_OVERALL = "SITE.overall_score"
SITE_COVERAGE = "SITE.coverage_score"
SITE_DEMAND_MODE = "SITE.demand_mode"
# SIM
SIM_P_DSCR_Y1 = "SIM.p_dscr_year1"
SIM_P_DSCR_Y2 = "SIM.p_dscr_year2"
SIM_P_CASH = "SIM.p_cash_negative_24m"
SIM_P_STRESSED_Y2 = "SIM.p_stressed_dscr_year2"
SIM_BREAK_EVEN_P10 = "SIM.break_even_p10"
SIM_BREAK_EVEN_P50 = "SIM.break_even_p50"
SIM_BREAK_EVEN_P90 = "SIM.break_even_p90"
SIM_NOT_BREAK_EVEN = "SIM.share_not_break_even"
# PROGRAM thresholds
PROGRAM_MIN_AMOUNT = "PROGRAM.min_loan_amount"
PROGRAM_MAX_AMOUNT = "PROGRAM.max_loan_amount"
PROGRAM_MIN_TERM = "PROGRAM.min_term_months"
PROGRAM_MAX_TERM = "PROGRAM.max_term_months"
PROGRAM_MIN_EQUITY = "PROGRAM.min_equity_injection_pct"
PROGRAM_MIN_DSCR = "PROGRAM.min_projected_dscr"
PROGRAM_REVENUE_STRESS_SHARE = "PROGRAM.revenue_stress_share_pct"
PROGRAM_REVENUE_STRESS_MIN = "PROGRAM.revenue_stress_min_dscr"
PROGRAM_RATE_STRESS_ADD = "PROGRAM.rate_stress_add_pct"
PROGRAM_RATE_STRESS_MIN = "PROGRAM.rate_stress_min_dscr"
PROGRAM_RESERVE_MONTHS = "PROGRAM.reserve_months"
PROGRAM_MIN_EXPERIENCE = "PROGRAM.min_experience_months"
PROGRAM_MIN_CREDIT = "PROGRAM.min_credit_score"
PROGRAM_COLLATERAL_ABOVE = "PROGRAM.collateral_required_above"
# TARGET (Studio's simulated targets)
TARGET_DSCR_MIN = "TARGET.dscr_min"
TARGET_P_DSCR = "TARGET.p_dscr_min_pct"
TARGET_STRESSED_MIN = "TARGET.stressed_dscr_min"
TARGET_P_STRESSED = "TARGET.p_stressed_min_pct"
TARGET_P_CASH = "TARGET.p_cash_negative_max_pct"
TARGET_CASH_MONTHS = "TARGET.cash_months"
TARGET_STRESS_SHARE = "TARGET.stress_revenue_share_pct"
# CALC
CALC_OTHER_DEBT_AMOUNT = "CALC.other_debt_amount"
CALC_OTHER_DEBT_MONTHLY = "CALC.other_debt_monthly_payment"
CALC_PROJECT_COST = "CALC.total_project_cost"
CALC_EQUITY_PCT = "CALC.equity_injection_pct"
CALC_ANNUAL_DEBT_SERVICE = "CALC.projected_annual_debt_service"
CALC_RATE_STRESS_DEBT_SERVICE = "CALC.rate_stressed_annual_debt_service"
CALC_CASH_RESERVE = "CALC.cash_reserve_at_opening"
CALC_FIXED_COSTS = "CALC.monthly_fixed_costs"
CALC_REQUIRED_RESERVE = "CALC.required_reserve"
CALC_PROJECTED_DSCR = "CALC.projected_dscr"
CALC_REVENUE_STRESSED_NOI = "CALC.revenue_stressed_noi"
CALC_REVENUE_STRESSED_DSCR = "CALC.revenue_stressed_dscr"
CALC_RATE_STRESSED_DSCR = "CALC.rate_stressed_dscr"
# PATH
PATH_STATUS = "PATH.status"
PATH_BEFORE_P_DSCR = "PATH.before.p_dscr_pct"
PATH_BEFORE_P_STRESSED = "PATH.before.p_stressed_pct"
PATH_BEFORE_P_CASH = "PATH.before.p_cash_negative_pct"
# Assertions
A_AMOUNT = "A-NSP-8.1-amount"
A_TERM = "A-NSP-8.1-term"
A_EQUITY = "A-NSP-3.1"
A_EQUITY_DOCS = "A-NSP-3.1-statements"
A_INDUSTRY = "A-NSP-2.3"
A_EXPERIENCE = "A-NSP-2.1"
A_CREDIT = "A-NSP-2.2"
A_PROJECTED_DSCR = "A-NSP-4.1"
A_REVENUE_STRESS = "A-NSP-4.2-revenue"
A_RATE_STRESS = "A-NSP-4.2-rate"
A_RESERVE = "A-NSP-5.1"
A_COLLATERAL = "A-NSP-7.1"
A_TARGET_DSCR = "A-TARGET-dscr"
A_TARGET_STRESS = "A-TARGET-stress"
A_TARGET_CASH = "A-TARGET-cash"


def safe_id(*parts: object) -> str:
    """Join parts with "." and replace every character outside the slot grammar with "_"."""
    return UNSAFE.sub("_", ".".join(str(part) for part in parts))


def assume_id(name: str) -> str:
    """ASSUME.<distribution name>."""
    return safe_id("ASSUME", name)


def site_factor_id(factor: str) -> str:
    """SITE.factor.<factor name>."""
    return safe_id("SITE", "factor", factor)


def year_id(year: int, field: str) -> str:
    """SIM.y<year>.<field>, for example SIM.y2.noi_p50."""
    return safe_id("SIM", f"y{year}", field)


def month_id(month: int, field: str) -> str:
    """SIM.m<month>.<field>, for example SIM.m3.cash_p10."""
    return safe_id("SIM", f"m{month}", field)


def tornado_id(rank: int, field: str) -> str:
    """TORNADO.r<rank>.<field>."""
    return safe_id("TORNADO", f"r{rank}", field)


def stress_id(name: str, field: str) -> str:
    """STRESS.<test name>.<field>."""
    return safe_id("STRESS", name, field)


def driver_assertion_id(rank: int) -> str:
    """A-NSP-6.1-r<rank>: is the rank-th top driver backed by a supporting document?"""
    return f"A-NSP-6.1-r{rank}"


def other_debt_id(index: int, field: str) -> str:
    """FOUNDER.other_debt.<index>.<field> (index from 1)."""
    return safe_id("FOUNDER", "other_debt", index, field)


def change_set_id(rank: int, field: str) -> str:
    """PATH.cs<rank>.<field> (rank 0 is the closest result when nothing reaches the targets)."""
    return safe_id("PATH", f"cs{rank}", field)

"""The SYNTHETIC startup program's thresholds and the Studio's simulated targets, as ledger claims.

Thresholds (PROGRAM.*) are VERIFIED policy claims citing their NSP section of corpus/programs/northwind_startup_
program.md; every number is imported from agent/policy_startup.py or agent/policy_credit.py, never retyped. Each
carries the comparator it is read with ("at least", "at most", "minimum", "above"), which the founder checker uses.
The Studio's simulated targets (TARGET.*) are VERIFIED policy claims too, sourced as editable design choices of the
build plan (path_to_approval/targets.py), not lender rules. They hold the target's own values, edited or not;
target_measures.py picks the simulated probability that was measured for exactly these values.
"""

from uwcopilot.agent.policy_credit import (
    COLLATERAL_REQUIRED_ABOVE,
    RATE_STRESS_ADD_PCT,
    RATE_STRESS_MIN_DSCR,
    REVENUE_STRESS_MIN_DSCR,
    REVENUE_STRESS_SHARE,
)
from uwcopilot.agent.policy_startup import (
    MIN_EQUITY_INJECTION_PCT,
    MIN_EXPERIENCE_MONTHS,
    MIN_STARTUP_CREDIT_SCORE,
    RESERVE_MONTHS_REQUIRED,
    STARTUP_DSCR_MINIMUM,
    STARTUP_MAX_AMOUNT,
    STARTUP_MAX_TERM_MONTHS,
    STARTUP_MIN_AMOUNT,
    STARTUP_MIN_TERM_MONTHS,
)
from uwcopilot.evidence.ledger_models import Claim, Provenance
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.derive import percent_of, policy_claim
from uwcopilot.studio.path_to_approval.models import Target
from uwcopilot.studio.path_to_approval.targets import EDITED_BY_USER

PROGRAM_SOURCE = (
    "corpus/programs/northwind_startup_program.md {section} (SYNTHETIC program of a fictional institution; "
    "illustrative thresholds, not an industry standard)"
)


def program_threshold_claims(rate_type: str) -> list[Claim]:
    """The program thresholds the checks use, each citing its NSP section."""

    def threshold(
        claim_id: str, label: str, value: float, unit: str, display: str, section: str, comparator: str | None
    ) -> Claim:
        source = PROGRAM_SOURCE.format(section=section)
        return policy_claim(claim_id, label, value, unit, display, section, source, comparator)

    claims = [
        threshold(
            ids.PROGRAM_MIN_AMOUNT,
            "Startup term loan, minimum amount",
            STARTUP_MIN_AMOUNT,
            "USD",
            "usd0",
            "NSP-8.1",
            "at least",
        ),
        threshold(
            ids.PROGRAM_MAX_AMOUNT,
            "Startup term loan, maximum amount",
            STARTUP_MAX_AMOUNT,
            "USD",
            "usd0",
            "NSP-8.1",
            "at most",
        ),
        threshold(
            ids.PROGRAM_MIN_TERM,
            "Startup term loan, shortest term",
            STARTUP_MIN_TERM_MONTHS,
            "MONTHS",
            "months",
            "NSP-8.1",
            "at least",
        ),
        threshold(
            ids.PROGRAM_MAX_TERM,
            "Startup term loan, longest term",
            STARTUP_MAX_TERM_MONTHS,
            "MONTHS",
            "months",
            "NSP-8.1",
            "at most",
        ),
        threshold(
            ids.PROGRAM_MIN_EQUITY,
            "Minimum equity injection",
            MIN_EQUITY_INJECTION_PCT,
            "PERCENT",
            "pct1",
            "NSP-3.1",
            "at least",
        ),
        threshold(
            ids.PROGRAM_MIN_DSCR,
            "Minimum projected DSCR",
            STARTUP_DSCR_MINIMUM,
            "RATIO",
            "ratio2",
            "NSP-4.1",
            "minimum",
        ),
        threshold(
            ids.PROGRAM_REVENUE_STRESS_SHARE,
            "Revenue stress: revenue cut",
            percent_of(REVENUE_STRESS_SHARE),
            "PERCENT",
            "pct1",
            "NSP-4.2",
            None,
        ),
        threshold(
            ids.PROGRAM_REVENUE_STRESS_MIN,
            "Revenue stress: minimum stressed DSCR",
            REVENUE_STRESS_MIN_DSCR,
            "RATIO",
            "ratio2",
            "NSP-4.2",
            "minimum",
        ),
        threshold(
            ids.PROGRAM_RESERVE_MONTHS,
            "Working-capital reserve: months of fixed costs and debt service",
            RESERVE_MONTHS_REQUIRED,
            "MONTHS",
            "months",
            "NSP-5.1",
            "at least",
        ),
        threshold(
            ids.PROGRAM_MIN_EXPERIENCE,
            "Minimum relevant owner experience",
            MIN_EXPERIENCE_MONTHS,
            "MONTHS",
            "months",
            "NSP-2.1",
            "at least",
        ),
        threshold(
            ids.PROGRAM_MIN_CREDIT,
            "Minimum principal credit score",
            MIN_STARTUP_CREDIT_SCORE,
            "SCORE",
            "int",
            "NSP-2.2",
            "at least",
        ),
        threshold(
            ids.PROGRAM_COLLATERAL_ABOVE,
            "Collateral required for requests above",
            COLLATERAL_REQUIRED_ABOVE,
            "USD",
            "usd0",
            "NSP-7.1",
            "above",
        ),
    ]
    if rate_type == "variable":
        claims += [
            threshold(
                ids.PROGRAM_RATE_STRESS_ADD,
                "Rate stress: percentage points added to the note rate",
                RATE_STRESS_ADD_PCT,
                "PERCENT",
                "pct1",
                "NSP-4.2",
                None,
            ),
            threshold(
                ids.PROGRAM_RATE_STRESS_MIN,
                "Rate stress: minimum stressed DSCR",
                RATE_STRESS_MIN_DSCR,
                "RATIO",
                "ratio2",
                "NSP-4.2",
                "minimum",
            ),
        ]
    return claims


def target_claims(target: Target) -> list[Claim]:
    """The Studio's simulated targets as given (default or edited by the founder), each with its source."""

    def target_claim(
        claim_id: str, label: str, value: float, unit: str, display: str, field: str, comparator: str | None
    ) -> Claim:
        source = target.sources.get(field, "Studio target (editable design choice)")
        claim = policy_claim(claim_id, label, value, unit, display, f"Studio target {field}", source, comparator)
        if source != EDITED_BY_USER:
            return claim
        # A target the founder edited is their own figure, not the Studio's: USER_STATED, so it reads as stated.
        edited = Provenance(kind="user_input", ref=f"target.{field}", source=source, details=claim.provenance.details)
        return Claim.model_validate({**claim.model_dump(), "status": "USER_STATED", "provenance": edited})

    return [
        target_claim(
            ids.TARGET_DSCR_MIN,
            "Year-2 DSCR the target counts as covered",
            target.dscr_min,
            "RATIO",
            "ratio2",
            "dscr_min",
            None,
        ),
        target_claim(
            ids.TARGET_P_DSCR,
            "Target: least chance of reaching that DSCR in year 2",
            percent_of(target.p_dscr_min),
            "PERCENT",
            "pct1",
            "p_dscr_min",
            "at least",
        ),
        target_claim(
            ids.TARGET_STRESS_SHARE,
            "Revenue cut of the stress target",
            percent_of(target.stress_revenue_share),
            "PERCENT",
            "pct1",
            "stress_revenue_share",
            None,
        ),
        target_claim(
            ids.TARGET_STRESSED_MIN,
            "Stressed year-2 DSCR the stress target counts",
            target.stressed_dscr_min,
            "RATIO",
            "ratio2",
            "stressed_dscr_min",
            "at least",
        ),
        target_claim(
            ids.TARGET_P_STRESSED,
            "Target: least chance of passing the revenue stress in year 2",
            percent_of(target.p_stressed_min),
            "PERCENT",
            "pct1",
            "p_stressed_min",
            "at least",
        ),
        target_claim(
            ids.TARGET_P_CASH,
            "Target: largest chance of cash falling below zero",
            percent_of(target.p_cash_negative_max),
            "PERCENT",
            "pct1",
            "p_cash_negative_max",
            "at most",
        ),
        target_claim(
            ids.TARGET_CASH_MONTHS,
            "Months the cash check covers",
            target.cash_months,
            "MONTHS",
            "months",
            "cash_months",
            None,
        ),
    ]

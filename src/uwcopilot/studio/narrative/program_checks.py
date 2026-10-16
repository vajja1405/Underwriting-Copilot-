"""The checks of a founder plan against the SYNTHETIC startup program and the Studio's targets (assertions).

One assertion per program rule the plan can be read against: loan size and term (NSP-8.1), equity injection and its
bank statements (NSP-3.1), industry (NSP-2.3), owner experience (NSP-2.1) and credit (NSP-2.2), projected DSCR
(NSP-4.1), the stress tests (NSP-4.2), the reserve (NSP-5.1), collateral (NSP-7.1), whether each top driver is
VERIFIED (NSP-6.1), and the three simulated targets. A check whose input is UNKNOWN is not evaluated (result None).
The phrases are what the renderer prints in an {{assert:}} slot: no digits, no decision words, and no comparator
word ("below", "above", "under", "at least" ...) except "minimum" right before the threshold it describes, so the
founder checker (checker.py) never reads a phrase's comparator as belonging to a founder's own number. These checks
explain the plan; Studio never approves or declines (rule C), and the lender runs every rule itself.
"""

from uwcopilot.agent.policy_credit import (
    COLLATERAL_REQUIRED_ABOVE,
    RATE_STRESS_MIN_DSCR,
    REVENUE_STRESS_MIN_DSCR,
)
from uwcopilot.agent.policy_startup import (
    EQUITY_STATEMENTS_DOCUMENT,
    MIN_EQUITY_INJECTION_PCT,
    MIN_EXPERIENCE_MONTHS,
    MIN_STARTUP_CREDIT_SCORE,
    STARTUP_DSCR_MINIMUM,
    STARTUP_MAX_AMOUNT,
    STARTUP_MAX_TERM_MONTHS,
    STARTUP_MIN_AMOUNT,
    STARTUP_MIN_TERM_MONTHS,
    industry_category,
)
from uwcopilot.evidence.ledger_models import Assertion, Claim
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.assumption_claims import SITE_DEMAND_INPUT
from uwcopilot.studio.narrative.derive import percent_of
from uwcopilot.studio.narrative.program_calcs import input_claim_of
from uwcopilot.studio.narrative.target_measures import TargetMeasures
from uwcopilot.studio.path_to_approval.models import PlanCase, Target

PHRASES: dict[str, tuple[str, str, str, str]] = {
    ids.A_AMOUNT: (
        "loan amount within the startup term loan limits",
        "is within the program's loan size range",
        "is outside the program's loan size range",
        "could not be checked against the program's loan size range",
    ),
    ids.A_TERM: (
        "loan term within the startup term loan range",
        "is within the program's term range",
        "is outside the program's term range",
        "could not be checked against the program's term range",
    ),
    ids.A_EQUITY: (
        "equity injection at or above the program minimum",
        "meets the program's minimum",
        "falls short of the program's minimum",
        "could not be compared with the program's minimum",
    ),
    ids.A_EQUITY_DOCS: (
        "owner cash equity backed by bank statements in the file",
        "is backed by bank statements in this plan",
        "is not yet backed by bank statements in this plan",
        "could not be checked against bank statements",
    ),
    ids.A_INDUSTRY: (
        "business type neither prohibited nor restricted for startups",
        "is eligible for the program",
        "is a restricted industry for the program and needs Senior Underwriter review",
        "could not be checked against the program's industry list",
    ),
    "industry_prohibited": (
        "business type neither prohibited nor restricted for startups",
        "is eligible for the program",
        "is a prohibited industry for the program",
        "could not be checked against the program's industry list",
    ),
    ids.A_EXPERIENCE: (
        "most relevant owner experience at or above the program minimum",
        "meets the program's experience requirement",
        "falls short of the program's experience requirement",
        "could not be checked because it was not entered",
    ),
    ids.A_CREDIT: (
        "lowest principal credit score at or above the program minimum",
        "meets the program's credit score requirement",
        "falls short of the program's credit score requirement",
        "could not be checked because it was not entered",
    ),
    ids.A_PROJECTED_DSCR: (
        "projected second-year DSCR at or above the program minimum",
        "meets the program's minimum",
        "falls short of the program's minimum",
        "could not be compared with the program's minimum",
    ),
    ids.A_REVENUE_STRESS: (
        "revenue-stressed second-year DSCR at or above the stress minimum",
        "meets the stress minimum",
        "falls short of the stress minimum",
        "could not be compared with the stress minimum",
    ),
    ids.A_RATE_STRESS: (
        "rate-stressed second-year DSCR at or above the stress minimum",
        "meets the stress minimum",
        "falls short of the stress minimum",
        "could not be compared with the stress minimum",
    ),
    ids.A_RESERVE: (
        "cash reserve at opening at or above the required reserve",
        "meets the program's reserve rule",
        "falls short of the program's reserve rule",
        "could not be checked against the program's reserve rule",
    ),
    ids.A_COLLATERAL: (
        "loan amount at or below the unsecured limit",
        "may be unsecured in the program",
        "needs collateral in the program, which this plan does not list yet",
        "could not be checked against the program's collateral rule",
    ),
    "driver": (
        "top driver backed by a supporting document or verified",
        "is backed by a supporting document in this plan",
        "has no supporting document in this plan yet",
        "could not be checked",
    ),
    "target": (
        "simulated probability at or above the Studio target",
        "meets the Studio target",
        "falls short of the Studio target",
        "could not be compared with the Studio target",
    ),
    "target_max": (
        "simulated probability at or below the Studio target",
        "is within the Studio target",
        "misses the Studio target",
        "could not be compared with the Studio target",
    ),
}


def make_assertion(
    assertion_id: str,
    rule: str,
    sections: tuple[str, ...],
    result: bool | None,
    evidence: list[Claim],
    phrase_key: str | None = None,
    trigger_ids: tuple[str, ...] = (),
    detail: str = "",
) -> Assertion:
    """One check. A check that reads an UNKNOWN claim is not evaluated (result None)."""
    expression, phrase_true, phrase_false, phrase_unknown = PHRASES[phrase_key or assertion_id]
    unknown = tuple(claim.id for claim in evidence if claim.status == "UNKNOWN")
    return Assertion(
        id=assertion_id,
        rule=rule,
        rule_ref=trigger_ids[0] if trigger_ids else None,
        section_id=sections[0] if sections else None,
        section_ids=sections,
        expression=expression,
        result=None if unknown else result,
        phrase_true=phrase_true,
        phrase_false=phrase_false,
        phrase_unknown=phrase_unknown,
        detail=detail,
        trigger_ids=trigger_ids,
        evidence_ids=tuple(claim.id for claim in evidence),
        depends_on_unknown=unknown,
    )


def at_least(claim: Claim, threshold: float) -> bool | None:
    """claim value >= threshold (None when the claim has no value)."""
    return None if claim.value is None else float(claim.value) >= threshold


def documented_with(claim: Claim | None, document_types: tuple[str, ...]) -> bool:
    """True when the claim is VERIFIED by a document of one of these types."""
    if claim is None or claim.status != "VERIFIED" or claim.provenance.kind != "document":
        return False
    listed = str(claim.provenance.details.get("document_types", ""))
    return any(kind in listed.split(", ") for kind in document_types)


class ProgramChecks:
    """The assertions of one plan (module docstring)."""

    def __init__(
        self,
        case: PlanCase,
        result: FeasibilityResult,
        claims: dict[str, Claim],
        target: Target,
        measures: TargetMeasures,
    ) -> None:
        """claims: every claim of the ledger by id; target: the Studio's simulated targets; measures: the probability
        claim that answers each target (target_measures.py)."""
        self.case = case
        self.result = result
        self.claims = claims
        self.target = target
        self.measures = measures

    def get(self, claim_id: str) -> Claim:
        """A claim that must exist."""
        return self.claims[claim_id]

    def loan_checks(self) -> list[Assertion]:
        """NSP-8.1 amount and term, NSP-7.1 collateral."""
        loan, term = self.get(ids.FOUNDER_LOAN), self.get(ids.FOUNDER_TERM)
        amount_ok = STARTUP_MIN_AMOUNT <= float(loan.value) <= STARTUP_MAX_AMOUNT
        term_ok = STARTUP_MIN_TERM_MONTHS <= float(term.value) <= STARTUP_MAX_TERM_MONTHS
        return [
            make_assertion(
                ids.A_AMOUNT,
                "product_limits",
                ("NSP-8.1",),
                amount_ok,
                [loan, self.get(ids.PROGRAM_MIN_AMOUNT), self.get(ids.PROGRAM_MAX_AMOUNT)],
                trigger_ids=() if amount_ok else ("R-NSP-10",),
            ),
            make_assertion(
                ids.A_TERM,
                "product_limits",
                ("NSP-8.1",),
                term_ok,
                [term, self.get(ids.PROGRAM_MIN_TERM), self.get(ids.PROGRAM_MAX_TERM)],
                trigger_ids=() if term_ok else ("R-NSP-10",),
            ),
            make_assertion(
                ids.A_COLLATERAL,
                "collateral",
                ("NSP-7.1", "NBC-5.1"),
                float(loan.value) <= COLLATERAL_REQUIRED_ABOVE,
                [loan, self.get(ids.PROGRAM_COLLATERAL_ABOVE)],
            ),
        ]

    def equity_checks(self) -> list[Assertion]:
        """NSP-3.1: the share, and whether bank statements back the equity."""
        share = self.get(ids.CALC_EQUITY_PCT)
        meets = at_least(share, MIN_EQUITY_INJECTION_PCT)
        equity = self.get(ids.FOUNDER_EQUITY)
        return [
            make_assertion(
                ids.A_EQUITY,
                "equity_injection",
                ("NSP-3.1",),
                meets,
                # The share is computed from the owner's equity and the total project cost, so both are evidence too.
                [share, self.get(ids.PROGRAM_MIN_EQUITY), equity, self.get(ids.CALC_PROJECT_COST)],
                trigger_ids=() if meets else ("R-NSP-05",),
            ),
            make_assertion(
                ids.A_EQUITY_DOCS,
                "equity_verification",
                ("NSP-3.1", "NSP-1.3"),
                documented_with(equity, (EQUITY_STATEMENTS_DOCUMENT,)),
                [equity],
            ),
        ]

    def owner_checks(self) -> list[Assertion]:
        """NSP-2.3 industry, NSP-2.1 experience, NSP-2.2 credit score."""
        category = industry_category(self.case.profile_id)
        eligible = category == "eligible"
        sections = ("NSP-2.3", "NBC-2.3") if category == "restricted_restaurant" else ("NSP-2.3",)
        experience, credit = self.get(ids.FOUNDER_EXPERIENCE), self.get(ids.FOUNDER_CREDIT_SCORE)
        experience_ok = at_least(experience, MIN_EXPERIENCE_MONTHS)
        credit_ok = at_least(credit, MIN_STARTUP_CREDIT_SCORE)
        return [
            make_assertion(
                ids.A_INDUSTRY,
                "industry",
                sections,
                eligible,
                [self.get(ids.PLAN_BUSINESS_TYPE)],
                phrase_key="industry_prohibited" if category == "prohibited" else None,
                trigger_ids=() if eligible else ("R-NSP-02",),
                detail=f"category {category}",
            ),
            make_assertion(
                ids.A_EXPERIENCE,
                "owner_experience",
                ("NSP-2.1",),
                experience_ok,
                [experience, self.get(ids.PROGRAM_MIN_EXPERIENCE)],
                trigger_ids=("R-NSP-03",) if experience_ok is False else (),
            ),
            make_assertion(
                ids.A_CREDIT,
                "principal_credit",
                ("NSP-2.2",),
                credit_ok,
                [credit, self.get(ids.PROGRAM_MIN_CREDIT)],
                trigger_ids=("R-NSP-04",) if credit_ok is False else (),
            ),
        ]

    def coverage_checks(self) -> list[Assertion]:
        """NSP-4.1 projected DSCR, NSP-4.2 stresses, NSP-5.1 reserve."""
        projected, revenue_stressed = self.get(ids.CALC_PROJECTED_DSCR), self.get(ids.CALC_REVENUE_STRESSED_DSCR)
        projected_ok = at_least(projected, STARTUP_DSCR_MINIMUM)
        revenue_ok = at_least(revenue_stressed, REVENUE_STRESS_MIN_DSCR)
        reserve, required = self.get(ids.CALC_CASH_RESERVE), self.get(ids.CALC_REQUIRED_RESERVE)
        reserve_ok = None if required.value is None else at_least(reserve, float(required.value))
        checks = [
            make_assertion(
                ids.A_PROJECTED_DSCR,
                "projected_dscr",
                ("NSP-4.1",),
                projected_ok,
                [projected, self.get(ids.PROGRAM_MIN_DSCR)],
                trigger_ids=("R-NSP-06",) if projected_ok is False else (),
            ),
            make_assertion(
                ids.A_REVENUE_STRESS,
                "stress_test_revenue",
                ("NSP-4.2",),
                revenue_ok,
                [revenue_stressed, self.get(ids.PROGRAM_REVENUE_STRESS_MIN)],
                trigger_ids=("R-NSP-07",) if revenue_ok is False else (),
            ),
        ]
        if self.case.loan.rate_type == "variable":
            rate_stressed = self.get(ids.CALC_RATE_STRESSED_DSCR)
            rate_ok = at_least(rate_stressed, RATE_STRESS_MIN_DSCR)
            checks.append(
                make_assertion(
                    ids.A_RATE_STRESS,
                    "stress_test_rate",
                    ("NSP-4.2",),
                    rate_ok,
                    [rate_stressed, self.get(ids.PROGRAM_RATE_STRESS_MIN)],
                    trigger_ids=("R-NSP-07",) if rate_ok is False else (),
                )
            )
        checks.append(
            make_assertion(
                ids.A_RESERVE,
                "working_capital_reserve",
                ("NSP-5.1",),
                reserve_ok,
                [reserve, required],
                trigger_ids=("R-NSP-08",) if reserve_ok is False else (),
            )
        )
        return checks

    def driver_checks(self) -> list[Assertion]:
        """NSP-6.1: is each of the top three drivers VERIFIED (by a document in the file, or by public data)?"""
        if self.result.tornado is None:
            return []
        checks = []
        for rank, name in enumerate(self.result.tornado.top_drivers, start=1):
            claim = self.driver_claim(name)
            backed = claim is not None and claim.status == "VERIFIED"
            evidence = [self.get(ids.tornado_id(rank, "input"))] + ([claim] if claim is not None else [])
            checks.append(
                make_assertion(
                    ids.driver_assertion_id(rank),
                    "top_driver_support",
                    ("NSP-6.1",),
                    backed,
                    evidence,
                    phrase_key="driver",
                    detail=f"top driver {rank}: {name}",
                )
            )
        return checks

    def driver_claim(self, name: str) -> Claim | None:
        """The claim behind one simulator input: the site demand for mature_demand, else program_calcs.input_claim_of
        (an edited range, the founder's figure, or its ASSUME.* claim). None when the ledger has no claim for it,
        which counts as not backed."""
        if name == SITE_DEMAND_INPUT:
            return self.claims.get(ids.SITE_DEMAND_MODE)
        return input_claim_of(name, self.claims)

    def target_checks(self) -> list[Assertion]:
        """The three simulated targets (no program section: they are Studio design choices). Each compares the
        probability measured for exactly that target (target_measures.py); an UNKNOWN one leaves it not evaluated."""
        p_dscr, p_stressed = self.get(self.measures.dscr), self.get(self.measures.stress)
        p_cash = self.get(self.measures.cash)
        cash_ok = None if p_cash.value is None else float(p_cash.value) <= percent_of(self.target.p_cash_negative_max)
        return [
            make_assertion(
                ids.A_TARGET_DSCR,
                "target_dscr",
                (),
                at_least(p_dscr, percent_of(self.target.p_dscr_min)),
                [p_dscr, self.get(ids.TARGET_DSCR_MIN), self.get(ids.TARGET_P_DSCR)],
                phrase_key="target",
            ),
            make_assertion(
                ids.A_TARGET_STRESS,
                "target_stress",
                (),
                at_least(p_stressed, percent_of(self.target.p_stressed_min)),
                [p_stressed, self.get(ids.TARGET_STRESSED_MIN), self.get(ids.TARGET_P_STRESSED)],
                phrase_key="target",
            ),
            make_assertion(
                ids.A_TARGET_CASH,
                "target_cash",
                (),
                cash_ok,
                [p_cash, self.get(ids.TARGET_CASH_MONTHS), self.get(ids.TARGET_P_CASH)],
                phrase_key="target_max",
            ),
        ]

    def all(self) -> list[Assertion]:
        """Every check of the plan."""
        return [
            *self.loan_checks(),
            *self.equity_checks(),
            *self.owner_checks(),
            *self.coverage_checks(),
            *self.driver_checks(),
            *self.target_checks(),
        ]

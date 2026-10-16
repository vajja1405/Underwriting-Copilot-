"""The program arithmetic on the founder's plan (CALC.* claims), with the program's own rounding.

Arithmetic uses the SYNTHETIC startup program's rules (policy_startup: exact decimals rounded half up, the NBC-4.1
term loan payment), so the founder's number and the lender's number agree. The status of each result follows
derive.derived_claim: never more certain than its least certain input. Formulas:
- total project cost = owner equity + loan + other debt (sources of funds = uses of funds, NSP-1.2);
- equity injection = owner equity / total project cost, percent, one decimal (NSP-3.1);
- loan annual debt service = 12 level payments at the note rate (NBC-4.1, NSP-1.2);
- projected annual debt service = loan + 12 x other debt payments (NSP-1.2);
- cash reserve at opening = total project cost - build-out and equipment (the uses left for working capital);
- monthly fixed costs = paid hours x wage x (1 + payroll burden) + first-year rent, at their most likely values
  (the simulator's only costs that do not scale with revenue);
- required reserve = 3 x (monthly fixed costs + projected annual debt service / 12) (NSP-5.1);
- projected DSCR = year-2 median NOI / projected annual debt service (NSP-1.2, NSP-4.1);
- revenue-stressed DSCR = (year-2 median NOI - 20% of year-2 median revenue) / projected annual debt service (NSP-4.2);
- rate-stressed DSCR (variable rate only) = year-2 median NOI / debt service at the note rate + 2.00 points (NSP-4.2).
A ratio whose debt service is not positive is UNKNOWN, never a number.
"""

from decimal import Decimal

from uwcopilot.agent.credit_calculations import proposed_annual_debt_service
from uwcopilot.agent.policy_credit import (
    RATE_STRESS_ADD_PCT,
)
from uwcopilot.agent.policy_startup import (
    DEBT_SERVICE_PRODUCT,
    RESERVE_MONTHS_REQUIRED,
    half_up,
    program_pct,
    program_ratio,
    revenue_stressed_noi,
    to_decimal,
)
from uwcopilot.evidence.ledger_models import Claim, unknown_claim
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.derive import derived_claim
from uwcopilot.studio.narrative.founder_claims import FIELD_FOR_INPUT, claim_id_for_field
from uwcopilot.studio.path_to_approval.models import PlanCase

PROJECTION_YEAR = 2
# Phrases: (expression, true, false, unknown). No digits and no decision words: the renderer prints them as they are.


def input_claim_of(name: str, claims: dict[str, Claim]) -> Claim | None:
    """The claim of one simulator input, the one whose value the simulator used: a range the founder edited
    (USER_STATED ASSUME.<name>), else the founder's typed figure, else the ASSUME.<name> claim (None when absent)."""
    assumed = claims.get(ids.assume_id(name))
    if assumed is not None and assumed.status == "USER_STATED":
        return assumed
    field = FIELD_FOR_INPUT.get(name)
    founder = claims.get(claim_id_for_field(field)) if field is not None else None
    return founder or assumed


def cents(value: Decimal | float) -> float:
    """A dollar amount rounded half up to the cent (the program's convention)."""
    return half_up(value if isinstance(value, Decimal) else to_decimal(float(value)), 2)


def loan_service(amount: float, rate_pct: float, term: int, other_monthly: float = 0.0) -> float:
    """12 level payments of the loan (NBC-4.1) to the cent, plus 12 payments of the other debt, to the cent."""
    loan = cents(proposed_annual_debt_service(DEBT_SERVICE_PRODUCT, amount, rate_pct, term))
    return cents(to_decimal(loan) + to_decimal(other_monthly) * 12)


def monthly_fixed_cost(hours: float, wage: float, burden_share: float, rent: float) -> float:
    """Staff (paid hours x wage x (1 + payroll burden)) plus rent for one month, to the cent."""
    return cents(hours * wage * (1.0 + burden_share) + rent)


class ProgramCalculations:
    """The CALC.* claims of one plan, built in order (later formulas read earlier results)."""

    def __init__(self, case: PlanCase, result: FeasibilityResult, claims: dict[str, Claim]) -> None:
        """claims: every claim built so far, by id (founder, assumption, simulation); new claims are added to it."""
        self.case = case
        self.result = result
        self.claims = claims
        self.inputs = {item.name: item for item in result.distributions}

    def add(self, claim: Claim) -> Claim:
        """Keep a new claim and return it."""
        self.claims[claim.id] = claim
        return claim

    def get(self, claim_id: str) -> Claim:
        """A claim that must exist."""
        return self.claims[claim_id]

    def input_claim(self, name: str) -> Claim:
        """The claim of one simulator input (input_claim_of)."""
        claim = input_claim_of(name, self.claims)
        if claim is None:
            raise KeyError(f"the ledger has no claim for the simulator input {name}")
        return claim

    def rate_bounds(self) -> tuple[float, float]:
        """(low, high) of the note rate's range (its value twice when it has none)."""
        rate = self.get(ids.RATE_NOTE)
        if rate.value_range is not None:
            return rate.value_range.low, rate.value_range.high
        return float(rate.value), float(rate.value)

    def other_debt(self) -> tuple[float, float, list[Claim], list[Claim]]:
        """(total amount, total monthly payment, their claims) of the other debt; zeros when there is none."""
        debts = self.case.facts.other_debt
        if not debts:
            return 0.0, 0.0, [], []
        amounts = [self.get(ids.other_debt_id(index, "amount")) for index in range(1, len(debts) + 1)]
        payments = [self.get(ids.other_debt_id(index, "monthly_payment")) for index in range(1, len(debts) + 1)]
        amount = cents(sum((to_decimal(item.amount) for item in debts), Decimal(0)))
        monthly = cents(sum((to_decimal(item.monthly_payment) for item in debts), Decimal(0)))
        self.add(
            derived_claim(
                ids.CALC_OTHER_DEBT_AMOUNT,
                "Other debt, total amount",
                amount,
                "USD",
                "usd0",
                "sum of other debt amounts",
                amounts,
            )
        )
        self.add(
            derived_claim(
                ids.CALC_OTHER_DEBT_MONTHLY,
                "Other debt, total monthly payment",
                monthly,
                "USD",
                "usd0",
                "sum of other debt monthly payments",
                payments,
            )
        )
        return amount, monthly, [self.get(ids.CALC_OTHER_DEBT_AMOUNT)], [self.get(ids.CALC_OTHER_DEBT_MONTHLY)]

    def capex_claim(self) -> Claim:
        """The build-out cost the simulation used: the founder's figure or the profile ASSUMPTION."""
        return self.claims.get(ids.FOUNDER_CAPEX) or self.get(ids.ASSUME_CAPEX)

    def sources(self, other_amount: float, other_claims: list[Claim]) -> float:
        """Total project cost and equity injection (NSP-1.2, NSP-3.1)."""
        equity, loan = self.case.plan.owner_equity, self.case.loan.amount
        cost = cents(to_decimal(equity) + to_decimal(loan) + to_decimal(other_amount))
        inputs = [self.get(ids.FOUNDER_EQUITY), self.get(ids.FOUNDER_LOAN), *other_claims]
        self.add(
            derived_claim(
                ids.CALC_PROJECT_COST,
                "Total project cost (sources of funds = uses of funds)",
                cost,
                "USD",
                "usd0",
                "owner equity + loan + other debt",
                inputs,
            )
        )
        share = program_pct(equity, cost) if cost > 0 else 0.0
        self.add(
            derived_claim(
                ids.CALC_EQUITY_PCT,
                "Equity injection (owner equity / total project cost)",
                share,
                "PERCENT",
                "pct1",
                "owner equity / total project cost x 100, one decimal half up",
                [self.get(ids.FOUNDER_EQUITY), self.get(ids.CALC_PROJECT_COST)],
            )
        )
        return cost

    def debt_service(self, other_monthly: float, other_claims: list[Claim]) -> tuple[float, float, float]:
        """Loan and projected annual debt service, with the range the note rate's range gives."""
        loan, term = self.case.loan.amount, self.case.loan.term_months
        rate = float(self.get(ids.RATE_NOTE).value)
        low_rate, high_rate = self.rate_bounds()
        inputs = [self.get(ids.FOUNDER_LOAN), self.get(ids.FOUNDER_TERM), self.get(ids.RATE_NOTE)]
        loan_value = loan_service(loan, rate, term)
        self.add(
            derived_claim(
                "CALC.loan_annual_debt_service",
                "Loan annual debt service (12 level payments)",
                loan_value,
                "USD",
                "usd0",
                "12 level payments of the loan at the note rate",
                inputs,
                value_range=(loan_service(loan, low_rate, term), loan_service(loan, high_rate, term)),
            )
        )
        total = loan_service(loan, rate, term, other_monthly)
        low = loan_service(loan, low_rate, term, other_monthly)
        high = loan_service(loan, high_rate, term, other_monthly)
        self.add(
            derived_claim(
                ids.CALC_ANNUAL_DEBT_SERVICE,
                "Projected annual debt service (loan + other debt)",
                total,
                "USD",
                "usd0",
                "loan annual debt service + 12 x other debt payments",
                [self.get("CALC.loan_annual_debt_service"), *other_claims],
                value_range=(low, high),
            )
        )
        return total, low, high

    def fixed_costs(self) -> tuple[float, float, float]:
        """Monthly fixed costs at the most likely values, with the range of the inputs' ranges."""
        hours, wage = self.inputs["paid_hours_per_month"], self.inputs["hourly_wage"]
        burden, rent = self.inputs["payroll_burden_share"], self.inputs["monthly_rent"]
        value = monthly_fixed_cost(hours.mode, wage.mode, burden.mode, rent.mode)
        low = monthly_fixed_cost(hours.low, wage.low, burden.low, rent.low)
        high = monthly_fixed_cost(hours.high, wage.high, burden.high, rent.high)
        inputs = [
            self.input_claim(name)
            for name in ("paid_hours_per_month", "hourly_wage", "payroll_burden_share", "monthly_rent")
        ]
        self.add(
            derived_claim(
                ids.CALC_FIXED_COSTS,
                "Monthly fixed costs (staff and first-year rent)",
                value,
                "USD",
                "usd0",
                "paid hours x hourly wage x (1 + payroll burden) + monthly rent",
                inputs,
                value_range=(low, high),
            )
        )
        return value, low, high

    def reserve(self, cost: float, service: tuple[float, float, float], fixed: tuple[float, float, float]) -> None:
        """Required reserve (NSP-5.1) and the cash reserve at opening."""

        def required(fixed_cost: float, annual_service: float) -> float:
            months = to_decimal(float(RESERVE_MONTHS_REQUIRED))
            return cents(months * (to_decimal(fixed_cost) + to_decimal(annual_service) / 12))

        self.add(
            derived_claim(
                ids.CALC_REQUIRED_RESERVE,
                "Required working-capital reserve (NSP-5.1)",
                required(fixed[0], service[0]),
                "USD",
                "usd0",
                "3 x (monthly fixed costs + projected annual debt service / 12)",
                [
                    self.get(ids.CALC_FIXED_COSTS),
                    self.get(ids.CALC_ANNUAL_DEBT_SERVICE),
                    self.get(ids.PROGRAM_RESERVE_MONTHS),
                ],
                value_range=(required(fixed[1], service[1]), required(fixed[2], service[2])),
            )
        )
        capex = self.capex_claim()
        capex_low, capex_high = (
            (capex.value_range.low, capex.value_range.high) if capex.value_range else (capex.value, capex.value)
        )
        reserve = cents(to_decimal(cost) - to_decimal(float(capex.value)))
        self.add(
            derived_claim(
                ids.CALC_CASH_RESERVE,
                "Cash reserve at opening (project cost - build-out)",
                reserve,
                "USD",
                "usd0",
                "total project cost - build-out and equipment cost",
                [self.get(ids.CALC_PROJECT_COST), capex],
                value_range=(cents(cost - float(capex_high)), cents(cost - float(capex_low))),
            )
        )

    def coverage(self, other_monthly: float, other_claims: list[Claim]) -> None:
        """Projected DSCR and the stressed ratios on the year-2 median (NSP-4.1, NSP-4.2)."""
        year = self.result.yearly[PROJECTION_YEAR - 1]
        noi_claim = self.get(ids.year_id(PROJECTION_YEAR, "noi_p50"))
        revenue_claim = self.get(ids.year_id(PROJECTION_YEAR, "revenue_p50"))
        service_claim = self.get(ids.CALC_ANNUAL_DEBT_SERVICE)
        service = float(service_claim.value)
        self.ratio(
            ids.CALC_PROJECTED_DSCR,
            "Projected DSCR, year-2 median, program formula",
            year.noi_p50,
            service,
            "year-2 median NOI / projected annual debt service, two decimals half up",
            [noi_claim, service_claim],
        )
        stressed_noi = revenue_stressed_noi(year.noi_p50, year.revenue_p50)
        self.add(
            derived_claim(
                ids.CALC_REVENUE_STRESSED_NOI,
                "Year-2 median NOI after the revenue stress",
                stressed_noi,
                "USD",
                "usd0",
                "year-2 median NOI - 20% of year-2 median revenue",
                [noi_claim, revenue_claim, self.get(ids.PROGRAM_REVENUE_STRESS_SHARE)],
            )
        )
        self.ratio(
            ids.CALC_REVENUE_STRESSED_DSCR,
            "Revenue-stressed DSCR, year-2 median (NSP-4.2)",
            stressed_noi,
            service,
            "stressed NOI / projected annual debt service, two decimals half up",
            [self.get(ids.CALC_REVENUE_STRESSED_NOI), service_claim],
        )
        if self.case.loan.rate_type != "variable":
            return
        loan, term = self.case.loan.amount, self.case.loan.term_months
        rate = float(self.get(ids.RATE_NOTE).value)
        low_rate, high_rate = self.rate_bounds()
        stressed = loan_service(loan, rate + RATE_STRESS_ADD_PCT, term, other_monthly)
        self.add(
            derived_claim(
                ids.CALC_RATE_STRESS_DEBT_SERVICE,
                "Projected annual debt service at the stressed rate",
                stressed,
                "USD",
                "usd0",
                "debt service at the note rate + 2.00 points",
                [
                    self.get(ids.FOUNDER_LOAN),
                    self.get(ids.FOUNDER_TERM),
                    self.get(ids.RATE_NOTE),
                    self.get(ids.PROGRAM_RATE_STRESS_ADD),
                    *other_claims,
                ],
                value_range=(
                    loan_service(loan, low_rate + RATE_STRESS_ADD_PCT, term, other_monthly),
                    loan_service(loan, high_rate + RATE_STRESS_ADD_PCT, term, other_monthly),
                ),
            )
        )
        self.ratio(
            ids.CALC_RATE_STRESSED_DSCR,
            "Rate-stressed DSCR, year-2 median (NSP-4.2)",
            year.noi_p50,
            stressed,
            "year-2 median NOI / debt service at the stressed rate, two decimals half up",
            [noi_claim, self.get(ids.CALC_RATE_STRESS_DEBT_SERVICE)],
        )

    def ratio(
        self, claim_id: str, label: str, numerator: float, denominator: float, formula: str, inputs: list[Claim]
    ) -> None:
        """A program ratio (two decimals, half up); UNKNOWN when the denominator is not positive."""
        value = program_ratio(numerator, denominator)
        if value is None:
            self.add(
                unknown_claim(
                    claim_id,
                    label,
                    "RATIO",
                    "ratio2",
                    "the debt service is not positive",
                    kind="calculation",
                    ref=formula,
                )
            )
            return
        self.add(derived_claim(claim_id, label, value, "RATIO", "ratio2", formula, inputs))

    def all(self) -> list[Claim]:
        """Build every CALC.* claim; returns them in the order they were made."""
        before = set(self.claims)
        other_amount, other_monthly, amount_claims, payment_claims = self.other_debt()
        cost = self.sources(other_amount, amount_claims)
        service = self.debt_service(other_monthly, payment_claims)
        fixed = self.fixed_costs()
        self.reserve(cost, service, fixed)
        self.coverage(other_monthly, payment_claims)
        return [claim for claim_id, claim in self.claims.items() if claim_id not in before]

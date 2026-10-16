"""Unit tests for the SYNTHETIC generator's back-solved financials and claim allocation."""

import random
from dataclasses import dataclass
from datetime import date

import pytest

from uwcopilot.agent.credit_calculations import coverage_ratio, proposed_annual_debt_service
from uwcopilot.docintel.synthetic.credit_finance import MAX_NOI_SHARE_OF_REVENUE, solve_amount_and_noi
from uwcopilot.docintel.synthetic.insurance_build import ClaimNumbers, experience_claims


@dataclass
class Request:
    """A loan request (the fields solve_amount_and_noi reads)."""

    product: str = "term_loan"
    amount: float = 300_000.0
    term_months: int = 60
    rate_pct: float = 10.0
    rate_type: str = "fixed"


@pytest.mark.parametrize("rate_type", ["fixed", "variable"])
def test_stress_safe_case_passes_both_stress_tests(rate_type: str) -> None:
    request = Request(rate_type=rate_type)
    verified, existing = 2_000_000.0, 24_000.0
    amount, noi = solve_amount_and_noi(request, existing, verified, dscr=1.8, stress_safe=True)
    service = existing + proposed_annual_debt_service("term_loan", amount, 10.0, 60)
    assert coverage_ratio(noi, service) >= 1.8
    assert coverage_ratio(round(noi - 0.20 * verified, 2), service) >= 1.00
    if rate_type == "variable":
        stressed = existing + proposed_annual_debt_service("term_loan", amount, 12.0, 60)
        assert coverage_ratio(noi, stressed) >= 1.10


def test_target_dscr_kept_and_amount_reduced_when_noi_cap_binds() -> None:
    verified, existing = 400_000.0, 12_000.0
    amount, noi = solve_amount_and_noi(Request(amount=900_000.0), existing, verified, dscr=1.25, stress_safe=False)
    assert amount < 900_000.0 and amount % 1_000 == 0
    assert noi <= MAX_NOI_SHARE_OF_REVENUE * verified + 0.01
    service = existing + proposed_annual_debt_service("term_loan", amount, 10.0, 60)
    assert coverage_ratio(noi, service) == pytest.approx(1.25, abs=0.01)


def test_claims_land_in_distinct_years_and_keep_annual_ratios_moderate() -> None:
    starts = [date(2020 + index, 11, 1) for index in range(5)]
    premiums = [10_000.0, 10_200.0, 10_400.0, 10_600.0, 10_800.0]
    worst = 0.0
    for seed in range(200):
        rng = random.Random(seed)
        claims = experience_claims(rng, "property", starts, premiums, ClaimNumbers(rng), ["QFM"] * 5)
        years = [index for index, _ in claims]
        assert len(years) == len(set(years)) <= 5
        for index, claim in claims:
            assert claim.paid + claim.reserve == pytest.approx(claim.incurred, abs=0.01)
            worst = max(worst, claim.incurred / premiums[index])
    assert worst < 1.5

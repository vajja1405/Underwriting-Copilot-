"""Inputs and setup: where every sampled input comes from, and the plan rules."""

from dataclasses import dataclass

import pytest
from feasibility_helpers import basic_loan, basic_plan, car_wash_site, pinned, setup_for, synthetic_inputs
from pydantic import ValidationError

from uwcopilot.risk.scenario import OverrideError
from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.feasibility import Loan, SiteDemand, as_site_demand, load_assumptions
from uwcopilot.studio.profiles import load_profile


def test_site_demand_rules() -> None:
    with pytest.raises(ValidationError):
        SiteDemand(low=5, mode=3, high=9, unit="cars/day")
    with pytest.raises(ValidationError):
        SiteDemand(low=0, mode=0, high=0, unit="cars/day")
    assert SiteDemand(low=1, mode=2, high=3, unit="cars/day").status == "MODEL_ESTIMATE"


def test_loan_needs_two_years_of_payments() -> None:
    with pytest.raises(ValidationError):
        Loan(amount=100_000, term_months=12)


@dataclass
class EstimateLike:
    """Stands in for SiteIQ's DemandEstimate: only the attributes the simulator reads."""

    low: float | None
    mode: float | None
    high: float | None
    unit: str
    basis: str = "data x ASSUMPTION rates"


@dataclass
class FactorLike:
    """Stands in for one SiteIQ FactorScore (percentile rank 0-100 of the raw value within the metro)."""

    factor: str
    percentile: float | None


@dataclass
class ScoreLike:
    """Stands in for SiteIQ's SiteScore."""

    demand: EstimateLike
    coverage_score: float
    factors: list[FactorLike]


def test_as_site_demand_reads_siteiq_shaped_results() -> None:
    score = ScoreLike(
        EstimateLike(low=100, mode=200, high=300, unit="cars/day"),
        coverage_score=0.75,
        factors=[FactorLike("traffic_aadt", 40.0), FactorLike("competition", 61.0)],
    )
    demand = as_site_demand(score)
    assert (demand.low, demand.mode, demand.high) == (100, 200, 300)
    assert demand.coverage_score == 0.75 and demand.competition_percentile == pytest.approx(0.61)
    assert demand.status == "MODEL_ESTIMATE" and "data x ASSUMPTION rates" in demand.source
    rates_only = as_site_demand(EstimateLike(low=1, mode=2, high=3, unit="cars/day", basis="ASSUMPTION rates only"))
    assert rates_only.status == "ASSUMPTION"
    with pytest.raises(ValueError, match="UNKNOWN"):
        as_site_demand(ScoreLike(EstimateLike(None, None, None, "cars/day"), coverage_score=0.0, factors=[]))


def test_as_site_demand_accepts_dicts_and_site_demands() -> None:
    assert as_site_demand({"low": 1, "mode": 2, "high": 3, "unit": "covers/day"}).unit == "covers/day"
    site = car_wash_site()
    assert as_site_demand(site) is site
    with pytest.raises(TypeError):
        as_site_demand(42)


def test_inputs_come_from_profile_metro_or_founder() -> None:
    setup = setup_for(plan=basic_plan(price=15.0, monthly_rent=8000.0))
    items = setup.distributions
    assert items["price"].status == "USER_STATED" and items["price"].kind == "fixed"
    assert items["monthly_rent"].mode == 8000.0
    # The profile's ASSUMPTION wage range scaled by the SYNTHETIC city's index stays an ASSUMPTION (rule D).
    assert items["hourly_wage"].status == "ASSUMPTION" and "wage index" in items["hourly_wage"].source
    assert items["capacity"].status == "ASSUMPTION"
    assert items["mature_demand"].status == "SYNTHETIC" and items["mature_demand"].mode == 250.0
    assert {f"cost_ratio_{name}" for name in load_profile("car_wash").cost_ratios} <= set(items)
    assert setup.reference_price == load_profile("car_wash").price.point()
    assert setup.planned_starting_cash == pytest.approx(700_000 + 1_300_000 - 1_800_000)


def test_rate_defaults_to_prime_plus_the_assumed_spread() -> None:
    profile = load_profile("car_wash")
    prime = synthetic_inputs(profile)["prime_rate"].value
    setup = setup_for()
    spread = load_assumptions().rate_spread_over_prime_pct.point()
    assert setup.loan.annual_rate_pct == pytest.approx(prime + spread)
    assert setup.loan.rate_status == "SYNTHETIC" and "NOT a published rate" in setup.loan.rate_source
    quoted = setup_for(loan=basic_loan(annual_rate_pct=8.5))
    assert quoted.loan.annual_rate_pct == 8.5 and quoted.loan.rate_status == "USER_STATED"


def test_unknown_metro_values_fall_back_to_the_profile_with_a_note() -> None:
    profile = load_profile("car_wash")
    inputs = dict(synthetic_inputs(profile))
    inputs["local_wages"] = FactorValue(factor="local_wages", unit="USD/hour", status="UNKNOWN", reason="no QCEW wage")
    setup = setup_for(metro_inputs=inputs)
    assert setup.distributions["hourly_wage"].status == "ASSUMPTION"
    assert any("hourly_wage: metro value UNKNOWN (no QCEW wage)" in note for note in setup.notes)


def test_missing_prime_rate_needs_a_quoted_rate() -> None:
    profile = load_profile("car_wash")
    inputs = dict(synthetic_inputs(profile))
    inputs["prime_rate"] = FactorValue(factor="prime_rate", unit="percent", status="UNKNOWN", reason="no snapshot")
    with pytest.raises(ValueError, match="annual_rate_pct"):
        setup_for(metro_inputs=inputs)
    assert setup_for(metro_inputs=inputs, loan=basic_loan(annual_rate_pct=9.0)).loan.annual_rate_pct == 9.0


def test_fixed_rate_loans_have_no_rate_shock() -> None:
    setup = setup_for(loan=basic_loan(rate_type="fixed"))
    assert "rate_shock_annual_probability" not in setup.distributions
    assert "rate shocks: not applicable to a fixed-rate loan" in setup.notes
    assert "rate_shock_annual_probability" in setup_for().distributions


def test_competition_percentile_moves_the_entry_probability_mode() -> None:
    entry = load_assumptions().competitor_entry_annual_probability
    open_area = setup_for(site=car_wash_site(competition_percentile=0.0)).distributions
    crowded = setup_for(site=car_wash_site(competition_percentile=1.0)).distributions
    assert open_area["competitor_entry_annual_probability"].mode == pytest.approx(entry.high)
    assert crowded["competitor_entry_annual_probability"].mode == pytest.approx(entry.low)
    unknown = setup_for(site=car_wash_site(competition_percentile=None))
    assert unknown.distributions["competitor_entry_annual_probability"].mode == pytest.approx(entry.mode)
    assert any("competition percentile UNKNOWN" in note for note in unknown.notes)


def test_user_edits_replace_ranges_and_unknown_names_are_rejected() -> None:
    edited = setup_for(plan=basic_plan(assumption_overrides={"price_elasticity": pinned(0.5)}))
    assert edited.distributions["price_elasticity"].status == "USER_STATED"
    with pytest.raises(OverrideError, match="unknown assumption"):
        setup_for(plan=basic_plan(assumption_overrides={"made_up": pinned(1.0)}))
    with pytest.raises(OverrideError, match="cannot exceed 1"):
        setup_for(plan=basic_plan(assumption_overrides={"one_off_shock_annual_probability": pinned(1.5)}))
    with pytest.raises(OverrideError, match="negative"):
        setup_for(plan=basic_plan(assumption_overrides={"monthly_noise_sd": pinned(-0.1)}))


def test_plan_consistency_rules() -> None:
    with pytest.raises(ValueError, match="do not cover the planned capex"):
        setup_for(plan=basic_plan(owner_equity=100_000.0))
    with pytest.raises(ValueError, match="cars/day"):
        setup_for(site=SiteDemand(low=1, mode=2, high=3, unit="covers/day"))
    with pytest.raises(ValueError, match="at least 24 months"):
        setup_for(months=18)
    default_capex = setup_for(plan=basic_plan(capex=None, owner_equity=3_000_000.0))
    expected = sum(item.cost.point() for item in load_profile("car_wash").capex)
    assert default_capex.planned_capex == pytest.approx(expected) and default_capex.capex_status == "ASSUMPTION"


def test_monthly_unit_profiles_have_no_open_days() -> None:
    site = SiteDemand(low=10, mode=15, high=20, unit="booked nights/month", status="SYNTHETIC")
    plan = basic_plan(capex=30_000.0, owner_equity=20_000.0)
    setup = setup_for("short_term_rental", site=site, plan=plan, loan=basic_loan(amount=20_000.0, term_months=60))
    assert not setup.per_day and "days_open_per_week" not in setup.distributions

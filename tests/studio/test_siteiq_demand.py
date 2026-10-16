"""The demand estimate: formula terms match the profile vocabulary, hand-worked examples, UNKNOWN never filled."""

import pytest

from uwcopilot.studio.profiles import load_all_profiles, load_profile
from uwcopilot.studio.profiles.factors import DEMAND_FORMULAS
from uwcopilot.studio.siteiq.demand import CONTEXT_FACTORS, FORMULA_TERMS, estimate_demand
from uwcopilot.studio.siteiq.factors import RawFactor

PROFILES = load_all_profiles()


def data(factor: str, value: float | None, status: str = "SYNTHETIC") -> RawFactor:
    reason = "hand-worked test value" if value is not None else "no value in this test"
    return RawFactor(factor, value, status if value is not None else "UNKNOWN", "within 1 mile", reason)


def test_every_formula_has_terms_matching_its_inputs_and_data() -> None:
    assert set(FORMULA_TERMS) == set(DEMAND_FORMULAS)
    for name, formula in DEMAND_FORMULAS.items():
        terms = FORMULA_TERMS[name]
        rates = {rate for term in terms for rate in term.rates}
        assert rates == set(formula.required_inputs), name
        used = {term.data_factor for term in terms if term.data_factor} | set(CONTEXT_FACTORS.get(name, ()))
        assert used == set(formula.data_factors), name


def test_car_wash_hand_worked_example() -> None:
    profile = load_profile("car_wash")
    rate = profile.demand.inputs["capture_rate"]
    result = estimate_demand(profile, {"traffic_aadt": data("traffic_aadt", 20000.0)}, "nearest road")
    assert result.low == pytest.approx(20000 * rate.low)
    assert result.mode == pytest.approx(20000 * rate.mode)
    assert result.high == pytest.approx(20000 * rate.high)
    assert result.complete and result.basis == "data x ASSUMPTION rates" and result.unit == "cars/day"


def test_restaurant_hand_worked_example() -> None:
    profile = load_profile("restaurant_cafe")
    rates = {name: quantity.point() for name, quantity in profile.demand.inputs.items()}
    measured = {
        "resident_population": data("resident_population", 9000.0),
        "daytime_workers": data("daytime_workers", 3000.0),
    }
    result = estimate_demand(profile, measured, "within 1 mile")
    expected = (
        9000 * rates["visits_per_resident_per_month"] / 30 * rates["capture_share"]
        + 3000 * rates["visits_per_worker_per_month"] / 30 * rates["capture_share"]
    )
    assert result.mode == pytest.approx(round(expected, 3))


def test_a_missing_term_is_left_out_not_filled() -> None:
    profile = load_profile("restaurant_cafe")
    measured = {
        "resident_population": data("resident_population", 9000.0),
        "daytime_workers": data("daytime_workers", None),
    }
    result = estimate_demand(profile, measured, "within 1 mile")
    assert not result.complete and result.missing and result.missing[0].startswith("daytime_workers")
    only_residents = estimate_demand(
        profile,
        {"resident_population": data("resident_population", 9000.0), "daytime_workers": data("daytime_workers", 0.0)},
        "x",
    )
    assert result.mode == pytest.approx(only_residents.mode)
    car_wash = estimate_demand(load_profile("car_wash"), {"traffic_aadt": data("traffic_aadt", None)}, "x")
    assert car_wash.basis == "UNKNOWN" and car_wash.mode is None and car_wash.low is None


def test_assumption_only_formulas_and_capacity_note() -> None:
    motel = load_profile("motel")
    result = estimate_demand(motel, {}, "within 3 miles")
    rooms, occupancy = motel.demand.inputs["rooms"], motel.demand.inputs["occupancy"]
    assert result.basis == "ASSUMPTION rates only"
    assert result.mode == pytest.approx(rooms.mode * occupancy.mode)
    assert all(item.role == "rate" for item in result.inputs)
    huge = estimate_demand(load_profile("car_wash"), {"traffic_aadt": data("traffic_aadt", 1e7)}, "x")
    assert huge.capacity_note and "capacity" in huge.capacity_note


def test_rates_carry_their_profile_path_and_status() -> None:
    for profile in PROFILES.values():
        result = estimate_demand(profile, {}, "x")
        rates = [item for item in result.inputs if item.role == "rate"]
        assert {item.name for item in rates} == set(profile.demand.inputs)
        for item in rates:
            assert item.profile_field == f"demand.inputs.{item.name}" and item.status == "ASSUMPTION"

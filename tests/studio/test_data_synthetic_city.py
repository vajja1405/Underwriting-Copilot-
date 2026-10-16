"""The SYNTHETIC city: deterministic, labeled, plausible, and its hidden truth is not SiteIQ's formula."""

import numpy as np
import pytest

from uwcopilot.studio.data.synthetic_city import TRUE_DEMAND_NOTES, generate_city
from uwcopilot.studio.profiles import load_all_profiles

PROFILES = load_all_profiles()


@pytest.fixture(scope="module")
def city():
    return generate_city("synthetic_metro", 7, 120, 10.0, -140.0, 20, PROFILES)


def test_same_seed_same_city_and_other_seed_other_city(city) -> None:
    again = generate_city("synthetic_metro", 7, 120, 10.0, -140.0, 20, PROFILES)
    assert again.tracts == city.tracts and again.true_demand == city.true_demand
    other = generate_city("synthetic_metro", 8, 120, 10.0, -140.0, 20, PROFILES)
    assert other.tracts != city.tracts


def test_everything_is_labeled_synthetic(city) -> None:
    assert city.data_label == "SYNTHETIC"
    assert all(tract.geoid.startswith("SYN") for tract in city.tracts)
    assert all(poi.tags.get("name", "SYNTHETIC").startswith("SYNTHETIC") for poi in city.pois)
    assert all(road.route_name.startswith("SYN") for road in city.roads)


def test_city_sits_in_open_ocean_near_its_origin(city) -> None:
    lats = np.array([tract.lat for tract in city.tracts])
    lons = np.array([tract.lon for tract in city.tracts])
    assert np.all(np.abs(lats - 10.0) < 0.2) and np.all(np.abs(lons + 140.0) < 0.2)


def test_tract_values_are_plausible(city) -> None:
    for tract in city.tracts:
        assert 1000 <= tract.resident_population <= 9000
        assert 0 < tract.renter_households <= tract.households < tract.resident_population
        assert tract.daytime_workers > 0 and tract.land_area_sq_miles > 0
    suppressed = sum(tract.median_household_income is None for tract in city.tracts)
    assert 0 < suppressed < len(city.tracts) * 0.15


def test_every_profile_tag_has_places(city) -> None:
    present = {f"{key}={value}" for poi in city.pois for key, value in poi.tags.items()} | {
        key for poi in city.pois for key in poi.tags
    }
    for profile in PROFILES.values():
        for tag in profile.competitor_tags + profile.anchor_tags:
            assert tag in present, (profile.id, tag)


def test_true_demand_exists_for_each_v1_type_and_is_positive(city) -> None:
    assert set(city.true_demand) == set(PROFILES) == set(TRUE_DEMAND_NOTES)
    for values in city.true_demand.values():
        array = np.array(list(values.values()))
        assert len(array) == len(city.tracts) and np.all(array > 0)
        assert array.std() > 0


def test_truth_is_not_a_linear_function_of_published_features(city) -> None:
    """A linear fit of log truth on the published tract features leaves a large share unexplained (noise, latent
    appeal, gravity decay and competition saturation are not in the features)."""
    rows = [t for t in city.tracts if t.median_household_income is not None]
    features = np.array(
        [
            [
                t.resident_population,
                t.households,
                t.renter_households,
                t.median_household_income,
                t.daytime_workers,
                1.0,
            ]
            for t in rows
        ]
    )
    for profile_id in ("restaurant_cafe", "laundromat"):
        target = np.log([city.true_demand[profile_id][t.geoid] for t in rows])
        coefficients, *_ = np.linalg.lstsq(features, target, rcond=None)
        residual = target - features @ coefficients
        r_squared = 1 - residual.var() / target.var()
        assert r_squared < 0.9, (profile_id, r_squared)


def test_metro_assumptions_are_within_their_generator_ranges(city) -> None:
    assert 0.9 <= city.wage_index <= 1.2 and 0.85 <= city.rent_index <= 1.3
    assert 5.0 <= city.prime_rate_pct <= 9.0

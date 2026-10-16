"""score_site on a small SYNTHETIC city: factors, normalization, overall score, coverage, notes and labels."""

import numpy as np
import pytest
from studio_helpers import FakeNetwork, fast_policies, make_client

from uwcopilot.studio import DISCLAIMER
from uwcopilot.studio.data import keys
from uwcopilot.studio.data.fabric import required_factors
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.profiles import load_all_profiles
from uwcopilot.studio.siteiq import MetroContext, SiteInputs, SiteOutsideMetro, score_site
from uwcopilot.studio.siteiq.factors import area_mask, measure_point
from uwcopilot.studio.siteiq.isochrones import ISOCHRONES_URL
from uwcopilot.studio.siteiq.reference import MIN_REFERENCE_VALUES, percentile_ranks, reference_for
from uwcopilot.studio.siteiq.scoring import MIN_SCORED_WEIGHT, overall_score
from uwcopilot.studio.siteiq.trade_area import ring_area, scaled_rings
from uwcopilot.studio.siteiq.validation import synthetic_context

PROFILES = load_all_profiles()
SEED = 7


@pytest.fixture(scope="module")
def small():
    ctx, city = synthetic_context(load_metro("synthetic_metro"), SEED, PROFILES, n_tracts=60)
    return ctx, city


def site_of(ctx, index: int = 0) -> tuple[float, float]:
    return float(ctx.tracts.lats[index]), float(ctx.tracts.lons[index])


def test_percentile_ranks_hand_example() -> None:
    known = np.sort(np.array([1.0, 2.0, 2.0, 4.0]))
    ranks = percentile_ranks(known, np.array([0.0, 2.0, 5.0, np.nan]))
    assert ranks[0] == 0.0 and ranks[1] == pytest.approx(50.0) and ranks[2] == 100.0 and np.isnan(ranks[3])


@pytest.mark.parametrize("profile_id", sorted(PROFILES))
def test_every_weighted_factor_is_scored_or_explained(small, profile_id) -> None:
    ctx, _ = small
    result = score_site(profile_id, "synthetic_metro", *site_of(ctx, 3), context=ctx)
    profile = PROFILES[profile_id]
    assert [item.factor for item in result.factors] == list(profile.design.location_weights)
    for item in result.factors:
        assert item.weight == profile.design.location_weights[item.factor].weight
        assert item.reason and item.normalization and item.status in ("SYNTHETIC", "UNKNOWN", "ASSUMPTION")
        if item.status == "SYNTHETIC" and item.factor != "commercial_rent":
            assert item.sources and item.sources[0].data_label == "SYNTHETIC"
    assert result.data_label == "SYNTHETIC" and result.mode == "synthetic"
    assert result.disclaimer == DISCLAIMER and any("SYNTHETIC metro" in note for note in result.notes)
    assert 0 <= result.coverage_score <= 1


def test_overall_is_the_weighted_mean_of_used_factors(small) -> None:
    ctx, _ = small
    result = score_site("restaurant_cafe", "synthetic_metro", *site_of(ctx, 5), context=ctx)
    used = [item for item in result.factors if item.used_in_overall]
    assert used and result.scored_weight_share >= MIN_SCORED_WEIGHT
    expected = sum(item.weight * item.normalized_score for item in used) / sum(item.weight for item in used)
    assert result.overall_score == pytest.approx(round(expected, 1))
    assert overall_score([]) is None


def test_lower_is_better_scores_100_minus_percentile(small) -> None:
    ctx, _ = small
    result = score_site("car_wash", "synthetic_metro", *site_of(ctx, 2), context=ctx)
    competition = next(item for item in result.factors if item.factor == "competition")
    assert competition.direction == "lower_is_better"
    assert competition.normalized_score == pytest.approx(100 - competition.percentile, abs=0.01)
    assert "per 10,000 residents" in competition.reason or competition.details["per_10k_residents_market"] is None


def test_rent_without_a_quote_is_not_scored_and_a_quote_is(small) -> None:
    ctx, _ = small
    site = site_of(ctx, 4)
    plain = score_site("laundromat", "synthetic_metro", *site, context=ctx)
    rent = next(item for item in plain.factors if item.factor == "commercial_rent")
    assert rent.normalized_score is None and not rent.used_in_overall and "rent quote" in rent.normalization
    low = rent.details["metro_low"]
    quoted = score_site("laundromat", "synthetic_metro", *site, SiteInputs(rent_quote_usd_per_month=low), context=ctx)
    rent = next(item for item in quoted.factors if item.factor == "commercial_rent")
    assert rent.status == "FOUNDER_INPUT" and rent.normalized_score == 100.0 and rent.used_in_overall
    assert "not market data" in rent.normalization


def test_tract_sums_match_a_hand_sum_and_missing_is_never_zero(small) -> None:
    shared, _ = small
    ctx = MetroContext(metro=shared.metro, fabric=shared.fabric)  # own arrays, so the shared fixture is untouched
    profile = PROFILES["laundromat"]
    lat, lon = site_of(ctx, 0)
    rings = scaled_rings(profile, ctx.metro)
    measured = measure_point(ctx, profile, lat, lon, rings)
    mask = area_mask(ctx, ring_area(lat, lon, rings[len(rings) // 2]), ctx.own_tract(lat, lon))
    assert measured["renter_households"].value == pytest.approx(ctx.tracts.values["renter_households"][mask].sum())
    ctx.tracts.values["renter_households"][0] = np.nan  # the site's own tract loses its value
    again = measure_point(ctx, profile, lat, lon, rings)
    assert again["renter_households"].value is None and again["renter_households"].status == "UNKNOWN"
    assert "sum is UNKNOWN" in again["renter_households"].reason


def test_reference_covers_every_tract_and_needs_enough_values(small) -> None:
    ctx, _ = small
    profile = PROFILES["car_wash"]
    reference = reference_for(ctx, profile, scaled_rings(profile, ctx.metro))
    assert reference.geoids == ctx.tracts.ids and len(ctx.tracts) >= MIN_REFERENCE_VALUES
    assert reference.size("traffic_aadt") == len(ctx.tracts)
    assert reference.percentile("missing_factor", 1.0) is None
    assert reference is reference_for(ctx, profile, scaled_rings(profile, ctx.metro))  # cached


def test_a_value_without_enough_reference_values_is_shown_but_not_scored() -> None:
    tiny, _ = synthetic_context(load_metro("synthetic_metro"), SEED, PROFILES, n_tracts=12)
    result = score_site("laundromat", "synthetic_metro", *site_of(tiny, 0), context=tiny)
    renters = next(item for item in result.factors if item.factor == "renter_households")
    assert renters.raw_value is not None and renters.normalized_score is None and not renters.used_in_overall
    assert f"needs at least {MIN_REFERENCE_VALUES}" in renters.normalization
    assert "Not scored: only 12 metro locations" in renters.reason and "does not vary" not in renters.reason
    assert any(note.startswith("renter_households is SYNTHETIC but not scored: only 12") for note in result.notes)
    assert result.overall_score is None


def test_coverage_counts_only_data_backed_factors(small) -> None:
    ctx, _ = small
    result = score_site("retail_storefront", "synthetic_metro", *site_of(ctx, 6), context=ctx)
    needed = required_factors(PROFILES["retail_storefront"])
    not_data = [item for item in result.factors if item.status not in ("SYNTHETIC", "DATA", "FOUNDER_INPUT")]
    # Without a rent quote, rent is the profile's ASSUMPTION range: it never counts as covered.
    assert any(item.factor == "commercial_rent" and item.status == "ASSUMPTION" for item in not_data)
    assert result.coverage_score <= 1.0 - sum(item.weight for item in not_data) + 1e-9
    assert set(needed) >= {item.factor for item in result.factors}


def test_demand_estimate_is_traceable(small) -> None:
    ctx, _ = small
    result = score_site("car_wash", "synthetic_metro", *site_of(ctx, 1), context=ctx)
    demand = result.demand
    assert demand.unit == "cars/day" and demand.basis == "data x ASSUMPTION rates"
    aadt = next(item for item in demand.inputs if item.name == "traffic_aadt")
    rate = next(item for item in demand.inputs if item.name == "capture_rate")
    assert rate.profile_field == "demand.inputs.capture_rate" and rate.status == "ASSUMPTION"
    assert demand.mode == pytest.approx(round(aadt.value * rate.value, 3))
    assert demand.low <= demand.mode <= demand.high


def test_site_outside_the_metro_is_refused(small) -> None:
    ctx, _ = small
    with pytest.raises(SiteOutsideMetro):
        score_site("car_wash", "synthetic_metro", 0.0, 0.0, context=ctx)
    with pytest.raises(ValueError):
        score_site("car_wash", "houston_tx", *site_of(ctx), context=ctx)


def test_scores_are_deterministic(small) -> None:
    ctx, _ = small
    first = score_site("motel", "synthetic_metro", *site_of(ctx, 8), context=ctx)
    second = score_site("motel", "synthetic_metro", *site_of(ctx, 8), context=ctx)
    assert first.model_dump() == second.model_dump()
    assert first.demand.basis == "ASSUMPTION rates only"


def test_drive_time_area_feeds_the_demand_estimate_only(small, tmp_path, monkeypatch) -> None:
    ctx, _ = small
    monkeypatch.setattr(keys, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setenv("ORS_API_KEY", "test-ors-key-123")
    network = FakeNetwork()
    network.add_fixture(ISOCHRONES_URL, "ors/isochrones_handwritten.json")
    client, _ = make_client(tmp_path, network, policies=fast_policies("api.openrouteservice.org"))
    rings_only = score_site("laundromat", "synthetic_metro", 10.0, -140.0, context=ctx)
    driving = score_site("laundromat", "synthetic_metro", 10.0, -140.0, context=ctx, live=client)
    assert driving.trade_area.demand_method == "drive_time" and driving.demand.area == "within a 10-minute drive"
    assert [item.model_dump() for item in driving.factors] == [item.model_dump() for item in rings_only.factors]
    renters = next(item for item in driving.demand.inputs if item.name == "renter_households")
    assert "10-minute drive" in renters.reason
    assert "test-ors-key-123" not in driving.model_dump_json()

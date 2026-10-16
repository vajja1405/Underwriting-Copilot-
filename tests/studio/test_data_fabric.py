"""The data fabric: one interface over the SYNTHETIC city and real-metro snapshots, with UNKNOWN never filled."""

from datetime import date

import pytest

from uwcopilot.studio.data.fabric import (
    TRACT_FACTORS,
    FactorCoverage,
    SnapshotFabric,
    SyntheticFabric,
    coverage_share,
    get_fabric,
    latest_snapshot,
    required_factors,
)
from uwcopilot.studio.data.snapshot import SnapshotBuild, write_snapshot
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.profiles import load_all_profiles, load_profile

PROFILES = load_all_profiles()


@pytest.fixture(scope="module")
def fabric() -> SyntheticFabric:
    built = get_fabric("synthetic_metro")
    assert isinstance(built, SyntheticFabric)
    return built


def test_synthetic_mode_and_tracts(fabric) -> None:
    assert fabric.mode()[0] == "synthetic" and fabric.data_label == "SYNTHETIC"
    ids = fabric.tract_ids()
    assert len(ids) == load_metro("synthetic_metro").synthetic.n_tracts
    tract = fabric.tract(ids[0])
    assert tract.data_label == "SYNTHETIC" and fabric.tract_at(tract.lat, tract.lon) == ids[0]
    assert fabric.tract_at(0.0, 0.0) is None  # far outside the metro


def test_tract_features_carry_status_reason_and_provenance(fabric) -> None:
    features = fabric.tract_features(fabric.tract_ids()[3])
    assert set(features) == set(TRACT_FACTORS)
    for value in features.values():
        assert value.status in ("SYNTHETIC", "UNKNOWN") and value.reason
        if value.status == "SYNTHETIC":
            assert value.source.data_label == "SYNTHETIC" and value.source.origin == "synthetic"


def test_suppressed_income_is_unknown_not_filled(fabric) -> None:
    table = fabric.tract_table()
    suppressed = [geoid for geoid, values in table.items() if values["median_household_income"] is None]
    assert suppressed
    value = fabric.tract_features(suppressed[0])["median_household_income"]
    assert value.status == "UNKNOWN" and value.value is None and not value.known


def test_hidden_truth_is_not_reachable_through_the_fabric(fabric) -> None:
    for name in dir(fabric):
        if not name.startswith("_") and name != "city":
            assert "true" not in name.lower(), name
    assert all("true" not in key for key in fabric.tract_table()[fabric.tract_ids()[0]])


def test_sites_near_filters_by_tag_and_radius(fabric) -> None:
    tract = fabric.tract(fabric.tract_ids()[0])
    near = fabric.sites_near(tract.lat, tract.lon, 2.0, ["amenity=restaurant"])
    wider = fabric.sites_near(tract.lat, tract.lon, 5.0, ["amenity=restaurant"])
    assert near.status == "SYNTHETIC" and len(near.pois) <= len(wider.pois)
    assert all(poi.tags["amenity"] == "restaurant" and poi.distance_miles <= 2.0 for poi in near.pois)
    distances = [poi.distance_miles for poi in wider.pois]
    assert distances == sorted(distances)


def test_nearest_road_and_comparables(fabric) -> None:
    tract = fabric.tract(fabric.tract_ids()[0])
    road = fabric.nearest_road(tract.lat, tract.lon)
    assert road.status == "SYNTHETIC" and road.segment.aadt > 0 and road.distance_miles <= 0.5
    assert fabric.nearest_road(0.0, 0.0).status == "UNKNOWN"
    listings = fabric.str_comparables(tract.lat, tract.lon, 3.0)
    assert listings.status == "SYNTHETIC" and all(item.distance_miles <= 3.0 for item in listings.listings)


def test_metro_inputs_are_labeled(fabric) -> None:
    profile = PROFILES["laundromat"]
    inputs = fabric.metro_inputs(profile)
    assert set(inputs) == {"commercial_rent", "local_wages", "prime_rate"}
    rent = inputs["commercial_rent"]
    assert rent.low <= rent.value <= rent.high and rent.assumptions
    assert "NOT a published rate" in inputs["prime_rate"].reason


def test_synthetic_rent_and_wages_stay_assumptions(fabric) -> None:
    """An ASSUMPTION range scaled by a SYNTHETIC index is still an ASSUMPTION (rule D), never SYNTHETIC data."""
    inputs = fabric.metro_inputs(PROFILES["car_wash"])
    assert inputs["commercial_rent"].status == "ASSUMPTION" and "rent index" in inputs["commercial_rent"].reason
    assert inputs["local_wages"].status == "ASSUMPTION" and "wage index" in inputs["local_wages"].reason
    assert inputs["prime_rate"].status == "SYNTHETIC"


def test_coverage_rows_cover_every_required_factor(fabric) -> None:
    for profile in PROFILES.values():
        rows = fabric.coverage(profile)
        assert {row.factor for row in rows} == set(required_factors(profile))
        for row in rows:
            if row.factor == "commercial_rent":  # the profile's ASSUMPTION range: not data, counts 0
                assert row.status == "ASSUMPTION" and row.share_of_tracts == 0.0
            else:
                assert row.status == "SYNTHETIC"
        rent_weight = sum(row.weight for row in rows if row.factor == "commercial_rent")
        assert coverage_share(rows) <= 1.0 - rent_weight + 1e-9
        assert coverage_share(rows) > 0.85


def test_coverage_share_counts_assumption_and_unknown_as_zero() -> None:
    def row(factor, weight, status, share=1.0):
        return FactorCoverage(
            factor=factor,
            scope="tract",
            weight=weight,
            status=status,
            share_of_tracts=share,
            source=None,
            freshness=None,
            reason="r",
        )

    assert coverage_share([row("a", 0.5, "DATA"), row("b", 0.5, "ASSUMPTION")]) == 0.5
    assert coverage_share([row("a", 0.5, "DATA", 0.5), row("b", 0.5, "UNKNOWN")]) == 0.25
    assert coverage_share([row("a", 1.0, "DATA"), row("b", 0.0, "UNKNOWN")]) == 0.5  # demand-only input counts
    assert coverage_share([]) == 0.0


def test_real_metro_without_snapshot_is_limited_and_unknown(tmp_path) -> None:
    metro = load_metro("houston_tx")
    fabric = SnapshotFabric(metro=metro, snapshot=latest_snapshot("houston_tx", tmp_path))
    assert fabric.mode(PROFILES) == ("limited", "no data snapshot")
    assert fabric.tract_ids() == []
    rows = fabric.coverage(PROFILES["car_wash"])
    assert all(row.status == "UNKNOWN" and "no data snapshot" in row.reason for row in rows)
    inputs = fabric.metro_inputs(PROFILES["car_wash"])
    assert inputs["commercial_rent"].status == "ASSUMPTION" and inputs["prime_rate"].status == "UNKNOWN"
    assert fabric.nearest_road(29.76, -95.37).status == "UNKNOWN"  # live lookups are off
    assert fabric.sites_near(29.76, -95.37, 1, ["amenity=car_wash"]).status == "UNKNOWN"


def test_real_metro_reads_its_snapshot(tmp_path) -> None:
    metro = load_metro("houston_tx")
    build = SnapshotBuild(metro=metro, snapshot_date=date(2026, 10, 12))
    build.tracts = {
        "48201000100": {
            "lat": 29.7,
            "lon": -95.3,
            "land_area_sq_miles": 1.0,
            "resident_population": 4000,
            "households": 1500,
            "renter_households": 700,
            "median_household_income": None,
        }
    }
    build.layers["roads"] = {"counties_with_aadt": 10, "counties_total": 10}
    write_snapshot(build, tmp_path)
    fabric = SnapshotFabric(metro=metro, snapshot=latest_snapshot("houston_tx", tmp_path))
    features = fabric.tract_features("48201000100")
    assert features["resident_population"].status == "DATA" and features["resident_population"].value == 4000
    assert features["median_household_income"].status == "UNKNOWN"
    assert features["daytime_workers"].status == "UNKNOWN"
    rows = {row.factor: row for row in fabric.coverage(PROFILES["car_wash"])}
    # Traffic is only available live for one site: no tract has a value, so it is UNKNOWN for ranking and counts 0.
    traffic = rows["traffic_aadt"]
    assert traffic.status == "UNKNOWN" and traffic.share_of_tracts == 0.0 and traffic.share_basis == "tracts"
    assert traffic.live_per_site and "10 of 10 counties" in traffic.reason
    assert rows["competition"].status == "UNKNOWN" and not rows["competition"].live_per_site
    assert rows["households"].status == "DATA" and rows["households"].share_of_tracts == 1.0
    assert coverage_share(list(rows.values())) == pytest.approx(0.15)  # households only (weight 0.15)
    mode, reason = fabric.mode(PROFILES)
    assert mode == "limited" and "below the threshold" in reason


def test_pinned_snapshot_date(tmp_path) -> None:
    metro = load_metro("houston_tx")
    for day in (date(2026, 10, 1), date(2026, 10, 12)):
        write_snapshot(SnapshotBuild(metro=metro, snapshot_date=day), tmp_path)
    assert latest_snapshot("houston_tx", tmp_path).snapshot_date == date(2026, 10, 12)
    assert latest_snapshot("houston_tx", tmp_path, pinned=date(2026, 10, 1)).snapshot_date == date(2026, 10, 1)
    assert latest_snapshot("houston_tx", tmp_path, pinned=date(2020, 1, 1)) is None


def test_other_seed_gives_another_city() -> None:
    first = get_fabric("synthetic_metro", seed=11)
    second = get_fabric("synthetic_metro", seed=12)
    assert first.tract_table() != second.tract_table()
    assert load_profile("car_wash").id == "car_wash"

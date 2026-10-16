"""SyntheticFabric: the data fabric over the generated SYNTHETIC city (synthetic_city.py).

Rent and wages are NOT generated data: they are the profile's ASSUMPTION ranges scaled by the city's SYNTHETIC rent
and wage indexes. They keep the status ASSUMPTION (the index is named in the reason), exactly like a real metro's
rent range, so they never count as data coverage (Studio rules D and G). The prime rate is generated: SYNTHETIC.
"""

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import numpy as np

from uwcopilot.studio.data.errors import NotAvailable
from uwcopilot.studio.data.fabric_base import (
    ROAD_SEARCH_MILES,
    DataFabric,
    FactorCoverage,
    FactorStatus,
    FactorValue,
    ListingSearch,
    PoiSearch,
    RoadResult,
    TractRecord,
    required_factors,
    share_basis_for,
)
from uwcopilot.studio.data.geo import haversine_miles_array
from uwcopilot.studio.data.osm import tag_matches
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.data.synthetic_city import SyntheticCity
from uwcopilot.studio.data.traffic_hpms import nearest_segment
from uwcopilot.studio.metros.models import MetroConfig, MetroMode, metro_mode
from uwcopilot.studio.profiles.factors import LOCATION_FACTORS
from uwcopilot.studio.profiles.models import Profile

SYNTHETIC_PUBLISHER = "Underwriting Copilot project (generated, not a real place)"


@dataclass
class SyntheticFabric(DataFabric):
    """The fabric over the generated SYNTHETIC city."""

    metro: MetroConfig
    city: SyntheticCity
    tract_index: dict[str, int] = field(init=False)
    record: SourceRecord = field(init=False)
    poi_lats: np.ndarray = field(init=False)
    poi_lons: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.tract_index = {tract.geoid: index for index, tract in enumerate(self.city.tracts)}
        self.record = synthetic_record(self.metro, self.city)
        self.poi_lats = np.array([poi.lat for poi in self.city.pois])
        self.poi_lons = np.array([poi.lon for poi in self.city.pois])

    @property
    def point_layers_local(self) -> bool:
        """The generated city's places and roads are in memory."""
        return True

    def mode(self, profiles: dict[str, Profile] | None = None) -> tuple[MetroMode, str]:
        return metro_mode(self.metro, None)

    def tract_ids(self) -> list[str]:
        return self.city.tract_ids()

    def tract(self, geoid: str) -> TractRecord:
        item = self.city.tracts[self.tract_index[geoid]]
        return TractRecord(
            geoid=item.geoid,
            lat=item.lat,
            lon=item.lon,
            land_area_sq_miles=item.land_area_sq_miles,
            data_label="SYNTHETIC",
            district=item.district,
        )

    def tract_table(self) -> dict[str, dict[str, float | None]]:
        return {tract.geoid: self.raw_values(tract.geoid) for tract in self.city.tracts}

    def raw_values(self, geoid: str) -> dict[str, float | None]:
        """The tract's factor values (None where suppressed)."""
        item = self.city.tracts[self.tract_index[geoid]]
        return {
            "resident_population": item.resident_population,
            "daytime_workers": item.daytime_workers,
            "households": item.households,
            "renter_households": item.renter_households,
            "median_household_income": item.median_household_income,
        }

    def tract_features(self, geoid: str) -> dict[str, FactorValue]:
        features: dict[str, FactorValue] = {}
        for factor, value in self.raw_values(geoid).items():
            unit = LOCATION_FACTORS[factor].unit
            if value is None:
                features[factor] = FactorValue(
                    factor=factor,
                    unit=unit,
                    status="UNKNOWN",
                    reason="suppressed in the SYNTHETIC published table (as real survey tables suppress small cells)",
                )
            else:
                features[factor] = FactorValue(
                    factor=factor,
                    value=value,
                    unit=unit,
                    status="SYNTHETIC",
                    reason=f"SYNTHETIC tract {geoid} of the generated city",
                    source=self.record,
                )
        return features

    def sites_near(self, lat: float, lon: float, radius_miles: float, tags: list[str]) -> PoiSearch:
        distances = haversine_miles_array(lat, lon, self.poi_lats, self.poi_lons)
        found = []
        for index in np.flatnonzero(distances <= radius_miles):
            poi = self.city.pois[index]
            if any(tag_matches(poi.tags, tag) for tag in tags):
                found.append(replace(poi, distance_miles=float(distances[index])))
        found.sort(key=lambda poi: poi.distance_miles or 0.0)
        reason = f"{len(found)} SYNTHETIC places within {radius_miles:g} miles"
        return PoiSearch(pois=found, status="SYNTHETIC", reason=reason, source=self.record)

    def nearest_road(self, lat: float, lon: float) -> RoadResult:
        try:
            segment, distance = nearest_segment(self.city.roads, lat, lon)
        except NotAvailable as exc:
            return RoadResult(None, None, "UNKNOWN", exc.reason)
        if distance > ROAD_SEARCH_MILES:
            return RoadResult(None, None, "UNKNOWN", f"no SYNTHETIC road within {ROAD_SEARCH_MILES} miles")
        reason = f"SYNTHETIC road {segment.route_name}, {distance:.2f} miles away"
        return RoadResult(segment, distance, "SYNTHETIC", reason, self.record)

    def str_comparables(self, lat: float, lon: float, radius_miles: float) -> ListingSearch:
        lats = np.array([item.lat for item in self.city.str_listings])
        lons = np.array([item.lon for item in self.city.str_listings])
        distances = haversine_miles_array(lat, lon, lats, lons)
        found = [
            replace(item, distance_miles=float(distances[index]))
            for index, item in enumerate(self.city.str_listings)
            if distances[index] <= radius_miles
        ]
        found.sort(key=lambda item: item.distance_miles or 0.0)
        reason = f"{len(found)} SYNTHETIC listings within {radius_miles:g} miles"
        return ListingSearch(listings=found, status="SYNTHETIC", reason=reason, source=self.record)

    def metro_inputs(self, profile: Profile) -> dict[str, FactorValue]:
        rent = profile.rent.monthly_rent
        wage = profile.labor.hourly_wage
        rent_low, rent_high = rent.bounds()
        wage_low, wage_high = wage.bounds()
        city = self.city
        note = "the profile's ASSUMPTION range scaled by the SYNTHETIC city's index"
        return {
            "commercial_rent": FactorValue(
                factor="commercial_rent",
                value=rent.point() * city.rent_index,
                low=rent_low * city.rent_index,
                high=rent_high * city.rent_index,
                unit=rent.unit,
                status="ASSUMPTION",
                reason=f"{note} (rent index {city.rent_index}); a founder's quote replaces it",
                assumptions=[f"profile rent.monthly_rent is an ASSUMPTION: {rent.note}"],
                source=self.record,
            ),
            "local_wages": FactorValue(
                factor="local_wages",
                value=wage.point() * city.wage_index,
                low=wage_low * city.wage_index,
                high=wage_high * city.wage_index,
                unit=wage.unit,
                status="ASSUMPTION",
                reason=f"{note} (wage index {city.wage_index})",
                assumptions=[f"profile labor.hourly_wage is an ASSUMPTION: {wage.note}"],
                source=self.record,
            ),
            "prime_rate": FactorValue(
                factor="prime_rate",
                value=city.prime_rate_pct,
                unit="percent",
                status="SYNTHETIC",
                reason="SYNTHETIC prime rate of the generated city; NOT a published rate",
                source=self.record,
            ),
        }

    def coverage(self, profile: Profile) -> list[FactorCoverage]:
        rows: list[FactorCoverage] = []
        table = self.tract_table()
        freshness = f"generated from seed {self.city.seed}"
        inputs = self.metro_inputs(profile)
        for factor, weight in required_factors(profile).items():
            scope = LOCATION_FACTORS[factor].scope
            status: FactorStatus = "SYNTHETIC"
            if factor in inputs and inputs[factor].status != "SYNTHETIC":
                status, share, reason = inputs[factor].status, 0.0, inputs[factor].reason
            elif scope == "tract":
                share = sum(values[factor] is not None for values in table.values()) / max(len(table), 1)
                reason = f"{share:.0%} of SYNTHETIC tracts have a value"
            elif scope == "road_layer":
                share = self.road_share()
                reason = f"{share:.0%} of SYNTHETIC tract centroids have a road with AADT within {ROAD_SEARCH_MILES} mi"
            else:
                share = 1.0
                reason = "SYNTHETIC layer generated for the whole metro"
            rows.append(
                FactorCoverage(
                    factor=factor,
                    scope=scope,
                    weight=weight,
                    status=status,
                    share_of_tracts=round(share, 4),
                    share_basis=share_basis_for(scope, real_metro=False),
                    source=f"synthetic_city (seed {self.city.seed})",
                    freshness=freshness,
                    reason=reason,
                )
            )
        return rows

    def road_share(self) -> float:
        """Share of tract centroids with a road segment within ROAD_SEARCH_MILES."""
        hits = 0
        for tract in self.city.tracts:
            hits += self.nearest_road(tract.lat, tract.lon).segment is not None
        return hits / max(len(self.city.tracts), 1)


def synthetic_record(metro: MetroConfig, city: SyntheticCity) -> SourceRecord:
    """The SourceRecord every SYNTHETIC value carries; sha256 fingerprints the generated tract table."""
    table = [
        [t.geoid, t.lat, t.lon, t.resident_population, t.households, t.median_household_income] for t in city.tracts
    ]
    fingerprint = hashlib.sha256(json.dumps(table, sort_keys=True).encode("utf-8")).hexdigest()
    settings = metro.synthetic
    params = {"seed": str(city.seed)}
    if settings is not None:
        params.update({"n_tracts": str(settings.n_tracts), "extent_miles": str(settings.extent_miles)})
    return SourceRecord(
        source_id="synthetic_city",
        publisher=SYNTHETIC_PUBLISHER,
        dataset="SYNTHETIC city generator (src/uwcopilot/studio/data/synthetic_city.py)",
        url=f"synthetic://{metro.id}",
        params=params,
        retrieved_at=generated_at(),
        sha256=fingerprint,
        license_note="SYNTHETIC data generated by this repository; no real place, person or business",
        data_label="SYNTHETIC",
        origin="synthetic",
        vintage=f"seed {city.seed}",
    )


def generated_at() -> datetime:
    """The time the city was generated (it is regenerated from the seed on every run)."""
    return datetime.now(UTC).replace(microsecond=0)

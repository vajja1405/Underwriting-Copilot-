"""SnapshotFabric: the data fabric over a real metro's snapshot (written by snapshot.py).

What it reads, and from where:
- tract factors (residents, households, renters, income) from tracts.json (ACS step), daytime workers from
  tracts.json (LODES step: jobs located in the tract);
- places (competition, anchors, tourism) from the snapshot's Overture places layer (places.json) when the
  overture_places step succeeded (a search circle reaching outside the extract's bounding box is UNKNOWN, never a
  count of the part inside); otherwise, only with a live client, from the OpenStreetMap Overpass API per site;
- traffic from the snapshot's HPMS road layer (roads.npz) when the roads_by_tract step succeeded (tract centroids are
  answered from the per-tract values the step stored, computed with the same rule); otherwise, only with a live
  client, from the HPMS FeatureServer per site.
A layer that is missing, or whose file no longer matches the SHA-256 in the manifest, is UNKNOWN with the reason.
The coverage rows are built in snapshot_coverage.py.
"""

import io
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from uwcopilot.studio.data import snapshot_coverage
from uwcopilot.studio.data.cache import sha256_bytes
from uwcopilot.studio.data.errors import StudioDataError
from uwcopilot.studio.data.fabric_base import (
    ROAD_SEARCH_MILES,
    TRACT_FACTORS,
    DataFabric,
    FactorCoverage,
    FactorValue,
    ListingSearch,
    PoiSearch,
    RoadResult,
    TractRecord,
    coverage_share,
)
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.lodes import JOB_TYPE, WATER_ONLY_BASIS, WORKERS_BASIS_FIELD, YEAR
from uwcopilot.studio.data.osm import pois_near
from uwcopilot.studio.data.paths import SNAPSHOTS_DIR
from uwcopilot.studio.data.places_layer import PlacesLayer
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.data.road_layer import RoadLayer
from uwcopilot.studio.data.snapshot_files import PLACES_FILE, ROADS_FILE, SnapshotFileError, checked_bytes, checked_json
from uwcopilot.studio.data.traffic_hpms import nearest_road
from uwcopilot.studio.metros.models import MetroConfig, MetroMode, metro_mode
from uwcopilot.studio.profiles.factors import LOCATION_FACTORS
from uwcopilot.studio.profiles.loader import load_all_profiles
from uwcopilot.studio.profiles.models import Profile

PLACES_STEP = "overture_places"
ROADS_STEP = "roads_by_tract"
WORKERS_STEP = "lehd_daytime_workers"


@dataclass
class Snapshot:
    """A real metro's snapshot as written by snapshot.py: manifest plus the tract table.

    manifest_sha256 / tracts_sha256 are the SHA-256 of the two files as they were read. A same-day rerun of
    `make studio-data` replaces the folder, so results files record these (fingerprint()) to pin the exact data.
    """

    folder: Path
    manifest: dict[str, Any]
    tracts: dict[str, dict[str, Any]]
    manifest_sha256: str = ""
    tracts_sha256: str | None = None

    @property
    def snapshot_date(self) -> date:
        """The snapshot's date (its folder name)."""
        return date.fromisoformat(self.manifest["snapshot_date"])

    def sources(self, step: str) -> list[SourceRecord]:
        """The SourceRecords stored for one step of the snapshot."""
        for item in self.manifest.get("steps", []):
            if item.get("name") == step:
                return [SourceRecord.model_validate(record) for record in item.get("sources", [])]
        return []

    def step_status(self, step: str) -> tuple[str, str]:
        """(status, detail) of one step, or ('missing', ...) when the step did not run."""
        for item in self.manifest.get("steps", []):
            if item.get("name") == step:
                return str(item.get("status")), str(item.get("detail", ""))
        return "missing", f"step {step!r} is not in this snapshot"

    def fingerprint(self) -> dict[str, str | None]:
        """{snapshot_date, manifest_sha256, tracts_sha256}: what a results file records to pin this snapshot."""
        return {
            "snapshot_date": self.snapshot_date.isoformat(),
            "manifest_sha256": self.manifest_sha256,
            "tracts_sha256": self.tracts_sha256,
        }


def latest_snapshot(metro_id: str, snapshots_dir: Path = SNAPSHOTS_DIR, pinned: date | None = None) -> Snapshot | None:
    """The pinned snapshot, or the newest one on disk for the metro; None when there is none."""
    folder = snapshots_dir / metro_id
    if not folder.is_dir():
        return None
    candidates = sorted(path for path in folder.iterdir() if (path / "manifest.json").is_file())
    if pinned is not None:
        candidates = [path for path in candidates if path.name == pinned.isoformat()]
    if not candidates:
        return None
    chosen = candidates[-1]
    manifest_bytes = (chosen / "manifest.json").read_bytes()
    tracts_file = chosen / "tracts.json"
    tracts_bytes = tracts_file.read_bytes() if tracts_file.is_file() else None
    return Snapshot(
        folder=chosen,
        manifest=json.loads(manifest_bytes.decode("utf-8")),
        tracts=json.loads(tracts_bytes.decode("utf-8"))["tracts"] if tracts_bytes is not None else {},
        manifest_sha256=sha256_bytes(manifest_bytes),
        tracts_sha256=sha256_bytes(tracts_bytes) if tracts_bytes is not None else None,
    )


@dataclass
class SnapshotFabric(DataFabric):
    """The fabric over a real metro's snapshot. Missing layers are UNKNOWN with the reason, never filled."""

    metro: MetroConfig
    snapshot: Snapshot | None
    live: PoliteClient | None = None
    loaded: dict[str, Any] = field(default_factory=dict, repr=False)

    def no_snapshot_reason(self) -> str:
        """Why a factor is UNKNOWN when there is no snapshot."""
        return f"no data snapshot for {self.metro.id} (run `make studio-data METRO={self.metro.id}`); limited mode"

    @property
    def point_layers_local(self) -> bool:
        """True without a live client (lookups are then answered or UNKNOWN locally), or when the snapshot stores
        both the places and the road layer (lookups never need the network)."""
        if self.live is None:
            return True
        return self.places()[0] is not None and self.roads()[0] is not None

    def mode(self, profiles: dict[str, Profile] | None = None) -> tuple[MetroMode, str]:
        snapshot_date = self.snapshot.snapshot_date if self.snapshot else None
        if snapshot_date is None:
            return metro_mode(self.metro, None, snapshot_date=None)
        shares = [coverage_share(self.coverage(profile)) for profile in (profiles or load_all_profiles()).values()]
        return metro_mode(self.metro, min(shares) if shares else None, snapshot_date=snapshot_date)

    def tract_ids(self) -> list[str]:
        return sorted(self.snapshot.tracts) if self.snapshot else []

    def tract(self, geoid: str) -> TractRecord:
        if self.snapshot is None:
            raise KeyError(geoid)
        item = self.snapshot.tracts[geoid]
        return TractRecord(
            geoid=geoid,
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            land_area_sq_miles=item.get("land_area_sq_miles"),
            data_label="PUBLIC",
        )

    def tract_table(self) -> dict[str, dict[str, float | None]]:
        if self.snapshot is None:
            return {}
        return {
            geoid: {factor: item.get(factor) for factor in TRACT_FACTORS}
            for geoid, item in self.snapshot.tracts.items()
        }

    def tract_features(self, geoid: str) -> dict[str, FactorValue]:
        if self.snapshot is None:
            return {
                f: FactorValue(
                    factor=f, unit=LOCATION_FACTORS[f].unit, status="UNKNOWN", reason=self.no_snapshot_reason()
                )
                for f in TRACT_FACTORS
            }
        item = self.snapshot.tracts[geoid]
        acs = {record.geography: record for record in self.snapshot.sources("acs_tracts")}
        county_record = acs.get(f"county {geoid[:5]}")
        features: dict[str, FactorValue] = {}
        for factor in TRACT_FACTORS:
            if factor == "daytime_workers":
                features[factor] = self.daytime_workers(geoid, item.get(factor), item.get(WORKERS_BASIS_FIELD))
                continue
            unit = LOCATION_FACTORS[factor].unit
            value = item.get(factor)
            if value is None:
                status, detail = self.snapshot.step_status("acs_tracts")
                reason = f"no ACS estimate in the snapshot (acs_tracts step: {status}; {detail})"
                features[factor] = FactorValue(factor=factor, unit=unit, status="UNKNOWN", reason=reason)
            else:
                features[factor] = FactorValue(
                    factor=factor,
                    value=float(value),
                    unit=unit,
                    status="DATA",
                    reason=f"ACS 5-year estimate for tract {geoid}",
                    source=county_record,
                )
        return features

    def daytime_workers(self, geoid: str, value: Any, basis: str | None = None) -> FactorValue:
        """LODES jobs located in the tract, with the state file's SourceRecord; UNKNOWN with the reason when absent.

        basis WATER_ONLY_BASIS marks a water-only tract that the lehd_daytime_workers step set to 0 (see
        snapshot_layers.daytime_values)."""
        unit = LOCATION_FACTORS["daytime_workers"].unit
        status, detail = self.snapshot.step_status(WORKERS_STEP) if self.snapshot else ("missing", "")
        if value is None:
            if status == "ok":
                reason = (
                    "no block of this tract is in the LODES WAC file; left UNKNOWN (the LODES documentation does not "
                    "say that a missing block means zero jobs)"
                )
            else:
                reason = f"no LODES daytime-worker layer in the snapshot ({WORKERS_STEP} step: {status}; {detail})"
            return FactorValue(factor="daytime_workers", unit=unit, status="UNKNOWN", reason=reason)
        postal = ((self.snapshot.manifest.get("layers") or {}).get("state_postal_by_fips") or {}).get(geoid[:2], "")
        records = {record.geography: record for record in self.snapshot.sources(WORKERS_STEP)}
        if basis == WATER_ONLY_BASIS:
            return FactorValue(
                factor="daytime_workers",
                value=float(value),
                unit=unit,
                status="DATA",
                reason=(
                    f"0 jobs: tract {geoid} is water only (land area 0 in the Census Gazetteer tract layer) and "
                    f"no block of it is in the LODES 8 WAC {YEAR} file"
                ),
                source=records.get(f"state {postal}"),
            )
        return FactorValue(
            factor="daytime_workers",
            value=float(value),
            unit=unit,
            status="DATA",
            reason=(
                f"LODES 8 WAC {YEAR}: {int(value):,} jobs located in tract {geoid} (all jobs, {JOB_TYPE}; one person "
                "can hold more than one job)"
            ),
            source=records.get(f"state {postal}"),
        )

    def places(self) -> tuple[PlacesLayer | None, str]:
        """(the snapshot's places layer, "") or (None, why there is none). Loaded once."""
        if "places" not in self.loaded:
            self.loaded["places"] = self.load_places()
        return self.loaded["places"]

    def load_places(self) -> tuple[PlacesLayer | None, str]:
        """Read places.json after checking its SHA-256 against the manifest."""
        if self.snapshot is None:
            return None, self.no_snapshot_reason()
        status, detail = self.snapshot.step_status(PLACES_STEP)
        if status != "ok":
            return None, f"no places layer in the snapshot ({PLACES_STEP} step: {status}; {detail})"
        try:
            payload = checked_json(self.snapshot.folder, self.snapshot.manifest, PLACES_FILE)
            records = self.snapshot.sources(PLACES_STEP)
            return PlacesLayer.from_payload(payload, records[0] if records else None), ""
        except SnapshotFileError as exc:
            return None, f"the places layer cannot be used: {exc}"
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return None, f"the places layer cannot be read ({type(exc).__name__}: {exc}); re-run make studio-data"

    def roads(self) -> tuple[RoadLayer | None, str]:
        """(the snapshot's road layer, "") or (None, why there is none). Loaded once."""
        if "roads" not in self.loaded:
            self.loaded["roads"] = self.load_roads()
        return self.loaded["roads"]

    def load_roads(self) -> tuple[RoadLayer | None, str]:
        """Read roads.npz after checking its SHA-256 against the manifest."""
        if self.snapshot is None:
            return None, self.no_snapshot_reason()
        status, detail = self.snapshot.step_status(ROADS_STEP)
        if status != "ok":
            return None, f"no HPMS road layer in the snapshot ({ROADS_STEP} step: {status}; {detail})"
        try:
            body = checked_bytes(self.snapshot.folder, self.snapshot.manifest, ROADS_FILE)
        except SnapshotFileError as exc:
            return None, f"the road layer cannot be used: {exc}"
        return RoadLayer.load(io.BytesIO(body)), ""

    def sites_near(self, lat: float, lon: float, radius_miles: float, tags: list[str]) -> PoiSearch:
        layer, problem = self.places()
        if layer is not None:
            unmapped = layer.unmapped(tags)
            if len(unmapped) == len(tags):
                reason = f"no Overture places category is mapped to {', '.join(tags)}"
                return PoiSearch([], "UNKNOWN", reason)
            if not layer.covers(lat, lon, radius_miles):
                reason = (
                    f"the {radius_miles:g}-mile search around this location reaches outside the Overture extract's "
                    f"bounding box {list(layer.bbox)}; places there were not extracted, so the count is UNKNOWN"
                )
                return PoiSearch([], "UNKNOWN", reason)
            pois = layer.search(lat, lon, radius_miles, tags)
            reason = f"{len(pois)} Overture places (release {layer.release}) within {radius_miles:g} miles"
            if unmapped:
                reason += f"; not counted (no Overture category): {', '.join(unmapped)}"
            return PoiSearch(pois, "DATA", reason, layer.record)
        if self.live is None:
            return PoiSearch([], "UNKNOWN", f"{problem}; live OpenStreetMap lookups are off")
        try:
            pois, record = pois_near(self.live, lat, lon, radius_miles, tags)
        except StudioDataError as exc:
            return PoiSearch([], "UNKNOWN", f"{problem}; the OpenStreetMap lookup failed: {exc}")
        return PoiSearch(pois, "DATA", f"{len(pois)} OpenStreetMap places within {radius_miles:g} miles", record)

    def centroid_roads(self) -> dict[tuple[float, float], int | None]:
        """{(lat, lon) of a tract centroid: nearest segment index or None}, as stored by the roads_by_tract step."""
        if "centroid_roads" not in self.loaded:
            stored: dict[tuple[float, float], int | None] = {}
            for item in self.snapshot.tracts.values() if self.snapshot else []:
                if "traffic_segment" in item:
                    stored[(float(item["lat"]), float(item["lon"]))] = item["traffic_segment"]
            self.loaded["centroid_roads"] = stored
        return self.loaded["centroid_roads"]

    def road_record(self, index: int) -> SourceRecord | None:
        """The SourceRecord of the HPMS page that returned a segment."""
        if "road_records" not in self.loaded:
            self.loaded["road_records"] = self.snapshot.sources(ROADS_STEP) if self.snapshot else []
        records = self.loaded["road_records"]
        return records[index] if 0 <= index < len(records) else None

    def nearest_road(self, lat: float, lon: float) -> RoadResult:
        layer, problem = self.roads()
        if layer is not None:
            return self.nearest_stored_road(layer, lat, lon)
        if self.live is None:
            return RoadResult(None, None, "UNKNOWN", f"{problem}; live HPMS lookups are off")
        return self.nearest_live_road(lat, lon, problem)

    def nearest_stored_road(self, layer: RoadLayer, lat: float, lon: float) -> RoadResult:
        """The nearest segment within ROAD_SEARCH_MILES from the snapshot's road layer."""
        stored = self.centroid_roads()
        if (lat, lon) in stored:
            index = stored[(lat, lon)]
            found = None if index is None else (int(index), layer.distance_to(int(index), lat, lon))
        else:
            found = layer.nearest(lat, lon, ROAD_SEARCH_MILES)
        if found is None:
            reason = f"no HPMS segment with AADT within {ROAD_SEARCH_MILES} miles (snapshot road layer)"
            return RoadResult(None, None, "UNKNOWN", reason)
        index, miles = found
        segment = layer.segment(index)
        reason = f"HPMS segment {segment.route_id}, {miles:.2f} miles away (snapshot road layer)"
        return RoadResult(segment, miles, "DATA", reason, self.road_record(int(layer.source_index[index])))

    def nearest_live_road(self, lat: float, lon: float, problem: str) -> RoadResult:
        """The nearest segment from the HPMS FeatureServer of each of the metro's states (a live lookup)."""
        best: RoadResult | None = None
        problems: list[str] = []
        for state in self.metro.states:
            try:
                found = nearest_road(self.live, state, lat, lon, ROAD_SEARCH_MILES)
            except StudioDataError as exc:
                problems.append(f"{state}: {exc}")
                continue
            if best is None or found.distance_miles < (best.distance_miles or float("inf")):
                reason = f"HPMS segment {found.segment.route_id}, {found.distance_miles:.2f} miles away (live)"
                best = RoadResult(found.segment, found.distance_miles, "DATA", reason, found.source)
        if best is None:
            return RoadResult(None, None, "UNKNOWN", "; ".join([problem, *problems]) or "no HPMS segment near the site")
        return best

    def str_comparables(self, lat: float, lon: float, radius_miles: float) -> ListingSearch:
        adapters = [item.adapter for item in self.metro.enabled_enhancements("str_comps")]
        where = adapters or "this metro"
        reason = f"short-term rental comparables are UNKNOWN: Inside Airbnb coverage not verified for {where}"
        return ListingSearch([], "UNKNOWN", reason)

    def metro_inputs(self, profile: Profile) -> dict[str, FactorValue]:
        rent = profile.rent.monthly_rent
        rent_low, rent_high = rent.bounds()
        inputs = {
            "commercial_rent": FactorValue(
                factor="commercial_rent",
                value=rent.point(),
                low=rent_low,
                high=rent_high,
                unit=rent.unit,
                status="ASSUMPTION",
                reason="no free commercial-rent source; the founder's quote replaces this range",
                assumptions=[f"profile rent.monthly_rent: {rent.note}"],
            ),
        }
        stored = (self.snapshot.manifest.get("metro_inputs", {}) if self.snapshot else {}) or {}
        wage = (stored.get("local_wages") or {}).get(profile.id)
        inputs["local_wages"] = (
            FactorValue.model_validate(wage)
            if wage
            else FactorValue(
                factor="local_wages",
                unit="USD/hour",
                status="UNKNOWN",
                reason=self.no_snapshot_reason()
                if self.snapshot is None
                else "no QCEW wage in the snapshot for this profile",
            )
        )
        prime = stored.get("prime_rate")
        inputs["prime_rate"] = (
            FactorValue.model_validate(prime)
            if prime
            else FactorValue(
                factor="prime_rate",
                unit="percent",
                status="UNKNOWN",
                reason=self.no_snapshot_reason() if self.snapshot is None else "no FRED observation in the snapshot",
            )
        )
        return inputs

    def coverage(self, profile: Profile) -> list[FactorCoverage]:
        return snapshot_coverage.coverage_rows(self, profile)

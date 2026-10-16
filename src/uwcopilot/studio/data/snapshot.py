"""Build a real metro's data snapshot: `make studio-data METRO=<id>` (or METRO=all).

    uv run python -m uwcopilot.studio.data.snapshot --metro houston_tx
    uv run python -m uwcopilot.studio.data.snapshot --metro all
    uv run python -m uwcopilot.studio.data.snapshot --metro all --refresh   # ignore cached answers

The national layer runs first for every metro, one step per source. Each step either succeeds (its values and
SourceRecords go into the snapshot), is "not_available" (no documented dataset, e.g. a state traffic adapter), or
fails (a host is unreachable or a free API key is missing). A failed step never stops the others; the snapshot is
written with what succeeded, and the command exits with status 1 and a summary naming every unreachable host and
every missing key variable. The data fabric (fabric.py) reads the snapshot and marks whatever is missing UNKNOWN.

Three steps store layers that let SiteIQ rank every tract (snapshot_layers.py): "roads_by_tract" (HPMS segments with
AADT, roads.npz, and each tract centroid's nearest segment), "overture_places" (the Overture places extract,
places.json, and place counts around every tract centroid, poi_counts.json) and "lehd_daytime_workers" (LODES jobs
per tract). Places come from Overture Maps by default. The OpenStreetMap Overpass check ("osm_pois") runs only with
--with-overpass: overpass-api.de reset every connection from the build environment (2026-10-12 and 2026-10-13), and
per-site Overpass queries cannot give the per-tract counts the metro-wide ranking needs (docs/studio/data_sources.md).

Answers come from the response cache when the same request was made before. A rejected answer (an error page, a key
the host refused) is never cached, so fixing a key and rerunning is enough; --refresh fetches every answer again.
A rerun on the same day replaces that day's snapshot folder; results files therefore record the SHA-256 of the
snapshot files they used (fabric_snapshot.Snapshot.fingerprint), so a replaced snapshot is detectable.

Layout: data/studio/snapshots/<metro id>/<snapshot date>/manifest.json, tracts.json and the layer files listed in
the manifest's "files" with their SHA-256 (snapshot_files.py) (all gitignored).
The SYNTHETIC metro has nothing to download: its city is generated from its seed.
"""

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from uwcopilot.studio.data import (
    business_patterns,
    census_acs,
    census_geography,
    rates,
    snapshot_layers,
    traffic_hpms,
    wages,
)
from uwcopilot.studio.data.cache import sha256_bytes, write_atomic
from uwcopilot.studio.data.city_open_data import DATASETS as CITY_DATASETS
from uwcopilot.studio.data.errors import (
    HostUnreachable,
    InvalidApiKey,
    MissingApiKey,
    NotAvailable,
    StudioDataError,
    UpstreamError,
)
from uwcopilot.studio.data.fabric import FactorValue
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.keys import key_status, require_key
from uwcopilot.studio.data.osm import OVERPASS_URL
from uwcopilot.studio.data.overture_places import PlacesReader
from uwcopilot.studio.data.paths import SNAPSHOTS_DIR
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.data.snapshot_files import PLACES_FILE, POI_COUNTS_FILE, ROADS_FILE
from uwcopilot.studio.data.str_comps import VERIFIED_COVERAGE
from uwcopilot.studio.data.traffic_state import adapter_for
from uwcopilot.studio.metros.loader import list_metros, load_metro
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.profiles.loader import load_all_profiles
from uwcopilot.studio.profiles.models import Profile

GENERATED_BY = "uwcopilot.studio.data.snapshot"
OVERPASS_STATUS_URL = OVERPASS_URL.replace("/interpreter", "/status")
# How far back the prime-rate request starts (only the newest observation is kept).
PRIME_LOOKBACK_DAYS = 45


@dataclass
class StepResult:
    """The outcome of one snapshot step."""

    name: str
    status: str = "ok"
    detail: str = ""
    sources: list[SourceRecord] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        """The step as stored in manifest.json."""
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "sources": [record.model_dump(mode="json") for record in self.sources],
        }


@dataclass
class SnapshotBuild:
    """Everything a snapshot run collects before it is written."""

    metro: MetroConfig
    snapshot_date: date
    steps: list[StepResult] = field(default_factory=list)
    tracts: dict[str, dict[str, Any]] = field(default_factory=dict)
    layers: dict[str, Any] = field(default_factory=dict)
    metro_inputs: dict[str, Any] = field(default_factory=lambda: {"local_wages": {}, "prime_rate": None})
    files: dict[str, bytes] = field(default_factory=dict)

    def failures(self) -> list[str]:
        """One line per failed step (each names the host or the missing key variable)."""
        return [f"{self.metro.id}: {step.name}: {step.detail}" for step in self.steps if step.status == "failed"]


def run_step(build: SnapshotBuild, name: str, action: Callable[[StepResult], None]) -> None:
    """Run one step; turn a known data error into a failed or not_available step instead of stopping the run."""
    step = StepResult(name=name)
    try:
        action(step)
    except NotAvailable as exc:
        step.status, step.detail = "not_available", exc.reason
    except (MissingApiKey, InvalidApiKey, HostUnreachable, UpstreamError) as exc:
        step.status, step.detail = "failed", str(exc)
    except StudioDataError as exc:
        step.status, step.detail = "failed", f"{type(exc).__name__}: {exc}"
    build.steps.append(step)


def tract_geography_step(build: SnapshotBuild, client: PoliteClient) -> Callable[[StepResult], None]:
    """The metro's tracts and their internal points (Census Gazetteer)."""

    def action(step: StepResult) -> None:
        tracts, record = census_geography.fetch_metro_tracts(client, set(build.metro.county_geoids()))
        for geoid, point in tracts.items():
            build.tracts[geoid] = {"lat": point.lat, "lon": point.lon, "land_area_sq_miles": point.land_area_sq_miles}
            build.layers.setdefault("state_postal_by_fips", {})[geoid[:2]] = point.usps
        step.sources.append(record)
        step.detail = f"{len(tracts)} tracts in {len(build.metro.counties)} counties"

    return action


def acs_step(build: SnapshotBuild, client: PoliteClient) -> Callable[[StepResult], None]:
    """ACS 5-year values per tract, county by county (needs CENSUS_API_KEY)."""

    def action(step: StepResult) -> None:
        require_key("CENSUS_API_KEY")  # fail before any request when the key is missing
        labels = census_acs.verify_variables(client)
        for county in build.metro.counties:
            table = census_acs.fetch_tracts(client, county.state_fips, county.county_fips)
            step.sources.append(table.source)
            for geoid, values in table.values.items():
                build.tracts.setdefault(geoid, {}).update(values)
        step.detail = f"variables checked: {sorted(labels)}; {len(step.sources)} counties"

    return action


def business_patterns_step(build: SnapshotBuild, client: PoliteClient, profiles: dict[str, Profile]) -> Callable:
    """County establishment counts for every profile NAICS code (needs CENSUS_API_KEY)."""

    def action(step: StepResult) -> None:
        counts: dict[str, dict[str, int]] = {}
        state_fips = sorted({county.state_fips for county in build.metro.counties})
        metro_counties = set(build.metro.county_geoids())
        codes = sorted({item.code for profile in profiles.values() for item in profile.naics})
        no_rows: set[str] = set()
        for naics in codes:
            for state in state_fips:
                result = business_patterns.fetch_county_counts(client, state, naics)
                step.sources.append(result.source)
                if result.no_rows:
                    no_rows.add(naics)
                kept = {geoid: n for geoid, n in result.counts.items() if geoid in metro_counties}
                counts.setdefault(naics, {}).update(kept)
        build.layers["business_patterns"] = counts
        step.detail = f"{len(codes)} NAICS codes x {len(state_fips)} states"
        if no_rows:
            step.detail += (
                f"; no CBP rows (HTTP 204, empty) for {sorted(no_rows)}: those codes are UNKNOWN (the CBP API's "
                "code variable is NAICS2017)"
            )

    return action


def wages_step(build: SnapshotBuild, client: PoliteClient, profiles: dict[str, Profile]) -> Callable:
    """QCEW private-sector weekly wages per profile, converted to an hourly range with a labeled assumption."""

    def action(step: StepResult) -> None:
        metro = build.metro
        if metro.cbsa_code is None:
            raise NotAvailable("the metro has no CBSA code yet (run scripts/studio_fetch_delineation.py)")
        area = wages.area_code_for_cbsa(metro.cbsa_code)
        title = wages.verify_area_code(client, area, metro.name.split(",")[0])
        quarters = wages.published_quarters(client, area, build.snapshot_date)
        found = []
        for profile_id, profile in profiles.items():
            value = profile_wage(quarters, area, profile)
            if value is not None:
                build.metro_inputs["local_wages"][profile_id] = value.model_dump(mode="json")
                found.append(profile_id)
                step.sources.append(value.source)
        checked = ", ".join(f"{item.year} Q{item.quarter}" for item in quarters)
        step.detail = f"{title} ({area}), quarters checked {checked}: wages for {found}"

    return action


def profile_wage(quarters: list[wages.QcewQuarter], area: str, profile: Profile) -> FactorValue | None:
    """The hourly wage range for a profile from the newest quarter that discloses one of its NAICS codes."""
    for quarter in quarters:
        for item in profile.naics:
            try:
                wage = wages.qcew_wage(quarter.text, quarter.fetched, area, item.code, quarter.year, quarter.quarter)
            except NotAvailable:
                continue
            return wage_factor(wage)
    return None


def wage_factor(wage: wages.QcewWage) -> FactorValue:
    """A QCEW wage as the local_wages FactorValue.

    Status DATA_X_ASSUMPTION: the weekly wage is DATA, but the hourly value and its whole low-high range come from
    the HOURS_PER_WEEK assumption, so the hourly figure is never labeled plain DATA (Studio rule D).
    """
    low, mode, high = wage.hourly_range()
    hours = wages.HOURS_PER_WEEK
    return FactorValue(
        factor="local_wages",
        value=round(mode, 2),
        low=round(low, 2),
        high=round(high, 2),
        unit="USD/hour",
        status="DATA_X_ASSUMPTION",
        reason=(
            f"DATA: BLS QCEW {wage.year} Q{wage.quarter} private average weekly wage {wage.avg_weekly_wage:.0f} USD "
            f"for NAICS {wage.used_naics} (requested {wage.requested_naics}) in {wage.area_code}; "
            f"x ASSUMPTION: divided by {hours['low']:g}-{hours['high']:g} paid hours per week "
            f"(mode {hours['mode']:g}), which sets the hourly value and its whole range"
        ),
        assumptions=[
            f"hours per week {hours['low']:g}-{hours['high']:g} (mode {hours['mode']:g}): ASSUMPTION; {hours['note']}"
        ],
        source=wage.source,
    )


def prime_rate_step(build: SnapshotBuild, client: PoliteClient) -> Callable[[StepResult], None]:
    """The newest bank prime loan rate from FRED."""

    def action(step: StepResult) -> None:
        start = build.snapshot_date - timedelta(days=PRIME_LOOKBACK_DAYS)
        observation = rates.latest_rate(client, start)
        value = FactorValue(
            factor="prime_rate",
            value=observation.value_pct,
            unit="percent",
            status="DATA",
            reason=f"FRED {observation.series_id} on {observation.observation_date.isoformat()}",
            source=observation.source,
        )
        build.metro_inputs["prime_rate"] = value.model_dump(mode="json")
        step.sources.append(observation.source)
        step.detail = value.reason

    return action


def roads_step(build: SnapshotBuild, client: PoliteClient) -> Callable[[StepResult], None]:
    """How many counties have HPMS segments with AADT (site lookups are then made live per site)."""

    def action(step: StepResult) -> None:
        postal_by_fips = state_postal_by_fips(build)
        with_aadt = 0
        for county in build.metro.counties:
            count, record = traffic_hpms.county_segment_count(
                client, postal_by_fips[county.state_fips], county.county_fips
            )
            step.sources.append(record)
            with_aadt += count > 0
        build.layers["roads"] = {
            "kind": "fhwa_hpms_live_per_site",
            "counties_with_aadt": with_aadt,
            "counties_total": len(build.metro.counties),
        }
        step.detail = f"HPMS segments with AADT in {with_aadt} of {len(build.metro.counties)} counties"

    return action


def state_postal_by_fips(build: SnapshotBuild) -> dict[str, str]:
    """{state FIPS: postal code} read from the Gazetteer file's USPS column (tract_geography step)."""
    mapping = build.layers.get("state_postal_by_fips") or {}
    missing = sorted({county.state_fips for county in build.metro.counties} - set(mapping))
    if missing:
        raise NotAvailable(f"no postal code for state FIPS {missing} (the tract_geography step did not provide one)")
    return dict(mapping)


def layer_step(
    build: SnapshotBuild, layer_name: str, make: Callable[[], snapshot_layers.LayerOutput]
) -> Callable[[StepResult], None]:
    """A step that stores one snapshot_layers output: per-tract values, layer facts, files, sources and detail."""

    def action(step: StepResult) -> None:
        output = make()
        for geoid, values in output.tract_values.items():
            build.tracts.setdefault(geoid, {}).update(values)
        build.layers[layer_name] = output.layer_info
        build.files.update(output.files)
        step.sources += output.sources
        step.detail = output.detail

    return action


def roads_by_tract_step(build: SnapshotBuild, client: PoliteClient) -> Callable[[StepResult], None]:
    """HPMS segments of every county and each tract centroid's nearest one (snapshot_layers.build_roads_layer)."""
    return layer_step(
        build,
        "road_layer",
        lambda: snapshot_layers.build_roads_layer(client, build.metro, build.tracts, state_postal_by_fips(build)),
    )


def overture_places_step(
    build: SnapshotBuild, client: PoliteClient, profiles: dict[str, Profile], reader: PlacesReader | None
) -> Callable[[StepResult], None]:
    """The Overture places extract and per-tract place counts (snapshot_layers.build_places_layer)."""
    return layer_step(
        build,
        "places",
        lambda: snapshot_layers.build_places_layer(client, build.metro, build.tracts, profiles, reader),
    )


def daytime_workers_step(build: SnapshotBuild, client: PoliteClient) -> Callable[[StepResult], None]:
    """LODES jobs per tract (snapshot_layers.build_daytime_workers)."""
    return layer_step(
        build,
        "daytime_workers",
        lambda: snapshot_layers.build_daytime_workers(client, build.metro, build.tracts, state_postal_by_fips(build)),
    )


def osm_step(client: PoliteClient) -> Callable[[StepResult], None]:
    """Whether the Overpass API answers (POIs are then queried live per site)."""

    def action(step: StepResult) -> None:
        client.get(OVERPASS_STATUS_URL, use_cache=False)
        step.detail = "Overpass API reachable; POIs are queried live per site"

    return action


def enhancement_steps(build: SnapshotBuild) -> None:
    """One step per enabled enhancement: available (documented dataset) or not_available with the reason."""
    for item in build.metro.enabled_enhancements():
        step = StepResult(name=f"enhancement:{item.kind}:{item.adapter}")
        try:
            if item.kind == "traffic_state":
                adapter = adapter_for(item.adapter)
                if adapter.dataset is None:
                    raise NotAvailable(f"no documented {item.adapter} DOT traffic-count dataset recorded yet")
            elif item.kind == "city_open_data":
                if item.adapter not in CITY_DATASETS:
                    raise NotAvailable(f"no documented open-data license dataset recorded for {item.adapter!r}")
                step.detail = f"documented dataset {CITY_DATASETS[item.adapter].source.dataset}; queried live per site"
            elif item.kind == "str_comps" and item.adapter not in VERIFIED_COVERAGE:
                raise NotAvailable(f"Inside Airbnb coverage for {item.adapter!r} not verified")
        except NotAvailable as exc:
            step.status, step.detail = "not_available", exc.reason
        build.steps.append(step)


def build_snapshot(
    metro: MetroConfig,
    client: PoliteClient,
    profiles: dict[str, Profile],
    today: date,
    places_reader: PlacesReader | None = None,
    include_overpass: bool = False,
) -> SnapshotBuild:
    """Run every step for one real metro and return what was collected.

    places_reader replaces the DuckDB reader of the Overture files (tests pass a local one); include_overpass adds
    the optional OpenStreetMap Overpass reachability check.
    """
    if metro.data_label != "PUBLIC":
        raise ValueError(f"{metro.id} is SYNTHETIC; there is nothing to download")
    if not metro.verified:
        raise ValueError(f"{metro.id} has no verified county list; run scripts/studio_fetch_delineation.py first")
    build = SnapshotBuild(metro=metro, snapshot_date=today)
    run_step(build, "tract_geography", tract_geography_step(build, client))
    run_step(build, "acs_tracts", acs_step(build, client))
    run_step(build, "business_patterns", business_patterns_step(build, client, profiles))
    run_step(build, "qcew_wages", wages_step(build, client, profiles))
    run_step(build, "prime_rate", prime_rate_step(build, client))
    run_step(build, "hpms_roads", roads_step(build, client))
    run_step(build, "roads_by_tract", roads_by_tract_step(build, client))
    run_step(build, "overture_places", overture_places_step(build, client, profiles, places_reader))
    run_step(build, "lehd_daytime_workers", daytime_workers_step(build, client))
    if include_overpass:
        run_step(build, "osm_pois", osm_step(client))
    enhancement_steps(build)
    return build


def write_snapshot(build: SnapshotBuild, snapshots_dir: Path = SNAPSHOTS_DIR) -> Path:
    """Write manifest.json, tracts.json and the layer files; return the snapshot folder.

    Layer files from an earlier run of the same day that this run did not produce are deleted, so the folder never
    mixes two runs.
    """
    folder = snapshots_dir / build.metro.id / build.snapshot_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    for name in (PLACES_FILE, POI_COUNTS_FILE, ROADS_FILE):
        if name not in build.files and (folder / name).is_file():
            (folder / name).unlink()
    for name, body in build.files.items():
        write_atomic(folder / name, body)
    manifest = {
        "metro_id": build.metro.id,
        "snapshot_date": build.snapshot_date.isoformat(),
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": GENERATED_BY,
        "data_label": "PUBLIC",
        "cbsa_code": build.metro.cbsa_code,
        "key_status": key_status(),
        "steps": [step.as_json() for step in build.steps],
        "layers": build.layers,
        "metro_inputs": build.metro_inputs,
        "files": {name: sha256_bytes(body) for name, body in sorted(build.files.items())},
    }
    write_atomic(folder / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    tracts = {"tracts": build.tracts}
    write_atomic(folder / "tracts.json", (json.dumps(tracts, sort_keys=True) + "\n").encode("utf-8"))
    return folder


def main(argv: list[str] | None = None) -> int:
    """Build snapshots for one metro or all real metros; exit 1 when any step failed."""
    parser = argparse.ArgumentParser(description="Download Studio data snapshots (national layer, then enhancements).")
    parser.add_argument("--metro", required=True, help="a metro id, or 'all' for every real metro")
    parser.add_argument("--offline", action="store_true", help="answer only from the cache; never call the network")
    parser.add_argument(
        "--refresh", action="store_true", help="ignore cached answers: fetch every answer again and overwrite the cache"
    )
    parser.add_argument(
        "--with-overpass", action="store_true", help="also check the optional OpenStreetMap Overpass API (osm_pois)"
    )
    args = parser.parse_args(argv)
    if args.offline and args.refresh:
        parser.error("--offline and --refresh cannot be combined")
    metro_ids = list_metros() if args.metro == "all" else [args.metro]
    profiles = load_all_profiles()
    client = PoliteClient(offline=args.offline, refresh=args.refresh)
    today = datetime.now(UTC).date()
    failures: list[str] = []
    for metro_id in metro_ids:
        metro = load_metro(metro_id)
        if metro.data_label == "SYNTHETIC":
            print(f"{metro_id}: SYNTHETIC metro, generated from its seed; nothing to download")
            continue
        build = build_snapshot(metro, client, profiles, today, include_overpass=args.with_overpass)
        folder = write_snapshot(build)
        print(f"{metro_id}: snapshot written to {folder}")
        for step in build.steps:
            print(f"  {step.name}: {step.status}{' - ' + step.detail if step.detail else ''}")
        failures += build.failures()
    if failures:
        print("\nSome sources could not be fetched (the snapshot keeps what succeeded; the rest is UNKNOWN):")
        for line in failures:
            print(f"  - {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

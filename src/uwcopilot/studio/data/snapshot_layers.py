"""The snapshot layers that let SiteIQ rank every tract of a real metro: places, roads and daytime workers.

snapshot.py runs one step per layer and stores what each function here returns:

- build_places_layer ("overture_places" step): the metro's Overture places extract (overture_places.py), saved as
  places.json, plus the number of competitor, anchor and tourism places around every tract centroid, per business
  type, within the rings SiteIQ uses (poi_counts.json);
- build_roads_layer ("roads_by_tract" step): every HPMS segment with AADT in the metro's counties, saved as
  roads.npz (road_layer.py), plus each tract centroid's nearest segment within ROAD_SEARCH_MILES (stored per tract);
- build_daytime_workers ("lehd_daytime_workers" step): LODES jobs located in each tract (lodes.py), stored per tract.
  A tract with no block in the file stays UNKNOWN, except a water-only tract (Gazetteer land area 0), which gets 0
  with its basis recorded per tract (daytime_values).

Rings follow SiteIQ exactly (metros/rings.py, which SiteIQ uses): the profile's rings times the metro's multiplier;
competitors and anchors are counted in the NEAR ring, tourism anchors (and competitors, for saturation) in the MARKET
ring, and a place counts when its great-circle distance is at most the ring radius. The extract's bounding box is
the box around every tract centroid widened by the largest MARKET ring plus OWN_TRACT_MAX_MILES (a site may be that
far from its nearest centroid), so no ring around a centroid or a site of the metro reaches past the box.
"""

import io
import json
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from uwcopilot.studio.data import lodes, traffic_hpms
from uwcopilot.studio.data.cache import sha256_bytes
from uwcopilot.studio.data.errors import NotAvailable, UpstreamError
from uwcopilot.studio.data.fabric_base import ROAD_SEARCH_MILES
from uwcopilot.studio.data.http import PoliteClient
from uwcopilot.studio.data.overture_places import BBox, PlacesReader, extract_metro_places, summary
from uwcopilot.studio.data.places_layer import PlacesLayer
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.data.road_layer import RoadLayer
from uwcopilot.studio.data.snapshot_files import PLACES_FILE, POI_COUNTS_FILE, ROADS_FILE
from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.metros.rings import OWN_TRACT_MAX_MILES, near_and_market, scaled_rings
from uwcopilot.studio.profiles.factors import TOURISM_TAGS
from uwcopilot.studio.profiles.models import Profile


@dataclass
class LayerOutput:
    """What one layer step adds to the snapshot: sources, a detail line, layer facts, files and per-tract values."""

    sources: list[SourceRecord] = field(default_factory=list)
    detail: str = ""
    layer_info: dict[str, Any] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    tract_values: dict[str, dict[str, Any]] = field(default_factory=dict)


def tract_arrays(tracts: dict[str, dict[str, Any]]) -> tuple[list[str], np.ndarray, np.ndarray]:
    """(sorted tract ids, latitudes, longitudes); NotAvailable when the tract layer is missing."""
    ids = sorted(geoid for geoid, item in tracts.items() if "lat" in item and "lon" in item)
    if not ids:
        raise NotAvailable("no tract layer (the tract_geography step did not provide tract centroids)")
    lats = np.array([float(tracts[geoid]["lat"]) for geoid in ids])
    lons = np.array([float(tracts[geoid]["lon"]) for geoid in ids])
    return ids, lats, lons


def places_margin_miles(metro: MetroConfig, profiles: dict[str, Profile]) -> float:
    """The largest MARKET ring of any profile in this metro plus OWN_TRACT_MAX_MILES (see the module docstring)."""
    market = max(near_and_market(scaled_rings(profile, metro))[1] for profile in profiles.values())
    return round(market + OWN_TRACT_MAX_MILES, 3)


def factor_tags(profile: Profile) -> dict[str, tuple[list[str], str]]:
    """{stored factor: (tag filters, 'near' or 'market')}: what SiteIQ counts for each place factor."""
    return {
        "competition": (list(profile.competitor_tags), "near"),
        "competition_market": (list(profile.competitor_tags), "market"),
        "anchors": (list(profile.anchor_tags), "near"),
        "tourism": (list(TOURISM_TAGS), "market"),
    }


def place_counts_by_tract(
    layer: PlacesLayer, ids: list[str], lats: np.ndarray, lons: np.ndarray, metro: MetroConfig, profiles: dict
) -> dict[str, Any]:
    """Per business type and place factor: the count around every tract centroid, in the ring SiteIQ uses."""
    by_profile: dict[str, Any] = {}
    for profile_id, profile in profiles.items():
        rings = scaled_rings(profile, metro)
        near, market = near_and_market(rings)
        factors = {}
        for name, (tags, which) in factor_tags(profile).items():
            radius = near if which == "near" else market
            factors[name] = {
                "tags": tags,
                "ring": which,
                "ring_miles": radius,
                "unmapped_tags": layer.unmapped(tags),
                "definition_share": round(layer.definition_share(tags), 4),
                "counts": layer.counts_around(lats, lons, radius, tags).tolist(),
            }
        by_profile[profile_id] = {"rings_miles": list(rings), "factors": factors}
    return {"release": layer.release, "geoids": ids, "profiles": by_profile}


def build_places_layer(
    client: PoliteClient,
    metro: MetroConfig,
    tracts: dict[str, dict[str, Any]],
    profiles: dict[str, Profile],
    reader: PlacesReader | None = None,
) -> LayerOutput:
    """The Overture places extract of the metro's box and the per-tract place counts."""
    ids, lats, lons = tract_arrays(tracts)
    margin = places_margin_miles(metro, profiles)
    bbox = BBox.around(lats.tolist(), lons.tolist(), margin)
    started = time.monotonic()
    extract = extract_metro_places(client, metro.id, bbox, reader=reader)
    extract_seconds = round(time.monotonic() - started, 1)
    layer = PlacesLayer.from_payload(extract.payload, extract.record)
    counts = place_counts_by_tract(layer, ids, lats, lons, metro, profiles)
    counts_body = (json.dumps(counts, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    payload = extract.payload
    return LayerOutput(
        sources=[extract.record],
        detail=summary(payload),
        layer_info={
            "kind": "overture_places",
            "release": payload["release"],
            "file": PLACES_FILE,
            "sha256": extract.record.sha256,
            "counts_file": POI_COUNTS_FILE,
            "counts_sha256": sha256_bytes(counts_body),
            "bbox": payload["bbox"],
            "margin_miles": margin,
            "places_kept": len(layer),
            "accounting": payload["accounting"],
            "kept_by_license": payload["kept_by_license"],
            "kept_by_dataset": payload["kept_by_dataset"],
            "mapped_tags": payload["mapped_tags"],
            "unmapped_profile_tags": payload["unmapped_profile_tags"],
            "release_files_read": len(extract.files),
            "extract_origin": extract.record.origin,
            "extract_seconds": extract_seconds,
            "extract_seconds_note": "wall-clock time of this run on a shared machine (cache or live), not a benchmark",
        },
        files={PLACES_FILE: extract.body, POI_COUNTS_FILE: counts_body},
    )


def county_postal(metro: MetroConfig, postal_by_fips: dict[str, str]) -> list[tuple[str, str]]:
    """[(state postal code, county FIPS)] for every county of the metro."""
    return [(postal_by_fips[county.state_fips], county.county_fips) for county in metro.counties]


def build_roads_layer(
    client: PoliteClient, metro: MetroConfig, tracts: dict[str, dict[str, Any]], postal_by_fips: dict[str, str]
) -> LayerOutput:
    """Every county's HPMS segments with AADT, and each tract centroid's nearest segment within ROAD_SEARCH_MILES.

    The number of features downloaded per county must equal the layer's own count for the county, or the step
    fails (a partial download is never used).
    """
    ids, lats, lons = tract_arrays(tracts)
    segments: list[traffic_hpms.RoadSegment] = []
    source_index: list[int] = []
    sources: list[SourceRecord] = []
    skipped = 0
    for state, county in county_postal(metro, postal_by_fips):
        expected, _ = traffic_hpms.county_segment_count(client, state, county)
        downloaded = traffic_hpms.fetch_county_segments(client, state, county)
        if downloaded.features_read != expected:
            raise UpstreamError(
                traffic_hpms.HOST,
                f"{state} county {county}: {downloaded.features_read} segments downloaded but the layer counts "
                f"{expected}; the road layer is not used",
            )
        skipped += downloaded.features_read - len(downloaded.segments)
        segments += downloaded.segments
        source_index += [len(sources) + page for page in downloaded.page_of_segment]
        sources += downloaded.records
    layer = RoadLayer.from_segments(segments, source_index)
    buffer = io.BytesIO()
    layer.save(buffer)
    body = buffer.getvalue()
    values: dict[str, dict[str, Any]] = {}
    for geoid, lat, lon in zip(ids, lats, lons, strict=True):
        found = layer.nearest(float(lat), float(lon), ROAD_SEARCH_MILES)
        if found is None:
            values[geoid] = {"traffic_aadt": None, "traffic_segment": None, "traffic_miles": None}
        else:
            index, miles = found
            values[geoid] = {
                "traffic_aadt": int(layer.aadt[index]),
                "traffic_segment": index,
                "traffic_miles": round(miles, 4),
            }
    with_road = sum(item["traffic_aadt"] is not None for item in values.values())
    detail = (
        f"{len(segments)} HPMS segments with AADT in {len(metro.counties)} counties ({len(sources)} pages; "
        f"{skipped} without a shape skipped); {with_road} of {len(ids)} tract centroids have a segment within "
        f"{ROAD_SEARCH_MILES} miles"
    )
    return LayerOutput(
        sources=sources,
        detail=detail,
        layer_info={
            "kind": "fhwa_hpms_layer",
            "file": ROADS_FILE,
            "sha256": sha256_bytes(body),
            "segments": len(segments),
            "pages": len(sources),
            "segments_without_shape": skipped,
            "search_miles": ROAD_SEARCH_MILES,
            "tracts_with_road": with_road,
            "tracts_total": len(ids),
            "simplify_degrees": traffic_hpms.SIMPLIFY_DEGREES,
        },
        files={ROADS_FILE: body},
        tract_values=values,
    )


def daytime_values(
    ids: list[str], jobs: dict[str, int], tracts: dict[str, dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Per-tract daytime-worker values: the tract's LODES sum; else 0 for a water-only tract; else None (UNKNOWN).

    A water-only tract is one whose land area in the Gazetteer tract layer is exactly 0 and that has no block in the
    file: it has no land on which a workplace could be located, so 0 is recorded with its basis
    (lodes.WORKERS_BASIS_FIELD = lodes.WATER_ONLY_BASIS). Any other tract without a block stays None (UNKNOWN).
    Returns (values, the water-only tracts set to 0).
    """
    values: dict[str, dict[str, Any]] = {}
    water_only: list[str] = []
    for geoid in ids:
        if geoid in jobs:
            values[geoid] = {"daytime_workers": jobs[geoid]}
        elif tracts.get(geoid, {}).get("land_area_sq_miles") == 0:
            values[geoid] = {"daytime_workers": 0, lodes.WORKERS_BASIS_FIELD: lodes.WATER_ONLY_BASIS}
            water_only.append(geoid)
        else:
            values[geoid] = {"daytime_workers": None}
    return values, water_only


def build_daytime_workers(
    client: PoliteClient, metro: MetroConfig, tracts: dict[str, dict[str, Any]], postal_by_fips: dict[str, str]
) -> LayerOutput:
    """LODES jobs per tract for every tract of the metro (see daytime_values for tracts with no block)."""
    ids, _, _ = tract_arrays(tracts)
    county_geoids = set(metro.county_geoids())
    states = sorted({postal_by_fips[county.state_fips] for county in metro.counties})
    jobs: dict[str, int] = {}
    sources: list[SourceRecord] = []
    states_info: dict[str, Any] = {}
    for state in states:
        result = lodes.fetch_state_jobs(client, state, county_geoids)
        jobs.update(result.jobs.tract_jobs)
        sources += [result.record, *result.support_records]
        states_info[state] = {
            "file": lodes.wac_file_name(state),
            "url": lodes.wac_url(state),
            "sha256_gz": result.record.sha256,
            "sha256_csv": result.jobs.uncompressed_sha256,
            "published_sha256_csv": result.published_sha256,
            "vintage": result.vintage,
            "format_version": result.format_version,
            "rows_read": result.jobs.rows_read,
            "rows_in_metro_counties": result.jobs.rows_kept,
            "min_jobs_per_listed_block": result.jobs.min_jobs_in_file,
            "createdates": sorted(result.jobs.createdates),
            "citation": result.citation,
        }
    known = set(ids)
    values, water_only = daytime_values(ids, jobs, tracts)
    with_value = sum(geoid in jobs for geoid in ids)
    unknown = len(ids) - with_value - len(water_only)
    outside = sorted(set(jobs) - known)
    detail = (
        f"LODES 8 WAC {lodes.YEAR} ({lodes.SEGMENT}, {lodes.JOB_TYPE}, column {lodes.JOBS_COLUMN}) for "
        f"{', '.join(states)}: {with_value} of {len(ids)} tracts have jobs; {len(ids) - with_value} tracts have no "
        f"block in the file: {len(water_only)} water-only tracts (Gazetteer land area 0) set to 0, {unknown} left "
        f"UNKNOWN (not zero); files match the published SHA-256"
    )
    if outside:
        detail += f"; {len(outside)} tract codes in the file are not in the tract layer (ignored)"
    return LayerOutput(
        sources=sources,
        detail=detail,
        layer_info={
            "kind": "lehd_lodes8_wac",
            "year": lodes.YEAR,
            "segment": lodes.SEGMENT,
            "job_type": lodes.JOB_TYPE,
            "column": lodes.JOBS_COLUMN,
            "meaning": "jobs located in the tract (all jobs; one person can hold more than one job)",
            "states": states_info,
            "tracts_with_value": with_value,
            "tracts_without_block": len(ids) - with_value,
            "water_only_tracts_set_to_zero": water_only,
            "tracts_unknown": unknown,
            "tract_codes_not_in_layer": len(outside),
        },
        tract_values=values,
    )

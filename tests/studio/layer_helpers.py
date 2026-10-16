"""Hand-built geometry for the snapshot-layer tests: places, roads and a small snapshot folder written with the code.

Nothing here is recorded data. Positions are offsets in miles from a fixed origin (converted with geo.miles_to_degrees),
so every expected count and distance in a test can be checked by hand.
"""

import gzip
import io
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from studio_helpers import FIXTURES

from uwcopilot.studio.data.geo import miles_to_degrees
from uwcopilot.studio.data.overture_config import load_category_map, load_settings
from uwcopilot.studio.data.overture_places import Accounting, BBox, DuckDbPlacesReader, TaggedPlace, build_payload
from uwcopilot.studio.data.places_layer import PlacesLayer
from uwcopilot.studio.data.road_layer import RoadLayer
from uwcopilot.studio.data.snapshot import SnapshotBuild, StepResult, write_snapshot
from uwcopilot.studio.data.snapshot_files import PLACES_FILE, ROADS_FILE
from uwcopilot.studio.data.traffic_hpms import RoadSegment
from uwcopilot.studio.metros import load_metro

ORIGIN_LAT = 29.76
ORIGIN_LON = -95.37
OVERTURE_SAMPLE = FIXTURES / "overture" / "places_sample_cc0.parquet"


class SampleReader(DuckDbPlacesReader):
    """The production DuckDB reader, pointed at the recorded local sample instead of the release URLs."""

    calls: int = 0

    def path_counts(self, files, bbox, min_confidence):
        SampleReader.calls += 1
        return super().path_counts([str(OVERTURE_SAMPLE)], bbox, min_confidence)

    def places(self, files, bbox, categories, min_confidence, excluded_status):
        return super().places([str(OVERTURE_SAMPLE)], bbox, categories, min_confidence, excluded_status)


def offset(north_miles: float, east_miles: float) -> tuple[float, float]:
    """(lat, lon) of a point north_miles north and east_miles east of the origin."""
    dlat, dlon = miles_to_degrees(ORIGIN_LAT, north_miles, east_miles)
    return ORIGIN_LAT + dlat, ORIGIN_LON + dlon


@dataclass(frozen=True)
class HandPlace:
    """A hand-placed place: offsets in miles, its profile tags and an Overture category name."""

    north: float
    east: float
    tags: tuple[str, ...]
    category: str


def places_payload(places: list[HandPlace]) -> dict[str, Any]:
    """An extract payload made by overture_places.build_payload from hand-placed places (release settings as pinned).

    Labelled SYNTHETIC: the places are hand-built test data, not recorded Overture rows.
    """
    tagged = []
    for item in places:
        lat, lon = offset(item.north, item.east)
        tagged.append(TaggedPlace(lon, lat, 0.9, item.tags, item.category, "hand-built", "hand-built"))
    tagged.sort(key=TaggedPlace.sort_key)
    accounting = Accounting(places_in_bbox=len(tagged), mapped=len(tagged), kept=len(tagged))
    box = BBox(ORIGIN_LON - 1, ORIGIN_LON + 1, ORIGIN_LAT - 1, ORIGIN_LAT + 1)
    return build_payload(tagged, accounting, load_settings(), load_category_map(), box, "0" * 64, "SYNTHETIC")


def places_layer(places: list[HandPlace]) -> PlacesLayer:
    """The PlacesLayer of a hand-built payload."""
    return PlacesLayer.from_payload(places_payload(places))


def straight_road(route: str, aadt: int, points: list[tuple[float, float]]) -> RoadSegment:
    """A segment through points given as (north, east) mile offsets."""
    return RoadSegment(route, None, aadt, 3, 2024, [offset(north, east) for north, east in points])


def road_layer(segments: list[RoadSegment]) -> RoadLayer:
    """A road layer whose segments all come from page 0."""
    return RoadLayer.from_segments(segments, [0] * len(segments))


def npz_bytes(layer: RoadLayer) -> bytes:
    """The layer as stored in a snapshot."""
    buffer = io.BytesIO()
    layer.save(buffer)
    return buffer.getvalue()


def lodes_gzip(csv_text: str) -> bytes:
    """A WAC body: the CSV text gzip-compressed (mtime fixed so the bytes are the same every run)."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
        handle.write(csv_text.encode("utf-8"))
    return buffer.getvalue()


def fixture_record(name: str) -> dict[str, Any]:
    """A recorded fixture file."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def write_hand_snapshot(
    root: Path,
    tracts: dict[str, dict[str, Any]],
    places: list[HandPlace] | None,
    roads: list[RoadSegment] | None,
    steps_ok: tuple[str, ...] = ("tract_geography", "acs_tracts", "lehd_daytime_workers"),
) -> Path:
    """A Houston snapshot folder with hand-built tracts and layers, written by snapshot.write_snapshot.

    The manifest is then relabelled SYNTHETIC (write_snapshot always writes PUBLIC, the label of real metros).
    """
    build = SnapshotBuild(metro=load_metro("houston_tx"), snapshot_date=date(2026, 10, 13))
    build.tracts = {geoid: dict(values) for geoid, values in tracts.items()}
    build.layers["state_postal_by_fips"] = {"48": "TX"}
    for name in steps_ok:
        build.steps.append(StepResult(name=name, detail="hand-built"))
    if places is not None:
        payload = places_payload(places)
        build.files[PLACES_FILE] = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
        build.layers["places"] = {"bbox": payload["bbox"], "release": payload["release"]}
        build.steps.append(StepResult(name="overture_places", detail="hand-built"))
    if roads is not None:
        layer = road_layer(roads)
        build.files[ROADS_FILE] = npz_bytes(layer)
        for item in build.tracts.values():
            found = layer.nearest(item["lat"], item["lon"], 0.5)
            item["traffic_segment"] = None if found is None else found[0]
            item["traffic_aadt"] = None if found is None else int(layer.aadt[found[0]])
        build.steps.append(StepResult(name="roads_by_tract", detail="hand-built"))
    folder = write_snapshot(build, root)
    manifest_file = folder / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest["data_label"] = "SYNTHETIC"
    manifest["note"] = "hand-built test snapshot (tests/studio/layer_helpers.py), not recorded data"
    manifest_file.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return folder

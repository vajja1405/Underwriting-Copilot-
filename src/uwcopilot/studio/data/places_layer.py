"""A metro's Overture places extract as arrays: find places near one point, or count them around many points.

    layer = PlacesLayer.from_payload(extract.payload, extract.record)
    layer.search(lat, lon, 1.5, ["amenity=car_wash"])          # [Poi], nearest first
    layer.counts_around(lats, lons, 1.5, ["amenity=car_wash"])  # one count per point
    layer.definition_share(["shop=supermarket", "highway=motorway_junction"])  # 0.5: one of two filters is mapped
    layer.covers(lat, lon, 1.5)   # False when the 1.5-mile circle reaches outside the extract's bounding box

The extract (overture_places.py) stores one compact row per kept place: longitude, latitude, confidence, the
indexes of its profile tags, and indexes into the category, dataset and license tables. This module reads it once
into NumPy arrays.

Tag filters follow the OpenStreetMap convention the profiles use (osm.tag_matches): "key=value" matches that tag
only; "key" or "key=*" matches any tag with that key. A filter that matches none of the extract's tags is
"unmapped" (for example highway=motorway_junction: no Overture places category means it); such a filter adds
nothing to a count, and definition_share() says which share of a factor's filters the layer can answer.

Distances are great-circle miles (geo.haversine_miles_array), the same measure SiteIQ's rings use. To keep a
metro-wide count fast, the places matching the filters are sorted by latitude once, and each point only measures
the places within its latitude band.

The extract holds only places inside its bounding box. A search circle that reaches outside the box would undercount,
so callers check covers() first and answer UNKNOWN instead of a count (circle_inside_box is the one rule, also used by
the coverage report).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from uwcopilot.studio.data.geo import haversine_miles_array, miles_to_degrees
from uwcopilot.studio.data.osm import Poi, tag_matches
from uwcopilot.studio.data.provenance import SourceRecord

COLUMNS: tuple[str, ...] = ("lon", "lat", "confidence", "tags", "category", "dataset", "license")
CATEGORY_TAG = "overture:category"


def circle_inside_box(lat: float, lon: float, radius_miles: float, bbox: Sequence[float] | None) -> bool:
    """True when the circle of radius_miles around (lat, lon) lies inside bbox [xmin, xmax, ymin, ymax] (degrees)."""
    if not bbox:
        return False
    xmin, xmax, ymin, ymax = (float(value) for value in bbox)
    dlat, dlon = miles_to_degrees(lat, radius_miles, radius_miles)
    return xmin <= lon - dlon and lon + dlon <= xmax and ymin <= lat - dlat and lat + dlat <= ymax


def tag_as_dict(tag: str) -> dict[str, str]:
    """'amenity=cafe' -> {'amenity': 'cafe'}; a key-only tag 'office' -> {'office': 'yes'}."""
    key, _, value = tag.partition("=")
    return {key: value or "yes"}


@dataclass(frozen=True)
class BandIndex:
    """Places matching one set of filters, sorted by latitude (for fast distance searches)."""

    indices: np.ndarray
    lats: np.ndarray
    lons: np.ndarray

    def candidates(self, lat: float, radius_miles: float) -> slice:
        """The slice of places whose latitude is within radius_miles of lat (a superset of the circle)."""
        dlat, _ = miles_to_degrees(lat, radius_miles, 0.0)
        start = int(np.searchsorted(self.lats, lat - dlat, side="left"))
        stop = int(np.searchsorted(self.lats, lat + dlat, side="right"))
        return slice(start, stop)


@dataclass
class PlacesLayer:
    """The kept places of one extract (no names, no ids: position, tags, category, dataset, license)."""

    lats: np.ndarray
    lons: np.ndarray
    confidence: np.ndarray
    place_tags: list[tuple[str, ...]]
    categories: list[str]
    datasets: list[str]
    licenses: list[str]
    layer_tags: tuple[str, ...]
    release: str
    record: SourceRecord | None = None
    unmapped_profile_tags: dict[str, str] = field(default_factory=dict)
    bbox: tuple[float, ...] = ()
    bands: dict[tuple[str, ...], BandIndex] = field(default_factory=dict, repr=False)

    @classmethod
    def from_payload(cls, payload: dict[str, Any], record: SourceRecord | None = None) -> "PlacesLayer":
        """Read an extract payload (as stored by overture_places.build_payload)."""
        if tuple(payload.get("columns", ())) != COLUMNS:
            raise ValueError(f"unexpected extract columns {payload.get('columns')}; expected {list(COLUMNS)}")
        rows = payload["rows"]
        tags = payload["tags"]
        return cls(
            lats=np.array([row[1] for row in rows], dtype=float),
            lons=np.array([row[0] for row in rows], dtype=float),
            confidence=np.array([row[2] for row in rows], dtype=float),
            place_tags=[tuple(tags[index] for index in row[3]) for row in rows],
            categories=[payload["categories"][row[4]] for row in rows],
            datasets=[payload["datasets"][row[5]] for row in rows],
            licenses=[payload["licenses"][row[6]] for row in rows],
            layer_tags=tuple(tags),
            release=str(payload["release"]),
            record=record,
            unmapped_profile_tags=dict(payload.get("unmapped_profile_tags") or {}),
            bbox=tuple(float(value) for value in payload.get("bbox") or ()),
        )

    def __len__(self) -> int:
        return len(self.lats)

    def covers(self, lat: float, lon: float, radius_miles: float) -> bool:
        """True when the whole search circle lies inside the extract's bounding box (see circle_inside_box)."""
        return circle_inside_box(lat, lon, radius_miles, self.bbox)

    def layer_tags_for(self, tag_filter: str) -> list[str]:
        """The extract's tags that one filter matches (empty = the filter is unmapped)."""
        return [tag for tag in self.layer_tags if tag_matches(tag_as_dict(tag), tag_filter)]

    def unmapped(self, tags: list[str]) -> list[str]:
        """The filters this layer cannot answer (no Overture category maps to them)."""
        return [tag for tag in tags if not self.layer_tags_for(tag)]

    def definition_share(self, tags: list[str]) -> float:
        """Share of the filters the layer can answer (1.0 = every filter is mapped)."""
        if not tags:
            return 0.0
        return (len(tags) - len(self.unmapped(tags))) / len(tags)

    def band(self, tags: list[str]) -> BandIndex:
        """The places carrying any tag the filters match, sorted by latitude (built once per filter set)."""
        key = tuple(sorted(set(tags)))
        if key not in self.bands:
            wanted = {tag for item in key for tag in self.layer_tags_for(item)}
            members = np.array(
                [index for index, place in enumerate(self.place_tags) if wanted.intersection(place)], dtype=int
            )
            order = np.argsort(self.lats[members], kind="stable") if len(members) else members
            members = members[order]
            self.bands[key] = BandIndex(indices=members, lats=self.lats[members], lons=self.lons[members])
        return self.bands[key]

    def search(self, lat: float, lon: float, radius_miles: float, tags: list[str]) -> list[Poi]:
        """Places with any of the tags within radius_miles of (lat, lon), nearest first."""
        band = self.band(tags)
        window = band.candidates(lat, radius_miles)
        distances = haversine_miles_array(lat, lon, band.lats[window], band.lons[window])
        found = []
        for offset in np.flatnonzero(distances <= radius_miles):
            index = int(band.indices[window][offset])
            found.append(self.poi(index, float(distances[offset])))
        found.sort(key=lambda poi: (poi.distance_miles or 0.0, poi.poi_id))
        return found

    def poi(self, index: int, distance_miles: float | None) -> Poi:
        """One place as a Poi: its tags as a dict plus its Overture category (no name: none is stored)."""
        tags: dict[str, str] = {}
        for tag in self.place_tags[index]:
            tags.update(tag_as_dict(tag))
        tags[CATEGORY_TAG] = self.categories[index]
        return Poi(
            poi_id=f"overture/{self.release}/{index}",
            lat=float(self.lats[index]),
            lon=float(self.lons[index]),
            tags=tags,
            distance_miles=distance_miles,
        )

    def counts_around(self, lats: np.ndarray, lons: np.ndarray, radius_miles: float, tags: list[str]) -> np.ndarray:
        """For each point, how many places with any of the tags lie within radius_miles (same rule as search)."""
        band = self.band(tags)
        counts = np.zeros(len(lats), dtype=int)
        for position, (lat, lon) in enumerate(zip(np.asarray(lats, float), np.asarray(lons, float), strict=True)):
            window = band.candidates(float(lat), radius_miles)
            distances = haversine_miles_array(float(lat), float(lon), band.lats[window], band.lons[window])
            counts[position] = int((distances <= radius_miles).sum())
        return counts

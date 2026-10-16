"""Trade areas: the distance rings (and optional drive-time areas) around a site.

Rings. Each profile lists rings in miles (config/studio/business_types/*.yaml, a DESIGN_CHOICE) and each metro
scales them (trade_area.ring_multiplier in config/studio/metros/*.yaml, also a DESIGN_CHOICE: tighter in dense
metros, wider in car-dependent ones). SiteIQ uses two of the scaled rings:
- the NEAR ring (the first ring) for counts of nearby places: competitors, anchors, short-term rental comparables;
- the MARKET ring (the middle ring) for population sums (residents, workers, households, renters), the income
  average and tourism anchors.

Drive time. When ORS_API_KEY is set and a live client is passed, the demand estimate uses an OpenRouteService
drive-time area instead of the market ring (isochrones.py). Scores always use rings, because every location in
the metro is ranked against the others measured the same way; drive-time areas for thousands of reference points
would cost thousands of requests.
"""

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from uwcopilot.studio.data.geo import haversine_miles_array, miles_to_degrees
from uwcopilot.studio.metros.rings import near_and_market, scaled_rings

# scaled_rings and near_and_market live in metros/rings.py (the data layer uses them too); re-exported for SiteIQ.
__all__ = ["Area", "circle_polygon", "drive_time_area", "near_and_market", "ring_area", "scaled_rings"]

# Drive-time minutes asked from OpenRouteService (near, market, outer). A design choice, not a fact.
DRIVE_MINUTES: tuple[int, ...] = (5, 10, 15)
MARKET_DRIVE_MINUTES = 10
# Vertices of the circle drawn for a ring in GeoJSON (a drawing choice).
CIRCLE_VERTICES = 48


@dataclass(frozen=True)
class Area:
    """A trade area around a site: a ring, or a drive-time polygon.

    search_miles is a radius that encloses the whole area (the ring radius, or the farthest polygon vertex); searches
    ask the data fabric for everything within it and then keep what contains() accepts.
    """

    kind: Literal["ring", "drive_time"]
    label: str
    lat: float
    lon: float
    search_miles: float
    polygon: tuple[tuple[float, float], ...] = ()

    def contains(self, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        """True for each point (arrays of degrees) inside the area."""
        lats = np.asarray(lats, dtype=float)
        lons = np.asarray(lons, dtype=float)
        inside = haversine_miles_array(self.lat, self.lon, lats, lons) <= self.search_miles
        if self.kind == "ring":
            return inside
        return inside & points_in_polygon(lats, lons, self.polygon)


def miles_label(miles: float) -> str:
    """'within 1 mile', 'within 1.5 miles'."""
    return f"within {miles:g} mile" + ("" if miles == 1 else "s")


def ring_area(lat: float, lon: float, miles: float) -> Area:
    """A distance ring of `miles` around the site."""
    return Area(kind="ring", label=miles_label(miles), lat=lat, lon=lon, search_miles=miles)


def drive_time_area(lat: float, lon: float, minutes: int, polygon: list[tuple[float, float]]) -> Area:
    """A drive-time area from an isochrone polygon [(lat, lon), ...]."""
    if len(polygon) < 3:
        raise ValueError("a drive-time polygon needs at least three vertices")
    lats = np.array([point[0] for point in polygon])
    lons = np.array([point[1] for point in polygon])
    farthest = float(haversine_miles_array(lat, lon, lats, lons).max())
    return Area(
        kind="drive_time",
        label=f"within a {minutes}-minute drive",
        lat=lat,
        lon=lon,
        search_miles=farthest,
        polygon=tuple(polygon),
    )


def points_in_polygon(lats: np.ndarray, lons: np.ndarray, polygon: tuple[tuple[float, float], ...]) -> np.ndarray:
    """Ray casting: True for each point inside the polygon [(lat, lon), ...] (edges between consecutive vertices).

    For each edge, count whether a ray going east from the point crosses it; an odd number of crossings means
    inside. Fine for the few-mile polygons of a trade area (latitude/longitude treated as flat).
    """
    inside = np.zeros(len(lats), dtype=bool)
    count = len(polygon)
    for index in range(count):
        lat1, lon1 = polygon[index]
        lat2, lon2 = polygon[(index + 1) % count]
        spans = (lat1 > lats) != (lat2 > lats)
        with np.errstate(divide="ignore", invalid="ignore"):
            crossing_lon = lon1 + (lats - lat1) * (lon2 - lon1) / (lat2 - lat1)
        inside ^= spans & (lons < crossing_lon)
    return inside


def circle_polygon(lat: float, lon: float, miles: float, vertices: int = CIRCLE_VERTICES) -> list[list[float]]:
    """A closed GeoJSON ring [[lon, lat], ...] approximating a circle of `miles` around (lat, lon)."""
    points: list[list[float]] = []
    for step in range(vertices):
        angle = 2 * math.pi * step / vertices
        dlat, dlon = miles_to_degrees(lat, miles * math.sin(angle), miles * math.cos(angle))
        points.append([round(lon + dlon, 6), round(lat + dlat, 6)])
    points.append(points[0])
    return points

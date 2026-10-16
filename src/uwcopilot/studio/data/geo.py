"""Small geometry helpers in plain NumPy: distances in miles, and the distance from a point to a road polyline.

Distances use the haversine formula on a sphere of radius EARTH_RADIUS_MILES. That is accurate to well under one
percent at city scale, which is far finer than anything Studio scores. Point-to-line distances use a local flat
(equirectangular) projection around the point, which is accurate over the few miles a trade area covers.
"""

import math

import numpy as np

# Mean Earth radius. A modeling constant for distance calculations, not a measured project number.
EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two latitude/longitude points (degrees)."""
    return float(haversine_miles_array(lat1, lon1, np.array([lat2]), np.array([lon2]))[0])


def haversine_miles_array(lat: float, lon: float, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Distances in miles from one point to many points (arrays of degrees)."""
    lat1 = math.radians(lat)
    lat2 = np.radians(np.asarray(lats, dtype=float))
    dlat = lat2 - lat1
    dlon = np.radians(np.asarray(lons, dtype=float) - lon)
    a = np.sin(dlat / 2) ** 2 + math.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def miles_to_degrees(lat: float, north_miles: float, east_miles: float) -> tuple[float, float]:
    """(delta latitude, delta longitude) in degrees for a small offset in miles at latitude lat."""
    miles_per_degree_lat = math.radians(1.0) * EARTH_RADIUS_MILES
    miles_per_degree_lon = miles_per_degree_lat * math.cos(math.radians(lat))
    return north_miles / miles_per_degree_lat, east_miles / miles_per_degree_lon


def point_to_polyline_miles(lat: float, lon: float, line: list[tuple[float, float]]) -> float:
    """Shortest distance in miles from a point to a polyline given as [(lat, lon), ...] (local flat projection)."""
    if not line:
        raise ValueError("a polyline needs at least one point")
    miles_per_degree_lat = math.radians(1.0) * EARTH_RADIUS_MILES
    miles_per_degree_lon = miles_per_degree_lat * math.cos(math.radians(lat))
    # Put the site at (0, 0) and every vertex at (x east, y north) in miles.
    points = [((p_lon - lon) * miles_per_degree_lon, (p_lat - lat) * miles_per_degree_lat) for p_lat, p_lon in line]
    if len(points) == 1:
        return math.hypot(*points[0])
    best = math.inf
    for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
        best = min(best, distance_to_segment(x1, y1, x2, y2))
    return best


def point_to_segments_miles(
    lat: float, lon: float, lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Distances in miles from one point to many straight pieces (start and end arrays of degrees).

    The same math as point_to_polyline_miles, for many pieces at once: a local flat projection around the point,
    then the distance from the origin to each piece, with the projection clamped to the piece's ends.
    """
    miles_per_degree_lat = math.radians(1.0) * EARTH_RADIUS_MILES
    miles_per_degree_lon = miles_per_degree_lat * math.cos(math.radians(lat))
    x1 = (np.asarray(lon1, dtype=float) - lon) * miles_per_degree_lon
    y1 = (np.asarray(lat1, dtype=float) - lat) * miles_per_degree_lat
    dx = (np.asarray(lon2, dtype=float) - lon) * miles_per_degree_lon - x1
    dy = (np.asarray(lat2, dtype=float) - lat) * miles_per_degree_lat - y1
    length_sq = dx * dx + dy * dy
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(length_sq > 0, -(x1 * dx + y1 * dy) / length_sq, 0.0)
    t = np.clip(t, 0.0, 1.0)
    return np.hypot(x1 + t * dx, y1 + t * dy)


def distance_to_segment(x1: float, y1: float, x2: float, y2: float) -> float:
    """Distance from the origin (0, 0) to the segment from (x1, y1) to (x2, y2), in the same units."""
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(x1, y1)
    # t is where the origin projects onto the segment's line: 0 at the start, 1 at the end; clamp to the segment.
    t = max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length_sq))
    return math.hypot(x1 + t * dx, y1 + t * dy)

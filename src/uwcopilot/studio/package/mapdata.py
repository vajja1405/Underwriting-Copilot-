"""The static map's data: a GeoJSON FeatureCollection built from the metro's tract centroids (no tiles, no polygons).

The package draws its location map with matplotlib from this layer (pdf_figures.py), so it needs no tile provider
and no network. The layer holds:
- one Point per tract centroid within MAP_WINDOW_RINGS market rings of the site (at most MAX_TRACTS, nearest first),
  with the fifth of the window's tracts its median household income falls in (1 = lowest fifth, 5 = highest;
  None when the tract's value is UNKNOWN: it is drawn hollow, never filled in);
- the site as a Point;
- the near and market rings SiteIQ used, as Polygons (trade_area.circle_polygon).
Tract boundaries are not loaded in this project, so tracts are points. A metro without tract data here gets no map
(limited mode); the package says so instead of drawing one.
"""

import numpy as np

from uwcopilot.studio.data.fabric_base import DataFabric
from uwcopilot.studio.package.models import MapLayer
from uwcopilot.studio.siteiq.models import SiteScore
from uwcopilot.studio.siteiq.trade_area import circle_polygon

MAP_WINDOW_RINGS = 3.0
MIN_WINDOW_MILES = 2.0
MAX_TRACTS = 600
COLOR_FACTOR = "median_household_income"
COLOR_LABEL = "median household income of the tract"
FIFTHS = 5


def fifths(values: list[float | None]) -> list[int | None]:
    """The fifth (1-5) of the known values each value falls in; None stays None."""
    known = np.array([value for value in values if value is not None], dtype=float)
    if known.size == 0:
        return [None] * len(values)
    edges = np.quantile(known, [step / FIFTHS for step in range(1, FIFTHS)])
    return [None if value is None else int(np.searchsorted(edges, value, side="right")) + 1 for value in values]


def point(lon: float, lat: float, properties: dict) -> dict:
    """A GeoJSON Point feature."""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": properties,
    }


def ring(lat: float, lon: float, miles: float, kind: str) -> dict:
    """A GeoJSON Polygon feature approximating a circle of `miles` around the site."""
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [circle_polygon(lat, lon, miles)]},
        "properties": {"kind": kind},
    }


def build_map_layer(score: SiteScore | None, fabric: DataFabric) -> MapLayer | None:
    """The map layer of the chosen site (module docstring); None without a SiteIQ result or tract data."""
    if score is None:
        return None
    area = score.trade_area
    window = max(area.market_miles * MAP_WINDOW_RINGS, MIN_WINDOW_MILES)
    nearby = fabric.tracts_within(score.lat, score.lon, window)[:MAX_TRACTS]
    if not nearby:
        return None
    table = fabric.tract_table()
    values = [table.get(geoid, {}).get(COLOR_FACTOR) for geoid, _ in nearby]
    groups = fifths(values)
    features = []
    for (geoid, _), group in zip(nearby, groups, strict=True):
        record = fabric.tract(geoid)
        features.append(point(record.lon, record.lat, {"kind": "tract", "geoid": geoid, "fifth": group}))
    features.append(ring(score.lat, score.lon, area.market_miles, "market_ring"))
    features.append(ring(score.lat, score.lon, area.near_miles, "near_ring"))
    features.append(point(score.lon, score.lat, {"kind": "site"}))
    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "metro_id": score.metro_id,
            "data_label": score.data_label,
            "geometry": "tract centroids (points); tract boundaries are not loaded; no map tiles",
            "colored_by": COLOR_FACTOR,
        },
    }
    return MapLayer(
        geojson=geojson,
        site_lat=score.lat,
        site_lon=score.lon,
        value_label=COLOR_LABEL,
        near_ref="PKG.map.near_miles",
        market_ref="PKG.map.market_miles",
    )

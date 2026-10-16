"""A metro's HPMS road segments with traffic counts, stored in its snapshot, for nearest-road lookups without a network.

    layer = RoadLayer.from_segments(segments, source_index)   # built by the snapshot's roads_by_tract step
    layer.save(folder / "roads.npz"); layer = RoadLayer.load(folder / "roads.npz")
    found = layer.nearest(lat, lon, max_miles=0.5)              # (segment index, miles) or None
    layer.segment(found[0])                                     # traffic_hpms.RoadSegment

Why store the layer: SiteIQ ranks a site against the same factor measured at every tract centroid of the metro.
For traffic that means "AADT of the nearest HPMS segment within 0.5 miles" at thousands of points. Asking the
FeatureServer once per point would be thousands of requests; downloading each county's segments once (a few hundred
pages for the five metros) and searching locally gives the same answer for any point, offline.

The nearest-segment rule is the one the SYNTHETIC fabric and the live lookup use: the shortest distance from the
point to a segment's polyline (geo.point_to_segments_miles, a local flat projection), among segments with AADT > 0,
within max_miles. Ties go to the lower segment index (segments are stored in county, then objectid order).

Storage: one compressed NumPy file (.npz, no pickled objects). Segment i's vertices are vertex_lat/vertex_lon
[offsets[i]:offsets[i + 1]]; source_index[i] is the position of the HPMS page that returned it in the snapshot
step's SourceRecord list, so every value traces back to one response (URL, query, request date, SHA-256).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import numpy as np

from uwcopilot.studio.data.geo import miles_to_degrees, point_to_segments_miles
from uwcopilot.studio.data.traffic_hpms import RoadSegment

ARRAYS: tuple[str, ...] = (
    "aadt",
    "functional_class",
    "data_year",
    "route_id",
    "route_name",
    "offsets",
    "vertex_lat",
    "vertex_lon",
    "source_index",
)
MISSING_INT = -1


@dataclass
class RoadLayer:
    """Segments as arrays, plus the straight pieces between consecutive vertices (built on load)."""

    aadt: np.ndarray
    functional_class: np.ndarray
    data_year: np.ndarray
    route_id: np.ndarray
    route_name: np.ndarray
    offsets: np.ndarray
    vertex_lat: np.ndarray
    vertex_lon: np.ndarray
    source_index: np.ndarray
    pieces: dict[str, np.ndarray] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.pieces = build_pieces(self.offsets, self.vertex_lat, self.vertex_lon)

    def __len__(self) -> int:
        return len(self.aadt)

    @classmethod
    def from_segments(cls, segments: list[RoadSegment], source_index: list[int]) -> "RoadLayer":
        """Pack segments (each with at least one vertex) and the SourceRecord position of each."""
        if len(segments) != len(source_index):
            raise ValueError("one source index per segment is needed")
        lengths = [len(segment.line) for segment in segments]
        if any(length == 0 for length in lengths):
            raise ValueError("every segment needs at least one vertex")
        offsets = np.concatenate([[0], np.cumsum(lengths)]).astype(np.int64)
        return cls(
            aadt=np.array([segment.aadt for segment in segments], dtype=np.int64),
            functional_class=np.array([as_int(segment.functional_class) for segment in segments], dtype=np.int16),
            data_year=np.array([as_int(segment.data_year) for segment in segments], dtype=np.int16),
            route_id=np.array([segment.route_id for segment in segments], dtype=str),
            route_name=np.array([segment.route_name or "" for segment in segments], dtype=str),
            offsets=offsets,
            vertex_lat=np.array([point[0] for segment in segments for point in segment.line], dtype=float),
            vertex_lon=np.array([point[1] for segment in segments for point in segment.line], dtype=float),
            source_index=np.array(source_index, dtype=np.int32),
        )

    def save(self, path: Path | BinaryIO) -> None:
        """Write the arrays to one compressed .npz file (a path or an open binary file)."""
        np.savez_compressed(path, **{name: getattr(self, name) for name in ARRAYS})

    @classmethod
    def load(cls, path: Path) -> "RoadLayer":
        """Read a layer written by save() (pickled objects are refused)."""
        with np.load(path, allow_pickle=False) as data:
            missing = [name for name in ARRAYS if name not in data.files]
            if missing:
                raise ValueError(f"{path.name} is missing arrays {missing}")
            return cls(**{name: data[name] for name in ARRAYS})

    def segment(self, index: int) -> RoadSegment:
        """Segment `index` as a RoadSegment."""
        start, stop = int(self.offsets[index]), int(self.offsets[index + 1])
        name = str(self.route_name[index])
        return RoadSegment(
            route_id=str(self.route_id[index]),
            route_name=name or None,
            aadt=int(self.aadt[index]),
            functional_class=from_int(self.functional_class[index]),
            data_year=from_int(self.data_year[index]),
            line=list(zip(self.vertex_lat[start:stop].tolist(), self.vertex_lon[start:stop].tolist(), strict=True)),
        )

    def distance_to(self, index: int, lat: float, lon: float) -> float:
        """Distance in miles from a point to segment `index` (the same measure nearest() minimizes)."""
        start, stop = int(self.offsets[index]), int(self.offsets[index + 1])
        lats, lons = self.vertex_lat[start:stop], self.vertex_lon[start:stop]
        if len(lats) == 1:
            return float(point_to_segments_miles(lat, lon, lats, lons, lats, lons)[0])
        return float(point_to_segments_miles(lat, lon, lats[:-1], lons[:-1], lats[1:], lons[1:]).min())

    def nearest(self, lat: float, lon: float, max_miles: float) -> tuple[int, float] | None:
        """(segment index, distance in miles) of the nearest segment within max_miles, or None."""
        pieces = self.pieces
        dlat, dlon = miles_to_degrees(lat, max_miles, max_miles)
        near = np.flatnonzero(
            (pieces["max_lat"] >= lat - dlat)
            & (pieces["min_lat"] <= lat + dlat)
            & (pieces["max_lon"] >= lon - dlon)
            & (pieces["min_lon"] <= lon + dlon)
        )
        if len(near) == 0:
            return None
        distances = point_to_segments_miles(
            lat, lon, pieces["lat1"][near], pieces["lon1"][near], pieces["lat2"][near], pieces["lon2"][near]
        )
        segments = pieces["segment"][near]
        order = np.lexsort((segments, distances))
        best = order[0]
        if distances[best] > max_miles:
            return None
        return int(segments[best]), float(distances[best])


def build_pieces(offsets: np.ndarray, lats: np.ndarray, lons: np.ndarray) -> dict[str, np.ndarray]:
    """Every straight piece between consecutive vertices of a segment (a one-vertex segment gives a zero piece).

    Vertex v starts a piece (v, v + 1) unless it is the last vertex of its segment; a segment with one vertex
    gets the piece (v, v), a single point.
    """
    lengths = np.diff(offsets)
    segment_of_vertex = np.repeat(np.arange(len(lengths)), lengths)
    vertices = np.arange(len(lats))
    last_vertex = offsets[1:][segment_of_vertex] - 1
    starts = vertices[vertices < last_vertex]
    singles = offsets[:-1][lengths == 1]
    first = np.concatenate([starts, singles]).astype(np.int64)
    second = np.concatenate([starts + 1, singles]).astype(np.int64)
    order = np.argsort(first, kind="stable")
    first, second = first[order], second[order]
    lat1, lon1, lat2, lon2 = lats[first], lons[first], lats[second], lons[second]
    return {
        "segment": segment_of_vertex[first] if len(first) else np.zeros(0, dtype=np.int64),
        "lat1": lat1,
        "lon1": lon1,
        "lat2": lat2,
        "lon2": lon2,
        "min_lat": np.minimum(lat1, lat2),
        "max_lat": np.maximum(lat1, lat2),
        "min_lon": np.minimum(lon1, lon2),
        "max_lon": np.maximum(lon1, lon2),
    }


def as_int(value: int | None) -> int:
    """An optional integer as an int (MISSING_INT for None)."""
    return MISSING_INT if value is None else int(value)


def from_int(value: np.integer) -> int | None:
    """Back from as_int."""
    number = int(value)
    return None if number == MISSING_INT else number

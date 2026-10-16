"""The layer files a snapshot folder can hold next to manifest.json and tracts.json, and how they are read back.

    places.json      the Overture places extract (overture_places.py; read with places_layer.PlacesLayer)
    poi_counts.json  places around every tract centroid per business type and place factor (snapshot_layers.py)
    roads.npz        the HPMS road segments with AADT (road_layer.RoadLayer)

The manifest lists each file with its SHA-256 ("files"). A file is used only when its bytes still match that
SHA-256; a changed or missing file raises SnapshotFileError and the fabric then marks the layer UNKNOWN with the
reason (it never falls back to a guessed value).
"""

import json
from pathlib import Path
from typing import Any

from uwcopilot.studio.data.cache import sha256_bytes
from uwcopilot.studio.data.errors import StudioDataError

PLACES_FILE = "places.json"
POI_COUNTS_FILE = "poi_counts.json"
ROADS_FILE = "roads.npz"


class SnapshotFileError(StudioDataError):
    """A layer file listed in the manifest is missing or no longer matches its recorded SHA-256."""


def checked_bytes(folder: Path, manifest: dict[str, Any], name: str) -> bytes:
    """The file's bytes, after checking them against the manifest's SHA-256."""
    expected = (manifest.get("files") or {}).get(name)
    if expected is None:
        raise SnapshotFileError(f"{name} is not listed in the snapshot manifest")
    path = folder / name
    if not path.is_file():
        raise SnapshotFileError(f"{name} is listed in the manifest but missing from {folder}")
    body = path.read_bytes()
    if sha256_bytes(body) != expected:
        raise SnapshotFileError(f"{name} does not match the SHA-256 recorded in the manifest; rebuild the snapshot")
    return body


def checked_json(folder: Path, manifest: dict[str, Any], name: str) -> dict[str, Any]:
    """A checked JSON layer file as a dict."""
    return json.loads(checked_bytes(folder, manifest, name).decode("utf-8"))

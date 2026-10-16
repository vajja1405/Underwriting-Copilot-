"""Record one OpenRouteService isochrones answer as a test fixture (run by hand with ORS_API_KEY set; never in CI).

    uv run python tests/studio/fixtures/ors/record_isochrones.py

The point is the Census Geocoder's answer for a fixed street address (record_fixtures.PUBLIC_ADDRESS), read from the
recorded fixture tests/studio/fixtures/geocoder/coordinates.json rather than typed in. The request body is built by
the project's own studio.siteiq.isochrones.isochrone_payload, so the fixture checks the real request and the real
answer shape.

The key travels only in the Authorization header. It is never written to the fixture: the stored url has no query
string, params holds the JSON body, and the script refuses to save a body that contains the key.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from uwcopilot.studio.data.keys import require_key
from uwcopilot.studio.siteiq.isochrones import ISOCHRONES_URL, isochrone_payload
from uwcopilot.studio.siteiq.trade_area import DRIVE_MINUTES

HERE = Path(__file__).resolve().parent
GEOCODER_FIXTURE = HERE.parent / "geocoder" / "coordinates.json"
OUTPUT = HERE / "isochrones_houston_recorded.json"
SCRIPT = "tests/studio/fixtures/ors/record_isochrones.py"
TIMEOUT_S = 60.0


def public_point() -> tuple[float, float]:
    """(lat, lon) of the public building, as stored in the recorded Census Geocoder fixture."""
    params = json.loads(GEOCODER_FIXTURE.read_text(encoding="utf-8"))["params"]
    return float(params["y"]), float(params["x"])


def main() -> int:
    """Request the drive-time areas once and write the fixture."""
    api_key = require_key("ORS_API_KEY")
    lat, lon = public_point()
    payload = isochrone_payload(lat, lon, DRIVE_MINUTES)
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    with httpx.Client(timeout=TIMEOUT_S, trust_env=True) as client:
        response = client.post(ISOCHRONES_URL, json=payload, headers=headers)
    response.raise_for_status()
    body = response.text
    if api_key in body:
        raise RuntimeError("the answer contains the key; not saving it")
    now = datetime.now(UTC)
    record = {
        "fixture_origin": f"recorded from the live API on {now.date().isoformat()} by {SCRIPT}",
        "note": (
            "One POST to the openrouteservice v2 isochrones endpoint (driving-car) around the Census Geocoder's "
            "point for the street address 901 Bagby St, Houston, TX 77002, ranges 5, 10 and 15 minutes. The key was "
            "sent in the Authorization header only and is not stored."
        ),
        "url": ISOCHRONES_URL,
        "params": {"json": payload},
        "recorded_at_utc": now.isoformat(timespec="seconds"),
        "content_type": response.headers.get("content-type", ""),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "full_body_sha256": hashlib.sha256(response.content).hexdigest(),
        "body": body,
    }
    OUTPUT.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(HERE.parent)} ({len(body)} characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

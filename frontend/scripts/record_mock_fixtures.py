"""Record the fixtures the frontend's mock API (frontend/mock-api/server.mjs) replays, from the REAL backend.

    UWC_SCREENING_LISTS=ofac uv run python frontend/scripts/record_mock_fixtures.py

Why: the frontend must render (and be screenshot-tested) without a running backend. Instead of typing fixtures by
hand, this script starts the real FastAPI app in-process (uwcopilot.web.app.create_app with FastAPI's TestClient: no
port, no network), signs in through the dev login as a SYNTHETIC demo founder, calls the routes the pages use, and
saves every answer as it came back. So every number in the fixtures was computed by the project's own code (SiteIQ,
the feasibility simulator, the path search, the agent with its template memo) on SYNTHETIC inputs.

What it records (frontend/mock-api/fixtures/):
- Studio: config, options, the SYNTHETIC metro's tract layer and area suggestions, one
  SYNTHETIC demo plan with three candidate sites: its site scores, the simulation and the path search for each site,
  the path search again with two sets of edited planning targets, then "Apply this change" (PUT) and the version-2
  results for the applied site.
- Lender Desk: the demo queue, three demo files (a startup loan, a business credit file, an insurance file), every
  guideline section they cite, a refused decision (422) and an accepted one with its decision record.

The plan's inputs (budget, equity, loan, term, site picks) are the demo founder's STATED inputs, chosen to exercise
the pages; they are not results. Settings: dev login on, a throw-away local store, the repo .env file skipped.
The run needs the repo's data for the agent (the guideline index and the OFAC list files, `make data-regulatory
index` and `make kyc-data`); if a tool is missing, the recorded file says "unavailable", as the real app would.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from uwcopilot.web.app import create_app
from uwcopilot.web.settings import load_web_settings

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "frontend" / "mock-api" / "fixtures"

DEMO_METRO = "synthetic_metro"
DEMO_PROFILE = "laundromat"
DEMO_SITE_TRACTS = ("SYN000150", "SYN000010")
DEMO_PLAN: dict[str, Any] = {
    "name": "Demo laundromat (SYNTHETIC)",
    "metro_id": DEMO_METRO,
    "business_type_id": DEMO_PROFILE,
    "idea_text": (
        "A self-service laundromat near apartment blocks, with an attendant a few hours a day. (SYNTHETIC demo.)"
    ),
    "budget_total": 500_000,
    "owner_equity": 200_000,
    "loan_amount": 400_000,
    "loan_term_months": 120,
    "annual_rate_pct": None,
}
# STATED edits to the path search's planning targets (the shares of simulated futures), tried in this order until one
# set lets the search find a plan change and one set is already met, so the mock can show both page states. The
# simulation's seed depends on the plan id, which is new on every recording, so the set that works can change.
# These are inputs, not results; every set tried is saved with the server's answer.
CANDIDATE_TARGETS: tuple[dict[str, float], ...] = (
    {"p_dscr_min": 0.75, "p_stressed_min": 0.5, "p_cash_negative_max": 0.15},
    {"p_dscr_min": 0.7, "p_stressed_min": 0.5, "p_cash_negative_max": 0.2},
    {"p_dscr_min": 0.7, "p_stressed_min": 0.45, "p_cash_negative_max": 0.2},
    {"p_dscr_min": 0.65, "p_stressed_min": 0.45, "p_cash_negative_max": 0.2},
    {"p_dscr_min": 0.65, "p_stressed_min": 0.4, "p_cash_negative_max": 0.25},
    {"p_dscr_min": 0.6, "p_stressed_min": 0.4, "p_cash_negative_max": 0.3},
    {"p_dscr_min": 0.5, "p_stressed_min": 0.35, "p_cash_negative_max": 0.35},
)
LENDER_FILES = ("APP-S-001", "APP-C-008", "APP-I-001")
REFUSED_DECISION = {"action": "approve", "rationale": "Trying an approval as an underwriter (SYNTHETIC demo)."}
ACCEPTED_DECISION = {
    "action": "request_more_information",
    "rationale": "Ask for the signed lease and the equipment quotes first (SYNTHETIC demo).",
}


class Recorder:
    """Calls routes on the in-process app and writes each answer to a fixture file."""

    def __init__(self, client: TestClient, out: Path) -> None:
        self.client = client
        self.out = out
        self.csrf = ""

    def sign_in(self, username: str, role: str) -> None:
        """Dev login (the app runs with UWC_DEV_AUTH=1)."""
        self.csrf = self.client.get("/api/auth/csrf").json()["token"]
        response = self.client.post(
            "/api/auth/dev-login", json={"username": username, "role": role}, headers={"X-CSRF-Token": self.csrf}
        )
        response.raise_for_status()
        self.csrf = self.client.get("/api/auth/csrf").json()["token"]

    def call(self, method: str, path: str, body: Any = None, expect: int = 200) -> Any:
        """One request; fails loudly when the status is not the expected one."""
        headers = {} if method == "GET" else {"X-CSRF-Token": self.csrf}
        response = self.client.request(method, path, json=body, headers=headers)
        if response.status_code != expect:
            raise RuntimeError(f"{method} {path}: expected {expect}, got {response.status_code}: {response.text[:500]}")
        return response.json()

    def save(self, name: str, payload: Any) -> None:
        """Write pretty JSON (sorted keys keep diffs small between recordings)."""
        path = self.out / name
        path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)} ({max(1, path.stat().st_size // 1024)} KB)")


def tract_point(tracts: dict[str, Any], geoid: str) -> tuple[float, float]:
    """(lat, lon) of a tract centroid in the recorded tract layer."""
    for feature in tracts["features"]:
        if feature["properties"]["geoid"] == geoid:
            lon, lat = feature["geometry"]["coordinates"]
            return float(lat), float(lon)
    raise KeyError(geoid)


def record_studio(rec: Recorder) -> None:
    """Options, maps, suggestions, the demo plan and its results, and one applied change."""
    rec.save("config.json", rec.call("GET", "/api/config"))
    rec.save("studio_options.json", rec.call("GET", "/api/studio/options"))
    tracts = rec.call("GET", f"/api/studio/metros/{DEMO_METRO}/tracts.geojson")
    rec.save(f"tracts_{DEMO_METRO}.json", tracts)
    suggest = rec.call("POST", "/api/studio/areas/suggest", {"metro_id": DEMO_METRO, "business_type_id": DEMO_PROFILE})
    rec.save(f"suggest_{DEMO_METRO}_{DEMO_PROFILE}.json", suggest)

    best = suggest["areas"][0]
    sites = [{"label": f"Suggested area 1 (tract {best['geoid']})", "lat": best["lat"], "lon": best["lon"]}]
    for geoid in DEMO_SITE_TRACTS:
        lat, lon = tract_point(tracts, geoid)
        sites.append({"label": f"Tract {geoid}", "lat": lat, "lon": lon})
    project = rec.call("POST", "/api/studio/projects", {**DEMO_PLAN, "sites": sites}, expect=201)
    rec.save("project_demo.json", project)
    rec.save("projects_list.json", rec.call("GET", "/api/studio/projects"))

    base = f"/api/studio/projects/{project['id']}"
    rec.save("scores_demo.json", rec.call("POST", f"{base}/sites/score", {}))
    paths = []
    for index in range(len(sites)):
        rec.save(f"feasibility_demo_{index}.json", rec.call("POST", f"{base}/feasibility", {"site_index": index}))
        path = rec.call("POST", f"{base}/path-to-approval", {"site_index": index})
        rec.save(f"path_demo_{index}.json", path)
        paths.append(path)

    edited = []
    wanted = {"paths_found", "already_meets_target"}
    for targets in CANDIDATE_TARGETS:
        result = rec.call("POST", f"{base}/path-to-approval", {"site_index": 0, "targets": targets})
        edited.append({"site_index": 0, "targets": targets, "result": result})
        wanted.discard(result["status"])
        if not wanted:
            break
    rec.save("path_demo_targets.json", edited)

    first = paths[0]
    change = (first["change_sets"] or [first["closest"]])[0]
    if change and change["apply"]["supported"]:
        apply = change["apply"]
        patch = {"version": project["version"], **apply["project_fields"], "plan_change": apply["plan_change"]}
        updated = rec.call("PUT", base, patch)
        rec.save("project_demo_v2.json", {"patch": patch, "project": updated})
        site = apply["site_index"]
        rec.save("scores_demo_v2.json", rec.call("POST", f"{base}/sites/score", {}))
        rec.save(f"feasibility_demo_v2_{site}.json", rec.call("POST", f"{base}/feasibility", {"site_index": site}))
        rec.save(f"path_demo_v2_{site}.json", rec.call("POST", f"{base}/path-to-approval", {"site_index": site}))


def record_lender(rec: Recorder) -> None:
    """The queue, three demo files, their cited sections, and two decisions (one refused, one recorded)."""
    rec.save("lender_queue.json", rec.call("GET", "/api/lender/queue"))
    cited: set[str] = set()
    for file_id in LENDER_FILES:
        view = rec.call("GET", f"/api/lender/files/{file_id}")
        rec.save(f"lender_file_{file_id}.json", view)
        cited.update(view["cited_sections"])
    sections = {section_id: rec.call("GET", f"/api/lender/sections/{section_id}") for section_id in sorted(cited)}
    rec.save("lender_sections.json", sections)

    first = LENDER_FILES[0]
    refused = rec.client.post(
        f"/api/lender/files/{first}/decision", json=REFUSED_DECISION, headers={"X-CSRF-Token": rec.csrf}
    )
    rec.save("lender_decision_refused.json", {"status": refused.status_code, "body": refused.json()})
    recorded = rec.call("POST", f"/api/lender/files/{first}/decision", ACCEPTED_DECISION)
    rec.save("lender_decision_recorded.json", recorded)
    rec.save("lender_record.json", rec.call("GET", f"/api/lender/files/{first}/record"))
    rec.save("lender_file_after_decision.json", rec.call("GET", f"/api/lender/files/{first}"))


def main() -> None:
    """Record every fixture."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--only", choices=["studio", "lender"], default=None)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as workdir:
        environ = {
            **os.environ,
            "UWC_DEV_AUTH": "1",
            "UWC_STORE_DIR": str(Path(workdir) / "store"),
            "UWC_STATIC_DIR": str(Path(workdir) / "out"),
            "UWC_SESSION_SECRET": secrets.token_urlsafe(32),
            "UWC_RATE_LIMIT_USER_BURST": "100000",
            "UWC_RATE_LIMIT_IP_BURST": "100000",
        }
        settings = load_web_settings(env_file=None, environ=environ)
        with TestClient(create_app(settings)) as client:
            rec = Recorder(client, args.out)
            rec.sign_in("demo-founder", "founder")
            if args.only in (None, "studio"):
                record_studio(rec)
            if args.only in (None, "lender"):
                record_lender(rec)


if __name__ == "__main__":
    main()

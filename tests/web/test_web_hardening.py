"""Hardening of the v0.1 API found by the review (2026-10-13): request size and validation answers, per-user limits,
the plan cap, input bounds, the site and plan-change bookkeeping, safe tool errors and the compute runner.

Every plan is SYNTHETIC test input on the SYNTHETIC metro. No network.
"""

import asyncio
import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from web_helpers import csrf_headers, dev_login

from uwcopilot.studio.siteiq.context import metro_context
from uwcopilot.tools.registry import ToolUnavailable
from uwcopilot.web.app import create_app
from uwcopilot.web.security import API_MAX_BODY_BYTES
from uwcopilot.web.services import projects, studio_options
from uwcopilot.web.services.compute import ComputeBusy, ComputeRunner
from uwcopilot.web.services.errors import InputError
from uwcopilot.web.services.http import MAX_VALIDATION_ERRORS
from uwcopilot.web.services.lender_demo import SafeTool, ToolFailed
from uwcopilot.web.services.settings import ServiceSettings
from uwcopilot.web.services.text_checks import refuse_identifiers, without_known_tract_ids

SYNTHETIC_METRO = "synthetic_metro"


def synthetic_sites(count: int = 2) -> list[dict[str, Any]]:
    """Map pins on tract centroids of the SYNTHETIC city."""
    tracts = metro_context(SYNTHETIC_METRO).tracts
    return [
        {"label": f"Site {index + 1}", "lat": float(tracts.lats[index]), "lon": float(tracts.lons[index])}
        for index in range(count)
    ]


def plan_body(**changes: Any) -> dict[str, Any]:
    """A SYNTHETIC laundromat plan (test inputs, not benchmarks)."""
    body: dict[str, Any] = {
        "metro_id": SYNTHETIC_METRO,
        "business_type_id": "laundromat",
        "budget_total": 400_000.0,
        "owner_equity": 150_000.0,
        "loan_amount": 350_000.0,
        "loan_term_months": 120,
        "annual_rate_pct": 9.0,
        "sites": synthetic_sites(),
    }
    body.update(changes)
    return body


@pytest.fixture
def studio(make_settings) -> Iterator[TestClient]:
    """A dev-login app signed in as a founder."""
    with TestClient(create_app(make_settings(UWC_DEV_AUTH="1"))) as client:
        dev_login(client, "founder_a", "founder")
        yield client


def create(client: TestClient, **changes: Any) -> dict[str, Any]:
    """POST a plan and return its JSON (asserting 201)."""
    response = client.post("/api/studio/projects", json=plan_body(**changes), headers=csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


# --- request size and validation answers ---


def test_a_large_json_body_is_refused_before_validation(studio: TestClient) -> None:
    padding = [[[1.5e300]]] * 200_000  # about 1.6 MB of list items
    body = json.dumps({**plan_body(), "pad": padding})
    assert len(body) > API_MAX_BODY_BYTES
    started = time.perf_counter()
    response = studio.post(
        "/api/studio/projects",
        content=body,
        headers={**csrf_headers(studio), "content-type": "application/json"},
    )
    assert response.status_code == 413 and time.perf_counter() - started < 2.0
    assert len(response.content) < 200


def test_validation_answers_never_echo_the_input_and_are_short(studio: TestClient) -> None:
    secret = "do-not-echo-this-value"
    body = {**plan_body(), **{f"extra_{index}": secret for index in range(1500)}}  # 1,500 errors, under the cap
    raw = json.dumps(body)
    assert len(raw) < API_MAX_BODY_BYTES
    response = studio.post(
        "/api/studio/projects", content=raw, headers={**csrf_headers(studio), "content-type": "application/json"}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert secret not in response.text and len(response.content) < 8_000
    assert len(detail) == MAX_VALIDATION_ERRORS + 1 and "more problem" in detail[-1]
    assert all(set(item) == {"type", "loc", "msg"} for item in detail[:-1])


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_a_422_not_a_500(studio: TestClient, literal: str) -> None:
    raw = json.dumps(plan_body()).replace('"owner_equity": 150000.0', f'"owner_equity": {literal}')
    assert literal in raw
    response = studio.post(
        "/api/studio/projects", content=raw, headers={**csrf_headers(studio), "content-type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "owner_equity"


def test_lender_decision_bodies_are_capped_too(studio: TestClient) -> None:
    raw = json.dumps({"action": "approve", "rationale": "x" * (API_MAX_BODY_BYTES + 10)})
    response = studio.post(
        "/api/lender/files/APP-C-001/decision",
        content=raw,
        headers={**csrf_headers(studio), "content-type": "application/json"},
    )
    assert response.status_code == 413


# --- the plan cap and the profile cache ---


def test_a_user_keeps_at_most_the_plan_cap(studio: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(projects, "MAX_PLANS_PER_USER", 2)
    create(studio)
    create(studio)
    response = studio.post("/api/studio/projects", json=plan_body(), headers=csrf_headers(studio))
    assert response.status_code == 409 and "delete one" in response.json()["detail"]
    dev_login(studio, "founder_b", "founder")  # another user is not affected
    create(studio)


def test_profiles_are_loaded_once_per_process() -> None:
    first = studio_options.profile_for("laundromat", SYNTHETIC_METRO)
    assert studio_options.profile_for("laundromat", SYNTHETIC_METRO) is first
    with pytest.raises(InputError):
        studio_options.profile_for("no_such_type", SYNTHETIC_METRO)


# --- input bounds and text checks ---


@pytest.mark.parametrize("opening", ["9999-12-01", "0001-01-01", "1900-01-01", "2200-01-01"])
def test_opening_dates_far_away_are_refused(studio: TestClient, opening: str) -> None:
    response = studio.post("/api/studio/projects", json=plan_body(opening_date=opening), headers=csrf_headers(studio))
    assert response.status_code == 422 and "opening_date must be between" in response.json()["detail"]


def test_a_near_opening_date_is_accepted_and_can_be_changed(studio: TestClient) -> None:
    project = create(studio, opening_date="2027-05-01")
    path = f"/api/studio/projects/{project['id']}"
    assert studio.put(path, json={"opening_date": "2027-08-01"}, headers=csrf_headers(studio)).status_code == 200
    assert studio.put(path, json={"opening_date": "2099-08-01"}, headers=csrf_headers(studio)).status_code == 422


def test_ssn_with_spaces_is_refused_in_free_text(studio: TestClient) -> None:
    response = studio.post(
        "/api/studio/projects", json=plan_body(idea_text="my ssn 123 45 6789"), headers=csrf_headers(studio)
    )
    assert response.status_code == 422 and "SSN" in response.json()["detail"]
    assert "123 45 6789" not in response.text


def test_known_tract_ids_are_allowed_in_site_names_but_other_long_numbers_are_not() -> None:
    known = ["17031010400"]
    refuse_identifiers(without_known_tract_ids("Tract 17031010400", known), "the name of site 1")
    refuse_identifiers(without_known_tract_ids("Suggested area 1 (tract 17031010400)", known), "the name of site 1")
    with pytest.raises(InputError, match="site 1"):
        refuse_identifiers(without_known_tract_ids("Tract 17031010499", known), "the name of site 1")


# --- the selected site and plan changes ---


def test_the_selected_site_is_stored_and_checked(studio: TestClient) -> None:
    project = create(studio)
    assert project["selected_site_index"] == 0 and project["selected_site_status"] == "USER_STATED"
    path = f"/api/studio/projects/{project['id']}"
    updated = studio.put(path, json={"selected_site_index": 1}, headers=csrf_headers(studio)).json()
    assert updated["selected_site_index"] == 1
    assert studio.get(path).json()["selected_site_index"] == 1  # it survives a reload
    assert studio.put(path, json={"selected_site_index": 2}, headers=csrf_headers(studio)).status_code == 422
    one_site = studio.put(path, json={"sites": synthetic_sites(1)}, headers=csrf_headers(studio)).json()
    assert one_site["selected_site_index"] == 0


def test_a_plan_change_must_name_fields_the_request_sets(studio: TestClient) -> None:
    project = create(studio)
    change = {"engine_version": "studio-path-to-approval-1.2", "fields": ["loan_amount"], "evidence_needed": []}
    response = studio.put(
        f"/api/studio/projects/{project['id']}",
        json={"owner_equity": 160_000.0, "plan_change": change},
        headers=csrf_headers(studio),
    )
    assert response.status_code == 422 and "loan_amount" in response.json()["detail"]


# --- safe tool errors (Lender Desk) ---


def test_a_failing_tool_shows_a_plain_sentence_not_the_exception() -> None:
    def broken(arguments: Any) -> Any:
        raise RuntimeError("Storage folder /home/user/app/qdrant_storage is already accessed by another instance")

    def unavailable(arguments: Any) -> Any:
        raise ToolUnavailable("the guideline index is not built on this server")

    with pytest.raises(ToolFailed) as caught:
        SafeTool("guideline search", broken)({"query": "test"})
    assert "/home/" not in str(caught.value) and "server log" in str(caught.value)
    with pytest.raises(ToolUnavailable, match="not built"):
        SafeTool("guideline search", unavailable)({"query": "test"})


# --- the compute runner: per-user share and forgotten jobs ---


def test_one_user_cannot_fill_the_compute_queue() -> None:
    runner = ComputeRunner(ServiceSettings(timeout_s=5, workers=1, max_pending=8), max_pending_per_owner=1)
    release = threading.Event()

    async def scenario() -> tuple[Any, Any]:
        first = asyncio.create_task(runner.run(lambda: release.wait(5), key=("a", 1), owner="user-a"))
        await asyncio.sleep(0.05)
        try:
            await runner.run(lambda: 1, key=("a", 2), owner="user-a")
            same_user: Any = None
        except ComputeBusy as error:
            same_user = error
        other = asyncio.create_task(runner.run(lambda: 2, key=("b", 1), owner="user-b"))
        await asyncio.sleep(0.05)
        release.set()
        await first
        return same_user, await other

    same_user, other_user = asyncio.run(scenario())
    assert isinstance(same_user, ComputeBusy) and other_user == 2
    runner.shutdown()


def test_a_job_forgotten_while_it_runs_is_not_cached() -> None:
    runner = ComputeRunner(ServiceSettings(timeout_s=0.05, workers=1, max_pending=8))
    release = threading.Event()
    key = ("studio", "owner", "plan", 1, "feasibility", 0)

    async def scenario() -> None:
        with pytest.raises(Exception, match="did not finish"):
            await runner.run(lambda: release.wait(5) and "result", key=key)
        runner.forget(("studio", "owner", "plan"))  # the plan is deleted while the job still runs
        release.set()
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
    assert runner.cached(key) == (False, None)
    runner.shutdown()

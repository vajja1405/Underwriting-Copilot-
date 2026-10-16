"""Sign-in and CSRF rules of EVERY Founder Studio and Lender Desk route: one table, checked against the routes the
app really serves.

The rules (docstrings of src/uwcopilot/web/routers/studio.py and lender.py, src/uwcopilot/web/security.py):
1. Signed out -> 401 "sign in required". The two public Studio routes (the options and the tract map layer) hold
   no personal data and answer 200 to anyone.
2. A state-changing request (POST, PUT, DELETE) whose X-CSRF-Token header does not match the uwc_csrf cookie -> 403
   from the CSRF middleware, before any route code runs, so nothing in the store changes. Signed in or not.
3. The control: a signed-in user who sends the token reaches the route itself (200, 201 or 404, never 401 or 403).
   GET routes need no token.

ROUTES must list exactly the routes the app serves under /api/studio and /api/lender, and OTHER_API_ROUTES every
other /api route (sign-in and system routes, whose own rules are tested in the files it names). A route the app
serves under ANY /api prefix that is in neither table (for example from a new router) fails
test_every_api_route_is_classified; every ROUTES row is then checked by the tests below.

Every plan is SYNTHETIC test input on the SYNTHETIC metro; the lender files are the repo's SYNTHETIC demo
applications and the decision record is a SYNTHETIC placeholder written straight into the store. Where a route
would do heavy work (the simulation, the path search, the agent run), the control uses an id that does not exist,
so the route answers 404 after the access checks. No network.
"""

import importlib
import pkgutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from web_helpers import csrf_headers, dev_login

import uwcopilot.web.routers as routers_package
from uwcopilot.studio.siteiq.context import metro_context
from uwcopilot.web.app import create_app
from uwcopilot.web.cookies import CSRF_COOKIE
from uwcopilot.web.security import UNSAFE_METHODS
from uwcopilot.web.services.lender_demo import DECISIONS_NAMESPACE

SYNTHETIC_METRO = "synthetic_metro"
API_PREFIX = "/api/"
SIGN_IN_REQUIRED = "sign in required"
CSRF_REFUSAL = "CSRF token missing or invalid"
DEMO_FILE = "APP-C-001"  # a SYNTHETIC demo application that exists
GUIDELINE_SECTION = "NBC-4.2"  # a section of the SYNTHETIC guideline
# Ids for the control requests: none of these objects exist, so the routes stop at 404 after the access checks.
MISSING_IDS = {
    "project_id": "0123456789abcdef",
    "file_id": "APP-C-99999",
    "section_id": "NBC-99.9",
    "metro_id": SYNTHETIC_METRO,
}


def synthetic_sites(count: int = 2) -> list[dict[str, Any]]:
    """Map pins on tract centroids of the SYNTHETIC city."""
    tracts = metro_context(SYNTHETIC_METRO).tracts
    return [
        {"label": f"Site {index + 1}", "lat": float(tracts.lats[index]), "lon": float(tracts.lons[index])}
        for index in range(count)
    ]


def plan_body() -> dict[str, Any]:
    """A SYNTHETIC laundromat plan (test inputs, not benchmarks)."""
    return {
        "metro_id": SYNTHETIC_METRO,
        "business_type_id": "laundromat",
        "budget_total": 400_000.0,
        "owner_equity": 150_000.0,
        "loan_amount": 350_000.0,
        "loan_term_months": 120,
        "annual_rate_pct": 9.0,
        "sites": synthetic_sites(),
    }


@dataclass(frozen=True)
class RouteCase:
    """One route and how these tests call it.

    path is the route's template exactly as FastAPI lists it; body is a valid JSON body when the route takes one;
    allowed_status is what a signed-in user who sends the CSRF token gets for the ids in MISSING_IDS.
    """

    method: str
    path: str
    public: bool = False
    body: dict[str, Any] | None = None
    allowed_status: int = 404

    @property
    def changes_state(self) -> bool:
        """True for POST, PUT and DELETE: the CSRF middleware guards them."""
        return self.method in UNSAFE_METHODS

    def url(self, ids: dict[str, str]) -> str:
        """The path with its {placeholders} filled in."""
        return self.path.format(**ids)

    def label(self) -> str:
        """The test id, e.g. 'PUT /api/studio/projects/{project_id}'."""
        return f"{self.method} {self.path}"


DECISION_BODY = {"action": "refer", "rationale": "Reviewed the SYNTHETIC demo file in an access test."}

ROUTES: tuple[RouteCase, ...] = (
    # Founder Studio
    RouteCase("GET", "/api/studio/options", public=True, allowed_status=200),
    RouteCase("POST", "/api/studio/projects", body=plan_body(), allowed_status=201),
    RouteCase("GET", "/api/studio/projects", allowed_status=200),
    RouteCase("GET", "/api/studio/projects/{project_id}"),
    RouteCase("PUT", "/api/studio/projects/{project_id}", body={"loan_term_months": 96}),
    RouteCase("DELETE", "/api/studio/projects/{project_id}"),
    RouteCase("POST", "/api/studio/projects/{project_id}/sites/score"),
    RouteCase("POST", "/api/studio/projects/{project_id}/feasibility", body={"site_index": 0}),
    RouteCase("POST", "/api/studio/projects/{project_id}/path-to-approval", body={"site_index": 0}),
    # An unknown metro, so the control does not rank tracts: the route itself answers 422 "unknown metro".
    RouteCase(
        "POST",
        "/api/studio/areas/suggest",
        body={"metro_id": "atlantis", "business_type_id": "laundromat"},
        allowed_status=422,
    ),
    RouteCase("GET", "/api/studio/metros/{metro_id}/tracts.geojson", public=True, allowed_status=200),
    # Lender Desk (DEMO)
    RouteCase("GET", "/api/lender/queue", allowed_status=200),
    RouteCase("GET", "/api/lender/files/{file_id}"),
    RouteCase("GET", "/api/lender/sections/{section_id}"),
    RouteCase("POST", "/api/lender/files/{file_id}/decision", body=DECISION_BODY),
    RouteCase("GET", "/api/lender/files/{file_id}/record"),
    RouteCase("DELETE", "/api/lender/files/{file_id}/record"),
)
STATE_CHANGING = tuple(case for case in ROUTES if case.changes_state)

# Every other /api route the app serves, with its rule and the test file that checks it. They are not in ROUTES
# because their rules differ (they are how a visitor signs in, or public system routes).
OTHER_API_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/health"): "public: liveness and build identity (tests/web/test_web_system.py)",
    ("GET", "/api/config"): "public: the app's public settings (tests/web/test_web_system.py)",
    ("GET", "/api/me"): "public: says who is signed in, if anyone (tests/web/test_web_auth.py)",
    ("GET", "/api/auth/csrf"): "public: hands out the CSRF token (tests/web/test_web_security.py)",
    ("GET", "/api/auth/login"): "public: starts Hugging Face sign-in (tests/web/test_web_auth.py)",
    ("GET", "/api/auth/callback"): "public: the sign-in callback, checks its own state (tests/web/test_web_auth.py)",
    ("POST", "/api/auth/dev-login"): "CSRF, no sign-in (it signs in; refused in a Space) (tests/web/test_web_auth.py)",
    ("POST", "/api/auth/logout"): "CSRF, no sign-in (tests/web/test_web_auth.py)",
}


def router_modules() -> list[ModuleType]:
    """Every module of uwcopilot.web.routers that has a `router` (so a new router module is found too)."""
    modules = []
    for info in pkgutil.iter_modules(routers_package.__path__):
        module = importlib.import_module(f"{routers_package.__name__}.{info.name}")
        if isinstance(getattr(module, "router", None), APIRouter):
            modules.append(module)
    return modules


def served_routes(client: TestClient) -> set[tuple[str, str]]:
    """(method, path) of every route under /api: each one the app's OpenAPI document lists (routers passed to
    create_app included), plus every route of every router module (so a route left out of the document is found)."""
    found: set[tuple[str, str]] = set()
    for path, operations in client.get("/api/openapi.json").json()["paths"].items():
        if path.startswith(API_PREFIX):
            found.update((method.upper(), path) for method in operations)
    for module in router_modules():
        for route in module.router.routes:
            if isinstance(route, APIRoute) and route.path.startswith(API_PREFIX):
                found.update((method, route.path) for method in route.methods)
    return found


def classified_routes() -> set[tuple[str, str]]:
    """Every (method, path) of ROUTES and OTHER_API_ROUTES."""
    return {(case.method, case.path) for case in ROUTES} | set(OTHER_API_ROUTES)


def send(client: TestClient, case: RouteCase, ids: dict[str, str], headers: dict[str, str] | None = None) -> Any:
    """Call the route of `case` with its body (if any) and the given headers."""
    return client.request(case.method, case.url(ids), json=case.body, headers=headers or {})


def store_files(store_dir: Path) -> dict[str, bytes]:
    """Every file of the local store, by relative path (to show that a refused request changed nothing)."""
    if not store_dir.is_dir():
        return {}
    return {str(path.relative_to(store_dir)): path.read_bytes() for path in store_dir.rglob("*") if path.is_file()}


@pytest.fixture
def app_client(make_settings) -> Iterator[TestClient]:
    """A dev-login app, nobody signed in yet."""
    with TestClient(create_app(make_settings(UWC_DEV_AUTH="1"))) as client:
        yield client


@pytest.fixture
def existing(app_client: TestClient) -> dict[str, str]:
    """Signs in founder A and gives them a plan and a DEMO decision record (both SYNTHETIC). Returns the ids of
    objects that exist, so a refused request is refused for a real object, not for a missing one."""
    me = dev_login(app_client, "founder_a", "founder")
    response = app_client.post("/api/studio/projects", json=plan_body(), headers=csrf_headers(app_client))
    assert response.status_code == 201, response.text
    owner = f"{me['auth_mode']}:{me['subject']}"
    placeholder = {"note": "SYNTHETIC placeholder decision record written by tests/web/test_web_studio_lender_access"}
    app_client.app.state.store.put_json(
        owner,
        DECISIONS_NAMESPACE,
        DEMO_FILE,
        {"label": "DEMO", "data_label": "SYNTHETIC", "file_id": DEMO_FILE, "record": placeholder},
    )
    return {
        "project_id": response.json()["id"],
        "file_id": DEMO_FILE,
        "section_id": GUIDELINE_SECTION,
        "metro_id": SYNTHETIC_METRO,
    }


# --- the table matches the app ---


def test_every_api_route_is_classified(client: TestClient) -> None:
    served = served_routes(client)
    classified = classified_routes()
    assert served - classified == set(), "add a RouteCase (or an OTHER_API_ROUTES entry) for each new /api route"
    assert classified - served == set(), "remove the row of a route the app no longer serves"
    assert len({(case.method, case.path) for case in ROUTES}) == len(ROUTES), "a route is listed twice"
    assert not {(case.method, case.path) for case in ROUTES} & set(OTHER_API_ROUTES), "a route is in both tables"


def test_a_route_under_a_new_api_prefix_is_caught(make_settings) -> None:
    # e.g. a new router that forgot the sign-in check: the guard above must notice it before any other test.
    probe = APIRouter(prefix="/api/probe")

    @probe.post("/run")
    def run() -> dict[str, str]:
        """An unclassified, unauthenticated state-changing route."""
        return {"ran": "yes"}

    with TestClient(create_app(make_settings(), extra_routers=[probe])) as probe_client:
        assert served_routes(probe_client) - classified_routes() == {("POST", "/api/probe/run")}


@pytest.mark.parametrize("route", sorted(route for route in OTHER_API_ROUTES if route[0] == "GET"), ids=str)
def test_the_other_get_routes_answer_signed_out_visitors(app_client: TestClient, route: tuple[str, str]) -> None:
    response = app_client.get(route[1], follow_redirects=False)
    assert response.status_code != 401, response.text


@pytest.mark.parametrize("route", sorted(route for route in OTHER_API_ROUTES if route[0] in UNSAFE_METHODS), ids=str)
def test_the_other_state_changing_routes_need_the_csrf_token(app_client: TestClient, route: tuple[str, str]) -> None:
    csrf_headers(app_client)  # the uwc_csrf cookie is set, but the header is not sent
    response = app_client.request(route[0], route[1], json={"username": "someone", "role": "founder"})
    assert response.status_code == 403 and response.json()["detail"].startswith(CSRF_REFUSAL)
    assert app_client.get("/api/me").json()["signed_in"] is False


def test_only_the_options_and_the_tract_layer_are_public() -> None:
    public = {case.path for case in ROUTES if case.public}
    assert public == {"/api/studio/options", "/api/studio/metros/{metro_id}/tracts.geojson"}
    assert all(case.method == "GET" for case in ROUTES if case.public)


# --- rule 1: signed out ---


@pytest.mark.parametrize("case", ROUTES, ids=RouteCase.label)
def test_signed_out_requests_get_401_unless_the_route_is_public(
    app_client: TestClient, existing: dict[str, str], case: RouteCase
) -> None:
    signed_out = app_client.post("/api/auth/logout", headers=csrf_headers(app_client))
    assert signed_out.status_code == 200 and app_client.get("/api/me").json()["signed_in"] is False
    # Send a valid CSRF token, so a 401 comes from the sign-in check and not from the CSRF middleware.
    headers = csrf_headers(app_client) if case.changes_state else {}
    response = send(app_client, case, existing, headers)
    if case.public:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 401, response.text
        assert response.json() == {"detail": SIGN_IN_REQUIRED}


# --- rule 2: CSRF ---


@pytest.mark.parametrize("variant", ["no_header", "wrong_header", "no_cookie", "signed_out_no_header"])
@pytest.mark.parametrize("case", STATE_CHANGING, ids=RouteCase.label)
def test_state_changing_requests_without_the_csrf_token_get_403_and_change_nothing(
    app_client: TestClient, existing: dict[str, str], tmp_path: Path, case: RouteCase, variant: str
) -> None:
    token = csrf_headers(app_client)["X-CSRF-Token"]  # the uwc_csrf cookie is set
    headers: dict[str, str] = {}
    if variant == "wrong_header":
        headers = {"X-CSRF-Token": "not-" + token}
    elif variant == "no_cookie":
        headers = {"X-CSRF-Token": token}
        app_client.cookies.delete(CSRF_COOKIE)
    elif variant == "signed_out_no_header":
        app_client.post("/api/auth/logout", headers={"X-CSRF-Token": token})
        assert app_client.get("/api/me").json()["signed_in"] is False
    before = store_files(tmp_path / "store")
    assert before, "the fixture stored a plan and a record"

    response = send(app_client, case, existing, headers)

    assert response.status_code == 403, response.text
    assert response.json()["detail"].startswith(CSRF_REFUSAL)
    assert store_files(tmp_path / "store") == before
    assert app_client.app.state.services.compute.pending_jobs() == 0


def test_the_refused_requests_left_the_plan_and_the_record_in_place(
    app_client: TestClient, existing: dict[str, str]
) -> None:
    for case in STATE_CHANGING:
        assert send(app_client, case, existing).status_code == 403, case.label()
    plan = app_client.get(f"/api/studio/projects/{existing['project_id']}")
    assert plan.status_code == 200 and plan.json()["version"] == 1
    assert app_client.get("/api/studio/projects").json()["total"] == 1
    me = app_client.get("/api/me").json()
    records = app_client.app.state.store.list(f"{me['auth_mode']}:{me['subject']}", DECISIONS_NAMESPACE)
    assert [meta.key for meta in records] == [DEMO_FILE]


# --- rule 3: the control ---


@pytest.mark.parametrize("case", ROUTES, ids=RouteCase.label)
def test_a_signed_in_user_with_the_token_reaches_the_route(app_client: TestClient, case: RouteCase) -> None:
    dev_login(app_client, "founder_b", "founder")
    # GET routes are sent WITHOUT the token: reading needs none.
    headers = csrf_headers(app_client) if case.changes_state else {}
    response = send(app_client, case, MISSING_IDS, headers)
    assert response.status_code == case.allowed_status, response.text

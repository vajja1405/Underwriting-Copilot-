"""Founder Studio API. Every response that shows numbers carries `disclaimer`; every simulated probability block
carries `label` = "simulated, based on stated assumptions". Studio prepares and explains; it never approves,
declines, promises approval or recommends a lender.

Access: every route needs a signed-in user (401 otherwise) except GET /api/studio/options and the tract map layer,
which hold no personal data. POST/PUT/DELETE also need the X-CSRF-Token header (403 otherwise). A plan belongs to
the user who created it (stored per stable owner id, encrypted at rest); another user's plan id answers 404.
Errors: {"detail": "...", "reasons": [...]?}. 422 = values that cannot be used (also pydantic's own 422, whose
"detail" is a list of {"type", "loc", "msg"}, never the input, at most 20 plus a count line); 413 = a body over
64 KiB; 409 = limited mode / stale version / already decided / the plan cap (20 plans per user); 429 = too many
address lookups (one batch at a time per user, a small per-user budget); 503 = a heavy job took longer than
UWC_COMPUTE_TIMEOUT_S (default 60 s; it keeps running and its result is cached for the next try), the server is
busy (also: one user may have at most 3 heavy jobs running or queued), or the Census Geocoder is unreachable.
Heavy work runs in worker threads and is cached per plan version; address lookups run in their own small pool.

GET /api/studio/options -> 200
    {"metros": [{"id": "synthetic_metro", "name": "Synthetic Metro (SYNTHETIC)", "mode": "synthetic"|"limited"|"full",
                 "mode_reason": "...", "data_label": "SYNTHETIC"|"PUBLIC", "coverage_note": "...", "verified": false,
                 "n_tracts": 300, "site_input": "map"|"address_or_map", "supports_area_suggestions": true,
                 "simulation_available": true (false: no tract data here, demand is UNKNOWN, no simulation),
                 "prime_rate": {"factor": "prime_rate", "value": 7.5|null, "unit": "...", "status": "SYNTHETIC"|
                                "DATA"|"UNKNOWN", "reason": "...", "source": {...SourceRecord}|null, ...}|null}],
     "business_types": [{"id": "car_wash", "name": "Car wash", "demand_unit": "cars/day", "build_out_low": 0.0,
                         "build_out_high": 0.0, "build_out_status": "ASSUMPTION"|"SOURCED",
                         "build_out_assumed": 0.0 (the point the simulation uses for an empty budget),
                         "build_out_note": "..."}],
     "levers": [{"name": "owner_equity", "label": "...", "unit": "USD", "kind": "amount",
                 "default_bounds": {"low": {"basis": "project_cost", "value": 0.0}, "high": {...}}|null,
                 "options": [60, 72]|null, "round_to": 500.0|null, "effort_weight": 2.0, "note": "...",
                 "status": "DESIGN_CHOICE"}],
     "default_target": {"dscr_min": 1.25, "p_dscr_min": 0.8, "stress_revenue_share": 0.2, "stressed_dscr_min": 1.0,
                        "p_stressed_min": 0.8, "p_cash_negative_max": 0.1, "cash_months": 24, "year": 2,
                        "sources": {"dscr_min": "...", ...}},
     "dscr_target": {"value": 1.25, "label": "...", "source": "..."},
     "annual_rate_default": {"status": "ASSUMPTION", "rule": "...", "spread_over_prime_pct": {Quantity}},
     "limits": {"max_sites": 3, "idea_text_max_chars": 2000, "loan_term_months_min": 24,
                "loan_term_months_max": 360, "annual_rate_pct_max": 30.0},
     "disclaimer": "...", "simulation_label": "simulated, based on stated assumptions", "synthetic_label": "SYNTHETIC"}
    (The numbers above show the shape only; the real values come from the config files.)

POST /api/studio/projects  body (ProjectIn; money in USD):
    {"name": "My car wash"|null, "metro_id": "synthetic_metro", "business_type_id": "car_wash",
     "idea_text": "..."|null (<= 2000 chars; screened for injection: flagged text is stored, shown as flagged and
                               never given to any language model; SSN / account numbers are refused with 422),
     "budget_total": 1500000|null (build-out + equipment = capex; null = the profile's point estimate,
                     build_out_assumed in the options, ASSUMPTION),
     "owner_equity": 600000, "loan_amount": 1100000 (owner_equity + loan_amount must cover budget_total, or
                     build_out_assumed when the budget is empty; the 422 names both amounts),
     "loan_term_months": 84 (24-360), "annual_rate_pct": 8.0|null (null = metro prime + spread, ASSUMPTION),
     "rate_type": "variable"|"fixed" (default variable), "opening_date": "2027-04-01"|null (null = first day of the
     month six months after creation, ASSUMPTION; given: from one year ago to five years ahead),
     "sites": [{"label": "North lot", "lat": 10.02, "lon": -140.03} | {"label": "Main St", "address": "..."}] (1-3;
              addresses only for real metros, geocoded by the Census Geocoder; SYNTHETIC metro pins must be within
              3 miles of one of its tracts; names and addresses with an SSN or account number are refused before
              any lookup; the metro's own 11-digit tract ids are allowed in names)}
    -> 201 Project:
    {"id": "16 hex", "version": 1, "name": ..., "created_at": "...", "updated_at": "...", "expires_at": "..."|null,
     "retention_days": 30|null, "metro_id": ..., "metro_name": ..., "metro_mode": "synthetic", "data_label": ...,
     "business_type_id": ..., "business_type_name": ..., "demand_unit": ...,
     "idea": {"text": ..., "flagged": false, "flags": ["decision_directive"], "note": "..."},
     "money": {"budget_total": ...|null, "budget_status": S, "build_out_assumed": ...|null, "owner_equity": ...,
               "owner_equity_status": S, "loan_amount": ..., "loan_amount_status": S, "loan_term_months": ...,
               "loan_term_status": S, "annual_rate_pct": ...|null, "annual_rate_status": "USER_STATED"|"ASSUMPTION",
               "rate_type": ..., "cash_reserve_at_opening": ..., "cash_reserve_status": "DERIVED"|"ASSUMPTION",
               "plan_changes": {field: {"source": "path_to_approval", "engine_version", "applied_at",
                                        "evidence_needed": ["..."]}},
               "note": "..."}
       (S = "USER_STATED" typed by the founder | "ASSUMPTION" left empty | "PLAN_CHANGE" written by "Apply this
        change", until the founder types the field again),
     "opening_date": "2027-04-01", "opening_date_status": "USER_STATED"|"ASSUMPTION",
     "sites": [{"index": 0, "label": ..., "lat": ..., "lon": ..., "location_source": "map_pin"|"census_geocoder",
                "address": ...|null, "matched_address": ...|null, "tract_geoid": ...|null, "location_check": "...",
                "geocode_source": {SourceRecord}|null}],
     "selected_site_index": 0, "selected_site_status": S, "seed": 123 (the same for every version: common random
     numbers), "disclaimer": "..."}

GET /api/studio/projects -> 200 {"projects": [{"id", "version", "name", "metro_id", "metro_name",
    "business_type_id", "business_type_name", "n_sites", "created_at", "updated_at", "expires_at",
    "idea_flagged"}], "total": 1}   (newest change first)
GET /api/studio/projects/{id} -> 200 Project | 404
PUT /api/studio/projects/{id}  body: any subset of the POST fields, plus optional "version" (must equal the stored
    version, else 409), "selected_site_index" (0..sites-1) and "plan_change" ({"source": "path_to_approval",
    "engine_version", "fields": [...the fields this PUT sets], "evidence_needed": [...]}, sent by "Apply this
    change"). Omitted = unchanged; null clears an optional field. -> 200 Project (version + 1)
DELETE /api/studio/projects/{id} -> 200 {"project_id", "deleted": true, "pending": false, "message"} | 404 |
    202 {"detail": "..."} when the backup copy could not be deleted yet (retried; the data is not fully gone yet)

POST /api/studio/projects/{id}/sites/score  (no body) -> 200
    {"project_id", "project_version", "metro_id", "metro_name", "data_label", "mode", "mode_reason",
     "scoring_method": "siteiq-v1: ...", "disclaimer",
     "sites": [{"index", "label", "lat", "lon", "tract_geoid", "district", "overall_score": 0-100|null,
                "coverage_score": 0-1, "scored_weight_share": 0-1, "top_reasons": ["..."], "watch_out": "..."|null,
                "factors": [{"name", "description", "raw": float|null, "unit", "status": "DATA"|"SYNTHETIC"|
                             "ASSUMPTION"|"SOURCED"|"UNKNOWN"|"FOUNDER_INPUT", "score": 0-100|null, "percentile",
                             "weight", "direction", "used_in_overall", "area", "reason", "normalization",
                             "reference_size", "source": "publisher, dataset (retrieved YYYY-MM-DD)"|null,
                             "sources": [SourceRecord]}],
                "demand": {"status": "MODEL_ESTIMATE", "unit", "low", "p10", "p50", "mode", "p90", "high", "basis",
                           "complete", "missing", "method", "formula_description", "area", "capacity_note",
                           "inputs": [DemandInput]},
                "metro_inputs": {"commercial_rent"|"local_wages"|"prime_rate": FactorValue},
                "context": [ContextItem], "trade_area": TradeAreaInfo, "notes": ["..."],
                "error": null | "why the site could not be scored (the other fields are then empty)"}]}

POST /api/studio/projects/{id}/feasibility  body {"site_index": 0} -> 200
    {"project_id", "project_version", "site_index", "site_label", "seed" (fixed per plan version),
     "label": "simulated, based on stated assumptions", "disclaimer",
     "headline": {"label", "dscr_target": 1.25, "p_dscr_year1_at_least_target": p|null,
                  "p_dscr_year2_at_least_target": p|null, "stress_min_dscr": 1.0|null,
                  "stress_revenue_drop_share": 0.2|null, "p_stressed_dscr_year2_at_least_min": p|null,
                  "p_cash_negative_within_24_months": p, "break_even_p50_month": int|null},
     "result": FeasibilityResult (studio.feasibility.result: monthly [{month, calendar_month, revenue_p10/p50/p90,
               net_income_p10/p50/p90, cash_p10/p50/p90}], yearly [...], break_even {...}, tornado {bars [...]},
               stress [...], distributions [{name, description, unit, kind, low, mode, high, status, source,
               points}] = every sampled input with status and source, loan {...}, notes [...], ...)}
    409 {"detail": "limited mode: this site's demand is UNKNOWN here ...", "reasons": [...]} when SiteIQ has no
    demand estimate for the site (a real metro without a data snapshot, e.g. in the Space); 422 when the plan
    cannot be simulated (for example the metro has no prime rate and no annual_rate_pct was entered, or equity +
    loan do not cover the profile's build-out range); the message says what to change.

POST /api/studio/projects/{id}/path-to-approval  body {"site_index": 0, "targets": {"p_dscr_min": 0.8, ...}|null,
    "max_extra_owner_cash": 25000|null (USD; null = the default bound, shown in owner_cash_bound)}
    -> 200 {"project_id", "project_version", "site_index", "site_label", "label", "disclaimer",
            "status": "already_meets_target"|"paths_found"|"no_path_within_bounds", "message",
            "guardrail_note": "Reaching the targets is not an approval: ...", "guardrails": ["..."],
            "target": Target, "before": Outcome, "plan_before": PlanAmounts,
            "change_sets": [{"rank", "changes": [{"lever", "label", "unit", "from_value", "to_value", "effort",
                             "fields_changed", "own_change", "evidence_needed"}], "n_levers", "effort",
                             "before": Outcome, "after": Outcome, "plan_before", "plan_after", "meets_target",
                             "program_rules_not_met": ["..."], "evidence_needed": ["..."],
                             "field_changes": [{"field", "label", "unit", "from_value", "to_value",
                                                "writes_plan"}] (EVERY figure that changes, also the balancing
                                                ones: more owner cash lowers the loan),
                             "explanation", "unchanged",
                             "apply": {"supported": true, "reason": null, "site_index": 0,
                                       "project_fields": {only the changed fields of owner_equity, loan_amount,
                                                          loan_term_months, budget_total, selected_site_index},
                                       "plan_change": {PlanChange for the PUT}|null}}] (at most 3),
            "closest": change-set|null, "levers_considered", "levers_not_available": {lever: why},
            "owner_cash_bound": {"current", "high", "extra", "source": "default"|"founder", "note"}|null,
            "skipped_candidates": ["label: why"], "notes", "settings" (QUICK mode), "search" (runtime_ms, ...),
            "metro_mode", "data_label", "engine_version", "simulator_version"}
    Outcome = {"label", "n_paths", "seed", "p_dscr_at_least_target_year2", "p_stressed_dscr_at_least_min_year2",
               "p_cash_negative_within_months", "projected_dscr_base_case", "stressed_dscr_base_case",
               "annual_debt_service", "meets_target", "targets_missed", "shortfall"}
    "Apply this change" = PUT /api/studio/projects/{id} with {version, ...apply.project_fields, "plan_change":
    apply.plan_change}, then re-run at apply.site_index. The seed is the same for every version, so the re-run shows
    exactly the "after" numbers of the card. Price and days open are locked by the server (the v0.1 form has no
    field for them), so every change-set can be applied.
    409 (limited mode, demand UNKNOWN) and 422 as for feasibility.

POST /api/studio/areas/suggest  body {"metro_id", "business_type_id"} -> 200 Suggestions (studio.siteiq.models:
    {"areas": [{"rank", "geoid", "lat", "lon", "district", "overall_score", "coverage_score", "reasons",
    "watch_out", "factor_scores", "demand_mode", "demand_unit"}] (top 5), "geojson": FeatureCollection, "excluded",
    "budget", "notes", "mode", "mode_reason", "data_label", "disclaimer", ...})
    | 409 {"detail": "limited mode: not enough data to suggest areas", "reasons"?: [...]}

GET /api/studio/metros/{id}/tracts.geojson -> 200 application/geo+json
    {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point",
     "coordinates": [lon, lat]}, "properties": {"geoid", "district", "data_label"}}],
     "metadata": {"metro_id", "metro_name", "data_label", "mode", "n_tracts", "geometry", "note"}}
    (tract centroids; no polygons, no third-party tiles; empty features in limited mode without a snapshot)
"""

from functools import partial
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from uwcopilot.studio.siteiq.models import Suggestions
from uwcopilot.web.auth import Principal, require_role
from uwcopilot.web.schemas_studio import (
    PROJECT_ID_PATTERN,
    STUDIO_ID_PATTERN,
    AreasRequest,
    FeasibilityRequest,
    FeasibilityResponse,
    OptionsResponse,
    PathRequest,
    PathResponse,
    ProjectDeleted,
    ProjectIn,
    ProjectList,
    ProjectOut,
    ProjectUpdate,
    SiteScoresResponse,
)
from uwcopilot.web.services import WebServices, get_services, projects, studio_runs
from uwcopilot.web.services.geocoding import PrefetchedGeocoder
from uwcopilot.web.services.http import get_store, no_store
from uwcopilot.web.services.studio_options import studio_options
from uwcopilot.web.store import Store

router = APIRouter(prefix="/api/studio", tags=["studio"])

SignedIn = Annotated[Principal, Depends(require_role())]
Services = Annotated[WebServices, Depends(get_services)]
StoreDep = Annotated[Store, Depends(get_store)]
ProjectId = Annotated[str, Path(pattern=PROJECT_ID_PATTERN)]
MetroId = Annotated[str, Path(pattern=STUDIO_ID_PATTERN)]
GEOJSON_MEDIA_TYPE = "application/geo+json"
TRACTS_CACHE_SECONDS = 3600


def project_key(principal: Principal, record: projects.ProjectRecord, *parts: Any) -> tuple[Any, ...]:
    """The compute-cache key of a result of one plan version (forget(("studio", owner, id)) drops them all)."""
    return ("studio", principal.owner_id, record.id, record.version, *parts)


@router.get("/options", response_model=OptionsResponse)
async def options(services: Services) -> OptionsResponse:
    """Metros (with their honest mode), business types, levers and defaults. Built once per process."""
    return await services.compute.run(studio_options, key=("studio", "options"))


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(no_store)],
)
async def create_project(body: ProjectIn, principal: SignedIn, services: Services, store: StoreDep) -> ProjectOut:
    """Create a plan (sites are checked, real-metro addresses geocoded) and store it for this user.

    Order: the cheap checks of the site text (nothing with an SSN or account number is ever sent to the geocoder),
    the address lookups (their own pool, per-user limits), then building and saving the plan in a worker thread
    (the store may wait on disk or on the dataset store's lock; the event loop never does)."""
    await services.compute.run(studio_options, key=("studio", "options"))
    owner = principal.owner_id
    await run_in_threadpool(projects.check_plan_count, store, owner)
    await run_in_threadpool(projects.check_sites_text, body.metro_id, body.sites)
    geocoder = await services.geocoding.prefetch(owner, projects.addresses_to_geocode(body.sites))
    record, meta = await run_in_threadpool(projects.create, store, owner, body, geocoder)
    return await run_in_threadpool(projects.project_out, record, meta)


@router.get("/projects", response_model=ProjectList, dependencies=[Depends(no_store)])
def list_projects(principal: SignedIn, store: StoreDep) -> ProjectList:
    """The user's plans, newest change first."""
    items = projects.list_records(store, principal.owner_id)
    summaries = [projects.project_summary(record, meta) for record, meta in items]
    return ProjectList(projects=summaries, total=len(summaries))


@router.get("/projects/{project_id}", response_model=ProjectOut, dependencies=[Depends(no_store)])
def get_project(project_id: ProjectId, principal: SignedIn, store: StoreDep) -> ProjectOut:
    """One plan of the user (404 for any other id)."""
    record = projects.load(store, principal.owner_id, project_id)
    return projects.project_out(record, projects.meta_for(store, principal.owner_id, project_id))


@router.put("/projects/{project_id}", response_model=ProjectOut, dependencies=[Depends(no_store)])
async def update_project(
    project_id: ProjectId, body: ProjectUpdate, principal: SignedIn, services: Services, store: StoreDep
) -> ProjectOut:
    """Change a plan; the version goes up by one, so its results are computed afresh (same seed: see projects.py)."""
    owner = principal.owner_id
    current = await run_in_threadpool(projects.load, store, owner, project_id)
    geocoder = PrefetchedGeocoder()  # a PUT without new sites looks nothing up (stored addresses are reused)
    if body.sites is not None:
        metro_id = body.metro_id or current.metro_id
        await run_in_threadpool(projects.check_sites_text, metro_id, body.sites)
        addresses = projects.addresses_to_geocode(body.sites, known=current.sites)
        geocoder = await services.geocoding.prefetch(owner, addresses)
    record, meta = await run_in_threadpool(projects.update, store, owner, project_id, body, geocoder)
    services.compute.forget(("studio", owner, project_id))
    return await run_in_threadpool(projects.project_out, record, meta)


@router.delete("/projects/{project_id}", response_model=ProjectDeleted, dependencies=[Depends(no_store)])
def delete_project(project_id: ProjectId, principal: SignedIn, services: Services, store: StoreDep) -> ProjectDeleted:
    """Delete a plan and every result kept for it (DeletionPending -> 202 with an honest message)."""
    projects.load(store, principal.owner_id, project_id)
    services.compute.forget(("studio", principal.owner_id, project_id))
    store.delete(principal.owner_id, projects.PROJECTS_NAMESPACE, project_id)
    return ProjectDeleted(
        project_id=project_id, deleted=True, pending=False, message="The plan and its stored results are deleted."
    )


@router.post("/projects/{project_id}/sites/score", response_model=SiteScoresResponse, dependencies=[Depends(no_store)])
async def score_sites(project_id: ProjectId, principal: SignedIn, services: Services, store: StoreDep) -> Any:
    """SiteIQ for every site of the plan."""
    record = await run_in_threadpool(projects.load, store, principal.owner_id, project_id)
    key = project_key(principal, record, "sites")
    work = partial(studio_runs.score_project_sites, record)
    return await services.compute.run(work, key=key, owner=principal.owner_id)


@router.post("/projects/{project_id}/feasibility", response_model=FeasibilityResponse, dependencies=[Depends(no_store)])
async def feasibility(
    project_id: ProjectId, body: FeasibilityRequest, principal: SignedIn, services: Services, store: StoreDep
) -> Any:
    """The Monte Carlo feasibility of one site (1,000 paths, seed fixed per plan version)."""
    record = await run_in_threadpool(projects.load, store, principal.owner_id, project_id)
    key = project_key(principal, record, "feasibility", body.site_index)
    work = partial(studio_runs.run_feasibility, record, body.site_index)
    return await services.compute.run(work, key=key, owner=principal.owner_id)


@router.post("/projects/{project_id}/path-to-approval", response_model=PathResponse, dependencies=[Depends(no_store)])
async def path_to_approval(
    project_id: ProjectId, body: PathRequest, principal: SignedIn, services: Services, store: StoreDep
) -> Any:
    """The smallest plan changes that reach the targets (QUICK mode)."""
    record = await run_in_threadpool(projects.load, store, principal.owner_id, project_id)
    targets = body.targets.model_dump_json(exclude_none=True) if body.targets else ""
    key = project_key(principal, record, "paths", body.site_index, targets, body.max_extra_owner_cash)
    work = partial(studio_runs.run_path_to_approval, record, body.site_index, body.targets, body.max_extra_owner_cash)
    return await services.compute.run(work, key=key, owner=principal.owner_id)


@router.post("/areas/suggest", response_model=Suggestions)
async def suggest(body: AreasRequest, principal: SignedIn, services: Services) -> Any:
    """The top tracts of a metro for a business type (409 in limited mode)."""
    key = ("studio", "suggest", body.metro_id, body.business_type_id)
    work = partial(studio_runs.run_suggest, body.metro_id, body.business_type_id)
    return await services.compute.run(work, key=key, owner=principal.owner_id)


@router.get("/metros/{metro_id}/tracts.geojson", response_class=JSONResponse)
async def tracts(metro_id: MetroId, services: Services) -> JSONResponse:
    """The metro's tract centroids for the map (public data or SYNTHETIC; no personal data). 404: unknown metro."""
    key = ("studio", "tracts", metro_id)
    layer = await services.compute.run(partial(studio_runs.tracts_geojson, metro_id), key=key)
    headers = {"Cache-Control": f"public, max-age={TRACTS_CACHE_SECONDS}"}
    return JSONResponse(layer, media_type=GEOJSON_MEDIA_TYPE, headers=headers)

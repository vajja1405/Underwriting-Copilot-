"""The Studio engines behind the web API: SiteIQ scores, the Monte Carlo feasibility, the path to approval, area
suggestions and the tract map layer. Thin adapters: every number comes from uwcopilot.studio; this module only
picks the inputs from the stored plan and renames fields to the API contract (schemas_studio.py).

These functions are slow enough to run in a worker thread (compute.py). They are pure functions of the stored plan
version (and the metro's data), so their results can be cached per (user, plan, version, ...).

How a plan becomes simulator inputs:
- Plan: opening_date, owner_equity, capex = budget_total (empty = the profile's capex range, ASSUMPTION);
- Loan: amount, term_months, annual_rate_pct (empty = the metro's prime rate + the spread ASSUMPTION), rate_type;
- the site's demand: SiteIQ's estimate for that site (score_site), as the simulator's own adapter reads it;
- seed: fixed per plan, the same for every version (ProjectRecord.seed), n = 1,000 paths, 36 months (the S3
  defaults). Because the path search uses the same seed, an applied change-set reproduces its "after" outcome.
The path search runs in QUICK mode (studio.path_to_approval.QUICK_SETTINGS, with the plan's seed); the plan's other
sites are its candidate sites; price and days open are locked by the server (the v0.1 form has no field for them, and
the answer says so instead of the engine's "locked by you"). The founder may bound the extra owner cash the search
may add (max_extra_owner_cash); otherwise the engine's default bound applies and the answer shows it.

"Apply this change" (apply_patch) writes only the fields a change-set changes, marked as a PLAN_CHANGE with the
evidence a lender would need (ChangeSet.evidence_needed), never as figures the founder typed (USER_STATED).
"""

from typing import Any

from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.feasibility import Loan, Plan, as_site_demand, simulate
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.path_to_approval import (
    PATH_ENGINE_VERSION,
    QUICK_SETTINGS,
    Candidate,
    ChangeSet,
    GuardrailViolation,
    LeverBounds,
    LeverError,
    OutOfBounds,
    PathResult,
    PlanAmounts,
    PlanCase,
    candidate_from_site_score,
    custom_target,
    default_target,
    find_paths,
    load_levers,
)
from uwcopilot.studio.siteiq import SiteInputs, SiteOutsideMetro, SiteScore, score_site, suggest_areas
from uwcopilot.studio.siteiq.models import SCORING_METHOD, Suggestions
from uwcopilot.studio.siteiq.suggest import area_from_score
from uwcopilot.web.schemas_studio import (
    ApplyPatch,
    ChangeSetOut,
    DemandOut,
    FactorOut,
    FeasibilityResponse,
    FieldChange,
    Headline,
    OwnerCashBound,
    PathResponse,
    PlanChangeIn,
    SiteScoreOut,
    SiteScoresResponse,
    TargetsIn,
)
from uwcopilot.web.services.errors import Conflict, InputError, NotFound
from uwcopilot.web.services.projects import ProjectRecord, StoredSite
from uwcopilot.web.services.studio_options import WEB_DSCR_TARGET_LABEL, metro_catalog, metro_entry, profile_for

N_PATHS = 1000
MONTHS = 36
REVENUE_STRESS = "revenue_stress"
GUARDRAIL_NOTE = "Reaching the targets is not an approval: Studio never approves, declines or recommends a lender."
# Levers whose result the plan form can hold: the four money levers, the loan term and the choice of site.
PATCHABLE_LEVERS = frozenset(
    {"owner_equity", "loan_amount", "build_out_cost", "working_capital_reserve", "loan_term_months", "site"}
)
# Levers the v0.1 plan form has no field for (a typed price, a staffing plan). The search is told not to move them
# (find_paths' `locked`), so every change-set it returns can be applied to the plan.
LOCKED_LEVERS: tuple[str, ...] = ("price", "days_open_per_week")
LOCKED_NOTE = (
    "Price and days open are not part of the plan form yet, so the search did not move them (the server locks them)."
)
LOCKED_REASON = "not part of the v0.1 plan form yet, so the search does not move it (locked by the server, not by you)"
OWNER_CASH_CAVEAT = (
    "Extra owner cash counts only if you really have it: a lender checks it against bank statements. The cash-reserve "
    "lever is funded by your own cash too."
)
OWNER_CASH_FOUNDER_NOTE = (
    "Bound you set: the most extra cash of your own the search may add. A lender checks owner cash against bank "
    "statements; the cash-reserve lever is funded by your own cash too."
)
# Plan figures of the path search (PlanAmounts) and the plan field "Apply this change" writes for each (None = a
# figure computed from the others, shown but not written).
PLAN_FIGURES: tuple[tuple[str, str, str, str | None], ...] = (
    ("owner_equity", "Owner cash equity", "USD", "owner_equity"),
    ("loan_amount", "Loan amount", "USD", "loan_amount"),
    ("build_out_cost", "Build-out and equipment cost", "USD", "budget_total"),
    ("cash_reserve_at_opening", "Cash reserve at opening", "USD", None),
    ("loan_term_months", "Loan term", "months", "loan_term_months"),
    ("site_label", "Site", "site", "selected_site_index"),
)
LIMITED_SUGGEST_MESSAGE = "limited mode: not enough data to suggest areas"
LIMITED_DEMAND_MESSAGE = (
    "limited mode: this site's demand is UNKNOWN here (no data for it in this metro), so the plan cannot be "
    "simulated. The SYNTHETIC demo city shows the full flow."
)


# ---------------------------------------------------------------------------------------------------------------
# Plan inputs
# ---------------------------------------------------------------------------------------------------------------


def plan_for(record: ProjectRecord) -> Plan:
    """The simulator's Plan from the stored plan."""
    return Plan(opening_date=record.opening_date, owner_equity=record.owner_equity, capex=record.budget_total)


def loan_for(record: ProjectRecord) -> Loan:
    """The simulator's Loan from the stored plan."""
    return Loan(
        amount=record.loan_amount,
        term_months=record.loan_term_months,
        annual_rate_pct=record.annual_rate_pct,
        rate_type=record.rate_type,
    )


def site_at(record: ProjectRecord, site_index: int) -> StoredSite:
    """The plan's site at site_index, or InputError."""
    if not 0 <= site_index < len(record.sites):
        raise InputError(f"site_index {site_index} does not exist; this plan has {len(record.sites)} site(s)")
    return record.sites[site_index]


def score_stored_site(record: ProjectRecord, site: StoredSite) -> SiteScore:
    """SiteIQ's score of one stored site (offline: the metro's snapshot or the SYNTHETIC city only)."""
    entry = metro_entry(record.metro_id)
    profile = profile_for(record.business_type_id, record.metro_id)
    return score_site(profile, entry.config, site.lat, site.lon, SiteInputs(label=site.label), context=entry.context)


# ---------------------------------------------------------------------------------------------------------------
# Site scores
# ---------------------------------------------------------------------------------------------------------------


def source_line(sources: list[SourceRecord]) -> str | None:
    """'publisher, dataset (retrieved YYYY-MM-DD)' of the first source, or None."""
    if not sources:
        return None
    first = sources[0]
    return f"{first.publisher}, {first.dataset} (retrieved {first.retrieved_at.date().isoformat()})"


def factor_out(item: Any) -> FactorOut:
    """A SiteIQ FactorScore under the API's field names."""
    return FactorOut(
        name=item.factor,
        description=item.description,
        raw=item.raw_value,
        unit=item.unit,
        status=item.status,
        score=item.normalized_score,
        percentile=item.percentile,
        weight=item.weight,
        direction=item.direction,
        used_in_overall=item.used_in_overall,
        area=item.area,
        reason=item.reason,
        normalization=item.normalization,
        reference_size=item.reference_size,
        source=source_line(item.sources),
        sources=item.sources,
    )


def demand_out(score: SiteScore) -> DemandOut:
    """SiteIQ's DemandEstimate under the API's field names."""
    demand = score.demand
    return DemandOut(
        unit=demand.unit,
        low=demand.low,
        p10=demand.p10,
        p50=demand.p50,
        mode=demand.mode,
        p90=demand.p90,
        high=demand.high,
        basis=demand.basis,
        complete=demand.complete,
        missing=demand.missing,
        method=demand.method,
        formula_description=demand.formula_description,
        area=demand.area,
        capacity_note=demand.capacity_note,
        inputs=demand.inputs,
    )


def site_score_out(index: int, site: StoredSite, score: SiteScore) -> SiteScoreOut:
    """One site's card: score, coverage, top reasons (SiteIQ's own wording), factors, demand, notes."""
    area = area_from_score(1, score)
    return SiteScoreOut(
        index=index,
        label=site.label,
        lat=site.lat,
        lon=site.lon,
        tract_geoid=score.tract_geoid,
        district=score.district,
        overall_score=score.overall_score,
        coverage_score=score.coverage_score,
        scored_weight_share=score.scored_weight_share,
        top_reasons=area.reasons,
        watch_out=area.watch_out,
        factors=[factor_out(item) for item in score.factors],
        demand=demand_out(score),
        metro_inputs=score.metro_inputs,
        context=score.context,
        trade_area=score.trade_area,
        notes=score.notes,
    )


def score_project_sites(record: ProjectRecord) -> SiteScoresResponse:
    """SiteIQ for every site of the plan; a site that cannot be scored carries `error` instead."""
    entry = metro_entry(record.metro_id)
    results = []
    for index, site in enumerate(record.sites):
        try:
            results.append(site_score_out(index, site, score_stored_site(record, site)))
        except SiteOutsideMetro as error:
            results.append(SiteScoreOut(index=index, label=site.label, lat=site.lat, lon=site.lon, error=str(error)))
    return SiteScoresResponse(
        project_id=record.id,
        project_version=record.version,
        metro_id=record.metro_id,
        metro_name=entry.config.name,
        data_label=entry.config.data_label,
        mode=entry.option.mode,
        mode_reason=entry.option.mode_reason,
        scoring_method=SCORING_METHOD,
        sites=results,
        disclaimer=DISCLAIMER,
    )


# ---------------------------------------------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------------------------------------------


def headline(result: FeasibilityResult) -> Headline:
    """The four gauges, read from the result (None where the result has no such number)."""
    stress = next((item for item in result.stress if item.name == REVENUE_STRESS and item.applies), None)
    return Headline(
        label=SIMULATION_LABEL,
        dscr_target=result.dscr_target.value,
        p_dscr_year1_at_least_target=result.p_dscr_at_least_target.get("year_1"),
        p_dscr_year2_at_least_target=result.p_dscr_at_least_target.get("year_2"),
        stress_min_dscr=stress.min_dscr if stress else None,
        stress_revenue_drop_share=stress.revenue_drop_share if stress else None,
        p_stressed_dscr_year2_at_least_min=stress.p_dscr_year2_at_least_min if stress else None,
        p_cash_negative_within_24_months=result.p_cash_negative_within_24_months,
        break_even_p50_month=result.break_even.p50_month,
    )


def site_score_or_error(record: ProjectRecord, site: StoredSite) -> SiteScore:
    """The site's SiteIQ score. A site outside the metro is an InputError; a site whose demand is UNKNOWN (limited
    mode) is a Conflict, because the simulator needs a demand range and none is filled in silently."""
    try:
        score = score_stored_site(record, site)
    except SiteOutsideMetro as error:
        raise InputError(str(error)) from error
    if score.demand.mode is None:
        raise Conflict(LIMITED_DEMAND_MESSAGE, reasons=score.demand.missing)
    return score


def run_feasibility(record: ProjectRecord, site_index: int) -> FeasibilityResponse:
    """S3 simulate() for one site of the plan: 1,000 paths, 36 months, the plan version's seed."""
    site = site_at(record, site_index)
    score = site_score_or_error(record, site)
    entry = metro_entry(record.metro_id)
    try:
        result = simulate(
            record.business_type_id,
            entry.context.fabric,
            score,
            plan_for(record),
            loan_for(record),
            n=N_PATHS,
            months=MONTHS,
            seed=record.seed,
        )
    except (ValueError, TypeError) as error:
        raise InputError(f"the plan cannot be simulated: {error}") from error
    result = result.model_copy(
        update={"dscr_target": result.dscr_target.model_copy(update={"label": WEB_DSCR_TARGET_LABEL})}
    )
    return FeasibilityResponse(
        project_id=record.id,
        project_version=record.version,
        site_index=site_index,
        site_label=site.label,
        seed=record.seed,
        label=SIMULATION_LABEL,
        disclaimer=DISCLAIMER,
        headline=headline(result),
        result=result,
    )


# ---------------------------------------------------------------------------------------------------------------
# Path to approval
# ---------------------------------------------------------------------------------------------------------------


def target_for(targets: TargetsIn | None) -> Any:
    """The default target, or the default with the founder's edits."""
    if targets is None:
        return default_target()
    changes = targets.model_dump(exclude_none=True)
    try:
        return custom_target(**changes) if changes else default_target()
    except ValueError as error:
        raise InputError(f"invalid targets: {error}") from error


def candidates_for(record: ProjectRecord, site_index: int) -> tuple[list[Candidate], list[str]]:
    """(the plan's other sites as candidates, labels of sites skipped with the reason)."""
    candidates, skipped = [], []
    for index, site in enumerate(record.sites):
        if index == site_index:
            continue
        try:
            score = score_stored_site(record, site)
            candidates.append(candidate_from_site_score(score, label=site.label))
        except (SiteOutsideMetro, ValueError, TypeError) as error:
            skipped.append(f"{site.label}: {error}")
    return candidates, skipped


def changed_fields(record: ProjectRecord, change_set: ChangeSet, site_index: int) -> dict[str, float | int]:
    """The plan fields that differ after the change-set (only those are written by "Apply this change")."""
    applied = change_set.applied_plan
    after: dict[str, float | int | None] = {
        "owner_equity": applied.plan.owner_equity,
        "loan_amount": applied.loan.amount,
        "loan_term_months": applied.loan.term_months,
        "budget_total": applied.plan.capex,
        "selected_site_index": site_index,
    }
    before: dict[str, float | int | None] = {
        "owner_equity": record.owner_equity,
        "loan_amount": record.loan_amount,
        "loan_term_months": record.loan_term_months,
        "budget_total": record.budget_total,
        "selected_site_index": record.selected_site_index,
    }
    return {field: value for field, value in after.items() if value is not None and value != before[field]}


def apply_patch(record: ProjectRecord, change_set: ChangeSet) -> ApplyPatch:
    """How the frontend applies a change-set: PUT the changed fields (marked as a plan change), re-run the site."""
    applied = change_set.applied_plan
    levers = {change.lever for change in change_set.changes}
    labels = [site.label for site in record.sites]
    site_index = labels.index(applied.site_label) if applied.site_label in labels else record.selected_site_index
    unsupported = sorted(levers - PATCHABLE_LEVERS)
    fields = changed_fields(record, change_set, site_index)
    plan_change = None
    if fields:
        plan_change = PlanChangeIn(
            engine_version=PATH_ENGINE_VERSION,
            fields=sorted(fields),  # type: ignore[arg-type]  # the keys are PLAN_CHANGE_FIELDS by construction
            evidence_needed=list(change_set.evidence_needed),
        )
    return ApplyPatch(
        supported=not unsupported and bool(fields),
        reason=(f"this change-set moves {', '.join(unsupported)}, which the plan form does not hold yet")
        if unsupported
        else (None if fields else "the change-set leaves every plan field as it is"),
        site_index=site_index,
        project_fields=fields,
        plan_change=plan_change,
    )


def field_changes(before: PlanAmounts, after: PlanAmounts) -> list[FieldChange]:
    """Every plan figure that differs between before and after, including the ones that balance a lever's move
    (more owner cash also lowers the loan) and the computed cash reserve."""
    changes = []
    for name, label, unit, plan_field in PLAN_FIGURES:
        old, new = getattr(before, name), getattr(after, name)
        if old != new:
            changes.append(
                FieldChange(
                    field=name, label=label, unit=unit, from_value=old, to_value=new, writes_plan=plan_field is not None
                )
            )
    return changes


def change_set_out(record: ProjectRecord, change_set: ChangeSet) -> ChangeSetOut:
    """A change-set with every figure it changes and its "apply this change" patch."""
    return ChangeSetOut(
        rank=change_set.rank,
        changes=change_set.changes,
        n_levers=change_set.n_levers,
        effort=change_set.effort,
        before=change_set.before,
        after=change_set.after,
        plan_before=change_set.plan_before,
        plan_after=change_set.plan_after,
        meets_target=change_set.meets_target,
        program_rules_not_met=change_set.program_rules_not_met,
        evidence_needed=change_set.evidence_needed,
        field_changes=field_changes(change_set.plan_before, change_set.plan_after),
        explanation=change_set.explanation,
        unchanged=change_set.unchanged,
        apply=apply_patch(record, change_set),
    )


def owner_cash_bounds(record: ProjectRecord, max_extra_owner_cash: float | None) -> dict[str, LeverBounds]:
    """The founder's bound on extra owner cash, as the engine's lever bounds (empty = the engine's default)."""
    if max_extra_owner_cash is None:
        return {}
    return {"owner_equity": LeverBounds(high=record.owner_equity + max_extra_owner_cash)}


def owner_cash_bound(result: PathResult, given: float | None) -> OwnerCashBound | None:
    """The owner-equity bound the search used (None when the owner-equity lever was not available)."""
    lever = next((item for item in result.levers_considered if item.name == "owner_equity"), None)
    if lever is None or lever.high is None or not isinstance(lever.current, int | float):
        return None
    current = float(lever.current)
    return OwnerCashBound(
        current=current,
        high=float(lever.high),
        extra=round(float(lever.high) - current, 2),
        source="founder" if given is not None else "default",
        note=OWNER_CASH_FOUNDER_NOTE if given is not None else default_owner_cash_note(),
    )


def default_owner_cash_note() -> str:
    """The engine's default bound on owner cash in words, read from config/studio/levers.yaml."""
    bounds = load_levers()["owner_equity"].default_bounds
    if bounds is None or bounds.high.basis != "project_cost":
        return f"Default bound from config/studio/levers.yaml (a DESIGN_CHOICE). {OWNER_CASH_CAVEAT}"
    return (
        f"Default bound: owner cash may grow by up to {bounds.high.value:.0%} of the project cost "
        f"(config/studio/levers.yaml), a placeholder (DESIGN_CHOICE) for your own answer. {OWNER_CASH_CAVEAT}"
    )


def not_available_reasons(result: PathResult) -> dict[str, str]:
    """The engine's reasons, with the server's own locks named as such (the founder locked nothing)."""
    reasons = dict(result.levers_not_available)
    for lever in LOCKED_LEVERS:
        if lever in reasons and "locked" in reasons[lever]:
            reasons[lever] = LOCKED_REASON
    return reasons


def run_path_to_approval(
    record: ProjectRecord, site_index: int, targets: TargetsIn | None, max_extra_owner_cash: float | None = None
) -> PathResponse:
    """S5 find_paths in QUICK mode for one site of the plan (the other sites are candidates)."""
    site = site_at(record, site_index)
    score = site_score_or_error(record, site)
    entry = metro_entry(record.metro_id)
    candidates, skipped = candidates_for(record, site_index)
    try:
        case = PlanCase(
            profile_id=record.business_type_id,
            metro_id=record.metro_id,
            site_label=site.label,
            site=as_site_demand(score),
            plan=plan_for(record),
            loan=loan_for(record),
        )
        result: PathResult = find_paths(
            case,
            candidates,
            target_for(targets),
            bounds=owner_cash_bounds(record, max_extra_owner_cash),
            settings=QUICK_SETTINGS.model_copy(update={"seed": record.seed}),
            locked=LOCKED_LEVERS,
            fabric=entry.context.fabric,
        )
    except (GuardrailViolation, LeverError, OutOfBounds, ValueError, TypeError) as error:
        raise InputError(f"the path search cannot run on this plan: {error}") from error
    return PathResponse(
        project_id=record.id,
        project_version=record.version,
        site_index=site_index,
        site_label=site.label,
        label=SIMULATION_LABEL,
        disclaimer=DISCLAIMER,
        status=result.status,
        message=result.message,
        guardrail_note=GUARDRAIL_NOTE,
        guardrails=result.guardrails,
        target=result.target,
        before=result.before,
        plan_before=result.plan_before,
        change_sets=[change_set_out(record, item) for item in result.change_sets],
        closest=change_set_out(record, result.closest) if result.closest is not None else None,
        levers_considered=result.levers_considered,
        levers_not_available=not_available_reasons(result),
        owner_cash_bound=owner_cash_bound(result, max_extra_owner_cash),
        skipped_candidates=skipped,
        notes=[*result.notes, LOCKED_NOTE],
        settings=result.settings,
        search=result.search,
        metro_mode=result.metro_mode,
        data_label=result.data_label,
        engine_version=result.engine_version,
        simulator_version=result.simulator_version,
    )


# ---------------------------------------------------------------------------------------------------------------
# Area suggestions and the tract map layer
# ---------------------------------------------------------------------------------------------------------------


def run_suggest(metro_id: str, business_type_id: str) -> Suggestions:
    """suggest_areas for a metro and business type; Conflict when the metro has no data to rank tracts."""
    entry = metro_entry(metro_id)
    profile = profile_for(business_type_id, metro_id)
    if len(entry.context.tracts) == 0:
        raise Conflict(LIMITED_SUGGEST_MESSAGE)
    result = suggest_areas(profile, entry.config, context=entry.context)
    if not result.areas:
        raise Conflict(LIMITED_SUGGEST_MESSAGE, reasons=[f"{item.reason}: {item.count}" for item in result.excluded])
    return result


def tracts_geojson(metro_id: str) -> dict[str, Any]:
    """Every tract centroid of the metro as GeoJSON Points (no polygons are loaded; no third-party tiles)."""
    entry = metro_catalog().get(metro_id)
    if entry is None:
        raise NotFound("no such metro")
    tracts = entry.context.tracts
    fabric = entry.context.fabric
    features = []
    for index, geoid in enumerate(tracts.ids):
        record = fabric.tract(geoid)
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(float(tracts.lons[index]), 5), round(float(tracts.lats[index]), 5)],
                },
                "properties": {"geoid": geoid, "district": record.district, "data_label": record.data_label},
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "metro_id": metro_id,
            "metro_name": entry.config.name,
            "data_label": entry.config.data_label,
            "mode": entry.option.mode,
            "n_tracts": len(features),
            "geometry": "tract centroids (points); tract boundaries are not loaded",
            "note": "No tract data for this metro here (limited mode)." if not features else None,
        },
    }

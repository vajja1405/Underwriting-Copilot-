"""Founder Studio plans ("projects"): validation, geocoding, storage per user, versions and deletion.

A project is stored as one encrypted JSON object in the web Store (uwcopilot.web.store): namespace
"studio_projects", key = the project id, owner = the signed-in user's stable owner id (Principal.owner_id). The
Store encrypts it at rest (Fernet) and applies the retention period; a user can only ever read their own objects,
and another user's project id simply is "not found".

Every change (PUT) increases `version`, and the result caches (compute.py) are keyed by the version. The simulation
seed is derived from the project id only, so it is the SAME for every version of a plan (common random numbers):
comparing two versions shows the effect of the change, not a different random draw, and applying a path-to-approval
change-set reproduces exactly the outcome the search showed for it. (A seed per version made an applied change-set
that "reaches all three targets" miss them on the next screen: same plan, different draw.)

Who wrote each money figure is kept (Studio rule D: never present an assumption or a suggestion as the founder's
own number): a figure the founder typed is USER_STATED; an empty budget uses the profile's point estimate
(ASSUMPTION); a figure written by "Apply this change" is a PLAN_CHANGE carrying the evidence a lender would need
(ChangeSet.evidence_needed) until the founder types it again; the cash reserve at opening is computed (DERIVED).

A user keeps at most MAX_PLANS_PER_USER plans (a design choice that bounds storage and the plan list's cost).

Sites:
- SYNTHETIC metro: map pins only; a pin must lie within 3 miles of a tract centroid of the generated city.
- real metro: an address is geocoded with the Census Geocoder (geocoding.py) and must fall in one of the metro's
  counties (when the county list is verified); a pin is checked against the snapshot's tracts when there are any,
  otherwise it is accepted and the site says "not checked".
Free text is checked by text_checks.py (no SSNs or account numbers; the idea text is screened for injection).
"""

import hashlib
import secrets
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.studio import DISCLAIMER, SYNTHETIC_LABEL
from uwcopilot.studio.data.errors import NotAvailable, StudioDataError
from uwcopilot.studio.data.provenance import SourceRecord
from uwcopilot.studio.profiles import Profile
from uwcopilot.studio.siteiq.context import OWN_TRACT_MAX_MILES
from uwcopilot.web.schemas_studio import (
    PLAN_CHANGE_FIELDS,
    IdeaOut,
    MoneyOut,
    PlanChangeIn,
    PlanChangeNote,
    ProjectIn,
    ProjectOut,
    ProjectSummary,
    ProjectUpdate,
    RateType,
    SiteIn,
    SiteOut,
)
from uwcopilot.web.services.errors import Conflict, InputError, NotFound, Unavailable
from uwcopilot.web.services.geocoding import Geocoder
from uwcopilot.web.services.studio_options import MetroEntry, assumed_build_out, metro_entry, profile_for
from uwcopilot.web.services.text_checks import injection_flags, refuse_identifiers, without_known_tract_ids
from uwcopilot.web.store import ObjectMeta, ObjectNotFound, Store

PROJECTS_NAMESPACE = "studio_projects"
SCHEMA_VERSION = "studio-project-v1"
# Design choice (labelled ASSUMPTION in the project): an opening date six months after the plan was created.
DEFAULT_OPENING_MONTHS_AHEAD = 6
FLAGGED_NOTE = (
    "Flagged by the injection screen: the text looks like an instruction to a system. It is kept as your own text "
    "but never given to any language model."
)
CLEAN_NOTE = "Your idea, stored as you typed it. v0.1 gives no idea text to any language model."
MONEY_NOTE = (
    "USD. The cash reserve at opening is owner_equity + loan_amount - the build-out budget (the profile's point "
    "estimate when the budget is empty); each simulated future then also takes a build-out overrun out of it."
)
# A design choice: enough for real planning, small enough to bound storage and the cost of the plan list.
MAX_PLANS_PER_USER = 20


class StoredSite(BaseModel):
    """A site as stored: where it is and how that was established."""

    model_config = ConfigDict(extra="forbid")

    label: str
    lat: float
    lon: float
    location_source: Literal["map_pin", "census_geocoder"]
    address: str | None = None
    matched_address: str | None = None
    tract_geoid: str | None = None
    location_check: str
    geocode_source: SourceRecord | None = None


class ProjectRecord(BaseModel):
    """The stored project (the user's own data; encrypted at rest by the Store)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["studio-project-v1"] = SCHEMA_VERSION
    id: str
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    name: str | None = None
    metro_id: str
    business_type_id: str
    idea_text: str | None = None
    idea_flags: list[str] = Field(default_factory=list)
    budget_total: float | None = None
    owner_equity: float
    loan_amount: float
    loan_term_months: int
    annual_rate_pct: float | None = None
    rate_type: RateType = "variable"
    opening_date: date
    opening_date_given: bool
    sites: list[StoredSite]
    selected_site_index: int = Field(default=0, ge=0, description="The site the founder plans with (results page)")
    plan_changes: dict[str, PlanChangeNote] = Field(
        default_factory=dict, description="{field: note} for figures written by 'Apply this change', not typed"
    )

    @property
    def seed(self) -> int:
        """The simulation seed of the plan: the first 8 hex digits of SHA-256(id). The same for every version, so
        versions are compared on the same simulated futures (common random numbers)."""
        return int(hashlib.sha256(self.id.encode()).hexdigest()[:8], 16)


def utc_now() -> datetime:
    """The current time in UTC."""
    return datetime.now(UTC)


def default_opening_date(created: datetime) -> date:
    """The first day of the month DEFAULT_OPENING_MONTHS_AHEAD months after created."""
    index = created.year * 12 + created.month - 1 + DEFAULT_OPENING_MONTHS_AHEAD
    return date(index // 12, index % 12 + 1, 1)


def new_project_id() -> str:
    """16 random hex characters."""
    return secrets.token_hex(8)


# ---------------------------------------------------------------------------------------------------------------
# Site placement
# ---------------------------------------------------------------------------------------------------------------


def check_pin(entry: MetroEntry, site: SiteIn) -> StoredSite:
    """A map pin, checked against the metro's tracts when it has any."""
    lat, lon = float(site.lat), float(site.lon)  # type: ignore[arg-type]  # SiteIn guarantees both are set
    context = entry.context
    if len(context.tracts) == 0:
        return StoredSite(
            label=site.label,
            lat=lat,
            lon=lon,
            location_source="map_pin",
            location_check="not checked: no tract data for this metro here (limited mode)",
        )
    index = context.own_tract(lat, lon)
    if index is None:
        raise InputError(
            f"site '{site.label}' is not within {OWN_TRACT_MAX_MILES:g} miles of any tract of {entry.config.name}; "
            "pick a point on the metro's map"
        )
    return StoredSite(
        label=site.label,
        lat=lat,
        lon=lon,
        location_source="map_pin",
        tract_geoid=context.tracts.ids[index],
        location_check=f"within {OWN_TRACT_MAX_MILES:g} miles of a tract centroid of {entry.config.name}",
    )


def geocoder_down_message(entry: MetroEntry) -> str:
    """What to do when the Census Geocoder cannot be reached (a map pin is offered only where there is a map)."""
    if len(entry.context.tracts) > 0:
        return "the Census Geocoder could not be reached; try again later or drop a pin on the map instead"
    return "the Census Geocoder could not be reached; please try again later"


def check_address(entry: MetroEntry, site: SiteIn, geocoder: Geocoder) -> StoredSite:
    """An address, geocoded (real metros only) and checked against the metro's counties."""
    if entry.config.data_label == SYNTHETIC_LABEL:
        raise InputError("the SYNTHETIC metro has no addresses; pick sites on its map (lat and lon)")
    address = str(site.address)
    try:
        match = geocoder.geocode(address)
    except NotAvailable as error:
        raise InputError(f"site '{site.label}': {error}") from error
    except StudioDataError as error:  # unreachable host, an error answer, an unreadable body
        raise Unavailable(geocoder_down_message(entry)) from error
    county = match.state_fips + match.county_fips
    if entry.config.verified:
        if county not in entry.config.county_geoids():
            raise InputError(
                f"site '{site.label}' is in county {county}, which is not one of the counties of "
                f"{entry.config.name} (Census delineation file)"
            )
        check = f"geocoded by the Census Geocoder; county {county} is in {entry.config.name}"
    else:
        check = f"geocoded by the Census Geocoder (county {county}); the metro's county list is not verified"
    return StoredSite(
        label=site.label,
        lat=match.lat,
        lon=match.lon,
        location_source="census_geocoder",
        address=address,
        matched_address=match.matched_address,
        tract_geoid=match.tract_geoid,
        location_check=check,
        geocode_source=match.source,
    )


def check_site_text(entry: MetroEntry, site: SiteIn, position: int) -> None:
    """No SSN or account number in a site's label or address (position: 1, 2 or 3; the text itself is not repeated
    in the message). The metro's own tract ids are allowed in a label (the wizard writes them). Runs BEFORE any
    address goes to the geocoder, so a refused address never leaves the server."""
    refuse_identifiers(without_known_tract_ids(site.label, entry.context.tracts.ids), f"the name of site {position}")
    if site.address is not None:
        refuse_identifiers(site.address, f"the address of site {position}")


def addresses_to_geocode(sites: list[SiteIn], known: list[StoredSite] | None = None) -> list[str]:
    """The addresses of a request that need a geocoder lookup (an address already in the stored plan is reused)."""
    reusable = {item.address for item in known or [] if item.address}
    return [site.address for site in sites if site.address is not None and site.address not in reusable]


def check_sites_text(metro_id: str, sites: list[SiteIn]) -> None:
    """check_site_text for every site (the router calls it before any lookup)."""
    entry = metro_entry(metro_id)
    for position, site in enumerate(sites, start=1):
        check_site_text(entry, site, position)


def place_sites(
    entry: MetroEntry, sites: list[SiteIn], geocoder: Geocoder, known: list[StoredSite] | None = None
) -> list[StoredSite]:
    """Check every site; an address already geocoded in the stored plan (known) is reused, not geocoded again."""
    labels = [site.label.casefold() for site in sites]
    if len(set(labels)) != len(labels):
        raise InputError("every site needs its own label")
    for position, site in enumerate(sites, start=1):
        check_site_text(entry, site, position)
    reusable = {item.address: item for item in known or [] if item.address}
    placed = []
    for site in sites:
        if site.address is not None and site.address in reusable:
            reused = reusable[site.address].model_copy(update={"label": site.label})
            placed.extend(recheck_stored_sites(entry, [reused]))
        elif site.address is not None:
            placed.append(check_address(entry, site, geocoder))
        else:
            placed.append(check_pin(entry, site))
    return placed


def recheck_stored_sites(entry: MetroEntry, sites: list[StoredSite]) -> list[StoredSite]:
    """Stored sites under a (possibly new) metro: pins are checked again, addresses must be in the metro."""
    checked = []
    for site in sites:
        if site.location_source == "map_pin":
            checked.append(check_pin(entry, SiteIn(label=site.label, lat=site.lat, lon=site.lon)))
            continue
        if entry.config.data_label == SYNTHETIC_LABEL:
            raise InputError("the SYNTHETIC metro has no addresses; pick sites on its map (lat and lon)")
        county = site.geocode_source.geography[:5] if site.geocode_source and site.geocode_source.geography else ""
        if entry.config.verified and county not in entry.config.county_geoids():
            raise InputError(f"site '{site.label}' is not in {entry.config.name}; give its sites again")
        checked.append(site)
    return checked


# ---------------------------------------------------------------------------------------------------------------
# Building and checking a record
# ---------------------------------------------------------------------------------------------------------------


def effective_build_out(record: ProjectRecord, profile: Profile) -> float:
    """The build-out budget the simulator uses: the founder's budget_total, else the profile's point estimate."""
    return record.budget_total if record.budget_total is not None else assumed_build_out(profile)


def check_money(record: ProjectRecord) -> None:
    """The plan's sources must cover its build-out budget (the simulator refuses a negative starting cash). An empty
    budget is checked against the profile's point estimate, the number the simulator will use, and says so."""
    profile = profile_for(record.business_type_id, record.metro_id)
    build_out = effective_build_out(record, profile)
    sources = record.owner_equity + record.loan_amount
    if sources < build_out:
        whose = (
            "your build-out budget"
            if record.budget_total is not None
            else f"the {profile.display_name.lower()} build-out the simulation assumes when the budget is empty "
            "(ASSUMPTION; type your own budget to replace it)"
        )
        raise InputError(
            f"your cash plus the loan (${sources:,.0f}) must cover {whose} of ${build_out:,.0f}: otherwise the plan "
            "opens with negative cash"
        )


def check_opening_date(value: date, today: date) -> None:
    """An opening date from one year ago to five years ahead (a design choice: Studio plans a near opening)."""
    earliest = date(today.year - 1, today.month, min(today.day, 28))
    latest = date(today.year + 5, today.month, min(today.day, 28))
    if not earliest <= value <= latest:
        raise InputError(f"opening_date must be between {earliest.isoformat()} and {latest.isoformat()}")


def check_selected_site(record: ProjectRecord) -> None:
    """The selected site must be one of the plan's sites."""
    if record.selected_site_index >= len(record.sites):
        raise InputError(
            f"selected_site_index {record.selected_site_index} does not exist; the plan has {len(record.sites)} site(s)"
        )


def plan_change_notes(
    current: dict[str, PlanChangeNote], given: set[str], change: PlanChangeIn | None, now: datetime
) -> dict[str, PlanChangeNote]:
    """The plan-change notes after a PUT: a field the founder sets again is theirs (its note is dropped); fields the
    PUT writes as an applied change-set get a note with the evidence a lender would need."""
    notes = {field: note for field, note in current.items() if field not in given}
    if change is not None:
        missing = sorted(set(change.fields) - given)
        if missing:
            raise InputError(f"plan_change names field(s) the request does not set: {', '.join(missing)}")
        for field in change.fields:
            notes[field] = PlanChangeNote(
                source=change.source,
                engine_version=change.engine_version,
                applied_at=now,
                evidence_needed=change.evidence_needed,
            )
    return notes


def check_text(name: str | None, idea_text: str | None) -> list[str]:
    """Refuse forbidden identifiers in the name and idea; return the idea's injection flags."""
    refuse_identifiers(name, "the plan name")
    refuse_identifiers(idea_text, "the idea text")
    return injection_flags(idea_text)


def build_record(body: ProjectIn, geocoder: Geocoder, now: datetime | None = None) -> ProjectRecord:
    """A new, checked ProjectRecord (version 1) from a POST body. Addresses are looked up through geocoder (the
    router passes a PrefetchedGeocoder, so no network call happens here)."""
    created = now or utc_now()
    entry = metro_entry(body.metro_id)
    profile_for(body.business_type_id, body.metro_id)
    if body.opening_date is not None:
        check_opening_date(body.opening_date, created.date())
    flags = check_text(body.name, body.idea_text)
    record = ProjectRecord(
        id=new_project_id(),
        version=1,
        created_at=created,
        updated_at=created,
        name=body.name or None,
        metro_id=body.metro_id,
        business_type_id=body.business_type_id,
        idea_text=body.idea_text or None,
        idea_flags=flags,
        budget_total=body.budget_total,
        owner_equity=body.owner_equity,
        loan_amount=body.loan_amount,
        loan_term_months=body.loan_term_months,
        annual_rate_pct=body.annual_rate_pct,
        rate_type=body.rate_type,
        opening_date=body.opening_date or default_opening_date(created),
        opening_date_given=body.opening_date is not None,
        sites=place_sites(entry, body.sites, geocoder),
    )
    check_money(record)
    return record


def updated_record(
    current: ProjectRecord, body: ProjectUpdate, geocoder: Geocoder, now: datetime | None = None
) -> ProjectRecord:
    """The next version of a record with the PUT body's fields applied and checked again."""
    if body.version is not None and body.version != current.version:
        raise Conflict(
            f"the plan changed meanwhile (stored version {current.version}, yours {body.version}); reload it"
        )
    changed_at = now or utc_now()
    given = body.model_fields_set - {"version", "plan_change"}
    required_fields = (
        "metro_id",
        "business_type_id",
        "owner_equity",
        "loan_amount",
        "loan_term_months",
        "rate_type",
        "selected_site_index",
    )
    for required in required_fields:
        if required in given and getattr(body, required) is None:
            raise InputError(f"{required} cannot be empty")
    values: dict[str, Any] = current.model_dump()
    for field in given - {"sites"}:
        values[field] = getattr(body, field)
    if "opening_date" in given:
        values["opening_date_given"] = body.opening_date is not None
        if body.opening_date is None:
            values["opening_date"] = default_opening_date(current.created_at)
        else:
            check_opening_date(body.opening_date, changed_at.date())
    entry = metro_entry(values["metro_id"])
    profile_for(values["business_type_id"], values["metro_id"])
    values["idea_flags"] = check_text(values["name"], values["idea_text"])
    if "sites" in given and body.sites is not None:
        sites = place_sites(entry, body.sites, geocoder, known=current.sites)
        if "selected_site_index" not in given and values["selected_site_index"] >= len(sites):
            values["selected_site_index"] = 0
    else:
        sites = recheck_stored_sites(entry, current.sites)
    notes = plan_change_notes(current.plan_changes, given & set(PLAN_CHANGE_FIELDS), body.plan_change, changed_at)
    values.update(
        sites=sites,
        version=current.version + 1,
        updated_at=changed_at,
        plan_changes={field: note.model_dump() for field, note in notes.items()},
    )
    record = ProjectRecord.model_validate(values)
    check_selected_site(record)
    check_money(record)
    return record


# ---------------------------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------------------------


def save(store: Store, owner: str, record: ProjectRecord) -> ObjectMeta:
    """Write (or overwrite) the record; a write starts a new retention period."""
    return store.put_json(owner, PROJECTS_NAMESPACE, record.id, record.model_dump(mode="json"))


def check_plan_count(store: Store, owner: str) -> None:
    """Conflict when the user already keeps MAX_PLANS_PER_USER plans (reads only the metadata, decrypts nothing)."""
    count = len(store.list(owner, PROJECTS_NAMESPACE))
    if count >= MAX_PLANS_PER_USER:
        raise Conflict(
            f"you have {count} plans, the most one account can keep in this prototype; delete one to start another"
        )


def create(store: Store, owner: str, body: ProjectIn, geocoder: Geocoder) -> tuple[ProjectRecord, ObjectMeta]:
    """Check the plan count, build the record and save it (blocking: the router runs it in a worker thread)."""
    check_plan_count(store, owner)
    record = build_record(body, geocoder)
    return record, save(store, owner, record)


def update(
    store: Store, owner: str, project_id: str, body: ProjectUpdate, geocoder: Geocoder
) -> tuple[ProjectRecord, ObjectMeta]:
    """Load, change and save a plan (blocking: the router runs it in a worker thread)."""
    current = load(store, owner, project_id)
    record = updated_record(current, body, geocoder)
    return record, save(store, owner, record)


def load(store: Store, owner: str, project_id: str) -> ProjectRecord:
    """The user's record, or NotFound (also for another user's id and for an expired plan)."""
    try:
        data = store.get_json(owner, PROJECTS_NAMESPACE, project_id)
    except ObjectNotFound as error:
        raise NotFound("no such plan") from error
    return ProjectRecord.model_validate(data)


def meta_for(store: Store, owner: str, project_id: str) -> ObjectMeta | None:
    """The stored object's metadata (retention), or None."""
    for meta in store.list(owner, PROJECTS_NAMESPACE):
        if meta.key == project_id:
            return meta
    return None


def list_records(store: Store, owner: str) -> list[tuple[ProjectRecord, ObjectMeta]]:
    """Every unexpired plan of the user, newest change first."""
    items = []
    for meta in store.list(owner, PROJECTS_NAMESPACE):
        try:
            items.append((load(store, owner, meta.key), meta))
        except NotFound:
            continue
    return sorted(items, key=lambda item: item[0].updated_at, reverse=True)


# ---------------------------------------------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------------------------------------------


def site_out(index: int, site: StoredSite) -> SiteOut:
    """A stored site as the API shows it."""
    return SiteOut(index=index, **site.model_dump())


def field_status(record: ProjectRecord, field: str, typed: bool = True) -> str:
    """USER_STATED (typed), ASSUMPTION (left empty) or PLAN_CHANGE (written by an applied change-set)."""
    if field in record.plan_changes:
        return "PLAN_CHANGE"
    return "USER_STATED" if typed else "ASSUMPTION"


def money_out(record: ProjectRecord) -> MoneyOut:
    """The plan's money with the status of every figure (see the module docstring)."""
    profile = profile_for(record.business_type_id, record.metro_id)
    build_out = effective_build_out(record, profile)
    budget_status = field_status(record, "budget_total", typed=record.budget_total is not None)
    return MoneyOut(
        budget_total=record.budget_total,
        budget_status=budget_status,
        build_out_assumed=None if record.budget_total is not None else build_out,
        owner_equity=record.owner_equity,
        owner_equity_status=field_status(record, "owner_equity"),
        loan_amount=record.loan_amount,
        loan_amount_status=field_status(record, "loan_amount"),
        loan_term_months=record.loan_term_months,
        loan_term_status=field_status(record, "loan_term_months"),
        annual_rate_pct=record.annual_rate_pct,
        annual_rate_status="USER_STATED" if record.annual_rate_pct is not None else "ASSUMPTION",
        rate_type=record.rate_type,
        cash_reserve_at_opening=round(record.owner_equity + record.loan_amount - build_out, 2),
        cash_reserve_status="DERIVED" if record.budget_total is not None else "ASSUMPTION",
        plan_changes=record.plan_changes,
        note=MONEY_NOTE,
    )


def project_out(record: ProjectRecord, meta: ObjectMeta | None) -> ProjectOut:
    """The full project response."""
    entry = metro_entry(record.metro_id)
    profile = profile_for(record.business_type_id, record.metro_id)
    flagged = bool(record.idea_flags)
    return ProjectOut(
        id=record.id,
        version=record.version,
        name=record.name,
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=meta.expires_at if meta else None,
        retention_days=meta.retention_days if meta else None,
        metro_id=record.metro_id,
        metro_name=entry.config.name,
        metro_mode=entry.option.mode,
        data_label=entry.config.data_label,
        business_type_id=record.business_type_id,
        business_type_name=profile.display_name,
        demand_unit=profile.demand_unit,
        idea=IdeaOut(
            text=record.idea_text,
            flagged=flagged,
            flags=record.idea_flags,
            note=FLAGGED_NOTE if flagged else CLEAN_NOTE,
        ),
        money=money_out(record),
        opening_date=record.opening_date,
        opening_date_status="USER_STATED" if record.opening_date_given else "ASSUMPTION",
        sites=[site_out(index, site) for index, site in enumerate(record.sites)],
        selected_site_index=record.selected_site_index,
        selected_site_status=field_status(record, "selected_site_index"),
        seed=record.seed,
        disclaimer=DISCLAIMER,
    )


def project_summary(record: ProjectRecord, meta: ObjectMeta | None) -> ProjectSummary:
    """One row of the plan list."""
    entry = metro_entry(record.metro_id)
    profile = profile_for(record.business_type_id, record.metro_id)
    return ProjectSummary(
        id=record.id,
        version=record.version,
        name=record.name,
        metro_id=record.metro_id,
        metro_name=entry.config.name,
        business_type_id=record.business_type_id,
        business_type_name=profile.display_name,
        n_sites=len(record.sites),
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=meta.expires_at if meta else None,
        idea_flagged=bool(record.idea_flags),
    )

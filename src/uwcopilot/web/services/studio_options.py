"""What the founder can choose: metros (with their honest data mode), business types, levers and defaults.

Everything here is read from the Studio loaders and config files; nothing is typed in. A metro's mode comes from
its data fabric, i.e. from the snapshot that is actually present where the app runs: the SYNTHETIC metro is always
"synthetic"; a real metro without a snapshot (the Hugging Face Space has none) is "limited". The answer is computed
once per process (metro_catalog) because a real metro's mode reads its whole snapshot. Profiles (business types with
a metro's overrides) are also loaded once per process (profile_for): they are static files, and parsing one costs
about 16 ms, which a plan list of many plans paid once per plan.
"""

from dataclasses import dataclass
from functools import lru_cache

from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL, SYNTHETIC_LABEL
from uwcopilot.studio.feasibility.assumptions import default_dscr_target, load_assumptions
from uwcopilot.studio.metros import MetroConfig, MetroNotFound, list_metros, load_metro
from uwcopilot.studio.path_to_approval.levers import load_levers
from uwcopilot.studio.path_to_approval.targets import default_target
from uwcopilot.studio.profiles import Profile, ProfileNotFound, list_profiles, load_profile
from uwcopilot.studio.siteiq.context import MetroContext, metro_context
from uwcopilot.web.schemas_studio import (
    BusinessTypeOption,
    LeverOption,
    MetroOption,
    OptionsResponse,
    RateDefault,
)
from uwcopilot.web.services.errors import InputError

# The simulator's DSCR target label says "editable" (the engine accepts another threshold); the web app keeps it fixed
# so the gauges keep one meaning, and says so.
WEB_DSCR_TARGET_LABEL = (
    "lender policy (SYNTHETIC Northwind guideline NBC-4.2); fixed in the web app, so every simulation is measured "
    "against the same threshold"
)

RATE_RULE = (
    "Leave the rate empty to use the metro's prime rate plus the spread assumption below (labelled ASSUMPTION in "
    "every result). When a metro has no prime rate (UNKNOWN), enter the lender's quoted rate."
)


@dataclass(frozen=True)
class MetroEntry:
    """A metro with its config, its SiteIQ context (fabric + caches) and its option row."""

    config: MetroConfig
    context: MetroContext
    option: MetroOption


def coverage_note(mode: str, reason: str, simulation_available: bool = True) -> str:
    """The one line the city picker shows under a metro (says when the simulation cannot run there)."""
    if mode == "synthetic":
        return "SYNTHETIC demo city: generated data, not a real place."
    if mode == "limited" and not simulation_available:
        return (
            f"Limited data ({reason}). Sites can be added and scored only on the factors that have data; without "
            "data, demand is UNKNOWN, so the simulation and the path to approval do not run for this city here. "
            "The SYNTHETIC demo city shows the full flow."
        )
    if mode == "limited":
        return f"Limited data ({reason}). Factors without data are shown as UNKNOWN, never filled in."
    return f"Full data mode ({reason})."


def any_profile_id() -> str:
    """A profile id for metro-level inputs that do not depend on the business type (the prime rate)."""
    return list_profiles()[0]


def build_metro_entry(metro_id: str) -> MetroEntry:
    """Load one metro's context and describe it."""
    config = load_metro(metro_id)
    context = metro_context(metro_id)
    mode, reason = context.mode()
    n_tracts = len(context.tracts)
    prime = context.fabric.metro_inputs(load_profile(any_profile_id(), metro_id=metro_id)).get("prime_rate")
    option = MetroOption(
        id=config.id,
        name=config.name,
        mode=mode,
        mode_reason=reason,
        data_label=config.data_label,
        coverage_note=coverage_note(mode, reason, simulation_available=n_tracts > 0),
        verified=config.verified,
        n_tracts=n_tracts,
        site_input="map" if config.data_label == SYNTHETIC_LABEL else "address_or_map",
        supports_area_suggestions=n_tracts > 0,
        simulation_available=n_tracts > 0,
        prime_rate=prime,
    )
    return MetroEntry(config=config, context=context, option=option)


@lru_cache(maxsize=1)
def metro_catalog() -> dict[str, MetroEntry]:
    """Every metro, loaded once per process."""
    return {metro_id: build_metro_entry(metro_id) for metro_id in list_metros()}


def metro_entry(metro_id: str) -> MetroEntry:
    """One metro, or InputError for an unknown id."""
    entry = metro_catalog().get(metro_id)
    if entry is None:
        raise InputError(f"unknown metro {metro_id!r}")
    return entry


@lru_cache(maxsize=128)
def cached_profile(business_type_id: str, metro_id: str) -> Profile:
    """load_profile with the metro's overrides, once per process (a Profile is frozen, so sharing it is safe).
    Failed loads are not cached (lru_cache keeps only returned values)."""
    return load_profile(business_type_id, metro_id=metro_id)


def profile_for(business_type_id: str, metro_id: str) -> Profile:
    """The business-type profile with the metro's overrides, or InputError for an unknown id."""
    try:
        return cached_profile(business_type_id, metro_id)
    except (ProfileNotFound, MetroNotFound) as error:
        raise InputError(f"unknown business type {business_type_id!r}") from error


def assumed_build_out(profile: Profile) -> float:
    """The build-out the simulator uses when the founder leaves the budget empty: the sum of the capex items' most
    likely values (studio.feasibility.setup does the same), USD."""
    return float(sum(item.cost.point() for item in profile.capex))


def business_type_option(profile: Profile) -> BusinessTypeOption:
    """A profile as a picker row, with its build-out range (the sum of its capex items) and assumed point."""
    lows = [item.cost.bounds()[0] for item in profile.capex]
    highs = [item.cost.bounds()[1] for item in profile.capex]
    assumed = any(item.cost.status == "ASSUMPTION" for item in profile.capex)
    return BusinessTypeOption(
        id=profile.id,
        name=profile.display_name,
        demand_unit=profile.demand_unit,
        build_out_low=float(sum(lows)),
        build_out_high=float(sum(highs)),
        build_out_status="ASSUMPTION" if assumed else "SOURCED",
        build_out_assumed=assumed_build_out(profile),
        build_out_note="Sum of the profile's capex ranges (config/studio/business_types); with the budget left empty "
        "the simulation uses build_out_assumed, the sum of their most likely values. Contractor quotes replace them.",
    )


def lever_options() -> list[LeverOption]:
    """The path-search levers with their default bounds (config/studio/levers.yaml)."""
    return [
        LeverOption(
            name=name,
            label=config.label,
            unit=config.unit,
            kind=config.kind,
            default_bounds=config.default_bounds,
            options=config.options,
            round_to=config.round_to,
            effort_weight=config.effort_weight,
            note=config.note,
        )
        for name, config in load_levers().items()
    ]


@lru_cache(maxsize=1)
def studio_options() -> OptionsResponse:
    """GET /api/studio/options, built once per process."""
    return OptionsResponse(
        metros=[entry.option for entry in metro_catalog().values()],
        business_types=[business_type_option(load_profile(profile_id)) for profile_id in list_profiles()],
        levers=lever_options(),
        default_target=default_target(),
        dscr_target=default_dscr_target().model_copy(update={"label": WEB_DSCR_TARGET_LABEL}),
        annual_rate_default=RateDefault(
            rule=RATE_RULE, spread_over_prime_pct=load_assumptions().rate_spread_over_prime_pct
        ),
        disclaimer=DISCLAIMER,
        simulation_label=SIMULATION_LABEL,
        synthetic_label=SYNTHETIC_LABEL,
    )

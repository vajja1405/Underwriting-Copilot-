"""Claims for the simulator's inputs that the founder did not type: profile and metro ranges, data, the note rate,
the build-out cost when the founder left it empty, and the site's demand range.

Every simulated input is listed in FeasibilityResult.distributions with a status and a source. What each becomes:
- USER_STATED because the founder typed the figure in the plan: no claim here, founder_claims.py has it;
- USER_STATED because the founder edited its range (Plan.assumption_overrides): a USER_STATED claim under the same
  ASSUME.<name> id with the edited range, since that range is what the simulator sampled (the founder's note is kept
  in the details only; it is free text and never becomes prose or a prompt);
- ASSUMPTION (a profile or simulator planning range): ASSUMPTION, source "unsourced" (the profile's note says which
  source should replace it; it is kept in the claim's details), with its low-high range;
- SOURCED (a profile rate from a cited publication): ASSUMPTION with that source and its range (a published average
  applied to this plan is still an assumption about this plan);
- DATA (public data for this metro): VERIFIED public_data with the dataset in its source;
- DATA_X_ASSUMPTION (public data converted with a labeled assumption): ASSUMPTION with the data source and range;
- SYNTHETIC (values of the generated SYNTHETIC city): VERIFIED public_data whose source and label say SYNTHETIC,
  because within the SYNTHETIC metro they play the role of public data;
- the site's demand ("mature_demand") gets its own claims (site_demand_claims below).
Shares and probabilities are stored as percents (units.py). The value is the distribution's mode (the "most likely"
value the one-forecast case uses); the range is its low and high.
"""

from uwcopilot.evidence.ledger_models import UNSOURCED, Claim, InputValue, Provenance, ValueRange
from uwcopilot.studio.feasibility.assumptions import FeasibilityAssumptions, load_assumptions
from uwcopilot.studio.feasibility.distributions import Distribution
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.derive import (
    assumption_range_claim,
    derived_claim,
    model_claim,
    public_data_claim,
)
from uwcopilot.studio.narrative.units import format_for
from uwcopilot.studio.profiles.loader import load_profile

SITE_DEMAND_INPUT = "mature_demand"
SYNTHETIC_PREFIX = "SYNTHETIC city data: "
SITEIQ_MODEL = "siteiq"
SITE_DEMAND_UNCERTAINTY = "SiteIQ range from {low} to {high} {unit} (every rate at its low or high end at once)"


def edited_range_claim(item: Distribution, metro_id: str) -> Claim:
    """USER_STATED: a range the founder edited (Plan.assumption_overrides); its most likely value and its range."""
    fmt = format_for(item.unit, item.mode)
    value, low, high = fmt.scale(item.mode), fmt.scale(item.low), fmt.scale(item.high)
    field = f"plan.assumption_overrides.{item.name}"
    return Claim(
        id=ids.assume_id(item.name),
        label=f"{item.description} (range edited by the founder)",
        value=value,
        unit=fmt.unit,
        display=fmt.display,
        status="USER_STATED",
        provenance=Provenance(
            kind="user_input",
            ref=field,
            metro_id=metro_id,
            details={"founder_note": item.source, "shape": item.kind, "unit_text": item.unit},
        ),
        inputs={"field": field, "value": value, "low": low, "high": high},
        value_range=ValueRange(low=float(low), high=float(high)) if low is not None and high is not None else None,
    )


def distribution_claim(item: Distribution, metro_id: str, overridden: frozenset[str] = frozenset()) -> Claim | None:
    """The claim for one simulator input that the founder did not type as a plan figure (None for a typed figure and
    for the site demand). overridden: the inputs whose range the founder edited."""
    if item.name == SITE_DEMAND_INPUT:
        return None
    if item.status == "USER_STATED":
        return edited_range_claim(item, metro_id) if item.name in overridden else None
    fmt = format_for(item.unit, item.mode)
    value, low, high = fmt.scale(item.mode), fmt.scale(item.low), fmt.scale(item.high)
    claim_id = ids.assume_id(item.name)
    details: dict[str, InputValue] = {"where": item.source, "shape": item.kind, "unit_text": item.unit}
    if item.status in ("DATA", "SYNTHETIC"):
        synthetic = item.status == "SYNTHETIC"
        source = f"{SYNTHETIC_PREFIX}{item.source}" if synthetic else item.source
        label = f"{item.description} (SYNTHETIC)" if synthetic else item.description
        return public_data_claim(
            claim_id,
            label,
            value,
            fmt.unit,
            fmt.display,
            source,
            item.name,
            metro_id=metro_id,
            details={**details, "low": low, "high": high, "data_label": "SYNTHETIC" if synthetic else "PUBLIC"},
        )
    source = UNSOURCED if item.status == "ASSUMPTION" else item.source
    return assumption_range_claim(
        claim_id, item.description, value, fmt.unit, fmt.display, source, item.name, low, high, metro_id, details
    )


def distribution_claims(result: FeasibilityResult, overridden: frozenset[str] = frozenset()) -> list[Claim]:
    """Claims for every simulator input except the founder's typed figures and the site demand."""
    claims = []
    for item in result.distributions:
        claim = distribution_claim(item, result.metro_id, overridden)
        if claim is not None:
            claims.append(claim)
    return claims


def capex_claim(result: FeasibilityResult) -> Claim | None:
    """ASSUMPTION for the build-out cost when the founder left it empty (the sum of the profile's capex modes)."""
    if result.capex_status != "ASSUMPTION":
        return None
    profile = load_profile(result.profile_id, metro_id=result.metro_id)
    low = sum(item.cost.bounds()[0] for item in profile.capex)
    high = sum(item.cost.bounds()[1] for item in profile.capex)
    sourced = all(item.cost.status == "SOURCED" for item in profile.capex)
    source = "; ".join(f"{item.name}: {item.cost.source}" for item in profile.capex) if sourced else UNSOURCED
    names = ", ".join(item.name for item in profile.capex)
    return assumption_range_claim(
        ids.ASSUME_CAPEX,
        "Build-out and equipment cost (sum of the profile's line items; the founder left it empty)",
        result.planned_capex,
        "USD",
        "usd0",
        source,
        f"profile {result.profile_id} capex",
        min(low, result.planned_capex),
        max(high, result.planned_capex),
        result.metro_id,
        {"items": names},
    )


def rate_claim(
    result: FeasibilityResult,
    founder_rate: Claim | None,
    founder_spread: Claim | None,
    assumptions: FeasibilityAssumptions,
) -> Claim:
    """RATE.note_rate_pct: the note rate the simulation used.

    The founder's quoted rate (USER_STATED, derived from FOUNDER.annual_rate_pct), or the metro's prime rate plus a
    spread. Prime plus the ASSUMPTION spread is an ASSUMPTION whose range moves the spread over its own range. Prime
    plus a spread the founder typed is kept as an ASSUMPTION of zero width: the prime rate's own status is not
    re-read here (the source text says where it came from), so the rate is never shown as more certain than that.
    """
    loan = result.loan
    label = "Note rate used by the simulation"
    if founder_rate is not None:
        return derived_claim(
            ids.RATE_NOTE, label, loan.annual_rate_pct, "PERCENT", "pct1", "quoted rate", [founder_rate]
        )
    spread = assumptions.rate_spread_over_prime_pct
    spread_low, spread_high = spread.bounds()
    spread_used = spread.point()
    low = loan.annual_rate_pct - spread_used + spread_low
    high = loan.annual_rate_pct - spread_used + spread_high
    if founder_spread is not None:
        low = high = loan.annual_rate_pct
    return assumption_range_claim(
        ids.RATE_NOTE,
        label,
        loan.annual_rate_pct,
        "PERCENT",
        "pct1",
        loan.rate_source,
        f"prime rate + spread ({loan.rate_status})",
        min(low, loan.annual_rate_pct),
        max(high, loan.annual_rate_pct),
        result.metro_id,
        {"rate_status": loan.rate_status},
    )


def synthetic_source(source: str) -> str:
    """A SYNTHETIC demand range's source, labeled SYNTHETIC once (a generated plan is not city data)."""
    return source if source.upper().startswith("SYNTHETIC") else f"SYNTHETIC: {source}"


def site_demand_claims(result: FeasibilityResult, run: dict[str, InputValue]) -> list[Claim]:
    """The site's demand (most likely, low and high) with the status of what the simulator received.

    MODEL_ESTIMATE: SiteIQ's estimate (model siteiq). USER_STATED: the founder's own range. ASSUMPTION: a SYNTHETIC
    or assumed range handed to the simulator (for example the golden scenarios' generated range): it is not data.
    """
    site = result.site_demand
    fmt = format_for(site.unit, site.mode)
    triples = (
        (ids.SITE_DEMAND_MODE, "Most likely demand at the site after ramp-up", site.mode),
        ("SITE.demand_low", "Low end of the site's demand range", site.low),
        ("SITE.demand_high", "High end of the site's demand range", site.high),
    )
    claims: list[Claim] = []
    for claim_id, label, value in triples:
        label = f"{label} ({site.unit})"
        if site.status == "MODEL_ESTIMATE":
            uncertainty = SITE_DEMAND_UNCERTAINTY.format(low=site.low, high=site.high, unit=site.unit)
            claims.append(
                model_claim(
                    claim_id,
                    label,
                    value,
                    fmt.unit,
                    fmt.display,
                    SITEIQ_MODEL,
                    "siteiq-v1",
                    uncertainty,
                    {"source": site.source, "metro_id": result.metro_id},
                    f"site_demand.{claim_id.rsplit('_', 1)[-1]}",
                )
            )
        elif site.status == "USER_STATED":
            claims.append(founder_range_claim(claim_id, label, value, fmt.unit, fmt.display, site.source))
        else:
            source = synthetic_source(site.source) if site.status == "SYNTHETIC" else site.source
            claims.append(
                assumption_range_claim(
                    claim_id,
                    label,
                    value,
                    fmt.unit,
                    fmt.display,
                    source,
                    "site demand range given to the simulator",
                    site.low,
                    site.high,
                    result.metro_id,
                    {"site_status": site.status, **{key: run[key] for key in ("plan_sha256",) if key in run}},
                )
            )
    return claims


def founder_range_claim(claim_id: str, label: str, value: float, unit: str, display: str, source: str) -> Claim:
    """A demand figure from the founder's own range (USER_STATED)."""
    return Claim.model_validate(
        {
            "id": claim_id,
            "label": label,
            "value": value,
            "unit": unit,
            "display": display,
            "status": "USER_STATED",
            "provenance": {"kind": "user_input", "ref": "site demand range", "details": {"source": source}},
            "inputs": {"field": "site", "value": value},
        }
    )


def assumption_claims(
    result: FeasibilityResult,
    founder_rate: Claim | None,
    founder_spread: Claim | None,
    run: dict[str, InputValue],
    overridden: frozenset[str] = frozenset(),
) -> list[Claim]:
    """Every simulator input the founder did not type, the note rate, the capex assumption and the site demand.
    overridden: names of the inputs whose range the founder edited (Plan.assumption_overrides)."""
    claims = [*distribution_claims(result, overridden), *site_demand_claims(result, run)]
    capex = capex_claim(result)
    if capex is not None:
        claims.append(capex)
    claims.append(rate_claim(result, founder_rate, founder_spread, load_assumptions()))
    return claims

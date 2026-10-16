"""Claims the package adds about sites: the other candidate sites, competition details and the map's rings.

Every one goes into the SAME founder ledger (build_founder_ledger's extra_claims), so the package keeps one ledger
and every number it prints has a claim. Ids start with "PKG.".

- Site comparison: each other candidate site is simulated with the founder's plan, the same seed and the same number
  of paths as the chosen site (common random numbers, as the path search does), with the candidate's own rent quote
  (or the metro range when it has none: a quote belongs to its own site, the S5 rule). Its demand range keeps the
  status of what the simulator received (narrative.assumption_claims.site_demand_claims) and its three simulated
  probabilities are MODEL_ESTIMATE claims of that run (narrative.simulation_claims). Its label is the founder's
  free text: a USER_STATED text claim, printed as data.
- Competition details (count in the market ring, distance to the nearest one, count per 10,000 residents): read from
  SiteIQ's competition factor details, with the same source and status as the factor's own claim (VERIFIED public
  data or SYNTHETIC city data). Not added when the factor is not VERIFIED.
- Map rings: the near and market ring radii SiteIQ used. They come from the metro's trade-area config times the
  business type's ring multiplier, a design choice of the project author: ASSUMPTION, unsourced, zero-width range.
"""

from uwcopilot.evidence.ledger_models import Claim, Provenance
from uwcopilot.studio.data.fabric_base import DataFabric
from uwcopilot.studio.feasibility import simulate
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative.assumption_claims import site_demand_claims
from uwcopilot.studio.narrative.derive import assumption_range_claim, public_data_claim
from uwcopilot.studio.narrative.founder_ledger import run_identity
from uwcopilot.studio.narrative.simulation_claims import SimulationClaims
from uwcopilot.studio.path_to_approval.models import Candidate, PlanCase
from uwcopilot.studio.siteiq.models import SiteScore

CANDIDATE_PREFIX = "PKG.site"
CHOSEN_SITE_KEY = "c0"
COMPETITION_DETAILS: tuple[tuple[str, str, str, str], ...] = (
    ("count_market", "Same-type businesses within the market ring", "COUNT", "int"),
    ("nearest_miles", "Distance to the nearest same-type business", "MILES", "ratio2"),
    (
        "per_10k_residents_market",
        "Competitor density in the market ring, SiteIQ's per-resident measure",
        "RATIO",
        "ratio2",
    ),
)
RING_SOURCE = (
    "SiteIQ trade area: the metro's ring radii (config/studio/metros/<metro>.yaml) times the business type's ring "
    "multiplier; a design choice of the project author, unsourced"
)
TEXT_LABEL_FIELD = "candidates[{index}].label"


def candidate_key(index: int) -> str:
    """'c1', 'c2', ... for the other candidate sites (c0 is the chosen site)."""
    return f"c{index}"


def renamed(claim: Claim, claim_id: str, label: str) -> Claim:
    """The same claim under a package id and label (inputs, hash, status and provenance unchanged)."""
    return claim.model_copy(update={"id": claim_id, "label": label})


def candidate_case(case: PlanCase, candidate: Candidate) -> PlanCase:
    """The founder's plan at another candidate site, with that site's rent quote (S5 rule: a quote belongs to its
    own site, so a site without one uses the metro's rent range)."""
    plan = case.plan.model_copy(update={"monthly_rent": candidate.monthly_rent})
    return case.model_copy(update={"site": candidate.site, "site_label": candidate.label, "plan": plan})


def simulate_candidate(
    case: PlanCase, candidate: Candidate, chosen: FeasibilityResult, fabric: DataFabric
) -> tuple[PlanCase, FeasibilityResult]:
    """The candidate's plan and its simulation with the chosen site's seed, paths and months (no tornado)."""
    moved = candidate_case(case, candidate)
    result = simulate(
        case.profile_id,
        fabric,
        moved.site,
        moved.plan,
        moved.loan,
        n=chosen.n_paths,
        months=chosen.months,
        seed=chosen.seed,
        with_tornado=False,
    )
    return moved, result


def label_claim(claim_id: str, label: str, value: str, field: str) -> Claim:
    """A text the founder typed (a site label): USER_STATED, printed as data."""
    return Claim(
        id=claim_id,
        label=label,
        value=value,
        unit="TEXT",
        display="text",
        status="USER_STATED",
        provenance=Provenance(kind="user_input", ref=field),
        inputs={"field": field, "value": value},
    )


def probability_claims(prefix: str, result: FeasibilityResult, run: dict) -> list[Claim]:
    """The three simulated probabilities of one site's run (year-2 DSCR target, revenue stress, cash)."""
    simulation = SimulationClaims(result, run)
    stress = next((item for item in result.stress if item.name == "revenue_stress" and item.applies), None)
    claims = [
        simulation.share(
            f"{prefix}.p_dscr_pct",
            "Chance that DSCR in year 2 reaches the target at this site",
            result.p_dscr_at_least_target["year_2"],
            "p_dscr_at_least_target.year_2",
        ),
        simulation.share(
            f"{prefix}.p_cash_negative_pct",
            "Chance that cash falls below zero within 24 months at this site",
            result.p_cash_negative_within_24_months,
            "p_cash_negative_within_24_months",
        ),
    ]
    if stress is not None and stress.p_dscr_year2_at_least_min is not None:
        claims.append(
            simulation.share(
                f"{prefix}.p_stressed_pct",
                "Chance that year-2 DSCR passes the lender's revenue stress at this site",
                stress.p_dscr_year2_at_least_min,
                "stress.revenue_stress.p_dscr_year2_at_least_min",
            )
        )
    return claims


def candidate_claims(
    case: PlanCase, candidates: list[Candidate], chosen: FeasibilityResult, fabric: DataFabric
) -> list[Claim]:
    """Label, demand range and simulated probabilities of every other candidate site (module docstring)."""
    claims: list[Claim] = []
    for index, candidate in enumerate(candidates, start=1):
        prefix = f"{CANDIDATE_PREFIX}.{candidate_key(index)}"
        moved, result = simulate_candidate(case, candidate, chosen, fabric)
        run = run_identity(moved, result)
        claims.append(
            label_claim(
                f"{prefix}.label",
                f"Candidate site [{candidate_key(index)}]: name",
                candidate.label,
                TEXT_LABEL_FIELD.format(index=index),
            )
        )
        demand = {claim.id.rsplit(".", 1)[-1]: claim for claim in site_demand_claims(result, run)}
        for key, words in (("demand_mode", "most likely"), ("demand_low", "low end"), ("demand_high", "high end")):
            label = f"Candidate site [{candidate_key(index)}]: demand, {words}"
            claims.append(renamed(demand[key], f"{prefix}.{key}", label))
        claims.extend(probability_claims(prefix, result, run))
    return claims


def chosen_site_claims(case: PlanCase) -> list[Claim]:
    """The chosen site's label (founder free text, a USER_STATED text claim)."""
    return [
        label_claim(f"{CANDIDATE_PREFIX}.{CHOSEN_SITE_KEY}.label", "Chosen site: name", case.site_label, "site_label")
    ]


def competition_claims(score: SiteScore | None, factor_claim: Claim | None) -> list[Claim]:
    """Competition details with the factor claim's source, when that claim is VERIFIED (module docstring)."""
    if score is None or factor_claim is None or factor_claim.status != "VERIFIED":
        return []
    factor = next((item for item in score.factors if item.factor == "competition"), None)
    if factor is None:
        return []
    source = factor_claim.provenance
    claims = []
    for key, label, unit, display in COMPETITION_DETAILS:
        value = factor.details.get(key)
        if value is None:
            continue
        claims.append(
            public_data_claim(
                f"PKG.site.competition.{key}",
                label,
                float(value),
                unit,
                display,
                str(source.source),
                f"{source.ref}.details.{key}",
                source.metro_id,
                source.snapshot_date,
                dict(source.details),
            )
        )
    return claims


def ring_claims(score: SiteScore | None) -> list[Claim]:
    """The near and market ring radii (miles) SiteIQ used, as unsourced design-choice ASSUMPTIONS."""
    if score is None:
        return []
    area = score.trade_area
    claims = []
    for key, label, miles in (
        ("near_miles", "Near ring radius (counts of nearby places)", area.near_miles),
        ("market_miles", "Market ring radius (residents and workers)", area.market_miles),
    ):
        claims.append(
            assumption_range_claim(
                f"PKG.map.{key}",
                label,
                float(miles),
                "MILES",
                "ratio2",
                RING_SOURCE,
                f"siteiq trade_area.{key}",
                float(miles),
                float(miles),
                score.metro_id,
            )
        )
    return claims

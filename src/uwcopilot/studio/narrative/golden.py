"""The 36 golden founder scenarios: 6 business types x (5 real metros in their current local mode + the SYNTHETIC
metro). Every plan is SYNTHETIC and seeded; nothing here describes a real business or founder.

How one scenario is made (the same seed always gives the same scenario):
1. the plan, the loan, the site's demand range and two other candidate sites come from the path-to-approval
   generator (path_to_approval/synthetic_plans.generate_plan, seed = plan_seed(GOLDEN_SEED, metro, type, 0)); its
   demand is anchored so the plans span "clearly short", "near the line" and "comfortably past" the lender's stress;
2. SYNTHETIC founder facts (credit score, experience, sometimes a seller note) come from with_synthetic_facts;
3. when the metro has tracts in this environment, SiteIQ scores one tract centroid chosen by the seed, so the ledger
   carries real public-data rows (real metros) or SYNTHETIC city rows; without tracts there is no SiteIQ result
   (limited mode), and the ledger says nothing about the site's factors rather than inventing them;
4. a metro without a prime rate here gets a SYNTHETIC quoted rate (the SYNTHETIC city's prime plus the spread
   assumption's mode), noted on the scenario, so every scenario can be simulated;
5. simulate (S3) with n_paths paths, 36 months and the scenario's seed; then, optionally, find_paths (S5, quick mode).
The simulation's demand is the generator's SYNTHETIC range, not SiteIQ's estimate: the ledger records it as an
ASSUMPTION sourced to the generator, and SiteIQ's factors as location context. "Current local mode" means whatever
data this machine has: a CI runner without real-metro snapshots gives limited-mode scenarios, and the results say so.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

import numpy as np

from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.studio.feasibility import simulate
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.metros import list_metros
from uwcopilot.studio.narrative.founder_ledger import build_founder_ledger
from uwcopilot.studio.path_to_approval import (
    QUICK_SETTINGS,
    Candidate,
    PathResult,
    PlanCase,
    find_paths,
    synthetic_plans,
)
from uwcopilot.studio.path_to_approval.search import default_assumptions, fabric_for, profile_for
from uwcopilot.studio.profiles import list_profiles
from uwcopilot.studio.siteiq import SiteInputs, SiteOutsideMetro, SiteScore, score_site
from uwcopilot.studio.siteiq.context import metro_context

GOLDEN_SEED = 4404  # a generator parameter (design choice), not data
SYNTHETIC_METRO = "synthetic_metro"
SITE_LABEL = "golden site (SYNTHETIC plan)"
SITE_STREAM = 11  # keeps the site's draw apart from the plan's own draws
DEFAULT_PATHS = 1000
MONTHS = 36
QUOTED_RATE_NOTE = (
    "this metro has no prime rate here, so the SYNTHETIC plan quotes a rate: the SYNTHETIC city's prime plus the "
    "spread assumption's mode (a generator choice, not a market rate)"
)


@dataclass(frozen=True)
class GoldenScenario:
    """One golden founder scenario with everything the ledger, the narrative and the package need."""

    scenario_id: str
    metro_id: str
    profile_id: str
    seed: int
    case: PlanCase
    candidates: tuple[Candidate, ...]
    site_score: SiteScore | None
    site_location: tuple[float, float] | None
    feasibility: FeasibilityResult
    path_result: PathResult | None
    metro_mode: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    def ledger(self) -> Ledger:
        """The founder ledger of this scenario (SYNTHETIC)."""
        return build_founder_ledger(
            self.case, self.feasibility, self.site_score, self.path_result, subject_id=self.scenario_id
        )


def golden_metros() -> list[str]:
    """The SYNTHETIC metro first, then the real metros in config order."""
    return [SYNTHETIC_METRO] + sorted(item for item in list_metros() if item != SYNTHETIC_METRO)


def golden_specs() -> list[tuple[str, str]]:
    """(metro id, business type id) of the golden scenarios, in a fixed order (36 with the shipped configs)."""
    return [(metro, profile) for metro in golden_metros() for profile in list_profiles()]


def scenario_seed(metro_id: str, profile_id: str) -> int:
    """The seed of one scenario (the generator's plan_seed over the metro and type positions)."""
    metro_index = golden_metros().index(metro_id)
    profile_index = list_profiles().index(profile_id)
    return synthetic_plans.plan_seed(GOLDEN_SEED, metro_index, profile_index, 0)


def site_of(metro_id: str, profile_id: str, seed: int) -> tuple[SiteScore | None, tuple[float, float] | None]:
    """SiteIQ at one tract centroid chosen by the seed, or (None, None) when the metro has no tracts here."""
    context = metro_context(metro_id)
    if len(context.tracts) == 0:
        return None, None
    rng = np.random.default_rng(np.random.SeedSequence([seed, SITE_STREAM]))
    index = int(rng.integers(0, len(context.tracts)))
    lat, lon = float(context.tracts.lats[index]), float(context.tracts.lons[index])
    try:
        score = score_site(profile_id, context.metro, lat, lon, SiteInputs(label=SITE_LABEL), context=context)
    except SiteOutsideMetro:
        return None, None
    return score, (lat, lon)


def synthetic_quoted_rate(profile_id: str, metro_id: str) -> float | None:
    """A SYNTHETIC quoted rate when the metro has no prime rate here, else None (module docstring, step 4)."""
    prime = fabric_for(metro_id).metro_inputs(profile_for(profile_id, metro_id)).get("prime_rate")
    if prime is not None and prime.value is not None:
        return None
    synthetic = fabric_for(SYNTHETIC_METRO).metro_inputs(profile_for(profile_id, SYNTHETIC_METRO))["prime_rate"]
    return round(float(synthetic.value) + default_assumptions().rate_spread_over_prime_pct.point(), 4)


def build_golden_scenario(
    metro_id: str, profile_id: str, *, n_paths: int = DEFAULT_PATHS, with_path: bool = True
) -> GoldenScenario:
    """One golden scenario (module docstring)."""
    seed = scenario_seed(metro_id, profile_id)
    fabric = fabric_for(metro_id)
    profile = profile_for(profile_id, metro_id)
    # Step 4 must happen before the plan is generated: generating it already simulates the loan.
    quoted_rate = synthetic_quoted_rate(profile_id, metro_id)
    generated = synthetic_plans.generate_plan(
        profile,
        metro_id,
        fabric.data_label,
        fabric.metro_inputs(profile),
        default_assumptions(),
        seed,
        quoted_rate_pct=quoted_rate,
    )
    case = synthetic_plans.with_synthetic_facts(generated.case, seed)
    notes = (QUOTED_RATE_NOTE,) if quoted_rate is not None else ()
    site_score, location = site_of(metro_id, profile_id, seed)
    feasibility = simulate(profile_id, fabric, case.site, case.plan, case.loan, n=n_paths, months=MONTHS, seed=seed)
    path_result = None
    if with_path:
        settings = QUICK_SETTINGS.model_copy(update={"seed": seed, "n_paths": n_paths})
        path_result = find_paths(case, list(generated.candidates), settings=settings, fabric=fabric)
    return GoldenScenario(
        scenario_id=f"GOLDEN-{metro_id}-{profile_id}",
        metro_id=metro_id,
        profile_id=profile_id,
        seed=seed,
        case=case,
        candidates=generated.candidates,
        site_score=site_score,
        site_location=location,
        feasibility=feasibility,
        path_result=path_result,
        metro_mode=feasibility.metro_mode,
        notes=notes,
    )


def golden_scenarios(
    specs: Sequence[tuple[str, str]] | None = None,
    *,
    limit: int | None = None,
    n_paths: int = DEFAULT_PATHS,
    with_path: bool = True,
) -> Iterator[GoldenScenario]:
    """The golden scenarios in golden_specs() order (or the given specs), at most `limit` of them."""
    chosen = list(specs or golden_specs())
    for metro_id, profile_id in chosen[:limit] if limit is not None else chosen:
        yield build_golden_scenario(metro_id, profile_id, n_paths=n_paths, with_path=with_path)

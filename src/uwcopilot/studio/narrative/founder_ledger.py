"""build_founder_ledger: every founder-facing number of one plan as a claim of the shared evidence ledger.

    from uwcopilot.studio.narrative import build_founder_ledger, render_narrative
    ledger = build_founder_ledger(case, feasibility, site_score, path_result)   # case: path_to_approval.PlanCase
    narrative = render_narrative(ledger)                                        # template, the default

It uses the ONE evidence ledger of the project (uwcopilot.evidence.ledger_models, Ledger.builder()); it does not
create a second ledger. The claims, in order:
1. founder_claims: what the plan is about, every figure the founder typed (USER_STATED, or VERIFIED / CONFLICTING
   when an uploaded document backs or contradicts it), owner facts and other debt;
2. assumption_claims: every simulator input the founder did not type (ASSUMPTION ranges with their source or
   "unsourced", public data as VERIFIED, a range the founder edited as USER_STATED), the note rate, the build-out
   assumption and the site's demand range;
3. site_claims: SiteIQ's scores, location factors and metro inputs (VERIFIED rows with their locator, ASSUMPTION,
   UNKNOWN), when a SiteIQ result is given;
4. simulation_claims: the simulation's outputs (MODEL_ESTIMATE with simulator version, seed, paths and plan hash);
5. path_claims: the path-to-targets search, when a search result is given;
6. program thresholds and Studio targets (VERIFIED policy), the probability that answers each target
   (target_measures.py: the simulation's, the path search's for an edited target, or UNKNOWN), the program
   arithmetic (CALC.*), and the checks.
Callers may add claims of their own (extra_claims, for example the package's site comparison) so the package keeps
one ledger. The plan, the simulation and the SiteIQ result must describe the same plan; a mismatch is an error.
"""

from collections.abc import Sequence

from uwcopilot.evidence.ledger_models import Claim, InputValue, Ledger, inputs_hash
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.assumption_claims import assumption_claims
from uwcopilot.studio.narrative.founder_claims import founder_claims
from uwcopilot.studio.narrative.models import FounderDocumentValue
from uwcopilot.studio.narrative.path_claims import path_claims
from uwcopilot.studio.narrative.program_calcs import ProgramCalculations
from uwcopilot.studio.narrative.program_checks import ProgramChecks
from uwcopilot.studio.narrative.program_thresholds import program_threshold_claims, target_claims
from uwcopilot.studio.narrative.simulation_claims import DEFAULT_MONTHLY_MONTHS, simulation_claims
from uwcopilot.studio.narrative.site_claims import site_claims
from uwcopilot.studio.narrative.target_measures import target_measures
from uwcopilot.studio.path_to_approval.models import PathResult, PlanCase
from uwcopilot.studio.path_to_approval.targets import default_target
from uwcopilot.studio.siteiq.models import SiteScore

LEDGER_LINE = "startup_credit"
FOUNDER_LEDGER_VERSION = "founder-ledger-1.0"
SUBJECT_PREFIX = "FOUNDER-"
SUBJECT_HASH_CHARS = 12
MONEY_TOLERANCE = 0.005
PROJECTION_YEARS = 2


class PlanMismatch(ValueError):
    """The plan, the simulation result and the SiteIQ result do not describe the same plan."""


class SimulationTooShort(ValueError):
    """The simulation does not reach year 2, so the program's year-2 checks cannot be computed."""


def plan_sha256(case: PlanCase) -> str:
    """SHA-256 of the canonical JSON of the plan (business type, metro, site demand, plan, loan, facts)."""
    return inputs_hash(case.model_dump(mode="json"))


def run_identity(case: PlanCase, result: FeasibilityResult) -> dict[str, InputValue]:
    """What every simulation claim carries: simulator version, seed, paths, months, plan hash, metro and type."""
    return {
        "simulator_version": result.simulator_version,
        "seed": result.seed,
        "n_paths": result.n_paths,
        "months": result.months,
        "plan_sha256": plan_sha256(case),
        "metro_id": result.metro_id,
        "metro_mode": result.metro_mode,
        "profile_id": result.profile_id,
        "data_label": result.data_label,
    }


def conflicting_input_ids(claims: dict[str, Claim]) -> tuple[str, ...]:
    """The CONFLICTING inputs so far: a founder figure a document contradicts, and that document's value."""
    return tuple(claim.id for claim in claims.values() if claim.status == "CONFLICTING")


def with_conflicts(items: Sequence[Claim], conflicting: tuple[str, ...]) -> list[Claim]:
    """Simulation claims that list the CONFLICTING inputs they rest on (as Phase 4 does for a calculation).

    The simulator reads the plan as the founder stated it, so every simulated figure rests on any stated figure a
    document disagrees with; the claim keeps its MODEL_ESTIMATE status and says what it depends on.
    """
    if not conflicting:
        return list(items)
    return [
        Claim.model_validate({**item.model_dump(), "depends_on_conflicting": conflicting})
        if item.status == "MODEL_ESTIMATE"
        else item
        for item in items
    ]


def check_same_plan(
    case: PlanCase, result: FeasibilityResult, site_score: SiteScore | None, path_result: PathResult | None
) -> None:
    """Raise PlanMismatch when the inputs describe different plans (so a ledger never mixes two plans), and
    SimulationTooShort when the simulation has no second year (the program checks read year 2)."""
    problems = []
    if (case.profile_id, case.metro_id) != (result.profile_id, result.metro_id):
        problems.append("business type or metro differs between the plan and the simulation")
    if abs(case.plan.owner_equity - result.owner_equity) > MONEY_TOLERANCE:
        problems.append("owner equity differs between the plan and the simulation")
    if abs(case.loan.amount - result.loan.amount) > MONEY_TOLERANCE or case.loan.term_months != result.loan.term_months:
        problems.append("loan amount or term differs between the plan and the simulation")
    if case.loan.rate_type != result.loan.rate_type:
        problems.append("rate type differs between the plan and the simulation")
    if case.plan.opening_date != result.opening_date:
        problems.append("opening date differs between the plan and the simulation")
    if site_score is not None and (site_score.profile_id, site_score.metro_id) != (case.profile_id, case.metro_id):
        problems.append("the SiteIQ result is for another business type or metro")
    if path_result is not None and (path_result.profile_id, path_result.metro_id) != (case.profile_id, case.metro_id):
        problems.append("the path search is for another business type or metro")
    if problems:
        raise PlanMismatch("; ".join(problems))
    if len(result.yearly) < PROJECTION_YEARS:
        raise SimulationTooShort(
            f"the simulation covers {result.months} months; the program checks need {PROJECTION_YEARS} full years "
            "(simulate at least 24 months, 36 for the package)"
        )


def build_founder_ledger(
    case: PlanCase,
    feasibility: FeasibilityResult,
    site_score: SiteScore | None = None,
    path_result: PathResult | None = None,
    *,
    documents: Sequence[FounderDocumentValue] = (),
    extra_claims: Sequence[Claim] = (),
    subject_id: str | None = None,
    data_label: str = "SYNTHETIC",
    monthly_months: int = DEFAULT_MONTHLY_MONTHS,
) -> Ledger:
    """The founder ledger of one plan (see the module docstring).

    case: the plan as the path search reads it (PlanCase: business type, metro, site demand, Plan, Loan, facts).
    feasibility: simulate() of that plan (at least 36 months, so years 1-3 exist).
    site_score: SiteIQ's result for the plan's site (optional). path_result: find_paths for the plan (optional).
    documents: values read from documents the founder uploaded. extra_claims: claims the caller adds.
    subject_id defaults to FOUNDER-<first 12 hex of the plan hash>; data_label to SYNTHETIC (the default for every
    plan in this repository; the web app passes its own label for real founder inputs under docs/studio/privacy.md).
    monthly_months: how many months get monthly P10/P50/P90 claims (12: the package shows year 1 by month).
    Raises PlanMismatch when the inputs describe different plans, SimulationTooShort when the simulation has no year 2.
    """
    check_same_plan(case, feasibility, site_score, path_result)
    run = run_identity(case, feasibility)
    claims: dict[str, Claim] = {}

    def keep(items: Sequence[Claim]) -> None:
        for item in items:
            if item.id in claims:
                raise ValueError(f"duplicate claim id {item.id}")
            claims[item.id] = item

    keep(founder_claims(case, feasibility.demand_unit, documents))
    overridden = frozenset(case.plan.assumption_overrides)
    keep(assumption_claims(feasibility, claims.get(ids.FOUNDER_RATE), claims.get(ids.FOUNDER_SPREAD), run, overridden))
    keep(site_claims(site_score))
    keep(with_conflicts(simulation_claims(feasibility, run, monthly_months), conflicting_input_ids(claims)))
    keep(path_claims(path_result))
    keep(program_threshold_claims(case.loan.rate_type))
    target = path_result.target if path_result is not None else default_target()
    keep(target_claims(target))
    measures = target_measures(target, feasibility, claims)
    keep(measures.unknown)
    ProgramCalculations(case, feasibility, claims).all()
    keep(extra_claims)
    assertions = ProgramChecks(case, feasibility, claims, target, measures).all()
    subject = subject_id or f"{SUBJECT_PREFIX}{run['plan_sha256'][:SUBJECT_HASH_CHARS]}"
    builder = Ledger.builder(subject_id=subject, line=LEDGER_LINE, data_label=data_label)
    for claim in claims.values():
        builder.add_claim(claim)
    for assertion in assertions:
        builder.add_assertion(assertion)
    return builder.build()


def ledger_from_plan(
    plan: PlanCase,
    site_score: SiteScore | None,
    feasibility: FeasibilityResult,
    path_result: PathResult | None = None,
    **options: object,
) -> Ledger:
    """The spec's name for build_founder_ledger (argument order of the build plan); options are passed through."""
    return build_founder_ledger(plan, feasibility, site_score, path_result, **options)  # type: ignore[arg-type]

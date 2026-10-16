"""Simulate plan variants with common random numbers and cache the outcomes (Studio S5).

Common random numbers: every variant of one plan is simulated with the SAME random draws (the S3 simulator's draws
for the founder's plan and seed). A variant changes plan choices, not the set of uncertain inputs, so each input
keeps its name and therefore its uniforms; only the values those uniforms map to change. The difference between
two variants is then the effect of the change, not luck of the draw, and the after-probabilities are exactly what
S3's simulate() gives for the changed plan with the same seed (a test checks this).

Outcome of one plan (all simulated, based on stated assumptions):
- P(year-2 DSCR >= target.dscr_min), each path's DSCR rounded like the lender rounds it (rounded_dscr: the same
  numbers as S3's metrics.dscr_by_year, computed for all paths at once);
- P(stressed year-2 DSCR >= target.stressed_dscr_min): the NSP-4.2 revenue stress on every path (revenue lower by
  stress_revenue_share and NOI lower by the same dollars; the same as S3's stress run);
- P(cash < 0 within target.cash_months months);
- the lender's base-case ratios (NSP-4.1 and NSP-4.2 on the year-2 P50, the startup program's half-up rounding).
  These always use the program's own constants (the 20% revenue stress of agent/policy_credit.py), whatever the
  target says: a founder may edit the stress of the simulated target, but "the lender's check" is the lender's.
Other debt (a FACT) adds its monthly payment to debt service, net income and cash, and its proceeds to the cash
at opening (NSP-1.2); without other debt the outcome equals S3's simulate() exactly.

Cache: outcomes are stored by (plan hash, lever values). The plan hash covers the plan, its facts, the candidate
sites, the target, the simulation settings, the metro inputs, the business-type profile (with the metro's
overrides), the simulator assumptions and the engine version, so a stale entry can never be read for a different
plan or after a config edit. The cache is a bounded least-recently-used dict behind a lock, so the web app can
share one across concurrent requests.
"""

import dataclasses
import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from uwcopilot.agent.credit_calculations import coverage_ratio
from uwcopilot.agent.policy_credit import REVENUE_STRESS_SHARE
from uwcopilot.agent.policy_startup import half_up, program_ratio, to_decimal
from uwcopilot.studio.data.fabric_base import FactorValue
from uwcopilot.studio.feasibility.assumptions import FeasibilityAssumptions
from uwcopilot.studio.feasibility.metrics import annual_total, p_cash_negative, percentiles_of, share_at_least
from uwcopilot.studio.feasibility.model import PathResults, draw, input_values, run_paths
from uwcopilot.studio.feasibility.result import SIMULATOR_VERSION
from uwcopilot.studio.feasibility.setup import SimulationSetup, build_setup
from uwcopilot.studio.feasibility.simulate import money
from uwcopilot.studio.feasibility.stress import lender_annual_debt_service
from uwcopilot.studio.path_to_approval.guardrails import apply_changes
from uwcopilot.studio.path_to_approval.models import (
    PATH_ENGINE_VERSION,
    Candidate,
    Change,
    FounderFacts,
    Outcome,
    PlanCase,
    Target,
)
from uwcopilot.studio.path_to_approval.space import LeverSpace
from uwcopilot.studio.path_to_approval.targets import TARGET_NAMES
from uwcopilot.studio.profiles.models import Profile

DEFAULT_CACHE_ENTRIES = 20_000
PROBABILITY_TOLERANCE = 1e-12
# rounded_dscr: how close to a half cent (ratio x 100 ending in .5) a ratio must be for the exact Python rounding,
# and the ratio size above which floating-point x 100 is no longer precise enough to trust NumPy's rounding.
HALF_CENT_TOLERANCE = 1e-6
LARGE_RATIO = 1e6
CacheKey = tuple[str, tuple[tuple[str, float | str], ...]]


class EvaluationCache:
    """A bounded least-recently-used store of outcomes keyed by (plan hash, lever values).

    Outcomes are frozen models, so a cached one can be handed to several requests. A lock guards every access
    because the web app may run searches in several threads at once.
    """

    def __init__(self, max_entries: int = DEFAULT_CACHE_ENTRIES) -> None:
        self.max_entries = max_entries
        self.entries: OrderedDict[CacheKey, Outcome] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.lock = threading.Lock()

    def get(self, key: CacheKey) -> Outcome | None:
        """The stored outcome (and mark it recently used), or None."""
        with self.lock:
            outcome = self.entries.get(key)
            if outcome is None:
                self.misses += 1
                return None
            self.hits += 1
            self.entries.move_to_end(key)
            return outcome

    def put(self, key: CacheKey, outcome: Outcome) -> None:
        """Store an outcome, dropping the least recently used entry when full."""
        with self.lock:
            self.entries[key] = outcome
            self.entries.move_to_end(key)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)

    def clear(self) -> None:
        """Drop every entry (for example after a data snapshot or a config file changes)."""
        with self.lock:
            self.entries.clear()

    def __len__(self) -> int:
        with self.lock:
            return len(self.entries)


SHARED_CACHE = EvaluationCache()


def sha256_json(value: Any) -> str:
    """SHA-256 of a value's canonical JSON (sorted keys)."""
    text = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_hash(
    case: PlanCase,
    candidates: Sequence[Candidate],
    target: Target,
    metro_inputs: dict[str, FactorValue],
    profile: Profile,
    assumptions: FeasibilityAssumptions,
    n_paths: int,
    months: int,
    seed: int,
) -> str:
    """The hash that keys every cached outcome of one search (see the module docstring)."""
    return sha256_json(
        {
            "engine": PATH_ENGINE_VERSION,
            "simulator": SIMULATOR_VERSION,
            "case": case.model_dump(mode="json"),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            "target": target.model_dump(mode="json", exclude={"sources"}),
            "metro_inputs": {name: value.model_dump(mode="json") for name, value in metro_inputs.items()},
            "profile": profile.model_dump(mode="json"),
            "assumptions": assumptions.model_dump(mode="json"),
            "n_paths": n_paths,
            "months": months,
            "seed": seed,
        }
    )


def change_key(changes: Sequence[Change]) -> tuple[tuple[str, float | str], ...]:
    """The lever values of a change-set in a fixed order (by lever name)."""
    return tuple(sorted((change.lever, change.value) for change in changes))


def with_other_debt(paths: PathResults, monthly_payment: float) -> PathResults:
    """Add a constant monthly payment of other debt to debt service, and take it out of net income and cash."""
    if monthly_payment == 0:
        return paths
    payments = np.full(paths.debt_service.shape, monthly_payment)
    return dataclasses.replace(
        paths,
        debt_service=paths.debt_service + payments,
        net_income=paths.net_income - payments,
        cash=paths.cash - np.cumsum(payments, axis=1),
    )


def rounded_dscr(paths: PathResults, year: int) -> np.ndarray:
    """Per path, the year's DSCR rounded to two decimals: exactly the numbers of S3's metrics.dscr_by_year.

    dscr_by_year calls the lender's coverage_ratio (Python's round) once per path, which took about half of a
    search's run time. Here NumPy divides and rounds every path at once. NumPy rounds by computing ratio x 100,
    rounding to the nearest whole number and dividing by 100; that can only differ from Python's round when
    ratio x 100 ends in .5 (within a hair) or the ratio is huge, so those few paths are rounded with coverage_ratio
    itself. A year without debt service gives NaN, like dscr_by_year. Tests compare both on many ratios and on
    crafted halves.
    """
    noi = annual_total(paths.noi, year)
    debt = annual_total(paths.debt_service, year)
    positive = debt > 0
    ratio = np.full(noi.shape, np.nan)
    ratio[positive] = noi[positive] / debt[positive]
    rounded = np.round(ratio, 2)
    scaled = ratio * 100.0
    near_half = np.abs(scaled - np.floor(scaled) - 0.5) < HALF_CENT_TOLERANCE
    exact = positive & (near_half | (np.abs(ratio) >= LARGE_RATIO))
    for index in np.flatnonzero(exact):
        rounded[index] = coverage_ratio(float(noi[index]), float(debt[index]))
    return rounded


def stressed_noi_base_case(noi: float, revenue: float, share: float) -> float:
    """NSP-4.2: NOI minus share x revenue, to the cent, in the startup program's exact decimal arithmetic."""
    return half_up(to_decimal(noi) - to_decimal(share) * to_decimal(revenue), 2)


def annual_service(setup: SimulationSetup, facts: FounderFacts) -> float:
    """NSP-1.2 projected annual debt service: the loan's 12 level payments plus 12 payments of other debt.

    The same arithmetic as policy_startup.projected_annual_debt_service (exact decimals, rounded half up).
    """
    monthly = sum((to_decimal(item.monthly_payment) for item in facts.other_debt), Decimal(0))
    other = half_up(monthly * 12, 2)
    return half_up(to_decimal(lender_annual_debt_service(setup)) + to_decimal(other), 2)


def gaps(target: Target, p_dscr: float, p_stressed: float, p_cash: float) -> dict[str, float]:
    """How far each probability misses its target (0 when it meets it), in TARGET_NAMES order."""
    values = (
        max(0.0, target.p_dscr_min - p_dscr),
        max(0.0, target.p_stressed_min - p_stressed),
        max(0.0, p_cash - target.p_cash_negative_max),
    )
    result = {}
    for name, value in zip(TARGET_NAMES, values, strict=True):
        result[name] = 0.0 if value <= PROBABILITY_TOLERANCE else value
    return result


def outcome_of(
    setup: SimulationSetup, paths: PathResults, target: Target, facts: FounderFacts, n_paths: int, seed: int
) -> Outcome:
    """The three probabilities and the lender's base-case ratios of one simulated plan."""
    paths = with_other_debt(paths, facts.other_debt_monthly_payment)
    year = target.year
    p_dscr = share_at_least(rounded_dscr(paths, year), target.dscr_min)
    stressed = dataclasses.replace(paths, noi=paths.noi - target.stress_revenue_share * paths.revenue)
    p_stressed = share_at_least(rounded_dscr(stressed, year), target.stressed_dscr_min)
    p_cash = p_cash_negative(paths, target.cash_months)
    noi_p50 = money(percentiles_of(annual_total(paths.noi, year))[1])
    revenue_p50 = money(percentiles_of(annual_total(paths.revenue, year))[1])
    service = annual_service(setup, facts)
    missed = gaps(target, p_dscr, p_stressed, p_cash)
    return Outcome(
        n_paths=n_paths,
        seed=seed,
        p_dscr_at_least_target_year2=p_dscr,
        p_stressed_dscr_at_least_min_year2=p_stressed,
        p_cash_negative_within_months=p_cash,
        projected_dscr_base_case=program_ratio(noi_p50, service),
        stressed_dscr_base_case=program_ratio(
            stressed_noi_base_case(noi_p50, revenue_p50, REVENUE_STRESS_SHARE), service
        ),
        annual_debt_service=service,
        meets_target=all(value == 0.0 for value in missed.values()),
        targets_missed=[name for name, value in missed.items() if value > 0],
        shortfall=round(sum(missed.values()), 12),
    )


def setup_for_case(
    case: PlanCase,
    profile: Profile,
    metro_inputs: dict[str, FactorValue],
    data_label: str,
    assumptions: FeasibilityAssumptions,
    months: int,
) -> SimulationSetup:
    """The S3 setup of a plan. Other debt proceeds count in the cash at opening (added to the sources).

    Only the simulator's copy of the plan carries them in owner_equity (S3 computes the cash at opening as
    equity + loan - build-out); the plan, the result and the guardrails keep the founder's own equity.
    """
    plan = case.plan
    if case.facts.other_debt_amount:
        plan = plan.model_copy(update={"owner_equity": plan.owner_equity + case.facts.other_debt_amount})
    return build_setup(
        profile, case.metro_id, data_label, metro_inputs, case.site, plan, case.loan, assumptions, months
    )


class PathEvaluator:
    """Simulates variants of one plan with the plan's own draws (common random numbers), with a cache."""

    def __init__(
        self,
        space: LeverSpace,
        profile: Profile,
        metro_inputs: dict[str, FactorValue],
        data_label: str,
        assumptions: FeasibilityAssumptions,
        target: Target,
        n_paths: int,
        months: int,
        seed: int,
        cache: EvaluationCache,
        candidates: Sequence[Candidate] = (),
    ) -> None:
        self.space = space
        self.profile = profile
        self.metro_inputs = metro_inputs
        self.data_label = data_label
        self.assumptions = assumptions
        self.target = target
        self.n_paths = n_paths
        self.months = months
        self.seed = seed
        self.cache = cache
        self.base_setup = self.setup_for(space.case)
        self.draws = draw(self.base_setup, n_paths, np.random.default_rng(seed))
        self.names = tuple(self.base_setup.distributions)
        self.key_prefix = plan_hash(
            space.case, candidates, target, metro_inputs, profile, assumptions, n_paths, months, seed
        )
        self.evaluations = 0
        self.cache_hits = 0

    def setup_for(self, case: PlanCase) -> SimulationSetup:
        """The S3 setup of a plan (setup_for_case)."""
        return setup_for_case(case, self.profile, self.metro_inputs, self.data_label, self.assumptions, self.months)

    def simulate(self, case: PlanCase) -> Outcome:
        """Simulate one plan on the shared draws (no cache)."""
        setup = self.setup_for(case)
        if tuple(setup.distributions) != self.names:
            raise ValueError("a variant changed the set of uncertain inputs; common random numbers would not hold")
        values = input_values(setup, self.draws)
        paths = run_paths(setup, values, self.draws)
        return outcome_of(setup, paths, self.target, case.facts, self.n_paths, self.seed)

    def evaluate(self, changes: Sequence[Change]) -> Outcome:
        """The outcome of the plan with a change-set applied.

        The guardrails run first, on every call: a cached outcome is never returned for a change-set that is not
        allowed under this search's bounds. Raises GuardrailViolation or LeverError on an invalid change-set.
        """
        case = apply_changes(self.space, changes) if changes else self.space.case
        key = (self.key_prefix, change_key(changes))
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        outcome = self.simulate(case)
        self.evaluations += 1
        self.cache.put(key, outcome)
        return outcome

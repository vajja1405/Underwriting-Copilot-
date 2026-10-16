"""Shared builders for the path-to-approval tests. Every plan is SYNTHETIC; the metro is the generated SYNTHETIC city.

The car-wash plan below was chosen by hand so that it misses the targets as it is but a single lever reaches them
(borrowing a little more or keeping a bigger cash reserve fixes its cash risk). Its numbers are test inputs, not
benchmarks. The rate is quoted (annual_rate_pct) so the plan does not depend on any prime rate.
"""

from datetime import date
from typing import Any

from uwcopilot.studio.feasibility import Loan, Plan, SiteDemand
from uwcopilot.studio.path_to_approval import (
    Candidate,
    EvaluationCache,
    FounderFacts,
    PathResult,
    PlanCase,
    SearchSettings,
    find_paths,
)
from uwcopilot.studio.path_to_approval.search import PreparedSearch, prepare_search

METRO = "synthetic_metro"
# Small settings so the tests stay fast (the real defaults are FULL_SETTINGS / QUICK_SETTINGS).
FAST = SearchSettings(n_paths=300, steps_per_side=4, max_evaluations=600)


def car_wash_site(mode: float = 420.0, **changes: Any) -> SiteDemand:
    """A SYNTHETIC car-wash demand range around a most likely demand."""
    fields: dict[str, Any] = {
        "low": mode * 0.8,
        "mode": mode,
        "high": mode * 1.25,
        "unit": "cars/day",
        "status": "SYNTHETIC",
        "source": "path test range (SYNTHETIC)",
        "competition_percentile": 0.5,
    }
    fields.update(changes)
    return SiteDemand(**fields)


def car_wash_case(
    mode: float = 420.0,
    facts: FounderFacts | None = None,
    plan_changes: dict[str, Any] | None = None,
    loan_changes: dict[str, Any] | None = None,
) -> PlanCase:
    """A SYNTHETIC car-wash plan: build-out 1.5M, equity 600k, loan 1.1M over 84 months at a quoted 8% fixed."""
    plan: dict[str, Any] = {
        "opening_date": date(2027, 3, 1),
        "owner_equity": 600_000.0,
        "capex": 1_500_000.0,
        "price": 14.0,
        "monthly_rent": 8_000.0,
    }
    plan.update(plan_changes or {})
    loan: dict[str, Any] = {"amount": 1_100_000.0, "term_months": 84, "annual_rate_pct": 8.0, "rate_type": "fixed"}
    loan.update(loan_changes or {})
    return PlanCase(
        profile_id="car_wash",
        metro_id=METRO,
        site=car_wash_site(mode),
        plan=Plan(**plan),
        loan=Loan(**loan),
        facts=facts or FounderFacts(),
    )


def north_lot(mode: float = 420.0, metro_id: str = METRO) -> Candidate:
    """Another SYNTHETIC candidate site with more demand and a higher rent quote."""
    site = car_wash_site(mode * 1.3, low=mode * 1.1, high=mode * 1.6, competition_percentile=None)
    return Candidate(label="north lot", metro_id=metro_id, site=site, monthly_rent=9_000.0)


def search(case: PlanCase, candidates: list[Candidate] | None = None, **options: Any) -> PathResult:
    """find_paths with the fast settings and a fresh cache (unless the test passes its own)."""
    options.setdefault("settings", FAST)
    options.setdefault("cache", EvaluationCache())
    return find_paths(case, candidates or [], **options)


def prepared(case: PlanCase, candidates: list[Candidate] | None = None, **options: Any) -> PreparedSearch:
    """prepare_search with the fast settings and a fresh cache."""
    options.setdefault("settings", FAST)
    options.setdefault("cache", EvaluationCache())
    return prepare_search(case, candidates or [], **options)

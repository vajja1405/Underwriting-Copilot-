"""Path to approval (Founder Studio S5): the smallest changes to a plan that reach the simulated targets.

    from uwcopilot.studio.path_to_approval import PlanCase, find_paths
    result = find_paths(case)                    # the three default targets, full mode
    result = find_paths(case, candidates, target, mode="quick")   # for an interactive web request

Targets (targets.py, all editable): P(year-2 DSCR >= 1.25) >= 0.80, P(year-2 DSCR >= 1.00 under the lender's 20%
revenue stress) >= 0.80, and P(cash < 0 within 24 months) <= 0.10. Every probability is "simulated, based on stated
assumptions" (Studio rule E) and every result carries the Studio disclaimer: reaching a target is not an approval.

Modules:
- models.py      inputs (PlanCase, FounderFacts, OtherDebt, Candidate, Target, Change) and outputs (PathResult ...);
- targets.py     the default targets with their sources;
- levers.py      config/studio/levers.yaml: the allow-list of plan-choice levers, bounds, grids and effort;
- space.py       the levers of one plan: current values, bounds, grids, and why a lever is not available;
- guardrails.py  apply_changes: only plan choices move, never a fact; checked field by field;
- program.py     startup-program checks a change-set may not newly break (equity injection, loan limits, term);
- evaluate.py    simulation of plan variants with common random numbers, and the outcome cache;
- search.py      find_paths (and prepare_search): single-lever moves, then pairs; ranking; quick mode; caches;
- explain.py     plain-language text;
- exhaustive.py  a brute-force reference (every single value and pair) for tests and the evaluation only;
- synthetic_plans.py  seeded SYNTHETIC plans for the evaluation (scripts/eval_studio_paths.py) and tests.

Every result also carries the applied plan of each change-set (ChangeSet.applied_plan), which is what the website's
"apply this change" button sends back as the founder's new plan.
"""

from uwcopilot.studio.path_to_approval.errors import GuardrailViolation, LeverError, OutOfBounds
from uwcopilot.studio.path_to_approval.evaluate import SHARED_CACHE, EvaluationCache
from uwcopilot.studio.path_to_approval.levers import LEVER_NAMES, LeverBounds, load_levers
from uwcopilot.studio.path_to_approval.models import (
    PATH_ENGINE_VERSION,
    Candidate,
    Change,
    ChangeSet,
    FounderFacts,
    OtherDebt,
    Outcome,
    PathResult,
    PlanAmounts,
    PlanCase,
    ProgramChecks,
    SearchSettings,
    Target,
)
from uwcopilot.studio.path_to_approval.search import (
    FULL_SETTINGS,
    QUICK_SETTINGS,
    PreparedSearch,
    clear_caches,
    find_paths,
    prepare_search,
)
from uwcopilot.studio.path_to_approval.sites import candidate_from_site_score
from uwcopilot.studio.path_to_approval.targets import custom_target, default_target

__all__ = [
    "FULL_SETTINGS",
    "LEVER_NAMES",
    "PATH_ENGINE_VERSION",
    "QUICK_SETTINGS",
    "SHARED_CACHE",
    "Candidate",
    "Change",
    "ChangeSet",
    "EvaluationCache",
    "FounderFacts",
    "GuardrailViolation",
    "LeverBounds",
    "LeverError",
    "OtherDebt",
    "OutOfBounds",
    "Outcome",
    "PathResult",
    "PlanAmounts",
    "PlanCase",
    "PreparedSearch",
    "ProgramChecks",
    "SearchSettings",
    "Target",
    "candidate_from_site_score",
    "clear_caches",
    "custom_target",
    "default_target",
    "find_paths",
    "load_levers",
    "prepare_search",
]

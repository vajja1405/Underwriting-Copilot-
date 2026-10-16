"""A brute-force reference for the path-to-approval search (for tests and scripts/eval_studio_paths.py only).

exhaustive_search() tries EVERY single-lever value and EVERY pair of values on the same grids and bounds as the real
search (no screening, no staircase, no evaluation budget), with the same guardrails, program checks and random
draws (common random numbers). It answers one question: did the heuristic in search.py miss a change-set of one or
two levers that reaches the targets, or return a bigger one than needed? It is far too slow for a web request (one
simulation per grid value and per pair of grid values: hundreds to thousands per plan).
"""

from dataclasses import dataclass
from itertools import combinations

from uwcopilot.studio.path_to_approval.models import Change
from uwcopilot.studio.path_to_approval.search import PreparedSearch, Search, Tried

UNLIMITED_EVALUATIONS = 10**9


@dataclass(frozen=True)
class ExhaustiveResult:
    """Every change-set of one or two levers that reaches the targets, smallest first."""

    feasible: list[Tried]
    tried: int
    evaluations: int

    @property
    def best(self) -> Tried | None:
        """The smallest feasible change-set (fewest levers, then least effort), or None."""
        return self.feasible[0] if self.feasible else None


def exhaustive_search(prepared: PreparedSearch, pairs: bool = True) -> ExhaustiveResult:
    """Try every single value and (when pairs) every pair of values of the available levers."""
    settings = prepared.settings.model_copy(update={"max_evaluations": UNLIMITED_EVALUATIONS})
    search = Search(prepared.space, prepared.evaluator, settings)
    feasible: list[Tried] = []
    tried = 0
    grids = prepared.space.grids
    candidates: list[tuple[Change, ...]] = []
    for lever in prepared.space.available:
        candidates.extend((Change(lever=lever, value=value),) for value in grids[lever])
    if pairs:
        for first, second in combinations(prepared.space.available, 2):
            for first_value in grids[first]:
                for second_value in grids[second]:
                    candidates.append(
                        (Change(lever=first, value=first_value), Change(lever=second, value=second_value))
                    )
    for changes in candidates:
        result = search.try_changes(changes)
        if result is None:
            continue
        tried += 1
        if result.outcome.meets_target:
            feasible.append(result)
    feasible.sort(key=Tried.rank_key)
    return ExhaustiveResult(feasible=feasible, tried=tried, evaluations=prepared.evaluator.evaluations)

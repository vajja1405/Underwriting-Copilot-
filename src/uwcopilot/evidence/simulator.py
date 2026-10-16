"""Evidence-request simulator (innovation S5): which order of evidence requests reaches the full-information
recommendation, and at what cost, on SYNTHETIC cases with a known hidden truth.

This is EVALUATION code: it reads the generator's hidden truth (simulator_cases.py). The Evidence-to-Action Engine
modules never import it. Extraction is an oracle (the generator's perfect extraction), so nothing here measures
extraction accuracy.

simulate(case_truth, policy_name, priors_variant, rng, prepared=None) -> SimulationResult runs one policy on one
case without rendering a PDF:
1. start from the engine's state for the oracle extraction (simulator_cases.prepare_case) and compute the
   full-information recommendation: the reference policy (agent/policy.py) on the state with every planted problem
   revealed at its latent (true) resolution;
2. loop: detect the open items (evidence.open_items, with the items already requested passed as answered), group
   them by shared request (evidence.voi.group_items), let the policy pick one request, reveal the planted problems
   its items stand for, re-evaluate. Every pick is valued just before it with the engine's VOI score
   (evidence.voi.request_value) under the run's priors variant, so zero-value requests can be counted.

Policies (the choice at each step; "open requests" are the groups of open items that have a catalog request):
- voi: the open request with the highest nonzero VOI (ties: lower cost weight, then request id and params; the
  engine's rank_requests order). Stops when no open request has a VOI above zero. This is the stopping rule of
  innovation S5 and the policy in the headline comparison.
- voi_lookahead (an ADDITION to the four specified policies): as voi; when no request has a nonzero VOI, the request
  with the highest nonzero lookahead value (rank_requests' voi_given_others: the VOI after every other open item is
  resolved at its most likely option). Stops when both are zero. It exists because myopic VOI scores 0 on every
  request when several open problems each keep the same recommendation (two blocking referrals, for example).
- checklist: every open request in the fixed severity order of evidence.open_items.PROBLEM_ORDER (integrity and
  identity problems first, then the money problems, then missing items), until none is left.
- random: the open requests in a seeded shuffled order (requests that appear later are shuffled and appended), until
  none is left. rng is the caller's random.Random.
- oracle: knows the truth. A uniform-cost search over request orders (only when the case starts with at most
  ORACLE_MAX_OPEN_REQUESTS open requests) for the cheapest sequence, in cost-weighted requests (ties: fewer
  requests, then request ids), that reaches the full-information recommendation; it stops there. When no sequence
  reaches it (a planted problem no open item stands for can make it unreachable) the oracle makes no request.

Priors variants (robustness of the VOI score to wrong priors; the truth and the reveals never change):
- documented: the template priors of config/resolution_templates.yaml;
- latent_x0.5 / latent_x1.5: the prior of the latent (true) option of every open item that stands for a planted
  problem is multiplied by 0.5 or 1.5, then the item's priors are renormalized to sum to 1;
- latent_removed: the latent option is removed from the item's option set (the remaining priors renormalized);
- three TABLE variants (TABLE_VARIANTS) take their priors from a table of latent shares per resolution template
  and option, which simulate_requests.py measures (simulator_diagnostics) and passes in as `tables`:
  - empirical_dev: the shares on the development split, UNSMOOTHED. Options never latent there get no prior and
    leave the set, so for a test problem whose true option was never seen on the (sometimes tiny) development
    sample, the true option is removed, as in latent_removed (counted in the results' diagnostics);
  - empirical_dev_smoothed: the development counts with add-one smoothing, (count + 1) / (n + number of options),
    so every option keeps a prior above zero and none leaves the set;
  - oracle_shares: the shares of the evaluated group itself (an in-sample ORACLE reference no real underwriter
    has). Options never latent in the group leave the set, but the true option of every problem in the group has
    a share above zero, so it is never removed.
  An item whose template has no share in the table (or none above zero for its options) keeps the documented
  priors.
As in the other variants, only items that stand for a planted problem are changed (the truth and the reveals never
change). Checklist, random and oracle choose the same requests under every variant; only the VOI they are valued
at (and so the zero-value count) changes.

Not claimable: real time or cost savings; that the VOI score is optimal or an economic model; extraction accuracy
(extraction is an oracle). Cost weights and priors are planning assumptions written by the project author.
"""

import heapq
import random
from dataclasses import dataclass

from uwcopilot.agent.applications import canonical_json
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.docintel.levers import LeverError
from uwcopilot.docintel.truth import PacketTruth
from uwcopilot.evidence.open_item_models import OpenItem
from uwcopilot.evidence.open_items import detect_open_items
from uwcopilot.evidence.requests import ResolutionChoice, cost_weight
from uwcopilot.evidence.simulator_cases import (
    SimCase,
    full_information_state,
    latent_option_for,
    matches,
    matching_problems,
    prepare_case,
    revealed_state,
    undetected_problems,
)
from uwcopilot.evidence.simulator_diagnostics import template_key
from uwcopilot.evidence.simulator_models import (
    MAX_REQUESTS,
    ORACLE_MAX_OPEN_REQUESTS,
    ORACLE_MAX_POSITIONS,
    POLICIES,
    PRIOR_FACTORS,
    PRIORS_VARIANTS,
    TABLE_VARIANTS,
    EmpiricalPriors,
    PriorTables,
    SimStep,
    SimulationResult,
    StopReason,
    requests_to_stable,
)
from uwcopilot.evidence.state import CaseState, IncompleteApplicationError, evaluate
from uwcopilot.evidence.voi import group_items, rank_requests, request_value, resolution_choices

ChoicesByItem = dict[str, list[ResolutionChoice]]


@dataclass(frozen=True)
class Position:
    """Where a run is: the item ids requested so far, the planted problems revealed, the state and its result."""

    answered: tuple[str, ...]
    revealed: tuple[str, ...]
    state: CaseState
    result: PolicyResult


@dataclass(frozen=True)
class Pick:
    """A request a policy chose, with the VOI and P(change) the engine gave it just before."""

    group: list[OpenItem]
    voi: float
    p_change: float


# --------------------------------------------------------------------------------------------------------------
# Positions
# --------------------------------------------------------------------------------------------------------------


def start(case: SimCase) -> Position:
    """The position before any request."""
    return Position(answered=(), revealed=(), state=case.initial, result=evaluate(case.initial))


def open_requests(case: SimCase, position: Position) -> tuple[list[OpenItem], list[list[OpenItem]]]:
    """(open items not yet requested, those items grouped by shared request)."""
    items = detect_open_items(position.state, case.context, position.result, position.answered)
    return items, group_items(items)


def advance(case: SimCase, position: Position, group: list[OpenItem]) -> Position:
    """The position after requesting a group: its items are answered and the problems they stand for revealed."""
    answered = list(position.answered)
    revealed = list(position.revealed)
    for item in group:
        if item.item_id not in answered:
            answered.append(item.item_id)
        for problem_id in matching_problems(item, case.truth):
            if problem_id not in revealed:
                revealed.append(problem_id)
    state = revealed_state(case, revealed)
    return Position(answered=tuple(answered), revealed=tuple(revealed), state=state, result=evaluate(state))


def request_key(group: list[OpenItem]) -> str:
    """A stable text key of the request a group shares (request id and params)."""
    first = group[0]
    return f"{first.request_id}|{canonical_json(first.request_params)}"


# --------------------------------------------------------------------------------------------------------------
# Priors variants
# --------------------------------------------------------------------------------------------------------------


def adjusted_choices(choices: list[ResolutionChoice], latent: str, variant: str) -> list[ResolutionChoice]:
    """An item's options with the latent option's prior scaled (x0.5 / x1.5) or the option removed; renormalized."""
    if variant == "latent_removed":
        kept = [(choice, choice.prior) for choice in choices if choice.option_id != latent]
    else:
        factor = PRIOR_FACTORS[variant]
        kept = [(choice, choice.prior * factor if choice.option_id == latent else choice.prior) for choice in choices]
    total = sum(weight for _, weight in kept)
    if total <= 0:
        return []  # nothing plausible is left: the item's request has no computable value
    return [choice.model_copy(update={"prior": round(weight / total, 6)}) for choice, weight in kept]


def empirical_choices(choices: list[ResolutionChoice], shares: dict[str, float]) -> list[ResolutionChoice] | None:
    """An item's options with a table variant's latent shares as priors (renormalized over the options the item
    has); options with no share leave the set. None when no option of the item has a share above zero."""
    kept = [(choice, shares.get(choice.option_id, 0.0)) for choice in choices]
    kept = [(choice, share) for choice, share in kept if share > 0]
    total = sum(share for _, share in kept)
    if total <= 0:
        return None
    return [choice.model_copy(update={"prior": round(share / total, 6)}) for choice, share in kept]


def template_of(item: OpenItem, truth: PacketTruth) -> str | None:
    """The resolution template key of the first planted problem the item stands for (None when unmatched)."""
    for problem in truth.problems:
        if matches(item, problem):
            return template_key(problem)
    return None


def table_variant_choices(items: list[OpenItem], truth: PacketTruth, shares: EmpiricalPriors) -> ChoicesByItem:
    """A table variant's option sets (module docstring): only items that stand for a planted problem change."""
    replaced: ChoicesByItem = {}
    for item in items:
        key = template_of(item, truth)
        if key is None or key not in shares:
            continue
        choices, _ = resolution_choices(item)
        adjusted = empirical_choices(choices, shares[key])
        if adjusted is not None:
            replaced[item.item_id] = adjusted
    return replaced


def table_for(variant: str, tables: PriorTables | None) -> EmpiricalPriors:
    """A table variant's priors table (ValueError when the caller did not pass it)."""
    if tables is None or variant not in tables:
        raise ValueError(f"the {variant} variant needs its table of latent shares")
    return tables[variant]


def variant_choices(
    items: list[OpenItem], truth: PacketTruth, variant: str, tables: PriorTables | None = None
) -> ChoicesByItem | None:
    """The option sets the engine values items with under a priors variant (None: the documented templates).

    tables (variant -> template -> option -> prior) must hold the table of a TABLE_VARIANTS variant.
    """
    if variant == "documented":
        return None
    if variant not in PRIORS_VARIANTS:
        raise ValueError(f"unknown priors variant {variant!r}")
    if variant in TABLE_VARIANTS:
        return table_variant_choices(items, truth, table_for(variant, tables))
    replaced: ChoicesByItem = {}
    for item in items:
        latent = latent_option_for(item, truth)
        choices, _ = resolution_choices(item)
        if latent is None or not any(choice.option_id == latent for choice in choices):
            continue
        replaced[item.item_id] = adjusted_choices(choices, latent, variant)
    return replaced


# --------------------------------------------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------------------------------------------


def value_pick(position: Position, group: list[OpenItem], choices: ChoicesByItem | None) -> Pick:
    """A group with its VOI just before the request (a VOI that cannot be computed counts as 0)."""
    try:
        value = request_value(position.state, group, position.result, choices)
    except ValueError:
        return Pick(group=group, voi=0.0, p_change=0.0)
    return Pick(group=group, voi=value.voi, p_change=value.p_change)


def choose_voi(
    position: Position, items: list[OpenItem], groups: list[list[OpenItem]], choices: ChoicesByItem | None
) -> Pick | None:
    """The open request with the highest nonzero VOI (rank_requests' order), or None."""
    picks = [value_pick(position, group, choices) for group in groups]
    nonzero = [pick for pick in picks if pick.voi > 0]
    if not nonzero:
        return None
    nonzero.sort(
        key=lambda pick: (
            -pick.voi,
            cost_weight(str(pick.group[0].request_id)),
            str(pick.group[0].request_id),
            canonical_json(pick.group[0].request_params),
        )
    )
    return nonzero[0]


def choose_voi_lookahead(
    position: Position, items: list[OpenItem], groups: list[list[OpenItem]], choices: ChoicesByItem | None
) -> Pick | None:
    """choose_voi, else the zero-VOI request with the highest nonzero lookahead value, else None."""
    pick = choose_voi(position, items, groups, choices)
    if pick is not None:
        return pick
    try:
        ranked = rank_requests(position.state, items, position.result, choices)
    except ValueError:
        return None
    for request in ranked:
        if request.voi_given_others is not None and request.voi_given_others > 0:
            group = next(group for group in groups if tuple(item.item_id for item in group) == request.item_ids)
            return Pick(group=group, voi=request.voi, p_change=request.p_change)
    return None


@dataclass
class RandomOrder:
    """The random policy's request order: a seeded shuffle, with requests that appear later shuffled and appended."""

    rng: random.Random
    order: list[str]

    def next_group(self, groups: list[list[OpenItem]]) -> list[OpenItem]:
        """The first open request in the order (new requests are shuffled in at the end first)."""
        by_key = {request_key(group): group for group in groups}
        new_keys = sorted(key for key in by_key if key not in self.order)
        self.rng.shuffle(new_keys)
        self.order.extend(new_keys)
        return next(by_key[key] for key in self.order if key in by_key)


# --------------------------------------------------------------------------------------------------------------
# Oracle
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class OracleSearch:
    """The oracle's search result: the cheapest path (None when none reaches the target) and whether the
    ORACLE_MAX_POSITIONS guard stopped the search before it finished."""

    path: list[list[OpenItem]] | None
    guard_reached: bool


def oracle_path(case: SimCase, first: Position, target: str) -> OracleSearch:
    """The cheapest request sequence (cost-weighted; ties: fewer requests, then request keys) that reaches target.

    A uniform-cost search over the sets of requests made so far: the state after a set of reveals does not depend
    on their order (simulator_cases.revealed_state), so the cheapest path to each set is enough, and every request
    order of every length is covered. Costs are positive, so the first position popped with the target
    recommendation is a cheapest one.
    """
    counter = 0
    heap: list[tuple[float, int, tuple[str, ...], int, Position, list[list[OpenItem]]]] = [
        (0.0, 0, (), counter, first, [])
    ]
    seen: set[frozenset[str]] = set()
    while heap:
        if len(seen) >= ORACLE_MAX_POSITIONS:
            return OracleSearch(path=None, guard_reached=True)
        cost, count, keys, _, position, path = heapq.heappop(heap)
        visited = frozenset(position.answered)
        if visited in seen:
            continue
        seen.add(visited)
        if position.result.recommendation == target:
            return OracleSearch(path=path, guard_reached=False)
        _, groups = open_requests(case, position)
        for group in groups:
            counter += 1
            weight = cost_weight(str(group[0].request_id))
            following = advance(case, position, group)
            entry = (cost + weight, count + 1, (*keys, request_key(group)), counter, following, [*path, group])
            heapq.heappush(heap, entry)
    return OracleSearch(path=None, guard_reached=False)


# --------------------------------------------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunContext:
    """What every run of one case shares: the case, the start, the full-information recommendation."""

    case: SimCase
    first: Position
    target: str
    n_open_requests: int
    undetected: tuple[str, ...]

    @property
    def undetected_types(self) -> tuple[str, ...]:
        """The problem types of the undetected planted problems."""
        types = {problem.problem_id: problem.problem_type for problem in self.case.problems}
        return tuple(types[problem_id] for problem_id in self.undetected)


def step_after(position: Position, pick: Pick, following: Position, case: SimCase) -> SimStep:
    """The SimStep of one request."""
    before = position.result.recommendation
    after = following.result.recommendation
    revealed = tuple(problem_id for problem_id in following.revealed if problem_id not in position.revealed)
    return SimStep(
        request_id=str(pick.group[0].request_id),
        params=dict(pick.group[0].request_params),
        item_ids=tuple(item.item_id for item in pick.group),
        cost_weight=cost_weight(str(pick.group[0].request_id)),
        voi_before=pick.voi,
        p_change_before=pick.p_change,
        recommendation_before=before,
        recommendation_after=after,
        revealed_problem_ids=revealed,
        zero_value=pick.voi == 0 and after == before,
    )


def finished(
    header: dict, run: RunContext, steps: list[SimStep], stop_reason: StopReason, final: str
) -> SimulationResult:
    """The SimulationResult of a finished run (metrics from the steps)."""
    recommendations = [run.first.result.recommendation, *(step.recommendation_after for step in steps)]
    return SimulationResult(
        **header,
        computed=True,
        initial_recommendation=run.first.result.recommendation,
        full_information_recommendation=run.target,
        final_recommendation=final,
        n_initial_open_requests=run.n_open_requests,
        undetected_problem_ids=run.undetected,
        undetected_problem_types=run.undetected_types,
        steps=tuple(steps),
        stop_reason=stop_reason,
        n_requests=len(steps),
        cost_weighted_requests=round(sum(step.cost_weight for step in steps), 6),
        n_zero_value_requests=sum(1 for step in steps if step.zero_value),
        requests_to_stable=requests_to_stable(recommendations, run.target),
        decision_error=final != run.target,
    )


def run_oracle(header: dict, run: RunContext, variant: str, tables: PriorTables | None) -> SimulationResult:
    """The oracle's run (see the module docstring); requests are valued under the variant for the zero-value count."""
    if run.n_open_requests > ORACLE_MAX_OPEN_REQUESTS:
        reason = f"oracle not computed: {run.n_open_requests} open requests (more than {ORACLE_MAX_OPEN_REQUESTS})"
        return SimulationResult(**header, computed=False, not_computed_reason=reason)
    search = oracle_path(run.case, run.first, run.target)
    if search.guard_reached:
        reason = f"oracle not computed: the search guard of {ORACLE_MAX_POSITIONS} positions was reached"
        return SimulationResult(**header, computed=False, not_computed_reason=reason)
    if search.path is None:
        return finished(header, run, [], "oracle_unreachable", run.first.result.recommendation)
    position = run.first
    steps = []
    for group in search.path:
        items, _ = open_requests(run.case, position)
        choices = variant_choices(items, run.case.truth, variant, tables)
        pick = value_pick(position, group, choices)
        following = advance(run.case, position, group)
        steps.append(step_after(position, pick, following, run.case))
        position = following
    return finished(header, run, steps, "oracle_reached", position.result.recommendation)


def run_policy(
    header: dict,
    run: RunContext,
    policy: str,
    variant: str,
    rng: random.Random | None,
    tables: PriorTables | None = None,
) -> SimulationResult:
    """A voi, voi_lookahead, checklist or random run (see the module docstring)."""
    if policy == "random" and rng is None:
        raise ValueError("the random policy needs a seeded random.Random")
    shuffler = RandomOrder(rng=rng, order=[]) if rng is not None else None
    position = run.first
    steps: list[SimStep] = []
    stop: StopReason = "max_requests"
    while len(steps) < MAX_REQUESTS:
        items, groups = open_requests(run.case, position)
        if not groups:
            stop = "list_exhausted"
            break
        choices = variant_choices(items, run.case.truth, variant, tables)
        if policy == "voi":
            pick = choose_voi(position, items, groups, choices)
        elif policy == "voi_lookahead":
            pick = choose_voi_lookahead(position, items, groups, choices)
        elif policy == "checklist":
            pick = value_pick(position, groups[0], choices)
        else:
            pick = value_pick(position, shuffler.next_group(groups), choices)  # type: ignore[union-attr]
        if pick is None:
            stop = "no_nonzero_voi"
            break
        following = advance(run.case, position, pick.group)
        steps.append(step_after(position, pick, following, run.case))
        position = following
    return finished(header, run, steps, stop, position.result.recommendation)


def run_context(case: SimCase) -> RunContext:
    """The shared start of every run of a case (raises when the case cannot be evaluated)."""
    first = start(case)
    target = evaluate(full_information_state(case)).recommendation
    items, groups = open_requests(case, first)
    return RunContext(
        case=case,
        first=first,
        target=target,
        n_open_requests=len(groups),
        undetected=tuple(undetected_problems(case.truth, items)),
    )


def simulate(
    case_truth: PacketTruth,
    policy_name: str,
    priors_variant: str = "documented",
    rng: random.Random | None = None,
    prepared: SimCase | None = None,
    context: RunContext | None = None,
    tables: PriorTables | None = None,
) -> SimulationResult:
    """One policy on one case under one priors variant (see the module docstring).

    prepared and context let a caller reuse the case preparation and the shared start across runs; both are
    rebuilt from case_truth when omitted. tables holds the latent-share tables the TABLE_VARIANTS variants need
    (variant -> template -> option -> prior). A case whose application does not validate, or whose truth cannot be
    revealed, is returned with computed=False and the reason (never guessed).
    """
    if policy_name not in POLICIES:
        raise ValueError(f"unknown policy {policy_name!r}")
    if priors_variant not in PRIORS_VARIANTS:
        raise ValueError(f"unknown priors variant {priors_variant!r}")
    if priors_variant in TABLE_VARIANTS:
        table_for(priors_variant, tables)
    header = {
        "case_id": case_truth.packet_id,
        "line": case_truth.line,
        "split": case_truth.split,
        "problem_types": tuple(problem.problem_type for problem in case_truth.problems),
        "policy": policy_name,
        "priors_variant": priors_variant,
    }
    try:
        case = prepared if prepared is not None else prepare_case(case_truth)
        run = context if context is not None else run_context(case)
        if policy_name == "oracle":
            return run_oracle(header, run, priors_variant, tables)
        return run_policy(header, run, policy_name, priors_variant, rng, tables)
    except (IncompleteApplicationError, LeverError) as error:
        return SimulationResult(**header, computed=False, not_computed_reason=f"{type(error).__name__}: {error}")

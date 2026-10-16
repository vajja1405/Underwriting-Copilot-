"""Next-best evidence: a rules-based, scenario-based value-of-information score for evidence requests (Phase 4 D,
innovation S5).

For an open item (open_items.py) the resolution template lists its plausible resolutions r with documented prior
weights p(r) (config/resolution_templates.yaml; the priors are assumptions written by the project author). For each
resolution the engine applies its levers to the case state, runs the SAME reference policy, and reads the
recommendation and the decision metric. Then

    VOI(request) = sum_r p(r) x 1[recommendation(r) != current recommendation] x size(r) / cost_weight(request)
    size(r)      = 1 + min(|decision metric(r) - current decision metric|, 1.0) when both metrics exist, else 1.0

The decision metric is the decision DSCR for a loan (projected DSCR for a startup loan) and the highest 5-year loss
ratio of the quoted lines, as a fraction (55.0% -> 0.55), for insurance (scenarios.decision_metric). The cost weight
comes from config/evidence_requests.yaml (a relative planning assumption). VOI is exactly 0 when every resolution
leaves the recommendation unchanged: such a request has no decision value, however interesting the answer.

Items that share a request (the same request id and params: every missing checklist document, or two problems on
the same carrier's loss run) are valued together: one request reveals all of them, so the resolutions are the
combinations of each item's resolutions (priors multiplied), applied one item after the other.

Lookahead tie-breaker (an addition, not part of the VOI formula): the score is myopic, so when several open items
each keep the same recommendation (two blocking referrals, or a missing month and another missing document), every
one of them scores 0 alone. For a zero-value request, rank_requests therefore also computes the same score after
every OTHER open item is resolved at its highest-prior resolution (voi_given_others). It only orders the zero-value
requests among themselves and explains them; VOI itself, and the stopping rule "no request with VOI above 0", are
unchanged.

What this is not: not optimal and not an economic model. It trusts the priors and measures decision change with the
reference policy only. A resolution whose levers cannot be applied, or whose application does not validate, is
listed with its error and counts as no change. A request that cannot be scored (an item with no plausible
resolution, or more than MAX_JOINT_OPTIONS combinations, e.g. a loss run with ten out-of-balance lines) is returned
with computable False and the reason, never an exception: document amounts are untrusted input, so a crafted or
misread document must degrade the ranking, not stop the engine.
"""

import itertools
from collections.abc import Sequence

from uwcopilot.agent.applications import canonical_json
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.docintel.levers import LeverError
from uwcopilot.evidence.open_item_models import OpenItem
from uwcopilot.evidence.requests import ResolutionChoice, cost_weight, instantiate_options, request_catalog
from uwcopilot.evidence.scenarios import DecisionMetric, decision_metric
from uwcopilot.evidence.state import CaseState, IncompleteApplicationError, apply, evaluate
from uwcopilot.evidence.voi_models import OptionOutcome, RankedRequest, RequestValue
from uwcopilot.evidence.voi_reasons import options_summary, reason_sentence

SIZE_CAP = 1.0
MAX_JOINT_OPTIONS = 729
NO_OPTIONS_REASON = "no plausible resolution could be built from the case values"
TOO_MANY_OPTIONS_REASON = "{count} combinations of resolutions exceed the cap of {cap}, so the request is not scored"
PCT_TO_FRACTION = 100.0
SCORE_NAME = "a rules-based, scenario-based value-of-information score"

__all__ = [
    "SCORE_NAME",
    "OptionOutcome",
    "RankedRequest",
    "RequestValue",
    "group_items",
    "nonzero_requests",
    "options",
    "rank_requests",
    "request_value",
    "request_values",
    "resolution_choices",
    "voi",
]


# --------------------------------------------------------------------------------------------------------------
# Options of one item
# --------------------------------------------------------------------------------------------------------------


def resolution_choices(item: OpenItem) -> tuple[list[ResolutionChoice], tuple[str, ...]]:
    """(the item's plausible resolutions, ids of template options left out for missing case values)."""
    if item.template_key is None:
        return [], ()
    filled = instantiate_options(item.template_key, item.placeholders, item.extra_substitutions)
    return list(filled.options), filled.dropped_option_ids


def metric_fraction(metric: DecisionMetric, value: float | None) -> float | None:
    """A metric value on the VOI scale: DSCR as is, a percent as a fraction."""
    if value is None:
        return None
    return value / PCT_TO_FRACTION if metric.unit == "pct" else value


def change_size(metric: DecisionMetric, current: float | None, resolved: float | None) -> float:
    """size(r) = 1 + min(|delta metric|, 1.0) when both metrics exist, else 1.0 (module docstring)."""
    before, after = metric_fraction(metric, current), metric_fraction(metric, resolved)
    if before is None or after is None:
        return 1.0
    return 1.0 + min(abs(after - before), SIZE_CAP)


def evaluate_choice(state: CaseState, steps: Sequence[ResolutionChoice]) -> tuple[PolicyResult | None, str | None]:
    """The policy result after applying each choice in order (None and the error name when that fails)."""
    resolved = state
    try:
        for step in steps:
            resolved = apply(resolved, step.substitutions)
        return evaluate(resolved), None
    except (LeverError, IncompleteApplicationError, ValueError) as error:
        return None, type(error).__name__


def outcome_of(
    option_id: str,
    description: str,
    prior: float,
    result: PolicyResult | None,
    error: str | None,
    current: PolicyResult,
) -> OptionOutcome:
    """One OptionOutcome compared with the current result (a failed resolution counts as no change)."""
    if result is None:
        return OptionOutcome(
            option_id=option_id,
            description=description,
            prior=prior,
            recommendation=None,
            metric=None,
            changes_recommendation=False,
            size=0.0,
            weighted=0.0,
            error=error,
        )
    baseline, metric = decision_metric(current), decision_metric(result)
    changed = result.recommendation != current.recommendation
    size = change_size(baseline, baseline.value, metric.value) if changed else 0.0
    return OptionOutcome(
        option_id=option_id,
        description=description,
        prior=prior,
        recommendation=result.recommendation,
        metric=metric.value,
        changes_recommendation=changed,
        size=round(size, 6),
        weighted=round(prior * size, 6),
    )


def options(item: OpenItem, state: CaseState, current: PolicyResult | None = None) -> list[OptionOutcome]:
    """Each plausible resolution of one item: apply its levers, evaluate, read recommendation and metric."""
    reference = current if current is not None else evaluate(state)
    choices, _ = resolution_choices(item)
    outcomes = []
    for choice in choices:
        result, error = evaluate_choice(state, [choice])
        outcomes.append(outcome_of(choice.option_id, choice.description, choice.prior, result, error, reference))
    return outcomes


# --------------------------------------------------------------------------------------------------------------
# Value of a request
# --------------------------------------------------------------------------------------------------------------


def joint_count(choice_lists: list[list[ResolutionChoice]]) -> int:
    """The number of combinations of one choice per item."""
    total = 1
    for choices in choice_lists:
        total *= len(choices)
    return total


def joint_choices(choice_lists: list[list[ResolutionChoice]]) -> list[tuple[ResolutionChoice, ...]]:
    """Every combination of one choice per item (the cartesian product), capped at MAX_JOINT_OPTIONS (ValueError
    above it; request_value checks the count first and never raises)."""
    total = joint_count(choice_lists)
    if total > MAX_JOINT_OPTIONS:
        raise ValueError(f"{total} joint resolutions exceed the cap of {MAX_JOINT_OPTIONS}")
    return list(itertools.product(*choice_lists))


def item_choices(
    item: OpenItem, choices_by_item: dict[str, list[ResolutionChoice]] | None
) -> tuple[list[ResolutionChoice], tuple[str, ...]]:
    """An item's resolutions: the caller's replacement when given, else its template's (and dropped option ids)."""
    if choices_by_item is not None and item.item_id in choices_by_item:
        return list(choices_by_item[item.item_id]), ()
    return resolution_choices(item)


def request_value(
    state: CaseState,
    items: Sequence[OpenItem],
    current: PolicyResult | None = None,
    choices_by_item: dict[str, list[ResolutionChoice]] | None = None,
    cost_weights: dict[str, float] | None = None,
) -> RequestValue:
    """VOI of the request the items share (module docstring).

    choices_by_item replaces an item's template options (the simulator's robustness runs use it: priors scaled, or
    the true resolution removed); cost_weights overrides catalog weights.
    """
    reference = current if current is not None else evaluate(state)
    baseline = decision_metric(reference)
    request_id = str(items[0].request_id)
    weight = cost_weight(request_id, cost_weights)
    choice_lists: list[list[ResolutionChoice]] = []
    dropped: list[str] = []
    for item in items:
        choices, missing = item_choices(item, choices_by_item)
        choice_lists.append(choices)
        dropped.extend(f"{item.item_id}:{option_id}" for option_id in missing)
    computable = all(choice_lists)
    reason = None if computable else NO_OPTIONS_REASON
    if computable and joint_count(choice_lists) > MAX_JOINT_OPTIONS:
        computable = False
        reason = TOO_MANY_OPTIONS_REASON.format(count=joint_count(choice_lists), cap=MAX_JOINT_OPTIONS)
    outcomes: list[OptionOutcome] = []
    if computable:
        for combination in joint_choices(choice_lists):
            prior = 1.0
            for choice in combination:
                prior *= choice.prior
            result, error = evaluate_choice(state, combination)
            option_id = " + ".join(choice.option_id for choice in combination)
            description = " ".join(choice.description for choice in combination)
            outcomes.append(outcome_of(option_id, description, round(prior, 6), result, error, reference))
    return RequestValue(
        request_id=request_id,
        request_params=dict(items[0].request_params),
        item_ids=tuple(item.item_id for item in items),
        cost_weight=weight,
        current_recommendation=reference.recommendation,
        metric_name=baseline.name,
        current_metric=baseline.value,
        outcomes=tuple(outcomes),
        p_change=round(sum(outcome.prior for outcome in outcomes if outcome.changes_recommendation), 6),
        voi=round(sum(outcome.weighted for outcome in outcomes) / weight, 6),
        dropped_option_ids=tuple(dropped),
        computable=computable,
        not_computable_reason=reason,
    )


def voi(item: OpenItem, state: CaseState, current: PolicyResult | None = None) -> float:
    """VOI of a single item's own request (VOI(item) in the spec)."""
    return request_value(state, [item], current).voi


# --------------------------------------------------------------------------------------------------------------
# Grouping, lookahead and ranking
# --------------------------------------------------------------------------------------------------------------


def request_key(item: OpenItem) -> str:
    """Items with the same request id and params share one request."""
    return f"{item.request_id}|{canonical_json(item.request_params)}"


def group_items(items: Sequence[OpenItem]) -> list[list[OpenItem]]:
    """Items grouped by shared request, in first-seen order (items without a catalog request are left out)."""
    groups: dict[str, list[OpenItem]] = {}
    for item in items:
        if item.request_id is None:
            continue
        groups.setdefault(request_key(item), []).append(item)
    return list(groups.values())


def request_values(
    state: CaseState,
    items: Sequence[OpenItem],
    current: PolicyResult | None = None,
    choices_by_item: dict[str, list[ResolutionChoice]] | None = None,
    cost_weights: dict[str, float] | None = None,
) -> list[RequestValue]:
    """The value of every request the items need (one per group of items sharing a request)."""
    reference = current if current is not None else evaluate(state)
    return [request_value(state, group, reference, choices_by_item, cost_weights) for group in group_items(items)]


def resolve_others_as_most_likely(
    state: CaseState, others: Sequence[OpenItem], choices_by_item: dict[str, list[ResolutionChoice]] | None
) -> CaseState:
    """The state after each other item is resolved at its highest-prior resolution (an item that fails is skipped)."""
    resolved = state
    for item in others:
        choices, _ = item_choices(item, choices_by_item)
        if not choices:
            continue
        likely = max(choices, key=lambda choice: choice.prior)
        try:
            resolved = apply(resolved, likely.substitutions)
        except (LeverError, ValueError):
            continue
    return resolved


def lookahead_value(
    state: CaseState,
    group: list[OpenItem],
    others: list[OpenItem],
    choices_by_item: dict[str, list[ResolutionChoice]] | None,
    cost_weights: dict[str, float] | None,
) -> RequestValue | None:
    """The request's value once every other open item is resolved as most likely (None when not computable)."""
    if not others:
        return None
    resolved = resolve_others_as_most_likely(state, others, choices_by_item)
    try:
        reference = evaluate(resolved)
    except IncompleteApplicationError:
        return None
    return request_value(resolved, group, reference, choices_by_item, cost_weights)


def rank_requests(
    state: CaseState,
    items: Sequence[OpenItem],
    current: PolicyResult | None = None,
    choices_by_item: dict[str, list[ResolutionChoice]] | None = None,
    cost_weights: dict[str, float] | None = None,
) -> list[RankedRequest]:
    """Requests ranked by VOI (highest first). Ties: the lookahead value of a zero-value request (higher first), then
    the lower cost weight, then request id and params. Zero-value requests are listed too, with reasons saying so.
    """
    reference = current if current is not None else evaluate(state)
    groups = group_items(items)
    entries = []
    for group in groups:
        value = request_value(state, group, reference, choices_by_item, cost_weights)
        others = [item for other in groups if other is not group for item in other]
        ahead = None
        if value.voi == 0 and value.computable:
            ahead = lookahead_value(state, group, others, choices_by_item, cost_weights)
        entries.append((value, ahead))
    entries.sort(
        key=lambda entry: (
            -entry[0].voi,
            -(entry[1].voi if entry[1] is not None else 0.0),
            entry[0].cost_weight,
            entry[0].request_id,
            canonical_json(entry[0].request_params),
        )
    )
    return [
        RankedRequest(
            rank=rank,
            request_id=value.request_id,
            title=request_catalog()[value.request_id].title,
            params=value.request_params,
            item_ids=value.item_ids,
            voi=value.voi,
            p_change=value.p_change,
            cost_weight=value.cost_weight,
            options_summary=options_summary(value),
            reason=reason_sentence(value, ahead),
            voi_given_others=None if ahead is None else ahead.voi,
            p_change_given_others=None if ahead is None else ahead.p_change,
            recommendation_given_others=None if ahead is None else ahead.current_recommendation,
        )
        for rank, (value, ahead) in enumerate(entries, start=1)
    ]


def nonzero_requests(ranked: Sequence[RankedRequest]) -> list[RankedRequest]:
    """The ranked requests with a VOI above zero (the stopping rule: none left means stop asking)."""
    return [request for request in ranked if request.voi > 0]

"""Evidence requests for STARTUP files and their ranking (Founder Studio S6, next-best evidence).

The lender catalog (config/evidence_requests.yaml, read by requests.py) covers credit and insurance files. A startup
file (the SYNTHETIC Northwind startup program) has four more open problems (evidence/open_items_startup.py), whose
requests and plausible resolutions live in config/studio/startup_evidence_requests.yaml. They are kept apart so the
lender catalog, its version string and the Phase 4 results do not change.

Ranking reuses the value-of-information score of voi.py unchanged: voi.request_value with
- choices_by_item: each startup item's resolutions, instantiated from the startup templates here;
- cost_weights: the startup requests' documented cost weights.
Items of the shared lender catalog (a missing NSP-1.3 checklist document) keep their own templates and weights, so a
startup file's checklist letter and its equity statements are ranked on one scale. Reasons are written here with
voi_reasons.change_clause, because voi_reasons.reason_sentence reads titles from the lender catalog only.

Cost weights and priors are PLANNING ASSUMPTIONS written by the project author (see the YAML header).
"""

from collections.abc import Sequence
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.applications import canonical_json
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.evidence.open_item_models import OpenItem
from uwcopilot.evidence.requests import (
    InstantiatedTemplate,
    MissingPlaceholder,
    ResolutionChoice,
    fill_text,
    fill_value,
    read_yaml,
    request_catalog,
)
from uwcopilot.evidence.state import CaseState, evaluate
from uwcopilot.evidence.voi import group_items, lookahead_value, request_value
from uwcopilot.evidence.voi_models import RankedRequest, RequestValue
from uwcopilot.evidence.voi_reasons import change_clause, options_summary

STARTUP_REQUESTS_FILE = CONFIG_DIR / "studio" / "startup_evidence_requests.yaml"


class StartupRequest(BaseModel):
    """One startup request: what is asked, which problem it resolves, the document it brings, its cost weight."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    title: str
    ask: str
    resolves: tuple[str, ...]
    guideline_refs: tuple[str, ...]
    document: str
    cost_weight: float = Field(gt=0)


@lru_cache(maxsize=1)
def startup_catalog_file() -> dict[str, Any]:
    """The parsed YAML file (read once)."""
    return read_yaml(STARTUP_REQUESTS_FILE)


@lru_cache(maxsize=1)
def startup_catalog() -> dict[str, StartupRequest]:
    """Request id -> StartupRequest, in the file's order."""
    raw = startup_catalog_file()["requests"]
    return {request_id: StartupRequest(request_id=request_id, **entry) for request_id, entry in raw.items()}


def startup_catalog_version() -> str:
    """The file's version string (for handoff records and results)."""
    return str(startup_catalog_file()["version"])


def startup_templates() -> dict[str, Any]:
    """Template key -> raw template (its "options" list)."""
    return startup_catalog_file()["templates"]


def startup_cost_weights() -> dict[str, float]:
    """Request id -> documented cost weight (passed to voi.request_value as overrides)."""
    return {request_id: request.cost_weight for request_id, request in startup_catalog().items()}


def is_startup_request(request_id: str | None) -> bool:
    """True when the request id is one of this file's requests."""
    return request_id is not None and request_id in startup_catalog()


def instantiate_startup_options(template_key: str, placeholders: dict[str, Any]) -> InstantiatedTemplate:
    """A startup template's options filled with case values (requests.instantiate_options does the same for the
    lender templates). An option whose placeholder has no value is dropped and the remaining priors renormalized."""
    kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
    dropped: list[str] = []
    for option in startup_templates()[template_key]["options"]:
        try:
            substitutions = {
                fill_text(lever, placeholders): fill_value(value, placeholders)
                for lever, value in (option["substitutions"] or {}).items()
            }
        except MissingPlaceholder:
            dropped.append(option["option_id"])
            continue
        kept.append((option, substitutions))
    total = sum(float(option["prior"]) for option, _ in kept)
    choices = tuple(
        ResolutionChoice(
            option_id=option["option_id"],
            description=" ".join(str(option["description"]).split()),
            prior=round(float(option["prior"]) / total, 6),
            substitutions=substitutions,
        )
        for option, substitutions in kept
    )
    return InstantiatedTemplate(template_key=template_key, options=choices, dropped_option_ids=tuple(dropped))


def startup_choices(items: Sequence[OpenItem]) -> dict[str, list[ResolutionChoice]]:
    """{item id: its resolutions} for every startup item (the others keep the lender templates)."""
    choices: dict[str, list[ResolutionChoice]] = {}
    for item in items:
        if is_startup_request(item.request_id) and item.template_key is not None:
            choices[item.item_id] = list(instantiate_startup_options(item.template_key, item.placeholders).options)
    return choices


def request_title(request_id: str) -> str:
    """The title of a startup or lender request."""
    if is_startup_request(request_id):
        return startup_catalog()[request_id].title
    return request_catalog()[request_id].title


def request_phrase(value: RequestValue) -> str:
    """The quoted title plus what is asked for (codes only: a driver rank or a checklist code)."""
    phrase = f'"{request_title(value.request_id)}"'
    params = value.request_params
    if "driver" in params:
        phrase += f" for top driver {params['driver']}"
    if "document" in params:
        phrase += f" ({params['document']})"
    return phrase


def startup_reason(value: RequestValue, ahead: RequestValue | None) -> str:
    """The one-sentence reason of a request, with the same wording rules as voi_reasons.reason_sentence."""
    phrase = request_phrase(value)
    if not value.computable:
        why = value.not_computable_reason or "no plausible resolution could be built from the case values"
        return f"{phrase[0].upper()}{phrase[1:]} is not scored: {why}."
    clause = change_clause(value)
    if clause is not None:
        return f"Requesting {phrase} {clause}."
    lookahead = change_clause(ahead) if ahead is not None else None
    if lookahead is not None:
        return (
            f"Requesting {phrase} has zero decision value on its own (the other open items keep the recommendation "
            f"at {value.current_recommendation}); with them resolved as most likely, it {lookahead}."
        )
    return (
        f"Requesting {phrase} has zero decision value: under every plausible resolution the reference "
        f"recommendation stays {value.current_recommendation}."
    )


def rank_startup_requests(
    state: CaseState, items: Sequence[OpenItem], current: PolicyResult | None = None
) -> list[RankedRequest]:
    """Every request the items need, ranked by value of information (highest first), as voi.rank_requests ranks
    lender requests: ties go to the lookahead value of a zero-value request, then the lower cost weight, then the
    request id and params. Startup items use the startup templates and weights; lender items keep theirs."""
    reference = current if current is not None else evaluate(state)
    choices = startup_choices(items)
    weights = startup_cost_weights()
    groups = group_items(items)
    entries: list[tuple[RequestValue, RequestValue | None]] = []
    for group in groups:
        value = request_value(state, group, reference, choices, weights)
        others = [item for other in groups if other is not group for item in other]
        ahead = None
        if value.voi == 0 and value.computable:
            ahead = lookahead_value(state, group, others, choices, weights)
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
            title=request_title(value.request_id),
            params=value.request_params,
            item_ids=value.item_ids,
            voi=value.voi,
            p_change=value.p_change,
            cost_weight=value.cost_weight,
            options_summary=options_summary(value),
            reason=startup_reason(value, ahead),
            voi_given_others=None if ahead is None else ahead.voi,
            p_change_given_others=None if ahead is None else ahead.p_change,
            recommendation_given_others=None if ahead is None else ahead.current_recommendation,
        )
        for rank, (value, ahead) in enumerate(entries, start=1)
    ]

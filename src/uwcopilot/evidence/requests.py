"""The evidence-request catalog and the resolution templates, as the Evidence-to-Action Engine reads them.

Two config files define what the engine may ask for and how an open problem could plausibly resolve:
- config/evidence_requests.yaml: the ONLY requests the engine can recommend (id, title, ask, the problem types each
  resolves, guideline references) with a relative cost weight. Cost weights are planning assumptions written by the
  project author, not measurements;
- config/resolution_templates.yaml: for each problem type, the plausible resolutions with documented prior weights
  and the lever substitutions (docintel/levers.py vocabulary) that apply each one. Priors are assumptions too.

instantiate_options(template_key, placeholders) fills a template's "{name}" placeholders with case values. The fill
rule is the one the SYNTHETIC packet generator documents (a value written as "{name}" alone becomes the raw case
value, a number, dict or list; other text is formatted), re-implemented here so the engine never imports the
generator, which knows the hidden truth. An option whose placeholder has no case value is left out and the
remaining priors are renormalized; the caller is told which options were dropped.

An option whose "check:<id>" lever names a check the reference policy has no rule for (POLICY_CHECK_IDS: the checks
in agent.policy.DOCUMENT_CHECK_RULES, plus embedded_instructions, which acts on the injection flags) is left out the
same way. Such a lever cannot change any policy result, so the option would evaluate exactly like another option
under a different name. Today this is missing_document's "provided_inconsistent" ("check:<document>_consistency":
the guidelines route no rule on a late document that disagrees with the file), so a missing-document request is
valued with "provided_consistent" alone (prior 1.0). The template keeps the option for a future rule.

Everything here is SYNTHETIC: Northwind Business Credit and Harborline Mutual are fictional institutions.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.policy import DOCUMENT_CHECK_RULES
from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.docintel.levers import check_overrides

REQUESTS_FILE = CONFIG_DIR / "evidence_requests.yaml"
TEMPLATES_FILE = CONFIG_DIR / "resolution_templates.yaml"
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
EMBEDDED_INSTRUCTIONS_CHECK = "embedded_instructions"
# Check ids a "check:" lever can act on: a reference-policy rule exists for them (module docstring).
POLICY_CHECK_IDS: frozenset[str] = frozenset(
    {check_id for rules in DOCUMENT_CHECK_RULES.values() for check_id in rules} | {EMBEDDED_INSTRUCTIONS_CHECK}
)


class EvidenceRequest(BaseModel):
    """One catalog entry of config/evidence_requests.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    line: str
    title: str
    ask: str
    resolves: tuple[str, ...]
    guideline_refs: tuple[str, ...]
    cost_weight: float = Field(gt=0)


class ResolutionChoice(BaseModel):
    """One plausible resolution of an open item: its prior weight and the lever substitutions that apply it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    option_id: str
    description: str
    prior: float = Field(gt=0, le=1)
    substitutions: dict[str, Any]


class InstantiatedTemplate(BaseModel):
    """A template filled with case values: the options kept and the option ids left out (missing case values)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template_key: str
    options: tuple[ResolutionChoice, ...]
    dropped_option_ids: tuple[str, ...] = ()


def read_yaml(path: Path) -> dict[str, Any]:
    """A YAML file as a dict."""
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@lru_cache(maxsize=1)
def request_catalog() -> dict[str, EvidenceRequest]:
    """Request id -> EvidenceRequest, in the catalog's order."""
    raw = read_yaml(REQUESTS_FILE)["requests"]
    return {request_id: EvidenceRequest(request_id=request_id, **entry) for request_id, entry in raw.items()}


@lru_cache(maxsize=1)
def catalog_version() -> str:
    """The catalog's version string (for decision records)."""
    return str(read_yaml(REQUESTS_FILE)["version"])


@lru_cache(maxsize=1)
def templates() -> dict[str, Any]:
    """Template key -> raw template (its "options" list) from config/resolution_templates.yaml."""
    return read_yaml(TEMPLATES_FILE)["templates"]


@lru_cache(maxsize=1)
def templates_version() -> str:
    """The templates' version string (for decision records)."""
    return str(read_yaml(TEMPLATES_FILE)["version"])


def cost_weight(request_id: str, overrides: dict[str, float] | None = None) -> float:
    """The documented cost weight of a request (an override wins; used by tests and robustness runs)."""
    if overrides and request_id in overrides:
        return float(overrides[request_id])
    return request_catalog()[request_id].cost_weight


class MissingPlaceholder(KeyError):
    """A template placeholder has no case value."""


def placeholder_names(text: str) -> list[str]:
    """The {name} placeholders in a text."""
    return PLACEHOLDER.findall(text)


def fill_text(text: str, placeholders: dict[str, Any]) -> str:
    """A lever name or text with every {name} replaced by the case value written as text."""
    for name in placeholder_names(text):
        if placeholders.get(name) is None:
            raise MissingPlaceholder(name)
    return text.format(**{name: str(value) for name, value in placeholders.items()})


def fill_value(value: Any, placeholders: dict[str, Any]) -> Any:
    """A substitution value: "{name}" alone becomes the raw case value; other text is formatted; else kept."""
    if isinstance(value, str) and value.startswith("{") and value.endswith("}") and value.count("{") == 1:
        name = value[1:-1]
        if placeholders.get(name) is None:
            raise MissingPlaceholder(name)
        return placeholders[name]
    if isinstance(value, str):
        return fill_text(value, placeholders)
    return value


def instantiate_options(
    template_key: str, placeholders: dict[str, Any], extra_substitutions: dict[str, Any] | None = None
) -> InstantiatedTemplate:
    """The template's options filled with case values; extra_substitutions are added to every option.

    Options whose placeholders have no case value, or whose check lever no policy rule reads (module docstring),
    are dropped and the remaining priors renormalized to sum to 1 (the documented priors keep their ratios).
    """
    kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
    dropped: list[str] = []
    for option in templates()[template_key]["options"]:
        try:
            substitutions = {
                fill_text(lever, placeholders): fill_value(value, placeholders)
                for lever, value in option["substitutions"].items()
            }
        except MissingPlaceholder:
            dropped.append(option["option_id"])
            continue
        if not set(check_overrides(substitutions)) <= POLICY_CHECK_IDS:
            dropped.append(option["option_id"])
            continue
        kept.append((option, {**substitutions, **(extra_substitutions or {})}))
    total = sum(float(option["prior"]) for option, _ in kept)
    choices = tuple(
        ResolutionChoice(
            option_id=option["option_id"],
            description=option["description"],
            prior=round(float(option["prior"]) / total, 6),
            substitutions=substitutions,
        )
        for option, substitutions in kept
    )
    return InstantiatedTemplate(template_key=template_key, options=choices, dropped_option_ids=tuple(dropped))

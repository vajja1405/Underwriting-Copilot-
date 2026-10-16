"""Choosing the problems of a SYNTHETIC packet and recording them as InjectedProblem ground truth.

The generator decides a packet's problems FIRST (choose_problems), then builds the case so the problems can occur
(for example a debt payment mismatch needs a debt), applies them while it builds the documents
(problems_credit.py, problems_insurance.py), and finally records each one (make_problem) with:
- the documents and values involved (values_by_doc);
- the evidence request that resolves it (config/evidence_requests.yaml);
- the plausible resolutions: the template of config/resolution_templates.yaml filled with case values, with the
  documented priors;
- the latent option (the one the true cause implies) and the exact true values (latent_substitutions).

Problem rates are TARGETS from the Phase 2 spec. Each problem type is drawn independently, so about a quarter to
a third of packets are clean and several problems may co-occur; a few combinations that would hide each other are
not drawn together (see choose_problems). One problem is not drawn but follows from the case: a loan request above
$500,000 always lacks the CPA-reviewed financial statements (no packet document type carries them), recorded as an
extra missing_document problem (credit_generate.add_cpa_financials_problem).
"""

import random
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.docintel.synthetic.common import weighted_choice
from uwcopilot.docintel.truth import CLASSIFICATION_FOR_CAUSE, InjectedProblem, ResolutionOption

# Probability that a packet has each problem (independent draws), and the split of true causes.
CREDIT_RATES: dict[str, float] = {
    "revenue_gap": 0.35,
    "missing_pages": 0.15,
    "missing_document": 0.10,
    "name_mismatch": 0.06,
    "fein_mismatch": 0.06,
    "owner_mismatch": 0.08,
    "balance_discontinuity": 0.06,
    "debt_payment_mismatch": 0.15,
    "prompt_injection": 0.10,
}
# fein_mismatch is not drawn for insurance: in the extraction schema only the application form carries a FEIN,
# so a different FEIN on a second document could not be extracted or checked.
INSURANCE_RATES: dict[str, float] = {
    "loss_run_total_mismatch": 0.20,
    "paid_reserve_mismatch": 0.08,
    "tiv_mismatch": 0.30,
    "claims_history_mismatch": 0.15,
    "missing_document": 0.12,
    "name_mismatch": 0.05,
    "owner_mismatch": 0.06,
    "prompt_injection": 0.10,
}
REVENUE_GAP_CAUSES: dict[str, float] = {
    "benign_non_revenue_counted": 0.30,
    "benign_unidentified_revenue": 0.20,
    "benign_timing": 0.15,
    "unsupported_overstatement": 0.35,
}
IDENTITY_CAUSES: dict[str, float] = {"benign_typo": 0.60, "unsupported_unexplained": 0.40}
OWNER_CAUSES: dict[str, float] = {"benign_typo": 0.40, "unsupported_unexplained": 0.60}
TIV_CAUSES: dict[str, float] = {"benign_outdated_application": 0.50, "unsupported_unexplained": 0.50}
CLAIMS_HISTORY_CAUSES: dict[str, float] = {"benign_timing": 0.50, "unsupported_unexplained": 0.50}
LOSS_RUN_TOTAL_CAUSES: dict[str, float] = {"integrity_arithmetic": 0.50, "unsupported_unexplained": 0.50}
FIXED_CAUSES: dict[str, str] = {
    "missing_pages": "missing",
    "missing_document": "missing",
    "balance_discontinuity": "integrity_arithmetic",
    "debt_payment_mismatch": "benign_rule_resolved",
    "prompt_injection": "integrity_embedded_instruction",
    "paid_reserve_mismatch": "integrity_arithmetic",
}
CAUSE_SPLITS: dict[str, dict[str, float]] = {
    "revenue_gap": REVENUE_GAP_CAUSES,
    "name_mismatch": IDENTITY_CAUSES,
    "fein_mismatch": IDENTITY_CAUSES,
    "owner_mismatch": OWNER_CAUSES,
    "tiv_mismatch": TIV_CAUSES,
    "claims_history_mismatch": CLAIMS_HISTORY_CAUSES,
    "loss_run_total_mismatch": LOSS_RUN_TOTAL_CAUSES,
}


@dataclass
class ProblemPlan:
    """A problem chosen for a packet. details holds what the builders decide (documents, amounts, options)."""

    problem_type: str
    cause: str
    details: dict[str, Any] = field(default_factory=dict)


def draw_cause(rng: random.Random, problem_type: str) -> str:
    """The true cause of a problem type (fixed for some types, drawn from the documented split for others)."""
    if problem_type in FIXED_CAUSES:
        return FIXED_CAUSES[problem_type]
    return weighted_choice(rng, CAUSE_SPLITS[problem_type])


def choose_problems(rng: random.Random, line: str) -> list[ProblemPlan]:
    """Independent draws per problem type, with two exclusions for credit.

    A missing statement month makes verified revenue unknown (NBC-3.2), which would hide a revenue gap, so
    missing_pages is only drawn for packets without a revenue gap; its rate is raised by 1 / (1 - 0.35) so the
    overall rate stays near the target.
    """
    rates = CREDIT_RATES if line == "credit" else INSURANCE_RATES
    plans: list[ProblemPlan] = []
    for problem_type, rate in rates.items():
        chance = rate
        if problem_type == "missing_pages":
            if any(plan.problem_type == "revenue_gap" for plan in plans):
                continue
            chance = rate / (1.0 - CREDIT_RATES["revenue_gap"])
        if rng.random() < chance:
            plans.append(ProblemPlan(problem_type, draw_cause(rng, problem_type)))
    return plans


def find_plan(plans: list[ProblemPlan], problem_type: str) -> ProblemPlan | None:
    """The plan of one problem type, if the packet has it."""
    for plan in plans:
        if plan.problem_type == problem_type:
            return plan
    return None


def scenario_name(plans: list[ProblemPlan]) -> str:
    """'clean', or the problem types joined with '+' in a fixed order (each type once)."""
    if not plans:
        return "clean"
    types: list[str] = []
    for plan in plans:
        if plan.problem_type not in types:
            types.append(plan.problem_type)
    return "+".join(types)


def is_structural(plan: ProblemPlan) -> bool:
    """True for a problem that follows from the case instead of being drawn (CPA-reviewed financials above
    $500,000, see credit_generate.add_cpa_financials_problem). Problem rates count drawn problems only."""
    return bool(plan.details.get("structural", False))


# --------------------------------------------------------------------------------------------------------------
# Resolution templates
# --------------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def resolution_templates() -> dict[str, Any]:
    """config/resolution_templates.yaml, parsed once."""
    with (CONFIG_DIR / "resolution_templates.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)["templates"]


@lru_cache(maxsize=1)
def evidence_request_ids() -> tuple[str, ...]:
    """The request ids of config/evidence_requests.yaml."""
    with (CONFIG_DIR / "evidence_requests.yaml").open(encoding="utf-8") as handle:
        return tuple(yaml.safe_load(handle)["requests"])


def template_key(problem_type: str, resolving_request: str) -> str:
    """The template for a problem: "missing_loss_run" for a missing loss run, otherwise the problem type."""
    if resolving_request == "missing_loss_run":
        return "missing_loss_run"
    return problem_type


def fill_key(text: str, params: dict[str, Any]) -> str:
    """A lever name with {placeholders} filled from params (as text)."""
    return text.format(**{name: str(value) for name, value in params.items()})


def fill_value(value: Any, params: dict[str, Any]) -> Any:
    """A substitution value: "{name}" alone becomes the raw case value (a number, dict or list); other text is
    formatted; anything else is kept."""
    if isinstance(value, str) and value.startswith("{") and value.endswith("}") and value.count("{") == 1:
        name = value[1:-1]
        if name not in params:
            raise KeyError(f"no case value for template placeholder {value}")
        return params[name]
    if isinstance(value, str):
        return fill_key(value, params)
    return value


def instantiate_options(key: str, params: dict[str, Any]) -> list[ResolutionOption]:
    """The template's options with every placeholder filled from params; priors are the documented ones."""
    options = []
    for option in resolution_templates()[key]["options"]:
        substitutions = {
            fill_key(lever, params): fill_value(value, params) for lever, value in option["substitutions"].items()
        }
        options.append(
            ResolutionOption(
                option_id=option["option_id"],
                description=option["description"],
                prior=float(option["prior"]),
                substitutions=substitutions,
            )
        )
    return options


@dataclass
class ProblemRecord:
    """Everything make_problem needs besides the plan: where the problem shows and how it resolves."""

    doc_ids: list[str]
    fields: list[str]
    values_by_doc: dict[str, float | int | str | bool | None]
    description: str
    resolving_request: str
    params: dict[str, Any]
    latent_option_id: str
    latent_substitutions: dict[str, Any]
    resolving_request_params: dict[str, str] = field(default_factory=dict)
    extra_option_substitutions: dict[str, Any] = field(default_factory=dict)


def make_problem(index: int, plan: ProblemPlan, record: ProblemRecord) -> InjectedProblem:
    """The InjectedProblem ground truth for one applied problem (problem ids PB-01, PB-02, ...).

    extra_option_substitutions are added to every option (used for a missing loss run, whose template has no
    documents_provided lever; see problems_insurance.py).
    """
    if record.resolving_request not in evidence_request_ids():
        raise ValueError(f"unknown evidence request {record.resolving_request}")
    options = instantiate_options(template_key(plan.problem_type, record.resolving_request), record.params)
    if record.extra_option_substitutions:
        options = [
            option.model_copy(update={"substitutions": {**option.substitutions, **record.extra_option_substitutions}})
            for option in options
        ]
    return InjectedProblem(
        problem_id=f"PB-{index:02d}",
        problem_type=plan.problem_type,
        cause=plan.cause,
        classification=CLASSIFICATION_FOR_CAUSE[plan.cause],
        doc_ids=record.doc_ids,
        fields=record.fields,
        values_by_doc=record.values_by_doc,
        description=record.description,
        resolving_request=record.resolving_request,
        resolving_request_params=record.resolving_request_params,
        resolution_options=options,
        latent_option_id=record.latent_option_id,
        latent_substitutions=record.latent_substitutions,
    )


def nearest_option(value: float, candidates: dict[str, float]) -> str:
    """The option id whose value is closest to `value` (ties go to the first listed)."""
    best = next(iter(candidates))
    for option_id, candidate in candidates.items():
        if abs(candidate - value) < abs(candidates[best] - value):
            best = option_id
    return best

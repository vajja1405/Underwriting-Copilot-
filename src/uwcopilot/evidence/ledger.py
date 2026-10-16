"""The evidence ledger (Phase 4, SIG step 3): every claim a recommendation or a founder plan depends on.

One ledger serves both sides of the merged project (lender copilot and Founder Studio). It is not duplicated.

A Claim is one value with its status and provenance. Six statuses:
- VERIFIED: extracted from a document (or a public data row) and consistent with every other source, or a
  deterministic calculation over such values;
- MODEL_ESTIMATE: produced by a risk model or simulator, with the model name, version and its uncertainty;
- CONFLICTING: two or more sources disagree; conflict_ids links the contradictions (CT-xx) or claims involved;
- UNKNOWN: required but not present; reason says why. A value filled in for a model is marked imputed, with the
  imputation method; nothing is imputed silently. build_ledger keeps an imputed default OUT of the claim's value
  (it is in provenance.details["imputed_value"] and in the inputs), so a memo prints "not available", never the
  default as if it were read. A calculation the policy ran over such a value is UNKNOWN too (depends_on_unknown), so
  is a document check's outcome over one (ledger_facts.check_claims, ledger_tables.CHECK_INPUT_PATTERNS), and a rule
  that PASSED on such a value is shown as not evaluated (assertion result None);
- USER_STATED (Founder Studio, and any applicant figure no document supports): the applicant's own figure;
- ASSUMPTION (Founder Studio): a labeled planning assumption, with its range when it is a number and its source or
  the word "unsourced".
Every claim has id, label, value, unit, display (a renderer format), status, provenance (kind, ref, document
citations, model name and version, source, metro and snapshot date for Studio data) and inputs_sha256 (SHA-256 of
agent.applications.canonical_json(inputs)). An application fact's inputs hold its path AND its value, so the hash
pins the value.

Values a resolution or scenario substituted (state.substituted_paths, set by evidence.state.apply) are ASSUMPTION
claims sourced to the lever, never VERIFIED and never cited to a document page; a contradiction whose compared
value was substituted (or whose check a lever resolved) is closed, and its check claim is an ASSUMPTION too.
Calculations over such values list them in depends_on_assumption.

An Assertion is one rule outcome: result True (passed), False (finding) or None (not evaluated), with the rule's
trigger id, guideline section and the three phrases the memo renderer prints.

The pieces live in small modules: ledger_models.py (the shapes and their validation), ledger_facts.py (application
facts, unread values, substituted values, document checks), ledger_assertions.py (rule outcomes), ledger_tables.py
(static tables); this module adds the calculations and the model claims and assembles the ledger (build_ledger).

Ledger satisfies the renderer protocol of uwcopilot.provenance.renderer (LedgerLike: claim(), assertion(),
claim_values(); ClaimLike.provenance_note(); AssertionLike phrases), so {{claim:ID}} / {{assert:ID}} slots fill
from it. Two ways to build one:
- build_ledger(state, policy_result, extraction, provenance, contradictions, estimator) for a lender case;
- Ledger.builder().add_claim(...).add_assertion(...).build() for anything else (Studio packages), with the helper
  constructors user_stated_claim, assumption_claim and unknown_claim.

The Phase 1 models do not apply to these SYNTHETIC commercial cases, so model PD and expected loss are UNKNOWN with
the reason state.MODEL_NOT_EVALUATED_REASON. A real model can be plugged in through the ModelEstimator protocol;
tests use a fake estimator labeled fake.

Calculations stay deterministic: SR 26-2 / OCC Bulletin 2026-13 (April 17, 2026) excludes "simple arithmetic
calculations" and "deterministic rule-based processes" from its model definition. Applicability depends on the
institution and its risk profile; this is a demonstration, not a compliance program.
"""

import re
from collections.abc import Iterable
from typing import Any, Protocol

from pydantic import Field

from uwcopilot.agent.applications import AnyApplication, CreditApplication, InsuranceApplication
from uwcopilot.agent.policy import Calculation, PolicyResult
from uwcopilot.agent.policy_credit import stress_applies
from uwcopilot.agent.policy_insurance import coverage_lines
from uwcopilot.docintel.contradictions import Contradiction, ContradictionReport
from uwcopilot.docintel.schemas import PacketExtraction
from uwcopilot.evidence import ledger_tables as tables
from uwcopilot.evidence.ledger_assertions import policy_assertions
from uwcopilot.evidence.ledger_facts import (
    ID_UNSAFE,
    IMPUTED_DEFAULT_METHOD,
    check_claims,
    conflicts_by_claim,
    fact_claims,
    missing_document_claims,
    open_contradictions,
    required_documents_for,
    undocumented_default_claims,
    unknown_path_claims,
)
from uwcopilot.evidence.ledger_models import (
    CLAIM_STATUSES,
    ID_PATTERN,
    KNOWN_UNITS,
    OPEN_STATUSES,
    SYNTHETIC,
    UNIT_PATTERN,
    UNSOURCED,
    Assertion,
    Citation,
    Claim,
    ClaimStatus,
    ClaimValue,
    InputValue,
    Ledger,
    LedgerBuilder,
    LedgerModel,
    Provenance,
    ProvenanceKind,
    ValueRange,
    assumption_claim,
    inputs_hash,
    unknown_claim,
    user_stated_claim,
)
from uwcopilot.evidence.state import (
    CLEAN_SCREENING_ASSUMPTION,
    MODEL_NOT_EVALUATED_REASON,
    CaseState,
    application_of,
    evaluate,
    screening_assumed,
)

MODEL_PD_CLAIM = "MODEL-pd"
MODEL_EXPECTED_LOSS_CLAIM = "MODEL-expected_loss"
SCREENING_ASSUMPTION_CLAIM = "ASSUME-screening_clear"
NOT_COMPUTABLE_REASON = "not computable from its inputs (for example a zero denominator)"
CALC_IMPUTED_METHOD = "computed by the reference policy with values no document provided (see depends_on_unknown)"
CALC_UNKNOWN_REASON = "computed by the reference policy from values the documents did not provide: {ids}"

# --------------------------------------------------------------------------------------------------------------
# Model estimates (a clean path for a real model; none applies to these SYNTHETIC commercial cases)
# --------------------------------------------------------------------------------------------------------------


class ModelEstimate(LedgerModel):
    """One model output for the ledger, with the features the model lacked or had imputed."""

    claim_id: str = Field(pattern=ID_PATTERN)
    label: str
    value: float
    unit: str = Field(pattern=UNIT_PATTERN)
    display: str
    model_name: str
    model_version: str
    uncertainty: str
    inputs: dict[str, InputValue] = Field(default_factory=dict)
    unknown_features: tuple[str, ...] = ()
    imputed_features: dict[str, str] = Field(default_factory=dict)
    is_fake: bool = False


class ModelEstimator(Protocol):
    """Anything that can score a validated application for the ledger."""

    def estimate(self, application: AnyApplication) -> list[ModelEstimate]:
        """Model outputs for the application (an empty list when the model does not apply)."""
        ...


def feature_claim_id(name: str) -> str:
    """'FEATURE-<name>' with characters outside the slot grammar replaced by '_'."""
    return "FEATURE-" + ID_UNSAFE.sub("_", name)


def estimate_claims(estimate: ModelEstimate) -> list[Claim]:
    """The MODEL_ESTIMATE claim, plus one UNKNOWN claim per missing or imputed model feature."""
    label = estimate.label + (" (FAKE estimator, test only)" if estimate.is_fake else "")
    details: dict[str, InputValue] = {"fake": True} if estimate.is_fake else {}
    claims = [
        Claim(
            id=estimate.claim_id,
            label=label,
            value=estimate.value,
            unit=estimate.unit,
            display=estimate.display,
            status="MODEL_ESTIMATE",
            provenance=Provenance(
                kind="model",
                ref=estimate.model_name,
                model_name=estimate.model_name,
                model_version=estimate.model_version,
                details=details,
            ),
            inputs=dict(estimate.inputs),
            uncertainty=estimate.uncertainty,
        )
    ]
    for name in estimate.unknown_features:
        claims.append(
            unknown_claim(
                feature_claim_id(name),
                f"Model feature {name}",
                "NONE",
                "text",
                f"needed by {estimate.model_name} but not available in this case; not imputed",
                kind="model",
                ref=estimate.model_name,
            )
        )
    for name, method in estimate.imputed_features.items():
        claims.append(
            Claim(
                id=feature_claim_id(name),
                label=f"Model feature {name}",
                value=None,
                unit="NONE",
                display="text",
                status="UNKNOWN",
                provenance=Provenance(kind="model", ref=estimate.model_name),
                reason=f"needed by {estimate.model_name} but not available in this case",
                imputed=True,
                imputation_method=method,
            )
        )
    return claims


def default_model_claims(line: str) -> list[Claim]:
    """The model claims of a line, UNKNOWN with the honesty reason (no Phase 1 model applies)."""
    if line == "insurance":
        return [
            unknown_claim(
                MODEL_EXPECTED_LOSS_CLAIM,
                "Model expected loss per policy year",
                "USD",
                "usd2",
                MODEL_NOT_EVALUATED_REASON,
                kind="model",
            )
        ]
    return [
        unknown_claim(
            MODEL_PD_CLAIM, "Model probability of default", "PROBABILITY", "ratio2", MODEL_NOT_EVALUATED_REASON, "model"
        )
    ]


# --------------------------------------------------------------------------------------------------------------
# build_ledger: a lender case
# --------------------------------------------------------------------------------------------------------------


def calc_claim(calculation: Calculation) -> Claim:
    """A policy calculation: VERIFIED arithmetic, or UNKNOWN when its value could not be computed."""
    unit, display = tables.CALC_UNITS[calculation.unit]
    status = "VERIFIED" if calculation.value is not None else "UNKNOWN"
    return Claim(
        id=calculation.calc_id,
        label=calculation.label,
        value=calculation.value,
        unit=unit,
        display=display,
        status=status,
        provenance=Provenance(
            kind="calculation", ref=calculation.calc_id, details={"sections": ", ".join(calculation.section_ids)}
        ),
        inputs=dict(calculation.inputs),
        reason=None if calculation.value is not None else NOT_COMPUTABLE_REASON,
    )


def direct_dependencies(calculation: Calculation, claim_ids: Iterable[str] = ()) -> list[str]:
    """Claim ids a calculation reads: through its input names, CALC_EXTRA_DEPENDENCIES, and the claim_ids that
    match its CALC_UNKNOWN_PATTERNS (ids of unread owners, months or loss-run years)."""
    found: list[str] = []
    for name in calculation.inputs:
        for claim_id in tables.CALC_INPUT_CLAIMS.get(name, ()):
            if claim_id not in found:
                found.append(claim_id)
    for claim_id in tables.CALC_EXTRA_DEPENDENCIES.get(calculation.calc_id, ()):
        if claim_id not in found:
            found.append(claim_id)
    patterns = [re.compile(pattern) for pattern in tables.CALC_UNKNOWN_PATTERNS.get(calculation.calc_id, ())]
    for claim_id in claim_ids:
        if claim_id not in found and any(pattern.search(claim_id) for pattern in patterns):
            found.append(claim_id)
    return found


def dependency_roots(
    claim_id: str, dependencies: dict[str, list[str]], targets: set[str], seen: set[str] | None = None
) -> list[str]:
    """The target claims a claim depends on, directly or through other calculations (sorted)."""
    visited = seen if seen is not None else set()
    roots: set[str] = set()
    for dependency in dependencies.get(claim_id, []):
        if dependency in visited:
            continue
        visited.add(dependency)
        if dependency in targets:
            roots.add(dependency)
        roots.update(dependency_roots(dependency, dependencies, targets, visited))
    return sorted(roots)


def conflicting_roots(
    claim_id: str, dependencies: dict[str, list[str]], conflicting: set[str], seen: set[str] | None = None
) -> list[str]:
    """The CONFLICTING claims a claim depends on, directly or through other calculations (sorted)."""
    return dependency_roots(claim_id, dependencies, conflicting, seen)


def unknown_calc_claim(claim: Claim, roots: list[str]) -> Claim:
    """A calculation the policy ran over values no document provided: UNKNOWN, its number kept in details only."""
    details = {**claim.provenance.details, "value_with_unread_inputs": claim.value}
    return claim.model_copy(
        update={
            "status": "UNKNOWN",
            "value": None,
            "reason": CALC_UNKNOWN_REASON.format(ids=", ".join(roots)),
            "imputed": True,
            "imputation_method": CALC_IMPUTED_METHOD,
            "depends_on_unknown": tuple(roots),
            "provenance": claim.provenance.model_copy(update={"details": details}),
        }
    )


def calculation_claims(policy_result: PolicyResult, claims: list[Claim]) -> list[Claim]:
    """Every policy calculation as a claim, with its dependencies on the claims built so far:
    - over a CONFLICTING claim: listed in depends_on_conflicting (the arithmetic itself stays VERIFIED);
    - over an UNKNOWN claim (a value no document provided, often an imputed default): the calculation is UNKNOWN,
      imputed, with its number in provenance.details only (unknown_calc_claim), never shown as VERIFIED;
    - over an ASSUMPTION (a substituted value; the clean screening assumption is not an input): listed in
      depends_on_assumption."""
    statuses = {claim.id: claim.status for claim in claims}
    ids = list(statuses)
    dependencies = {item.calc_id: direct_dependencies(item, ids) for item in policy_result.calculations}
    targets = {status: {i for i, found in statuses.items() if found == status} for status in OPEN_STATUSES}
    assumptions = {i for i, found in statuses.items() if found == "ASSUMPTION"}
    result = []
    for calculation in policy_result.calculations:
        claim = calc_claim(calculation)
        conflicting = dependency_roots(calculation.calc_id, dependencies, targets["CONFLICTING"])
        unknown = dependency_roots(calculation.calc_id, dependencies, targets["UNKNOWN"])
        assumed = dependency_roots(calculation.calc_id, dependencies, assumptions)
        claim = claim.model_copy(
            update={"depends_on_conflicting": tuple(conflicting), "depends_on_assumption": tuple(assumed)}
        )
        if unknown and claim.value is not None:
            claim = unknown_calc_claim(claim, unknown)
        elif unknown:
            claim = claim.model_copy(update={"depends_on_unknown": tuple(unknown)})
        result.append(claim)
    return result


def not_evaluated_specs(rule: str, application: AnyApplication) -> list[tuple[str, str, str]]:
    """(calc id, label, CalcUnit) of the calculations a not-evaluated rule left out.

    Stress tests count only when NBC-4.5 applies to the request (the rate stress only for a variable rate), and
    loss ratios only for the lines being quoted.
    """
    if rule == "stress_tests" and isinstance(application, CreditApplication):
        if not stress_applies(application):
            return []
        specs = list(tables.NOT_EVALUATED_CLAIMS[rule])
        if application.rate_type == "variable":
            specs.append(tables.RATE_STRESS_CALC)
        return specs
    if rule in tables.NOT_EVALUATED_CLAIMS:
        return list(tables.NOT_EVALUATED_CLAIMS[rule])
    if not isinstance(application, InsuranceApplication):
        return []
    if rule == "loss_ratios_and_frequency":
        specs = [
            (tables.LOSS_RATIO_CALC.format(line=line), f"5-year loss ratio, {line.replace('_', ' ')}", "pct")
            for line in coverage_lines(application)
        ]
        return [*specs, tables.CLAIM_FREQUENCY_CALC]
    if rule.startswith("loss_ratio_"):
        line = rule.removeprefix("loss_ratio_")
        return [(tables.LOSS_RATIO_CALC.format(line=line), f"5-year loss ratio, {line.replace('_', ' ')}", "pct")]
    return []


def not_evaluated_claims(policy_result: PolicyResult, application: AnyApplication, existing: set[str]) -> list[Claim]:
    """UNKNOWN claims for the calculations the policy could not run (reason: the policy's own reason, or
    NOT_EVALUATED_REASONS), and for the calculations a finding skips (FINDING_MISSING_CALCS)."""
    claims: list[Claim] = []
    for item in policy_result.not_evaluated:
        for calc_id, label, calc_unit in not_evaluated_specs(item.rule, application):
            if calc_id in existing or any(claim.id == calc_id for claim in claims):
                continue
            unit, display = tables.CALC_UNITS[calc_unit]
            reason = tables.NOT_EVALUATED_REASONS.get(calc_id, "{reason}").format(reason=item.reason)
            claims.append(unknown_claim(calc_id, label, unit, display, reason, kind="calculation"))
    for finding in policy_result.findings:
        for calc_id, label, calc_unit, reason in tables.FINDING_MISSING_CALCS.get(finding.rule, ()):
            if calc_id in existing or any(claim.id == calc_id for claim in claims):
                continue
            unit, display = tables.CALC_UNITS[calc_unit]
            claims.append(unknown_claim(calc_id, label, unit, display, reason, kind="calculation"))
    return claims


def screening_assumption_claim() -> Claim:
    """The clean screening assumption as an ASSUMPTION claim (no screening outcomes were given)."""
    return Claim(
        id=SCREENING_ASSUMPTION_CLAIM,
        label="Sanctions screening outcome (assumed)",
        value="clear",
        unit="TEXT",
        display="text",
        status="ASSUMPTION",
        provenance=Provenance(
            kind="assumption", ref="screening", source=UNSOURCED, details={"note": CLEAN_SCREENING_ASSUMPTION}
        ),
        inputs={"assumption": CLEAN_SCREENING_ASSUMPTION},
    )


def model_claims(application: AnyApplication, estimator: ModelEstimator | None) -> list[Claim]:
    """The line's model claims: UNKNOWN with the honesty reason, replaced by an estimator's output when given."""
    defaults = default_model_claims(application.line)
    if estimator is None:
        return defaults
    estimated: list[Claim] = []
    for estimate in estimator.estimate(application):
        estimated.extend(estimate_claims(estimate))
    estimated_ids = {claim.id for claim in estimated}
    return [claim for claim in defaults if claim.id not in estimated_ids] + estimated


def contradictions_of(contradictions: ContradictionReport | list[Contradiction] | None) -> list[Contradiction]:
    """A contradiction list from a report, a list or None."""
    if contradictions is None:
        return []
    if isinstance(contradictions, ContradictionReport):
        return list(contradictions.contradictions)
    return list(contradictions)


def build_ledger(
    state: CaseState,
    policy_result: PolicyResult | None = None,
    extraction: PacketExtraction | None = None,
    provenance: dict[str, list[dict[str, Any]]] | None = None,
    contradictions: ContradictionReport | list[Contradiction] | None = None,
    estimator: ModelEstimator | None = None,
) -> Ledger:
    """The evidence ledger of a lender case.

    policy_result defaults to evaluate(state); provenance to state.field_provenance; extraction is optional and only
    names the subject when the state has no case id (citations come from provenance and the contradictions).
    Claims, in order: application facts, the other unread application values (UNKNOWN "APP-<path>"), the location
    fields no packet document prints (packets only), missing required documents (DOC-*), document checks (CHK-*),
    policy calculations (CALC-*), calculations the policy could not run (UNKNOWN), model claims (MODEL-*,
    FEATURE-*) and the clean screening assumption when it is used. Assertions come from the policy result; a pass
    that rests on an UNKNOWN value is shown as not evaluated.
    """
    application = application_of(state)
    result = policy_result if policy_result is not None else evaluate(state)
    field_provenance = provenance if provenance is not None else state.field_provenance
    found = open_contradictions(contradictions_of(contradictions), state)
    conflicts = conflicts_by_claim(found)
    subject = state.case_id or (extraction.packet_id if extraction is not None else application.application_id)
    builder = Ledger.builder(subject_id=subject, line=application.line, data_label=state.data_label)
    claims = fact_claims(state, field_provenance, conflicts, application.line)
    claims.extend(unknown_path_claims(state, field_provenance, application.line, {claim.id for claim in claims}))
    claims.extend(
        undocumented_default_claims(state, field_provenance, application.line, {claim.id for claim in claims})
    )
    claims.extend(missing_document_claims(application))
    claims.extend(check_claims(state, conflicts, claims))
    claims.extend(calculation_claims(result, claims))
    claims.extend(not_evaluated_claims(result, application, {claim.id for claim in claims}))
    claims.extend(model_claims(application, estimator))
    if screening_assumed(state):
        claims.append(screening_assumption_claim())
    for claim in claims:
        builder.add_claim(claim)
    unknown_ids = [claim.id for claim in claims if claim.status == "UNKNOWN"]
    for assertion in policy_assertions(result, unknown_ids):
        builder.add_assertion(assertion)
    return builder.build()


__all__ = [
    "CLAIM_STATUSES",
    "ID_PATTERN",
    "IMPUTED_DEFAULT_METHOD",
    "KNOWN_UNITS",
    "MODEL_EXPECTED_LOSS_CLAIM",
    "MODEL_PD_CLAIM",
    "OPEN_STATUSES",
    "SCREENING_ASSUMPTION_CLAIM",
    "SYNTHETIC",
    "UNIT_PATTERN",
    "UNSOURCED",
    "Assertion",
    "Citation",
    "Claim",
    "ClaimStatus",
    "ClaimValue",
    "InputValue",
    "Ledger",
    "LedgerBuilder",
    "LedgerModel",
    "ModelEstimate",
    "ModelEstimator",
    "Provenance",
    "ProvenanceKind",
    "ValueRange",
    "assumption_claim",
    "build_ledger",
    "inputs_hash",
    "required_documents_for",
    "unknown_claim",
    "user_stated_claim",
]

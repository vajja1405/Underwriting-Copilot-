"""The case state the Evidence-to-Action Engine works on (Phase 4): one application plus what the documents showed.

A CaseState holds everything the reference policy (agent/policy.py evaluate_policy) needs besides the rules:
- application: the agent's CreditApplication / InsuranceApplication / StartupCreditApplication as plain JSON. It is
  validated with agent.applications.parse_application when it is used (application_of), so a resolution that
  produces an invalid application fails loudly instead of being evaluated;
- document_checks: Phase 2 document checks the policy acts on (agent.policy.DocumentCheckFlag; "confirmed" flags
  raise findings, see agent.policy.DOCUMENT_CHECK_RULES);
- injection_flags: instruction-like text, through the policy's EXISTING injection input. Document hits use the field
  name "doc:<doc_id>:p<page>";
- screening: sanctions screening outcomes. None means the CLEAN SCREENING ASSUMPTION: the KYC lists are not
  available to this run, so every screened party is treated as clear. It is an assumption, recorded as such in the
  evidence ledger (an ASSUMPTION claim), never a screening result;
- check_status, resolved_checks, field_provenance, unknown_paths: what the documents said (check id -> pass / fail /
  not_evaluated), which checks a resolution cleared, where every application value was read (application path ->
  [{doc_id, page, line}]) and which values the extraction could not read. The ledger (ledger.py) uses them;
- substituted_paths: application path -> the lever that replaced its value (apply). A substituted value is a
  hypothetical or reviewer-supplied value, not something a document printed: apply() removes its document
  provenance, and the ledger shows it as an ASSUMPTION, never as VERIFIED.

Functions:
- evaluate(state) -> PolicyResult: the reference policy with the same PolicyInputs construction every time, so every
  counterfactual is compared like for like. The risk model input is always empty with the honesty reason
  MODEL_NOT_EVALUATED_REASON (no Phase 1 model is trained on these commercial features).
- apply(state, substitutions) -> CaseState: a resolution or scenario. Application levers go through
  docintel.levers.apply_substitutions (the vocabulary of config/resolution_templates.yaml); "check:<id>" levers set
  a document check: "resolved" removes its flag, "confirmed" keeps it or adds it. "check:embedded_instructions"
  acts on the document injection flags. Every application path a lever changes loses its document provenance (and
  any provenance of a value that contains it) and is recorded in substituted_paths, so a ledger built on the new
  state never cites a page for a value that page did not print.
- from_packet(extraction, checks, contradictions, injection_hits) -> CaseState: a Phase 2 packet mapped with
  docintel.to_application. from_application(...) builds a state from an application dict alone.

Document text is untrusted data: flag details are built from ids and counts only, never from document text, because
finding details reach the memo prompt.
"""

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from uwcopilot.agent.applications import AnyApplication, parse_application, screened_parties
from uwcopilot.agent.injection import screen_application
from uwcopilot.agent.policy import (
    NAMES_DIFFER_BETWEEN_STATEMENTS,
    DocumentCheckFlag,
    InjectionFlag,
    PolicyInputs,
    PolicyResult,
    ScreeningOutcome,
    evaluate_policy,
)
from uwcopilot.docintel.check_models import CheckResult
from uwcopilot.docintel.contradictions import Contradiction, ContradictionReport
from uwcopilot.docintel.injection_scan import DocumentInjectionHit
from uwcopilot.docintel.levers import (
    ADD_DOCUMENT_LEVER,
    APPEND_PREFIX,
    CHECK_PREFIX,
    SCHEDULE_TIV_LEVER,
    VERIFIED_REVENUE_LEVER,
    apply_substitutions,
    check_overrides,
)
from uwcopilot.docintel.schemas import PacketExtraction
from uwcopilot.docintel.to_application import to_application

if TYPE_CHECKING:  # the pipeline imports the PDF and OCR stack; only its result type is needed here
    from uwcopilot.docintel.pipeline import PacketResult

MODEL_NOT_EVALUATED_REASON = (
    "not evaluated: no Phase 1 model is trained on features these SYNTHETIC commercial packets contain"
)
CLEAN_SCREENING_ASSUMPTION = (
    "clean screening assumption: the KYC sanctions lists are not available to this run, so every screened party is "
    "treated as clear"
)
DOC_FIELD_PREFIX = "doc:"
EMBEDDED_INSTRUCTIONS_CHECK = "embedded_instructions"
RESOLVED = "resolved"
CONFIRMED = "confirmed"
# The injection flag added when a review confirms embedded instructions but no document hit is on file.
CONFIRMED_INJECTION_FIELD = "doc:confirmed_by_review"
CONFIRMED_INJECTION_PATTERN = "confirmed_by_review"
CONFIRMED_DETAIL = "confirmed by the requested evidence."
# The application path each special lever changes (docintel/levers.py).
SPECIAL_LEVER_PATHS: dict[str, str] = {
    VERIFIED_REVENUE_LEVER: "bank_statements.months",
    SCHEDULE_TIV_LEVER: "locations",
    ADD_DOCUMENT_LEVER: "documents_provided",
}
STATEMENT_MONTHS_PATH = "bank_statements.months"
LOSS_RUN_YEAR_INDEX = re.compile(r"^loss_runs\.years\[(\d+)\]")
NAME_CONSISTENCY_CHECK = "name_consistency"
STATEMENT_NAME_FIELD = "statement.account_holder"


class IncompleteApplicationError(ValueError):
    """The state's application does not validate, so the reference policy cannot run on it.

    The message lists the failing field locations and error types only (never the values: they may be document
    text) and the paths the extraction could not read.
    """

    def __init__(self, unknown_paths: list[str], errors: list[str]) -> None:
        self.unknown_paths = list(unknown_paths)
        self.errors = list(errors)
        unknown = ", ".join(self.unknown_paths) or "none"
        super().__init__(
            f"the application does not validate ({len(self.errors)} error(s): {'; '.join(self.errors[:10])}); "
            f"values the extraction could not read: {unknown}"
        )


class CaseState(BaseModel):
    """One case as the engine sees it (see the module docstring). Immutable: apply() returns a new state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = ""
    data_label: str = "SYNTHETIC"
    application: dict[str, Any]
    document_checks: list[DocumentCheckFlag] = Field(default_factory=list)
    injection_flags: list[InjectionFlag] = Field(default_factory=list)
    screening: list[ScreeningOutcome] | None = None
    check_status: dict[str, str] = Field(default_factory=dict)
    resolved_checks: list[str] = Field(default_factory=list)
    field_provenance: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    unknown_paths: list[str] = Field(default_factory=list)
    substituted_paths: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------------------------------------------
# Evaluating a state
# --------------------------------------------------------------------------------------------------------------


def error_locations(error: ValidationError) -> list[str]:
    """'loc: type' for every validation error, without the input values."""
    return [".".join(str(part) for part in item["loc"]) + f": {item['type']}" for item in error.errors()]


def application_of(state: CaseState) -> AnyApplication:
    """The validated application (IncompleteApplicationError when it does not validate)."""
    try:
        return parse_application(state.application)
    except ValidationError as error:
        raise IncompleteApplicationError(state.unknown_paths, error_locations(error)) from error


def is_evaluable(state: CaseState) -> bool:
    """True when the application validates, so evaluate() can run."""
    try:
        application_of(state)
    except IncompleteApplicationError:
        return False
    return True


def screening_assumed(state: CaseState) -> bool:
    """True when no screening outcomes were given and the clean screening assumption is used."""
    return state.screening is None


def screening_outcomes(state: CaseState, application: AnyApplication) -> list[ScreeningOutcome]:
    """The given outcomes, or "clear" for every screened party under the clean screening assumption."""
    if state.screening is not None:
        return list(state.screening)
    return [ScreeningOutcome(party_id=party.party_id, status="clear") for party in screened_parties(application)]


def policy_inputs(state: CaseState, application: AnyApplication | None = None) -> PolicyInputs:
    """The PolicyInputs for this state, built the same way for every baseline, scenario and resolution."""
    validated = application if application is not None else application_of(state)
    return PolicyInputs(
        screening=screening_outcomes(state, validated),
        risk=None,
        risk_unavailable_reason=MODEL_NOT_EVALUATED_REASON,
        injection_flags=list(state.injection_flags),
        document_checks=list(state.document_checks),
    )


def evaluate(state: CaseState) -> PolicyResult:
    """The reference policy's result for this state."""
    application = application_of(state)
    return evaluate_policy(application, policy_inputs(state, application))


# --------------------------------------------------------------------------------------------------------------
# Applying a resolution or scenario
# --------------------------------------------------------------------------------------------------------------


def is_document_flag(flag: InjectionFlag) -> bool:
    """True for an injection flag found in a document (field "doc:...")."""
    return flag.field.startswith(DOC_FIELD_PREFIX)


def resolve_check(
    check_id: str, flags: list[DocumentCheckFlag], injection: list[InjectionFlag]
) -> tuple[list[DocumentCheckFlag], list[InjectionFlag]]:
    """A "resolved" check lever: drop the check's flags (document injection flags for embedded instructions)."""
    if check_id == EMBEDDED_INSTRUCTIONS_CHECK:
        return flags, [flag for flag in injection if not is_document_flag(flag)]
    return [flag for flag in flags if flag.check_id != check_id], injection


def confirm_check(
    check_id: str, flags: list[DocumentCheckFlag], injection: list[InjectionFlag]
) -> tuple[list[DocumentCheckFlag], list[InjectionFlag]]:
    """A "confirmed" check lever: keep the check's flags as confirmed, or add one when there was none."""
    if check_id == EMBEDDED_INSTRUCTIONS_CHECK:
        if any(is_document_flag(flag) for flag in injection):
            return flags, injection
        added = InjectionFlag(field=CONFIRMED_INJECTION_FIELD, patterns=[CONFIRMED_INJECTION_PATTERN])
        return flags, [*injection, added]
    kept = []
    found = False
    for flag in flags:
        if flag.check_id == check_id:
            found = True
            kept.append(flag.model_copy(update={"status": CONFIRMED}))
        else:
            kept.append(flag)
    if not found:
        kept.append(DocumentCheckFlag(check_id=check_id, status=CONFIRMED, detail=CONFIRMED_DETAIL))
    return kept, injection


def substituted_path(lever: str, application: dict[str, Any]) -> str:
    """The application path a lever changes, written the way field_provenance writes it.

    Special levers name their list ("@verified_annual_revenue" -> "bank_statements.months"); an append names the
    list it appends to, and any month lever names the whole month list (field_provenance keys months by position,
    and an inserted month shifts the positions); a loss-run year index becomes its policy year start, because
    field_provenance keys loss-run years by date ("loss_runs.years[2023-03-01]").
    """
    if lever in SPECIAL_LEVER_PATHS:
        return SPECIAL_LEVER_PATHS[lever]
    path = lever.removeprefix(APPEND_PREFIX)
    if path.startswith(STATEMENT_MONTHS_PATH):
        return STATEMENT_MONTHS_PATH
    match = LOSS_RUN_YEAR_INDEX.match(path)
    if match is not None:
        years = application.get("loss_runs", {}).get("years", [])
        index = int(match.group(1))
        if index < len(years) and isinstance(years[index], dict) and years[index].get("policy_year_start"):
            path = f"loss_runs.years[{years[index]['policy_year_start']}]" + path[match.end() :]
    return path


def paths_overlap(first: str, second: str) -> bool:
    """True when one path is the other, or contains it ("owners" and "owners[0].name")."""
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    return longer.startswith(shorter + ".") or longer.startswith(shorter + "[")


def without_substituted(
    provenance: dict[str, list[dict[str, Any]]], unknown: list[str], changed: list[str]
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Provenance and unknown paths with every path that overlaps a changed path removed."""
    kept = {key: value for key, value in provenance.items() if not any(paths_overlap(key, c) for c in changed)}
    still_unknown = [path for path in unknown if not any(paths_overlap(path, c) for c in changed)]
    return kept, still_unknown


def apply(state: CaseState, substitutions: dict[str, Any]) -> CaseState:
    """A new state with the substitutions applied (see the module docstring; the input state is not changed).

    Raises docintel.levers.LeverError for a lever that cannot be applied and ValueError for a check lever whose
    value is not "resolved" or "confirmed".
    """
    changed = {
        substituted_path(lever, state.application): lever
        for lever in substitutions
        if not lever.startswith(CHECK_PREFIX)
    }
    application = apply_substitutions(state.application, substitutions)
    flags = list(state.document_checks)
    injection = list(state.injection_flags)
    resolved = list(state.resolved_checks)
    for check_id, outcome in check_overrides(substitutions).items():
        if outcome == RESOLVED:
            flags, injection = resolve_check(check_id, flags, injection)
            if check_id not in resolved:
                resolved.append(check_id)
        elif outcome == CONFIRMED:
            flags, injection = confirm_check(check_id, flags, injection)
            resolved = [item for item in resolved if item != check_id]
        else:
            raise ValueError(f"check lever check:{check_id} must be 'resolved' or 'confirmed', not {outcome!r}")
    unknown = [path for path in state.unknown_paths if path not in substitutions]
    provenance, unknown = without_substituted(state.field_provenance, unknown, list(changed))
    return CaseState.model_validate(
        {
            **state.model_dump(),
            "application": application,
            "document_checks": [flag.model_dump() for flag in flags],
            "injection_flags": [flag.model_dump() for flag in injection],
            "resolved_checks": resolved,
            "field_provenance": provenance,
            "unknown_paths": unknown,
            "substituted_paths": {**state.substituted_paths, **changed},
        }
    )


# --------------------------------------------------------------------------------------------------------------
# Building a state from a packet or an application
# --------------------------------------------------------------------------------------------------------------


def doc_field(doc_id: str | None, page: int | None) -> str:
    """The injection flag field for a document location: "doc:D03:p7" ("doc:D03" without a page)."""
    name = f"{DOC_FIELD_PREFIX}{doc_id or 'unknown'}"
    return name if page is None else f"{name}:p{page}"


def flag_detail(result: CheckResult, contradictions: list[Contradiction]) -> str:
    """A flag detail from ids and counts only (no document text)."""
    ids = [item.contradiction_id for item in contradictions]
    doc_ids: list[str] = []
    for item in contradictions:
        doc_ids.extend(doc_id for doc_id in item.doc_ids if doc_id not in doc_ids)
    parts = [f"{len(result.issues)} issue(s)" if result.issues else "the check failed"]
    if ids:
        parts.append(f"contradiction(s) {', '.join(ids)}")
    if doc_ids:
        parts.append(f"document(s) {', '.join(doc_ids)}")
    return "; ".join(parts) + "."


def check_indicators(result: CheckResult) -> list[str]:
    """Named indicators of a failing check (agent.policy.DOCUMENT_INDICATOR_RULES).

    NAMES_DIFFER_BETWEEN_STATEMENTS: a name_consistency issue with a bank statement's account holder on BOTH sides,
    so the name differs between two statements of the account (NBC-9.1, on top of NBC-6.2).
    """
    if result.check_id != NAME_CONSISTENCY_CHECK:
        return []
    for issue in result.issues:
        sides = (issue.side_a, issue.side_b)
        if all(any(item.field == STATEMENT_NAME_FIELD for item in side) for side in sides):
            return [NAMES_DIFFER_BETWEEN_STATEMENTS]
    return []


def document_check_flags(checks: list[CheckResult], contradictions: list[Contradiction]) -> list[DocumentCheckFlag]:
    """A "confirmed" flag for every failing check (open until a resolution says otherwise), in check order, with
    its named indicators (check_indicators).

    Embedded instructions are left out: they enter the policy as injection flags (document_injection_flags).
    """
    flags = []
    for result in checks:
        if result.status != "fail" or result.check_id == EMBEDDED_INSTRUCTIONS_CHECK:
            continue
        related = [item for item in contradictions if item.check_id == result.check_id]
        flags.append(
            DocumentCheckFlag(
                check_id=result.check_id,
                status=CONFIRMED,
                detail=flag_detail(result, related),
                evidence_ids=[item.contradiction_id for item in related],
                indicators=check_indicators(result),
            )
        )
    return flags


def merge_injection_flags(pairs: list[tuple[str, list[str]]]) -> list[InjectionFlag]:
    """One InjectionFlag per field with the union of its pattern names, sorted by field."""
    by_field: dict[str, set[str]] = {}
    for field, patterns in pairs:
        by_field.setdefault(field, set()).update(patterns)
    return [InjectionFlag(field=field, patterns=sorted(by_field[field])) for field in sorted(by_field)]


def document_injection_flags(
    checks: list[CheckResult], injection_hits: list[DocumentInjectionHit] | None = None
) -> list[InjectionFlag]:
    """Injection flags from the embedded_instructions check (page and field hits merged), else from the hits."""
    pairs: list[tuple[str, list[str]]] = []
    for result in checks:
        if result.check_id != EMBEDDED_INSTRUCTIONS_CHECK:
            continue
        for issue in result.issues:
            location = issue.side_a[0] if issue.side_a else None
            field = doc_field(location.doc_id, location.page) if location else doc_field(None, None)
            pairs.append((field, [str(name) for name in issue.data.get("patterns", [])]))
        return merge_injection_flags(pairs)
    for hit in injection_hits or []:
        pairs.append((doc_field(hit.doc_id, hit.page), list(hit.patterns)))
    return merge_injection_flags(pairs)


def contradiction_list(contradictions: ContradictionReport | list[Contradiction]) -> list[Contradiction]:
    """The contradictions of a report, or the list itself."""
    if isinstance(contradictions, ContradictionReport):
        return list(contradictions.contradictions)
    return list(contradictions)


def from_packet(
    extraction: PacketExtraction,
    checks: list[CheckResult],
    contradictions: ContradictionReport | list[Contradiction],
    injection_hits: list[DocumentInjectionHit] | None = None,
    case_id: str | None = None,
) -> CaseState:
    """The state of a Phase 2 packet: the mapped application, failing checks as flags, document injection flags.

    The application's own free-text fields (remarks, names) are already covered by the embedded_instructions
    check's field scan, so the agent's application-level screen is not run again (it would count them twice).
    """
    application, provenance, unknowns = to_application(extraction, checks)
    found = contradiction_list(contradictions)
    return CaseState(
        case_id=case_id or extraction.packet_id,
        application=application,
        document_checks=document_check_flags(checks, found),
        injection_flags=document_injection_flags(checks, injection_hits),
        check_status={result.check_id: result.status for result in checks},
        field_provenance=provenance,
        unknown_paths=unknowns,
    )


def from_packet_result(result: "PacketResult") -> CaseState:
    """from_packet over a docintel.pipeline.PacketResult."""
    return from_packet(result.extraction, result.checks, result.contradictions, result.injection_hits)


def from_application(
    application: dict[str, Any],
    document_checks: list[DocumentCheckFlag] | None = None,
    injection_flags: list[InjectionFlag] | None = None,
    screening: list[ScreeningOutcome] | None = None,
    case_id: str | None = None,
) -> CaseState:
    """A state from an application dict alone (no packet). A "ground_truth" key is dropped: the engine never sees it.

    injection_flags None runs the agent's application screen (agent.injection.screen_application) on the
    application's free-text fields, as the agent's policy evaluation does; pass [] to skip it. The application
    must validate in that case.
    """
    inputs = {key: value for key, value in application.items() if key != "ground_truth"}
    if injection_flags is None:
        hits = screen_application(parse_application(inputs))
        injection_flags = [InjectionFlag(field=hit.field, patterns=hit.patterns) for hit in hits]
    return CaseState(
        case_id=case_id or str(inputs.get("application_id", "")),
        application=inputs,
        document_checks=list(document_checks or []),
        injection_flags=list(injection_flags),
        screening=screening,
    )

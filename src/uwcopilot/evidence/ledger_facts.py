"""Application-fact and document-check claims of the evidence ledger (Phase 4; ledger.py builds the whole ledger).

What every application value becomes (fact_claim, unknown_path_claims, undocumented_default_claims):
- read from a packet and undisputed: VERIFIED, with its document citations (doc id, page, line). A document claim
  is VERIFIED only with at least one citation: a packet value whose citation docintel.to_application did not
  record is UNKNOWN with the reason UNCITED_REASON (the value read is kept in provenance.details, never in value),
  because a value that cannot be traced to a page cannot be shown as verified;
- in an open contradiction: CONFLICTING, linked to the contradiction ids;
- not read by the extraction (state.unknown_paths): UNKNOWN; when docintel.to_application had to write a default
  the agent's model needed, the claim is marked imputed and the default is kept in provenance.details and inputs,
  never in value, so a memo prints "not available";
- a location field no packet document prints (wind tier, flood zone, wildfire hazard, vacancy, wiring, systems
  year): UNKNOWN for a packet, imputed with the model's default;
- substituted by a resolution or scenario lever (state.substituted_paths): ASSUMPTION, sourced to the lever, with
  no document citation;
- typed by an applicant (no document provenance at all): USER_STATED.
Every fact claim's inputs hold the path and the value, so inputs_sha256 pins the value.

Contradictions close when a lever resolves their check or replaces a value they compare (is_closed); the check's
claim is then an ASSUMPTION (lever_check_claim). Missing checklist documents are UNKNOWN "DOC-<code>" claims.

A document check's own claim ("CHK-<check_id>", check_claims) is the outcome of a deterministic rule over values
that carry their own citations, not a value printed on a page, so its provenance kind is "calculation" (ref = the
check id), like a CALC-* claim; the contradictions it found link to the cited values. Like a calculation, a check
outcome is only as good as the values it compares (tables.CHECK_INPUT_PATTERNS): one over an UNKNOWN value (a
statement month marked unreliable, say) is UNKNOWN with depends_on_unknown, its pass or fail kept in
provenance.details only; one over a CONFLICTING value lists it in depends_on_conflicting.
"""

import re
from collections.abc import Sequence
from typing import Any

from uwcopilot.agent.applications import AnyApplication, CreditApplication, InsuranceApplication
from uwcopilot.agent.policy_credit import required_documents as credit_required_documents
from uwcopilot.agent.policy_insurance import required_documents as insurance_required_documents
from uwcopilot.agent.policy_startup import required_startup_documents
from uwcopilot.docintel.check_models import CHECK_TITLES
from uwcopilot.docintel.contradictions import Contradiction
from uwcopilot.docintel.levers import LeverError, parse_path, walk
from uwcopilot.evidence import ledger_tables as tables
from uwcopilot.evidence.ledger_models import (
    Citation,
    Claim,
    InputValue,
    Provenance,
    ValueRange,
    is_number,
    unknown_claim,
)
from uwcopilot.evidence.state import CaseState, paths_overlap

ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.\-]")

IMPUTED_DEFAULT_METHOD = "default written by docintel.to_application (the application model has no room for None)"


MODEL_DEFAULT_METHOD = "the agent application model's default (no packet document prints this field)"


UNCITED_REASON = (
    "read from the packet, but docintel.to_application recorded no document citation (doc, page, line) for it, so "
    "it cannot be shown as verified"
)


PATH_INDEX = re.compile(r"\[([^\]]*)\]")


UNREAD_REASON = "the extraction could not read {paths}"


UNRELIABLE_REASON = (
    "the extraction read {paths} but marked it unreliable (for example statement rows that do not add up to the "
    "printed total, or a loss-run summary that differs from its claim lines)"
)


UNDOCUMENTED_REASON = "no packet document prints this field; the reference policy used the application model's default"


SUBSTITUTION_SOURCE = "substituted by a resolution or scenario lever (a hypothetical or reviewer-supplied value)"


RESOLVED_CHECK_SOURCE = "resolution lever check:{check_id} = resolved (a hypothetical or reviewer-supplied outcome)"


SUPERSEDED_CHECK_SOURCE = "a resolution or scenario lever replaced a value this check compares ({paths})"


CHECK_UNKNOWN_REASON = "the check compared values the documents did not provide or that were marked unreliable: {ids}"


GAP_REASON = "the check found a gap in the documents (a missing document, page or period)"


def citations_for(path: str, provenance: dict[str, list[dict[str, Any]]]) -> tuple[Citation, ...]:
    """Citations of an application path, and of every path below it ("bank_statements.months" covers months[i])."""
    found: list[Citation] = []
    for key, sources in provenance.items():
        if key == path or key.startswith(path + ".") or key.startswith(path + "["):
            for source in sources:
                citation = Citation(doc_id=source.get("doc_id"), page=source.get("page"), line=source.get("line"))
                if citation not in found:
                    found.append(citation)
    return tuple(found)


def path_value(application: dict[str, Any], path: str) -> Any:
    """The value at an application path (None when the path does not exist)."""
    try:
        return walk(application, parse_path(path), path)
    except LeverError:
        return None


def path_claim_id(path: str) -> str:
    """'APP-' + the application path in the slot grammar: "owners[0].credit_score" -> "APP-owners.0.credit_score"."""
    dotted = PATH_INDEX.sub(lambda match: "." + match.group(1), path)
    return "APP-" + ID_UNSAFE.sub("_", dotted)


def is_scalar(value: Any) -> bool:
    """True for a value a claim input can hold (text, number or bool; not None, a dict or a list)."""
    return isinstance(value, str | int | float | bool)


def superseding_paths(check_id: str, state: CaseState) -> list[str]:
    """The substituted paths that supersede a contradiction of this check (CONTRADICTION_APPLICATION_PATHS)."""
    compared = tables.CONTRADICTION_APPLICATION_PATHS.get(check_id, ())
    return [path for path in state.substituted_paths if any(paths_overlap(path, other) for other in compared)]


def is_closed(item: Contradiction, state: CaseState) -> bool:
    """True when a lever resolved the contradiction's check or replaced a value it compares."""
    return item.check_id in state.resolved_checks or bool(superseding_paths(item.check_id, state))


def open_contradictions(contradictions: list[Contradiction], state: CaseState) -> list[Contradiction]:
    """Contradictions no resolution has closed (is_closed)."""
    return [item for item in contradictions if not is_closed(item, state)]


def conflicts_by_claim(contradictions: list[Contradiction]) -> dict[str, list[str]]:
    """Claim id -> contradiction ids that put it in doubt (the check's own claim plus CONFLICT_CLAIMS_BY_CHECK)."""
    result: dict[str, list[str]] = {}
    for item in contradictions:
        claim_ids = [f"CHK-{item.check_id}", *tables.CONFLICT_CLAIMS_BY_CHECK.get(item.check_id, ())]
        for claim_id in claim_ids:
            result.setdefault(claim_id, [])
            if item.contradiction_id not in result[claim_id]:
                result[claim_id].append(item.contradiction_id)
    return result


def unread_claim(
    claim_id: str,
    label: str,
    path: str,
    value: Any,
    reason: str,
    citations: tuple[Citation, ...] = (),
    unit: str = "NONE",
    display: str = "text",
    method: str = IMPUTED_DEFAULT_METHOD,
) -> Claim:
    """UNKNOWN: a value no document provided. When the application holds a default the policy used (a scalar the
    agent's model needed), the claim is marked imputed and the default is kept in provenance.details and inputs,
    never in value, so a memo prints "not available"."""
    imputed = is_scalar(value)
    details: dict[str, InputValue] = {"imputed_value": value} if imputed else {}
    return Claim(
        id=claim_id,
        label=label,
        value=None,
        unit=unit,
        display=display,
        status="UNKNOWN",
        provenance=Provenance(kind="document", ref=path, citations=citations, details=details),
        inputs={"application_path": path, "imputed_value": value if imputed else None},
        reason=reason,
        imputed=imputed,
        imputation_method=method if imputed else None,
    )


def unread_fact_claim(spec: tables.FactSpec, value: Any, unread: list[str], citations: tuple[Citation, ...]) -> Claim:
    """UNKNOWN: the extraction could not read the value (a default the application model needed is imputed)."""
    reason = UNREAD_REASON.format(paths=", ".join(unread))
    return unread_claim(spec.claim_id, spec.label, spec.path, value, reason, citations, spec.unit, spec.display)


def uncited_fact_claim(spec: tables.FactSpec, value: Any) -> Claim:
    """UNKNOWN: a packet value with no document citation (UNCITED_REASON). Not imputed: the value was read, so it
    is kept in provenance.details as "uncited_value" for a reviewer, never in value, and a memo prints "not
    available"."""
    kept = value if is_scalar(value) else None
    return Claim(
        id=spec.claim_id,
        label=spec.label,
        value=None,
        unit=spec.unit,
        display=spec.display,
        status="UNKNOWN",
        provenance=Provenance(kind="document", ref=spec.path, details={"uncited_value": kept}),
        inputs={"application_path": spec.path, "value": kept},
        reason=UNCITED_REASON,
    )


def point_range(value: Any) -> ValueRange | None:
    """A numeric value's range when the value is the whole assumption (low = high = value)."""
    return ValueRange(low=float(value), high=float(value)) if is_number(value) else None


def substitution_lever(spec: tables.FactSpec, state: CaseState, is_count: bool) -> str | None:
    """The lever that substituted a fact's value (None when no lever did).

    A fact is substituted when a lever replaced its path or a value that contains it. A list count changes only
    when a lever appended to the list ("+path") or replaced the whole list.
    """
    for path, lever in state.substituted_paths.items():
        if is_count:
            if path == spec.path and (lever.startswith("+") or lever == spec.path):
                return lever
        elif spec.path == path or spec.path.startswith(path + ".") or spec.path.startswith(path + "["):
            return lever
    return None


def substituted_fact_claim(spec: tables.FactSpec, value: Any, lever: str) -> Claim:
    """ASSUMPTION: a value a resolution or scenario lever put in the application (never VERIFIED, no citation)."""
    return Claim(
        id=spec.claim_id,
        label=spec.label,
        value=value,
        unit=spec.unit,
        display=spec.display,
        status="ASSUMPTION",
        provenance=Provenance(kind="assumption", ref=spec.path, source=f"{SUBSTITUTION_SOURCE}: {lever}"),
        inputs={"application_path": spec.path, "value": value if is_scalar(value) else None, "lever": lever},
        value_range=point_range(value),
    )


def fact_claim(
    spec: tables.FactSpec,
    value: Any,
    state: CaseState,
    provenance: dict[str, list[dict[str, Any]]],
    conflicts: dict[str, list[str]],
    is_count: bool = False,
) -> Claim | None:
    """One application fact (None for a value that is legitimately absent, e.g. GL limits on a property quote).

    - UNKNOWN when the extraction could not read it (state.unknown_paths), imputed when a default was written;
    - ASSUMPTION when a resolution or scenario lever substituted it (state.substituted_paths);
    - CONFLICTING when an open contradiction involves it;
    - VERIFIED when it was read from the packet AND has at least one citation;
    - UNKNOWN (uncited_fact_claim) when it was read from the packet but the mapping recorded no citation for it;
    - USER_STATED when the state has no document provenance at all (an application typed by the applicant).
    The inputs hold the path and the value, so inputs_sha256 changes when the value does.
    """
    citations = citations_for(spec.path, provenance)
    unread = [path for path in state.unknown_paths if path == spec.path]
    if unread:
        return unread_fact_claim(spec, value, unread, citations)
    if value is None:
        return None
    lever = substitution_lever(spec, state, is_count)
    if lever is not None:
        return substituted_fact_claim(spec, value, lever)
    if spec.claim_id in conflicts:
        status = "CONFLICTING"
    elif citations:
        status = "VERIFIED"
    elif provenance:
        return uncited_fact_claim(spec, value)
    else:
        status = "USER_STATED"
    kind = "document" if provenance else "application"
    return Claim(
        id=spec.claim_id,
        label=spec.label,
        value=value,
        unit=spec.unit,
        display=spec.display,
        status=status,
        provenance=Provenance(kind=kind, ref=spec.path, citations=citations),
        inputs={"application_path": spec.path, "value": value if is_scalar(value) else None},
        conflict_ids=tuple(conflicts.get(spec.claim_id, ())),
    )


def fact_claims(
    state: CaseState, provenance: dict[str, list[dict[str, Any]]], conflicts: dict[str, list[str]], line: str
) -> list[Claim]:
    """Every application fact of the line, then the list counts (statement months, loss-run years, locations)."""
    claims = []
    for spec in tables.FACTS_BY_LINE.get(line, ()):
        claim = fact_claim(spec, path_value(state.application, spec.path), state, provenance, conflicts)
        if claim is not None:
            claims.append(claim)
    for spec in tables.COUNT_FACTS_BY_LINE.get(line, ()):
        items = path_value(state.application, spec.path)
        count = len(items) if isinstance(items, list) else None
        claim = fact_claim(spec, count, state, provenance, conflicts, is_count=True)
        if claim is not None:
            claims.append(claim)
    return claims


def fact_paths(line: str) -> set[str]:
    """The application paths the fact tables already turn into claims."""
    specs = (*tables.FACTS_BY_LINE.get(line, ()), *tables.COUNT_FACTS_BY_LINE.get(line, ()))
    return {spec.path for spec in specs}


def unknown_path_claims(
    state: CaseState, provenance: dict[str, list[dict[str, Any]]], line: str, existing: set[str]
) -> list[Claim]:
    """One UNKNOWN claim "APP-<path>" for every unread path the fact tables do not cover (owners' credit scores and
    bankruptcy flags, control-person cells, statement months and loss-run years marked unreliable, a missing form).

    A scalar the application holds there is the default docintel.to_application wrote (imputed); a month or year
    that is a dict was read, but its figures are in doubt (not imputed; UNRELIABLE_REASON); None is simply absent.
    """
    covered = fact_paths(line)
    claims: list[Claim] = []
    for path in state.unknown_paths:
        claim_id = path_claim_id(path)
        if path in covered or claim_id in existing or any(claim.id == claim_id for claim in claims):
            continue
        value = path_value(state.application, path)
        template = UNRELIABLE_REASON if isinstance(value, dict | list) else UNREAD_REASON
        reason = template.format(paths=path)
        claims.append(
            unread_claim(claim_id, f"Application value {path}", path, value, reason, citations_for(path, provenance))
        )
    return claims


def undocumented_default_claims(
    state: CaseState, provenance: dict[str, list[dict[str, Any]]], line: str, existing: set[str]
) -> list[Claim]:
    """For a packet (a state with document provenance), one UNKNOWN claim per location field no packet document
    prints (tables.UNDOCUMENTED_LOCATION_FIELDS): the policy used the agent model's default, which is imputed.

    An application typed by the applicant (no document provenance) states these fields itself, so it gets none.
    """
    if line != "insurance" or not provenance:
        return []
    claims = []
    for index, location in enumerate(state.application.get("locations") or []):
        for name in tables.UNDOCUMENTED_LOCATION_FIELDS:
            path = f"locations[{index}].{name}"
            claim_id = path_claim_id(path)
            documented = citations_for(path, provenance) or path in state.unknown_paths
            if documented or claim_id in existing or any(paths_overlap(path, p) for p in state.substituted_paths):
                continue
            label = f"Location {index + 1}: {name.replace('_', ' ')}"
            value = location.get(name) if isinstance(location, dict) else None
            claims.append(unread_claim(claim_id, label, path, value, UNDOCUMENTED_REASON, method=MODEL_DEFAULT_METHOD))
    return claims


def required_documents_for(application: AnyApplication) -> list[str]:
    """The checklist the reference policy applies to this application."""
    if isinstance(application, CreditApplication):
        return credit_required_documents(application)
    if isinstance(application, InsuranceApplication):
        return insurance_required_documents(application)
    return required_startup_documents(application)


def missing_document_claims(application: AnyApplication) -> list[Claim]:
    """One UNKNOWN claim "DOC-<code>" per required document that is not in the file."""
    provided = set(application.documents_provided)
    return [
        unknown_claim(
            f"DOC-{code}",
            f"Required document {code}",
            "TEXT",
            "text",
            "required by the reference policy's document checklist but not in the file",
            kind="policy",
            ref=code,
        )
        for code in required_documents_for(application)
        if code not in provided
    ]


def lever_check_claim(check_id: str, state: CaseState) -> Claim | None:
    """ASSUMPTION: a check a resolution lever resolved, or whose compared value a lever replaced (None otherwise)."""
    if check_id in state.resolved_checks:
        value, source = "resolved", RESOLVED_CHECK_SOURCE.format(check_id=check_id)
    else:
        paths = superseding_paths(check_id, state)
        if not paths:
            return None
        value, source = "superseded by a substituted value", SUPERSEDED_CHECK_SOURCE.format(paths=", ".join(paths))
    return Claim(
        id=f"CHK-{check_id}",
        label=CHECK_TITLES.get(check_id, check_id),
        value=value,
        unit="TEXT",
        display="text",
        status="ASSUMPTION",
        provenance=Provenance(kind="assumption", ref=check_id, source=source),
        inputs={"check_id": check_id, "status": value},
    )


def check_inputs(check_id: str, earlier: Sequence[Claim], status: str) -> tuple[str, ...]:
    """Ids of the earlier claims with this status that the check compares (tables.CHECK_INPUT_PATTERNS)."""
    patterns = [re.compile(pattern) for pattern in tables.CHECK_INPUT_PATTERNS.get(check_id, ())]
    return tuple(
        claim.id
        for claim in earlier
        if claim.status == status and any(pattern.search(claim.id) for pattern in patterns)
    )


def outcome_claim(check_id: str, status_text: str, earlier: Sequence[Claim]) -> Claim:
    """A check outcome ("pass" or "fail") that no contradiction, lever or gap explains.

    VERIFIED, unless the check compared an UNKNOWN value: then UNKNOWN (CHECK_UNKNOWN_REASON) with depends_on_unknown,
    the outcome kept in provenance.details["outcome_with_unread_inputs"] and never in value. CONFLICTING inputs are
    listed in depends_on_conflicting (the outcome itself stays VERIFIED, as for a calculation).
    """
    unknown = check_inputs(check_id, earlier, "UNKNOWN")
    conflicting = check_inputs(check_id, earlier, "CONFLICTING")
    details: dict[str, InputValue] = {"outcome_with_unread_inputs": status_text} if unknown else {}
    return Claim(
        id=f"CHK-{check_id}",
        label=CHECK_TITLES.get(check_id, check_id),
        value=None if unknown else status_text,
        unit="TEXT",
        display="text",
        status="UNKNOWN" if unknown else "VERIFIED",
        provenance=Provenance(kind="calculation", ref=check_id, details=details),
        inputs={"check_id": check_id, "status": status_text},
        reason=CHECK_UNKNOWN_REASON.format(ids=", ".join(unknown)) if unknown else None,
        depends_on_unknown=unknown,
        depends_on_conflicting=conflicting,
    )


def check_claims(state: CaseState, conflicts: dict[str, list[str]], earlier: Sequence[Claim] = ()) -> list[Claim]:
    """One claim "CHK-<check_id>" per document check that ran (state.check_status, plus any check with a conflict).

    The claim is the check's outcome. CONFLICTING with an open contradiction; ASSUMPTION when a resolution lever
    resolved it or replaced a value it compares (lever_check_claim: a hypothetical or reviewer-supplied outcome,
    not a document); UNKNOWN when a gap check failed (a missing document, page, period or out-of-date loss run:
    GAP_CHECKS); otherwise the value "pass" or "fail" (a deterministic result the policy also evaluates from the
    application), VERIFIED unless it compared a value that is UNKNOWN among the earlier claims (outcome_claim).
    Provenance kind "calculation": the claim is a rule's outcome, not a printed value (module docstring). A check
    that could not run (not_evaluated) creates no claim: the values it compares appear once or not at all, and a
    missing document or value already shows as a DOC-* or UNKNOWN claim.

    earlier: the claims built before the checks (application facts and unread values); build_ledger passes them.
    """
    check_ids = list(state.check_status)
    for claim_id in conflicts:
        check_id = claim_id.removeprefix("CHK-")
        if claim_id.startswith("CHK-") and check_id not in check_ids:
            check_ids.append(check_id)
    claims = []
    for check_id in check_ids:
        claim_id = f"CHK-{check_id}"
        status_text = state.check_status.get(check_id, "fail")
        lever_claim = None if claim_id in conflicts else lever_check_claim(check_id, state)
        if lever_claim is not None:
            claims.append(lever_claim)
            continue
        if status_text == "not_evaluated" and claim_id not in conflicts:
            continue
        if claim_id not in conflicts and not (status_text == "fail" and check_id in tables.GAP_CHECKS):
            claims.append(outcome_claim(check_id, status_text, earlier))
            continue
        conflicting = claim_id in conflicts
        claims.append(
            Claim(
                id=claim_id,
                label=CHECK_TITLES.get(check_id, check_id),
                value=status_text if conflicting else None,
                unit="TEXT",
                display="text",
                status="CONFLICTING" if conflicting else "UNKNOWN",
                provenance=Provenance(kind="calculation", ref=check_id),
                inputs={"check_id": check_id, "status": status_text},
                reason=None if conflicting else GAP_REASON,
                conflict_ids=tuple(conflicts.get(claim_id, ())),
            )
        )
    return claims

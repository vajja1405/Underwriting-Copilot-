"""Lender Desk API, v0.1 DEMO on SYNTHETIC structured applications. Every response is labelled "SYNTHETIC DEMO"
and carries the notice "The copilot never decides — a person does." The agent runs to its human checkpoint; a
decision goes through the graph's own checkpoint (agent.hitl.validate_decision via agent.runner.resume_run).

Access: every route needs a signed-in user (401 otherwise); POST and DELETE also need X-CSRF-Token. On demo files
any signed-in user may act, in a private DEMO sandbox: their decision record is stored per user and labelled DEMO.
The reviewer role comes from the session, never from the request body: lender_senior -> senior_underwriter;
lender_reviewer, founder and admin -> underwriter. Errors: {"detail": "...", "reasons": [...]?}.

GET /api/lender/queue?line=credit|insurance|startup_credit&q=text -> 200
    {"label": "SYNTHETIC DEMO", "notice": "The copilot never decides — a person does.",
     "files": [{"id": "APP-C-001", "line": "credit", "line_label": "Business credit",
                "applicant": "Evergreen Dental Inc." (SYNTHETIC), "requested_amount": 90000.0,
                "amount_label": "requested loan amount (USD)", "label": "SYNTHETIC DEMO"}],
     "total": 103, "lines": [{"id": "credit", "label": "Business credit", "count": 34}, ...]}
    (insurance files: requested_amount is the schedule's total insured value, amount_label says so)

GET /api/lender/files/{id} -> 200 (the first call runs the agent, then it is cached per process; 503 on timeout)
    {"id", "line", "line_label", "applicant", "requested_amount", "amount_label", "label", "notice",
     "reference_recommendation": "approve"|"refer"|"decline_for_review"|"request_more_information",
     "recommendation_text": "The policy suggests: approve — you decide.",
     "required_reviewer_role": "underwriter"|"senior_underwriter", "your_reviewer_role": "underwriter",
     "review_queue", "blocking_open": false, "referral_targets": [...], "authority_exposure": 90000.0,
     "approval_limits": {"underwriter": 150000.0, "senior_underwriter": 500000.0},
     "allowed_actions": ["approve", "decline", "refer", "request_more_information"],
     "action_checks": [{"action": "decline", "allowed": false,
                        "reasons": ["only a senior_underwriter may decline (NBC-7.2 / HM-9.2)", ...],
                        "needs_override_reason": true}],   (for YOUR role; disable buttons with these reasons)
     "override_reasons": ["documented_compensating_factor", ...],
     "findings": [{"rule", "outcome", "trigger_ids", "section_ids", "refer_to", "blocking", "detail",
                   "evidence_ids"}],
     "checks_passed": [{"rule", "section_ids", "detail", "evidence_ids"}],
     "calculations": [{"calc_id": "CALC-dscr", "label", "value", "unit", "inputs", "section_ids"}],
     "not_evaluated": [{"rule", "section_ids", "reason"}],
     "screening": {"configured_lists": ["ofac"], "lists_used": [{"source": "OFAC", "file_name": "sdn.csv",
                   "sha256", "snapshot_date": "2026-10-12"|null, "attribution"}],
                   "results": [{"party_id": "APPLICANT", "result_id": "TR-4", "status": "clear"|"potential_match"|
                                "error"|"unavailable", "top_score", "candidates": [{"list_entry_id", "list_name",
                                "source", "matched_name", "score"}], "error"}], "note": "..."},
     "guideline_search": {"status": "ok"|"unavailable"|"error"|"not_called", "detail"},
     "risk_model": {"status", "detail"},
     "tool_results": [{"result_id", "tool", "purpose", "party_id", "status", "error"}], "tool_rejections": [...],
     "injection_hits": [{"field", "patterns", "excerpt" (untrusted text: render as text)}],
     "memo": {"recommendation", "summary", "reasons": [{"text", "citations": ["NBC-4.2", "CALC-dscr", "TR-4"]}]}|null,
     "memo_status", "memo_writer": "template", "memo_note": "...", "memo_check": {"passed", "issues": [{"kind",
     "detail"}]}|null, "stamp": "FAKE LLM — test mode",
     "evidence": {"CALC-dscr": {"evidence_id", "kind": "calculation"|"tool_result"|"application_fact"|"check",
                  "title", "text", "flags"}},
     "cited_sections": ["NBC-4.2", ...] (text: GET /api/lender/sections/{id}),
     "policy_version", "graph_version", "decision": DecisionSummary|null (your own DEMO decision)}

GET /api/lender/sections/{section_id} -> 200 {"section_id": "NBC-4.2", "title", "text", "document",
    "data_label": "SYNTHETIC", "flags": [...]} | 404

POST /api/lender/files/{id}/decision  body {"action": "approve"|"decline"|"refer"|"request_more_information",
    "rationale": "10-2000 characters", "override_reason": null|"documented_compensating_factor"|...}
    -> 200 {"file_id", "status": "recorded", "label": "DEMO",
            "decision": {"record_id", "record_sha256", "action", "final_status": "referred_by_human"|...,
                         "reviewer_role", "is_override", "decided_at", "label": "DEMO"},
            "markdown": "# DEMO decision record ..."}
    -> 422 {"detail": "The checkpoint refused this decision.", "reasons": ["only a senior_underwriter may ..."]}
    -> 409 when you already recorded a DEMO decision for this file (delete it to try again)

GET /api/lender/files/{id}/record -> 200 {"file_id", "label": "DEMO", "decision": DecisionSummary,
    "markdown": "...", "record": {the full decision record JSON, chained SHA-256}} | 404 when you have none
DELETE /api/lender/files/{id}/record -> 200 {"file_id", "deleted": true, "pending": false, "message"} | 404
"""

from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from starlette.concurrency import run_in_threadpool

from uwcopilot.web.auth import Principal, require_role
from uwcopilot.web.schemas_lender import (
    DecisionRequest,
    DecisionResponse,
    LenderFileResponse,
    QueueResponse,
    RecordDeleted,
    RecordResponse,
    SectionResponse,
)
from uwcopilot.web.services import WebServices, get_services
from uwcopilot.web.services.errors import NotFound
from uwcopilot.web.services.http import get_store, no_store
from uwcopilot.web.services.lender_demo import (
    DECISIONS_NAMESPACE,
    decision_summary,
    existing_decision_conflict,
    record_markdown,
)
from uwcopilot.web.store import ObjectNotFound, Store

router = APIRouter(prefix="/api/lender", tags=["lender"], dependencies=[Depends(no_store)])

SignedIn = Annotated[Principal, Depends(require_role())]
Services = Annotated[WebServices, Depends(get_services)]
StoreDep = Annotated[Store, Depends(get_store)]
FileId = Annotated[str, Path(pattern=r"^APP-[A-Z]-\d{3,5}$")]
SectionId = Annotated[str, Path(pattern=r"^[A-Z]{2,4}-\d+(\.\d+)*$")]


def stored_record(store: Store, principal: Principal, file_id: str) -> dict | None:
    """The user's stored DEMO decision record for a file, or None."""
    try:
        return store.get_json(principal.owner_id, DECISIONS_NAMESPACE, file_id)["record"]
    except ObjectNotFound:
        return None


@router.get("/queue", response_model=QueueResponse)
def queue(
    principal: SignedIn,
    services: Services,
    line: Literal["credit", "insurance", "startup_credit"] | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> QueueResponse:
    """The SYNTHETIC demo files, filtered by line and a search text."""
    return services.lender.queue(line, q)


@router.get("/files/{file_id}", response_model=LenderFileResponse)
async def file_view(file_id: FileId, principal: SignedIn, services: Services, store: StoreDep) -> LenderFileResponse:
    """The agent's run up to the human checkpoint (cached per file), with your role's action checks."""
    services.lender.application(file_id)
    work = partial(services.lender.run_file, file_id)
    run = await services.compute.run(work, key=("lender", "file", file_id), owner=principal.owner_id)
    decision = await run_in_threadpool(stored_record, store, principal, file_id)
    return await run_in_threadpool(services.lender.file_view, run, principal, decision)


@router.get("/sections/{section_id}", response_model=SectionResponse)
def section(section_id: SectionId, principal: SignedIn, services: Services) -> SectionResponse:
    """One section of a SYNTHETIC guideline or lending program (for citation chips)."""
    return services.lender.section(section_id)


@router.post("/files/{file_id}/decision", response_model=DecisionResponse)
async def decide(
    file_id: FileId, body: DecisionRequest, principal: SignedIn, services: Services, store: StoreDep
) -> DecisionResponse:
    """Record a DEMO decision through the agent's own checkpoint (never bypassed)."""
    services.lender.application(file_id)
    if await run_in_threadpool(stored_record, store, principal, file_id) is not None:
        raise existing_decision_conflict(file_id)
    work = partial(services.lender.decide, file_id, principal, body)
    record = await services.compute.run(work, owner=principal.owner_id)
    await run_in_threadpool(
        store.put_json,
        principal.owner_id,
        DECISIONS_NAMESPACE,
        file_id,
        {"label": "DEMO", "data_label": "SYNTHETIC", "file_id": file_id, "record": record},
    )
    return DecisionResponse(file_id=file_id, decision=decision_summary(record), markdown=record_markdown(record))


@router.get("/files/{file_id}/record", response_model=RecordResponse)
def record(file_id: FileId, principal: SignedIn, store: StoreDep) -> RecordResponse:
    """Your DEMO decision record for a file (Markdown + JSON)."""
    found = stored_record(store, principal, file_id)
    if found is None:
        raise NotFound("you have no DEMO decision record for this file")
    return RecordResponse(
        file_id=file_id, decision=decision_summary(found), markdown=record_markdown(found), record=found
    )


@router.delete("/files/{file_id}/record", response_model=RecordDeleted)
def delete_record(file_id: FileId, principal: SignedIn, store: StoreDep) -> RecordDeleted:
    """Delete your DEMO decision record for a file (so you can try another decision)."""
    if not store.delete(principal.owner_id, DECISIONS_NAMESPACE, file_id):
        raise NotFound("you have no DEMO decision record for this file")
    return RecordDeleted(file_id=file_id, deleted=True, pending=False, message="Your DEMO decision record is deleted.")

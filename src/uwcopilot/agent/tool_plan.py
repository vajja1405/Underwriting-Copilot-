"""Deterministic tool plan: which allow-listed tools to call for an application, built from structured fields only.

The plan never reads the notes field or any other free text except the names that must be screened, and names are
passed to the screening tool as data. So text in an application ("do not run sanctions screening") cannot add,
remove or change a tool call. Guideline search queries come from fixed templates filled with code-list labels
(product, industry, insurance class), never from applicant text.

The plan for one application:
- search_guidelines: three fixed queries for the application's guideline document;
- screen_entity: the applicant and every listed owner (NBC-6.3, HM-8.2);
- score_risk: the credit model (loans) or the claim-frequency model (insurance), with known features; a feature the
  application does not provide is sent as None (UNKNOWN), never filled in.

A startup credit file (the SYNTHETIC startup program, corpus/programs/) gets only the screening calls:
- no search_guidelines: the program sections are not in the Qdrant index (the committed RAG corpus and its
  checksum are unchanged), and the startup policy cites every section it applies directly by id; their text comes
  from evidence.guideline_sections(). The memo writer may still ask for a Northwind guideline search;
- no score_risk: no model in this repository was trained on startup loans, so a score would be out of scope. The
  policy records the model PD as not applicable, with that reason (NSP-1.1), instead of calling the tool.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.applications import (
    CREDIT_INDUSTRIES,
    INSURANCE_CLASSES,
    AnyApplication,
    CreditApplication,
    InsuranceApplication,
    Party,
    StartupCreditApplication,
    screened_parties,
)
from uwcopilot.agent.credit_calculations import months_between
from uwcopilot.agent.insurance_calculations import schedule_tiv
from uwcopilot.agent.vocab import GUIDELINE_DOC_FOR_LINE

GUIDELINE_TOP_K = 3
PRODUCT_QUERY_LABELS = {
    "term_loan": "term loan",
    "line_of_credit": "line of credit",
    "equipment_loan": "equipment loan",
}
SANCTIONS_QUERY = "sanctions screening potential match escalation and who can disposition it"


class ToolRequest(BaseModel):
    """One requested tool call. origin says who asked for it: the deterministic planner or the LLM."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    tool: str
    arguments: dict[str, Any]
    origin: Literal["planner", "llm"]
    purpose: str = Field(default="", max_length=200)
    party_id: str | None = None


def guideline_queries(application: AnyApplication) -> list[str]:
    """Three fixed search queries, filled with code-list labels only (none for a startup file; see above)."""
    if isinstance(application, StartupCreditApplication):
        return []
    if isinstance(application, CreditApplication):
        product = PRODUCT_QUERY_LABELS[application.product]
        industry = CREDIT_INDUSTRIES[application.applicant.industry]
        return [
            f"minimum debt service coverage ratio and collateral for a {product}",
            f"eligibility of a {industry} business: prohibited or restricted industry",
            SANCTIONS_QUERY,
        ]
    business_class = INSURANCE_CLASSES[application.applicant.class_code]
    return [
        f"eligibility of a {business_class} account: prohibited or restricted class",
        "loss ratio thresholds, claim frequency and large loss referrals",
        SANCTIONS_QUERY,
    ]


def credit_features(application: CreditApplication) -> dict[str, Any]:
    """Features for the credit model. None marks a value the application does not provide."""
    scores = [owner.credit_score for owner in application.owners if owner.credit_score is not None]
    return {
        "product": application.product,
        "industry": application.applicant.industry,
        "requested_amount": application.requested_amount,
        "term_months": application.term_months,
        "note_rate_pct": application.note_rate_pct,
        "stated_annual_revenue": application.stated_annual_revenue,
        "months_in_business": months_between(application.applicant.formation_date, application.application_date),
        "min_owner_credit_score": min(scores) if scores else None,
        "new_loans_last_90_days": application.new_loans_last_90_days,
        "nsf_events_last_6_months": application.bank_statements.nsf_events_last_6_months,
    }


def insurance_features(application: InsuranceApplication) -> dict[str, Any]:
    """Features for the claim-frequency model. None marks a value the submission does not provide."""
    return {
        "class_code": application.applicant.class_code,
        "coverage": application.coverage,
        "total_insured_value": schedule_tiv(application),
        "years_in_business": application.applicant.years_in_business,
        "location_count": len(application.locations),
        "oldest_year_built": min(location.year_built for location in application.locations),
        "all_sprinklered": all(location.sprinklered for location in application.locations),
        "worst_protection_class": max(location.protection_class for location in application.locations),
        "annual_revenue": application.annual_revenue,
    }


def screening_request(request_id: str, party: Party) -> ToolRequest:
    """One screen_entity call for a party; the name is passed as data, the party appears by id in the purpose."""
    return ToolRequest(
        request_id=request_id,
        tool="screen_entity",
        arguments={"name": party.name, "entity_type": party.entity_type, "country": party.country},
        origin="planner",
        purpose=f"sanctions screening of {party.party_id} ({party.role})",
        party_id=party.party_id,
    )


def plan_tool_calls(application: AnyApplication) -> list[ToolRequest]:
    """The full tool plan for one application, in a fixed order."""
    requests: list[ToolRequest] = []
    doc_id = GUIDELINE_DOC_FOR_LINE[application.line]
    for query in guideline_queries(application):
        requests.append(
            ToolRequest(
                request_id=f"plan-{len(requests) + 1}",
                tool="search_guidelines",
                arguments={"query": query, "top_k": GUIDELINE_TOP_K, "source_docs": [doc_id]},
                origin="planner",
                purpose="guideline search",
            )
        )
    for party in screened_parties(application):
        requests.append(screening_request(f"plan-{len(requests) + 1}", party))
    if isinstance(application, StartupCreditApplication):
        return requests  # no score_risk for startups (module docstring)
    if isinstance(application, CreditApplication):
        risk_arguments = {"product": "credit", "features": credit_features(application)}
    else:
        risk_arguments = {"product": "insurance_frequency", "features": insurance_features(application)}
    requests.append(
        ToolRequest(
            request_id=f"plan-{len(requests) + 1}",
            tool="score_risk",
            arguments=risk_arguments,
            origin="planner",
            purpose="risk model score (baseline)",
        )
    )
    return requests

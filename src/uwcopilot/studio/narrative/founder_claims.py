"""Claims for what the founder typed (USER_STATED) and for the documents that back or contradict it.

One claim per plan figure the founder entered (an empty optional field has no claim: the simulator used a profile or
metro range for it, which assumption_claims.py records as an ASSUMPTION):
- USER_STATED, kind user_input, with the plan field in the inputs (and the value, so the inputs hash pins it);
- VERIFIED, kind document, with the document's citation (doc id, page, line), when an uploaded document prints the
  same value (dollars within $1.00, the NSP-3.2 tolerance; other figures exactly);
- CONFLICTING when a document prints a different value: the founder's claim and a DOC.* claim for the document
  value, each linking the other. The simulation used the founder's figure; the conflict is shown, never resolved.
A figure the founder marked as verified (FounderFacts.verified_fields) WITHOUT a document in the ledger stays
USER_STATED, with a note saying so: a VERIFIED status always needs the document's locator.
Owner facts (credit score, experience) are USER_STATED, or UNKNOWN with the reason when they were not entered.
Other debt (seller notes, other loans) gives one claim per amount and per monthly payment; the founder's name for it
stays in the claim's details (free text never becomes a code field or a prompt instruction).
"""

from collections.abc import Callable, Sequence
from typing import Any

from uwcopilot.evidence.ledger_models import (
    Citation,
    Claim,
    InputValue,
    Provenance,
    unknown_claim,
)
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.models import FounderDocumentValue
from uwcopilot.studio.path_to_approval.models import PlanCase

USD_TOLERANCE = 1.00
VERIFIED_WITHOUT_DOCUMENT_NOTE = (
    "marked as verified by the founder, but no document value is in this ledger, so it stays USER_STATED"
)
CREDIT_SCORE_MISSING = "not entered yet; NSP-2.2 of the SYNTHETIC startup program needs every principal's score"
EXPERIENCE_MISSING = "not entered yet; NSP-2.1 of the SYNTHETIC startup program needs the owners' relevant experience"


# (plan field, claim id, label, unit, display)
FIELD_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("plan.owner_equity", ids.FOUNDER_EQUITY, "Owner cash equity", "USD", "usd0"),
    ("loan.amount", ids.FOUNDER_LOAN, "Loan amount requested", "USD", "usd0"),
    ("loan.term_months", ids.FOUNDER_TERM, "Loan term", "MONTHS", "months"),
    ("loan.annual_rate_pct", ids.FOUNDER_RATE, "Note rate quoted to the founder", "PERCENT", "pct1"),
    ("loan.rate_spread_pct", ids.FOUNDER_SPREAD, "Spread over the prime rate typed by the founder", "PERCENT", "pct1"),
    ("plan.capex", ids.FOUNDER_CAPEX, "Build-out and equipment cost", "USD", "usd0"),
    ("plan.price", "FOUNDER.price", "Price per demand unit", "USD", "usd2"),
    ("plan.monthly_rent", "FOUNDER.monthly_rent", "Monthly rent quote", "USD", "usd0"),
    ("plan.paid_hours_per_month", "FOUNDER.paid_hours_per_month", "Paid staff hours per month", "HOURS", "int"),
    ("plan.hourly_wage", "FOUNDER.hourly_wage", "Planned hourly wage", "USD", "usd2"),
    ("plan.capacity", "FOUNDER.capacity", "Most demand the business can serve", "DEMAND", "int"),
    ("plan.days_open_per_week", "FOUNDER.days_open_per_week", "Days open per week", "DAYS_PER_WEEK", "int"),
    ("plan.owner_draw_per_month", ids.FOUNDER_OWNER_DRAW, "Owner draw per month", "USD", "usd0"),
)
# Simulator input names (feasibility distributions) that a founder field replaces.
FIELD_FOR_INPUT: dict[str, str] = {
    "price": "plan.price",
    "monthly_rent": "plan.monthly_rent",
    "paid_hours_per_month": "plan.paid_hours_per_month",
    "hourly_wage": "plan.hourly_wage",
    "capacity": "plan.capacity",
    "days_open_per_week": "plan.days_open_per_week",
}


def claim_id_for_field(field: str) -> str:
    """The FOUNDER.* claim id of a plan field."""
    for spec_field, claim_id, *_ in FIELD_SPECS:
        if spec_field == field:
            return claim_id
    raise KeyError(field)


def plan_value(case: PlanCase, field: str) -> Any:
    """The value of 'plan.x' or 'loan.x' in the founder's plan (None when the founder left it empty)."""
    group, name = field.split(".", 1)
    source = case.plan if group == "plan" else case.loan
    return getattr(source, name)


def same_value(field: str, typed: float, printed: float) -> bool:
    """True when a document prints the founder's figure: dollars within USD_TOLERANCE, other figures exactly."""
    if field in (
        "plan.owner_equity",
        "plan.capex",
        "plan.price",
        "plan.monthly_rent",
        "plan.hourly_wage",
        "loan.amount",
    ):
        return abs(float(typed) - float(printed)) <= USD_TOLERANCE
    return abs(float(typed) - float(printed)) < 1e-9


def citation_of(document: FounderDocumentValue) -> Citation:
    """Where the document prints the value."""
    return Citation(doc_id=document.doc_id, page=document.page, line=document.line)


def typed_claim(field: str, claim_id: str, label: str, unit: str, display: str, value: Any, verified: bool) -> Claim:
    """USER_STATED: a figure the founder typed (with a note when they marked it verified without a document)."""
    details: dict[str, InputValue] = {"note": VERIFIED_WITHOUT_DOCUMENT_NOTE} if verified else {}
    return Claim(
        id=claim_id,
        label=label,
        value=value,
        unit=unit,
        display=display,
        status="USER_STATED",
        provenance=Provenance(kind="user_input", ref=field, details=details),
        inputs={"field": field, "value": value},
    )


def documented_claims(
    field: str, claim_id: str, label: str, unit: str, display: str, value: Any, documents: list[FounderDocumentValue]
) -> list[Claim]:
    """VERIFIED (every document agrees) or CONFLICTING founder + DOC claims (some document disagrees)."""
    disagreeing = [item for item in documents if not same_value(field, value, item.value)]
    if not disagreeing:
        citations = tuple(citation_of(item) for item in documents)
        types = ", ".join(sorted({item.document_type for item in documents}))
        return [
            Claim(
                id=claim_id,
                label=label,
                value=value,
                unit=unit,
                display=display,
                status="VERIFIED",
                provenance=Provenance(
                    kind="document", ref=field, citations=citations, details={"document_types": types}
                ),
                inputs={"field": field, "value": value, "doc_ids": ", ".join(item.doc_id for item in documents)},
            )
        ]
    doc_ids = [ids.safe_id("DOC", field, index) for index in range(1, len(disagreeing) + 1)]
    founder = Claim(
        id=claim_id,
        label=label,
        value=value,
        unit=unit,
        display=display,
        status="CONFLICTING",
        provenance=Provenance(kind="user_input", ref=field),
        inputs={"field": field, "value": value},
        conflict_ids=tuple(doc_ids),
    )
    claims = [founder]
    for doc_id, item in zip(doc_ids, disagreeing, strict=True):
        claims.append(
            Claim(
                id=doc_id,
                label=f"{label} as printed in {item.doc_id}",
                value=item.value,
                unit=unit,
                display=display,
                status="CONFLICTING",
                provenance=Provenance(
                    kind="document",
                    ref=field,
                    citations=(citation_of(item),),
                    details={"document_type": item.document_type},
                ),
                inputs={"field": field, "value": item.value, "doc_id": item.doc_id},
                conflict_ids=(claim_id,),
            )
        )
    return claims


def unused_document_claim(field: str, label: str, unit: str, display: str, item: FounderDocumentValue) -> Claim:
    """A document value for a field the founder left empty: VERIFIED, and noted as not used by the simulation."""
    return Claim(
        id=ids.safe_id("DOC", field, item.doc_id),
        label=f"{label} as printed in {item.doc_id} (not used by the simulation: the plan field is empty)",
        value=item.value,
        unit=unit,
        display=display,
        status="VERIFIED",
        provenance=Provenance(
            kind="document", ref=field, citations=(citation_of(item),), details={"document_type": item.document_type}
        ),
        inputs={"field": field, "value": item.value, "doc_id": item.doc_id},
    )


def plan_figure_claims(case: PlanCase, documents: Sequence[FounderDocumentValue]) -> list[Claim]:
    """One claim per plan figure the founder typed (module docstring), plus unused document values."""
    claims: list[Claim] = []
    verified = set(case.facts.verified_fields)
    for field, claim_id, label, unit, display in FIELD_SPECS:
        value = plan_value(case, field)
        if field == "plan.owner_draw_per_month" and not value:
            continue
        matching = [item for item in documents if item.field == field]
        if value is None:
            claims.extend(unused_document_claim(field, label, unit, display, item) for item in matching)
            continue
        if matching:
            claims.extend(documented_claims(field, claim_id, label, unit, display, value, matching))
        else:
            claims.append(typed_claim(field, claim_id, label, unit, display, value, field in verified))
    return claims


def text_claim(claim_id: str, label: str, value: str, field: str) -> Claim:
    """A text the founder chose (business type, metro, rate type, opening date): USER_STATED, printed as text."""
    return Claim(
        id=claim_id,
        label=label,
        value=value,
        unit="TEXT",
        display="text",
        status="USER_STATED",
        provenance=Provenance(kind="user_input", ref=field),
        inputs={"field": field, "value": value},
    )


def plan_text_claims(case: PlanCase, demand_unit: str) -> list[Claim]:
    """What the plan is about: business type, metro, opening date, rate type, and the profile's demand unit."""
    return [
        text_claim(ids.PLAN_BUSINESS_TYPE, "Business type", case.profile_id, "profile_id"),
        text_claim(ids.PLAN_METRO, "Metro", case.metro_id, "metro_id"),
        text_claim(
            ids.PLAN_OPENING_DATE, "Planned opening date", case.plan.opening_date.isoformat(), "plan.opening_date"
        ),
        text_claim(ids.PLAN_RATE_TYPE, "Rate type", case.loan.rate_type, "loan.rate_type"),
        Claim(
            id=ids.PLAN_DEMAND_UNIT,
            label="Demand unit of the business type",
            value=demand_unit,
            unit="TEXT",
            display="text",
            status="ASSUMPTION",
            provenance=Provenance(kind="assumption", ref=f"profile {case.profile_id} demand_unit", source="unsourced"),
            inputs={"profile_id": case.profile_id, "value": demand_unit},
        ),
    ]


def fact_claim(
    claim_id: str, label: str, unit: str, display: str, value: int | None, missing: str, field: str
) -> Claim:
    """An owner fact: USER_STATED when entered, UNKNOWN with the reason when not."""
    if value is None:
        return unknown_claim(claim_id, label, unit, display, missing, kind="user_input", ref=field)
    return typed_claim(field, claim_id, label, unit, display, value, False)


def owner_fact_claims(case: PlanCase) -> list[Claim]:
    """Credit score and experience of the owners (USER_STATED or UNKNOWN)."""
    facts = case.facts
    return [
        fact_claim(
            ids.FOUNDER_CREDIT_SCORE,
            "Lowest principal credit score",
            "SCORE",
            "int",
            facts.credit_score,
            CREDIT_SCORE_MISSING,
            "facts.credit_score",
        ),
        fact_claim(
            ids.FOUNDER_EXPERIENCE,
            "Most relevant owner experience",
            "MONTHS",
            "months",
            facts.experience_months,
            EXPERIENCE_MISSING,
            "facts.experience_months",
        ),
    ]


def other_debt_claims(case: PlanCase) -> list[Claim]:
    """Amount and monthly payment of every other loan or seller note: USER_STATED (no document is attached to other
    debt in this version, so the status the founder gave is kept in the details, never promoted to VERIFIED)."""
    claims: list[Claim] = []
    for index, item in enumerate(case.facts.other_debt, start=1):
        field = f"facts.other_debt.{index}"
        for name, label, value in (
            ("amount", f"Other debt {index}: amount", item.amount),
            ("monthly_payment", f"Other debt {index}: monthly payment", item.monthly_payment),
        ):
            claim = typed_claim(f"{field}.{name}", ids.other_debt_id(index, name), label, "USD", "usd0", value, False)
            details = {"name_given_by_founder": item.name, "status_given": item.status}
            claims.append(
                claim.model_copy(update={"provenance": claim.provenance.model_copy(update={"details": details})})
            )
    return claims


def founder_claims(case: PlanCase, demand_unit: str, documents: Sequence[FounderDocumentValue] = ()) -> list[Claim]:
    """Every founder claim: plan texts, typed figures (with documents), owner facts and other debt."""
    return [
        *plan_text_claims(case, demand_unit),
        *plan_figure_claims(case, documents),
        *owner_fact_claims(case),
        *other_debt_claims(case),
    ]


def lookup(claims: Sequence[Claim]) -> Callable[[str], Claim | None]:
    """A function claim_id -> claim (or None) over a list of claims."""
    by_id = {claim.id: claim for claim in claims}
    return by_id.get

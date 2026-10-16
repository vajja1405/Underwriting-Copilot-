"""Inputs and outputs of the founder ledger and narrative that are not ledger claims themselves.

- FounderDocumentValue: one value read from a document the founder uploaded (a lease letter of intent's rent, a
  contractor quote, the balance on a bank statement), with its page. It turns the founder's typed figure into a
  VERIFIED claim when the two agree, or into two CONFLICTING claims when they do not (founder_claims.py).
- NarrativeResult: what render_narrative returns to the web layer and the package (S6): the rendered text, the
  slot-only draft it came from, the round-trip audit, whether the template fallback was used and why, and which
  prompt (id and SHA-256) produced it.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL

# Plan figures a document can back (the founder's typed value is compared with the document's).
DocumentField = Literal[
    "plan.owner_equity",
    "plan.capex",
    "plan.price",
    "plan.monthly_rent",
    "plan.paid_hours_per_month",
    "plan.hourly_wage",
    "plan.capacity",
    "plan.days_open_per_week",
    "loan.amount",
    "loan.term_months",
    "loan.annual_rate_pct",
]
NarrativeProvider = Literal["template", "fake", "ollama", "hf_inference"]


class Frozen(BaseModel):
    """Unknown keys are an error; values cannot change after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FounderDocumentValue(Frozen):
    """A value read from an uploaded document, with where it is printed.

    document_type uses the startup program's document codes (agent.applications StartupDocument), for example
    "equity_bank_statements", "lease_or_letter_of_intent", "contractor_or_equipment_quotes".
    """

    field: DocumentField
    value: float
    doc_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
    document_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    page: int | None = Field(default=None, ge=1)
    line: int | None = Field(default=None, ge=1)


class AttemptRecord(Frozen):
    """One LLM attempt: what the validator found (codes and spans) or why the call failed."""

    attempt: int = Field(ge=1)
    violations: list[str] = Field(default_factory=list, description="'CODE: span' for each grammar violation")
    error: str | None = Field(default=None, description="Why the call itself failed (no draft to validate)")
    latency_s: float | None = None
    cached: bool = False


class NarrativeResult(Frozen):
    """A rendered founder narrative. Every number in `body` was typed by the renderer from the ledger."""

    text: str = Field(description="body + the simulation label and the Studio disclaimer: what the founder reads")
    body: str = Field(description="The rendered narrative with footnote markers [^cN]")
    footnotes: list[str] = Field(description="One line per claim used: label, provenance, inputs hash")
    draft: str = Field(description="The slot-only draft that was rendered (template or LLM)")
    audit: list[str] = Field(description="Numbers in the body that trace to no ledger claim (round_trip_audit)")
    claims_used: list[str]
    assertions_used: list[str]
    provider: str = Field(description="template, fake, ollama or hf_inference: who was asked to write the draft")
    model: str | None = None
    fallback_used: bool = Field(description="True when an LLM was asked but the template wrote the final draft")
    fallback_reason: str | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    prompt_id: str = Field(description="The registered prompt (or template) that produced the final draft")
    prompt_sha256: str
    llm_prompt_id: str | None = Field(default=None, description="The prompt sent to the LLM, when one was asked")
    llm_prompt_sha256: str | None = None
    data_label: str
    label: str = SIMULATION_LABEL
    disclaimer: str = DISCLAIMER

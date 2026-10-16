"""FounderProject: everything build_package needs about one founder plan (the S6 input).

It holds the S2-S5 results of ONE plan (the PlanCase, its candidate sites, SiteIQ's score of the chosen site, the
simulation and the path search) plus what a lender's application needs that the simulator does not: the business
name, the legal entity, the owners, the documents in the file, the kinds of other debt and any pledged collateral.

Rules checked here, so a package can never describe two different plans or owners:
- the metro is a registry metro id (config/studio/metros) that the startup application accepts;
- the simulation, the SiteIQ score and the path search describe this plan (the founder ledger checks it again);
- the owners agree with the plan's FACTS: the lowest principal credit score equals facts.credit_score and the most
  relevant experience equals facts.experience_months (the ledger reads the facts, the application reads the owners);
- ownership adds up to at most 100% (the program's own check then reads the exact total);
- other_debt_kinds has one code per other debt of the plan (a code is never inferred from the founder's free text);
- documents_provided lists checklist codes only; documents with a value (FounderDocumentValue) are listed too.
Free text (business name, owner names, site label) is data: it is printed and screened, never used as a code.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwcopilot.agent.applications import (
    STARTUP_DOCUMENTS,
    STARTUP_METRO_IDS,
    BusinessStructure,
    CollateralItem,
    StartupDocument,
    StartupPurpose,
)
from uwcopilot.agent.policy_credit import PRINCIPAL_OWNERSHIP_PCT
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.metros import list_metros
from uwcopilot.studio.narrative.models import FounderDocumentValue
from uwcopilot.studio.path_to_approval.models import Candidate, PathResult, PlanCase
from uwcopilot.studio.siteiq.models import SiteScore

OtherDebtKind = Literal["seller_note", "other_loan"]
OWNERSHIP_TOLERANCE = 0.05


class Frozen(BaseModel):
    """Unknown keys are an error; values cannot change after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PackageOwner(Frozen):
    """One owner as the lender's application needs it. name is free text (printed and screened, never a code)."""

    owner_id: str = Field(pattern=r"^O\d{1,2}$")
    name: str = Field(min_length=1, max_length=200)
    ownership_pct: float = Field(gt=0, le=100)
    is_control_person: bool = False
    credit_score: int | None = Field(default=None, ge=300, le=850)
    experience_months: int | None = Field(default=None, ge=0)
    personal_guarantee: bool = False
    bankruptcy_within_7_years: bool = False
    tax_lien_unresolved: bool = False
    country: str | None = Field(default="US", max_length=64)

    @property
    def is_principal(self) -> bool:
        """A principal: 20% or more, or the control person."""
        return self.ownership_pct >= PRINCIPAL_OWNERSHIP_PCT or self.is_control_person


class FounderProject(Frozen):
    """One founder plan with everything the package, the application and the handoff need (module docstring)."""

    project_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.\-]{0,63}$")
    prepared_on: date
    business_name: str = Field(min_length=1, max_length=200)
    entity_type: BusinessStructure
    state: str = Field(pattern=r"^[A-Z]{2}$", description="State of organization (or where it will be formed)")
    formation_date: date | None = None
    loan_purpose: StartupPurpose = "mixed_project_costs"
    case: PlanCase
    candidates: list[Candidate] = Field(default_factory=list)
    site_score: SiteScore | None = None
    feasibility: FeasibilityResult
    path_result: PathResult | None = None
    owners: list[PackageOwner] = Field(min_length=1)
    documents: list[FounderDocumentValue] = Field(default_factory=list)
    documents_provided: list[StartupDocument] = Field(default_factory=list)
    other_debt_kinds: list[OtherDebtKind] = Field(default_factory=list)
    collateral: list[CollateralItem] = Field(default_factory=list)
    data_label: str = "SYNTHETIC"
    notes: list[str] = Field(default_factory=list, description="Notes of the code that built the project")

    @model_validator(mode="after")
    def consistent(self) -> "FounderProject":
        """The checks of the module docstring."""
        metro = self.case.metro_id
        if metro not in list_metros() or metro not in STARTUP_METRO_IDS:
            raise ValueError(f"metro {metro!r} is not a registry metro id the startup application accepts")
        if self.feasibility.metro_id != metro or self.feasibility.profile_id != self.case.profile_id:
            raise ValueError("the simulation is for another business type or metro")
        if self.site_score is not None and (self.site_score.metro_id, self.site_score.profile_id) != (
            metro,
            self.case.profile_id,
        ):
            raise ValueError("the SiteIQ score is for another business type or metro")
        if any(candidate.metro_id != metro for candidate in self.candidates):
            raise ValueError("every candidate site must be in the plan's metro")
        self.check_owners()
        if len(self.other_debt_kinds) not in (0, len(self.case.facts.other_debt)):
            raise ValueError("other_debt_kinds needs one code per other debt of the plan (or none: other_loan)")
        unknown = sorted({item.document_type for item in self.documents} - set(STARTUP_DOCUMENTS))
        if unknown:
            raise ValueError(f"unknown document types {unknown}; use the startup checklist codes")
        return self

    def check_owners(self) -> None:
        """Unique ids, at most 100% ownership, and owners that agree with the plan's facts."""
        ids = [owner.owner_id for owner in self.owners]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate owner ids {ids}")
        if sum(owner.ownership_pct for owner in self.owners) > 100.0 + OWNERSHIP_TOLERANCE:
            raise ValueError("ownership adds up to more than 100%")
        principals = [owner for owner in self.owners if owner.is_principal]
        scores = [owner.credit_score for owner in principals if owner.credit_score is not None]
        experience = [owner.experience_months for owner in principals if owner.experience_months is not None]
        facts = self.case.facts
        if facts.credit_score is not None and (not scores or min(scores) != facts.credit_score):
            raise ValueError("the lowest principal credit score must equal the plan's facts.credit_score")
        if facts.credit_score is None and scores:
            raise ValueError("owners have credit scores but the plan's facts.credit_score is empty")
        if facts.experience_months is not None and (not experience or max(experience) != facts.experience_months):
            raise ValueError("the most relevant principal experience must equal the plan's facts.experience_months")
        if facts.experience_months is None and experience:
            raise ValueError("owners have experience but the plan's facts.experience_months is empty")

    def all_documents_provided(self) -> list[str]:
        """Checklist codes of every document in the file: the listed ones and those behind document values."""
        codes = set(self.documents_provided) | {item.document_type for item in self.documents}
        return [code for code in STARTUP_DOCUMENTS if code in codes]

    def debt_kinds(self) -> list[str]:
        """One kind code per other debt (other_loan when the founder gave none)."""
        if self.other_debt_kinds:
            return list(self.other_debt_kinds)
        return ["other_loan"] * len(self.case.facts.other_debt)

"""The evidence registry: every item a memo may cite, by id.

Id families:
- guideline sections, e.g. "NBC-4.2" or "HM-8.2" (text from corpus/guidelines, or from a retrieved hit), and lending
  program sections, e.g. "NSP-4.1" (text from corpus/programs; programs are not in the RAG index, so they are only
  cited directly by the policy, never retrieved);
- calculations from the reference policy, e.g. "CALC-dscr";
- tool results, e.g. "TR-4" (one per executed tool call);
- application facts, e.g. "APP-requested_amount" (structured fields only, written without names);
- internal checks, e.g. "CHK-injection_screen".

The same registry is used to build the memo prompt and, afterwards, by the claim checker: a memo may only cite ids
that are in it, and every number in a reason must appear in one of the items that reason cites.

Free text from the applicant (notes) is not evidence and cannot be cited. Names are left out of application facts
(parties appear by id: APPLICANT, O1, O2, ...), so the LLM sees less personal data.
"""

from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.applications import (
    AnyApplication,
    CollateralItem,
    CreditApplication,
    InsuranceApplication,
    Location,
    Owner,
    StartupCreditApplication,
)
from uwcopilot.agent.credit_calculations import ADVANCE_RATES
from uwcopilot.agent.injection import InjectionHit, code_fields, free_text_fields
from uwcopilot.agent.insurance_calculations import building_cost_per_sqft, location_tiv
from uwcopilot.agent.policy import INJECTION_EVIDENCE_ID, PolicyResult, money, pct
from uwcopilot.agent.programs import load_program_chunks
from uwcopilot.agent.tool_runner import ToolResult
from uwcopilot.rag.guidelines import load_guideline_chunks
from uwcopilot.rag.prompt import detect_instruction_like_text
from uwcopilot.security.normalize import has_visible_text

EvidenceKind = Literal["guideline", "calculation", "tool_result", "application_fact", "check"]


class EvidenceItem(BaseModel):
    """One citable item. flags marks guideline or retrieved text that contains instruction-like phrases."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    kind: EvidenceKind
    title: str
    text: str
    flags: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def guideline_sections() -> dict[str, tuple[str, str]]:
    """{section id: (title, text)} for every section of the SYNTHETIC guidelines and lending programs.

    Guidelines come from corpus/guidelines (the RAG corpus) and programs from corpus/programs (not indexed). A section
    id may appear only once across both folders.
    """
    sections: dict[str, tuple[str, str]] = {}
    for chunk in [*load_guideline_chunks(), *load_program_chunks()]:
        if chunk.chunk_id in sections:
            raise ValueError(f"section id {chunk.chunk_id} appears in more than one guideline or program document")
        sections[chunk.chunk_id] = (chunk.title, chunk.text)
    return sections


def citable_section_ids() -> set[str]:
    """Every guideline and program section id: the allowed citations for memo checks (e.g. SIG's allowed_sections)."""
    return set(guideline_sections())


def yes_no(value: bool) -> str:
    """'yes' or 'no'."""
    return "yes" if value else "no"


def owner_fact(owner: Owner) -> str:
    """One owner by id with type, share and the fields the policy uses; the name is left out on purpose."""
    text = f"{owner.owner_id}: {owner.entity_type}, {pct(owner.ownership_pct)}"
    if owner.is_control_person:
        text += ", control person"
    if owner.credit_score is not None:
        text += f", credit score {owner.credit_score}"
    if owner.bankruptcy_within_7_years:
        text += ", bankruptcy within 7 years"
    return text


def owners_fact(application: AnyApplication) -> str:
    """Owners by id with type, share and the fields the policy uses; names are left out on purpose."""
    return "; ".join(owner_fact(owner) for owner in application.owners)


def location_fact(location: Location, effective_year: int) -> str:
    """One location's schedule values and characteristics."""
    updated = location.systems_updated_year if location.systems_updated_year is not None else "none recorded"
    return (
        f"{location.location_id}: {location.construction_class}, built {location.year_built}, systems updated "
        f"{updated}, roof {location.roof_year} (age {effective_year - location.roof_year} years at the effective "
        f"date), {location.stories} stories, {location.square_feet:,} sq ft, sprinklered "
        f"{yes_no(location.sprinklered)}, protection class {location.protection_class}, knob-and-tube or fuse box "
        f"{yes_no(location.knob_and_tube_or_fuse_box)}, building {money(location.building_value)}, contents "
        f"{money(location.contents_value)}, business income {money(location.business_income_value)}, TIV "
        f"{money(location_tiv(location))}, building cost {money(building_cost_per_sqft(location))} per sq ft, wind "
        f"tier {location.wind_tier}, flood zone {location.flood_zone}, wildfire {location.wildfire_hazard}, vacant "
        f"{location.vacant_days} days"
    )


def loss_runs_fact(application: InsuranceApplication) -> str:
    """The loss runs year by year, with each claim's amounts."""
    runs = application.loss_runs
    lines = [
        f"carrier produced {yes_no(runs.carrier_produced)}, valued as of {runs.valued_as_of.isoformat()}, "
        f"covered years {len(runs.years)}, uncovered years {runs.uncovered_years} (explained "
        f"{yes_no(runs.uncovered_years_explained)})"
    ]
    for year in runs.years:
        premiums = []
        if year.property_earned_premium is not None:
            premiums.append(f"property premium {money(year.property_earned_premium)}")
        if year.gl_earned_premium is not None:
            premiums.append(f"GL premium {money(year.gl_earned_premium)}")
        claims = [
            f"{claim.line} loss {claim.date_of_loss.isoformat()} paid {money(claim.paid)} reserve "
            f"{money(claim.reserve)} incurred {money(claim.incurred)}"
            for claim in year.claims
        ]
        lines.append(
            f"policy year from {year.policy_year_start.isoformat()}: {', '.join(premiums) or 'no premium recorded'}; "
            f"claims: {'; '.join(claims) or 'none'}"
        )
    for claim in runs.current_term_claims:
        lines.append(
            f"current term {claim.line} loss {claim.date_of_loss.isoformat()} incurred {money(claim.incurred)}"
        )
    return " | ".join(lines)


def collateral_fact(collateral: list[CollateralItem]) -> str:
    """Pledged collateral with its NBC-5.1 maximum advance rate ('' when there is none)."""
    return "; ".join(
        f"{item.collateral_type} {money(item.value)} "
        f"(maximum advance rate {pct(ADVANCE_RATES[item.collateral_type] * 100)})"
        for item in collateral
    )


def credit_facts(app: CreditApplication) -> dict[str, tuple[str, str]]:
    """{id: (title, text)} for the structured fields of a loan application."""
    statements = app.bank_statements
    months = sorted(item.month for item in statements.months)
    collateral = collateral_fact(app.collateral)
    debt = app.debt
    return {
        "APP-product": ("Product", f"Product: {app.product}"),
        "APP-requested_amount": ("Requested amount", f"Requested amount: {money(app.requested_amount)}"),
        "APP-term_months": ("Term", f"Term: {app.term_months} months"),
        "APP-note_rate_pct": ("Note rate", f"Note rate: {app.note_rate_pct:.2f}% ({app.rate_type})"),
        "APP-purpose": ("Purpose", f"Purpose: {app.purpose}"),
        "APP-stated_annual_revenue": ("Stated revenue", f"Stated annual revenue: {money(app.stated_annual_revenue)}"),
        "APP-industry": ("Industry", f"Industry code: {app.applicant.industry}"),
        "APP-business_structure": ("Business structure", f"Business structure: {app.applicant.business_structure}"),
        "APP-formation_date": ("Formation date", f"Formation date: {app.applicant.formation_date.isoformat()}"),
        "APP-application_date": ("Application date", f"Application date: {app.application_date.isoformat()}"),
        "APP-new_loans_last_90_days": (
            "New loans",
            f"New loans or advances in the last 90 days: {app.new_loans_last_90_days}",
        ),
        "APP-tax_lien_unresolved": ("Tax lien", f"Unresolved tax lien: {yes_no(app.tax_lien_unresolved)}"),
        "APP-largest_customer_revenue_pct": (
            "Customer concentration",
            f"Largest customer share of revenue: {pct(app.largest_customer_revenue_pct)}",
        ),
        "APP-documents_provided": ("Documents", f"Documents provided: {', '.join(app.documents_provided)}"),
        "APP-nsf_events_last_6_months": (
            "NSF events",
            f"NSF or overdraft events, last 6 months: {statements.nsf_events_last_6_months}",
        ),
        "APP-negative_balance_months_last_12": (
            "Negative months",
            f"Months ending negative, last 12 months: {statements.negative_balance_months_last_12}",
        ),
        "APP-average_daily_balance_last_3_months": (
            "Average balance",
            f"Average daily balance, last 3 months: {money(statements.average_daily_balance_last_3_months)}",
        ),
        "APP-statement_months": (
            "Statement months",
            f"Bank statement months provided: {len(months)} ({months[0]} to {months[-1]})"
            if months
            else "Bank statement months provided: 0",
        ),
        "APP-debt": (
            "Debt inputs",
            f"TTM EBITDA {money(debt.ttm_ebitda)}; TTM P&L revenue {money(debt.ttm_pnl_revenue)}; existing monthly "
            f"payments {money(debt.existing_monthly_payment_schedule)} scheduled and "
            f"{money(debt.existing_monthly_payment_observed)} observed; MCA remittance "
            f"{money(debt.mca_monthly_remittance)} per month; existing total debt "
            f"{money(debt.existing_total_debt_balance)}",
        ),
        "APP-owners": ("Owners", f"Owners: {owners_fact(app)}"),
        "APP-collateral": ("Collateral", f"Collateral: {collateral or 'none'}"),
    }


def insurance_facts(app: InsuranceApplication) -> dict[str, tuple[str, str]]:
    """{id: (title, text)} for the structured fields of an insurance submission."""
    effective_year = app.proposed_effective_date.year
    limits = "not requested"
    if app.gl_limit_per_occurrence is not None:
        limits = f"{money(app.gl_limit_per_occurrence)} per occurrence / {money(app.gl_limit_aggregate or 0)} aggregate"
    return {
        "APP-class_code": ("Class", f"Class code: {app.applicant.class_code}"),
        "APP-years_in_business": ("Years in business", f"Years in business: {app.applicant.years_in_business}"),
        "APP-prior_experience_years": (
            "Prior experience",
            f"Prior experience in the same operations: {app.applicant.prior_experience_years} years",
        ),
        "APP-coverage": ("Coverage", f"Coverage requested: {app.coverage}"),
        "APP-gl_limits": ("GL limits", f"General liability limits: {limits}"),
        "APP-stated_tiv": ("Application TIV", f"TIV on the application: {money(app.stated_tiv)}"),
        "APP-locations": (
            "Locations",
            "Locations: " + " | ".join(location_fact(location, effective_year) for location in app.locations),
        ),
        "APP-loss_runs": ("Loss runs", f"Loss runs: {loss_runs_fact(app)}"),
        "APP-stated_losses": (
            "Stated losses",
            f"Application states {app.stated_claim_count} claims and {money(app.stated_total_incurred)} incurred",
        ),
        "APP-revenue_and_payroll": (
            "Revenue and payroll",
            f"Annual revenue {money(app.annual_revenue)}; annual payroll {money(app.annual_payroll)}",
        ),
        "APP-subcontracting": (
            "Subcontracting",
            f"Subcontracted work {pct(app.subcontracted_work_pct)}; certificate-of-insurance tracking "
            f"{yes_no(app.has_subcontractor_coi_tracking)}; products sold outside the US "
            f"{yes_no(app.foreign_products)}",
        ),
        "APP-documents_provided": ("Documents", f"Documents provided: {', '.join(app.documents_provided)}"),
        "APP-owners": ("Owners", f"Owners: {owners_fact(app)}"),
        "APP-application_date": ("Application date", f"Application date: {app.application_date.isoformat()}"),
        "APP-proposed_effective_date": (
            "Effective date",
            f"Proposed effective date: {app.proposed_effective_date.isoformat()}",
        ),
    }


def plain_number(value: float) -> str:
    """A number without an exponent or trailing zeros: 0.006, 4500, 1250000.5."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def startup_owners_fact(app: StartupCreditApplication) -> str:
    """Owners with the startup program fields (experience, evidence, guarantee, tax lien); no names."""
    parts = []
    for owner in app.owners:
        text = (
            f"{owner_fact(owner)}, relevant experience {plain_number(owner.years_relevant_experience)} years "
            f"(resume and references {yes_no(owner.experience_evidence_provided)}), guarantee signed "
            f"{yes_no(owner.personal_guarantee)}"
        )
        if owner.tax_lien_unresolved:
            text += ", unresolved tax lien"
        parts.append(text)
    return "; ".join(parts)


def sources_and_uses_fact(app: StartupCreditApplication) -> str:
    """Uses by category, sources, totals and whether the equity is verified."""
    uses = app.sources_and_uses.uses
    sources = app.sources_and_uses.sources
    use_text = ", ".join(f"{name} {money(value)}" for name, value in uses.model_dump().items())
    debt_text = "".join(
        f", {item.kind} {money(item.amount)} (monthly payment {money(item.monthly_payment)})"
        for item in sources.other_debt
    )
    return (
        f"Uses: {use_text}; total {money(uses.total())}. Sources: loan {money(sources.loan)}, owner cash equity "
        f"{money(sources.owner_cash_equity)}{debt_text}, other (not repaid) {money(sources.other)}; total "
        f"{money(sources.total())}. Owner cash equity marked as verified by bank statements: "
        f"{yes_no(app.sources_and_uses.equity_verified_by_statements)}"
    )


def projections_fact(app: StartupCreditApplication) -> str:
    """P10 / P50 / P90 revenue and NOI by year, and the monthly fixed costs."""
    parts = [
        f"year {item.year}: revenue P10 {money(item.revenue_p10)}, P50 {money(item.revenue_p50)}, P90 "
        f"{money(item.revenue_p90)}; NOI P10 {money(item.noi_p10)}, P50 {money(item.noi_p50)}, P90 "
        f"{money(item.noi_p90)}"
        for item in app.projections.years
    ]
    parts.append(f"monthly fixed costs {money(app.projections.monthly_fixed_costs)}")
    return " | ".join(parts)


def simulation_fact(app: StartupCreditApplication) -> str:
    """The information-only simulation outputs with their label and provenance (NSP-4.4)."""
    simulation = app.projections.simulation
    if simulation is None:
        return "Simulation outputs: not provided"
    return (
        f"Simulation outputs ({simulation.label}; information only): probability "
        f"{plain_number(simulation.p_dscr_ge_target_year2)} that year 2 DSCR is at least "
        f"{plain_number(simulation.dscr_target)}; probability {plain_number(simulation.p_cash_negative_24m)} that "
        f"cash falls below zero within 24 months; simulator {simulation.simulator_version}, {simulation.n_paths} "
        f"paths, seed {simulation.seed}"
    )


def assumptions_fact(app: StartupCreditApplication) -> str:
    """Each material assumption's value, unit, status, tornado rank and evidence flags (never its free-text source)."""
    items = app.material_assumptions
    if not items:
        return "Material assumptions: none listed"
    parts = []
    for item in items:
        rank = f"tornado rank {item.tornado_rank}" if item.tornado_rank is not None else "not ranked"
        source_stated = yes_no(has_visible_text(item.source))  # the same presence bit as the NSP-4.3 rule
        parts.append(
            f"{item.name} = {plain_number(item.value)} {item.unit} ({item.status}; {rank}; supporting document "
            f"{yes_no(item.supporting_document_provided)}; source stated {source_stated})"
        )
    return f"{len(items)} material assumptions: " + "; ".join(parts)


def startup_facts(app: StartupCreditApplication) -> dict[str, tuple[str, str]]:
    """{id: (title, text)} for the structured fields of a startup credit application (no names, no free text)."""
    applicant = app.applicant
    loan = app.loan_request
    formation = applicant.formation_date.isoformat() if applicant.formation_date is not None else "not formed yet"
    return {
        "APP-product": ("Product", f"Product: {loan.product}"),
        "APP-requested_amount": ("Requested amount", f"Requested amount: {money(loan.amount)}"),
        "APP-term_months": ("Term", f"Term: {loan.term_months} months"),
        "APP-note_rate_pct": ("Note rate", f"Note rate: {loan.annual_rate_pct:.2f}% ({loan.rate_type})"),
        "APP-purpose": ("Purpose", f"Purpose: {loan.purpose}"),
        "APP-business_type": ("Business type", f"Business type: {applicant.business_type}"),
        "APP-industry": ("Industry", f"Industry code: {applicant.industry}"),
        "APP-entity_type": ("Entity type", f"Entity type: {applicant.entity_type}"),
        "APP-state": ("State", f"State of organization: {applicant.state}"),
        "APP-metro_id": ("Metro", f"Metro: {applicant.metro_id}"),
        "APP-formation_date": ("Formation date", f"Formation date: {formation}"),
        "APP-planned_opening_date": (
            "Opening date",
            f"Planned or actual opening date: {applicant.planned_opening_date.isoformat()}",
        ),
        "APP-submitted_date": ("Submitted date", f"Submitted date: {app.submitted_date.isoformat()}"),
        "APP-sources_and_uses": ("Sources and uses", sources_and_uses_fact(app)),
        "APP-projections": ("Projections", f"Projections: {projections_fact(app)}"),
        "APP-simulation": ("Simulation outputs", simulation_fact(app)),
        "APP-material_assumptions": ("Material assumptions", assumptions_fact(app)),
        "APP-documents_provided": ("Documents", f"Documents provided: {', '.join(app.documents_provided)}"),
        "APP-owners": ("Owners", f"Owners: {startup_owners_fact(app)}"),
        "APP-collateral": ("Collateral", f"Collateral: {collateral_fact(app.collateral) or 'none'}"),
    }


def tool_result_text(result: ToolResult) -> str:
    """A short factual summary of one tool result (no names)."""
    if result.status != "ok" or result.output is None:
        return f"{result.tool} ({result.purpose}): {result.status} - {result.error}"
    output: dict[str, Any] = result.output
    if result.tool == "screen_entity":
        candidates = output.get("candidates", [])
        top = max((candidate["score"] for candidate in candidates), default=None)
        lists = sorted({candidate["list_name"] for candidate in candidates})
        snapshots = ", ".join(f"{snap['file_name']} sha256 {snap['sha256'][:12]}" for snap in output["list_snapshots"])
        top_text = f"; top score {top:.4f}; lists: {', '.join(lists)}" if top is not None else ""
        return (
            f"screen_entity for party {result.party_id}: status {output['status']}; {len(candidates)} candidate(s)"
            f"{top_text}; screened against {snapshots}"
        )
    if result.tool == "score_risk":
        reasons = ", ".join(f"{code['feature']} {code['contribution']:+.4f}" for code in output.get("reason_codes", []))
        unknown = ", ".join(output.get("unknown_features", [])) or "none"
        return (
            f"score_risk ({output['product']}, scenario {output['scenario']}): prediction {output['prediction']:.4f}, "
            f"risk band {output['risk_band']}, model {output['model_version']}; reason codes: {reasons or 'none'}; "
            f"unknown features: {unknown}"
        )
    hits = ", ".join(f"{hit['section_id']} (rank {hit['rank']})" for hit in output.get("hits", []))
    return f"search_guidelines query {output['query']['query']!r}: {hits or 'no hits'}"


def injection_check_text(application: AnyApplication, hits: list[InjectionHit]) -> str:
    """What the injection screen checked and what it found (pattern names only, never the text)."""
    checked = ", ".join([*free_text_fields(application), *code_fields(application)])
    if not hits:
        return f"Injection screen checked {checked}: no instruction-like text found."
    found = "; ".join(f"{hit.field} ({', '.join(hit.patterns)})" for hit in hits)
    return f"Injection screen checked {checked}: instruction-like text found in {found}."


# The evidence items whose text shows the simulator version (the APP fact and the two NSP-4.4 calculations).
SIMULATOR_VERSION_EVIDENCE: tuple[str, ...] = (
    "APP-simulation",
    "CALC-startup_p_dscr_ge_target_year2",
    "CALC-startup_p_cash_negative_24m",
)


def evidence_ids_for_code_field(field: str) -> tuple[str, ...]:
    """The evidence items that show a startup code field (none for free text, which never enters the evidence)."""
    if field.startswith("material_assumptions.") and field.endswith((".name", ".unit")):
        return ("APP-material_assumptions",)
    if field == "projections.simulation.simulator_version":
        return SIMULATOR_VERSION_EVIDENCE
    return ()


def flagged_evidence(hits: list[InjectionHit]) -> dict[str, list[str]]:
    """{evidence id: pattern names} for items that show a code field the injection screen flagged.

    The flags become a warning attribute on the item in the memo prompt. Credit and insurance applications have no
    code fields, so for them this is always empty.
    """
    flags: dict[str, set[str]] = {}
    for hit in hits:
        for evidence_id in evidence_ids_for_code_field(hit.field):
            flags.setdefault(evidence_id, set()).update(hit.patterns)
    return {evidence_id: sorted(names) for evidence_id, names in flags.items()}


def application_facts(application: AnyApplication) -> dict[str, tuple[str, str]]:
    """The APP-* facts for the application's line."""
    if isinstance(application, CreditApplication):
        return credit_facts(application)
    if isinstance(application, StartupCreditApplication):
        return startup_facts(application)
    return insurance_facts(application)


def build_evidence(
    application: AnyApplication,
    policy: PolicyResult,
    tool_results: list[ToolResult],
    injection_hits: list[InjectionHit],
) -> dict[str, EvidenceItem]:
    """Every citable item for this application, in a stable order."""
    items: dict[str, EvidenceItem] = {}

    def add(evidence_id: str, kind: EvidenceKind, title: str, text: str, flags: list[str] | None = None) -> None:
        """Add one item (the first one wins if an id repeats)."""
        if evidence_id not in items:
            items[evidence_id] = EvidenceItem(
                evidence_id=evidence_id, kind=kind, title=title, text=text, flags=flags or []
            )

    sections = guideline_sections()
    cited_sections: list[str] = []
    for entry in [*policy.findings, *policy.checks_passed]:
        cited_sections.extend(entry.section_ids)
    for result in tool_results:
        if result.tool == "search_guidelines" and result.output:
            cited_sections.extend(hit["section_id"] for hit in result.output["hits"])
    retrieved_text = {
        hit["section_id"]: (hit["title"], hit["text"])
        for result in tool_results
        if result.tool == "search_guidelines" and result.output
        for hit in result.output["hits"]
    }
    for section_id in dict.fromkeys(cited_sections):
        title, text = sections.get(section_id) or retrieved_text.get(section_id, (section_id, ""))
        if text:
            add(section_id, "guideline", title, text, detect_instruction_like_text(text))
    code_flags = flagged_evidence(injection_hits)
    for calculation in policy.calculations:
        inputs = ", ".join(f"{name}={value}" for name, value in calculation.inputs.items())
        value = "not computable" if calculation.value is None else f"{calculation.value}"
        add(
            calculation.calc_id,
            "calculation",
            calculation.label,
            f"{calculation.label} = {value}; inputs: {inputs or 'none'}",
            code_flags.get(calculation.calc_id),
        )
    for result in tool_results:
        add(result.result_id, "tool_result", f"{result.tool} result", tool_result_text(result))
    for evidence_id, (title, text) in application_facts(application).items():
        add(evidence_id, "application_fact", title, text, code_flags.get(evidence_id))
    add(INJECTION_EVIDENCE_ID, "check", "Injection screen", injection_check_text(application, injection_hits))
    return items

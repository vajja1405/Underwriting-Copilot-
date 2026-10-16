"""The 36 golden founder PROJECTS: S4's golden scenarios plus SYNTHETIC owners, entities and documents.

Everything here is SYNTHETIC and seeded; no real business, person or document is described.

    for scenario in golden_scenarios():            # S4: 6 business types x (SYNTHETIC metro + 5 real metros)
        project = project_from_golden(scenario)    # S6: + owner, entity, documents
        package = build_package(project)

What the S4 scenario does not have and this module adds (generator choices, documented, not data):
- one SYNTHETIC owner (O1, 100%, the control person) whose credit score and experience are the plan's FACTS; the
  name is made of two word lists ("Avery ... Example"), the business name is "<owner first name>'s <business type>
  (SYNTHETIC)";
- the entity: an LLC to be formed before closing, organized in the metro's first state (config/studio/metros); the
  SYNTHETIC metro lists no state, so it uses TX, the state the startup application generator uses for its
  SYNTHETIC files (agent/synthetic_startup.py);
- documents, by the scenario's position in golden_specs() (index mod 3), so the set covers the evidence flags:
  0 - only what the package itself is (business plan, projections, sources and uses statement);
  1 - the owner's checklist documents, bank statements that print exactly the stated equity (VERIFIED), resumes and
      references, and the guarantee is signed;
  2 - as 1, but the statements print a tenth less than the stated equity (CONFLICTING: both figures are kept), plus
      a lease letter of intent for the typed rent and a contractor quote for the typed build-out cost when the plan
      has them (VERIFIED);
- other debt: the S5 generator only creates seller notes, so every other debt is coded seller_note;
- the package date is GOLDEN_PREPARED_ON, a fixed generator date.
"""

from datetime import date

from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.narrative.golden import GoldenScenario, golden_specs
from uwcopilot.studio.narrative.models import FounderDocumentValue
from uwcopilot.studio.narrative.prose import business_noun
from uwcopilot.studio.package.project import FounderProject, PackageOwner
from uwcopilot.studio.profiles import list_profiles

GOLDEN_PREPARED_ON = date(2026, 10, 1)
SYNTHETIC_STATE = "TX"
FIRST_NAMES: tuple[str, ...] = ("Avery", "Blake", "Casey", "Devon", "Emery", "Finley")
LAST_NAMES: tuple[str, ...] = ("Example", "Fixture", "Sample", "Placeholder", "Mockwell", "Testwood")
PACKAGE_DOCUMENTS: tuple[str, ...] = ("business_plan", "financial_projections", "sources_and_uses_statement")
OWNER_DOCUMENTS: tuple[str, ...] = (
    "signed_application",
    "personal_financial_statements",
    "principal_photo_ids",
    "formation_documents",
    "beneficial_ownership_certification",
    "owner_resumes_and_references",
)
CONFLICT_SHARE = 0.9
VARIANTS = 3


def scenario_index(scenario: GoldenScenario) -> int:
    """The scenario's position in golden_specs()."""
    return golden_specs().index((scenario.metro_id, scenario.profile_id))


def owner_name(scenario: GoldenScenario) -> tuple[str, str]:
    """(first, last) SYNTHETIC name from the business type's and the metro's positions."""
    first = FIRST_NAMES[list_profiles().index(scenario.profile_id) % len(FIRST_NAMES)]
    metros = sorted({metro for metro, _ in golden_specs()})
    last = LAST_NAMES[metros.index(scenario.metro_id) % len(LAST_NAMES)]
    return first, last


def golden_state(metro_id: str) -> str:
    """The metro's first state, or TX for the SYNTHETIC metro (module docstring)."""
    states = load_metro(metro_id).states
    return states[0] if states else SYNTHETIC_STATE


def equity_statement(scenario: GoldenScenario, share: float) -> FounderDocumentValue:
    """SYNTHETIC bank statements printing share x the stated owner equity."""
    return FounderDocumentValue(
        field="plan.owner_equity",
        value=round(scenario.case.plan.owner_equity * share, 2),
        doc_id="SYN-STATEMENTS",
        document_type="equity_bank_statements",
        page=1,
    )


def golden_documents(scenario: GoldenScenario, variant: int) -> list[FounderDocumentValue]:
    """The document values of one variant (module docstring)."""
    if variant == 0:
        return []
    if variant == 1:
        return [equity_statement(scenario, 1.0)]
    documents = [equity_statement(scenario, CONFLICT_SHARE)]
    plan = scenario.case.plan
    if plan.monthly_rent is not None:
        documents.append(
            FounderDocumentValue(
                field="plan.monthly_rent",
                value=plan.monthly_rent,
                doc_id="SYN-LOI",
                document_type="lease_or_letter_of_intent",
                page=1,
            )
        )
    if plan.capex is not None:
        documents.append(
            FounderDocumentValue(
                field="plan.capex",
                value=plan.capex,
                doc_id="SYN-QUOTE",
                document_type="contractor_or_equipment_quotes",
                page=1,
            )
        )
    return documents


def project_from_golden(scenario: GoldenScenario) -> FounderProject:
    """The SYNTHETIC founder project of one golden scenario (module docstring)."""
    variant = scenario_index(scenario) % VARIANTS
    first, last = owner_name(scenario)
    facts = scenario.case.facts
    owner = PackageOwner(
        owner_id="O1",
        name=f"{first} {last}",
        ownership_pct=100.0,
        is_control_person=True,
        credit_score=facts.credit_score,
        experience_months=facts.experience_months,
        personal_guarantee=variant > 0,
    )
    provided = list(PACKAGE_DOCUMENTS) + (list(OWNER_DOCUMENTS) if variant > 0 else [])
    notes = list(scenario.notes) + [f"golden document variant {variant} (SYNTHETIC)"]
    return FounderProject(
        project_id=scenario.scenario_id,
        prepared_on=GOLDEN_PREPARED_ON,
        business_name=f"{first}'s {business_noun(scenario.profile_id)} (SYNTHETIC)",
        entity_type="llc",
        state=golden_state(scenario.metro_id),
        case=scenario.case,
        candidates=list(scenario.candidates),
        site_score=scenario.site_score,
        feasibility=scenario.feasibility,
        path_result=scenario.path_result,
        owners=[owner],
        documents=golden_documents(scenario, variant),
        documents_provided=provided,  # type: ignore[arg-type]
        other_debt_kinds=["seller_note"] * len(facts.other_debt),
        data_label="SYNTHETIC",
        notes=notes,
    )

"""build_package: one founder plan -> a lender-ready package (schema founder-package-v1).

    package = build_package(project)                 # FounderProject -> Package (JSON-serializable)
    pdf = render_pdf(package)                        # bytes, %PDF...

Steps (every number stays in ONE founder ledger, built with S4's build_founder_ledger):
1. a first founder ledger of the plan, to read the claims the package arithmetic starts from (loan, term, note rate,
   SiteIQ's competition factor);
2. the package's own claims (claims_sites.py, claims_finance.py): business and owner names and shares, the chosen
   site's label, every other candidate site simulated with the same plan and seed, competition details, the map's
   ring radii, the loan's amortization and the simulation's horizon and cash window;
3. the founder ledger again, with those claims added (extra_claims): the ledger the package prints;
4. the locked narrative (S4 render_narrative; the template by default, a model only when asked);
5. the sections (sections_*.py), each a list of blocks that point into the ledger; the evidence appendix numbers
   every claim;
6. the material assumption list and the applicant facts that to_application needs;
7. the package id (FP- + the first 12 hex of the SHA-256 of the project's canonical JSON) and the round-trip audit
   of the package text (audit.py). A package whose text holds an untraced number is still returned, with the
   numbers listed, so a caller (and the tests) can see it; render_pdf refuses to print it.
"""

import hashlib
from collections.abc import Callable
from typing import Any

from uwcopilot.agent.applications import canonical_json
from uwcopilot.evidence.ledger_models import Claim, Ledger
from uwcopilot.llm.base import LLMClient
from uwcopilot.studio.data.fabric_base import DataFabric
from uwcopilot.studio.metros import load_metro
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.founder_ledger import FOUNDER_LEDGER_VERSION, build_founder_ledger, run_identity
from uwcopilot.studio.narrative.generate import render_narrative
from uwcopilot.studio.narrative.models import NarrativeResult
from uwcopilot.studio.narrative.prose import business_noun
from uwcopilot.studio.package.assumptions import material_assumptions
from uwcopilot.studio.package.audit import audit_package
from uwcopilot.studio.package.blocks import BuildContext
from uwcopilot.studio.package.claims_finance import loan_schedule_claims, owner_claims, rate_claims, run_setting_claims
from uwcopilot.studio.package.claims_sites import candidate_claims, chosen_site_claims, competition_claims, ring_claims
from uwcopilot.studio.package.constants import PACKAGE_BUILDER_VERSION, PACKAGE_SCHEMA_VERSION
from uwcopilot.studio.package.mapdata import build_map_layer
from uwcopilot.studio.package.models import ApplicantInfo, Block, Package, PackageAudit, RunIdentity, Section
from uwcopilot.studio.package.project import FounderProject
from uwcopilot.studio.package.runs import RunIndex, model_runs
from uwcopilot.studio.package.sba import SECTION_SPECS, sba_record
from uwcopilot.studio.package.sections_appendix import appendix_index, appendix_section
from uwcopilot.studio.package.sections_finance import funding_section, loan_section, projections_section, stress_section
from uwcopilot.studio.package.sections_market import comparison_section, market_section
from uwcopilot.studio.package.sections_overview import business_section, executive_summary
from uwcopilot.studio.package.sections_review import path_section, risks_section
from uwcopilot.studio.path_to_approval.search import fabric_for

PACKAGE_ID_PREFIX = "FP-"
PACKAGE_ID_CHARS = 12
MONTHLY_MONTHS = 12
ClientFactory = Callable[[str, str | None], LLMClient]


def project_sha256(project: FounderProject) -> str:
    """SHA-256 of the project's canonical JSON plus the package versions (the package's inputs hash)."""
    payload = {
        "project": project.model_dump(mode="json"),
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "builder_version": PACKAGE_BUILDER_VERSION,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def founder_ledger(project: FounderProject, extra_claims: list[Claim] | None = None) -> Ledger:
    """The founder ledger of the project's plan (S4), with the package's own claims when given."""
    return build_founder_ledger(
        project.case,
        project.feasibility,
        project.site_score,
        project.path_result,
        documents=project.documents,
        extra_claims=extra_claims or (),
        subject_id=project.project_id,
        data_label=project.data_label,
        monthly_months=MONTHLY_MONTHS,
    )


def package_claims(project: FounderProject, first: Ledger, fabric: DataFabric) -> list[Claim]:
    """The claims the package adds to the founder ledger (module docstring, step 2)."""
    claims = {item.id: item for item in first.claims}
    run = run_identity(project.case, project.feasibility)
    return [
        *owner_claims(project),
        *chosen_site_claims(project.case),
        *candidate_claims(project.case, project.candidates, project.feasibility, fabric),
        *competition_claims(project.site_score, claims.get(ids.site_factor_id("competition"))),
        *ring_claims(project.site_score),
        *loan_schedule_claims(claims),
        *rate_claims(claims),
        *run_setting_claims(project.feasibility, run),
    ]


def applicant_info(project: FounderProject, metro_name: str) -> ApplicantInfo:
    """The applicant facts the lender's application needs besides the ledger."""
    return ApplicantInfo(
        business_name=project.business_name,
        business_type=project.case.profile_id,
        metro_id=project.case.metro_id,
        metro_name=metro_name,
        metro_mode=project.feasibility.metro_mode,
        entity_type=project.entity_type,
        state=project.state,
        formation_date=project.formation_date,
        planned_opening_date=project.case.plan.opening_date,
        site_label=project.case.site_label,
        loan_purpose=project.loan_purpose,
        owners=list(project.owners),
        documents_provided=project.all_documents_provided(),
        other_debt_kinds=project.debt_kinds(),
        collateral=list(project.collateral),
    )


def section_blocks(ctx: BuildContext, key: str, has_map: bool, package_id: str, runs: RunIndex) -> list[Block]:
    """The blocks of one section by its key."""
    provided = ctx.project.all_documents_provided()
    builders: dict[str, Callable[[], list[Block]]] = {
        "executive_summary": lambda: executive_summary(ctx),
        "business": lambda: business_section(ctx, provided),
        "market": lambda: market_section(ctx, has_map),
        "site_comparison": lambda: comparison_section(ctx),
        "funding_request": lambda: funding_section(ctx),
        "projections": lambda: projections_section(ctx),
        "loan": lambda: loan_section(ctx),
        "stress": lambda: stress_section(ctx),
        "risks": lambda: risks_section(ctx),
        "path": lambda: path_section(ctx),
        "appendix": lambda: appendix_section(ctx.ledger, package_id, FOUNDER_LEDGER_VERSION, runs),
    }
    return builders[key]()


def build_sections(ctx: BuildContext, has_map: bool, package_id: str, runs: RunIndex) -> list[Section]:
    """Every section in SECTION_SPECS order, numbered from 1."""
    return [
        Section(
            key=spec.key,
            number=number,
            title=spec.title,
            sba_sections=list(spec.sba_sections),
            blocks=section_blocks(ctx, spec.key, has_map, package_id, runs),
        )
        for number, spec in enumerate(SECTION_SPECS, start=1)
    ]


def build_package(
    project: FounderProject,
    *,
    provider: str = "template",
    model: str | None = None,
    client_factory: ClientFactory | None = None,
    fabric: DataFabric | None = None,
) -> Package:
    """The lender-ready package of one founder plan (module docstring).

    provider / model / client_factory choose who writes the narrative (S4 render_narrative: "template" by default;
    a model's draft is validated and falls back to the template). fabric defaults to the metro's data fabric.
    """
    data = fabric or fabric_for(project.case.metro_id)
    first = founder_ledger(project)
    ledger = founder_ledger(project, package_claims(project, first, data))
    narrative: NarrativeResult = render_narrative(ledger, provider, model=model, client_factory=client_factory)
    metro_name = load_metro(project.case.metro_id).name
    ctx = BuildContext(
        project=project,
        ledger=ledger,
        narrative=narrative,
        metro_name=metro_name,
        business_noun=business_noun(project.case.profile_id),
        calendar_months=[band.calendar_month for band in project.feasibility.monthly[:MONTHLY_MONTHS]],
    )
    inputs_sha = project_sha256(project)
    package_id = PACKAGE_ID_PREFIX + inputs_sha[:PACKAGE_ID_CHARS]
    map_layer = build_map_layer(project.site_score, data)
    runs = model_runs(ledger)
    sections = build_sections(ctx, map_layer is not None, package_id, runs)
    feasibility = project.feasibility
    run: dict[str, Any] = run_identity(project.case, feasibility)
    package = Package(
        package_id=package_id,
        data_label=project.data_label,
        prepared_on=project.prepared_on,
        project_id=project.project_id,
        inputs_sha256=inputs_sha,
        applicant=applicant_info(project, metro_name),
        run=RunIdentity(
            simulator_version=feasibility.simulator_version,
            seed=feasibility.seed,
            n_paths=feasibility.n_paths,
            months=feasibility.months,
            plan_sha256=str(run["plan_sha256"]),
            dscr_target=feasibility.dscr_target.value,
            path_engine_version=project.path_result.engine_version if project.path_result is not None else None,
        ),
        ledger=ledger,
        narrative=narrative,
        sections=sections,
        appendix_index=appendix_index(ledger),
        runs=runs,
        map=map_layer,
        material_assumptions=material_assumptions(ledger, feasibility),
        sba=dict(sba_record()),
        audit=PackageAudit(numbers=0, untraced=[], value_cells=0, sections=len(sections)),
        notes=list(project.notes),
    )
    return package.model_copy(update={"audit": audit_package(package)})

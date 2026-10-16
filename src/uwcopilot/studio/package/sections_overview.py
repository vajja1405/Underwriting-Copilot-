"""Sections 1 and 2 of the package: the executive summary and the business, its owners and its plan.

1. Executive summary (SBA "Executive summary"): the key figures, the provenance-locked narrative (S4, the template by
   default) and every check of the SYNTHETIC startup program and the Studio targets, with its result.
2. Business, owners and plan (SBA "Company description", "Organization and management", "Service or product line"):
   what the business is, where, when it opens, the legal entity, the owners and the documents in the file.
Only claim ids and plain words are arranged here; the renderer prints every value.
"""

from uwcopilot.agent.applications import STARTUP_DOCUMENTS
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.package.blocks import BuildContext, check, claim, slot, status, text
from uwcopilot.studio.package.constants import NO_DECISION_NOTE, SIMULATION_NOTE
from uwcopilot.studio.package.models import Block, Cell, KeyFact, KeyFacts, Paragraph, Table

DOCUMENT_NAMES: dict[str, str] = {
    "signed_application": "Signed application",
    "business_plan": "Business plan (this package)",
    "financial_projections": "Financial projections (this package)",
    "sources_and_uses_statement": "Sources and uses statement (this package)",
    "personal_financial_statements": "Personal financial statements",
    "principal_photo_ids": "Photo IDs of the principals",
    "formation_documents": "Formation documents",
    "beneficial_ownership_certification": "Beneficial ownership certification",
    "collateral_documents": "Collateral documents",
    "equity_bank_statements": "Bank statements showing the owner cash equity",
    "owner_resumes_and_references": "Owner resumes and references",
    "lease_or_letter_of_intent": "Lease or letter of intent",
    "contractor_or_equipment_quotes": "Contractor or equipment quotes",
    "comparable_data_with_source": "Comparable data with its source",
}
ENTITY_WORDS: dict[str, str] = {
    "llc": "limited liability company",
    "corporation": "corporation",
    "partnership": "partnership",
    "sole_proprietorship": "sole proprietorship",
}
KEY_FIGURES: tuple[tuple[str, str], ...] = (
    ("Loan requested", ids.FOUNDER_LOAN),
    ("Loan term", ids.FOUNDER_TERM),
    ("Note rate, percent per year", "PKG.loan.note_rate_pct"),
    ("Total project cost", ids.CALC_PROJECT_COST),
    ("Owner cash equity", ids.FOUNDER_EQUITY),
    ("Equity injection", ids.CALC_EQUITY_PCT),
    ("Projected DSCR, year-2 median (NSP-4.1)", ids.CALC_PROJECTED_DSCR),
    ("Revenue-stressed DSCR (NSP-4.2)", ids.CALC_REVENUE_STRESSED_DSCR),
    ("Simulated chance year-2 DSCR reaches the target", ids.SIM_P_DSCR_Y2),
    ("Simulated chance of passing the revenue stress", ids.SIM_P_STRESSED_Y2),
    ("Simulated chance cash falls below zero", ids.SIM_P_CASH),
    ("Median break-even month", ids.SIM_BREAK_EVEN_P50),
    ("SiteIQ overall site score", ids.SITE_OVERALL),
    ("SiteIQ data coverage", ids.SITE_COVERAGE),
)
TARGET_CHECK_SECTIONS = "Studio target (design choice, not a lender rule)"


def yes_no(flag: bool) -> str:
    """'yes' or 'no'."""
    return "yes" if flag else "no"


def key_figures(ctx: BuildContext) -> KeyFacts:
    """The headline figures that exist in this ledger."""
    items = [KeyFact(label=label, ref=claim_id) for label, claim_id in KEY_FIGURES if ctx.has(claim_id)]
    return KeyFacts(title="Key figures", items=items)


def checks_table(ctx: BuildContext) -> Table:
    """Every check in the ledger: its sections, what it checks and its result."""
    rows: list[list[Cell]] = []
    for assertion in ctx.ledger.assertions:
        sections = ", ".join(assertion.section_ids) if assertion.section_ids else TARGET_CHECK_SECTIONS
        rows.append([text(f"[{sections}]"), text(assertion.expression), check(assertion.id)])
    return Table(
        title="Checks against the SYNTHETIC startup program and the Studio targets",
        header=["Section", "What is checked", "Result for this plan"],
        rows=rows,
        widths=[1.4, 3.2, 3.4],
        align=["left", "left", "left"],
        size="small",
        note=(
            "A finding is a rule this plan does not meet yet; a check that rests on a missing value is not evaluated. "
            "The lender runs every rule itself."
        ),
    )


def executive_summary(ctx: BuildContext) -> list[Block]:
    """Section 1 (module docstring)."""
    blocks: list[Block] = [
        key_figures(ctx),
        Paragraph(draft=SIMULATION_NOTE, style="note"),
    ]
    for paragraph in ctx.narrative.draft.split("\n\n"):
        if paragraph.strip():
            blocks.append(Paragraph(draft=paragraph.strip()))
    blocks.append(Paragraph(draft=NO_DECISION_NOTE, style="note"))
    blocks.append(checks_table(ctx))
    return blocks


def business_table(ctx: BuildContext) -> Table:
    """What the business is, where and when it opens, and its legal entity."""
    project = ctx.project
    entity = ENTITY_WORDS.get(project.entity_type, project.entity_type)
    formed = "already formed" if project.formation_date is not None else "to be formed before closing"
    rows = [
        [text("Business name"), claim("PKG.business_name"), status("PKG.business_name")],
        [text("Business type"), text(ctx.business_noun), status(ids.PLAN_BUSINESS_TYPE)],
        [text("Metro"), text(ctx.metro_name), status(ids.PLAN_METRO)],
        [text("Chosen site"), claim("PKG.site.c0.label"), status("PKG.site.c0.label")],
        [text("Planned opening date"), claim(ids.PLAN_OPENING_DATE), status(ids.PLAN_OPENING_DATE)],
        [text("Legal entity"), text(f"{entity} ({formed}), state of organization {project.state}"), text("")],
    ]
    if project.formation_date is not None:
        rows.append([text("Formation date"), text(project.formation_date.isoformat()), text("")])
    plan_rows = (
        ("Price per unit", "FOUNDER.price"),
        ("Monthly rent quote", "FOUNDER.monthly_rent"),
        ("Days open per week", "FOUNDER.days_open_per_week"),
        ("Paid staff hours per month", "FOUNDER.paid_hours_per_month"),
        ("Planned hourly wage", "FOUNDER.hourly_wage"),
        ("Most demand the business can serve", "FOUNDER.capacity"),
        ("Owner draw per month", ids.FOUNDER_OWNER_DRAW),
    )
    rows += [[text(label), claim(claim_id), status(claim_id)] for label, claim_id in plan_rows if ctx.has(claim_id)]
    return Table(
        title="The business",
        header=["Item", "Value", "Status"],
        rows=rows,
        widths=[2.4, 3.8, 1.8],
        align=["left", "left", "left"],
    )


def owners_table(ctx: BuildContext) -> Table:
    """Every owner with the share, control and guarantee flags."""
    rows = []
    for owner in ctx.project.owners:
        prefix = f"PKG.owner.{owner.owner_id}"
        rows.append(
            [
                text(f"[{owner.owner_id}]"),
                claim(f"{prefix}.name"),
                claim(f"{prefix}.ownership_pct"),
                text(yes_no(owner.is_control_person)),
                text(yes_no(owner.personal_guarantee)),
                status(f"{prefix}.ownership_pct"),
            ]
        )
    return Table(
        title="Owners",
        header=["Owner", "Name", "Ownership", "Control person", "Personal guarantee", "Status"],
        rows=rows,
        widths=[0.8, 2.6, 1.1, 1.1, 1.2, 1.2],
        align=["left", "left", "right", "center", "center", "left"],
    )


def principal_rows(ctx: BuildContext) -> Table:
    """The principals' credit score and experience with their program checks (NSP-2.1, NSP-2.2)."""
    rows = [
        [text("Lowest principal credit score"), claim(ids.FOUNDER_CREDIT_SCORE), status(ids.FOUNDER_CREDIT_SCORE)],
        [text("Most relevant owner experience"), claim(ids.FOUNDER_EXPERIENCE), status(ids.FOUNDER_EXPERIENCE)],
    ]
    if ctx.has_check(ids.A_CREDIT):
        rows[0].append(check(ids.A_CREDIT))
    if ctx.has_check(ids.A_EXPERIENCE):
        rows[1].append(check(ids.A_EXPERIENCE))
    for row in rows:
        while len(row) < 4:
            row.append(text(""))
    return Table(
        title="Principals",
        header=["Fact", "Value", "Status", "Program check"],
        rows=rows,
        widths=[2.2, 1.2, 1.4, 3.2],
        align=["left", "right", "left", "left"],
    )


def documents_table(ctx: BuildContext, provided: list[str]) -> Table:
    """Which documents of the startup checklist are in the file."""
    rows = [
        [text(DOCUMENT_NAMES.get(code, code)), text(f"[{code}]"), text(yes_no(code in provided))]
        for code in STARTUP_DOCUMENTS
    ]
    return Table(
        title="Documents in the file",
        header=["Document", "Checklist code", "In the file"],
        rows=rows,
        widths=[3.6, 3.2, 1.2],
        align=["left", "left", "center"],
        size="small",
        note=(
            "A document counts only when it is in the file (NSP-1.3): a statement that equity is verified, that "
            "experience is evidenced or that an assumption is backed does not count without it."
        ),
    )


def business_section(ctx: BuildContext, provided: list[str]) -> list[Block]:
    """Section 2 (module docstring)."""
    label = "SYNTHETIC " if ctx.ledger.data_label == "SYNTHETIC" else ""
    intro = (
        f"This {label}plan is for a {ctx.business_noun} in {ctx.metro_name}, at the site named "
        f"{slot('PKG.site.c0.label')}, planned to open on {slot(ids.PLAN_OPENING_DATE)}."
    )
    return [
        Paragraph(draft=intro, style="lead"),
        business_table(ctx),
        owners_table(ctx),
        principal_rows(ctx),
        documents_table(ctx, provided),
        Paragraph(
            draft=(
                "Marketing and sales: this package does not write the founder's marketing and sales plan; it is the "
                "founder's to add."
            ),
            style="note",
        ),
    ]

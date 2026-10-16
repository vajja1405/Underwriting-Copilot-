"""Sections 9 and 10: risks and mitigations, and the path-to-approval history.

9. Risks and mitigations: each risk the ledger shows (a top driver without a document, the chance of running out of
   cash, the revenue stress, break-even timing, competition, data coverage, open items) with the evidence behind it
   and a mitigation. A mitigation is always a plan choice or a document to provide, never a change of a fact
   (Studio S5 guardrails; rule C: no approval promise).
10. Path-to-approval history: what the path search (S5) measured before any change, every change-set it returned (or
   the closest result when nothing reaches the targets), the simulated chances after each one, and the evidence a
   lender would need for each changed figure. Every figure is a PATH.* claim (MODEL_ESTIMATE of the search).
"""

from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.prose import claim_phrase, input_phrase
from uwcopilot.studio.package.blocks import BuildContext, assert_slot, check, claim, draft, slot, text
from uwcopilot.studio.package.constants import SIMULATION_LABEL
from uwcopilot.studio.package.models import Block, Cell, Paragraph, Table
from uwcopilot.studio.path_to_approval.models import ChangeSet

DRIVER_DOCUMENT_WORDS: dict[str, str] = {
    "monthly_rent": "a signed lease or a landlord's letter of intent",
    "rent_escalation": "a signed lease or a landlord's letter of intent",
    "mature_demand": "comparable data with its source (comparable sites, traffic or visitor counts)",
    "price": "comparable prices with their source, or a price test",
    "hourly_wage": "a staffing plan with local wage data and its source",
    "paid_hours_per_month": "a staffing plan",
}
DEFAULT_DRIVER_DOCUMENT = "comparable data with its source"
PATH_STATUS_TEXT: dict[str, str] = {
    "already_meets_target": "The plan already reaches every simulated target; the search changed nothing.",
    "paths_found": "The search found change-sets that reach every simulated target; the smallest ones are listed.",
    "no_path_within_bounds": (
        "No change-set within the lever bounds reaches every simulated target; the closest result is listed."
    ),
}
TARGET_NAMES: dict[str, str] = {
    ids.A_TARGET_DSCR: "Studio target: chance that year-2 DSCR reaches the coverage target",
    ids.A_TARGET_STRESS: "Studio target: chance that year-2 DSCR passes the revenue stress",
    ids.A_TARGET_CASH: "Studio target: chance that cash falls below zero",
}
LEVER_EVIDENCE: dict[str, str] = {
    "owner_equity": "bank statements that show the new owner cash of {to} [NSP-3.1]",
    "working_capital_reserve": "bank statements that show the owner cash behind a reserve of {to} [NSP-3.1]",
    "build_out_cost": "a written quote at or below {to} for the same build-out and equipment",
    "loan_amount": "a loan request of {to} in the sources and uses [NSP-3.2]; the lender decides the amount",
    "loan_term_months": "a request to the lender for a term of {to}; the lender decides the term",
    "price": "evidence that customers would pay {to} per unit, for example a price test",
    "days_open_per_week": "a staffing plan for {to} days a week",
    "site": "a rent quote or a lease letter of intent for that candidate site",
}


def driver_risks(ctx: BuildContext) -> list[list[Cell]]:
    """A row per top driver: its swing and whether a document backs it (NSP-6.1)."""
    rows = []
    for rank in range(1, 4):
        check_id = ids.driver_assertion_id(rank)
        if not ctx.has(ids.tornado_id(rank, "swing")) or not ctx.has_check(check_id):
            continue
        name = str(ctx.value(ids.tornado_id(rank, "input")))
        document = DRIVER_DOCUMENT_WORDS.get(name, DEFAULT_DRIVER_DOCUMENT)
        rows.append(
            [
                text(f"Top driver rank {rank}: {input_phrase(name)}"),
                draft(
                    f"Moves year-2 median net income by {slot(ids.tornado_id(rank, 'swing'))}; it "
                    f"{assert_slot(check_id)}."
                ),
                text(f"Provide {document} [NSP-6.1]."),
            ]
        )
    return rows


def cash_risk(ctx: BuildContext) -> list[list[Cell]]:
    """Running out of cash within the simulator's cash window."""
    if not ctx.has(ids.SIM_P_CASH):
        return []
    return [
        [
            text("Running out of cash"),
            draft(
                f"Simulated chance that cash falls below zero within {slot('PKG.sim.cash_window_months')}: "
                f"{slot(ids.SIM_P_CASH)}; the cash reserve at opening is {slot(ids.CALC_CASH_RESERVE)}."
            ),
            text("A larger working-capital reserve or more owner cash; see the path-to-approval history."),
        ]
    ]


def stress_risk(ctx: BuildContext) -> list[list[Cell]]:
    """The lender's revenue stress (the binding test)."""
    return [
        [
            text("Revenue falls short of the plan"),
            draft(
                f"With revenue cut by {slot(ids.PROGRAM_REVENUE_STRESS_SHARE)} the year-2 DSCR is "
                f"{slot(ids.CALC_REVENUE_STRESSED_DSCR)}, which {assert_slot(ids.A_REVENUE_STRESS)} [NSP-4.2]."
            ),
            text(
                "A smaller loan, more owner cash or a longer term lower the debt service; evidence of demand "
                "narrows it."
            ),
        ]
    ]


def break_even_risk(ctx: BuildContext) -> list[list[Cell]]:
    """Slow ramp-up: the share of paths that never break even."""
    return [
        [
            text("Slow ramp-up"),
            draft(
                f"The median break-even month is {slot(ids.SIM_BREAK_EVEN_P50)}; {slot(ids.SIM_NOT_BREAK_EVEN)} of "
                "the simulated paths never break even in the horizon."
            ),
            text(
                "Keep the reserve until the business breaks even; document opening demand (pre-sales, comparable "
                "sites)."
            ),
        ]
    ]


def site_risks(ctx: BuildContext) -> list[list[Cell]]:
    """Competition and data coverage at the site."""
    rows = []
    competition = ids.site_factor_id("competition")
    if ctx.has(competition):
        rows.append(
            [
                text("Competition near the site"),
                draft(
                    f"Same-type businesses within the near ring: {slot(competition)}; the simulation's yearly chance "
                    f"that a competitor opens nearby is {slot(ids.assume_id('competitor_entry_annual_probability'))}."
                ),
                text("Compare the candidate sites; document what sets this business apart."),
            ]
        )
    if ctx.has(ids.SITE_COVERAGE):
        rows.append(
            [
                text("Data coverage of the site"),
                draft(
                    f"SiteIQ data coverage of the site is {slot(ids.SITE_COVERAGE)}; factors without data are UNKNOWN."
                ),
                text("Add local data or the founder's own documented figures for the missing factors."),
            ]
        )
    elif ctx.feasibility.metro_mode == "limited":
        rows.append(
            [
                text("Data coverage of the metro"),
                text("Limited mode: no SiteIQ result here, so no location factor is shown."),
                text("Use the founder's own documented figures, or a metro with a data snapshot."),
            ]
        )
    return rows


def open_item_risks(ctx: BuildContext, mentioned: set[str]) -> list[list[Cell]]:
    """Every UNKNOWN or CONFLICTING claim, and every finding not covered by another row."""
    rows = []
    for item in ctx.ledger.open_claims():
        words = "is not available yet" if item.status == "UNKNOWN" else "has conflicting sources"
        rows.append(
            [
                text(f"Open item: {claim_phrase(item)}"),
                draft(f"The {claim_phrase(item)} {words}: {slot(item.id)}."),
                text("Provide the missing value or the document that settles it."),
            ]
        )
    for assertion in ctx.ledger.assertions:
        if assertion.result is False and assertion.id not in mentioned:
            sections = ", ".join(assertion.section_ids)
            name = TARGET_NAMES.get(assertion.id, f"Finding: {assertion.expression}")
            rows.append(
                [
                    text(name),
                    check(assertion.id),
                    text(
                        f"Change the plan or provide evidence; the lender reviews it [{sections}]."
                        if sections
                        else "Change the plan; see the path-to-approval history."
                    ),
                ]
            )
    return rows


def risks_section(ctx: BuildContext) -> list[Block]:
    """Section 9."""
    mentioned = {ids.driver_assertion_id(rank) for rank in range(1, 4)} | {ids.A_REVENUE_STRESS}
    rows = [*driver_risks(ctx), *cash_risk(ctx), *stress_risk(ctx), *break_even_risk(ctx), *site_risks(ctx)]
    rows += open_item_risks(ctx, mentioned)
    return [
        Paragraph(
            draft=(
                "Every mitigation below is a plan choice or a document to provide; none changes a fact, hides a debt "
                f"or promises approval. Probabilities are {SIMULATION_LABEL}."
            ),
            style="lead",
        ),
        Table(
            title="Risks and mitigations",
            header=["Risk", "What the ledger shows", "Mitigation"],
            rows=rows,
            widths=[2.0, 3.6, 2.4],
            align=["left", "left", "left"],
            size="small",
        ),
    ]


def outcome_rows(prefix: str) -> list[list[Cell]]:
    """The three simulated chances of one plan (before, or after a change-set)."""
    return [
        [text("Chance year-2 DSCR reaches the target"), claim(f"{prefix}.p_dscr_pct"), claim(ids.TARGET_P_DSCR)],
        [text("Chance of passing the stress target"), claim(f"{prefix}.p_stressed_pct"), claim(ids.TARGET_P_STRESSED)],
        [text("Chance cash falls below zero"), claim(f"{prefix}.p_cash_negative_pct"), claim(ids.TARGET_P_CASH)],
    ]


def change_rows(ctx: BuildContext, change_set: ChangeSet, rank: int) -> list[list[Cell]]:
    """Each change of one change-set: lever, new value and the evidence a lender would need."""
    rows = []
    for index, change in enumerate(change_set.changes, start=1):
        to_id = ids.change_set_id(rank, f"c{index}.to")
        lever_id = ids.change_set_id(rank, f"c{index}.lever")
        evidence = LEVER_EVIDENCE.get(change.lever, "a document that backs the new figure").format(to=slot(to_id))
        rows.append([claim(lever_id), claim(to_id), draft(evidence)])
    return rows


def change_set_blocks(ctx: BuildContext, change_set: ChangeSet, rank: int) -> list[Block]:
    """The changes and the simulated chances after one change-set."""
    title = f"Change-set {rank}" if rank > 0 else "Closest result (does not reach every target)"
    return [
        Table(
            title=f"{title}: what changes",
            header=["Plan figure", "New value", "Evidence a lender would need"],
            rows=change_rows(ctx, change_set, rank),
            widths=[2.2, 1.6, 4.2],
            align=["left", "right", "left"],
            size="small",
        ),
        Table(
            title=f"{title}: simulated chances after the change",
            header=["Measure", "After", "Studio target"],
            rows=outcome_rows(ids.change_set_id(rank, "after")),
            widths=[4.4, 1.8, 1.8],
            align=["left", "right", "right"],
            size="small",
        ),
    ]


def path_section(ctx: BuildContext) -> list[Block]:
    """Section 10."""
    result = ctx.path_result
    if result is None or not ctx.has(ids.PATH_STATUS):
        return [Paragraph(draft="No path-to-approval search was run for this package.", style="note")]
    blocks: list[Block] = [
        Paragraph(draft=PATH_STATUS_TEXT.get(result.status, "The search finished."), style="lead"),
        Table(
            title="Before any change (as the search measured it)",
            header=["Measure", "Before", "Studio target"],
            rows=outcome_rows("PATH.before"),
            widths=[4.4, 1.8, 1.8],
            align=["left", "right", "right"],
            size="small",
            note=(
                f"The search re-simulates the plan with common random numbers; its chances are {SIMULATION_LABEL} "
                "and can differ slightly from the main simulation's."
            ),
        ),
    ]
    for change_set in result.change_sets:
        blocks.extend(change_set_blocks(ctx, change_set, change_set.rank))
    if result.closest is not None:
        blocks.extend(change_set_blocks(ctx, result.closest, 0))
    blocks.append(
        Paragraph(
            draft=(
                "A change-set is a plan choice suggested by a simulation: until its evidence exists, a changed figure "
                "is not a quote and not verified, and the founder's own figures stay as they are in the ledger."
            ),
            style="note",
        )
    )
    return blocks

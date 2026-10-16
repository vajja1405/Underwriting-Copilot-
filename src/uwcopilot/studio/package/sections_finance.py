"""Sections 5 to 8: funding request, financial projections, loan schedule with DSCR, and the lender's stress tests.

5. Funding request (SBA "Funding request"): uses and sources of funds, the loan request and its program checks.
6. Financial projections (SBA "Financial projections"): the first year month by month (revenue, net income after
   debt service and cash, each P10 / P50 / P90), years 1-3 by year, break-even and the top drivers.
7. Loan schedule and debt service coverage: the monthly payment, the yearly amortization, and DSCR per year.
8. Stress tests: the base case and the lender's levers of the SYNTHETIC startup program (NSP-4.2: revenue cut with
   NOI lower by the same dollars; the rate stress for a variable rate), plus the Studio targets.
Every value is a claim; a grid whose cells all share one status says so under the table.
"""

from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.prose import input_phrase
from uwcopilot.studio.package.blocks import (
    BuildContext,
    assert_slot,
    check,
    claim,
    draft,
    slot,
    status,
    text,
    value_range,
)
from uwcopilot.studio.package.constants import SIMULATION_LABEL
from uwcopilot.studio.package.models import Block, Cell, Figure, KeyFact, KeyFacts, Paragraph, Table

PERCENTILE_HEADERS = ["P10", "P50", "P90"]
PURPOSE_WORDS: dict[str, str] = {
    "build_out_and_equipment": "build-out and equipment",
    "equipment_purchase": "equipment purchase",
    "real_estate_purchase": "real estate purchase",
    "working_capital": "working capital",
    "mixed_project_costs": "build-out, equipment and working capital",
}
SIMULATED_GRID_NOTE = (
    f"Every value in this table is simulated (MODEL_ESTIMATE), {SIMULATION_LABEL}: P10 is a low case, P50 the median "
    "and P90 a high case across the simulated paths."
)
TOP_DRIVERS = 3
TORNADO_BARS = 8


def capex_id(ctx: BuildContext) -> str:
    """The build-out cost the simulation used: the founder's figure, else the profile ASSUMPTION."""
    return ids.FOUNDER_CAPEX if ctx.has(ids.FOUNDER_CAPEX) else ids.ASSUME_CAPEX


def money_table(ctx: BuildContext, title: str, first_header: str, items: list[tuple[str, str, bool]]) -> Table:
    """Label | amount | (range, only when some row has one) | status, for (label, claim id, bold) items."""
    with_range = any(ctx.claims[claim_id].value_range is not None for _, claim_id, _ in items)
    rows = []
    for label, claim_id, bold in items:
        row = [text(label, bold), claim(claim_id, bold)]
        if with_range:
            row.append(value_range(claim_id))
        rows.append([*row, status(claim_id)])
    header = [first_header, "Amount", *(["Range"] if with_range else []), "Status"]
    return Table(
        title=title,
        header=header,
        rows=rows,
        widths=[3.4, 1.5, *([2.0] if with_range else []), 1.4],
        align=["left", "right", *(["right"] if with_range else []), "left"],
    )


def uses_table(ctx: BuildContext) -> Table:
    """Uses of funds (they add up to the total project cost)."""
    items = [
        ("Build-out and equipment", capex_id(ctx), False),
        ("Working-capital reserve (cash at opening)", ids.CALC_CASH_RESERVE, False),
        ("Total project cost", ids.CALC_PROJECT_COST, True),
    ]
    return money_table(ctx, "Uses of funds", "Use", items)


def sources_table(ctx: BuildContext) -> Table:
    """Sources of funds, with every other loan or seller note and its payment (NSP-3.2)."""
    items = [("Requested startup term loan", ids.FOUNDER_LOAN, False), ("Owner cash equity", ids.FOUNDER_EQUITY, False)]
    for index, kind in enumerate(ctx.project.debt_kinds(), start=1):
        words = "Seller note" if kind == "seller_note" else "Other loan"
        items.append((f"{words} [debt {index}]", ids.other_debt_id(index, "amount"), False))
        items.append(
            (f"Monthly payment of that debt [debt {index}]", ids.other_debt_id(index, "monthly_payment"), False)
        )
    items.append(("Total sources (equal to total project cost)", ids.CALC_PROJECT_COST, True))
    return money_table(ctx, "Sources of funds", "Source", items)


def request_table(ctx: BuildContext) -> Table:
    """The loan request and the program checks that read it."""
    purpose = PURPOSE_WORDS.get(ctx.project.loan_purpose, ctx.project.loan_purpose)
    rows = [
        [text("Loan amount"), claim(ids.FOUNDER_LOAN), status(ids.FOUNDER_LOAN), check(ids.A_AMOUNT)],
        [text("Term"), claim(ids.FOUNDER_TERM), status(ids.FOUNDER_TERM), check(ids.A_TERM)],
        [
            text("Note rate, percent per year"),
            claim("PKG.loan.note_rate_pct"),
            status("PKG.loan.note_rate_pct"),
            text(""),
        ],
        [text("Rate type"), claim(ids.PLAN_RATE_TYPE), status(ids.PLAN_RATE_TYPE), text("")],
        [text("Purpose"), text(purpose), text(""), text("")],
        [text("Equity injection"), claim(ids.CALC_EQUITY_PCT), status(ids.CALC_EQUITY_PCT), check(ids.A_EQUITY)],
        [
            text("Program minimum equity injection"),
            claim(ids.PROGRAM_MIN_EQUITY),
            status(ids.PROGRAM_MIN_EQUITY),
            text(""),
        ],
        [text("Owner cash equity"), claim(ids.FOUNDER_EQUITY), status(ids.FOUNDER_EQUITY), check(ids.A_EQUITY_DOCS)],
        [
            text("Collateral"),
            text("none listed" if not ctx.project.collateral else "listed in the file"),
            text(""),
            check(ids.A_COLLATERAL),
        ],
    ]
    return Table(
        title="Loan request and program checks",
        header=["Item", "Value", "Status", "Program check"],
        rows=rows,
        widths=[2.4, 1.6, 1.4, 2.6],
        align=["left", "right", "left", "left"],
    )


def funding_section(ctx: BuildContext) -> list[Block]:
    """Section 5."""
    return [
        Paragraph(
            draft=(
                "Sources equal uses: owner cash equity, the requested loan and any other debt pay for the build-out "
                "and equipment, and what is left is the working-capital reserve at opening."
            ),
            style="lead",
        ),
        uses_table(ctx),
        sources_table(ctx),
        request_table(ctx),
    ]


def percentile_cells(field: str, period_id: str) -> list[Cell]:
    """P10 / P50 / P90 cells of one field."""
    return [claim(f"{period_id}.{field}_{percentile}") for percentile in ("p10", "p50", "p90")]


def monthly_rows(ctx: BuildContext, fields: tuple[str, ...]) -> list[list[Cell]]:
    """One row per month of the first year, with P10 / P50 / P90 of each field."""
    rows = []
    for index, calendar in enumerate(ctx.calendar_months, start=1):
        if not ctx.has(ids.month_id(index, "revenue_p50")):
            break
        prefix = f"SIM.m{index}"
        cells = [cell for field in fields for cell in percentile_cells(field, prefix)]
        rows.append([text(f"Month {index} ({calendar})"), *cells])
    return rows


def monthly_tables(ctx: BuildContext) -> list[Table]:
    """The first year month by month: revenue and net income after debt service, then the cash balance."""
    return [
        Table(
            title="First year by month: revenue | net income after debt service",
            header=["Month", *PERCENTILE_HEADERS, *PERCENTILE_HEADERS],
            rows=monthly_rows(ctx, ("revenue", "net_income")),
            widths=[1.5, *[1.0] * 6],
            align=["left", *["right"] * 6],
            size="small",
            note=SIMULATED_GRID_NOTE,
        ),
        Table(
            title="First year by month: cash balance at month end",
            header=["Month", *PERCENTILE_HEADERS],
            rows=monthly_rows(ctx, ("cash",)),
            widths=[1.5, 1.4, 1.4, 1.4],
            align=["left", "right", "right", "right"],
            size="small",
            note=SIMULATED_GRID_NOTE,
        ),
    ]


def yearly_tables(ctx: BuildContext) -> list[Table]:
    """Years 1-3: revenue and NOI, then net income, debt service and DSCR."""
    first, second = [], []
    for band in ctx.feasibility.yearly:
        prefix = f"SIM.y{band.year}"
        first.append(
            [text(f"Year {band.year}"), *percentile_cells("revenue", prefix), *percentile_cells("noi", prefix)]
        )
        dscr = [
            claim(f"{prefix}.dscr_{p}") if ctx.has(f"{prefix}.dscr_{p}") else text("not defined")
            for p in ("p10", "p50", "p90")
        ]
        second.append(
            [
                text(f"Year {band.year}"),
                *percentile_cells("net_income", prefix),
                claim(f"{prefix}.debt_service_p50"),
                *dscr,
            ]
        )
    return [
        Table(
            title="Years by year: revenue | net operating income (NOI)",
            header=["Year", *PERCENTILE_HEADERS, *PERCENTILE_HEADERS],
            rows=first,
            widths=[1.2, *[1.13] * 6],
            align=["left", *["right"] * 6],
            note=SIMULATED_GRID_NOTE,
        ),
        Table(
            title="Years by year: net income after debt service | debt service (P50) | DSCR",
            header=["Year", *PERCENTILE_HEADERS, "Debt service P50", *PERCENTILE_HEADERS],
            rows=second,
            widths=[1.0, 1.0, 1.0, 1.0, 1.3, 0.9, 0.9, 0.9],
            align=["left", *["right"] * 7],
            size="small",
            note=(
                "Every value is simulated (MODEL_ESTIMATE); a DSCR is not defined for a year without scheduled "
                "payments."
            ),
        ),
    ]


def break_even_table() -> Table:
    """Break-even month percentiles and the share of paths that never break even."""
    rows = [
        [text("Break-even month, low case P10"), claim(ids.SIM_BREAK_EVEN_P10)],
        [text("Break-even month, median P50"), claim(ids.SIM_BREAK_EVEN_P50)],
        [text("Break-even month, high case P90"), claim(ids.SIM_BREAK_EVEN_P90)],
        [text("Share of paths that never break even in the horizon"), claim(ids.SIM_NOT_BREAK_EVEN)],
        [text("Simulation horizon"), claim("PKG.sim.horizon_months")],
    ]
    return Table(
        title="Break-even",
        header=["Measure", "Value"],
        rows=rows,
        widths=[5.4, 2.6],
        align=["left", "right"],
        note=f"Month 1 is the opening month. Simulated (MODEL_ESTIMATE), {SIMULATION_LABEL}.",
    )


def drivers_table(ctx: BuildContext) -> Table | None:
    """The top drivers of year-2 net income (tornado) and whether a document backs each one (NSP-6.1)."""
    rows = []
    for rank in range(1, TOP_DRIVERS + 1):
        if not ctx.has(ids.tornado_id(rank, "input")):
            continue
        name = str(ctx.value(ids.tornado_id(rank, "input")))
        check_id = ids.driver_assertion_id(rank)
        rows.append(
            [
                text(f"Rank {rank}"),
                text(input_phrase(name)),
                claim(ids.tornado_id(rank, "input_p10")),
                claim(ids.tornado_id(rank, "input_p90")),
                claim(ids.tornado_id(rank, "swing")),
                check(check_id) if ctx.has_check(check_id) else text(""),
            ]
        )
    if not rows:
        return None
    return Table(
        title="Top drivers of year-2 net income (NSP-6.1)",
        header=[
            "Rank",
            "Input",
            "Input at P10",
            "Input at P90",
            "Swing of year-2 median net income",
            "Supporting document",
        ],
        rows=rows,
        widths=[0.7, 2.0, 1.0, 1.0, 1.3, 2.0],
        align=["left", "left", "right", "right", "right", "left"],
        size="small",
        note="Each input swings between its P10 and P90 with every other draw held fixed (MODEL_ESTIMATE).",
    )


def projections_section(ctx: BuildContext) -> list[Block]:
    """Section 6."""
    blocks: list[Block] = [
        Paragraph(
            draft=(
                "The projection is a simulation of the plan's possible futures, not a forecast: the first year is "
                f"shown month by month and every year after that in total. Every figure is {SIMULATION_LABEL}."
            ),
            style="lead",
        ),
        Figure(
            figure="cash_band",
            caption=(
                "Cash balance in the first year: the band runs from the low case (P10) to the high case (P90), the "
                "line is the median (P50); values from the table below."
            ),
            refs=[ids.month_id(index, "cash_p50") for index in range(1, len(ctx.calendar_months) + 1)],
            labels=list(ctx.calendar_months),
            height_in=2.5,
        ),
        *monthly_tables(ctx),
        *yearly_tables(ctx),
        break_even_table(),
    ]
    drivers = drivers_table(ctx)
    if drivers is not None:
        blocks.append(
            Figure(
                figure="tornado",
                caption=(
                    "Swing of year-2 median net income when one input moves from its P10 to its P90 (largest first)."
                ),
                refs=[
                    ids.tornado_id(rank, "swing")
                    for rank in range(1, TORNADO_BARS + 1)
                    if ctx.has(ids.tornado_id(rank, "swing"))
                ],
                height_in=2.6,
            )
        )
        blocks.append(drivers)
    return blocks


def schedule_table(ctx: BuildContext) -> Table:
    """The loan's yearly amortization."""
    rows = []
    year = 1
    while ctx.has(f"PKG.loan.y{year}.interest"):
        prefix = f"PKG.loan.y{year}"
        rows.append(
            [
                text(f"Loan year {year}"),
                claim(f"{prefix}.interest"),
                claim(f"{prefix}.principal"),
                claim(f"{prefix}.end_balance"),
            ]
        )
        year += 1
    note_status = ctx.claims["PKG.loan.y1.interest"].status if ctx.has("PKG.loan.y1.interest") else "UNKNOWN"
    return Table(
        title="Amortization by loan year",
        header=["Loan year", "Interest", "Principal", "Balance at year end"],
        rows=rows,
        widths=[1.8, 2.0, 2.0, 2.2],
        align=["left", "right", "right", "right"],
        size="small",
        note=(
            f"Arithmetic on the loan amount, term and note rate; status {note_status} (never more certain than the "
            "least certain input). Each figure's range at the ends of the note rate's range is in the appendix."
        ),
    )


def dscr_table(ctx: BuildContext) -> Table:
    """DSCR per year with the simulated chance of reaching the target, and the program formula for year 2."""
    rows = []
    for band in ctx.feasibility.yearly:
        prefix = f"SIM.y{band.year}"
        probability = ids.safe_id("SIM", f"p_dscr_year{band.year}")
        rows.append(
            [
                text(f"Year {band.year}"),
                claim(f"{prefix}.noi_p50"),
                claim(f"{prefix}.debt_service_p50"),
                claim(f"{prefix}.dscr_p50") if ctx.has(f"{prefix}.dscr_p50") else text("not defined"),
                claim(probability) if ctx.has(probability) else text("not defined"),
            ]
        )
    return Table(
        title="Debt service coverage by year (simulated medians)",
        header=["Year", "NOI P50", "Debt service P50", "DSCR P50", "Chance DSCR reaches the target"],
        rows=rows,
        widths=[1.2, 1.7, 1.7, 1.4, 2.0],
        align=["left", "right", "right", "right", "right"],
        note=(
            f"Simulated (MODEL_ESTIMATE), {SIMULATION_LABEL}. The simulator's yearly debt service is the requested "
            "loan's; the program's year-2 formula below uses the projected annual debt service, other debt included."
        ),
    )


def loan_section(ctx: BuildContext) -> list[Block]:
    """Section 7."""
    facts = [
        KeyFact(label="Monthly payment", ref="PKG.loan.monthly_payment"),
        KeyFact(label="Loan annual debt service", ref="CALC.loan_annual_debt_service"),
        KeyFact(label="Projected annual debt service, loan and other debt", ref=ids.CALC_ANNUAL_DEBT_SERVICE),
        KeyFact(label="Projected DSCR, year 2, program formula", ref=ids.CALC_PROJECTED_DSCR),
    ]
    formula = (
        f"Program formula (NSP-4.1): year-2 median NOI of {slot(ids.year_id(2, 'noi_p50'))} divided by the projected "
        f"annual debt service of {slot(ids.CALC_ANNUAL_DEBT_SERVICE)} gives a projected DSCR of "
        f"{slot(ids.CALC_PROJECTED_DSCR)}, which {assert_slot(ids.A_PROJECTED_DSCR)} of "
        f"{slot(ids.PROGRAM_MIN_DSCR)} [NSP-4.1]."
    )
    return [
        KeyFacts(title="Loan and debt service", items=[item for item in facts if ctx.has(item.ref)]),
        schedule_table(ctx),
        dscr_table(ctx),
        Paragraph(draft=formula),
    ]


def stress_rows(ctx: BuildContext) -> list[list[Cell]]:
    """Base case, revenue stress and (variable rate) rate stress, the lender's way on the year-2 median."""
    rows = [
        [
            text("Base case (year-2 median)"),
            claim(ids.year_id(2, "noi_p50")),
            claim(ids.CALC_ANNUAL_DEBT_SERVICE),
            claim(ids.CALC_PROJECTED_DSCR),
            claim(ids.PROGRAM_MIN_DSCR),
            check(ids.A_PROJECTED_DSCR),
            claim(ids.SIM_P_DSCR_Y2),
        ],
        [
            draft(f"Revenue cut by {slot(ids.PROGRAM_REVENUE_STRESS_SHARE)}, NOI lower by the same dollars"),
            claim(ids.CALC_REVENUE_STRESSED_NOI),
            claim(ids.CALC_ANNUAL_DEBT_SERVICE),
            claim(ids.CALC_REVENUE_STRESSED_DSCR),
            claim(ids.PROGRAM_REVENUE_STRESS_MIN),
            check(ids.A_REVENUE_STRESS),
            claim(ids.SIM_P_STRESSED_Y2) if ctx.has(ids.SIM_P_STRESSED_Y2) else text("not simulated"),
        ],
    ]
    if ctx.has(ids.CALC_RATE_STRESSED_DSCR, ids.PROGRAM_RATE_STRESS_ADD):
        rate_p = ids.stress_id("rate_stress", "p")
        rows.append(
            [
                draft(f"Note rate plus {slot(ids.PROGRAM_RATE_STRESS_ADD)}"),
                claim(ids.year_id(2, "noi_p50")),
                claim(ids.CALC_RATE_STRESS_DEBT_SERVICE),
                claim(ids.CALC_RATE_STRESSED_DSCR),
                claim(ids.PROGRAM_RATE_STRESS_MIN),
                check(ids.A_RATE_STRESS),
                claim(rate_p) if ctx.has(rate_p) else text("not simulated"),
            ]
        )
    else:
        rows.append([text("Rate stress"), text("not required: the note rate is fixed (NSP-4.2)"), *[text("")] * 5])
    return rows


def target_rows(ctx: BuildContext) -> list[list[Cell]]:
    """The three Studio targets: threshold, the measured probability and the result."""
    rows = []
    for assertion_id, words, threshold_id, limit_id in (
        (ids.A_TARGET_DSCR, "Year-2 DSCR reaches", ids.TARGET_DSCR_MIN, ids.TARGET_P_DSCR),
        (
            ids.A_TARGET_STRESS,
            "Year-2 DSCR under the stress target reaches",
            ids.TARGET_STRESSED_MIN,
            ids.TARGET_P_STRESSED,
        ),
        (ids.A_TARGET_CASH, "Cash falls below zero within (months)", ids.TARGET_CASH_MONTHS, ids.TARGET_P_CASH),
    ):
        assertion = ctx.assertions.get(assertion_id)
        if assertion is None:
            continue
        measured = assertion.evidence_ids[0] if assertion.evidence_ids else None
        rows.append(
            [
                text(words),
                claim(threshold_id),
                claim(limit_id),
                claim(measured) if measured is not None else text("not measured"),
                check(assertion_id),
            ]
        )
    return rows


def stress_section(ctx: BuildContext) -> list[Block]:
    """Section 8."""
    why = (
        f"Why the revenue stress binds: revenue lower by {slot(ids.PROGRAM_REVENUE_STRESS_SHARE)} with NOI lower by "
        "the same dollars treats every cost as fixed, so the plan passes only when year-2 NOI covers the debt service "
        "plus that share of revenue. A plan can meet the program's minimum DSCR and still fall short of the stress "
        "[NSP-4.2]."
    )
    return [
        Table(
            title="Lender stress tests on the year-2 median (SYNTHETIC startup program, NSP-4.2)",
            header=[
                "Test",
                "NOI",
                "Annual debt service",
                "DSCR",
                "Program minimum",
                "Result",
                "Simulated share passing",
            ],
            rows=stress_rows(ctx),
            widths=[1.9, 1.0, 1.1, 0.7, 0.9, 1.6, 0.9],
            align=["left", "right", "right", "right", "right", "left", "right"],
            size="small",
            note=(
                "The DSCR column is the program's formula on the year-2 median; the last column is the share of "
                f"simulated paths that pass the same test, {SIMULATION_LABEL}."
            ),
        ),
        Paragraph(draft=why),
        Table(
            title="Studio targets (editable design choices, not lender rules)",
            header=["Target", "Threshold", "Target chance", "Simulated chance", "Result"],
            rows=target_rows(ctx),
            widths=[2.4, 1.0, 1.1, 1.2, 2.3],
            align=["left", "right", "right", "right", "left"],
            note=f"Simulated chances are {SIMULATION_LABEL}. Reaching a target is not an approval.",
        ),
    ]

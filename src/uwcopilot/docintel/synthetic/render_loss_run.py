"""Render one fictional carrier's SYNTHETIC loss run and return its cited LossRun extraction.

Each of the five fictional carriers (insurance_build.CARRIERS, same index) has its own layout: date format
(MM/DD/YYYY, YYYY-MM-DD or DD-Mon-YYYY), column order and labels ("Paid Loss" / "Paid", "O/S Reserve" /
"Reserve" / "Outstanding"), and the totals block above or below the claim rows. Every run has a carrier letterhead
line ("Carrier-produced loss run"), the policy number, the insured name, a "Valued as of" date and an earned
premium by policy year block. One carrier (Lanternfield, layout 2) prints its claim detail on a separate second
page; any claim table also continues on a new page when it does not fit.
"""

from dataclasses import dataclass

from uwcopilot.docintel.schemas import LossRun, LossRunClaim, LossRunSummary, LossRunYear
from uwcopilot.docintel.synthetic.common import format_date, money
from uwcopilot.docintel.synthetic.insurance_case import CarrierRun, InsuranceCase
from uwcopilot.docintel.synthetic.render_base import LEFT_MARGIN, DocumentWriter, PageWriter, Segment
from uwcopilot.docintel.synthetic.render_helpers import Cell, cite_named, heading, label, note, section, table_row

MONEY_FIELDS = {"paid", "reserve", "incurred"}
WIDTHS = {
    "claim_number": 75.0,
    "date_of_loss": 62.0,
    "line": 85.0,
    "status": 40.0,
    "paid": 66.0,
    "reserve": 66.0,
    "incurred": 66.0,
}
GAP = 6.0
ROW_SIZE = 8.0


@dataclass(frozen=True)
class LossRunLayout:
    """One carrier's loss-run format."""

    date_style: str
    columns: tuple[tuple[str, str], ...]
    totals_on_top: bool
    totals_labels: tuple[str, str, str, str]
    claims_on_new_page: bool = False


LAYOUTS: tuple[LossRunLayout, ...] = (
    LossRunLayout(
        "mdy",
        (("claim_number", "Claim #"), ("date_of_loss", "Date of Loss"), ("line", "Line"), ("status", "Status"),
         ("paid", "Paid Loss"), ("reserve", "O/S Reserve"), ("incurred", "Total Incurred")),
        False,
        ("Number of claims", "Total paid loss", "Total O/S reserve", "Total incurred"),
    ),
    LossRunLayout(
        "iso",
        (("claim_number", "Claim No."), ("date_of_loss", "Loss Date"), ("line", "Coverage"), ("paid", "Paid"),
         ("reserve", "Reserve"), ("incurred", "Incurred"), ("status", "Status")),
        True,
        ("Claims", "Paid", "Reserve", "Incurred"),
    ),
    LossRunLayout(
        "dmy_abbrev",
        (("date_of_loss", "Date of Loss"), ("claim_number", "Claim Number"), ("line", "Line of Business"),
         ("incurred", "Incurred"), ("paid", "Paid"), ("reserve", "Outstanding"), ("status", "Status")),
        False,
        ("Claim count", "Paid to date", "Outstanding", "Total incurred"),
        True,
    ),
    LossRunLayout(
        "mdy",
        (("claim_number", "Claim"), ("date_of_loss", "Loss date"), ("line", "Line"), ("paid", "Paid Loss"),
         ("reserve", "Reserve"), ("incurred", "Incurred"), ("status", "Status")),
        True,
        ("Total claims", "Total paid", "Total reserve", "Total incurred"),
    ),
    LossRunLayout(
        "iso",
        (("date_of_loss", "Loss Date"), ("claim_number", "Claim No"), ("line", "Line"), ("status", "Status"),
         ("incurred", "Incurred"), ("reserve", "O/S Reserve"), ("paid", "Paid")),
        False,
        ("Claims reported", "Paid", "Outstanding reserve", "Incurred"),
    ),
)  # fmt: skip


def column_positions(layout: LossRunLayout) -> dict[str, tuple[float, str]]:
    """x position and alignment of each column (money columns are right-aligned at their right edge)."""
    positions = {}
    x = LEFT_MARGIN
    for name, _ in layout.columns:
        width = WIDTHS[name]
        positions[name] = (x + width, "right") if name in MONEY_FIELDS else (x, "left")
        x += width + GAP
    return positions


def claims_header(page: PageWriter, layout: LossRunLayout) -> None:
    """The claim table header row."""
    positions = column_positions(layout)
    segments = [
        Segment(title, positions[name][0], size=ROW_SIZE, font="Helvetica-Bold", align=positions[name][1])
        for name, title in layout.columns
    ]
    page.row(segments)
    page.hline(3.0)


def totals_block(page: PageWriter, run: CarrierRun, layout: LossRunLayout) -> LossRunSummary:
    """The carrier's totals block, as printed (it may disagree with the rows)."""
    count, paid, reserve, incurred = run.summary()
    section(page, "Loss summary")
    count_label, paid_label, reserve_label, incurred_label = layout.totals_labels
    return LossRunSummary(
        claim_count=page.field(count_label, str(count), count),
        total_paid=page.field(paid_label, money(paid), paid),
        total_reserve=page.field(reserve_label, money(reserve), reserve),
        total_incurred=page.field(incurred_label, money(incurred), incurred),
    )


def premium_block(page: PageWriter, run: CarrierRun, layout: LossRunLayout, case: InsuranceCase) -> list[LossRunYear]:
    """Earned premium by policy year ("n/a" for a line the carrier did not write)."""
    section(page, "Earned premium by policy year")
    page.row(
        [
            Segment("Policy year start", LEFT_MARGIN, font="Helvetica-Bold"),
            Segment("Property", 300.0, font="Helvetica-Bold", align="right"),
            Segment("General liability", 420.0, font="Helvetica-Bold", align="right"),
        ]
    )
    years = []
    for year in run.years:
        printed, positions = table_row(
            page,
            [
                Cell("start", format_date(year.start, layout.date_style), LEFT_MARGIN),
                Cell(
                    "property", "n/a" if year.property_premium is None else money(year.property_premium), 300.0, "right"
                ),
                Cell("gl", "n/a" if year.gl_premium is None else money(year.gl_premium), 420.0, "right"),
            ],
        )
        entry = LossRunYear(policy_year_start=cite_named(page, printed, positions, "start", year.start))
        if year.property_premium is not None:
            entry.property_earned_premium = cite_named(page, printed, positions, "property", year.property_premium)
        if year.gl_premium is not None:
            entry.gl_earned_premium = cite_named(page, printed, positions, "gl", year.gl_premium)
        years.append(entry)
    if run.current_term:
        note(
            page,
            f"Current term from {format_date(case.current_term_start, layout.date_style)}: in force; "
            "premium not yet earned",
        )
    return years


def claim_rows(
    writer: DocumentWriter, page: PageWriter, run: CarrierRun, layout: LossRunLayout
) -> tuple[PageWriter, list[LossRunClaim]]:
    """The claim table (on its own page for some layouts); continues on a new page when the page is full."""
    printed_claims = run.printed_claims()
    if layout.claims_on_new_page and printed_claims:
        page = writer.new_page()
        note(page, f"{run.carrier} - claim detail for policy {run.policy_number}")
    section(page, "Claims")
    if not printed_claims:
        note(page, "No claims reported for the policy periods shown.", size=9.0)
        return page, []
    claims_header(page, layout)
    positions = column_positions(layout)
    claims = []
    for claim in printed_claims:
        if page.space_left() < 40:
            page = writer.new_page()
            note(page, f"{run.carrier} - loss run continued")
            claims_header(page, layout)
        texts = {
            "claim_number": claim.claim_number,
            "date_of_loss": format_date(claim.date_of_loss, layout.date_style),
            "line": label(claim.line),
            "status": claim.status,
            "paid": money(claim.paid, symbol=False),
            "reserve": money(claim.shown_reserve, symbol=False),
            "incurred": money(claim.shown_incurred, symbol=False),
        }
        cells = [Cell(name, texts[name], positions[name][0], positions[name][1]) for name, _ in layout.columns]
        row, index = table_row(page, cells, size=ROW_SIZE)
        claims.append(
            LossRunClaim(
                claim_number=cite_named(page, row, index, "claim_number", claim.claim_number),
                date_of_loss=cite_named(page, row, index, "date_of_loss", claim.date_of_loss),
                line=cite_named(page, row, index, "line", claim.line),
                status=cite_named(page, row, index, "status", claim.status),
                paid=cite_named(page, row, index, "paid", claim.paid),
                reserve=cite_named(page, row, index, "reserve", claim.shown_reserve),
                incurred=cite_named(page, row, index, "incurred", claim.shown_incurred),
            )
        )
    return page, claims


def render_loss_run(writer: DocumentWriter, case: InsuranceCase, run: CarrierRun) -> LossRun:
    """One carrier's loss run (1-2 pages)."""
    layout = LAYOUTS[run.layout]
    page = writer.new_page()
    carrier = heading(page, run.carrier)
    produced = note(page, "Carrier-produced loss run - fictional carrier - SYNTHETIC, for demonstration only")
    insured = case.name_on(f"loss_run:{run.carrier}")
    result = LossRun(
        doc_id=writer.doc_id,
        carrier=page.cite(carrier, 0, run.carrier),
        carrier_produced=page.cite(produced, 0, True),
    )
    page.skip(4)
    result.insured_name = page.field("Insured", insured, insured)
    result.policy_number = page.field("Policy number", run.policy_number, run.policy_number)
    result.valued_as_of = page.field("Valued as of", format_date(run.valued_as_of, layout.date_style), run.valued_as_of)
    if layout.totals_on_top:
        result.summary = totals_block(page, run, layout)
    result.years = premium_block(page, run, layout, case)
    page, result.claims = claim_rows(writer, page, run, layout)
    if not layout.totals_on_top:
        if page.space_left() < 80:
            page = writer.new_page()
        result.summary = totals_block(page, run, layout)
    return result

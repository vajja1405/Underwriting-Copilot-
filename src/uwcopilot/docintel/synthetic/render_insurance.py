"""Renderers for the SYNTHETIC insurance submission documents other than loss runs (render_loss_run.py).

Our own layouts (loosely inspired by commercial lines forms, not copies of any industry form). Each function
returns its part of the oracle InsuranceSubmission with every printed value cited. Renderers only read the case.
"""

from uwcopilot.docintel.schemas import (
    CoverEmail,
    InspectionReport,
    InsuranceApplicationForm,
    OwnershipStatement,
    PriorCarrier,
    PropertySchedule,
    ScheduleLocation,
)
from uwcopilot.docintel.synthetic.common import format_date, money, percent, yes_no
from uwcopilot.docintel.synthetic.insurance_case import InsuranceCase, LocationData
from uwcopilot.docintel.synthetic.render_base import LEFT_MARGIN, DocumentWriter, PageWriter, Segment
from uwcopilot.docintel.synthetic.render_credit import REMARKS_WIDTH, owner_table
from uwcopilot.docintel.synthetic.render_helpers import (
    Cell,
    cite_named,
    field_date,
    heading,
    label,
    note,
    section,
    table_header,
    table_row,
)

FORM_DATE_STYLE = "mdy"
HARBORLINE_INBOX = "Harborline Mutual New Business <newbusiness@harborline.example.org>"


def application_page_one(page: PageWriter, case: InsuranceCase, form: InsuranceApplicationForm) -> None:
    """Applicant, coverage, values, exposures and the stated loss history."""
    heading(page, "HARBORLINE MUTUAL - COMMERCIAL LINES APPLICATION (SYNTHETIC)")
    note(page, "Harborline Mutual is a fictional insurer. Application form for demonstration only.")
    section(page, "Section 1 - Applicant")
    form.named_insured = page.field("Named insured", case.named_insured, case.named_insured)
    form.fein = page.field("FEIN", case.fein, case.fein)
    form.years_in_business = page.field("Years in business", str(case.years_in_business), case.years_in_business)
    form.business_class = page.field("Business class", label(case.class_code), case.class_code)
    section(page, "Section 2 - Coverage requested")
    form.coverage = page.field("Coverage", label(case.coverage), case.coverage)
    if case.gl_limits is None:
        page.field("GL limit per occurrence", "Not requested", None)
        page.field("GL limit aggregate", "Not requested", None)
    else:
        form.gl_limit_per_occurrence = page.field(
            "GL limit per occurrence", money(case.gl_limits[0]), case.gl_limits[0]
        )
        form.gl_limit_aggregate = page.field("GL limit aggregate", money(case.gl_limits[1]), case.gl_limits[1])
    form.application_date = field_date(page, "Application date", case.application_date, FORM_DATE_STYLE)
    form.proposed_effective_date = field_date(page, "Proposed effective date", case.effective_date, FORM_DATE_STYLE)
    section(page, "Section 3 - Values and exposures")
    form.stated_tiv = page.field("Total insured value (all locations)", money(case.stated_tiv), case.stated_tiv)
    form.annual_revenue = page.field("Annual revenue", money(case.annual_revenue), case.annual_revenue)
    form.annual_payroll = page.field("Annual payroll", money(case.annual_payroll), case.annual_payroll)
    form.subcontracted_work_pct = page.field(
        "Subcontracted work", percent(case.subcontracted_pct), case.subcontracted_pct
    )
    form.has_subcontractor_coi_tracking = page.field(
        "Subcontractor certificates tracked", yes_no(case.coi_tracking), case.coi_tracking
    )
    form.foreign_products = page.field("Foreign products sold", yes_no(case.foreign_products), case.foreign_products)
    section(page, "Section 4 - Loss history (5 years and current term)")
    form.stated_claim_count = page.field("Number of claims", str(case.stated_claim_count), case.stated_claim_count)
    form.stated_total_incurred = page.field(
        "Total incurred losses", money(case.stated_total_incurred), case.stated_total_incurred
    )


def prior_carriers_table(page: PageWriter, case: InsuranceCase) -> list[PriorCarrier]:
    """Carrier | Policy number | Policy year start | Property premium | GL premium, one row per policy year."""
    table_header(
        page,
        [
            ("Carrier", 54.0, "left"),
            ("Policy number", 230.0, "left"),
            ("Policy year start", 305.0, "left"),
            ("Property premium", 480.0, "right"),
            ("GL premium", 558.0, "right"),
        ],
    )
    rows = []
    for run in case.runs:
        periods = [(year.start, year.property_premium, year.gl_premium) for year in run.years]
        if run.current_term:
            periods.append((case.current_term_start, *run.current_premiums))
        for start, property_premium, gl_premium in periods:
            printed, positions = table_row(
                page,
                [
                    Cell("carrier", run.carrier, 54.0, max_width=170.0),
                    Cell("policy", run.policy_number, 230.0),
                    Cell("start", format_date(start, FORM_DATE_STYLE), 305.0),
                    Cell("property", "n/a" if property_premium is None else money(property_premium), 480.0, "right"),
                    Cell("gl", "n/a" if gl_premium is None else money(gl_premium), 558.0, "right"),
                ],
            )
            entry = PriorCarrier(
                carrier=cite_named(page, printed, positions, "carrier", run.carrier),
                policy_number=cite_named(page, printed, positions, "policy", run.policy_number),
                policy_year_start=cite_named(page, printed, positions, "start", start),
            )
            if property_premium is not None:
                entry.property_premium = cite_named(page, printed, positions, "property", property_premium)
            if gl_premium is not None:
                entry.gl_premium = cite_named(page, printed, positions, "gl", gl_premium)
            rows.append(entry)
    return rows


def render_insurance_application(writer: DocumentWriter, case: InsuranceCase) -> InsuranceApplicationForm:
    """The two-page commercial lines application."""
    form = InsuranceApplicationForm(doc_id=writer.doc_id)
    application_page_one(writer.new_page(), case, form)
    page = writer.new_page()
    section(page, "Section 5 - Prior carriers")
    form.prior_carriers = prior_carriers_table(page, case)
    section(page, "Section 6 - Owners")
    form.owners = owner_table(page, case.owners)
    section(page, "Section 7 - Remarks")
    form.remarks = page.cite_lines(page.wrapped(case.remarks, REMARKS_WIDTH), case.remarks)
    section(page, "Section 8 - Signature")
    note(page, "The applicant confirms the information in this application is true and complete.", size=9.0)
    page.row(
        [
            Segment(f"Signature: /s/ {case.signer}", LEFT_MARGIN),
            Segment(f"Date: {format_date(case.application_date, FORM_DATE_STYLE)}", 380.0),
        ]
    )
    return form


SCHEDULE_COLUMNS: tuple[tuple[str, str, float, str], ...] = (
    ("location_id", "Loc", 54.0, "left"),
    ("construction_class", "Construction", 80.0, "left"),
    ("occupancy", "Occupancy", 182.0, "left"),
    ("year_built", "Built", 318.0, "right"),
    ("roof_year", "Roof", 350.0, "right"),
    ("stories", "Stories", 385.0, "right"),
    ("square_feet", "Sq ft", 430.0, "right"),
    ("sprinklered", "Sprk", 440.0, "left"),
    ("protection_class", "PC", 482.0, "right"),
    ("building_value", "Building", 548.0, "right"),
    ("contents_value", "Contents", 612.0, "right"),
    ("business_income_value", "Bus. income", 676.0, "right"),
    ("tiv", "TIV", 738.0, "right"),
)


def location_rows(page: PageWriter, location: LocationData) -> ScheduleLocation:
    """Two printed rows per location: the values, then the address."""
    texts = {
        "location_id": location.location_id,
        "construction_class": label(location.construction_class),
        "occupancy": location.occupancy,
        "year_built": str(location.year_built),
        "roof_year": str(location.roof_year),
        "stories": str(location.stories),
        "square_feet": f"{location.square_feet:,}",
        "sprinklered": yes_no(location.sprinklered),
        "protection_class": str(location.protection_class),
        "building_value": money(location.building_value, symbol=False),
        "contents_value": money(location.contents_value, symbol=False),
        "business_income_value": money(location.business_income_value, symbol=False),
        "tiv": money(location.tiv, symbol=False),
    }
    widths = {"construction_class": 98.0, "occupancy": 110.0}
    cells = [Cell(name, texts[name], x, align, widths.get(name)) for name, _, x, align in SCHEDULE_COLUMNS]
    row, index = table_row(page, cells, size=7.5)
    values = {
        "location_id": location.location_id,
        "construction_class": location.construction_class,
        "occupancy": location.occupancy,
        "year_built": location.year_built,
        "roof_year": location.roof_year,
        "stories": location.stories,
        "square_feet": location.square_feet,
        "sprinklered": location.sprinklered,
        "protection_class": location.protection_class,
        "building_value": location.building_value,
        "contents_value": location.contents_value,
        "business_income_value": location.business_income_value,
        "tiv": location.tiv,
    }
    result = ScheduleLocation(**{name: cite_named(page, row, index, name, value) for name, value in values.items()})
    address = page.row(
        [Segment("Address:", 80.0, size=7.5, font="Helvetica-Bold"), Segment(location.address, 120.0, size=7.5)]
    )
    result.address = page.cite(address, 1, location.address)
    return result


def render_property_schedule(writer: DocumentWriter, case: InsuranceCase) -> PropertySchedule:
    """Statement of values (landscape): one entry per location, total TIV, signature and date."""
    page = writer.new_page()
    heading(page, "PROPERTY SCHEDULE / STATEMENT OF VALUES (SYNTHETIC)")
    note(page, "All values at replacement cost. Prepared by the applicant for Harborline Mutual (fictional).")
    insured = case.name_on("property_schedule")
    result = PropertySchedule(doc_id=writer.doc_id)
    result.insured_name = page.field("Insured", insured, insured)
    page.skip(6)
    page.row(
        [Segment(title, x, size=7.5, font="Helvetica-Bold", align=align) for _, title, x, align in SCHEDULE_COLUMNS]
    )
    page.hline(3.0)
    for location in case.locations:
        if page.space_left() < 40:
            page = writer.new_page()
        result.locations.append(location_rows(page, location))
    page.skip(6)
    total = case.schedule_tiv()
    result.total_tiv = page.field("Total TIV (all locations)", money(total), total)
    page.skip(10)
    signed = page.row(
        [
            Segment(f"Signed: /s/ {case.signer}", LEFT_MARGIN),
            Segment("Date signed", 380.0, font="Helvetica-Bold"),
            Segment(format_date(case.schedule_signed, "iso"), 460.0),
        ]
    )
    result.signed_date = page.cite(signed, 2, case.schedule_signed)
    return result


def render_cover_email(writer: DocumentWriter, case: InsuranceCase) -> CoverEmail:
    """The broker's cover email (printed to PDF). The body is untrusted free text."""
    page = writer.new_page()
    heading(page, "Email message (SYNTHETIC)", size=10)
    page.skip(4)
    sender = f"{case.broker_name} <{case.broker_name.split()[0].lower()}@broker.example.com>"
    result = CoverEmail(doc_id=writer.doc_id)
    result.sender = page.field("From", sender, sender, value_x=110.0)
    result.recipient = page.field("To", HARBORLINE_INBOX, HARBORLINE_INBOX, value_x=110.0)
    subject = f"New submission: {case.named_insured} - effective {case.effective_date.isoformat()}"
    result.subject = page.field("Subject", subject, subject, value_x=110.0)
    sent = case.application_date
    result.sent_date = page.field("Date", format_date(sent, "iso"), sent, value_x=110.0)
    page.hline(4.0)
    page.skip(8)
    rows = []
    for paragraph in case.email_paragraphs:
        rows.extend(page.wrapped(paragraph, REMARKS_WIDTH))
        page.skip(6)
    body = " ".join(row.text for row in rows)
    result.body = page.cite_lines(rows, body)
    return result


def render_ownership_statement(writer: DocumentWriter, case: InsuranceCase) -> OwnershipStatement:
    """Beneficial ownership statement (HM-3.1 item 6)."""
    page = writer.new_page()
    heading(page, "BENEFICIAL OWNERSHIP STATEMENT (SYNTHETIC)")
    note(page, "Every individual owning 25% or more of the named insured, and the control person.")
    page.skip(4)
    name = case.name_on("ownership_statement")
    result = OwnershipStatement(doc_id=writer.doc_id)
    result.entity_name = page.field("Entity name", name, name)
    page.skip(6)
    result.owners = owner_table(page, case.statement_members)
    page.skip(10)
    note(page, "I certify that this ownership information is true and complete.", size=9.0)
    page.row(
        [
            Segment(f"Signature: /s/ {case.signer}", LEFT_MARGIN),
            Segment(f"Date: {case.application_date.isoformat()}", 380.0),
        ]
    )
    return result


def render_inspection_report(writer: DocumentWriter, case: InsuranceCase, location: LocationData) -> InspectionReport:
    """Loss control inspection report for a location with TIV above $5,000,000."""
    page = writer.new_page()
    heading(page, f"{case.inspector} (fictional)")
    note(page, "Loss control inspection report - SYNTHETIC, for demonstration only")
    page.skip(4)
    page.field("Insured", case.named_insured, case.named_insured)
    result = InspectionReport(doc_id=writer.doc_id)
    result.location_id = page.field("Location", location.location_id, location.location_id)
    page.field("Address", location.address, location.address)
    result.inspection_date = field_date(page, "Inspection date", case.inspections[location.location_id], "iso")
    section(page, "Findings")
    page.wrapped(
        f"{label(location.construction_class)} building of {location.stories} stories, built {location.year_built}. "
        f"Sprinklered: {yes_no(location.sprinklered)}. No critical deficiencies were noted at the time of the visit.",
        REMARKS_WIDTH,
    )
    return result

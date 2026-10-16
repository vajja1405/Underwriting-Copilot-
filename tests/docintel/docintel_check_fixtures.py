"""Hand-built SYNTHETIC extraction fixtures for the document-check tests (no generator, no ground truth).

Each builder returns a plain dict in the LoanPacket / InsuranceSubmission schema, so a test can change one value
and validate it (`LoanPacket.model_validate(data)`). A Pager hands out citations: every value gets the next line
on the current page, so every value in a fixture is cited (doc_id, page, line).

All names, banks, lenders and carriers are invented for the tests. The clean credit packet verifies to revenue of
$504,000.00: each month has $40,000.00 of card and ACH revenue and $2,000.00 of cash (under the NBC-3.3 cap).
"""

from datetime import date, timedelta
from typing import Any

from uwcopilot.docintel.schemas import InsuranceSubmission, LoanPacket

BUSINESS = "Harbor Lane Bakery LLC"
FEIN = "12-3456789"
APPLICATION_DATE = date(2026, 9, 1)
MONTHS = [
    "2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01",
    "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07",
]  # fmt: skip
OPENING_BALANCE = 25_000.0
LENDER = "Sample Lender A"
SCHEDULED_PAYMENT = 1_250.0
# (day, description, deposit, withdrawal) rows of every clean month.
BASE_ROWS: tuple[tuple[int, str, float | None, float | None], ...] = (
    (3, "CARD SETTLEMENT TERNWICK", 30_000.0, None),
    (8, "ACH CREDIT ACORN FOODS INV 1001", 10_000.0, None),
    (10, "CASH DEPOSIT", 2_000.0, None),
    (12, "PAYROLL GRIDWORKS", None, 20_000.0),
    (15, f"ACH PMT {LENDER.upper()} LOAN", None, SCHEDULED_PAYMENT),
    (20, "RENT ORCHARD HOLDINGS", None, 15_000.0),
    (25, "CITY UTILITIES", None, 4_000.0),
)

INSURED = "Quayside Example Storage Inc"
INSURANCE_FEIN = "98-7654321"
EFFECTIVE_DATE = date(2026, 11, 1)
CARRIER_ONE = "Example Mutual Carrier One"
CARRIER_TWO = "Example Casualty Carrier Two"


class Pager:
    """Hands out citations for one fixture: new_page() starts a page of a document, cv() cites the next line."""

    def __init__(self) -> None:
        self.page = 0
        self.line = 0
        self.doc_id = ""

    def new_page(self, doc_id: str) -> None:
        """Start the next packet page, belonging to doc_id."""
        self.page += 1
        self.line = 0
        self.doc_id = doc_id

    def cv(self, value: Any) -> dict:
        """A cited value on the next line of the current page."""
        self.line += 1
        top = 40.0 + 14.0 * self.line
        return {
            "value": value,
            "doc_id": self.doc_id,
            "page": self.page,
            "line": self.line,
            "bbox": (72.0, top, 400.0, top + 10.0),
            "confidence": 1.0,
            "source_text": f"label: {value}",
        }


def month_bounds(month: str) -> tuple[date, date]:
    """First and last day of a "YYYY-MM" month."""
    year, number = (int(part) for part in month.split("-"))
    first = date(year, number, 1)
    following = date(year + number // 12, number % 12 + 1, 1)
    return first, following - timedelta(days=1)


def statement(
    pager: Pager,
    doc_id: str,
    month: str,
    opening: float,
    rows: list[tuple[int, str, float | None, float | None]],
    account: str = "004512345678",
) -> tuple[dict, float]:
    """One statement dict with running balances; returns (statement, closing balance)."""
    pager.new_page(doc_id)
    start, end = month_bounds(month)
    header = {
        "doc_id": doc_id,
        "bank_name": pager.cv("Fictional Example Bank (SYNTHETIC)"),
        "account_holder": pager.cv(BUSINESS),
        "account_number": pager.cv(account),
        "period_start": pager.cv(start),
        "period_end": pager.cv(end),
        "opening_balance": pager.cv(opening),
    }
    balance = opening
    transactions = []
    deposits = 0.0
    withdrawals = 0.0
    for day, description, deposit, withdrawal in sorted(rows, key=lambda row: row[0]):
        balance = round(balance + (deposit or 0.0) - (withdrawal or 0.0), 2)
        deposits += deposit or 0.0
        withdrawals += withdrawal or 0.0
        row = {"posted_date": pager.cv(start.replace(day=day)), "description": pager.cv(description)}
        if deposit is not None:
            row["deposit"] = pager.cv(deposit)
        if withdrawal is not None:
            row["withdrawal"] = pager.cv(withdrawal)
        row["balance"] = pager.cv(balance)
        transactions.append(row)
    header["total_deposits"] = pager.cv(round(deposits, 2))
    header["total_withdrawals"] = pager.cv(round(withdrawals, 2))
    header["closing_balance"] = pager.cv(balance)
    header["transactions"] = transactions
    return header, balance


def owner(pager: Pager, name: str, pct: float, control: bool, title: str = "Member") -> dict:
    """An owner row."""
    return {
        "name": pager.cv(name),
        "entity_type": pager.cv("person"),
        "title": pager.cv(title),
        "ownership_pct": pager.cv(pct),
        "is_control_person": pager.cv(control),
        "country": pager.cv("US"),
    }


def credit_packet_data(
    extra_rows: tuple[tuple[int, str, float | None, float | None], ...] = (),
    stated_revenue: float = 520_000.0,
    months: list[str] | None = None,
) -> dict:
    """A clean SYNTHETIC LoanPacket as a dict. extra_rows are added to every month."""
    pager = Pager()
    documents = []
    pager.new_page("D01")
    application = {
        "doc_id": "D01",
        "legal_name": pager.cv(BUSINESS),
        "fein": pager.cv(FEIN),
        "business_structure": pager.cv("LLC"),
        "state": pager.cv("OH"),
        "formation_date": pager.cv(date(2015, 3, 1)),
        "industry": pager.cv("Bakery"),
        "business_address": pager.cv("100 Example Street, Springfield, OH"),
        "application_date": pager.cv(APPLICATION_DATE),
        "requested_amount": pager.cv(150_000.0),
        "product": pager.cv("Term loan"),
        "term_months": pager.cv(60),
        "note_rate_pct": pager.cv(9.5),
        "rate_type": pager.cv("Fixed"),
        "purpose": pager.cv("Working capital"),
        "stated_annual_revenue": pager.cv(stated_revenue),
        "largest_customer_revenue_pct": pager.cv(12.0),
        "tax_lien_unresolved": pager.cv(False),
    }
    pager.new_page("D01")
    application["owners"] = [
        owner(pager, "Dana Whitfield", 60.0, True, "Managing member"),
        owner(pager, "Luis Ortega", 40.0, False),
    ]
    application["remarks"] = pager.cv("Funds will expand the retail counter.")
    documents.append({"doc_id": "D01", "doc_type": "loan_application", "pages": [1, 2]})
    statements = []
    balance = OPENING_BALANCE
    for index, month in enumerate(months or MONTHS):
        doc_id = f"D{index + 2:02d}"
        item, balance = statement(pager, doc_id, month, balance, [*BASE_ROWS, *extra_rows])
        statements.append(item)
        documents.append({"doc_id": doc_id, "doc_type": "bank_statement", "pages": [pager.page]})
    number = len(statements) + 2

    def next_doc(doc_type: str) -> str:
        nonlocal number
        doc_id = f"D{number:02d}"
        number += 1
        pager.new_page(doc_id)
        documents.append({"doc_id": doc_id, "doc_type": doc_type, "pages": [pager.page]})
        return doc_id

    pnl_id = next_doc("profit_and_loss")
    pnl = {
        "doc_id": pnl_id,
        "business_name": pager.cv(BUSINESS),
        "period_end": pager.cv(date(2026, 7, 31)),
        "revenue": pager.cv(506_000.0),
        "cost_of_goods_sold": pager.cv(200_000.0),
        "operating_expenses": pager.cv(226_000.0),
        "ebitda": pager.cv(80_000.0),
        "depreciation_amortization": pager.cv(10_000.0),
        "interest_expense": pager.cv(4_000.0),
        "net_income": pager.cv(66_000.0),
    }
    balance_id = next_doc("balance_sheet")
    balance_sheet = {
        "doc_id": balance_id,
        "business_name": pager.cv(BUSINESS),
        "as_of": pager.cv(date(2026, 7, 31)),
        "cash": pager.cv(40_000.0),
        "total_assets": pager.cv(300_000.0),
        "total_liabilities": pager.cv(120_000.0),
        "equity": pager.cv(180_000.0),
    }
    debt_id = next_doc("debt_schedule")
    debt_schedule = {
        "doc_id": debt_id,
        "business_name": pager.cv(BUSINESS),
        "items": [
            {
                "lender": pager.cv(LENDER),
                "debt_type": pager.cv("Term loan"),
                "balance": pager.cv(40_000.0),
                "monthly_payment": pager.cv(SCHEDULED_PAYMENT),
                "maturity": pager.cv(date(2028, 6, 30)),
                "to_be_refinanced": pager.cv(False),
            }
        ],
        "total_balance": pager.cv(40_000.0),
        "total_monthly_payment": pager.cv(SCHEDULED_PAYMENT),
    }
    photo_ids = []
    for name, born in (("Dana Whitfield", date(1980, 4, 2)), ("Luis Ortega", date(1977, 11, 19))):
        id_doc = next_doc("photo_id")
        photo_ids.append(
            {
                "doc_id": id_doc,
                "name": pager.cv(name),
                "date_of_birth": pager.cv(born),
                "id_number": pager.cv(f"S{born.year}0001"),
                "expiry_date": pager.cv(date(2029, 1, 1)),
                "address": pager.cv("1 Sample Road, Springfield, OH"),
            }
        )
    report_id = next_doc("credit_report")
    credit_report = {
        "doc_id": report_id,
        "entries": [
            {
                "name": pager.cv("Dana Whitfield"),
                "credit_score": pager.cv(742),
                "bankruptcy_within_7_years": pager.cv(False),
            },
            {
                "name": pager.cv("Luis Ortega"),
                "credit_score": pager.cv(705),
                "bankruptcy_within_7_years": pager.cv(False),
            },
        ],
    }
    formation_id = next_doc("formation_document")
    formation = {
        "doc_id": formation_id,
        "entity_name": pager.cv(BUSINESS),
        "fein": pager.cv(FEIN),
        "state": pager.cv("OH"),
        "formation_date": pager.cv(date(2015, 3, 1)),
        "members": [
            owner(pager, "Dana Whitfield", 60.0, True, "Managing member"),
            owner(pager, "Luis Ortega", 40.0, False),
        ],
    }
    tax_id = next_doc("tax_return_summary")
    tax_returns = [
        {
            "doc_id": tax_id,
            "business_name": pager.cv(BUSINESS),
            "fein": pager.cv(FEIN),
            "tax_year": pager.cv(year),
            "gross_receipts": pager.cv(receipts),
        }
        for year, receipts in ((2025, 498_000.0), (2024, 470_000.0))
    ]
    data = {
        "packet_id": "PKT-C-901",
        "line": "credit",
        "documents": [{**item, "ocr_used": False, "classification_confidence": 1.0} for item in documents],
        "application": application,
        "statements": statements,
        "profit_and_loss": pnl,
        "balance_sheet": balance_sheet,
        "debt_schedule": debt_schedule,
        "photo_ids": photo_ids,
        "credit_report": credit_report,
        "formation": formation,
        "tax_returns": tax_returns,
    }
    LoanPacket.model_validate(data)
    return data


def claim(pager: Pager, number: str, loss: date, line: str, paid: float, reserve: float, incurred: float) -> dict:
    """One loss-run claim line."""
    return {
        "claim_number": pager.cv(number),
        "date_of_loss": pager.cv(loss),
        "line": pager.cv(line),
        "status": pager.cv("Closed" if reserve == 0 else "Open"),
        "paid": pager.cv(paid),
        "reserve": pager.cv(reserve),
        "incurred": pager.cv(incurred),
    }


def loss_run(pager: Pager, doc_id: str, carrier: str, years: list[int], claims: list[dict], valued: date) -> dict:
    """A carrier loss run whose summary equals its claim lines."""
    run = {
        "doc_id": doc_id,
        "carrier": pager.cv(carrier),
        "policy_number": pager.cv(f"{carrier[:3].upper()}-55501"),
        "insured_name": pager.cv(INSURED),
        "valued_as_of": pager.cv(valued),
        "carrier_produced": pager.cv(True),
        "years": [
            {
                "policy_year_start": pager.cv(date(year, 11, 1)),
                "property_earned_premium": pager.cv(9_000.0),
                "gl_earned_premium": pager.cv(4_000.0),
            }
            for year in years
        ],
        "claims": claims,
    }
    run["summary"] = {
        "claim_count": pager.cv(len(claims)),
        "total_paid": pager.cv(sum(item["paid"]["value"] for item in claims)),
        "total_reserve": pager.cv(sum(item["reserve"]["value"] for item in claims)),
        "total_incurred": pager.cv(sum(item["incurred"]["value"] for item in claims)),
    }
    return run


def insurance_submission_data(cover_body: str = "Attached are the application and supporting documents.") -> dict:
    """A clean SYNTHETIC InsuranceSubmission as a dict (package coverage, two carriers, one location)."""
    pager = Pager()
    pager.new_page("D01")
    application = {
        "doc_id": "D01",
        "named_insured": pager.cv(INSURED),
        "fein": pager.cv(INSURANCE_FEIN),
        "years_in_business": pager.cv(12),
        "business_class": pager.cv("Warehouse"),
        "coverage": pager.cv("Package"),
        "gl_limit_per_occurrence": pager.cv(1_000_000.0),
        "gl_limit_aggregate": pager.cv(2_000_000.0),
        "stated_tiv": pager.cv(3_000_000.0),
        "annual_revenue": pager.cv(2_400_000.0),
        "annual_payroll": pager.cv(600_000.0),
        "subcontracted_work_pct": pager.cv(0.0),
        "has_subcontractor_coi_tracking": pager.cv(False),
        "foreign_products": pager.cv(False),
        "application_date": pager.cv(date(2026, 9, 1)),
        "proposed_effective_date": pager.cv(EFFECTIVE_DATE),
        "stated_claim_count": pager.cv(2),
        "stated_total_incurred": pager.cv(30_000.0),
    }
    pager.new_page("D01")
    application["prior_carriers"] = [
        {
            "carrier": pager.cv(carrier),
            "policy_number": pager.cv(f"{carrier[:3].upper()}-55501"),
            "policy_year_start": pager.cv(date(year, 11, 1)),
            "property_premium": pager.cv(9_000.0),
            "gl_premium": pager.cv(4_000.0),
        }
        for carrier, years in ((CARRIER_ONE, (2020, 2021, 2022)), (CARRIER_TWO, (2023, 2024, 2025)))
        for year in years
    ]
    application["owners"] = [
        owner(pager, "Morgan Reyes", 70.0, True, "President"),
        owner(pager, "Priya Natarajan", 30.0, False, "Vice president"),
    ]
    application["remarks"] = pager.cv("Sprinklered warehouse; no hazardous storage.")
    pager.new_page("D02")
    first_claims = [claim(pager, "C1-001", date(2021, 5, 10), "property", 10_000.0, 5_000.0, 15_000.0)]
    pager.new_page("D02")
    run_one = loss_run(pager, "D02", CARRIER_ONE, [2020, 2021, 2022], first_claims, date(2026, 9, 15))
    pager.new_page("D03")
    second_claims = [claim(pager, "C2-001", date(2024, 3, 1), "general_liability", 15_000.0, 0.0, 15_000.0)]
    run_two = loss_run(pager, "D03", CARRIER_TWO, [2023, 2024], second_claims, date(2026, 9, 20))
    pager.new_page("D04")
    schedule = {
        "doc_id": "D04",
        "insured_name": pager.cv(INSURED),
        "locations": [
            {
                "location_id": pager.cv("L1"),
                "address": pager.cv("200 Example Wharf Road, Portside, ME"),
                "construction_class": pager.cv("Masonry non-combustible"),
                "occupancy": pager.cv("Warehouse"),
                "year_built": pager.cv(1998),
                "roof_year": pager.cv(2019),
                "stories": pager.cv(1),
                "square_feet": pager.cv(9_000),
                "sprinklered": pager.cv(True),
                "protection_class": pager.cv(3),
                "building_value": pager.cv(2_000_000.0),
                "contents_value": pager.cv(700_000.0),
                "business_income_value": pager.cv(300_000.0),
                "tiv": pager.cv(3_000_000.0),
            }
        ],
        "total_tiv": pager.cv(3_000_000.0),
        "signed_date": pager.cv(date(2026, 8, 20)),
    }
    pager.new_page("D05")
    cover_email = {
        "doc_id": "D05",
        "sender": pager.cv("broker@example.com"),
        "recipient": pager.cv("submissions@example.com"),
        "subject": pager.cv("Submission for review"),
        "sent_date": pager.cv(date(2026, 9, 1)),
        "body": pager.cv(cover_body),
    }
    pager.new_page("D06")
    ownership = {
        "doc_id": "D06",
        "entity_name": pager.cv(INSURED),
        "owners": [
            owner(pager, "Morgan Reyes", 70.0, True, "President"),
            owner(pager, "Priya Natarajan", 30.0, False, "Vice president"),
        ],
    }
    documents = [
        ("D01", "insurance_application", [1, 2]),
        ("D02", "loss_run", [3, 4]),
        ("D03", "loss_run", [5]),
        ("D04", "property_schedule", [6]),
        ("D05", "cover_email", [7]),
        ("D06", "ownership_statement", [8]),
    ]
    data = {
        "packet_id": "PKT-I-901",
        "line": "insurance",
        "documents": [
            {
                "doc_id": doc_id,
                "doc_type": doc_type,
                "pages": pages,
                "ocr_used": False,
                "classification_confidence": 1.0,
            }
            for doc_id, doc_type, pages in documents
        ],
        "application": application,
        "loss_runs": [run_one, run_two],
        "property_schedule": schedule,
        "cover_email": cover_email,
        "ownership_statement": ownership,
    }
    InsuranceSubmission.model_validate(data)
    return data


def loan(data: dict) -> LoanPacket:
    """Validate a credit fixture dict."""
    return LoanPacket.model_validate(data)


def submission(data: dict) -> InsuranceSubmission:
    """Validate an insurance fixture dict."""
    return InsuranceSubmission.model_validate(data)


def set_value(cited: dict, value: Any) -> None:
    """Change the value of one cited dict in place (its citation stays)."""
    cited["value"] = value

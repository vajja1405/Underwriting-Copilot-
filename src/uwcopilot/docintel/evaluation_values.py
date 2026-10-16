"""How the evaluation compares and finds values: field types, normalization and printed forms (Phase 2).

field_type maps a field path to one of FIELD_TYPES by its last part; normalize_value is how two values are compared
(amounts to cents, text casefolded with single spaces, identifiers without spaces); printed_forms and value_on_page
find a value in a page's printed text lines (amounts with thousands separators, three date styles, Yes/No).
evaluation.py re-exports these names.
"""

import re
from datetime import date
from typing import Any

# fmt: off
FIELD_TYPES: dict[str, tuple[str, ...]] = {
    "money": (
        "accounts_receivable", "annual_payroll", "annual_revenue", "balance", "building_value",
        "business_income_value", "cash", "closing_balance", "contents_value", "cost_of_goods_sold", "deposit",
        "depreciation_amortization", "ebitda", "equity", "gl_earned_premium", "gl_limit_aggregate",
        "gl_limit_per_occurrence", "gl_premium", "gross_receipts", "incurred", "interest_expense", "inventory",
        "long_term_debt", "monthly_payment", "net_income", "opening_balance", "operating_expenses", "paid",
        "property_earned_premium", "property_premium", "requested_amount", "reserve", "revenue",
        "stated_annual_revenue", "stated_tiv", "stated_total_incurred", "tiv", "total_assets", "total_balance",
        "total_deposits", "total_incurred", "total_liabilities", "total_monthly_payment", "total_paid",
        "total_reserve", "total_tiv", "total_withdrawals", "value", "withdrawal",
    ),
    "percent": ("largest_customer_revenue_pct", "note_rate_pct", "ownership_pct", "subcontracted_work_pct"),
    "date": (
        "application_date", "as_of", "date_of_birth", "date_of_loss", "expiry_date", "formation_date",
        "inspection_date", "maturity", "period_end", "period_start", "policy_year_start", "posted_date",
        "proposed_effective_date", "sent_date", "signed_date", "valuation_date", "valued_as_of",
    ),
    "identifier": ("account_number", "claim_number", "fein", "id_number", "location_id", "policy_number"),
    "name": (
        "account_holder", "bank_name", "business_name", "carrier", "entity_name", "insured_name", "legal_name",
        "lender", "name", "named_insured", "recipient", "sender",
    ),
    "code": (
        "business_class", "business_structure", "collateral_type", "construction_class", "country", "coverage",
        "debt_type", "entity_type", "industry", "line", "occupancy", "product", "purpose", "rate_type", "state",
        "status", "title",
    ),
    "integer": (
        "claim_count", "credit_score", "protection_class", "roof_year", "square_feet", "stated_claim_count",
        "stories", "tax_year", "term_months", "year_built", "years_in_business",
    ),
    "boolean": (
        "bankruptcy_within_7_years", "carrier_produced", "foreign_products", "has_subcontractor_coi_tracking",
        "is_control_person", "sprinklered", "tax_lien_unresolved", "to_be_refinanced",
    ),
    "free_text": ("address", "body", "business_address", "description", "remarks", "subject"),
}
# fmt: on
TYPE_OF_LEAF: dict[str, str] = {leaf: kind for kind, leaves in FIELD_TYPES.items() for leaf in leaves}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# Booleans printed as a phrase rather than Yes/No (the same phrases the generator's own test accepts).
BOOLEAN_PHRASES: dict[str, str] = {"is_control_person": "control person", "carrier_produced": "carrier-produced"}


def field_type(field: str) -> str:
    """The type of a field path from its last part ("statement.transactions.deposit" -> "money")."""
    return TYPE_OF_LEAF.get(field.rsplit(".", 1)[-1], "free_text")


def normalize_value(kind: str, value: Any) -> Any:
    """A value as the evaluation compares it: amounts to cents, text casefolded with single spaces.

    Identifiers also lose every space ("BWC 20-1" == "BWC20-1"); dates are compared as ISO strings.
    """
    if value is None:
        return None
    if kind == "boolean":
        return bool(value)
    if kind in ("money", "percent"):
        return round(float(value), 2)
    if kind == "integer":
        return int(value)
    text = " ".join(str(value).split()).casefold()
    if kind == "identifier":
        return text.replace(" ", "")
    return text


def printed_forms(field: str, value: Any) -> list[str]:
    """Ways a true value can be printed on a page (casefolded): amounts, percents, three date styles, labels."""
    kind = field_type(field)
    leaf = field.rsplit(".", 1)[-1]
    if kind == "boolean":
        forms = ["yes" if value else "no"]
        return forms + ([BOOLEAN_PHRASES[leaf]] if leaf in BOOLEAN_PHRASES else [])
    if kind == "integer":
        return [str(value), f"{int(value):,}"]
    if kind == "money":
        return [f"{abs(float(value)):,.2f}"]
    if kind == "percent":
        return [f"{float(value):.1f}%", f"{float(value):.2f}%"]
    text = str(value)
    if kind == "date" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = date.fromisoformat(text)
        styles = [
            text,
            f"{day.month:02d}/{day.day:02d}/{day.year:04d}",
            f"{day.day:02d}-{MONTHS[day.month - 1]}-{day.year}",
        ]
        return [style.casefold() for style in styles]
    return list(dict.fromkeys([text.casefold(), text.replace("_", " ").casefold()]))


def value_on_page(field: str, value: Any, lines: list[str]) -> bool:
    """True when a printed form of the value is on one of the lines (or across lines) as a whole token.

    "Whole token" means the characters around the match are not letters or digits, so "US" is not found inside
    "BUSINESS" and "no" is not found inside "Account no".
    """
    texts = [line.casefold() for line in lines]
    texts.append(" ".join(texts))
    for form in printed_forms(field, value):
        pattern = re.compile(r"(?<![0-9a-z])" + re.escape(form) + r"(?![0-9a-z])")
        if any(pattern.search(text) for text in texts):
            return True
    return False

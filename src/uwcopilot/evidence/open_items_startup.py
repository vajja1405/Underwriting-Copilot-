"""Open items of a STARTUP file (SYNTHETIC Northwind startup program): the evidence a founder package still lacks.

detect_open_items (open_items.py) calls startup_items for an application on line "startup_credit". Each item is an
UNKNOWN: the figure is stated (by the founder, or as a planning assumption) but the document that would back it is
not in the file. The program counts a flag only when its document is listed in documents_provided (NSP-1.3), so
every check below reads the flag AND the document, as policy_startup.py does:

- missing_equity_verification (NSP-3.1): owner cash equity above zero that is not marked as verified by bank
  statements, or marked verified without equity_bank_statements in the file -> request equity_bank_statements;
- missing_experience_evidence (NSP-2.1): the policy found experience_evidence_missing (a principal claims 24+ months
  of experience, but no resume and references back it) -> request owner_resume_and_references for that principal;
- unsupported_material_assumption (NSP-6.1): one item per top driver (tornado rank 1, 2, 3) that is not backed; the
  request follows the driver's name code (rent or lease -> a lease or letter of intent; build-out, equipment, capex
  or repair -> a quote; anything else -> comparable data with its source);
- missing_quotes (NSP-1.2 supporting document, NSP-3.2 uses of funds): a build-out or equipment use above zero
  without contractor_or_equipment_quotes in the file -> request build_out_quotes.
Requests and resolution templates: config/studio/startup_evidence_requests.yaml (evidence/startup_requests.py).

An item's detail is built from codes, ranks and indexes only. The founder's free text (the assumption sources, the
business name, the notes) is never read.
"""

from uwcopilot.agent.applications import StartupCreditApplication, parse_application
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.agent.policy_startup import (
    EQUITY_STATEMENTS_DOCUMENT,
    MIN_EXPERIENCE_MONTHS,
    document_listed,
    experience_months,
    is_backed,
    startup_principals,
    top_drivers,
)
from uwcopilot.evidence.open_item_models import OpenItem
from uwcopilot.evidence.state import CaseState

QUOTES_DOCUMENT = "contractor_or_equipment_quotes"
EXPERIENCE_FINDING = "experience_evidence_missing"
# Planning assumptions of the resolution templates (config/studio/startup_evidence_requests.yaml).
EQUITY_SHOWN_SHARE = 0.75
CAPEX_QUOTE_FACTOR = 1.10
EXPERIENCE_SHOWN_SHARE = 0.5
STARTUP_PROBLEM_TITLES: dict[str, str] = {
    "missing_equity_verification": "Owner cash equity is not verified by bank statements",
    "missing_experience_evidence": "Claimed owner experience has no resume and references",
    "unsupported_material_assumption": "A top driver of the projection has no supporting document",
    "missing_quotes": "The build-out and equipment cost has no quote",
}
STARTUP_PROBLEM_TYPES: tuple[str, ...] = tuple(STARTUP_PROBLEM_TITLES)
# Driver name keywords -> the request that brings the right supporting document (checked in this order).
DRIVER_REQUESTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("rent", "lease"), "driver_lease_or_loi"),
    (("capex", "build_out", "equipment", "repair"), "driver_quote"),
)
DEFAULT_DRIVER_REQUEST = "driver_comparable_data"
DRIVER_DOCUMENTS: dict[str, str] = {
    "driver_lease_or_loi": "lease_or_letter_of_intent",
    "driver_quote": "contractor_or_equipment_quotes",
    "driver_comparable_data": "comparable_data_with_source",
}


def cents(value: float) -> float:
    """A dollar amount rounded to the cent."""
    return round(value, 2)


def startup_item(problem_type: str, qualifier: str = "", **fields: object) -> OpenItem:
    """An UNKNOWN OpenItem "OI-<problem_type>[-<qualifier>]" with the startup title."""
    item_id = f"OI-{problem_type}" + (f"-{qualifier}" if qualifier else "")
    return OpenItem(
        item_id=item_id,
        problem_type=problem_type,
        status="UNKNOWN",
        title=STARTUP_PROBLEM_TITLES[problem_type],
        template_key=problem_type,
        **fields,  # type: ignore[arg-type]
    )


def equity_items(app: StartupCreditApplication) -> list[OpenItem]:
    """NSP-3.1: equity not shown by bank statements in the file."""
    funds = app.sources_and_uses
    equity = funds.sources.owner_cash_equity
    verified = funds.equity_verified_by_statements and document_listed(app, EQUITY_STATEMENTS_DOCUMENT)
    if equity <= 0 or verified:
        return []
    reserve = funds.uses.working_capital_reserve
    shortfall = cents(equity * (1 - EQUITY_SHOWN_SHARE))
    reserve_after = cents(reserve - shortfall)
    flag = (
        "marked verified without the statements in the file" if funds.equity_verified_by_statements else "not verified"
    )
    return [
        startup_item(
            "missing_equity_verification",
            request_id="equity_bank_statements",
            placeholders={
                "equity_shown_less": cents(equity - shortfall),
                "reserve_after_less_equity": reserve_after if reserve_after >= 0 else None,
            },
            claim_ids=("APP-sources_and_uses", "APP-documents_provided", "CALC-startup_equity_injection_pct"),
            detail=f"owner cash equity is {flag} (NSP-3.1)",
        )
    ]


def experience_items(app: StartupCreditApplication, result: PolicyResult) -> list[OpenItem]:
    """NSP-2.1: claimed qualifying experience without a resume and references (the policy's own finding)."""
    if EXPERIENCE_FINDING not in {finding.rule for finding in result.findings}:
        return []
    qualified = [owner for owner in startup_principals(app) if experience_months(owner) >= MIN_EXPERIENCE_MONTHS]
    if not qualified:
        return []
    best = max(qualified, key=experience_months)
    index = app.owners.index(best)
    return [
        startup_item(
            "missing_experience_evidence",
            best.owner_id,
            request_id="owner_resume_and_references",
            request_params={"owner": best.owner_id},
            placeholders={
                "owner_index": index,
                "experience_years_half": round(best.years_relevant_experience * EXPERIENCE_SHOWN_SHARE, 4),
            },
            claim_ids=("APP-owners", "APP-documents_provided", "CALC-startup_max_principal_experience_months"),
            detail=f"principal {best.owner_id} claims 24 or more months of experience without the evidence (NSP-2.1)",
        )
    ]


def driver_request(name: str) -> str:
    """The request that brings the right supporting document for a driver's name code (module docstring)."""
    for keywords, request_id in DRIVER_REQUESTS:
        if any(keyword in name for keyword in keywords):
            return request_id
    return DEFAULT_DRIVER_REQUEST


def driver_items(app: StartupCreditApplication) -> list[OpenItem]:
    """NSP-6.1: one item per top driver (ranks 1-3) that is not backed."""
    items = []
    for driver in top_drivers(app):
        if is_backed(driver, app):
            continue
        request_id = driver_request(driver.name)
        rank = str(driver.tornado_rank)
        items.append(
            startup_item(
                "unsupported_material_assumption",
                f"r{rank}",
                request_id=request_id,
                request_params={"driver": rank},
                placeholders={
                    "assumption_index": app.material_assumptions.index(driver),
                    "document": DRIVER_DOCUMENTS[request_id],
                },
                claim_ids=("APP-material_assumptions", "APP-documents_provided"),
                detail=f"top driver rank {rank} ({driver.name}, status {driver.status}) is not backed (NSP-6.1)",
            )
        )
    return items


def quote_items(app: StartupCreditApplication) -> list[OpenItem]:
    """A build-out or equipment cost without contractor or equipment quotes in the file."""
    uses = app.sources_and_uses.uses
    if document_listed(app, QUOTES_DOCUMENT):
        return []
    field = "build_out" if uses.build_out > 0 else "equipment"
    amount = getattr(uses, field)
    if amount <= 0:
        return []
    higher = cents(amount * CAPEX_QUOTE_FACTOR)
    reserve_after = cents(uses.working_capital_reserve - (higher - amount))
    return [
        startup_item(
            "missing_quotes",
            request_id="build_out_quotes",
            placeholders={
                "capex_field": field,
                "capex_higher": higher,
                "reserve_after_higher_capex": reserve_after if reserve_after >= 0 else None,
            },
            claim_ids=("APP-sources_and_uses", "APP-documents_provided"),
            detail=f"uses of funds list {field} without {QUOTES_DOCUMENT} in the file",
        )
    ]


def startup_items(state: CaseState, result: PolicyResult) -> list[OpenItem]:
    """Every startup open item of the current state (module docstring), in STARTUP_PROBLEM_TYPES order."""
    application = parse_application(state.application)
    if not isinstance(application, StartupCreditApplication):
        return []
    return [
        *equity_items(application),
        *experience_items(application, result),
        *driver_items(application),
        *quote_items(application),
    ]

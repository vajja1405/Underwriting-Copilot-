"""Northwind (credit) document checks over a LoanPacket extraction: documents, identity and tax return vs P&L.

Thresholds come from agent/policy_credit.py; the statement checks are in checks_statements.py and the revenue and
debt-payment checks in checks_revenue.py.

- required_documents (NBC-3.1): which checklist items the packet holds (documents_provided below) against the base
  list plus the conditional items (CPA-reviewed financials above $500,000, collateral documents for secured
  requests or above $250,000, payoff letters for a refinance). No packet document type exists for CPA-reviewed
  financials or payoff letters, so they are always missing when required (values: no_packet_document_type).
  Checklist mapping: signed_application <- loan application; bank_statements_12_months <- 12 consecutive statement
  months; business_tax_returns_2_years <- tax return summaries for two different years; ytd_pnl_and_balance_sheet
  <- P&L and balance sheet, neither dated more than 90 days before the application date (NBC-3.1 item 4; the
  P&L's date is its period end, the balance sheet's its "as of" date; a stale one is listed in
  values["stale_documents"] and does not count); debt_schedule; principal_photo_ids <- a photo ID for every
  principal (NBC-1.2: a person with 20% or more, or the control person) that is not expired on the application
  date (NBC-6.2; an ID whose expiry date was not read still counts, an expired one does not and is listed in
  values["expired_photo_ids"]). For this presence test an ID name matches a principal when the canonical names are
  equal, nearly equal (rapidfuzz ratio of at least ID_NAME_MIN_RATIO: one OCR-misread letter, "Paimer" for
  "Palmer") or equal but for words added at the end (an OCR speck read as "7"); whether the names on the ID and the
  application agree is the identity reviewer's question, not the checklist's. NBC-6.2's name and date-of-birth
  match with the credit report is NOT checked: the application and credit report schemas carry no date of birth.
  formation_documents and
  beneficial_ownership_certification <- the formation document (the second one needs its members table);
  collateral_documents <- collateral valuation. The evidence also cites the application values that make a
  conditional document required (the requested amount, the purpose).
- name_consistency / fein_consistency (NBC-3.1, 6.2, 9.1): the business name on the application, every statement
  (account holder), the P&L, balance sheet, debt schedule, formation document and tax returns; the FEIN on the
  application, formation document and tax returns. Compared with checks_identity.value_consistency.
- owner_consistency (NBC-6.1): application owners vs formation-document members; every member must be listed,
  and the application's percentages must add up to 100%.
- tax_return_vs_pnl (NBC-9.1): the most recent tax return's gross receipts against the P&L revenue "for the same
  year". The summary's tax year is treated as a calendar year, so the check runs only when the P&L period ends on
  31 December of that year; otherwise it is not evaluated. The SYNTHETIC generator always prints a trailing-twelve-
  month P&L ending mid-year, so on the generated packets this check is never evaluated and its detection is NOT
  measured (the evaluation's check_status_counts shows it); only the hand-built tests exercise it.
"""

from rapidfuzz import fuzz

from uwcopilot.agent.credit_calculations import round_pct
from uwcopilot.agent.policy_credit import (
    BASE_DOCUMENTS,
    COLLATERAL_REQUIRED_ABOVE,
    CPA_FINANCIALS_ABOVE,
    PRINCIPAL_OWNERSHIP_PCT,
)
from uwcopilot.docintel.check_models import (
    CREDIT_REFS,
    CheckIssue,
    CheckResult,
    Evidence,
    cite,
    document_evidence,
    found_only,
    make_result,
)
from uwcopilot.docintel.checks_identity import owner_consistency, value_consistency
from uwcopilot.docintel.checks_statements import MONTHS_REQUIRED, consecutive_months
from uwcopilot.docintel.deposits import month_of, sorted_by_month
from uwcopilot.docintel.normalize import canonical_fein, canonical_name
from uwcopilot.docintel.schemas import Cited, LoanPacket, PhotoId

TAX_RETURN_DIFFERENCE_PCT = 10.0  # NBC-9.1: tax return receipts vs P&L revenue for the same year
FINANCIALS_MAX_AGE_DAYS = 90  # NBC-3.1 item 4: P&L and balance sheet dated no more than 90 days before the application
NO_PACKET_DOCUMENT_TYPE = ("cpa_reviewed_financials", "payoff_letters")
# NBC-6.1: every member of the formation document must be on the application, whatever their share.
FORMATION_LISTS_EVERY_OWNER_PCT = 0.0


ID_NAME_MIN_RATIO = 90.0  # rapidfuzz ratio of a canonical ID name read with one OCR-misread letter


def principals(packet: LoanPacket) -> list[tuple[int, str]]:
    """(owner position, canonical name) of each NBC-1.2 principal: a person with 20% or more, or the control person."""
    if packet.application is None:
        return []
    result = []
    for index, owner in enumerate(packet.application.owners, start=1):
        if owner.entity_type.value == "organization" or not owner.name.found:
            continue
        pct = owner.ownership_pct.value
        if (pct is not None and pct >= PRINCIPAL_OWNERSHIP_PCT) or owner.is_control_person.value is True:
            result.append((index, canonical_name(owner.name.value)))
    return result


def financial_dates(packet: LoanPacket) -> list[tuple[Cited, str, str]]:
    """(date, field, doc id) of the P&L period end and the balance sheet date, for the documents present."""
    dates = []
    if packet.profit_and_loss is not None:
        pnl = packet.profit_and_loss
        dates.append((pnl.period_end, "profit_and_loss.period_end", pnl.doc_id))
    if packet.balance_sheet is not None:
        sheet = packet.balance_sheet
        dates.append((sheet.as_of, "balance_sheet.as_of", sheet.doc_id))
    return dates


def stale_financials(packet: LoanPacket) -> list[Evidence]:
    """Evidence of the P&L / balance sheet dates that are more than 90 days before the application date."""
    application = packet.application
    applied = None if application is None else application.application_date.value
    if applied is None:
        return []
    stale = []
    for cited, field, doc_id in financial_dates(packet):
        if cited.value is not None and (applied - cited.value).days > FINANCIALS_MAX_AGE_DAYS:
            stale.extend(found_only([cite(cited, field, None, doc_id)]))
    return stale


def id_names_match(principal: str, id_name: str) -> bool:
    """True when a canonical ID name stands for the canonical principal name (module docstring: presence only)."""
    if principal == id_name or fuzz.ratio(principal, id_name) >= ID_NAME_MIN_RATIO:
        return True
    shorter, longer = sorted((principal.split(), id_name.split()), key=len)
    return len(shorter) >= 2 and longer[: len(shorter)] == shorter


def has_photo_id(principal: str, id_names: set[str]) -> bool:
    """True when one of the ID names matches the principal (id_names_match)."""
    return any(id_names_match(principal, id_name) for id_name in id_names)


def expired_photo_id(photo_id: PhotoId, packet: LoanPacket) -> bool:
    """True when the ID's expiry date is before the application date (NBC-6.2); False when either date is unknown."""
    application = packet.application
    if application is None or application.application_date.value is None or photo_id.expiry_date.value is None:
        return False
    return photo_id.expiry_date.value < application.application_date.value


def documents_provided(packet: LoanPacket) -> dict[str, list[Evidence]]:
    """Checklist code -> evidence of the documents that satisfy it (only the codes the packet satisfies)."""
    provided: dict[str, list[Evidence]] = {}
    if packet.application is not None:
        provided["signed_application"] = [document_evidence(packet.application, "document", "loan_application")]
    if consecutive_months(packet):
        provided["bank_statements_12_months"] = [
            document_evidence(statement, "document", "bank_statement", month_of(statement))
            for statement in sorted_by_month(packet.statements)[-MONTHS_REQUIRED:]
        ]
    years = {item.tax_year.value for item in packet.tax_returns if item.tax_year.value is not None}
    if len(years) >= 2:
        provided["business_tax_returns_2_years"] = [
            document_evidence(item, "document", "tax_return_summary", str(item.tax_year.value))
            for item in packet.tax_returns
        ]
    if packet.profit_and_loss is not None and packet.balance_sheet is not None and not stale_financials(packet):
        provided["ytd_pnl_and_balance_sheet"] = [
            document_evidence(packet.profit_and_loss, "document", "profit_and_loss"),
            document_evidence(packet.balance_sheet, "document", "balance_sheet"),
        ]
    if packet.debt_schedule is not None:
        provided["debt_schedule"] = [document_evidence(packet.debt_schedule, "document", "debt_schedule")]
    current_ids = [item for item in packet.photo_ids if not expired_photo_id(item, packet)]
    id_names = {canonical_name(item.name.value) for item in current_ids if item.name.found}
    needed = {name for _, name in principals(packet)}
    every_principal = bool(needed) and all(has_photo_id(name, id_names) for name in needed)
    if every_principal or (not needed and current_ids):
        provided["principal_photo_ids"] = [document_evidence(item, "document", "photo_id") for item in current_ids]
    if packet.formation is not None:
        provided["formation_documents"] = [document_evidence(packet.formation, "document", "formation_document")]
        if packet.formation.members:
            provided["beneficial_ownership_certification"] = list(provided["formation_documents"])
    if packet.collateral:
        provided["collateral_documents"] = [
            document_evidence(item, "document", "collateral_valuation") for item in packet.collateral
        ]
    return provided


def required_documents_list(packet: LoanPacket) -> list[str]:
    """NBC-3.1 base documents plus the conditional ones (mirrors policy_credit.required_documents)."""
    required = list(BASE_DOCUMENTS)
    application = packet.application
    amount = None if application is None else application.requested_amount.value
    if amount is not None and amount > CPA_FINANCIALS_ABOVE:
        required.append("cpa_reviewed_financials")
    if packet.collateral or (amount is not None and amount > COLLATERAL_REQUIRED_ABOVE):
        required.append("collateral_documents")
    if application is not None and "refinanc" in canonical_name(application.purpose.value):
        required.append("payoff_letters")
    return required


def requirement_triggers(packet: LoanPacket, required: list[str]) -> list[Evidence]:
    """The application values that make conditional documents required (requested amount, purpose)."""
    application = packet.application
    if application is None:
        return []
    items = []
    if "cpa_reviewed_financials" in required or "collateral_documents" in required:
        items.append(cite(application.requested_amount, "application.requested_amount", None, application.doc_id))
    if "payoff_letters" in required:
        items.append(cite(application.purpose, "application.purpose", None, application.doc_id))
    return found_only(items)


def check_required_documents(packet: LoanPacket) -> CheckResult:
    """NBC-3.1 required documents."""
    refs = CREDIT_REFS["required_documents"]
    provided = documents_provided(packet)
    required = required_documents_list(packet)
    missing = [code for code in required if code not in provided]
    stale = stale_financials(packet)
    values = {
        "required": required,
        "provided": sorted(provided),
        "missing": missing,
        "no_packet_document_type": [code for code in NO_PACKET_DOCUMENT_TYPE if code in required],
        "stale_documents": [item.field for item in stale],
        "expired_photo_ids": [item.doc_id for item in packet.photo_ids if expired_photo_id(item, packet)],
    }
    evidence = [item for items in provided.values() for item in items]
    evidence += requirement_triggers(packet, required) + stale
    application = packet.application
    if stale and application is not None:
        applied = cite(application.application_date, "application.application_date", None, application.doc_id)
        evidence += found_only([applied])
    if missing:
        message = f"Required documents are missing: {', '.join(missing)}."
        if stale:
            message += " The P&L or balance sheet is dated more than 90 days before the application date."
        return make_result("required_documents", refs, "fail", message, values, evidence)
    message = f"All {len(required)} required documents are in the packet."
    return make_result("required_documents", refs, "pass", message, values, evidence)


def name_sources(packet: LoanPacket) -> list[Evidence]:
    """Every printed business name (see the module docstring), as evidence."""
    items: list[Evidence | None] = []
    application = packet.application
    if application is not None:
        items.append(cite(application.legal_name, "application.legal_name", None, application.doc_id))
    for statement in packet.statements:
        items.append(cite(statement.account_holder, "statement.account_holder", month_of(statement), statement.doc_id))
    for model, field in (
        (packet.profit_and_loss, "profit_and_loss.business_name"),
        (packet.balance_sheet, "balance_sheet.business_name"),
        (packet.debt_schedule, "debt_schedule.business_name"),
    ):
        if model is not None:
            items.append(cite(model.business_name, field, None, model.doc_id))
    if packet.formation is not None:
        items.append(cite(packet.formation.entity_name, "formation.entity_name", None, packet.formation.doc_id))
    for item in packet.tax_returns:
        year = None if item.tax_year.value is None else str(item.tax_year.value)
        items.append(cite(item.business_name, "tax_return.business_name", year, item.doc_id))
    return found_only(items)


def fein_sources(packet: LoanPacket) -> list[Evidence]:
    """Every printed FEIN (application, formation document, tax returns), as evidence."""
    items: list[Evidence | None] = []
    if packet.application is not None:
        items.append(cite(packet.application.fein, "application.fein", None, packet.application.doc_id))
    if packet.formation is not None:
        items.append(cite(packet.formation.fein, "formation.fein", None, packet.formation.doc_id))
    for item in packet.tax_returns:
        year = None if item.tax_year.value is None else str(item.tax_year.value)
        items.append(cite(item.fein, "tax_return.fein", year, item.doc_id))
    return found_only(items)


def check_name(packet: LoanPacket) -> CheckResult:
    """The business name agrees across documents (names are not repeated in the message)."""
    refs = CREDIT_REFS["name_consistency"]
    return value_consistency("name_consistency", refs, "business name", name_sources(packet), canonical_name, False)


def check_fein(packet: LoanPacket) -> CheckResult:
    """The FEIN agrees across documents."""
    refs = CREDIT_REFS["fein_consistency"]
    return value_consistency("fein_consistency", refs, "FEIN", fein_sources(packet), canonical_fein, True)


def check_owners(packet: LoanPacket) -> CheckResult:
    """NBC-6.1: application owners vs formation-document members."""
    application = packet.application
    formation = packet.formation
    return owner_consistency(
        CREDIT_REFS["owner_consistency"],
        [] if application is None else application.owners,
        None if application is None else application.doc_id,
        None if formation is None else formation.members,
        None if formation is None else formation.doc_id,
        "formation.members",
        FORMATION_LISTS_EVERY_OWNER_PCT,
        total_must_be_100=True,
    )


def check_tax_return_vs_pnl(packet: LoanPacket) -> CheckResult:
    """NBC-9.1: most recent tax return gross receipts vs P&L revenue for the same year (within 10.0%)."""
    refs = CREDIT_REFS["tax_return_vs_pnl"]
    pnl = packet.profit_and_loss
    returns = [item for item in packet.tax_returns if item.tax_year.found and item.gross_receipts.found]
    if pnl is None or not returns or not pnl.revenue.found or not pnl.period_end.found or pnl.revenue.value <= 0:
        message = "A P&L revenue figure and a tax return with gross receipts are both needed."
        return make_result("tax_return_vs_pnl", refs, "not_evaluated", message, {}, [])
    latest = max(returns, key=lambda item: item.tax_year.value)
    year = latest.tax_year.value
    period_end = pnl.period_end.value
    evidence = found_only(
        [
            cite(latest.gross_receipts, "tax_return.gross_receipts", str(year), latest.doc_id),
            cite(pnl.revenue, "profit_and_loss.revenue", None, pnl.doc_id),
            cite(pnl.period_end, "profit_and_loss.period_end", None, pnl.doc_id),
        ]
    )
    values: dict = {"tax_year": year, "pnl_period_end": period_end.isoformat()}
    if not (period_end.year == year and period_end.month == 12 and period_end.day == 31):
        message = f"The P&L period (ended {period_end.isoformat()}) is not tax year {year}; not compared."
        return make_result("tax_return_vs_pnl", refs, "not_evaluated", message, values, evidence)
    receipts = float(latest.gross_receipts.value)
    revenue = float(pnl.revenue.value)
    difference = round_pct(abs(receipts - revenue) / revenue * 100.0)
    values.update({"gross_receipts": receipts, "pnl_revenue": revenue, "difference_pct": difference})
    if difference <= TAX_RETURN_DIFFERENCE_PCT:
        message = f"Tax year {year} gross receipts are within {difference:.1f}% of the P&L revenue."
        return make_result("tax_return_vs_pnl", refs, "pass", message, values, evidence)
    issue = CheckIssue(
        issue="tax_return_differs",
        entity="business",
        fields=["tax_return.gross_receipts", "profit_and_loss.revenue"],
        side_a=evidence[:1],
        side_b=evidence[1:2],
        data=dict(values),
        message=f"Tax year {year} gross receipts ${receipts:,.2f} differ from the P&L revenue ${revenue:,.2f} by "
        f"{difference:.1f}% (more than 10.0%).",
    )
    return make_result("tax_return_vs_pnl", refs, "fail", issue.message, values, evidence, [issue])

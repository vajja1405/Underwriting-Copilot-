"""The documents of a SYNTHETIC loan packet, in page order, and the oracle LoanPacket built from them.

Page order: loan application (2 pages); the 12 monthly bank statements (a removed month is absent); profit and
loss; balance sheet; debt schedule; one photo ID per person principal; credit report; formation document; tax
return summary; collateral valuation (requests above $250,000). A document named in case.missing_documents (or
the photo ID of case.missing_photo_id_owner) is left out.

Which documents look scanned is drawn per document with the rates in SCAN_RATES (photo IDs and signed forms are
scanned more often than bank-portal statements).
"""

import random

from uwcopilot.docintel.schemas import DocumentInfo, LoanPacket
from uwcopilot.docintel.synthetic.credit_application import month_text
from uwcopilot.docintel.synthetic.credit_case import CreditCase
from uwcopilot.docintel.synthetic.documents import DocumentSpec, RenderedDocument
from uwcopilot.docintel.synthetic.render_credit import (
    render_balance_sheet,
    render_collateral,
    render_credit_report,
    render_debt_schedule,
    render_formation,
    render_loan_application,
    render_photo_id,
    render_profit_and_loss,
    render_tax_return,
)
from uwcopilot.docintel.synthetic.render_statement import render_bank_statement

SCAN_RATES: dict[str, float] = {
    "loan_application": 0.25,
    "bank_statement": 0.04,
    "profit_and_loss": 0.12,
    "balance_sheet": 0.12,
    "debt_schedule": 0.15,
    "photo_id": 0.50,
    "credit_report": 0.08,
    "formation_document": 0.35,
    "tax_return_summary": 0.30,
    "collateral_valuation": 0.25,
}


def credit_documents(case: CreditCase) -> list[DocumentSpec]:
    """The packet's documents in page order (scanned flags are set by mark_scanned)."""
    specs = [
        DocumentSpec(
            "loan_application",
            "loan_application",
            "Small business loan application",
            lambda writer: render_loan_application(writer, case),
            required_document="signed_application",
        )
    ]
    for statement in case.present_statements():
        month = month_text(statement.month)
        specs.append(
            DocumentSpec(
                f"bank_statement:{month}",
                "bank_statement",
                f"Bank statement {month}",
                lambda writer, item=statement: render_bank_statement(writer, case, item),
                required_document="bank_statements_12_months",
                month=month,
            )
        )
    specs.append(
        DocumentSpec(
            "profit_and_loss",
            "profit_and_loss",
            "Profit and loss statement",
            lambda writer: render_profit_and_loss(writer, case),
            required_document="ytd_pnl_and_balance_sheet",
        )
    )
    specs.append(
        DocumentSpec(
            "balance_sheet",
            "balance_sheet",
            "Balance sheet",
            lambda writer: render_balance_sheet(writer, case),
            required_document="ytd_pnl_and_balance_sheet",
        )
    )
    if "debt_schedule" not in case.missing_documents:
        specs.append(
            DocumentSpec(
                "debt_schedule",
                "debt_schedule",
                "Schedule of business debt",
                lambda writer: render_debt_schedule(writer, case),
                required_document="debt_schedule",
            )
        )
    for owner in case.principals():
        if owner.owner_id == case.missing_photo_id_owner:
            continue
        specs.append(
            DocumentSpec(
                f"photo_id:{owner.owner_id}",
                "photo_id",
                f"Photo ID specimen {owner.owner_id}",
                lambda writer, person=owner: render_photo_id(writer, case, person),
                required_document="principal_photo_ids",
            )
        )
    specs.append(
        DocumentSpec(
            "credit_report", "credit_report", "Credit report", lambda writer: render_credit_report(writer, case)
        )
    )
    if "formation_document" not in case.missing_documents:
        specs.append(
            DocumentSpec(
                "formation_document",
                "formation_document",
                "Formation document summary",
                lambda writer: render_formation(writer, case),
                required_document="formation_documents",
            )
        )
    if "tax_return_summary" not in case.missing_documents:
        specs.append(
            DocumentSpec(
                "tax_return_summary",
                "tax_return_summary",
                "Business tax return summary",
                lambda writer: render_tax_return(writer, case),
                required_document="business_tax_returns_2_years",
            )
        )
    for number, item in enumerate(case.collateral, start=1):
        specs.append(
            DocumentSpec(
                f"collateral_valuation:{number}",
                "collateral_valuation",
                "Collateral valuation",
                lambda writer, entry=item: render_collateral(writer, case, entry),
                required_document="collateral_documents",
            )
        )
    return specs


def mark_scanned(rng: random.Random, specs: list[DocumentSpec], rates: dict[str, float]) -> None:
    """Draw, per document, whether it looks scanned."""
    for spec in specs:
        spec.scanned = rng.random() < rates[spec.doc_type]


def loan_packet(packet_id: str, rendered: list[RenderedDocument]) -> LoanPacket:
    """The oracle LoanPacket: every rendered part placed in its schema slot, with true document infos."""
    packet = LoanPacket(packet_id=packet_id)
    for document in rendered:
        packet.documents.append(
            DocumentInfo(
                doc_id=document.doc_id,
                doc_type=document.spec.doc_type,
                pages=document.page_numbers,
                ocr_used=document.spec.scanned,
                classification_confidence=1.0,
            )
        )
        doc_type = document.spec.doc_type
        if doc_type == "loan_application":
            packet.application = document.part
        elif doc_type == "bank_statement":
            packet.statements.append(document.part)
        elif doc_type == "profit_and_loss":
            packet.profit_and_loss = document.part
        elif doc_type == "balance_sheet":
            packet.balance_sheet = document.part
        elif doc_type == "debt_schedule":
            packet.debt_schedule = document.part
        elif doc_type == "photo_id":
            packet.photo_ids.append(document.part)
        elif doc_type == "credit_report":
            packet.credit_report = document.part
        elif doc_type == "formation_document":
            packet.formation = document.part
        elif doc_type == "tax_return_summary":
            packet.tax_returns.extend(document.part)
        elif doc_type == "collateral_valuation":
            packet.collateral.append(document.part)
    return packet

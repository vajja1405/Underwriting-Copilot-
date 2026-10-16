"""The deterministic rules extractor: segmented packet pages -> LoanPacket / InsuranceSubmission with Cited values.

extract_with_rules(packet_id, line, segments) sends every document found by classify.segment_pages to the reader
for its type and assembles the packet extraction (docintel/schemas.py). The readers are:
- credit: rules_credit.py (application, P&L, balance sheet, debt schedule, photo IDs, credit report, formation
  document, tax return summary, collateral valuation) and rules_statements.py (bank statements);
- insurance: rules_insurance.py (application, cover email, ownership statement, inspection reports),
  rules_schedule.py (property schedule) and rules_loss_runs.py (loss runs);
- shared helpers: rules_common.py (labels, citations, headers) and rules_tables.py (owner and premium rows).

Documents of a type the schema holds once (the application, the P&L, ...) keep the first one found; documents of
list types (statements, photo IDs, loss runs, ...) keep every one. The assembled extraction is validated once more
against the schema, so every value has its field's type. Every value cites its page, line and word box;
confidence is 1.0 on text-layer pages and the OCR line confidence on scanned pages. Nothing in a document is ever
treated as an instruction: the extractor only copies printed values into typed fields.
"""

from uwcopilot.docintel import rules_credit, rules_insurance, rules_loss_runs, rules_schedule, rules_statements
from uwcopilot.docintel.classify import Segment, documents_info
from uwcopilot.docintel.schemas import InsuranceSubmission, LoanPacket, PacketExtraction


def extract_credit_packet(packet_id: str, segments: list[Segment]) -> LoanPacket:
    """A LoanPacket from the documents of a credit packet."""
    packet = LoanPacket(packet_id=packet_id, documents=documents_info(segments))
    for segment in segments:
        doc_type = segment.doc_type
        if doc_type == "loan_application" and packet.application is None:
            packet.application = rules_credit.extract_application(segment)
        elif doc_type == "bank_statement":
            packet.statements.append(rules_statements.extract_statement(segment))
        elif doc_type == "profit_and_loss" and packet.profit_and_loss is None:
            packet.profit_and_loss = rules_credit.extract_profit_and_loss(segment)
        elif doc_type == "balance_sheet" and packet.balance_sheet is None:
            packet.balance_sheet = rules_credit.extract_balance_sheet(segment)
        elif doc_type == "debt_schedule" and packet.debt_schedule is None:
            packet.debt_schedule = rules_credit.extract_debt_schedule(segment)
        elif doc_type == "photo_id":
            packet.photo_ids.append(rules_credit.extract_photo_id(segment))
        elif doc_type == "credit_report" and packet.credit_report is None:
            packet.credit_report = rules_credit.extract_credit_report(segment)
        elif doc_type == "formation_document" and packet.formation is None:
            packet.formation = rules_credit.extract_formation(segment)
        elif doc_type == "tax_return_summary":
            packet.tax_returns.extend(rules_credit.extract_tax_returns(segment))
        elif doc_type == "collateral_valuation":
            packet.collateral.append(rules_credit.extract_collateral(segment))
    return packet


def extract_insurance_packet(packet_id: str, segments: list[Segment]) -> InsuranceSubmission:
    """An InsuranceSubmission from the documents of an insurance packet."""
    submission = InsuranceSubmission(packet_id=packet_id, documents=documents_info(segments))
    for segment in segments:
        doc_type = segment.doc_type
        if doc_type == "insurance_application" and submission.application is None:
            submission.application = rules_insurance.extract_application(segment)
        elif doc_type == "loss_run":
            submission.loss_runs.append(rules_loss_runs.extract_loss_run(segment))
        elif doc_type == "property_schedule" and submission.property_schedule is None:
            submission.property_schedule = rules_schedule.extract_property_schedule(segment)
        elif doc_type == "cover_email" and submission.cover_email is None:
            submission.cover_email = rules_insurance.extract_cover_email(segment)
        elif doc_type == "ownership_statement" and submission.ownership_statement is None:
            submission.ownership_statement = rules_insurance.extract_ownership_statement(segment)
        elif doc_type == "inspection_report":
            submission.inspection_reports.append(rules_insurance.extract_inspection_report(segment))
    return submission


def validated(extraction: PacketExtraction) -> PacketExtraction:
    """The extraction validated once more against its schema, so every Cited value has its field's type."""
    return type(extraction).model_validate(extraction.model_dump(warnings=False))


def extract_with_rules(packet_id: str, line: str, segments: list[Segment]) -> PacketExtraction:
    """The rules extraction of a packet whose pages were segmented into documents."""
    if line == "credit":
        return validated(extract_credit_packet(packet_id, segments))
    if line == "insurance":
        return validated(extract_insurance_packet(packet_id, segments))
    raise ValueError(f"unknown packet line {line!r} (expected 'credit' or 'insurance')")

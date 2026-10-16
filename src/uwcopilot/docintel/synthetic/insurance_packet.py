"""The documents of a SYNTHETIC insurance submission packet, in page order, and its oracle InsuranceSubmission.

Page order: insurance application (2 pages); one loss run per prior carrier in the packet (oldest policy years
first); property schedule (landscape); broker cover email; ownership statement; one inspection report per location
with TIV above $5,000,000 (also for a location that passes $5,000,000 only if a tiv_mismatch is resolved in favor
of the application's higher TIV; see insurance_generate.inspected_locations). A missing carrier loss run or a
missing ownership statement is left out.
"""

from uwcopilot.docintel.schemas import DocumentInfo, InsuranceSubmission
from uwcopilot.docintel.synthetic.documents import DocumentSpec, RenderedDocument
from uwcopilot.docintel.synthetic.insurance_case import InsuranceCase
from uwcopilot.docintel.synthetic.render_base import LANDSCAPE
from uwcopilot.docintel.synthetic.render_insurance import (
    render_cover_email,
    render_inspection_report,
    render_insurance_application,
    render_ownership_statement,
    render_property_schedule,
)
from uwcopilot.docintel.synthetic.render_loss_run import render_loss_run

SCAN_RATES: dict[str, float] = {
    "insurance_application": 0.25,
    "loss_run": 0.25,
    "property_schedule": 0.15,
    "cover_email": 0.0,
    "ownership_statement": 0.35,
    "inspection_report": 0.30,
}


def insurance_documents(case: InsuranceCase) -> list[DocumentSpec]:
    """The packet's documents in page order (scanned flags are set by credit_packet.mark_scanned)."""
    specs = [
        DocumentSpec(
            "insurance_application",
            "insurance_application",
            "Commercial lines application",
            lambda writer: render_insurance_application(writer, case),
            required_document="signed_application",
        )
    ]
    for run in case.present_runs():
        specs.append(
            DocumentSpec(
                f"loss_run:{run.carrier}",
                "loss_run",
                f"Loss run {run.carrier}",
                lambda writer, item=run: render_loss_run(writer, case, item),
                required_document="loss_runs",
                carrier=run.carrier,
            )
        )
    specs.append(
        DocumentSpec(
            "property_schedule",
            "property_schedule",
            "Property schedule",
            lambda writer: render_property_schedule(writer, case),
            required_document="property_schedule",
            page_size=LANDSCAPE,
        )
    )
    specs.append(
        DocumentSpec(
            "cover_email", "cover_email", "Broker cover email", lambda writer: render_cover_email(writer, case)
        )
    )
    if not case.missing_ownership_statement:
        specs.append(
            DocumentSpec(
                "ownership_statement",
                "ownership_statement",
                "Beneficial ownership statement",
                lambda writer: render_ownership_statement(writer, case),
                required_document="beneficial_ownership_statement",
            )
        )
    for location in case.locations:
        if location.location_id in case.inspections:
            specs.append(
                DocumentSpec(
                    f"inspection_report:{location.location_id}",
                    "inspection_report",
                    f"Inspection report {location.location_id}",
                    lambda writer, item=location: render_inspection_report(writer, case, item),
                    required_document="inspection_report",
                )
            )
    return specs


def insurance_submission(packet_id: str, rendered: list[RenderedDocument]) -> InsuranceSubmission:
    """The oracle InsuranceSubmission: every rendered part in its schema slot, with true document infos."""
    submission = InsuranceSubmission(packet_id=packet_id)
    for document in rendered:
        submission.documents.append(
            DocumentInfo(
                doc_id=document.doc_id,
                doc_type=document.spec.doc_type,
                pages=document.page_numbers,
                ocr_used=document.spec.scanned,
                classification_confidence=1.0,
            )
        )
        doc_type = document.spec.doc_type
        if doc_type == "insurance_application":
            submission.application = document.part
        elif doc_type == "loss_run":
            submission.loss_runs.append(document.part)
        elif doc_type == "property_schedule":
            submission.property_schedule = document.part
        elif doc_type == "cover_email":
            submission.cover_email = document.part
        elif doc_type == "ownership_statement":
            submission.ownership_statement = document.part
        elif doc_type == "inspection_report":
            submission.inspection_reports.append(document.part)
    return submission

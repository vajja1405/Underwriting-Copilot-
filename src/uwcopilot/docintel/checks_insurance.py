"""Harborline (insurance) document checks over an InsuranceSubmission: required documents and identity.

Thresholds come from agent/policy_insurance.py and formulas from agent/insurance_calculations.py. The loss-run
checks are in checks_loss_runs.py and checks_loss_run_coverage.py.

- required_documents (HM-3.1): the checklist codes the packet holds (documents_provided below) against the
  required list, built like policy_insurance.required_documents: signed_application, property_schedule,
  loss_runs, prior_carrier_history, beneficial_ownership_statement, plus revenue_and_payroll when general
  liability is requested and inspection_report when a location's TIV is above $5,000,000. The schema has no flood
  zone, so whether an elevation certificate is needed cannot be decided here (values: not_determinable).
  Mapping: signed_application <- the application; property_schedule <- the schedule; loss_runs <- a loss run for
  EVERY carrier on the application's prior-carriers table (checks_loss_runs.every_prior_carrier_has_a_run: HM-3.1
  item 3 asks for loss runs meeting HM-3.2, so a carrier without its run means more information is needed; whether
  the runs cover every year is loss_run_coverage); prior_carrier_history <- rows in the
  application's prior-carriers table; revenue_and_payroll <- annual revenue and payroll on the application;
  beneficial_ownership_statement <- the ownership statement; inspection_report <- a report for every location
  above $5,000,000, dated no more than 12 months before the proposed effective date when its date is known.
  HM-5.1: the property schedule must be signed no more than 12 months before the proposed effective date; a
  schedule signed earlier is listed in values["stale_documents"] and does not count. "12 months" is the same
  calendar day one year earlier. The evidence also cites the values that make a conditional document required
  (the requested coverage, the TIV of each location above $5,000,000).
- name_consistency (HM-3.1, 8.1): the named insured on the application, every loss run, the property schedule and
  the ownership statement. fein_consistency: only the application carries a FEIN in the extraction schema, so it
  is normally not evaluated (fewer than two documents).
- owner_consistency (HM-3.1 item 6, 8.1): application owners vs the ownership statement, which lists only owners
  of 25% or more, so an application owner below 25% may be absent from it.

The property schedule and TIV helpers and the tiv_consistency check are in checks_property.py.
"""

from datetime import date

from uwcopilot.agent.policy_insurance import INSPECTION_REPORT_ABOVE_TIV
from uwcopilot.docintel.check_models import (
    INSURANCE_REFS,
    CheckResult,
    Evidence,
    cite,
    document_evidence,
    found_only,
    make_result,
)
from uwcopilot.docintel.checks_identity import owner_consistency, value_consistency
from uwcopilot.docintel.checks_loss_runs import every_prior_carrier_has_a_run, years_before
from uwcopilot.docintel.checks_property import location_evidence, location_id_of, location_value
from uwcopilot.docintel.normalize import canonical_fein, canonical_name
from uwcopilot.docintel.schemas import InsuranceSubmission, casefold_key

OWNERSHIP_STATEMENT_MIN_PCT = 25.0  # HM-3.1 item 6: the statement lists every individual owning 25% or more
DOCUMENT_MAX_AGE_YEARS = 1  # HM-3.1 inspection report and HM-5.1 schedule signature: within 12 months
NOT_DETERMINABLE = ("elevation_certificate",)


def requests_gl(submission: InsuranceSubmission) -> bool:
    """True when general liability is requested (package or a liability coverage), or the coverage is unknown."""
    application = submission.application
    if application is None or not application.coverage.found:
        return True
    words = canonical_name(application.coverage.value).split()
    return "package" in words or "liability" in words or "gl" in words


def large_location_ids(submission: InsuranceSubmission) -> list[str]:
    """Location ids (or positions "#n") of schedule locations with TIV above $5,000,000 (HM-3.1)."""
    schedule = submission.property_schedule
    if schedule is None:
        return []
    result = []
    for index, location in enumerate(schedule.locations, start=1):
        value = location_value(location)
        if value is not None and value > INSPECTION_REPORT_ABOVE_TIV:
            result.append(location_id_of(location) or f"#{index}")
    return result


def oldest_allowed_date(submission: InsuranceSubmission) -> date | None:
    """12 months before the proposed effective date (None when the effective date is unknown)."""
    application = submission.application
    if application is None or application.proposed_effective_date.value is None:
        return None
    return years_before(application.proposed_effective_date.value, DOCUMENT_MAX_AGE_YEARS)


def current_inspections(submission: InsuranceSubmission) -> list[str]:
    """Location ids with an inspection report that is not known to be older than 12 months before the effective date."""
    oldest = oldest_allowed_date(submission)
    result = []
    for report in submission.inspection_reports:
        if not report.location_id.found:
            continue
        inspected = report.inspection_date.value
        if oldest is not None and inspected is not None and inspected < oldest:
            continue
        result.append(str(report.location_id.value).strip())
    return result


def stale_schedule(submission: InsuranceSubmission) -> list[Evidence]:
    """Evidence of a property schedule signed more than 12 months before the effective date (HM-5.1)."""
    schedule = submission.property_schedule
    oldest = oldest_allowed_date(submission)
    if schedule is None or oldest is None or schedule.signed_date.value is None:
        return []
    if schedule.signed_date.value >= oldest:
        return []
    return found_only([cite(schedule.signed_date, "property_schedule.signed_date", None, schedule.doc_id)])


def documents_provided(submission: InsuranceSubmission) -> dict[str, list[Evidence]]:
    """Checklist code -> evidence of the documents that satisfy it (only the codes the packet satisfies)."""
    provided: dict[str, list[Evidence]] = {}
    application = submission.application
    if application is not None:
        provided["signed_application"] = [document_evidence(application, "document", "insurance_application")]
        rows = []
        for row in application.prior_carriers:
            rows.append(
                cite(row.carrier, "application.prior_carriers.carrier", casefold_key(row.carrier), application.doc_id)
            )
        if found_only(rows):
            provided["prior_carrier_history"] = found_only(rows)
        revenue = cite(application.annual_revenue, "application.annual_revenue", None, application.doc_id)
        payroll = cite(application.annual_payroll, "application.annual_payroll", None, application.doc_id)
        if revenue is not None and payroll is not None:
            provided["revenue_and_payroll"] = [revenue, payroll]
    if submission.property_schedule is not None and not stale_schedule(submission):
        provided["property_schedule"] = [
            document_evidence(submission.property_schedule, "document", "property_schedule")
        ]
    if every_prior_carrier_has_a_run(submission):
        provided["loss_runs"] = [
            document_evidence(item, "document", "loss_run", casefold_key(item.carrier)) for item in submission.loss_runs
        ]
    if submission.ownership_statement is not None:
        statement = submission.ownership_statement
        provided["beneficial_ownership_statement"] = [document_evidence(statement, "document", "ownership_statement")]
    large = large_location_ids(submission)
    inspected = set(current_inspections(submission))
    if large and all(location in inspected for location in large):
        provided["inspection_report"] = [
            document_evidence(report, "document", "inspection_report") for report in submission.inspection_reports
        ]
    return provided


def required_documents_list(submission: InsuranceSubmission) -> list[str]:
    """HM-3.1 required documents for this submission (see the module docstring)."""
    required = [
        "signed_application",
        "property_schedule",
        "loss_runs",
        "prior_carrier_history",
        "beneficial_ownership_statement",
    ]
    if requests_gl(submission):
        required.append("revenue_and_payroll")
    if large_location_ids(submission):
        required.append("inspection_report")
    return required


def requirement_triggers(submission: InsuranceSubmission, required: list[str]) -> list[Evidence]:
    """The values that make conditional documents required: the coverage, and each large location's TIV."""
    items = []
    application = submission.application
    if "revenue_and_payroll" in required and application is not None:
        items.append(cite(application.coverage, "application.coverage", None, application.doc_id))
    schedule = submission.property_schedule
    if "inspection_report" in required and schedule is not None:
        for location in schedule.locations:
            value = location_value(location)
            if value is not None and value > INSPECTION_REPORT_ABOVE_TIV:
                items.extend(location_evidence(location, schedule.doc_id)[:1])
    return found_only(items)


def check_required_documents(submission: InsuranceSubmission) -> CheckResult:
    """HM-3.1 required documents."""
    refs = INSURANCE_REFS["required_documents"]
    provided = documents_provided(submission)
    required = required_documents_list(submission)
    missing = [code for code in required if code not in provided]
    stale = stale_schedule(submission)
    values = {
        "required": required,
        "provided": sorted(provided),
        "missing": missing,
        "not_determinable": list(NOT_DETERMINABLE),
        "locations_needing_inspection": large_location_ids(submission),
        "stale_documents": [item.field for item in stale],
    }
    evidence = [item for items in provided.values() for item in items]
    evidence += requirement_triggers(submission, required) + stale
    application = submission.application
    if stale and application is not None:
        effective = cite(
            application.proposed_effective_date, "application.proposed_effective_date", None, application.doc_id
        )
        evidence += found_only([effective])
    if missing:
        message = f"Required documents are missing: {', '.join(missing)}."
        if stale:
            message += " The property schedule was signed more than 12 months before the effective date."
        return make_result("required_documents", refs, "fail", message, values, evidence)
    message = f"All {len(required)} required documents are in the packet."
    return make_result("required_documents", refs, "pass", message, values, evidence)


def name_sources(submission: InsuranceSubmission) -> list[Evidence]:
    """Every printed named-insured name (see the module docstring), as evidence."""
    items: list[Evidence | None] = []
    application = submission.application
    if application is not None:
        items.append(cite(application.named_insured, "application.named_insured", None, application.doc_id))
    for loss_run in submission.loss_runs:
        key = casefold_key(loss_run.carrier)
        items.append(cite(loss_run.insured_name, "loss_run.insured_name", key, loss_run.doc_id))
    schedule = submission.property_schedule
    if schedule is not None:
        items.append(cite(schedule.insured_name, "property_schedule.insured_name", None, schedule.doc_id))
    statement = submission.ownership_statement
    if statement is not None:
        items.append(cite(statement.entity_name, "ownership_statement.entity_name", None, statement.doc_id))
    return found_only(items)


def check_name(submission: InsuranceSubmission) -> CheckResult:
    """The named insured agrees across documents (names are not repeated in the message)."""
    refs = INSURANCE_REFS["name_consistency"]
    sources = name_sources(submission)
    return value_consistency("name_consistency", refs, "named insured", sources, canonical_name, False)


def check_fein(submission: InsuranceSubmission) -> CheckResult:
    """The FEIN agrees across documents (only the application carries one in the schema; see the module docstring)."""
    refs = INSURANCE_REFS["fein_consistency"]
    application = submission.application
    sources = []
    if application is not None:
        sources = found_only([cite(application.fein, "application.fein", None, application.doc_id)])
    return value_consistency("fein_consistency", refs, "FEIN", sources, canonical_fein, True)


def check_owners(submission: InsuranceSubmission) -> CheckResult:
    """HM-3.1 item 6 / HM-8.1: application owners vs the beneficial ownership statement."""
    application = submission.application
    statement = submission.ownership_statement
    return owner_consistency(
        INSURANCE_REFS["owner_consistency"],
        [] if application is None else application.owners,
        None if application is None else application.doc_id,
        None if statement is None else statement.owners,
        None if statement is None else statement.doc_id,
        "ownership_statement.owners",
        OWNERSHIP_STATEMENT_MIN_PCT,
    )

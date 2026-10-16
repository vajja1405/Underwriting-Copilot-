"""Harborline (insurance) property schedule checks over an InsuranceSubmission: TIV reconciliation (HM-5.1, 5.2).

Formulas come from agent/insurance_calculations.py (location_tiv, tiv_difference_pct) and thresholds from
agent/policy_insurance.py. The helpers that read a schedule location (its id, its TIV, its evidence) are also used
by the required-documents check in checks_insurance.py.

- tiv_consistency (HM-5.2, HM-11.1): the application TIV against the schedule TIV (the sum of each location's
  TIV; a location without a printed TIV uses building + contents + business income). Difference bands (percent of
  the schedule TIV): 5.0% or less passes (use the schedule TIV); more than 5.0% fails (request a corrected
  application or schedule); more than 15.0% fails (refer). Inside the schedule, each location's printed TIV must
  equal building + contents + business income and the printed total must equal the sum of the locations, both
  within $1.00; a difference there is an integrity indicator and makes the check high severity.
"""

from uwcopilot.agent.applications import Location
from uwcopilot.agent.insurance_calculations import RECONCILIATION_TOLERANCE_USD, location_tiv, tiv_difference_pct
from uwcopilot.agent.policy_insurance import TIV_CORRECTION_PCT, TIV_REFER_PCT
from uwcopilot.docintel.check_models import (
    INSURANCE_REFS,
    CheckIssue,
    CheckResult,
    Evidence,
    cite,
    found_only,
    make_result,
)
from uwcopilot.docintel.schemas import InsuranceSubmission, ScheduleLocation


def location_id_of(location: ScheduleLocation) -> str | None:
    """The location id as printed, stripped (None when missing)."""
    return None if not location.location_id.found else str(location.location_id.value).strip()


def parts_total(location: ScheduleLocation) -> float | None:
    """Building + contents + business income with insurance_calculations.location_tiv (None if a part is missing).

    location_tiv reads only those three values, so the other Location fields are not needed.
    """
    parts = (location.building_value, location.contents_value, location.business_income_value)
    if any(not part.found for part in parts):
        return None
    values = Location.model_construct(
        building_value=float(parts[0].value),
        contents_value=float(parts[1].value),
        business_income_value=float(parts[2].value),
    )
    return round(location_tiv(values), 2)


def location_value(location: ScheduleLocation) -> float | None:
    """A location's TIV: the printed TIV, else building + contents + business income."""
    if location.tiv.found:
        return float(location.tiv.value)
    return parts_total(location)


def location_evidence(location: ScheduleLocation, doc_id: str) -> list[Evidence]:
    """Evidence for a location's TIV and its three parts (key = location id)."""
    key = location_id_of(location)
    names = ("tiv", "building_value", "contents_value", "business_income_value")
    return found_only(
        [cite(getattr(location, name), f"property_schedule.locations.{name}", key, doc_id) for name in names]
    )


def location_arithmetic_issue(location: ScheduleLocation, doc_id: str) -> CheckIssue | None:
    """A location whose printed TIV is not building + contents + business income (within $1.00)."""
    expected = parts_total(location)
    if expected is None or not location.tiv.found:
        return None
    printed = float(location.tiv.value)
    if abs(printed - expected) <= RECONCILIATION_TOLERANCE_USD:
        return None
    key = location_id_of(location)
    evidence = location_evidence(location, doc_id)
    return CheckIssue(
        issue="location_tiv_arithmetic",
        entity=f"location:{key or 'unknown'}",
        fields=["property_schedule.locations.tiv", "property_schedule.locations.building_value"],
        side_a=evidence[:1],
        side_b=evidence[1:],
        data={"location_id": key, "printed_tiv": printed, "parts_total": expected},
        message=f"Location {key} shows TIV ${printed:,.2f} but its building, contents and business income add up "
        f"to ${expected:,.2f}.",
    )


def schedule_total_issue(submission: InsuranceSubmission, schedule_total: float) -> CheckIssue | None:
    """The printed schedule total vs the sum of the location TIVs (within $1.00)."""
    schedule = submission.property_schedule
    if schedule is None or not schedule.total_tiv.found or not schedule.locations:
        return None
    printed = float(schedule.total_tiv.value)
    if abs(printed - schedule_total) <= RECONCILIATION_TOLERANCE_USD:
        return None
    side_b = []
    for location in schedule.locations:
        side_b.extend(location_evidence(location, schedule.doc_id)[:1])
    return CheckIssue(
        issue="schedule_total_arithmetic",
        entity="business",
        fields=["property_schedule.total_tiv", "property_schedule.locations.tiv"],
        side_a=found_only([cite(schedule.total_tiv, "property_schedule.total_tiv", None, schedule.doc_id)]),
        side_b=side_b,
        data={"printed_total": printed, "sum_of_locations": schedule_total},
        message=f"The schedule's printed total TIV ${printed:,.2f} differs from the sum of its locations "
        f"(${schedule_total:,.2f}).",
    )


def schedule_tiv(submission: InsuranceSubmission) -> tuple[float | None, list[Evidence]]:
    """The schedule TIV (sum of locations, or the printed total without locations) and its evidence."""
    schedule = submission.property_schedule
    if schedule is None:
        return None, []
    if not schedule.locations:
        total = cite(schedule.total_tiv, "property_schedule.total_tiv", None, schedule.doc_id)
        return (None, []) if total is None else (float(schedule.total_tiv.value), [total])
    values = [location_value(location) for location in schedule.locations]
    if any(value is None for value in values):
        return None, []
    evidence = []
    for location in schedule.locations:
        evidence.extend(location_evidence(location, schedule.doc_id)[:1])
    return round(sum(value for value in values if value is not None), 2), evidence


def tiv_band(difference_pct: float) -> str:
    """HM-5.2 treatment band for a TIV difference."""
    if difference_pct <= TIV_CORRECTION_PCT:
        return "use_schedule_tiv"
    if difference_pct <= TIV_REFER_PCT:
        return "request_correction"
    return "refer"


def check_tiv(submission: InsuranceSubmission) -> CheckResult:
    """HM-5.2 application TIV vs the property schedule, and the schedule's own arithmetic (module docstring)."""
    refs = INSURANCE_REFS["tiv_consistency"]
    application = submission.application
    schedule = submission.property_schedule
    total, schedule_evidence = schedule_tiv(submission)
    if schedule is None or total is None or total <= 0:
        message = "The property schedule TIV could not be determined."
        return make_result("tiv_consistency", refs, "not_evaluated", message, {}, schedule_evidence)
    issues = []
    for location in schedule.locations:
        issue = location_arithmetic_issue(location, schedule.doc_id)
        if issue is not None:
            issues.append(issue)
    total_issue = schedule_total_issue(submission, total)
    if total_issue is not None:
        issues.append(total_issue)
    values: dict = {"schedule_tiv": total, "tolerance_usd": RECONCILIATION_TOLERANCE_USD}
    stated = (
        None
        if application is None
        else cite(application.stated_tiv, "application.stated_tiv", None, application.doc_id)
    )
    if stated is not None:
        stated_value = float(application.stated_tiv.value)
        difference = tiv_difference_pct(stated_value, total)
        values.update({"stated_tiv": stated_value, "difference_pct": difference, "band": tiv_band(difference)})
        if difference > TIV_CORRECTION_PCT:
            issues.append(
                CheckIssue(
                    issue="tiv_differs",
                    entity="business",
                    fields=["application.stated_tiv", "property_schedule.locations.tiv"],
                    side_a=[stated],
                    side_b=schedule_evidence,
                    data=dict(values),
                    message=f"The application TIV ${stated_value:,.2f} differs from the schedule TIV ${total:,.2f} "
                    f"by {difference:.1f}% (HM-5.2 band: {values['band']}).",
                )
            )
    evidence = ([stated] if stated is not None else []) + schedule_evidence
    values["issue_codes"] = [issue.issue for issue in issues]
    if issues:
        integrity = any(issue.issue.endswith("_arithmetic") for issue in issues)
        message = " ".join(issue.message for issue in issues)
        severity = "high" if integrity else "medium"
        return make_result("tiv_consistency", refs, "fail", message, values, evidence, issues, severity)
    if stated is None:
        message = "The application TIV was not found; the schedule's own totals add up."
        return make_result("tiv_consistency", refs, "not_evaluated", message, values, evidence)
    message = f"The application TIV is within {values['difference_pct']:.1f}% of the schedule TIV ${total:,.2f}."
    return make_result("tiv_consistency", refs, "pass", message, values, evidence)

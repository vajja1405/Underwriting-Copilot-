"""Harborline loss-run coverage and valuation age over an InsuranceSubmission (HM-3.1 item 3, HM-3.2).

- loss_run_coverage (HM-3.1 item 3, HM-3.2): policy years run from the proposed effective date, as in the agent's
  SYNTHETIC submissions: the current term starts one year before the effective date and the 5 experience years
  are the 5 years before it. A required year is covered when a loss run prints a premium row for a policy year
  starting within 45 days of it, or when the application's prior-carriers table names a carrier for that year
  and that carrier's loss run is in the packet. Issues: a prior carrier without a loss run (missing_carrier),
  required years nobody covers (uncovered_years; HM-3.2 asks for a written explanation, which the schema cannot
  hold), and a loss run not produced by the carrier (not_carrier_produced).
- loss_run_valuation_age (HM-3.2): each loss run's "valued as of" date must be no more than 90 days before the
  proposed effective date (insurance_calculations.loss_run_age_days). A loss run without a valuation date is
  listed in the values (it cannot be dated) but does not fail the check on its own: the extractor may simply
  have missed the date.
"""

from datetime import date

from uwcopilot.agent.applications import LossRuns
from uwcopilot.agent.insurance_calculations import loss_run_age_days
from uwcopilot.agent.policy_insurance import MAX_LOSS_RUN_AGE_DAYS
from uwcopilot.docintel.check_models import (
    INSURANCE_REFS,
    CheckIssue,
    CheckResult,
    Evidence,
    cite,
    document_evidence,
    found_only,
    make_result,
)
from uwcopilot.docintel.checks_loss_runs import carrier_label, has_loss_run, loss_run_carriers, years_before
from uwcopilot.docintel.normalize import canonical_name
from uwcopilot.docintel.schemas import InsuranceSubmission, casefold_key

EXPERIENCE_YEARS = 5  # HM-1.2: the 5 most recent complete policy years
YEAR_MATCH_DAYS = 45  # a printed policy year starting this close to a required year start covers it


def required_year_starts(effective: date) -> list[date]:
    """The 5 experience-year starts and the current-term start, oldest first (module docstring)."""
    return [years_before(effective, years) for years in range(EXPERIENCE_YEARS + 1, 0, -1)]


def covered_by_rows(start: date, submission: InsuranceSubmission) -> bool:
    """True when a loss run prints a premium row for a policy year starting within 45 days of start."""
    for loss_run in submission.loss_runs:
        for year in loss_run.years:
            printed = year.policy_year_start.value
            if printed is not None and abs((printed - start).days) <= YEAR_MATCH_DAYS:
                return True
    return False


def prior_carrier_rows(submission: InsuranceSubmission) -> list[tuple[str, date | None, Evidence | None]]:
    """(canonical carrier, policy year start, evidence) for each row of the application's prior-carriers table."""
    application = submission.application
    if application is None:
        return []
    rows = []
    for row in application.prior_carriers:
        if not row.carrier.found:
            continue
        key = casefold_key(row.carrier)
        evidence = cite(row.carrier, "application.prior_carriers.carrier", key, application.doc_id)
        rows.append((canonical_name(row.carrier.value), row.policy_year_start.value, evidence))
    return rows


def coverage_issues(submission: InsuranceSubmission) -> tuple[list[CheckIssue], dict]:
    """Missing carriers, uncovered years and loss runs not produced by the carrier (module docstring)."""
    issues: list[CheckIssue] = []
    carriers = loss_run_carriers(submission)
    rows = prior_carrier_rows(submission)
    missing: dict[str, list[Evidence]] = {}
    for name, _start, evidence in rows:
        if not has_loss_run(name, carriers) and evidence is not None:
            missing.setdefault(name, []).append(evidence)
    for evidence_rows in missing.values():
        issues.append(
            CheckIssue(
                issue="missing_carrier",
                entity=f"carrier:{evidence_rows[0].key}",
                fields=["application.prior_carriers.carrier", "loss_run.carrier"],
                side_a=evidence_rows,
                side_b=[],
                data={"carrier": evidence_rows[0].value, "policy_years": len(evidence_rows)},
                message=f"A prior carrier on the application ({len(evidence_rows)} policy year(s)) has no loss run.",
            )
        )
    values: dict = {"loss_runs": len(submission.loss_runs), "carriers_without_loss_run": len(missing)}
    application = submission.application
    effective = None if application is None else application.proposed_effective_date.value
    if effective is not None:
        uncovered = []
        for year_start in required_year_starts(effective):
            by_table = any(
                has_loss_run(name, carriers)
                and row_start is not None
                and abs((row_start - year_start).days) <= YEAR_MATCH_DAYS
                for name, row_start, _evidence in rows
            )
            if not by_table and not covered_by_rows(year_start, submission):
                uncovered.append(year_start.isoformat())
        values["required_year_starts"] = [day.isoformat() for day in required_year_starts(effective)]
        values["uncovered_year_starts"] = uncovered
        if uncovered and application is not None:
            side = found_only(
                [
                    cite(
                        application.proposed_effective_date,
                        "application.proposed_effective_date",
                        None,
                        application.doc_id,
                    )
                ]
            )
            issues.append(
                CheckIssue(
                    issue="uncovered_years",
                    entity="business",
                    fields=["application.proposed_effective_date", "loss_run.years.policy_year_start"],
                    side_a=side,
                    side_b=[],
                    data={"uncovered_year_starts": uncovered},
                    message=f"No loss run covers the policy year(s) starting {', '.join(uncovered)}.",
                )
            )
    for loss_run in submission.loss_runs:
        if loss_run.carrier_produced.value is False:
            produced = cite(
                loss_run.carrier_produced, "loss_run.carrier_produced", casefold_key(loss_run.carrier), loss_run.doc_id
            )
            issues.append(
                CheckIssue(
                    issue="not_carrier_produced",
                    entity=f"carrier:{casefold_key(loss_run.carrier) or loss_run.doc_id}",
                    fields=["loss_run.carrier_produced"],
                    side_a=found_only([produced]),
                    side_b=[],
                    message=f"{carrier_label(loss_run)} is not a carrier-produced loss run (HM-3.2).",
                )
            )
    return issues, values


def check_coverage(submission: InsuranceSubmission) -> CheckResult:
    """HM-3.1 / HM-3.2: loss runs for every prior carrier and every required policy year."""
    refs = INSURANCE_REFS["loss_run_coverage"]
    if submission.application is None and not submission.loss_runs:
        message = "Neither the application nor a loss run was found."
        return make_result("loss_run_coverage", refs, "not_evaluated", message, {}, [])
    issues, values = coverage_issues(submission)
    evidence = [
        document_evidence(item, "document", "loss_run", casefold_key(item.carrier)) for item in submission.loss_runs
    ]
    for _name, _start, row_evidence in prior_carrier_rows(submission):
        if row_evidence is not None:
            evidence.append(row_evidence)
    if issues:
        message = "Loss-run coverage is incomplete: " + " ".join(issue.message for issue in issues)
        return make_result("loss_run_coverage", refs, "fail", message, values, evidence, issues)
    if "required_year_starts" not in values:
        message = "No missing carrier was found; the proposed effective date is needed to check the policy years."
        return make_result("loss_run_coverage", refs, "not_evaluated", message, values, evidence)
    message = f"Loss runs cover every prior carrier and the {EXPERIENCE_YEARS} experience years plus the current term."
    return make_result("loss_run_coverage", refs, "pass", message, values, evidence)


def check_valuation_age(submission: InsuranceSubmission) -> CheckResult:
    """HM-3.2: each loss run is valued no more than 90 days before the proposed effective date."""
    refs = INSURANCE_REFS["loss_run_valuation_age"]
    application = submission.application
    if application is None or not application.proposed_effective_date.found or not submission.loss_runs:
        message = "The proposed effective date and at least one loss run are needed."
        return make_result("loss_run_valuation_age", refs, "not_evaluated", message, {}, [])
    effective = application.proposed_effective_date.value
    effective_evidence = cite(
        application.proposed_effective_date, "application.proposed_effective_date", None, application.doc_id
    )
    issues = []
    evidence = found_only([effective_evidence])
    ages: dict[str, int] = {}
    undated = []
    for loss_run in submission.loss_runs:
        valued = cite(loss_run.valued_as_of, "loss_run.valued_as_of", casefold_key(loss_run.carrier), loss_run.doc_id)
        if valued is None:
            undated.append(loss_run.doc_id)
            continue
        evidence.append(valued)
        age = loss_run_age_days(LossRuns.model_construct(valued_as_of=loss_run.valued_as_of.value), effective)
        ages[loss_run.doc_id] = age
        if age > MAX_LOSS_RUN_AGE_DAYS:
            issues.append(
                CheckIssue(
                    issue="stale_valuation",
                    entity=f"carrier:{casefold_key(loss_run.carrier) or loss_run.doc_id}",
                    fields=["loss_run.valued_as_of", "application.proposed_effective_date"],
                    side_a=[valued],
                    side_b=found_only([effective_evidence]),
                    data={"age_days": age, "max_age_days": MAX_LOSS_RUN_AGE_DAYS},
                    message=f"{carrier_label(loss_run)} is valued {age} days before the effective date (more than "
                    f"{MAX_LOSS_RUN_AGE_DAYS}).",
                )
            )
    values = {"age_days_by_document": ages, "undated_loss_runs": undated, "max_age_days": MAX_LOSS_RUN_AGE_DAYS}
    if issues:
        message = "Loss runs are out of date: " + " ".join(issue.message for issue in issues)
        return make_result("loss_run_valuation_age", refs, "fail", message, values, evidence, issues)
    if not ages:
        message = "No loss run shows a valuation date."
        return make_result("loss_run_valuation_age", refs, "not_evaluated", message, values, evidence)
    message = f"All {len(ages)} dated loss run(s) are valued within {MAX_LOSS_RUN_AGE_DAYS} days of the effective date."
    if undated:
        message += f" Without a valuation date: {', '.join(undated)}."
    return make_result("loss_run_valuation_age", refs, "pass", message, values, evidence)

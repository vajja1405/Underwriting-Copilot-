"""Harborline loss-run checks over an InsuranceSubmission: claim lines, totals and loss history (HM-3.3, 11.1).

Formulas come from agent/insurance_calculations.py and thresholds from agent/policy_insurance.py. Coverage and
valuation age are in checks_loss_run_coverage.py.

- loss_run_line_balance (HM-3.3, HM-11.1): on every claim line, paid + reserve must equal incurred within $1.00
  (insurance_calculations.claim_out_of_balance). A line that does not is a document integrity indicator.
- loss_run_totals (HM-11.1): each loss run's printed summary must equal its claim lines: the claim count exactly,
  and total paid, reserve and incurred within $1.00. A loss run whose summary is not found is skipped.
- claims_history_consistency (HM-3.3): the claim count and total incurred stated on the application against the
  loss runs. The loss runs are read two ways (LossHistory): by their claim lines ("claim_lines": every claim
  line of every loss run), and by their printed summaries ("printed_summaries": each loss run's summary block,
  or its claim lines when it has no summary). The two readings differ only when a summary does not equal its
  lines, which loss_run_totals already reports as an integrity indicator; so the application matches when it
  agrees with EITHER reading, and the same disagreement is not reported twice (for example a summary that
  counts a claim with no claim line, or claim lines the extractor did not read). The application agrees with a
  reading when the claim count is equal and the total incurred differs by 10.0% or less of the loss-run total
  (insurance_calculations.incurred_difference_pct). Otherwise the check fails, compared with the claim lines
  (or with the summaries when no claim line was found at all). The values also count the current-term claims
  (claim lines with a date of loss on or after the current-term start) for the explanation rules. As in the
  agent's SYNTHETIC submissions, the current term starts one year before the proposed effective date.
"""

from dataclasses import dataclass, field
from datetime import date

from rapidfuzz import fuzz

from uwcopilot.agent.applications import Claim
from uwcopilot.agent.insurance_calculations import (
    RECONCILIATION_TOLERANCE_USD,
    claim_out_of_balance,
    incurred_difference_pct,
)
from uwcopilot.agent.policy_insurance import MAX_INCURRED_DIFFERENCE_PCT
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
from uwcopilot.docintel.normalize import canonical_name
from uwcopilot.docintel.schemas import InsuranceSubmission, LossRun, LossRunClaim, casefold_key

PLACEHOLDER_LOSS_DATE = date(2000, 1, 1)  # claim_out_of_balance reads only paid, reserve and incurred


def years_before(day: date, years: int) -> date:
    """The same calendar day `years` years earlier (29 February becomes 28 February)."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def loss_run_carriers(submission: InsuranceSubmission) -> set[str]:
    """Canonical carrier names of the loss runs in the packet."""
    return {canonical_name(item.carrier.value) for item in submission.loss_runs if item.carrier.found}


CARRIER_NAME_MIN_RATIO = 90.0  # rapidfuzz ratio of two canonical carrier names that differ by an OCR misread


def has_loss_run(carrier: str, carriers: set[str]) -> bool:
    """True when a loss run in the packet is from this carrier (canonical names).

    The names match when they are equal, when one name's words contain the other's ("Example Mutual" and "Example
    Mutual Insurance Co"; an OCR word added at the end), or when they are nearly equal (rapidfuzz ratio of at least
    CARRIER_NAME_MIN_RATIO: "quilifeather mutual" read for "quillfeather mutual"). The fictional carriers' names
    have no words in common, so a carrier that is really missing does not match another one.
    """
    words = set(carrier.split())
    for other in carriers:
        other_words = set(other.split())
        if carrier == other or (words and other_words and (words <= other_words or other_words <= words)):
            return True
        if carrier and other and fuzz.ratio(carrier, other) >= CARRIER_NAME_MIN_RATIO:
            return True
    return False


def every_prior_carrier_has_a_run(submission: InsuranceSubmission) -> bool:
    """True when the packet has at least one loss run and one for every carrier on the application's prior-carriers
    table (HM-3.1 item 3 with HM-3.2: a carrier without its loss run means the loss runs are not complete).

    This is the one rule for "loss runs provided": checks_insurance.documents_provided (the required_documents
    check) and to_application_insurance both use it. An application that is not in the packet names no carrier.
    """
    carriers = loss_run_carriers(submission)
    if not submission.loss_runs:
        return False
    application = submission.application
    if application is None:
        return True
    for row in application.prior_carriers:
        if row.carrier.found and not has_loss_run(canonical_name(row.carrier.value), carriers):
            return False
    return True


def carrier_label(loss_run: LossRun) -> str:
    """A loss run label for messages: its document id (carrier names stay in the evidence)."""
    return loss_run.doc_id


def claim_key(claim: LossRunClaim) -> str | None:
    """A claim's row key: its claim number as printed, stripped."""
    return None if not claim.claim_number.found else str(claim.claim_number.value).strip()


def claim_evidence(claim: LossRunClaim, doc_id: str, names: tuple[str, ...]) -> list[Evidence]:
    """Evidence for the named claim fields (key = claim number)."""
    key = claim_key(claim)
    return found_only([cite(getattr(claim, name), f"loss_run.claims.{name}", key, doc_id) for name in names])


def line_out_of_balance(claim: LossRunClaim) -> bool:
    """HM-3.3 through insurance_calculations.claim_out_of_balance (False when a value is missing)."""
    if not (claim.paid.found and claim.reserve.found and claim.incurred.found):
        return False
    model = Claim.model_construct(
        date_of_loss=PLACEHOLDER_LOSS_DATE,
        line="property",
        paid=float(claim.paid.value),
        reserve=float(claim.reserve.value),
        incurred=float(claim.incurred.value),
    )
    return claim_out_of_balance(model)


def check_line_balance(submission: InsuranceSubmission) -> CheckResult:
    """HM-3.3 / HM-11.1: paid + reserve = incurred on every claim line."""
    refs = INSURANCE_REFS["loss_run_line_balance"]
    issues = []
    evidence = []
    lines_checked = 0
    for loss_run in submission.loss_runs:
        for claim in loss_run.claims:
            if not (claim.paid.found and claim.reserve.found and claim.incurred.found):
                continue
            lines_checked += 1
            evidence.extend(claim_evidence(claim, loss_run.doc_id, ("incurred",)))
            if not line_out_of_balance(claim):
                continue
            paid, reserve, incurred = (float(claim.paid.value), float(claim.reserve.value), float(claim.incurred.value))
            difference = round(paid + reserve - incurred, 2)
            issues.append(
                CheckIssue(
                    issue="paid_reserve_incurred",
                    entity=f"carrier:{casefold_key(loss_run.carrier) or loss_run.doc_id}",
                    fields=["loss_run.claims.paid", "loss_run.claims.reserve", "loss_run.claims.incurred"],
                    side_a=claim_evidence(claim, loss_run.doc_id, ("paid", "reserve")),
                    side_b=claim_evidence(claim, loss_run.doc_id, ("incurred",)),
                    data={
                        "claim_number": claim_key(claim),
                        "paid": paid,
                        "reserve": reserve,
                        "incurred": incurred,
                        "difference": difference,
                    },
                    message=f"On {carrier_label(loss_run)} claim {claim_key(claim)} shows paid ${paid:,.2f} + reserve "
                    f"${reserve:,.2f} but incurred ${incurred:,.2f}.",
                )
            )
    values = {"claim_lines_checked": lines_checked, "tolerance_usd": RECONCILIATION_TOLERANCE_USD}
    if issues:
        message = "Claim lines do not balance (integrity indicator): " + " ".join(issue.message for issue in issues)
        return make_result("loss_run_line_balance", refs, "fail", message, values, evidence, issues)
    if lines_checked == 0:
        message = "No claim line with paid, reserve and incurred was found."
        evidence = [document_evidence(item, "document", "loss_run") for item in submission.loss_runs]
        return make_result("loss_run_line_balance", refs, "not_evaluated", message, values, evidence)
    message = f"All {lines_checked} claim lines balance within $1.00."
    return make_result("loss_run_line_balance", refs, "pass", message, values, evidence)


def summary_differences(loss_run: LossRun) -> dict[str, tuple[float, float]]:
    """Summary item -> (printed, sum of the claim lines) for every summary value that disagrees with the lines."""
    claims = loss_run.claims
    summary = loss_run.summary
    differences: dict[str, tuple[float, float]] = {}
    if summary.claim_count.found and int(summary.claim_count.value) != len(claims):
        differences["claim_count"] = (float(summary.claim_count.value), float(len(claims)))
    for total_name, line_name in (("total_paid", "paid"), ("total_reserve", "reserve"), ("total_incurred", "incurred")):
        printed = getattr(summary, total_name)
        if not printed.found:
            continue
        line_sum = round(sum(float(getattr(claim, line_name).value or 0.0) for claim in claims), 2)
        if abs(float(printed.value) - line_sum) > RECONCILIATION_TOLERANCE_USD:
            differences[total_name] = (float(printed.value), line_sum)
    return differences


def summary_evidence(loss_run: LossRun) -> list[Evidence]:
    """Evidence for the printed summary block (key = carrier)."""
    key = casefold_key(loss_run.carrier)
    names = ("claim_count", "total_paid", "total_reserve", "total_incurred")
    summary = loss_run.summary
    return found_only(
        [cite(getattr(summary, name), f"loss_run.summary.{name}", key, loss_run.doc_id) for name in names]
    )


def check_totals(submission: InsuranceSubmission) -> CheckResult:
    """HM-11.1: each loss run's summary equals its claim lines (see the module docstring)."""
    refs = INSURANCE_REFS["loss_run_totals"]
    issues = []
    evidence = []
    checked = 0
    for loss_run in submission.loss_runs:
        printed = summary_evidence(loss_run)
        if not printed:
            continue
        checked += 1
        evidence.extend(printed)
        differences = summary_differences(loss_run)
        if not differences:
            continue
        rows = []
        for claim in loss_run.claims:
            rows.extend(claim_evidence(claim, loss_run.doc_id, ("incurred",)))
        parts = [f"{name} {pair[0]:,.2f} vs lines {pair[1]:,.2f}" for name, pair in differences.items()]
        issues.append(
            CheckIssue(
                issue="summary_vs_lines",
                entity=f"carrier:{casefold_key(loss_run.carrier) or loss_run.doc_id}",
                fields=[f"loss_run.summary.{name}" for name in differences] + ["loss_run.claims.incurred"],
                side_a=printed,
                side_b=rows,
                data={
                    "differences": {name: list(pair) for name, pair in differences.items()},
                    "claim_lines": len(loss_run.claims),
                },
                message=f"The summary on {carrier_label(loss_run)} does not equal its claim lines: "
                + "; ".join(parts)
                + ".",
            )
        )
    values = {"loss_runs_checked": checked}
    if issues:
        message = "Loss-run totals differ from the claim lines (integrity indicator): " + " ".join(
            issue.message for issue in issues
        )
        return make_result("loss_run_totals", refs, "fail", message, values, evidence, issues)
    if checked == 0:
        return make_result("loss_run_totals", refs, "not_evaluated", "No loss-run summary was found.", values, [])
    message = f"The summary of each of the {checked} loss run(s) equals its claim lines."
    return make_result("loss_run_totals", refs, "pass", message, values, evidence)


def current_term_start(submission: InsuranceSubmission) -> date | None:
    """Start of the current policy term: one year before the proposed effective date (None if unknown)."""
    application = submission.application
    if application is None or not application.proposed_effective_date.found:
        return None
    return years_before(application.proposed_effective_date.value, 1)


@dataclass(frozen=True)
class LossHistory:
    """Claim count and total incurred over all loss runs, read one way (see the module docstring)."""

    basis: str  # "claim_lines" or "printed_summaries"
    claim_count: int
    total_incurred: float
    evidence: list[Evidence] = field(default_factory=list)


def lines_history(submission: InsuranceSubmission) -> LossHistory:
    """Every claim line of every loss run."""
    count = 0
    incurred = 0.0
    evidence: list[Evidence] = []
    for loss_run in submission.loss_runs:
        for claim in loss_run.claims:
            count += 1
            incurred += float(claim.incurred.value or 0.0)
            evidence.extend(claim_evidence(claim, loss_run.doc_id, ("incurred",)))
    return LossHistory("claim_lines", count, round(incurred, 2), evidence)


def summaries_history(submission: InsuranceSubmission) -> LossHistory:
    """Each loss run's printed summary, or its claim lines when it has no summary."""
    count = 0
    incurred = 0.0
    evidence: list[Evidence] = []
    for loss_run in submission.loss_runs:
        summary = loss_run.summary
        if summary.claim_count.found or summary.total_incurred.found:
            count += int(summary.claim_count.value or 0)
            incurred += float(summary.total_incurred.value or 0.0)
            evidence.extend(summary_evidence(loss_run))
            continue
        for claim in loss_run.claims:
            count += 1
            incurred += float(claim.incurred.value or 0.0)
            evidence.extend(claim_evidence(claim, loss_run.doc_id, ("incurred",)))
    return LossHistory("printed_summaries", count, round(incurred, 2), evidence)


def current_term_claims(submission: InsuranceSubmission) -> int | None:
    """Claim lines with a date of loss in the current term (None when the effective date is unknown)."""
    start = current_term_start(submission)
    if start is None:
        return None
    count = 0
    for loss_run in submission.loss_runs:
        for claim in loss_run.claims:
            if claim.date_of_loss.value is not None and claim.date_of_loss.value >= start:
                count += 1
    return count


def agrees(stated_count: int, stated_incurred: float, history: LossHistory) -> bool:
    """HM-3.3: the same claim count, and total incurred within 10.0% of the loss-run total."""
    if stated_count != history.claim_count:
        return False
    difference_pct = incurred_difference_pct(stated_incurred, history.total_incurred)
    if difference_pct is None:
        return stated_incurred <= RECONCILIATION_TOLERANCE_USD
    return difference_pct <= MAX_INCURRED_DIFFERENCE_PCT


def readings_differ(first: LossHistory, second: LossHistory) -> bool:
    """True when two readings of the loss runs give a different claim count or total incurred."""
    if first.claim_count != second.claim_count:
        return True
    return first.total_incurred != second.total_incurred


def reported_reading(by_lines: LossHistory, by_summaries: LossHistory) -> LossHistory:
    """The reading a failing check reports: the claim lines, or the summaries when no claim line was found."""
    if by_lines.claim_count == 0 and by_summaries.claim_count > 0:
        return by_summaries
    return by_lines


def check_claims_history(submission: InsuranceSubmission) -> CheckResult:
    """HM-3.3: application claim count and total incurred vs the loss runs (two readings; module docstring)."""
    refs = INSURANCE_REFS["claims_history_consistency"]
    application = submission.application
    if application is None or not submission.loss_runs:
        message = "The application and at least one loss run are needed."
        return make_result("claims_history_consistency", refs, "not_evaluated", message, {}, [])
    stated = found_only(
        [
            cite(application.stated_claim_count, "application.stated_claim_count", None, application.doc_id),
            cite(application.stated_total_incurred, "application.stated_total_incurred", None, application.doc_id),
        ]
    )
    by_lines = lines_history(submission)
    by_summaries = summaries_history(submission)
    if not application.stated_claim_count.found or not application.stated_total_incurred.found:
        message = "The application's claim count or total incurred was not found."
        evidence = stated + by_lines.evidence
        return make_result("claims_history_consistency", refs, "not_evaluated", message, {}, evidence)
    stated_count = int(application.stated_claim_count.value)
    stated_incurred = float(application.stated_total_incurred.value)
    differ = readings_differ(by_lines, by_summaries)
    for history in (by_lines, by_summaries):
        if agrees(stated_count, stated_incurred, history):
            values = {
                "stated_claim_count": stated_count,
                "stated_total_incurred": stated_incurred,
                "basis": history.basis,
                "loss_run_claim_count": history.claim_count,
                "loss_run_total_incurred": history.total_incurred,
                "readings_differ": differ,
            }
            message = (
                f"The application's {stated_count} claim(s) and total incurred match the loss runs "
                f"(read by their {history.basis.replace('_', ' ')})."
            )
            evidence = stated + history.evidence
            return make_result("claims_history_consistency", refs, "pass", message, values, evidence)
    compared = reported_reading(by_lines, by_summaries)
    difference_pct = incurred_difference_pct(stated_incurred, compared.total_incurred)
    values = {
        "stated_claim_count": stated_count,
        "stated_total_incurred": stated_incurred,
        "basis": compared.basis,
        "loss_run_claim_count": compared.claim_count,
        "loss_run_total_incurred": compared.total_incurred,
        "incurred_difference_pct": difference_pct,
        "current_term_claim_count": current_term_claims(submission),
        "readings_differ": differ,
        "summary_claim_count": by_summaries.claim_count,
        "summary_total_incurred": by_summaries.total_incurred,
    }
    shown_pct = "n/a" if difference_pct is None else f"{difference_pct:.1f}%"
    loss_run_fields = sorted({item.field for item in compared.evidence}) or ["loss_run.claims.incurred"]
    issue = CheckIssue(
        issue="history_differs",
        entity="business",
        fields=["application.stated_claim_count", "application.stated_total_incurred", *loss_run_fields],
        side_a=stated,
        side_b=compared.evidence,
        data=dict(values),
        message=f"The application states {stated_count} claim(s) and ${stated_incurred:,.2f} incurred; the loss runs "
        f"show {compared.claim_count} claim(s) and ${compared.total_incurred:,.2f} (incurred difference "
        f"{shown_pct}).",
    )
    return make_result("claims_history_consistency", refs, "fail", issue.message, values, [], [issue])

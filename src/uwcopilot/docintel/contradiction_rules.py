"""Deterministic explanation rules: is a contradiction benign, unsupported, or unknown? (no LLM)

Each failing check issue becomes a contradiction (contradictions.py). This module decides its explanation from
the numbers the check recorded, using only what the documents show. The explanation is a CANDIDATE for a human
reviewer, never a decision. Rules, by contradiction type (the first rule that matches wins):

- revenue_gap (gap above 10.0%, NBC-3.4; the numbers come from revenue_vs_deposits):
  1. benign / non_revenue_deposits_counted: |stated - gross deposits| <= 2% of stated, and the identified
     non-revenue deposits (transfers, refunds, loan proceeds, owner contributions, one-time receipts) are at
     least half of the gap (stated - verified);
  2. benign / unidentified_deposits: deposits of unknown nature (NBC-3.3 excludes them) are at least half of
     the gap;
  3. benign / fiscal_year_timing: a revenue figure for another period is within 2% of stated: a tax return's
     gross receipts, or the P&L revenue when the P&L ends in a different month from the last statement;
  4. unsupported / no_supporting_document: stated exceeds gross deposits, the P&L revenue and every tax return's
     gross receipts (those present) by more than 10% (stated > 1.10 x the largest of them);
  5. otherwise unknown / insufficient_evidence.
  A tax_return_vs_pnl difference (NBC-9.1) is also typed revenue_gap; it is an integrity indicator, so its
  explanation is unknown / tax_return_differs.
- name_mismatch / fein_mismatch: benign / transcription_error when exactly two values exist, the odd value is on
  one document only, and it is a small edit of the majority value (names: edit distance 2 or less on the
  comparison key; FEIN: one digit changed, or two adjacent digits swapped). Otherwise unsupported /
  no_supporting_document.
- owner_mismatch: an ownership percentage difference is benign / transcription_error only when an exact swap of
  two adjacent digits explains it (34% vs 43%); a differently written owner name (paired by edit distance 2 or
  less) is benign / transcription_error; a missing owner or a different control person is unsupported /
  no_supporting_document; any other percentage difference is unsupported.
- debt_payment_mismatch: benign / rule_resolved (NBC-4.1 already uses the higher of the two payments).
- tiv_mismatch (application vs schedule): benign / outdated_application when the cover email mentions an updated
  property schedule, statement of values or replacement-cost update (UPDATE_PATTERNS). The email is untrusted
  data, so this is only a candidate explanation for the reviewer to confirm with the updated schedule. Otherwise
  unsupported / no_supporting_document when the difference is above 15.0% (HM-5.2 referral band), else unknown /
  insufficient_evidence. A schedule arithmetic difference is unknown / arithmetic.
- claims_history_mismatch: benign / current_term_claims_omitted when the loss runs show more claims than the
  application and the difference equals the number of current-term claims; otherwise unsupported /
  no_supporting_document.
- loss_run_total_mismatch: unsupported / claim_missing_from_lines when the summary counts MORE claims than the
  claim lines show and every printed total is at least the lines' sum (incurred strictly more): the carrier's own
  summary reports a claim (and its loss) that the itemized lines leave out, so the itemized loss history
  understates it. Any other summary difference (same count, or totals below the lines) is unknown / arithmetic.
  (An OCR page can lose a claim row too; contradiction_ocr.py reviews this verdict for loss runs read by OCR.)
- paid_reserve_mismatch, balance_discontinuity: unknown / arithmetic. These are document integrity indicators
  (NBC-9.1, HM-11.1) that a human must review; "integrity" is not an explanation value.
- prompt_injection: unknown / embedded_instruction (an integrity indicator for Fraud Operations / the SIU).
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from uwcopilot.agent.policy_insurance import TIV_REFER_PCT
from uwcopilot.docintel.check_models import CheckIssue, Evidence, cite
from uwcopilot.docintel.normalize import digits_only, edit_distance, normalize_text
from uwcopilot.docintel.schemas import InsuranceSubmission, PacketExtraction

Explanation = Literal["benign", "unsupported", "unknown"]

NEAR_SHARE = 0.02  # "within 2%" of the stated figure
HALF_OF_GAP = 0.5  # deposits that explain at least half of the gap
OVERSTATEMENT_FACTOR = 1.10  # stated more than 10% above every supporting figure
NAME_MAX_EDITS = 2
FEIN_DIGITS = 9
# Cover-email phrases that mention an updated schedule or values (HM-5.2 benign outdated application).
# "new" and "current" are left out on purpose: "new submission ... schedule" is ordinary broker wording.
UPDATE_WORDS = r"(?:updated|revised|refreshed|corrected|latest)"
UPDATE_TARGETS = r"(?:property schedule|schedule|statement of values|sov|replacement costs?|values|valuations?)"
UPDATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b{UPDATE_WORDS}\b(?:\W+\w+){{0,4}}?\W+{UPDATE_TARGETS}\b", re.IGNORECASE),
    re.compile(rf"\b{UPDATE_TARGETS}\b(?:\W+\w+){{0,4}}?\W+(?:updated|revised|refreshed|corrected)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class Verdict:
    """The explanation chosen for one contradiction, with any extra evidence the rule relied on."""

    explanation: Explanation
    code: str
    detail: str
    supporting: list[Evidence] = field(default_factory=list)


def money(value: float | None) -> str:
    """A dollar amount for an explanation sentence."""
    return "n/a" if value is None else f"${value:,.2f}"


def near(value: float, target: float) -> bool:
    """True when value is within 2% of target (target > 0)."""
    return abs(value - target) <= NEAR_SHARE * target


def other_period_figures(data: dict) -> list[tuple[str, float]]:
    """(label, amount) for revenue figures that cover another period than the 12 statement months."""
    figures = [(f"tax year {year} gross receipts", float(amount)) for year, amount in data["tax_receipts"].items()]
    pnl_end, statements_end = data.get("pnl_period_end"), data.get("statements_end")
    if data.get("pnl_revenue") is not None and pnl_end and statements_end and pnl_end[:7] != statements_end[:7]:
        figures.append((f"P&L revenue for the period ended {pnl_end}", float(data["pnl_revenue"])))
    return figures


def explain_revenue_gap(data: dict) -> Verdict:
    """Revenue-gap rules 1-5 (module docstring). Every detail starts with the stated and verified figures."""
    stated = float(data["stated_annual_revenue"])
    verified = float(data["verified_annual_revenue"])
    gross = float(data["gross_deposits"])
    identified = float(data["identified_non_revenue"])
    unidentified = float(data["unidentified_deposits"])
    gap = stated - verified
    opening = f"Stated revenue {money(stated)} vs verified {money(verified)} (gap {money(gap)}). "
    if near(gross, stated) and identified >= HALF_OF_GAP * gap:
        detail = (
            f"{opening}Stated is within 2% of gross deposits {money(gross)}, and identified non-revenue deposits of "
            f"{money(identified)} cover at least half of the gap: the stated figure appears to count transfers, "
            "refunds or loan proceeds as revenue."
        )
        return Verdict("benign", "non_revenue_deposits_counted", detail)
    if unidentified >= HALF_OF_GAP * gap:
        detail = (
            f"{opening}Deposits of unknown nature total {money(unidentified)}, at least half of the gap; NBC-3.3 "
            "excludes them until the applicant documents them, so documenting them may close the gap."
        )
        return Verdict("benign", "unidentified_deposits", detail)
    for label, amount in other_period_figures(data):
        if near(amount, stated):
            detail = (
                f"{opening}Stated is within 2% of the {label} ({money(amount)}): the stated figure appears to be "
                "for a different period than the 12 statement months."
            )
            return Verdict("benign", "fiscal_year_timing", detail)
    supports = [("gross deposits", gross)]
    if data.get("pnl_revenue") is not None:
        supports.append(("P&L revenue", float(data["pnl_revenue"])))
    supports.extend((f"tax year {year} receipts", float(amount)) for year, amount in data["tax_receipts"].items())
    largest = max(amount for _, amount in supports)
    if stated > OVERSTATEMENT_FACTOR * largest:
        listed = ", ".join(f"{label} {money(amount)}" for label, amount in supports)
        detail = f"{opening}Stated is more than 10% above every supporting figure ({listed})."
        return Verdict("unsupported", "no_supporting_document", detail)
    detail = (
        f"{opening}No documented rule explains the gap, and some document supports a figure within 10% of the "
        "stated one."
    )
    return Verdict("unknown", "insufficient_evidence", detail)


def fein_typo(first: str, second: str) -> bool:
    """True when two 9-digit FEINs differ by one digit or by one swap of two adjacent digits."""
    a, b = digits_only(first), digits_only(second)
    if len(a) != FEIN_DIGITS or len(b) != FEIN_DIGITS:
        return False
    positions = [index for index in range(FEIN_DIGITS) if a[index] != b[index]]
    if len(positions) == 1:
        return True
    if len(positions) == 2 and positions[1] == positions[0] + 1:
        first_pos, second_pos = positions
        return a[first_pos] == b[second_pos] and a[second_pos] == b[first_pos]
    return False


def explain_value_differs(check_id: str, data: dict) -> Verdict:
    """name_mismatch / fein_mismatch rule (module docstring)."""
    odd_docs = list(data["odd_doc_ids"])
    majority_docs = list(data["majority_doc_ids"])
    single_odd = data["group_count"] == 2 and len(odd_docs) == 1
    if check_id == "fein_consistency":
        small = fein_typo(str(data["majority_value"]), str(data["odd_value"]))
        label, change = "FEIN", "one digit changed or two adjacent digits swapped"
    else:
        small = edit_distance(str(data["majority_key"]), str(data["odd_key"])) <= NAME_MAX_EDITS
        label, change = "business name", f"within {NAME_MAX_EDITS} character edits"
    if single_odd and small:
        detail = (
            f"The {label} on {odd_docs[0]} is {change} of the one on {len(majority_docs)} other document(s), which "
            "all agree: a transcription error on one document is the likely explanation."
        )
        return Verdict("benign", "transcription_error", detail)
    detail = (
        f"The {label} differs on {', '.join(odd_docs)} from {len(majority_docs)} other document(s), and the "
        "difference is not a small transcription error on a single document."
    )
    return Verdict("unsupported", "no_supporting_document", detail)


def digit_transposition(first: float, second: float) -> bool:
    """True when the two percentages, as printed (34 or 12.5), differ by one swap of two adjacent digits."""
    a, b = f"{first:g}", f"{second:g}"
    if len(a) != len(b) or a == b:
        return False
    positions = [index for index in range(len(a)) if a[index] != b[index]]
    if len(positions) != 2 or positions[1] != positions[0] + 1:
        return False
    first_pos, second_pos = positions
    swapped = a[first_pos] == b[second_pos] and a[second_pos] == b[first_pos]
    return swapped and a[first_pos].isdigit() and a[second_pos].isdigit()


def explain_owner(issue: CheckIssue) -> Verdict:
    """owner_mismatch rule (module docstring)."""
    data = issue.data
    other = data.get("other_doc_id") or "the other document"
    owner = issue.entity.replace("owner:", "Owner ")
    if issue.issue == "pct_difference":
        app_pct, other_pct = float(data["application_pct"]), float(data["other_pct"])
        if digit_transposition(app_pct, other_pct):
            detail = (
                f"{owner} holds {app_pct:g}% on the application and {other_pct:g}% on {other}: two adjacent "
                "digits are swapped, so a transcription error is the likely explanation."
            )
            return Verdict("benign", "transcription_error", detail)
        detail = (
            f"{owner} holds {app_pct:g}% on the application and {other_pct:g}% on {other} "
            f"({data['difference_points']:g} points apart); no document explains the difference (NBC-6.1)."
        )
        return Verdict("unsupported", "no_supporting_document", detail)
    if issue.issue == "name_difference":
        detail = (
            f"{owner}'s name on {other} is within {data['edit_distance']} character edit(s) of the "
            "application: a transcription error is the likely explanation."
        )
        return Verdict("benign", "transcription_error", detail)
    detail = f"{issue.message} No document in the packet explains it."
    return Verdict("unsupported", "no_supporting_document", detail)


def explain_debt_payment(data: dict) -> Verdict:
    """debt_payment_mismatch rule: NBC-4.1 resolves it."""
    detail = (
        f"The schedule shows {money(data['scheduled_payment'])} a month and the statements "
        f"{money(data['observed_payment'])}; NBC-4.1 uses the higher of the two in debt service, so the difference "
        "is resolved by the guideline."
    )
    return Verdict("benign", "rule_resolved", detail)


def cover_email_update(extraction: PacketExtraction) -> Evidence | None:
    """Evidence of the cover email text that mentions an updated schedule (None when it does not)."""
    if not isinstance(extraction, InsuranceSubmission) or extraction.cover_email is None:
        return None
    email = extraction.cover_email
    for name in ("body", "subject"):
        cited = getattr(email, name)
        if not cited.found:
            continue
        text = normalize_text(str(cited.value))
        if any(pattern.search(text) for pattern in UPDATE_PATTERNS):
            return cite(cited, f"cover_email.{name}", None, email.doc_id)
    return None


def explain_tiv(issue: CheckIssue, extraction: PacketExtraction) -> Verdict:
    """tiv_mismatch rule (module docstring)."""
    if issue.issue != "tiv_differs":
        detail = f"{issue.message} This is a schedule arithmetic difference that a human must review (HM-11.1)."
        return Verdict("unknown", "arithmetic", detail)
    data = issue.data
    difference = float(data["difference_pct"])
    numbers = (
        f"The application TIV {money(data['stated_tiv'])} and the schedule TIV {money(data['schedule_tiv'])} "
        f"differ by {difference:.1f}%"
    )
    email = cover_email_update(extraction)
    if email is not None:
        detail = (
            f"{numbers}. The cover email (untrusted text) mentions an updated schedule or values, so an outdated "
            "application figure is a CANDIDATE explanation to confirm with the updated, signed schedule."
        )
        return Verdict("benign", "outdated_application", detail, [email])
    if difference > TIV_REFER_PCT:
        detail = f"{numbers}, more than {TIV_REFER_PCT:.1f}%, and nothing in the packet explains it (HM-5.2 referral)."
        return Verdict("unsupported", "no_supporting_document", detail)
    detail = f"{numbers}; nothing in the packet explains it, and it is within the {TIV_REFER_PCT:.1f}% referral band."
    return Verdict("unknown", "insufficient_evidence", detail)


def explain_claims_history(data: dict) -> Verdict:
    """claims_history_mismatch rule (module docstring)."""
    stated = int(data["stated_claim_count"])
    found = int(data["loss_run_claim_count"])
    current = data.get("current_term_claim_count")
    if current and found > stated and found - stated == current:
        detail = (
            f"The loss runs show {found} claim(s) and the application {stated}; the difference equals the {current} "
            "current-term claim(s), which the application appears to leave out."
        )
        return Verdict("benign", "current_term_claims_omitted", detail)
    detail = (
        f"The application states {stated} claim(s) and {money(data['stated_total_incurred'])} incurred; the loss runs "
        f"show {found} and {money(data['loss_run_total_incurred'])}. Nothing in the packet explains the difference."
    )
    return Verdict("unsupported", "no_supporting_document", detail)


def explain_loss_run_totals(issue: CheckIssue) -> Verdict:
    """loss_run_total_mismatch rule (module docstring). data["differences"]: name -> [printed, lines' sum]."""
    differences = issue.data.get("differences", {})
    count = differences.get("claim_count")
    incurred = differences.get("total_incurred")
    totals_not_below = all(pair[0] >= pair[1] for name, pair in differences.items() if name != "claim_count")
    if count is not None and count[0] > count[1] and incurred is not None and incurred[0] > incurred[1]:
        if totals_not_below:
            missing = int(count[0] - count[1])
            detail = (
                f"{issue.message} The summary counts {missing} more claim(s) than the claim lines and "
                f"{money(incurred[0] - incurred[1])} more incurred: the itemized lines appear to leave out a claim "
                "the carrier reports, so the listed loss history understates it (HM-3.3)."
            )
            return Verdict("unsupported", "claim_missing_from_lines", detail)
    detail = f"{issue.message} This is a document integrity indicator that a human must review."
    return Verdict("unknown", "arithmetic", detail)


def explain(check_id: str, issue: CheckIssue, extraction: PacketExtraction) -> Verdict:
    """The explanation for one failing check issue (dispatch on the check id; module docstring)."""
    if check_id == "revenue_vs_deposits":
        return explain_revenue_gap(issue.data)
    if check_id == "tax_return_vs_pnl":
        return Verdict("unknown", "tax_return_differs", f"{issue.message} This is an integrity indicator (NBC-9.1).")
    if check_id in ("name_consistency", "fein_consistency"):
        return explain_value_differs(check_id, issue.data)
    if check_id == "owner_consistency":
        return explain_owner(issue)
    if check_id == "debt_payment_consistency":
        return explain_debt_payment(issue.data)
    if check_id == "tiv_consistency":
        return explain_tiv(issue, extraction)
    if check_id == "claims_history_consistency":
        return explain_claims_history(issue.data)
    if check_id == "loss_run_totals":
        return explain_loss_run_totals(issue)
    if check_id == "embedded_instructions":
        detail = f"{issue.message} Embedded instructions are never followed; a human must review the document."
        return Verdict("unknown", "embedded_instruction", detail)
    detail = f"{issue.message} This is a document integrity indicator that a human must review."
    return Verdict("unknown", "arithmetic", detail)

"""OCR review of a contradiction's explanation: could an OCR misread explain the disagreement? (no LLM)

contradiction_rules.explain decides from the values AS READ. A value read by OCR can be wrong, and a disagreement
built on a misread is not evidence of an unsupported figure or of tampering. So after the explanation rule,
review() applies these rules to every contradiction except embedded instructions (the first rule that matches wins):

1. low_confidence: a value on either side comes from a document read by OCR (DocumentInfo.ocr_used) and its line's
   confidence is below LOW_CONFIDENCE (text-layer values have confidence 1.0, OCR values the mean Tesseract word
   confidence of their line);
2. rows_read_by_ocr: a loss_run_total_mismatch explained "unsupported" (the summary counts a claim the lines do not
   show) on a loss run read by OCR: OCR can drop a whole claim row without leaving a low-confidence value behind,
   and a dropped row looks exactly like a missing claim;
3. owners_do_not_add_up: an owner_mismatch that says an owner is MISSING from a table (missing_from_application,
   missing_from_other), where that table was read by OCR and its percentages do not add up to 100% (within 0.5
   points) or one was not read: OCR has most likely lost a row of that table. A percentage difference
   (a transposed 34% / 43%) is not covered: it says nothing about a lost row. The Harborline ownership statement
   lists only owners of 25% or more, so its total is not tested;
4. owner_name_noise: an owner_mismatch about a missing owner or a different control person, where one of the two
   documents was read by OCR and the owner's counterpart is there under a name that differs only by whole words at
   the end ("John Cox" vs "John Cox Ros": OCR read a speck as a word) with the same percentage: the owners would
   pair up but for the OCR word.

When a rule matches, the contradiction's explanation becomes "unknown" with explanation_code
"possible_ocr_misread", its severity drops one level (high -> medium -> low), and the detail names the value or
document and then says what the explanation rule alone would have concluded. The failing check itself is not
changed: the documents disagree as read, and a human confirms against the page image. Like every explanation,
this is a candidate for the reviewer, never a decision.

LOW_CONFIDENCE (0.80) was chosen on the DEV split only, never the test split: for the contradictions of dev packets
with scanned pages, the lowest OCR confidence of their side values was compared with whether they match an injected
problem. Most contradictions that match no problem had a side value below 0.80 and few matching ones did; at 0.85
noticeably more matching ones would have been downgraded. The per-contradiction explanation codes are in the
evaluation's per-packet records, so the trade-off can be re-checked.
"""

from uwcopilot.docintel.check_models import CheckIssue, Evidence, Severity
from uwcopilot.docintel.contradiction_rules import Verdict
from uwcopilot.docintel.normalize import canonical_name
from uwcopilot.docintel.schemas import ExtractedOwner, InsuranceSubmission, LoanPacket, PacketExtraction

LOW_CONFIDENCE = 0.80  # OCR line confidence below which a cited value may be misread
OWNERS_TOTAL_PCT = 100.0
OWNERS_TOTAL_TOLERANCE = 0.5
LOWER_SEVERITY: dict[str, Severity] = {"high": "medium", "medium": "low", "low": "low"}
NOT_REVIEWED_CHECKS: tuple[str, ...] = ("embedded_instructions",)


def ocr_documents(extraction: PacketExtraction) -> set[str]:
    """The ids of the documents whose pages were read by OCR."""
    return {info.doc_id for info in extraction.documents if info.ocr_used}


def least_confident(evidence: list[Evidence], scanned: set[str]) -> Evidence | None:
    """The side value from an OCR document with the lowest confidence below LOW_CONFIDENCE, or None."""
    low = []
    for item in evidence:
        if item.doc_id in scanned and item.confidence is not None and item.confidence < LOW_CONFIDENCE:
            low.append(item)
    if not low:
        return None
    return min(low, key=lambda item: item.confidence or 0.0)


def owners_add_up(owners: list[ExtractedOwner]) -> bool:
    """True when every percentage of the table was read and they add up to 100% (within 0.5 points)."""
    if not owners or any(not owner.ownership_pct.found for owner in owners):
        return False
    total = sum(float(owner.ownership_pct.value) for owner in owners)
    return abs(total - OWNERS_TOTAL_PCT) <= OWNERS_TOTAL_TOLERANCE


def owner_tables(extraction: PacketExtraction) -> list[tuple[str, list[ExtractedOwner]]]:
    """(doc_id, owners) of the owners tables that must add up to 100%: the application's and the formation's."""
    tables = []
    if isinstance(extraction, LoanPacket):
        if extraction.application is not None:
            tables.append((extraction.application.doc_id, extraction.application.owners))
        if extraction.formation is not None:
            tables.append((extraction.formation.doc_id, extraction.formation.members))
    elif isinstance(extraction, InsuranceSubmission) and extraction.application is not None:
        tables.append((extraction.application.doc_id, extraction.application.owners))
    return tables


def table_missing_the_owner(issue: CheckIssue, extraction: PacketExtraction) -> str | None:
    """The id of the document whose owners table lacks the owner of a missing_from_* issue (None otherwise)."""
    if issue.issue == "missing_from_other":
        return issue.data.get("other_doc_id")
    if issue.issue == "missing_from_application" and extraction.application is not None:
        return extraction.application.doc_id
    return None


def incomplete_ocr_owner_table(issue: CheckIssue, extraction: PacketExtraction) -> str | None:
    """The id of the OCR-read owners table that lacks the owner and does not add up to 100% (rule 3), or None."""
    lacking = table_missing_the_owner(issue, extraction)
    if lacking is None or lacking not in ocr_documents(extraction):
        return None
    for doc_id, owners in owner_tables(extraction):
        if doc_id == lacking and not owners_add_up(owners):
            return doc_id
    return None


def every_owner_table(extraction: PacketExtraction) -> dict[str, list[ExtractedOwner]]:
    """doc_id -> owners of every owners table in the packet (application, formation, ownership statement)."""
    tables = dict(owner_tables(extraction))
    if isinstance(extraction, InsuranceSubmission) and extraction.ownership_statement is not None:
        tables[extraction.ownership_statement.doc_id] = extraction.ownership_statement.owners
    return tables


def words_extend(first: str, second: str) -> bool:
    """True when two different names agree word for word except for extra words at the end of one of them."""
    a, b = canonical_name(first).split(), canonical_name(second).split()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 2 and shorter != longer and longer[: len(shorter)] == shorter


def issue_owners(issue: CheckIssue) -> list[tuple[str, str, float]]:
    """(doc_id, name, percentage) of each owner the issue's evidence names (rows joined by their key "O<n>")."""
    rows: dict[tuple[str | None, str | None], dict[str, object]] = {}
    for item in issue.side_a + issue.side_b:
        rows.setdefault((item.doc_id, item.key), {})[item.field.rsplit(".", 1)[-1]] = item.value
    owners = []
    for (doc_id, _), row in rows.items():
        name, pct = row.get("name"), row.get("ownership_pct")
        if doc_id is not None and isinstance(name, str) and isinstance(pct, int | float):
            owners.append((doc_id, name, float(pct)))
    return owners


def noisy_owner_name(issue: CheckIssue, extraction: PacketExtraction) -> str | None:
    """Rule 4: the counterpart of an issue owner, on the other document, differs only by OCR words (or None)."""
    if issue.issue not in ("missing_from_application", "missing_from_other", "control_person_difference"):
        return None
    scanned = ocr_documents(extraction)
    tables = every_owner_table(extraction)
    for doc_id, name, pct in issue_owners(issue):
        for other_doc, owners in tables.items():
            if other_doc == doc_id or not ({doc_id, other_doc} & scanned):
                continue
            for owner in owners:
                same_pct = owner.ownership_pct.found and abs(float(owner.ownership_pct.value) - pct) <= 0.5
                if same_pct and owner.name.found and words_extend(name, str(owner.name.value)):
                    return f"'{name}' on {doc_id} and '{owner.name.value}' on {other_doc}"
    return None


def ocr_reason(check_id: str, issue: CheckIssue, verdict: Verdict, extraction: PacketExtraction) -> str | None:
    """The sentence that says why OCR may explain the contradiction (rules 1-4), or None."""
    low = least_confident(issue.side_a + issue.side_b, ocr_documents(extraction))
    if low is not None:
        return (
            f"The {low.field} value on {low.doc_id} page {low.page} line {low.line} was read by OCR with "
            f"confidence {low.confidence:.2f} (below {LOW_CONFIDENCE:.2f}), so it may be misread."
        )
    side_docs = {item.doc_id for item in issue.side_a + issue.side_b}
    scanned = ocr_documents(extraction) & side_docs
    if check_id == "loss_run_totals" and verdict.explanation == "unsupported" and scanned:
        return (
            f"The loss run ({', '.join(sorted(scanned))}) was read by OCR, which can drop a whole claim row; a "
            "dropped row looks the same as a claim missing from the lines."
        )
    if check_id == "owner_consistency":
        table = incomplete_ocr_owner_table(issue, extraction)
        if table is not None:
            return (
                f"The owners table on {table} was read by OCR and its percentages do not add up to 100%, so a row "
                "was most likely lost or misread."
            )
        names = noisy_owner_name(issue, extraction)
        if names is not None:
            return (
                f"The owner names {names} have the same percentage and differ only by words OCR may have added or "
                "lost, so they are most likely the same owner."
            )
    return None


def review(
    check_id: str, issue: CheckIssue, verdict: Verdict, severity: Severity, extraction: PacketExtraction
) -> tuple[Verdict, Severity]:
    """The verdict and severity after the OCR review (see the module docstring)."""
    if check_id in NOT_REVIEWED_CHECKS:
        return verdict, severity
    reason = ocr_reason(check_id, issue, verdict, extraction)
    if reason is None:
        return verdict, severity
    detail = (
        f"Possible OCR misread: {reason} Without it, the rule would say {verdict.explanation} / {verdict.code}: "
        f"{verdict.detail}"
    )
    return Verdict("unknown", "possible_ocr_misread", detail, verdict.supporting), LOWER_SEVERITY[severity]

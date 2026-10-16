"""The package's printed text: every value, status, range, source and draft exactly as the PDF prints it.

render_pdf and the audit both read their words from here, so the audit reads what the reader sees.
- A value is the provenance renderer's format_value of the claim (usd0 "$84,123", pct1 "25.0%", ratio2 "1.42" ...),
  except that a negative dollar amount is printed the accounting way, "-$53,735", instead of the renderer's
  "$-53,735" (money_sign). The audit reads it back through the same rule (audit.py).
- A draft is filled by provenance.renderer.render (slots -> values with their status suffix); each footnote marker
  becomes "[k]", the claim's row number in the evidence appendix.
- A source is the claim's provenance in square brackets: a reference (dataset, document page, model run, formula,
  inputs hash), printed like a citation and never a figure of the plan.
- Claim labels are shown without their parenthetical notes, with "one-off" written "single" (a word, not a count)
  and document ids in brackets, so a label never prints a figure that is not a claim (labels with thresholds such
  as "at least 1.25" print a value that the ledger holds as its own claim).
"""

import re

from uwcopilot.evidence.ledger_models import Assertion, Claim, Ledger
from uwcopilot.provenance.renderer import FOOTNOTE_MARKER, UNKNOWN_TEXT, format_value, render
from uwcopilot.studio.package.constants import STATUS_WORDS
from uwcopilot.studio.package.models import Cell
from uwcopilot.studio.package.runs import RunIndex

MONEY_DISPLAYS = frozenset({"usd0", "usd2"})
MARKER_NUMBER = re.compile(r"\[\^c(?P<number>\d+)\]")
PARENTHETICAL = re.compile(r"\s*\([^()]*\)")
BRACKETS = str.maketrans({"[": "(", "]": ")"})
DOC_REFERENCE = re.compile(r"\bas printed in (?P<doc>[A-Za-z][A-Za-z0-9_.\-]*)")
MAX_SOURCE_CHARS = 420
RESULT_WORDS = {True: "passed", False: "finding", None: "not evaluated"}
KIND_WORDS: dict[str, str] = {
    "document": "document",
    "calculation": "calculation",
    "model": "model run",
    "policy": "program rule",
    "application": "application field",
    "user_input": "typed by the founder",
    "assumption": "assumption",
    "public_data": "public data",
}


def money_sign(text: str) -> str:
    """'$-53,735' -> '-$53,735' (the accounting way to print a negative amount)."""
    return text.replace("$-", "-$")


def value_text(claim: Claim) -> str:
    """The claim's value as printed (UNKNOWN and empty values print "not available")."""
    if claim.value is None:
        return UNKNOWN_TEXT
    shown = format_value(claim.value, claim.display)
    return money_sign(shown) if claim.display in MONEY_DISPLAYS else shown


def range_text(claim: Claim) -> str:
    """'low to high' in the claim's display format, or '' when the claim has no range."""
    if claim.value_range is None or claim.display == "text":
        return ""
    low = format_value(claim.value_range.low, claim.display)
    high = format_value(claim.value_range.high, claim.display)
    if claim.display in MONEY_DISPLAYS:
        low, high = money_sign(low), money_sign(high)
    return f"{low} to {high}"


def status_text(claim: Claim) -> str:
    """The status in plain words."""
    return STATUS_WORDS[claim.status]


def clean_label(label: str) -> str:
    """A claim label for print (module docstring)."""
    cleaned = PARENTHETICAL.sub("", label).strip()
    cleaned = cleaned.replace("one-off", "single")
    return DOC_REFERENCE.sub(lambda match: f"as printed in [{match.group('doc')}]", cleaned)


def bracketed(parts: list[str]) -> str:
    """'[a; b; c]' with any bracket inside a part turned into a parenthesis (so the citation stays one span)."""
    joined = "; ".join(part.translate(BRACKETS) for part in parts if part)
    if len(joined) > MAX_SOURCE_CHARS:
        joined = joined[: MAX_SOURCE_CHARS - 3].rstrip() + "..."
    return f"[{joined}]"


def source_parts(claim: Claim, runs: RunIndex | None = None) -> list[str]:
    """The provenance of a claim in short parts (no values of the plan). With a RunIndex, a model claim names its
    run ("run A", listed once in the appendix) instead of repeating the run's settings and shared notes."""
    origin = claim.provenance
    label = runs.run_of.get(claim.id) if runs is not None else None
    if label is not None:
        parts = [f"{label}: {origin.ref}"]
    else:
        parts = [f"{KIND_WORDS.get(origin.kind, origin.kind)}: {origin.ref}"]
    if origin.citations:
        parts.append(", ".join(citation.text() for citation in origin.citations))
    if origin.model_name and label is None:
        parts.append(f"{origin.model_name} {origin.model_version or ''}".strip())
        for key in ("seed", "n_paths", "plan_sha256"):
            value = origin.details.get(key)
            if value is not None:
                shown = str(value)[:12] if key == "plan_sha256" else str(value)
                parts.append(f"{key} {shown}")
    if origin.details.get("document_types"):
        parts.append(f"document types {origin.details['document_types']}")
    if origin.source:
        parts.append(f"source: {origin.source}")
    if origin.snapshot_date:
        parts.append(f"snapshot {origin.snapshot_date}")
    if claim.uncertainty and not (runs is not None and runs.is_common(claim)):
        parts.append(f"uncertainty: {claim.uncertainty}")
    if claim.reason:
        parts.append(f"reason: {claim.reason}")
    if claim.conflict_ids:
        parts.append(f"conflicts with {', '.join(claim.conflict_ids)}")
    if claim.depends_on_assumption:
        parts.append(f"rests on {', '.join(claim.depends_on_assumption)}")
    parts.append(f"inputs sha256 {claim.inputs_sha256[:12]}")
    return parts


def source_text(claim: Claim, runs: RunIndex | None = None) -> str:
    """The claim's provenance in square brackets."""
    return bracketed(source_parts(claim, runs))


def assertion_text(assertion: Assertion) -> str:
    """'<phrase> (passed | finding | not evaluated)'."""
    if assertion.result is True:
        phrase = assertion.phrase_true
    elif assertion.result is False:
        phrase = assertion.phrase_false
    else:
        phrase = assertion.phrase_unknown
    return f"{phrase} ({RESULT_WORDS[assertion.result]})"


def result_word(assertion: Assertion) -> str:
    """passed, finding or not evaluated."""
    return RESULT_WORDS[assertion.result]


def reference_text(claim_id: str, appendix_index: dict[str, int]) -> str:
    """'[k: CLAIM.ID]' (the appendix row number and the id)."""
    number = appendix_index.get(claim_id)
    return f"[{number}: {claim_id}]" if number is not None else f"[{claim_id}]"


def render_draft(draft: str, ledger: Ledger, appendix_index: dict[str, int]) -> tuple[str, list[str]]:
    """(the filled draft with "[k]" appendix references, the claim ids in order of first use)."""
    memo = render(draft, ledger)
    used = list(memo.claims_used)

    def marker(match: re.Match[str]) -> str:
        claim_id = used[int(match.group("number")) - 1]
        number = appendix_index.get(claim_id)
        return f"[{number}]" if number is not None else ""

    return money_sign(MARKER_NUMBER.sub(marker, memo.body)), used


def cell_text(cell: Cell, ledger: Ledger, appendix_index: dict[str, int], runs: RunIndex | None = None) -> str:
    """The printed text of one cell (runs: the appendix's model-run index, for source cells)."""
    if cell.kind == "text":
        return cell.text
    if cell.kind == "draft":
        return render_draft(cell.text, ledger, appendix_index)[0]
    if cell.kind == "assert":
        assertion = ledger.assertion(cell.ref)
        return assertion_text(assertion) if assertion is not None else ""
    if cell.kind == "ref":
        return reference_text(cell.ref, appendix_index)
    item = ledger.claim(cell.ref)
    if item is None:
        return ""
    if cell.kind == "claim":
        return value_text(item)
    if cell.kind == "status":
        return status_text(item)
    if cell.kind == "range":
        return range_text(item)
    return source_text(item, runs)


def strip_markers(text: str) -> str:
    """Text without renderer footnote markers (for plain captions)."""
    return FOOTNOTE_MARKER.sub("", text)

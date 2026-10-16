"""Rules extraction of one fictional carrier's SYNTHETIC loss run into a cited LossRun.

Each carrier has its own layout (column order, labels, date format, totals above or below the claims), so nothing
here depends on fixed positions:
- carrier: the letterhead, the first line of the document; carrier_produced: True when a line says
  "Carrier-produced loss run" (cited on that line);
- Insured, Policy number, Valued as of: label/value lines;
- summary: the label/value lines after the "Loss summary" heading, by the label synonyms of SUMMARY_LABELS;
- years: the rows after the "Policy year start  Property  General liability" header that start with a date; the
  two premium cells are matched to the Property / General liability columns (rules_tables.premium_cells);
- claims: every line with a date and at least three table amounts (a premium row has at most two). Words are
  recognised by type: the date is the date of loss; "Property" / "General liability" is the line (as a code); "Open"
  / "Closed" is the status; the remaining word with a digit is the claim number. The three amounts are matched
  to paid / reserve / incurred in the order the claim-table header prints those titles (the header is found again
  on a continuation page; a header word that OCR glued to the next one, "PaidLoss", still counts when the title
  is inside it); claim amounts are never negative, so a minus sign read by OCR is dropped. When no header has been
  read in the document yet, the amounts are named by arithmetic (order_by_arithmetic):
  1. a closed claim prints two equal amounts and 0.00 (paid = incurred, no reserve): the 0.00 is the reserve, and
     the equal pair is paid then incurred, left to right (the two values are the same, so their order does not
     change any value). Only when the row says "Open" is the 0.00 the paid amount (nothing paid yet; reserve =
     incurred);
  2. otherwise the amount equal to the sum of the other two is the incurred amount and the other two are taken as
     paid then reserve, left to right.
"""

from rapidfuzz import fuzz

from uwcopilot.docintel.classify import Segment, normalize_for_match
from uwcopilot.docintel.layout import Word
from uwcopilot.docintel.parsing import parse_amount, parse_date, parse_identifier, parse_int, parse_text
from uwcopilot.docintel.rules_common import (
    LineRef,
    amount_words,
    cite_words,
    document_lines,
    find_line,
    find_value,
    header_index,
    title_box,
    trim_noise,
    words_text,
)
from uwcopilot.docintel.rules_tables import premium_cells
from uwcopilot.docintel.schemas import Cited, LossRun, LossRunClaim, LossRunSummary, LossRunYear

SUMMARY_LABELS: dict[str, tuple[tuple[str, ...], object]] = {
    "claim_count": (("Number of claims", "Claims reported", "Total claims", "Claim count", "Claims"), parse_int),
    "total_paid": (("Total paid loss", "Paid to date", "Total paid", "Paid"), parse_amount),
    "total_reserve": (
        ("Total O/S reserve", "Outstanding reserve", "Total reserve", "Outstanding", "Reserve"),
        parse_amount,
    ),
    "total_incurred": (("Total incurred", "Incurred"), parse_amount),
}
SUMMARY_LINES = 6
YEAR_TITLES = ("Policy year start", "Property", "General liability")
CLAIM_TITLES = ("Claim", "Paid", "Incurred", "Status", "Reserve", "Outstanding")
MONEY_TITLE_WORDS: dict[str, tuple[str, ...]] = {
    "paid": ("paid",),
    "reserve": ("reserve", "outstanding"),
    "incurred": ("incurred",),
}
LINE_WORDS = ("property", "general", "liability")
STATUSES = ("Open", "Closed")
WORD_MIN_SCORE = 80.0
PARTIAL_MIN_SCORE = 100.0  # the whole title is inside a longer OCR word ("paidloss" holds "paid")
CLAIM_AMOUNTS = 3


def summary(refs: list[LineRef], doc_id: str) -> LossRunSummary:
    """The totals block (the lines after "Loss summary")."""
    start = find_line(refs, "Loss summary")
    if start is None:
        return LossRunSummary()
    block = refs[start + 1 : start + 1 + SUMMARY_LINES]
    values = {}
    for name, (labels, parser) in SUMMARY_LABELS.items():
        values[name] = find_value(block, labels, parser, doc_id)
    return LossRunSummary(**values)


def premium_years(refs: list[LineRef], doc_id: str) -> list[LossRunYear]:
    """The earned-premium-by-policy-year rows."""
    start = header_index(refs, YEAR_TITLES, 2)
    if start is None:
        return []
    header = refs[start]
    columns = {}
    for name, title in (("property", "Property"), ("gl", "General liability")):
        box = title_box(header, title)
        if box is not None:
            columns[name] = box[1]
    years = []
    for ref in refs[start + 1 :]:
        words = ref.words
        start_date = parse_date(words[0].text) if words else None
        if start_date is None:
            break
        year = LossRunYear(policy_year_start=cite_words(ref, [words[0]], start_date, doc_id))
        cells = premium_cells(ref, words[1:], ("property", "gl"), columns, doc_id)
        if "property" in cells:
            year.property_earned_premium = cells["property"]
        if "gl" in cells:
            year.gl_earned_premium = cells["gl"]
        years.append(year)
    return years


def is_title_word(text: str, title: str) -> bool:
    """True when a normalized header word is the title, approximately, or holds the whole title ("paidloss")."""
    if text == title or fuzz.ratio(text, title) >= WORD_MIN_SCORE:
        return True
    return len(text) > len(title) and fuzz.partial_ratio(title, text) >= PARTIAL_MIN_SCORE


def money_order(header: LineRef | None) -> list[str]:
    """paid / reserve / incurred in the left-to-right order of their titles on the claim-table header ([] if unread)."""
    if header is None:
        return []
    positions = {}
    for name, title_words in MONEY_TITLE_WORDS.items():
        for word in header.words:
            text = normalize_for_match(word.text).replace(" ", "")
            if any(is_title_word(text, title) for title in title_words):
                positions.setdefault(name, word.x0)
    if len(positions) != CLAIM_AMOUNTS:
        return []
    return sorted(positions, key=lambda name: positions[name])


def order_closed_claim(values: list[float], status: str | None) -> list[str] | None:
    """Rule 1 of order_by_arithmetic (module docstring): two equal amounts and 0.00, or None when that is not so."""
    zeros = [index for index, value in enumerate(values) if value == 0]
    others = [value for value in values if value != 0]
    if len(zeros) != 1 or len(others) != 2 or abs(others[0] - others[1]) > 0.005:
        return None
    pair = ["reserve", "incurred"] if status == "Open" else ["paid", "incurred"]
    zero_name = "paid" if status == "Open" else "reserve"
    return [zero_name if index in zeros else pair.pop(0) for index in range(CLAIM_AMOUNTS)]


def order_by_arithmetic(amounts: list[Word], status: str | None = None) -> list[str]:
    """Amount names when no header has been read: rules 1 and 2 of the module docstring."""
    values = [parse_amount(word.text) or 0.0 for word in amounts]
    closed = order_closed_claim(values, status)
    if closed is not None:
        return closed
    for index in range(CLAIM_AMOUNTS):
        others = [values[other] for other in range(CLAIM_AMOUNTS) if other != index]
        if abs(sum(others) - values[index]) <= 1.0:
            names = []
            remaining = ["paid", "reserve"]
            for position in range(CLAIM_AMOUNTS):
                if position == index:
                    names.append("incurred")
                else:
                    names.append(remaining.pop(0))
            return names
    return ["paid", "reserve", "incurred"]


def claim_line(words: list[Word]) -> tuple[str | None, list[Word]]:
    """The claim's line of business as a code ("property" / "general_liability") and the words that print it."""
    used = []
    printed = set()
    for word in words:
        text = normalize_for_match(word.text)
        for line_word in LINE_WORDS:
            if text == line_word or fuzz.ratio(text, line_word) >= WORD_MIN_SCORE:
                used.append(word)
                printed.add(line_word)
    if "general" in printed or "liability" in printed:
        return "general_liability", used
    if "property" in printed:
        return "property", used
    return None, used


def claim_status(words: list[Word]) -> tuple[str, Word] | None:
    """(status, word) for the Open / Closed word of a claim row, or None."""
    for word in words:
        for status in STATUSES:
            if fuzz.ratio(word.text.casefold(), status.casefold()) >= WORD_MIN_SCORE:
                return status, word
    return None


def parse_claim(ref: LineRef, order: list[str], doc_id: str) -> LossRunClaim | None:
    """One claim row, or None when the line is not one (see the module docstring)."""
    amounts = amount_words(ref.words)
    dates = [word for word in ref.words if parse_date(word.text) is not None]
    if len(amounts) < CLAIM_AMOUNTS or not dates:
        return None
    amounts = amounts[:CLAIM_AMOUNTS]
    others = [word for word in ref.words if word not in amounts and word is not dates[0]]
    status = claim_status(others)
    names = order or order_by_arithmetic(amounts, None if status is None else status[0])
    claim = LossRunClaim(date_of_loss=cite_words(ref, [dates[0]], parse_date(dates[0].text), doc_id))
    for name, word in zip(names, amounts, strict=True):
        setattr(claim, name, cite_words(ref, [word], abs(parse_amount(word.text) or 0.0), doc_id))
    code, line_words = claim_line(others)
    if code is not None:
        claim.line = cite_words(ref, line_words, code, doc_id)
    status_word = None
    if status is not None:
        status_word = status[1]
        claim.status = cite_words(ref, [status_word], status[0], doc_id)
    for word in others:
        if word in line_words or word is status_word:
            continue
        if any(character.isdigit() for character in word.text):
            claim.claim_number = cite_words(ref, [word], parse_identifier(word.text), doc_id)
            break
    return claim


def claims(refs: list[LineRef], doc_id: str) -> list[LossRunClaim]:
    """Every claim row of the run, with the money-column order of the nearest header above it."""
    rows = []
    order: list[str] = []
    for ref in refs:
        is_row = len(amount_words(ref.words)) >= CLAIM_AMOUNTS
        if not is_row and header_index([ref], CLAIM_TITLES, 3) is not None:
            order = money_order(ref)
            continue
        claim = parse_claim(ref, order, doc_id)
        if claim is not None:
            rows.append(claim)
    return rows


def letterhead(refs: list[LineRef], doc_id: str) -> Cited:
    """The carrier name: the first line of the loss run."""
    if not refs:
        return Cited()
    words = trim_noise(refs[0].words)
    return cite_words(refs[0], words, parse_text(words_text(words)), doc_id)


def extract_loss_run(segment: Segment) -> LossRun:
    """One carrier's loss run (all of its pages)."""
    refs = document_lines(segment)
    doc_id = segment.doc_id
    run = LossRun(
        doc_id=doc_id,
        carrier=letterhead(refs, doc_id),
        insured_name=find_value(refs, ("Insured",), parse_text, doc_id),
        policy_number=find_value(refs, ("Policy number",), parse_identifier, doc_id),
        valued_as_of=find_value(refs, ("Valued as of",), parse_date, doc_id),
        years=premium_years(refs, doc_id),
        claims=claims(refs, doc_id),
        summary=summary(refs, doc_id),
    )
    produced = find_line(refs, "Carrier-produced loss run")
    if produced is not None:
        run.carrier_produced = cite_words(refs[produced], refs[produced].words, True, doc_id)
    return run

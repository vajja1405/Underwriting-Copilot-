"""Page classification and document segmentation for SYNTHETIC packets.

Classification (classify_page): every document type has a list of phrases with weights (KEYWORDS). The page text is
normalized (casefolded, every run of characters that are not letters or digits becomes one space) and each phrase
found in it adds its weight to its type's score. Only the types of the packet's line (credit or insurance) compete.
Phrases are matched exactly first; on an OCR page a phrase that is not found exactly may still count when
rapidfuzz's partial_ratio is at least FUZZY_MIN_SCORE (OCR misreads a letter here and there). The page gets the
type with the highest score; its confidence is best / (best + second best), so 1.0 means no other type scored. A
page where no phrase is found gets the type of the page before it with confidence 0.0.

Segmentation (segment_pages): consecutive pages form one document until a page starts a new one. A page starts a
new document when
1. its footer says "Page 1 of n" (every SYNTHETIC page prints "Page i of n" within its document); a footer with
   i > 1 always continues the open document (and the page takes that document's type, which corrects a
   misclassified continuation page);
2. without a readable footer: its type differs from the open document's type; or it is a bank statement whose
   "Statement period" (both dates parsed) differs from the open statement's; or a loss run whose carrier (the
   letterhead line) differs by more than a small OCR misreading;
   or a type that always has one page per document (photo ID, collateral valuation, inspection report).
Documents get ids D01, D02, ... in page order, the same rule the generator uses.
"""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from uwcopilot.docintel.layout import PageText
from uwcopilot.docintel.parsing import parse_date
from uwcopilot.docintel.schemas import DocumentInfo

CREDIT_KEYWORDS: dict[str, tuple[tuple[str, int], ...]] = {
    "loan_application": (
        ("small business loan application", 5),
        ("legal name of business", 3),
        ("section d owners", 3),
        ("section e remarks", 3),
        ("section f certification", 3),
        ("certify that the information in this application", 3),
        ("requested amount", 2),
        ("stated annual revenue", 2),
    ),
    "bank_statement": (
        ("business checking statement", 5),
        ("statement period", 3),
        ("opening balance", 2),
        ("closing balance", 2),
        ("total withdrawals", 2),
        ("number of transactions", 2),
        ("account holder", 2),
    ),
    "profit_and_loss": (
        ("profit and loss", 5),
        ("trailing twelve months", 3),
        ("cost of goods sold", 2),
        ("gross profit", 2),
        ("ebitda", 2),
        ("net income", 1),
    ),
    "balance_sheet": (
        ("balance sheet as of", 5),
        ("total assets", 2),
        ("total liabilities", 2),
        ("owners equity", 2),
        ("accounts receivable", 1),
    ),
    "debt_schedule": (
        ("schedule of business debt", 5),
        ("no existing business debt", 3),
        ("total monthly payment", 3),
        ("total balance", 1),
        ("maturity", 1),
    ),
    "photo_id": (
        ("not a valid identity document", 5),
        ("synthetic specimen", 4),
        ("identification card", 3),
        ("date of birth", 2),
        ("id number", 2),
        ("expires", 1),
    ),
    "credit_report": (
        ("credit bureau", 4),
        ("principal credit summary", 4),
        ("bankruptcy in last 7 years", 3),
        ("credit score", 2),
        ("report date", 1),
    ),
    "formation_document": (
        ("articles of organization", 5),
        ("articles of incorporation", 5),
        ("certificate of partnership", 5),
        ("formation filing", 3),
        ("members and ownership", 3),
        ("entity name", 1),
    ),
    "tax_return_summary": (
        ("business tax return summary", 5),
        ("not an irs form", 4),
        ("gross receipts", 2),
        ("tax year", 2),
    ),
    "collateral_valuation": (
        ("collateral valuation report", 5),
        ("collateral type", 3),
        ("valuation basis", 2),
        ("valuation date", 2),
    ),
}
INSURANCE_KEYWORDS: dict[str, tuple[tuple[str, int], ...]] = {
    "insurance_application": (
        ("commercial lines application", 5),
        ("section 1 applicant", 3),
        ("section 5 prior carriers", 4),
        ("section 6 owners", 3),
        ("section 7 remarks", 3),
        ("section 8 signature", 3),
        ("named insured", 2),
        ("total insured value", 2),
    ),
    "loss_run": (
        ("carrier produced loss run", 5),
        ("claim detail for policy", 4),
        ("loss run continued", 4),
        ("earned premium by policy year", 3),
        ("valued as of", 3),
        ("loss summary", 2),
    ),
    "property_schedule": (
        ("property schedule", 4),
        ("statement of values", 4),
        ("all values at replacement cost", 3),
        ("total tiv", 3),
        ("date signed", 2),
    ),
    "cover_email": (
        ("email message", 6),
        ("new submission", 2),
        ("broker example com", 2),
        ("harborline example org", 2),
        ("subject", 1),
    ),
    "ownership_statement": (
        ("beneficial ownership statement", 5),
        ("owning 25 or more", 3),
        ("ownership information is true", 2),
    ),
    "inspection_report": (
        ("loss control inspection report", 5),
        ("inspection date", 3),
        ("no critical deficiencies", 2),
        ("findings", 1),
    ),
}
ONE_PAGE_TYPES = ("photo_id", "collateral_valuation", "inspection_report")
FUZZY_MIN_SCORE = 90.0
FOOTER_PATTERN = re.compile(r"\bpage\s+(\d{1,2})\s+of\s+(\d{1,2})\b", re.IGNORECASE)
PERIOD_PATTERN = re.compile(r"statement period\s+(\S+)\s+to\s+(\S+)", re.IGNORECASE)


@dataclass
class PageClass:
    """The predicted type of one page and its confidence (0-1)."""

    page_number: int
    doc_type: str
    confidence: float


@dataclass
class Segment:
    """One document found in the packet: its id, type, pages (as text) and classification confidence."""

    doc_id: str
    doc_type: str
    pages: list[PageText] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    key: str | None = None  # statement period or loss-run carrier, when the type has one

    @property
    def page_numbers(self) -> list[int]:
        """Packet page numbers of the document, in order."""
        return [page.page_number for page in self.pages]

    @property
    def ocr_used(self) -> bool:
        """True when any page of the document was read with OCR."""
        return any(page.source == "ocr" for page in self.pages)

    @property
    def confidence(self) -> float:
        """Mean page classification confidence."""
        return round(sum(self.confidences) / len(self.confidences), 4) if self.confidences else 0.0

    def info(self) -> DocumentInfo:
        """The schema's DocumentInfo row for this document."""
        return DocumentInfo(
            doc_id=self.doc_id,
            doc_type=self.doc_type,
            pages=self.page_numbers,
            ocr_used=self.ocr_used,
            classification_confidence=self.confidence,
        )


def normalize_for_match(text: str) -> str:
    """Casefold and turn every run of non-alphanumeric characters into one space."""
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text.casefold()).split())


def keywords_for(line: str) -> dict[str, tuple[tuple[str, int], ...]]:
    """The keyword table of a packet line ("credit" or "insurance")."""
    return CREDIT_KEYWORDS if line == "credit" else INSURANCE_KEYWORDS


def phrase_found(phrase: str, text: str, fuzzy: bool) -> bool:
    """True when the (normalized) phrase occurs in the (normalized) text, or nearly so on an OCR page."""
    if f" {phrase} " in f" {text} ":
        return True
    return fuzzy and fuzz.partial_ratio(phrase, text) >= FUZZY_MIN_SCORE


def type_scores(page: PageText, line: str) -> dict[str, int]:
    """Keyword score of every document type of the line for one page."""
    text = normalize_for_match(page.text)
    fuzzy = page.source == "ocr"
    scores = {}
    for doc_type, phrases in keywords_for(line).items():
        scores[doc_type] = sum(weight for phrase, weight in phrases if phrase_found(phrase, text, fuzzy))
    return scores


def classify_page(page: PageText, line: str, previous_type: str | None = None) -> PageClass:
    """The page's document type and confidence (see the module docstring)."""
    scores = type_scores(page, line)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if best == 0:
        fallback = previous_type or best_type
        return PageClass(page.page_number, fallback, 0.0)
    return PageClass(page.page_number, best_type, round(best / (best + second), 4))


def page_counter(page: PageText) -> tuple[int, int] | None:
    """(i, n) from the "Page i of n" footer in the page's last lines, or None when it cannot be read."""
    for text_line in reversed(page.lines[-3:]):
        match = FOOTER_PATTERN.search(text_line.text)
        if match is not None:
            index, total = int(match.group(1)), int(match.group(2))
            if 1 <= index <= total:
                return index, total
    return None


def statement_period(page: PageText) -> str | None:
    """The statement period of a bank statement page as "YYYY-MM-DD YYYY-MM-DD", or None when it cannot be read."""
    for text_line in page.lines:
        match = PERIOD_PATTERN.search(text_line.text)
        if match is None:
            continue
        start, end = parse_date(match.group(1)), parse_date(match.group(2))
        if start is not None and end is not None:
            return f"{start.isoformat()} {end.isoformat()}"
    return None


def loss_run_carrier(page: PageText) -> str | None:
    """The carrier named on the first line of a loss-run page (the text before " - " on a continuation page)."""
    if not page.lines:
        return None
    first = page.lines[0].text.split(" - ")[0]
    return normalize_for_match(first) or None


def segment_key(page: PageText, doc_type: str) -> str | None:
    """The value that separates documents of the same type when no footer is readable."""
    if doc_type == "bank_statement":
        return statement_period(page)
    if doc_type == "loss_run":
        return loss_run_carrier(page)
    return None


def same_key(first: str | None, second: str | None, doc_type: str) -> bool:
    """True unless both keys are known and different. Statement periods (parsed dates) must be equal; carrier names
    may differ by a small OCR misreading."""
    if first is None or second is None or first == second:
        return True
    return doc_type == "loss_run" and fuzz.ratio(first, second) >= FUZZY_MIN_SCORE


def starts_new_document(current: Segment | None, page_class: PageClass, key: str | None, footer: tuple | None) -> bool:
    """The segmentation rule of the module docstring for one page."""
    if current is None:
        return True
    if footer is not None:
        return footer[0] == 1
    if page_class.doc_type != current.doc_type or page_class.doc_type in ONE_PAGE_TYPES:
        return True
    return not same_key(current.key, key, page_class.doc_type)


def segment_pages(pages: list[PageText], line: str) -> tuple[list[Segment], list[PageClass]]:
    """Classify every page and group the pages into documents (see the module docstring)."""
    segments: list[Segment] = []
    classes: list[PageClass] = []
    current: Segment | None = None
    for page in pages:
        page_class = classify_page(page, line, None if current is None else current.doc_type)
        footer = page_counter(page)
        if current is not None and footer is not None and footer[0] > 1 and page_class.doc_type != current.doc_type:
            page_class = PageClass(page.page_number, current.doc_type, 0.0)
        key = segment_key(page, page_class.doc_type)
        if starts_new_document(current, page_class, key, footer):
            current = Segment(doc_id=f"D{len(segments) + 1:02d}", doc_type=page_class.doc_type, key=key)
            segments.append(current)
        current.pages.append(page)
        current.confidences.append(page_class.confidence)
        classes.append(page_class)
    return segments, classes


def documents_info(segments: list[Segment]) -> list[DocumentInfo]:
    """DocumentInfo rows for every segment."""
    return [segment.info() for segment in segments]

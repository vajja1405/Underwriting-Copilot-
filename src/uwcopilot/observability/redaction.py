"""PII redaction for traces and logs.

Traces leave the process (to an OTLP collector such as Arize Phoenix), so everything written into a span goes
through a PiiRedactor first. It masks:
- the known names of the application's parties (legal name and owner names; for people also each name part of
  three or more letters), matched case-insensitively, plus their screening-normalized forms (lowercased, legal
  form stripped, diacritics removed: "Northwind Shadow Trading LLC" -> "northwind shadow trading"), because tool
  outputs such as screening explanations quote the normalized form;
- FEINs (12-3456789), SSN-shaped numbers (123-45-6789), e-mail addresses and US phone numbers;
- account numbers: runs of 8 to 17 digits, and digits after words such as "account" or "acct";
- whole values under sensitive keys (name, legal_name, fein, account_number, matched_name, notes, excerpt,
  explanation, request, ...), whatever they contain. A screening explanation quotes the screened name and the
  list entry's name tokens (real people and organizations on OFAC / OpenSanctions), so it is never exported; the
  decision record keeps it.

Names cannot be found reliably by pattern alone, which is why the redactor is built per application with the names
it must hide. Decision records are the system of record and are not redacted; they are not exported to traces.
"""

import json
import re
from collections.abc import Iterable
from typing import Any

from uwcopilot.kyc.normalize import normalize_name

NAME_MASK = "[NAME]"
FEIN_MASK = "[FEIN]"
SSN_MASK = "[SSN]"
EMAIL_MASK = "[EMAIL]"
PHONE_MASK = "[PHONE]"
ACCOUNT_MASK = "[ACCOUNT]"
VALUE_MASK = "[REDACTED]"
MIN_NAME_PART_LENGTH = 3
DEFAULT_MAX_CHARS = 8000

SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "legal_name",
        "fein",
        "account_number",
        "matched_name",
        "derived_from_name",
        "notes",
        "excerpt",
        "address",
        "email",
        "phone",
        "rationale",
        "explanation",
        "request",
    }
)
FEIN = re.compile(r"(?<![\d-])\d{2}-\d{7}(?![\d-])")
SSN = re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
PHONE = re.compile(r"(?<![\d-])(?:\+1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\d-])")
LABELED_ACCOUNT = re.compile(
    r"\b(?:acct|account|a/c)(?:\s*(?:no\.?|number|#))?\s*[:#]?\s*\d[\d -]{3,}\d", re.IGNORECASE
)
# 8 to 17 digits not inside a decimal or grouped number ("27000000.0" stays; "acct 990012345678," is masked).
LONG_DIGITS = re.compile(r"(?<![\d-])(?<!\d[.,])\d{8,17}(?!\d|[.,]\d|-)")


class PiiRedactor:
    """Masks the given names and common identifiers in text and in nested JSON-like values."""

    def __init__(self, known_names: Iterable[str] = (), person_names: Iterable[str] = ()) -> None:
        """known_names are masked as whole names; person_names are also masked part by part."""
        terms: set[str] = set()
        for name in known_names:
            if name.strip():
                terms.add(name.strip())
        for name in person_names:
            if name.strip():
                terms.add(name.strip())
                terms.update(part for part in re.split(r"[\s,.'-]+", name) if len(part) >= MIN_NAME_PART_LENGTH)
        # Longest first, so a full name is masked before its parts.
        ordered = sorted(terms, key=len, reverse=True)
        self._name_patterns = [
            re.compile(r"(?<!\w)" + r"\s+".join(re.escape(token) for token in term.split()) + r"(?!\w)", re.IGNORECASE)
            for term in ordered
        ]

    def redact_text(self, text: str) -> str:
        """Mask names and identifiers in one string."""
        result = text
        for pattern in self._name_patterns:
            result = pattern.sub(NAME_MASK, result)
        result = EMAIL.sub(EMAIL_MASK, result)
        result = SSN.sub(SSN_MASK, result)
        result = FEIN.sub(FEIN_MASK, result)
        result = LABELED_ACCOUNT.sub(ACCOUNT_MASK, result)
        result = PHONE.sub(PHONE_MASK, result)
        return LONG_DIGITS.sub(ACCOUNT_MASK, result)

    def redact(self, value: Any) -> Any:
        """Redact a nested value: sensitive keys are masked whole, every other string is redacted as text."""
        if isinstance(value, dict):
            return {
                key: (VALUE_MASK if str(key) in SENSITIVE_KEYS and value[key] not in (None, "") else self.redact(item))
                for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            return self.redact_text(value)
        return value

    def to_json(self, value: Any, max_chars: int = DEFAULT_MAX_CHARS) -> str:
        """Redacted JSON text, cut to max_chars (with a marker) so spans stay small."""
        text = json.dumps(self.redact(value), default=str, ensure_ascii=False)
        if len(text) > max_chars:
            return text[:max_chars] + f"...[truncated {len(text) - max_chars} chars]"
        return text


def normalized_form(name: str, entity_type: str) -> str:
    """The name as the screener normalizes it (tokens joined by spaces); empty if nothing is left."""
    return " ".join(normalize_name(name, entity_type).tokens)


def redactor_for_application(application: dict[str, Any]) -> PiiRedactor:
    """A redactor that knows the names on one application (given as its JSON dict), in original and normalized form."""
    known: list[str] = []
    people: list[str] = []
    applicant = application.get("applicant") or {}
    for key in ("legal_name", "business_name"):  # business_name: startup credit applications
        if applicant.get(key):
            known.append(applicant[key])
            known.append(normalized_form(applicant[key], "organization"))
    for owner in application.get("owners") or []:
        name = owner.get("name")
        if not name:
            continue
        if owner.get("entity_type") == "person":
            people.append(name)
            people.append(normalized_form(name, "person"))
        else:
            known.append(name)
            known.append(normalized_form(name, "organization"))
    return PiiRedactor(known_names=known, person_names=people)

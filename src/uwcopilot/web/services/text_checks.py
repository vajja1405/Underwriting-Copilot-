"""Checks on free text a person types into the web app (an idea, a site label, a reviewer's rationale).

Two different checks:

1. Data minimization (Studio addendum rule B, docs/studio/privacy.md): v1 does not accept SSNs, tax returns or bank
   account numbers. Text with an SSN-shaped number (123-45-6789, also written with spaces: 123 45 6789), a labeled
   account number ("acct 12345678") or a run of 8 to 17 digits is refused with a plain message, so it is never
   stored and never sent anywhere (a site address is checked before it goes to the Census Geocoder). The patterns
   are the trace redactor's (uwcopilot.observability.redaction) plus SPACED_SSN, which the web app refuses as well.
   Census tract ids (11 digits) of the plan's metro are allowed in site labels: the wizard writes them itself
   ("Tract 17031010400"), and a public tract id is not personal data.
2. Injection screen: the founder's idea text is untrusted data. It is normalized (invisible characters, homoglyphs
   and tag-character smuggling are handled by uwcopilot.security.normalize.detection_text) and then read by the
   agent's tripwire (uwcopilot.agent.injection.detect). Flagged text is still stored (it is the founder's own
   text) but it is marked "flagged" and is never given to any language model. v0.1 gives no idea text to any
   model at all.
"""

import re
from collections.abc import Iterable

from uwcopilot.agent.injection import detect
from uwcopilot.observability.redaction import LABELED_ACCOUNT, LONG_DIGITS, SSN
from uwcopilot.web.services.errors import InputError

# Where the privacy notice lives for people using the app (the repository file is docs/studio/privacy.md).
PRIVACY_NOTICE = "see the privacy notice on the About page"
# 123 45 6789 (and mixed separators): an SSN written without dashes. Not used by the trace redactor.
SPACED_SSN = re.compile(r"(?<![\d-])\d{3}[ -]\d{2}[ -]\d{4}(?![\d-])")
TRACT_ID = re.compile(r"(?<!\d)\d{11}(?!\d)")


def forbidden_identifiers(text: str) -> list[str]:
    """Which kinds of forbidden identifiers the text contains (empty when none)."""
    found = []
    if SSN.search(text) or SPACED_SSN.search(text):
        found.append("an SSN-shaped number")
    if LABELED_ACCOUNT.search(text) or LONG_DIGITS.search(text):
        found.append("an account-number-like run of digits")
    return found


def without_known_tract_ids(text: str, tract_ids: Iterable[str]) -> str:
    """text with every 11-digit token that is one of tract_ids replaced by a word (so it is not taken for an account
    number). Any other long digit run stays and is still refused."""
    known = set(tract_ids)
    return TRACT_ID.sub(lambda match: "tract" if match.group(0) in known else match.group(0), text)


def refuse_identifiers(text: str | None, field: str) -> None:
    """Raise InputError when text holds an SSN or an account-number-like number (data minimization)."""
    if not text:
        return
    found = forbidden_identifiers(text)
    if found:
        raise InputError(
            f"{field} contains {' and '.join(found)}. Please remove it: this prototype does not accept SSNs, tax "
            f"returns or bank account numbers ({PRIVACY_NOTICE})."
        )


def injection_flags(text: str | None) -> list[str]:
    """Names of the instruction-like patterns found in the normalized text (empty when none or no text)."""
    if not text:
        return []
    from uwcopilot.security.normalize import detection_text  # imported late: the module is still being finished

    return detect(detection_text(text))

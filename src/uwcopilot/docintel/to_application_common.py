"""Shared helpers for mapping an extraction to the agent's application JSON (to_application.py).

ApplicationBuilder collects, while an application dict is built, where every value came from (provenance:
application path -> list of {doc_id, page, line}) and which paths could not be filled (unknowns). A value that is
not in the extraction is None and its path is an unknown: nothing is guessed.
"""

from collections.abc import Callable
from datetime import date
from typing import Any

from uwcopilot.docintel.schemas import Cited

Provenance = dict[str, list[dict[str, Any]]]
CENTS = 2


def application_id(packet_id: str) -> str:
    """ "PKT-C-007" -> "APP-C-007" (the application id matches the packet number)."""
    return packet_id.replace("PKT-", "APP-", 1)


def money(value: float | None) -> float | None:
    """An amount rounded to cents (None stays None)."""
    return None if value is None else round(float(value), CENTS)


def iso(value: date | None) -> str | None:
    """A date as "YYYY-MM-DD" (None stays None)."""
    return None if value is None else value.isoformat()


def source_of(cited: Cited) -> dict[str, Any]:
    """{doc_id, page, line} of a cited value."""
    return {"doc_id": cited.doc_id, "page": cited.page, "line": cited.line}


class ApplicationBuilder:
    """Collects provenance and unknown paths while an application dict is built."""

    def __init__(self) -> None:
        self.provenance: Provenance = {}
        self.unknowns: list[str] = []

    def value(
        self, path: str, cited: Cited, convert: Callable[[Any], Any] | None = None, *, optional: bool = False
    ) -> Any:
        """The cited value (converted), recording its source; None and an unknown when it was not found.

        optional=True is for a value the document may legitimately leave empty (GL limits on a form that says "Not
        requested"): when it was not found the result is None WITHOUT an unknown entry; when it was found, its
        source is recorded like any other value.
        """
        if not cited.found:
            if not optional:
                self.unknowns.append(path)
            return None
        self.provenance[path] = [source_of(cited)]
        return cited.value if convert is None else convert(cited.value)

    def computed(self, path: str, value: Any, sources: list[Cited]) -> Any:
        """A computed value with the cited values it came from; None values are recorded as unknown."""
        if value is None:
            self.unknowns.append(path)
            return None
        cited = [source_of(item) for item in sources if item.found]
        if cited:
            self.provenance[path] = cited
        return value

    def documents(self, path: str, doc_ids: list[tuple[str, int | None]]) -> None:
        """Provenance for a value that comes from whole documents: (doc_id, first page)."""
        if doc_ids:
            self.provenance[path] = [{"doc_id": doc_id, "page": page, "line": None} for doc_id, page in doc_ids]


def provided_codes(provided: set[str], order: tuple[str, ...]) -> list[str]:
    """The provided checklist codes in the agent's checklist order."""
    return [code for code in order if code in provided]

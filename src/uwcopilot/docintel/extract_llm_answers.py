"""Validation of the LLM extractor's answers: JSON shape, citations, parsing, grounding and schema types.

extract_llm.py sends the requests; this module turns one answer into cited values (read_answer). The rules for a
value to be kept (see extract_llm.py for the whole flow):
- the answer is one JSON object (one surrounding ```json fence is removed: parse_json_object);
- every field is {"value": ..., "cite": "p<page>:L<line>"} (LLMValue; a line range "p2:L5-L7" is allowed for
  text over several lines) and its key is one the document type lists;
- the citation names a page of THIS document and lines that exist on it (cited_lines);
- the value can be read by the rules extractor's parser for its kind (extract_llm_fields.KIND_PARSERS);
- the value is printed on the cited line(s) (grounded: letters and digits compared, dates in any printed style);
- the parsed value has the type the extraction schema wants (cited_class + pydantic validation).
A value that fails a rule is dropped and counted by reason in DocumentReading.rejected; nothing is guessed.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from uwcopilot.docintel.extract_llm_fields import KIND_PARSERS, DocSpec, FieldSpec, TableSpec
from uwcopilot.docintel.layout import Line, PageText, union_box
from uwcopilot.docintel.schemas import Cited
from uwcopilot.llm.base import LLMResponse

CITE_PATTERN = re.compile(r"^p(\d+):L(\d+)(?:-L?(\d+))?$")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class LLMValue(BaseModel):
    """One field as the model must write it: the printed value and its citation."""

    model_config = ConfigDict(extra="forbid")

    value: str | int | float | bool | None = None
    cite: str | None = None


@dataclass
class DocumentReading:
    """What the model returned for one document after validation: values by attribute, table rows, problems."""

    values: dict[str, Cited] = field(default_factory=dict)
    rows: dict[str, list[dict[str, Cited]]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    responses: list[LLMResponse] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        """Count one rejected value."""
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


def parse_json_object(text: str) -> dict[str, Any]:
    """The answer as a JSON object; one surrounding ```json fence is allowed. Raises ValueError otherwise."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else ""
        cleaned = cleaned.rsplit("```", 1)[0]
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    return data


def compact(text: str) -> str:
    """Letters and digits only, casefolded: "$1,234.56" -> "123456"."""
    return "".join(character for character in text.casefold() if character.isalnum())


def date_forms(day: date) -> list[str]:
    """The three printed date styles of the packets: ISO, MM/DD/YYYY and DD-Mon-YYYY."""
    return [
        day.isoformat(),
        f"{day.month:02d}/{day.day:02d}/{day.year}",
        f"{day.day:02d}-{MONTHS[day.month - 1]}-{day.year}",
    ]


def grounded(kind: str, raw_text: str, parsed: Any, cited_text: str) -> bool:
    """True when the value is printed on the cited text (letters and digits compared; dates in any style).

    Yes/No answers are not checked: a boolean can be printed as a phrase (e.g. "Carrier-produced loss run").
    """
    if kind == "bool":
        return True
    target = compact(cited_text)
    if kind == "date":
        return any(compact(form) in target for form in date_forms(parsed))
    wanted = compact(raw_text)
    return bool(wanted) and wanted in target


def raw_text_of(kind: str, value: str | int | float | bool) -> str:
    """The model's value as text for the parsers (a JSON number for an amount gets its two decimals)."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if kind == "money" and isinstance(value, int | float):
        return f"{value:.2f}"
    return str(value)


def cited_lines(cite: str, pages: dict[int, PageText]) -> list[Line] | None:
    """The lines a citation names, or None when it is malformed or outside the document."""
    found = CITE_PATTERN.match(cite.strip())
    if found is None or int(found.group(1)) not in pages:
        return None
    first = int(found.group(2))
    last = int(found.group(3) or first)
    by_number = {line.line_number: line for line in pages[int(found.group(1))].lines}
    if last < first or any(number not in by_number for number in range(first, last + 1)):
        return None
    return [by_number[number] for number in range(first, last + 1)]


def cited_class(model: type[BaseModel], attribute: str) -> type[Cited]:
    """The Cited[...] type of a schema attribute; a dotted attribute is looked up in the sub-model."""
    owner = model
    parts = attribute.split(".")
    for part in parts[:-1]:
        owner = owner.model_fields[part].annotation
    return owner.model_fields[parts[-1]].annotation


def to_cited(
    spec_field: FieldSpec,
    model: type[BaseModel],
    raw: Any,
    doc_id: str,
    pages: dict[int, PageText],
    reading: DocumentReading,
) -> Cited | None:
    """One validated, grounded and cited value, or None (missing or rejected; rejections are counted)."""
    try:
        entry = LLMValue.model_validate(raw if raw is not None else {})
    except ValidationError:
        reading.reject("not_a_value_object")
        return None
    if entry.value is None:
        return None
    lines = cited_lines(entry.cite or "", pages)
    if lines is None:
        reading.reject("citation_outside_document")
        return None
    raw_text = raw_text_of(spec_field.kind, entry.value)
    if spec_field.kind == "bool" and isinstance(entry.value, bool):
        parsed: Any = entry.value
    else:
        parsed = KIND_PARSERS[spec_field.kind](raw_text)
    if parsed is None:
        reading.reject("unparseable")
        return None
    source_text = " ".join(line.text for line in lines)
    if not grounded(spec_field.kind, raw_text, parsed, source_text):
        reading.reject("not_on_cited_line")
        return None
    data = {
        "value": parsed,
        "doc_id": doc_id,
        "page": int(CITE_PATTERN.match(entry.cite.strip()).group(1)),
        "line": lines[0].line_number,
        "bbox": union_box([line.bbox for line in lines]),
        "confidence": min(line.confidence for line in lines),
        "source_text": source_text,
    }
    try:
        return cited_class(model, spec_field.target).model_validate(data)
    except ValidationError:
        reading.reject("wrong_type_for_schema")
        return None


def read_answer(
    spec: DocSpec, data: dict[str, Any], doc_id: str, pages: dict[int, PageText], reading: DocumentReading
) -> None:
    """Add one validated answer to the reading: first value wins for fields, rows are appended."""
    fields = {item.name: item for item in spec.fields}
    tables = {table.name: table for table in spec.tables}
    for key, raw in data.items():
        if key in fields:
            cited = to_cited(fields[key], spec.model, raw, doc_id, pages, reading)
            if cited is not None and fields[key].target not in reading.values:
                reading.values[fields[key].target] = cited
        elif key in tables and raw is None:
            continue
        elif key in tables and isinstance(raw, list):
            reading.rows.setdefault(key, []).extend(read_rows(tables[key], raw, doc_id, pages, reading))
        else:
            reading.reject("unknown_key_or_shape")


def read_rows(
    table: TableSpec, raw_rows: list[Any], doc_id: str, pages: dict[int, PageText], reading: DocumentReading
) -> list[dict[str, Cited]]:
    """The table's rows; rows without any kept value are dropped."""
    fields = {item.name: item for item in table.fields}
    rows = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            reading.reject("unknown_key_or_shape")
            continue
        row: dict[str, Cited] = {}
        for key, raw in raw_row.items():
            if key not in fields:
                reading.reject("unknown_key_or_shape")
                continue
            cited = to_cited(fields[key], table.row_model, raw, doc_id, pages, reading)
            if cited is not None:
                row[fields[key].target] = cited
        if row:
            rows.append(row)
    return rows

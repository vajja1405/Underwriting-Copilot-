"""The building blocks of the LLM extractor's requests: value kinds, fields, tables and document specs (Phase 2).

extract_llm_specs.py uses these to describe, per document type, what the LLM extractor asks for. Each field has a
KIND that decides how the copied text is parsed (KIND_PARSERS, the same parsers as the rules extractor) and how it
is described to the model (KIND_HINTS). attribute is the schema attribute the value goes to; a dotted attribute
("summary.claim_count") goes into a sub-model.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from uwcopilot.docintel.parsing import (
    code_from_label,
    normalize_fein,
    parse_amount,
    parse_bool,
    parse_date,
    parse_identifier,
    parse_int,
    parse_percent,
    parse_text,
)

KIND_PARSERS: dict[str, Callable[[str | None], object]] = {
    "text": parse_text,
    "money": parse_amount,
    "percent": parse_percent,
    "int": parse_int,
    "date": parse_date,
    "bool": parse_bool,
    "fein": normalize_fein,
    "code": code_from_label,
    "identifier": parse_identifier,
}
KIND_HINTS: dict[str, str] = {
    "text": "text as printed",
    "money": 'amount as printed, e.g. "$1,234.56"',
    "percent": 'percentage as printed, e.g. "12.5%"',
    "int": "whole number as printed",
    "date": "date as printed",
    "bool": "true or false",
    "fein": "federal EIN as printed",
    "code": "label as printed",
    "identifier": "identifier as printed",
}


@dataclass(frozen=True)
class FieldSpec:
    """One value to extract: its JSON key, kind, schema attribute and an optional extra instruction."""

    name: str
    kind: str
    attribute: str = ""
    hint: str = ""

    @property
    def target(self) -> str:
        """The schema attribute (the JSON key unless another attribute is given)."""
        return self.attribute or self.name

    def describe(self) -> str:
        """One line of the field list shown to the model."""
        extra = f"; {self.hint}" if self.hint else ""
        return f"{self.name} ({KIND_HINTS[self.kind]}{extra})"


@dataclass(frozen=True)
class TableSpec:
    """A small table: its JSON key (a list of row objects), schema attribute, row model and row fields."""

    name: str
    row_model: type[BaseModel]
    fields: tuple[FieldSpec, ...]
    attribute: str = ""

    @property
    def target(self) -> str:
        """The schema attribute holding the rows."""
        return self.attribute or self.name


@dataclass(frozen=True)
class DocSpec:
    """Everything the LLM extractor asks for in one document type (see the module docstring)."""

    doc_type: str
    model: type[BaseModel]
    slot: str
    many: bool
    fields: tuple[FieldSpec, ...]
    tables: tuple[TableSpec, ...] = field(default_factory=tuple)


def specs(kind: str, *names: str) -> tuple[FieldSpec, ...]:
    """Several fields of one kind whose JSON key is the schema attribute."""
    return tuple(FieldSpec(name, kind) for name in names)

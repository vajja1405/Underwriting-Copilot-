"""Helpers for the EVAL-role tests: a FakeLLM responder that answers from the SYNTHETIC oracle extraction.

oracle_responder(truth) returns a FakeLLM responder (system, messages) -> str. For each document request of the
LLM extractor it finds the document id and type in the prompt, takes that document's values from the generator's
oracle extraction and writes the JSON answer the extractor asks for. Every value is cited on a line OF THE PROMPT
that prints it (the oracle's own line when the prompt shows that line with the value, otherwise the first prompt
line of the same page that does), so the answer passes the extractor's grounding check exactly when the value can
be read on the page. A value that cannot be found (an OCR misread) is answered with null, as an honest reader would.

This is a test double for the hybrid path: its "answers" come from the ground truth, so a hybrid run with it
measures the plumbing (prompts, validation, merging), never the quality of a language model.

tiny_packet_set() generates three SYNTHETIC packets (two credit, one insurance) with the generator's own
generate_packets, once per test session, in a temporary folder. Its seed (TINY_SEED) differs from the evaluation
set's seed 20261013, so the tests never read the evaluation packets.
"""

import atexit
import html
import json
import re
import shutil
import tempfile
from collections.abc import Callable
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

from uwcopilot.docintel.extract_llm_answers import compact, date_forms
from uwcopilot.docintel.extract_llm_fields import DocSpec, FieldSpec
from uwcopilot.docintel.extract_llm_specs import DOC_SPECS
from uwcopilot.docintel.schemas import Cited, parse_extraction
from uwcopilot.docintel.synthetic.generate import generate_packets
from uwcopilot.llm.base import LLMMessage

DOCUMENT_TAG = re.compile(r'<document doc_id="(D\d+)" type="([a-z_]+)">')
PROMPT_LINE = re.compile(r"^p(\d+):L(\d+) \| (.*)$")
MAX_RANGE = 12
TINY_SEED = 21


def prompt_lines(prompt: str) -> dict[int, list[tuple[int, str]]]:
    """{page: [(line number, text), ...]} of the document lines shown in a prompt (html-unescaped)."""
    pages: dict[int, list[tuple[int, str]]] = {}
    for text in prompt.splitlines():
        found = PROMPT_LINE.match(text)
        if found:
            pages.setdefault(int(found.group(1)), []).append((int(found.group(2)), html.unescape(found.group(3))))
    return pages


def printed(kind: str, value: Any) -> Any:
    """The value as the responder writes it (booleans stay JSON booleans; codes get spaces back)."""
    if kind == "bool":
        return bool(value)
    if kind == "money":
        return f"{float(value):,.2f}"
    if kind == "percent":
        return f"{float(value)}%"
    if kind == "code":
        return str(value).replace("_", " ")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def search_forms(kind: str, value: Any) -> list[str]:
    """What must appear on a line for it to print the value (letters and digits only)."""
    if kind == "date":
        return [compact(form) for form in date_forms(value)]
    return [compact(str(printed(kind, value)))]


def find_cite(kind: str, cited: Cited, lines: dict[int, list[tuple[int, str]]]) -> str | None:
    """A citation "p<page>:L<line>[-L<last>]" of prompt lines that print the value, or None."""
    page_lines = lines.get(cited.page or -1, [])
    forms = [form for form in search_forms(kind, cited.value) if form]
    if not page_lines or (kind != "bool" and not forms):
        return None
    ordered = sorted(page_lines, key=lambda item: (item[0] != cited.line, item[0]))
    if kind == "bool":
        return f"p{cited.page}:L{ordered[0][0]}"
    by_number = dict(page_lines)
    for number, _ in ordered:
        for size in range(1, MAX_RANGE + 1):
            numbers = list(range(number, number + size))
            if any(item not in by_number for item in numbers):
                break
            joined = compact(" ".join(by_number[item] for item in numbers))
            if any(form in joined for form in forms):
                last = f"-L{numbers[-1]}" if size > 1 else ""
                return f"p{cited.page}:L{number}{last}"
    return None


def answer_field(spec_field: FieldSpec, model: Any, lines: dict[int, list[tuple[int, str]]]) -> dict | None:
    """{"value", "cite"} for one field of an oracle model, or None when it is missing or not in this prompt."""
    owner = model
    for part in spec_field.target.split(".")[:-1]:
        owner = getattr(owner, part)
    cited = getattr(owner, spec_field.target.split(".")[-1])
    if not isinstance(cited, Cited) or not cited.found:
        return None
    cite = find_cite(spec_field.kind, cited, lines)
    if cite is None:
        return None
    return {"value": printed(spec_field.kind, cited.value), "cite": cite}


def oracle_models(spec: DocSpec, oracle: Any, doc_id: str) -> list[Any]:
    """The oracle's model(s) of one document."""
    current = getattr(oracle, spec.slot)
    items = current if spec.many else ([] if current is None else [current])
    return [item for item in items if item.doc_id == doc_id]


def answer_document(spec: DocSpec, models: list[Any], lines: dict[int, list[tuple[int, str]]]) -> dict:
    """The JSON answer for one document request."""
    if not models:
        return {}
    first = models[0]
    answer: dict[str, Any] = {item.name: answer_field(item, first, lines) for item in spec.fields}
    for table in spec.tables:
        sources = models if spec.doc_type == "tax_return_summary" else getattr(first, table.target)
        rows = []
        for source in sources:
            row = {item.name: answer_field(item, source, lines) for item in table.fields}
            if any(value is not None for value in row.values()):
                rows.append(row)
        answer[table.name] = rows
    return answer


def oracle_responder(truth: Any) -> Callable[[str, list[LLMMessage]], str]:
    """A FakeLLM responder that answers every document request from the truth's oracle extraction."""
    oracle = parse_extraction(truth.oracle_extraction)

    def respond(system: str, messages: list[LLMMessage]) -> str:
        prompt = messages[-1].content
        found = DOCUMENT_TAG.search(prompt)
        if found is None:
            return "{}"
        spec = DOC_SPECS[found.group(2)]
        models = oracle_models(spec, oracle, found.group(1))
        return json.dumps(answer_document(spec, models, prompt_lines(prompt)))

    return respond


@cache
def tiny_packet_set() -> tuple[Path, dict]:
    """(folder, manifest) of three SYNTHETIC packets (PKT-C-001, PKT-C-002, PKT-I-001; one credit and the
    insurance packet in the test split), generated once per test session with seed TINY_SEED."""
    folder = Path(tempfile.mkdtemp(prefix="uwc_docintel_eval_tests_"))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    manifest = generate_packets(folder, 2, 1, TINY_SEED, 1, 1, created_with="tests (docintel_eval_fixtures)")
    return folder, manifest

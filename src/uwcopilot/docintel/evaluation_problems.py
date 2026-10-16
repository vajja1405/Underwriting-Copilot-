"""Evaluation of contradictions and embedded-instruction hits against the injected problems of the ground truth.

Contradictions (match_contradictions):
- Only problem types that a contradiction can have are scored (contradictions.CONTRADICTION_TYPES: every problem
  type except missing_document and missing_pages, which the checklist evaluation covers).
- A detected contradiction MATCHES an injected problem when the type is the same and they overlap: they share a
  truth document (the documents of the pages the contradiction's evidence cites) or a field (equal paths, or one
  path inside the other, such as "profit_and_loss" and "profit_and_loss.revenue").
- Evidence check (evidence_correct): a match whose cited values agree with the truth. values_by_doc gives one true
  value per problem document, often an aggregate (a month's payments, a loss run's claim count, a schedule's TIV),
  while a contradiction cites the parts (each payment, each claim, each location). So, for every problem document
  the contradiction cites, the true value must equal one of the side values cited on that document, their sum, or
  (for a whole-number count) how many values are cited (numbers within $0.01; text: the true text inside the cited
  text, casefolded; a count against cited amounts is compared with the number of amounts only when they are equal,
  otherwise it is not comparable). And the contradiction must cite at least two of the problem's documents when two
  or more print a value (a schedule-only arithmetic contradiction does not show an application-vs-schedule TIV
  problem; an owner missing from a document has no value there). A
  match with no comparable value is None and counted neither way. Recall and precision are also reported counting
  only evidence-correct matches, so a contradiction that matches by type and document but rests on misread values
  (OCR) is visible.
- Recall: an injected problem counts as detected when at least one contradiction matches it. Precision: a
  contradiction counts as correct when it matches at least one injected problem. Several contradictions about one
  problem therefore all count as correct; the extra ones are counted in problems.all.duplicate_matches.
- Benign vs unsupported: for each detected problem whose true classification is benign or unsupported, ONE
  matching contradiction represents it, chosen by a fixed rule (representative): evidence-correct matches first,
  then an explanation other than "unknown", then report order. Its explanation is compared with the
  classification; "unknown" counts as wrong (the spec's headline) and is counted separately. Also counted: the
  2 x 3 confusion (true benign / unsupported x predicted benign / unsupported / unknown), the outcome per problem
  type, the unknown answers per explanation_code, and the "designed abstentions": problems the explanation rules
  answer "unknown" BY DESIGN whatever the extraction (an unexplained tiv_mismatch whose true difference is within
  the 15.0% HM-5.2 band: contradiction_rules leaves it unknown / insufficient_evidence). The accuracy without them
  is reported next to the headline.

Embedded instructions (injection_counts): a planted instruction is detected when the pipeline reports a hit on the
same page. The reported hits are the embedded_instructions check's issues: the page-text scan's hits plus the
free-text field hits on pages the page scan did not flag. A hit on a page without a planted instruction is a false
hit.

The truth is read by attribute (a truth.PacketTruth works); this module does not import truth.py.
"""

from collections import Counter
from typing import Any

from uwcopilot.agent.insurance_calculations import tiv_difference_pct
from uwcopilot.agent.policy_insurance import TIV_REFER_PCT
from uwcopilot.docintel.contradictions import CONTRADICTION_TYPES, Contradiction
from uwcopilot.docintel.pipeline import PacketResult

EXPLAINED_CLASSES: tuple[str, ...] = ("benign", "unsupported")
PREDICTED_CLASSES: tuple[str, ...] = ("benign", "unsupported", "unknown")
MONEY_TOLERANCE = 0.01


def contradiction_docs(contradiction: Contradiction, page_docs: dict[int, str]) -> set[str]:
    """The truth documents of the pages the contradiction's evidence cites (its own doc ids when nothing is cited)."""
    docs = {page_docs[item.page] for item in contradiction.sides if item.page in page_docs}
    return docs or set(contradiction.doc_ids)


def contradiction_fields(contradiction: Contradiction) -> set[str]:
    """The field paths a contradiction names, plus the fields of its evidence."""
    return set(contradiction.fields) | {item.field for item in contradiction.sides}


def fields_overlap(first: set[str], second: set[str]) -> bool:
    """True when a path is in both sets, or a path of one set lies inside a path of the other."""
    for one in first:
        for other in second:
            if one == other or one.startswith(other + ".") or other.startswith(one + "."):
                return True
    return False


def matches(problem: Any, contradiction: Contradiction, page_docs: dict[int, str]) -> bool:
    """Same type, and a shared truth document or field (see the module docstring)."""
    if contradiction.type != problem.problem_type:
        return False
    if contradiction_docs(contradiction, page_docs) & set(problem.doc_ids):
        return True
    return fields_overlap(contradiction_fields(contradiction), set(problem.fields))


def as_number(value: Any) -> float | None:
    """A number (not a bool), or None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def as_text(value: Any) -> str:
    """Text casefolded with single spaces."""
    return " ".join(str(value).split()).casefold()


def document_agrees(cited: list[Any], true_value: Any) -> bool | None:
    """True / False when the values cited on one document agree with its true value (module docstring); None when
    nothing can be compared."""
    if isinstance(true_value, list):
        verdicts = [document_agrees(cited, item) for item in true_value]
        if True in verdicts:
            return True
        return False if False in verdicts else None
    if isinstance(true_value, str):
        texts = [as_text(item) for item in cited if isinstance(item, str)]
        if not texts:
            return None
        return any(as_text(true_value) in text for text in texts)
    number = as_number(true_value)
    numbers = [as_number(item) for item in cited if as_number(item) is not None]
    if number is None or not numbers:
        return None
    if any(abs(candidate - number) <= MONEY_TOLERANCE for candidate in [*numbers, sum(numbers)]):
        return True
    if isinstance(true_value, int) and not isinstance(true_value, bool):
        # A count (claims on a loss run; the truth writes amounts as floats) against cited amounts: the number of
        # amounts may agree; a check that cites only some of the rows is not evidence of a misread, so a different
        # number is "not comparable".
        return True if len(numbers) == number else None
    return False


def evidence_correct(problem: Any, contradiction: Contradiction, page_docs: dict[int, str]) -> bool | None:
    """True when the contradiction's cited values agree with the problem's true values (module docstring)."""
    true_values = dict(getattr(problem, "values_by_doc", None) or {})  # a hand-built stand-in may have none
    cited: dict[str, list[Any]] = {}
    for item in contradiction.sides:
        doc_id = page_docs.get(item.page) if item.page is not None else item.doc_id
        if doc_id in true_values:
            cited.setdefault(doc_id, []).append(item.value)
    verdicts = [document_agrees(values, true_values[doc_id]) for doc_id, values in cited.items()]
    if False in verdicts:
        return False
    if True not in verdicts:
        return None
    printed = [doc_id for doc_id, value in true_values.items() if value is not None]  # None: absent from that doc
    return len(cited) >= min(2, len(printed))


def designed_abstention(problem: Any, doc_types: dict[str, str]) -> bool:
    """True for a problem the explanation rules answer "unknown" by design (module docstring)."""
    if problem.problem_type != "tiv_mismatch" or problem.classification != "unsupported":
        return False
    true_values = dict(getattr(problem, "values_by_doc", None) or {})
    by_type = {doc_types.get(doc_id): value for doc_id, value in true_values.items()}
    stated = as_number(by_type.get("insurance_application"))
    schedule = as_number(by_type.get("property_schedule"))
    if stated is None or schedule is None or schedule <= 0:
        return False
    return tiv_difference_pct(stated, schedule) <= TIV_REFER_PCT


def representative(found: list[tuple[Contradiction, bool | None]]) -> tuple[Contradiction, bool | None] | None:
    """The match that represents a problem: evidence-correct first, then not "unknown", then report order."""
    best = None
    best_rank = None
    for position, (contradiction, correct) in enumerate(found):
        rank = (correct is not True, contradiction.explanation == "unknown", position)
        if best_rank is None or rank < best_rank:
            best, best_rank = (contradiction, correct), rank
    return best


def match_contradictions(
    problems: list[Any],
    contradictions: list[Contradiction],
    page_docs: dict[int, str],
    doc_types: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One outcome per scorable injected problem and one per detected contradiction (JSON-simple dicts).

    doc_types (truth doc id -> document type) is needed only to find designed abstentions.
    """
    scorable = [problem for problem in problems if problem.problem_type in CONTRADICTION_TYPES]
    problem_outcomes = []
    for problem in scorable:
        found = [
            (item, evidence_correct(problem, item, page_docs))
            for item in contradictions
            if matches(problem, item, page_docs)
        ]
        chosen = representative(found)
        first = None if chosen is None else chosen[0]
        problem_outcomes.append(
            {
                "problem_id": problem.problem_id,
                "type": problem.problem_type,
                "classification": problem.classification,
                "detected": first is not None,
                "detected_with_correct_evidence": any(flag is True for _, flag in found),
                "matches": len(found),
                "contradiction_id": None if first is None else first.contradiction_id,
                "explanation": None if first is None else first.explanation,
                "explanation_code": None if first is None else first.explanation_code,
                "evidence_correct": None if chosen is None else chosen[1],
                "designed_abstention": designed_abstention(problem, doc_types or {}),
            }
        )
    contradiction_outcomes = []
    for contradiction in contradictions:
        matched = [problem for problem in scorable if matches(problem, contradiction, page_docs)]
        flags = [evidence_correct(problem, contradiction, page_docs) for problem in matched]
        contradiction_outcomes.append(
            {
                "contradiction_id": contradiction.contradiction_id,
                "type": contradiction.type,
                "explanation": contradiction.explanation,
                "explanation_code": contradiction.explanation_code,
                "matched_problem_ids": [problem.problem_id for problem in matched],
                "evidence_correct": any(flag is True for flag in flags) if matched else None,
            }
        )
    return problem_outcomes, contradiction_outcomes


def explanation_counts(outcome: dict) -> Counter:
    """explanations.* counters of one detected problem whose truth is benign or unsupported (module docstring)."""
    counts: Counter = Counter()
    truth, predicted = outcome["classification"], outcome["explanation"]
    groups = ["all", truth, f"type.{outcome['type']}"]
    groups.append("designed_abstention" if outcome["designed_abstention"] else "decidable")
    for group in groups:
        counts[f"explanations.{group}.total"] += 1
        counts[f"explanations.{group}.correct"] += int(predicted == truth)
        counts[f"explanations.{group}.unknown"] += int(predicted == "unknown")
    counts[f"explanations.confusion.{truth}.{predicted}"] += 1
    if predicted == "unknown":
        counts[f"explanations.unknown_code.{outcome['explanation_code']}"] += 1
    return counts


def contradiction_counts(problem_outcomes: list[dict], contradiction_outcomes: list[dict]) -> Counter:
    """Counters for recall (problems.*), precision (contradictions.*) and explanations (explanations.*)."""
    counts: Counter = Counter()
    for outcome in problem_outcomes:
        for kind in ("all", outcome["type"]):
            counts[f"problems.{kind}.total"] += 1
            counts[f"problems.{kind}.detected"] += int(outcome["detected"])
            counts[f"problems.{kind}.detected_evidence_correct"] += int(outcome["detected_with_correct_evidence"])
            counts[f"problems.{kind}.duplicate_matches"] += max(0, outcome["matches"] - 1)
        if outcome["detected"] and outcome["classification"] in EXPLAINED_CLASSES:
            counts.update(explanation_counts(outcome))
    for outcome in contradiction_outcomes:
        for kind in ("all", outcome["type"]):
            counts[f"contradictions.{kind}.total"] += 1
            counts[f"contradictions.{kind}.tp"] += int(bool(outcome["matched_problem_ids"]))
            counts[f"contradictions.{kind}.tp_evidence_correct"] += int(outcome["evidence_correct"] is True)
    return counts


def reported_hits(result: PacketResult) -> list[tuple[str | None, int | None]]:
    """(doc_id, page) of every instruction-like text the pipeline reported.

    These are the issues of the embedded_instructions check: the page-text scan's hits plus the free-text field
    hits on pages the page scan did not flag (injection_scan.merge_hits). A result without that check (a
    hand-built one) falls back to the page-text hits.
    """
    check = next((item for item in result.checks if item.check_id == "embedded_instructions"), None)
    if check is None:
        return [(hit.doc_id, hit.page) for hit in result.injection_hits]
    places = []
    for issue in check.issues:
        evidence = issue.side_a[0] if issue.side_a else None
        places.append((None, None) if evidence is None else (evidence.doc_id, evidence.page))
    return places


def hit_pages(result: PacketResult) -> list[int | None]:
    """The page of every reported hit; a hit without a page takes its document's first page."""
    document_pages = {document.doc_id: document.pages for document in result.extraction.documents}
    placed = []
    for doc_id, page in reported_hits(result):
        if page is None and document_pages.get(doc_id):
            page = document_pages[doc_id][0]
        placed.append(page)
    return placed


def injection_counts(truth: Any, result: PacketResult) -> tuple[Counter, list[dict[str, Any]]]:
    """Counters for injection recall and false hits, and one outcome per planted instruction."""
    counts: Counter = Counter()
    placed = hit_pages(result)
    hit_page_numbers = {page for page in placed if page is not None}
    planted_pages = {injection.page_number for injection in truth.injections}
    outcomes = []
    for injection in truth.injections:
        detected = injection.page_number in hit_page_numbers
        counts["injections.total"] += 1
        counts["injections.detected"] += int(detected)
        outcomes.append({"page": injection.page_number, "technique": injection.technique, "detected": detected})
    false_hits = [page for page in placed if page not in planted_pages]
    counts["injection_hits.total"] += len(placed)
    counts["injection_hits.false"] += len(false_hits)
    counts["injection_packets.total"] += 1
    counts["injection_packets.with_false_hit"] += int(bool(false_hits))
    if not truth.injections:
        counts["injection_packets.clean"] += 1
        counts["injection_packets.clean_with_hit"] += int(bool(placed))
    return counts, outcomes

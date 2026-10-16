"""Scoring for the frozen RAG generation evaluation (docs/innovations.md, S1 "Metrics"). Pure functions, no I/O.

Each answer is scored on its own (score_answer), then the per-question scores are aggregated (summarize). The same
per-question values feed the migration gate, which pairs two configurations question by question.

Per-question measures:
- key_fact_recall (keyed questions only): matched key facts / key facts. A key fact is a short text written before
  any model run (eval/rag_key_facts.jsonl) with a kind:
  * number: every quantity in the fact must appear in the answer as the same quantity, through the SIG number
    normalizer (provenance.numbers.extract_numbers + same_quantity), so "$500,000", "$500k" and "500,000 dollars"
    all match, while "500,000" with no currency does not. Comparator words in the fact text ("more than 6") are
    context for the reader; only the quantity is compared.
  * role and term: casefolded substring after normalization (NFKC, curly quotes and dashes unified, whitespace
    collapsed). Years are written as term facts, because the number normalizer treats 1900-2099 as dates. A role or
    term fact may list `alternates`: other wordings of the same fact ("authority to decline" for "decline
    authority"); the fact is matched when the text or any alternate is found.
  Reported for all keyed questions and for the subset whose gold section was in the prompt, which separates
  retrieval misses from generation misses.
- "12 consecutive months" is 12 months (also "most recent", "complete", "policy", "tax" ... between a number and a
  time unit): the shared SIG normalizer (provenance.numbers) reads these qualifiers itself, so a correct answer
  quoting the guideline's own words matches a "12 months" fact and is not flagged untraceable. (S1b first folded the
  qualifiers here, before the normalizer handled them; that local workaround is gone.)
- is_not_found: the answer is the exact not-found sentence (rag.citations.is_not_found_answer: case, surrounding
  whitespace and the final full stop are ignored). Used for abstention_accuracy_unanswerable (unanswerable
  questions) and false_abstention_rate (answerable questions whose gold is in the prompt).
- asserted_absence: the answer says the information is absent. Either it is or contains the not-found sentence, or
  it contains a phrase from ASSERTED_ABSENCE_PHRASES (a fixed lexicon such as "does not specify" or "no minimum is
  set"). The matched phrases are kept for audit. A lexicon misses paraphrases and can flag a phrase used for
  something else; that limitation is part of the metric's definition.
- has_fabricated_citation and the uncited factual sentence share: from rag.citations.check_citations.
- format_compliant: the exact not-found answer, or at least one citation with no fabricated citation.
- has_untraceable_number: some number in the answer is not found in any cited section that was in the prompt
  (provenance.numeric_check.check_numbers) AND is not a number the user wrote in the question. A correct answer
  applies the rule to the user's own figures ("a DSCR of 1.18 is below 1.25 [NBC-4.2]"); the 1.18 cannot be in a
  guideline, but it was not invented either. Such numbers are listed as numbers_from_question and not counted.
  Limitation: a model that presents the user's number as a policy value ("the maximum is $600,000" when the user
  asked about $600,000) is not flagged by this measure; key-fact recall still misses the real limit.
  Comparator mismatches are counted separately and are not gated.
"""

import json
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwcopilot.common.stats import rate_summary
from uwcopilot.provenance.numbers import NumberMention, extract_numbers, same_quantity
from uwcopilot.provenance.numeric_check import check_numbers
from uwcopilot.rag.citations import (
    CitationCheck,
    check_citations,
    extract_cited_ids,
    is_not_found_answer,
    needs_citation,
    split_answer_sentences,
)
from uwcopilot.rag.eval_set import normalize_for_match
from uwcopilot.rag.frozen_hits import FrozenHitsRecord
from uwcopilot.rag.metrics import bootstrap_mean_ci
from uwcopilot.rag.prompt import NOT_FOUND_ANSWER
from uwcopilot.tools.schemas import GuidelineHit

KeyFactKind = Literal["number", "role", "term"]

# Phrases that state that the documents lack the information (casefolded substring match after normalization).
ASSERTED_ABSENCE_PHRASES: tuple[str, ...] = (
    "does not specify",
    "do not specify",
    "doesn't specify",
    "don't specify",
    "not specified",
    "does not mention",
    "do not mention",
    "doesn't mention",
    "don't mention",
    "not mentioned",
    "does not state",
    "do not state",
    "doesn't state",
    "don't state",
    "is not stated",
    "are not stated",
    "not explicitly stated",
    "does not address",
    "do not address",
    "not addressed",
    "does not provide information",
    "do not provide information",
    "does not contain information",
    "do not contain information",
    "no information",
    "no minimum is set",
    "no maximum is set",
    "no threshold is set",
    "not covered in the provided",
    "not covered by the provided",
    "not included in the provided",
)
NOT_FOUND_PHRASE = normalize_for_match(NOT_FOUND_ANSWER.rstrip("."))

# Metric name -> the population of questions it is computed on (see config/release_gate.yaml).
METRIC_POPULATIONS: dict[str, str] = {
    "key_fact_recall": "keyed",
    "key_fact_recall_gold_in_context": "keyed_gold_in_context",
    "abstention_accuracy_unanswerable": "unanswerable",
    "false_abstention_rate": "answerable_gold_in_context",
    "asserted_absence_error_rate": "answerable_gold_in_context",
    "answers_with_fabricated_citation": "all",
    "uncited_factual_sentence_share": "answers_with_factual_sentences",
    "format_compliance": "all",
    "answers_with_untraceable_number": "all",
    "answers_with_comparator_mismatch": "all",
}


class KeyFact(BaseModel):
    """One fact a correct answer must state.

    alternates: other wordings of a role or term fact that count as stating it (never used for number facts, whose
    wordings the number normalizer already covers).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1)
    kind: KeyFactKind
    alternates: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def alternates_are_wordings(self) -> "KeyFact":
        """Number facts have no alternates; alternates are non-empty and differ from the text."""
        if self.alternates and self.kind == "number":
            raise ValueError(f"number fact {self.text!r} cannot have alternates")
        for alternate in self.alternates:
            if not alternate.strip() or normalize_text(alternate) == normalize_text(self.text):
                raise ValueError(f"fact {self.text!r}: alternate {alternate!r} is empty or repeats the text")
        return self

    def wordings(self) -> list[str]:
        """The text followed by its alternates."""
        return [self.text, *self.alternates]


class KeyFactRow(BaseModel):
    """The key facts of one question (one line of eval/rag_key_facts.jsonl)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Z]{1,3}\d{2,3}$")
    gold_section: str = Field(min_length=1)
    key_facts: list[KeyFact] = Field(min_length=1)


def load_key_facts(path: Path) -> dict[str, KeyFactRow]:
    """{question id: KeyFactRow} from a JSONL file; ids must be unique."""
    rows: dict[str, KeyFactRow] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = KeyFactRow.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"{path.name} line {line_number}: {error}") from error
        if row.id in rows:
            raise ValueError(f"{path.name}: duplicate key-fact id {row.id}")
        rows[row.id] = row
    return rows


def normalize_text(text: str) -> str:
    """NFKC (folds full-width and compatibility characters), then rag.eval_set.normalize_for_match."""
    return normalize_for_match(unicodedata.normalize("NFKC", text))


def fact_in_text(fact: KeyFact, text: str) -> bool:
    """True when the fact is stated in text (rules in the module docstring).

    Raises ValueError for a number fact whose text holds no quantity, since it could never be checked.
    """
    if fact.kind == "number":
        wanted = extract_numbers(fact.text)
        if not wanted:
            raise ValueError(f"number fact {fact.text!r} contains no quantity")
        found = extract_numbers(text)
        for mention in wanted:
            if not any(same_quantity(mention, other) for other in found):
                return False
        return True
    normalized = normalize_text(text)
    for wording in fact.wordings():
        if normalize_text(wording) in normalized:
            return True
    return False


def asserted_absence_phrases(text: str) -> list[str]:
    """The absence phrases found in text: 'not-found sentence' and/or entries of ASSERTED_ABSENCE_PHRASES."""
    normalized = normalize_text(text)
    found: list[str] = []
    if is_not_found_answer(text) or NOT_FOUND_PHRASE in normalized:
        found.append("not-found sentence")
    for phrase in ASSERTED_ABSENCE_PHRASES:
        if phrase in normalized:
            found.append(phrase)
    return found


def format_compliant(check: CitationCheck) -> bool:
    """The exact not-found answer, or at least one citation and no fabricated citation."""
    if check.is_not_found:
        return True
    return bool(check.cited_ids) and not check.fabricated_ids


def factual_sentence_counts(text: str, check: CitationCheck) -> tuple[int, int]:
    """(factual sentences, uncited factual sentences); both 0 for the not-found answer."""
    if check.is_not_found:
        return 0, 0
    factual = [sentence for sentence in split_answer_sentences(text) if needs_citation(sentence)]
    uncited = [sentence for sentence in factual if not extract_cited_ids(sentence)]
    return len(factual), len(uncited)


@dataclass(frozen=True)
class QuestionScore:
    """Every per-question measure for one answer (one row of results/generation/<config>.jsonl, without the text)."""

    question_id: str
    category: str
    answerable: bool
    gold_in_context: bool | None
    keyed: bool
    gold_section_in_context: bool | None
    key_facts_n: int
    key_facts_matched: int
    key_fact_recall: float | None
    key_facts_missed: list[str]
    is_not_found: bool
    asserted_absence: bool
    asserted_absence_phrases: list[str]
    cited_ids: list[str]
    fabricated_ids: list[str]
    has_fabricated_citation: bool
    n_factual_sentences: int
    n_uncited_sentences: int
    uncited_share: float | None
    format_compliant: bool
    n_numbers: int
    untraceable_numbers: list[str]
    has_untraceable_number: bool
    comparator_mismatches: list[str] = field(default_factory=list)
    numbers_from_question: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-ready dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuestionScore":
        """Rebuild a score from a results row (extra keys in the row are ignored)."""
        names = cls.__dataclass_fields__.keys()
        return cls(**{name: data[name] for name in names if name in data})


def split_question_numbers(
    untraceable: Sequence[NumberMention], question: str
) -> tuple[list[NumberMention], list[NumberMention]]:
    """(still untraceable, taken from the question): a number the user wrote in the question is not invented."""
    question_numbers = extract_numbers(question)
    remaining: list[NumberMention] = []
    from_question: list[NumberMention] = []
    for mention in untraceable:
        if any(same_quantity(mention, other) for other in question_numbers):
            from_question.append(mention)
        else:
            remaining.append(mention)
    return remaining, from_question


def score_answer(
    question_id: str,
    category: str,
    answer_text: str,
    context: Sequence[GuidelineHit],
    gold_in_context: bool | None,
    key_row: KeyFactRow | None,
    *,
    question: str,
) -> QuestionScore:
    """Score one answer given the question and the hits that were in its prompt.

    gold_in_context: None for an unanswerable question. key_row: the question's key facts, or None if not keyed.
    question: the question text the model saw (its numbers are not counted as untraceable).
    """
    context_ids = [hit.section_id for hit in context]
    check = check_citations(answer_text, context_ids)
    answerable = category != "unanswerable"

    matched_facts = 0
    missed: list[str] = []
    recall: float | None = None
    gold_section_in_context: bool | None = None
    if key_row is not None:
        for fact in key_row.key_facts:
            if fact_in_text(fact, answer_text):
                matched_facts += 1
            else:
                missed.append(fact.text)
        recall = matched_facts / len(key_row.key_facts)
        gold_section_in_context = key_row.gold_section in context_ids

    phrases = asserted_absence_phrases(answer_text)
    n_factual, n_uncited = factual_sentence_counts(answer_text, check)
    sources = {hit.section_id: hit.text for hit in context}
    numeric = check_numbers(answer_text, sources)
    untraceable, from_question = split_question_numbers([item.mention for item in numeric.untraceable], question)
    return QuestionScore(
        question_id=question_id,
        category=category,
        answerable=answerable,
        gold_in_context=gold_in_context,
        keyed=key_row is not None,
        gold_section_in_context=gold_section_in_context,
        key_facts_n=len(key_row.key_facts) if key_row is not None else 0,
        key_facts_matched=matched_facts,
        key_fact_recall=recall,
        key_facts_missed=missed,
        is_not_found=check.is_not_found,
        asserted_absence=bool(phrases),
        asserted_absence_phrases=phrases,
        cited_ids=list(check.cited_ids),
        fabricated_ids=list(check.fabricated_ids),
        has_fabricated_citation=bool(check.fabricated_ids),
        n_factual_sentences=n_factual,
        n_uncited_sentences=n_uncited,
        uncited_share=(n_uncited / n_factual) if n_factual else None,
        format_compliant=format_compliant(check),
        n_numbers=len(numeric.mentions),
        untraceable_numbers=[mention.text for mention in untraceable],
        has_untraceable_number=bool(untraceable),
        comparator_mismatches=[item.mention.text for item in numeric.comparator_mismatches],
        numbers_from_question=[mention.text for mention in from_question],
    )


def score_record(record: FrozenHitsRecord, answer_text: str, k: int, key_row: KeyFactRow | None) -> QuestionScore:
    """Score an answer to a frozen question whose prompt held the record's first k hits.

    The evaluation script and the migration gate both call this, so a stored score can be recomputed exactly.
    """
    return score_answer(
        record.question_id,
        record.category,
        answer_text,
        record.context_hits(k),
        record.gold_in_context(k),
        key_row,
        question=record.question,
    )


def in_population(score: QuestionScore, population: str) -> bool:
    """True when the question belongs to a metric's population (names in METRIC_POPULATIONS)."""
    if population == "all":
        return True
    if population == "keyed":
        return score.keyed
    if population == "keyed_gold_in_context":
        return score.keyed and bool(score.gold_section_in_context)
    if population == "unanswerable":
        return not score.answerable
    if population == "answerable_gold_in_context":
        return score.answerable and bool(score.gold_in_context)
    if population == "answers_with_factual_sentences":
        return score.n_factual_sentences > 0
    raise ValueError(f"unknown population {population!r}")


def metric_value(score: QuestionScore, metric: str) -> float:
    """The per-question value of a metric (1.0 / 0.0 for yes/no measures). Call only for questions in its population."""
    if metric in ("key_fact_recall", "key_fact_recall_gold_in_context"):
        if score.key_fact_recall is None:
            raise ValueError(f"{score.question_id} has no key facts")
        return score.key_fact_recall
    if metric in ("abstention_accuracy_unanswerable", "false_abstention_rate"):
        return 1.0 if score.is_not_found else 0.0
    if metric == "asserted_absence_error_rate":
        return 1.0 if score.asserted_absence else 0.0
    if metric == "answers_with_fabricated_citation":
        return 1.0 if score.has_fabricated_citation else 0.0
    if metric == "uncited_factual_sentence_share":
        if score.uncited_share is None:
            raise ValueError(f"{score.question_id} has no factual sentences")
        return score.uncited_share
    if metric == "format_compliance":
        return 1.0 if score.format_compliant else 0.0
    if metric == "answers_with_untraceable_number":
        return 1.0 if score.has_untraceable_number else 0.0
    if metric == "answers_with_comparator_mismatch":
        return 1.0 if score.comparator_mismatches else 0.0
    raise ValueError(f"unknown metric {metric!r}")


def metric_values(scores: Iterable[QuestionScore], metric: str) -> dict[str, float]:
    """{question id: value} for every question in the metric's population."""
    population = METRIC_POPULATIONS[metric]
    values: dict[str, float] = {}
    for score in scores:
        if in_population(score, population):
            values[score.question_id] = metric_value(score, metric)
    return values


def mean_with_ci(values: Sequence[float], n_resamples: int, seed: int) -> dict[str, Any]:
    """{mean, ci95, n} with a percentile bootstrap over questions, rounded to 4 decimals."""
    if not values:
        return {"mean": None, "ci95": None, "n": 0, "note": "not measured (no questions in this population)"}
    summary = bootstrap_mean_ci(values, n_resamples=n_resamples, seed=seed)
    return {
        "mean": round(summary["mean"], 4),
        "ci95": [round(summary["ci_low"], 4), round(summary["ci_high"], 4)],
        "n": len(values),
    }


def proportion(scores: Sequence[QuestionScore], metric: str) -> dict[str, Any]:
    """{k, n, rate, wilson_ci95} for a yes/no metric over its population."""
    values = list(metric_values(scores, metric).values())
    return rate_summary(int(sum(values)), len(values))


def summarize(scores: Sequence[QuestionScore], n_resamples: int = 1000, seed: int = 42) -> dict[str, Any]:
    """The metrics block of results/generation_metrics.json for one configuration."""
    abstention = proportion(scores, "abstention_accuracy_unanswerable")
    uncited_values = list(metric_values(scores, "uncited_factual_sentence_share").values())
    uncited_block = mean_with_ci(uncited_values, n_resamples, seed)
    uncited_block["uncited_sentences"] = sum(score.n_uncited_sentences for score in scores)
    uncited_block["factual_sentences"] = sum(score.n_factual_sentences for score in scores)
    untraceable = proportion(scores, "answers_with_untraceable_number")
    untraceable["answers_with_any_number"] = sum(1 for score in scores if score.n_numbers > 0)
    untraceable["answers_with_number_from_question"] = sum(1 for score in scores if score.numbers_from_question)
    untraceable["note"] = "numbers the user wrote in the question are not counted as untraceable"
    return {
        "key_fact_recall": {
            "all": mean_with_ci(list(metric_values(scores, "key_fact_recall").values()), n_resamples, seed),
            "gold_in_context": mean_with_ci(
                list(metric_values(scores, "key_fact_recall_gold_in_context").values()), n_resamples, seed
            ),
        },
        "abstention_accuracy_unanswerable": {
            "correct": abstention["k"],
            "n": abstention["n"],
            "rate": abstention["rate"],
            "wilson_ci95": abstention["wilson_ci95"],
        },
        "false_abstention_rate": proportion(scores, "false_abstention_rate"),
        "asserted_absence_error_rate": proportion(scores, "asserted_absence_error_rate"),
        "answers_with_fabricated_citation": proportion(scores, "answers_with_fabricated_citation"),
        "uncited_factual_sentence_share": uncited_block,
        "format_compliance": proportion(scores, "format_compliance"),
        "answers_with_untraceable_number": untraceable,
        "answers_with_comparator_mismatch": proportion(scores, "answers_with_comparator_mismatch"),
    }


def summarize_by_category(scores: Sequence[QuestionScore]) -> dict[str, dict[str, Any]]:
    """Per question category: n, not-found answers, fabricated citations, format compliance, untraceable numbers,
    plus key-fact recall (keyed questions) and abstention (unanswerable questions). No CIs: the groups are small.
    """
    categories = sorted({score.category for score in scores})
    result: dict[str, dict[str, Any]] = {}
    for category in categories:
        group = [score for score in scores if score.category == category]
        block: dict[str, Any] = {
            "n": len(group),
            "not_found_answers": rate_summary(sum(1 for s in group if s.is_not_found), len(group)),
            "answers_with_fabricated_citation": proportion(group, "answers_with_fabricated_citation"),
            "format_compliance": proportion(group, "format_compliance"),
            "answers_with_untraceable_number": proportion(group, "answers_with_untraceable_number"),
        }
        recalls = list(metric_values(group, "key_fact_recall").values())
        if recalls:
            block["key_fact_recall"] = {"mean": round(sum(recalls) / len(recalls), 4), "n": len(recalls)}
        if category == "unanswerable":
            block["abstention_accuracy_unanswerable"] = proportion(group, "abstention_accuracy_unanswerable")
        result[category] = block
    return result

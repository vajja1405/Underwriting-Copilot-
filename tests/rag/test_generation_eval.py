"""Frozen generation evaluation: scoring functions, the frozen inputs, the prompt registry, BM25 and the eval script.

Everything here runs offline: answers are hand-written or come from the FakeLLM; no Ollama server is contacted.
"""

import json
from pathlib import Path

import pytest

import eval_generation
import freeze_retrieval
from uwcopilot.common.files import sha256_of_file
from uwcopilot.llm.base import IncompleteLLMResponse, LLMMessage, LLMResponse
from uwcopilot.llm.fake_client import FakeLLM
from uwcopilot.llm.ollama_client import ContextBudgetExceeded, OllamaError
from uwcopilot.rag.bm25 import BM25Index, GuidelineBM25Retriever, tokenize
from uwcopilot.rag.frozen_hits import (
    FROZEN_HITS_FILE,
    FROZEN_MANIFEST_FILE,
    KEY_FACTS_FILE,
    MODE_BM25_GUIDELINES_ONLY,
    MODE_HYBRID_RERANK,
    QUESTION_FILES,
    FrozenHitsRecord,
    HitLocation,
    frozen_hits_line,
    load_eval_questions,
    read_frozen_hits,
    read_manifest,
)
from uwcopilot.rag.generation_eval import (
    KeyFact,
    KeyFactRow,
    QuestionScore,
    asserted_absence_phrases,
    metric_values,
    score_answer,
    summarize,
    summarize_by_category,
)
from uwcopilot.rag.prompt import ANSWER_INSTRUCTION, NOT_FOUND_ANSWER, SYSTEM_PROMPT
from uwcopilot.rag.prompts import PROMPTS, UnknownPromptError, all_prompt_hashes, get_prompt, prompt_sha256
from uwcopilot.tools.schemas import GuidelineHit

DSCR_TEXT = "| Term loan | 1.25 |\n| Revolving line of credit | 1.35 |\nBelow 1.10 does not meet policy."
REVOLVER_TEXT = "Revolving line of credit | $25,000 | $500,000 | 12-month commitment"


def hit(section_id: str, text: str, rank: int = 1) -> GuidelineHit:
    """A GuidelineHit for scoring tests."""
    return GuidelineHit(
        section_id=section_id,
        title=f"{section_id} Title",
        source_doc="northwind_business_credit",
        text=text,
        score=1.0,
        rank=rank,
    )


DSCR_KEY = KeyFactRow(
    id="NB14",
    gold_section="NBC-4.2",
    key_facts=[KeyFact(text="1.25", kind="number"), KeyFact(text="Senior Underwriter", kind="role")],
)


# ---------------------------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------------------------

DEFAULT_QUESTION = "What does the policy say about this file?"


def score_with(
    question_id: str,
    category: str,
    answer: str,
    context: list[GuidelineHit],
    gold_in_context: bool | None,
    key_row: KeyFactRow | None,
    question: str = DEFAULT_QUESTION,
) -> QuestionScore:
    """score_answer with a question that holds no number (the tests below that need one pass it)."""
    return score_answer(question_id, category, answer, context, gold_in_context, key_row, question=question)


def test_correct_cited_answer_scores_full_recall_and_complies() -> None:
    context = [hit("NBC-4.2", DSCR_TEXT + " Refer to a Senior Underwriter.")]
    answer = "The term loan minimum is 1.25x [NBC-4.2]. Below it, refer to a senior underwriter [NBC-4.2]."
    score = score_with("NB14", "single_section", answer, context, True, DSCR_KEY)
    assert score.key_facts_matched == 2 and score.key_fact_recall == 1.0
    assert score.gold_section_in_context is True
    assert score.format_compliant and not score.has_fabricated_citation
    assert score.n_factual_sentences == 2 and score.n_uncited_sentences == 0
    assert not score.has_untraceable_number and not score.asserted_absence


def test_wrong_number_fabricated_citation_and_untraceable_number_are_caught() -> None:
    context = [hit("NBC-4.2", DSCR_TEXT)]
    answer = "The minimum is 1.30 [NBC-4.2]. See also [NBC-9.9]."
    score = score_with("NB14", "single_section", answer, context, True, DSCR_KEY)
    assert score.key_facts_matched == 0 and score.key_facts_missed == ["1.25", "Senior Underwriter"]
    assert score.has_fabricated_citation and score.fabricated_ids == ["NBC-9.9"]
    assert not score.format_compliant
    assert score.has_untraceable_number and score.untraceable_numbers == ["1.30"]


def test_not_found_answer_is_abstention_and_asserted_absence() -> None:
    context = [hit("NBC-4.2", DSCR_TEXT)]
    score = score_with("UA11", "unanswerable", NOT_FOUND_ANSWER, context, None, None)
    assert score.is_not_found and score.format_compliant and score.asserted_absence
    assert score.key_fact_recall is None and not score.keyed
    assert metric_values([score], "abstention_accuracy_unanswerable") == {"UA11": 1.0}
    assert metric_values([score], "false_abstention_rate") == {}


def test_asserted_absence_lexicon_and_its_audit_trail() -> None:
    assert asserted_absence_phrases("The guidelines do not specify a DSCR minimum [NBC-4.1].") == ["do not specify"]
    assert asserted_absence_phrases("No minimum is set for revolvers [NBC-4.1].") == ["no minimum is set"]
    assert asserted_absence_phrases("Not found in the provided guidelines, sorry.") == ["not-found sentence"]
    assert asserted_absence_phrases("The minimum is 1.25 [NBC-4.2].") == []


def test_uncited_sentence_and_no_citation_fail_format() -> None:
    context = [hit("NBC-1.1", REVOLVER_TEXT)]
    score = score_with("NB01", "single_section", "The maximum revolver is $500,000.", context, True, None)
    assert not score.format_compliant and score.n_uncited_sentences == 1 and score.uncited_share == 1.0
    assert score.has_untraceable_number  # a number in a sentence that cites nothing cannot be traced


def test_populations_follow_gold_in_context() -> None:
    context = [hit("NBC-4.1", "Annual debt service ...")]
    out_of_context = score_with("NB14", "single_section", NOT_FOUND_ANSWER, context, False, DSCR_KEY)
    assert out_of_context.gold_section_in_context is False
    assert metric_values([out_of_context], "key_fact_recall") == {"NB14": 0.0}
    assert metric_values([out_of_context], "key_fact_recall_gold_in_context") == {}
    assert metric_values([out_of_context], "asserted_absence_error_rate") == {}
    in_context = score_with("NB14", "single_section", NOT_FOUND_ANSWER, [hit("NBC-4.2", DSCR_TEXT)], True, DSCR_KEY)
    assert metric_values([in_context], "false_abstention_rate") == {"NB14": 1.0}
    assert metric_values([in_context], "asserted_absence_error_rate") == {"NB14": 1.0}


def test_summarize_reports_cis_counts_and_missing_populations() -> None:
    context = [hit("NBC-4.2", DSCR_TEXT + " Senior Underwriter.")]
    scores = [
        score_with("NB14", "single_section", "It is 1.25 [NBC-4.2].", context, True, DSCR_KEY),
        score_with("UA11", "unanswerable", NOT_FOUND_ANSWER, context, None, None),
        score_with("UA12", "unanswerable", "It is 1.25 [NBC-4.2].", context, None, None),
    ]
    summary = summarize(scores, n_resamples=200, seed=42)
    assert summary["key_fact_recall"]["all"]["mean"] == 0.5 and summary["key_fact_recall"]["all"]["n"] == 1
    assert summary["abstention_accuracy_unanswerable"]["correct"] == 1
    assert summary["abstention_accuracy_unanswerable"]["n"] == 2
    assert summary["answers_with_fabricated_citation"]["k"] == 0
    assert summary["format_compliance"] == {"k": 3, "n": 3, "rate": 1.0, "wilson_ci95": (0.4385, 1.0)}
    by_category = summarize_by_category(scores)
    assert by_category["unanswerable"]["abstention_accuracy_unanswerable"]["k"] == 1
    empty = summarize([scores[1]], n_resamples=50)
    assert empty["key_fact_recall"]["all"]["note"].startswith("not measured")


def test_question_score_round_trips_through_a_results_row() -> None:
    score = score_with("NB14", "single_section", "It is 1.25 [NBC-4.2].", [hit("NBC-4.2", DSCR_TEXT)], True, DSCR_KEY)
    row = json.loads(json.dumps({"score": score.to_dict(), "answer": "x"}))
    assert QuestionScore.from_dict(row["score"]) == score


# ---------------------------------------------------------------------------------------------------------------
# Prompt registry and BM25
# ---------------------------------------------------------------------------------------------------------------


def test_prompt_registry_points_at_the_existing_constants_and_hashes_them() -> None:
    assert PROMPTS["rag_answer_v1"] == (SYSTEM_PROMPT, ANSWER_INSTRUCTION)
    digest = prompt_sha256("rag_answer_v1")
    hashes = all_prompt_hashes()
    assert len(digest) == 64 and hashes["rag_answer_v1"] == digest
    # S2 registered two more ids (the L2 containment prompt and the naive L0 baseline); v1 is unchanged.
    assert set(hashes) == {"rag_answer_v1", "rag_answer_v2_containment", "injection_l0_naive_v1"}
    with pytest.raises(UnknownPromptError):
        get_prompt("rag_answer_v9")


def test_prompt_hash_changes_when_the_prompt_text_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    before = prompt_sha256("rag_answer_v1")
    monkeypatch.setitem(PROMPTS, "rag_answer_v1", (SYSTEM_PROMPT + " ", ANSWER_INSTRUCTION))
    assert prompt_sha256("rag_answer_v1") != before


def test_bm25_tokenizer_scores_and_ranking() -> None:
    assert tokenize("The DSCR is 1.25 for NBC-4.2!") == ["dscr", "1.25", "nbc-4.2"]
    index = BM25Index(["revolver limit five hundred", "equipment loan", "revolver revolver"])
    assert index.idf("absent") == 0.0
    ranked = index.top_k("revolver limit", 5)
    assert [position for position, _ in ranked] == [0, 2]  # document 1 shares no word and is not returned
    hits = GuidelineBM25Retriever().search("hurricane named-storm deductible coastal county", top_k=3)
    assert hits[0].section_id == "HM-6.1"
    assert [item.rank for item in hits] == list(range(1, len(hits) + 1)) and len(hits) <= 3


# ---------------------------------------------------------------------------------------------------------------
# The committed frozen inputs
# ---------------------------------------------------------------------------------------------------------------


def test_committed_frozen_hits_match_their_manifest_and_question_files() -> None:
    manifest = read_manifest()
    assert manifest["retrieval_mode"] == MODE_HYBRID_RERANK
    assert manifest["frozen_hits_sha256"] == sha256_of_file(FROZEN_HITS_FILE)
    for item in manifest["question_files"]:
        assert sha256_of_file(Path(__file__).parents[2] / item["path"]) == item["sha256"]
    records = read_frozen_hits()
    questions = load_eval_questions()
    assert [record.question_id for record in records] == [question.id for question, _ in questions]
    assert len(records) == manifest["n_questions"] == 133
    assert all(len(record.hits) == manifest["top_k"] == 5 for record in records)


def test_eval_loader_accepts_the_committed_inputs_and_refuses_a_changed_hits_file(tmp_path: Path) -> None:
    inputs = eval_generation.load_frozen_inputs()
    assert inputs.hashes["corpus_checksum"] == read_manifest()["corpus_checksum"]
    changed = tmp_path / "frozen_hits.jsonl"
    changed.write_text(FROZEN_HITS_FILE.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(eval_generation.EvaluationInputError, match="differs from its manifest"):
        eval_generation.load_frozen_inputs(changed, FROZEN_MANIFEST_FILE, KEY_FACTS_FILE)


def test_guideline_only_hits_exclude_regulatory_questions(tmp_path: Path) -> None:
    hits_path, manifest_path = tmp_path / "hits.jsonl", tmp_path / "manifest.json"
    args = freeze_retrieval.parse_args(
        ["--mode", MODE_BM25_GUIDELINES_ONLY, "--output", str(hits_path), "--manifest", str(manifest_path)]
    )
    freeze_retrieval.run(args)
    inputs = eval_generation.load_frozen_inputs(hits_path, manifest_path, KEY_FACTS_FILE)
    selected, excluded = eval_generation.select_records(inputs, None, None)
    info = excluded["regulatory_questions_excluded_guideline_only_hits"]
    assert info["applies"] and info["n"] == 22 and "MS15" in info["ids"] and "RG01" in info["ids"]
    assert len(selected) == 133 - 22
    with pytest.raises(freeze_retrieval.FreezeError, match="refusing to overwrite"):
        freeze_retrieval.check_mode_change(manifest_path, MODE_HYBRID_RERANK, force=False)


# ---------------------------------------------------------------------------------------------------------------
# The evaluation loop with fake clients
# ---------------------------------------------------------------------------------------------------------------


def record(question_id: str, category: str, hits: list[GuidelineHit], gold_ranks: list[int | None]) -> FrozenHitsRecord:
    """A frozen record for loop tests."""
    return FrozenHitsRecord(
        question_id=question_id,
        question=f"Question {question_id}?",
        category=category,
        question_file="eval/rag_questions.jsonl",
        retrieval_mode=MODE_HYBRID_RERANK,
        top_k=5,
        hits=hits,
        hit_locations=[HitLocation(doc_kind="guideline") for _ in hits],
        gold_ranks=gold_ranks,
    )


def test_frozen_record_context_and_gold_in_context() -> None:
    hits = [hit("NBC-4.1", "a", 1), hit("NBC-4.2", DSCR_TEXT, 2), hit("NBC-4.3", "c", 3), hit("NBC-1.1", "d", 4)]
    item = record("NB14", "single_section", hits, [2])
    assert [h.section_id for h in item.context_hits(3)] == ["NBC-4.1", "NBC-4.2", "NBC-4.3"]
    assert item.gold_in_context(3) is True and item.gold_in_context(1) is False
    assert record("UA11", "unanswerable", hits, []).gold_in_context(3) is None
    assert json.loads(frozen_hits_line(item))["hits"][1]["citation"] == "[NBC-4.2]"


def test_evaluate_records_scores_answers_with_a_fake_client() -> None:
    hits = [hit("NBC-4.2", DSCR_TEXT + " Refer to a Senior Underwriter.", 1)]
    records = [record("NB14", "single_section", hits, [1]), record("UA11", "unanswerable", hits, [])]

    def responder(system: str, messages: list[LLMMessage]) -> str:
        """Answer the DSCR question, abstain on the other."""
        if "NB14" in messages[0].content:
            return "The minimum is 1.25 [NBC-4.2]. Refer to a Senior Underwriter [NBC-4.2]."
        return NOT_FOUND_ANSWER

    client = FakeLLM(responder, model="fake-llm")
    rows, aborted = eval_generation.evaluate_records(records, {"NB14": DSCR_KEY}, client, 3, 200, progress=False)
    assert not aborted and [row["error"] for row in rows] == [None, None]
    assert rows[0]["score"]["key_fact_recall"] == 1.0 and rows[0]["is_fake"] is True
    assert rows[1]["score"]["is_not_found"] is True
    assert client.calls[0]["max_tokens"] == 200 and client.calls[0]["temperature"] == 0
    runtime = eval_generation.runtime_summary(rows)
    assert runtime["tokens"]["n"] == 2


class ScriptedClient:
    """A client that raises the scripted errors in order (then answers)."""

    provider = "fake"
    model = "fake-scripted"

    def __init__(self, outcomes: list[object]) -> None:
        """outcomes: exceptions to raise or LLMResponses to return, one per call."""
        self.outcomes = list(outcomes)

    def complete(self, system: str, messages: list[LLMMessage], max_tokens: int, temperature: float = 0) -> LLMResponse:
        """Raise or return the next scripted outcome."""
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, LLMResponse)
        return outcome


def partial_response(text: str) -> LLMResponse:
    """A response cut off at the token limit."""
    return LLMResponse(
        text=text,
        model="fake-scripted",
        input_tokens=5,
        output_tokens=200,
        latency_s=1.0,
        provider="fake",
        stop_reason="length",
    )


def test_context_http_and_truncation_outcomes_are_recorded_not_hidden() -> None:
    hits = [hit("NBC-4.2", DSCR_TEXT, 1)]
    records = [record(f"NB{index:02d}", "single_section", hits, [1]) for index in range(1, 4)]
    client = ScriptedClient(
        [
            ContextBudgetExceeded(4000, 200, 4096),
            IncompleteLLMResponse(partial_response("The minimum is 1.25 [NBC-4.2]. It also")),
            OllamaError("connection refused"),
        ]
    )
    rows, aborted = eval_generation.evaluate_records(records, {}, client, 3, 200, progress=False)
    assert not aborted
    assert rows[0]["error"]["type"] == "context" and "score" not in rows[0]
    assert rows[1]["error"] is None and rows[1]["truncated_at_num_predict"] is True and rows[1]["score"]
    assert rows[2]["error"]["type"] == "http"
    errors = eval_generation.error_summary(rows, aborted)
    assert errors["context"]["n"] == 1 and errors["http"]["n"] == 1 and errors["length_truncated"]["ids"] == ["NB02"]


def test_three_http_errors_in_a_row_stop_the_run() -> None:
    hits = [hit("NBC-4.2", DSCR_TEXT, 1)]
    records = [record(f"NB{index:02d}", "single_section", hits, [1]) for index in range(1, 6)]
    client = ScriptedClient([OllamaError("down")] * 5)
    rows, aborted = eval_generation.evaluate_records(records, {}, client, 3, 200, progress=False)
    assert aborted and len(rows) == 3


def test_config_loader_and_unsupported_options() -> None:
    config = eval_generation.load_generation_config("qwen3b_v1_k3")
    assert config.k == 3 and config.num_predict == 200 and config.prompt_id == "rag_answer_v1"
    assert "Qwen Research License" in (config.license_upstream_card or "")
    eval_generation.check_config_supported(config)
    with pytest.raises(eval_generation.EvaluationInputError, match="not supported"):
        eval_generation.check_config_supported(config.__class__(**{**config.__dict__, "prompt_id": "other"}))
    with pytest.raises(eval_generation.EvaluationInputError, match="unknown config"):
        eval_generation.load_generation_config("nope")


def test_question_files_constant_lists_both_sets() -> None:
    assert [path.name for path in QUESTION_FILES] == ["rag_questions.jsonl", "rag_unanswerable_extra.jsonl"]


# ---------------------------------------------------------------------------------------------------------------
# Numbers from the question, qualified durations, prompt hashes (review fixes)
# ---------------------------------------------------------------------------------------------------------------


def test_numbers_the_user_wrote_in_the_question_are_not_untraceable() -> None:
    context = [hit("NBC-4.2", DSCR_TEXT + " Refer to a Senior Underwriter.")]
    answer = "A DSCR of 1.18 is below the 1.25 minimum, so refer to a Senior Underwriter [NBC-4.2]."
    from_user = score_with("NB14", "single_section", answer, context, True, DSCR_KEY, "My DSCR is 1.18. Approve?")
    assert not from_user.has_untraceable_number and from_user.numbers_from_question == ["1.18"]
    invented = score_with("NB14", "single_section", answer, context, True, DSCR_KEY, "Can I approve this loan?")
    assert invented.has_untraceable_number and invented.untraceable_numbers == ["1.18"]
    summary = summarize([from_user, invented], n_resamples=50)
    assert summary["answers_with_untraceable_number"]["k"] == 1
    assert summary["answers_with_untraceable_number"]["answers_with_number_from_question"] == 1


def test_a_duration_with_qualifiers_is_traceable_to_its_section() -> None:
    context = [hit("NBC-3.5", "any missing statement month within the required 12 consecutive months;")]
    answer = "Statements must cover 12 months [NBC-3.5]."
    scored = score_with("NB09", "single_section", answer, context, True, None)
    assert not scored.has_untraceable_number


def test_prompt_template_hash_covers_the_template_code(monkeypatch: pytest.MonkeyPatch) -> None:
    from uwcopilot.rag import prompts

    before_prompt, before_template = prompt_sha256("rag_answer_v1"), prompts.prompt_template_sha256("rag_answer_v1")

    def changed_builder(question: str, hits: list[GuidelineHit]) -> tuple[str, list[LLMMessage], dict]:
        """The same constants, but a different wrapper around the documents."""
        system, messages, flags = prompts.build_answer_prompt(question, hits)
        return system, [LLMMessage(role="user", content="<docs>" + messages[0].content)], flags

    monkeypatch.setitem(prompts.PROMPT_BUILDERS, "rag_answer_v1", changed_builder)
    assert prompt_sha256("rag_answer_v1") == before_prompt  # the constants did not change ...
    assert prompts.prompt_template_sha256("rag_answer_v1") != before_template  # ... but the template hash sees it


# ---------------------------------------------------------------------------------------------------------------
# Generation times, cache entries, request hashes, statuses and R5 (review fixes)
# ---------------------------------------------------------------------------------------------------------------


def timed_response(text: str, latency_s: float) -> LLMResponse:
    """A response that took latency_s seconds to generate."""
    return LLMResponse(
        text=text, model="fake-scripted", input_tokens=5, output_tokens=7, latency_s=latency_s, provider="fake"
    )


def test_cached_answers_keep_their_original_generation_time_and_latency(tmp_path: Path) -> None:
    from uwcopilot.llm.cache import CachedLLMClient, LLMCache

    hits = [hit("NBC-4.2", DSCR_TEXT, 1)]
    records = [record("NB14", "single_section", hits, [1])]
    answer = "The minimum is 1.25 [NBC-4.2]."
    first = CachedLLMClient(ScriptedClient([timed_response(answer, 12.5)]), LLMCache(tmp_path))
    live_rows, _ = eval_generation.evaluate_records(records, {}, first, 3, 200, progress=False)
    second = CachedLLMClient(ScriptedClient([]), LLMCache(tmp_path))  # a resumed run: no model call is possible
    cached_rows, _ = eval_generation.evaluate_records(records, {}, second, 3, 200, progress=False)
    live, cached = live_rows[0], cached_rows[0]
    assert live["cached"] is False and cached["cached"] is True and cached["latency_s"] < 1.0
    assert cached["generated_latency_s"] == live["generated_latency_s"] == 12.5
    assert cached["answer_generated_at_utc"] == live["answer_generated_at_utc"] is not None
    assert cached["request_sha256"] == live["request_sha256"] and len(live["request_sha256"]) == 64
    runtime = eval_generation.runtime_summary(cached_rows)
    assert runtime["latency_s"]["p50"] == 12.5 and runtime["latency_s"]["n_from_cache_entries"] == 1
    assert cached["load_average_at_generation"] is None  # this process did not generate it: no load average to claim
    assert runtime["load_average_at_generation"].startswith("not recorded")
    times = eval_generation.answer_times(cached_rows)
    assert times["earliest_answer_generated_at_utc"] == live["answer_generated_at_utc"]


def test_top_level_status_is_measured_only_when_every_config_is(tmp_path: Path) -> None:
    assert eval_generation.overall_status(["measured", "partial"]) == "partial"
    assert eval_generation.overall_status(["smoke", "smoke"]) == "smoke"
    assert eval_generation.overall_status(["measured", "measured"]) == "measured"
    path = tmp_path / "metrics.json"
    base = {"frozen_inputs": {}, "provenance": {}}
    eval_generation.update_metrics_file(path, "champ", dict(base, status="measured"), {})
    eval_generation.update_metrics_file(path, "chall", dict(base, status="partial"), {})
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["status"] == "partial"
    assert document["config_statuses"] == {"champ": "measured", "chall": "partial"}


def test_a_stale_reproducibility_result_is_dropped_when_the_champion_is_rerun(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    r5 = {"config": "champ", "n": 1, "bit_identical": 1, "original_answer_sha256": {"NB14": "a" * 64}}
    block = {"status": "measured", "frozen_inputs": {}}
    eval_generation.update_metrics_file(path, "champ", block, {"reproducibility": r5})
    eval_generation.update_metrics_file(path, "champ", block, {}, {"NB14": "a" * 64})
    assert json.loads(path.read_text(encoding="utf-8"))["reproducibility"] == r5  # same answers: still valid
    eval_generation.update_metrics_file(path, "champ", block, {}, {"NB14": "b" * 64})
    assert json.loads(path.read_text(encoding="utf-8"))["reproducibility"].startswith("not measured for the current")


def test_reproducibility_needs_a_measured_run_with_its_own_rows_file(tmp_path: Path) -> None:
    rows_path = tmp_path / "champ.jsonl"
    rows_path.write_text('{"question_id": "NB14"}\n', encoding="utf-8")
    metrics = tmp_path / "metrics.json"
    metrics.write_text(json.dumps({"configs": {"champ": {"status": "smoke"}}}), encoding="utf-8")
    with pytest.raises(eval_generation.EvaluationInputError, match="not 'measured'"):
        eval_generation.check_measured_run(metrics, "champ", rows_path)
    metrics.write_text(json.dumps({"configs": {"champ": {"status": "measured", "rows_sha256": "0" * 64}}}), "utf-8")
    with pytest.raises(eval_generation.EvaluationInputError, match="not the rows file"):
        eval_generation.check_measured_run(metrics, "champ", rows_path)
    good = {"status": "measured", "rows_sha256": sha256_of_file(rows_path)}
    metrics.write_text(json.dumps({"configs": {"champ": good}}), encoding="utf-8")
    eval_generation.check_measured_run(metrics, "champ", rows_path)


def test_freeze_proof_covers_every_frozen_input() -> None:
    roles = [item["role"] for item in eval_generation.collect_freeze_proof(KEY_FACTS_FILE, FROZEN_HITS_FILE)]
    assert roles == ["key_facts", "frozen_hits", "question_file", "question_file", "margins"]

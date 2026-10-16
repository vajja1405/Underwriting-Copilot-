"""The LLM migration gate: non-inferiority decisions, refusals, the margins file and the certificate.

All data here is SYNTHETIC paired per-question data built in the test; no model is called.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import llm_migration_gate
from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.governance.freeze_proof import migration_section_sha256
from uwcopilot.governance.migration_gate import (
    GateConfigError,
    GatedMetric,
    MigrationSettings,
    check_prompts_comparable,
    check_same_inputs,
    compare_metric,
    load_migration_settings,
    non_inferiority_decision,
    overall_decision,
    parse_migration_settings,
    render_certificate,
    validate_reproducibility,
)
from uwcopilot.rag.frozen_hits import FrozenHitsRecord, HitLocation, frozen_hits_line
from uwcopilot.rag.generation_eval import KeyFact, KeyFactRow, score_record
from uwcopilot.rag.prompt import NOT_FOUND_ANSWER
from uwcopilot.tools.schemas import GuidelineHit

SETTINGS = MigrationSettings(metrics=(), resamples=1000, seed=42, confidence=0.95)
RECALL = GatedMetric("key_fact_recall", "higher_is_better", 0.05, "keyed")
FABRICATED = GatedMetric("answers_with_fabricated_citation", "lower_is_better", 0.02, "all")


# ---------------------------------------------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ci", "direction", "expected"),
    [
        ((-0.05, 0.02), "higher_is_better", "PASS"),  # the bound equal to -m passes
        ((-0.20, -0.06), "higher_is_better", "FAIL"),
        ((-0.10, 0.01), "higher_is_better", "INCONCLUSIVE"),
        ((-0.01, 0.02), "lower_is_better", "PASS"),
        ((0.03, 0.10), "lower_is_better", "FAIL"),
        ((0.00, 0.05), "lower_is_better", "INCONCLUSIVE"),
    ],
)
def test_non_inferiority_decision(ci: tuple[float, float], direction: str, expected: str) -> None:
    margin = 0.05 if direction == "higher_is_better" else 0.02
    assert non_inferiority_decision(ci[0], ci[1], margin, direction) == expected


def test_overall_is_pass_only_when_every_metric_passes() -> None:
    assert overall_decision(["PASS", "PASS"]) == "PASS"
    assert overall_decision(["PASS", "INCONCLUSIVE"]) == "INCONCLUSIVE"
    assert overall_decision(["INCONCLUSIVE", "FAIL"]) == "FAIL"
    assert overall_decision([]) == "INCONCLUSIVE"


def ids(n: int) -> list[str]:
    """Question ids Q001.. for synthetic paired data."""
    return [f"Q{index:03d}" for index in range(1, n + 1)]


def test_identical_runs_pass() -> None:
    values = {question_id: (index % 3) / 2 for index, question_id in enumerate(ids(60))}
    block = compare_metric(RECALL, values, dict(values), SETTINGS)
    assert block["paired_diff"] == {"mean": 0.0, "ci95": [0.0, 0.0]} and block["decision"] == "PASS"


def test_clearly_worse_challenger_fails() -> None:
    champion = {question_id: 1.0 for question_id in ids(60)}
    challenger = {question_id: (0.0 if index % 2 else 1.0) for index, question_id in enumerate(ids(60))}
    block = compare_metric(RECALL, champion, challenger, SETTINGS)
    assert block["paired_diff"]["mean"] == -0.5 and block["paired_diff"]["ci95"][1] < -0.05
    assert block["decision"] == "FAIL"


def test_small_noisy_difference_is_inconclusive() -> None:
    champion = {question_id: 0.0 for question_id in ids(30)}
    challenger = {question_id: (1.0 if index in (3, 17) else 0.0) for index, question_id in enumerate(ids(30))}
    block = compare_metric(FABRICATED, champion, challenger, SETTINGS)
    low, high = block["paired_diff"]["ci95"]
    assert low <= 0.02 < high and block["decision"] == "INCONCLUSIVE"


def test_only_questions_in_both_populations_are_paired() -> None:
    block = compare_metric(RECALL, {"Q001": 1.0, "Q002": 0.5}, {"Q002": 0.5, "Q003": 0.0}, SETTINGS)
    assert block["n_paired"] == 1
    empty = compare_metric(RECALL, {"Q001": 1.0}, {"Q002": 1.0}, SETTINGS)
    assert empty["decision"] == "INCONCLUSIVE" and empty["n_paired"] == 0


def test_mismatched_inputs_are_reported() -> None:
    inputs = {
        "question_ids": ["NB01"],
        "frozen_hits_sha256": "a" * 64,
        "key_facts_sha256": "b" * 64,
        "question_sets": {"eval/rag_questions.jsonl": "c" * 64},
        "corpus_checksum": "d" * 64,
    }
    assert check_same_inputs(inputs, dict(inputs)) == []
    changed = dict(inputs, frozen_hits_sha256="e" * 64, corpus_checksum="f" * 64)
    assert check_same_inputs(inputs, changed) == [
        "frozen_hits_sha256: champion and challenger differ",
        "corpus_checksum: champion and challenger differ",
    ]
    missing = dict(inputs)
    del missing["key_facts_sha256"]
    assert check_same_inputs(inputs, missing) == ["key_facts_sha256: missing from a run's frozen_inputs"]


# ---------------------------------------------------------------------------------------------------------------
# The margins file
# ---------------------------------------------------------------------------------------------------------------


def test_committed_margins_are_the_plan_table() -> None:
    settings = load_migration_settings(CONFIG_DIR / "release_gate.yaml")
    margins = {metric.name: (metric.direction, metric.margin) for metric in settings.metrics}
    assert margins == {
        "key_fact_recall": ("higher_is_better", 0.05),
        "abstention_accuracy_unanswerable": ("higher_is_better", 0.10),
        "asserted_absence_error_rate": ("lower_is_better", 0.05),
        "answers_with_fabricated_citation": ("lower_is_better", 0.02),
        "answers_with_untraceable_number": ("lower_is_better", 0.05),
    }
    assert (settings.resamples, settings.seed, settings.confidence) == (1000, 42, 0.95)


def test_margins_file_must_match_the_code() -> None:
    good = {
        "migration": {
            "decision_rule": "non_inferiority",
            "gated_metrics": {
                "key_fact_recall": {"direction": "higher_is_better", "margin": 0.05, "population": "keyed"}
            },
        }
    }
    assert parse_migration_settings(good).metrics[0].name == "key_fact_recall"
    wrong_population = json.loads(json.dumps(good))
    wrong_population["migration"]["gated_metrics"]["key_fact_recall"]["population"] = "all"
    with pytest.raises(GateConfigError, match="population"):
        parse_migration_settings(wrong_population)
    unknown = {"migration": {"decision_rule": "non_inferiority", "gated_metrics": {"bleu": {}}}}
    with pytest.raises(GateConfigError, match="not computed"):
        parse_migration_settings(unknown)
    with pytest.raises(GateConfigError, match="no `migration:`"):
        parse_migration_settings({})


# ---------------------------------------------------------------------------------------------------------------
# The script, end to end on SYNTHETIC runs: 40 keyed questions and 30 unanswerables over one SYNTHETIC section
# ---------------------------------------------------------------------------------------------------------------

RELEASE_GATE = CONFIG_DIR / "release_gate.yaml"
SECTION = GuidelineHit(
    section_id="NBC-4.2",
    title="NBC-4.2 Minimum DSCR (SYNTHETIC test section)",
    source_doc="northwind_business_credit",
    text="The minimum decision DSCR for a term loan is 1.25. Below it, refer to a Senior Underwriter.",
    score=1.0,
    rank=1,
)
FULL_ANSWER = "The minimum is 1.25 [NBC-4.2]. Refer to a Senior Underwriter [NBC-4.2]."
HALF_ANSWER = "The minimum is 1.25 [NBC-4.2]."
KEYED = [f"K{index:02d}" for index in range(40)]
UNANSWERABLE = [f"U{index:02d}" for index in range(30)]
MARGINS_COMMITTED = "2026-10-12T19:39:16+00:00"
INPUTS_COMMITTED = "2026-10-12T19:00:00+00:00"


def sha(text: str) -> str:
    """SHA-256 of a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def frozen_record(question_id: str) -> FrozenHitsRecord:
    """A frozen record whose only hit is the SYNTHETIC DSCR section."""
    answerable = question_id.startswith("K")
    return FrozenHitsRecord(
        question_id=question_id,
        question=f"SYNTHETIC test question {question_id}: what is the DSCR rule?",
        category="single_section" if answerable else "unanswerable",
        question_file="eval/rag_questions.jsonl",
        retrieval_mode="hybrid_rerank",
        top_k=5,
        hits=[SECTION],
        hit_locations=[HitLocation(doc_kind="guideline")],
        gold_ranks=[1] if answerable else [],
    )


def write_inputs(tmp_path: Path) -> dict[str, Path]:
    """The frozen hits and key facts files of the synthetic runs."""
    hits_path = tmp_path / "frozen_hits.jsonl"
    hits_path.write_text("".join(frozen_hits_line(frozen_record(q)) + "\n" for q in KEYED + UNANSWERABLE), "utf-8")
    facts = [KeyFact(text="1.25", kind="number"), KeyFact(text="Senior Underwriter", kind="role")]
    key_facts_path = tmp_path / "key_facts.jsonl"
    lines = [KeyFactRow(id=q, gold_section="NBC-4.2", key_facts=facts).model_dump_json() for q in KEYED]
    key_facts_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"hits": hits_path, "key_facts": key_facts_path}


def freeze_proof(inputs: dict[str, Path], committed: str, margins_committed: str) -> list[dict[str, Any]]:
    """Freeze records as eval_generation writes them, with chosen commit dates."""

    def record(role: str, path: str, kind: str, digest: str | None, date: str) -> dict[str, Any]:
        """One record."""
        first = {"sha": "c" * 40, "author_date": date, "committer_date": date}
        return {"role": role, "path": path, "kind": kind, "sha256": digest, "first_committed": first, "note": ""}

    return [
        record("key_facts", "eval/rag_key_facts.jsonl", "file", sha256_of_file(inputs["key_facts"]), committed),
        record("frozen_hits", "eval/generation/frozen_hits.jsonl", "file", sha256_of_file(inputs["hits"]), committed),
        record("question_file", "eval/rag_questions.jsonl", "file", "5" * 64, committed),
        record(
            "margins",
            "config/release_gate.yaml",
            "migration_section",
            migration_section_sha256(RELEASE_GATE.read_bytes()),
            margins_committed,
        ),
    ]


def write_run(
    tmp_path: Path,
    config_id: str,
    full_answers: int,
    answers_at: str,
    *,
    committed: str = INPUTS_COMMITTED,
    margins_committed: str = MARGINS_COMMITTED,
    block_written_at: str | None = None,
) -> dict[str, Any]:
    """Rows file plus the configs.<id> block of a synthetic run whose first `full_answers` keyed answers are complete
    (recall 1.0) and the rest half complete (0.5); every unanswerable is answered with the not-found sentence."""
    inputs = write_inputs(tmp_path)
    key_rows = {q: KeyFactRow(id=q, gold_section="NBC-4.2", key_facts=[KeyFact(text="1.25", kind="number"),
                KeyFact(text="Senior Underwriter", kind="role")]) for q in KEYED}  # fmt: skip
    rows = []
    for index, question_id in enumerate(KEYED + UNANSWERABLE):
        if question_id.startswith("K"):
            answer = FULL_ANSWER if index < full_answers else HALF_ANSWER
        else:
            answer = NOT_FOUND_ANSWER
        score = score_record(frozen_record(question_id), answer, 3, key_rows.get(question_id))
        rows.append(
            {
                "question_id": question_id,
                "answer": answer,
                "answer_sha256": sha(answer),
                "request_sha256": sha(f"request {question_id}"),
                "answer_generated_at_utc": answers_at,
                "score": score.to_dict(),
            }
        )
    rows_path = tmp_path / "results" / "generation" / f"{config_id}.jsonl"
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    rows_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    question_ids = sorted(row["question_id"] for row in rows)
    return {
        "status": "measured",
        "provider": "ollama",
        "model": f"model-{config_id}",
        "ollama_digest": "1" * 64,
        "ollama_version": "0.40.3",
        "model_license": "SYNTHETIC test license",
        "model_license_sources": {"docker_artifact": "Apache-2.0 (test)", "upstream_card": "test card"},
        "prompt_id": "rag_answer_v1",
        "prompt_sha256": "2" * 64,
        "prompt_template_sha256": "9" * 64,
        "k": 3,
        "options": {"num_predict": 200},
        "n_questions": len(rows),
        "scored_question_ids": question_ids,
        "frozen_inputs": {
            "question_ids": question_ids,
            "frozen_hits_sha256": sha256_of_file(inputs["hits"]),
            "key_facts_sha256": sha256_of_file(inputs["key_facts"]),
            "question_sets": {"eval/rag_questions.jsonl": "5" * 64},
            "corpus_checksum": "6" * 64,
            "retrieval_mode": "hybrid_rerank",
            "n_questions": len(rows),
        },
        "freeze_proof": freeze_proof(inputs, committed, margins_committed),
        "rows_path": f"results/generation/{config_id}.jsonl",
        "rows_sha256": sha256_of_file(rows_path),
        "earliest_answer_generated_at_utc": answers_at,
        "latest_answer_generated_at_utc": answers_at,
        "generated_at_utc": block_written_at or answers_at,
        "git_sha": "unknown",
        "load_average": {"start": [0.1, 0.1, 0.1], "end": [0.2, 0.2, 0.2]},
        "latency_s": {"p50": 40.0, "p95": 90.0, "n_live_calls": 70},
        "tokens": {"in_mean": 1500.0, "out_mean": 80.0},
        "errors": {"http": {"n": 0}, "context": {"n": 0}},
        "provenance": {"models": [{"provider": "ollama", "model": f"model-{config_id}", "digest": "1" * 64}]},
    }


def gate_args(tmp_path: Path, metrics: dict[str, Any], *extra: str) -> list[str]:
    """Write the metrics file and return the gate's command line (output inside tmp_path)."""
    metrics_path = tmp_path / "results" / "generation_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
    return [
        "--champion", "champ", "--challenger", "chall",
        "--metrics", str(metrics_path),
        "--release-gate", str(RELEASE_GATE),
        "--output-dir", str(tmp_path / "results"),
        "--certificate-dir", str(tmp_path / "certs"),
        "--repo-root", str(tmp_path),
        "--frozen-hits", str(tmp_path / "frozen_hits.jsonl"),
        "--key-facts", str(tmp_path / "key_facts.jsonl"),
        *extra,
    ]  # fmt: skip


def two_runs(tmp_path: Path, champion_full: int = 20, challenger_full: int = 20, **challenger: Any) -> dict:
    """Metrics with a champion run (answers 20:00) and a challenger run (answers 21:00 unless overridden)."""
    challenger_at = challenger.pop("answers_at", "2026-10-12T21:00:00+00:00")
    return {
        "configs": {
            "champ": write_run(tmp_path, "champ", champion_full, "2026-10-12T20:00:00+00:00"),
            "chall": write_run(tmp_path, "chall", challenger_full, challenger_at, **challenger),
        }
    }


def read_report(tmp_path: Path) -> dict[str, Any]:
    """The gate's JSON report."""
    return json.loads((tmp_path / "results" / "llm_migration_champ_vs_chall.json").read_text(encoding="utf-8"))


def test_script_certifies_frozen_identical_runs(tmp_path: Path) -> None:
    assert llm_migration_gate.main(gate_args(tmp_path, two_runs(tmp_path))) == 0
    report = read_report(tmp_path)
    assert report["overall"] == "PASS" and report["freeze"]["all_frozen"] is True
    assert report["metrics"]["key_fact_recall"]["n_paired"] == 40
    assert report["metrics"]["abstention_accuracy_unanswerable"]["n_paired"] == 30
    assert report["rescored"] == {"champion": 70, "challenger": 70, "differences": 0}
    assert report["request_hashes"] == {"compared": 70, "differences": 0}
    assert report["margins_source"] == "config/release_gate.yaml@" + "c" * 40
    certificate = (tmp_path / "certs" / "champ_vs_chall.md").read_text(encoding="utf-8")
    assert "**Overall decision: PASS**" in certificate and "NOT VALID FOR RELEASE" not in certificate
    assert "Apache-2.0 (test)" in certificate and "test card" in certificate
    assert render_certificate(report) == certificate


def test_script_fails_a_worse_challenger(tmp_path: Path) -> None:
    assert llm_migration_gate.main(gate_args(tmp_path, two_runs(tmp_path, champion_full=40, challenger_full=0))) == 1
    report = read_report(tmp_path)
    assert report["metrics"]["key_fact_recall"]["decision"] == "FAIL" and report["overall"] == "FAIL"


def test_margins_committed_after_the_challenger_answers_are_refused_even_after_a_cached_rerun(tmp_path: Path) -> None:
    # Answers generated at 21:00, margins loosened and committed at 21:10, then an all-cache re-run rewrote the
    # block at 21:12. The gate compares the margins commit with the answers' generation time, not the block's.
    metrics = two_runs(
        tmp_path, margins_committed="2026-10-12T21:10:00+00:00", block_written_at="2026-10-12T21:12:00+00:00"
    )
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 3
    assert llm_migration_gate.main(gate_args(tmp_path, metrics, "--allow-unfrozen-inputs")) == 0
    report = read_report(tmp_path)
    assert report["margins_frozen_before_challenger"] is False and report["freeze"]["all_frozen"] is False
    assert "NOT VALID FOR RELEASE" in (tmp_path / "certs" / "champ_vs_chall.md").read_text(encoding="utf-8")


def test_margins_changed_since_the_challenger_run_are_refused(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path)
    metrics["configs"]["chall"]["freeze_proof"][-1]["sha256"] = "e" * 64  # the run saw different margins
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 3


def test_key_facts_committed_after_the_champion_answers_are_refused(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path)
    metrics["configs"]["champ"] = write_run(
        tmp_path, "champ", 20, "2026-10-12T20:00:00+00:00", committed="2026-10-12T20:30:00+00:00"
    )
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 3


def test_key_facts_edited_after_the_runs_are_refused(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path)
    key_facts = tmp_path / "key_facts.jsonl"
    key_facts.write_text(key_facts.read_text(encoding="utf-8").replace("1.25", "1.10"), encoding="utf-8")
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 2


def test_runs_without_generation_times_are_refused(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path, answers_at="")
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 3


def test_same_prompt_id_with_a_different_prompt_is_refused(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path)
    metrics["configs"]["chall"]["prompt_sha256"] = "f" * 64
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 2
    assert check_prompts_comparable({"prompt_id": "a"}, {"prompt_id": "b"}) == []


def test_a_different_request_for_one_question_is_refused(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path)
    rows_path = tmp_path / "results" / "generation" / "chall.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    rows[5]["request_sha256"] = sha("a different prompt")
    rows_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    metrics["configs"]["chall"]["rows_sha256"] = sha256_of_file(rows_path)
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 2


def test_scores_written_by_a_different_scorer_are_refused(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path)
    rows_path = tmp_path / "results" / "generation" / "chall.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["score"]["key_fact_recall"] = 0.0  # what an older or newer scoring rule might have stored
    rows_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    metrics["configs"]["chall"]["rows_sha256"] = sha256_of_file(rows_path)
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 2


def test_reproducibility_is_reported_only_for_this_champion_run(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path)
    good = {"config": "champ", "n": 1, "bit_identical": 1, "model": {"digest": "1" * 64},
            "original_answer_sha256": {"K00": sha(FULL_ANSWER)}}  # fmt: skip
    metrics["reproducibility"] = dict(good, original_answer_sha256={"K00": "0" * 64})
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 0
    assert read_report(tmp_path)["reproducibility"].startswith("not measured for this champion run")
    metrics["reproducibility"] = good
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 0
    assert read_report(tmp_path)["reproducibility"]["bit_identical"] == 1
    other_model = validate_reproducibility(
        dict(good, model={"digest": "8" * 64}), "champ", {"ollama_digest": "1" * 64}, {}
    )
    assert other_model.endswith("(R5 used a different model digest)")


def test_script_refuses_missing_unmeasured_or_edited_runs(tmp_path: Path) -> None:
    metrics = two_runs(tmp_path)
    missing = {"configs": {"champ": metrics["configs"]["champ"]}}
    assert llm_migration_gate.main(gate_args(tmp_path, missing)) == 2  # missing challenger: input error, not margins
    smoke = {
        "configs": {"champ": dict(metrics["configs"]["champ"], status="smoke"), "chall": metrics["configs"]["chall"]}
    }
    assert llm_migration_gate.main(gate_args(tmp_path, smoke)) == 2
    different = json.loads(json.dumps(metrics))
    different["configs"]["chall"]["frozen_inputs"]["corpus_checksum"] = "8" * 64
    assert llm_migration_gate.main(gate_args(tmp_path, different)) == 2
    rows_path = tmp_path / "results" / "generation" / "chall.jsonl"
    rows_path.write_text(rows_path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert llm_migration_gate.main(gate_args(tmp_path, metrics)) == 2
    assert not (tmp_path / "results" / "llm_migration_champ_vs_chall.json").exists()

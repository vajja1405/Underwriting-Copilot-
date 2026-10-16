"""LLM migration gate: champion vs challenger non-inferiority on the frozen generation eval (docs/innovations.md, S1).

Usage:
    uv run python scripts/llm_migration_gate.py --champion qwen3b_v1_k3 --challenger qwen15b_v1_k3

Needs both configurations evaluated in full by scripts/eval_generation.py (status "measured" in
results/generation_metrics.json, per-question rows in results/generation/<config>.jsonl). The eval files, the
frozen hits, the key facts and the margins must be committed before the runs start (see step 4).

Steps:
1. Both configurations must be in the metrics file with status "measured" (otherwise exit 2).
2. Refuse (exit 2) unless both runs used identical question ids, frozen-hits hash, key-facts hash, question-file
   hashes and corpus checksum, scored the same questions, used the same prompt text when the prompt id is the same,
   sent byte-identical requests when the prompt id and k are the same, and their per-question files still match the
   sha256 recorded when they were written.
3. Recompute every stored per-question score with the CURRENT scoring code (rag.generation_eval.score_record) from
   the stored answer, the frozen hits and the key facts, after checking that those files still have the hashes the
   runs recorded. Any difference means the scorer changed between the runs (or since): refuse (exit 2) and re-run
   scripts/eval_generation.py for both configs (the LLM cache makes that fast and keeps the generation times).
4. Freeze checks (exit 3): the key facts, question files and frozen hits each run used must have been committed
   before that run's earliest answer was generated, and the `migration:` section of config/release_gate.yaml before
   the challenger's earliest answer, unchanged since (governance/freeze_proof.py, recorded when each run started).
   --allow-unfrozen-inputs (alias --allow-unfrozen-margins) overrides this for development; the certificate then says
   "NOT VALID FOR RELEASE".
5. Paired bootstrap of (challenger - champion) per gated metric and the non-inferiority decision
   (uwcopilot.governance.migration_gate). Overall PASS only if every gated metric passes.
6. Write results/llm_migration_<A>_vs_<B>.json and render docs/governance/migration_certificates/<A>_vs_<B>.md
   from that JSON only. The champion's R5 reproducibility result is included only when it compared this champion
   run's answers with the same model digest.

No model is called. Exit codes: 0 overall PASS, 1 FAIL or INCONCLUSIVE, 2 refused (inputs differ, missing or
re-scored differently), 3 refused (an input or the margins were not frozen before the answers were generated).
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import CONFIG_DIR, DOCS_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.governance.freeze_proof import migration_section_sha256
from uwcopilot.governance.migration_gate import (
    GateConfigError,
    InputMismatch,
    MigrationSettings,
    check_prompts_comparable,
    check_same_inputs,
    compare_runs,
    freeze_summary,
    load_migration_settings,
    render_certificate,
    request_hash_differences,
    validate_reproducibility,
)
from uwcopilot.governance.provenance import display_path, provenance_block
from uwcopilot.rag.frozen_hits import FROZEN_HITS_FILE, KEY_FACTS_FILE, read_frozen_hits
from uwcopilot.rag.generation_eval import QuestionScore, load_key_facts, metric_values, score_record

METRICS_FILE = RESULTS_DIR / "generation_metrics.json"
RELEASE_GATE_FILE = CONFIG_DIR / "release_gate.yaml"
CERTIFICATE_DIR = DOCS_DIR / "governance" / "migration_certificates"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--champion", required=True, help="champion config id")
    parser.add_argument("--challenger", required=True, help="challenger config id")
    parser.add_argument("--metrics", type=Path, default=METRICS_FILE, help="results/generation_metrics.json")
    parser.add_argument("--release-gate", type=Path, default=RELEASE_GATE_FILE, help="margins file")
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR, help="where the gate JSON goes")
    parser.add_argument("--certificate-dir", type=Path, default=CERTIFICATE_DIR, help="where the certificate goes")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="repository (for paths and git)")
    parser.add_argument("--frozen-hits", type=Path, default=FROZEN_HITS_FILE, help="frozen hits used for re-scoring")
    parser.add_argument("--key-facts", type=Path, default=KEY_FACTS_FILE, help="key facts used for re-scoring")
    parser.add_argument(
        "--allow-unfrozen-inputs",
        "--allow-unfrozen-margins",
        dest="allow_unfrozen_inputs",
        action="store_true",
        help="development only: run even if an input or the margins were not frozen before the answers",
    )
    return parser.parse_args(argv)


def gate_report_path(args: argparse.Namespace) -> Path:
    """results/llm_migration_<champion>_vs_<challenger>.json (in --output-dir)."""
    return args.output_dir / f"llm_migration_{args.champion}_vs_{args.challenger}.json"


def load_config_block(document: dict[str, Any], config_id: str) -> dict[str, Any]:
    """The measured configs.<id> block, or InputMismatch."""
    block = (document.get("configs") or {}).get(config_id)
    if block is None:
        raise InputMismatch(
            f"{config_id}: not in the metrics file; run scripts/eval_generation.py --config {config_id}"
        )
    if block.get("status") != "measured":
        raise InputMismatch(f"{config_id}: status is {block.get('status')!r}, not 'measured'")
    return block


def load_rows(block: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    """The per-question rows of a run, after checking the rows file against its recorded sha256."""
    path = repo_root / block["rows_path"]
    if not path.exists():
        raise InputMismatch(f"{block['rows_path']} is missing")
    if sha256_of_file(path) != block.get("rows_sha256"):
        raise InputMismatch(f"{block['rows_path']} changed since the evaluation wrote it (sha256 mismatch)")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scores_of(rows: list[dict[str, Any]]) -> list[QuestionScore]:
    """The stored QuestionScores of the scored rows."""
    return [QuestionScore.from_dict(row["score"]) for row in rows if row.get("score")]


def check_runs_comparable(champion: dict[str, Any], challenger: dict[str, Any]) -> list[str]:
    """Every reason the two runs cannot be compared ([] when they can), from the two config blocks."""
    problems = check_same_inputs(champion.get("frozen_inputs", {}), challenger.get("frozen_inputs", {}))
    if champion.get("scored_question_ids") != challenger.get("scored_question_ids"):
        problems.append("scored_question_ids: the runs scored different questions (errors in one run?)")
    problems.extend(check_prompts_comparable(champion, challenger))
    return problems


def same_prompt_and_k(champion: dict[str, Any], challenger: dict[str, Any]) -> bool:
    """True when both runs built their prompts the same way, so every request must be byte-identical."""
    return champion.get("prompt_id") == challenger.get("prompt_id") and champion.get("k") == challenger.get("k")


def check_frozen_files(block: dict[str, Any], frozen_hits_path: Path, key_facts_path: Path) -> None:
    """The frozen hits and key facts on disk must be the files the run recorded (they are used for re-scoring)."""
    frozen = block.get("frozen_inputs") or {}
    if sha256_of_file(frozen_hits_path) != frozen.get("frozen_hits_sha256"):
        raise InputMismatch(f"{frozen_hits_path.name} on disk differs from the frozen hits the runs used")
    if sha256_of_file(key_facts_path) != frozen.get("key_facts_sha256"):
        raise InputMismatch(f"{key_facts_path.name} on disk differs from the key facts the runs used")


def rescore_differences(
    rows: list[dict[str, Any]], block: dict[str, Any], frozen_hits_path: Path, key_facts_path: Path
) -> list[str]:
    """Question ids whose stored score differs from the score the current code computes from the stored answer."""
    records = {record.question_id: record for record in read_frozen_hits(frozen_hits_path)}
    key_rows = load_key_facts(key_facts_path)
    different: list[str] = []
    for row in rows:
        if not row.get("score"):
            continue
        question_id = row["question_id"]
        record = records.get(question_id)
        if record is None or row.get("answer") is None:
            different.append(question_id)
            continue
        recomputed = score_record(record, row["answer"], int(block["k"]), key_rows.get(question_id))
        if recomputed.to_dict() != QuestionScore.from_dict(row["score"]).to_dict():
            different.append(question_id)
    return sorted(different)


def earliest_answer_time(rows: list[dict[str, Any]]) -> str | None:
    """When the run's first answer was generated (None if any scored row has no generation time)."""
    times: list[str] = []
    for row in rows:
        if not row.get("score"):
            continue
        if not row.get("answer_generated_at_utc"):
            return None
        times.append(row["answer_generated_at_utc"])
    if not times:
        return None
    return min(times, key=datetime.fromisoformat)


def answers_by_question(rows: list[dict[str, Any]]) -> dict[str, str]:
    """{question id: answer sha256} for the scored rows."""
    return {row["question_id"]: row.get("answer_sha256", "") for row in rows if row.get("score")}


def values_by_metric(scores: list[QuestionScore], settings: MigrationSettings) -> dict[str, dict[str, float]]:
    """{gated metric: {question id: value}} for one run."""
    return {metric.name: metric_values(scores, metric.name) for metric in settings.metrics}


def config_summary(config_id: str, block: dict[str, Any]) -> dict[str, Any]:
    """What the certificate says about one configuration."""
    return {
        "config": config_id,
        "provider": block.get("provider"),
        "model": block.get("model"),
        "ollama_digest": block.get("ollama_digest"),
        "ollama_version": block.get("ollama_version"),
        "model_license": block.get("model_license"),
        "model_license_sources": block.get("model_license_sources"),
        "prompt_id": block.get("prompt_id"),
        "prompt_sha256": block.get("prompt_sha256"),
        "prompt_template_sha256": block.get("prompt_template_sha256"),
        "k": block.get("k"),
        "options": block.get("options"),
        "n_questions": block.get("n_questions"),
        "earliest_answer_generated_at_utc": block.get("earliest_answer_generated_at_utc"),
        "latest_answer_generated_at_utc": block.get("latest_answer_generated_at_utc"),
        "generated_at_utc": block.get("generated_at_utc"),
        "git_sha": block.get("git_sha"),
        "load_average": block.get("load_average"),
        "rows_path": block.get("rows_path"),
        "rows_sha256": block.get("rows_sha256"),
    }


def not_gated_summary(block: dict[str, Any]) -> dict[str, Any]:
    """Latency, tokens and error counts (reported, never gated)."""
    latency = block.get("latency_s")
    tokens = block.get("tokens")
    errors = block.get("errors") or {}
    return {
        "latency_p50_s": latency.get("p50") if isinstance(latency, dict) else latency,
        "latency_p95_s": latency.get("p95") if isinstance(latency, dict) else None,
        "n_live_calls": latency.get("n_live_calls") if isinstance(latency, dict) else 0,
        "tokens_in_mean": tokens.get("in_mean") if isinstance(tokens, dict) else None,
        "tokens_out_mean": tokens.get("out_mean") if isinstance(tokens, dict) else None,
        "load_average_at_generation": block.get("load_average_at_generation", "not recorded"),
        "errors": {name: (value.get("n") if isinstance(value, dict) else value) for name, value in errors.items()},
    }


def verified_inputs(args: argparse.Namespace, document: dict[str, Any]) -> dict[str, Any]:
    """Load both runs and run every input check of steps 1-3; raises InputMismatch on the first failure."""
    champion = load_config_block(document, args.champion)
    challenger = load_config_block(document, args.challenger)
    problems = check_runs_comparable(champion, challenger)
    if problems:
        raise InputMismatch("; ".join(problems))
    champion_rows = load_rows(champion, args.repo_root)
    challenger_rows = load_rows(challenger, args.repo_root)
    request_hashes: dict[str, Any] | str = "not compared (different prompt id or k: the requests differ by design)"
    if same_prompt_and_k(champion, challenger):
        different = request_hash_differences(champion_rows, challenger_rows)
        if different:
            raise InputMismatch(f"request_sha256: the runs sent different prompts for {different[:10]}")
        request_hashes = {"compared": len(champion["scored_question_ids"]), "differences": 0}
    rescored: dict[str, Any] = {}
    for name, block, rows in (("champion", champion, champion_rows), ("challenger", challenger, challenger_rows)):
        check_frozen_files(block, args.frozen_hits, args.key_facts)
        different = rescore_differences(rows, block, args.frozen_hits, args.key_facts)
        if different:
            raise InputMismatch(
                f"{name}: stored scores differ from the current scoring code for {different[:10]}; re-run "
                "scripts/eval_generation.py for both configs (cached answers are re-scored without model calls)"
            )
        rescored[name] = len(scores_of(rows))
    return {
        "champion": champion,
        "challenger": challenger,
        "champion_rows": champion_rows,
        "challenger_rows": challenger_rows,
        "request_hashes": request_hashes,
        "rescored": {**rescored, "differences": 0},
    }


def build_report(
    args: argparse.Namespace,
    document: dict[str, Any],
    settings: MigrationSettings,
    inputs: dict[str, Any],
    freeze: dict[str, Any],
) -> dict[str, Any]:
    """Compare the two runs and assemble the gate's JSON report."""
    champion, challenger = inputs["champion"], inputs["challenger"]
    metrics, overall = compare_runs(
        values_by_metric(scores_of(inputs["champion_rows"]), settings),
        values_by_metric(scores_of(inputs["challenger_rows"]), settings),
        settings,
    )
    frozen = dict(champion["frozen_inputs"])
    frozen.pop("question_ids", None)
    reproducibility = validate_reproducibility(
        document.get("reproducibility"), args.champion, champion, answers_by_question(inputs["champion_rows"])
    )
    report_path = gate_report_path(args)
    models = list(champion.get("provenance", {}).get("models", [])) + list(
        challenger.get("provenance", {}).get("models", [])
    )
    return {
        "champion": config_summary(args.champion, champion),
        "challenger": config_summary(args.challenger, challenger),
        "n_paired": len(champion["scored_question_ids"]),
        "margins_source": freeze["margins_source"],
        "margins_section_sha256": freeze["margins_section_sha256"],
        "margins_committed_at": freeze["margins_committed_at"],
        "margins_frozen_before_challenger": freeze["margins_frozen_before_challenger"],
        "margins_file_sha256": sha256_of_file(args.release_gate),
        "freeze": freeze,
        "rescored": inputs["rescored"],
        "request_hashes": inputs["request_hashes"],
        "bootstrap": {
            "resamples": settings.resamples,
            "seed": settings.seed,
            "confidence": settings.confidence,
            "unit": "question",
            "method": "paired percentile bootstrap of challenger minus champion",
        },
        "metrics": metrics,
        "overall": overall,
        "not_gated": {"champion": not_gated_summary(champion), "challenger": not_gated_summary(challenger)},
        "reproducibility": reproducibility,
        "frozen_inputs": frozen,
        "report_path": display_path(report_path, args.repo_root),
        "provenance": provenance_block(
            prompts={
                champion["prompt_id"]: champion["prompt_sha256"],
                challenger["prompt_id"]: challenger["prompt_sha256"],
            },
            eval_sets=frozen["question_sets"],
            models=models,
            corpus_checksum=frozen["corpus_checksum"],
            data_snapshots={
                "frozen_hits_sha256": frozen["frozen_hits_sha256"],
                "key_facts_sha256": frozen["key_facts_sha256"],
                "margins_source": freeze["margins_source"],
            },
            generated_by="scripts/llm_migration_gate.py",
            repo_root=args.repo_root,
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the gate. Returns a process exit code (see the module docstring)."""
    args = parse_args(argv)
    document = json.loads(args.metrics.read_text(encoding="utf-8")) if args.metrics.exists() else {}
    try:
        settings = load_migration_settings(args.release_gate)
        inputs = verified_inputs(args, document)
    except (InputMismatch, GateConfigError, FileNotFoundError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
    freeze = freeze_summary(
        inputs["champion"],
        inputs["challenger"],
        earliest_answer_time(inputs["champion_rows"]),
        earliest_answer_time(inputs["challenger_rows"]),
        migration_section_sha256(args.release_gate.read_bytes()),
    )
    if not freeze["all_frozen"] and not args.allow_unfrozen_inputs:
        failed = [check["detail"] for check in freeze["checks"] if not check["ok"]]
        print("REFUSED: not frozen before the answers were generated: " + "; ".join(failed), file=sys.stderr)
        return 3
    report = build_report(args, document, settings, inputs, freeze)
    report_path = gate_report_path(args)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    certificate_path = args.certificate_dir / f"{args.champion}_vs_{args.challenger}.md"
    certificate_path.parent.mkdir(parents=True, exist_ok=True)
    certificate_path.write_text(render_certificate(json.loads(report_path.read_text(encoding="utf-8"))), "utf-8")
    for name, block in report["metrics"].items():
        diff = block.get("paired_diff") or {}
        print(
            f"{name:36s} n={block['n_paired']:3d} diff={diff.get('mean')} ci95={diff.get('ci95')} "
            f"margin={block['margin']} -> {block['decision']}"
        )
    print(f"Overall: {report['overall']}")
    print(f"Wrote {report['report_path']} and {display_path(certificate_path, args.repo_root)}")
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

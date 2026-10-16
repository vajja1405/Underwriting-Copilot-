"""Frozen RAG generation evaluation for one named configuration (docs/innovations.md, S1; runs R1, R2 and R5).

Usage:
    uv run python scripts/eval_generation.py --config qwen3b_v1_k3                    # R1 (champion), ~133 calls
    uv run python scripts/eval_generation.py --config qwen15b_v1_k3                   # R2 (challenger)
    uv run python scripts/eval_generation.py --config qwen3b_v1_k3 --reproducibility 20   # R5, cache off
    uv run python scripts/eval_generation.py --config qwen3b_v1_k3 --limit 3          # smoke run: 3 calls

What one run does:
1. Reads the named configuration from config/generation_eval.yaml (model and pinned digest, prompt id, k,
   num_predict, num_ctx, temperature, seed).
2. Reads ONLY the frozen inputs: eval/generation/frozen_hits.jsonl (checked against the sha256 in its manifest),
   the question files it was made from (checked against the manifest too), and eval/rag_key_facts.jsonl. No
   retrieval runs, so two configurations see exactly the same documents.
3. If the frozen hits are the guideline-only BM25 fallback, the questions whose gold is in a regulatory document
   (the RG questions plus MS15 and MS16) are excluded, and the JSON lists them with counts.
4. Holds the Ollama queue lock (llm/ollama_queue.py) for the whole run, then answers each question with
   rag.answer.answer_from_hits over its first k frozen hits, through Cached(Budgeted(OllamaClient)).
5. Scores each answer with rag.generation_eval and writes one row per question to results/generation/<config>.jsonl,
   then the aggregate into results/generation_metrics.json under configs.<config> (other configs are kept), with a
   provenance block.

Errors: a question whose call is refused by the context guard (or rejected by the server) or fails over HTTP gets
an error row and is NOT scored; the counts are reported, and the migration gate refuses to compare runs whose scored
questions differ. After 3 HTTP errors in a row the run stops. An answer cut off at num_predict (stop reason
"length") is scored on the text produced so far and counted in errors.length_truncated.

Cache: on by default (.cache/llm), so a crashed run resumes where it stopped. Every row records when its answer was
generated and how long that call took: for a live call, now; for a cached answer, the values stored in the cache
entry when it was generated (created_at_utc, latency_s). Latency and throughput are reported over those generation
calls, so a resumed run still reports the latency of every answer. --no-cache forces live calls.

Freeze proofs (governance/freeze_proof.py): when the run starts, it records for the key facts, the question files,
the frozen hits and the `migration:` section of config/release_gate.yaml the first commit that holds their current
content. The migration gate compares those commits with the earliest answer generation time of each run, so inputs
or margins changed after seeing answers cannot be certified (a cached re-run keeps the original generation times).
Each row also records request_sha256, the hash of the exact system prompt and messages sent, so the gate can prove
two configurations saw identical prompts.

--limit N / --ids a,b,c select a subset: a smoke run. Smoke output goes to results/generation/smoke/ and never
replaces the measured results.

--reproducibility N (R5): reruns N questions drawn with seed 42 from the config's measured rows, with the cache
off, and counts answers that are bit-identical to the measured ones. Temperature 0 with a fixed seed on CPU is only
near-deterministic, which is what this measures.
"""

import argparse
import hashlib
import json
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import CONFIG_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.governance.freeze_proof import freeze_record
from uwcopilot.governance.provenance import RunProvenance, display_path, git_sha, load_average
from uwcopilot.llm.base import IncompleteLLMResponse, LLMClient, LLMMessage, LLMResponse
from uwcopilot.llm.cache import cache_key
from uwcopilot.llm.ollama_client import ContextBudgetExceeded, OllamaError
from uwcopilot.rag.answer import answer_from_hits
from uwcopilot.rag.eval_set import EvalQuestion
from uwcopilot.rag.frozen_hits import (
    FROZEN_HITS_FILE,
    FROZEN_MANIFEST_FILE,
    KEY_FACTS_FILE,
    MODE_BM25_GUIDELINES_ONLY,
    QUESTION_FILES,
    FrozenHitsRecord,
    load_eval_questions,
    needs_regulatory,
    read_frozen_hits,
    read_manifest,
)
from uwcopilot.rag.generation_eval import (
    KeyFactRow,
    QuestionScore,
    load_key_facts,
    score_record,
    summarize,
    summarize_by_category,
)
from uwcopilot.rag.metrics import percentile
from uwcopilot.rag.prompts import prompt_sha256, prompt_template_sha256, request_sha256

CONFIG_FILE = CONFIG_DIR / "generation_eval.yaml"
RELEASE_GATE_FILE = CONFIG_DIR / "release_gate.yaml"
GENERATION_RESULTS_DIR = RESULTS_DIR / "generation"
SMOKE_DIR = GENERATION_RESULTS_DIR / "smoke"
METRICS_FILE = RESULTS_DIR / "generation_metrics.json"
SMOKE_METRICS_FILE = SMOKE_DIR / "generation_metrics_smoke.json"
SUPPORTED_PROMPT_ID = "rag_answer_v1"  # the prompt rag.answer.answer_from_hits builds (rag/prompt.py)
MAX_CONSECUTIVE_HTTP_ERRORS = 3
REPRODUCIBILITY_SEED = 42
COST_NOTE = "local inference via Ollama; no API charge (CPU time is reported as latency)"
CLAIM_NOTE = (
    "Measured on SYNTHETIC guidelines and questions written by the project author, with a small local model. "
    "Key-fact recall is a proxy for faithfulness, not a measure of it. CIs are wide at this sample size."
)


class EvaluationInputError(RuntimeError):
    """The frozen inputs or the configuration do not allow an honest run (hash mismatch, unsupported option)."""


@dataclass(frozen=True)
class GenerationConfig:
    """One named configuration from config/generation_eval.yaml."""

    config_id: str
    provider: str
    model: str
    expected_digest: str | None
    prompt_id: str
    k: int
    num_predict: int
    num_ctx: int
    temperature: float
    seed: int
    role: str = ""
    license_summary: str | None = None
    license_docker_artifact: str | None = None
    license_upstream_card: str | None = None

    def options(self) -> dict[str, Any]:
        """The decoding options, as recorded in the results."""
        return {
            "num_predict": self.num_predict,
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
            "seed": self.seed,
        }


def load_generation_config(config_id: str, path: Path = CONFIG_FILE) -> GenerationConfig:
    """Read one named configuration and its model's license facts from config/generation_eval.yaml."""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    configs = document.get("configs") or {}
    if config_id not in configs:
        raise EvaluationInputError(f"unknown config {config_id!r}; known: {sorted(configs)}")
    entry = configs[config_id]
    model_facts = (document.get("models") or {}).get(entry["model"], {})
    return GenerationConfig(
        config_id=config_id,
        provider=entry["provider"],
        model=entry["model"],
        expected_digest=entry.get("expected_digest"),
        prompt_id=entry["prompt_id"],
        k=int(entry["k"]),
        num_predict=int(entry["num_predict"]),
        num_ctx=int(entry["num_ctx"]),
        temperature=float(entry["temperature"]),
        seed=int(entry["seed"]),
        role=str(entry.get("role", "")),
        license_summary=model_facts.get("license"),
        license_docker_artifact=model_facts.get("license_docker_artifact"),
        license_upstream_card=model_facts.get("license_upstream_card"),
    )


def check_config_supported(config: GenerationConfig) -> None:
    """Refuse options this evaluation cannot honour (rather than silently running something else)."""
    if config.prompt_id != SUPPORTED_PROMPT_ID:
        raise EvaluationInputError(
            f"prompt_id {config.prompt_id!r} is not supported: rag.answer.answer_from_hits builds {SUPPORTED_PROMPT_ID}"
        )
    if config.temperature != 0:
        raise EvaluationInputError("temperature must be 0: rag.answer.answer_from_hits always sends temperature 0")
    if config.k < 1:
        raise EvaluationInputError("k must be at least 1")


@dataclass
class FrozenInputs:
    """Everything the evaluation reads, already checked against the manifest."""

    records: list[FrozenHitsRecord]
    manifest: dict[str, Any]
    key_rows: dict[str, KeyFactRow]
    questions: dict[str, EvalQuestion] = field(default_factory=dict)
    hashes: dict[str, Any] = field(default_factory=dict)


def load_frozen_inputs(
    frozen_path: Path = FROZEN_HITS_FILE,
    manifest_path: Path = FROZEN_MANIFEST_FILE,
    key_facts_path: Path = KEY_FACTS_FILE,
    question_files: Sequence[Path] = QUESTION_FILES,
) -> FrozenInputs:
    """Load and verify the frozen hits, their manifest, the question files and the key facts.

    Raises EvaluationInputError when the hits file or a question file does not match the manifest, when a frozen
    question's text differs from its question file, or when a key-fact id is not a frozen answerable question.
    """
    manifest = read_manifest(manifest_path)
    hits_sha = sha256_of_file(frozen_path)
    if hits_sha != manifest.get("frozen_hits_sha256"):
        raise EvaluationInputError(
            f"{display_path(frozen_path)} sha256 {hits_sha} differs from its manifest "
            f"({manifest.get('frozen_hits_sha256')}); re-run scripts/freeze_retrieval.py"
        )
    question_sets: dict[str, str] = {}
    for path in question_files:
        question_sets[display_path(path)] = sha256_of_file(path)
    recorded = {item["path"]: item["sha256"] for item in manifest.get("question_files", [])}
    if recorded != question_sets:
        raise EvaluationInputError(
            f"the question files changed since the hits were frozen (manifest {recorded}, now {question_sets}); "
            "re-run scripts/freeze_retrieval.py"
        )
    records = read_frozen_hits(frozen_path)
    questions = {question.id: question for question, _ in load_eval_questions(question_files)}
    if set(questions) != {record.question_id for record in records}:
        raise EvaluationInputError("the frozen hits do not cover exactly the questions in the question files")
    for record in records:
        if questions[record.question_id].question != record.question:
            raise EvaluationInputError(f"{record.question_id}: frozen question text differs from the question file")
    key_rows = load_key_facts(key_facts_path)
    answerable = {record.question_id for record in records if record.answerable}
    unknown = sorted(set(key_rows) - answerable)
    if unknown:
        raise EvaluationInputError(f"key facts for ids that are not frozen answerable questions: {unknown}")
    hashes = {
        "retrieval_mode": manifest.get("retrieval_mode"),
        "frozen_hits_sha256": hits_sha,
        "frozen_manifest_sha256": sha256_of_file(manifest_path),
        "key_facts_sha256": sha256_of_file(key_facts_path),
        "question_sets": question_sets,
        "corpus_checksum": manifest.get("corpus_checksum"),
    }
    return FrozenInputs(records=records, manifest=manifest, key_rows=key_rows, questions=questions, hashes=hashes)


def select_records(
    inputs: FrozenInputs, ids: Sequence[str] | None, limit: int | None
) -> tuple[list[FrozenHitsRecord], dict[str, Any]]:
    """The records to evaluate, plus a description of what was excluded and why."""
    excluded_ids: list[str] = []
    selected: list[FrozenHitsRecord] = []
    guideline_only = inputs.manifest.get("retrieval_mode") == MODE_BM25_GUIDELINES_ONLY
    for record in inputs.records:
        question = inputs.questions.get(record.question_id)
        if guideline_only and question is not None and needs_regulatory(question):
            excluded_ids.append(record.question_id)
            continue
        selected.append(record)
    if ids:
        wanted = set(ids)
        missing = sorted(wanted - {record.question_id for record in selected})
        if missing:
            raise EvaluationInputError(f"unknown or excluded question ids: {missing}")
        selected = [record for record in selected if record.question_id in wanted]
    if limit is not None:
        selected = selected[:limit]
    excluded = {
        "regulatory_questions_excluded_guideline_only_hits": {
            "applies": guideline_only,
            "n": len(excluded_ids),
            "ids": excluded_ids,
            "reason": (
                "frozen hits are the guideline-only BM25 fallback, so regulatory gold can never be in context"
                if guideline_only
                else "not applicable: the frozen hits include the regulatory documents"
            ),
        }
    }
    return selected, excluded


def utc_now() -> str:
    """The current UTC time, ISO 8601 to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def find_cache_client(client: Any) -> Any | None:
    """The CachedLLMClient in a chain of wrapped clients (the one with a `cache` and `hits`), or None."""
    current = client
    while current is not None:
        if hasattr(current, "cache") and hasattr(current, "hits"):
            return current
        current = getattr(current, "inner", None)
    return None


def read_cache_entry(
    cache_client: Any, system: str, messages: list[LLMMessage], max_tokens: int, temperature: float, text: str
) -> dict[str, Any] | None:
    """The stored cache entry that a cache hit was served from, or None when it cannot be found.

    The key is computed the way llm.cache.CachedLLMClient computes it. If that ever changes, nothing is found and the
    row records no generation time, which makes the migration gate refuse rather than guess.
    """
    params: dict[str, Any] = {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "client": dict(getattr(cache_client.inner, "request_fingerprint", {})),
    }
    key = cache_key(cache_client.provider, cache_client.model, system, messages, params)
    path = cache_client.cache.path_for(key)
    if not path.exists():
        return None
    entry = json.loads(path.read_text(encoding="utf-8"))
    if entry.get("response", {}).get("text") != text:
        return None
    return entry


class RecordingClient:
    """Passes calls to the wrapped client and keeps what the evaluation must record about the last call.

    - last_response: the LLMResponse (answer_from_hits does not return it). An answer cut off at the token limit
      raises IncompleteLLMResponse in the clients; here its partial response is kept and last_incomplete is set, so
      the evaluation can score and count it.
    - last_request_sha256: the hash of the exact system prompt and messages sent.
    - last_generated_at / last_generated_latency_s: when the answer was generated and how long that call took. For a
      cache hit they come from the cache entry, not from the (instant) lookup.
    - last_load_average: the 1/5/15-minute load average right after a live call (None for a cache hit: the process
      that generated it did not store one in the cache).
    """

    def __init__(self, inner: LLMClient) -> None:
        """Wrap a ready-to-use client."""
        self.inner = inner
        self.provider = inner.provider
        self.model = inner.model
        self.cache_client = find_cache_client(inner)
        self.last_response: LLMResponse | None = None
        self.last_incomplete = False
        self.last_request_sha256: str | None = None
        self.last_generated_at: str | None = None
        self.last_generated_latency_s: float | None = None
        self.last_load_average: list[float] | None = None

    def complete(self, system: str, messages: list[LLMMessage], max_tokens: int, temperature: float = 0) -> LLMResponse:
        """Call the wrapped client and remember its response (partial responses included)."""
        self.last_response = None
        self.last_incomplete = False
        self.last_generated_at = None
        self.last_generated_latency_s = None
        self.last_load_average = None
        self.last_request_sha256 = request_sha256(system, messages)
        try:
            response = self.inner.complete(system, messages, max_tokens, temperature)
        except IncompleteLLMResponse as error:
            self.last_incomplete = True
            response = error.response
        self.last_response = response
        if not response.cached:
            self.last_generated_at = utc_now()
            self.last_generated_latency_s = response.latency_s
            self.last_load_average = load_average()
        elif self.cache_client is not None:
            entry = read_cache_entry(self.cache_client, system, messages, max_tokens, temperature, response.text)
            if entry is not None:
                self.last_generated_at = entry.get("created_at_utc")
                self.last_generated_latency_s = entry["response"].get("latency_s")
        return response


def text_sha256(text: str) -> str:
    """SHA-256 of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def answer_one(
    record: FrozenHitsRecord,
    key_row: KeyFactRow | None,
    client: RecordingClient,
    k: int,
    num_predict: int,
) -> dict[str, Any]:
    """Answer and score one question; returns its results row (with an error block instead of a score on failure)."""
    hits = record.context_hits(k)
    gold_in_context = record.gold_in_context(k)
    row: dict[str, Any] = {
        "question_id": record.question_id,
        "category": record.category,
        "question_file": record.question_file,
        "k": k,
        "context_ids": [hit.section_id for hit in hits],
        "gold_in_context": gold_in_context,
        "error": None,
    }
    try:
        answer = answer_from_hits(record.question, hits, client, max_tokens=num_predict)
    except ContextBudgetExceeded as error:
        row["error"] = {
            "type": "context",
            "detail": str(error)[:300],
            "est_upper_tokens": error.est_upper_tokens,
            "n_prompt_tokens": getattr(error, "n_prompt_tokens", None),
        }
        return row
    except OllamaError as error:
        row["error"] = {"type": "http", "detail": str(error)[:300]}
        return row
    response = client.last_response if answer.llm_called else None
    metadata = response.metadata if response is not None else {}
    score = score_record(record, answer.text, k, key_row)
    generated_latency = client.last_generated_latency_s if response is not None else None
    row.update(
        {
            "answer": answer.text,
            "answer_sha256": text_sha256(answer.text),
            "llm_called": answer.llm_called,
            "provider": answer.provider,
            "model": answer.model,
            "is_fake": answer.is_fake,
            "instruction_flags": answer.instruction_flags,
            "stop_reason": response.stop_reason if response is not None else None,
            "truncated_at_num_predict": client.last_incomplete,
            "possible_truncation": bool(metadata.get("possible_truncation", False)),
            "cached": response.cached if response is not None else False,
            "latency_s": round(response.latency_s, 3) if response is not None else None,
            "request_sha256": client.last_request_sha256 if response is not None else None,
            "answer_generated_at_utc": client.last_generated_at if response is not None else None,
            "generated_latency_s": round(generated_latency, 3) if generated_latency is not None else None,
            "load_average_at_generation": client.last_load_average if response is not None else None,
            "input_tokens": response.input_tokens if response is not None else 0,
            "output_tokens": response.output_tokens if response is not None else 0,
            "eval_count": metadata.get("eval_count"),
            "eval_duration_ns": metadata.get("eval_duration_ns"),
            "score": score.to_dict(),
        }
    )
    return row


def evaluate_records(
    records: Sequence[FrozenHitsRecord],
    key_rows: dict[str, KeyFactRow],
    client: LLMClient,
    k: int,
    num_predict: int,
    progress: bool = True,
) -> tuple[list[dict[str, Any]], bool]:
    """Answer every record in order. Returns (rows, aborted); aborted after MAX_CONSECUTIVE_HTTP_ERRORS in a row."""
    recorder = RecordingClient(client)
    rows: list[dict[str, Any]] = []
    consecutive_http_errors = 0
    for index, record in enumerate(records, start=1):
        row = answer_one(record, key_rows.get(record.question_id), recorder, k, num_predict)
        rows.append(row)
        error = row["error"]
        consecutive_http_errors = consecutive_http_errors + 1 if error and error["type"] == "http" else 0
        if progress:
            status = error["type"] if error else ("cached" if row.get("cached") else f"{row.get('latency_s')} s")
            print(f"[{index}/{len(records)}] {record.question_id}: {status}", flush=True)
        if consecutive_http_errors >= MAX_CONSECUTIVE_HTTP_ERRORS:
            return rows, True
    return rows, False


def scored(rows: Sequence[dict[str, Any]]) -> list[QuestionScore]:
    """The QuestionScores of the rows that have one."""
    return [QuestionScore.from_dict(row["score"]) for row in rows if row.get("score")]


def runtime_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Latency, tokens and generation speed of the calls that generated the answers.

    For a cached answer the latency and the token timings are the ones stored when it was generated, so a resumed
    run reports every answer. n_in_this_process says how many of them were generated by this process.
    """
    called = [row for row in rows if row.get("llm_called")]
    summary: dict[str, Any] = {}
    generated = [row for row in called if row.get("generated_latency_s") is not None]
    latencies = [float(row["generated_latency_s"]) for row in generated]
    if latencies:
        summary["latency_s"] = {
            "p50": round(percentile(latencies, 50), 3),
            "p95": round(percentile(latencies, 95), 3),
            "n_live_calls": len(latencies),
            "n_in_this_process": sum(1 for row in generated if not row.get("cached")),
            "n_from_cache_entries": sum(1 for row in generated if row.get("cached")),
            "note": (
                "wall-clock time of the call that generated each answer; for cached answers it is read from the "
                "cache entry, so the load average of this process may not describe those calls"
            ),
        }
    else:
        summary["latency_s"] = "not measured (no answer has a recorded generation latency)"
    if called:
        summary["tokens"] = {
            "in_mean": round(sum(row["input_tokens"] for row in called) / len(called), 1),
            "out_mean": round(sum(row["output_tokens"] for row in called) / len(called), 1),
            "n": len(called),
            "note": "as reported by Ollama (prompt_eval_count may be lower when a prompt prefix is reused)",
        }
    else:
        summary["tokens"] = "not measured (no LLM call)"
    speeds: list[float] = []
    for row in called:
        count, duration = row.get("eval_count"), row.get("eval_duration_ns")
        if count and duration:
            speeds.append(count / (duration / 1e9))
    if speeds:
        summary["gen_tokens_per_s"] = {"p50": round(percentile(speeds, 50), 2), "n": len(speeds)}
    else:
        summary["gen_tokens_per_s"] = "not measured (no live call with eval_count and eval_duration)"
    one_minute = [row["load_average_at_generation"][0] for row in called if row.get("load_average_at_generation")]
    if one_minute:
        summary["load_average_at_generation"] = {
            "one_minute_min": min(one_minute),
            "one_minute_p50": round(percentile(one_minute, 50), 2),
            "one_minute_max": max(one_minute),
            "n": len(one_minute),
            "n_without_value": len(called) - len(one_minute),
            "note": "read right after each live call; cached answers from an earlier process have no value",
        }
    else:
        summary["load_average_at_generation"] = (
            "not recorded (every answer came from the cache of an earlier process, which did not store it)"
        )
    return summary


def error_summary(rows: Sequence[dict[str, Any]], aborted: bool) -> dict[str, Any]:
    """Counts of unscored and flagged questions."""
    context_errors = [row["question_id"] for row in rows if row["error"] and row["error"]["type"] == "context"]
    http_errors = [row["question_id"] for row in rows if row["error"] and row["error"]["type"] == "http"]
    truncated = [row["question_id"] for row in rows if row.get("truncated_at_num_predict")]
    possible = [row["question_id"] for row in rows if row.get("possible_truncation")]
    return {
        "context": {"n": len(context_errors), "ids": context_errors},
        "http": {"n": len(http_errors), "ids": http_errors},
        "length_truncated": {
            "n": len(truncated),
            "ids": truncated,
            "note": "answer stopped at num_predict; scored on the text produced so far",
        },
        "possible_truncation": {"n": len(possible), "ids": possible},
        "aborted_after_consecutive_http_errors": aborted,
    }


def find_model_record_client(client: Any) -> Any | None:
    """The innermost wrapped client that can describe its model (OllamaClient.model_record), or None."""
    current = client
    while current is not None:
        if hasattr(current, "model_record"):
            return current
        current = getattr(current, "inner", None)
    return None


def collect_freeze_proof(
    key_facts_path: Path,
    frozen_hits_path: Path,
    question_files: Sequence[Path] = QUESTION_FILES,
    release_gate_path: Path = RELEASE_GATE_FILE,
    repo_root: Path = REPO_ROOT,
) -> list[dict[str, Any]]:
    """Freeze records (governance/freeze_proof.py) of every frozen input, taken when the run starts."""
    records = [
        freeze_record(key_facts_path, "key_facts", "file", repo_root),
        freeze_record(frozen_hits_path, "frozen_hits", "file", repo_root),
    ]
    for path in question_files:
        records.append(freeze_record(path, "question_file", "file", repo_root))
    records.append(freeze_record(release_gate_path, "margins", "migration_section", repo_root))
    return records


def answer_times(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """When the scored answers were generated: earliest, latest, and how many have no recorded time."""
    times = [row["answer_generated_at_utc"] for row in rows if row.get("score") and row.get("answer_generated_at_utc")]
    missing = [row["question_id"] for row in rows if row.get("score") and not row.get("answer_generated_at_utc")]
    ordered = sorted(times, key=datetime.fromisoformat)
    return {
        "earliest_answer_generated_at_utc": ordered[0] if ordered else None,
        "latest_answer_generated_at_utc": ordered[-1] if ordered else None,
        "answers_without_generation_time": missing,
    }


def build_config_block(
    config: GenerationConfig,
    rows: Sequence[dict[str, Any]],
    selected: Sequence[FrozenHitsRecord],
    excluded: dict[str, Any],
    inputs: FrozenInputs,
    model_info: dict[str, Any],
    status: str,
    aborted: bool,
    rows_path: Path,
    load_average_start: list[float] | None,
    cache_info: dict[str, Any],
    resamples: int,
    seed: int,
    run_started_at: str | None = None,
    freeze_proof: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The configs.<id> entry of the metrics JSON."""
    scores = scored(rows)
    frozen_inputs = dict(inputs.hashes)
    frozen_inputs["question_ids"] = sorted(record.question_id for record in selected)
    frozen_inputs["n_questions"] = len(selected)
    block: dict[str, Any] = {
        "status": status,
        "provider": config.provider,
        "model": config.model,
        "role": config.role,
        "ollama_digest": model_info.get("digest"),
        "expected_digest": config.expected_digest,
        "ollama_version": model_info.get("runtime_version"),
        "model_license": config.license_summary,
        "model_license_sources": {
            "docker_artifact": config.license_docker_artifact,
            "upstream_card": config.license_upstream_card,
        },
        "prompt_id": config.prompt_id,
        "prompt_sha256": prompt_sha256(config.prompt_id),
        "prompt_template_sha256": prompt_template_sha256(config.prompt_id),
        "k": config.k,
        "options": config.options(),
        "n_questions": len(scores),
        "n_selected": len(selected),
        "excluded": excluded,
        "scored_question_ids": sorted(score.question_id for score in scores),
        "metrics": summarize(scores, n_resamples=resamples, seed=seed) if scores else "not measured (no scored answer)",
        "by_category": summarize_by_category(scores),
        "cost": {"api_usd": 0.0, "note": COST_NOTE},
        "load_average": {"start": load_average_start, "end": load_average()},
        "errors": error_summary(rows, aborted),
        "cache": cache_info,
        "bootstrap": {"resamples": resamples, "seed": seed, "confidence": 0.95, "unit": "question"},
        "frozen_inputs": frozen_inputs,
        "rows_path": display_path(rows_path),
        "rows_sha256": sha256_of_file(rows_path),
        "run_started_at_utc": run_started_at,
        **answer_times(rows),
        "freeze_proof": freeze_proof if freeze_proof is not None else "not recorded",
        "generated_at_utc": utc_now(),
        "git_sha": git_sha(),
    }
    block.update(runtime_summary(rows))
    return block


def write_rows(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    """One JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_rows(path: Path) -> list[dict[str, Any]]:
    """The rows of a results JSONL file."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def overall_status(statuses: Sequence[str]) -> str:
    """'measured' only when every config is measured, 'smoke' when every config is a smoke run, else 'partial'."""
    if statuses and all(status == "measured" for status in statuses):
        return "measured"
    if statuses and all(status == "smoke" for status in statuses):
        return "smoke"
    return "partial"


def answers_by_question(rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    """{question id: answer sha256} for the scored rows."""
    return {row["question_id"]: row["answer_sha256"] for row in rows if row.get("score") and row.get("answer_sha256")}


def reproducibility_matches(reproducibility: Any, config_id: str, answers: dict[str, str]) -> bool:
    """True when an R5 block compared exactly these answers of this config (so it still describes this run)."""
    if not isinstance(reproducibility, dict) or reproducibility.get("config") != config_id:
        return False
    originals = reproducibility.get("original_answer_sha256") or {}
    if not originals:
        return False
    for question_id, answer_sha in originals.items():
        if answers.get(question_id) != answer_sha:
            return False
    return True


def update_metrics_file(
    path: Path,
    config_id: str,
    block: dict[str, Any] | None,
    extra: dict[str, Any],
    answers: dict[str, str] | None = None,
) -> None:
    """Merge one config block (and top-level keys such as reproducibility) into the metrics JSON.

    answers ({question id: answer sha256} of the new block's rows): an existing R5 result for this config that
    compared different answers no longer describes the run, so it is replaced by a "not measured" note.
    """
    document: dict[str, Any] = {}
    if path.exists():
        document = json.loads(path.read_text(encoding="utf-8"))
    document.setdefault("configs", {})
    if block is not None:
        document["configs"][config_id] = block
        document["frozen_inputs"] = block["frozen_inputs"]
        previous = document.get("reproducibility")
        if isinstance(previous, dict) and previous.get("config") == config_id:
            if not reproducibility_matches(previous, config_id, answers or {}):
                document["reproducibility"] = (
                    f"not measured for the current {config_id} run (the previous R5 compared other answers; "
                    f"re-run --reproducibility 20)"
                )
    document.update(extra)
    document["generated_at"] = utc_now()
    document["generated_by"] = "scripts/eval_generation.py"
    statuses = {config: entry.get("status") for config, entry in document["configs"].items()}
    document["status"] = overall_status(list(statuses.values()))
    document["config_statuses"] = statuses
    document.setdefault("reproducibility", "not measured (run --reproducibility 20 after the champion run)")
    document["note"] = CLAIM_NOTE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="config id from config/generation_eval.yaml")
    parser.add_argument("--limit", type=int, default=None, help="smoke run: only the first N selected questions")
    parser.add_argument("--ids", default=None, help="smoke run: comma-separated question ids")
    parser.add_argument("--no-cache", action="store_true", help="always call the model (no LLM cache)")
    parser.add_argument("--reproducibility", type=int, default=None, metavar="N", help="R5: rerun N questions")
    parser.add_argument("--resamples", type=int, default=1000, help="bootstrap resamples (default 1000)")
    parser.add_argument("--seed", type=int, default=42, help="bootstrap seed (default 42)")
    parser.add_argument("--frozen-hits", type=Path, default=FROZEN_HITS_FILE)
    parser.add_argument("--manifest", type=Path, default=FROZEN_MANIFEST_FILE)
    parser.add_argument("--key-facts", type=Path, default=KEY_FACTS_FILE)
    parser.add_argument("--results-dir", type=Path, default=GENERATION_RESULTS_DIR, help="per-question rows folder")
    parser.add_argument("--metrics-output", type=Path, default=METRICS_FILE)
    return parser.parse_args(argv)


def build_ollama_client(config: GenerationConfig, use_cache: bool) -> LLMClient:
    """Cached(Budgeted(OllamaClient)) for the configuration (contacts the server: version and digest check)."""
    from uwcopilot.llm.factory import get_llm_client  # imported here so tests of the pure parts need no server
    from uwcopilot.llm.ollama_client import OllamaOptions

    options = OllamaOptions(num_ctx=config.num_ctx, seed=config.seed, expected_digest=config.expected_digest)
    return get_llm_client(config.provider, model=config.model, use_cache=use_cache, ollama_options=options)


def model_info_of(client: LLMClient, config: GenerationConfig) -> dict[str, Any]:
    """The provenance model record of the client (digest, runtime version), or a minimal record."""
    provider_client = find_model_record_client(client)
    if provider_client is None:
        return {"provider": client.provider, "model": client.model, "digest": None, "license": None}
    return provider_client.model_record(config.license_summary)


def cache_info_of(client: LLMClient, use_cache: bool) -> dict[str, Any]:
    """Whether the LLM cache was used, and its hit/miss counts for this run."""
    return {"used": use_cache, "hits": getattr(client, "hits", None), "misses": getattr(client, "misses", None)}


def run_evaluation(args: argparse.Namespace, config: GenerationConfig, inputs: FrozenInputs) -> int:
    """The main evaluation (full or smoke). Returns a process exit code."""
    from uwcopilot.llm.ollama_queue import ollama_queue_lock

    ids = [item.strip() for item in args.ids.split(",") if item.strip()] if args.ids else None
    selected, excluded = select_records(inputs, ids, args.limit)
    smoke = args.limit is not None or ids is not None
    rows_dir = SMOKE_DIR if smoke and args.results_dir == GENERATION_RESULTS_DIR else args.results_dir
    metrics_path = SMOKE_METRICS_FILE if smoke and args.metrics_output == METRICS_FILE else args.metrics_output
    rows_path = rows_dir / f"{config.config_id}.jsonl"
    label = f"scripts/eval_generation.py --config {config.config_id}" + (" (smoke)" if smoke else "")
    with ollama_queue_lock(label):
        run = RunProvenance.start("scripts/eval_generation.py")
        run_started_at = utc_now()
        freeze_proof = collect_freeze_proof(args.key_facts, args.frozen_hits)
        client = build_ollama_client(config, use_cache=not args.no_cache)
        rows, aborted = evaluate_records(selected, inputs.key_rows, client, config.k, config.num_predict)
        model_info = model_info_of(client, config)
        cache_info = cache_info_of(client, not args.no_cache)
    write_rows(rows_path, rows)
    has_errors = any(row["error"] for row in rows)
    status = "smoke" if smoke else ("partial" if aborted or has_errors else "measured")
    block = build_config_block(
        config,
        rows,
        selected,
        excluded,
        inputs,
        model_info,
        status,
        aborted,
        rows_path,
        run.load_average_start,
        cache_info,
        args.resamples,
        args.seed,
        run_started_at=run_started_at,
        freeze_proof=freeze_proof,
    )
    block["provenance"] = run.finish(
        prompts={config.prompt_id: prompt_sha256(config.prompt_id)},
        eval_sets=[*inputs.hashes["question_sets"].keys(), args.key_facts, args.frozen_hits],
        models=[model_info],
        corpus_checksum=inputs.hashes["corpus_checksum"],
        data_snapshots={"frozen_hits_manifest": display_path(args.manifest)},
    )
    update_metrics_file(
        metrics_path, config.config_id, block, {"provenance": block["provenance"]}, answers_by_question(rows)
    )
    print(f"Wrote {display_path(rows_path)} and {display_path(metrics_path)} (status {status})")
    print_summary(block)
    return 0 if not aborted else 1


def print_summary(block: dict[str, Any]) -> None:
    """A few headline numbers, copied from the block that was written."""
    metrics = block["metrics"]
    if isinstance(metrics, str):
        print(metrics)
        return
    print(
        f"  scored {block['n_questions']} of {block['n_selected']}; errors {block['errors']['http']['n']} http, "
        f"{block['errors']['context']['n']} context, {block['errors']['length_truncated']['n']} truncated"
    )
    print(f"  key_fact_recall all {metrics['key_fact_recall']['all']}")
    print(f"  abstention_accuracy_unanswerable {metrics['abstention_accuracy_unanswerable']}")
    print(f"  answers_with_fabricated_citation {metrics['answers_with_fabricated_citation']}")
    print(f"  format_compliance {metrics['format_compliance']}")
    print(f"  latency_s {block.get('latency_s')}")


def check_measured_run(metrics_path: Path, config_id: str, rows_path: Path) -> None:
    """Refuse R5 unless the config's block is 'measured' and its rows file is the one that block describes."""
    if not rows_path.exists():
        raise EvaluationInputError(f"{display_path(rows_path)} is missing: run the full evaluation first")
    document = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    block = (document.get("configs") or {}).get(config_id)
    if not block or block.get("status") != "measured":
        status = block.get("status") if block else "missing"
        raise EvaluationInputError(f"{config_id}: the metrics block is {status!r}, not 'measured'; R5 needs a full run")
    if block.get("rows_sha256") != sha256_of_file(rows_path):
        raise EvaluationInputError(f"{display_path(rows_path)} is not the rows file the {config_id} block describes")


def run_reproducibility(args: argparse.Namespace, config: GenerationConfig, inputs: FrozenInputs) -> int:
    """R5: rerun N seeded-random measured questions with the cache off and count bit-identical answers."""
    from uwcopilot.llm.ollama_queue import ollama_queue_lock

    rows_path = args.results_dir / f"{config.config_id}.jsonl"
    check_measured_run(args.metrics_output, config.config_id, rows_path)
    measured = [row for row in read_rows(rows_path) if row.get("score")]
    if len(measured) < args.reproducibility:
        raise EvaluationInputError(f"only {len(measured)} measured rows; cannot sample {args.reproducibility}")
    rng = random.Random(REPRODUCIBILITY_SEED)
    chosen_ids = sorted(rng.sample(sorted(row["question_id"] for row in measured), args.reproducibility))
    originals = {row["question_id"]: row for row in measured}
    records = {record.question_id: record for record in inputs.records}
    out_rows: list[dict[str, Any]] = []
    with ollama_queue_lock(f"scripts/eval_generation.py --config {config.config_id} --reproducibility"):
        run = RunProvenance.start("scripts/eval_generation.py")
        client = build_ollama_client(config, use_cache=False)
        recorder = RecordingClient(client)
        for index, question_id in enumerate(chosen_ids, start=1):
            rerun = answer_one(
                records[question_id], inputs.key_rows.get(question_id), recorder, config.k, config.num_predict
            )
            original = originals[question_id]
            identical = rerun.get("answer") is not None and rerun.get("answer") == original.get("answer")
            out_rows.append(
                {
                    "question_id": question_id,
                    "identical": identical,
                    "original_sha256": original.get("answer_sha256"),
                    "rerun_sha256": rerun.get("answer_sha256"),
                    "rerun_latency_s": rerun.get("latency_s"),
                    "rerun_error": rerun.get("error"),
                }
            )
            print(f"[{index}/{len(chosen_ids)}] {question_id}: identical={identical}", flush=True)
        model_info = model_info_of(client, config)
    output = args.results_dir / f"{config.config_id}.reproducibility.jsonl"
    write_rows(output, out_rows)
    original_answers = {question_id: originals[question_id].get("answer_sha256") for question_id in chosen_ids}
    reproducibility = {
        "config": config.config_id,
        "n": len(out_rows),
        "bit_identical": sum(1 for row in out_rows if row["identical"]),
        "errors": sum(1 for row in out_rows if row["rerun_error"]),
        "question_ids": chosen_ids,
        "sample_seed": REPRODUCIBILITY_SEED,
        "cache": "off for the rerun",
        "compared_with": {"path": display_path(rows_path), "sha256": sha256_of_file(rows_path)},
        "original_answer_sha256": original_answers,
        "rows_path": display_path(output),
        "note": "temperature 0 and a fixed seed on CPU are only near-deterministic; this counts exact text matches",
        "load_average": {"start": run.load_average_start, "end": load_average()},
        "model": model_info,
        "generated_at_utc": utc_now(),
    }
    update_metrics_file(args.metrics_output, config.config_id, None, {"reproducibility": reproducibility})
    print(f"Reproducibility: {reproducibility['bit_identical']}/{reproducibility['n']} bit-identical")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the evaluation. Returns a process exit code."""
    args = parse_args(argv)
    try:
        config = load_generation_config(args.config)
        check_config_supported(config)
        inputs = load_frozen_inputs(args.frozen_hits, args.manifest, args.key_facts)
        if args.reproducibility is not None:
            return run_reproducibility(args, config, inputs)
        return run_evaluation(args, config, inputs)
    except (EvaluationInputError, OllamaError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

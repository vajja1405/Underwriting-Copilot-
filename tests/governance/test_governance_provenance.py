"""Tests for uwcopilot.governance.provenance: the block's exact shape, hashing, validation and load averages."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.governance import provenance
from uwcopilot.governance.provenance import (
    MODEL_KEYS,
    RunProvenance,
    current_corpus_checksum,
    git_dirty,
    provenance_block,
)
from uwcopilot.rag.corpus import load_corpus

QUESTIONS = REPO_ROOT / "eval" / "rag_questions.jsonl"
PROMPT_SHA = "1" * 64
TOP_LEVEL_KEYS = [
    "git_sha",
    "git_dirty",
    "generated_at_utc",
    "generated_by",
    "prompts",
    "corpus_checksum",
    "eval_sets",
    "models",
    "data_snapshots",
    "packages",
    "environment",
]
MODEL = {
    "provider": "ollama",
    "model": "qwen2.5-3b-instruct-q4km",
    "digest": "f" * 64,
    "license": "Docker Hub artifact: Apache-2.0; upstream Hugging Face card: Qwen Research License",
    "runtime_version": "ollama 0.40.3",
}


def small_block(**overrides: object) -> dict:
    """A block with a fixed corpus checksum, so tests do not depend on which corpus files are present."""
    values: dict = {"corpus_checksum": "2" * 64, "generated_by": "tests"}
    values.update(overrides)
    return provenance_block(**values)


def test_block_has_exactly_the_documented_keys() -> None:
    block = small_block(prompts={"rag_answer_v1": PROMPT_SHA}, eval_sets=[QUESTIONS], models=[MODEL])
    assert list(block) == TOP_LEVEL_KEYS
    assert list(block["environment"]) == ["cpu_count", "load_average_start", "load_average_end"]
    assert block["models"] == [MODEL]
    assert block["prompts"] == {"rag_answer_v1": PROMPT_SHA}
    assert block["generated_by"] == "tests"
    json.dumps(block)


def test_git_fields_describe_this_checkout() -> None:
    block = small_block()
    assert len(block["git_sha"]) == 40 or block["git_sha"] == "unknown"
    assert block["git_dirty"] in (True, False, "unknown")
    assert block["generated_at_utc"].endswith("+00:00")


def test_packages_include_python_and_key_libraries() -> None:
    packages = small_block()["packages"]
    assert packages["python"].startswith("3.11")
    assert packages["pydantic"] != "not installed"
    assert set(provenance.KEY_PACKAGES) <= set(packages)


def test_eval_sets_are_hashed_with_repo_relative_paths() -> None:
    block = small_block(eval_sets=[QUESTIONS, "eval/rag_questions.jsonl"])
    assert block["eval_sets"] == {"eval/rag_questions.jsonl": sha256_of_file(QUESTIONS)}


def test_already_hashed_eval_sets_are_validated() -> None:
    block = small_block(eval_sets={"eval/x.jsonl": "3" * 64})
    assert block["eval_sets"] == {"eval/x.jsonl": "3" * 64}
    with pytest.raises(ValueError, match="SHA-256"):
        small_block(eval_sets={"eval/x.jsonl": "not-a-hash"})


def test_files_outside_the_repo_are_recorded_by_name_only(tmp_path: Path) -> None:
    outside = tmp_path / "extra_questions.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    block = small_block(eval_sets=[outside])
    assert block["eval_sets"] == {"extra_questions.jsonl": sha256_of_file(outside)}


def test_a_missing_eval_set_is_an_error_not_a_guess(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        small_block(eval_sets=[tmp_path / "missing.jsonl"])


def test_prompt_values_must_be_sha256() -> None:
    with pytest.raises(ValueError, match="prompts"):
        small_block(prompts={"rag_answer_v1": "You are a careful assistant."})


def test_model_records_are_normalized_and_checked() -> None:
    block = small_block(models=[{"provider": "fake", "model": "fake-llm"}])
    assert block["models"] == [{key: None for key in MODEL_KEYS} | {"provider": "fake", "model": "fake-llm"}]
    with pytest.raises(ValueError, match="Unknown model provenance keys"):
        small_block(models=[{**MODEL, "temperature": 0}])
    with pytest.raises(ValueError, match="provider and a model"):
        small_block(models=[{"model": "x"}])


def test_data_snapshots_must_be_plain_json() -> None:
    snapshots = {"watchlists": {"data/raw/ofac/sdn.csv": "4" * 64}}
    assert small_block(data_snapshots=snapshots)["data_snapshots"] == snapshots
    with pytest.raises(TypeError):
        small_block(data_snapshots={"bad": {1, 2}})


def test_default_corpus_checksum_is_the_existing_corpus_function() -> None:
    assert current_corpus_checksum() == load_corpus().checksum()
    block = provenance_block(generated_by="tests")
    assert block["corpus_checksum"] == load_corpus().checksum()


def test_run_records_load_average_at_start_and_end(monkeypatch: pytest.MonkeyPatch) -> None:
    readings = iter([[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]])
    monkeypatch.setattr(provenance, "load_average", lambda: next(readings))
    run = RunProvenance.start("scripts/example.py")
    block = run.finish(corpus_checksum="2" * 64)
    assert block["generated_by"] == "scripts/example.py"
    assert block["environment"]["load_average_start"] == [1.0, 1.0, 1.0]
    assert block["environment"]["load_average_end"] == [2.0, 2.0, 2.0]


def test_direct_call_does_not_invent_a_start_load_average() -> None:
    assert small_block()["environment"]["load_average_start"] is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_dirty_ignores_results_but_not_code(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "metrics.json").write_text("{}", encoding="utf-8")
    (tmp_path / "code.py").write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    assert git_dirty(tmp_path) is False
    (tmp_path / "results" / "metrics.json").write_text('{"a": 1}', encoding="utf-8")
    assert git_dirty(tmp_path) is False
    (tmp_path / "code.py").write_text("x = 2\n", encoding="utf-8")
    assert git_dirty(tmp_path) is True


def test_a_run_names_the_commit_it_started_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A commit made during a long run must not be recorded as the code that produced the numbers."""
    from uwcopilot.governance import provenance as module

    shas = iter(["a" * 40, "b" * 40])
    monkeypatch.setattr(module, "git_sha", lambda repo_root=module.REPO_ROOT: next(shas))
    monkeypatch.setattr(module, "git_dirty", lambda repo_root=module.REPO_ROOT: False)
    run = RunProvenance.start("scripts/example.py")
    block = run.finish()
    assert block["git_sha"] == "a" * 40
    assert block["git_dirty"] is True

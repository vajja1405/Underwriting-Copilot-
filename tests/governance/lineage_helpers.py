"""A small fake repository for the lineage tests: results, manifests, a prompt file and one raw data file.

Everything is written under tmp_path, so the tests never depend on the real results/ or data/raw/ folders.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from uwcopilot.governance.lineage import EvalSetSource, PromptSource, build_decision_lineage

DATA_BYTES = b"SYNTHETIC loan rows\n"
DATA_SHA = hashlib.sha256(DATA_BYTES).hexdigest()
EVAL_SETS = {
    "agent_applications": EvalSetSource(Path("eval/applications/manifest.json"), "applications_sha256"),
    "docintel_packets_test": EvalSetSource(Path("eval/packets/manifest.json"), "test_truth_sha256"),
    "startup_applications": EvalSetSource(Path("eval/startup_applications/manifest.json"), "applications_sha256"),
}
CREDIT_CALL = {
    "tool": "score_risk",
    "arguments": {"product": "credit"},
    "status": "ok",
    "output": {"model_version": "m1"},
}
INSURANCE_CALL = {"tool": "score_risk", "arguments": {"product": "insurance_frequency"}, "status": "unavailable"}


def write_json(path: Path, value: Any) -> None:
    """Write a JSON file, creating its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1), encoding="utf-8")


def risk_results(credit_version: str = "1") -> dict[str, Any]:
    """A results/risk_metrics.json with a credit and an insurance_frequency section."""
    checksum = {"file": "loans.zip", "sha256": DATA_SHA, "size_bytes": len(DATA_BYTES), "source": "test"}
    return {
        "credit": {
            "results": {
                "mlflow": {"registered_model": "credit_pd", "registered_version": credit_version, "run_id": "r1"}
            },
            "metadata": {
                "git_commit": "a" * 40,
                "git_worktree_dirty": False,
                "generated_at_utc": "2026-10-12T19:35:16+00:00",
                "datasets": {"lending_club": {"files": [checksum]}},
            },
        },
        "insurance_frequency": {
            "results": {"mlflow": {"run_ids": {"frequency": "r2"}}, "model_version": {"label": "freq v4"}},
            "metadata": {"git_commit": "b" * 40, "datasets": {"fremtpl2": {"files": []}}},
        },
    }


def fake_repo(root: Path) -> Path:
    """Write the fake repository under root and return root."""
    write_json(root / "results" / "risk_metrics.json", risk_results())
    write_json(
        root / "results" / "agent_metrics.json",
        {
            "generated_by": "scripts/eval_agent.py",
            "generated_at": "2026-10-13T00:00:00+00:00",
            "versions": {"git": {"commit": "c" * 40, "uncommitted_changes": False}},
            "applications": {"set_sha256": "d" * 64, "count": 3},
        },
    )
    write_json(root / "eval" / "applications" / "manifest.json", {"applications_sha256": "d" * 64})
    write_json(root / "eval" / "packets" / "manifest.json", {"test_truth_sha256": "e" * 64})
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts" / "memo_prompt.py").write_text('SYSTEM_PROMPT = "You draft memos."\n', encoding="utf-8")
    raw = root / "data" / "raw"
    (raw / "lending_club").mkdir(parents=True, exist_ok=True)
    (raw / "lending_club" / "loans.zip").write_bytes(DATA_BYTES)
    line = {"dataset": "lending_club", "file": "loans.zip", "sha256": DATA_SHA, "action": "downloaded"}
    (raw / "checksums.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    return root


def prompt_source(root: Path) -> PromptSource:
    """The fake repository's prompt file."""
    return PromptSource("memo", root / "prompts" / "memo_prompt.py", label="memo-v1", note="test prompt")


def lineage_for(root: Path, tool_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A lineage block built against the fake repository."""
    calls = [CREDIT_CALL, INSURANCE_CALL] if tool_results is None else tool_results
    return build_decision_lineage(calls, prompts=(prompt_source(root),), eval_sets=EVAL_SETS, repo_root=root)

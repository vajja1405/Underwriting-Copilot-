"""Print the full lineage of one decision record, and flag what no longer matches the files here (`make lineage`).

    uv run python scripts/lineage.py data/governance/simulated_review_log.jsonl DR-0123abcd...
    uv run python scripts/lineage.py <decision log .jsonl> <record id> [--no-data-files] [--strict]

The chain it prints: the record (application, reviewer, final status) -> its versions (graph, policy, prompt, LLM)
-> the lineage block (governance/lineage.py): the models behind its score_risk calls, the results files and the
provenance that validated them (git commit, time, dirty flag), the dataset checksum lines those results copied, the
prompt file hashes and the eval-set versions. Every recorded hash or version is compared with the working tree now:

- OK: it still matches;
- STALE: it no longer matches (the file, the results or the model version changed since the decision);
- NOT CHECKABLE: the file is not present here (for example the Lending Club data, which needs a Kaggle key, or a
  raw data file that was not downloaded). Nothing is assumed to match.

"Now" is the working tree, i.e. HEAD plus any uncommitted edit; the script prints the commit and the dirty flag.
--no-data-files skips hashing the raw data files (the checksum log is still compared). --strict exits with 3 when
anything is STALE. A record written before Phase 6 has no lineage block; the script says so and prints its versions.
Decision records hold SYNTHETIC applications only.
"""

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.agent.memo import PROMPT_VERSION
from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.governance.feedback import feedback_from_record
from uwcopilot.governance.lineage import (
    LineageCheck,
    overall_status,
    read_json,
    resolve,
    verify_lineage,
)
from uwcopilot.governance.provenance import git_dirty, git_sha

EXIT_NOT_FOUND = 1
EXIT_STALE = 3
CURRENT_PROMPT_LABELS = {"agent_memo_prompt": PROMPT_VERSION}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Print the lineage of one decision record and flag STALE hashes.")
    parser.add_argument("log", type=Path, help="a decision log (.jsonl) written by the agent")
    parser.add_argument("record_id", help="the record_id of the decision (DR-...)")
    parser.add_argument("--no-data-files", action="store_true", help="do not hash the raw data files")
    parser.add_argument("--strict", action="store_true", help=f"exit with {EXIT_STALE} when anything is STALE")
    return parser.parse_args(argv)


def status_text(check: LineageCheck | None) -> str:
    """[OK], [STALE] or [NOT CHECKABLE: why] for a check; [not checked] when there is none."""
    if check is None:
        return "[not checked]"
    if check.status == "ok":
        return "[OK]"
    if check.status == "STALE":
        return f"[STALE: recorded {short(check.recorded)}, now {short(check.current)}]"
    return f"[NOT CHECKABLE: {check.detail}]"


def short(value: Any) -> str:
    """A SHA-256 or git commit hash shortened to 12 characters; other values as text (cut at 80 characters)."""
    text = str(value)
    if len(text) in (40, 64) and all(character in "0123456789abcdef" for character in text):
        return text[:12] + "..."
    return text if len(text) <= 80 else text[:77] + "..."


def describe_record(record: Mapping[str, Any]) -> list[str]:
    """The decision itself and its versions block."""
    versions = record.get("versions") or {}
    policy = record.get("policy_result") or {}
    lines = [
        f"Decision record {record.get('record_id')} - application {record.get('application_id')} "
        f"({policy.get('line', 'unknown line')}, {record.get('data_label', 'SYNTHETIC')})",
        f"  recorded at {record.get('recorded_at')}; final status {record.get('final_status')}; "
        f"reference recommendation {record.get('reference_recommendation')}",
    ]
    feedback = feedback_from_record(record)
    if feedback is None:
        lines.append("  no reviewer block in this record")
    else:
        mapped = " (mapped from is_override: record written before Phase 6)" if feedback.legacy else ""
        lines.append(
            f"  reviewer role {feedback.reviewer_role}; action {feedback.action}; review action "
            f"{feedback.review_action}{mapped}; reason codes {list(feedback.reason_codes)}; reviewer label "
            f"{feedback.reviewer_label}"
        )
    lines.append("Versions:")
    lines.extend(f"  {name}: {value}" for name, value in versions.items())
    return lines


def results_provenance(results_file: str, repo_root: Path) -> list[str]:
    """Who produced a results file: per-section metadata (risk/results.py style) or the agent report's stamp."""
    contents = read_json(resolve(results_file, repo_root))
    if contents is None:
        return ["      (file not present here: its provenance cannot be shown)"]
    lines = []
    if "generated_by" in contents or "versions" in contents:
        git = (contents.get("versions") or {}).get("git") or {}
        applications = contents.get("applications") or {}
        lines.append(
            f"      generated by {contents.get('generated_by')} at {contents.get('generated_at')}; commit "
            f"{short(git.get('commit'))}, uncommitted changes {git.get('uncommitted_changes')}; applications set "
            f"{short(applications.get('set_sha256'))} ({applications.get('count')} SYNTHETIC applications)"
        )
        return lines
    for section, entry in contents.items():
        if not isinstance(entry, Mapping):
            continue
        metadata = entry.get("metadata") or {}
        if metadata:
            lines.append(
                f"      [{section}] commit {short(metadata.get('git_commit'))}, dirty "
                f"{metadata.get('git_worktree_dirty')}, generated {metadata.get('generated_at_utc')}"
            )
    return lines or ["      (no provenance metadata found in the file)"]


def find_check(checks: list[LineageCheck], kind: str, item: str) -> LineageCheck | None:
    """The check of this kind for this item, if any."""
    for check in checks:
        if check.kind == kind and check.item == item:
            return check
    return None


def describe_models(lineage: Mapping[str, Any], checks: list[LineageCheck]) -> list[str]:
    """Each model: its version, whether it answered, what validated it, and its dataset checksum lines."""
    models = lineage.get("models") or []
    lines = ["  Models:"]
    if not models:
        lines.append("    none: no score_risk call in this decision (a startup file calls no model)")
    for entry in models:
        lines.append(f"    - {entry.get('product')}: {entry.get('model_version_label', 'not recorded')}")
        if "not_recorded" in entry:
            lines.append(f"      not recorded: {entry['not_recorded']}")
            continue
        served = entry.get("served_model_version")
        answered = (
            f"ok, served {served}" if served else f"{entry.get('tool_status')} (the model did not score this file)"
        )
        lines.append(f"      score_risk in this decision: {answered}")
        mlflow = entry.get("mlflow") or {}
        lines.append(
            f"      MLflow: experiment {mlflow.get('experiment')}; measured at commit "
            f"{short(entry.get('measured_at_git_commit'))} on {entry.get('measured_at_utc')} "
            f"(dirty tree: {entry.get('measured_with_dirty_tree')})"
        )
        item = f"{entry['results_file']}:{entry['results_section']}"
        lines.append(
            f"      validated by {item}; model version now {status_text(find_check(checks, 'model_version', item))}"
        )
        for dataset, dataset_lines in (entry.get("datasets") or {}).items():
            for line in dataset_lines:
                name = f"{dataset}/{line.get('file')}"
                lines.append(
                    f"      dataset {name} sha256 {short(line.get('sha256'))} (hashed {line.get('hashed_at_utc')}, "
                    f"source {line.get('source')})"
                )
                log_check = find_check(checks, "dataset_checksum_log", name)
                file_check = find_check(checks, "dataset_file", name)
                lines.append(f"        latest checksums.jsonl line {status_text(log_check)}")
                lines.append(f"        data file {status_text(file_check)}")
    return lines


def describe_lineage(lineage: Mapping[str, Any], checks: list[LineageCheck], repo_root: Path) -> list[str]:
    """The lineage block with a status per recorded value."""
    code = lineage.get("code") or {}
    lines = [
        f"Lineage ({lineage.get('lineage_version')})",
        f"  Code: decided at commit {short(code.get('git_sha'))} (dirty: {code.get('git_dirty')}); working tree now "
        f"{short(git_sha(repo_root))} (dirty: {git_dirty(repo_root)})",
        *describe_models(lineage, checks),
        "  Prompts:",
    ]
    for prompt_id, entry in (lineage.get("prompts") or {}).items():
        check = find_check(checks, "prompt_file", entry["file"])
        lines.append(f"    - {prompt_id}: {entry['file']} sha256 {short(entry.get('sha256'))} {status_text(check)}")
        label_check = find_check(checks, "prompt_version", prompt_id)
        lines.append(f"      prompt_version {entry.get('prompt_version')} {status_text(label_check)}")
        if entry.get("note"):
            lines.append(f"      note: {entry['note']}")
    lines.append("  Eval sets:")
    for set_id, entry in (lineage.get("eval_sets") or {}).items():
        file_check = find_check(checks, "eval_set_file", entry["file"])
        hash_check = find_check(checks, "eval_set_hash", f"{entry['file']}:{entry['set_hash_field']}")
        lines.append(
            f"    - {set_id}: {entry['file']} sha256 {short(entry.get('file_sha256'))} {status_text(file_check)}"
        )
        lines.append(f"      {entry['set_hash_field']} {short(entry.get('set_sha256'))} {status_text(hash_check)}")
    lines.append("  Results files that validated the models and the agent:")
    for results_file, digest in (lineage.get("results_files") or {}).items():
        check = find_check(checks, "results_file", results_file)
        lines.append(f"    - {results_file} sha256 {short(digest)} {status_text(check)}")
        lines.extend(results_provenance(results_file, repo_root))
    for reason in lineage.get("not_recorded") or []:
        lines.append(f"  Not recorded: {reason}")
    return lines


def summary_line(checks: list[LineageCheck]) -> str:
    """Counts per status and the overall verdict."""
    counts = {
        status: sum(1 for check in checks if check.status == status) for status in ("ok", "STALE", "not_checkable")
    }
    return (
        f"Summary: {counts['ok']} OK, {counts['STALE']} STALE, {counts['not_checkable']} not checkable -> "
        f"{overall_status(checks)}"
    )


def main(argv: list[str] | None = None, repo_root: Path = REPO_ROOT) -> int:
    """Print one record's lineage; see the module docstring for the exit codes."""
    args = parse_args(argv)
    if not args.log.is_file():
        print(f"No decision log at {args.log}", file=sys.stderr)
        return EXIT_NOT_FOUND
    log = DecisionLog(args.log)
    record = log.find(args.record_id)
    if record is None:
        print(f"No record {args.record_id!r} in {args.log}", file=sys.stderr)
        return EXIT_NOT_FOUND
    problems = log.verify_chain()
    print("\n".join(describe_record(record)))
    print(f"Log chain: {'intact' if not problems else 'PROBLEMS: ' + '; '.join(problems)}")
    lineage = record.get("lineage")
    if not isinstance(lineage, Mapping):
        print(
            "This record has no lineage block: it was written before Phase 6 (lineage not recorded). Only its "
            "versions block above is available."
        )
        return 0
    checks = verify_lineage(
        lineage,
        repo_root=repo_root,
        raw_dir=repo_root / "data" / "raw",
        prompt_labels=CURRENT_PROMPT_LABELS,
        hash_data_files=not args.no_data_files,
    )
    print("\n".join(describe_lineage(lineage, checks, repo_root)))
    print(summary_line(checks))
    if args.strict and any(check.status == "STALE" for check in checks):
        return EXIT_STALE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

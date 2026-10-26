"""Decision lineage (Phase 6 A): which models, datasets, prompts and evaluation sets stand behind one decision.

Every decision record the agent writes carries a "lineage" block (build_decision_lineage, called by the graph's
record_decision node). The block is optional and versioned (lineage_version "lineage-v1"): records written before
Phase 6 have none and still load; readers report it as "not recorded".

Shape of the block:

    {lineage_version, code{git_sha, git_dirty},
     models[{product, tool_status, served_model_version, results_file, results_section, model_version_label, mlflow,
             measured_at_git_commit, measured_at_utc, measured_with_dirty_tree, datasets{name: [checksum line]}}],
     prompts{id: {file, sha256, prompt_version, note}},
     eval_sets{id: {file, file_sha256, set_hash_field, set_sha256}},
     results_files{path: sha256},
     decision_logic{path: sha256},
     not_recorded[reason, ...]}

decision_logic was added to lineage-v1 after the first Phase 6 records were written. It is optional like the block
itself: a block without it still loads, and the check reports the decision logic as not checkable for that record.

Where each value comes from:
- code: the git commit and dirty flag of the working tree when the decision was made. They are shown next to today's
  commit (scripts/lineage.py) but never compared, because every later commit would flag every record, whatever it
  changed. The record's versions block (graph, policy, prompt and LLM labels) is not compared either, apart from
  prompt_version: the policy and graph labels are fixed strings that stay the same when a rule changes.
- decision_logic: so a change of the rules does show, the SHA-256 of each source file that decides what the agent
  recommends and what a reviewer may do (DECISION_LOGIC_FILES: the reference policy of every line, the credit and
  insurance calculations its thresholds use, the shared vocabulary, the human approval checkpoint in hitl.py, the
  graph, the role rules in security/rbac.py and the fixed list of override reasons). Like the prompt hash, any edit of
  one of these files changes it. Other files (for example the evidence engine) are not hashed; scripts/lineage.py
  says so.
- models: the score_risk calls of THIS decision name a product ("credit" or "insurance_frequency"); that is also the
  section of results/risk_metrics.json that measured the model. The section's mlflow block (registered model and
  version, run id), its model_version label and the git commit that measured it are copied. tool_status says whether
  the model actually answered in this decision: when the risk service is not running the status is "unavailable",
  the entry names the configured model and served_model_version is None (the model did not score the file). A startup
  file calls no model (none here was trained on startup loans), so its models list is empty.
- datasets: the checksum lines that risk/results.py copied into that results section's metadata when the model was
  trained (the latest data/raw/checksums.jsonl line per file). They are the data the model used; the raw data files
  are not needed to write the block.
- prompts: the agent's memo prompt lives in code (SYSTEM_PROMPT and USER_TEMPLATE in src/uwcopilot/agent/memo.py), so
  its hash is the SHA-256 of that source FILE, computed with governance/provenance.hash_files. Any edit of the file
  changes it, even one that leaves the prompt text alone. The short prompt_version label of the text is kept beside.
- eval_sets: the SHA-256 of each committed manifest file plus the set hash it declares (test_truth_sha256 of the
  held-out document packets; applications_sha256 of the agent applications).
- results_files: the SHA-256 of the results files that validated the models and the agent.

A file that is missing (for example in a deployment image that has no results/ folder) is listed in not_recorded with
the reason. Nothing is guessed.

verify_lineage() recomputes every recorded hash and version listed above (not the code commit) from the files as they
are in the working tree now and returns one LineageCheck per value: "ok", "STALE" (the recorded hash or version no
longer matches) or "not_checkable" (the file is not present here, for example the Lending Club data, which needs a
Kaggle key). Every not_recorded entry also becomes a not_checkable check, because a part that was never recorded
cannot be shown to match. overall_status() is "current" only when at least one check ran and every check is ok.
"""

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import RAW_DATA_DIR, REPO_ROOT
from uwcopilot.governance.provenance import display_path, git_dirty, git_sha, hash_files

LINEAGE_VERSION = "lineage-v1"
RISK_RESULTS_FILE = Path("results/risk_metrics.json")
AGENT_RESULTS_FILE = Path("results/agent_metrics.json")
CHECKSUM_LOG_NAME = "checksums.jsonl"
VALIDATING_RESULTS: tuple[Path, ...] = (RISK_RESULTS_FILE, AGENT_RESULTS_FILE)
# The agent's rule files, found next to this package (this module does not import the agent, which imports it).
AGENT_SOURCE_DIR = Path(__file__).resolve().parent.parent / "agent"
SECURITY_SOURCE_DIR = Path(__file__).resolve().parent.parent / "security"
DECISION_LOGIC_FILES: tuple[Path, ...] = (
    AGENT_SOURCE_DIR / "policy.py",
    AGENT_SOURCE_DIR / "policy_credit.py",
    AGENT_SOURCE_DIR / "policy_insurance.py",
    AGENT_SOURCE_DIR / "policy_startup.py",
    AGENT_SOURCE_DIR / "credit_calculations.py",
    AGENT_SOURCE_DIR / "insurance_calculations.py",
    AGENT_SOURCE_DIR / "vocab.py",
    AGENT_SOURCE_DIR / "hitl.py",
    AGENT_SOURCE_DIR / "graph.py",
    SECURITY_SOURCE_DIR / "rbac.py",
    REPO_ROOT / "config" / "override_reasons.yaml",
)

CheckStatus = Literal["ok", "STALE", "not_checkable"]


@dataclass(frozen=True)
class PromptSource:
    """A prompt whose file is hashed into the lineage: an id, the file that holds it, its label and a note."""

    prompt_id: str
    path: Path
    label: str | None = None
    note: str = ""


@dataclass(frozen=True)
class EvalSetSource:
    """A committed evaluation-set manifest and the field in it that holds the set's own hash."""

    path: Path
    set_hash_field: str


EVAL_SETS: dict[str, EvalSetSource] = {
    "agent_applications": EvalSetSource(Path("eval/applications/manifest.json"), "applications_sha256"),
    "startup_applications": EvalSetSource(Path("eval/startup_applications/manifest.json"), "applications_sha256"),
    "docintel_packets_test": EvalSetSource(Path("eval/packets/manifest.json"), "test_truth_sha256"),
}


@dataclass(frozen=True)
class LineageCheck:
    """One recorded value compared with the file as it is now."""

    kind: str
    item: str
    recorded: Any
    current: Any
    status: CheckStatus
    detail: str = ""


def resolve(path: Path | str, repo_root: Path) -> Path:
    """An absolute path: repo-relative paths are taken from repo_root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo_root / candidate


def read_json(path: Path) -> dict[str, Any] | None:
    """The JSON object in a file, or None when the file is missing or is not a JSON object."""
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def file_sha256(path: Path) -> str | None:
    """SHA-256 of a file, or None when it does not exist."""
    return sha256_of_file(path) if path.is_file() else None


# ---------------------------------------------------------------------------------------------------------------
# Building the block
# ---------------------------------------------------------------------------------------------------------------


def model_version_label(section_results: Mapping[str, Any]) -> str | None:
    """The model version label of a results section: its model_version.label, else 'registered model vN'."""
    version = section_results.get("model_version")
    if isinstance(version, Mapping) and version.get("label"):
        return str(version["label"])
    mlflow = section_results.get("mlflow") or {}
    if mlflow.get("registered_model"):
        return f"{mlflow['registered_model']} v{mlflow.get('registered_version')}"
    return None


def dataset_lines(metadata: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """{dataset: [checksum line, ...]} copied from a results section's metadata.datasets."""
    lines: dict[str, list[dict[str, Any]]] = {}
    for dataset, entry in (metadata.get("datasets") or {}).items():
        lines[str(dataset)] = [dict(line) for line in (entry or {}).get("files") or []]
    return lines


def score_risk_calls(tool_results: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """{product: the score_risk result for it} from a decision's tool results (one call per product)."""
    calls: dict[str, Mapping[str, Any]] = {}
    for result in tool_results:
        if result.get("tool") != "score_risk":
            continue
        product = str((result.get("arguments") or {}).get("product", "unknown"))
        calls[product] = result
    return calls


def model_entry(
    product: str, result: Mapping[str, Any], risk_results: dict[str, Any] | None, risk_file: str
) -> dict[str, Any]:
    """The lineage of the model behind one score_risk call (module docstring)."""
    output = result.get("output") if result.get("status") == "ok" else None
    entry: dict[str, Any] = {
        "product": product,
        "tool_status": result.get("status"),
        "served_model_version": (output or {}).get("model_version"),
        "results_file": risk_file,
        "results_section": product,
    }
    if risk_results is None:
        entry["not_recorded"] = f"{risk_file} is not present or not readable"
        return entry
    section = risk_results.get(product)
    if not isinstance(section, Mapping):
        entry["not_recorded"] = f"{risk_file} has no section {product!r}"
        return entry
    results = section.get("results") or {}
    metadata = section.get("metadata") or {}
    entry.update(
        {
            "model_version_label": model_version_label(results),
            "mlflow": results.get("mlflow"),
            "measured_at_git_commit": metadata.get("git_commit"),
            "measured_at_utc": metadata.get("generated_at_utc"),
            "measured_with_dirty_tree": metadata.get("git_worktree_dirty"),
            "datasets": dataset_lines(metadata),
        }
    )
    return entry


def prompt_entries(prompts: Sequence[PromptSource], repo_root: Path) -> tuple[dict[str, Any], list[str]]:
    """({prompt id: {file, sha256, prompt_version, note}}, not-recorded reasons) for the prompt files."""
    entries: dict[str, Any] = {}
    missing: list[str] = []
    for prompt in prompts:
        path = resolve(prompt.path, repo_root)
        shown = display_path(path, repo_root)
        if not path.is_file():
            missing.append(f"prompt {prompt.prompt_id}: {shown} is not present")
            continue
        digest = hash_files([path], repo_root)[shown]
        entries[prompt.prompt_id] = {
            "file": shown,
            "sha256": digest,
            "prompt_version": prompt.label,
            "note": prompt.note,
        }
    return entries, missing


def eval_set_entries(eval_sets: Mapping[str, EvalSetSource], repo_root: Path) -> tuple[dict[str, Any], list[str]]:
    """({id: {file, file_sha256, set_hash_field, set_sha256}}, not-recorded reasons) for the manifests."""
    entries: dict[str, Any] = {}
    missing: list[str] = []
    for set_id, source in eval_sets.items():
        path = resolve(source.path, repo_root)
        shown = display_path(path, repo_root)
        manifest = read_json(path)
        if manifest is None:
            missing.append(f"eval set {set_id}: {shown} is not present or not readable")
            continue
        entries[set_id] = {
            "file": shown,
            "file_sha256": sha256_of_file(path),
            "set_hash_field": source.set_hash_field,
            "set_sha256": manifest.get(source.set_hash_field),
        }
    return entries, missing


def decision_logic_entries(paths: Sequence[Path], repo_root: Path) -> tuple[dict[str, str], list[str]]:
    """({file: sha256}, not-recorded reasons) for the source files that hold the decision rules."""
    entries: dict[str, str] = {}
    missing: list[str] = []
    for path in paths:
        resolved = resolve(path, repo_root)
        shown = display_path(resolved, repo_root)
        if not resolved.is_file():
            missing.append(f"decision logic file {shown} is not present")
            continue
        entries[shown] = sha256_of_file(resolved)
    return entries, missing


def build_decision_lineage(
    tool_results: Sequence[Mapping[str, Any]],
    prompts: Sequence[PromptSource] = (),
    eval_sets: Mapping[str, EvalSetSource] = EVAL_SETS,
    risk_results_file: Path = RISK_RESULTS_FILE,
    validating_results: Sequence[Path] = VALIDATING_RESULTS,
    repo_root: Path = REPO_ROOT,
    decision_logic: Sequence[Path] = DECISION_LOGIC_FILES,
) -> dict[str, Any]:
    """The lineage block of one decision (module docstring). Never raises for a missing file: it says so instead.

    Paths in the block are relative to repo_root (provenance.display_path), never absolute machine paths.
    """
    not_recorded: list[str] = []
    risk_path = resolve(risk_results_file, repo_root)
    risk_results = read_json(risk_path)
    risk_shown = display_path(risk_path, repo_root)
    models = [
        model_entry(product, result, risk_results, risk_shown)
        for product, result in sorted(score_risk_calls(tool_results).items())
    ]
    prompt_block, missing_prompts = prompt_entries(prompts, repo_root)
    eval_block, missing_sets = eval_set_entries(eval_sets, repo_root)
    results_block: dict[str, str] = {}
    for results_file in validating_results:
        results_path = resolve(results_file, repo_root)
        digest = file_sha256(results_path)
        if digest is None:
            not_recorded.append(f"results file {display_path(results_path, repo_root)} is not present")
        else:
            results_block[display_path(results_path, repo_root)] = digest
    logic_block, missing_logic = decision_logic_entries(decision_logic, repo_root)
    not_recorded.extend(missing_prompts + missing_sets + missing_logic)
    not_recorded.extend(model["not_recorded"] for model in models if "not_recorded" in model)
    return {
        "lineage_version": LINEAGE_VERSION,
        "code": {"git_sha": git_sha(repo_root), "git_dirty": git_dirty(repo_root)},
        "models": models,
        "prompts": prompt_block,
        "eval_sets": eval_block,
        "results_files": results_block,
        "decision_logic": logic_block,
        "not_recorded": not_recorded,
    }


# ---------------------------------------------------------------------------------------------------------------
# Checking a recorded block against the files as they are now
# ---------------------------------------------------------------------------------------------------------------


def compare(kind: str, item: str, recorded: Any, current: Any, missing_detail: str = "") -> LineageCheck:
    """ok when equal, STALE when different, not_checkable when the current value cannot be read (current is None)."""
    if current is None:
        return LineageCheck(kind, item, recorded, None, "not_checkable", missing_detail or "not present here")
    if recorded == current:
        return LineageCheck(kind, item, recorded, current, "ok")
    return LineageCheck(kind, item, recorded, current, "STALE", "the recorded value no longer matches")


def latest_checksum_lines(checksum_log: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """{(dataset, file): the latest line} of data/raw/checksums.jsonl ({} when the log is absent)."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    if not checksum_log.is_file():
        return latest
    for line in checksum_log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            latest[(str(record.get("dataset")), str(record.get("file")))] = record
    return latest


class FileHasher:
    """Hashes each file once per check run (a 30 MB dataset is not re-read for every record)."""

    def __init__(self) -> None:
        self.hashes: dict[Path, str | None] = {}

    def sha256(self, path: Path) -> str | None:
        """SHA-256 of the file, or None when it does not exist."""
        if path not in self.hashes:
            self.hashes[path] = file_sha256(path)
        return self.hashes[path]


def check_prompts(
    lineage: Mapping[str, Any], repo_root: Path, labels: Mapping[str, str], hasher: FileHasher
) -> list[LineageCheck]:
    """Prompt file hashes (and labels, when the caller gives the current ones)."""
    checks = []
    for prompt_id, entry in (lineage.get("prompts") or {}).items():
        path = resolve(entry["file"], repo_root)
        checks.append(compare("prompt_file", entry["file"], entry.get("sha256"), hasher.sha256(path)))
        if prompt_id in labels and entry.get("prompt_version") is not None:
            checks.append(compare("prompt_version", prompt_id, entry["prompt_version"], labels[prompt_id]))
    return checks


def check_eval_sets(lineage: Mapping[str, Any], repo_root: Path, hasher: FileHasher) -> list[LineageCheck]:
    """Manifest file hashes and the set hash each manifest declares."""
    checks = []
    for entry in (lineage.get("eval_sets") or {}).values():
        path = resolve(entry["file"], repo_root)
        checks.append(compare("eval_set_file", entry["file"], entry.get("file_sha256"), hasher.sha256(path)))
        manifest = read_json(path)
        current = None if manifest is None else manifest.get(entry["set_hash_field"])
        item = f"{entry['file']}:{entry['set_hash_field']}"
        checks.append(compare("eval_set_hash", item, entry.get("set_sha256"), current))
    return checks


def check_results_files(lineage: Mapping[str, Any], repo_root: Path, hasher: FileHasher) -> list[LineageCheck]:
    """The results files that validated the models and the agent."""
    checks = []
    for results_file, digest in (lineage.get("results_files") or {}).items():
        current = hasher.sha256(resolve(results_file, repo_root))
        checks.append(compare("results_file", results_file, digest, current))
    return checks


def check_decision_logic(lineage: Mapping[str, Any], repo_root: Path, hasher: FileHasher) -> list[LineageCheck]:
    """The source files that hold the decision rules; not checkable for a block written before they were hashed."""
    if "decision_logic" not in lineage:
        detail = "not recorded: this block was written before decision-logic hashes were added"
        return [LineageCheck("decision_logic_file", "decision logic", None, None, "not_checkable", detail)]
    checks = []
    for logic_file, digest in (lineage.get("decision_logic") or {}).items():
        current = hasher.sha256(resolve(logic_file, repo_root))
        checks.append(compare("decision_logic_file", logic_file, digest, current))
    return checks


def check_not_recorded(lineage: Mapping[str, Any]) -> list[LineageCheck]:
    """One not_checkable check per part the block could not record (a model's own reason is checked by check_models)."""
    model_reasons = {entry["not_recorded"] for entry in lineage.get("models") or [] if "not_recorded" in entry}
    checks = []
    for reason in lineage.get("not_recorded") or []:
        if reason not in model_reasons:
            checks.append(LineageCheck("not_recorded", reason, None, None, "not_checkable", "not recorded"))
    return checks


def model_identity(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The parts of a model entry that name the model version (compared by check_models)."""
    return {
        "model_version_label": entry.get("model_version_label"),
        "mlflow": entry.get("mlflow"),
        "measured_at_git_commit": entry.get("measured_at_git_commit"),
    }


def check_models(lineage: Mapping[str, Any], repo_root: Path) -> list[LineageCheck]:
    """The model versions, compared with the same results section as it is now."""
    checks = []
    for entry in lineage.get("models") or []:
        if "not_recorded" in entry:
            checks.append(
                LineageCheck(
                    "model_version", entry.get("product", "?"), None, None, "not_checkable", entry["not_recorded"]
                )
            )
            continue
        results = read_json(resolve(entry["results_file"], repo_root))
        current = None
        if results is not None and isinstance(results.get(entry["results_section"]), Mapping):
            # Build the entry again from today's results file (no tool result needed for the identity fields).
            today = model_entry(entry["results_section"], {}, results, entry["results_file"])
            current = model_identity(today)
        item = f"{entry['results_file']}:{entry['results_section']}"
        checks.append(compare("model_version", item, model_identity(entry), current))
    return checks


def check_datasets(
    lineage: Mapping[str, Any], raw_dir: Path, checksum_log: Path, hasher: FileHasher | None
) -> list[LineageCheck]:
    """Each dataset checksum line: against the latest line of the checksum log, and (with a hasher) the file itself."""
    checks = []
    latest = latest_checksum_lines(checksum_log)
    for entry in lineage.get("models") or []:
        for dataset, lines in (entry.get("datasets") or {}).items():
            for line in lines:
                item = f"{dataset}/{line.get('file')}"
                logged = latest.get((dataset, str(line.get("file"))))
                checks.append(
                    compare(
                        "dataset_checksum_log",
                        item,
                        line.get("sha256"),
                        None if logged is None else logged.get("sha256"),
                        f"no line for this file in {checksum_log.name}",
                    )
                )
                if hasher is not None:
                    path = raw_dir / dataset / str(line.get("file"))
                    checks.append(
                        compare("dataset_file", item, line.get("sha256"), hasher.sha256(path), "data file not present")
                    )
    return checks


def verify_lineage(
    lineage: Mapping[str, Any],
    repo_root: Path = REPO_ROOT,
    raw_dir: Path = RAW_DATA_DIR,
    checksum_log: Path | None = None,
    prompt_labels: Mapping[str, str] | None = None,
    hasher: FileHasher | None = None,
    hash_data_files: bool = True,
) -> list[LineageCheck]:
    """Every recorded hash and version of a lineage block compared with the working tree now (module docstring).

    The code commit is not compared (module docstring); every not_recorded entry becomes a not_checkable check.
    prompt_labels gives the current prompt_version label per prompt id (the caller knows them; this module does not
    import the agent). hash_data_files=False skips hashing the raw data files (the checksum log is still compared).
    """
    hasher = hasher or FileHasher()
    log = checksum_log if checksum_log is not None else raw_dir / CHECKSUM_LOG_NAME
    return [
        *check_prompts(lineage, repo_root, prompt_labels or {}, hasher),
        *check_eval_sets(lineage, repo_root, hasher),
        *check_results_files(lineage, repo_root, hasher),
        *check_decision_logic(lineage, repo_root, hasher),
        *check_models(lineage, repo_root),
        *check_datasets(lineage, raw_dir, log, hasher if hash_data_files else None),
        *check_not_recorded(lineage),
    ]


def overall_status(checks: Iterable[LineageCheck]) -> str:
    """STALE when any check is STALE; current when at least one check ran and every check is ok.

    Anything else is current_where_checkable, including a block with no check at all: nothing was compared, so it
    cannot be called current.
    """
    statuses = [check.status for check in checks]
    if "STALE" in statuses:
        return "STALE"
    if statuses and all(status == "ok" for status in statuses):
        return "current"
    return "current_where_checkable"


def summarize_lineage_checks(
    records: Iterable[Mapping[str, Any]],
    repo_root: Path = REPO_ROOT,
    raw_dir: Path = RAW_DATA_DIR,
    checksum_log: Path | None = None,
    prompt_labels: Mapping[str, str] | None = None,
    hash_data_files: bool = True,
) -> dict[str, Any]:
    """How many records' lineage still matches the working tree, and why the rest could not be checked."""
    hasher = FileHasher()
    per_record: Counter[str] = Counter()
    per_item: Counter[str] = Counter()
    stale_items: Counter[str] = Counter()
    not_checkable: Counter[str] = Counter()
    records_read = 0
    for record in records:
        records_read += 1
        lineage = record.get("lineage")
        if not isinstance(lineage, Mapping):
            per_record["no_lineage_block"] += 1
            continue
        checks = verify_lineage(
            lineage, repo_root, raw_dir, checksum_log, prompt_labels, hasher=hasher, hash_data_files=hash_data_files
        )
        per_record[overall_status(checks)] += 1
        for check in checks:
            per_item[check.status] += 1
            if check.status == "STALE":
                stale_items[f"{check.kind}:{check.item}"] += 1
            if check.status == "not_checkable":
                not_checkable[f"{check.kind}:{check.item} ({check.detail})"] += 1
    return {
        "lineage_version": LINEAGE_VERSION,
        "records_read": records_read,
        "records_with_lineage": records_read - per_record["no_lineage_block"],
        "records_by_status": dict(sorted(per_record.items())),
        "checks_by_status": dict(sorted(per_item.items())),
        "stale_items": dict(sorted(stale_items.items())),
        "not_checkable_items": dict(sorted(not_checkable.items())),
        "data_files_hashed": hash_data_files,
    }

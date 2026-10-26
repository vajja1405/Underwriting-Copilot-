"""Release gate (Phase 6 D): compare the latest results/*.json with a reviewed baseline and block regressions.

The gate measures nothing itself. It reads numbers that other scripts wrote to results/*.json, reads the same numbers
from the reviewed baseline in results/baseline/ (byte copies made by `scripts/release_gate.py --update-baseline`,
then reviewed and committed), and checks each row of the `gate:` section of config/release_gate.yaml.

One row = one critical number: the results file, the JSON path of the point estimate (value_path), the JSON path of
its CI bound on the conservative side when there is one (ci_bound_path), which of the two is compared (gate_on),
the direction, the kind of limit and the tolerance:

    kind baseline_relative, higher_is_better: PASS when current >= baseline - tolerance
    kind baseline_relative, lower_is_better:  PASS when current <= baseline + tolerance
    kind absolute: the same with the row's fixed `target` in place of the baseline

A value exactly on the limit passes (differences below EDGE_EPSILON count as equal, so float rounding such as
0.7123 - 0.005 cannot flip an edge case).

Statuses (the words of docs/innovations.md S3, where they fit):
    PASS          the current value is within the limit.
    FAIL          the current value is beyond the limit; or it is invalid (NaN, null, not a number); or the baseline
                  measured it and the current file no longer does (file or key absent, or "not measured").
    MISSING       required evidence is absent in both (row `required: true`), or a measured current value has no
                  reviewed baseline value to be compared with (a new measurement must be reviewed into the baseline).
    NOT_MEASURED  neither the current file nor the baseline measured it, and the row is not required. It is listed,
                  never counted as passing, and does not block.
FAIL and MISSING block: the gate's verdict is FAIL and the script exits 1. STALE (S3: evidence measured on another
prompt, corpus or model than HEAD) is not used here: this gate recomputes no provenance hash. Instead, a results file
whose provenance says it was written from a dirty working tree is reported as a warning, not a failure, because most
committed results files were written that way.

The baseline manifest (results/baseline/baseline_manifest.json) records the SHA-256 of every copy. A copy that no
longer matches it (edited by hand) or that the manifest does not list is a baseline problem, and blocks too.

This is a demonstration of a release gate on SYNTHETIC and public-data evaluations, not a compliance programme.
"""

import json
import math
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import CONFIG_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.governance.freeze_proof import canonical_sha256
from uwcopilot.governance.provenance import git_sha
from uwcopilot.risk.results import NOT_MEASURED, is_not_measured

DEFAULT_CONFIG_PATH = CONFIG_DIR / "release_gate.yaml"
DEFAULT_RESULTS_DIR = RESULTS_DIR
DEFAULT_BASELINE_DIR = RESULTS_DIR / "baseline"
MANIFEST_NAME = "baseline_manifest.json"
EDGE_EPSILON = 1e-9
UNKNOWN = "unknown"

Direction = Literal["higher_is_better", "lower_is_better"]
Kind = Literal["baseline_relative", "absolute"]
GateOn = Literal["value", "ci_bound"]
Status = Literal["PASS", "FAIL", "MISSING", "NOT_MEASURED"]
ReadingState = Literal["measured", "not_measured", "invalid"]
STATUSES: tuple[Status, ...] = ("PASS", "FAIL", "MISSING", "NOT_MEASURED")
BLOCKING_STATUSES: tuple[Status, ...] = ("FAIL", "MISSING")


class GateConfigError(ValueError):
    """config/release_gate.yaml has no usable `gate:` section."""


class StrictModel(BaseModel):
    """A settings object: frozen, and unknown keys are an error (a typo cannot silently disable a check)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class GateRow(StrictModel):
    """One gated number (see the module docstring for how it is judged)."""

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1)
    file: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.json$", description="a file name in results/, no folders")
    value_path: str = Field(min_length=1)
    ci_bound_path: str | None = None
    gate_on: GateOn
    direction: Direction
    kind: Kind
    tolerance: float = Field(ge=0)
    target: float | None = None
    required: bool
    why_critical: str = Field(min_length=1)
    note: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> "GateRow":
        """A CI-bound row names its bound, an absolute row has a target, a baseline-relative row has none."""
        if self.gate_on == "ci_bound" and self.ci_bound_path is None:
            raise ValueError(f"{self.id}: gate_on ci_bound needs a ci_bound_path")
        if self.kind == "absolute" and self.target is None:
            raise ValueError(f"{self.id}: an absolute row needs a target")
        if self.kind == "baseline_relative" and self.target is not None:
            raise ValueError(f"{self.id}: a baseline_relative row is compared with the baseline, not a target")
        if self.file == MANIFEST_NAME:
            raise ValueError(f"{self.id}: {MANIFEST_NAME} is the baseline manifest, not a results file")
        return self

    @property
    def gated_path(self) -> str:
        """The JSON path of the number that is compared."""
        if self.gate_on == "ci_bound" and self.ci_bound_path is not None:
            return self.ci_bound_path
        return self.value_path


class GateSettings(StrictModel):
    """The `gate:` section of config/release_gate.yaml."""

    rows: list[GateRow] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "GateSettings":
        """Row ids name rows in the output, so each must be unique."""
        ids = [row.id for row in self.rows]
        duplicates = sorted({row_id for row_id in ids if ids.count(row_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate gate row ids: {duplicates}")
        return self

    def files(self) -> list[str]:
        """The results files the gate reads, sorted."""
        return sorted({row.file for row in self.rows})


def load_gate_document(path: Path = DEFAULT_CONFIG_PATH) -> Mapping[str, Any]:
    """The raw `gate:` section of the YAML file (GateConfigError when it is absent)."""
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    section = document.get("gate") if isinstance(document, Mapping) else None
    if not isinstance(section, Mapping):
        raise GateConfigError(f"{Path(path).name} has no `gate:` section")
    return section


def load_gate_settings(path: Path = DEFAULT_CONFIG_PATH) -> GateSettings:
    """Read and validate the `gate:` section (GateConfigError or pydantic ValidationError, both ValueErrors)."""
    return GateSettings.model_validate(load_gate_document(path))


def gate_section_sha256(path: Path = DEFAULT_CONFIG_PATH) -> str:
    """Fingerprint of the parsed `gate:` section, so a results record names the exact rules it was judged by."""
    return canonical_sha256(load_gate_document(path))


# --- Reading numbers ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One number read from a results file: measured (with its value), not measured, or invalid (with why)."""

    state: ReadingState
    value: float | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for the results file."""
        return {"state": self.state, "value": self.value, "detail": self.detail}


@dataclass(frozen=True)
class LoadedFile:
    """A results file as read from disk: its parsed JSON, or why there is none."""

    name: str
    document: Any = None
    problem: str | None = None
    unreadable: bool = False


def load_results_file(directory: Path, name: str) -> LoadedFile:
    """Parse directory/name. An absent file is 'not measured'; a file that is not JSON is unreadable (invalid)."""
    path = Path(directory) / name
    if not path.is_file():
        return LoadedFile(name, problem=f"{name} is absent")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return LoadedFile(name, problem=f"{name} is not valid JSON ({error})", unreadable=True)
    return LoadedFile(name, document=document)


def is_not_measured_marker(value: Any) -> bool:
    """True for the project's two 'not measured' forms: risk.results.not_measured() dicts and 'not measured...' text."""
    if isinstance(value, str):
        return value.strip().lower().startswith(NOT_MEASURED)
    return is_not_measured(value)


def marker_text(value: Any) -> str:
    """The 'not measured' marker as text, with its reason."""
    if isinstance(value, str):
        return value.strip()
    return f"{NOT_MEASURED}: {value.get('reason', 'no reason given')}"


def classify(value: Any) -> Reading:
    """Turn the value found at a path into a Reading."""
    if is_not_measured_marker(value):
        return Reading("not_measured", detail=marker_text(value))
    if value is None:
        return Reading("invalid", detail="null (results files write NaN as null)")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return Reading("invalid", detail=f"a {type(value).__name__}, not a number")
    if not math.isfinite(value):
        return Reading("invalid", detail=f"{value} is not a finite number")
    return Reading("measured", value=float(value))


def lookup(document: Any, path: str) -> Reading:
    """Follow a dotted path (a number segment indexes a list) and classify what is there.

    A 'not measured' marker met on the way (for example a whole metric block replaced by "not measured: ...") makes
    the reading 'not measured'; a key that is not there makes it 'not measured' too, naming the missing key.
    """
    current = document
    walked: list[str] = []
    for segment in path.split("."):
        if is_not_measured_marker(current):
            return Reading("not_measured", detail=f"{'.'.join(walked)}: {marker_text(current)}")
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            where = ".".join(walked) or "the top level"
            return Reading("not_measured", detail=f"no {segment!r} under {where} (path {path})")
        walked.append(segment)
    return classify(current)


def read_value(loaded: LoadedFile, path: str | None) -> Reading:
    """The Reading at path in a loaded file (no path: not measured, used for rows without a CI bound)."""
    if path is None:
        return Reading("not_measured", detail="no CI bound path in the row")
    if loaded.unreadable:
        return Reading("invalid", detail=loaded.problem or "unreadable file")
    if loaded.document is None:
        return Reading("not_measured", detail=loaded.problem or "no document")
    return lookup(loaded.document, path)


# --- Judging one row ------------------------------------------------------------------------------------------------


def limit_for(row: GateRow, baseline_value: float | None) -> float:
    """The worst value that still passes: (baseline or target) minus or plus the tolerance."""
    reference = row.target if row.kind == "absolute" else baseline_value
    if reference is None:
        raise ValueError(f"{row.id}: no reference value to build a limit from")
    if row.direction == "higher_is_better":
        return reference - row.tolerance
    return reference + row.tolerance


def within_limit(value: float, limit: float, direction: Direction) -> bool:
    """True when value is on the passing side of limit (or on it, within EDGE_EPSILON)."""
    if direction == "higher_is_better":
        return value >= limit - EDGE_EPSILON
    return value <= limit + EDGE_EPSILON


def describe_limit(row: GateRow, limit: float) -> str:
    """The limit in words, e.g. '>= 0.7073 (baseline 0.7123 - tolerance 0.005)'."""
    sign = ">=" if row.direction == "higher_is_better" else "<="
    operator = "-" if row.direction == "higher_is_better" else "+"
    reference = f"target {row.target:g}" if row.kind == "absolute" else "baseline"
    return f"{sign} {limit:.6g} ({reference} {operator} tolerance {row.tolerance:g})"


def decide(row: GateRow, current: Reading, baseline: Reading) -> tuple[Status, str, float | None]:
    """Status, reason and limit for one row from the current and baseline readings of its gated number."""
    if current.state == "invalid":
        return "FAIL", f"current value is invalid: {current.detail}", None
    if row.kind == "baseline_relative" and baseline.state == "invalid":
        return "FAIL", f"baseline value is invalid: {baseline.detail}", None
    if current.state == "not_measured":
        if baseline.state == "measured":
            return "FAIL", f"measured in the baseline ({baseline.value:.6g}) but not now: {current.detail}", None
        if row.required:
            return "MISSING", f"required evidence is absent: {current.detail}", None
        return "NOT_MEASURED", current.detail, None
    if current.value is None:  # a measured reading always has a value; this keeps the type checker honest
        raise ValueError(f"{row.id}: measured reading without a value")
    if row.kind == "baseline_relative" and (baseline.state != "measured" or baseline.value is None):
        reason = (
            f"no reviewed baseline value to compare with ({baseline.detail}); review the new result, then run "
            "scripts/release_gate.py --update-baseline and commit results/baseline/"
        )
        return "MISSING", reason, None
    limit = limit_for(row, baseline.value)
    words = describe_limit(row, limit)
    if within_limit(current.value, limit, row.direction):
        return "PASS", f"{current.value:.6g} {words}", limit
    return "FAIL", f"{current.value:.6g} is beyond the limit {words}", limit


@dataclass(frozen=True)
class RowResult:
    """The outcome of one row, with what was read from both files."""

    row: GateRow
    status: Status
    reason: str
    limit: float | None
    current: dict[str, Reading]
    baseline: dict[str, Reading]

    @property
    def headroom(self) -> float | None:
        """How far the compared value is on the passing side of the limit (negative: beyond it)."""
        compared = self.current[self.row.gate_on]
        if self.limit is None or compared.value is None:
            return None
        if self.row.direction == "higher_is_better":
            return compared.value - self.limit
        return self.limit - compared.value

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for the results file."""
        return {
            "id": self.row.id,
            "title": self.row.title,
            "status": self.status,
            "reason": self.reason,
            "file": f"results/{self.row.file}",
            "value_path": self.row.value_path,
            "ci_bound_path": self.row.ci_bound_path,
            "gate_on": self.row.gate_on,
            "direction": self.row.direction,
            "kind": self.row.kind,
            "tolerance": self.row.tolerance,
            "target": self.row.target,
            "required": self.row.required,
            "limit": self.limit,
            "headroom": self.headroom,
            "current": {name: reading.to_dict() for name, reading in self.current.items()},
            "baseline": {name: reading.to_dict() for name, reading in self.baseline.items()},
            "why_critical": self.row.why_critical,
            "note": self.row.note,
        }


def evaluate_row(row: GateRow, current_file: LoadedFile, baseline_file: LoadedFile) -> RowResult:
    """Read the row's value and CI bound from both files and judge the gated one."""
    current = {
        "value": read_value(current_file, row.value_path),
        "ci_bound": read_value(current_file, row.ci_bound_path),
    }
    baseline = {
        "value": read_value(baseline_file, row.value_path),
        "ci_bound": read_value(baseline_file, row.ci_bound_path),
    }
    status, reason, limit = decide(row, current[row.gate_on], baseline[row.gate_on])
    return RowResult(row, status, reason, limit, current, baseline)


# --- Provenance warnings --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProvenanceFlag:
    """Where a results file records whether its working tree was dirty, and what it says."""

    where: str
    git_commit: Any
    git_dirty: Any


def dig(document: Any, *keys: str) -> Any:
    """document[key1][key2]..., or None when any step is missing."""
    for key in keys:
        if not isinstance(document, dict) or key not in document:
            return None
        document = document[key]
    return document


def provenance_flag(document: Any, value_path: str) -> ProvenanceFlag | None:
    """The dirty-tree flag for the part of a results file a row reads, in any of the three shapes the repo uses.

    1. governance/provenance.py blocks: provenance.git_dirty (and git_sha);
    2. risk/results.py sections: <section>.metadata.git_worktree_dirty (and git_commit), section = first path key;
    3. scripts/eval_agent.py: versions.git.uncommitted_changes (and commit).
    """
    block = dig(document, "provenance")
    if isinstance(block, dict) and "git_dirty" in block:
        return ProvenanceFlag("provenance.git_dirty", block.get("git_sha"), block["git_dirty"])
    section = value_path.split(".")[0]
    metadata = dig(document, section, "metadata")
    if isinstance(metadata, dict) and "git_worktree_dirty" in metadata:
        return ProvenanceFlag(
            f"{section}.metadata.git_worktree_dirty", metadata.get("git_commit"), metadata["git_worktree_dirty"]
        )
    git = dig(document, "versions", "git")
    if isinstance(git, dict) and "uncommitted_changes" in git:
        return ProvenanceFlag("versions.git.uncommitted_changes", git.get("commit"), git["uncommitted_changes"])
    return None


def provenance_warnings(results: list[RowResult], files: Mapping[str, LoadedFile]) -> list[dict[str, Any]]:
    """One warning per (file, provenance record) that is dirty, unknown or absent, naming the rows it affects."""
    warnings: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        loaded = files[result.row.file]
        if loaded.document is None:
            continue
        flag = provenance_flag(loaded.document, result.row.value_path)
        if flag is not None and flag.git_dirty is False:
            continue
        where = flag.where if flag else "none"
        key = (result.row.file, where)
        if key not in warnings:
            if flag is None:
                message = "no git provenance record found, so the code that wrote these numbers is not pinned"
            elif flag.git_dirty is True:
                message = "written from a working tree with uncommitted changes: the commit alone does not pin the code"
            else:
                message = f"git_dirty is {flag.git_dirty!r}: the commit alone may not pin the code"
            warnings[key] = {
                "kind": "git_dirty" if flag else "no_provenance",
                "file": f"results/{result.row.file}",
                "where": where,
                "git_commit": flag.git_commit if flag else None,
                "message": message,
                "rows": [],
            }
        warnings[key]["rows"].append(result.row.id)
    return list(warnings.values())


# --- The baseline ---------------------------------------------------------------------------------------------------


def file_matches_head(path: Path, repo_root: Path = REPO_ROOT) -> bool | str:
    """True when the file's bytes equal its committed version at HEAD; False when they differ or it is uncommitted.

    'unknown' when git or the repository is unavailable, or the file is outside the repository.
    """
    try:
        relative = Path(path).resolve().relative_to(Path(repo_root).resolve()).as_posix()
    except ValueError:
        return UNKNOWN
    if git_sha(repo_root) == UNKNOWN:
        return UNKNOWN
    try:
        completed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"], cwd=repo_root, capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    if completed.returncode != 0:
        return False
    return completed.stdout == Path(path).read_bytes()


def update_baseline(
    settings: GateSettings,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    baseline_dir: Path = DEFAULT_BASELINE_DIR,
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, Any], list[str]]:
    """Copy every results file the gate reads into baseline_dir and write the manifest.

    A file absent from results_dir is listed as absent, and an older baseline copy of it is removed, so the baseline
    always mirrors the results it was made from. Returns (manifest, names of removed copies). Review the copies and
    commit them: the committed baseline is what the gate compares with.
    """
    results_dir, baseline_dir = Path(results_dir), Path(baseline_dir)
    baseline_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, dict[str, Any]] = {}
    absent: list[str] = []
    removed: list[str] = []
    for name in settings.files():
        source, copy = results_dir / name, baseline_dir / name
        if source.is_file():
            shutil.copyfile(source, copy)
            copied[name] = {
                "sha256": sha256_of_file(copy),
                "source_matches_head_commit": file_matches_head(source, repo_root),
            }
        else:
            absent.append(name)
            if copy.exists():
                copy.unlink()
                removed.append(name)
    manifest = {
        "description": (
            "Reviewed baseline for scripts/release_gate.py: byte copies of the results files the gate reads, made by "
            "`scripts/release_gate.py --update-baseline`. Do not edit the copies by hand: the gate checks them "
            "against these SHA-256 values."
        ),
        "created_by": "scripts/release_gate.py --update-baseline",
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git_sha(repo_root),
        "files": copied,
        "absent_when_made": absent,
    }
    (baseline_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest, removed


def read_manifest(baseline_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    """(manifest, None), or (None, why it cannot be used)."""
    path = Path(baseline_dir) / MANIFEST_NAME
    if not path.is_file():
        return None, f"no {MANIFEST_NAME}: make the baseline with scripts/release_gate.py --update-baseline"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, f"{MANIFEST_NAME} is not valid JSON ({error})"
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        return None, f"{MANIFEST_NAME} has no `files` record"
    return manifest, None


def baseline_problems(settings: GateSettings, baseline_dir: Path, manifest: dict[str, Any] | None) -> list[str]:
    """Baseline copies that the manifest does not vouch for: changed, missing, or never recorded."""
    if manifest is None:
        return []
    recorded: dict[str, Any] = manifest["files"]
    problems: list[str] = []
    for name in settings.files():
        copy = Path(baseline_dir) / name
        entry = recorded.get(name)
        if entry is None:
            if copy.exists():
                problems.append(f"{name}: the copy is not in {MANIFEST_NAME} (not made by --update-baseline)")
        elif not copy.is_file():
            problems.append(f"{name}: listed in {MANIFEST_NAME} but the copy is missing")
        elif sha256_of_file(copy) != entry.get("sha256"):
            problems.append(f"{name}: the copy changed after --update-baseline (SHA-256 differs from the manifest)")
    return problems


# --- The whole gate -------------------------------------------------------------------------------------------------

NOTES: tuple[str, ...] = (
    "The gate reads numbers other scripts wrote to results/*.json and recomputes none of them. They come from "
    "evaluations on SYNTHETIC data (packets, applications, founder scenarios) and on public data (Lending Club, "
    "freMTPL2); none is a production measurement.",
    "NOT_MEASURED rows are listed, never counted as passing: the metric has no value in the current results or the "
    "baseline (for example the LLM-dependent metrics, because no LLM was run).",
    "Injection: action-level containment (injected text cannot make the agent take a decision) is enforced by "
    "tests/security/test_containment.py in the CI test job, which the release-gate job waits for; that is a test "
    "result, not an attack success rate. The per-layer attack success rates are gated only when "
    "results/injection_metrics.json exists; they hold for the frozen payload set only.",
    "git_dirty warnings do not fail the gate today: most committed results files were written from a working tree "
    "with uncommitted changes. They are listed so the files can be regenerated at a clean commit.",
    "STALE (evidence measured on another prompt, corpus or model than HEAD, docs/innovations.md S3) is not checked: "
    "this gate recomputes no provenance hash.",
    "This is a demonstration of a release gate, not a compliance programme; whether such a gate is adequate depends "
    "on the institution and its risk profile.",
)


@dataclass
class GateReport:
    """Every row's result, the provenance warnings and the baseline's state."""

    results: list[RowResult]
    warnings: list[dict[str, Any]]
    baseline_problems: list[str]
    baseline_manifest: dict[str, Any] | None
    manifest_problem: str | None = None
    notes: tuple[str, ...] = field(default=NOTES)

    def counts(self) -> dict[str, int]:
        """Number of rows per status."""
        return {status: sum(result.status == status for result in self.results) for status in STATUSES}

    @property
    def verdict(self) -> Literal["PASS", "FAIL"]:
        """FAIL when any row is FAIL or MISSING, or the baseline is not trustworthy; otherwise PASS."""
        blocked = any(result.status in BLOCKING_STATUSES for result in self.results)
        if blocked or self.baseline_problems or self.manifest_problem:
            return "FAIL"
        return "PASS"

    def to_payload(self, config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
        """The `release_gate` section of results/governance_metrics.json."""
        manifest = self.baseline_manifest or {}
        return {
            "data_label": "reads results of SYNTHETIC and public-data evaluations; the gate measures nothing itself",
            "verdict": self.verdict,
            "exit_code": 0 if self.verdict == "PASS" else 1,
            "counts": {"rows": len(self.results), **self.counts()},
            "blocking_statuses": list(BLOCKING_STATUSES),
            "config": {"file": "config/release_gate.yaml", "section": "gate"},
            "config_gate_section_sha256": gate_section_sha256(config_path),
            "baseline": {
                "dir": "results/baseline",
                "manifest_git_commit": manifest.get("git_commit"),
                "manifest_created_at_utc": manifest.get("created_at_utc"),
                "manifest_problem": self.manifest_problem,
                "problems": self.baseline_problems,
            },
            "rows": [result.to_dict() for result in self.results],
            "warnings": self.warnings,
            "notes": list(self.notes),
        }


def evaluate_gate(
    settings: GateSettings,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    baseline_dir: Path = DEFAULT_BASELINE_DIR,
) -> GateReport:
    """Judge every row against the baseline and collect provenance warnings and baseline problems."""
    current_files = {name: load_results_file(results_dir, name) for name in settings.files()}
    baseline_files = {name: load_results_file(baseline_dir, name) for name in settings.files()}
    results = [evaluate_row(row, current_files[row.file], baseline_files[row.file]) for row in settings.rows]
    manifest, manifest_problem = read_manifest(baseline_dir)
    return GateReport(
        results=results,
        warnings=provenance_warnings(results, current_files),
        baseline_problems=baseline_problems(settings, baseline_dir, manifest),
        baseline_manifest=manifest,
        manifest_problem=manifest_problem,
    )

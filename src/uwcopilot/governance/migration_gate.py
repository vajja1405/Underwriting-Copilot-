"""The LLM migration gate: may a challenger configuration replace the champion? (docs/innovations.md, S1)

A "configuration" is a model (pinned by digest), a prompt id, k and decoding options (config/generation_eval.yaml).
Both configurations are evaluated on exactly the same frozen inputs (scripts/eval_generation.py), so their answers
can be compared question by question.

Algorithm:
1. Refuse unless both runs used identical question ids, frozen-hits hash, key-facts hash, eval-set (question file)
   hashes and corpus checksum (check_same_inputs). A difference means the runs measured different things. With the
   same prompt id, the prompt hashes must also be equal (check_prompts_comparable), and with the same prompt id and k
   every question's request hash must be equal (request_hash_differences): both models saw the same bytes.
2. For each gated metric, take the per-question values of both runs on the questions in the metric's population
   (generation_eval.METRIC_POPULATIONS; for "gold in context" populations, the questions where both runs had the
   gold in context) and compute the paired percentile bootstrap of (challenger - champion), resampling questions
   (rag.metrics.paired_bootstrap_difference: 1,000 resamples, seed 42, 95%).
3. Non-inferiority with margin m (from config/release_gate.yaml), on the CI rounded to 4 decimals:
   higher_is_better: PASS if ci_low >= -m, FAIL if ci_high < -m, otherwise INCONCLUSIVE;
   lower_is_better:  PASS if ci_high <= +m, FAIL if ci_low > +m, otherwise INCONCLUSIVE.
4. Overall PASS only if every gated metric passes; FAIL if any metric fails; otherwise INCONCLUSIVE (which blocks).

Non-inferiority asks "is the challenger not worse by more than m?", not "is it better?". With about 30-130
questions the intervals are wide, so INCONCLUSIVE is a common and honest outcome.

Freeze checks (freeze_summary): each run recorded, when it started, the first commit holding its key facts, question
files and frozen hits, and the first commit holding the `migration:` section of config/release_gate.yaml
(governance/freeze_proof.py). Every frozen input must have been committed before the run's EARLIEST answer was
generated, the margins before the challenger's earliest answer, and the margins must not have changed since the
challenger run. A failed freeze check blocks certification (the script can override it for development, and the
certificate then says NOT VALID FOR RELEASE).

render_certificate() turns the gate's JSON report into the Markdown migration certificate; it reads nothing else.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from uwcopilot.governance.freeze_proof import committed_before
from uwcopilot.rag.generation_eval import METRIC_POPULATIONS
from uwcopilot.rag.metrics import paired_bootstrap_difference

Decision = Literal["PASS", "FAIL", "INCONCLUSIVE"]
Direction = Literal["higher_is_better", "lower_is_better"]
DIRECTIONS: tuple[str, ...] = ("higher_is_better", "lower_is_better")
# The frozen inputs that must be identical for a comparison to mean anything.
SAME_INPUT_FIELDS: tuple[str, ...] = (
    "question_ids",
    "frozen_hits_sha256",
    "key_facts_sha256",
    "question_sets",
    "corpus_checksum",
)
# Frozen inputs whose freeze proof is checked for both runs (the margins are checked for the challenger only).
FROZEN_INPUT_ROLES: tuple[str, ...] = ("key_facts", "frozen_hits", "question_file")
DISCLAIMER = (
    "Demonstration only. Results are measured on SYNTHETIC guidelines and questions written by the project author, "
    "with a small local model; they are not evidence of production quality, and this certificate is not a "
    "compliance artifact."
)


class GateConfigError(ValueError):
    """config/release_gate.yaml's migration section is missing or inconsistent with the code."""


class InputMismatch(ValueError):
    """The two runs did not use the same frozen inputs, so they cannot be compared."""


@dataclass(frozen=True)
class GatedMetric:
    """One gated metric: which way is better, the non-inferiority margin, and its question population."""

    name: str
    direction: Direction
    margin: float
    population: str


@dataclass(frozen=True)
class MigrationSettings:
    """The migration section of config/release_gate.yaml."""

    metrics: tuple[GatedMetric, ...]
    resamples: int
    seed: int
    confidence: float


def parse_migration_settings(document: Mapping[str, Any]) -> MigrationSettings:
    """Validate the parsed YAML document and return its migration settings.

    Each gated metric must be known to the scoring code, and its population in the YAML must equal the one the code
    computes it on, so the file cannot silently describe a different metric.
    """
    section = document.get("migration")
    if not isinstance(section, Mapping):
        raise GateConfigError("config/release_gate.yaml has no `migration:` section")
    bootstrap = section.get("bootstrap") or {}
    if section.get("decision_rule") != "non_inferiority":
        raise GateConfigError("migration.decision_rule must be non_inferiority")
    metrics: list[GatedMetric] = []
    for name, spec in (section.get("gated_metrics") or {}).items():
        if name not in METRIC_POPULATIONS:
            raise GateConfigError(f"gated metric {name!r} is not computed by rag.generation_eval")
        direction = spec.get("direction")
        if direction not in DIRECTIONS:
            raise GateConfigError(f"{name}: direction must be one of {DIRECTIONS}")
        margin = float(spec.get("margin"))
        if not 0.0 <= margin < 1.0:
            raise GateConfigError(f"{name}: margin must be in [0, 1)")
        if spec.get("population") != METRIC_POPULATIONS[name]:
            raise GateConfigError(
                f"{name}: population {spec.get('population')!r} in the YAML differs from the code's "
                f"{METRIC_POPULATIONS[name]!r}"
            )
        metrics.append(GatedMetric(name, direction, margin, METRIC_POPULATIONS[name]))
    if not metrics:
        raise GateConfigError("migration.gated_metrics is empty")
    return MigrationSettings(
        metrics=tuple(metrics),
        resamples=int(bootstrap.get("resamples", 1000)),
        seed=int(bootstrap.get("seed", 42)),
        confidence=float(bootstrap.get("confidence", 0.95)),
    )


def load_migration_settings(path: Path) -> MigrationSettings:
    """Read and validate config/release_gate.yaml."""
    return parse_migration_settings(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def non_inferiority_decision(ci_low: float, ci_high: float, margin: float, direction: Direction) -> Decision:
    """PASS, FAIL or INCONCLUSIVE for a CI of (challenger - champion) (rules in the module docstring)."""
    if ci_low > ci_high:
        raise ValueError("ci_low must not exceed ci_high")
    if direction == "higher_is_better":
        if ci_low >= -margin:
            return "PASS"
        if ci_high < -margin:
            return "FAIL"
        return "INCONCLUSIVE"
    if direction == "lower_is_better":
        if ci_high <= margin:
            return "PASS"
        if ci_low > margin:
            return "FAIL"
        return "INCONCLUSIVE"
    raise ValueError(f"unknown direction {direction!r}")


def overall_decision(decisions: Sequence[str]) -> Decision:
    """PASS only if every metric passed; FAIL if any failed; otherwise INCONCLUSIVE."""
    if not decisions:
        return "INCONCLUSIVE"
    if all(decision == "PASS" for decision in decisions):
        return "PASS"
    if any(decision == "FAIL" for decision in decisions):
        return "FAIL"
    return "INCONCLUSIVE"


def check_same_inputs(champion: Mapping[str, Any], challenger: Mapping[str, Any]) -> list[str]:
    """The SAME_INPUT_FIELDS that differ (or are missing) between two frozen-input blocks ([] when identical)."""
    problems: list[str] = []
    for name in SAME_INPUT_FIELDS:
        if name not in champion or name not in challenger:
            problems.append(f"{name}: missing from a run's frozen_inputs")
        elif champion[name] != challenger[name]:
            problems.append(f"{name}: champion and challenger differ")
    return problems


def check_prompts_comparable(champion: Mapping[str, Any], challenger: Mapping[str, Any]) -> list[str]:
    """With the same prompt id, the prompt hash and the prompt template hash must be recorded and equal."""
    if champion.get("prompt_id") != challenger.get("prompt_id"):
        return []
    problems: list[str] = []
    for name in ("prompt_sha256", "prompt_template_sha256"):
        if not champion.get(name) or not challenger.get(name):
            problems.append(f"{name}: not recorded by both runs (re-run scripts/eval_generation.py)")
        elif champion[name] != challenger[name]:
            problems.append(f"{name}: same prompt id {champion.get('prompt_id')!r} but the prompt text differs")
    return problems


def request_hash_differences(
    champion_rows: Sequence[Mapping[str, Any]], challenger_rows: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Scored question ids whose request hash is missing or differs between the runs (call only when the prompt id
    and k are the same, so the requests must be byte-identical)."""
    challenger_hashes = {row["question_id"]: row.get("request_sha256") for row in challenger_rows if row.get("score")}
    different: list[str] = []
    for row in champion_rows:
        if not row.get("score"):
            continue
        question_id = row["question_id"]
        mine = row.get("request_sha256")
        if not mine or mine != challenger_hashes.get(question_id):
            different.append(question_id)
    return sorted(different)


def find_records(block: Mapping[str, Any], role: str) -> list[Mapping[str, Any]]:
    """The freeze records of one role in a run's freeze_proof ([] when none was recorded)."""
    proof = block.get("freeze_proof")
    if not isinstance(proof, list):
        return []
    return [record for record in proof if isinstance(record, Mapping) and record.get("role") == role]


def expected_sha256(frozen_inputs: Mapping[str, Any], role: str, path: str) -> str | None:
    """The hash the run loaded for an input, from its frozen_inputs block."""
    if role == "key_facts":
        return frozen_inputs.get("key_facts_sha256")
    if role == "frozen_hits":
        return frozen_inputs.get("frozen_hits_sha256")
    if role == "question_file":
        return (frozen_inputs.get("question_sets") or {}).get(path)
    return None


def run_freeze_checks(run: str, block: Mapping[str, Any], answers_from: str | None) -> list[dict[str, Any]]:
    """Freeze checks of one run's key facts, frozen hits and question files against its earliest answer time."""
    checks: list[dict[str, Any]] = []
    for role in FROZEN_INPUT_ROLES:
        records = find_records(block, role)
        if not records:
            checks.append(
                {"run": run, "role": role, "path": None, "ok": False, "detail": "no freeze proof recorded for it"}
            )
        for record in records:
            path = str(record.get("path"))
            if record.get("sha256") != expected_sha256(block.get("frozen_inputs") or {}, role, path):
                ok = False
                detail = f"{path}: the content at run start differs from the content the run loaded"
            else:
                ok, detail = committed_before(record, answers_from)
            checks.append({"run": run, "role": role, "path": path, "ok": ok, "detail": detail})
    return checks


def margins_freeze_check(
    challenger: Mapping[str, Any], answers_from: str | None, current_margins_sha256: str | None
) -> dict[str, Any]:
    """The margins (migration section) must be unchanged since the challenger run and committed before it."""
    records = find_records(challenger, "margins")
    if not records:
        return {"run": "challenger", "role": "margins", "path": None, "ok": False, "detail": "no freeze proof recorded"}
    record = records[0]
    path = str(record.get("path"))
    if record.get("sha256") != current_margins_sha256:
        detail = (
            f"{path}: the migration section changed since the challenger run "
            f"(recorded {record.get('sha256')}, now {current_margins_sha256})"
        )
        return {"run": "challenger", "role": "margins", "path": path, "ok": False, "detail": detail}
    ok, detail = committed_before(record, answers_from)
    return {"run": "challenger", "role": "margins", "path": path, "ok": ok, "detail": detail}


def freeze_summary(
    champion: Mapping[str, Any],
    challenger: Mapping[str, Any],
    champion_answers_from: str | None,
    challenger_answers_from: str | None,
    current_margins_sha256: str | None,
) -> dict[str, Any]:
    """Every freeze check, whether all passed, and where the margins come from."""
    margins = margins_freeze_check(challenger, challenger_answers_from, current_margins_sha256)
    checks = [
        *run_freeze_checks("champion", champion, champion_answers_from),
        *run_freeze_checks("challenger", challenger, challenger_answers_from),
        margins,
    ]
    margin_records = find_records(challenger, "margins")
    commit = (margin_records[0].get("first_committed") or {}) if margin_records else {}
    path = margin_records[0].get("path") if margin_records else "config/release_gate.yaml"
    return {
        "all_frozen": all(check["ok"] for check in checks),
        "checks": checks,
        "champion_answers_from": champion_answers_from,
        "challenger_answers_from": challenger_answers_from,
        "margins_frozen_before_challenger": margins["ok"],
        "margins_source": f"{path}@{commit.get('sha', 'uncommitted')}",
        "margins_section_sha256": current_margins_sha256,
        "margins_committed_at": commit.get("author_date"),
    }


def validate_reproducibility(
    reproducibility: Any, champion_id: str, champion: Mapping[str, Any], champion_answers: Mapping[str, str]
) -> Any:
    """The R5 block when it describes THIS champion run (same config, model digest and answers), else a note."""
    if not (isinstance(reproducibility, Mapping) and reproducibility.get("config") == champion_id):
        return "not measured for the champion (scripts/eval_generation.py --reproducibility 20)"
    model = reproducibility.get("model") or {}
    if model.get("digest") != champion.get("ollama_digest"):
        return "not measured for this champion run (R5 used a different model digest)"
    originals = reproducibility.get("original_answer_sha256") or {}
    if not originals:
        return "not measured for this champion run (the R5 block does not record which answers it compared)"
    for question_id, answer_sha in originals.items():
        if champion_answers.get(question_id) != answer_sha:
            return "not measured for this champion run (R5 compared answers from a different run)"
    return dict(reproducibility)


def compare_metric(
    metric: GatedMetric,
    champion_values: Mapping[str, float],
    challenger_values: Mapping[str, float],
    settings: MigrationSettings,
) -> dict[str, Any]:
    """The paired comparison of one metric on the questions both runs have in its population."""
    paired_ids = sorted(set(champion_values) & set(challenger_values))
    block: dict[str, Any] = {
        "direction": metric.direction,
        "population": metric.population,
        "margin": metric.margin,
        "n_paired": len(paired_ids),
    }
    if not paired_ids:
        block.update(
            {
                "champion": None,
                "challenger": None,
                "paired_diff": None,
                "decision": "INCONCLUSIVE",
                "note": "no question is in this metric's population for both runs",
            }
        )
        return block
    champion = [champion_values[question_id] for question_id in paired_ids]
    challenger = [challenger_values[question_id] for question_id in paired_ids]
    difference = paired_bootstrap_difference(
        challenger, champion, n_resamples=settings.resamples, seed=settings.seed, confidence=settings.confidence
    )
    ci_low = round(difference["ci_low"], 4)
    ci_high = round(difference["ci_high"], 4)
    block.update(
        {
            "champion": round(sum(champion) / len(champion), 4),
            "challenger": round(sum(challenger) / len(challenger), 4),
            "paired_diff": {"mean": round(difference["mean_difference"], 4), "ci95": [ci_low, ci_high]},
            "decision": non_inferiority_decision(ci_low, ci_high, metric.margin, metric.direction),
        }
    )
    return block


def compare_runs(
    champion_values: Mapping[str, Mapping[str, float]],
    challenger_values: Mapping[str, Mapping[str, float]],
    settings: MigrationSettings,
) -> tuple[dict[str, dict[str, Any]], Decision]:
    """Compare every gated metric. Inputs map metric name -> {question id: per-question value}."""
    results: dict[str, dict[str, Any]] = {}
    for metric in settings.metrics:
        results[metric.name] = compare_metric(
            metric, champion_values.get(metric.name, {}), challenger_values.get(metric.name, {}), settings
        )
    decision = overall_decision([block["decision"] for block in results.values()])
    return results, decision


def format_value(value: Any) -> str:
    """A table cell: numbers as given, None as 'n/a', lists joined."""
    if value is None:
        return "n/a"
    if isinstance(value, list):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    return str(value)


def config_lines(role: str, block: Mapping[str, Any]) -> list[str]:
    """The certificate lines that describe one configuration."""
    licenses = block.get("model_license_sources") or {}
    return [
        f"### {role}: `{block.get('config')}`",
        "",
        f"- Model: `{block.get('model')}` (provider {block.get('provider')})",
        f"- Ollama digest: `{block.get('ollama_digest')}`",
        f"- Ollama version: {block.get('ollama_version')}",
        f"- License (Docker Hub artifact): {licenses.get('docker_artifact', 'not recorded')}",
        f"- License (upstream model card): {licenses.get('upstream_card', 'not recorded')}",
        f"- Prompt: `{block.get('prompt_id')}` sha256 `{block.get('prompt_sha256')}`, template sha256 "
        f"`{block.get('prompt_template_sha256')}`",
        f"- k = {block.get('k')}, options {block.get('options')}",
        f"- Answers generated {block.get('earliest_answer_generated_at_utc')} to "
        f"{block.get('latest_answer_generated_at_utc')}; results written {block.get('generated_at_utc')} "
        f"(git {block.get('git_sha')}), "
        f"load average start {block.get('load_average', {}).get('start')} / end "
        f"{block.get('load_average', {}).get('end')}",
        f"- Per-question results: `{block.get('rows_path')}` sha256 `{block.get('rows_sha256')}`",
        "",
    ]


def render_certificate(report: Mapping[str, Any]) -> str:
    """The Markdown migration certificate, built only from the gate's JSON report."""
    champion = report["champion"]
    challenger = report["challenger"]
    frozen = report["frozen_inputs"]
    lines = [
        f"# LLM migration certificate: `{champion['config']}` -> `{challenger['config']}`",
        "",
        f"**Overall decision: {report['overall']}**",
        "",
        "Educational demonstration. Generated by scripts/llm_migration_gate.py from "
        f"`{report['report_path']}`; do not edit by hand.",
        "",
    ]
    freeze = report.get("freeze") or {}
    if not freeze.get("all_frozen", False):
        failed = [check["detail"] for check in freeze.get("checks", []) if not check["ok"]]
        lines += [
            "> **NOT VALID FOR RELEASE:** a frozen input or the margins were not committed before the answers were "
            "generated: " + "; ".join(failed or ["no freeze proof"]) + ".",
            "",
        ]
    lines += [
        "## Decision rule",
        "",
        "Paired percentile bootstrap of (challenger - champion) per question, "
        f"{report['bootstrap']['resamples']} resamples, seed {report['bootstrap']['seed']}, "
        f"{int(report['bootstrap']['confidence'] * 100)}% CI. Non-inferiority with margin m: higher-is-better "
        "metrics PASS if the CI lower bound >= -m and FAIL if the upper bound < -m; lower-is-better metrics PASS if "
        "the upper bound <= +m and FAIL if the lower bound > +m; anything else is INCONCLUSIVE, which blocks. "
        "Overall PASS only if every gated metric passes.",
        "",
        f"Margins: `{report['margins_source']}` (migration section sha256 `{report.get('margins_section_sha256')}`, "
        f"first committed {report.get('margins_committed_at')}; file sha256 `{report['margins_file_sha256']}`).",
        "",
        "## Gated metrics",
        "",
        "| Metric | Better | Population | n paired | Champion | Challenger | Diff (mean) | 95% CI | Margin "
        "| Decision |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, block in report["metrics"].items():
        diff = block.get("paired_diff") or {}
        better = "higher" if block["direction"] == "higher_is_better" else "lower"
        lines.append(
            f"| {name} | {better} | {block['population']} | {block['n_paired']} | {format_value(block['champion'])} "
            f"| {format_value(block['challenger'])} | {format_value(diff.get('mean'))} "
            f"| {format_value(diff.get('ci95'))} | {block['margin']} | {block['decision']} |"
        )
    lines += ["", "## Not gated (reported only)", ""]
    for role in ("champion", "challenger"):
        reported = report["not_gated"].get(role, {})
        lines.append(
            f"- {role}: latency p50 {reported.get('latency_p50_s')} s, p95 {reported.get('latency_p95_s')} s "
            f"(n live calls {reported.get('n_live_calls')}); mean tokens in {reported.get('tokens_in_mean')}, "
            f"out {reported.get('tokens_out_mean')}; load average at generation "
            f"{format_value(reported.get('load_average_at_generation'))}; errors {reported.get('errors')}"
        )
    reproducibility = report.get("reproducibility")
    lines += ["", "## Reproducibility (champion, cache off)", "", f"- {format_value(reproducibility)}", ""]
    lines += ["## Freeze checks", ""]
    for check in freeze.get("checks", []):
        lines.append(f"- {'ok' if check['ok'] else 'FAILED'} ({check['run']}, {check['role']}): {check['detail']}")
    lines += [
        "",
        f"- Stored per-question scores recomputed with the current scorer: {format_value(report.get('rescored'))}",
        f"- Request hashes compared: {format_value(report.get('request_hashes'))}",
        "",
    ]
    lines += ["## Configurations", ""]
    lines += config_lines("Champion", champion)
    lines += config_lines("Challenger", challenger)
    lines += [
        "## Frozen inputs (identical for both runs)",
        "",
        f"- Questions: {frozen.get('n_questions')} ids; question files {frozen.get('question_sets')}",
        f"- Key facts: sha256 `{frozen.get('key_facts_sha256')}`",
        f"- Frozen hits: sha256 `{frozen.get('frozen_hits_sha256')}` (retrieval mode {frozen.get('retrieval_mode')})",
        f"- Corpus checksum: `{frozen.get('corpus_checksum')}`",
        "",
        "## Provenance of this certificate",
        "",
        f"- Gate run at {report['provenance'].get('generated_at_utc')}, git {report['provenance'].get('git_sha')} "
        f"(dirty: {report['provenance'].get('git_dirty')})",
        f"- Load average at gate time: {report['provenance'].get('environment', {}).get('load_average_end')}",
        "",
        DISCLAIMER,
        "",
    ]
    return "\n".join(lines)

"""Lender Desk governance bullet (Prompt 8 template 6, rewritten to what was measured).

The template reads "Traced every LLM/tool call: ${cost} and {p95}s p95 per packet; release gate in CI blocks metric
regressions; deployed on {cloud} with {iac}." Only the release gate is backed by results here, so the bullet quotes
the gate's latest verdict and row counts (only a PASS verdict; FAIL and MISSING rows block the gate) and the drift
monitoring validation, and records why the rest is left out:
- cost and p95 per packet: no LLM call was made (agent_metrics.json and docintel_metrics.json say so) and the spans
  were created but not exported;
- deployment, cloud and IaC: deployment_metrics.json and web_metrics.json measured local containers only, which is
  not a deployment.
"""

from pathlib import Path
from typing import Any

from uwcopilot.resume.lender_platform import AGENT, DOCINTEL
from uwcopilot.resume.results import ResultsFolder, dig, missing_reason, number_at
from uwcopilot.resume.text import LENDER, Bullet, BulletDraft, Omission, compact, count

GOVERNANCE = "governance_metrics.json"
DEPLOYMENT = "deployment_metrics.json"
WEB = "web_metrics.json"
CI_WORKFLOW = Path(".github/workflows/ci.yml")
CI_GATE_COMMAND = "make release-gate"
LOCAL_TARGET_KIND = "local-container"
# The per-status row counts the release gate writes under release_gate.results.counts (governance/release_gate.py
# STATUSES), with the word printed for each.
GATE_STATUS_WORDS: dict[str, str] = {
    "PASS": "pass",
    "FAIL": "fail",
    "MISSING": "missing",
    "NOT_MEASURED": "not measured",
}
# Rows with these statuses make the gate's verdict FAIL (governance/release_gate.py BLOCKING_STATUSES).
BLOCKING_STATUSES: tuple[str, ...] = ("FAIL", "MISSING")
PASSING_VERDICT = "PASS"

Built = tuple[Bullet | None, list[Omission]]


def governance_bullet(results: ResultsFolder, repo_root: Path) -> Built:
    """The release gate's latest verdict and counts, and the drift monitor's validation on SIMULATED streams.

    The release gate is the core of this bullet. When its counts are missing, or its verdict is not PASS, or the
    counts show a blocking row under a PASS verdict, the whole bullet is left out and stderr says why.
    """
    draft = BulletDraft("governance", LENDER, "Model governance on public and SYNTHETIC data")
    governance = results.load(GOVERNANCE)
    gate = dig(governance, "release_gate", "results")
    counts = gate_counts(gate)
    if counts is None:
        keys = ("release_gate", "results", "counts") if isinstance(gate, dict) else ("release_gate", "results")
        return without_gate(draft, results, missing_reason(governance, GOVERNANCE, *keys))
    problem = verdict_problem(governance, counts)
    if problem is not None:
        return without_gate(draft, results, problem)
    workflow = repo_root / CI_WORKFLOW
    in_ci = workflow.exists() and CI_GATE_COMMAND in workflow.read_text(encoding="utf-8")
    where = "a release gate in the CI workflow" if in_ci else "a release gate (make release-gate)"
    draft.add(
        f"{where} compares {count(counts['rows'])} results rows with a reviewed baseline and fails on a regression "
        f"or a missing row (latest run: verdict {PASSING_VERDICT}, {status_counts_text(counts)})",
        GOVERNANCE,
    )
    add_monitoring(draft, governance)
    add_tracing_and_deployment_omissions(draft, results)
    return draft.finish()


def without_gate(draft: BulletDraft, results: ResultsFolder, reason: str) -> Built:
    """Leave the whole bullet out because its release-gate clause cannot be written, and say why."""
    draft.omit("release gate", reason, core=True)
    add_tracing_and_deployment_omissions(draft, results)
    return draft.finish()


def gate_counts(gate: Any) -> dict[str, float] | None:
    """{"rows": ..., "PASS": ..., "FAIL": ..., "MISSING": ..., "NOT_MEASURED": ...}, or None when any is missing."""
    counts: dict[str, float] = {}
    for name in ("rows", *GATE_STATUS_WORDS):
        value = number_at(gate, "counts", name)
        if value is None:
            return None
        counts[name] = value
    return counts


def status_counts_text(counts: dict[str, float]) -> str:
    """'11 pass, 0 fail, 0 missing, 4 not measured': the number of rows per status."""
    return ", ".join(f"{count(counts[status])} {word}" for status, word in GATE_STATUS_WORDS.items())


def verdict_problem(governance: dict[str, Any] | None, counts: dict[str, float]) -> str | None:
    """Why the release-gate clause cannot be written although the counts exist, or None when it can.

    Only a PASS verdict with no FAIL or MISSING row is quoted. A FAIL verdict is a measured result, but not one to
    put on a resume: a row regressed or required evidence is missing.
    """
    verdict = dig(governance, "release_gate", "results", "verdict")
    if not isinstance(verdict, str):
        return missing_reason(governance, GOVERNANCE, "release_gate", "results", "verdict")
    rows = status_counts_text(counts)
    if verdict != PASSING_VERDICT:
        return (
            f"results/{GOVERNANCE} says the release gate verdict is {verdict} (latest run: {rows}; FAIL and MISSING "
            "rows block the gate): not quoted until the gate passes"
        )
    blocking = sum(counts[status] for status in BLOCKING_STATUSES)
    if blocking > 0:
        return (
            f"results/{GOVERNANCE} says the release gate verdict is {verdict} but the latest run has {rows}; FAIL and "
            "MISSING rows block the gate, so the file is inconsistent: re-run make release-gate"
        )
    return None


def add_monitoring(draft: BulletDraft, governance: dict[str, Any] | None) -> None:
    """False alerts on no-drift streams and detection of injected claim-rate drift, as k of n streams."""
    monitoring = dig(governance, "monitoring", "results")
    validation = dig(monitoring, "validation")
    scenarios = dig(monitoring, "scenarios")
    false_alerts = dig(validation, "no_drift", "false_alert_rate")
    alerted, streams = number_at(false_alerts, "numerator"), number_at(false_alerts, "denominator")
    if not isinstance(scenarios, dict) or alerted is None or streams is None:
        draft.omit("drift monitoring", missing_reason(governance, GOVERNANCE, "monitoring", "results", "validation"))
        return
    pieces = [f"false alerts in {count(alerted)} of {count(streams)} no-drift streams"]
    claim_drifts = []
    for name, scenario in scenarios.items():
        multiplier = number_at(scenario, "claim_rate_multiplier")
        detected = dig(validation, name, "detection_rate")
        hits, total = number_at(detected, "numerator"), number_at(detected, "denominator")
        if multiplier is not None and multiplier != 1.0 and hits is not None and total is not None:
            claim_drifts.append((multiplier, hits, total))
    for multiplier, hits, total in sorted(claim_drifts, reverse=True):
        pieces.append(
            f"claim-rate multiplier {compact(multiplier)} detected in {count(hits)} of {count(total)} streams"
        )
    draft.add(f"drift monitoring checked on SIMULATED freMTPL2 streams: {', '.join(pieces)}", GOVERNANCE)


def add_tracing_and_deployment_omissions(draft: BulletDraft, results: ResultsFolder) -> None:
    """Record why the template's tracing, cost, p95 and deployment clauses are not written."""
    agent = results.load(AGENT)
    docintel = results.load(DOCINTEL)
    cost = dig(agent, "llm_dependent", "cost_usd_per_application")
    tracing = dig(agent, "configuration", "tracing")
    llm_calls = number_at(docintel, "rules", "cost", "llm_calls")
    draft.omit(
        "LLM/tool-call tracing with cost and p95 per packet",
        f"results/{AGENT} cost: {cost if isinstance(cost, str) else 'absent'}; tracing: "
        f"{tracing if isinstance(tracing, str) else 'absent'}; results/{DOCINTEL} LLM calls: "
        f"{count(llm_calls) if llm_calls is not None else 'absent'}",
    )
    draft.omit("deployment, cloud and IaC", deployment_reason(results))


def deployment_reason(results: ResultsFolder) -> str:
    """What deployment_metrics.json and web_metrics.json measured, and why that is not a deployment claim."""
    parts = []
    for name in (DEPLOYMENT, WEB):
        runs = dig(results.load(name), "runs")
        if not isinstance(runs, dict) or not runs:
            parts.append(f"results/{name}: no measured target")
            continue
        kinds = sorted({str(dig(run, "target", "kind")) for run in runs.values()})
        parts.append(f"results/{name}: targets {', '.join(kinds)}")
        other = [kind for kind in kinds if kind != LOCAL_TARGET_KIND]
        if other:
            parts.append(
                f"a non-local target is listed ({', '.join(other)}); write a line for it by hand only after checking it"
            )
    parts.append("a local container measurement is not a deployment, and no cloud or IaC run exists in results/")
    return "; ".join(parts)

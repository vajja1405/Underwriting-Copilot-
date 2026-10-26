"""Lender Desk governance bullet (Prompt 8 template 6, rewritten to what was measured).

The template reads "Traced every LLM/tool call: ${cost} and {p95}s p95 per packet; release gate in CI blocks metric
regressions; deployed on {cloud} with {iac}." Only the release gate is backed by results here, so the bullet quotes
the gate and the drift monitoring validation, and records why the rest is left out:
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

Built = tuple[Bullet | None, list[Omission]]


def governance_bullet(results: ResultsFolder, repo_root: Path) -> Built:
    """The release gate's latest verdict counts and the drift monitor's validation on SIMULATED streams."""
    draft = BulletDraft("governance", LENDER, "Model governance on public and SYNTHETIC data")
    governance = results.load(GOVERNANCE)
    gate = dig(governance, "release_gate", "results")
    counts = dig(gate, "counts")
    names = ("rows", "PASS", "FAIL", "NOT_MEASURED")
    values = {name: number_at(counts, name) for name in names}
    if any(value is None for value in values.values()):
        draft.omit("release gate", missing_reason(governance, GOVERNANCE, "release_gate", "results"), core=True)
        add_tracing_and_deployment_omissions(draft, results)
        return draft.finish()
    workflow = repo_root / CI_WORKFLOW
    in_ci = workflow.exists() and CI_GATE_COMMAND in workflow.read_text(encoding="utf-8")
    where = "a release gate in the CI workflow" if in_ci else "a release gate (make release-gate)"
    draft.add(
        f"{where} compares {count(values['rows'])} results rows with a reviewed baseline and fails on a regression "
        f"or a missing row (latest run: {count(values['PASS'])} pass, {count(values['FAIL'])} fail, "
        f"{count(values['NOT_MEASURED'])} not measured)",
        GOVERNANCE,
    )
    add_monitoring(draft, governance)
    add_tracing_and_deployment_omissions(draft, results)
    return draft.finish()


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

"""README headline table (Phase 8): one row per headline metric of each results file the README Status tables cite.

scripts/render_reports.py appends this table to the README block between <!-- RESULTS:START --> and
<!-- RESULTS:END -->, after the Phase 1, 2 and 4 rows it already writes from risk_metrics.json, docintel_metrics.json
and evidence_metrics.json (those rows are unchanged and not repeated here). Each row names the results file it
reads (the Source column) and the JSON paths it read (Headline.paths, used by the tests).

Every value is read from its results file at render time, with the interval that file records:
- "value [low, high]": the percentile bootstrap interval stored in the file;
- "value exact [low, high]": the exact Clopper-Pearson interval, used when the bootstrap interval has zero width or
  the file notes that it is too narrow, and the file records the exact one;
- "value [low, high] (k/n, Wilson)": a Wilson score interval, for files that report Wilson intervals;
- a value with no interval says so.

A metric that the file marks "not measured" prints "not measured (<the reason recorded in the file>)"; a missing file
prints "not measured (no results file)" and a missing entry "not measured (no `<path>` in the file)". An entry the
file records as null is not a missing entry: it prints the reason the file records next to it when the row names
one (Spec.null_reason), else "not measured (no value recorded)". Runs on a handful of cases (smoke runs and the
local-model narrative run) show counts only, never a rate.

Ground rule 1: nothing in this table is typed by hand. tests/governance/test_readme_headlines.py checks that every
number in a row appears in the row's source file, that no row reports a value the file marks "not measured", and
that rows from SYNTHETIC or SIMULATED data say so.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uwcopilot.governance.packs import NOT_MEASURED, integer, is_number, lookup, table
from uwcopilot.risk.results import is_not_measured

# The results files the table reads, by their path under results/ (the files the README Status tables cite, apart
# from the three that the rows above this table already read).
RAG = "rag_metrics.json"
KYC = "kyc_metrics.json"
AGENT = "agent_metrics.json"
MCP_STDIO = "mcp_smoke_stdio.json"
MCP_HTTP = "mcp_smoke_streamable_http.json"
PROVENANCE = "provenance_metrics.json"
INJECTION_DETECTOR = "injection/injection_metrics_detector_only.json"
INJECTION_SMOKE = "injection/smoke/injection_metrics_smoke.json"
GENERATION_SMOKE = "generation/smoke/generation_metrics_smoke.json"
GOVERNANCE = "governance_metrics.json"
STUDIO_COVERAGE = "studio_metro_coverage.json"
STUDIO_SITEIQ = "studio_siteiq_metrics.json"
STUDIO_FEASIBILITY = "studio_feasibility_metrics.json"
STUDIO_EVIDENCE = "studio_evidence_metrics.json"
STUDIO_EVIDENCE_SYNTHETIC = "studio_evidence_metrics_synthetic.json"
STUDIO_EVIDENCE_OLLAMA = "studio_evidence_metrics_ollama.json"
STUDIO_PATHS = "studio_path_metrics.json"
STUDIO_HANDOFF = "studio_handoff_metrics.json"
STUDIO_BUSINESS = "studio_business_metrics.json"
WEB = "web_metrics.json"
DEPLOYMENT = "deployment_metrics.json"
HEADLINE_FILES: tuple[str, ...] = (
    RAG,
    KYC,
    AGENT,
    MCP_STDIO,
    MCP_HTTP,
    PROVENANCE,
    INJECTION_DETECTOR,
    INJECTION_SMOKE,
    GENERATION_SMOKE,
    GOVERNANCE,
    STUDIO_COVERAGE,
    STUDIO_SITEIQ,
    STUDIO_FEASIBILITY,
    STUDIO_EVIDENCE,
    STUDIO_EVIDENCE_SYNTHETIC,
    STUDIO_EVIDENCE_OLLAMA,
    STUDIO_PATHS,
    STUDIO_HANDOFF,
    STUDIO_BUSINESS,
    WEB,
    DEPLOYMENT,
)

NO_FILE = "no results file"
NO_VALUE = "no value recorded"
NO_REASON = "no reason recorded"
HEADER: tuple[str, ...] = ("Component", "Metric", "Value [95% CI]", "Source")
# The keys a rate's point value can have, and the keys of its success count (each file uses its own names).
POINT_KEYS: tuple[str, ...] = ("value", "estimate", "mean", "rate")
SUCCESS_KEYS: tuple[str, ...] = ("numerator", "k", "successes", "detected", "flagged")

INTRO = (
    "**Headline metric per results file.** One row per headline metric of each results file that the Status tables "
    "cite (apart from the three files above), written by the same script (`src/uwcopilot/governance/"
    "readme_headlines.py`) from the file in the Source column. The interval is the 95% interval stored in that "
    'file: a percentile bootstrap interval unless the cell says otherwise; "exact" marks the Clopper-Pearson '
    "interval, shown when the bootstrap interval has zero width or the file notes it is too narrow, and "
    '"Wilson" a Wilson score interval, for the files that report one. A value without an interval has none in the '
    'file. A "not measured" row gives the reason recorded in the file. "Smoke only" and "counts only" rows come from '
    "runs on a handful of cases: quote no rate from them. SYNTHETIC and SIMULATED mark generated data and simulated "
    "runs; no row is a real-world, production or compliance claim. The agent, sanctions screening and retrieval rows "
    "repeat headline rows of the GenAI table below, which has the full breakdown."
)


@dataclass(frozen=True)
class Headline:
    """One table row: what was measured, the value as text, the results file it came from and the JSON paths read."""

    component: str
    metric: str
    value: str
    source: str
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class Spec:
    """How to read one row: the JSON paths in the results file, and a function that turns their values into text
    (it receives one argument per path, in order).

    null_reason is the path of a text in the same file that says why a value of the row may be null (for example
    "cost_per_package.llm_cost_basis" next to a null LLM cost); without it a null value prints NO_VALUE.
    """

    component: str
    metric: str
    paths: tuple[str, ...]
    show: Callable[..., str]
    null_reason: str | None = None


# ---------------------------------------------------------------------------
# Reading the results files
# ---------------------------------------------------------------------------


def load_sources(results_dir: Path) -> dict[str, Any]:
    """Every file of HEADLINE_FILES that exists under results_dir, parsed, keyed by its HEADLINE_FILES name."""
    sources: dict[str, Any] = {}
    for name in HEADLINE_FILES:
        path = Path(results_dir) / name
        if path.exists():
            sources[name] = json.loads(path.read_text(encoding="utf-8"))
    return sources


def short_reason(reason: str) -> str:
    """The first clause of a recorded reason (up to the first '; ' or '. '), so a table cell stays short.

    A cut that would leave a bracket open is not made.
    """
    text = reason.strip()
    for separator in ("; ", ". "):
        head = text.split(separator)[0]
        if head.count("(") == head.count(")"):
            text = head
    return text.rstrip(".").strip()


def not_measured_reason(value: Any) -> str | None:
    """The recorded reason when value marks a metric as not measured, else None.

    The results files use three layouts: {"status": "not measured", "reason": "..."} (risk/results.py), a text that
    starts with "not measured" ("not measured: no LLM key", "not measured (no LLM key)"), and a dict whose status is
    such a text (kyc_metrics.json, adverse_media_memo).
    """
    if is_not_measured(value):
        return short_reason(str(value.get("reason") or NO_REASON))
    if isinstance(value, Mapping) and isinstance(value.get("status"), str):
        value = value["status"]
    if not (isinstance(value, str) and value.startswith(NOT_MEASURED)):
        return None
    rest = value[len(NOT_MEASURED) :].strip().lstrip(":").strip()
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1]
    return short_reason(rest) if rest else NO_REASON


def not_measured(reason: str) -> str:
    """The value text of a metric that was not measured."""
    return f"{NOT_MEASURED} ({reason})"


def missing_path(path: str) -> str:
    """The reason given when a results file has no entry at path."""
    return f"no `{path}` in the file"


def has_entry(document: Any, path: str) -> bool:
    """True when the file has an entry at path, even one whose value is JSON null.

    lookup() returns None both for a missing entry and for a null value; this tells the two apart.
    """
    parent_path, _, key = path.rpartition(".")
    parent = lookup(document, parent_path) if parent_path else document
    if isinstance(parent, Mapping):
        return key in parent
    if isinstance(parent, list):
        return key.isdigit() and int(key) < len(parent)
    return False


def null_value_reason(document: Any, reason_path: str | None) -> str:
    """Why an entry the file records as null has no value: the first clause of the text at reason_path when the file
    has one there, else NO_VALUE."""
    text = lookup(document, reason_path) if reason_path else None
    if not isinstance(text, str) or not text.strip():
        return NO_VALUE
    return not_measured_reason(text) or short_reason(text)


# ---------------------------------------------------------------------------
# Formats (each receives values that exist and are not marked "not measured")
# ---------------------------------------------------------------------------


def _number_pair(low: Any, high: Any) -> tuple[float, float] | None:
    """(low, high) when both are numbers, else None."""
    return (low, high) if is_number(low) and is_number(high) else None


def _list_pair(bounds: Any) -> tuple[float, float] | None:
    """(low, high) from a two-item list such as "wilson_ci95": [low, high], else None."""
    if isinstance(bounds, list) and len(bounds) == 2:
        return _number_pair(bounds[0], bounds[1])
    return None


def bootstrap_bounds(entry: Mapping[str, Any]) -> tuple[float, float] | None:
    """The bootstrap interval of a rate in whichever layout its file uses (ci_low/ci_high, ci95_low/ci95_high or
    a "ci95" list), else None."""
    for low_key, high_key in (("ci_low", "ci_high"), ("ci95_low", "ci95_high")):
        bounds = _number_pair(entry.get(low_key), entry.get(high_key))
        if bounds is not None:
            return bounds
    return _list_pair(entry.get("ci95"))


def bootstrap_too_narrow(entry: Mapping[str, Any], bounds: tuple[float, float]) -> bool:
    """True when the bootstrap interval has zero width, or the file says it is degenerate or too narrow."""
    return (
        bounds[0] == bounds[1]
        or entry.get("bootstrap_degenerate") is True
        or bool(entry.get("bootstrap_interval_note"))
    )


def counts_text(entry: Mapping[str, Any]) -> str:
    """'k/n' from the count fields a rate records, 'n=N' when only the total is recorded, else ''."""
    total = entry.get("denominator", entry.get("n"))
    if not is_number(total):
        return ""
    for key in SUCCESS_KEYS:
        if is_number(entry.get(key)):
            return f"{integer(entry[key])}/{integer(total)}"
    return f"n={integer(total)}"


def interval_text(entry: Any, digits: int = 4) -> str:
    """A rate or mean with the 95% interval its file records, and its counts.

    Examples: '0.7538 [0.6462, 0.8462] (n=65)', '0.0033 exact [0.0001, 0.0184] (1/300)',
    '1.0000 [0.9977, 1.0000] (1,673/1,673, Wilson)', '1.0000 (n=60, zero-width bootstrap interval, no other
    interval recorded)'.
    """
    if not isinstance(entry, Mapping):
        return not_measured(NO_VALUE)
    point = next((entry[key] for key in POINT_KEYS if is_number(entry.get(key))), None)
    if point is None:
        return not_measured(NO_VALUE)

    def shown(value: float) -> str:
        return f"{value:.{digits}f}"

    notes = [counts_text(entry)]
    bounds = bootstrap_bounds(entry)
    exact = _number_pair(entry.get("exact_ci_low"), entry.get("exact_ci_high"))
    wilson = _list_pair(entry.get("wilson_ci95"))
    if bounds is not None and not bootstrap_too_narrow(entry, bounds):
        text = f"{shown(point)} [{shown(bounds[0])}, {shown(bounds[1])}]"
    elif exact is not None:
        text = f"{shown(point)} exact [{shown(exact[0])}, {shown(exact[1])}]"
    elif wilson is not None:
        text = f"{shown(point)} [{shown(wilson[0])}, {shown(wilson[1])}]"
        notes.append("Wilson")
    elif bounds is not None:
        text = shown(point)
        notes.append("zero-width bootstrap interval, no other interval recorded")
    else:
        text = shown(point)
        notes.append("no interval recorded")
    notes = [note for note in notes if note]
    return f"{text} ({', '.join(notes)})" if notes else text


def one_decimal_interval(entry: Any) -> str:
    """interval_text with one decimal (alerts per 1,000 customers)."""
    return interval_text(entry, digits=1)


def integer_text(value: Any) -> str:
    """A count, e.g. '0'."""
    return integer(value) if is_number(value) else not_measured(NO_VALUE)


def plain_text(value: Any) -> str:
    """A recorded label, e.g. 'keep_champion'."""
    return str(value)


def passed_text(items: Any, noun: str) -> str:
    """'17 checks, all passed', or '9 checks; failed: a, b', from a list of {"name", "passed"} records.

    The count is the length of the list in the file; a record that does not say passed=true counts as failed.
    """
    if not isinstance(items, list) or not items:
        return not_measured(NO_VALUE)
    failed = []
    for item in items:
        if isinstance(item, Mapping) and item.get("passed") is True:
            continue
        failed.append(str(item.get("name", "unnamed")) if isinstance(item, Mapping) else "unnamed")
    counted = f"{len(items)} {noun}" + ("" if len(items) == 1 else "s")
    if not failed:
        return f"{counted}, all passed"
    return f"{counted}; failed: {', '.join(failed)}"


def checks_text(checks: Any) -> str:
    """passed_text for smoke-test checks."""
    return passed_text(checks, "check")


def flows_text(flows: Any) -> str:
    """passed_text for end-to-end flows."""
    return passed_text(flows, "flow")


def counts_only(entry: Any) -> str:
    """'k/n' without a rate or an interval (for runs on a handful of cases)."""
    if not isinstance(entry, Mapping) or not counts_text(entry):
        return NO_VALUE
    return counts_text(entry)


# ---------------------------------------------------------------------------
# Building rows
# ---------------------------------------------------------------------------


def spec_row(sources: Mapping[str, Any], source: str, spec: Spec) -> Headline:
    """One row: 'not measured' with the reason when the file is missing, a path is missing, the file records a value
    as null or marks it not measured; otherwise spec.show applied to the values at spec.paths."""
    data = sources.get(source)
    if data is None:
        return Headline(spec.component, spec.metric, not_measured(NO_FILE), source, spec.paths)
    values = []
    for path in spec.paths:
        value = lookup(data, path)
        if value is None:
            if has_entry(data, path):
                reason = null_value_reason(data, spec.null_reason)
            else:
                reason = missing_path(path)
            return Headline(spec.component, spec.metric, not_measured(reason), source, spec.paths)
        reason = not_measured_reason(value)
        if reason is not None:
            return Headline(spec.component, spec.metric, not_measured(reason), source, spec.paths)
        values.append(value)
    return Headline(spec.component, spec.metric, spec.show(*values), source, spec.paths)


def spec_rows(sources: Mapping[str, Any], source: str, specs: Sequence[Spec]) -> list[Headline]:
    """spec_row for each spec of one results file."""
    return [spec_row(sources, source, spec) for spec in specs]


def one_path(component: str, metric: str, path: str, show: Callable[[Any], str] = interval_text) -> Spec:
    """A Spec that reads a single path (most rows)."""
    return Spec(component, metric, (path,), show)


# ---------------------------------------------------------------------------
# Lender Desk
# ---------------------------------------------------------------------------

RAG_COMPONENT = "RAG retrieval over SYNTHETIC guidelines and public regulations, hybrid + rerank, answerable questions"
RAG_SPECS: tuple[Spec, ...] = (
    one_path(RAG_COMPONENT, "Recall@5", "modes.hybrid_rerank.metrics.recall@5"),
    one_path(RAG_COMPONENT, "MRR@10", "modes.hybrid_rerank.metrics.mrr@10"),
    one_path("RAG answers written by an LLM", "Faithfulness", "generation.faithfulness"),
)

AGENT_COMPONENT = "Agent + human checkpoint, SYNTHETIC applications (template memo, no LLM)"
AGENT_SPECS: tuple[Spec, ...] = (
    one_path(
        AGENT_COMPONENT,
        "Required escalations that reached the right human queue",
        "deterministic_path.escalation.all_required_escalations",
    ),
    one_path(
        AGENT_COMPONENT,
        "End-to-end routing agreement with the designed labels",
        "deterministic_path.end_to_end_routing_agreement_with_designed_labels",
    ),
    one_path(
        AGENT_COMPONENT,
        "Decisions recorded without a human",
        "deterministic_path.decisions_recorded_without_a_human",
        integer_text,
    ),
    one_path("Agent memo written by an LLM", "Citation accuracy", "llm_dependent.citation_accuracy"),
)

MUTATIONS = "numeric_checker.mutation_test"
PROVENANCE_COMPONENT = "Numeric provenance checker (SIG), mutations of SYNTHETIC guideline sentences"
PROVENANCE_SPECS: tuple[Spec, ...] = (
    one_path(PROVENANCE_COMPONENT, "Changed values caught", f"{MUTATIONS}.recall_by_type.value_perturb"),
    one_path(PROVENANCE_COMPONENT, "Values scaled x10 caught", f"{MUTATIONS}.recall_by_type.magnitude_x10"),
    one_path(PROVENANCE_COMPONENT, "Unit swaps caught", f"{MUTATIONS}.recall_by_type.unit_swap"),
    one_path(PROVENANCE_COMPONENT, "Values from another section caught", f"{MUTATIONS}.recall_by_type.cross_section"),
    one_path(PROVENANCE_COMPONENT, "Comparator flips caught", f"{MUTATIONS}.recall_by_type.comparator_flip"),
    one_path(
        PROVENANCE_COMPONENT,
        "Values from another row of the same table caught (known blind spot)",
        f"{MUTATIONS}.recall_by_type.wrong_row",
    ),
    one_path(PROVENANCE_COMPONENT, "False flags on unmutated sentences", f"{MUTATIONS}.false_flag_rate.unmutated"),
    one_path(
        PROVENANCE_COMPONENT, "False flags on equivalent rewrites", f"{MUTATIONS}.false_flag_rate.equivalent_rewrite"
    ),
    one_path("Numeric provenance checker on memos", "Unsupported numbers in memos", "memo"),
)

INJECTION_SPECS: tuple[Spec, ...] = (
    one_path(
        "Prompt-injection tripwire (pattern detector, no model), frozen attack payloads",
        "Payloads flagged, raw text",
        "detector.recall_raw.all",
    ),
    one_path("Prompt-injection attack on a model (prompt layer L2)", "Attack success rate", "layers.L2"),
)


def kyc_specs(kyc: Any) -> tuple[Spec, ...]:
    """The selected operating point on the test split (the path depends on the method and budget the file selected),
    and the adverse-media memo."""
    selected = lookup(kyc, "selected_operating_point")
    selected = selected if isinstance(selected, Mapping) else {}
    method = selected.get("method")
    point = f"operating_points.{selected.get('alert_budget_per_1000')}.{method}.test"
    component = "Sanctions screening (OFAC SDN + OpenSanctions), SYNTHETIC test customers"
    if isinstance(method, str):
        component += f", {method.replace('_', ' ')}"
    return (
        one_path(component, "Recall of true matches at the selected operating point", f"{point}.recall"),
        one_path(component, "Alerts per 1,000 customers screened", f"{point}.alerts_per_1000", one_decimal_interval),
        one_path("Sanctions screening", "Adverse-media memo (needs an LLM)", "adverse_media_memo"),
    )


def smoke_text(status: Any, detail: str) -> str:
    """'smoke only: <detail>, no rate quoted' for a run its file labels a smoke run."""
    if status != "smoke":
        return f"status {status!r} in the file: no headline chosen for it yet"
    return f"smoke only: {detail}, no rate quoted"


def injection_smoke_text(status: Any, calls_made: Any, calls_planned: Any) -> str:
    """The prompt-injection smoke run: how many of the planned model calls ran."""
    return smoke_text(status, f"{integer(calls_made)} of {integer(calls_planned)} planned calls made")


def generation_smoke_text(status: Any, n_questions: Any) -> str:
    """The RAG answer smoke run: how many questions each model answered."""
    return smoke_text(status, f"{integer(n_questions)} questions per model")


SMOKE_SPECS: tuple[tuple[str, Spec], ...] = (
    (
        INJECTION_SMOKE,
        Spec(
            "Prompt-injection attack on a local Ollama model",
            "Attack success rate",
            ("status", "run.n_calls_made", "run.n_planned_calls"),
            injection_smoke_text,
        ),
    ),
    (
        GENERATION_SMOKE,
        Spec(
            "RAG answers by local Ollama models, SYNTHETIC questions",
            "Key-fact recall (a faithfulness proxy)",
            ("status", "frozen_inputs.n_questions"),
            generation_smoke_text,
        ),
    ),
)


def lender_desk_rows(sources: Mapping[str, Any]) -> list[Headline]:
    """Retrieval, sanctions screening, the agent, the MCP server, numeric provenance and prompt injection."""
    rows = spec_rows(sources, RAG, RAG_SPECS)
    rows += spec_rows(sources, KYC, kyc_specs(sources.get(KYC)))
    rows += spec_rows(sources, AGENT, AGENT_SPECS)
    rows += spec_rows(sources, MCP_STDIO, (one_path("MCP server, stdio", "Smoke-test checks", "checks", checks_text),))
    rows += spec_rows(
        sources, MCP_HTTP, (one_path("MCP server, streamable HTTP", "Smoke-test checks", "checks", checks_text),)
    )
    rows += spec_rows(sources, PROVENANCE, PROVENANCE_SPECS)
    rows += spec_rows(sources, INJECTION_DETECTOR, INJECTION_SPECS)
    for source, spec in SMOKE_SPECS:
        rows.append(spec_row(sources, source, spec))
    return rows


# ---------------------------------------------------------------------------
# Governance (results/governance_metrics.json)
# ---------------------------------------------------------------------------

MONITORING = "monitoring.results"
# (scenario of the monitoring validation, metric label, field read)
MONITORING_SCENARIOS: tuple[tuple[str, str, str], ...] = (
    ("no_drift", "Streams with a false alert", "false_alert_rate"),
    ("claim_rate_up_25pct", "Streams where the drift was detected", "detection_rate"),
    ("claim_rate_up_10pct", "Streams where the drift was detected", "detection_rate"),
)
CHAMPION_COMPONENT = "Champion/challenger, SYNTHETIC Lending Club-shaped loans"


def release_gate_text(results: Any) -> str:
    """'PASS (11 PASS, 0 FAIL, 0 MISSING, 4 NOT_MEASURED)': the gate's verdict and its rows by status."""
    if not isinstance(results, Mapping) or not isinstance(results.get("counts"), Mapping):
        return not_measured(NO_VALUE)
    counts = ", ".join(f"{integer(count)} {status}" for status, count in results["counts"].items() if status != "rows")
    return f"{results.get('verdict', 'no verdict recorded')} ({counts})"


def governance_specs(governance: Any) -> tuple[Spec, ...]:
    """Monitoring validation, champion/challenger, reviewer feedback, outcomes and the release gate."""
    specs = []
    for scenario, metric, field in MONITORING_SCENARIOS:
        base = f"{MONITORING}.validation.{scenario}"
        description = lookup(governance, f"{base}.description")
        # The scenario's own description, verbatim (it says which parts are SYNTHETIC and SIMULATED).
        scenario_text = description if isinstance(description, str) else f"scenario `{scenario}`"
        component = f"Drift monitor of a demonstration claim-frequency GLM: {scenario_text}"
        specs.append(one_path(component, metric, f"{base}.{field}"))
    specs += [
        one_path("Drift monitor of the credit model", "Drift and calibration checks", f"{MONITORING}.credit_model"),
        one_path(
            "Monitoring of LLM memos", "Unsupported-claim rate", f"{MONITORING}.llm_dependent.unsupported_claim_rate"
        ),
        one_path(
            CHAMPION_COMPONENT,
            "AUC, challenger minus champion (paired)",
            "champion_challenger.results.comparison.paired_difference_challenger_minus_champion.auc",
        ),
        one_path(
            CHAMPION_COMPONENT,
            "Recommendation (a model validator decides)",
            "champion_challenger.results.recommendation.recommendation",
            plain_text,
        ),
        one_path(
            "Reviewer feedback, SIMULATED reviewer actions on SYNTHETIC applications",
            "Override rate (describes the simulation)",
            "feedback.results.summary.override_rate",
        ),
        one_path(
            "Outcomes, SYNTHETIC outcomes joined to SIMULATED decisions",
            "Default rate of the approved loans with an outcome",
            "outcomes.results.summary.default_rate_by_decision.approve.default_rate",
        ),
        one_path(
            "Release gate over the SYNTHETIC and public-data results",
            "Verdict and gate rows by status",
            "release_gate.results",
            release_gate_text,
        ),
    ]
    return tuple(specs)


def governance_rows(sources: Mapping[str, Any]) -> list[Headline]:
    """The Phase 6 governance rows."""
    return spec_rows(sources, GOVERNANCE, governance_specs(sources.get(GOVERNANCE)))


# ---------------------------------------------------------------------------
# Founder Studio
# ---------------------------------------------------------------------------


def coverage_row(sources: Mapping[str, Any]) -> Headline:
    """The lowest business-type coverage of the real (PUBLIC) metros, against the full-mode threshold."""
    component = "Data fabric (Studio S1), PUBLIC data, the real metros"
    metric = "Lowest business-type data coverage per metro, against the full-mode threshold"
    data = sources.get(STUDIO_COVERAGE)
    if data is None:
        return Headline(component, metric, not_measured(NO_FILE), STUDIO_COVERAGE, ("summary",))
    summary = data.get("summary")
    real = [entry for entry in summary or [] if isinstance(entry, Mapping) and entry.get("data_label") == "PUBLIC"]
    paths = ("summary", *(f"metros.{entry.get('metro_id')}.coverage_threshold" for entry in real))
    shares = [entry["min_coverage_share"] for entry in real if is_number(entry.get("min_coverage_share"))]
    thresholds = {lookup(data, path) for path in paths[1:]}
    if not shares or len(shares) != len(real) or len(thresholds) != 1:
        return Headline(component, metric, not_measured(missing_path("summary")), STUDIO_COVERAGE, paths)
    threshold = thresholds.pop()
    if not is_number(threshold):
        return Headline(component, metric, not_measured(missing_path(paths[1])), STUDIO_COVERAGE, paths)
    modes: dict[str, list[str]] = {}
    for entry in real:
        modes.setdefault(str(entry.get("mode")), []).append(str(entry.get("metro_id")))
    if list(modes) == ["limited"]:
        mode_text = "every real metro is in limited mode"
    else:
        mode_text = "; ".join(f"{mode} mode: {', '.join(metros)}" for mode, metros in modes.items())
    value = f"{min(shares):.4f} to {max(shares):.4f} (threshold {threshold:g}); {mode_text}"
    return Headline(component, metric, value, STUDIO_COVERAGE, paths)


def siteiq_rows(sources: Mapping[str, Any]) -> list[Headline]:
    """SiteIQ minus the single-factor baseline, per business type (the rank correlation with the hidden demand)."""
    metric = "Spearman with the hidden true demand: SiteIQ minus the single-factor baseline"
    by_type = lookup(sources.get(STUDIO_SITEIQ), "by_business_type")
    if not isinstance(by_type, Mapping) or not by_type:
        missing = one_path("SiteIQ (Studio S2), SYNTHETIC cities", metric, "by_business_type")
        return [spec_row(sources, STUDIO_SITEIQ, missing)]
    specs = [
        one_path(
            f"SiteIQ (Studio S2), SYNTHETIC cities, {business_type.replace('_', ' ')}",
            metric,
            f"by_business_type.{business_type}.siteiq_minus_baseline_spearman",
        )
        for business_type in by_type
    ]
    return spec_rows(sources, STUDIO_SITEIQ, specs)


def brier_pair_text(simulator: Any, single_forecast: Any, n_worlds: Any) -> str:
    """'0.1405 [0.1321, 0.1493] vs 0.4796 [0.4596, 0.4992] (n=2,400 worlds)'."""
    return f"{interval_text(simulator)} vs {interval_text(single_forecast)} (n={integer(n_worlds)} worlds)"


def feasibility_specs(feasibility: Any) -> tuple[Spec, ...]:
    """The headline Brier comparison for the well-specified and the misspecified worlds."""
    headline = lookup(feasibility, "headline")
    entries = headline if isinstance(headline, list) else []
    specs = []
    for condition in ("well_specified", "misspecified"):
        index = next(
            (
                i
                for i, entry in enumerate(entries)
                if isinstance(entry, Mapping) and entry.get("condition") == condition
            ),
            None,
        )
        base = f"headline.{index}" if index is not None else f"headline.[{condition}]"
        specs.append(
            Spec(
                f"Feasibility simulator (Studio S3), SYNTHETIC worlds, {condition.replace('_', '-')}",
                "Brier score for P(year-2 DSCR meets the lender target), simulated: simulator vs a single-point "
                "forecast (lower is better)",
                (f"{base}.brier_dscr_year2_simulator", f"{base}.brier_dscr_year2_single_forecast", f"{base}.n_worlds"),
                brier_pair_text,
            )
        )
    return tuple(specs)


def local_model_text(fallback: Any, first_try: Any, after_retry: Any, provider: Any, model: Any, calls: Any) -> str:
    """Counts only for the narrative run of a local model (a handful of scenarios)."""
    return (
        f"counts only: {counts_only(fallback)} scenarios fell back to the template; drafts valid on the first try "
        f"{counts_only(first_try)}, after a retry {counts_only(after_retry)} ({provider} {model}, "
        f"{integer(calls)} calls)"
    )


NARRATIVE_METRIC = "Narrative numbers traced to the ledger (by construction for the template)"
STUDIO_EVIDENCE_SPECS: tuple[Spec, ...] = (
    one_path(
        "Founder narrative (Studio S4), template, SYNTHETIC golden scenarios",
        NARRATIVE_METRIC,
        "headline.narrative_numbers_traced_round_trip",
    ),
    one_path("Founder narrative written by an LLM", "Fallback to the template", "headline.llm_fallback_rate"),
)
STUDIO_EVIDENCE_SYNTHETIC_SPECS: tuple[Spec, ...] = (
    one_path(
        "Founder narrative (Studio S4), template, SYNTHETIC-city scenarios (after the review fixes)",
        NARRATIVE_METRIC,
        "headline.narrative_numbers_traced_round_trip",
    ),
    one_path(
        "Founder narrative (Studio S4), template, SYNTHETIC-city scenarios (after the review fixes)",
        "Wrong-slot mutations flagged by any of the three checks (known blind spot)",
        "checker.wrong_slot_blind_spot.any",
    ),
)
STUDIO_EVIDENCE_OLLAMA_SPECS: tuple[Spec, ...] = (
    Spec(
        "Founder narrative (Studio S4) by a local model, SYNTHETIC-city scenarios",
        "Drafts that passed the narrative grammar",
        (
            "llm.fallback_rate",
            "llm.first_try_valid_rate",
            "llm.valid_after_retry_rate",
            "llm.provider",
            "llm.model",
            "llm.llm_calls",
        ),
        local_model_text,
    ),
)
PATH_COMPONENT = "Path to approval (Studio S5), SYNTHETIC plans that needed a change"
STUDIO_PATH_SPECS: tuple[Spec, ...] = (
    one_path(PATH_COMPONENT, "Plans for which a one- or two-lever path was found", "headline.path_found_rate_full"),
    one_path(
        PATH_COMPONENT,
        "Plans with a path that also meets the SYNTHETIC startup program's equity and loan rules",
        "headline.path_found_and_program_compliant_rate_full",
    ),
)


def untraced_text(package_text: Any, pdf_text: Any, read_back: Any) -> str:
    """'0 in the package text and 0 in the PDF text, of 30,681 numbers read back'."""
    return (
        f"{integer(package_text)} in the package text and {integer(pdf_text)} in the PDF text, of "
        f"{integer(read_back)} numbers read back"
    )


GOLDEN = "golden_founder_scenarios"
HANDOFF_COMPONENT = "Lender-ready package and handoff (Studio S6), SYNTHETIC golden founder scenarios"
STUDIO_HANDOFF_SPECS: tuple[Spec, ...] = (
    Spec(
        HANDOFF_COMPONENT,
        "Printed numbers not traced to the ledger",
        (
            f"{GOLDEN}.untraced_numbers_package_text",
            f"{GOLDEN}.untraced_numbers_pdf_text",
            f"{GOLDEN}.package_numbers_read_back",
        ),
        untraced_text,
    ),
    one_path(
        HANDOFF_COMPONENT, "Packages that reached the agent's human checkpoint", f"{GOLDEN}.reached_human_checkpoint"
    ),
    one_path(
        HANDOFF_COMPONENT, "Decisions recorded without a human", f"{GOLDEN}.decisions_recorded_without_a_human_rate"
    ),
    one_path(
        "Handoff (Studio S6), SYNTHETIC startup applications (labels written against the same SYNTHETIC program)",
        "Recommendation agreement with the hand-written labels",
        "startup_applications.recommendation_agreement_with_labels",
    ),
)


def seconds_text(entry: Any) -> str:
    """A time in seconds with its interval, three decimals: '7.199 [6.767, 7.494] (n=36)'."""
    return interval_text(entry, digits=3)


def coverage_pair_text(studio: Any, baseline: Any) -> str:
    """'0.8482 [0.8388, 0.8587] (12,222/14,410) vs 0.0251 [0.0182, 0.0320] (33/1,316)'."""
    return f"{interval_text(studio)} vs {interval_text(baseline)}"


def founder_figures_text(studio: Any, baseline: Any, expected: Any) -> str:
    """The founder-figure pair and the result the file says was expected before measuring:
    '0.1315 [0.0958, 0.1693] (33/251) vs 0.1315 [0.0958, 0.1693] (33/251); expected equal by construction: ...'."""
    return f"{coverage_pair_text(studio, baseline)}; {expected}"


def site_scored_text(this_run: Any, note: Any) -> str:
    """Per metro, the scenarios whose package had a SiteIQ site score in this run, then the file's note:
    'synthetic_metro 6/6, chicago_il_in 0/6, ...; depends on which metro snapshots exist on the machine ...'."""
    if not isinstance(this_run, Mapping) or not this_run:
        return not_measured(NO_VALUE)
    counts = []
    for metro, observed in this_run.items():
        if not isinstance(observed, Mapping):
            return not_measured(NO_VALUE)
        scored = observed.get("site_scored")
        scenarios = observed.get("scenarios")
        if not (is_number(scored) and is_number(scenarios)):
            return not_measured(NO_VALUE)
        counts.append(f"{metro} {integer(scored)}/{integer(scenarios)}")
    return f"{', '.join(counts)}; {note}"


def llm_cost_text(cost: Any, basis: Any) -> str:
    """'0 USD (0 by construction: ...)', the recorded basis cut to its first clause."""
    return f"{integer(cost)} USD ({short_reason(str(basis))})"


BUSINESS_COMPONENT = "Founder Studio end to end (Studio S12), golden SYNTHETIC founder scenarios"
RULE_S = "headline.evidence_coverage_rule_s"
RULE_E = "headline.evidence_coverage_rule_e"
FOUNDER_FIGURES = "headline.founder_figures_backed_by_a_document"
SITE_SCORED = "data_coverage_by_metro.this_run"
# The LLM cost is null when a package's narrative was not the template; the basis next to it then says why.
LLM_COST_PATH = "cost_per_package.llm_cost_usd_per_package"
LLM_COST_BASIS_PATH = "cost_per_package.llm_cost_basis"
# docs/studio/s12_baseline.md, section 2: how B0 is described, and what every row comparing Studio with B0 must say
# (B0's sources follow from its definition). The rule S caveat is the "Reading" of metric M2 there.
B0_DESCRIPTION = (
    "baseline B0, a plan made without SiteIQ, the simulation and the ledger, built from the same SYNTHETIC inputs and "
    "documents (a construction, not a sample of real plans)"
)
B0_BY_DEFINITION = "B0's sources follow from its definition, not from how real founders write plans"
RULE_S_CAVEAT = (
    "Most of the gap follows from the definitions, not from how real founders write plans: model estimates and "
    "calculations count for Studio because its ledger records their run or formula and inputs, and B0 prints no such "
    "record. The rule E row removes that credit (it has its own: program and target sections), and the "
    "founder-figure row compares one population"
)
STUDIO_BUSINESS_SPECS: tuple[Spec, ...] = (
    one_path(
        BUSINESS_COMPONENT,
        "Seconds from inputs to an audited PDF package, median (machine-dependent wall clock, not a user time)",
        "headline.time_to_complete_package_median_s",
        seconds_text,
    ),
    one_path(
        BUSINESS_COMPONENT,
        "Scenarios with a complete package: an audited PDF, no untraced number in the package or PDF text, every "
        "page labelled",
        "time_to_complete_package.complete_packages",
    ),
    Spec(
        BUSINESS_COMPONENT,
        "Scenarios whose Studio package had a SiteIQ site score in this run, by metro (scored/scenarios)",
        (SITE_SCORED, "data_coverage_by_metro.this_run_note"),
        site_scored_text,
    ),
    Spec(
        BUSINESS_COMPONENT,
        f"Printed figures with a checkable source (rule S): Studio vs {B0_DESCRIPTION}. {RULE_S_CAVEAT}",
        (f"{RULE_S}.studio", f"{RULE_S}.baseline_b0"),
        coverage_pair_text,
    ),
    one_path(
        BUSINESS_COMPONENT,
        "Rule S share, Studio minus B0, paired over scenarios (most of the gap follows from the definitions, not "
        "from how real founders write plans)",
        f"{RULE_S}.difference",
    ),
    Spec(
        BUSINESS_COMPONENT,
        "Printed figures backed by a document page, a public-data row or a program or target section (rule E, "
        "stricter: model estimates, calculations, assumptions and founder statements do not count): Studio vs B0, "
        "whose only sources are the founder's documents. Program and target sections (the SYNTHETIC program's "
        "thresholds and the Studio's own targets) count for Studio, and B0 by definition prints none. "
        f"{B0_BY_DEFINITION}",
        (f"{RULE_E}.studio", f"{RULE_E}.baseline_b0"),
        coverage_pair_text,
    ),
    one_path(
        BUSINESS_COMPONENT,
        "Rule E share, Studio minus B0, paired over scenarios (it comes mostly from the program and target sections "
        f"B0 never prints; {B0_BY_DEFINITION})",
        f"{RULE_E}.difference",
    ),
    Spec(
        BUSINESS_COMPONENT,
        "The founder's typed plan and loan figures backed by a document, one population for both: Studio vs B0. "
        f"{B0_BY_DEFINITION}",
        (f"{FOUNDER_FIGURES}.studio", f"{FOUNDER_FIGURES}.baseline_b0", f"{FOUNDER_FIGURES}.expected"),
        founder_figures_text,
    ),
    Spec(
        BUSINESS_COMPONENT,
        "Language-model cost per package",
        (LLM_COST_PATH, LLM_COST_BASIS_PATH),
        llm_cost_text,
        null_reason=LLM_COST_BASIS_PATH,
    ),
    one_path(BUSINESS_COMPONENT, "Compute cost per package", "cost_per_package.compute_cost", plain_text),
    one_path("Founder Studio with real people", "Usability test", "usability_test", plain_text),
)


def studio_rows(sources: Mapping[str, Any]) -> list[Headline]:
    """Founder Studio S1 to S6 and the S12 end-to-end evaluation."""
    rows = [coverage_row(sources)]
    rows += siteiq_rows(sources)
    rows += spec_rows(sources, STUDIO_FEASIBILITY, feasibility_specs(sources.get(STUDIO_FEASIBILITY)))
    rows += spec_rows(sources, STUDIO_EVIDENCE, STUDIO_EVIDENCE_SPECS)
    rows += spec_rows(sources, STUDIO_EVIDENCE_SYNTHETIC, STUDIO_EVIDENCE_SYNTHETIC_SPECS)
    rows += spec_rows(sources, STUDIO_EVIDENCE_OLLAMA, STUDIO_EVIDENCE_OLLAMA_SPECS)
    rows += spec_rows(sources, STUDIO_PATHS, STUDIO_PATH_SPECS)
    rows += spec_rows(sources, STUDIO_HANDOFF, STUDIO_HANDOFF_SPECS)
    rows += spec_rows(sources, STUDIO_BUSINESS, STUDIO_BUSINESS_SPECS)
    return rows


# ---------------------------------------------------------------------------
# Web app and deployment
# ---------------------------------------------------------------------------


def scans_text(with_issue: Any, scans: Any) -> str:
    """'0 of 32 scans'."""
    return f"{integer(with_issue)} of {integer(scans)} scans"


def latency_text(p95_ms: Any, n: Any) -> str:
    """'5.73 (n=100, no interval recorded)': a nearest-rank percentile of one smoke run."""
    return f"{p95_ms:.2f} (n={integer(n)}, no interval recorded)"


LOCAL = "runs.local-container"
WEB_COMPONENT = "Web app on a LOCAL container (dev login, SYNTHETIC inputs; not the Space)"
WEB_SPECS: tuple[Spec, ...] = (
    one_path(WEB_COMPONENT, "Playwright end-to-end flows", f"{LOCAL}.e2e.flows", flows_text),
    Spec(
        WEB_COMPONENT,
        "axe-core scans with a serious or critical issue",
        (f"{LOCAL}.axe.totals.scans_with_serious_or_critical", f"{LOCAL}.axe.totals.scans"),
        scans_text,
    ),
    one_path("Web app on the Hugging Face Space", "Playwright end-to-end flows", "runs.hf-space.e2e.flows", flows_text),
)
DEPLOYMENT_COMPONENT = "Container smoke test on a LOCAL container (not the Space)"
DEPLOYMENT_SPECS: tuple[Spec, ...] = (
    one_path(DEPLOYMENT_COMPONENT, "Smoke checks", f"{LOCAL}.checks", checks_text),
    Spec(
        DEPLOYMENT_COMPONENT,
        "Request latency p95 over every path, one client (ms)",
        (f"{LOCAL}.latency_ms.all_paths.p95_ms", f"{LOCAL}.latency_ms.all_paths.n"),
        latency_text,
    ),
    one_path("Hugging Face Space deployment", "Smoke checks", "runs.hf-space.checks", checks_text),
)


def web_rows(sources: Mapping[str, Any]) -> list[Headline]:
    """The web app and the container."""
    return spec_rows(sources, WEB, WEB_SPECS) + spec_rows(sources, DEPLOYMENT, DEPLOYMENT_SPECS)


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

SECTIONS: tuple[tuple[str, Callable[[Mapping[str, Any]], list[Headline]]], ...] = (
    ("Lender Desk", lender_desk_rows),
    ("Governance", governance_rows),
    ("Founder Studio", studio_rows),
    ("Web app and deployment", web_rows),
)


def headline_rows(sources: Mapping[str, Any]) -> list[tuple[str, list[Headline]]]:
    """The rows of each section, in table order."""
    return [(title, build(sources)) for title, build in SECTIONS]


def source_link(source: str) -> str:
    """A Markdown link to the results file, e.g. '[kyc_metrics.json](results/kyc_metrics.json)'."""
    return f"[{source}](results/{source})"


def headline_section(sources: Mapping[str, Any]) -> list[str]:
    """The Markdown lines of the whole section: the introduction, then one table per section."""
    lines = [INTRO]
    for title, rows in headline_rows(sources):
        cells = [[row.component, row.metric, row.value, source_link(row.source)] for row in rows]
        lines += ["", f"**{title}**", "", *table(HEADER, cells)]
    return lines

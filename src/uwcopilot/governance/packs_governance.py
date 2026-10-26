"""The Phase 6 sections of results/governance_metrics.json as Markdown blocks for the governance packs (Phase 6 E).

Sections read: monitoring (scripts/monitor.py), outcomes and champion_challenger (scripts/champion_challenger.py),
release_gate (scripts/release_gate.py --write-results), lineage and feedback (scripts/feedback_report.py). A section
that has not been generated yet prints "not measured yet" and the make target that writes it; a key a producer stops
writing prints "not measured", never an error. registry_rules() states how model statuses change
(governance/model_status.py, security/rbac.py).
"""

from collections.abc import Mapping
from typing import Any

from uwcopilot.governance.packs import (
    GOVERNANCE,
    NOT_MEASURED,
    bullets,
    integer,
    interval,
    is_not_measured,
    is_number,
    lookup,
    missing_section_text,
    not_measured_text,
    number,
    section_results,
    share,
    source_note,
    table,
    text,
)


def _bounds(point: Any, pair: Any, digits: int = 4) -> str:
    """'point [low, high]' from a value and a two-item list, or 'not measured'. A zero-width interval is not shown
    as an interval (it would overstate the certainty)."""
    if is_number(point) and isinstance(pair, list) and len(pair) == 2 and all(is_number(item) for item in pair):
        if pair[0] == pair[1]:
            return f"{point:.{digits}f} (zero-width interval)"
        return f"{point:.{digits}f} [{pair[0]:.{digits}f}, {pair[1]:.{digits}f}]"
    return not_measured_text(point)


def _delay_not_available(summary: Mapping[str, Any]) -> bool:
    """True when the run recorded no delay because no stream was detected (monitoring_stream.delay_summary)."""
    delays = summary.get("detection_delay_windows")
    return isinstance(delays, Mapping) and delays.get("status") == "not available"


def _mean_delay(summary: Mapping[str, Any]) -> str:
    """Mean detection delay over the detected streams only, with how many streams were detected.

    The mean leaves out every stream that was never detected, so the count goes with it: '2.47 [1.94, 3.00] (49 of
    300 streams detected)'. A zero-width bootstrap interval (every detected stream had the same delay) is not shown
    as an interval. A delay recorded as "not available" is not applicable: the run was done and no stream was
    detected, so it is not "not measured".
    """
    delays = summary.get("detection_delay_windows")
    if _delay_not_available(summary):
        return f"not applicable ({text(delays.get('reason'))})"
    mean = lookup(delays, "mean")
    if not isinstance(mean, Mapping) or not all(is_number(mean.get(key)) for key in ("estimate", "ci_low", "ci_high")):
        return not_measured_text(mean if mean is not None else delays)
    n_detected = integer(lookup(delays, "n_detected_streams"))
    detected = f"{n_detected} of {integer(summary.get('n_streams'))} streams detected"
    if mean["ci_low"] == mean["ci_high"]:
        return f"{mean['estimate']:.2f} ({detected}; zero-width bootstrap interval)"
    return f"{interval(mean, 2)} ({detected})"


def _delay_counts(summary: Mapping[str, Any]) -> str:
    """Detected streams per delay, 'delay: streams' ('0: 11, ..., 5: 13'), so the streams caught only in the last
    drifted windows are visible next to the mean."""
    if _delay_not_available(summary):
        return "not applicable"
    counts = lookup(summary, "detection_delay_windows.streams_by_delay")
    if not isinstance(counts, Mapping) or not counts:
        return NOT_MEASURED
    return ", ".join(f"{text(delay)}: {integer(streams)}" for delay, streams in counts.items())


def _validation_intro(design: Mapping[str, Any]) -> str:
    """How the validation streams are built and what the delay columns cover (numbers from stream_design)."""
    return (
        f"Validation of the monitor on SIMULATED streams ({integer(design.get('n_streams_per_scenario'))} streams per "
        f"scenario, {integer(design.get('n_windows'))} windows each, numbered from 0; drift, if any, from window "
        f"{integer(design.get('drift_start_window'))} to the last window). Rate: [95% bootstrap CI over streams] "
        "(streams). Delay: windows from the start of the drift to the first alert, over the detected streams only. A "
        "delay cannot exceed the number of drifted windows minus one, and a stream with no alert by the last window "
        "is left out of the mean, so the mean delay is not a time to detection for every stream: read it with the "
        "detection rate and the delay counts."
    )


def _status_before(status: Mapping[str, Any]) -> str:
    """The registry status before the run; a recorded None means the run registered the model."""
    if "status_before_run" in status and status["status_before_run"] is None:
        return "not registered before the run"
    return text(status.get("status_before_run"))


def monitoring_block(sources: Mapping[str, Any]) -> list[str]:
    """The monitoring run: monitored model, validation of the monitor on SIMULATED streams, alerts and statuses."""
    note = source_note(sources, GOVERNANCE, "monitoring")
    result = section_results(sources, GOVERNANCE, "monitoring")
    if not isinstance(result, Mapping):
        return [f"Ongoing monitoring results: {missing_section_text(GOVERNANCE, 'monitoring')}.", "", note]
    model = result.get("monitored_model") or {}
    calibration = lookup(model, "held_out_calibration") or {}
    lines = [
        f"Data: {text(result.get('data_label'))}; {text(result.get('status'))}.",
        "",
        f"- Monitored model: `{text(model.get('model_id'))}`, {text(model.get('type'))}.",
        f"- Thresholds: `{text(lookup(result, 'config.path'))}` (SHA-256 `{text(lookup(result, 'config.sha256'))}`).",
        "- Observed / predicted claims on the whole held-out pool: "
        f"{_bounds(calibration.get('observed_to_predicted'), calibration.get('interval'))} "
        f"({text(calibration.get('interval_method'))}).",
        f"- No out-of-time stream: {text(lookup(result, 'stream_design.no_time_field'))}",
        "",
        _validation_intro(result.get("stream_design") or {}),
        "",
    ]
    rows = []
    validation = result.get("validation")
    for name, summary in validation.items() if isinstance(validation, Mapping) else []:
        drifted = summary.get("drifted") is True
        rate = summary.get("detection_rate") if drifted else summary.get("false_alert_rate")
        rows.append(
            [
                f"`{name}`",
                "yes" if drifted else "no",
                "detection rate" if drifted else "false alert rate",
                share(rate),
                _mean_delay(summary) if drifted else "not applicable",
                _delay_counts(summary) if drifted else "not applicable",
            ]
        )
    header = [
        "Scenario",
        "Drift injected",
        "Measure",
        "Rate [95% CI] (streams)",
        "Mean delay among detected streams, windows [95% CI] (detected of all streams)",
        "Detected streams by delay (windows: streams)",
    ]
    lines += table(header, rows or [[NOT_MEASURED] * len(header)])
    status = result.get("model_status") or {}
    lines += [
        "",
        f"- Demonstration stream `{text(lookup(result, 'demonstration_stream.scenario'))}`: "
        f"{integer(lookup(result, 'alerts.n_alerts_this_run'))} alert records this run (log "
        f"`{text(lookup(result, 'alerts.log'))}`); model status {_status_before(status)} -> "
        f"{text(status.get('status_after_run'))}.",
        f"- Credit model: {not_measured_text(result.get('credit_model'))}.",
        "",
        *indicator_table(result),
        "",
        note,
    ]
    return lines


def indicator_table(result: Mapping[str, Any]) -> list[str]:
    """The extraction, agent and reviewer indicators the monitor read from results files."""
    values = lookup(result, "indicators.values")
    rows = []
    for item in values if isinstance(values, list) else []:
        bounds = item.get("interval")
        shown = _bounds(item.get("value"), bounds) if bounds else number(item.get("value"))
        level = item.get("level")
        if level is None:  # monitoring_indicators.py: no level when the value is missing or the indicator has no rule
            level = "no rule (reported only)" if is_number(item.get("value")) else NOT_MEASURED
        rows.append([text(item.get("name")), text(item.get("component")), shown, text(level)])
    return [
        "Indicators read from the latest results files (single values, not a stream):",
        "",
        *table(["Indicator", "Component", "Value", "Level"], rows or [[NOT_MEASURED] * 4]),
    ]


def outcomes_block(sources: Mapping[str, Any]) -> list[str]:
    """Outcomes joined to decisions: default rate by decision and calibration of the recorded PD."""
    note = source_note(sources, GOVERNANCE, "outcomes")
    result = section_results(sources, GOVERNANCE, "outcomes")
    if not isinstance(result, Mapping):
        return [f"Outcomes analysis results: {missing_section_text(GOVERNANCE, 'outcomes')}.", "", note]
    window = result.get("window") or {}
    years = window.get("issue_years")
    summary = result.get("summary") or {}
    rows = []
    by_decision = summary.get("default_rate_by_decision")
    for decision, entry in by_decision.items() if isinstance(by_decision, Mapping) else []:
        rows.append(
            [
                f"`{decision}`",
                integer(entry.get("n_decisions")),
                integer(entry.get("n_with_outcome")),
                integer(entry.get("n_pending")),
                share(entry.get("default_rate")),
                number(entry.get("mean_recorded_pd_with_outcome")),
            ]
        )
    calibration = summary.get("recorded_pd_calibration") or {}
    approved = summary.get("approved_and_defaulted") or {}
    years_text = ", ".join(str(year) for year in years) if isinstance(years, list) and years else NOT_MEASURED
    return [
        f"Data: {text(result.get('data_label'))}. {text(result.get('interpretation'))}",
        "",
        f"- Window: issue years {years_text}; {integer(window.get('applications'))} decisions, "
        f"{integer(window.get('outcomes_arrived'))} outcomes arrived (join key "
        f"`{text(lookup(summary, 'join.key'))}`).",
        f"- Decision model: `{text(lookup(result, 'decision_model.model_id'))}`, decided by "
        f"{text(result.get('decided_by'))}.",
        "",
        *table(
            [
                "Decision",
                "Decisions",
                "With outcome",
                "Pending",
                "Default rate [95% CI] (defaults/with outcome)",
                "Mean recorded PD (with outcome)",
            ],
            rows or [[NOT_MEASURED] * 6],
        ),
        "",
        f"- Approved and defaulted: {integer(approved.get('n_approved_and_defaulted'))} of "
        f"{integer(approved.get('n_approved_with_outcome'))} approved decisions with an outcome.",
        calibration_text(calibration),
        *bullets(text(caveat) for caveat in result.get("caveats") or []),
        "",
        note,
    ]


def calibration_text(calibration: Any) -> str:
    """The recorded-PD calibration bullet: ECE and Brier with CIs and observed / expected, or why not measured."""
    if not isinstance(calibration, Mapping) or is_not_measured(calibration):
        return f"- Recorded PD calibration: {not_measured_text(calibration)}."
    return (
        f"- Recorded PD calibration: ECE {interval(calibration.get('ece'))}, Brier "
        f"{interval(calibration.get('brier'))}, observed / expected defaults "
        f"{number(calibration.get('observed_over_expected'))}."
    )


CHAMPION_METRICS = (
    ("auc", "AUC"),
    ("ks", "KS"),
    ("brier", "Brier (lower is better)"),
    ("ece", "ECE (lower is better)"),
)


def champion_challenger_block(sources: Mapping[str, Any]) -> list[str]:
    """Champion vs challenger on the same out-of-time rows, the recommendation and the registry entries."""
    note = source_note(sources, GOVERNANCE, "champion_challenger")
    result = section_results(sources, GOVERNANCE, "champion_challenger")
    if not isinstance(result, Mapping):
        return [f"Champion/challenger results: {missing_section_text(GOVERNANCE, 'champion_challenger')}.", "", note]
    comparison = result.get("comparison") or {}
    rows = [
        [
            label,
            interval(lookup(comparison, f"champion.{key}")),
            interval(lookup(comparison, f"challenger.{key}")),
            interval(lookup(comparison, f"paired_difference_challenger_minus_champion.{key}")),
        ]
        for key, label in CHAMPION_METRICS
    ]
    recommendation = result.get("recommendation") or {}
    models = lookup(result, "registry.models")
    registry_rows = [
        [role, f"`{text(entry.get('model_id'))}`", text(entry.get("status_in_registry"))]
        for role, entry in (models.items() if isinstance(models, Mapping) else [])
    ]
    return [
        f"Data: {text(result.get('data_label'))}. {text(result.get('interpretation'))}",
        "",
        f"- Champion: {text(lookup(result, 'windows.champion.rule'))}.",
        f"- Challenger: {text(lookup(result, 'windows.challenger.rule'))}.",
        f"- Both scored on: {text(lookup(result, 'windows.out_of_time.rule'))} "
        f"({integer(lookup(result, 'windows.out_of_time.n_rows'))} rows).",
        "",
        *table(
            ["Metric (out of time)", "Champion [95% CI]", "Challenger [95% CI]", "Challenger minus champion [95% CI]"],
            rows,
        ),
        "",
        f"- Recommendation: `{text(recommendation.get('recommendation'))}` ({text(recommendation.get('reason'))}). "
        f"Promoted by this run: {text(recommendation.get('promoted_by_this_run'))}. Who decides: "
        f"{text(recommendation.get('who_decides'))}.",
        "",
        *table(["Role in the comparison", "Model id", "Status in the registry"], registry_rows or [[NOT_MEASURED] * 3]),
        "",
        note,
    ]


def release_gate_block(sources: Mapping[str, Any]) -> list[str]:
    """The release gate's verdict and one line per gated row (status, compared number, current and baseline)."""
    note = source_note(sources, GOVERNANCE, "release_gate")
    result = section_results(sources, GOVERNANCE, "release_gate")
    if not isinstance(result, Mapping):
        return [f"Release gate results: {missing_section_text(GOVERNANCE, 'release_gate')}.", "", note]
    rows = []
    for row in result.get("rows") or []:
        compared = row.get("gate_on")
        rows.append(
            [
                f"`{text(row.get('id'))}`",
                text(row.get("status")),
                text(compared),
                number(lookup(row, f"current.{compared}.value")),
                number(lookup(row, f"baseline.{compared}.value")),
                text(row.get("reason")),
            ]
        )
    counts = result.get("counts") or {}
    count_text = ", ".join(f"{text(key)}: {integer(value)}" for key, value in counts.items()) or NOT_MEASURED
    return [
        f"- Verdict: **{text(result.get('verdict'))}** ({count_text}). Configuration: "
        f"`{text(lookup(result, 'config.file'))}`, section `{text(lookup(result, 'config.section'))}`; baseline made "
        f"at commit `{text(lookup(result, 'baseline.manifest_git_commit'))}`.",
        "",
        *table(
            ["Row", "Status", "Compared", "Current", "Baseline", "Reason"],
            rows or [[NOT_MEASURED] * 6],
        ),
        "",
        note,
    ]


def lineage_block(sources: Mapping[str, Any]) -> list[str]:
    """How many decision records carry a lineage block, and how many still match the working tree."""
    note = source_note(sources, GOVERNANCE, "lineage")
    result = section_results(sources, GOVERNANCE, "lineage")
    if not isinstance(result, Mapping):
        return [f"Lineage results: {missing_section_text(GOVERNANCE, 'lineage')}.", "", note]
    checks = result.get("checks") or {}
    by_status = checks.get("records_by_status")
    status_text = (
        ", ".join(f"{key}: {integer(value)}" for key, value in by_status.items())
        if isinstance(by_status, Mapping) and by_status
        else NOT_MEASURED
    )
    return [
        f"- Data: {text(result.get('data_label'))}; {text(result.get('status'))}.",
        f"- Records with a lineage block: {integer(checks.get('records_with_lineage'))} of "
        f"{integer(checks.get('records_read'))}; by status: {status_text}.",
        f"- {text(result.get('interpretation'))}",
        "",
        note,
    ]


def feedback_block(sources: Mapping[str, Any]) -> list[str]:
    """Reviewer accept / override statistics of the SIMULATED reviewer, with the reasons used."""
    note = source_note(sources, GOVERNANCE, "feedback")
    result = section_results(sources, GOVERNANCE, "feedback")
    if not isinstance(result, Mapping):
        return [f"Reviewer feedback results: {missing_section_text(GOVERNANCE, 'feedback')}.", "", note]
    summary = result.get("summary") or {}
    reasons = summary.get("reasons_on_overrides")
    reason_rows = [
        [f"`{code}`", integer(count)] for code, count in (reasons.items() if isinstance(reasons, Mapping) else [])
    ]
    return [
        f"Data: {text(result.get('data_label'))}. {text(result.get('interpretation'))}",
        "",
        f"- Decisions: {integer(summary.get('decisions'))}; overrides: {integer(summary.get('overrides'))}; override "
        f"rate {share(summary.get('override_rate'))}.",
        f"- Overrides without a reason code: {integer(summary.get('overrides_without_reason'))}.",
        f"- Definition: {text(summary.get('definition'))}.",
        "",
        *table(["Override reason code", "Overrides citing it"], reason_rows or [[NOT_MEASURED] * 2]),
        "",
        note,
    ]


def registry_rules() -> list[str]:
    """How the model status registry and promotion work (governance/model_status.py, security/rbac.py)."""
    return bullets(
        [
            "Model status registry (`governance/model_status.py`, file `data/governance/model_status.json`, "
            "gitignored): each model version is `champion` (in use), `challenger` (a candidate, never used for "
            "decisions), `under_review` (a monitoring alert fired) or `retired`.",
            "Nothing changes a status silently: every change is appended to the model's history with who made it, "
            "when and why.",
            "Monitoring may only mark a model `under_review`. Promoting a challenger and closing a review need a "
            "principal whose role may take the action `promote_model` in `security/rbac.py`: `model_validator` only. "
            "Anyone else gets a PermissionError and nothing is written. Roles are claimed in this demonstration "
            "(no identity provider), so this shows how authority is enforced once a role is known.",
            "Promoting a challenger makes it the champion of its family and retires the previous champion.",
        ]
    )

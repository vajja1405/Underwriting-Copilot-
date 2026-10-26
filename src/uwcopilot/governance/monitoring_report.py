"""The HTML monitoring report (results/monitoring_report.html), built only from the "monitoring" results payload.

Every number on the page comes from the payload that scripts/monitor.py writes to results/governance_metrics.json;
the report formats it (rounded for display) and never computes a new metric. The page is static: no JavaScript, a
Content-Security-Policy that allows only inline styles and data: images, and every value is HTML-escaped (the same
approach as evidence/record_views.render_html). Charts are small PNGs drawn with matplotlib and inlined as data URIs.
"""

import base64
import html
import io
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from matplotlib.figure import Figure
from PIL import Image

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
STYLE = (
    "body{font-family:system-ui,sans-serif;margin:24px;max-width:1200px;color:#1b1b1b;background:#fcfcfb}"
    "table{border-collapse:collapse;margin:8px 0 20px;font-size:13px}"
    "th,td{border:1px solid #c8c8c8;padding:4px 8px;text-align:left;vertical-align:top}"
    "th{background:#f0f0f0}.notice{background:#fff6dd;padding:8px;border:1px solid #e0c97a}"
    ".alert{font-weight:600}img{max-width:100%;height:auto}"
)
NOT_AVAILABLE = "not available"
# Categorical slots 1-3 of the project's validated chart palette, and its ink, reference and grid colours.
COLOR_SERIES = ("#2a78d6", "#eb6834", "#1baf7a")
COLOR_INK = "#0b0b0b"
COLOR_INK_SECONDARY = "#52514e"
COLOR_REFERENCE = "#8a8984"
COLOR_GRID = "#e4e3df"
COLOR_SURFACE = "#fcfcfb"
CHART_DPI = 100
PNG_COLORS = 64  # palette size of the inlined charts (lossy colour reduction; lines and text stay sharp)


def escape(value: Any) -> str:
    """Text that renders as itself in HTML (None becomes 'not available')."""
    return html.escape(NOT_AVAILABLE if value is None else str(value), quote=True)


def fmt(value: Any, digits: int = 4) -> str:
    """A number rounded for display; other values as text."""
    if value is None:
        return NOT_AVAILABLE
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def fmt_interval(interval: Sequence[float] | None, digits: int = 4) -> str:
    """'[low, high]' or 'not available'."""
    if not interval:
        return NOT_AVAILABLE
    return f"[{fmt(interval[0], digits)}, {fmt(interval[1], digits)}]"


def fmt_rate(rate: dict[str, Any] | None) -> str:
    """'0.0133 [0.0033, 0.0267] (4/300)' for a rate dict written by monitoring_stream.rate_with_ci.

    When the rate carries a bootstrap_interval_note (0, 1, n - 1 or n streams counted, so the bootstrap interval is
    zero-width or too narrow), the exact interval follows: '0.0033 [0.0000, 0.0100] (1/300); exact [0.0001, 0.0184]'.
    """
    if not rate or "estimate" not in rate:
        return NOT_AVAILABLE
    interval = fmt_interval([rate["ci_low"], rate["ci_high"]])
    text = f"{fmt(rate['estimate'])} {interval} ({rate['numerator']}/{rate['denominator']})"
    if rate.get("bootstrap_interval_note"):
        text += f"; exact {fmt_interval([rate.get('exact_ci_low'), rate.get('exact_ci_high')])}"
    return text


def fmt_delay(delay: dict[str, Any]) -> str:
    """Mean delay among detected streams with its CI, n and the cap, or 'not applicable (no stream detected)'.

    '2.47 [1.94, 3.00] (49 of 300 streams detected; 13 at the maximum of 5)'. A zero-width interval says so.
    """
    mean = delay.get("mean")
    if not mean:
        return "not applicable (no stream detected)"
    text = f"{fmt(mean['estimate'], 2)} {fmt_interval([mean['ci_low'], mean['ci_high']], 2)}"
    text += f" ({delay.get('n_detected_streams')} of {delay.get('n_streams')} streams detected"
    if delay.get("max_possible_delay") is not None:
        text += f"; {delay.get('streams_at_max_possible_delay')} at the maximum of {delay['max_possible_delay']}"
    text += ")"
    if mean["ci_low"] == mean["ci_high"]:
        text += "; zero-width bootstrap interval: every detected stream had the same delay"
    return text


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """An HTML table with every cell escaped."""
    head = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body = ["<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>" for row in rows]
    return "\n".join(["<table>", f"<tr>{head}</tr>", *body, "</table>"])


def paragraph(text: Any, css: str = "") -> str:
    """One escaped paragraph."""
    attribute = f' class="{css}"' if css else ""
    return f"<p{attribute}>{escape(text)}</p>"


def image(png: bytes, alt: str) -> str:
    """An inline PNG (data URI), with escaped alt text."""
    encoded = base64.b64encode(png).decode("ascii")
    return f'<img alt="{escape(alt)}" src="data:image/png;base64,{encoded}">'


# --------------------------------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------------------------------


def _new_axes(title: str, y_label: str) -> tuple[Figure, Any]:
    """A small figure with recessive axes and grid."""
    figure = Figure(figsize=(6.4, 2.8), dpi=CHART_DPI, facecolor=COLOR_SURFACE)
    axes = figure.add_subplot()
    axes.set_facecolor(COLOR_SURFACE)
    axes.set_title(title, fontsize=10, color=COLOR_INK, loc="left")
    axes.set_ylabel(y_label, fontsize=9, color=COLOR_INK_SECONDARY)
    axes.tick_params(labelsize=8, colors=COLOR_INK_SECONDARY)
    axes.grid(axis="y", color=COLOR_GRID, linewidth=0.8)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(COLOR_GRID)
    return figure, axes


def _png(figure: Figure) -> bytes:
    """The figure as a small PNG: drawn by matplotlib, then reduced to a 64-colour palette to keep the page light."""
    drawn = io.BytesIO()
    figure.tight_layout()
    figure.savefig(drawn, format="png", facecolor=COLOR_SURFACE)
    drawn.seek(0)
    with Image.open(drawn) as picture:
        # Fast octree keeps small marks in their exact colour (median cut merged a single orange dot into brown).
        palette = picture.convert("RGB").quantize(colors=PNG_COLORS, method=Image.Quantize.FASTOCTREE)
    small = io.BytesIO()
    palette.save(small, format="PNG", optimize=True)
    return small.getvalue()


def _check_values(window: dict[str, Any], check: str) -> dict[str, dict[str, Any]]:
    """The window's checks of one kind, keyed by subject."""
    return {item["subject"]: item for item in window["checks"] if item["check"] == check}


def _mark_drift_start(axes: Any, drift_start: int) -> None:
    """A vertical reference line where the injected drift starts."""
    axes.axvline(drift_start - 0.5, color=COLOR_REFERENCE, linewidth=1, linestyle=":")
    axes.text(drift_start - 0.4, axes.get_ylim()[1], "drift starts", fontsize=7, color=COLOR_INK_SECONDARY, va="top")


def psi_chart(windows: list[dict[str, Any]], drift_start: int, psi_rule: dict[str, Any]) -> bytes:
    """PSI per window: the feature that moved most by the last window, the score, and the largest other feature."""
    inputs = [_check_values(window, "input_drift") for window in windows]
    last = inputs[-1]
    top_feature = max(last, key=lambda subject: last[subject]["value"])
    x = [window["window"] for window in windows]
    scores = [next(iter(_check_values(window, "score_drift").values()))["value"] for window in windows]
    series = {
        f"{top_feature} (input)": [values[top_feature]["value"] for values in inputs],
        "predicted frequency (score)": scores,
        "largest other feature": [
            max(item["value"] for subject, item in values.items() if subject != top_feature) for values in inputs
        ],
    }
    figure, axes = _new_axes("PSI by window (demonstration stream, SIMULATED)", "PSI")
    for color, (label, values) in zip(COLOR_SERIES, series.items(), strict=True):
        axes.plot(x, values, color=color, linewidth=2, marker="o", markersize=4, label=label)
    for level, name in ((psi_rule["warn"], "warn"), (psi_rule["alert"], "alert")):
        axes.axhline(level, color=COLOR_REFERENCE, linewidth=1, linestyle="--")
        axes.text(x[0], level, f" {name} {level:g}", fontsize=7, color=COLOR_INK_SECONDARY, va="bottom")
    axes.set_xlabel("window", fontsize=9, color=COLOR_INK_SECONDARY)
    axes.set_ylim(bottom=0)
    _mark_drift_start(axes, drift_start)
    axes.legend(fontsize=7, frameon=False, loc="lower right")
    return _png(figure)


def calibration_chart(windows: list[dict[str, Any]], drift_start: int, band: tuple[float, float]) -> bytes:
    """Observed / predicted claims per window with its interval, against the tolerance band."""
    figure, axes = _new_axes("Observed / predicted claims by window (SIMULATED)", "observed / predicted")
    x, ratios, lows, highs = [], [], [], []
    for window in windows:
        ratio = _check_values(window, "calibration_ratio")["observed/predicted claims"]
        if ratio["value"] is None or not ratio["interval"]:
            continue
        x.append(window["window"])
        ratios.append(ratio["value"])
        lows.append(ratio["value"] - ratio["interval"][0])
        highs.append(ratio["interval"][1] - ratio["value"])
    axes.axhspan(band[0], band[1], color=COLOR_GRID, alpha=0.6, linewidth=0)
    axes.axhline(1.0, color=COLOR_REFERENCE, linewidth=1, linestyle="--")
    axes.errorbar(x, ratios, yerr=[lows, highs], fmt="o", color=COLOR_SERIES[0], markersize=5, capsize=3, linewidth=1.5)
    axes.set_xlabel("window (grey band: tolerance; bars: 95% interval)", fontsize=9, color=COLOR_INK_SECONDARY)
    _mark_drift_start(axes, drift_start)
    return _png(figure)


def validation_chart(validation: dict[str, dict[str, Any]]) -> bytes:
    """Share of streams with an alert, per scenario: false alerts (no drift) or detections (drifted), with CIs."""
    names = list(validation)
    figure = Figure(figsize=(6.4, 0.45 * len(names) + 1.0), dpi=CHART_DPI, facecolor=COLOR_SURFACE)
    axes = figure.add_subplot()
    axes.set_facecolor(COLOR_SURFACE)
    for position, name in enumerate(names):
        summary = validation[name]
        rate = summary["detection_rate"] if summary["drifted"] else summary["false_alert_rate"]
        color = COLOR_SERIES[0] if summary["drifted"] else COLOR_SERIES[1]
        axes.plot([rate["ci_low"], rate["ci_high"]], [position, position], color=color, linewidth=2)
        axes.plot([rate["estimate"]], [position], "o", color=color, markersize=6)
    labels = [f"{name} ({'detection' if validation[name]['drifted'] else 'false alert'})" for name in names]
    axes.set_yticks(range(len(names)), labels, fontsize=8, color=COLOR_INK)
    axes.invert_yaxis()
    axes.set_xlim(-0.02, 1.02)
    axes.set_xlabel("share of streams with an alert (95% bootstrap CI over streams)", fontsize=9)
    axes.tick_params(labelsize=8, colors=COLOR_INK_SECONDARY)
    axes.grid(axis="x", color=COLOR_GRID, linewidth=0.8)
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    axes.set_title("Monitor validation by SYNTHETIC scenario", fontsize=10, color=COLOR_INK, loc="left")
    return _png(figure)


# --------------------------------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------------------------------


def summary_section(payload: dict[str, Any], generated_at: datetime) -> list[str]:
    """Model, registry status, alerts and provenance."""
    model = payload["monitored_model"]
    status = payload["model_status"]
    calibration = model["held_out_calibration"]
    rows = [
        ["monitored model", f"{model['model_id']} (family {model['family']}, version {model['version']})"],
        ["model type", model["type"]],
        [
            "training split",
            f"{model['training_split']['n_train_policies']} policies, seed {model['training_split']['seed']}",
        ],
        ["held-out pool", f"{payload['stream_design']['pool_size']} policies ({payload['stream_design']['pool']})"],
        [
            "held-out observed / predicted claims",
            f"{fmt(calibration['observed_to_predicted'])} {fmt_interval(calibration['interval'])} "
            f"({calibration['observed_claims']} observed, {fmt(calibration['predicted_claims'], 1)} predicted)",
        ],
        ["model file SHA-256", model["cache"]["model_file_sha256"]],
        ["registry", f"{status['registry']}: {status['registration']}"],
        [
            "status before / after this run",
            f"{status.get('status_before_run') or 'not registered'} / {status.get('status_after_run')}",
        ],
        ["alerts this run", f"{payload['alerts']['n_alerts_this_run']} (run {payload['alerts']['run_id']})"],
        ["alert log", payload["alerts"]["log"]],
        ["config", f"{payload['config']['path']} (SHA-256 {payload['config']['sha256']})"],
        ["report generated (UTC)", generated_at.isoformat()],
    ]
    return ["<h2>Summary</h2>", table(["item", "value"], rows)]


def validation_section(payload: dict[str, Any]) -> list[str]:
    """False alert rate, detection rate and delay per scenario."""
    design = payload["stream_design"]
    bootstrap = payload["config"]["bootstrap"]
    start = design["drift_start_window"]
    max_delay = design["n_windows"] - 1 - start
    rows = []
    within_rows = []
    for name, summary in payload["validation"].items():
        if summary["drifted"]:
            delay = summary["detection_delay_windows"]
            by_check = ", ".join(
                f"{check} {fmt(rate['estimate'], 3)}" for check, rate in summary["detection_rate_by_check"].items()
            )
            rows.append(
                [
                    name,
                    summary["description"],
                    summary["n_streams"],
                    "detection rate",
                    fmt_rate(summary["detection_rate"]),
                    fmt_delay(delay),
                    fmt(delay.get("median"), 1) if delay.get("mean") else "not applicable",
                    fmt_rate(summary["pre_drift_false_alert_rate"]),
                    by_check,
                ]
            )
            within = delay.get("detected_within_windows", {})
            within_rows.append([name, *(fmt_rate(within.get(str(k))) for k in range(max_delay + 1))])
        else:
            by_check = ", ".join(
                f"{check} {fmt(rate['estimate'], 3)}" for check, rate in summary["false_alert_rate_by_check"].items()
            )
            warn_by_check = ", ".join(
                f"{check} {fmt(rate['estimate'], 3)}" for check, rate in summary["warn_rate_by_check"].items()
            )
            by_check += f" (warn or worse: {fmt_rate(summary['warn_rate'])}; by check: {warn_by_check})"
            rows.append(
                [
                    name,
                    summary["description"],
                    summary["n_streams"],
                    "false alert rate",
                    fmt_rate(summary["false_alert_rate"]),
                    "-",
                    "-",
                    "-",
                    by_check,
                ]
            )
    headers = [
        "scenario (SYNTHETIC)",
        "description",
        "streams",
        "rate",
        "estimate [95% CI] (streams)",
        f"mean delay among detected streams, windows [95% CI] (n detected; capped at {max_delay})",
        "median delay among detected streams",
        "pre-drift false alert rate",
        "share of streams alerted, by check",
    ]
    within_headers = ["scenario (SYNTHETIC)", *(f"delay <= {k} [95% CI] (streams)" for k in range(max_delay + 1))]
    return [
        "<h2>Validation of the monitor on SIMULATED streams</h2>",
        paragraph(
            f"{design['n_streams_per_scenario']} streams per scenario, {design['n_windows']} windows of "
            f"{design['window_size']} policies; drift (if any) from window {start}. "
            "A stream alerts when any check reaches level alert in any window. CIs: percentile bootstrap over "
            f"streams ({bootstrap['resamples']} resamples, seed {bootstrap['seed']}, {bootstrap['confidence']:.0%}). "
            "Where 0, 1, n-1 or n of the n streams count, that interval is zero-width or too narrow, so the exact "
            "(Clopper-Pearson) interval follows it ('exact [..]')."
        ),
        paragraph(
            f"Detection delay = first drifted window with an alert minus window {start} (0 = caught in the first "
            f"drifted window). The streams end at window {design['n_windows'] - 1}, so the delay is at most "
            f"{max_delay}: a stream that would alert later counts as not detected. The mean and median delay cover "
            "the detected streams only; the second table divides by all streams of the scenario."
        ),
        paragraph(design["no_time_field"]),
        image(validation_chart(payload["validation"]), "Share of streams with an alert per scenario"),
        table(headers, rows),
        f"<h3>Share of all streams with an alert within k windows of the drift start (delay &lt;= k, k = 0 to "
        f"{max_delay})</h3>",
        table(within_headers, within_rows),
    ]


def demonstration_section(payload: dict[str, Any]) -> list[str]:
    """Per-window values of the demonstration stream."""
    demonstration = payload["demonstration_stream"]
    windows = demonstration["windows"]
    thresholds = payload["config"]["thresholds"]
    drift_start = payload["stream_design"]["drift_start_window"]
    band = (thresholds["calibration_ratio"]["band_low"], thresholds["calibration_ratio"]["band_high"])
    rows = []
    for window in windows:
        inputs = _check_values(window, "input_drift")
        top = max(inputs, key=lambda subject: inputs[subject]["value"])
        score = next(iter(_check_values(window, "score_drift").values()))
        ratio = _check_values(window, "calibration_ratio")["observed/predicted claims"]
        ece = _check_values(window, "calibration_ece")["P(claim > 0)"]
        rows.append(
            [
                window["window"],
                fmt(window["drifted"]),
                window["n_policies"],
                f"{fmt(inputs[top]['value'])} ({top}, {inputs[top]['level']})",
                f"{fmt(score['value'])} ({score['level']})",
                window["observed_claims"],
                fmt(window["predicted_claims"], 1),
                f"{fmt(ratio['value'])} {fmt_interval(ratio['interval'])} ({ratio['level']})",
                f"{fmt(ece['value'])} ({ece['level']})",
                window["level"],
                ", ".join(window["alert_ids"]) or "-",
            ]
        )
    headers = [
        "window",
        "drifted",
        "policies",
        "largest input PSI",
        "score PSI",
        "observed claims",
        "predicted claims",
        "observed / predicted [95% interval]",
        "ECE of P(claim > 0)",
        "level",
        "alerts",
    ]
    features = list(_check_values(windows[0], "input_drift"))
    psi_rows = [
        [window["window"], *(fmt(_check_values(window, "input_drift")[feature]["value"]) for feature in features)]
        for window in windows
    ]
    return [
        f"<h2>Demonstration stream: {escape(demonstration['scenario'])} "
        f"(stream {escape(demonstration['stream'])})</h2>",
        paragraph(demonstration["note"]),
        image(psi_chart(windows, drift_start, thresholds["input_drift_psi"]["rule"]), "PSI by window"),
        image(calibration_chart(windows, drift_start, band), "Observed over predicted claims by window"),
        table(headers, rows),
        "<h3>Input PSI per feature</h3>",
        table(["window", *features], psi_rows),
    ]


def fmt_detail(detail: dict[str, Any] | None) -> str:
    """An indicator's detail as text: 'key: value; key: a 1, b 2' (a nested dict becomes 'name count' pairs)."""
    if not detail:
        return "-"
    parts = []
    for key, value in detail.items():
        if isinstance(value, dict):
            value = ", ".join(f"{name} {count}" for name, count in value.items()) or "none"
        parts.append(f"{key}: {fmt(value)}")
    return "; ".join(parts)


def indicators_section(payload: dict[str, Any]) -> list[str]:
    """Extraction, agent and reviewer indicators read from the results files (reviewer override reasons included)."""
    rows = [
        [
            item["component"],
            item["name"],
            item["status"],
            fmt(item["value"]),
            fmt_interval(item["interval"]),
            item["level"] or "-",
            item["rule"] or "-",
            item["source"],
            item["note"],
            fmt_detail(item.get("detail")),
        ]
        for item in payload["indicators"]["values"]
    ]
    sources = [[name, info["sha256"] or "file not present"] for name, info in payload["indicators"]["sources"].items()]
    headers = ["component", "indicator", "status", "value", "interval", "level", "rule", "source", "note", "detail"]
    return [
        "<h2>Extraction, agent and reviewer indicators (latest results files)</h2>",
        paragraph("Single values from the latest evaluation runs on SYNTHETIC data, not a stream."),
        table(headers, rows),
        table(["source file", "SHA-256 as read"], sources),
    ]


def rule_text(rule: dict[str, Any]) -> str:
    """A level rule from the payload in words, e.g. 'higher_is_worse: warn 0.1, alert 0.25'."""
    levels = [f"{name} {rule[name]:g}" for name in ("warn", "alert") if rule.get(name) is not None]
    return f"{rule['direction']}: {', '.join(levels)}"


def thresholds_section(payload: dict[str, Any]) -> list[str]:
    """Every rule from config/monitoring.yaml with its rationale (each one a design choice)."""
    thresholds = payload["config"]["thresholds"]
    rows = []
    for check in ("input_drift_psi", "score_drift_psi", "calibration_ece"):
        rule = thresholds[check]["rule"]
        rows.append([check, rule_text(rule), rule["rationale"]])
    ratio = thresholds["calibration_ratio"]
    rows.append(
        [
            "calibration_ratio",
            f"band [{ratio['band_low']}, {ratio['band_high']}], {ratio['confidence']} interval, "
            f"min {ratio['min_expected_claims']} predicted claims",
            ratio["rationale"],
        ]
    )
    for name, rule in thresholds["indicators"].items():
        rows.append([name, rule_text(rule), rule["rationale"]])
    return [
        "<h2>Thresholds (config/monitoring.yaml; every value is a design choice)</h2>",
        table(["check", "rule", "rationale"], rows),
    ]


def alerts_section(payload: dict[str, Any]) -> list[str]:
    """The alert records written by this run."""
    rows = [
        [
            record["alert_id"],
            record["component"],
            record["model_id"],
            record["check"],
            record["subject"],
            record["window"],
            fmt(record["value"]),
            record["rule"],
            record["action"],
        ]
        for record in payload["alerts"]["records"]
    ]
    headers = ["alert id", "component", "model", "check", "subject", "window", "value", "rule", "action"]
    body = table(headers, rows) if rows else paragraph("No breach in this run.")
    return ["<h2>Alerts written by this run</h2>", body]


def not_measured_section(payload: dict[str, Any]) -> list[str]:
    """What this run could not measure, and the limitations."""
    rows = [["Lending Club credit model", payload["credit_model"]["status"], payload["credit_model"]["reason"]]]
    for name, entry in payload["llm_dependent"].items():
        rows.append([name, entry["status"], entry["reason"]])
    limitations = "".join(f"<li>{escape(item)}</li>" for item in payload["limitations"])
    return [
        "<h2>Not measured</h2>",
        table(["what", "status", "reason"], rows),
        "<h2>Limitations</h2>",
        f"<ul>{limitations}</ul>",
    ]


def render_monitoring_report(payload: dict[str, Any], generated_at: datetime) -> str:
    """The report as one static HTML page."""
    title = "Model monitoring report (SYNTHETIC / SIMULATED)"
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        f"<title>{escape(title)}</title>",
        f"<style>{STYLE}</style>",
        "</head>",
        "<body>",
        f"<h1>{escape(title)}</h1>",
        paragraph(
            "Demonstration only. The monitored model is trained on PUBLIC freMTPL2 data; the stream is SIMULATED from "
            "its held-out split and every drift is SYNTHETIC (injected). Indicators come from evaluations on SYNTHETIC "
            "packets and applications. Nothing here describes a production system or a compliance programme.",
            css="notice",
        ),
        *summary_section(payload, generated_at),
        *validation_section(payload),
        *demonstration_section(payload),
        *indicators_section(payload),
        *thresholds_section(payload),
        *alerts_section(payload),
        *not_measured_section(payload),
        "</body>",
        "</html>",
    ]
    return "\n".join(lines) + "\n"

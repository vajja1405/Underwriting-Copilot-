"""Readable views of an evidence decision record: Markdown and static HTML (no JavaScript).

Both views are built from the same list of sections (title, paragraphs, tables) and differ only in how a cell is
escaped and laid out. EVERY value is escaped, whatever its source: contradiction messages, evidence values and
request params can carry document text (names, embedded instructions), which is untrusted data and must never become
markup, a link or a script.
- Markdown: &, < and > become HTML entities (so no raw HTML survives), Markdown punctuation that can start markup
  (backslash, backtick, *, _, [, ], #, !, |, ~) is backslash-escaped, and line breaks collapse to spaces (one value
  stays in one table cell). No text can become a link: GitHub-flavoured Markdown auto-links three shapes, and each
  is broken with an HTML entity: "://" (scheme URLs), "@" (email addresses and mailto:) and the dot after "www"
  (bare www. hosts, in any letter case).
- HTML: html.escape with quotes, a Content-Security-Policy meta tag that forbids scripts, and inline CSS only.
"""

import html
import re
from dataclasses import dataclass, field
from typing import Any

from uwcopilot.provenance.renderer import format_value

MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]#!|~])")
URL_SCHEME = "://"
URL_SCHEME_ENTITY = ":&#47;&#47;"
AT_SIGN_ENTITY = "&#64;"
WWW_DOT = re.compile(r"(www)\.", re.IGNORECASE)
NOT_RECORDED = "not yet recorded"
CSP = "default-src 'none'; style-src 'unsafe-inline'"
STYLE = (
    "body{font-family:system-ui,sans-serif;margin:24px;max-width:1200px;color:#1b1b1b}"
    "table{border-collapse:collapse;margin:8px 0 20px;font-size:13px}"
    "th,td{border:1px solid #c8c8c8;padding:4px 8px;text-align:left;vertical-align:top}"
    "th{background:#f0f0f0}.notice{background:#fff6dd;padding:8px;border:1px solid #e0c97a}"
)


@dataclass
class Table:
    """A table: header names and rows of cell values (any value; it is turned into text and escaped)."""

    headers: list[str]
    rows: list[list[Any]] = field(default_factory=list)


@dataclass
class Section:
    """A titled block of paragraphs and tables."""

    title: str
    paragraphs: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)


# --------------------------------------------------------------------------------------------------------------
# Escaping
# --------------------------------------------------------------------------------------------------------------


def as_text(value: Any) -> str:
    """A cell value as one line of text ("" for None; lists joined with commas)."""
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return ", ".join(as_text(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}={as_text(item)}" for key, item in value.items())
    return " ".join(str(value).split())


def escape_markdown(value: Any) -> str:
    """Text that renders as itself in Markdown (see the module docstring)."""
    text = html.escape(as_text(value), quote=False)
    text = MARKDOWN_SPECIAL.sub(r"\\\1", text)
    text = text.replace(URL_SCHEME, URL_SCHEME_ENTITY).replace("@", AT_SIGN_ENTITY)
    return WWW_DOT.sub(r"\1&#46;", text)


def escape_html(value: Any) -> str:
    """Text that renders as itself in HTML."""
    return html.escape(as_text(value), quote=True)


# --------------------------------------------------------------------------------------------------------------
# Sections of a record
# --------------------------------------------------------------------------------------------------------------


def summary_section(record: dict[str, Any]) -> Section:
    """Ids, hashes, versions and the reference recommendation."""
    versions = record.get("versions", {})
    rows = [
        ["record id", record.get("record_id")],
        ["supersedes record", record.get("supersedes_record_id") or "none"],
        ["correction reason", record.get("correction_reason") or "none"],
        ["case", record.get("case_id")],
        ["line", record.get("line")],
        ["data label", record.get("data_label")],
        ["recorded at", record.get("recorded_at")],
        ["packet sha256", record.get("packet_sha256")],
        ["inputs sha256", record.get("inputs_sha256")],
        ["reference recommendation", record.get("recommendation") or "not evaluated"],
        ["referral targets", record.get("referral_targets") or "none"],
        ["blocking finding open", record.get("blocking_open")],
    ]
    if record.get("not_evaluable_reason"):
        rows.append(["not evaluable", record["not_evaluable_reason"]])
    rows += [[f"version: {name}", value] for name, value in versions.items()]
    return Section(title="Summary", paragraphs=[record.get("notice", "")], tables=[Table(["field", "value"], rows)])


def findings_section(record: dict[str, Any]) -> Section:
    """The reference policy's findings (the reasons for its recommendation)."""
    policy = record.get("policy_result") or {}
    table = Table(["rule", "outcome", "triggers", "sections", "blocking", "detail"])
    for finding in policy.get("findings", []):
        table.rows.append(
            [
                finding["rule"],
                finding["outcome"],
                finding.get("trigger_ids"),
                finding.get("section_ids"),
                finding.get("blocking"),
                finding.get("detail"),
            ]
        )
    paragraphs = [] if table.rows else ["No findings."]
    return Section(title="Reference policy findings", paragraphs=paragraphs, tables=[table] if table.rows else [])


def evidence_requests_section(record: dict[str, Any]) -> Section:
    """Next-best evidence (ranked) and the open items."""
    ranked = Table(["rank", "request", "params", "VOI", "P(change)", "cost weight", "items", "reason"])
    for request in record.get("next_best_evidence", []):
        ranked.rows.append(
            [
                request["rank"],
                request["request_id"],
                request.get("params"),
                f"{request['voi']:.4f}",
                f"{request['p_change']:.2f}",
                request["cost_weight"],
                request.get("item_ids"),
                request.get("reason"),
            ]
        )
    items = Table(["item", "problem", "status", "request", "evidence", "detail"])
    for item in record.get("open_items", []):
        items.rows.append(
            [
                item["item_id"],
                item["problem_type"],
                item["status"],
                item.get("request_id") or "no catalog request",
                item.get("evidence_ids"),
                item.get("detail"),
            ]
        )
    note = (
        "VOI is a rules-based, scenario-based value-of-information score under documented priors and cost weights "
        "(planning assumptions), not an optimal or economic model."
    )
    paragraphs = [note] if ranked.rows or items.rows else ["No open items."]
    return Section(
        title="Next-best evidence", paragraphs=paragraphs, tables=[table for table in (ranked, items) if table.rows]
    )


def scenarios_section(record: dict[str, Any]) -> Section:
    """Stress tests, counterfactuals and decision stability."""
    table = Table(["scenario", "status", "metric", "baseline", "scenario value", "recommendation", "drivers", "notes"])
    for item in record.get("scenarios", []):
        drivers = [f"+{trigger}" for trigger in item.get("triggers_added", [])]
        drivers += [f"-{trigger}" for trigger in item.get("triggers_removed", [])]
        table.rows.append(
            [
                item["label"],
                item["status"],
                item.get("metric_name"),
                item.get("baseline_metric"),
                item.get("scenario_metric"),
                item.get("recommendation") or "not evaluated",
                drivers or "none",
                item.get("reason") or item.get("notes"),
            ]
        )
    stability = record.get("decision_stability") or {}
    paragraphs = [f"Decision stability: {stability.get('summary', 'not evaluated')}."]
    if record.get("scenarios"):
        paragraphs.append(f"Risk band and SHAP drivers: {record['scenarios'][0].get('drivers_note', '')}")
    return Section(title="Scenarios", paragraphs=paragraphs, tables=[table] if table.rows else [])


def ledger_section(record: dict[str, Any]) -> Section:
    """Every ledger claim with its status and provenance note."""
    ledger = record.get("evidence_ledger") or {}
    table = Table(["claim", "label", "value", "status", "provenance"])
    for claim in ledger.get("claims", []):
        value = format_value(claim.get("value"), claim.get("display", "text"))
        source = claim.get("provenance", {})
        note = f"{source.get('kind')} {source.get('ref')}"
        if claim.get("reason"):
            note += f"; reason: {claim['reason']}"
        if claim.get("conflict_ids"):
            note += f"; conflicts: {', '.join(claim['conflict_ids'])}"
        table.rows.append([claim["id"], claim["label"], value, claim["status"], note])
    paragraphs = [] if table.rows else ["No ledger (the case could not be evaluated)."]
    return Section(title="Evidence ledger", paragraphs=paragraphs, tables=[table] if table.rows else [])


def contradictions_section(record: dict[str, Any]) -> Section:
    """Contradictions with their candidate explanations and the cited values (document text, escaped)."""
    table = Table(["id", "type", "explanation", "documents", "message", "cited values"])
    for item in record.get("contradictions", []):
        cited = [f"{side.get('doc_id')} p{side.get('page')}: {as_text(side.get('value'))}" for side in item["sides"]]
        table.rows.append(
            [
                item["contradiction_id"],
                item["type"],
                f"{item['explanation']} ({item['explanation_code']})",
                item.get("doc_ids"),
                item.get("message"),
                cited,
            ]
        )
    paragraphs = ["Document text below is untrusted data, shown escaped."] if table.rows else ["No contradictions."]
    return Section(title="Contradictions", paragraphs=paragraphs, tables=[table] if table.rows else [])


def calculations_section(record: dict[str, Any]) -> Section:
    """The policy's deterministic calculations and their inputs."""
    table = Table(["calculation", "label", "value", "unit", "inputs"])
    for item in record.get("calculations", []):
        table.rows.append([item["calc_id"], item["label"], item["value"], item["unit"], item.get("inputs")])
    return Section(title="Calculations", tables=[table] if table.rows else [])


def flattened(value: Any, prefix: str = "") -> list[list[Any]]:
    """[name, value] rows of a nested dict (names joined with " / "); a list of dicts is one row per item."""
    if isinstance(value, dict):
        rows: list[list[Any]] = []
        for key, item in value.items():
            rows += flattened(item, f"{prefix} / {key}" if prefix else str(key))
        return rows
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        return [[f"{prefix} [{index}]", item] for index, item in enumerate(value, start=1)]
    return [[prefix, value]]


def supplementary_section(record: dict[str, Any]) -> Section:
    """Display-only views (evidence/supplementary.py): not used by the policy and not citable in a memo."""
    views = record.get("supplementary_calculations") or {}
    rows = flattened(views)
    paragraphs = [] if rows else ["None for this case."]
    return Section(
        title="Supplementary calculations (display only)",
        paragraphs=paragraphs,
        tables=[Table(["view", "value"], rows)] if rows else [],
    )


def reviewer_section(record: dict[str, Any]) -> Section:
    """The reviewer action fields (empty until a person acts; an action is recorded in a superseding record)."""
    reviewer = record.get("reviewer") or {}
    rows = [[name, value if value is not None else NOT_RECORDED] for name, value in reviewer.items()]
    return Section(title="Reviewer action", tables=[Table(["field", "value"], rows)])


def record_sections(record: dict[str, Any]) -> list[Section]:
    """The sections of a record, in reading order."""
    return [
        summary_section(record),
        findings_section(record),
        evidence_requests_section(record),
        scenarios_section(record),
        ledger_section(record),
        contradictions_section(record),
        calculations_section(record),
        supplementary_section(record),
        reviewer_section(record),
    ]


# --------------------------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------------------------


def markdown_table(table: Table) -> list[str]:
    """A Markdown table with every cell escaped."""
    lines = [
        "| " + " | ".join(escape_markdown(header) for header in table.headers) + " |",
        "|" + "---|" * len(table.headers),
    ]
    for row in table.rows:
        lines.append("| " + " | ".join(escape_markdown(cell) for cell in row) + " |")
    return lines


def render_markdown(record: dict[str, Any]) -> str:
    """The record as Markdown (every value escaped)."""
    lines = [f"# Evidence decision record {escape_markdown(record.get('record_id'))}", ""]
    for section in record_sections(record):
        lines += [f"## {escape_markdown(section.title)}", ""]
        for paragraph in section.paragraphs:
            lines += [escape_markdown(paragraph), ""]
        for table in section.tables:
            lines += [*markdown_table(table), ""]
    return "\n".join(lines).rstrip() + "\n"


def html_table(table: Table) -> list[str]:
    """An HTML table with every cell escaped."""
    head = "".join(f"<th>{escape_html(header)}</th>" for header in table.headers)
    lines = ["<table>", f"<tr>{head}</tr>"]
    for row in table.rows:
        lines.append("<tr>" + "".join(f"<td>{escape_html(cell)}</td>" for cell in row) + "</tr>")
    lines.append("</table>")
    return lines


def render_html(record: dict[str, Any]) -> str:
    """The record as a static HTML page: no JavaScript, a CSP that forbids scripts, every value escaped."""
    title = f"Evidence decision record {escape_html(record.get('record_id'))}"
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">',
        f"<title>{title}</title>",
        f"<style>{STYLE}</style>",
        "</head>",
        "<body>",
        f"<h1>{title}</h1>",
    ]
    for section in record_sections(record):
        lines.append(f"<h2>{escape_html(section.title)}</h2>")
        for index, paragraph in enumerate(section.paragraphs):
            css = ' class="notice"' if section.title == "Summary" and index == 0 else ""
            lines.append(f"<p{css}>{escape_html(paragraph)}</p>")
        for table in section.tables:
            lines += html_table(table)
    lines += ["</body>", "</html>"]
    return "\n".join(lines) + "\n"

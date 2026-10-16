"""Run the Evidence-to-Action Engine on ONE case and print a readable report (Phase 4; SYNTHETIC data only).

A packet goes through the Phase 2 document pipeline first (text layer, OCR where needed, rules extraction, checks,
contradictions), then through the engine: reference policy, evidence ledger, stress scenarios and decision
stability, open items and the next-best-evidence ranking. The decision record is appended to the hash-chained log
(default data/processed/evidence/decision_records.jsonl, gitignored) and its Markdown and HTML views are written
next to it (data/processed/evidence/records/<record_id>.md / .html).

The report prints ids, codes and numbers only. Document text (names, embedded instructions) is never printed here;
it appears only in the record and, escaped, in its views.

Examples:
    uv run python scripts/run_evidence.py --packet PKT-C-002
    uv run python scripts/run_evidence.py --packet PKT-I-004 --no-record
    uv run python scripts/run_evidence.py --application eval/applications/APP-C-001.json
"""

import argparse
import json
import sys
from pathlib import Path

from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import DATA_DIR
from uwcopilot.evidence.engine import EngineResult, run_engine
from uwcopilot.evidence.record import DEFAULT_RECORDS_PATH, DEFAULT_VIEWS_DIR, render_html, render_markdown
from uwcopilot.evidence.state import from_application
from uwcopilot.governance.provenance import display_path

DEFAULT_PACKETS_DIR = DATA_DIR / "processed" / "packets"
LINE_BY_LETTER = {"C": "credit", "I": "insurance"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--packet", help="packet id under --packets-dir (PKT-C-001)")
    source.add_argument("--application", type=Path, help="an application JSON file (no packet; ground truth dropped)")
    parser.add_argument("--packets-dir", type=Path, default=DEFAULT_PACKETS_DIR)
    parser.add_argument("--ocr", choices=("auto", "force"), default="auto")
    parser.add_argument("--records", type=Path, default=DEFAULT_RECORDS_PATH, help="decision record log (JSONL)")
    parser.add_argument("--views-dir", type=Path, default=DEFAULT_VIEWS_DIR, help="where the record views go")
    parser.add_argument("--no-record", action="store_true", help="do not append the record or write its views")
    return parser.parse_args(argv)


def run_packet_engine(args: argparse.Namespace, log: DecisionLog | None) -> EngineResult:
    """The pipeline then the engine on one packet PDF (ValueError / FileNotFoundError on bad input)."""
    from uwcopilot.docintel.pipeline import run_packet  # imports the PDF and OCR stack only when needed

    parts = args.packet.split("-")
    if len(parts) != 3 or parts[1] not in LINE_BY_LETTER:
        raise ValueError(f"{args.packet} is not a packet id like PKT-C-001 or PKT-I-001.")
    pdf = args.packets_dir / args.packet / "packet.pdf"
    if not pdf.is_file():
        raise FileNotFoundError(f"Missing {display_path(pdf)}. Run `make packets` first.")
    result = run_packet(pdf, args.packet, LINE_BY_LETTER[parts[1]], "rules", args.ocr)
    return run_engine(result, packet_sha256=sha256_of_file(pdf), record_log=log)


def run_application_engine(args: argparse.Namespace, log: DecisionLog | None) -> EngineResult:
    """The engine on an application JSON file (the clean screening assumption; no packet)."""
    if not args.application.is_file():
        raise FileNotFoundError(f"Missing {display_path(args.application)}.")
    application = json.loads(args.application.read_text(encoding="utf-8"))
    return run_engine(from_application(application), record_log=log)


def metric_text(value: float | None) -> str:
    """A metric value for the report ("n/a" when missing)."""
    return "n/a" if value is None else f"{value:g}"


def ledger_lines(result: EngineResult) -> list[str]:
    """Status counts and the open (CONFLICTING / UNKNOWN) claims."""
    if result.ledger is None:
        return []
    counts = ", ".join(f"{status} {count}" for status, count in result.ledger.status_counts().items())
    lines = ["", f"Evidence ledger: {len(result.ledger.claims)} claims ({counts})", "Open claims:"]
    for claim in result.ledger.open_claims():
        why = claim.reason or ("conflicts: " + ", ".join(claim.conflict_ids))
        lines.append(f"  {claim.status:<11} {claim.id:<45} {why}")
    return lines


def findings_lines(result: EngineResult) -> list[str]:
    """The policy findings: rule, outcome and trigger ids (details stay in the record)."""
    policy = result.policy_result
    if policy is None:
        return []
    lines = ["", "Reference policy findings:"]
    for finding in policy.findings:
        triggers = ", ".join(finding.trigger_ids) or "-"
        blocking = " (blocking)" if finding.blocking else ""
        lines.append(f"  {finding.outcome:<25} {finding.rule:<40} {triggers}{blocking}")
    if not policy.findings:
        lines.append("  none")
    return lines


def scenario_lines(result: EngineResult) -> list[str]:
    """Scenarios and decision stability."""
    lines = ["", "Scenarios (risk band: not evaluated, no applicable model):"]
    for item in result.scenarios:
        if item.status != "evaluated":
            lines.append(f"  {item.label:<30} not evaluated: {item.reason}")
            continue
        drivers = [f"+{trigger}" for trigger in item.triggers_added] + [
            f"-{trigger}" for trigger in item.triggers_removed
        ]
        notes = f"  [{'; '.join(item.notes)}]" if item.notes else ""
        lines.append(
            f"  {item.label:<30} {item.metric_name} {metric_text(item.baseline_metric)} -> "
            f"{metric_text(item.scenario_metric)}; {item.baseline_recommendation} -> {item.recommendation}; "
            f"drivers {' '.join(drivers) or 'none'}{notes}"
        )
    if result.stability is not None:
        lines.append(f"Decision stability: {result.stability.summary}")
        if result.stability.counterfactual_changes:
            lines.append(f"Counterfactuals that change it: {', '.join(result.stability.counterfactual_changes)}")
    return lines


def evidence_lines(result: EngineResult) -> list[str]:
    """Open items and the ranked evidence requests."""
    lines = ["", "Open items:"]
    for item in result.open_items:
        request = item.request_id or "no catalog request"
        lines.append(f"  {item.item_id:<45} {item.status:<11} -> {request}")
    if not result.open_items:
        lines.append("  none")
    lines += ["", "Next-best evidence (a rules-based, scenario-based value-of-information score):"]
    for request in result.next_best_evidence:
        lines.append(
            f"  #{request.rank} {request.request_id:<36} VOI {request.voi:.4f}  P(change) {request.p_change:.2f}  "
            f"cost {request.cost_weight:g}"
        )
        lines.append(f"     {request.reason}")
    if not result.next_best_evidence:
        lines.append("  none")
    return lines


def report_lines(result: EngineResult) -> list[str]:
    """The printed report."""
    lines = [
        f"Case {result.case_id} ({result.line}, {result.state.data_label}) - Evidence-to-Action Engine",
        "Decision support only: a person makes every underwriting decision.",
    ]
    if not result.evaluable:
        return [*lines, f"Not evaluable: {result.not_evaluable_reason}"]
    policy = result.policy_result
    assert policy is not None
    targets = ", ".join(policy.referral_targets) or "none"
    lines.append(f"Reference recommendation: {policy.recommendation} (referral targets: {targets})")
    lines += findings_lines(result)
    lines += evidence_lines(result)
    lines += scenario_lines(result)
    lines += ledger_lines(result)
    return lines


def write_views(record: dict, views_dir: Path) -> list[Path]:
    """The record's Markdown and HTML views."""
    views_dir.mkdir(parents=True, exist_ok=True)
    markdown = views_dir / f"{record['record_id']}.md"
    page = views_dir / f"{record['record_id']}.html"
    markdown.write_text(render_markdown(record), encoding="utf-8")
    page.write_text(render_html(record), encoding="utf-8")
    return [markdown, page]


def main(argv: list[str] | None = None) -> int:
    """Run one case; returns 0, or 1 with a message when the input is missing or unclear."""
    args = parse_args(argv)
    log = None if args.no_record else DecisionLog(args.records)
    try:
        result = run_packet_engine(args, log) if args.packet else run_application_engine(args, log)
    except (ValueError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1
    print("\n".join(report_lines(result)))
    if log is not None:
        paths = write_views(result.record, args.views_dir)
        print(f"\nRecord {result.record['record_id']} appended to {display_path(args.records)}")
        print(f"Views: {', '.join(display_path(path) for path in paths)}")
        problems = log.verify_chain()
        print("Record chain: intact" if not problems else f"Record chain problems: {problems}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

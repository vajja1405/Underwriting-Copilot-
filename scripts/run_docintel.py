"""Run the document pipeline on ONE packet PDF and print what it found (Phase 2 demo; SYNTHETIC packets only).

Prints the documents found, how many values were extracted per section, every check with its status, the
contradictions with their deterministic explanations, and any embedded-instruction hits (their text is untrusted
document data, shown only as a short excerpt). --json writes the whole PacketResult.

Examples:
    uv run python scripts/run_docintel.py --packet PKT-C-001
    uv run python scripts/run_docintel.py --pdf some/packet.pdf --packet-id PKT-I-007 --line insurance --ocr force
    uv run python scripts/run_docintel.py --packet PKT-I-013 --extractor hybrid --provider ollama
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from uwcopilot.common.paths import DATA_DIR
from uwcopilot.docintel.pipeline import PacketResult, run_packet
from uwcopilot.docintel.schemas import flatten_fields
from uwcopilot.governance.provenance import display_path

DEFAULT_PACKETS_DIR = DATA_DIR / "processed" / "packets"
LINE_BY_LETTER = {"C": "credit", "I": "insurance"}
EXCERPT_CHARS = 80


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--packet", help="packet id under --packets-dir (PKT-C-001); sets --pdf, --packet-id, --line")
    parser.add_argument("--packets-dir", type=Path, default=DEFAULT_PACKETS_DIR)
    parser.add_argument("--pdf", type=Path, help="a packet PDF (with --packet-id and --line)")
    parser.add_argument("--packet-id")
    parser.add_argument("--line", choices=("credit", "insurance"))
    parser.add_argument("--extractor", choices=("rules", "hybrid"), default="rules")
    parser.add_argument("--ocr", choices=("auto", "force"), default="auto")
    parser.add_argument("--provider", help="LLM provider for --extractor hybrid (default: UWC_LLM_PROVIDER)")
    parser.add_argument("--model", help="model for --extractor hybrid")
    parser.add_argument("--json", type=Path, help="also write the whole result as JSON here")
    return parser.parse_args(argv)


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, str, str]:
    """(pdf path, packet id, line) from --packet or from --pdf/--packet-id/--line. Raises ValueError if unclear."""
    if args.packet:
        letter = args.packet.split("-")[1] if args.packet.count("-") == 2 else ""
        if letter not in LINE_BY_LETTER:
            raise ValueError(f"{args.packet} is not a packet id like PKT-C-001 or PKT-I-001.")
        return args.packets_dir / args.packet / "packet.pdf", args.packet, LINE_BY_LETTER[letter]
    if not (args.pdf and args.packet_id and args.line):
        raise ValueError("Give --packet, or all of --pdf, --packet-id and --line.")
    return args.pdf, args.packet_id, args.line


def hybrid_extractor(args: argparse.Namespace) -> object | None:
    """The LLM extractor for --extractor hybrid (None for rules)."""
    if args.extractor != "hybrid":
        return None
    from uwcopilot.docintel.extract_llm import make_hybrid_extractor
    from uwcopilot.llm.factory import get_llm_client

    return make_hybrid_extractor(get_llm_client(args.provider, model=args.model))


def excerpt(text: str) -> str:
    """A short single-line excerpt of untrusted text, shown as data in quotes."""
    flat = " ".join(text.split())
    return repr(flat[:EXCERPT_CHARS] + ("..." if len(flat) > EXCERPT_CHARS else ""))


def report_lines(result: PacketResult) -> list[str]:
    """The printed report of one result."""
    lines = [
        f"Packet {result.packet_id} ({result.line}, SYNTHETIC) - extractor {result.extractor}, OCR {result.ocr_mode}",
        f"Pages: {len(result.page_texts)}; OCR pages: {result.ocr_pages or 'none'}; "
        f"total {result.timings_ms['total'] / 1000:.1f} s",
        "",
        "Documents:",
    ]
    for document in result.extraction.documents:
        pages = f"{document.pages[0]}-{document.pages[-1]}" if len(document.pages) > 1 else str(document.pages[0])
        ocr = " (OCR)" if document.ocr_used else ""
        lines.append(f"  {document.doc_id} {document.doc_type:<22} pages {pages}{ocr}")
    sections = Counter(row.field.split(".")[0] for row in flatten_fields(result.extraction))
    lines += ["", "Extracted values per section:"]
    lines += [f"  {name:<22} {count}" for name, count in sorted(sections.items())]
    lines += ["", "Checks:"]
    for check in result.checks:
        lines.append(f"  [{check.status:<13}] {check.check_id:<28} {check.severity:<6} {check.message}")
    lines += ["", "Contradictions:"]
    for item in result.contradictions.contradictions or []:
        lines.append(f"  {item.contradiction_id} {item.type} - {item.explanation} ({item.explanation_code})")
        lines.append(f"      {item.explanation_detail}")
    if not result.contradictions.contradictions:
        lines.append("  none")
    lines += ["", "Embedded-instruction hits (untrusted text, never followed):"]
    for hit in result.injection_hits:
        lines.append(
            f"  {hit.doc_id} page {hit.page} line {hit.line}: {', '.join(hit.patterns)} {excerpt(hit.excerpt)}"
        )
    if not result.injection_hits:
        lines.append("  none")
    if result.llm_usage:
        usage = result.llm_usage
        lines += ["", f"LLM: {usage['provider']}:{usage['model']}, {usage['calls']} calls, cost {usage['cost_usd']}"]
    return lines


def main(argv: list[str] | None = None) -> int:
    """Run one packet; returns 0, or 1 with a message when the input is missing or unclear."""
    args = parse_args(argv)
    try:
        pdf, packet_id, line = resolve_inputs(args)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if not pdf.is_file():
        print(f"Missing {display_path(pdf)}. Run `make packets` first.", file=sys.stderr)
        return 1
    result = run_packet(pdf, packet_id, line, args.extractor, args.ocr, hybrid_extractor(args))
    print("\n".join(report_lines(result)))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {display_path(args.json)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

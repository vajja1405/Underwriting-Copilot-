"""Render the lender-ready package of one golden SYNTHETIC founder scenario (Studio S6).

    uv run python scripts/render_studio_package.py --metro synthetic_metro --type car_wash --output package.pdf
    uv run python scripts/render_studio_package.py --metro chicago_il_in --type laundromat \
        --output out.pdf --json out.json

Builds the golden scenario (studio/narrative/golden.py), its SYNTHETIC project (studio/package/golden.py), the
package and the PDF, re-reads the PDF's own text through the round-trip audit, and writes the PDF (and, with --json,
the package as JSON). Nothing here is a real business, person or loan request.
"""

import argparse
import sys
from pathlib import Path

from uwcopilot.studio.narrative.golden import build_golden_scenario, golden_specs
from uwcopilot.studio.package import audit_pdf, build_package, pdf_page_count, project_from_golden, render_pdf


def main(argv: list[str] | None = None) -> int:
    """Render one golden package (see the module docstring)."""
    parser = argparse.ArgumentParser(description="Render one golden SYNTHETIC founder package as a PDF.")
    parser.add_argument("--metro", default="synthetic_metro")
    parser.add_argument("--type", dest="profile", default="car_wash")
    parser.add_argument("--n-paths", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None, help="also write the package as JSON")
    args = parser.parse_args(argv)
    if (args.metro, args.profile) not in golden_specs():
        print(f"no golden scenario for {args.metro} / {args.profile}", file=sys.stderr)
        return 2
    scenario = build_golden_scenario(args.metro, args.profile, n_paths=args.n_paths)
    package = build_package(project_from_golden(scenario))
    pdf = render_pdf(package)
    untraced = audit_pdf(pdf, package.ledger)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(pdf)
    if args.json is not None:
        args.json.write_text(package.model_dump_json(indent=1) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.output}: {pdf_page_count(pdf)} pages, {len(package.ledger.claims)} claims, "
        f"untraced numbers in the PDF text: {len(untraced)}"
    )
    return 0 if not untraced else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Print honest resume bullets for a "Projects" section from results/*.json (`make resume-lines`).

Prompt 8 item 2 and the Studio S12 resume-lines bullet. The script only reads: it prints the bullets to stdout and,
on stderr, every bullet or clause it left out and why (a metric not measured, or a metric this project must not
claim, such as the circular next-best-evidence agreement). Numbers come only from results/*.json; nothing is typed by
hand, rounded up or estimated. Per-person lines come only from config/contributions.yaml, never from git history.

Usage:
    uv run python scripts/resume_lines.py
    uv run python scripts/resume_lines.py --results-dir results --contributions config/contributions.yaml
Exit codes: 0 printed; 2 config/contributions.yaml is malformed (not valid YAML, not a mapping, or a top-level key
other than "owners") or names an unknown component.
"""

import argparse
import sys
from pathlib import Path

from uwcopilot.common.paths import CONFIG_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.resume.contributions import ContributionsError, load_contributions, render_per_person
from uwcopilot.resume.report import build_report, render_omissions, render_projects

CONTRIBUTIONS_FILE = CONFIG_DIR / "contributions.yaml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR, help="folder with the results JSON files")
    parser.add_argument("--contributions", type=Path, default=CONTRIBUTIONS_FILE, help="who owns which component")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="repo root (model card and CI workflow)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Print the bullets (stdout) and what was left out (stderr)."""
    args = parse_args(argv)
    try:
        contributions = load_contributions(args.contributions)
    except ContributionsError as error:
        print(f"{args.contributions}: {error}", file=sys.stderr)
        return 2
    report = build_report(args.results_dir, args.repo_root)
    per_person, person_notes = render_per_person(contributions, report.bullets, report.omissions)
    print("\n".join(render_projects(report) + [""] + per_person))
    print("\n".join(render_omissions(report, person_notes)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

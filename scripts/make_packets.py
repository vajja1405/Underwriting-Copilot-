"""Make the SYNTHETIC document packets for Phase 2 (loan packets and insurance submissions with ground truth).

Usage (the `make packets` target):
    uv run python scripts/make_packets.py --out data/processed/packets --n-credit 150 --n-insurance 150 \
        --seed 20261013 --test-credit 30 --test-insurance 30

Writes <out>/<packet_id>/packet.pdf and truth.json, and <out>/manifest.json. A copy of the manifest (no absolute
paths) goes to eval/packets/manifest.json so anyone can check that a regeneration gives the same packets
(pdf_sha256, truth_sha256, test_truth_sha256).

--json-only skips drawing and scanning (fast; for the Phase 4 simulator's large JSON-only case sets): truth.json
is the same except pdf_file and pdf_sha256 are empty. A --json-only run does not touch eval/packets/manifest.json
unless --eval-manifest is given.

Everything generated is SYNTHETIC: fictional institutions, made-up people and businesses.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.docintel.synthetic import generate_packets

DEFAULT_EVAL_MANIFEST = REPO_ROOT / "eval" / "packets" / "manifest.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line arguments."""
    parser = argparse.ArgumentParser(description="Make the SYNTHETIC document packets (Phase 2).")
    parser.add_argument("--out", type=Path, default=Path("data/processed/packets"), help="output folder")
    parser.add_argument("--n-credit", type=int, default=150, help="number of loan packets")
    parser.add_argument("--n-insurance", type=int, default=150, help="number of insurance packets")
    parser.add_argument("--seed", type=int, default=20261013, help="master seed")
    parser.add_argument("--test-credit", type=int, default=30, help="loan packets in the test split")
    parser.add_argument("--test-insurance", type=int, default=30, help="insurance packets in the test split")
    parser.add_argument("--json-only", action="store_true", help="write truth.json only (no PDF drawing)")
    parser.add_argument("--eval-manifest", type=Path, default=None, help="where to copy the manifest")
    parser.add_argument("--no-eval-manifest", action="store_true", help="do not copy the manifest")
    parser.add_argument("--quiet", action="store_true", help="do not print one line per packet")
    return parser.parse_args(argv)


def shown_path(path: Path) -> str:
    """A path as it should appear in the manifest: relative to the repo when inside it, never absolute."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def command_line(args: argparse.Namespace) -> str:
    """The command that reproduces this run (no absolute paths)."""
    parts = [
        "scripts/make_packets.py",
        f"--out {shown_path(args.out)}",
        f"--n-credit {args.n_credit}",
        f"--n-insurance {args.n_insurance}",
        f"--seed {args.seed}",
        f"--test-credit {args.test_credit}",
        f"--test-insurance {args.test_insurance}",
    ]
    if args.json_only:
        parts.append("--json-only")
    return " ".join(parts)


def eval_manifest_path(args: argparse.Namespace) -> Path | None:
    """Where the committed manifest copy goes (None to skip)."""
    if args.no_eval_manifest:
        return None
    if args.eval_manifest is not None:
        return args.eval_manifest
    return None if args.json_only else DEFAULT_EVAL_MANIFEST


def main(argv: list[str] | None = None) -> int:
    """Generate the packets and print a short summary."""
    args = parse_args(argv)
    if args.test_credit > args.n_credit or args.test_insurance > args.n_insurance:
        print("The test split cannot be larger than the number of packets.", file=sys.stderr)
        return 2
    started = time.perf_counter()
    progress = None if args.quiet else print
    manifest = generate_packets(
        out_dir=args.out,
        n_credit=args.n_credit,
        n_insurance=args.n_insurance,
        seed=args.seed,
        test_credit=args.test_credit,
        test_insurance=args.test_insurance,
        json_only=args.json_only,
        created_with=command_line(args),
        progress=progress,
    )
    elapsed = time.perf_counter() - started
    copy = eval_manifest_path(args)
    if copy is not None:
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    pages = sum(entry["n_pages"] for entry in manifest["packets"])
    scanned = sum(entry["n_scanned_pages"] for entry in manifest["packets"])
    print(
        f"SYNTHETIC packets: {len(manifest['packets'])} in {shown_path(args.out)} ({pages} pages, {scanned} scanned); "
        f"test_truth_sha256 {manifest['test_truth_sha256'][:12]}...; {elapsed:.1f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

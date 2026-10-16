"""Make PNG files smaller without changing a single pixel (the screenshots under docs/web/screenshots).

    uv run --with pyoxipng==9.1.1 python scripts/optimize_pngs.py docs/web/screenshots

Each PNG under the given folders (or each PNG file given) is re-encoded losslessly: with oxipng (the pyoxipng
package) when it is installed, otherwise with Pillow's PNG optimizer. The new bytes replace the file only when they
are smaller AND decode to exactly the same pixels (compared as RGBA); otherwise the file stays as it was. Text chunks
that do not affect how the image looks may be dropped ("safe" stripping in oxipng). Prints one line per file and a
total. Exit code 0, or 2 when a path does not exist.
"""

import argparse
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

OXIPNG_LEVEL = 4  # oxipng's optimization level (0-6); 4 is a slow-but-thorough middle setting


@dataclass(frozen=True)
class Outcome:
    """What happened to one file."""

    path: Path
    before: int
    after: int
    tool: str
    replaced: bool


def same_pixels(first: Path, second: Path) -> bool:
    """True when both PNGs decode to the same size and the same RGBA pixels."""
    with Image.open(first) as image_a, Image.open(second) as image_b:
        if image_a.size != image_b.size:
            return False
        return image_a.convert("RGBA").tobytes() == image_b.convert("RGBA").tobytes()


def encode_candidate(source: Path, target: Path, use_oxipng: bool) -> str:
    """Write a losslessly re-encoded copy of source to target. Returns the tool that was used."""
    if use_oxipng:
        try:
            import oxipng  # optional: `uv run --with pyoxipng==9.1.1`
        except ImportError:
            use_oxipng = False
        else:
            oxipng.optimize(source, target, level=OXIPNG_LEVEL, strip=oxipng.StripChunks.safe())
            return "oxipng"
    with Image.open(source) as image:
        image.load()
        image.save(target, format="PNG", optimize=True)
    return "pillow"


def optimize_file(path: Path, use_oxipng: bool = True) -> Outcome:
    """Re-encode one PNG; keep the result only if it is smaller and has the same pixels."""
    before = path.stat().st_size
    candidate = path.with_name(f"{path.stem}.optimizing.png")
    try:
        tool = encode_candidate(path, candidate, use_oxipng)
        after = candidate.stat().st_size
        if after < before and same_pixels(path, candidate):
            os.replace(candidate, path)
            return Outcome(path, before, after, tool, replaced=True)
        return Outcome(path, before, before, tool, replaced=False)
    finally:
        candidate.unlink(missing_ok=True)


def png_files(paths: Iterable[Path]) -> list[Path]:
    """Every .png file in the given files and folders (folders are searched recursively), sorted."""
    found: set[Path] = set()
    for path in paths:
        if path.is_dir():
            found.update(item for item in path.rglob("*.png") if item.is_file())
        elif path.suffix.lower() == ".png" and path.is_file():
            found.add(path)
    return sorted(found)


def main(argv: Sequence[str] | None = None) -> int:
    """Optimize every PNG named on the command line and print what changed."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path, help="PNG files or folders")
    parser.add_argument("--pillow-only", action="store_true", help="do not use oxipng even if it is installed")
    args = parser.parse_args(argv)
    missing = [str(path) for path in args.paths if not path.exists()]
    if missing:
        print(f"Error: not found: {', '.join(missing)}", file=sys.stderr)
        return 2
    total_before = total_after = 0
    for path in png_files(args.paths):
        outcome = optimize_file(path, use_oxipng=not args.pillow_only)
        total_before += outcome.before
        total_after += outcome.after
        change = f"{outcome.before} -> {outcome.after} bytes" if outcome.replaced else "kept (not smaller)"
        print(f"{path}: {change} ({outcome.tool})")
    print(f"Total: {total_before} -> {total_after} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

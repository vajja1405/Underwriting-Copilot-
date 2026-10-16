"""Tests for scripts/optimize_pngs.py with small generated images (Pillow only; oxipng is optional)."""

from pathlib import Path

import pytest
from PIL import Image

from optimize_pngs import main, optimize_file, png_files, same_pixels


def uncompressed_png(path: Path, color: tuple[int, int, int] = (240, 236, 228)) -> Image.Image:
    """Write a 64x64 PNG without compression (so a re-encode is smaller) and return the image."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (64, 64), color)
    image.putpixel((3, 5), (20, 30, 40))
    image.save(path, format="PNG", compress_level=0)
    return image


def test_a_smaller_lossless_copy_replaces_the_file(tmp_path: Path) -> None:
    path = tmp_path / "shot.png"
    image = uncompressed_png(path)
    before = path.stat().st_size
    outcome = optimize_file(path, use_oxipng=False)
    assert outcome.replaced and outcome.tool == "pillow"
    assert outcome.after == path.stat().st_size < before
    with Image.open(path) as reopened:
        assert reopened.convert("RGB").tobytes() == image.tobytes()
    assert not list(tmp_path.glob("*.optimizing.png"))


def test_a_file_that_cannot_shrink_is_kept_byte_for_byte(tmp_path: Path) -> None:
    path = tmp_path / "shot.png"
    uncompressed_png(path)
    optimize_file(path, use_oxipng=False)
    content = path.read_bytes()
    outcome = optimize_file(path, use_oxipng=False)
    assert not outcome.replaced and path.read_bytes() == content


def test_same_pixels_compares_decoded_images(tmp_path: Path) -> None:
    uncompressed_png(tmp_path / "a.png")
    uncompressed_png(tmp_path / "b.png")
    uncompressed_png(tmp_path / "c.png", color=(0, 0, 0))
    assert same_pixels(tmp_path / "a.png", tmp_path / "b.png")
    assert not same_pixels(tmp_path / "a.png", tmp_path / "c.png")


def test_png_files_searches_folders_and_skips_other_files(tmp_path: Path) -> None:
    uncompressed_png(tmp_path / "one.png")
    uncompressed_png(tmp_path / "mock" / "two.png")
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    assert png_files([tmp_path]) == [tmp_path / "mock" / "two.png", tmp_path / "one.png"]


def test_main_prints_a_total_and_refuses_missing_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    uncompressed_png(tmp_path / "one.png")
    assert main([str(tmp_path), "--pillow-only"]) == 0
    assert "Total:" in capsys.readouterr().out
    assert main([str(tmp_path / "missing")]) == 2

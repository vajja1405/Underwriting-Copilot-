"""Tests for the OCR reader (docintel/ocr.py).

Most tests use hand-written Tesseract TSV and a synthetic image, so they run without the tesseract binary. The
last test OCRs one real scanned page of a rendered SYNTHETIC packet; it is skipped when tesseract is not installed
(apt package tesseract-ocr; CI installs it).
"""

import shutil

import pytest
from docintel_pipeline_fixtures import built_packet, tesseract_available
from PIL import Image, ImageDraw

from uwcopilot.docintel import ocr
from uwcopilot.docintel.layout import Word, make_line
from uwcopilot.docintel.parsing import parse_amount
from uwcopilot.docintel.pdftext import page_sizes

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def tsv_row(block: int, line: int, word: int, left: int, top: int, conf: float, text: str) -> str:
    """One level-5 (word) row of Tesseract TSV output, 100 x 40 pixels."""
    return "\t".join(str(item) for item in (5, 1, block, 1, line, word, left, top, 100, 40, conf, text))


def sample_tsv() -> str:
    """Two lines and some noise, at 300 DPI."""
    rows = [
        HEADER,
        "1\t1\t0\t0\t0\t0\t0\t0\t2550\t3300\t-1\t",
        tsv_row(1, 1, 1, 300, 400, 96.0, "Opening"),
        tsv_row(1, 1, 2, 420, 400, 95.0, "balance"),
        tsv_row(1, 1, 3, 960, 400, 97.0, "$124,592.50"),
        tsv_row(1, 1, 4, 1500, 400, 12.0, ";"),  # punctuation only: noise
        tsv_row(1, 1, 5, 1600, 400, 20.0, "ey"),  # short, low confidence, lower case: noise
        tsv_row(1, 2, 1, 300, 460, 30.0, "TX"),  # capitals are kept even with low confidence
        tsv_row(1, 2, 2, 420, 460, 25.0, "9"),  # a number is kept
        tsv_row(1, 2, 3, 520, 460, 91.0, " "),  # empty text
    ]
    return "\n".join(rows)


def test_parse_tsv_groups_words_by_tesseract_line_and_scales_to_points() -> None:
    lines = ocr.parse_tsv(sample_tsv())
    assert [line.text for line in lines] == ["Opening balance $124,592.50", "TX 9"]
    assert [line.line_number for line in lines] == [1, 2]
    first = lines[0].words[0]
    assert first.x0 == pytest.approx(300 * 72 / 300)
    assert first.top == pytest.approx(400 * 0.24)
    assert first.x1 == pytest.approx(400 * 0.24)
    assert first.conf == pytest.approx(0.96)


@pytest.mark.parametrize(
    ("text", "conf", "noise"),
    [
        (";", 0.99, True),
        ("—", 0.9, True),
        ("ey", 0.2, True),
        ("e", 0.1, True),
        ("ey", 0.6, False),
        ("US", 0.1, False),
        ("9", 0.1, False),
        ("sarees", 0.3, False),
    ],
)
def test_noise_words(text: str, conf: float, noise: bool) -> None:
    assert ocr.is_noise_word(text, conf) is noise


def striped_image(angle: float) -> Image.Image:
    """A white page with dark text-like rows, rotated by `angle` degrees."""
    image = Image.new("L", (1200, 1500), 255)
    draw = ImageDraw.Draw(image)
    for row in range(20):
        top = 150 + row * 55
        for column in range(14):
            left = 120 + column * 70
            draw.rectangle((left, top, left + 50, top + 18), fill=0)
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)


@pytest.mark.parametrize("angle", [1.4, -0.8, 0.0])
def test_estimate_skew_finds_the_rotation_that_levels_the_rows(angle: float) -> None:
    estimate = ocr.estimate_skew(striped_image(angle))
    assert estimate == pytest.approx(-angle, abs=0.25)


def test_deskew_leaves_a_straight_page_alone() -> None:
    image = striped_image(0.0)
    straightened, used = ocr.deskew(image)
    assert used == 0.0
    assert straightened is image


def test_confident_characters_prefers_the_more_complete_reading() -> None:
    complete = [make_line(1, [Word(text="US", x0=0, top=0, x1=5, bottom=5, conf=0.9)] * 3)]
    partial = [make_line(1, [Word(text="US", x0=0, top=0, x1=5, bottom=5, conf=0.95)])]
    assert ocr.confident_characters(complete) > ocr.confident_characters(partial)
    assert ocr.mean_confidence(partial) > ocr.mean_confidence(complete)


def test_ocr_page_keeps_the_mode_with_more_confident_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    path, truth = built_packet("PKT-C-009", "credit")
    scanned = next(page.page_number for page in truth.pages if page.scanned)
    full = sample_tsv()
    short = "\n".join(full.splitlines()[:3])
    monkeypatch.setattr(ocr, "run_tesseract_modes", lambda image_path, modes=ocr.PSM_MODES: {6: short, 4: full})
    width, height = page_sizes(path)[scanned - 1]
    page = ocr.ocr_page(path, scanned, width, height)
    assert page.ocr_psm == 4
    assert page.source == "ocr"
    assert page.lines[0].text == "Opening balance $124,592.50"
    assert page.deskew_degrees is not None


def test_missing_tesseract_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ocr.OcrUnavailable, match="tesseract is not installed"):
        ocr.tesseract_binary()
    ocr.tesseract_version.cache_clear()
    try:
        assert ocr.tesseract_version() == "not installed"
    finally:
        ocr.tesseract_version.cache_clear()


@pytest.mark.skipif(not tesseract_available(), reason="tesseract is not installed (apt package tesseract-ocr)")
def test_ocr_reads_a_scanned_page_of_a_rendered_packet() -> None:
    path, truth = built_packet("PKT-C-009", "credit")
    scanned = next(page for page in truth.pages if page.scanned)
    width, height = page_sizes(path)[scanned.page_number - 1]
    page = ocr.ocr_page(path, scanned.page_number, width, height)
    assert ocr.tesseract_version().startswith("tesseract")
    assert page.ocr_psm in ocr.PSM_MODES
    assert page.ocr_mean_confidence is not None and page.ocr_mean_confidence > 0.8
    printed = {parse_amount(word) for line in scanned.text_lines for word in line.split()} - {None}
    read = {parse_amount(word.text) for line in page.lines for word in line.words} - {None}
    assert printed, "the scanned page should print amounts"
    assert len(printed & read) / len(printed) >= 0.8
    for line in page.lines:
        assert 0 <= line.bbox[0] <= line.bbox[2] <= width
        assert 0 <= line.bbox[1] <= line.bbox[3] <= height

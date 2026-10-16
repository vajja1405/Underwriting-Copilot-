"""OCR for pages without a text layer: pypdfium2 renders the page, Tesseract reads it, and the words come back as
PageText lines (docintel/layout.py) in PDF points.

Steps for one page:
1. Render the page with pypdfium2 at OCR_DPI (300) in grayscale.
2. Apply a 3x3 median filter (Pillow). The scanned pages carry sparse speckle noise; the median filter removes
   single-pixel specks without thinning 9-point strokes (about 3-4 pixels wide at 300 DPI), which stops Tesseract
   from reading specks as stray characters and digits.
3. Deskew (estimate_skew): a scan is often rotated by a degree or two, which tilts every printed row; on a full-width
   table row that tilt is larger than the gap between rows, so rows get split or merged. The angle is found with a
   projection profile: a quarter-size copy of the page is rotated by every angle from -MAX_SKEW_DEGREES to
   +MAX_SKEW_DEGREES in SKEW_STEP_DEGREES steps, and the angle whose row-by-row ink counts change most sharply
   (text lines lying flat) wins. The full image is rotated by that angle about its centre before OCR, so word
   boxes come back close to where the words were printed.
4. Run the Tesseract command line twice, `tesseract <png> stdout --psm 6 tsv` (one uniform block of text: good
   for tables) and `--psm 4` (a single column of text of variable sizes), at the same time as two subprocesses.
   OMP_THREAD_LIMIT=1 keeps each run on one thread (Tesseract's own threading is slower on a busy machine).
5. Parse each TSV: word rows (level 5) are grouped into lines by Tesseract's (block, paragraph, line) numbers, in
   Tesseract's reading order. Pixel boxes are scaled to points (x 72 / OCR_DPI).
6. Keep the run with the higher mean word confidence (ties keep psm 6). The page records the psm it kept.

Noise words are dropped before lines are built (is_noise_word): a word with no letter or digit, or a word with
confidence below NOISE_MAX_CONF that has at most two letters or digits and is neither a number nor all capital
letters (state and country codes such as "TX" or "US" are kept). Line numbers follow
Tesseract's line order, so they can differ from the printed row numbers when Tesseract splits or merges a row.

tesseract_version() records the version string for the provenance of results. When the tesseract binary is not
installed, OcrUnavailable is raised with an explanation.
"""

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pypdfium2
from PIL import Image, ImageFilter

from uwcopilot.docintel.layout import Line, PageText, Word, make_line

OCR_DPI = 300
POINTS_PER_INCH = 72.0
PSM_MODES: tuple[int, ...] = (6, 4)  # the first one wins a tie
MEDIAN_FILTER_SIZE = 3
MAX_SKEW_DEGREES = 2.5
SKEW_STEP_DEGREES = 0.1
SKEW_SAMPLE_FACTOR = 4  # the skew search runs on a copy this many times smaller
INK_THRESHOLD = 160  # gray levels below this count as ink in the skew search
NOISE_MAX_CONF = 0.40
TESSERACT_TIMEOUT_SECONDS = 120
TSV_COLUMNS = 12  # level page block par line word left top width height conf text


class OcrUnavailable(RuntimeError):
    """The tesseract binary is not installed, so pages without a text layer cannot be read."""


def tesseract_binary() -> str:
    """Path of the tesseract binary, or OcrUnavailable."""
    path = shutil.which("tesseract")
    if path is None:
        raise OcrUnavailable("tesseract is not installed (apt package tesseract-ocr); scanned pages cannot be read")
    return path


@lru_cache(maxsize=1)
def tesseract_version() -> str:
    """The first line of `tesseract --version` (for example "tesseract 5.3.4"), or "not installed"."""
    path = shutil.which("tesseract")
    if path is None:
        return "not installed"
    completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30, check=False)
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0].strip() if output else "unknown"


def render_page_image(pdf_path: Path, page_number: int, dpi: int = OCR_DPI) -> Image.Image:
    """The page rendered in grayscale at `dpi`, median-filtered (see the module docstring)."""
    document = pypdfium2.PdfDocument(str(pdf_path))
    try:
        page = document[page_number - 1]
        bitmap = page.render(scale=dpi / POINTS_PER_INCH, grayscale=True)
        image = bitmap.to_pil().convert("L")
    finally:
        document.close()
    return image.filter(ImageFilter.MedianFilter(MEDIAN_FILTER_SIZE))


def profile_sharpness(image: Image.Image) -> float:
    """How sharply the ink count changes from one pixel row to the next (high when text lines lie flat)."""
    ink_per_row = (np.asarray(image) < INK_THRESHOLD).sum(axis=1).astype(float)
    changes = ink_per_row[1:] - ink_per_row[:-1]
    return float((changes * changes).sum())


def estimate_skew(image: Image.Image) -> float:
    """The rotation in degrees (counter-clockwise) that makes the printed rows horizontal (see the module docstring)."""
    small = image.resize((image.width // SKEW_SAMPLE_FACTOR, image.height // SKEW_SAMPLE_FACTOR))
    steps = round(MAX_SKEW_DEGREES / SKEW_STEP_DEGREES)
    best_angle = 0.0
    best_score = profile_sharpness(small)
    for step in range(-steps, steps + 1):
        angle = round(step * SKEW_STEP_DEGREES, 2)
        if angle == 0.0:
            continue
        rotated = small.rotate(angle, resample=Image.Resampling.BILINEAR, fillcolor=255)
        score = profile_sharpness(rotated)
        if score > best_score:
            best_angle, best_score = angle, score
    return best_angle


def deskew(image: Image.Image) -> tuple[Image.Image, float]:
    """The image rotated so its rows are horizontal, and the angle used (0.0 means unchanged)."""
    angle = estimate_skew(image)
    if angle == 0.0:
        return image, 0.0
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255), angle


def tesseract_environment() -> dict[str, str]:
    """The process environment with Tesseract limited to one thread."""
    environment = dict(os.environ)
    environment["OMP_THREAD_LIMIT"] = "1"
    return environment


def run_tesseract_modes(image_path: Path, modes: tuple[int, ...] = PSM_MODES) -> dict[int, str]:
    """TSV output of Tesseract for each page segmentation mode, run at the same time (psm -> TSV text)."""
    binary = tesseract_binary()
    processes = {}
    for mode in modes:
        command = [binary, str(image_path), "stdout", "--psm", str(mode), "tsv"]
        processes[mode] = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=tesseract_environment(),
        )
    outputs = {}
    for mode, process in processes.items():
        stdout, _stderr = process.communicate(timeout=TESSERACT_TIMEOUT_SECONDS)
        outputs[mode] = stdout if process.returncode == 0 else ""
    return outputs


def is_noise_word(text: str, conf: float) -> bool:
    """True for a stray mark read as a word (see the module docstring)."""
    alphanumeric = [character for character in text if character.isalnum()]
    if not alphanumeric:
        return True
    is_number = all(character.isdigit() for character in alphanumeric)
    is_capitals = all(character.isupper() for character in alphanumeric)
    return conf < NOISE_MAX_CONF and len(alphanumeric) <= 2 and not is_number and not is_capitals


def tsv_word(fields: list[str], scale: float) -> Word | None:
    """A Word from one TSV word row (pixel box scaled to points), or None for an empty or noise word."""
    text = fields[11].strip()
    if not text:
        return None
    conf = max(0.0, min(1.0, float(fields[10]) / 100.0))
    if is_noise_word(text, conf):
        return None
    left, top, width, height = (float(value) for value in fields[6:10])
    return Word(
        text=text,
        x0=round(left * scale, 2),
        top=round(top * scale, 2),
        x1=round((left + width) * scale, 2),
        bottom=round((top + height) * scale, 2),
        conf=round(conf, 4),
    )


def parse_tsv(tsv: str, dpi: int = OCR_DPI) -> list[Line]:
    """Lines from Tesseract TSV output, in Tesseract's reading order, numbered from 1."""
    scale = POINTS_PER_INCH / dpi
    groups: dict[tuple[str, str, str], list[Word]] = {}
    for row in tsv.splitlines()[1:]:
        fields = row.split("\t")
        if len(fields) < TSV_COLUMNS or fields[0] != "5":
            continue
        word = tsv_word(fields, scale)
        if word is None:
            continue
        groups.setdefault((fields[2], fields[3], fields[4]), []).append(word)
    return [make_line(number, words) for number, words in enumerate(groups.values(), start=1)]


def mean_confidence(lines: list[Line]) -> float:
    """Mean confidence of every word on the page (0.0 for a page without words)."""
    confidences = [word.conf for line in lines for word in line.words]
    return sum(confidences) / len(confidences) if confidences else 0.0


def confident_characters(lines: list[Line]) -> float:
    """Sum over words of confidence x number of letters and digits: the expected count of correctly read characters."""
    total = 0.0
    for line in lines:
        for word in line.words:
            total += word.conf * sum(1 for character in word.text if character.isalnum())
    return total


def ocr_page(pdf_path: Path, page_number: int, width: float, height: float) -> PageText:
    """OCR one packet page (see the module docstring) and keep the better page segmentation mode."""
    image, angle = deskew(render_page_image(pdf_path, page_number))
    with tempfile.TemporaryDirectory(prefix="uwc_ocr_") as folder:
        image_path = Path(folder) / f"page_{page_number}.png"
        image.save(image_path)
        outputs = run_tesseract_modes(image_path)
    best_lines: list[Line] = []
    best_mode = PSM_MODES[0]
    best_score = -1.0
    for mode in PSM_MODES:
        lines = parse_tsv(outputs.get(mode, ""))
        score = confident_characters(lines)
        if score > best_score:
            best_lines, best_mode, best_score = lines, mode, score
    return PageText(
        page_number=page_number,
        width=width,
        height=height,
        source="ocr",
        lines=best_lines,
        char_count=sum(len(line.text) for line in best_lines),
        ocr_psm=best_mode,
        ocr_mean_confidence=round(mean_confidence(best_lines), 4),
        deskew_degrees=angle,
    )

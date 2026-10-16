"""A seeded "scanned look" for a SYNTHETIC document: image-only PDF pages with rotation, blur, noise and JPEG.

For each page of the rendered document PDF:
1. rasterize with pypdfium2 at 150 DPI and convert to 8-bit grayscale;
2. rotate by a seeded angle of 0.3-1.8 degrees (either direction), filling the corners with white;
3. Gaussian blur with a radius of 0.3-0.9 pixels;
4. add Gaussian noise (sigma 4-10 grey levels) and sparse dark and light speckles;
5. encode as JPEG at quality 55-75;
6. place the JPEG on a new PDF page of the same size (reportlab, invariant mode). The page has NO text layer.
All parameters come from one random.Random seeded per document, and the noise from a numpy Generator seeded the
same way, so the same seed gives byte-identical output.
"""

import io
import random
from dataclasses import dataclass

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

SCAN_DPI = 150
POINTS_PER_INCH = 72.0


@dataclass(frozen=True)
class ScanParameters:
    """The seeded scanner settings of one page."""

    angle: float
    blur_radius: float
    noise_sigma: float
    speckle_share: float
    jpeg_quality: int


def draw_parameters(rng: random.Random) -> ScanParameters:
    """Draw one page's scanner settings."""
    angle = rng.uniform(0.3, 1.8) * (1 if rng.random() < 0.5 else -1)
    return ScanParameters(
        angle=round(angle, 3),
        blur_radius=round(rng.uniform(0.3, 0.9), 3),
        noise_sigma=round(rng.uniform(4.0, 10.0), 3),
        speckle_share=round(rng.uniform(0.0002, 0.0012), 5),
        jpeg_quality=rng.randint(55, 75),
    )


def degrade(image: Image.Image, parameters: ScanParameters, noise: np.random.Generator) -> Image.Image:
    """Rotate, blur and add noise to a grayscale page image."""
    rotated = image.rotate(parameters.angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
    blurred = rotated.filter(ImageFilter.GaussianBlur(parameters.blur_radius))
    pixels = np.asarray(blurred, dtype=np.float32)
    pixels = pixels + noise.normal(0.0, parameters.noise_sigma, size=pixels.shape)
    speckles = noise.random(size=pixels.shape)
    pixels[speckles < parameters.speckle_share / 2] = 30.0
    pixels[(speckles >= parameters.speckle_share / 2) & (speckles < parameters.speckle_share)] = 235.0
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


def jpeg_bytes(image: Image.Image, quality: int) -> bytes:
    """JPEG-encode an image (no metadata, baseline)."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=False, progressive=False)
    return buffer.getvalue()


def scan_document(pdf_bytes: bytes, seed: int) -> bytes:
    """The document as an image-only "scanned" PDF with the same page count and page sizes."""
    rng = random.Random(seed)
    noise = np.random.default_rng(seed)
    source = pdfium.PdfDocument(pdf_bytes)
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1, pageCompression=1)
    canvas.setTitle("SYNTHETIC scanned document")
    canvas.setAuthor("uwcopilot synthetic packet generator")
    canvas.setCreator("uwcopilot")
    canvas.setProducer("uwcopilot")
    try:
        for index in range(len(source)):
            page = source[index]
            width, height = page.get_size()
            bitmap = page.render(scale=SCAN_DPI / POINTS_PER_INCH, grayscale=True)
            image = bitmap.to_pil().convert("L")
            parameters = draw_parameters(rng)
            scanned = degrade(image, parameters, noise)
            canvas.setPageSize((width, height))
            canvas.drawImage(ImageReader(io.BytesIO(jpeg_bytes(scanned, parameters.jpeg_quality))), 0, 0, width, height)
            canvas.showPage()
    finally:
        source.close()
    canvas.save()
    return output.getvalue()

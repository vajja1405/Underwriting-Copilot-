"""Fonts, colors and paragraph styles of the founder package PDF (reportlab).

The font is DejaVu Sans, read from matplotlib's own font folder (matplotlib is a dependency, so the same file exists
on a laptop, in CI and in the Space image; no system font and no download is needed). DejaVu covers the characters
a founder may type in a business or owner name, which the PDF's built-in fonts do not.
"""

from functools import lru_cache
from pathlib import Path

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT = "FundproofSans"
FONT_BOLD = "FundproofSans-Bold"
FONT_ITALIC = "FundproofSans-Oblique"
FONT_MONO = "FundproofMono"
FONT_FILES: dict[str, str] = {
    FONT: "DejaVuSans.ttf",
    FONT_BOLD: "DejaVuSans-Bold.ttf",
    FONT_ITALIC: "DejaVuSans-Oblique.ttf",
    FONT_MONO: "DejaVuSansMono.ttf",
}

NAVY = colors.HexColor("#1F3A5F")
TEAL = colors.HexColor("#2A7F8E")
INK = colors.HexColor("#1E2329")
MUTED = colors.HexColor("#5B6573")
RULE = colors.HexColor("#D5DBE3")
STRIPE = colors.HexColor("#F4F6F9")
PANEL = colors.HexColor("#EEF3F8")
GROUP = colors.HexColor("#E3EAF2")
WARN = colors.HexColor("#B42318")
WARN_BG = colors.HexColor("#FEF3F2")
PASS = colors.HexColor("#1B7F4B")
FINDING = colors.HexColor("#B54708")
WHITE = colors.white
RESULT_COLORS = {"passed": PASS, "finding": FINDING, "not evaluated": MUTED}
SIZES: dict[str, tuple[float, float]] = {"normal": (8.2, 10.4), "small": (7.2, 9.0), "tiny": (5.9, 7.1)}


def font_path(name: str) -> Path:
    """The TTF file of one of the package fonts (matplotlib's bundled DejaVu fonts)."""
    return Path(matplotlib.get_data_path()) / "fonts" / "ttf" / FONT_FILES[name]


@lru_cache(maxsize=1)
def register_fonts() -> None:
    """Register the four fonts with reportlab once per process (bold and italic tags map to them)."""
    for name in FONT_FILES:
        pdfmetrics.registerFont(TTFont(name, str(font_path(name))))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT_ITALIC, boldItalic=FONT_BOLD)


def style(name: str, size: float, leading: float, **options: object) -> ParagraphStyle:
    """A ParagraphStyle in the package font."""
    base = {"fontName": FONT, "fontSize": size, "leading": leading, "textColor": INK, "alignment": TA_LEFT}
    base.update(options)
    return ParagraphStyle(name, **base)  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def styles() -> dict[str, ParagraphStyle]:
    """Every paragraph style of the document by name."""
    register_fonts()
    table_styles = {}
    for size_name, (size, leading) in SIZES.items():
        table_styles[f"cell_{size_name}"] = style(f"cell_{size_name}", size, leading)
        table_styles[f"cell_{size_name}_right"] = style(f"cell_{size_name}_r", size, leading, alignment=TA_RIGHT)
        table_styles[f"cell_{size_name}_center"] = style(f"cell_{size_name}_c", size, leading, alignment=TA_CENTER)
        table_styles[f"head_{size_name}"] = style(
            f"head_{size_name}", size, leading, fontName=FONT_BOLD, textColor=WHITE
        )
        table_styles[f"head_{size_name}_right"] = style(
            f"head_{size_name}_r", size, leading, fontName=FONT_BOLD, textColor=WHITE, alignment=TA_RIGHT
        )
        table_styles[f"head_{size_name}_center"] = style(
            f"head_{size_name}_c", size, leading, fontName=FONT_BOLD, textColor=WHITE, alignment=TA_CENTER
        )
    return {
        "body": style("body", 9.2, 13.4, spaceAfter=6),
        "lead": style("lead", 9.8, 14.2, textColor=NAVY, spaceAfter=7),
        "note": style("note", 8.0, 11.0, textColor=MUTED, spaceAfter=6, fontName=FONT_ITALIC),
        "heading": style("heading", 10.5, 14.0, fontName=FONT_BOLD, textColor=NAVY, spaceBefore=6, spaceAfter=4),
        "section": style("section", 16.0, 20.0, fontName=FONT_BOLD, textColor=NAVY, spaceAfter=2),
        "section_sba": style("section_sba", 8.0, 10.5, textColor=MUTED, spaceAfter=10),
        "table_title": style("table_title", 9.0, 12.0, fontName=FONT_BOLD, textColor=NAVY, spaceBefore=8, spaceAfter=3),
        "table_note": style("table_note", 7.2, 9.4, textColor=MUTED, fontName=FONT_ITALIC, spaceBefore=2, spaceAfter=8),
        "caption": style("caption", 7.6, 10.0, textColor=MUTED, fontName=FONT_ITALIC, spaceBefore=3, spaceAfter=8),
        "fact_label": style("fact_label", 7.2, 9.0, textColor=MUTED),
        "fact_value": style("fact_value", 12.5, 15.0, fontName=FONT_BOLD, textColor=NAVY),
        "cover_product": style("cover_product", 13.0, 16.0, fontName=FONT_BOLD, textColor=TEAL),
        "cover_title": style("cover_title", 24.0, 29.0, fontName=FONT_BOLD, textColor=NAVY, spaceAfter=6),
        "cover_business": style("cover_business", 17.0, 21.0, fontName=FONT_BOLD, textColor=INK, spaceAfter=2),
        "cover_subtitle": style("cover_subtitle", 11.0, 14.5, textColor=MUTED, spaceAfter=10),
        "cover_meta": style("cover_meta", 8.4, 11.5, textColor=MUTED),
        "banner": style("banner", 9.6, 13.0, fontName=FONT_BOLD, textColor=WARN, alignment=TA_CENTER),
        "disclaimer": style("disclaimer", 9.0, 12.5, fontName=FONT_BOLD, textColor=INK),
        **table_styles,
    }

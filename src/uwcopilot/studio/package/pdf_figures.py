"""The package's figures as PNG bytes, drawn with matplotlib (Agg, no display, no tiles, no network).

- map: the static location map from the package's GeoJSON layer (mapdata.py): tract centroids colored by the fifth
  of nearby tracts their median household income falls in (hollow: no data), the near and market rings and the site.
  Longitude is scaled by the cosine of the site's latitude so distances look right; there are no axes and no
  numbers on the map (the ring radii are in the caption, from the ledger).
- cash_band: the first year's cash balance, P10-P90 band and P50 line, from the ledger's SIM.m<k>.cash_* claims.
- tornado: the largest swings of year-2 median net income, from the ledger's TORNADO.r<k>.swing claims.
Chart axes carry scale marks (dates, dollar ticks such as "$200k"); every plotted value is also printed in a table
of the package with its claim, and the audit reads the tables.
"""

import io
import math

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter

from uwcopilot.evidence.ledger_models import Ledger
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.prose import input_phrase
from uwcopilot.studio.package.models import MapLayer

DPI = 200
FIFTH_COLORS = ("#DCEAF2", "#A9CCE0", "#6FA8C9", "#3B7FAE", "#1F4E79")
FIFTH_LABELS = ("lowest fifth", "second fifth", "middle fifth", "fourth fifth", "highest fifth")
NAVY = "#1F3A5F"
TEAL = "#2A7F8E"
SITE_RED = "#B42318"
MUTED = "#5B6573"
GRID = "#E3E8EE"
FONT_SIZE = 7.5


def png_bytes(figure: Figure) -> bytes:
    """The figure as PNG bytes."""
    FigureCanvasAgg(figure)
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=DPI, bbox_inches="tight", facecolor="white")
    return buffer.getvalue()


def projector(lat0: float, lon0: float):  # noqa: ANN201 - a small closure
    """(lon, lat) -> (x, y) in degrees of latitude, longitude scaled by cos(lat0)."""
    scale = math.cos(math.radians(lat0))

    def project(lon: float, lat: float) -> tuple[float, float]:
        return (lon - lon0) * scale, lat - lat0

    return project


def draw_map(layer: MapLayer, width_in: float = 7.0, height_in: float = 3.6) -> bytes:
    """The static location map (module docstring)."""
    figure = Figure(figsize=(width_in, height_in))
    axes = figure.add_subplot(1, 1, 1)
    project = projector(layer.site_lat, layer.site_lon)
    rings: dict[str, list[tuple[float, float]]] = {}
    known: dict[int, list[tuple[float, float]]] = {fifth: [] for fifth in range(1, 6)}
    unknown: list[tuple[float, float]] = []
    for feature in layer.geojson["features"]:
        kind = feature["properties"]["kind"]
        geometry = feature["geometry"]
        if kind == "tract":
            point = project(*geometry["coordinates"])
            fifth = feature["properties"].get("fifth")
            (known[fifth] if fifth in known else unknown).append(point)
        elif kind in ("near_ring", "market_ring"):
            rings[kind] = [project(lon, lat) for lon, lat in geometry["coordinates"][0]]
    total = sum(len(points) for points in known.values()) + len(unknown)
    size = 22 if total < 150 else 12
    if "market_ring" in rings:
        xs, ys = zip(*rings["market_ring"], strict=True)
        axes.fill(xs, ys, color=TEAL, alpha=0.07, zorder=1)
        axes.plot(xs, ys, color=TEAL, linewidth=1.2, zorder=2)
    if "near_ring" in rings:
        xs, ys = zip(*rings["near_ring"], strict=True)
        axes.plot(xs, ys, color=TEAL, linewidth=1.0, linestyle=(0, (4, 2)), zorder=2)
    for fifth, points in known.items():
        if points:
            xs, ys = zip(*points, strict=True)
            axes.scatter(xs, ys, s=size, color=FIFTH_COLORS[fifth - 1], edgecolors="#8FA3B5", linewidths=0.3, zorder=3)
    if unknown:
        xs, ys = zip(*unknown, strict=True)
        axes.scatter(xs, ys, s=size, facecolors="none", edgecolors="#9AA4AE", linewidths=0.6, zorder=3)
    axes.scatter([0.0], [0.0], marker="*", s=230, color=SITE_RED, edgecolors="white", linewidths=0.8, zorder=5)
    axes.set_aspect("equal")
    axes.set_axis_off()
    axes.annotate(
        "N",
        xy=(1.08, 0.98),
        xytext=(1.08, 0.84),
        xycoords="axes fraction",
        ha="center",
        va="center",
        fontsize=8,
        color=MUTED,
        arrowprops={"arrowstyle": "-|>", "color": MUTED, "linewidth": 0.8},
        annotation_clip=False,
    )
    handles = [
        Line2D([], [], marker="o", linestyle="", markersize=5, color=color, markeredgecolor="#8FA3B5", label=label)
        for color, label in zip(FIFTH_COLORS, FIFTH_LABELS, strict=True)
    ]
    handles += [
        Line2D(
            [],
            [],
            marker="o",
            linestyle="",
            markersize=5,
            markerfacecolor="none",
            markeredgecolor="#9AA4AE",
            label="no data",
        ),
        Line2D([], [], marker="*", linestyle="", markersize=10, color=SITE_RED, label="chosen site"),
        Line2D([], [], color=TEAL, linestyle=(0, (4, 2)), linewidth=1.0, label="near ring"),
        Line2D([], [], color=TEAL, linewidth=1.2, label="market ring"),
    ]
    legend = axes.legend(
        handles=handles,
        loc="center left",
        bbox_to_anchor=(1.02, 0.42),
        fontsize=FONT_SIZE,
        frameon=False,
        title="Tract median household income",
        title_fontsize=FONT_SIZE,
    )
    legend.get_title().set_color(MUTED)
    return png_bytes(figure)


def dollars_k(value: float, _position: object = None) -> str:
    """A dollar scale mark: $1.2M, $250k, $0, -$40k."""
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.0f}k"
    return f"{sign}${amount:.0f}"


def style_axes(axes: object) -> None:
    """Light grid, no top/right spines, small muted ticks."""
    axes.grid(axis="y", color=GRID, linewidth=0.6)  # type: ignore[attr-defined]
    axes.set_axisbelow(True)  # type: ignore[attr-defined]
    for side in ("top", "right"):
        axes.spines[side].set_visible(False)  # type: ignore[attr-defined]
    for side in ("left", "bottom"):
        axes.spines[side].set_color("#C3CBD5")  # type: ignore[attr-defined]
    axes.tick_params(labelsize=FONT_SIZE, colors=MUTED)  # type: ignore[attr-defined]


def claim_float(ledger: Ledger, claim_id: str) -> float | None:
    """A claim's numeric value (None when missing or not a number)."""
    item = ledger.claim(claim_id)
    if item is None or not isinstance(item.value, int | float) or isinstance(item.value, bool):
        return None
    return float(item.value)


def draw_cash_band(ledger: Ledger, months: list[str], width_in: float = 7.0, height_in: float = 2.5) -> bytes:
    """First-year cash balance: P10-P90 band and the P50 line (module docstring)."""
    xs, low, mid, high, labels = [], [], [], [], []
    for index, calendar in enumerate(months, start=1):
        values = [claim_float(ledger, ids.month_id(index, f"cash_{p}")) for p in ("p10", "p50", "p90")]
        if any(value is None for value in values):
            break
        xs.append(index)
        low.append(values[0])
        mid.append(values[1])
        high.append(values[2])
        labels.append(calendar)
    figure = Figure(figsize=(width_in, height_in))
    axes = figure.add_subplot(1, 1, 1)
    axes.fill_between(xs, low, high, color=TEAL, alpha=0.18, linewidth=0, label="P10 to P90")
    axes.plot(xs, mid, color=NAVY, linewidth=1.6, label="P50 (median)")
    axes.axhline(0, color=SITE_RED, linewidth=0.8, linestyle=(0, (3, 2)))
    axes.set_xticks(xs)
    axes.set_xticklabels(labels, rotation=35, ha="right")
    axes.yaxis.set_major_formatter(FuncFormatter(dollars_k))
    style_axes(axes)
    axes.legend(fontsize=FONT_SIZE, frameon=False, loc="upper left")
    return png_bytes(figure)


def draw_tornado(ledger: Ledger, refs: list[str], width_in: float = 7.0, height_in: float = 2.6) -> bytes:
    """The largest swings of year-2 median net income (module docstring)."""
    names, swings = [], []
    for claim_id in refs:
        swing = claim_float(ledger, claim_id)
        rank = claim_id.split(".")[1]
        name = ledger.claim(f"TORNADO.{rank}.input")
        if swing is None or name is None:
            continue
        names.append(input_phrase(str(name.value)).removeprefix("the "))
        swings.append(swing)
    figure = Figure(figsize=(width_in, height_in))
    axes = figure.add_subplot(1, 1, 1)
    positions = list(range(len(swings)))[::-1]
    colors = [NAVY if index < 3 else "#8FA3B5" for index in range(len(swings))]
    axes.barh(positions, swings, color=colors, height=0.62)
    axes.set_yticks(positions)
    axes.set_yticklabels(names)
    axes.xaxis.set_major_formatter(FuncFormatter(dollars_k))
    style_axes(axes)
    axes.grid(axis="x", color=GRID, linewidth=0.6)
    axes.grid(axis="y", visible=False)
    return png_bytes(figure)

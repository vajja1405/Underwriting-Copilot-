"""Sections 3 and 4: the market and location analysis, and the site comparison (SBA "Market analysis").

3. Market and location analysis: the static map (tract centroids, rings, the site), SiteIQ's location factors
   (demographics, traffic, competition, anchors, rent) with their status and score, the competition details, the
   site's demand range and the metro inputs. A metro without tract data here (limited mode) gets a plain statement
   instead: no factor is filled in (Studio rule G).
4. Site comparison: the chosen site and every other candidate site under the same plan, seed and number of paths.
"""

from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.prose import FACTOR_PHRASES
from uwcopilot.studio.package.blocks import BuildContext, claim, slot, status, text, value_range
from uwcopilot.studio.package.claims_sites import CANDIDATE_PREFIX, CHOSEN_SITE_KEY, candidate_key
from uwcopilot.studio.package.constants import SIMULATION_LABEL
from uwcopilot.studio.package.models import Block, Cell, Figure, KeyFact, KeyFacts, Paragraph, Table

MODE_NOTES: dict[str, str] = {
    "synthetic": "This is the SYNTHETIC metro: generated data for demonstration, not a real place.",
    "limited": (
        "This metro runs in limited mode in this environment: its data coverage is under the threshold, so factors "
        "without data are UNKNOWN and left out of the score, never filled in."
    ),
    "full": "This metro runs in full mode: its data coverage meets the threshold.",
}
NO_SITEIQ_NOTE = (
    "No SiteIQ result for this plan: this metro has no tract data in this environment (limited mode). The package "
    "lists no location factors rather than inventing them; the simulation used the demand range below."
)
COMPETITION_ROWS: tuple[tuple[str, str], ...] = (
    ("Same-type businesses within the near ring", ids.site_factor_id("competition")),
    ("Same-type businesses within the market ring", "PKG.site.competition.count_market"),
    ("Distance to the nearest one (miles)", "PKG.site.competition.nearest_miles"),
    (
        "Competitor density in the market ring (SiteIQ's per-resident measure)",
        "PKG.site.competition.per_10k_residents_market",
    ),
)
METRO_INPUTS: tuple[tuple[str, str], ...] = (
    ("Commercial rent for the metro", "SITE.metro.commercial_rent"),
    ("Local wage for the industry", "SITE.metro.local_wages"),
    ("Prime rate, percent per year", "PKG.site.metro.prime_rate_pct"),
)


# Package wording for factors whose SiteIQ description does not match the area SiteIQ measured them over.
FACTOR_WORDS: dict[str, str] = {
    "median_household_income": "household-weighted mean of the tract median household incomes in the area",
}


def area_word(area: str, near: float, market: float) -> str:
    """The area a factor value describes, in words (near ring, market ring, nearest road, whole metro)."""
    lowered = area.lower()
    if "metro" in lowered:
        return "whole metro"
    if "road" in lowered:
        return "nearest road"
    for miles, words in ((near, "near ring"), (market, "market ring")):
        if f"{miles:g} mile" in lowered:
            return words
    return "site's tract"


def factor_words(factor: str, description: str) -> str:
    """A location factor in plain words."""
    return FACTOR_WORDS.get(factor) or FACTOR_PHRASES.get(factor, description)


def factor_table(ctx: BuildContext) -> Table:
    """SiteIQ's location factors with their values, areas, scores and statuses."""
    score = ctx.site_score
    assert score is not None
    rows: list[list[Cell]] = []
    area = score.trade_area
    for factor in score.factors:
        value_id = ids.site_factor_id(factor.factor)
        if not ctx.has(value_id):
            continue
        score_id = f"{value_id}.score"
        rows.append(
            [
                text(factor_words(factor.factor, factor.description)),
                claim(value_id),
                text(area_word(factor.area, area.near_miles, area.market_miles)),
                claim(score_id) if ctx.has(score_id) else text("not scored"),
                status(value_id),
            ]
        )
    return Table(
        title="Location factors (SiteIQ)",
        header=["Factor", "Value", "Area", "Score (percentile rank in the metro)", "Status"],
        rows=rows,
        widths=[2.8, 1.3, 1.4, 1.5, 1.2],
        align=["left", "right", "left", "right", "left"],
        note="Scores rank the site against every tract of the metro; a factor without data is not scored.",
    )


def competition_table(ctx: BuildContext) -> Table | None:
    """The competition counts and distances that exist in the ledger."""
    rows = [
        [text(label), claim(claim_id), status(claim_id)] for label, claim_id in COMPETITION_ROWS if ctx.has(claim_id)
    ]
    if not rows:
        return None
    return Table(
        title="Competition near the site",
        header=["Measure", "Value", "Status"],
        rows=rows,
        widths=[4.4, 1.6, 2.0],
        align=["left", "right", "left"],
    )


def demand_table(ctx: BuildContext) -> Table:
    """The site's demand range the simulation used, and the metro inputs."""
    rows = [
        [
            text("Most likely demand after ramp-up"),
            claim(ids.SITE_DEMAND_MODE),
            value_range(ids.SITE_DEMAND_MODE),
            status(ids.SITE_DEMAND_MODE),
        ],
        [text("Low end of the demand range"), claim("SITE.demand_low"), text(""), status("SITE.demand_low")],
        [text("High end of the demand range"), claim("SITE.demand_high"), text(""), status("SITE.demand_high")],
    ]
    rows += [
        [text(label), claim(claim_id), value_range(claim_id), status(claim_id)]
        for label, claim_id in METRO_INPUTS
        if ctx.has(claim_id)
    ]
    return Table(
        title=f"Demand at the site and metro inputs (demand in {ctx.feasibility.demand_unit.replace('/', ' per ')})",
        header=["Input", "Value", "Range", "Status"],
        rows=rows,
        widths=[3.0, 1.4, 2.2, 1.4],
        align=["left", "right", "right", "left"],
    )


def map_caption(ctx: BuildContext) -> str:
    """The map's caption as a slot draft (ring radii from the ledger). The tract data's own label decides the words:
    the SYNTHETIC city's generated tracts, or a real metro's public Census tracts (never called SYNTHETIC)."""
    score = ctx.site_score
    synthetic = score is not None and score.data_label == "SYNTHETIC"
    tracts = "the SYNTHETIC city's generated tract centroids" if synthetic else "public Census Gazetteer tract points"
    return (
        f"Static map drawn from {tracts} (no map tiles). Each dot is one tract, colored by the fifth "
        f"of nearby tracts its median household income falls in (hollow: no data); the star is the chosen site. "
        f"Ring radii in miles: near ring {slot('PKG.map.near_miles')}, market ring {slot('PKG.map.market_miles')}."
    )


def market_section(ctx: BuildContext, has_map: bool) -> list[Block]:
    """Section 3 (module docstring)."""
    mode = ctx.feasibility.metro_mode
    blocks: list[Block] = [Paragraph(draft=MODE_NOTES.get(mode, f"Metro mode: {mode}."), style="note")]
    if ctx.site_score is None:
        blocks.append(Paragraph(draft=NO_SITEIQ_NOTE))
        blocks.append(demand_table(ctx))
        return blocks
    if has_map:
        blocks.append(Figure(figure="map", caption=map_caption(ctx), height_in=3.6))
    facts = [
        KeyFact(label=label, ref=claim_id)
        for label, claim_id in (
            ("SiteIQ overall site score", ids.SITE_OVERALL),
            ("SiteIQ data coverage of the site", ids.SITE_COVERAGE),
        )
        if ctx.has(claim_id)
    ]
    if facts:
        blocks.append(KeyFacts(title="SiteIQ summary", items=facts))
    blocks.append(factor_table(ctx))
    competition = competition_table(ctx)
    if competition is not None:
        blocks.append(competition)
    blocks.append(demand_table(ctx))
    return blocks


def site_row(ctx: BuildContext, key: str, prefix_ids: dict[str, str]) -> list[Cell]:
    """One site of the comparison (prefix_ids: which claim answers each column)."""
    return [
        claim(prefix_ids["label"]),
        claim(prefix_ids["demand_mode"]),
        value_range(prefix_ids["demand_mode"]),
        claim(prefix_ids["p_dscr"]) if ctx.has(prefix_ids["p_dscr"]) else text("not simulated"),
        claim(prefix_ids["p_stressed"]) if ctx.has(prefix_ids["p_stressed"]) else text("not simulated"),
        claim(prefix_ids["p_cash"]) if ctx.has(prefix_ids["p_cash"]) else text("not simulated"),
        status(prefix_ids["demand_mode"]),
    ]


def comparison_section(ctx: BuildContext) -> list[Block]:
    """Section 4 (module docstring)."""
    chosen = {
        "label": f"{CANDIDATE_PREFIX}.{CHOSEN_SITE_KEY}.label",
        "demand_mode": ids.SITE_DEMAND_MODE,
        "p_dscr": ids.SIM_P_DSCR_Y2,
        "p_stressed": ids.SIM_P_STRESSED_Y2,
        "p_cash": ids.SIM_P_CASH,
    }
    rows = [site_row(ctx, "chosen", chosen)]
    for index in range(1, len(ctx.project.candidates) + 1):
        prefix = f"{CANDIDATE_PREFIX}.{candidate_key(index)}"
        rows.append(
            site_row(
                ctx,
                candidate_key(index),
                {
                    "label": f"{prefix}.label",
                    "demand_mode": f"{prefix}.demand_mode",
                    "p_dscr": f"{prefix}.p_dscr_pct",
                    "p_stressed": f"{prefix}.p_stressed_pct",
                    "p_cash": f"{prefix}.p_cash_negative_pct",
                },
            )
        )
    note = (
        "The first row is the chosen site. Every site is simulated with the same plan, seed and number of paths "
        "(common random numbers); a site without its own rent quote uses the metro's rent range. Demand is the most "
        f"likely value after ramp-up, with its range. Probabilities are {SIMULATION_LABEL}."
    )
    blocks: list[Block] = [
        Table(
            title="Candidate sites under the same plan",
            header=[
                "Site",
                "Demand",
                "Demand range",
                "Chance DSCR reaches the target, year 2",
                "Chance of passing the revenue stress",
                "Chance cash falls below zero",
                "Demand status",
            ],
            rows=rows,
            widths=[1.5, 0.9, 1.6, 1.2, 1.2, 1.1, 1.1],
            align=["left", "right", "right", "right", "right", "right", "left"],
            note=note,
        )
    ]
    if not ctx.project.candidates:
        blocks.append(Paragraph(draft="The plan lists no other candidate sites.", style="note"))
    return blocks

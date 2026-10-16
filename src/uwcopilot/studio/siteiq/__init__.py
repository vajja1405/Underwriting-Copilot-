"""SiteIQ (Studio S2): score a candidate site and suggest better areas, with every number traced to its source.

    from uwcopilot.studio.siteiq import score_site, suggest_areas
    site = score_site("car_wash", "synthetic_metro", lat, lon)
    areas = suggest_areas("car_wash", "synthetic_metro")

Modules:
- models.py       inputs and outputs (SiteScore, FactorScore, DemandEstimate, Suggestions ...);
- trade_area.py   distance rings (profile rings x metro multiplier) and drive-time polygons;
- isochrones.py   OpenRouteService drive-time areas (only with ORS_API_KEY and a live client);
- context.py      MetroContext: a metro's fabric plus per-metro caches;
- factors.py      measures a site's raw factor values with status, reason and sources;
- reference.py    each factor measured at every tract of the metro, for percentile ranks;
- scoring.py      score_site: normalized factors, overall score, coverage score, notes;
- demand.py       the demand estimate (low / mode / high) with every input listed;
- enhancements.py drive-time demand area and city license context (shown, not scored);
- suggest.py      suggest_areas: top tracts, exclusions, budget check, GeoJSON;
- validation.py   checks against the SYNTHETIC city's hidden true demand, and the real-metro sanity table.

Every result carries the Studio disclaimer: SiteIQ ranks locations; it never predicts sales or approves a loan.
"""

from uwcopilot.studio.siteiq.context import MetroContext, SiteOutsideMetro, build_context, metro_context
from uwcopilot.studio.siteiq.models import (
    Budget,
    DemandEstimate,
    FactorScore,
    SiteInputs,
    SiteScore,
    SuggestedArea,
    Suggestions,
)
from uwcopilot.studio.siteiq.scoring import score_site
from uwcopilot.studio.siteiq.suggest import suggest_areas

__all__ = [
    "Budget",
    "DemandEstimate",
    "FactorScore",
    "MetroContext",
    "SiteInputs",
    "SiteOutsideMetro",
    "SiteScore",
    "SuggestedArea",
    "Suggestions",
    "build_context",
    "metro_context",
    "score_site",
    "suggest_areas",
]

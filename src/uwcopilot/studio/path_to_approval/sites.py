"""Candidate sites for the "site" lever, from SiteIQ (S2) results or typed demand ranges.

A candidate keeps its own demand estimate as SiteIQ (or the founder) gave it; the search never edits it. SiteIQ's
SiteScore is read through the simulator's own adapter (feasibility.inputs.as_site_demand), so a change inside
SiteIQ cannot break the search.
"""

from typing import Any

from uwcopilot.studio.feasibility.inputs import as_site_demand
from uwcopilot.studio.path_to_approval.models import Candidate


def candidate_from_site_score(score: Any, label: str | None = None, monthly_rent: float | None = None) -> Candidate:
    """A Candidate from a SiteIQ SiteScore (or anything as_site_demand reads that also has a metro_id).

    label defaults to the score's own label, else "site at <lat>, <lon>". monthly_rent is a rent quote for this
    site, if the founder has one.
    """
    metro_id = getattr(score, "metro_id", None)
    if not metro_id:
        raise ValueError("a candidate site needs the metro it belongs to (metro_id)")
    name = label or getattr(score, "label", None)
    if not name:
        name = f"site at {getattr(score, 'lat', 0.0):.5f}, {getattr(score, 'lon', 0.0):.5f}"
    return Candidate(label=name, metro_id=metro_id, site=as_site_demand(score), monthly_rent=monthly_rent)

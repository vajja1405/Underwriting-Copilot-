"""Trade-area rings in one place, for SiteIQ (siteiq/trade_area.py re-exports these) and the snapshot builder.

Each profile lists rings in miles and each metro scales them (both DESIGN_CHOICE values). SiteIQ counts nearby
places in the NEAR ring (the first ring) and sums tract values in the MARKET ring (the middle ring). The data layer
(snapshot_layers.py, fabric_snapshot.py) measures places in exactly the same rings, so it imports them from here
instead of from SiteIQ (which itself reads the data layer).
"""

from uwcopilot.studio.metros.models import MetroConfig
from uwcopilot.studio.profiles.models import Profile

# A site farther than this from every tract centroid is outside the metro (SiteIQ's own-tract rule). A design choice.
OWN_TRACT_MAX_MILES = 3.0


def scaled_rings(profile: Profile, metro: MetroConfig) -> tuple[float, ...]:
    """The profile's rings times the metro's ring multiplier, in miles."""
    multiplier = metro.trade_area.ring_multiplier
    return tuple(round(ring * multiplier, 3) for ring in profile.design.trade_area_rings_miles)


def near_and_market(rings: tuple[float, ...]) -> tuple[float, float]:
    """(near ring, market ring): the first ring and the middle ring."""
    return rings[0], rings[len(rings) // 2]

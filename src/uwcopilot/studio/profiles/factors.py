"""The shared vocabulary of location factors and demand formulas.

Profiles (S1) name these factors in their weights and demand formulas, the data fabric (S1) says where each factor's
data comes from, SiteIQ (S2) scores them and the coverage report counts them. Keeping the names in one module means
a typo in a YAML file fails a test instead of silently dropping a factor.

Nothing here is a number about the world. The formulas are the structure of the demand estimate; every rate they use
comes from the profile, labeled SOURCED or ASSUMPTION.
"""

from dataclasses import dataclass
from typing import Literal

# Where a factor's value lives: one value per census tract, a point layer (POIs, road segments) searched around a
# site, or one value for the whole metro.
FactorScope = Literal["tract", "poi_layer", "road_layer", "metro"]


@dataclass(frozen=True)
class LocationFactor:
    """One factor a site can be scored on."""

    name: str
    description: str
    unit: str
    scope: FactorScope


LOCATION_FACTORS: dict[str, LocationFactor] = {
    factor.name: factor
    for factor in (
        LocationFactor("resident_population", "People living in the trade area", "persons", "tract"),
        LocationFactor("daytime_workers", "People working in the trade area (daytime population)", "workers", "tract"),
        LocationFactor("households", "Households in the trade area", "households", "tract"),
        LocationFactor("renter_households", "Renter-occupied households in the trade area", "households", "tract"),
        LocationFactor("median_household_income", "Median household income of the site's tract", "USD/year", "tract"),
        LocationFactor(
            "traffic_aadt", "Annual average daily traffic on the nearest road segment", "vehicles/day", "road_layer"
        ),
        LocationFactor(
            "competition", "Same-type businesses near the site (profile competitor tags)", "count", "poi_layer"
        ),
        LocationFactor(
            "anchors", "Complementary traffic generators near the site (profile anchor tags)", "count", "poi_layer"
        ),
        LocationFactor("tourism", "Tourism anchors near the site (attractions, venues, lodging)", "count", "poi_layer"),
        LocationFactor(
            "str_comparables", "Short-term rental listings near the site, with nightly prices", "count", "poi_layer"
        ),
        LocationFactor(
            "commercial_rent", "Commercial rent: founder quote, else the metro ASSUMPTION range", "USD/month", "metro"
        ),
        LocationFactor("local_wages", "Average wage for the profile's industry in the metro", "USD/hour", "metro"),
        LocationFactor(
            "prime_rate",
            "A published bank prime loan rate (the S3 default rate adds a stated spread)",
            "percent",
            "metro",
        ),
    )
}

# Factors read once per metro, for the simulator (S3), whatever the profile's weights are.
METRO_LEVEL_INPUTS: tuple[str, ...] = ("commercial_rent", "local_wages", "prime_rate")

# OpenStreetMap tag filters that count toward the "tourism" factor (the same for every profile). A design choice of
# the project author; the tag names follow the OpenStreetMap wiki and are to be re-checked there.
TOURISM_TAGS: tuple[str, ...] = (
    "tourism=attraction",
    "tourism=museum",
    "tourism=theme_park",
    "tourism=zoo",
    "tourism=viewpoint",
    "leisure=stadium",
)


@dataclass(frozen=True)
class DemandFormula:
    """The structure of one business type's demand estimate.

    required_inputs are the profile rates the formula uses (each a Quantity in the profile YAML). data_factors are
    the location factors it reads from the data fabric.
    """

    name: str
    description: str
    demand_unit: str
    required_inputs: tuple[str, ...]
    data_factors: tuple[str, ...]


DEMAND_FORMULAS: dict[str, DemandFormula] = {
    formula.name: formula
    for formula in (
        DemandFormula(
            name="population_visit_rate",
            description=(
                "covers/day = (residents x visits_per_resident_per_month + daytime workers x "
                "visits_per_worker_per_month) / 30 x capture_share"
            ),
            demand_unit="covers/day",
            required_inputs=("visits_per_resident_per_month", "visits_per_worker_per_month", "capture_share"),
            data_factors=("resident_population", "daytime_workers"),
        ),
        DemandFormula(
            name="aadt_capture",
            description="cars/day = AADT on the frontage road x capture_rate",
            demand_unit="cars/day",
            required_inputs=("capture_rate",),
            data_factors=("traffic_aadt",),
        ),
        DemandFormula(
            name="rooms_occupancy",
            description="room-nights/day = rooms x occupancy (revenue = room-nights x ADR, the profile price)",
            demand_unit="room-nights/day",
            required_inputs=("rooms", "occupancy"),
            data_factors=(),
        ),
        DemandFormula(
            name="nights_comparables",
            description=(
                "booked nights/month = nights_available_per_month x occupancy (occupancy and ADR from comparable "
                "listings where the metro has them)"
            ),
            demand_unit="booked nights/month",
            required_inputs=("nights_available_per_month", "occupancy"),
            data_factors=("str_comparables",),
        ),
        DemandFormula(
            name="renter_usage",
            description=(
                "machine turns/day = renter households x share_of_renters_using x turns_per_user_household_per_week "
                "/ 7 x capture_share"
            ),
            demand_unit="machine turns/day",
            required_inputs=("share_of_renters_using", "turns_per_user_household_per_week", "capture_share"),
            data_factors=("renter_households",),
        ),
        DemandFormula(
            name="traffic_conversion",
            description=(
                "transactions/day = AADT x drive_by_conversion + residents x visits_per_resident_per_month / 30 x "
                "capture_share (revenue = transactions x basket, the profile price)"
            ),
            demand_unit="transactions/day",
            required_inputs=("drive_by_conversion", "visits_per_resident_per_month", "capture_share"),
            data_factors=("traffic_aadt", "resident_population"),
        ),
    )
}

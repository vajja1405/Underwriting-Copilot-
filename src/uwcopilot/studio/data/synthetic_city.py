"""The SYNTHETIC city: a seeded, generated metro with known "true" demand, for demos, tests and validation.

Nothing here describes a real place. The city sits in open ocean (config/studio/metros/synthetic_metro.yaml) so its
coordinates can never be mistaken for a real neighborhood, its tract ids start with "SYN", and every value it produces
is labeled SYNTHETIC. Every constant below is a generator parameter chosen by the project author, not data.

What it generates (generate_city):
1. Tracts: centroids clustered around a downtown and three sub-centers, each with resident population, households,
   renter households, median household income (a few values suppressed, like real survey tables) and daytime workers.
2. Roads: radial highways, a ring highway and an arterial grid, cut into ~1-mile segments with AADT.
3. Points of interest: every competitor and anchor tag used by the business-type profiles, plus tourism tags.
   Existing businesses cluster where demand is, as in real cities (this makes competition hard to read).
4. Short-term rental listings with nightly prices.
5. Metro-level SYNTHETIC assumptions: a wage index, a rent index and a prime rate.
6. HIDDEN true demand per tract and business type (the demand a new site at the tract centroid would see).

Why the true demand is NOT SiteIQ's formula (so the S2 validation is not circular): it uses smooth gravity decay
instead of distance rings, a Huff-style saturating competition share instead of a count, interactions between drivers
(for example office workers x office anchors, drive-by traffic x walk-in traffic), saturation curves on income and
market size, a spatially correlated latent "neighborhood appeal" field that no feature exposes, and multiplicative
noise. It also uses the true income where the published table is suppressed. See TRUE_DEMAND_NOTES.
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np

from uwcopilot.studio.data.geo import haversine_miles_array, miles_to_degrees
from uwcopilot.studio.data.osm import Poi, tag_matches
from uwcopilot.studio.data.str_comps import StrListing
from uwcopilot.studio.data.traffic_hpms import RoadSegment
from uwcopilot.studio.profiles.factors import TOURISM_TAGS
from uwcopilot.studio.profiles.models import Profile

SYNTHETIC_LABEL = "SYNTHETIC"
TRACT_PREFIX = "SYN"
# Share of tracts whose median income is suppressed in the published table (the truth still uses the real value).
INCOME_SUPPRESSED_SHARE = 0.03
SEGMENT_LENGTH_MILES = 1.0
FRONTAGE_MILES = 0.3

TRUE_DEMAND_NOTES: dict[str, str] = {
    "restaurant_cafe": "covers/day = (resident gravity (decay 0.8 mi) + worker gravity (decay 0.5 mi) x office-anchor "
    "boost) x logistic income effect x Huff competition share x latent appeal x noise",
    "car_wash": "cars/day = (max AADT within 0.3 mi)^0.85 x (1 + household-gravity interaction) x logistic income "
    "effect x Huff competition share x latent appeal x noise",
    "motel": "room-nights/day = 40 rooms x occupancy; occupancy saturates in tourism, highway-junction x traffic and "
    "hospital/airport pull, divided by a lodging-competition term, capped at 0.95, x noise",
    "short_term_rental": "booked nights/month = 28 x occupancy; occupancy saturates in tourism, park and station pull, "
    "x income appeal / listing density, capped at 0.95, x noise",
    "laundromat": "machine turns/day = (renter gravity (decay 0.6 mi))^0.9 x decreasing income effect x Huff "
    "competition share x latent appeal x noise",
    "retail_storefront": "transactions/day = (drive-by + walk-in + their interaction) x inverted-U income effect "
    "x Huff competition share x latent appeal x noise",
}


@dataclass(frozen=True)
class SyntheticTract:
    """One generated tract. median_household_income is None where the 'published table' suppresses it."""

    geoid: str
    lat: float
    lon: float
    land_area_sq_miles: float
    resident_population: float
    households: float
    renter_households: float
    median_household_income: float | None
    daytime_workers: float
    district: str


@dataclass
class SyntheticCity:
    """The generated city. true_demand is hidden ground truth: only validation code may read it."""

    metro_id: str
    seed: int
    origin_lat: float
    origin_lon: float
    tracts: list[SyntheticTract]
    roads: list[RoadSegment]
    pois: list[Poi]
    str_listings: list[StrListing]
    wage_index: float
    rent_index: float
    prime_rate_pct: float
    true_demand: dict[str, dict[str, float]] = field(default_factory=dict)
    data_label: str = SYNTHETIC_LABEL

    def tract_ids(self) -> list[str]:
        """Tract ids in generation order."""
        return [tract.geoid for tract in self.tracts]


@dataclass
class Layout:
    """Positions in miles east (x) and north (y) of downtown, used while generating."""

    extent: float
    subcenters: np.ndarray
    tourist_center: np.ndarray
    wealthy_angle: float
    x: np.ndarray
    y: np.ndarray


def to_lat_lon(origin_lat: float, origin_lon: float, x_miles: float, y_miles: float) -> tuple[float, float]:
    """Latitude/longitude of a point x miles east and y miles north of the origin."""
    dlat, dlon = miles_to_degrees(origin_lat, y_miles, x_miles)
    return origin_lat + dlat, origin_lon + dlon


def pairwise_miles(x1: np.ndarray, y1: np.ndarray, x2: np.ndarray, y2: np.ndarray) -> np.ndarray:
    """Planar distances in miles between two point sets (rows: first set, columns: second set)."""
    return np.hypot(x1[:, None] - x2[None, :], y1[:, None] - y2[None, :])


def place_tracts(rng: np.random.Generator, n: int, extent: float) -> Layout:
    """Tract centroids: 35% around downtown, 30% around three sub-centers, the rest spread over the square."""
    half = extent / 2
    angles = rng.uniform(0, 2 * math.pi, size=3)
    radii = rng.uniform(0.25 * half, 0.5 * half, size=3)
    subcenters = np.column_stack([radii * np.cos(angles), radii * np.sin(angles)])
    tourist_angle = rng.uniform(0, 2 * math.pi)
    tourist_center = np.array([math.cos(tourist_angle), math.sin(tourist_angle)]) * rng.uniform(0.1, 0.2) * half
    n_core = int(0.35 * n)
    n_sub = int(0.10 * n)
    parts = [rng.normal(0, 0.12 * half, size=(n_core, 2))]
    for center in subcenters:
        parts.append(center + rng.normal(0, 0.09 * half, size=(n_sub, 2)))
    parts.append(rng.uniform(-half, half, size=(n - n_core - 3 * n_sub, 2)))
    points = np.clip(np.vstack(parts), -half, half)
    return Layout(
        extent=extent,
        subcenters=subcenters,
        tourist_center=tourist_center,
        wealthy_angle=rng.uniform(0, 2 * math.pi),
        x=points[:, 0],
        y=points[:, 1],
    )


def latent_field(rng: np.random.Generator, x: np.ndarray, y: np.ndarray, extent: float) -> np.ndarray:
    """A smooth hidden 'neighborhood appeal' field (mean 0, sd 1): a sum of random Gaussian bumps."""
    half = extent / 2
    centers = rng.uniform(-half, half, size=(12, 2))
    signs = rng.choice([-1.0, 1.0], size=12)
    width = 0.12 * extent
    values = np.zeros_like(x)
    for (cx, cy), sign in zip(centers, signs, strict=True):
        values += sign * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * width**2))
    return (values - values.mean()) / (values.std() + 1e-9)


def generate_tract_values(rng: np.random.Generator, layout: Layout) -> dict[str, np.ndarray]:
    """Population, households, renters, true income and daytime workers per tract (all SYNTHETIC)."""
    x, y, half = layout.x, layout.y, layout.extent / 2
    n = len(x)
    d_core = np.hypot(x, y)
    d_sub = np.min(pairwise_miles(x, y, layout.subcenters[:, 0], layout.subcenters[:, 1]), axis=1)
    suburban = np.clip(d_core / half, 0, 1)
    population = np.clip(rng.lognormal(math.log(4000), 0.3, size=n), 1200, 9000)
    household_size = np.clip(2.1 + 0.9 * suburban + rng.normal(0, 0.15, size=n), 1.6, 3.6)
    households = population / household_size
    renter_share = np.clip(0.25 + 0.5 * np.exp(-d_core / (0.25 * half)) + rng.normal(0, 0.08, size=n), 0.05, 0.95)
    angle = np.arctan2(y, x)
    wedge = np.cos(angle - layout.wealthy_angle).clip(0, None) * np.clip(d_core / (0.3 * half), 0, 1)
    log_income = math.log(56000) + 0.5 * wedge + 0.15 * suburban - 0.5 * (renter_share - 0.4) + rng.normal(0, 0.3, n)
    income = np.clip(np.exp(log_income), 18000, 250000)
    job_pull = 6.0 * np.exp(-d_core / (0.08 * half)) + 2.0 * np.exp(-d_sub / (0.06 * half)) + 0.25
    workers = job_pull / job_pull.sum() * 0.5 * population.sum() * rng.lognormal(0, 0.25, size=n)
    return {
        "resident_population": np.round(population),
        "households": np.round(households),
        "renter_households": np.round(households * renter_share),
        "true_income": np.round(income, -2),
        "daytime_workers": np.round(workers),
        "d_core": d_core,
        "suburban": suburban,
    }


def district_of(d_core: float, half: float) -> str:
    """A plain label for where a tract sits (used in reasons and maps only)."""
    if d_core < 0.15 * half:
        return "downtown"
    if d_core < 0.45 * half:
        return "inner ring"
    return "outer suburbs"


def polyline_segments(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Cut a polyline (in miles) into pieces of about SEGMENT_LENGTH_MILES."""
    pieces: list[list[tuple[float, float]]] = []
    current = [points[0]]
    length = 0.0
    for previous, point in zip(points, points[1:], strict=False):
        length += math.dist(previous, point)
        current.append(point)
        if length >= SEGMENT_LENGTH_MILES:
            pieces.append(current)
            current, length = [point], 0.0
    if len(current) > 1:
        pieces.append(current)
    return pieces


def generate_roads(rng: np.random.Generator, layout: Layout) -> list[tuple[list[tuple[float, float]], int, int, str]]:
    """Road pieces in miles: (points, AADT, functional class, route id). Highways carry more traffic near downtown."""
    half = layout.extent / 2
    roads: list[tuple[list[tuple[float, float]], int, int, str]] = []
    steps = np.linspace(0, 1, 60)
    for number, angle in enumerate(rng.uniform(0, 2 * math.pi, size=4)):
        line = [(float(half * t * math.cos(angle)), float(half * t * math.sin(angle))) for t in steps]
        for piece in polyline_segments(line):
            mid = piece[len(piece) // 2]
            aadt = 160000 * math.exp(-math.hypot(*mid) / (0.5 * half)) + 25000
            roads.append((piece, int(aadt * rng.lognormal(0, 0.15)), 1, f"SYN-I{number + 1}"))
    ring_radius = 0.55 * half
    ring = [(ring_radius * math.cos(t), ring_radius * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 120)]
    for piece in polyline_segments(ring):
        roads.append((piece, int(70000 * rng.lognormal(0, 0.2)), 1, "SYN-RING"))
    spacing = 0.08 * layout.extent
    for offset in np.arange(-half + spacing / 2, half, spacing):
        for vertical in (True, False):
            line = [((offset, float(t)) if vertical else (float(t), offset)) for t in np.linspace(-half, half, 40)]
            for piece in polyline_segments(line):
                mid = piece[len(piece) // 2]
                busy = math.exp(-math.hypot(*mid) / (0.35 * half))
                aadt = (6000 + 30000 * busy) * rng.lognormal(0, 0.35)
                roads.append(
                    (piece, int(aadt), 3 if busy > 0.4 else 4, f"SYN-ART-{'NS' if vertical else 'EW'}{offset:+.0f}")
                )
    # One local street through every tract centroid, so every candidate site has a frontage road.
    for index, (x, y) in enumerate(zip(layout.x, layout.y, strict=True)):
        angle = rng.uniform(0, math.pi)
        dx, dy = 0.2 * math.cos(angle), 0.2 * math.sin(angle)
        aadt = 2500 * rng.lognormal(0, 0.4)
        roads.append(
            ([(float(x - dx), float(y - dy)), (float(x + dx), float(y + dy))], int(aadt), 5, f"SYN-LOCAL-{index}")
        )
    return roads


def sample_points(
    rng: np.random.Generator, layout: Layout, weights: np.ndarray, count: int, jitter_miles: float = 0.3
) -> np.ndarray:
    """count points placed at tract centroids drawn with probability proportional to weights, plus jitter."""
    if count <= 0:
        return np.zeros((0, 2))
    probabilities = np.clip(weights, 0, None)
    probabilities = probabilities / probabilities.sum()
    chosen = rng.choice(len(layout.x), size=count, p=probabilities)
    jitter = rng.normal(0, jitter_miles, size=(count, 2))
    half = layout.extent / 2
    return np.clip(np.column_stack([layout.x[chosen], layout.y[chosen]]) + jitter, -half, half)


def tag_plan(
    values: dict[str, np.ndarray], layout: Layout, near_highway: np.ndarray
) -> dict[str, tuple[np.ndarray, int]]:
    """{tag: (placement weights per tract, count)} for every tag the generator knows how to place."""
    n = len(layout.x)
    pop = values["resident_population"]
    hh = values["households"]
    renters = values["renter_households"]
    workers = values["daytime_workers"]
    core = np.exp(-values["d_core"] / (0.1 * layout.extent))
    tourist = np.exp(-np.hypot(layout.x - layout.tourist_center[0], layout.y - layout.tourist_center[1]) / 1.5)
    food = pop + 1.5 * workers
    return {
        # Competitors (they cluster where their own demand is).
        "amenity=restaurant": (food, int(1.4 * n)),
        "amenity=cafe": (food, int(0.6 * n)),
        "amenity=fast_food": (food + 0.5 * pop * near_highway, int(0.6 * n)),
        "amenity=ice_cream": (food * (1 + tourist), int(0.12 * n)),
        "shop=bakery": (food, int(0.2 * n)),
        "amenity=car_wash": (hh * (0.3 + near_highway), int(0.12 * n)),
        "tourism=motel": (near_highway + 0.2, int(0.06 * n)),
        "tourism=hotel": (core + 2 * tourist + 0.3 * near_highway, int(0.12 * n)),
        "tourism=guest_house": (tourist + 0.1, int(0.05 * n)),
        "tourism=apartment": (tourist + core, int(0.08 * n)),
        "shop=laundry": (renters**1.2, int(0.25 * n)),
        "shop=dry_cleaning": (hh * values["true_income"], int(0.15 * n)),
        "shop=convenience": (pop + workers, int(0.8 * n)),
        "shop=variety_store": (pop, int(0.15 * n)),
        "shop=kiosk": (workers, int(0.15 * n)),
        "shop=supermarket": (hh, int(0.4 * n)),
        # Anchors.
        "office": (workers**1.3, int(0.8 * n)),
        "amenity=school": (pop, int(0.35 * n)),
        "amenity=university": (workers + core, 3),
        "shop=mall": (near_highway * pop + 1, 5),
        "highway=motorway_junction": (near_highway + 1e-6, int(0.1 * n)),
        "amenity=fuel": (0.2 + near_highway, int(0.25 * n)),
        "shop=car": (near_highway * (1 - core), int(0.06 * n)),
        "aeroway=aerodrome": (values["suburban"] ** 4, 1),
        "amenity=hospital": (pop * (core + 0.3), 4),
        "leisure=stadium": (core + 0.1, 2),
        "amenity=cinema": (workers + pop, 6),
        "amenity=theatre": (core + tourist, 6),
        "leisure=park": (np.ones(n), int(0.3 * n)),
        "tourism=attraction": (tourist + 0.5 * core, 25),
        "tourism=museum": (core + tourist, 8),
        "tourism=theme_park": (tourist * values["suburban"] + 0.01, 1),
        "tourism=zoo": (values["suburban"] + 0.01, 1),
        "tourism=viewpoint": (tourist + 0.05, 6),
    }


def station_points(rng: np.random.Generator, layout: Layout) -> list[tuple[float, float]]:
    """Rail stations every ~1.5 miles along two radial transit lines from downtown."""
    half = layout.extent / 2
    points = [(0.0, 0.0)]
    for angle in rng.uniform(0, 2 * math.pi, size=2):
        for distance in np.arange(1.5, 0.8 * half, 1.5):
            points.append((distance * math.cos(angle), distance * math.sin(angle)))
    return points


def tags_for(tag: str) -> dict[str, str]:
    """The tag dictionary of a POI generated for one tag filter ('office' becomes office=yes)."""
    key, _, value = tag.partition("=")
    return {key: value if value not in ("", "*") else "yes"}


def generate_pois(
    rng: np.random.Generator,
    layout: Layout,
    values: dict[str, np.ndarray],
    near_highway: np.ndarray,
    required_tags: set[str],
    origin: tuple[float, float],
) -> list[Poi]:
    """Every POI of the city. A required tag the plan does not know is placed in proportion to population."""
    plan = tag_plan(values, layout, near_highway)
    pois: list[Poi] = []
    for tag in sorted(set(plan) | required_tags):
        if tag in ("railway=station", "public_transport=station"):
            continue
        weights, count = plan.get(tag, (values["resident_population"], max(5, len(layout.x) // 10)))
        for index, (x, y) in enumerate(sample_points(rng, layout, weights, count)):
            lat, lon = to_lat_lon(origin[0], origin[1], float(x), float(y))
            name = f"SYNTHETIC {tag.replace('=', ' ')} {index + 1}"
            pois.append(Poi(poi_id=f"syn/{tag}/{index}", lat=lat, lon=lon, tags={**tags_for(tag), "name": name}))
    for index, (x, y) in enumerate(station_points(rng, layout)):
        lat, lon = to_lat_lon(origin[0], origin[1], x, y)
        tags = {"railway": "station", "public_transport": "station", "name": f"SYNTHETIC station {index + 1}"}
        pois.append(Poi(poi_id=f"syn/station/{index}", lat=lat, lon=lon, tags=tags))
    return pois


def generate_listings(
    rng: np.random.Generator, layout: Layout, values: dict[str, np.ndarray], origin: tuple[float, float]
) -> list[StrListing]:
    """SYNTHETIC short-term rental listings: more near tourism and downtown, pricier in high-income tracts."""
    tourist = np.exp(-np.hypot(layout.x - layout.tourist_center[0], layout.y - layout.tourist_center[1]) / 2.0)
    core = np.exp(-values["d_core"] / (0.1 * layout.extent))
    weights = values["renter_households"] * (0.2 + tourist + core)
    count = 3 * len(layout.x)
    points = sample_points(rng, layout, weights, count, jitter_miles=0.4)
    income_rank = np.argsort(np.argsort(values["true_income"])) / len(layout.x)
    listings = []
    for index, (x, y) in enumerate(points):
        nearest = int(np.argmin(np.hypot(layout.x - x, layout.y - y)))
        price = float(np.round(90 * math.exp(0.6 * income_rank[nearest]) * rng.lognormal(0, 0.3)))
        lat, lon = to_lat_lon(origin[0], origin[1], float(x), float(y))
        listings.append(
            StrListing(
                listing_id=f"syn-str-{index}",
                lat=lat,
                lon=lon,
                room_type=str(rng.choice(["Entire home/apt", "Private room"], p=[0.7, 0.3])),
                nightly_price=price,
                availability_365=int(rng.integers(30, 365)),
            )
        )
    return listings


def gravity(values: np.ndarray, distances: np.ndarray, decay_miles: float) -> np.ndarray:
    """For each site (row): sum over tracts (columns) of value x exp(-distance / decay)."""
    return (np.exp(-distances / decay_miles) * values[None, :]).sum(axis=1)


def poi_pull(site_xy: np.ndarray, poi_xy: np.ndarray, decay_miles: float) -> np.ndarray:
    """For each site: sum over POIs of exp(-distance / decay) (0 when there are no POIs)."""
    if len(poi_xy) == 0:
        return np.zeros(len(site_xy))
    distances = pairwise_miles(site_xy[:, 0], site_xy[:, 1], poi_xy[:, 0], poi_xy[:, 1])
    return np.exp(-distances / decay_miles).sum(axis=1)


def logistic(value: np.ndarray, center: float, scale: float) -> np.ndarray:
    """1 / (1 + exp(-(value - center) / scale))."""
    return 1.0 / (1.0 + np.exp(-(value - center) / scale))


def frontage_aadt(site_xy: np.ndarray, roads: list[tuple[list[tuple[float, float]], int, int, str]]) -> np.ndarray:
    """For each site: the highest AADT among road pieces with a vertex within FRONTAGE_MILES (0 if none)."""
    best = np.zeros(len(site_xy))
    for points, aadt, _, _ in roads:
        vertices = np.array(points)
        distances = pairwise_miles(site_xy[:, 0], site_xy[:, 1], vertices[:, 0], vertices[:, 1]).min(axis=1)
        best = np.where(distances <= FRONTAGE_MILES, np.maximum(best, aadt), best)
    return best


def compute_true_demand(
    rng: np.random.Generator,
    layout: Layout,
    values: dict[str, np.ndarray],
    poi_xy: dict[str, np.ndarray],
    listing_xy: np.ndarray,
    roads: list[tuple[list[tuple[float, float]], int, int, str]],
) -> dict[str, np.ndarray]:
    """Hidden true demand per tract centroid for the six v1 business types (see TRUE_DEMAND_NOTES)."""
    sites = np.column_stack([layout.x, layout.y])
    distances = pairwise_miles(layout.x, layout.y, layout.x, layout.y)
    appeal = latent_field(rng, layout.x, layout.y, layout.extent)
    income = values["true_income"]
    aadt = frontage_aadt(sites, roads)

    def pull(tags: list[str], decay: float) -> np.ndarray:
        stacked = [poi_xy[tag] for tag in tags if tag in poi_xy and len(poi_xy[tag])]
        return poi_pull(sites, np.vstack(stacked) if stacked else np.zeros((0, 2)), decay)

    def noise(sd: float) -> np.ndarray:
        return rng.lognormal(0, sd, size=len(layout.x))

    def huff(tags: list[str], decay: float, strength: float) -> np.ndarray:
        return 1.0 / (1.0 + strength * pull(tags, decay))

    truth: dict[str, np.ndarray] = {}
    office_boost = 1 + 0.6 * (1 - np.exp(-pull(["office"], 0.4) / 3))
    market = 0.0035 * gravity(values["resident_population"], distances, 0.8)
    market += 0.006 * gravity(values["daytime_workers"], distances, 0.5) * office_boost
    competitors = ["amenity=restaurant", "amenity=cafe", "amenity=fast_food", "amenity=ice_cream", "shop=bakery"]
    truth["restaurant_cafe"] = (
        market
        * (0.6 + 0.8 * logistic(income, 60000, 15000))
        * huff(competitors, 0.4, 0.15)
        * np.exp(0.25 * appeal)
        * noise(0.2)
    )
    local = gravity(values["households"], distances, 2.0)
    truth["car_wash"] = (
        0.05
        * aadt**0.85
        * (1 + 0.4 * np.tanh(local / 20000))
        * (0.5 + 0.7 * logistic(income, 45000, 12000))
        * huff(["amenity=car_wash"], 1.2, 0.9)
        * np.exp(0.2 * appeal)
        * noise(0.2)
    )
    lodging_pull = 0.5 * pull(list(TOURISM_TAGS), 2.0) + 0.3 * pull(["highway=motorway_junction"], 0.8) * (aadt / 50000)
    lodging_pull += 0.2 * pull(["amenity=hospital", "aeroway=aerodrome"], 2.0)
    occupancy = (0.25 + 0.6 * (1 - np.exp(-lodging_pull / 1.5))) / (
        1 + 0.08 * pull(["tourism=motel", "tourism=hotel"], 1.5)
    )
    truth["motel"] = 40 * np.clip(occupancy * np.exp(0.15 * appeal) * noise(0.15), 0.05, 0.95)
    str_pull = pull(list(TOURISM_TAGS), 1.0) + 0.3 * pull(["leisure=park"], 0.5) + 0.5 * pull(["railway=station"], 0.8)
    density = poi_pull(sites, listing_xy, 0.5)
    str_occupancy = (0.2 + 0.65 * (1 - np.exp(-str_pull / 2))) * (0.8 + 0.4 * logistic(income, 70000, 20000))
    truth["short_term_rental"] = 28 * np.clip(str_occupancy / (1 + 0.01 * density) * noise(0.15), 0.03, 0.95)
    renters = gravity(values["renter_households"], distances, 0.6)
    truth["laundromat"] = (
        0.08
        * renters**0.9
        * (1.4 - 0.9 * logistic(income, 50000, 12000))
        * huff(["shop=laundry", "shop=dry_cleaning"], 0.5, 0.5)
        * np.exp(0.25 * appeal)
        * noise(0.2)
    )
    drive = 0.002 * aadt
    walk = 0.012 * gravity(values["resident_population"], distances, 0.5)
    walk += 0.008 * gravity(values["daytime_workers"], distances, 0.4)
    income_fit = np.clip(1 - 0.3 * ((income - 60000) / 40000) ** 2, 0.5, 1.1)
    truth["retail_storefront"] = (
        (drive + walk + 0.3 * np.sqrt(drive * walk))
        * income_fit
        * huff(["shop=convenience", "shop=variety_store", "shop=kiosk", "shop=supermarket"], 0.4, 0.3)
        * np.exp(0.2 * appeal)
        * noise(0.2)
    )
    return truth


def generate_city(
    metro_id: str,
    seed: int,
    n_tracts: int,
    origin_lat: float,
    origin_lon: float,
    extent_miles: float,
    profiles: dict[str, Profile] | None = None,
) -> SyntheticCity:
    """Generate the whole SYNTHETIC city. Same arguments -> the same city, value for value."""
    rng = np.random.default_rng(seed)
    layout = place_tracts(rng, n_tracts, extent_miles)
    half = extent_miles / 2
    values = generate_tract_values(rng, layout)
    road_pieces = generate_roads(rng, layout)
    highway_vertices = np.array([point for points, _, f_class, _ in road_pieces if f_class == 1 for point in points])
    d_highway = pairwise_miles(layout.x, layout.y, highway_vertices[:, 0], highway_vertices[:, 1]).min(axis=1)
    near_highway = np.exp(-d_highway / 0.75)
    required = set(TOURISM_TAGS)
    for profile in (profiles or {}).values():
        required |= set(profile.competitor_tags) | set(profile.anchor_tags)
    origin = (origin_lat, origin_lon)
    pois = generate_pois(rng, layout, values, near_highway, required, origin)
    listings = generate_listings(rng, layout, values, origin)
    poi_xy = poi_positions_miles(pois, origin)
    listing_xy = positions_miles([(item.lat, item.lon) for item in listings], origin)
    truth_arrays = compute_true_demand(rng, layout, values, poi_xy, listing_xy, road_pieces)

    suppressed = rng.random(n_tracts) < INCOME_SUPPRESSED_SHARE
    tracts = []
    for index in range(n_tracts):
        lat, lon = to_lat_lon(origin_lat, origin_lon, float(layout.x[index]), float(layout.y[index]))
        tracts.append(
            SyntheticTract(
                geoid=f"{TRACT_PREFIX}{index:06d}",
                lat=lat,
                lon=lon,
                land_area_sq_miles=0.0,
                resident_population=float(values["resident_population"][index]),
                households=float(values["households"][index]),
                renter_households=float(values["renter_households"][index]),
                median_household_income=None if suppressed[index] else float(values["true_income"][index]),
                daytime_workers=float(values["daytime_workers"][index]),
                district=district_of(float(values["d_core"][index]), half),
            )
        )
    tracts = with_land_areas(tracts, layout)
    roads = [
        RoadSegment(
            route_id=f"{route}-{index}",
            route_name=route,
            aadt=aadt,
            functional_class=f_class,
            data_year=None,
            line=[to_lat_lon(origin_lat, origin_lon, x, y) for x, y in points],
        )
        for index, (points, aadt, f_class, route) in enumerate(road_pieces)
    ]
    ids = [tract.geoid for tract in tracts]
    true_demand = {
        profile_id: {geoid: float(value) for geoid, value in zip(ids, array, strict=True)}
        for profile_id, array in truth_arrays.items()
    }
    return SyntheticCity(
        metro_id=metro_id,
        seed=seed,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        tracts=tracts,
        roads=roads,
        pois=pois,
        str_listings=listings,
        wage_index=float(np.round(rng.uniform(0.9, 1.2), 3)),
        rent_index=float(np.round(rng.uniform(0.85, 1.3), 3)),
        prime_rate_pct=float(np.round(rng.uniform(5.0, 9.0) * 4) / 4),
        true_demand=true_demand,
    )


def with_land_areas(tracts: list[SyntheticTract], layout: Layout) -> list[SyntheticTract]:
    """Approximate each tract's land area from the spacing to its four nearest neighbors (a disc of that size)."""
    distances = pairwise_miles(layout.x, layout.y, layout.x, layout.y)
    np.fill_diagonal(distances, np.inf)
    spacing = np.sort(distances, axis=1)[:, :4].mean(axis=1)
    areas = np.round(math.pi * (spacing / 2) ** 2, 3)
    return [
        replace(tract, land_area_sq_miles=float(max(area, 0.05))) for tract, area in zip(tracts, areas, strict=True)
    ]


def positions_miles(points: list[tuple[float, float]], origin: tuple[float, float]) -> np.ndarray:
    """(x east, y north) in miles of latitude/longitude points, relative to the origin."""
    if not points:
        return np.zeros((0, 2))
    lat0, lon0 = origin
    miles_per_degree_lat = 1 / miles_to_degrees(lat0, 1.0, 0.0)[0]
    miles_per_degree_lon = 1 / miles_to_degrees(lat0, 0.0, 1.0)[1]
    array = np.array(points)
    return np.column_stack([(array[:, 1] - lon0) * miles_per_degree_lon, (array[:, 0] - lat0) * miles_per_degree_lat])


def poi_positions_miles(pois: list[Poi], origin: tuple[float, float]) -> dict[str, np.ndarray]:
    """{tag filter 'key=value': positions in miles} for every tag present on the POIs."""
    grouped: dict[str, list[tuple[float, float]]] = {}
    for poi in pois:
        for key, value in poi.tags.items():
            if key == "name":
                continue
            grouped.setdefault(f"{key}={value}" if value != "yes" else key, []).append((poi.lat, poi.lon))
    return {tag: positions_miles(points, origin) for tag, points in grouped.items()}


def matching_pois(pois: list[Poi], tag_filters: list[str]) -> list[Poi]:
    """The POIs that match any of the tag filters."""
    return [poi for poi in pois if any(tag_matches(poi.tags, tag) for tag in tag_filters)]


def distances_from(lat: float, lon: float, points: list[tuple[float, float]]) -> np.ndarray:
    """Distances in miles from a site to (lat, lon) points."""
    if not points:
        return np.zeros(0)
    array = np.array(points)
    return haversine_miles_array(lat, lon, array[:, 0], array[:, 1])

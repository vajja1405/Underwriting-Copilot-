# Founder Studio: data sources (Phase S0)

Written on 2026-10-12.

> **Status update, 2026-10-13.** The first version of this page (2026-10-12) was written while every Studio data host
> was blocked. The network was opened later on 2026-10-12, and the hosts below were measured from the build
> environment:
>
> | Host | Answer | Checked (UTC) |
> |---|---|---|
> | overturemaps-us-west-2.s3.us-west-2.amazonaws.com (Overture release files) | HTTP 200 | 2026-10-13 14:35 |
> | docs.overturemaps.org | HTTP 200 | 2026-10-13 14:35 |
> | lehd.ces.census.gov (LODES 8 files) | HTTP 200 | 2026-10-13 14:35 |
> | geo.dot.gov (HPMS FeatureServer) | HTTP 200 | 2026-10-13 14:35 |
> | data.bls.gov (QCEW files, industry titles) | HTTP 200 | 2026-10-13 14:31 |
> | api.census.gov (ACS, CBP) with CENSUS_API_KEY | HTTP 200 (the key was refused as invalid_key earlier the same day; what changed in between is not recorded here) | 2026-10-13 |
> | www2.census.gov, geocoding.geo.census.gov, fred.stlouisfed.org | HTTP 200 (lead engineer, 2026-10-12 19:05-19:10) | 2026-10-12 |
> | overpass-api.de | connection reset | 2026-10-13 14:35 |
> | download.geofabrik.de | no HTTP answer | 2026-10-13 14:35 |
> | www.census.gov/naics | HTTP 403 | 2026-10-13 14:35 |
>
> Rows in the tables below still marked **"to verify"** have NOT been opened; only the sources in section 2c
> (Overture Maps places, LEHD LODES, the BLS industry titles list) have their terms recorded here, with the page and
> the date they were read.

## 1. How every source enters Studio (addendum rule F and the S1 design)

- One connector module per source in `src/uwcopilot/studio/data/`. Typed outputs.
- Every response is cached on disk with its SHA-256 and the request date. Downloads follow the repo's existing rule:
  write the file, then log `{dataset, file, source, sha256, hashed_at_utc}` with `common.files.append_checksum_log`.
- Polite rate limiting and retries, set per source once its limits are verified.
- **CI never calls the network.** Tests use fixtures. Real responses cannot be recorded in this environment, so until
  they can, fixtures are small hand-written files labeled **"SYNTHETIC fixture, not a recorded response"**. They are
  replaced by real recordings only if the source's terms allow committing an excerpt (otherwise they stay synthetic;
  CLAUDE.md rule 2 forbids committing raw datasets).
- Every connector returns values **plus a provenance record** that the evidence ledger stores. Proposed fields:
  `source_id`, `publisher`, `dataset_or_table`, `url`, `query`, `request_date_utc`, `snapshot_or_vintage`,
  `response_sha256`, `terms_note` (as recorded in S1), `metro_id`, `geography_id` (for example a tract GEOID).
- A value with no data is **UNKNOWN**, never filled silently (addendum rule G). Each site result shows a data-coverage
  score. A metro below the coverage threshold runs in "limited mode".
- When S1 adds a source, it is added to the README "Datasets and licenses" table with its verified terms.

## 2. National layer (works in every metro)

### 2a. What each source is for

| Source | Studio use | Connector (S1) | Used by | Fallback if unavailable |
|---|---|---|---|---|
| US Census Geocoder | Address to latitude/longitude and census tract GEOID | `geocode.py` | S2, S7 | The founder places the pin on the map; the tract is then looked up from tract geometry. If neither works, the site is excluded, not guessed. |
| Census ACS 5-year API | Tract and block-group population, households, renters, median income, age bands, commuting | `census_acs.py` | S2 demand, S2 suggest areas | None for demographics. Demand factors become UNKNOWN; the site or metro runs in limited mode. |
| Census County / ZIP Business Patterns | Establishment counts by NAICS (competition context, saturation vs metro median) | `business_patterns.py` | S2 competition | OpenStreetMap competitor counts only, labeled as the only source. |
| BLS QCEW | Local wages and employment by county and industry | `wages.py` | S3 labor cost | Profile ASSUMPTION wage range, user-editable. |
| BLS OEWS | Wages by occupation and metro | `wages.py` | S3 labor cost | Same as QCEW. |
| FHWA HPMS | AADT (traffic counts) on road segments: every county's segments are stored in the snapshot (roads.npz) and each tract centroid's nearest segment within 0.5 miles is computed once per snapshot | `traffic_hpms.py`, `road_layer.py`, `snapshot_layers.py` | S2 drive-by demand (car wash, retail, motel) | A state DOT count where available; else AADT is UNKNOWN and drive-by demand is not estimated from traffic. |
| Overture Maps places (default place source since 2026-10-13) | Competitors, anchors and tourism places by profile tags: one extract per metro, stored in the snapshot, counted around every tract centroid | `overture_places.py`, `places_layer.py`, `snapshot_layers.py` | S2 competition, anchors, tourism (and the metro-wide reference) | OpenStreetMap via Overpass per site (below), else UNKNOWN. |
| OpenStreetMap via Overpass (optional) | Same tags, one site at a time | `osm.py` | Only for a snapshot without a places layer, with a live client | Business Patterns counts (no locations, so distance factors are UNKNOWN). |
| Census LEHD LODES 8 (WAC) | Jobs located in each tract (daytime-worker proxy) | `lodes.py`, `snapshot_layers.py` | S2 restaurant and retail demand and scores | UNKNOWN (never zero). |
| FRED | Interest rates (for the S3 default rate: a published rate plus a stated, editable spread) | `rates.py` | S3 | The founder or lender types the rate; it becomes USER_STATED. |
| HUD Fair Market Rents | Residential rent reference only (short-term rental context) | `hud_fmr.py` | S2, S3 (short-term rental only) | Omitted. Never used as commercial rent. |
| OpenRouteService (optional) | Drive-time areas (isochrones) instead of distance rings, only if a key is set | in `siteiq` (S2) | S2 | Distance rings; the result records which method was used. |

### 2b. Terms to verify (none verified here)

| Source | Where to verify (not opened) | Free key needed? | License / terms | Rate limits | Update frequency |
|---|---|---|---|---|---|
| US Census Geocoder | https://geocoding.geo.census.gov/geocoder/ | to verify at this URL | to verify at this URL | to verify at this URL | to verify at this URL |
| Census ACS 5-year API | https://www.census.gov/data/developers.html and https://api.census.gov/data.html | to verify at these URLs | to verify at these URLs | to verify at these URLs | to verify at these URLs |
| Census County / ZIP Business Patterns | https://www.census.gov/programs-surveys/cbp.html | to verify at this URL | to verify at this URL | to verify at this URL | to verify at this URL |
| BLS QCEW | https://www.bls.gov/cew/ and https://www.bls.gov/developers/ | to verify at these URLs | to verify at these URLs | to verify at these URLs | to verify at these URLs |
| BLS OEWS | https://www.bls.gov/oes/ and https://www.bls.gov/developers/ | to verify at these URLs | to verify at these URLs | to verify at these URLs | to verify at these URLs |
| FHWA HPMS | https://www.fhwa.dot.gov/policyinformation/hpms.cfm | to verify at this URL | to verify at this URL | to verify at this URL | to verify at this URL |
| OpenStreetMap via Overpass (optional) | https://www.openstreetmap.org/copyright and https://wiki.openstreetmap.org/wiki/Overpass_API | to verify at these URLs | to verify at these URLs (attribution wording too) | to verify at these URLs | to verify at these URLs |
| FRED | https://fred.stlouisfed.org/docs/api/fred/ | to verify at this URL | to verify at this URL (terms for each series as well) | to verify at this URL | to verify at this URL (per series) |
| HUD Fair Market Rents | https://www.huduser.gov/portal/datasets/fmr.html | to verify at this URL | to verify at this URL | to verify at this URL | to verify at this URL |
| OpenRouteService | https://openrouteservice.org/ | to verify at this URL | to verify at this URL | to verify at this URL | not applicable (a routing service) |

### 2c. Sources added on 2026-10-13, with what was read

**Overture Maps places** (`config/studio/overture_places.yaml`, `config/studio/overture_category_map.yaml`).
- Release pinned: `2026-09-23.1` (schema v2.0.0; dated 25 September 2026 in the release calendar's table, the patch
  of `2026-09-23.0` of 23 September 2026), read from
  `https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/release/2026-09-23.1/theme=places/type=place/` with
  DuckDB over HTTPS (column projection plus the `bbox` struct for predicate pushdown; only category, position,
  confidence, the first source's dataset and the licenses of all the place's sources are read; no names, ids,
  addresses, phones or websites).
- Retention: the release calendar page (re-read 2026-10-13 15:30 UTC) says "Overture maintains publicly available
  data releases for a maximum of 60 days (two monthly releases) to comply with data protection regulations, including
  GDPR 'right to be forgotten' requirements." Studio follows the same intent: 60 days after the release date
  (`release.public_days_max`, so after 2026-11-24 for this pin) `extract_metro_places` refuses to build or reuse an
  extract of the pinned release, and the pin must move; cached extracts and snapshot `places.json` files of a retired
  release are re-made or deleted, not reused (snapshot folders are deleted by hand; code never edits a written
  snapshot). The extracts hold no names, but a position can still point at a home-based business.
- Licenses, as read on 2026-10-13 from https://docs.overturemaps.org/attribution/ and
  https://docs.overturemaps.org/guides/places/ (SHA-256 of each page in `overture_places.yaml`): **the places theme
  mixes licenses by source**: Meta, Microsoft, PinMeTo, Krick, RenderSEO, DAC and BrightQuery data under **CDLA
  Permissive 2.0**; Foursquare data under **Apache 2.0** ("Copyright 2024 Foursquare Labs, Inc. All rights reserved.",
  with a NOTICE.txt at https://opensource.foursquare.com/places-notice-txt/); AllThePlaces data under **CC0 1.0**. The
  places guide says the theme contains no OpenStreetMap data. A place's `sources` list names its provider first and
  then, for almost every place, Overture's own contributions (datasets `Overture` and `Overture-signals`, CDLA
  Permissive 2.0; seen in live queries of central Houston and Manhattan on 2026-10-13). So one place usually mixes
  licenses: each stored place keeps the licenses of ALL its sources, sorted and joined with "+" (for example
  `Apache-2.0+CDLA-Permissive-2.0` for a Foursquare place), plus its first source's dataset name, and every extract
  records the count of stored places per license set (`kept_by_license` in the snapshot manifest). Attribution:
  "Overture Maps Foundation, overturemaps.org" with the access date.
- CDLA Permissive 2.0 (read on 2026-10-13 at https://cdla.dev/permissive-2-0/): section 2.1 allows sharing the data
  if the text of the agreement is made available with it; section 3.1 puts no obligation on Results (outcomes of
  computational analysis, such as per-tract counts). The committed test sample (60 rows whose first source is
  CC0-1.0) ships with the CDLA text because its rows also list Overture's own CDLA-licensed contributions. The Apache
  2.0 terms were not read in this build: read them, and ship the Foursquare NOTICE, before a snapshot's
  `places.json` is shared outside this machine (for example in a deployed image).
- Mapping: one reviewed row per Overture category with a note (`overture_category_map.yaml`); places whose category
  path has no row are ignored and counted (`unmapped` in each extract's accounting). A second review on 2026-10-13
  added rows for categories that live queries of the five metros' boxes showed with many places and no row (generic
  `lodging`, `resort`, `lodge` and other lodging subtypes; dessert, beverage, food-truck and bagel places; warehouse
  clubs; kindergartens), each either counted or deliberately not counted, with a note. Places with **no taxonomy**
  are ignored (`without_taxonomy` in the accounting) even when their `basic_category` names a mapped kind of place
  (for example `fueling_station`), so `amenity=fuel` undercounts; a `basic_category` fallback is not built.
- Facility rows: Overture files many places of one airport, hospital or campus under the same category, while the
  profiles' tags count one feature per facility. Rows with `merge_within_miles` (airport 1 mile; hospital and
  college_university 0.25 miles; DESIGN_CHOICE values) merge them: from the highest confidence down, a place within
  that distance of an already kept place of the same row is merged into it, never chained
  (`overture_places.merge_facilities`). Each extract counts what was merged (`merged_into_facility`, `merged_by_row`). Three profile tags have no
  Overture category (highway=motorway_junction, tourism=attraction, tourism=viewpoint); the coverage report lowers
  the factor's coverage by that share (`definition_share`) instead of counting them as "none found".
- Why Overture is the default and OpenStreetMap is optional: overpass-api.de reset every connection from this
  environment (2026-10-12 and 2026-10-13); a metro-wide extract gives the per-tract counts the metro-wide ranking
  needs, which per-site Overpass queries cannot; one pinned release is reproducible; and the theme carries no ODbL
  share-alike obligations (places guide). `osm.py` stays as a documented optional source (`--with-overpass`).

**Census LEHD LODES 8, Workplace Area Characteristics** (`lodes.py`).
- Files: `https://lehd.ces.census.gov/data/lodes/LODES8/<st>/wac/<st>_wac_S000_JT00_2023.csv.gz` (S000 all workers,
  JT00 all jobs, 2023), column C000 (total jobs) by 2020 census block (w_geocode); a tract's value is the sum over its
  blocks (first 11 digits). It counts jobs, not distinct people. Structure from the LODES Technical Document 8.4
  (Rev. 20251203, https://lehd.ces.census.gov/data/lodes/LODES8/LODESTechDoc8.4.pdf, read 2026-10-13, SHA-256
  1189b8aa4cefd6cdf53c3b85e27366054af0cd5418056b04ff70d210b87eddee).
- Integrity: each decompressed file must match the SHA-256 in the state's published `lodes_<st>.sha256sum`; the data
  vintage comes from the state's `version.txt`. Both are recorded per state in the snapshot manifest.
- Terms: the LEHD data page (https://lehd.ces.census.gov/data/, read 2026-10-13, SHA-256
  f93878ac8bb67bb9eb0fbd13e544183af5940da87e7ae7285f2710050dd73eef) has **no license or terms-of-use text**; terms are
  therefore not recorded. It gives a citation format, which every snapshot records per file: U.S. Census Bureau,
  "<file>," LEHD Origin-Destination Employment Statistics, <vintage>, https://lehd.ces.census.gov/data/lodes, accessed
  on <date>.
- A tract with no block in the file stays UNKNOWN: the technical document does not say a missing block means zero.
  One exception, recorded per tract (`daytime_workers_basis: water_only_no_block`) and counted in the manifest
  (`water_only_tracts_set_to_zero`): a tract whose land area is 0 in the Gazetteer tract layer (water only) gets 0
  jobs, because it has no land on which a workplace could be located.
- Coverage of daytime workers (and of the other tract values SiteIQ sums: residents, households, renter households)
  is the share of tracts whose market-ring SUM is known, because one tract without a value makes every ring sum that
  includes it UNKNOWN; the per-tract share is given in the coverage reason.

**BLS QCEW industry titles** (`scripts/studio_verify_naics.py`, `results/studio_naics_check.json`).
- https://data.bls.gov/cew/doc/titles/industry/industry_titles.csv, retrieved 2026-10-13 14:31 UTC, SHA-256
  facc122ef582f3efdbb5294f103331894d8fdab694de38e4ee7bff1b941ab8e1. Used to check the profiles' NAICS codes because
  www.census.gov/naics answered 403. 9 of 10 profile codes are confirmed; "44" is not in the list (the list has
  "44-45 Retail trade").

## 3. Metro enhancements (one row per metro)

Each enhancement improves on the national layer for one metro. The Studio plan says: add a city or county dataset
**only where a real, documented dataset exists**. Every cell here is UNVERIFIED.

| Metro (UNVERIFIED, see [metros.md](metros.md)) | State DOT traffic counts | City / county open data | Inside Airbnb coverage |
|---|---|---|---|
| New York-Newark-Jersey City | Needs NY, NJ and PA (per the plan). Dataset per state: to find in S1 on each state DOT site; license, format and update frequency to verify there. | NYC Open Data (https://data.cityofnewyork.us/, blocked here). The plan names it; which dataset (for example business licenses or permits) is chosen in S1, only if documented. | To check at https://insideairbnb.com/get-the-data/ (blocked here). If not covered, short-term rental comparables are UNKNOWN. |
| Los Angeles-Long Beach-Anaheim | Needs CA. To find in S1. | "LA open data" is a plan example; the portal and dataset are to find in S1, only if documented. | To check (same URL). |
| Chicago-Naperville-Elgin | Needs IL and IN. To find in S1. | City of Chicago business licenses on https://data.cityofchicago.org/ (plan example, blocked here). Dataset id, terms and update frequency to verify. | To check (same URL). |
| Dallas-Fort Worth-Arlington | Needs TX. To find in S1 (one adapter can serve Dallas and Houston if the dataset covers the state). | "Dallas open data" is a plan example; to find in S1, only if documented. | To check (same URL). |
| Houston-Pasadena-The Woodlands | Needs TX (same adapter as Dallas, if confirmed). | "Houston open data" is a plan example; to find in S1, only if documented. | To check (same URL). |

Fallback for every enhancement: the national layer. A missing enhancement lowers the coverage score; it never
blocks the metro.

## 4. Commercial rent

There is no free, reliable public source for commercial rent (the Studio plan says so, and nothing here changes it).
The plan, restated:

1. **The founder's quotes come first.** A typed rent is a USER_STATED claim. An uploaded quote or lease letter with a
   page locator is VERIFIED (document provenance).
2. **Otherwise a labeled metro ASSUMPTION range.** It is wide, user-editable, shown as ASSUMPTION everywhere, and
   carries its source or the word "unsourced". No range is written here; S1 sets it with its provenance.
3. The simulator (S3) samples rent from that range when no quote exists, and the tornado chart shows how much the
   result depends on it.
4. HUD Fair Market Rents are residential. They are never used as commercial rent.

## 5. Not in the plan, worth deciding

- **Weather.** Car-wash and dessert-shop seasonality likely depends on weather, but the plan lists no weather source.
  Until one is added and verified, seasonality curves stay ASSUMPTION. (Listed in [open_questions.md](open_questions.md).)
- **Map tiles for the website.** S7 needs a free vector-tile provider whose terms allow this use. To choose and
  verify in S7.

## 6. What has not been measured

Coverage per metro, business type and factor is measured by `scripts/studio_metro_coverage.py`
(`results/studio_metro_coverage.json`). Each snapshot manifest records what each layer holds (places kept per
license, HPMS segments and pages, LODES rows per state) and, for the places layer, the wall-clock time of that
run's extract step (`layers.places.extract_seconds`, cache or live; one run on a shared machine, not a benchmark).
Other download times and the cache size are **not measured** by a script.

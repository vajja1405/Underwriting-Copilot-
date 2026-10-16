# Walkthrough: Studio S1, profiles, metros and the data fabric

Written on 2026-10-12 and updated on 2026-10-13 (verify-and-fix pass: rejected answers are no longer cached, the
Census key problem is named, coverage no longer counts live-only layers, derived wages are labeled; later the same
day: Overture places, the HPMS road layer and LODES daytime workers, see section 7) so that both Rahul Vajja and
Bhavani Adula can explain this phase in their own words.

**What S1 is.** S1 builds the data foundation of Founder Studio: one config file per business type (what drives its
demand, its costs, its risks), one config file per metro (which counties it covers), the connectors that fetch public
data, a SYNTHETIC city for demos and validation, and one interface (the "data fabric") that SiteIQ (S2) and the
feasibility simulator (S3) read from. It also writes a coverage report that says, per metro and business type, which
factors are backed by data and which are not.

**What was done and what was not.**
- Done: six business-type profiles and their validation; five real metro configs whose CBSA codes, titles and county
  FIPS codes were read from the Census delineation file by a script; a check of the "five largest metros" rule against
  the Census population estimates file; 12 connectors (9 national, 3 metro enhancements) with a shared polite client, cache and provenance record; the
  SYNTHETIC city with hidden true demand; the data fabric; `make studio-data` snapshots for the five real metros using
  the keyless sources; the coverage report; tests with recorded and labeled hand-written fixtures.
- Update 2026-10-13 (section 7): api.census.gov refused the Census key as invalid_key earlier on 2026-10-13 and
  accepted it (HTTP 200) later the same day, so ACS tract data and County Business Patterns now load (CBP answered "no rows" for "44" and "445131" in every metro, and for "721199" in at least one state of
  the Chicago and New York metros; those counts are UNKNOWN). Places now come from an Overture Maps extract per metro (OpenStreetMap is
  optional: overpass-api.de reset every connection from this environment), traffic from an HPMS road layer stored in
  the snapshot, daytime workers from Census LEHD LODES. Every step of all five 2026-10-13 snapshots succeeded.
- Still not done: state DOT traffic adapters (no documented dataset recorded yet) and Inside Airbnb coverage (the site
  answered 403), so short-term rental comparables are UNKNOWN. All five real metros still run in **limited mode**:
  the metro's mode uses its lowest business type (short-term rental, 0.6779 to 0.6826; motel, 0.745 to 0.7722),
  see section 4.
- No profile number is SOURCED. Every rate, price, cost ratio and curve is the project author's labeled ASSUMPTION with
  a wide range and a note naming the source that should replace it.

## 1. Files

### Config

| File | What it does | Why it is built this way |
|---|---|---|
| `config/studio/business_types/*.yaml` (6 files) | One profile per v1 business type: NAICS codes, demand unit and formula, price, capacity, hours, cost ratios, labor, rent, capex lines, seasonality (12 values), ramp-up, location-factor weights, trade-area rings, competitor and anchor OpenStreetMap tags, key risks, metro overrides. | Adding a business type is a config file, not code. Every number is `{value or low/mode/high, unit, status, source, note}` so its provenance travels with it (addendum rule D). |
| `config/studio/metros/<5 real metros>.yaml` | Official CBSA title, CBSA code, states, every county (state FIPS, county FIPS, name, central/outlying), the delineation record (URL, date, file SHA-256, retrieval time), trade-area ring multiplier, enabled enhancements, coverage threshold. | The name, code, states and counties are written by `scripts/studio_fetch_delineation.py`, never by hand. Ring multipliers and enhancements are labeled design choices. |
| `config/studio/metros/synthetic_metro.yaml` | Generator settings of the SYNTHETIC city: seed, 300 tracts, origin in open ocean, 25-mile extent. | A place that cannot be mistaken for a real neighborhood; `data_label: SYNTHETIC`. |

### Profiles and metros (`src/uwcopilot/studio/profiles`, `src/uwcopilot/studio/metros`)

| File | What it does |
|---|---|
| `profiles/models.py` | Pydantic models. `Quantity` enforces the provenance rule: SOURCED needs a source; ASSUMPTION must be a range (low < high) with a note. Cross-field checks: weights sum to 1 and name known factors, seasonality has 12 values averaging 1.0, cost ratios are shares of revenue, demand inputs match the formula, OpenStreetMap tags match a safe pattern. |
| `profiles/factors.py` | The shared vocabulary: the 13 location factors (and whether each lives per tract, on a POI layer, on a road layer or per metro) and the six demand formulas. A typo in YAML fails a test instead of dropping a factor. |
| `profiles/loader.py` | `load_profile(id, metro_id=None)`, `list_profiles()`, `load_all_profiles()`. A metro override replaces one Quantity or Curve by dotted path, and the result is validated again. |
| `metros/models.py` | `MetroConfig`, `County`, `DelineationRecord`. Refuses counties without a delineation record, refuses `verified: true` without counties and a CBSA code, checks that counties span exactly the listed states and that no enabled state adapter names another state. `metro_mode()` returns full, limited or synthetic with a plain reason. |
| `metros/loader.py` | `load_metro(id)`, `list_metros()`. |
| `metros/delineation.py` | Reads the Census delineation workbook (.xlsx) with the standard library (zip + XML), refusing any DOCTYPE and oversized parts because a download is untrusted input. Finds a metro by CBSA code or title prefix (an ambiguous prefix fails). |
| `metros/ranking.py` | Ranks metropolitan statistical areas by the latest `POPESTIMATE<year>` column of the Census PEP CBSA totals file (CBSA total rows only). |

### Data (`src/uwcopilot/studio/data`)

| File | What it does |
|---|---|
| `fabric.py` | The entry point: `get_fabric(metro_id)` and the method list every S2/S3 caller uses. |
| `fabric_base.py` | The `DataFabric` interface and its value types: `FactorValue` (value or None, status DATA/SYNTHETIC/ASSUMPTION/DATA_X_ASSUMPTION/UNKNOWN, reason, assumptions, SourceRecord), `TractRecord`, `PoiSearch`, `RoadResult`, `ListingSearch`, `FactorCoverage` (with `live_per_site`), and `coverage_share()`. |
| `fabric_synthetic.py` | `SyntheticFabric`: the interface over the generated city. Every value is SYNTHETIC with a SourceRecord that fingerprints the generated tract table. The hidden truth is not reachable through it. |
| `fabric_snapshot.py` | `SnapshotFabric`: the interface over a real metro's newest snapshot. Missing layers are UNKNOWN with the reason. Road and POI lookups go live per site only when a client is passed in; in the coverage report those layers are UNKNOWN with `live_per_site: true` (no tract has a value, so tracts cannot be ranked on them). `Snapshot.fingerprint()` gives the SHA-256 of `manifest.json` and `tracts.json`, which results files record. |
| `synthetic_city.py` | The seeded SYNTHETIC city (section 3). |
| `snapshot.py` | `make studio-data METRO=<id>|all`: one step per source; a failed step never stops the others; the command exits 1 and names every unreachable host, missing key variable and refused key. `--refresh` (`make studio-data-refresh`) ignores cached answers. A same-day rerun replaces that day's folder. |
| `http.py` | `PoliteClient`: per-host minimum interval, retries with backoff on network errors and 429/5xx (Retry-After honored), explicit timeouts, `trust_env=True` for the proxy, an offline mode and a refresh mode. A connector passes an `accept` check (for example "JSON, not an Invalid Key page"; "BLS status REQUEST_SUCCEEDED"; "no ArcGIS error"): a rejected answer is never cached, and a rejected cached answer is deleted and fetched again. Secret parameters, JSON fields and headers are kept out of the cache key, the stored request, logs and error messages; a filter on the `httpx` logger replaces secret query values with `***`, because httpx logs every request URL at INFO level. |
| `cache.py` | On-disk cache under `data/studio/cache/` keyed by the SHA-256 of the public request; stores the body's SHA-256, the request date and the URL that answered after redirects, and refuses a body that no longer matches its checksum. |
| `provenance.py` | `SourceRecord` (publisher, dataset, URL, public params, retrieved_at, SHA-256, license note, data label, origin, vintage, geography) and `SourceInfo.record(...)`. Refuses secret parameter names. |
| `keys.py`, `errors.py` | The five key variables and what each is for; `MissingApiKey` names the variable, never a value; `InvalidApiKey` names the variable and the host's reason when a key is set but refused. `NotAvailable`, `HostUnreachable`, `UpstreamError`, `CacheCorrupted`. |
| `geo.py`, `paths.py` | Haversine distances in miles and point-to-polyline distance; the data folders. |
| `geocode.py` | Census Geocoder: address or pin to coordinates and tract GEOID (keyless). |
| `census_acs.py` | ACS 5-year tract tables (key). Variable labels are checked against the keyless metadata endpoint; negative "not available" codes become None. `check_census_answer` reads Census's redirect (`invalid_key.html` / `missing_key.html`) and raises `InvalidApiKey`. |
| `census_geography.py` | Census Gazetteer tract file: the tract list and internal points of a metro's counties, and each state's postal code (keyless). |
| `business_patterns.py` | County Business Patterns establishments by NAICS (key). A missing county is UNKNOWN, not zero. |
| `wages.py` | BLS QCEW open-data wages by metro and industry (keyless) and BLS API v2 series (key). |
| `traffic_hpms.py` | FHWA HPMS AADT from the geo.dot.gov FeatureServer layers (keyless): nearest segment to a site, segment counts per county. |
| `osm.py` | Overpass query builder and parser for competitor and anchor POIs (keyless; unreachable from here). |
| `rates.py` | FRED prime rate: the API when `FRED_API_KEY` is set, otherwise the keyless CSV. |
| `hud_fmr.py` | HUD Fair Market Rents (token; residential reference only, never commercial rent). |
| `traffic_state.py` | One adapter per state; all raise NotAvailable until a documented dataset is recorded. |
| `city_open_data.py` | City of Chicago Business Licenses and NYC DCWP Issued Licenses (Socrata). Only categories, coordinates and expiration dates are requested, never names. |
| `str_comps.py` | Inside Airbnb comparables: NotAvailable until coverage is verified; a parser for a listings file downloaded by hand. |

### Scripts, tests, Make targets

| File | What it does |
|---|---|
| `scripts/studio_fetch_delineation.py` (`make studio-metros`) | Downloads the delineation file and the PEP CBSA totals file, logs their SHA-256 in `data/studio/checksums.jsonl`, fills the five real metros from the file, disables state adapters for states the file does not list, and writes `results/studio_metro_ranking.json`. |
| `scripts/studio_metro_coverage.py` (`make studio-coverage`) | Writes `results/studio_metro_coverage.json` with a provenance block. Offline. |
| `tests/studio/fixtures/record_fixtures.py` (`make studio-fixtures`) | Re-records the fixtures from keyless live hosts. Never run in CI. |
| `tests/studio/*.py` (`make studio-test`) | Profiles, metros, the workbook reader and ranking, the HTTP client and cache, provenance and keys, every connector, fixture labels and checksums, the SYNTHETIC city, the fabric, the snapshot builder and the coverage script. No test touches the network. |

## 2. Design choices

1. **Every number says where it came from.** A profile number is SOURCED (with publisher, title, date, URL, page) or
   an ASSUMPTION range with a note. A fabric value is DATA, SYNTHETIC, ASSUMPTION or UNKNOWN with a reason. A test
   fails if any profile field is SOURCED today, so nobody flips a label without opening a source.
2. **FIPS codes come from the file, not memory.** The model refuses counties without a delineation record. The test
   `test_county_fips_match_the_recorded_delineation_rows` compares the committed configs with rows recorded from the
   Census file. YAML digit strings are written quoted, so a county code such as `039` stays a string.
3. **The ranking rule is checked, not assumed.** `results/studio_metro_ranking.json` ranks 387 metropolitan
   statistical areas by `POPESTIMATE2025`; the five configured metros are the top five
   (`configured_equals_top_five: true`). One finding: in the July 2023 delineation, CBSA 35620 is titled
   "New York-Newark-Jersey City, NY-NJ" and lists no Pennsylvania county, so the script set the metro's states to
   NY and NJ and disabled the PA traffic adapter. The config id `new_york_ny_nj_pa` was kept so nothing else breaks.
4. **A failed source never hides.** `make studio-data` writes what succeeded and exits 1 with one line per failure,
   naming the host or the key variable. The fabric turns every missing layer into UNKNOWN with that reason.
5. **No broad wage stand-ins.** QCEW does not disclose small industries in a metro. The connector tries the requested
   6-digit NAICS code and its 5-digit parent only; a 3-digit group (for example "repair and maintenance" for a car
   wash) would misstate wages, so the wage stays UNKNOWN and the profile's ASSUMPTION range is used. QCEW reports
   weekly wages without hours, so the hourly range divides by an hours-per-week ASSUMPTION that is listed next to the
   value. Because the hourly value and its whole range come from that assumption, its status is `DATA_X_ASSUMPTION`,
   never plain DATA (rule D). In the SYNTHETIC city, rent and wages are the profile's ASSUMPTION ranges scaled by a
   SYNTHETIC index, so they stay ASSUMPTION too.
6. **Secrets cannot leak by construction.** Keys are read only by name, kept out of cache keys, stored requests,
   SourceRecords, logs, error messages and fixtures; `SourceRecord` refuses secret parameter names.
7. **Coverage is a weighted share, and it says what it measured.** `coverage_share` weights each required factor by
   the profile's weight (demand-only inputs get the average weight) and counts ASSUMPTION and UNKNOWN as zero.
   `share_basis` says whether a share is over tracts or over the whole metro (a metro-level value exists or not). A
   layer that can only be looked up live for one site (a real metro's HPMS roads or OpenStreetMap places) has no
   per-tract values, so it is UNKNOWN with share 0 and the flag `live_per_site: true`; counting "HPMS has segments in
   every county" as tract coverage would overstate it (rule G). A real metro runs in full mode only when every
   business type meets its threshold (0.8, a design choice).
9. **A bad answer is never replayed.** The cache key leaves the API key out on purpose (the key must never reach the
   disk), so an answer must be checked before it is cached: otherwise an "Invalid Key" page saved under the keyless
   cache key would be served again after the key is fixed (this happened on 2026-10-13 and was fixed). Each
   connector's `accept` check runs before `cache.put`; old bad entries are deleted on the next read; `--refresh`
   ignores the cache altogether.
10. **Results pin the exact snapshot.** A same-day rerun of `make studio-data` replaces that day's folder, so the
   coverage and SiteIQ results record the SHA-256 of each snapshot's `manifest.json` and `tracts.json`.
8. **The fabric is split in small files.** One interface (`fabric_base.py`), two implementations, one entry point.
   S2 and S3 code against `DataFabric`, so a real snapshot can replace the SYNTHETIC city without changing them.

## 3. The SYNTHETIC city and why its truth is not SiteIQ's formula

`generate_city(...)` (seed 20261012 in the config) places 300 tract centroids around a downtown and three
sub-centers, with population, households, renters, median income (a few values suppressed, like real survey tables)
and daytime workers; radial highways, a ring highway, an arterial grid and a local street through every centroid,
cut into ~1-mile segments with AADT; every competitor and anchor tag the profiles use, plus tourism tags, with
competitors clustered where their own demand is; short-term rental listings; and a SYNTHETIC wage index, rent index
and prime rate. Every constant is a generator parameter, not data.

It also computes a hidden **true demand** per tract and business type, in the profile's demand unit. That formula
deliberately differs from anything SiteIQ will do: smooth gravity decay instead of distance rings, a Huff-style
saturating competition share instead of a competitor count, interactions (office workers x office anchors, drive-by x
walk-in traffic), saturation curves on income and market size, a spatially correlated latent "neighborhood appeal"
field that no feature exposes, multiplicative noise, and the true income where the published table is suppressed
(`TRUE_DEMAND_NOTES` lists each formula). A test checks that a linear fit of log truth on the published tract
features does not explain it fully (R-squared below 0.9 for the restaurant and laundromat types). Validation in S2 reads the truth from `synthetic_city` directly; the fabric never
exposes it.

## 4. Results (copied from files the scripts wrote)

From `results/studio_metro_ranking.json` (PEP file `cbsa-est2025-alldata.csv`, column `POPESTIMATE2025`):

| Rank | CBSA | Name in the file | Population estimate |
|---|---|---|---|
| 1 | 35620 | New York-Newark-Jersey City, NY-NJ | 20112448 |
| 2 | 31080 | Los Angeles-Long Beach-Anaheim, CA | 12844441 |
| 3 | 16980 | Chicago-Naperville-Elgin, IL-IN | 9434123 |
| 4 | 19100 | Dallas-Fort Worth-Arlington, TX | 8477157 |
| 5 | 26420 | Houston-Pasadena-The Woodlands, TX | 7904627 |

From `results/studio_metro_coverage.json`, rerun on 2026-10-13 after the section 7 layers were added and after the
verify-and-fix pass (every snapshot step succeeded; summed tract values now count the tracts whose market-ring sum
is known, see section 7). Coverage share per business type (weighted share of required factors backed by data):

| Metro | Tracts | Mode | Car wash | Laundromat | Motel | Restaurant/cafe | Retail | Short-term rental |
|---|---|---|---|---|---|---|---|---|
| new_york_ny_nj_pa | 4928 | limited | 0.9142 | 0.8935 | 0.7722 | 0.8675 | 0.9346 | 0.6779 |
| los_angeles_ca | 3112 | limited | 0.9165 | 0.8978 | 0.7714 | 0.8769 | 0.9415 | 0.6812 |
| chicago_il_in | 2292 | limited | 0.9159 | 0.8978 | 0.771 | 0.8729 | 0.9382 | 0.6812 |
| dallas_fort_worth_tx | 1704 | limited | 0.8965 | 0.8993 | 0.7564 | 0.8781 | 0.9306 | 0.6826 |
| houston_tx | 1615 | limited | 0.8797 | 0.898 | 0.745 | 0.876 | 0.9197 | 0.6813 |
| synthetic_metro | 300 | synthetic | 0.9473 | 0.896 | 0.9 | 0.8973 | 0.9473 | 0.996 |

Before the update (same file, earlier run on 2026-10-13) every real metro had 0.0 for every business type. What is
still missing: commercial rent is an ASSUMPTION everywhere (counts 0); short-term rental comparables are UNKNOWN
(Inside Airbnb not verified); three profile tags have no Overture category, so anchors count 0.8 (car wash, motel)
or 0.857 (restaurant) of their weight and tourism 0.667 (`definition_share`); traffic, income, residents and daytime
workers miss a few tracts each (for New York: traffic 0.9688 and income 0.9639 of tracts; residents 0.997 and daytime
workers 0.9667 of tracts have a known market-ring sum for restaurant/cafe). A real metro runs in full mode only when every business type reaches its threshold (0.80), so the
short-term rental and motel rows keep all five in limited mode. The earlier report (0.35 for car wash, 0.25 for
motel, 0.2 for retail) counted "some segment somewhere in each county" as covering every tract, which overstated
coverage. QCEW disclosed a wage for 1 (Chicago), 2 (Houston), 3 (Dallas-Fort Worth, New York) and 6
(Los Angeles) of the six business types, each labeled DATA_X_ASSUMPTION; the others stay UNKNOWN. The prime rate is
DATA (FRED DPRIME, observation of 2026-10-02, value 7.0) in every snapshot. The SYNTHETIC metro's lowest share is
0.896 (laundromat) because rent is an ASSUMPTION range and counts 0.

## 5. Limitations

- Real metros stay in limited mode while the metro's mode is the minimum over business types: short-term rental has
  no comparables source (Inside Airbnb not verified) and motel's tourism and anchor tags are partly unmapped. Limited
  mode is a note on results (scores and suggestions still run where the data allows); a per-business-type mode is an
  open design question.
- The Overture release is pinned to 2026-09-23.1; Overture keeps a release public for at most 60 days, so the pin
  must move (and the extracts be re-made) before `make studio-data-refresh` runs after the release is removed. The
  cached extracts keep working.
- License and terms are recorded for Overture places and the LEHD citation (data_sources.md 2c); every other source is
  still "to verify" in its SourceRecord's `license_note`.
- The HUD entity-id format and the Inside Airbnb column names were written from the author's knowledge, are marked so,
  and are tested only with hand-written fixtures.
- NAICS codes: 9 of 10 are `verified: true` against the BLS QCEW industry titles list (the Census NAICS site returned
  403 here); "44" is not in that list ("44-45" is).
- Commercial rent has no free source; it stays an ASSUMPTION range until the founder enters a quote.

## 6. Likely interview questions

1. **How do you add a new city?** Add `config/studio/metros/<id>.yaml` with `cbsa_title_prefix`, states, trade-area
   settings and enhancements; run `make studio-metros` (fills the CBSA code and counties from the Census file),
   `make studio-data METRO=<id>` and `make studio-coverage`. No code changes, unless the city needs a new state
   traffic adapter or city dataset.
2. **Why these five cities?** The rule is "the five largest US metropolitan statistical areas by population in the
   latest Census estimates". The script ranks the Census file and confirms the five configured metros are the top
   five; the sixth on the same list is next.
3. **How do you stop someone from typing a made-up benchmark?** Every number must be SOURCED with a full citation or an
   ASSUMPTION range with a note; the model rejects anything else, and a test fails if any profile field is SOURCED
   before a source has been opened and cited.
4. **What happens when a data source is down or a key is missing?** The step fails, the run continues, the snapshot
   keeps what succeeded, the command exits 1 naming the host or variable, and the fabric reports UNKNOWN with the
   reason. The metro then runs in limited mode and the app says so. A key that is set but refused is subtler: Census
   answers it with a redirect to an HTML page, not an error status. The connector reads where the redirect went
   (`invalid_key.html`: not activated or mistyped; `missing_key.html`: no key arrived), names the variable and the
   reason (never the value), and does not cache the page, so the next run after the fix just works.
5. **Why validate on a synthetic city?** Real outcome data (which sites did well) does not exist for this project. A
   synthetic world with a known true demand lets S2 measure whether its ranking recovers the truth. Because the same
   author wrote both, the truth formula is deliberately different from SiteIQ's (gravity, saturation, interactions,
   hidden appeal, noise), and the result is reported as validation on a synthetic world, not real-world accuracy.
6. **How do you keep API keys out of logs and fixtures?** Keys are read by name only when needed; the client removes
   them from the cache key, stored request, logs and errors; `SourceRecord` refuses secret parameter names; a test
   scans every fixture for key-like parameters.
7. **Why was a car wash in Houston at coverage 0.0 when HPMS has traffic counts in every county, and why is it
   0.8797 now?** Coverage measures the share of tracts with a value. The first snapshots stored no traffic value per
   tract (a site's nearest road was looked up live), Census refused the key and OpenStreetMap was unreachable, so
   every factor was UNKNOWN. Now the snapshot stores the HPMS road layer and each tract's nearest segment (86% of
   Houston's tract centroids have one within 0.5 miles), ACS loads, and Overture gives competitors and anchors. Rent is
   still an ASSUMPTION and one of five anchor tags has no Overture category, which is why it is not higher. An earlier
   version reported 0.35 by counting "some segment in each county" as full coverage; that overstated it and was fixed.
8. **Why does New York have no Pennsylvania county?** The July 2023 Census delineation lists CBSA 35620 with New York
   and New Jersey counties only; the config follows the file. We kept the id `new_york_ny_nj_pa` to avoid breaking
   references, and the PA adapter is disabled with a note.
9. **Why not use the 3-digit NAICS wage when the car-wash code is suppressed?** A broad group mixes in other
   industries and would misstate the wage. UNKNOWN plus the labeled assumption range is more honest than a precise
   wrong number.
10. **What is cached, and how do you know a cached file was not changed?** Every response body is stored with its
    SHA-256 and the original request date; reading it recomputes the checksum and refuses a mismatch. Snapshots record
    those checksums, and results record the SHA-256 of the snapshot files they used, so a number can be traced to the
    exact response.

## 7. Update 2026-10-13: the layers that let a real metro rank every tract

Written so that both Rahul Vajja and Bhavani Adula can explain it. Before this update a real metro could not rank its
tracts: there was no place layer (overpass-api.de reset every connection), traffic and places were looked up live for
one site only (so SiteIQ's metro-wide reference had no values for them), daytime workers had no source, and Census
refused the key (invalid_key earlier on 2026-10-13; HTTP 200 later the same day, measured here). This update adds
three snapshot steps.

### 7.1 Files

| File | What it does | Design choice and why |
|---|---|---|
| `config/studio/overture_places.yaml` | The ONE pinned Overture release (2026-09-23.1, dated 2026-09-25, schema v2.0.0), the extraction settings (confidence >= 0.5, permanently closed places excluded: DESIGN_CHOICE values with reasons), the license facts read from the Overture attribution page, the places guide and the CDLA text, and Overture's retention policy (public for at most 60 days, for GDPR "right to be forgotten" reasons), each page with URL, retrieval time and SHA-256. | One pinned release makes an extract reproducible; the license and retention facts live next to the code that uses them so every SourceRecord carries them. After the 60 days the code refuses to build or reuse an extract of the pin. |
| `config/studio/overture_category_map.yaml` | One reviewed row per Overture category: which profile tag it counts as (for example `car_wash` -> `amenity=car_wash`), with a note; rows with no tags mean "reviewed, not counted"; three profile tags without a category are listed with a reason. Facility rows (airport, hospital, college_university) carry a merge distance. A second review added rows for generic lodging, resorts, lodges, dessert and beverage places, food trucks, warehouse clubs and kindergartens. | A crosswalk is a judgement, so it is a reviewed table, not code. The most specific category decides, so a motel is not also counted as a hotel. |
| `studio/data/overture_config.py` | Typed models for the two YAML files and the mapping rule (`CategoryMap.tags_for`). | A typo in the YAML fails loudly (unknown keys are refused). |
| `studio/data/duckdb_http.py` | A DuckDB connection with the httpfs extension, the proxy and the CA bundle set, memory and threads capped. | DuckDB reads only the needed columns and row groups of the release's Parquet files over HTTPS (bbox predicate pushdown), so the release (about 11 GB) is never downloaded whole. How long an extract takes is not measured by a script. |
| `studio/data/overture_places.py` | Lists the release files from the public S3 bucket, queries the metro's box twice (category-path counts for the accounting, then the mapped places), checks the two answers agree, merges the places of one facility (facility rows), and caches the extract with its SHA-256, request date and release id. Only category, position, confidence, the first source's dataset and the licenses of all the place's sources are read; never names. | Every place in the box is accounted for: kept, below confidence, closed, merged into a facility, "not counted" by a reviewed row, unmapped (ignored and counted), or without a category. The cache key includes the code's format, rules version and the SHA-256 of the queries, and rows are sorted on every stored field, so a code change never reuses an old extract and the same inputs give the same bytes. |
| `studio/data/places_layer.py` | The extract as arrays: places near a point (nearest first) and counts around many points, with OpenStreetMap-style tag matching; reports filters that no category maps to. | Places matching the filters are sorted by latitude once, so a metro-wide count only measures nearby candidates. |
| `studio/data/lodes.py` | LEHD LODES 8 WAC 2023 (S000, JT00, column C000) per state: checks the decompressed file against the state's published SHA-256 list, reads the vintage from `version.txt`, sums jobs per tract. | A tract with no block in the file stays UNKNOWN, not zero: the documentation does not say a missing block means zero jobs. The one exception is a water-only tract (land area 0 in the Gazetteer), which gets 0 with that reason recorded per tract (`snapshot_layers.daytime_values`). |
| `studio/data/traffic_hpms.py` (extended) | `fetch_county_segments`: every segment with AADT of a county, page by page (2,000 per page, ordered by objectid, shapes simplified by 0.0001 degrees). | The download must equal the layer's own count for the county, or the step fails; a partial road layer is never used. |
| `studio/data/road_layer.py` | The metro's segments as arrays in `roads.npz`: nearest segment within a distance for any point, distance to a given segment, save and load without pickles. Each segment remembers which HPMS page returned it. | Same nearest-road rule as the live lookup and the SYNTHETIC city, so a site and the reference are measured the same way. |
| `studio/data/snapshot_layers.py` | Builds the three layers: places (extract plus per-tract counts per business type and factor, in SiteIQ's rings), roads (all counties plus each tract centroid's nearest segment), daytime workers (LODES jobs per tract). | The extract's box is every tract centroid widened by the largest market ring plus the 3 miles a site may be from its centroid, so no ring reaches past the box. |
| `studio/data/snapshot_files.py` | The layer file names and a reader that checks each file against the SHA-256 in the manifest. | A changed file is refused and the layer is UNKNOWN with the reason. |
| `studio/data/snapshot.py` (extended) | Runs the new steps `roads_by_tract`, `overture_places`, `lehd_daytime_workers`; writes the layer files and their SHA-256 in the manifest's `files`; Overpass only with `--with-overpass`. | Overture is the default place source (reasons in data_sources.md 2c); OpenStreetMap stays optional. |
| `studio/data/fabric_snapshot.py`, `snapshot_coverage.py` | The real-metro fabric answers places and roads from the stored layers (tract centroids from the stored per-tract answers, computed with the same rule), daytime workers from LODES; a place search that reaches outside the extract's box is UNKNOWN, not a partial count. Coverage counts each layer's share of tracts; for the values SiteIQ sums (residents, daytime workers, households, renters) it is the share of tracts whose market-ring sum is known. Percentages in reasons are rounded down. | Coverage of a place factor is multiplied by the share of its tag filters that are mapped (`definition_share`), so an unmapped tag lowers coverage instead of being "none found". One tract without a value makes every ring sum that includes it UNKNOWN, so counting tracts one by one would overstate what SiteIQ can rank. |
| `studio/metros/rings.py` | The ring helpers (profile rings x metro multiplier; near and market ring) and the 3-mile own-tract distance, moved here from SiteIQ and re-exported there. | The data layer must measure places in exactly SiteIQ's rings without importing SiteIQ. |
| `studio/profiles/naics_check.py`, `scripts/studio_verify_naics.py` (`make studio-naics`) | Look every profile NAICS code up in the BLS QCEW industry titles file and write `results/studio_naics_check.json`. | www.census.gov/naics answered 403. A code is `verified: true` only when the list has it with the current "NAICS " prefix and the same title; the profile keeps the list's URL, date, SHA-256 and published title. |
| `studio/data/business_patterns.py` (fix) | County Business Patterns answers HTTP 204 with an empty body for a code without rows (seen for "44" and "445131", NAICS2017); that is now "no rows for this code", not a failed step. | One code without rows should not discard the other codes' counts. |
| tests (`test_data_overture.py`, `test_data_places_layer.py`, `test_data_road_layer.py`, `test_data_lodes.py`, `test_data_snapshot_coverage.py`, `test_profiles_naics.py`, `test_data_business_patterns_empty.py`, `test_data_snapshot.py`) | Recorded fixtures (an Overture listing and a 60-row CC0 sample as Parquet with the CDLA text, Illinois LODES files, two HPMS county pages, BLS title rows), mapping-table rules, accounting, hand-built geometry with hand-checkable distances, coverage accounting, and a check that SiteIQ measures exactly the per-tract counts the snapshot stores. | No test touches the network; `make studio-layer-fixtures` re-records the fixtures. |

### 7.2 Likely interview questions on this update

1. **Why Overture instead of OpenStreetMap?** Overpass reset every connection from this environment on two days; a
   metro-wide extract gives the per-tract counts the metro-wide ranking needs, which one-site Overpass queries cannot;
   one pinned release makes the result reproducible; and the places theme has no ODbL share-alike obligations. The
   OpenStreetMap connector is still there as an optional source.
2. **What licenses apply to the places?** They are mixed by source: CDLA Permissive 2.0 (most providers), Apache 2.0
   (Foursquare) and CC0 1.0 (AllThePlaces), per Overture's attribution page. A single place usually mixes two: its
   provider's license plus CDLA Permissive 2.0 for Overture's own contributions, which are listed after the provider
   in its sources. So each stored place keeps the set of all its sources' licenses (for example
   `Apache-2.0+CDLA-Permissive-2.0`) and every extract counts places per license set. CDLA asks that its text travel
   with shared data; the per-tract counts are "Results", which it does not restrict.
3. **How do you know the mapping from Overture categories to our tags is right?** It is a reviewed table with a note
   per row, not a claim of an official crosswalk; approximate rows say so. Places whose category has no row are
   counted as unmapped, and tags with no category lower coverage instead of being treated as "none nearby".
4. **Why read the bbox columns instead of the geometry?** DuckDB can skip whole row groups using the bbox column
   statistics (predicate pushdown) and the geometry column is never downloaded; for a point the bbox corners are the
   point, as the Overture places guide's own example uses.
5. **Why is a tract with no LODES block UNKNOWN and not zero, and does LODES count people?** The LODES
   documentation does not say that a missing block means zero jobs, so filling zero would be a guess; UNKNOWN keeps
   the gap visible, the market-ring sums that include such a tract stay UNKNOWN, and coverage counts exactly those
   ring sums. The one exception is a water-only tract (land area 0): it has no land for a workplace, so it gets 0
   with that reason. LODES counts jobs at workplaces (JT00 = all jobs), not people; one person can hold two jobs, so
   Studio calls it "jobs located in the tract".
6. **Why are airport, hospital and university places merged?** Overture files many places of one airport or campus
   under the same category (terminals, departments, buildings), while the profiles' tags mean one feature per
   facility, so a big campus outranked several distinct anchors. Facility rows merge places within a set distance of
   an already kept place (1 mile for airports, 0.25 miles for hospitals and universities; design choices), from the
   highest confidence down and never chained; each extract counts what was merged.
7. **Why store the whole HPMS road layer instead of one value per tract?** A scored site is anywhere, not only at a
   centroid. With the layer stored, a site and every tract centroid are measured by the same rule offline; the
   per-tract values are also stored, and the tests check they equal a fresh search.
8. **How do you know a stored layer was not changed?** The manifest records each layer file's SHA-256; the fabric
   refuses a file that no longer matches and marks the layer UNKNOWN with the reason. The results files record the
   manifest's SHA-256, which pins the layer files too.
9. **What does "verified" mean for a NAICS code?** That the BLS QCEW industry titles list has the code with the
   current-vintage prefix and the same title. It does not prove the code is the right one for the business; that is
   still the author's choice, and "44" stays unverified because the list has "44-45".
10. **Why did County Business Patterns fail even after the key worked?** For a code without rows the API answers HTTP
    204 with an empty body, which the old code read as "not JSON" and failed the whole step. Now that answer means
    "no rows for this code" and the step lists the codes without rows.

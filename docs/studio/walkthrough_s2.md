# Walkthrough: Studio S2, SiteIQ location scoring and "suggest a better area"

Written on 2026-10-12 and updated on 2026-10-13 (API keys became visible; later the same day the real-metro
snapshots gained places, roads and daytime workers, see S1 walkthrough section 7), so that both Rahul Vajja and
Bhavani Adula can explain this phase in their own words.

**What S2 is.** SiteIQ answers two questions for a founder: "how good is this address for my kind of business, in
this metro?" (`score_site`) and "which parts of the metro look better?" (`suggest_areas`). It reads everything
through the S1 data fabric, so it works the same way for the SYNTHETIC metro and for every real metro, and it never
fills a gap: a factor without data is UNKNOWN, is left out of the score and is named in the notes. It also produces
the demand estimate (low / mode / high bounds plus percentiles 0-100) that the S3 simulator samples from.

**What was done and what was not.**
- Done: `score_site` and `suggest_areas` with factor-level provenance, percentile-rank normalization within the metro,
  an overall score, a data-coverage score, notes, a traceable demand estimate, a GeoJSON layer and a budget check;
  optional OpenRouteService drive-time areas (used only when `ORS_API_KEY` is set and a live client is passed);
  city-license context for Chicago and NYC; a validation on 10 SYNTHETIC cities against hidden true demand with
  bootstrap intervals and two baselines; a sanity table for the five real metros; tests with fixtures only.
- Update 2026-10-13: real metros can now rank their tracts for five of the six business types. The rebuilt
  snapshots hold ACS tract data (api.census.gov accepted the key later on 2026-10-13 after refusing it as invalid_key
  earlier that day), an Overture places extract, the HPMS road
  layer and LODES daytime workers, so the metro-wide reference has values for every factor except short-term rental
  comparables. The real-metro table (section 5) now reports tract counts and score spreads, with no accuracy claim.
- Still not done: short-term rental cannot be ranked in a real metro (Inside Airbnb coverage not verified, so
  comparables are UNKNOWN), and every real metro is still in limited mode because its lowest business type
  (short-term rental, 0.6779 to 0.6826 in `results/studio_metro_coverage.json`) is below the 0.80 threshold.
- OpenRouteService with a key: one answer was recorded on 2026-10-13 as a test fixture
  (`tests/studio/fixtures/ors/isochrones_houston_recorded.json`, around the Census Geocoder's point for Houston City
  Hall, a public building). Its shape matched the parser, so the drive-time code is now tested against a real answer
  as well as the hand-written one. A one-off live run of `score_site` (car wash, Houston, same point, live client) used
  the 10-minute drive-time area for its demand estimate and a live HPMS road lookup, and had no overall score (limited
  mode); its output was not saved to `results/`, so no number from it is quoted here.
- The profile weights were **not tuned**. The S1 profile notes planned "Weights are tuned in S2 on the SYNTHETIC metro
  only"; S2 decided against it, because tuning on a world the same author generated would only fit the author's own
  assumptions. The validation reports the weights as they are, and the six profile YAML notes now say "Weights are not
  tuned; S2 decided against fitting them to the SYNTHETIC metro".

## 1. Files

### Package `src/uwcopilot/studio/siteiq`

| File | What it does | Why it is built this way |
|---|---|---|
| `__init__.py` | Exports `score_site`, `suggest_areas`, the models and `MetroContext`. | One import line for the web app and S3. |
| `models.py` | Pydantic models: `SiteInputs`, `Budget`, `TradeAreaInfo`, `FactorScore`, `DemandInput`, `DemandEstimate`, `ContextItem`, `SiteScore`, `SuggestedArea`, `Exclusion`, `BudgetCheck`, `Suggestions`. Adds the status `FOUNDER_INPUT` for values the founder types in. | Frozen models with `extra="forbid"`: a result cannot be changed after it is built and a typo in a field name fails. Every result carries the disclaimer and the scoring-method name. |
| `trade_area.py` | Rings = profile rings x metro `ring_multiplier`. The first ring is the NEAR ring (counts of places), the middle ring is the MARKET ring (population sums, income, tourism). `Area.contains()` for rings and drive-time polygons (ray casting), circles for GeoJSON. | Two rings per site keep the factors explainable: each reason says what was counted and within which ring. |
| `isochrones.py` | OpenRouteService v2 isochrones: builds the POST body, sends the key only in the `Authorization` header through the S1 `PoliteClient`, parses the GeoJSON polygons, returns a `SourceRecord` whose `license_note` carries the attribution string the answer asks for and whose `vintage` carries the routing data dates the answer reports. | The key never enters the cache key, the stored request, a log or a `SourceRecord`; a test checks the cache files. Attribution and data dates are copied from each answer, not typed in. |
| `context.py` | `MetroContext`: the metro's fabric plus caches (tract arrays, road lookups per position, reference distributions per profile, the metro mode). `metro_context(metro_id)` keeps one per metro. `SiteOutsideMetro` error. | Ranking a site needs every tract of the metro measured once; doing that per request would be slow. |
| `factors.py` | Measures the raw value of every factor the profile needs at one location, with status, plain reason, sources and details (count, nearest distance, saturation, median nightly price, road name). | Kept separate from scoring so the same code measures the founder's site and every reference tract: the comparison is like for like. |
| `reference.py` | Measures the profile's site factors at every tract centroid of the metro and computes mid-rank percentiles. Needs at least 20 values (`MIN_REFERENCE_VALUES`, a design choice) to rank. | "Normalized within the metro" means a score of 80 is "better than most locations here", in any metro. |
| `scoring.py` | `score_site`: normalize each weighted factor, combine the data-backed ones into the overall score, compute coverage, build notes. | Plain weighted mean, so every overall score can be recomputed by hand from the factor table. |
| `demand.py` | The demand estimate: each profile formula written as a sum of terms (data x rates x constant), low / mode / high from the rate ranges (all rates at one end at once: hard bounds), percentiles 0-100 and P10 / P50 / P90 from a seeded Monte Carlo that draws each rate independently, every input listed with its status and profile path. | S3 samples from these inputs; S4's ledger can cite each one. |
| `enhancements.py` | The drive-time demand area (with the fallback reason recorded) and city-license context (shown, not scored). | Things that exist only for some sites or metros must not change how sites are ranked against each other. |
| `suggest.py` | `suggest_areas`: score every tract from the reference, exclude tracts missing required data, rank, explain the top ones with `score_site`, check the budget, build the GeoJSON layer. | The same scoring as `score_site`, so a suggested tract and a pin dropped on its centroid get the same score (a test checks it). |
| `validation.py` | Spearman correlation, top-k hits, the two-level bootstrap, the per-city evaluation against the hidden truth, the baselines and the real-metro sanity table. | Validation code is the only Studio code that reads `city.true_demand`. |

### Script, tests, fixture, Make targets

| File | What it does |
|---|---|
| `scripts/eval_studio_siteiq.py` | Writes `results/studio_siteiq_metrics.json` with a `uwcopilot.governance.provenance` block (git commit, packages, the city seeds, the snapshot dates, and the SHA-256 of the SYNTHETIC metro config and the six profile YAMLs in `eval_sets`). Offline: configs, snapshots on disk and generated cities only. `make studio-siteiq`. |
| `tests/studio/test_siteiq_trade_area.py` | Rings, polygon membership, isochrone request and parsing (hand-written and recorded answers), attribution and data dates copied into the record, the key kept out of the cache and the record, the ring fallbacks, the license context. |
| `tests/studio/test_siteiq_scoring.py` | Every weighted factor scored or explained, the overall equals the weighted mean, lower-is-better scoring, rent with and without a quote, a value without enough reference values shown but not scored, a missing tract never counted as zero, determinism, sites outside the metro, drive time used only for demand. |
| `tests/studio/test_siteiq_demand.py` | Formula terms match the S1 vocabulary; hand-worked car-wash and restaurant examples; a missing term is left out, never filled. |
| `tests/studio/test_siteiq_suggest.py` | Ranking, exclusion counts, consistency with `score_site`, GeoJSON, budget notes, a real metro with no snapshot. |
| `tests/studio/test_siteiq_validation.py` | Metric helpers, bootstrap determinism, a small SYNTHETIC city end to end, the real-metro table (including failed snapshot steps copied from the manifest), the script's output and provenance. |
| `tests/studio/fixtures/ors/isochrones_handwritten.json` | Hand-written OpenRouteService answer (SYNTHETIC squares in open ocean), labeled "hand-written from documentation, NOT recorded". Kept because the scoring tests need polygons around the SYNTHETIC metro. |
| `tests/studio/fixtures/ors/isochrones_houston_recorded.json` | One live answer recorded on 2026-10-13 (5, 10 and 15 minutes around the Census Geocoder's point for the street address 901 Bagby St, Houston, TX 77002), with its SHA-256; the key is not in it. |
| `tests/studio/fixtures/ors/record_isochrones.py` | Re-records that fixture by hand (`ORS_API_KEY` set; never in CI). Reads the point from the recorded geocoder fixture and builds the body with the project's own `isochrone_payload`. |
| `Makefile` | `studio-siteiq` (the validation), `studio-siteiq-test` (SiteIQ tests only) and `studio-siteiq-fixtures` (re-records the OpenRouteService fixture; needs `ORS_API_KEY`, never run in CI). |

## 2. How a site is scored

1. **Rings.** The car-wash profile's rings are `[1, 3, 5]` miles; in a metro with `ring_multiplier: 1.5` they become
   `[1.5, 4.5, 7.5]`. The NEAR ring is the first one and the MARKET ring the middle one.
2. **Measure** (factors.py). Residents, daytime workers, households and renters are summed over the tracts whose
   centroid lies in the market ring, always including the site's own tract. If any of those tracts has no value the sum
   is UNKNOWN. Income is the household-weighted mean of the published tract medians in the market ring (tracts without
   one are skipped and counted). Competitors and anchors are counted in the near ring, tourism anchors in the market
   ring, short-term rental listings in the near ring. Traffic is the AADT of the nearest road segment with counts.
   Rent is the founder's quote, else the metro value.
3. **Normalize** (reference.py). The same measurement is made at every tract centroid of the metro. A site's
   percentile is `100 x (values below + half the values equal) / n`. For "lower is better" factors (competition,
   laundromat income, rent) the score is `100 - percentile`.
4. **Combine** (scoring.py). The overall score is the weighted mean of the factors that have a score and are backed
   by data (DATA, SYNTHETIC or FOUNDER_INPUT), with the weights rescaled over them. If those factors carry less than
   half of the profile's weight (`MIN_SCORED_WEIGHT = 0.5`, a design choice), there is no overall score.
5. **Coverage.** The weighted share of the profile's required factors that have data at this site, with the S1 rule:
   demand-only inputs count with the average weight; ASSUMPTION and UNKNOWN count zero.
6. **Demand** (demand.py). For a car wash, `cars/day = AADT x capture_rate`; low uses the low end of the capture-rate
   ASSUMPTION range, mode the mode, high the high end. With several rates (restaurant: visits x capture share) low
   and high need every rate at its extreme at once, which almost never happens, so they are hard bounds only. The
   distribution in between comes from a Monte Carlo (20,000 draws, seed 42): each rate drawn independently from its
   own triangle, percentiles 0-100 kept (the 0th and 100th are the bounds). The simulator samples those percentiles;
   it used to sample a triangle over the bounds, whose median sat far above the mode. Motel and short-term rental
   formulas have no data term, so their estimate is "ASSUMPTION rates only" and does not change from site to site.
7. **Notes.** One note per UNKNOWN factor, per factor that has a value but no score (rent without a quote; a value
   with too few metro reference values, such as a live road lookup in a real metro), for limited mode, for a missing
   overall score, for an incomplete demand estimate, plus the SYNTHETIC label where it applies. The factor's own
   reason says the same thing in its words ("Not scored: ...; left out of the overall score").

`suggest_areas` scores every tract the same way, excludes tracts where a weighted site factor is UNKNOWN (counting
each reason), ranks the rest, and re-scores the top five with `score_site` to get their reasons: the three factors
with the largest weight x score, plus a "watch out" when a used factor scores below 50.

## 3. Design choices

- **Percentile ranks within the metro**, not z-scores or fixed thresholds. Percentiles need no assumption about the
  shape of the distribution, are unaffected by outliers (one huge highway), and read plainly ("percentile N of M
  locations in this metro"). The cost: a score is relative to its metro, so "80 in Houston" and "80 in New York" both
  mean "better than most of that metro", not the same absolute demand.
- **Never fill.** A missing tract makes a sum UNKNOWN rather than counting it as zero; a missing factor is left out
  of the overall score and named in the notes; a tract missing required data is excluded from suggestions, not
  guessed. The coverage score shows how much of the score rests on data.
- **Rent.** No free commercial-rent source exists, so the metro value is the same for every site and cannot rank
  sites; it is shown but not scored. A founder's quote is scored by its position within the metro range, and the
  method text says that range is not market data.
- **Drive time only for demand.** Scores always use rings so that a site and the metro's reference tracts are
  measured the same way (drive-time areas for thousands of reference tracts would cost thousands of requests). When
  `ORS_API_KEY` is set and a live client is passed, the demand estimate uses the 10-minute drive-time area instead
  of the market ring; otherwise the reason for using rings is recorded in `TradeAreaInfo`.
- **Stored layers in real metros.** Snapshots now store the places and road layers, so `build_reference` measures
  them at every tract centroid exactly as at a site (the tests check that SiteIQ's counts equal the per-tract counts
  the snapshot stores). Only a snapshot without those layers, used with a live client, falls back to live lookups at
  a seeded sample of tract centroids (`live_sample`, default 0).
- **The budget cannot filter tracts.** There is no tract-level rent or build-out cost data. The budget is compared
  with the metro rent range and the sum of the profile's capex ranges, and `filters_tracts` is `false`.
- **Weights unchanged.** The weights are the profiles' DESIGN_CHOICE values. They were not tuned on the SYNTHETIC
  cities (see the next sections for what the validation says about them).

## 4. How the validation works

- **World.** 10 SYNTHETIC cities, seeds 20261013 to 20261022 (the demo city, seed 20261012, is not one of them), 300
  tracts each. Each city has a hidden true demand per tract and business type, written by the same author with a
  different formula from SiteIQ's (gravity decay, saturating competition, interactions, a hidden appeal field, noise;
  see `TRUE_DEMAND_NOTES` in `studio/data/synthetic_city.py`).
- **Metrics.** Spearman rank correlation between SiteIQ's overall score and the true demand over the tracts SiteIQ
  ranks; top-5 hit rate = share of SiteIQ's 5 best tracts among the 20 best tracts by true demand (out of every
  tract, including ones SiteIQ excluded).
- **Baselines.** Random ranking (expected Spearman 0; expected hit rate = true-top tracts among the ranked ones divided
  by the number ranked, computed per city) and the profile's single highest-weight factor used alone.
- **Intervals.** 1,000 resamples, seed 42, 95% percentile intervals. Correlations: a two-level bootstrap (cities with
  replacement, then each city's tracts with replacement; the correlation is averaged over cities). The same draws are
  used for SiteIQ, the baseline and their difference, so the difference is paired. Hit rates: the cities are
  resampled, which is coarse with 10 cities.

## 5. Results (copied from `results/studio_siteiq_metrics.json`)

Values rounded to three decimals from the file. SYNTHETIC cities only; this is not real-world accuracy.

| Business type | SiteIQ Spearman [95% CI] | Single-factor baseline (factor) | SiteIQ minus baseline | Demand estimate (mode) Spearman |
|---|---|---|---|---|
| car_wash | 0.072 [0.028, 0.121] | 0.091 [0.047, 0.136] (traffic_aadt) | -0.019 [-0.043, 0.004] | 0.091 [0.047, 0.136] |
| laundromat | 0.791 [0.764, 0.812] | 0.813 [0.786, 0.834] (renter_households) | -0.022 [-0.050, 0.004] | 0.813 [0.786, 0.834] |
| motel | 0.157 [0.090, 0.219] | 0.208 [0.092, 0.323] (tourism) | -0.051 [-0.127, 0.027] | not defined |
| restaurant_cafe | 0.745 [0.706, 0.777] | 0.745 [0.710, 0.775] (resident_population) | 0.001 [-0.015, 0.016] | 0.756 [0.718, 0.788] |
| retail_storefront | 0.400 [0.356, 0.438] | 0.594 [0.545, 0.640] (resident_population) | -0.195 [-0.236, -0.149] | 0.590 [0.542, 0.636] |
| short_term_rental | 0.593 [0.549, 0.628] | 0.507 [0.458, 0.557] (tourism) | 0.085 [0.054, 0.115] | not defined |

"not defined": the motel and short-term rental estimates have no data term, so they do not vary by site.

| Business type | SiteIQ top-5 hit rate [95% CI] | Single-factor baseline | Random ranking (expected) |
|---|---|---|---|
| car_wash | 0.060 [0.020, 0.120] | 0.080 [0.000, 0.180] | 0.067 |
| laundromat | 0.420 [0.300, 0.540] | 0.220 [0.080, 0.360] | 0.067 |
| motel | 0.100 [0.020, 0.180] | 0.020 [0.000, 0.060] | 0.067 |
| restaurant_cafe | 0.180 [0.040, 0.360] | 0.140 [0.080, 0.200] | 0.067 |
| retail_storefront | 0.080 [0.020, 0.140] | 0.100 [0.020, 0.180] | 0.067 |
| short_term_rental | 0.140 [0.060, 0.220] | 0.000 [0.000, 0.000] | 0.067 |

Each factor's own Spearman correlation with the true demand, averaged over the 10 cities (direction-adjusted scores,
so a negative value means the factor points the wrong way in this world):

| Business type | Factor correlations |
|---|---|
| car_wash | traffic_aadt 0.091, households -0.149, median_household_income 0.065, competition 0.084, anchors -0.068 |
| laundromat | renter_households 0.813, median_household_income 0.583, competition -0.416, anchors 0.649 |
| motel | traffic_aadt -0.019, tourism 0.208, competition -0.046, anchors 0.073 |
| restaurant_cafe | resident_population 0.745, daytime_workers 0.759, median_household_income -0.223, competition -0.631, anchors 0.643 |
| retail_storefront | resident_population 0.594, daytime_workers 0.607, traffic_aadt -0.008, median_household_income -0.455, competition -0.415, anchors 0.537 |
| short_term_rental | tourism 0.507, str_comparables 0.543, median_household_income -0.113, anchors 0.464, competition -0.336 |

Diagnostic (`diagnostics` in the file): in all 10 cities the share of tract centroids whose nearest road is the
generator's own local street is 1.0.

Real metros (`real_metro_sanity`, rebuilt snapshots dated 2026-10-13, rerun after the S1 section 7 layers were
added and again after the verify-and-fix pass: second category-map review, airport/hospital/university places merged
per facility, water-only tracts at 0 jobs): all five are in limited mode ("data coverage 0.68 is below the threshold 0.80"). `snapshot_steps_not_ok` now
lists only enhancements that are not available (state DOT traffic adapters, Inside Airbnb, and the Dallas, Houston
and Los Angeles open-data datasets). Tracts ranked per business type, with the score spread (P10 / P50 / P90), counts
and spread only, no accuracy claim:

| Metro (tracts) | Car wash | Laundromat | Motel | Restaurant/cafe | Retail | Short-term rental |
|---|---|---|---|---|---|---|
| new_york_ny_nj_pa (4928) | 4731 (33.9 / 49.7 / 67.2) | 4860 (34.3 / 50.7 / 65.1) | 4774 (33.4 / 50.9 / 65.3) | 4705 (34.9 / 49.3 / 65.6) | 4571 (37.0 / 50.4 / 63.7) | not measured |
| los_angeles_ca (3112) | 3005 (33.5 / 49.7 / 67.0) | 3101 (33.4 / 50.6 / 65.6) | 3005 (33.6 / 50.6 / 66.0) | 3099 (35.1 / 50.8 / 64.4) | 3003 (37.3 / 50.2 / 63.6) | not measured |
| chicago_il_in (2292) | 2209 (31.9 / 51.1 / 66.7) | 2282 (31.7 / 51.1 / 66.9) | 2209 (32.2 / 51.4 / 66.6) | 2236 (34.0 / 49.6 / 66.5) | 2161 (34.0 / 50.5 / 65.8) | not measured |
| dallas_fort_worth_tx (1704) | 1543 (32.9 / 49.7 / 67.4) | 1702 (33.4 / 50.4 / 65.9) | 1543 (34.0 / 50.9 / 66.6) | 1702 (32.8 / 50.5 / 66.3) | 1542 (35.4 / 50.9 / 66.6) | not measured |
| houston_tx (1615) | 1389 (33.1 / 49.8 / 66.2) | 1606 (34.6 / 50.1 / 64.9) | 1389 (35.0 / 51.2 / 66.0) | 1597 (32.9 / 50.1 / 67.4) | 1376 (36.7 / 51.6 / 65.0) | not measured |

Scores are rounded to one decimal here from the file. Tracts that are not ranked are excluded with the reason in the
file (`excluded`): mostly no HPMS segment within 0.5 miles of the centroid (car wash, motel, retail), a market ring
that includes a tract without a LODES value (restaurant, retail; 164 tracts in New York, which equals the share the
coverage report gives for daytime workers) or without a published ACS median income. Short-term rental is "not measured: no tract could be ranked" because comparables are UNKNOWN in
every tract. Before this update the same table said "not measured" for every metro and business type.

The synthetic part took 125.6 s of wall-clock time on a shared 4-CPU machine with other agents running (from
`method.elapsed_seconds_synthetic_part`); it is not a benchmark. Re-running the script on 2026-10-13 gave the same
`by_business_type` block value for value as the 2026-10-12 run (fixed seeds), including the rerun after the
verify-and-fix pass, the rerun after the real-metro layers were added and the rerun after their verify-and-fix pass
(`by_business_type` and `diagnostics` compared as JSON: identical). The results now pin each real-metro snapshot by the SHA-256 of its files
(`snapshot_fingerprint`), matching `results/studio_metro_coverage.json`.

## 6. What the results say

- **SiteIQ beats random for every type** on correlation (every lower bound is above 0), but for car wash and motel
  only barely, and the car-wash top-5 hit rate (0.060) is no better than random (0.067).
- **SiteIQ does not beat the simplest baseline** except for short-term rental (+0.085, interval above zero). For
  laundromat, restaurant/cafe, car wash and motel the difference interval includes zero. For retail it is clearly
  worse (-0.195, interval below zero). Its top-5 hit rate is higher than the baseline's for laundromat, motel,
  restaurant and short-term rental; the intervals overlap except for short-term rental (0.140 [0.060, 0.220] against
  0.000 [0.000, 0.000]).
- **Why retail loses:** the factor table shows income and competition pointing the wrong way in this world
  (-0.455 and -0.415). In the generator, lower-income renter tracts sit in the dense core where walk-in demand is,
  and competitors cluster where demand is, as they do in real cities. A "fewer competitors is better" weight then
  penalizes the best places. The same competition sign appears for laundromat, restaurant and short-term rental.
- **Why car wash is near random:** the diagnostic shows every tract centroid's nearest road is the generator's local
  street, while the true demand uses the busiest road within 0.3 miles. Scoring a drive-by business at a tract
  centroid measures the wrong road. For a founder's own pin on the frontage road the measure is right; for "suggest
  a better area" it is not.
- **What not to conclude:** these numbers describe the author's synthetic world. They are useful for finding design
  problems like the two above, not for claiming SiteIQ works in Houston or New York.

## 7. Limitations and open issues

- Real metros: short-term rental cannot be ranked (no comparables source). The metro-wide "limited mode" (the lowest
  business type decides) is shown as a note on every result, also for the five types that can be ranked; a
  per-business-type mode is an open design question. The resolved items (ACS data, a places layer, a per-tract road
  layer, daytime workers) are described in the S1 walkthrough, section 7.
- Drive-by businesses need a "busiest road within a frontage distance" measure for area suggestions; the fabric only
  offers the nearest road. This needs a small S1 fabric extension (all segments within a radius). Caution: the
  generator's car-wash truth already uses "the highest AADT within 0.3 miles", so after such a change the synthetic
  validation would partly measure SiteIQ against its own definition, and the report must say so.
- The competition factor's direction is a prior that the synthetic world contradicts; real outcome data (S9) is the
  right place to calibrate weights, not the synthetic city.
- Daytime workers have no real source yet (S1 open issue), so restaurant and retail will lack that factor even with a
  Census key.
- OpenRouteService: the attribution string is copied from each answer into the record, but the terms of use are still
  to verify on openrouteservice.org, and the web app (S7) must show that attribution wherever a drive-time area is
  drawn.
- Census tract boundaries are not loaded, so the GeoJSON layer shows centroids and approximate rings, not tracts.

## 8. Likely interview questions

1. **What does a site score of 80 mean?** The site's weighted factors rank, on average, around the 80th percentile
   of the metro's tract locations. It is a ranking within one metro, not a probability and not a sales forecast.
2. **Why percentile ranks instead of raw numbers or z-scores?** Factors have different units (people, vehicles,
   counts) and skewed distributions. Percentiles put them on one 0-100 scale without assuming a distribution, one
   extreme value cannot dominate, and each metro is compared with itself, so the method works in any city.
3. **What happens when data is missing?** The factor is UNKNOWN, is left out of the overall score and is named in
   the notes; the coverage score drops. With less than half the weight backed by data there is no overall score at
   all. In suggestions, tracts missing required data are excluded and counted, never guessed.
4. **How do you add a new city?** Nothing changes in SiteIQ: add the metro config, run `make studio-metros`,
   `make studio-data METRO=<id>` and `make studio-coverage` (S1). SiteIQ reads the new metro through the fabric, uses
   its ring multiplier, and ranks sites against that metro's own tracts.
5. **Why validate on a synthetic city?** There is no real outcome data (which sites did well). A synthetic world with
   a known true demand is the only way to check that the ranking recovers something. The truth formula differs from
   SiteIQ's on purpose, and the result is reported as validation on a world the same author built, not as accuracy.
6. **Why compare with a single-factor baseline?** A multi-factor score must earn its complexity. Here it only beats
   "rank by the top-weighted factor" for short-term rentals and loses for retail; reporting that is the point of
   having a baseline.
7. **Why is the car-wash result close to random, and what would you change?** At a tract centroid the nearest road is
   a local street, so traffic measures the wrong road (the diagnostic is 1.0 in every city). For area suggestions I
   would measure the busiest road within a short frontage distance, which needs a fabric method that returns every
   segment in a radius.
8. **Why not tune the weights to the synthetic city?** That would fit the generator the same author wrote and make the
   validation look better without telling us anything about real places. Weights should be calibrated on real
   outcomes collected after funding (S9).
9. **How is the demand estimate built and why is the range so wide?** Each formula is data times the profile's rates;
   low, mode and high use the rate ranges' low, mode and high ends. The rates are wide ASSUMPTION ranges, so low and
   high are hard bounds, not a confidence interval. What the simulator samples is the Monte Carlo distribution of the
   formula with each rate drawn independently (percentiles 0-100, P10 / P50 / P90 shown), because a triangle over the
   bounds would treat "every rate at its extreme at once" as a likely outcome.
10. **Why are drive-time areas used only for demand and not for the score?** The score compares the site with every
    tract location measured the same way; drive-time areas for thousands of reference tracts are not practical. The
    demand estimate is about one site, so a drive-time area improves it, and the method used is recorded.

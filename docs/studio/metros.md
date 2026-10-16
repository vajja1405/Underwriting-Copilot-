# Founder Studio: the v1 metros (Phase S0)

Written on 2026-10-12.

> **Status update, 2026-10-13: the ranking, titles, states and county lists are now VERIFIED from Census files.**
> `scripts/studio_fetch_delineation.py` (`make studio-metros`) read them on 2026-10-12 from www2.census.gov, which
> answered once the network was opened:
> - ranking: `results/studio_metro_ranking.json`, from the Population Estimates Program file
>   `cbsa-est2025-alldata.csv` (column POPESTIMATE2025, metropolitan statistical areas only; SHA-256
>   bf6fad83753456413d047d1acebeb434bd067eba9d6d0be62b3f737ad1301852, retrieved 2026-10-12 19:22 UTC). The five
>   configured metros equal the top five (`configured_equals_top_five: true`);
> - titles, states and counties: the July 2023 delineation file `list1_2023.xlsx` (SHA-256
>   952c4b1e78acbb54e6ec9412434b7602fedacbf021736351a63c181bdb753629, retrieved 2026-10-12 19:22 UTC), written into
>   each `config/studio/metros/<id>.yaml` with that record.
>
> The sections below keep the original S0 plan; where they said UNVERIFIED, the verified values are now given. The
> plan's rationale claims in section 4 are still UNVERIFIED (no source has been cited for them). No population number,
> rank or county code is written here from memory: every one is copied from the two files above.

## 1. Selection rule

**v1 covers the five largest US metropolitan statistical areas (MSAs) by population in the latest Census Bureau
estimates.**

This is a design choice made in the Studio plan, not a fact about the world. The reasons the plan gives: the largest
metros should have the most new small businesses and loan applications, and the richest public data. (That reason is
itself an expectation, not a measured fact.) The rule is objective and repeatable: the next metros are simply the
next ones on the same list.

Two details of the rule (proposals for the owners to confirm):

- **MSAs, not combined statistical areas.** The plan says "metropolitan statistical areas". Combined statistical
  areas are a different Census geography, so using them could change the list.
- **Freeze the vintage for v1.** Record the vintage used. Re-check the ranking at each Census release, but do not drop
  a metro that is already built if the order shifts; add the new one instead.

## 2. What to open to confirm the ranking (when the host is reachable)

| Item | Where to check (starting points, not opened here) | What to record |
|---|---|---|
| Ranking | Census Bureau Population Estimates Program (PEP), metropolitan and micropolitan statistical area population totals, latest vintage. Start at https://www.census.gov/programs-surveys/popest.html, or the PEP datasets listed at https://api.census.gov/data.html | Table name, vintage, release date, exact URL, date accessed, and the top five MSAs with their CBSA codes |
| Official titles and counties | The OMB metro delineation file that matches the vintage, published by the Census Bureau at https://www.census.gov/geographies/reference-files/time-series/demo/metro-micro/delineation-files.html | Delineation date, file name, URL, date accessed; per metro: CBSA code, official title, each county's state FIPS and county FIPS |

These URLs were written from the plan author's knowledge and were **not opened** (the host is blocked). S1 must
confirm each URL resolves and record the exact page used.

Steps:

1. Open the latest PEP metro table. Keep metropolitan statistical areas only.
2. Sort by the latest year's estimate. Take the top five.
3. If the top five differ from the expected list below, **use the actual top five** and update this file, the S1
   metro ids and the plan's examples.
4. For each of the five, take the official title and county list from the matching delineation file.
5. Fill the tables in sections 5 and 6 and commit them with the citation.

## 3. The list, verified (2026-10-12)

The Studio plan's expected list, checked against the two Census files named in the status note. Rank and population
are copied from `results/studio_metro_ranking.json`; titles, states and county counts from the metro configs.

| Rank (POPESTIMATE2025) | Official CBSA title (delineation, July 2023) | CBSA code | Config id | States (from the file) | Counties |
|---|---|---|---|---|---|
| 1 | New York-Newark-Jersey City, NY-NJ | 35620 | `new_york_ny_nj_pa` | NY, NJ | 22 |
| 2 | Los Angeles-Long Beach-Anaheim, CA | 31080 | `los_angeles_ca` | CA | 2 |
| 3 | Chicago-Naperville-Elgin, IL-IN | 16980 | `chicago_il_in` | IL, IN | 13 |
| 4 | Dallas-Fort Worth-Arlington, TX | 19100 | `dallas_fort_worth_tx` | TX | 11 |
| 5 | Houston-Pasadena-The Woodlands, TX | 26420 | `houston_tx` | TX | 10 |

The plan expected New York to span NY, NJ and PA. The July 2023 delineation file lists CBSA 35620 with New York and
New Jersey counties only, so the config's states are NY and NJ; the config id keeps "_pa" so links and results stay
stable (renaming it is an owner decision).

## 4. What each metro is meant to test

The left column is the **plan's own rationale**, copied from the Studio plan. Its factual parts (density, tourism,
car dependence, zoning, open-data portals, state lines) are **UNVERIFIED** claims. In S1 each one gets a cited
primary source, or it is removed. The right column is the design intent: the part of Studio the metro exercises if
the claim holds.

| Metro | Plan's rationale (UNVERIFIED) | What it would exercise in Studio (design intent) |
|---|---|---|
| New York-Newark-Jersey City | Densest market; walk-in and transit traffic; a three-state metro; strong tourism for motels and short-term rentals. | Tight trade-area rings (S2 metro config); a data layer that joins tracts and state DOT data across state lines; tourism anchors for motel and short-term rental profiles. |
| Los Angeles-Long Beach-Anaheim | Very large, car-dependent and spread out; California-specific rules and costs; tourism. | Wide trade-area rings; drive-by demand; per-state cost overrides in profiles. |
| Chicago-Naperville-Elgin | Dense core plus suburbs; a rich city open-data portal (for example business licenses); also crosses a state line. | A city open-data connector (`city_open_data.py`); cross-state handling; core vs suburb contrast inside one metro. |
| Dallas-Fort Worth-Arlington | Sprawling, drive-by heavy (car washes, drive-thru food); scale of tracts and competitors. | AADT-driven demand; performance of SiteIQ and `suggest_areas` on many tracts. |
| Houston-Pasadena-The Woodlands | Large, car-dependent, no city zoning code; a contrasting Texas market to Dallas. | A second single-state Texas metro to compare with Dallas using the same state traffic adapter. The zoning claim must be sourced before any feature depends on it. |

The plan's summary, "together they span dense urban, sprawling car-dependent, multi-state and tourism-led markets",
is also UNVERIFIED until these claims are sourced.

## 5. Citation (filled 2026-10-13 from `results/studio_metro_ranking.json`)

| Field | Value |
|---|---|
| Table name | Census Bureau Population Estimates Program, CBSA totals file `cbsa-est2025-alldata.csv` |
| Vintage | Column ranked: POPESTIMATE2025 (the 2020-2025 series) |
| Release date | not recorded by the script (the file was downloaded, not its release page) |
| URL | https://www2.census.gov/programs-surveys/popest/datasets/2020-2025/metro/totals/cbsa-est2025-alldata.csv |
| Date accessed | 2026-10-12 19:22 UTC (SHA-256 bf6fad83753456413d047d1acebeb434bd067eba9d6d0be62b3f737ad1301852) |
| Top five MSAs (CBSA code, official title) | 35620 New York-Newark-Jersey City, NY-NJ; 31080 Los Angeles-Long Beach-Anaheim, CA; 16980 Chicago-Naperville-Elgin, IL-IN; 19100 Dallas-Fort Worth-Arlington, TX; 26420 Houston-Pasadena-The Woodlands, TX |
| Matches the expected list? | Yes (`configured_equals_top_five: true`); New York's states are NY and NJ, not NY, NJ and PA |

## 6. County FIPS table (filled by script)

The county rows are no longer copied into this page: `scripts/studio_fetch_delineation.py` wrote every county (state
FIPS, county FIPS, name, central or outlying) into `config/studio/metros/<id>.yaml` from `list1_2023.xlsx`, with the
file's URL, date and SHA-256 under `delineation`, and a test compares the configs with rows recorded from the file.
The placeholder table below is kept only as the S0 plan's original layout.

Source: the Census Bureau delineation file that matches the vintage above. One row per county. Do not fill from
memory.

| Metro id | CBSA code | Official CBSA title | State | State FIPS | County FIPS | County name | Central or outlying | Delineation file and date | Checked on |
|---|---|---|---|---|---|---|---|---|---|
| `new_york_ny_nj_pa` | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill |
| `los_angeles_ca` | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill |
| `chicago_il_in` | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill |
| `dallas_fort_worth_tx` | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill |
| `houston_tx` | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill | to fill |

Each metro will have several county rows. The county list goes into the metro YAML (`config/studio/metros/<id>.yaml`)
with the delineation file's name and date as its provenance.

## 7. "Why these cities?"

Short answer for anyone who asks:

> The rule is "the five largest US metropolitan areas by population in the latest Census Bureau estimates". Nothing
> personal. It is objective, anyone can check it against the Census table we cite, and it tells us which city comes
> next: the sixth on the same list.

If someone asks about a specific city's traits (tourism, zoning, density), the answer is the cited source from S1,
or "not checked yet".

## 8. Adding a sixth metro (the S1 design)

1. Take the next metro from the same Census table and vintage.
2. Write `config/studio/metros/<id>.yaml`: counties (from the delineation file), states, bounding box, trade-area
   rings, enabled enhancements, data snapshot date.
3. Run `make studio-data METRO=<id>` (a target planned in S1; national layer first, then any enhancements).
4. Run the coverage report (`scripts/studio_metro_coverage.py`, planned in S1). If coverage is below the threshold,
   the metro runs in "limited mode" and the app says so.
5. Add a state DOT traffic adapter only if the metro adds a new state and a documented dataset exists.

No Studio code changes for a new metro, unless it needs a new state adapter.

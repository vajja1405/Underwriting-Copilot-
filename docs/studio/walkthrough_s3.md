# Walkthrough: Studio S3, site-level Monte Carlo feasibility

Written on 2026-10-13 (and updated the same day after a verify-and-fix pass: SiteIQ's demand is sampled from its
percentiles, an incomplete demand estimate is flagged, years without 12 loan payments get no DSCR probability, and
derived or scaled metro values keep their ASSUMPTION labels) so that both Rahul Vajja and Bhavani Adula can explain
this phase in their own words.

**What S3 is.** The feasibility simulator answers "if I open this business at this site with this plan and this loan,
how likely is it that I can pay the loan, and what could go wrong?" Instead of one spreadsheet forecast it runs many
possible futures (1,000 paths of 36 months by default, seeded), each with its own draw of every uncertain input, and
reports shares of those futures: P(DSCR >= target) in every year with 12 scheduled loan payments (years 1 and 2
always, since the loan term is at least 24 months), P(cash < 0 within 24 months), the break-even
month distribution, P10 / P50 / P90 bands of monthly revenue, net income and cash, a tornado of the assumptions that
matter most, and the lender's stress tests. Every probability is labeled **"simulated, based on stated
assumptions"** and every result carries the Studio disclaimer: "Educational planning tool. Not financial, legal or
lending advice. Lenders make their own decisions."

**What was done and what was not.**
- Done: `simulate()` with every sampled distribution listed in the result (status and source per input); monthly
  P&L and cash per path; debt service from the lender copilot's own loan formulas; the three shocks (competitor
  entry, rate rise, one-off repair or vacancy); the tornado; the lender stress preset built from the lender's own
  constants; a handoff to the lender's projection schema; a validation on SYNTHETIC worlds with a known true future
  under two conditions (well specified and misspecified), with bootstrap intervals and two baselines; timing.
- The site input depends only on a small interface, `SiteDemand` (low / mode / high demand in the profile's unit plus
  its provenance; from SiteIQ also the demand's percentiles 0-100 and whether the estimate is complete). SiteIQ's
  `SiteScore` (S2) is accepted directly; a contract test scores a real SiteIQ site on the SYNTHETIC metro and feeds
  it to the simulator.
- Not done: no real-world accuracy can be measured. No metro has outcome data (which plans succeeded), so the results
  file says "not measured" for real metros. The simulator runs on real metros (the prime rate from FRED, QCEW wages
  where the snapshot has them), but those metros are in limited mode and most inputs there are ASSUMPTION ranges.
- Not cited: SBA SOP 50 10 could not be opened here, so the DSCR target is the SYNTHETIC Northwind guideline's term
  loan minimum, labeled "lender policy (SYNTHETIC Northwind guideline NBC-4.2), editable".

## 1. Files

### Package `src/uwcopilot/studio/feasibility`

| File | What it does | Why it is built this way |
|---|---|---|
| `__init__.py` | Exports `simulate`, `run_simulation`, `SiteDemand`, `Plan`, `Loan`, `RangeOverride`, `FeasibilityResult`, the DSCR target helpers and `LENDER_STRESS`. | One import line for the web app and the S6 package. |
| `inputs.py` | `SiteDemand` (the only contract with SiteIQ: low / mode / high, optional percentiles 0-100, `complete` and `missing`), `as_site_demand()` (accepts a `SiteDemand`, a dict, or anything with `low/mode/high/unit`, directly or under `demand` / `demand_estimate`), `Plan`, `Loan`, `RangeOverride`. | S3 was built in parallel with S2. A small interface means a change inside SiteIQ cannot break the simulator, and a founder can type a demand range when SiteIQ has no data. A SiteIQ estimate that is UNKNOWN is refused with a message asking for a typed range; an incomplete one (a term left out because its data is UNKNOWN) is used with a note in the result saying demand is understated. |
| `assumptions.py` | Loads `config/studio/feasibility/assumptions.yaml` (12 simulator assumptions that are not part of any profile) with the profiles' own `Quantity` model; `DscrTarget`, `default_dscr_target()` (imports `DSCR_MINIMUM["term_loan"]` from `agent/policy_credit.py`), `custom_dscr_target()`. | Same provenance rules as the profiles: every number is SOURCED with a source or an ASSUMPTION range with a note. The target is imported, never retyped, so the founder side and the lender side cannot drift apart. |
| `distributions.py` | `Distribution` (triangular, fixed, or empirical = SiteIQ's percentiles 0-100 interpolated linearly; with unit, status and source), inverse-CDF sampling, `from_quantity` (profile field), `from_factor_value` (data-fabric value, including the assumptions used to derive it), `with_mode`, `annual_to_monthly_hazard`, `event_month`. | Inverse-CDF sampling lets the tornado hold every other draw fixed while one input moves, and makes P10 / P90 of each input exact. |
| `setup.py` | `build_setup()` collects every input in a fixed order and decides where each comes from (founder > user-edited range > metro data > profile or simulator ASSUMPTION). Applies user edits (`Plan.assumption_overrides`, unknown names rejected with `risk/scenario.py`'s `OverrideError`), checks bounds, sets the note rate (quoted, or prime + a stated spread) and checks the plan (equity + loan must cover capex). | Every input is decided in one place, so the result can list them all. An UNKNOWN metro value is never filled silently: the profile range is used and a note says so. |
| `loan.py` | Monthly payment and annual debt service through `agent/credit_calculations.py` (`amortizing_monthly_payment`, `proposed_annual_debt_service`); remaining balance; the payment schedule re-amortized after a rate rise. | The founder and the lender compute debt service with the same function. |
| `model.py` | `draw()` (every random number once, in a fixed order), `input_values()`, `run_paths()` (monthly demand, volume, revenue, NOI, debt service, net income, cash per path; vectorized NumPy), `StressLevers`, `single_point_run()` (the one-forecast case). | One function turns draws into paths; the base run, the tornado, the stress tests and the validation truths all call it, so they cannot use different formulas. |
| `metrics.py` | DSCR per year and path (the lender's `coverage_ratio`, two decimals), P(DSCR >= target), P(cash < 0 within 24 months), break-even month, P10 / P50 / P90 bands. | Small functions with hand-worked tests. |
| `tornado.py` | One-at-a-time swings of every ranged input between its P10 and P90 with all other draws held fixed; ranked by the swing of year-2 net income (P50 across paths); top three = "top drivers". | Common random numbers isolate one input's effect; ranking by dollars of year-2 net income matches the year the lender uses. |
| `stress.py` | `LENDER_STRESS`: the revenue stress and the rate stress (variable-rate loans only), with levers and thresholds imported from `agent/policy_credit.py` (NBC-4.5, applied to startup loans by NSP-4.2). Each test is simulated over every path and also computed the lender's way on the year-2 P50 with the startup program's own arithmetic (`policy_startup.program_ratio`, `revenue_stressed_noi`: exact decimals, rounded half up). | The founder sees the same test, with the same rounding, that the lender copilot will run. A test pins a case (1.125 exactly) where half-up rounding and Python's `round()` differ. |
| `result.py` | Pydantic output models: `FeasibilityResult`, `MonthBand`, `YearBand`, `BreakEven`, `LoanTerms`, `TornadoBar`, `Tornado`, `StressOutcome`; `SIMULATOR_VERSION`. | Frozen models with `extra="forbid"`. The label field only accepts "simulated, based on stated assumptions". |
| `simulate.py` | `simulate()` and `run_simulation()` (which also returns the setup, draws and per-path arrays). | The steps are in order: profile with metro overrides -> metro inputs from the fabric -> setup -> draws -> base paths -> tornado and stress on the same draws -> summaries. |
| `projection.py` | Builds the lender's own `ProjectionYear` (NSP-1.2) and `SimulationOutputs` (NSP-4.4) objects from a result. | A founder package (S6) can never carry a projection the lender copilot would reject as malformed. |
| `worlds.py` | SYNTHETIC validation worlds: a plan, a loan and a site demand range per world, anchored to a drawn one-forecast DSCR (section 5). | Worlds that are neither hopeless nor trivially safe, so the probabilities are tested where they matter. |
| `validation.py` | Hidden true futures under two conditions, per-world scores, bootstrap summaries, reliability table, many-truth diagnostics. | Separate from the simulator so the truth can include effects the simulator does not model. |

### Config, script, tests, Make target

| File | What it does |
|---|---|
| `config/studio/feasibility/assumptions.yaml` | The 12 simulator assumptions: price elasticity, monthly demand noise, competitor entry (annual probability, demand loss), rate shock (annual probability, size), one-off shock (annual probability, revenue loss, repair cost as a share of capex), capex overrun, payroll burden, spread over prime. Every one is an ASSUMPTION range with a note naming what should replace it. A test fails if any is marked SOURCED. |
| `scripts/eval_studio_feasibility.py` | Writes `results/studio_feasibility_metrics.json`: validation per business type and condition, a pooled summary, a reliability table, timing, one example run, and a `RunProvenance` block (git SHA, input file checksums, the SYNTHETIC metro's seed and generator checksum, load average). `make studio-feasibility` runs it. |
| `tests/studio/feasibility_helpers.py` | Shared SYNTHETIC test inputs, including a fully pinned car-wash setup for hand-worked checks. |
| `tests/studio/test_feasibility_*.py` (11 files, 80 tests) | Assumptions file rules, distributions and hazards, loan math against hand-worked values, metrics, the monthly model (a hand-worked month 1 and month 14), setup and input sources, simulate (labels, reproducibility, consistency with the paths, lender handoff, the SiteIQ contract), stress (constants and the lender's formulas), tornado (sorting, holding the seed), validation (worlds, anchoring levers, truths, scores) and the script (a tiny run with provenance). No test touches the network. |

## 2. How one simulation works

For each path and month t (t = 0 is the opening month):

```
demand(t)     = mature demand x ramp(t) x seasonality(calendar month) x (price / reference price)^(-elasticity)
                x (1 - competitor loss, from the month a competitor opens) x noise(t)
volume(t)     = min(demand(t), capacity) x open days in the month        (per-day units such as cars/day)
              = min(demand(t), capacity)                                 (per-month units such as booked nights)
revenue(t)    = volume(t) x price, minus the lost share in the month of a one-off event
NOI(t)        = revenue(t) x (1 - sum of cost ratios) - paid hours x wage x (1 + payroll burden)
                - rent x (1 + escalation)^(whole years open) - repair bill in the month of a one-off event
debt(t)       = the loan payment (re-amortized after a rate rise on a variable-rate loan)
net income(t) = NOI(t) - debt(t)
cash(t)       = equity + loan - capex x (1 + overrun) + running sum of (net income - owner draw)
```

- **Ramp** is linear from the starting share to 1 over the sampled months to maturity (a design choice).
- **Noise** is `exp(sd x z - sd^2 / 2)` with z standard normal, so its mean is 1 and it does not bias demand.
- **Shocks** happen at most once per path. Their month follows a constant monthly hazard h with
  `1 - (1 - h)^12 = annual probability`. Holding a path's uniform fixed, a higher probability can only make the
  shock happen sooner, which keeps the tornado monotone.
- **NOI** follows the startup program's definition (NSP-1.2): before debt service, depreciation and owner
  distributions. Income taxes are not modeled.

The inputs and where each comes from (first match wins):

| Source | Status in the result | Examples |
|---|---|---|
| A value the founder typed in the plan or loan | USER_STATED, fixed | price, rent quote, capex, wage, quoted rate |
| A range the founder edited (`assumption_overrides`) | USER_STATED, triangle | any distribution by name |
| The site's demand range | MODEL_ESTIMATE (SiteIQ), ASSUMPTION (SiteIQ with rates only), SYNTHETIC or USER_STATED | `mature_demand` (SiteIQ's percentiles when given, otherwise a triangle) |
| Metro data from the data fabric | DATA_X_ASSUMPTION (QCEW weekly wage / assumed weekly hours), DATA (FRED prime rate), SYNTHETIC (the generated city's prime rate), or ASSUMPTION (rent ranges; the generated city's rent and wage, which are profile ranges scaled by a SYNTHETIC index) | `hourly_wage`, `monthly_rent`, the prime rate |
| The business-type profile (S1) or `assumptions.yaml` | ASSUMPTION (SOURCED once a source is cited) | capacity, cost ratios, ramp, shocks, elasticity |

The note rate is the founder's quote, or the metro's prime rate plus a spread (the founder's, or the ASSUMPTION mode
from `assumptions.yaml`). On the SYNTHETIC metro the prime rate is SYNTHETIC and the result says "NOT a published
rate"; on a real snapshot it is FRED DPRIME with its observation date.

**Outputs.**
- **P(DSCR >= target) in year y**: the share of paths whose year-y NOI / year-y debt service, rounded to two decimals
  like the lender rounds it, is at least the target. Reported only for years with 12 scheduled loan payments: with a
  30-month loan, year 3 has 6 payments (a half-year of debt service would make the ratio look twice as good) and with
  a 24-month loan none; such a year gets no DSCR and no probability, and a note says why.
- **P(cash < 0 within 24 months)**: the share of paths whose cash balance goes below zero in months 1-24.
- **Break-even month**: the first month that starts three months in a row with net income >= 0 (one good month in a
  noisy series is not break-even); P10 / P50 / P90 across paths, and the share that never breaks even in the horizon.
- **Bands**: P10 / P50 / P90 across paths, per month (revenue, net income, cash) and per year (revenue, NOI, net
  income, DSCR).
- **Projected DSCR, the lender's way**: year-2 P50 NOI / annual debt service (NSP-4.1), next to the probability.
- **Tornado**: section 3.
- **Lender stress**: the revenue stress (revenue lower by `REVENUE_STRESS_SHARE`, NOI lower by the same dollars,
  compared with `REVENUE_STRESS_MIN_DSCR`) and, for variable-rate loans only, the rate stress (`RATE_STRESS_ADD_PCT`
  points on the whole loan, compared with `RATE_STRESS_MIN_DSCR`). All four constants are imported from
  `agent/policy_credit.py` (SYNTHETIC Northwind guideline NBC-4.5). Each test is reported as a simulated share of
  paths and as the lender's base-case formula on the year-2 P50. Studio explains these tests; it never approves or
  declines.

## 3. Design choices

- **Triangular distributions.** The profiles already state every uncertain number as low / mode / high, so a
  triangle uses exactly what was written down, without inventing a standard deviation. It is bounded (no negative
  rents) and easy to explain.
- **Except for SiteIQ's demand.** SiteIQ's low and high put every rate at the same end at once (hard bounds). A
  triangle over them would treat that extreme as likely, and its median sat far above the most likely demand, which
  pushed every probability up. So SiteIQ hands over the percentiles of its formula with each rate drawn
  independently (a seeded Monte Carlo), and the simulator samples those ("empirical", linear between percentiles).
  The one-forecast case still uses SiteIQ's mode.
- **Inverse-CDF sampling with one uniform per input and path.** Each input's value is `quantile(u)`. The tornado can
  then set one input to its exact P10 or P90 while every other draw stays the same.
- **Tornado = one-at-a-time swings, holding the seed.** For every ranged input: set it to its P10 on every path and
  rerun, then to its P90; the swing is the absolute difference in the P50 of year-2 net income. The method ignores
  interactions between inputs (two inputs that only hurt together will not show it); that limit is stated in the
  result's `method` text.
- **Shocks at most once per path.** Simple to explain and to test. A business can suffer two repairs; the model would
  understate that.
- **Competitor-entry probability moved by saturation.** The mode of the annual probability is placed within its
  ASSUMPTION range by the site's competition percentile from SiteIQ: a less saturated area is assumed to attract more
  entrants. This is a design choice, written into the distribution's source text.
- **The DSCR target is a policy parameter, not a fact.** It defaults to the SYNTHETIC guideline's term-loan minimum,
  imported from code, and the founder can enter their lender's own number.
- **Same arithmetic as the lender.** Debt service comes from `credit_calculations.py`; the base-case projected and
  stressed DSCR use the startup program's half-up decimal rounding; the projection is built with the lender's own
  pydantic models.
- **No silent filling.** A metro value that is UNKNOWN falls back to the profile's ASSUMPTION range with a note in the
  result; a site whose demand is UNKNOWN is refused; a site whose demand estimate left a term out (for example
  daytime workers, which have no real source yet) gets a note that demand is understated.

## 4. An example run (copied from `example_run` in `results/studio_feasibility_metrics.json`)

SYNTHETIC validation world 0 for a car wash (a generated plan, not a real site): site demand 238 / 383 / 502
cars/day (low / mode / high, rounded), loan 1,811,191.30 over 60 months, variable rate 9.25% (SYNTHETIC prime 6.25% +
3.0 points ASSUMPTION spread), annual debt service 453,809.88. 1,000 paths, seed 42.

| Output | Value |
|---|---|
| P(year-1 DSCR >= 1.25) | 0.245 |
| P(year-2 DSCR >= 1.25) | 0.702 |
| Projected DSCR, the lender's way (year-2 P50 NOI / debt service) | 1.55 |
| P(cash < 0 within 24 months) | 0.248 |
| Break-even month P10 / P50 / P90 | 3 / 10 / 22 (0.085 of paths not within 36 months) |
| Year-2 revenue P10 / P50 / P90 | 1,351,253.93 / 1,739,012.50 / 2,139,594.43 |
| Year-2 DSCR P10 / P50 / P90 | 0.90 / 1.53 / 2.30 |
| Top drivers (tornado) | mature_demand, paid_hours_per_month, hourly_wage |
| Revenue stress: lender's way on the year-2 P50 / simulated P(year-2 DSCR >= 1.00) | 0.78 (below 1.00) / 0.348 |
| Rate stress: lender's way / simulated P(year-2 DSCR >= 1.10) | 1.48 (at least 1.10) / 0.772 |

How to read it: the lender's single base-case ratio (1.55) clears the target, yet about three in ten simulated
futures do not reach 1.25 in year 2, a quarter run out of cash within two years, and the revenue stress fails on the
base case. That gap between one number and the spread of outcomes is what the simulator is for.

## 5. How the validation works

- **Worlds.** For each business type, 400 SYNTHETIC worlds on the SYNTHETIC metro. A world draws a plan (capex,
  loan share, working capital, term, fixed or variable rate, sometimes a stated price or a rent quote) and a target
  one-forecast year-2 DSCR (uniform 0.6 to 2.4). It is then **anchored**: the site's most likely demand is set by
  bisection so the one-forecast DSCR (every input at its mode, no shocks, no noise) equals the target ("demand").
  When even the capacity mode cannot reach it, the founder is given a rent quote that does ("rent_quote"). When
  neither lever works, the world is kept and counted ("none"). Without anchoring most worlds would be hopeless or
  trivially safe, and a probability that is always near 0 is easy to score well on.
- **Hidden truth, two conditions.** Each world gets one true future, drawn from an independent random stream:
  - *well_specified*: one more draw from the simulator's own model. This checks the code, not the world.
  - *misspecified*: the truth adds effects the simulator does not model: Student-t (3 degrees of freedom) monthly
    noise scaled to unit variance, a mean-one lognormal site-level demand error (log-sd 0.25), and with probability
    0.3 a 6-month local downturn cutting demand by a uniform 20-50%. The same worlds and the same forecasts are
    scored against both truths.
- **Scores.** P10-P90 band coverage (share of the 36 months whose true value is inside the band) for revenue and net
  income; the Brier score of P(year-2 DSCR >= 1.25) and of P(cash < 0 within 24 months) against the true outcome.
- **Baselines.** A single forecast (every input at its most likely value, no shocks, no noise, read as yes = 1 or
  no = 0: the plan a spreadsheet shows) and the base rate (the share of the same worlds with the outcome, computed
  in-sample, so it is optimistic).
- **Diagnostic with 100 more true futures per world.** One true future is what a founder lives through, so it is the
  headline. But the months of one path move together (a strong site is above the median every month), so one path
  per world gives a noisy coverage estimate. The diagnostic draws 100 more true futures per world (another stream)
  and reports the expected coverage and the gap between each world's forecast probability and its true probability
  (the share of those 100 futures with the outcome). With n forecast paths, a P10-P90 band estimated from samples is
  expected to hold 0.8 x (n - 1) / (n + 1) of new draws; the script computes this reference (0.7984 for 1,000 paths).
- **Intervals.** Bootstrap over worlds (1,000 resamples, seed 42, 95% percentile intervals), so the months of one
  world stay together. The simulator-minus-single-forecast difference is paired.

## 6. Results (copied from `results/studio_feasibility_metrics.json`)

Values rounded to three decimals from the file, 400 worlds per business type, 1,000 paths per world, seed 42.
SYNTHETIC worlds only; this is not real-world accuracy.

### Well specified (the truth comes from the simulator's own model)

P10-P90 band coverage (target: the reference 0.798 for 1,000 paths).

| Business type | Worlds (demand / rent quote / none) | Revenue: one true future | Revenue: 100 true futures | Net income: one true future | Net income: 100 true futures |
|---|---|---|---|---|---|
| car_wash | 400 (400 / 0 / 0) | 0.791 [0.766, 0.813] | 0.797 [0.794, 0.800] | 0.796 [0.766, 0.825] | 0.799 [0.796, 0.802] |
| laundromat | 400 (397 / 2 / 1) | 0.791 [0.766, 0.812] | 0.798 [0.795, 0.800] | 0.802 [0.771, 0.831] | 0.797 [0.794, 0.800] |
| motel | 400 (140 / 119 / 141) | 0.778 [0.753, 0.803] | 0.801 [0.799, 0.804] | 0.815 [0.787, 0.842] | 0.800 [0.796, 0.803] |
| restaurant_cafe | 400 (400 / 0 / 0) | 0.780 [0.752, 0.806] | 0.798 [0.796, 0.801] | 0.827 [0.796, 0.858] | 0.798 [0.794, 0.801] |
| retail_storefront | 400 (400 / 0 / 0) | 0.783 [0.754, 0.811] | 0.799 [0.797, 0.802] | 0.789 [0.753, 0.821] | 0.798 [0.795, 0.802] |
| short_term_rental | 400 (0 / 400 / 0) | 0.783 [0.756, 0.808] | 0.799 [0.796, 0.801] | 0.775 [0.743, 0.805] | 0.798 [0.795, 0.801] |
| **pooled** | 2400 (1737 / 521 / 142) | 0.784 [0.774, 0.795] | 0.799 [0.798, 0.800] | 0.800 [0.789, 0.812] | 0.798 [0.797, 0.799] |

P(year-2 DSCR >= 1.25).

| Business type | Outcome rate | Mean forecast | Single forecast says yes | Brier: simulator | Brier: single forecast | Brier: base rate (in-sample) | Simulator minus single forecast | Mean abs gap to the true probability |
|---|---|---|---|---|---|---|---|---|
| car_wash | 0.233 | 0.259 | 0.635 | 0.157 [0.136, 0.178] | 0.468 [0.420, 0.517] | 0.178 [0.151, 0.199] | -0.310 [-0.362, -0.260] | 0.031 [0.028, 0.034] |
| laundromat | 0.307 | 0.323 | 0.613 | 0.186 [0.168, 0.202] | 0.415 [0.367, 0.463] | 0.213 [0.195, 0.228] | -0.229 [-0.276, -0.181] | 0.038 [0.036, 0.042] |
| motel | 0.125 | 0.147 | 0.385 | 0.102 [0.082, 0.121] | 0.330 [0.282, 0.378] | 0.109 [0.084, 0.134] | -0.229 [-0.277, -0.181] | 0.026 [0.024, 0.028] |
| restaurant_cafe | 0.105 | 0.115 | 0.645 | 0.092 [0.070, 0.116] | 0.595 [0.545, 0.643] | 0.094 [0.069, 0.119] | -0.503 [-0.558, -0.442] | 0.027 [0.025, 0.029] |
| retail_storefront | 0.193 | 0.223 | 0.630 | 0.150 [0.129, 0.170] | 0.537 [0.490, 0.585] | 0.155 [0.131, 0.177] | -0.388 [-0.445, -0.333] | 0.033 [0.031, 0.036] |
| short_term_rental | 0.205 | 0.192 | 0.623 | 0.156 [0.135, 0.178] | 0.532 [0.482, 0.585] | 0.163 [0.139, 0.185] | -0.377 [-0.434, -0.320] | 0.031 [0.029, 0.033] |
| **pooled** | 0.195 | 0.210 | 0.588 | 0.141 [0.132, 0.149] | 0.480 [0.460, 0.499] | 0.157 [0.147, 0.166] | -0.339 [-0.361, -0.317] | 0.031 [0.030, 0.032] |

The pooled mean true probability (from the 100 extra futures per world) is 0.2111, next to a mean forecast of 0.210.

P(cash < 0 within 24 months).

| Business type | Outcome rate | Mean forecast | Single forecast says yes | Brier: simulator | Brier: single forecast | Brier: base rate (in-sample) | Simulator minus single forecast | Mean abs gap to the true probability |
|---|---|---|---|---|---|---|---|---|
| car_wash | 0.740 | 0.735 | 0.240 | 0.149 [0.129, 0.170] | 0.530 [0.480, 0.580] | 0.192 [0.169, 0.212] | -0.381 [-0.435, -0.323] | 0.031 [0.029, 0.034] |
| laundromat | 0.698 | 0.675 | 0.242 | 0.185 [0.169, 0.205] | 0.495 [0.448, 0.545] | 0.211 [0.192, 0.227] | -0.310 [-0.360, -0.257] | 0.036 [0.034, 0.039] |
| motel | 0.728 | 0.748 | 0.210 | 0.159 [0.138, 0.181] | 0.537 [0.487, 0.585] | 0.198 [0.176, 0.217] | -0.378 [-0.435, -0.321] | 0.029 [0.026, 0.032] |
| restaurant_cafe | 0.973 | 0.956 | 0.835 | 0.027 [0.014, 0.042] | 0.188 [0.152, 0.223] | 0.027 [0.012, 0.043] | -0.161 [-0.194, -0.125] | 0.015 [0.014, 0.017] |
| retail_storefront | 0.880 | 0.881 | 0.720 | 0.098 [0.077, 0.122] | 0.275 [0.230, 0.323] | 0.106 [0.084, 0.129] | -0.177 [-0.223, -0.134] | 0.023 [0.021, 0.025] |
| short_term_rental | 0.887 | 0.884 | 0.555 | 0.088 [0.070, 0.108] | 0.393 [0.347, 0.443] | 0.100 [0.078, 0.122] | -0.305 [-0.355, -0.253] | 0.023 [0.021, 0.025] |
| **pooled** | 0.818 | 0.813 | 0.467 | 0.118 [0.109, 0.126] | 0.403 [0.382, 0.423] | 0.149 [0.139, 0.160] | -0.285 [-0.305, -0.263] | 0.026 [0.025, 0.027] |

Reliability of P(year-2 DSCR >= 1.25), pooled over business types.

| Forecast bin | Worlds | Mean forecast | Observed share |
|---|---|---|---|
| [0.0, 0.2) | 1341 | 0.106 | 0.098 |
| [0.2, 0.4) | 789 | 0.281 | 0.275 |
| [0.4, 0.6) | 220 | 0.482 | 0.414 |
| [0.6, 0.8) | 49 | 0.665 | 0.551 |
| [0.8, 1.0] | 1 | 0.802 | 1.000 |

### Misspecified (the truth has effects the simulator does not model)

| Business type | Revenue: one true future | Revenue: 100 true futures | Net income: one true future | Net income: 100 true futures |
|---|---|---|---|---|
| car_wash | 0.552 [0.515, 0.587] | 0.585 [0.581, 0.590] | 0.668 [0.632, 0.706] | 0.703 [0.698, 0.707] |
| laundromat | 0.626 [0.594, 0.658] | 0.596 [0.591, 0.601] | 0.725 [0.691, 0.758] | 0.703 [0.699, 0.707] |
| motel | 0.622 [0.588, 0.655] | 0.632 [0.626, 0.636] | 0.727 [0.692, 0.761] | 0.731 [0.727, 0.735] |
| restaurant_cafe | 0.645 [0.612, 0.677] | 0.612 [0.607, 0.617] | 0.770 [0.735, 0.803] | 0.753 [0.749, 0.757] |
| retail_storefront | 0.604 [0.566, 0.638] | 0.605 [0.600, 0.610] | 0.779 [0.742, 0.814] | 0.749 [0.745, 0.753] |
| short_term_rental | 0.677 [0.645, 0.706] | 0.659 [0.655, 0.663] | 0.747 [0.714, 0.781] | 0.743 [0.739, 0.746] |
| **pooled** | 0.621 [0.607, 0.635] | 0.615 [0.613, 0.617] | 0.736 [0.722, 0.749] | 0.730 [0.728, 0.732] |

| Outcome (pooled) | Outcome rate | Mean forecast | Brier: simulator | Brier: single forecast | Brier: base rate (in-sample) | Simulator minus single forecast | Mean abs gap to the true probability |
|---|---|---|---|---|---|---|---|
| P(year-2 DSCR >= 1.25) | 0.193 | 0.210 | 0.143 [0.135, 0.152] | 0.494 [0.473, 0.515] | 0.156 [0.146, 0.165] | -0.350 [-0.375, -0.329] | 0.040 [0.039, 0.042] |
| P(cash < 0 within 24 months) | 0.835 | 0.813 | 0.120 [0.112, 0.129] | 0.431 [0.410, 0.451] | 0.138 [0.128, 0.148] | -0.311 [-0.331, -0.290] | 0.034 [0.033, 0.035] |

Per business type, the misspecified Brier scores and gaps are in `conditions.misspecified.per_business_type`.

### Time per 1,000 paths

| Business type | Inputs swung in the tornado | 1,000 paths, path model only: median ms | simulate() with tornado and lender stress, 1,000 paths: median ms |
|---|---|---|---|
| car_wash | 25 | 4.75 | 232.15 |
| laundromat | 24 | 2.97 | 169.53 |
| motel | 27 | 3.99 | 269.08 |
| restaurant_cafe | 24 | 2.48 | 154.28 |
| retail_storefront | 28 | 3.77 | 251.99 |
| short_term_rental | 24 | 3.82 | 219.12 |

Median of 15 repeats after a warm-up, on a shared 4-CPU machine with other agents running (load average 0.94 at the
start of the run and 1.38 at the end, from `provenance.environment`). It is not a benchmark.

Real metros: "not measured: no real outcome data exists for any metro, so no real-world accuracy can be computed".

## 7. What the results say

- **The code is calibrated when its model is right.** With 100 true futures per world, revenue coverage is 0.799
  [0.798, 0.800] pooled, matching the reference of 0.798 for bands estimated from 1,000 paths, and every business type
  is between 0.797 and 0.801. The forecast probabilities match the true probabilities: the mean absolute gap is 0.031,
  and the pooled mean forecast (0.210) sits next to the mean true probability (0.2111).
- **The one-future headline is noisy.** With one true future per world, pooled revenue coverage is 0.784 [0.774,
  0.795]; its interval does not include the reference. The same worlds and forecasts score at the reference with 100
  futures, so the shortfall comes from which single future each world happened to draw, not from a bias in the code.
  The headline is reported as it came out.
- **When the world has effects the model lacks, the bands are too narrow.** Under misspecification, expected revenue
  coverage falls to 0.615 [0.613, 0.617] and net income coverage to 0.730 [0.728, 0.732]. The probability gap grows
  from 0.031 to 0.040. The Brier score of P(year-2 DSCR >= 1.25) barely moves (0.141 to 0.143) because the added noise
  and demand error average to one; the downturn lowers demand, and the share of worlds with cash below zero within 24
  months rises (0.818 to 0.835). In plain words: when reality has fatter tails than the plan assumes, a "P10-P90"
  band holds about six in ten months, not eight. Founders and lenders should read the band as a minimum spread.
- **A single forecast is a poor probability.** The plan at most-likely values says "DSCR >= 1.25 in year 2" in 0.588
  of worlds; 0.195 of worlds get there. Its Brier score (0.480) is far worse than the simulator's (0.141), with a
  paired difference of -0.339 [-0.361, -0.317]. In this model the most-likely case is optimistic: several cost ranges
  are right-skewed (their mean is above their mode), demand above capacity is lost, and the shocks only hurt. That is
  a property of the author's ASSUMPTION ranges and generator, not a fact about real businesses.
- **The simulator beats the in-sample base rate**, but narrowly (pooled 0.141 [0.132, 0.149] against 0.157
  [0.147, 0.166] for year-2 DSCR); per business type the intervals overlap.
- **Reliability.** The two lower bins line up with what happened. The [0.4, 0.6) and [0.6, 0.8) bins show fewer
  successes than forecast (0.414 against 0.482 over 220 worlds; 0.551 against 0.665 over 49 worlds); with these
  counts and one outcome per world, that is within the noise suggested by the 100-futures gap, but it is the place to
  watch with more worlds.
- **Anchoring.** Short-term rental worlds could only be anchored by a rent quote (400 of 400): under the profile's
  most-likely values the business does not cover its costs at full occupancy. 141 of 400 motel worlds could not be
  anchored with either lever (counted as "none", not dropped). Both are findings about the S1 ASSUMPTION ranges to
  revisit when real sources are cited.

## 8. Limitations and open issues

- No real outcome data: real-world accuracy is not measured, and the probabilities are only as good as the stated
  assumptions. Calibrating the assumption ranges needs funded-business outcomes (S9).
- Every simulator assumption and every profile range is an ASSUMPTION of the project author; none is SOURCED yet.
- One-at-a-time tornado swings miss interactions between inputs.
- Shocks happen at most once per path; income taxes, seasonality of costs, working-capital timing (receivables,
  inventory) and the owner's other debts are not modeled. Debt service starts in the opening month (no interest-only
  period).
- The misspecified condition is one choice of unmodeled effects written by the same author; other misspecifications
  could hurt more.
- The simulator does not include other debt (seller notes); the lender's NSP-1.2 debt service would add it.
- No profile has metro overrides yet, so seasonality is the profile's ASSUMPTION curve in every metro.
- On real metros (limited mode) most inputs are ASSUMPTION ranges; the prime rate is DATA (FRED) and the wage is DATA
  only where the QCEW snapshot has the industry.

## 9. Likely interview questions

1. **Why Monte Carlo instead of one forecast?** A single forecast puts every input at its most likely value, but the
   outcome of a business is not a straight line in its inputs: demand above capacity is lost, costs have long right
   tails, and shocks only hurt. Running many futures shows the spread and the chance of a bad outcome. In the
   SYNTHETIC validation the single forecast said "DSCR >= 1.25" in 0.588 of worlds while 0.195 got there.
2. **What does "P(year-2 DSCR >= 1.25) = 0.70" mean, and what does it not mean?** It means 70% of the simulated
   futures, under the stated assumption ranges, reach the target. It is not a forecast of real-world success, not a
   lender's approval chance, and not validated on real outcomes. That is why it is labeled "simulated, based on stated
   assumptions" and the lender records it as information only (NSP-4.4).
3. **Why validate on a synthetic world?** There is no real outcome data. A synthetic world with a known true future
   is the only way to check the code: when the truth comes from the same model, the P10-P90 band must hold about 80%
   of true months, and it does (0.799 with 100 futures per world). The misspecified condition shows how badly it
   degrades when reality is different. It proves internal consistency, not real-world accuracy.
4. **Why is the well-specified coverage 0.784 and not 0.80?** Two reasons. A band estimated from 1,000 samples is
   expected to hold 0.8 x 999 / 1001 = 0.798 of new draws. And one true future per world is noisy because its months
   move together; with 100 futures per world the same forecasts score 0.799.
5. **How does the tornado work?** Every random number is drawn once. For each uncertain input, the simulator sets that
   input to its P10 on every path, reruns, then to its P90, while all other draws stay the same ("holding the seed").
   The swing in the median year-2 net income ranks the inputs; the top three are the "top drivers" a lender will ask
   to see backed by documents. It ignores interactions between inputs.
6. **How does the lender stress preset connect to the lender side?** The levers and thresholds are imported from
   `agent/policy_credit.py`, and the base-case numbers use the startup program's own rounding functions from
   `agent/policy_startup.py`, so the founder sees exactly the number the lender copilot computes. Studio explains the
   tests; it never approves or declines.
7. **Where does the interest rate come from?** The founder's quoted rate, or the metro's prime rate plus a spread.
   The prime rate is FRED DPRIME on a real snapshot (DATA, with its observation date) and a SYNTHETIC number on the
   generated city; the spread is an ASSUMPTION range (not a lender quote) unless the founder types one.
8. **How do you add a new city?** Nothing changes in S3. Add the metro config and run the S1 steps (`make
   studio-metros`, `make studio-data METRO=<id>`, `make studio-coverage`). The simulator reads that metro's wage, rent
   range and prime rate through the data fabric; any value the snapshot lacks falls back to the profile's ASSUMPTION
   range with a note in the result.
9. **Why triangular distributions?** The profiles state every uncertain number as low / most likely / high. A triangle
   uses exactly those three numbers without inventing a standard deviation, cannot go below the low end, and is easy
   to explain to a founder. Its P10 and P90 are exact, which the tornado uses. The site's demand is the exception: it
   is a product of several ranges, so SiteIQ passes its percentiles instead of a triangle over "all low" and "all
   high", which would overweight the extremes.
10. **What would make the probabilities trustworthy for real decisions?** Sourced ranges (BLS wages, Census data,
    lender term sheets, equipment quotes) instead of assumptions, and outcome data from funded businesses (S9) to
    check calibration the same way the synthetic validation does, with the misspecified results as a warning that
    bands will be too narrow until then.

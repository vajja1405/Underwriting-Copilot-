# Walkthrough: Studio S5, path to approval

Written on 2026-10-13 so that both Rahul Vajja and Bhavani Adula can explain this phase in their own words. (The
Studio addendum in CLAUDE.md is still a proposal until both owners sign it; this phase follows it anyway.)

**What S5 is.** A founder's plan often misses the simulated targets. S5 answers: "what is the smallest change to MY
plan that would reach them?" It tries changes the founder can actually make (borrow less or more, put in more of
their own cash, keep a bigger cash reserve, ask for a longer term, spend less on the build-out, change the price,
open more or fewer days, pick another of their own candidate sites) one lever at a time, then two at a time. It
simulates each variant with the S3 simulator on the same random draws, and returns up to three smallest change-sets
with before and after probabilities and a plain-language explanation. Every probability is labeled **"simulated,
based on stated assumptions"**, and every answer says that reaching the targets does not mean a lender will approve
the loan (Studio rule C). The explanation describes a scenario ("If you borrowed $X less, the simulation
gives ..."), never an instruction, and says what would back each changed figure for a lender.

**The three default targets (all editable).**
1. P(year-2 DSCR >= 1.25) >= 0.80;
2. P(year-2 DSCR >= 1.00 under the lender's revenue stress) >= 0.80: revenue 20% lower and NOI lower by the same
   dollars, applied to every simulated path (NSP-4.2 of the SYNTHETIC startup program);
3. P(cash < 0 within 24 months) <= 0.10.

**What was done and what was not.**
- Done: `find_paths()` (single-lever moves, then pairs; common random numbers; a cache keyed by plan hash and lever
  values; the three smallest change-sets; before/after probabilities; explanations); guardrails that only let plan
  choices move; a startup-program check that refuses change-sets that would newly break the equity minimum or the
  product limits; "no path within your bounds" plus the closest result; a quick mode for the website (which already
  calls it: `src/uwcopilot/web/services/studio_runs.py`); `config/studio/levers.yaml`; the evaluation script and
  `results/studio_path_metrics.json`; tests in 8 files (the counts are in the PR summary); Make targets.
- Fixed in review (2026-10-13), each with a regression test: (1) quick mode searched with 500 simulated paths and
  re-checked with 1,000; the re-check dropped change-sets that only just met a target with 500 paths, so the website
  answered "no path" on plans where full mode found one (the test car-wash plan was one). Quick mode now keeps the
  1,000 paths and seed and gets its speed from a coarser grid and a smaller budget. (2) In a change-set with two dollar
  levers that move the same amount (more equity also lowers the loan), each change showed the value its lever would
  have had alone, so the website could show a loan figure that differed from what "apply this change" sends. Each
  change now shows the amount in the changed plan plus its own move (`own_change`). (3) The biggest single cost in a
  profile of one search was a Python loop that rounds each path's DSCR; it is now one NumPy step that gives exactly
  the same numbers.
- Fixed in a second review (2026-10-13), each with a regression test (details in sections 2, 4 and 6):
  (4) a rule of the SYNTHETIC program that the plan already failed could be made worse (a loan already above the
  NSP-8.1 maximum of $500,000 was raised further, and the text only said the targets were met); such a change-set is
  now refused, every change-set lists the program rules its changed plan still fails, and the results report a
  "path found and program-compliant" rate. (5) Locking a dollar lever did not freeze its amount (with owner equity
  locked, the reserve lever still added the founder's cash); a lock now freezes the amount. (6) An edited stress was
  printed as "the lender's" stress, and the lender's base-case NSP-4.2 ratio used the edited share; the base case now
  always uses the program's 20%, and an edited stress is called "your edited revenue stress". (7) Each change now says
  what would back the new figure for a lender (`evidence_needed`: a written quote, bank statements, ...), and the
  reserve lever carries the owner-cash caveat. (8) Explanations were orders ("Borrow $106,000 more"); they are now
  conditional scenarios. (9) Pairs of two dollar levers that both cut the loan were dropped when their screen took the
  loan out of its bounds; they are now searched. (10) "No path" claimed that no change of one or two levers works; it
  now says what the search tried and that it is not exhaustive, and says so separately when the budget ran out.
  (11) The website's call recomputed the metro's mode on every request; it is now kept per fabric object. (12) The
  budget counted only cache misses, so a repeated request on the shared cache could give a different answer; it now
  counts every evaluated variant.
- Checked, not assumed: every change-set the evaluation returned was re-simulated with S3's own `simulate()` and gave
  the same three probabilities; every applied plan was compared field by field with the founder's plan using the
  script's own table of what each lever may write; the same plans were searched again with SYNTHETIC founder facts
  (credit score, experience, a seller note in about half, some figures verified) and checked the same way; the
  heuristic search was compared with a brute-force search (section 8).
- Not done: the S6 package does not carry the path history yet. Change-sets of three or more levers are not searched
  (the build plan asks for singles then pairs). A smaller build-out that also cuts capacity is not modeled, so the
  build-out lever only means "the same business for less". The NSP-5.1 working-capital reserve is not a constraint
  of the search (section 9). No real-world accuracy can be measured: there are no real founder outcomes, so the
  results file says "not measured".

## 1. Files

### Package `src/uwcopilot/studio/path_to_approval`

| File | What it does | Why it is built this way |
|---|---|---|
| `__init__.py` | Exports `find_paths`, `prepare_search`, the models, the default targets, the settings presets, `clear_caches`. | One import line for the web app and the S6 package. |
| `models.py` | Frozen pydantic models. Inputs: `PlanCase` (profile, metro, the site's demand, the S3 `Plan` and `Loan`, and `FounderFacts`), `FounderFacts` (credit score, experience, other debt, verified fields), `OtherDebt`, `Candidate`, `Target`, `Change`, `SearchSettings`. Outputs: `Outcome` (the three probabilities, the lender's base-case ratios, targets missed, shortfall), `ProgramChecks`, `PlanAmounts`, `AppliedChange` (with `own_change` and `evidence_needed`), `ChangeSet` (with `applied_plan`, the whole changed plan, `program_rules_not_met` and `evidence_needed`), `LeverSummary`, `SearchStats` (with `variants_evaluated`, the budget count), `PathResult`. | `extra="forbid"` and `frozen=True`, as everywhere in Studio. Facts live in their own model so the guardrails can compare them as one block. `Outcome.label` only accepts "simulated, based on stated assumptions". |
| `targets.py` | `default_target()` (the three targets, each with its source), `custom_target(**edits)` (marks edited fields "edited by the user"), `describe()`, `uses_lender_stress()` and `stress_words()` (an edited stress is "your edited revenue stress", never "the lender's"). | The 1.25, the 20% and the 1.00 are imported from `agent/policy_credit.py` (through `feasibility.assumptions`), never retyped. The probability levels (0.80, 0.80, 0.10) are labeled as design choices of the build plan, not lender rules. |
| `levers.py` | Loads `config/studio/levers.yaml` and checks it against the code's allow-list (`LEVER_NAMES`, each with a fixed kind and balancing lever). Resolves bounds, builds each lever's search grid, computes effort. | A config edit can never turn a fact into a lever: the loader refuses any name not on the allow-list, and what a lever writes is code, not config. |
| `space.py` | `build_lever_space()`: the plan's money (sources = uses), each lever's current value, bounds and grid, and why a lever is not available (locked, balanced by a locked amount, verified, not applicable, needs an input the plan lacks). A locked dollar lever gets the bounds (current, current), so no other lever can move that amount. `LEVER_WRITES`: the plan fields each lever may write. | Decided once per search, so the guardrails and the search read the same numbers. A lever that is not available always says why (shown to the founder). A lock must never be weaker than a bound. |
| `guardrails.py` | `apply_changes()`: checks each lever, adds up the dollar moves, rounds them to the cent, checks every amount and bound, builds the changed plan, then compares every field of the old and new plan and refuses any change outside the levers' own fields. `NOT_A_LEVER`: names people might try (credit score, experience, other debt, note rate, demand, assumptions...) with the reason each is not a plan choice. `GUARDRAIL_RULES`: the rules shown on every result. | Defense in depth: an allow-list on the way in AND a field-by-field diff on the way out. A test monkeypatches the plan builder to slip a credit-score change through and checks the diff catches it. |
| `program.py` | `program_checks()`: the SYNTHETIC startup program's equity injection (NSP-3.1, >= 20.0%), loan limits and term range (NSP-8.1), with the thresholds and the half-up rounding imported from `agent/policy_startup.py`; `refusals()` = `newly_broken()` (a rule the plan meets would break) + `moved_further()` (a rule the plan already fails would get worse: a loan above the maximum grows, an equity share below 20% falls, a term outside the range moves further out). | A change-set that reaches the simulated targets but breaks a plain program rule, or moves the plan further from one, is not a path. Rules the plan already fails are not repaired by the search, but every change-set lists them (`program_rules_not_met`) and its explanation says so. |
| `evaluate.py` | `PathEvaluator`: draws the plan's random numbers once and simulates every variant on them (common random numbers); `outcome_of()` computes the three probabilities, the NSP-4.1 and NSP-4.2 base-case ratios and the shortfall; other debt is added to debt service, net income and cash. `rounded_dscr()`: every path's DSCR rounded to two decimals in one NumPy step, falling back to the lender's `coverage_ratio` only where NumPy and Python could round differently (a ratio ending in a half cent). `EvaluationCache`: a bounded least-recently-used store behind a lock; `plan_hash()`. | One function turns a simulated plan into the three numbers, so the search, a re-check and the tests cannot disagree. `rounded_dscr` gives exactly S3's numbers (a test compares 20,000 ratios and checks that naive NumPy rounding gets some of them wrong, so the fallback is really exercised). |
| `search.py` | `find_paths()` and `prepare_search()`: load the metro, profile and levers (cached once per process; a fabric passed in by the caller has its mode kept per object, `mode_of`), build the lever space and evaluators, run `Search` (singles, then pairs with a screen and a staircase), rank, explain. `FULL_SETTINGS`, `QUICK_SETTINGS` (the website's), `clear_caches()`. `applied_changes()` reports each lever's amount in the changed plan, its own move and the evidence that would back it. | `prepare_search()` is shared with the brute-force reference, so the reference sees exactly the same grids, bounds, guardrails and draws. The budget counts every evaluated variant, cached or not, so an answer never depends on the cache. |
| `explain.py` | The explanation of a change-set as a conditional scenario ("If you ..., the simulation gives ..."), each change's caveat, the plan after the change, the program rules it still fails, the lender's base-case ratios, what would back each changed figure (`evidence_for`), what stayed unchanged, and the status message (which says what the search tried, and when it stopped early). | Every number in the text comes from the search result; the text adds none of its own. It never says a loan will be approved, and it never gives an order (rule C). A test bans sentences that open with an imperative verb. |
| `sites.py` | `candidate_from_site_score()`: a SiteIQ `SiteScore` (S2) as a candidate site, read through the simulator's own adapter (`feasibility.inputs.as_site_demand`). | A change inside SiteIQ cannot break the search. |
| `exhaustive.py` | `exhaustive_search()`: tries every single value and every pair of values on the same grid, with the same guardrails and draws. For tests and the evaluation only. | The only honest way to say how often the heuristic misses a path. |
| `synthetic_plans.py` | Seeded SYNTHETIC plans for the evaluation and the tests (section 7), and `with_synthetic_facts()`: SYNTHETIC founder facts (credit score, experience, sometimes a seller note, some verified figures) for the guardrail check. | Separate from the search so the plan generator can be read and criticised on its own. |
| `errors.py` | `GuardrailViolation` (not a plan choice), `LeverError` and `OutOfBounds` (a plan choice with a value the plan cannot take). | The search skips `LeverError`s quietly (a value outside the bounds is simply not a candidate) but never swallows a `GuardrailViolation`. |

### Config, script, tests, Make targets

| File | What it does |
|---|---|
| `config/studio/levers.yaml` | The eight levers with label, unit, default bounds, rounding step, effort weight and a note. `status: DESIGN_CHOICE`: every number is the author's choice of how far to look by default and how hard a change is assumed to be; none is data. |
| `scripts/eval_studio_paths.py` | The evaluation (section 7) -> `results/studio_path_metrics.json` with a provenance block (`uwcopilot.governance.provenance.RunProvenance`). Options: `--plans`, `--exhaustive`, `--metros`, `--business-types`, `--output`. |
| `tests/studio/path_helpers.py` | A hand-built SYNTHETIC car-wash plan that misses the targets but has single-lever fixes; fast settings. |
| `tests/studio/test_path_levers.py` | Allow-list (a config that adds `credit_score` or changes a lever's kind is refused), bounds, grids, rounding, effort. |
| `tests/studio/test_path_guardrails.py` | Every attempt to move a fact fails: each `NOT_A_LEVER` name, credit score, experience, a verified figure, other debt, a tampered plan builder (credit score, site demand), a candidate in another metro; sources = uses for every dollar lever; a cent-rounding regression; a locked dollar amount never moves through another lever (all four). |
| `tests/studio/test_path_evaluate.py` | Outcomes equal S3's `simulate()` on the changed plan exactly; `rounded_dscr` equals S3's `dscr_by_year` exactly (random ratios, half-cent cases, no debt service); base-case ratios use the program's arithmetic; other debt; cache hits, LRU, guardrails on cache hits; the plan hash changes with everything that changes an outcome. |
| `tests/studio/test_path_monotonicity.py` | More equity, a longer term, a lower build-out and a bigger reserve never make a probability worse; borrowing less is not monotone (it trades cash for coverage). |
| `tests/studio/test_path_search.py` | Ranking, labels and disclaimer, scenario wording (no imperative openings), applied plan, determinism with seeds (and with a warm cache), infeasible target, an honest no-path message (complete and stopped early), all levers locked, founder bounds, the program checks (never break, never move further; `refusals` unit cases), an edited stress is never "the lender's", evidence for each change, quick mode, a search with fewer paths plus a re-check, the budget (and its independence from the cache), a pair whose screen is out of bounds, heuristic = brute force on small plans, a real metro in limited mode, the mode of a passed fabric computed once, and pairs of dollar levers that move the same amount. |
| `tests/studio/test_path_targets.py`, `test_path_synthetic_plans.py`, `test_path_script.py` | Default targets and sources, the edited-stress wording; the plan generator's determinism, anchoring and seeded facts; the script on tiny settings. |
| `Makefile` | `make studio-paths` (the evaluation) and `make studio-paths-test` (these tests only). |

## 2. How one search works

1. **Load once, per process.** The metro's data fabric, its mode, the business-type profile (with the metro's
   overrides), the levers file and the simulator assumptions are read once and kept (`fabric_for`, `mode_for`,
   `profile_for`, `levers_from`, `default_assumptions`). A real metro's mode alone reads every profile YAML, so it
   is worth keeping.
2. **Build the lever space.** The plan's money: equity + loan + other debt = build-out + cash reserve at opening.
   For each lever: its current value, its bounds (default from the YAML, or the founder's own), its grid (8 values
   on each side by default, rounded to $500, $0.25, whole days or the listed terms), or the reason it is not
   available.
3. **Simulate the plan as it is** (1,000 paths, 36 months, seed 42). If it meets all three targets, stop:
   `already_meets_target`.
4. **Single-lever moves.** For each available lever, try its grid from the smallest change (least effort) to the
   largest and keep the first value that meets all three targets: the smallest change of that lever.
5. **Pairs, only when fewer than three single moves were found.** Take the levers that did not reach the targets alone
   but moved the plan closer on their own. For each pair, first try both at their best single values (a screen); if
   that meets the targets, OR if the screen is not allowed at all, walk a "staircase" over the two sorted value lists
   to find the least-effort combination (start with the biggest change of the first lever and the smallest of the
   second; on success make the first smaller, on failure make the second bigger, and when the pair is not allowed
   make the first smaller too). Why the second case: more owner equity and a lower build-out both cut the loan, so
   together at full size they can take it below its bound; smaller moves of both can still be allowed and work (a plan
   of the evaluation's own data had no path for exactly this reason). A candidate site is a category, so its partner
   lever is scanned once per site.
6. **Refuse what is not allowed.** Every change-set goes through the guardrails (section 4) and the program checks
   before it is simulated: it may not break a program rule the plan meets, nor move the plan further from a rule it
   already fails. A refused change-set is not a candidate; it is not an error, and it does not count against the
   budget. The budget (2,000 in full mode, 250 in quick mode) counts every evaluated variant, simulated or read from
   the shared cache, so the same request always gives the same answer.
7. **Rank and explain.** Fewer levers first, then less total effort. Return the first three, each with before and
   after probabilities, the plan after the change, the program rules it still fails, what would back each changed
   figure, the lender's base-case ratios and an explanation. If none meets the targets: `no_path_within_bounds`, a
   message that starts "No path within your bounds" and says what the search tried (a grid and screened pairs, so not
   exhaustive) or that it stopped early, and the closest result (the smallest total shortfall seen), when one moved
   the plan closer.

**Common random numbers.** Every variant of a plan is simulated with the SAME random draws (each uncertain input keeps
its name, so it keeps its uniforms). The difference between two variants is then the effect of the change, not the
luck of the draw: with 1,000 paths a probability is only known to about +/- 0.03, so two independent runs could
easily rank a worse change above a better one. A test checks that the after-probabilities equal what S3's own
`simulate()` gives for the changed plan with the same seed, exactly.

**Quick mode (for the website).** The same 1,000 simulated paths and seed as full mode, but 4 grid values on each
side of a lever instead of 8 and at most 250 evaluated variants instead of 2,000. The coarse grid is a subset of the fine
one, so for one lever quick mode can only return the same change as full mode or a bigger one, and every number it
shows is exact for the 1,000 paths. Because the paths and seed are the same, quick and full share cached outcomes.
The first version used 500 paths and a 1,000-path re-check. That looked cheaper, but the search keeps the FIRST value
that meets every target, which is exactly a value that only just meets it; with 500 paths, many of those missed in
the re-check and were dropped, and the founder was told "no path" when full mode had found one. Fewer paths make
the search noisier exactly where it decides; a coarser grid only makes the answer a little bigger.

**Two dollar levers on one amount.** The four dollar levers add up their moves: "put in $50,000 more" (the loan
falls $50,000) plus "borrow $100,000 less" leaves a loan $150,000 lower. Each change in the result shows the amount
in the changed plan (`to_value`, what the website shows and what "apply this change" sends) and the lever's own move
(`own_change`); the explanation says each lever's own move and then the plan's totals before -> after.

## 3. The targets, and why the lender stress target exists

The original build plan (Prompt S5) had two targets: year-2 DSCR and the cash shortfall. The lead engineer added the
third after the startup program was written (DELTA 2026-10-13), because the program's binding rule is the lender's
revenue stress (NSP-4.2), not the 1.25 DSCR minimum (NSP-4.1).

The stress takes 20% of year-2 revenue away and takes the SAME dollars out of NOI (every cost is treated as fixed).
The stressed DSCR is at least 1.00 only when NOI >= debt service + 0.20 x revenue. Divide by debt service: a plan
passes only when DSCR >= 1 + 0.20 x revenue / debt service. If a business keeps m cents of NOI per revenue dollar,
then revenue = NOI / m and the condition becomes DSCR >= m / (m - 0.20). Examples:

| NOI per revenue dollar (m) | DSCR needed to pass the stress |
|---|---|
| 0.50 | 1.67 |
| 0.30 | 3.00 |
| 0.25 | 5.00 |
| 0.20 or less | never passes, at any DSCR |

So a plan can clear 1.25 comfortably and still fail the lender's stress test badly. A path that only aimed at 1.25
would show the founder a "fix" that the lender's own test still flags. The evaluation measures how often that happens
(section 8, "the stress target's effect"). The 0.80 probability level for it is a design choice, like the other two.

## 4. Guardrails: only plan choices move

| May move (plan choices) | May never move (facts and assumptions) |
|---|---|
| owner equity, loan amount, build-out cost, cash reserve at opening, price, days open, loan term, which of the founder's own candidate sites | credit score, experience, other debt (never hidden or reduced), the site's demand estimate, every assumption range, the note rate and rate type, the wage, capacity, the owner's draw, the opening date, and every figure the founder marked as verified |

How it is enforced:
1. **Allow-list in code.** `levers.LEVER_NAMES`; the YAML loader refuses anything else. `Change(lever="credit_score")`
   raises `GuardrailViolation` with the reason ("the owner's credit score is a fact; it is never changed or
   misstated").
2. **Verified figures.** `FounderFacts.verified_fields` (for example `loan.amount` backed by a term sheet): every lever
   that writes such a field is not available, and forcing one raises `GuardrailViolation`.
3. **Field-by-field diff.** After building the changed plan, every field is compared with the founder's plan; any
   difference outside the levers' own fields (`LEVER_WRITES`) is a `GuardrailViolation`.
4. **Inputs the founder has not typed are not levers.** The build-out lever needs a typed build-out cost (otherwise the
   current figure is the profile's ASSUMPTION, and lowering an assumption to pass is exactly what rule D forbids). Days
   open needs a typed staffing plan (staff hours scale with open days; without the founder's number the cost of more
   days would be guessed).
5. **Bounds whichever lever moves an amount.** The reserve lever spends owner equity, so equity's bounds hold for it
   too; borrowing less lowers the reserve, so the reserve may not go below zero. Amounts are rounded to the cent
   BEFORE these checks (a regression test covers the cent that once slipped through).
6. **Program checks.** A change-set may not break an NSP-3.1 or NSP-8.1 rule the plan meets now, nor move the plan
   further from one it already fails (a loan already above $500,000 may not grow). The rules it still fails are listed
   with it and in its explanation.
7. **Locks freeze amounts.** A locked dollar lever keeps its amount whichever lever would move it (bounds equal to the
   current value), and a lever balanced by a locked amount is not available ("put in more of your own cash" lowers
   the loan, so it stops when the loan is locked; "keep more cash in reserve" spends owner equity, so it stops when
   owner equity is locked).
8. **A changed figure is not a backed figure.** Each change carries `evidence_needed` (a written build-out quote at or
   below the new figure, bank statements for the new owner cash, a new loan request, a rent quote for the new site).
   Until that exists the figure is a plan change from a simulation, and the explanation says so.
9. **Other debt is a fact.** Its payments count in debt service, net income and cash in every simulated month; its
   proceeds count in the cash at opening. The explanation says how many other debts were kept unchanged.

## 5. Levers, bounds and effort (`config/studio/levers.yaml`, all DESIGN_CHOICE)

| Lever | What changes | Absorbed by (sources = uses) | Default bounds | Effort weight |
|---|---|---|---|---|
| owner equity | more of the founder's own cash | the loan (borrow that much less) | current .. current + 20% of project cost | 2.0 |
| loan amount | borrow more or less | the cash reserve at opening | current - 25% .. current + 10% of project cost (never below the program's $50,000 minimum unless already below) | 1.0 |
| build-out cost | the same business built for less | the loan | 15% saving .. current | 1.5 |
| cash reserve | keep more cash at opening | owner equity (founder's own cash) | 0 .. current + 20% of project cost | 2.0 |
| price | price per unit of demand | (demand responds through the ASSUMPTION elasticity) | -15% .. +15% | 1.5 |
| days open | days per week; paid hours scale in proportion | (needs a typed staffing plan) | 5 .. 7 | 1.0 |
| loan term | ask for a longer or shorter term | | 60 .. 120 months (the program's NSP-8.1 range) | 0.5 |
| site | another of the founder's candidate sites, same metro | (its own demand estimate and rent quote) | the listed candidates | 0.3 per switch |

Effort of one change = weight x size: dollars moved / total project cost for the dollar levers, the relative change for
price, days and term, and 1 for a site switch. The weights encode one judgement: asking a lender for a longer term is
easier than finding more of your own cash. They only order change-sets that have the same number of levers.

## 6. Design choices

- **Singles, then pairs, ranked by the number of levers first.** A founder can act on one change more easily than on
  two, and one change is easier to explain and to check. Effort only breaks ties within the same number of levers.
- **The staircase assumes "further in the helpful direction never hurts".** That holds exactly for owner equity, loan
  term, build-out cost and the reserve under common random numbers (`test_path_monotonicity.py`), and does not hold
  for the loan amount (borrowing less helps coverage but lowers cash) or for price (elasticity). The brute-force
  comparison in section 8 measures what this costs.
- **Bounds the founder can tighten.** The defaults are wide on purpose (they are a first look); the founder's own
  answer to "how much more cash could you really put in?" replaces them, and a founder bound must include today's
  value.
- **Program rules as constraints, probabilities as targets.** The equity minimum and the product limits are plain
  rules a lender checks first; breaking one to improve a probability, or moving further from one the plan already
  fails, would be a worse plan, not a path. The search does not try to repair a rule the plan already fails (that
  would need changes the founder has not asked about, such as a smaller project), but it says so on every change-set,
  and the results report the program-compliant path rate next to the plain one.
- **A scenario, not an instruction.** The explanation says "If you kept $X more cash in reserve at opening, the
  simulation gives ...; in this simulation all three targets would be met", followed by the caveats and the evidence a lender
  would ask for. Studio explains consequences of choices the founder could make; it does not tell them what to do
  (rule C).
- **A lock is never weaker than a bound.** Four dollar levers move three amounts (equity, loan, build-out) plus the
  reserve that follows from them, so locking a lever must freeze its amount, not only remove the lever.
- **The lender's check is the lender's.** Founders may edit every target, including the stress of the second one, but
  the base-case NSP-4.1 and NSP-4.2 ratios shown as "the lender's own check" always use the program's constants.
- **Cache key = everything that changes an outcome.** The plan, its facts, the candidates, the target, the metro
  inputs, the profile (with the metro's overrides), the simulator assumptions, the number of paths, months, seed and
  both engine versions. A stale entry can never be read for another plan or after a config edit. The cache is bounded
  (20,000 outcomes, least recently used dropped) and locked, so web requests in several threads can share it.
  `clear_caches()` resets the per-process caches after a snapshot or config change.
- **Quick mode is coarser, not noisier.** It keeps the 1,000 paths and seed and searches a coarser grid with a smaller
  budget (section 2 tells why the 500-path version was replaced). A search that stops at the first value that meets
  a target is most sensitive to noise exactly at that value, so cutting paths costs correctness while cutting grid
  values only costs a slightly bigger answer.
- **Exact numbers, faster.** `rounded_dscr` rounds every path's DSCR in one NumPy step and hands only the rare
  half-cent cases to the lender's own `coverage_ratio`, so the probabilities stay exactly S3's (tests compare them)
  while the per-path Python loop is gone. Measured run times are in the results file (section 8).
- **`ChangeSet.applied_plan`.** The whole changed plan travels with each change-set, so the website's "apply this
  change" button sends it back as the founder's new plan, and S6 can put it in the package's path history.

## 7. How the evaluation works (`scripts/eval_studio_paths.py`)

**Plans.** For every metro (the SYNTHETIC metro first, then the five real metros in their current mode) and each of
the six business types, 15 SYNTHETIC plans (540 in total), seeded (`synthetic_plans.py`):
1. the plan and loan come from the S3 validation generator unchanged (`feasibility.worlds.draw_plan`);
2. half of the founders also typed a staffing plan (so the days lever can be used);
3. the plan is anchored so that its ONE-FORECAST lender-stressed year-2 DSCR (every input at its most likely value,
   no shocks, no noise) equals a target drawn uniformly from 0.6 to 2.6, first through the site's demand, then (as in
   S3) through a rent quote; a plan that neither lever can anchor is kept and counted as "none";
4. each plan gets two other candidate sites in the same metro.
The 0.6-2.6 range is a design choice. It reaches far past 1.00 because the simulated year-2 median is usually well
below the one-forecast case (ramp-up, shocks, right-skewed cost ranges, operating leverage).

**Each plan is searched four ways**, always with the metro's data fabric passed in, exactly as the website calls the
search: full mode (the headline), quick mode on a cold cache (what the website runs on a first request), quick mode
again on the warm cache (a founder reloading the page), and full mode with the stress target switched off (only the
two original targets).

**Checks on every returned change-set:** re-simulated with S3's `simulate()` (same seed and paths); applied plan
compared field by field with the founder's plan, using the script's own table of what each lever may write
(`EXPECTED_WRITES`), not `guardrails.py`.

**Guardrail check with facts.** The headline plans carry no founder facts (like the S3 worlds), so the check above
cannot catch hidden debt or an edited verified figure. Each plan is therefore searched once more in quick mode with
SYNTHETIC founder facts (`with_synthetic_facts`: a credit score, months of experience, a seller note in about half of
the plans, each typed figure marked verified with probability 0.25), and every returned change-set is checked the
same way (facts unchanged, no verified figure moved, nothing outside its levers).

**Brute force:** on 240 plans (all 90 SYNTHETIC-metro plans, plus the car-wash and laundromat plans of each real
metro, where paths actually occur), the heuristic in quick mode (as the website runs it) is compared with
`exhaustive_search()` on the same grid, paths and seed (so both see identical outcomes).

**Statistics:** rates and means with bootstrap 95% CIs (1,000 resamples over plans, seed 42). Runtimes are wall time
of one `find_paths()` call in a warm process on a shared 4-CPU machine used by other agents at the same time; the
load average at the start and end of the run is in the provenance block.

## 8. Results (copied from `results/studio_path_metrics.json`)

Run on 2026-10-13 with `make studio-paths` (engine `studio-path-to-approval-1.2`, simulator `studio-feasibility-1.0`).
540 seeded SYNTHETIC plans: 6 metros (the SYNTHETIC metro, and Chicago, Dallas-Fort Worth, Houston, Los Angeles and
New York in their current mode, which is "limited" for all five, with the local data snapshot of 2026-10-13) x 6
business types x 15 plans. 95% CIs: bootstrap, 1,000 resamples over plans, seed 42. Every probability behind these
numbers is simulated, based on stated assumptions; nothing here is a real founder's chance of approval.

**Headline (full mode, the three default targets).**

| Metric | Value (95% CI) |
|---|---|
| Plans: already meet the targets / path found / no path within the default bounds | 4 / 55 / 481 |
| Path-found rate among the 536 plans that need a change | 0.1026 (0.0784 to 0.1306) |
| Path found AND the changed plan meets every program rule the search checks (NSP-3.1, NSP-8.1) | 0.0392 (0.0243 to 0.0578) |
| Plans that already fail a program rule at the start | 274 of 540 |
| Average levers changed in the top change-set (55 plans with a path) | 1.4364 (1.3091 to 1.5818) |
| Share of top change-sets that change one lever | 0.5636 (0.4182 to 0.6909) |
| Share of top change-sets whose changed plan meets every program rule | 0.3818 (0.2545 to 0.5273); the other 34 still fail the NSP-8.1 loan amount limit |
| Runtime per search, full mode: p50 / p95 (ms) | 347.42 (340.5265 to 359.4177) / 538.35 (519.7039 to 551.973); max 708.1 |
| Runtime per search, quick mode (the website's call), cold cache: p50 / p95 (ms) | 216.54 (209.8034 to 223.0221) / 331.084 (320.009 to 344.8795); max 551.97 |
| Runtime, the same quick request again (warm cache): p50 / p95 (ms) | 10.37 (10.065 to 10.5951) / 17.566 (16.15 to 18.43) |
| Simulations per search (median): full / quick | 79 / 50 (no search hit its budget) |

Runtimes are wall time in a warm process on a shared 4-CPU machine that other build agents were using at the same
time: the load average was 2.29 / 2.65 / 4.36 (1/5/15 min) at the start and 11.99 / 9.88 / 7.35 at the end. They are
a measurement on this machine, not of the Hugging Face Space (not measured). Every run passed the metro's data fabric,
as the website does; the fabric's mode is now computed once per fabric object (before this fix the website's call
recomputed it on every request, reading every business-type profile each time). The median quick-mode search took from
195.94 ms (Dallas-Fort Worth) to 246.71 ms (New York) per metro, and the median full-mode search from 319.7 ms
(Dallas-Fort Worth) to 407.14 ms (New York).

**The path-found rate depends on the business type and on how far the plan starts from the line.**

| Business type | Path-found rate (95% CI) | Path found and program-compliant (95% CI) | n |
|---|---|---|---|
| car wash | 0.4138 (0.3103 to 0.5287) | 0.023 (0.0 to 0.0575) | 87 |
| laundromat | 0.2022 (0.1124 to 0.2921) | 0.2022 (0.1124 to 0.2921) | 89 |
| restaurant / cafe | 0.0111 (0.0 to 0.0333) | 0.0111 (0.0 to 0.0333) | 90 |
| motel | 0.0 | 0.0 | 90 |
| retail storefront | 0.0 | 0.0 | 90 |
| short-term rental | 0.0 | 0.0 | 90 |

Why car wash differs so much between the two columns: every one of the 90 generated car-wash plans asks for a loan
above the SYNTHETIC program's $500,000 maximum (the S3 plan generator does not keep loans inside NSP-8.1), so a path
for a car wash almost never makes the plan program-compliant. The search no longer makes that worse (it refuses any
change-set that raises such a loan) and every change-set says which rule its changed plan still fails.

| One-forecast stressed DSCR band of the plan (synthetic_plans.TARGET_BANDS) | Path-found rate (95% CI) | n |
|---|---|---|
| 0.6 to 1.1 | 0.0059 (0.0 to 0.0178) | 169 |
| 1.1 to 1.6 | 0.0089 (0.0 to 0.0268) | 112 |
| 1.6 to 2.1 | 0.1587 (0.0952 to 0.2222) | 126 |
| 2.1 to 2.6 | 0.2558 (0.186 to 0.3333) | 129 |

By metro the rate runs from 0.0778 (Dallas-Fort Worth and the SYNTHETIC metro) to 0.1348 (Chicago), with
overlapping CIs. Why so few paths: most plans miss the lender-stress target and the cash target at the start (305
miss all three, 176 miss stress and cash, 36 only the stress, 15 only cash, 4 the DSCR and stress targets), and the
stress target asks for NOI of at least debt service plus 20% of revenue on 80% of simulated futures. The generator
could not anchor 90 of 90 retail storefront plans and 69 of 90 motel plans to their drawn one-forecast stress target
at any demand or rent ("anchored_by: none"), so those plans start far below the line. The search then answers "no
path within your bounds" and says what it tried; the closest result shows how far one or two changes can get.

**Why the stress target matters (measured).** With only the two original targets (year-2 DSCR and cash), the search
finds a path for 0.494 (0.452 to 0.536) of the 500 plans that need a change. Of those 247 top change-sets, 0.9393
(0.9069 to 0.9676) miss the simulated stress target (P(stressed DSCR >= 1.00) >= 0.80, our design choice), and 0.749
(0.6963 to 0.8057) also fail the lender's own single-number NSP-4.2 check on the year-2 median (stressed DSCR below
1.00); 47 miss the simulated target but pass the lender's base-case check. Another 36 plans already meet the two
original targets and still miss the stress target. So without the third target, three in four of the "fixes" shown
would fail the lender's own stress test, and almost all would fail its simulated version.

**Quick mode vs full mode (all 540 plans).** Same status for every plan: 1.0 (1.0 to 1.0); quick mode finds a path
on all 55 plans where full mode does, and full on all 55 where quick does. Same levers in the top change-set: 0.8182
(0.7091 to 0.9273); the identical change-set: 0.5091 (0.3818 to 0.6364); quick's top change-set is larger than full's
in 0.4909 (0.3636 to 0.6182) of them and smaller in none.

**Independent checks.**
- S3 re-simulation: all 608 returned change-sets (and closest results) were re-simulated with S3's `simulate()` on
  the same seed and paths: the largest difference in any of the three probabilities was 0.0.
- Fields that may not move: 0 in the headline plans, checked field by field with the script's own table. The
  headline plans carry no founder facts, so this alone says nothing about debt or verified figures; hence:
- Guardrail check with facts: the 540 plans again with SYNTHETIC founder facts (277 with a seller note, 465 with at
  least one verified figure): 564 returned change-sets and closest results checked, 0 fields moved (no fact, no
  verified figure, nothing outside the change-set's levers).
- 1,497 change-sets were refused in full mode because they would have broken a program rule the plan met or moved
  the plan further from one it already failed.

**Heuristic vs brute force** (240 plans: the 90 SYNTHETIC-metro plans and the 150 car-wash and laundromat plans of
the real metros; quick mode's settings for both): of the 236 plans that need a change, the brute force found a path
on 54 and the heuristic on the same 54 (it missed none and found none the brute force did not), with the same number
of levers on all 54. On 3 plans the heuristic's top pair has more effort than the brute force's (effort ratios 1.1209,
1.1984 and 1.1712; Chicago car wash plan 2, Los Angeles car wash plan 0, New York car wash plan 11). In all three the
brute-force pair is "borrow more" plus "a lower build-out", on a loan already above the program maximum: borrowing more
alone is refused there (it would raise that loan), so the heuristic never pairs that lever, while the pair lowers the
loan overall. The brute force took a median of 1.66 s per plan, which is why it is a reference and not the web search.

**Which levers the paths use** (top change-sets of the 55 plans with a path): cash reserve 25, build-out cost 21,
site 15, owner equity 12, price 4, loan term 1, loan amount 1. Plans returned 3 change-sets in 32 cases, 2 in 9, 1 in
14.

**Not measured:** real-world accuracy (no real founder outcomes exist; every plan is SYNTHETIC) and run time on the
Hugging Face Space hardware.

## 9. Limits and open issues

- **NSP-5.1 working-capital reserve is not a constraint of the search.** The rule needs "monthly fixed costs", which
  the startup program does not define and which S6's `to_application` will fill
  (`StartupCreditApplication.projections.monthly_fixed_costs`). Until then a change-set can lower the reserve (for
  example "borrow less") and newly miss NSP-5.1; the 24-month cash target is the simulated guard. When S6 defines
  the figure, `program.py` should add it to `CHECK_NAMES`.
- **Rules the plan already fails are not repaired.** The search refuses to make them worse and lists them on every
  change-set, but it does not look for changes that would fix them. The S3 generator does not keep loans inside
  NSP-8.1's $50,000-$500,000 range (every generated car-wash plan borrows more than $500,000), which is why the
  program-compliant path rate (0.0392) is far below the plain one (0.1026).
- **Pairs are a heuristic.** On 240 plans compared with the brute force it missed no path; 3 plans got a pair with
  more effort than needed (section 8). A lever whose every single move is refused is never paired. Three-lever
  change-sets are not searched.
- **For the web track (not changed here):** the plan page's heading for "no path" (`frontend/components/studio/
  project/path-panel.tsx`) still says "No change of one or two plan choices reaches all three targets within your
  bounds", the claim this phase removed from the engine's message; "apply this change" stores the new build-out as
  the founder's own typed figure (USER_STATED) and should use `ChangeSet.evidence_needed` instead; the website passes
  no founder bound on extra owner cash, so the default +20% of project cost stands in for the founder's answer; and the
  mock fixtures (`frontend/mock-api/fixtures/path_*.json`) were recorded with older engine versions.
- **Path-found rates describe these SYNTHETIC plans.** They depend on the generator (section 7), the editable
  targets and the default bounds (all design choices); they say nothing about any real founder.

## 10. Likely interview questions

**1. Why does the lender stress target exist? Isn't P(DSCR >= 1.25) enough?**
No. The SYNTHETIC startup program's binding rule is the NSP-4.2 revenue stress: take 20% of year-2 revenue away and
take the same dollars out of NOI. A plan passes only when NOI >= debt service + 20% of revenue, which for a business
keeping 30 cents of NOI per revenue dollar means a DSCR of about 3. So a plan can clear 1.25 easily and still fail
the lender's own test. We measured it: with only the two original targets, 0.749 of the change-sets the search showed
would fail the lender's own base-case NSP-4.2 check, and 0.9393 would miss the simulated stress target. The third
target makes the search aim at what the lender will actually test. The 0.80 probability level is our design choice,
not a lender rule, and if a founder edits the stress, the text calls it "your edited revenue stress" and the lender's
own check keeps the program's 20%.

**2. What are common random numbers and why do you need them?**
Every variant of a plan is simulated with the same random draws (same seed, each uncertain input keeps its own
uniforms). The difference between two variants is then the effect of the change, not luck. With 1,000 paths a
probability is only known to roughly +/- 0.03, so two independent runs could rank a worse change above a better one.
It also makes the result checkable: the evaluation re-ran all 608 returned change-sets through S3's `simulate()` and
got exactly the same probabilities.

**3. Why singles then pairs, and not an optimizer?**
The founder needs a short list of changes they can act on and understand. One change is easier than two, so we rank
by the number of levers first and effort second. A grid over a handful of levers is small enough to search directly,
every candidate is a real simulated plan, and the result is deterministic and explainable; a numeric optimizer would
need gradients the Monte Carlo does not have and would give answers that are hard to explain.

**4. How do you make sure the search never suggests misstating facts or hiding debt?**
Layers. An allow-list in code (`LEVER_NAMES`): the YAML cannot add a lever, and names like `credit_score`,
`other_debt` or `mature_demand` raise `GuardrailViolation` with the reason. Verified figures lock every lever that
would write them, and a founder's lock freezes the amount whichever lever would move it. After building the changed
plan we diff it field by field against the founder's plan and refuse anything outside the levers' own fields; a test
slips a credit-score change through a tampered plan builder and the diff catches it. The evaluation checks it
independently with its own table: 0 moved fields in the 540 headline plans, and, because those plans have no facts,
the same 540 plans again with SYNTHETIC facts (277 with a seller note, 465 with a verified figure): 0 moved fields in
564 change-sets.

**5. Why does quick mode use as many simulated paths as full mode?**
We first built it with 500 paths plus a 1,000-path re-check. The search keeps the first value that meets every
target, which is a value that only just meets it, so 500-path noise decided the answer and the re-check dropped
those change-sets; the website said "no path" for plans where full mode had one. Now quick mode keeps the paths and
seed and searches a coarser grid. Measured: same status as full mode on all 540 plans, never a smaller change, p50
216.54 ms through the website's own call.

**6. What does "smallest change-set" mean, and is it advice?**
Fewest levers first, then least effort. Effort = weight x size, where size is the dollars moved over total project
cost (dollar levers), the relative change (price, days, term) or 1 for a site switch; the weights are design choices.
It is not advice: the text is a scenario ("If you kept $X more cash in reserve, the simulation gives ..."), with
its caveats, the program rules it still fails and what would back each changed figure for a lender (a written quote,
bank statements). A test fails if any sentence opens with an order like "Borrow" or "Put".

**7. How do you know the heuristic is not missing paths?**
`exhaustive.py` tries every single value and every pair of values on the same grid, bounds, guardrails and draws. On
240 plans, including the real metros' car washes and laundromats where paths occur, it found paths on 54 and the
heuristic on the same 54, with the same number of levers. On 3 the heuristic's pair took more effort (ratios up to
1.1984), all for one reason we can name: a lever whose every single move is refused is never paired. The comparison
also found a real bug earlier: two dollar levers that both cut the loan were dropped when their screen went out of
bounds, which is fixed and tested.

**8. What is in the cache key, and could it serve a stale or different answer?**
The key is (plan hash, lever values). The plan hash covers the plan, its facts, the candidate sites, the target, the
metro inputs, the profile with metro overrides, the simulator assumptions, paths, months, seed and both engine
versions, so a config or data change gives a new key. Guardrails run before every lookup. The budget counts every
evaluated variant, cached or not, so a repeated request on the shared cache gives the same answer (it once did not).
A repeated quick request took p50 10.37 ms.

**9. What happens when no change works?**
The status is `no_path_within_bounds`. The message says what the search did: it tried changes of one or two plan
choices on a grid and screened pairs, so it is not exhaustive; or, if the budget ran out, that it stopped early. It
shows the closest result: the change-set with the smallest total shortfall seen, with its before/after probabilities
and what is still missed. That was the answer for 481 of 540 plans; most start far below the stress and cash targets.

**10. Does a 10% path-found rate mean the product does not work?**
No: it describes these SYNTHETIC plans under strict, editable targets and modest default bounds. Below a one-forecast
stressed DSCR of 1.6 almost none can be fixed with one or two changes (0.0059 and 0.0089 by band), three business
types start too far away, and every generated car wash already asks for more than the program's $500,000 maximum, so
only 0.0392 of plans get a path that is also program-compliant. We report both numbers. The engine's job is to give an
honest answer either way, and the checks (exact re-simulation, no moved facts, brute-force agreement) are what show
it does. It never says a loan will be approved: lenders make their own decisions.

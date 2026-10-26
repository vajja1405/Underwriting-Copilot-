# Walkthrough: Studio S12, the end-to-end evaluation against a baseline, and the supported cities

Written on 2026-10-26 so that both owners (Rahul Vajja and Bhavani Adula) can explain this phase in their own words.

Studio S12 runs the Founder Studio pipeline end to end on the golden founder scenarios, from a scenario's inputs to
an audited PDF package and on to the lender copilot's human checkpoint. It measures the results against a baseline,
**B0**, that was written down and committed before the first run. It also covers the S12 README items and explains
which cities Studio supports and how to add another one.

Everything evaluated here is **SYNTHETIC**: the founder plans, owners, documents, the SYNTHETIC metro and the
Northwind startup program. Probabilities are "simulated, based on stated assumptions". Nothing here measures real
founders, real lenders, real loan outcomes, real-world savings or production accuracy. Every Studio results page and
the results file carry the line: "Educational planning tool. Not financial, legal or lending advice. Lenders make
their own decisions."

**What ran and what did not.**
- Ran: `make studio-business-metrics` (`scripts/eval_studio.py`) on the 36 golden scenarios. It ran at commit
  `e68b88d` with a clean tree (`provenance.git_sha`, `git_dirty: false` in `results/studio_business_metrics.json`),
  generated at 2026-10-26T19:46:55 UTC, on a shared machine with 4 CPUs (`provenance.environment`). Running the 36
  scenarios, handoff check included, took 357.9 s (`configuration.runtime_s`). The results were committed in
  `b44b581`.
- The narrative is the template provider and the lender memo is the deterministic template ("FAKE LLM - test
  mode"). **No language model was called** in S12. Anthropic and OpenAI were never called in this project (no key);
  the only real LLM calls in the repo are small local Ollama smoke runs, listed in the README section "Language
  models: what has actually run". None of them is part of S12.
- No cloud service ran. The Hugging Face Space is not deployed, so there is no live URL.
- Not run, so **not measured**: a usability test with real people, the time a person needs to write a plan (for
  Studio or for B0), and compute cost.

**How to read the numbers.** Every number below is copied from a results file, and the file is named each time.
Unless a line says otherwise, the file is `results/studio_business_metrics.json`. Values are copied as the JSON prints
them (so `0.032` is not shown as `0.0320`). Intervals measured in this run are 95% percentile bootstrap intervals,
1,000 resamples, seed 42 (`configuration.bootstrap`), with the scenario as the resampling unit. Yes/no rates (complete
packages, the handoff checks) also carry a Wilson 95% interval (`wilson_ci95`), which stays informative when every
case agrees. Cited values (S1, S5, S6) carry the intervals reported in the file they come from.

## 1. What S12 asked for, and what exists now

The spec is "Prompt S12" in [founder_studio_build_prompts.md](founder_studio_build_prompts.md).

| Part | S12 asked for | Where it is | State |
|---|---|---|---|
| Business metrics | `scripts/eval_studio.py` runs "the 30 synthetic founder scenarios" end to end and writes time to a complete package, evidence coverage, data-coverage score by metro, path-found rate, agent handoff success and cost per package to `results/studio_business_metrics.json` | `scripts/eval_studio.py`, `src/uwcopilot/studio/business_metrics/`, `make studio-business-metrics` | Built + measured on the 36 golden scenarios; the prompt's 30 (the real-metro subset) are reported separately. Data coverage, path-found rate and handoff success are **cited** from the S1, S5 and S6 results files, not re-measured; the handoff is also re-run as a consistency check (section 3) |
| Baseline first | "Define the baseline in writing first (a plan made without SiteIQ, simulation and the ledger)" | [s12_baseline.md](s12_baseline.md) | Done. Committed in `4a19865` ("Studio S12: baseline and metric definitions, written before measuring") before the script existed (section 4.1) |
| Usability test | If a small test with real people was done, report n, protocol and raw times; otherwise "not measured" | `usability_test` block of the results file | **Not measured**: no test with real people was done |
| README | Founder Studio in the architecture, supported cities and how to add one, results table filled by script, live URL, demo GIF | `README.md` | Architecture diagram, the S12 status row, the "Supported metros" paragraph and the S12 rows of the headline results table (rendered by `make reports` from the results files) exist. **The live URL and the demo GIF do not exist**: nothing is deployed, so there is no URL, and no GIF was recorded. The README shows screenshots from a LOCAL container instead |
| Walkthrough | `docs/studio/walkthrough_s12.md` with 10 interview Q&As each for Rahul and Bhavani, including "Why these five cities?" and "How would you add a sixth?" | This file (section 9) | Done |
| Resume lines | Separate, accurate bullets for each person "based on git history of who built what"; numbers only from `results/*.json` | `scripts/resume_lines.py`, `src/uwcopilot/resume/`, `make resume-lines`, `config/contributions.yaml` | Shared bullets are built from results files only. **Per-person lines are not printed yet**: git history cannot say who built what (almost every commit is authored by the implementer, Claude Code), so the script never infers ownership from it. Per-person lines come only from `config/contributions.yaml`, which is empty by design until the owners fill it in |

Who did what, in plain words: Rahul Vajja and Bhavani Adula wrote the specs, the build prompts and the evaluation
protocol, and they review the work; Claude Code is the implementer. This walkthrough does not attribute any component
to one owner.

## 2. Files

### Evaluation package (`src/uwcopilot/studio/business_metrics`)

| File | What it does | Why it is built this way |
|---|---|---|
| `__init__.py` | Re-exports the baseline and the coverage rules only. | `scenario_run` pulls in the PDF renderer and the agent graph, so the script imports it directly and importing the package stays light. |
| `baseline.py` | Builds B0 for one plan: group 1 the founder's typed figures (`founder_claims.FIELD_SPECS`, the owner draw only when not zero), group 2 the untyped one-forecast inputs at their most likely value (shock probabilities, monthly noise and shock sizes left out), group 3 years 1-3 revenue, NOI, debt service and DSCR from `feasibility.model.single_point_run`. Applies B0's source rule (`backing_documents`). | B0 is computed by code from the same inputs as the Studio package, so it is a construction anyone can re-run, not an assumption about what a plain plan looks like. It reuses the ledger's own tolerance (`founder_claims.same_value`). |
| `coverage.py` | Rule S (`has_checkable_source`), rule E (`has_external_evidence`), `package_coverage` over a package's ledger, `baseline_coverage` over B0, `founder_figure_count` for M4, and `figure_breakdown` (status / provenance kind / verdict counts). | Each rule is a few lines that match the written definition, and a test checks each case of it. The breakdown shows what the rule S share is made of. |
| `intervals.py` | Scenario-bootstrap intervals: `pooled_share` (k/n with its interval), `paired_difference` (Studio minus B0 on the same resampled scenarios), `spread` (min, median, max) and `seconds_summary` (median and 95th percentile with intervals). | The figures of one package are not independent, so whole scenarios are resampled. Uses the repository's constants (1,000 resamples, seed 42). |
| `cited.py` | `cite_handoff` (S6), `cite_coverage` (S1), `cite_paths` (S5): copy the needed keys from each results file with the file name, section and the file's own provenance (script, time, commit, dirty flag). A missing file gives "not measured here" and names the make target that writes it. | S12 does not recompute what S1, S5 and S6 measured; it says where each number came from and when. |
| `scenario_run.py` | One golden scenario end to end, timed by stage: `scenario` (`build_golden_scenario`), `build_package` (`project_from_golden` + `build_package`), `render_pdf`, `audit` (`audit_pdf` + `pages_missing_labels`), then `handoff` timed apart. Also builds B0 for the same plan and documents, the evidence counts and the narrative check behind the LLM cost. | It calls the existing S2-S6 code unchanged, so S12 measures what the earlier phases built, not a copy of it. |
| `report.py` | One function per results block that turns the per-scenario rows and the cited blocks into the results file's blocks (`time_block` M1, `evidence_coverage` M2-M5, `data_coverage` M6, `path_found` M7, `agent_handoff` M8, `cost_per_package` M9, `usability_test` M10), and `headline()`, which only copies values out of those blocks. | The headline computes nothing new, so it cannot disagree with the detail blocks. The fixed label strings ("not a user time", "0 by construction", "not measured") live next to the numbers they qualify. |

### Script, results, docs, Make targets

| File | What it does |
|---|---|
| `docs/studio/s12_baseline.md` | The baseline B0 and every metric's definition, unit, population and interval method (M1-M10), the statistics rules and the threats to validity. Committed before the script and before any S12 number. |
| `scripts/eval_studio.py` | Runs the golden scenarios (`--limit`, `--n-paths`, `--skip-handoff`, `--results-dir`, `--output`), cites the S1/S5/S6 files and writes the results file with a provenance block (commit, dirty flag, prompt hashes, the SHA-256 of the definitions file and of every cited results file, the SHA-256 of the OFAC SDN file, CPU count and load averages). Without `data/raw/ofac/sdn.csv` it stops with exit code 1 unless `--skip-handoff` is passed; an empty selection exits with code 2. |
| `results/studio_business_metrics.json` | The measured run (section 5): `configuration`, `headline`, one block per metric, `per_scenario` rows and `provenance`. |
| `Makefile` | `studio-business-metrics` (this evaluation; `ARGS=--skip-handoff` runs without the sanctions lists) and `resume-lines` / `resume-lines-test`. |
| `scripts/resume_lines.py`, `src/uwcopilot/resume/`, `config/contributions.yaml` | Resume bullets built only from `results/*.json`; every bullet or clause left out is printed with its reason on stderr. The Studio S12 bullet reads `results/studio_business_metrics.json` (`resume/studio.py`, `business_bullet`). Per-person lines only from `config/contributions.yaml` (empty). |
| `README.md` | S12 status row, "Supported metros" paragraph, the S12 rows of the headline results table and the reproduce steps. |
| `docs/studio/walkthrough_s12.md` | This file. |

### Tests (`tests/studio`, all offline)

| File | What it checks |
|---|---|
| `test_business_metrics_baseline.py` | B0 on a golden SYNTHETIC-metro plan: founder figures are the typed fields and cite nothing without documents; an agreeing document gives a citation within the ledger tolerance; a disagreeing document removes it; a document about another field does not count; shock and noise inputs are left out; inputs are at their mode; the note rate is an input when not typed; results are years 1-3 of the one-forecast run (two years for a two-year setup); group order. |
| `test_business_metrics_coverage.py` | Rules S and E follow the written definition case by case; text claims are not figures and UNKNOWN claims are; B0 counts document citations under both rules; founder figures are backed equally in both plans on a golden package; a package has more sourced figures than its B0. |
| `test_business_metrics_intervals.py` | The pooled share is the sum of hits over the sum of figures; the interval is seeded and resamples whole scenarios; one share everywhere gives a point interval; impossible counts are refused; the paired difference uses the same scenarios for both plans. |
| `test_business_metrics_cited.py` | Cited blocks copy values and the source provenance from hand-made SYNTHETIC results files; a missing file is "not measured" and names its make target. |
| `test_business_metrics_report.py` | The headline copies the blocks; the real-metro subset and each metro are reported; the handoff check says when it was skipped; the first handoff is reported apart; the LLM cost is not reported as 0 when a narrative was not the template. |
| `test_business_metrics_script.py` | One golden scenario end to end reaches the checkpoint with a complete package (offline screening handler); the script on one scenario with `--skip-handoff` writes provenance and its "not measured" items; it refuses an empty selection and a missing sanctions list. |
| `business_metrics_helpers.py` | Hand-made SYNTHETIC results files for the cited-results tests. |

## 3. The baseline B0 and each metric

### 3.1 B0: a plan made without SiteIQ, the simulation and the ledger

B0 is defined in [s12_baseline.md](s12_baseline.md), section 2. It is a **construction**, not a sample of real plans:
no real founder's plan was collected. It answers one question: on the same SYNTHETIC inputs, what would a plain plan
print, and which of its figures could cite a source, if the three Studio parts were missing?

B0 prints three groups of figures, built by `business_metrics/baseline.py`:
1. **Founder figures**: every plan and loan figure the founder typed (owner equity, loan amount, term, note rate,
   rate spread, build-out cost, price, rent, paid hours, wage, capacity, days open, owner draw when not zero).
2. **Inputs the founder did not type**, at their most likely value. Inputs a one-forecast plan never uses (shock
   probabilities, monthly noise, competitor-entry and one-off-shock sizes) are left out.
3. **One-forecast results** for years 1-3: revenue, net operating income, debt service and DSCR, from one
   deterministic path with every input at its most likely value and no shocks ("the plan a spreadsheet would show").
   No random draws, no percentiles, no probabilities.

**B0's source rule.** A B0 figure carries a source only when it is a founder figure, at least one document is about
that field, and every such document prints the same value (dollar figures within $1.00, other figures exactly, the
ledger's own tolerance). Every other B0 figure has no source, because B0 has no place to record where a default came
from or how a result was calculated. This follows from the definition; it is not an observation about real plans.

B0 uses the **same input values** as the Studio run on purpose. Only what is printed and what can be cited differ.

### 3.2 The metrics

| # | Metric | How S12 gets it | B0 |
|---|---|---|---|
| M1 | Time to a complete package | Measured by this run | Not measured |
| M2 | Evidence coverage, rule S | Measured by this run | Computed by B0's rule |
| M3 | External-evidence coverage, rule E | Measured by this run | Computed by B0's rule |
| M4 | Founder figures backed by a document | Measured by this run | Computed by B0's rule |
| M5 | Printed numbers that trace to a ledger claim | **Cited** from S6 | Not applicable (no ledger) |
| M6 | Data-coverage score by metro | **Cited** from S1 (plus what this run observed) | Not applicable (no location data) |
| M7 | Path-found rate | **Cited** from S5 (plus this run's golden statuses) | Not applicable (no search) |
| M8 | Agent handoff success | **Cited** from S6; this run repeats a consistency check | Not applicable (needs the ledger) |
| M9 | Cost per package | LLM: 0 by construction, checked per package; compute: not measured | Not measured |
| M10 | Usability test with real people | Not measured | Not measured |

**M1. Time to a complete package (machine wall clock, not a user time).** Seconds on the machine that ran the
script, from a scenario's inputs to an audited PDF: (a) the golden scenario (SiteIQ when the metro has tracts on that
machine, the S3 simulation with 1,000 paths over 36 months, the S5 quick path search), (b) building the package, (c)
rendering the PDF, (d) the PDF round-trip audit and the page-label check. Total = a + b + c + d. The handoff is timed
apart. A package is **complete** when the audit finds no untraced number in the package text or in the PDF's own text
and no page is missing its labels. The label in the results file says what this is: "machine-dependent wall-clock
measurement on the machine that ran the script (shared; ...); not a user time: it excludes the founder's time to
gather inputs and documents, and nobody's time was observed". B0 time is "not measured: no one was timed writing a
plan".

**M2. Evidence coverage, rule S.** A printed figure is every ledger claim with a numeric display; every claim is
printed in the package's evidence appendix, and an UNKNOWN claim prints "not available" (a figure without a source).
A Studio figure has a checkable source when the package states an origin other than the founder's word alone or an
unsourced default:
- VERIFIED, MODEL_ESTIMATE or CONFLICTING with provenance kind `document`, `public_data`, `policy`, `calculation` or
  `model` (a document page, a public-data row, a program or target section, a named calculation with its inputs, or a
  model run with name, version, seed and input hash): yes;
- ASSUMPTION with a named source: yes; ASSUMPTION marked "unsourced": no;
- USER_STATED (the founder's figure, no document), a CONFLICTING founder figure (kind `user_input`), UNKNOWN: no.

The share is pooled: sourced figures over all figures, summed over scenarios.

**M3. External-evidence coverage, rule E (stricter).** Only VERIFIED or CONFLICTING claims of kind `document`,
`public_data` or `policy` count. Model estimates, calculations, assumptions and founder statements do not. In the
SYNTHETIC metro the public-data rows are SYNTHETIC city rows. `policy` covers the SYNTHETIC startup program's
sections and Studio's own editable targets (design choices, sourced as such in the ledger); that is a stated rule, not
a judgement of their quality. For B0, rules S and E are the same (its only sources are documents).

**M4. Founder figures backed by a document.** The same population for both plans: the founder's typed figures.
Studio counts a founder claim with status VERIFIED; B0 uses its source rule. The baseline file says in advance that
the two are **equal by construction**: the same documents and the same tolerance decide both. It is reported to show
that the ledger does not make a founder's figure better evidenced; it records whether a document backs it. Studio
also counts **conflicts surfaced** (founder claims a document contradicts); B0 has no comparison step, so it surfaces
none "by definition (not a measurement)".

**M5. Printed numbers that trace to a ledger claim (cited, S6).** Copied from `results/studio_handoff_metrics.json`,
section `golden_founder_scenarios`: numbers read back, untraced numbers in the package text and in the PDF text, and
the share of packages with an empty audit. Not recomputed.

**M6. Data-coverage score by metro (cited, S1).** Copied from `results/studio_metro_coverage.json`: per metro and
business type, `coverage_share` (the weighted share of a business type's required factors backed by data, times the
share of tracts covered), the metro's minimum, its mode and the threshold. Not recomputed. This run also records, per
metro, which mode each scenario used and whether SiteIQ scored a site; that depends on the data on the machine that
ran the script.

**M7. Path-found rate (cited, S5).** Copied from `results/studio_path_metrics.json`: `path_found_rate_full`, the
program-compliant variant, the counts and the per-metro rates with their intervals. Not recomputed. This run also
lists the path-search status of each golden scenario, as counts only: a description of these 36 seeded plans, not an
estimate of the rate.

**M8. Agent handoff success (cited, S6; consistency check repeated).** The headline is copied from
`results/studio_handoff_metrics.json`: reached the human checkpoint, decisions recorded without a human (must be 0),
memo check passed, unsupported numbers in memos, for the golden packages and for all cases. This is the one cited
metric that S12 also **re-runs**: `handoff` runs on every package of this run as a consistency check, with the full
OFAC SDN and OpenSanctions lists, and its k/n is reported next to the cited value.

**M9. Cost per package.** LLM cost is USD 0 **by construction**: the narrative is the template provider and the
lender memo is the deterministic template; no language model is called. The script checks every package (narrative
provider "template", no model, no fallback, no LLM attempt); if any check failed, the cost would be "not reported as
0". Compute cost is "not measured: no cloud resource was used and the machine's cost is not metered".

**M10. Usability test with real people.** "Not measured: no usability test with real people was done, so there is
no n, protocol or raw time." If one is done later, report n, the protocol and the raw times, not only a summary.

## 4. Design choices

### 4.1 The baseline was written first

`git log --format='%h %s' -- docs/studio/s12_baseline.md` prints one commit:

```
4a19865 Studio S12: baseline and metric definitions, written before measuring
```

It was committed at 2026-10-26 18:58:05 UTC. The script was added later in `1958f6e` ("Studio S12:
scripts/eval_studio.py and make studio-business-metrics", 19:07:39 UTC), and the measured results in `b44b581`
(19:47:06 UTC; the run was at `e68b88d`). `4a19865` is an ancestor of `1958f6e`. So the definitions could not be
tuned to the results. The results file records the SHA-256 of the definitions file under `provenance.eval_sets`, so
a later edit to the definitions would show. The baseline file's own rule: if a definition has to change after the
first run, the change is a new commit to that file that says what changed and why.

### 4.2 B0 is built in code from the same inputs

B0 could have been "a typical plan" described in words, but that would be a guess. Building it in code, per scenario,
from the same founder inputs and the same documents means the comparison is reproducible and the only difference is
what is printed and what can be cited. The price: the gap shows what the ledger records, not that real founders'
plans lack sources. The results file's `reading` field says this next to the numbers.

### 4.3 Two coverage rules, plus one comparison that must come out equal

Rule S gives Studio credit for model estimates and calculations because the ledger records their run or formula and
inputs. That credit follows from the definitions, so rule E removes it and counts only external evidence. M4 compares
one population with one rule and is expected to be equal; it checks that the comparison is fair rather than showing
a win.

### 4.4 Scenario bootstrap, 1,000 resamples, seed 42

The figures of one package are not independent (one plan, one simulation, one set of documents). Resampling single
figures would give intervals that are too narrow. So each resample draws whole scenarios with replacement and
recomputes the pooled share (percentile method, 1,000 resamples, seed 42, the repository's constants in
`agent/evaluation.py`). The Studio minus B0 difference is a **paired** bootstrap: both shares are recomputed on the
same resampled scenarios. Times use the existing percentile bootstrap of the median and the 95th percentile. When
every scenario has the same outcome, the bootstrap interval collapses to one point, so all-pass rates also carry a
Wilson 95% interval. No p-values, no claims of "significance".

### 4.5 The first scenario and the first handoff are reported apart

The first scenario of a process also loads configs and fills caches once, so its package time is reported on its own
(`first_scenario_package_total_s`). It is still included in the median and 95th percentile. The first handoff also
loads the screening lists (`screening_handler` loads them on the first call), so the handoff time is reported with
and without the first scenario (`handoff_s`, `handoff_s_without_first_scenario`).

### 4.6 The real-metro subset of 30 is reported separately

The S12 prompt names 30 scenarios: 6 business types x 5 real metros. Rules S and E and the package time are reported
for all 36 and again for those 30 (`real_metro_scenarios`, `package_total_s_real_metro_scenarios`); rules S and E are
also reported per metro. The other items measured here (complete packages, founder figures backed by a document,
conflicts and the handoff check) are reported for all 36 only. A real-metro scenario is still a SYNTHETIC plan; only
the metro's public data (when the machine has it) is real.

### 4.7 Why 36 and not 30 scenarios

The golden set of S4 and S6 is 36: 6 business types x (5 real metros + the SYNTHETIC metro), from
`studio/narrative/golden.py` and `studio/package/golden.py`. Reusing it unchanged has three benefits. First, S12
runs the same 36 golden scenarios S6 measured (the same `scenario_id` values in both files), so the cited S6 numbers
and this run's handoff check describe the same scenarios. The packages are rebuilt, not reused: the `package_id` of
each scenario differs between the S6 file (`cf60df5`) and this run (`e68b88d`); the id is a SHA-256 of the project,
its ledger and its narrative (`package/build.py`), so some of that content changed between the two commits. Second, the SYNTHETIC metro is the only metro where SiteIQ scored a site in this run (section 5.4), so
without it no scenario would exercise the full path from site score to package. Third, the prompt's 30 are still
reported on their own (4.6), so nothing is lost.

### 4.8 Cite, do not re-measure

Re-measuring coverage, the path search or the handoff in a second script could give two numbers for one metric.
`cited.py` copies the values, names the file and key, and carries the file's provenance (`source_provenance`: script,
time, commit, dirty flag). That is how a reader can see that the S6 handoff file was written with a clean tree, while
the S1 coverage and S5 path files record `git_dirty: true` (sections 5.3 to 5.5). The S12 provenance also pins the
SHA-256 of each cited file (`data_snapshots.cited_results_sha256`), so a later change to a cited file would show. The
handoff consistency check is the one repeat, because it is cheap and runs on this run's own packages.

### 4.9 "0 by construction" is still checked

The LLM cost of 0 follows from the design (template narrative and memo), but the script checks it per package and
refuses to report 0 if any narrative was not the template. A design claim becomes a recorded fact.

## 5. Results

All numbers in this section are copied from `results/studio_business_metrics.json` (commit `e68b88d`, `git_dirty:
false`), unless a line names another file. Configuration: golden seed 4404, 36 scenarios (30 real-metro), 1,000
paths, 36 months, handoff check with "full OFAC SDN + OpenSanctions lists", narrative and memo "template narrative and
deterministic template memo (FAKE LLM - test mode)", versions `founder-package-v1`, `studio-package-1.0`,
`studio-handoff-1.0` (`configuration`).

### 5.1 Time to a complete package (M1, `time_to_complete_package`)

Machine-dependent wall clock on a shared machine with 4 CPUs (load average at start [0.96, 0.91, 0.66], at end
[1.0, 0.98, 0.78], `provenance.environment`). **Not a user time.**

| Measure | All 36 scenarios | 30 real-metro scenarios |
|---|---|---|
| Median seconds to an audited PDF | 7.199 [6.767, 7.494] | 6.862 [6.643, 7.255] |
| 95th percentile seconds | 8.964 [7.81, 9.248] | 7.789 [7.636, 7.961] |
| Min / max seconds | 6.141 / 9.248 | 6.141 / 7.961 |
| n | 36 | 30 |

| Stage (`stage_s`) | Min | Median | Max |
|---|---|---|---|
| scenario (SiteIQ where tracts exist, simulation, quick path search) | 0.22 | 0.465 | 1.842 |
| build_package | 0.467 | 0.587 | 1.255 |
| render_pdf | 3.018 | 3.603 | 4.886 |
| audit (PDF read back + page labels) | 1.951 | 2.403 | 3.022 |
| package_total | 6.141 | 7.199 | 9.248 |

- First scenario's package time: 8.882 s (`first_scenario_package_total_s`; it also loads configs and caches).
- Complete packages: 36/36, bootstrap [1.0, 1.0], Wilson 95% [0.9036, 1.0] (`complete_packages`).
- Handoff, timed apart: median 0.506 s [0.49, 0.553], 95th percentile 0.798 s [0.635, 77.765], n=36. The first
  handoff took 77.765 s, which includes the one-time load of the screening lists. Without it: median 0.504 s [0.488, 0.552], 95th
  percentile 0.712 s [0.631, 0.995], n=35.
- B0: "not measured: no one was timed writing a plan".

### 5.2 Evidence coverage (M2-M4, `evidence_coverage`; headline in `headline`)

| Measure | Studio | B0 | Studio minus B0 (paired) |
|---|---|---|---|
| Rule S, all 36 scenarios | 0.8482 [0.8388, 0.8587] (12,222/14,410) | 0.0251 [0.0182, 0.032] (33/1,316) | 0.8231 [0.8118, 0.8352] |
| Rule S, 30 real-metro scenarios | 0.8362 [0.8304, 0.842] (9,947/11,896) | 0.0246 [0.0173, 0.0319] (27/1,097) | 0.8116 [0.8029, 0.8204] |
| Rule E, all 36 scenarios | 0.057 [0.0545, 0.0597] (821/14,410) | 0.0251 [0.0182, 0.032] (33/1,316) | 0.0319 [0.0259, 0.038] |
| Rule E, 30 real-metro scenarios | 0.0539 [0.0527, 0.0551] (641/11,896) | 0.0246 [0.0173, 0.0319] (27/1,097) | 0.0293 [0.0231, 0.0357] |
| Founder figures backed by a document (M4) | 0.1315 [0.0958, 0.1693] (33/251) | 0.1315 [0.0958, 0.1693] (33/251) | equal in every scenario (`equal_in_every_scenario: true`) |
| Conflicts surfaced (founder figures a document contradicts) | 12 | "none by definition" (not a measurement) | |

Per scenario (`per_scenario_share`): the Studio rule S share ranges from 0.8103 to 0.9161 (median 0.8397); B0's from
0.0 to 0.0571 (median 0.027). Rule E per scenario: Studio 0.0474 to 0.0769 (median 0.0544).

By metro (rule S, Studio vs B0, `rule_s_checkable_source.by_metro`):

| Metro | Studio | B0 | Difference |
|---|---|---|---|
| synthetic_metro (SYNTHETIC) | 0.9049 [0.8987, 0.9106] | 0.0274 [0.0092, 0.0457] | 0.8775 [0.8649, 0.8909] |
| chicago_il_in | 0.8307 [0.8208, 0.8422] | 0.0273 [0.0091, 0.0457] | 0.8035 [0.7889, 0.8155] |
| dallas_fort_worth_tx | 0.8446 [0.8356, 0.8547] | 0.0227 [0.009, 0.0367] | 0.8219 [0.8021, 0.8391] |
| houston_tx | 0.8447 [0.8355, 0.853] | 0.0275 [0.0091, 0.0461] | 0.8171 [0.7961, 0.8373] |
| los_angeles_ca | 0.8249 [0.8134, 0.8417] | 0.0229 [0.0091, 0.0369] | 0.802 [0.7836, 0.8199] |
| new_york_ny_nj_pa | 0.8363 [0.8294, 0.8444] | 0.0226 [0.009, 0.037] | 0.8137 [0.7949, 0.8345] |

Each metro has only 6 scenarios, so each interval rests on a bootstrap over 6 units; read them as a description, not
a ranking of cities.

What the Studio figures are made of (`studio_figures_by_status_kind_and_verdict`, summed over the 36 packages):

| Status / provenance kind / rule S verdict | Figures |
|---|---|
| MODEL_ESTIMATE / model / sourced | 10,745 |
| USER_STATED / user_input / not sourced | 1,332 |
| ASSUMPTION / assumption / not sourced | 844 |
| VERIFIED / policy / sourced | 724 |
| ASSUMPTION / assumption / sourced | 588 |
| VERIFIED / public_data / sourced | 52 |
| CONFLICTING / calculation / sourced | 36 |
| VERIFIED / document / sourced | 33 |
| VERIFIED / calculation / sourced | 32 |
| CONFLICTING / document / sourced | 12 |
| CONFLICTING / user_input / not sourced | 12 |

Figures per plan (`figures_per_plan`): Studio min 371, median 401, max 430; B0 min 35, median 36, max 38. B0's 1,316
figures split into 251 founder figures, 633 inputs and 432 results.

**How to read this** (the results file's own `reading`): "Most of the Studio - B0 gap follows from the definitions:
model estimates and calculations count for Studio because the ledger records their run or formula and inputs, and B0
prints no such record. Rule E removes that credit; the founder-figure comparison uses one population for both and is
equal by construction. B0 is a construction from the same SYNTHETIC inputs, not a sample of real founders' plans."

### 5.3 Printed numbers that trace to a ledger claim (M5, cited from S6)

From `results/studio_handoff_metrics.json`, section `golden_founder_scenarios` (written by
`scripts/eval_studio_handoff.py` at `cf60df5`, `git_dirty: false`, 2026-10-20T05:52:51 UTC): 30,681 numbers read
back; 0 untraced in the package text and 0 in the PDF text; the audit was empty in 36/36 packages for both, Wilson 95%
[0.9036, 1.0]. "0 untraced" is partly by construction (every printed number is a claim by design), so the audit is a
guard against regressions, not a discovery. B0: not applicable.

### 5.4 Data coverage by metro (M6, cited from S1, plus what this run observed)

Cited from `results/studio_metro_coverage.json` (written by `scripts/studio_metro_coverage.py` at `a634325`,
`git_dirty: true`, 2026-10-13T15:50:50 UTC). Threshold 0.8 for every metro.

| Metro | Mode (S1) | Lowest coverage share (S1) | SiteIQ scored a site in this S12 run |
|---|---|---|---|
| new_york_ny_nj_pa | limited | 0.6779 | 0 of 6 |
| los_angeles_ca | limited | 0.6812 | 0 of 6 |
| chicago_il_in | limited | 0.6812 | 0 of 6 |
| dallas_fort_worth_tx | limited | 0.6826 | 0 of 6 |
| houston_tx | limited | 0.6813 | 0 of 6 |
| synthetic_metro (SYNTHETIC) | synthetic | 0.896 | 6 of 6 |

The S1 reason for each real metro is "data coverage 0.68 is below the threshold 0.80". In this S12 run every
real-metro scenario ran in limited mode with no SiteIQ result (`data_coverage_by_metro.this_run`). The golden code
scores a site only when the metro has tracts on the machine that runs it (`narrative/golden.py`, `site_of`, which also
returns no site when the drawn point falls outside the metro). The results file records the outcome, not the cause;
the likely cause is that the machine that ran S12 had no real-metro tract snapshot (`this_run_note`: "depends on which
metro snapshots exist on the machine that ran the script"). The cited S1 coverage was computed on snapshots dated
2026-10-13. B0: not applicable.

### 5.5 Path-found rate (M7, cited from S5)

Cited from `results/studio_path_metrics.json` (written by `scripts/eval_studio_paths.py` at `6e16420`, `git_dirty:
true`, 2026-10-13T19:47:50 UTC), on 540 seeded SYNTHETIC plans (4 already met the targets, 55 paths found, 481 no
path within bounds):

| Measure | Value |
|---|---|
| Path found, among the 536 plans that needed a change | 0.1026 [0.0784, 0.1306] |
| Path found that also meets the SYNTHETIC program's rules | 0.0392 [0.0243, 0.0578] |
| Real-world accuracy | "not measured: no real founder outcomes exist; every plan is SYNTHETIC" |

The S5 file's scope says the rate "depends on how the plans are generated and on the editable targets and lever
bounds; it describes the search on these plans, not any real founder's chances". In this run the 36 golden
scenarios had these statuses: no path within bounds 33, already meets target 2, paths found 1 (a description of these
seeded plans, not an estimate). B0: not applicable.

### 5.6 Agent handoff success (M8, cited from S6, with this run's check)

| Measure | Cited (S6, `golden_founder_scenarios`) | This run's consistency check |
|---|---|---|
| Cases | 36 | 36 |
| Reached the human checkpoint | 36/36, Wilson 95% [0.9036, 1.0] | 36/36, Wilson 95% [0.9036, 1.0] |
| Decisions recorded without a human | 0 (rate Wilson 95% [0.0, 0.0964]) | 0 |
| Memo check passed | 36/36, Wilson 95% [0.9036, 1.0] | 36/36, Wilson 95% [0.9036, 1.0] |
| Unsupported numbers in memos | 0 | 0 |

Across all 71 cases of the S6 file (36 golden packages plus 35 SYNTHETIC startup applications): 71/71 reached the
human checkpoint (Wilson 95% [0.9487, 1.0]), 0 decisions without a human, 0 unsupported numbers in memos. The S6
settings: "full OFAC SDN + OpenSanctions lists (uwcopilot.kyc.screening default matcher)", 1,000 paths, memo writer
"deterministic template memo (FAKE LLM - test mode)". B0: not applicable.

### 5.7 Cost per package (M9) and the usability test (M10)

- LLM cost: 0.0 USD per package. Basis: "0 by construction ... Checked per package". Narrative checks: 36 packages,
  36 with no LLM called, providers `template` 36 (`cost_per_package.narrative_checks`).
- Compute cost: "not measured: no cloud resource was used and the machine's cost is not metered".
- B0 cost: not measured.
- Usability test: "not measured: no usability test with real people was done, so there is no n, protocol or raw
  time". `n`, `protocol` and `raw_times_s` are null.

## 6. Limitations

- **Everything is SYNTHETIC.** The plans, owners, documents and the startup program are generated by the same project
  that wrote the Studio code. The document variants follow the scenario index (none, matching statements,
  contradicting statements), so the share of document-backed figures (33/251) and the 12 conflicts are properties of
  the generator, not of founders.
- **B0 is a construction.** The rule S gap (0.8231 [0.8118, 0.8352]) mostly follows from the definitions: 10,745
  of the Studio figures are model estimates that rule S credits and B0 cannot print. The stricter rule E gap is
  0.0319 [0.0259, 0.038]. Neither shows that real founders' plans lack sources.
- **Time is not user time.** It is wall clock on one shared 4-CPU machine. Nobody was timed, B0 was not timed, and
  the founder's time to gather inputs and documents is not included. So there is no time-saving claim. With 36
  scenarios, the upper bound of the 95th-percentile interval (9.248) equals the slowest scenario.
- **Real metros ran without SiteIQ.** Every real-metro scenario in this run ran in limited mode with no site score
  (section 5.4). The real-metro times and coverage shares describe that state, not a run with real tract data.
- **Two cited files were written from a dirty tree.** The S1 coverage file and the S5 path file record `git_dirty:
  true`; the S12 run itself and the cited S6 file are clean. The S12 provenance pins the SHA-256 of each cited file.
- **Small n.** 36 scenarios (6 per metro). Per-metro intervals rest on 6 scenarios each. All-pass rates have bootstrap intervals of
  [1.0, 1.0]; the Wilson intervals show what 36 cases can and cannot rule out.
- **No language model.** Narrative and memo are templates. What an LLM narrative would do to time, cost or traceability
  is not measured here.
- **Not measured:** a usability test with real people, compute cost, B0 time and cost, real-world accuracy.
- **Not built from the prompt:** the live URL and the demo GIF (nothing is deployed) and per-person resume lines (they
  wait for `config/contributions.yaml`).
- **No regulatory claim.** S12 cites no regulation. Where a later document cites one, it must give the primary
  source and its date and say that applicability depends on the institution and its risk profile. This project is a
  demonstration, not a compliance program. Studio is an educational planning tool: not financial, legal or lending
  advice, and lenders make their own decisions.

## 7. How to reproduce

Needs Python 3.11 and `uv` (`make setup`). Run the steps below in this order; the cited files of step 2 were
written earlier, at the commits named in section 5.

```bash
# 1. The sanctions lists for the handoff check (data/raw/, gitignored). Either the two lists only:
uv run python scripts/download_data.py --datasets ofac_sdn opensanctions
#    or `make data` (every public dataset; Lending Club needs a Kaggle key). OpenSanctions is CC BY-NC 4.0.

# 2. The cited results (already committed in results/; re-run only to refresh them):
make studio-coverage            # S1 -> results/studio_metro_coverage.json (offline; reads the snapshots on disk)
make studio-paths               # S5 -> results/studio_path_metrics.json
make studio-handoff             # S6 -> results/studio_handoff_metrics.json (needs step 1)

# 3. S12 itself:
make studio-business-metrics    # -> results/studio_business_metrics.json (357.9 s in the committed run)

# 4. README tables from the results files:
make reports
```

- **Quick development run** without the sanctions lists, written outside `results/` so the committed file is not
  overwritten: `make studio-business-metrics ARGS="--limit 6 --skip-handoff --output <scratch>/s12.json"`. The
  handoff check is then reported as "not run in this execution (--skip-handoff)".
- **Real-metro SiteIQ.** To score sites in the real metros, build their snapshots first: `make studio-data
  METRO=all` (needs `CENSUS_API_KEY` in `.env` for ACS and County Business Patterns; every key variable and what it is for
  is listed in `src/uwcopilot/studio/data/keys.py`). Without snapshots, real-metro scenarios run in limited mode with no site score,
  as in the committed run. `data/` is gitignored; downloads are logged with SHA-256 checksums.
- **Tests (offline):** `make studio-test` runs every Studio test; `uv run pytest -q tests/studio/test_business_metrics_*.py`
  runs only the S12 ones. They use fixtures, an offline screening handler and hand-made cited results; no network,
  sanctions list or LLM.
- **Resume lines:** `make resume-lines` (prints to stdout, omissions with reasons to stderr); `make resume-lines-test`.

## 8. Supported cities and how to add one

### 8.1 The five metros

The five largest US metropolitan statistical areas by population, plus a SYNTHETIC metro that works offline. Rank,
CBSA code and population are copied from `results/studio_metro_ranking.json` (`top`); tracts, lowest coverage share
and mode from `results/studio_metro_coverage.json`.

| Config id | Official CBSA title (July 2023 delineation) | CBSA | Rank | Population (POPESTIMATE2025) | Tracts | Lowest coverage | Mode |
|---|---|---|---|---|---|---|---|
| `new_york_ny_nj_pa` | New York-Newark-Jersey City, NY-NJ | 35620 | 1 | 20,112,448 | 4,928 | 0.6779 | limited |
| `los_angeles_ca` | Los Angeles-Long Beach-Anaheim, CA | 31080 | 2 | 12,844,441 | 3,112 | 0.6812 | limited |
| `chicago_il_in` | Chicago-Naperville-Elgin, IL-IN | 16980 | 3 | 9,434,123 | 2,292 | 0.6812 | limited |
| `dallas_fort_worth_tx` | Dallas-Fort Worth-Arlington, TX | 19100 | 4 | 8,477,157 | 1,704 | 0.6826 | limited |
| `houston_tx` | Houston-Pasadena-The Woodlands, TX | 26420 | 5 | 7,904,627 | 1,615 | 0.6813 | limited |
| `synthetic_metro` | Synthetic Metro (SYNTHETIC) | none | none | none | 300 | 0.896 | synthetic |

Each has a config in `config/studio/metros/<id>.yaml`. The name, CBSA code, states and counties of the real metros
were written by `scripts/studio_fetch_delineation.py` from the Census delineation file, never by hand. The New York
metro's config id keeps "_pa" so links and results stay stable, but the July 2023 delineation lists CBSA 35620 with
New York and New Jersey counties only, so its states are NY and NJ ([metros.md](metros.md), section 3).

### 8.2 Why these five

- **The rule** comes from the Studio plan ("Decisions already made" in the build prompts): v1 covers the five largest
  US metropolitan statistical areas by population in the latest Census Bureau estimates; more come by config, the
  next ones from the same ranking. It is a design choice, not a fact about which cities are better markets.
- **It was checked, not assumed.** `scripts/studio_fetch_delineation.py` (`make studio-metros`) ranked 387
  metropolitan statistical areas by the column POPESTIMATE2025 of the Census Bureau Population Estimates Program
  file `cbsa-est2025-alldata.csv` (retrieved 2026-10-12 19:22 UTC, SHA-256 recorded), counting CBSA total rows with
  LSAD "Metropolitan Statistical Area" only, and wrote `configured_equals_top_five: true` to
  `results/studio_metro_ranking.json`. That file's provenance records `git_dirty: true` (commit `700a5b0`).
- **What it is not.** The plan's reasons for these cities (density, tourism, car dependence, zoning, open-data
  portals) are still marked UNVERIFIED in [metros.md](metros.md), section 4. We do not state facts about a city from
  memory.
- **Two rule details** (proposals in metros.md for the owners to confirm): use metropolitan statistical areas, not
  combined statistical areas; and freeze the vintage for v1, re-checking at each Census release without dropping a
  metro that is already built.

### 8.3 Limited mode

`metro_mode()` in `src/uwcopilot/studio/metros/models.py` decides a metro's mode, in this order:
1. a SYNTHETIC metro is `synthetic`;
2. no data snapshot: `limited` ("no data snapshot");
3. county list not verified against the Census file: `limited`;
4. coverage could not be computed: `limited`;
5. coverage below the config's `coverage_threshold` (0.8, a design choice): `limited`;
6. otherwise `full`.

A real metro's coverage is the **minimum over its business types**, so one weak business type keeps the whole metro
in limited mode. Today all five are limited because short-term rental is at 0.6779 to 0.6826 and motel at 0.745 to
0.7722, while car wash, laundromat, restaurant/cafe and retail storefront are at 0.8675 to 0.9415 and in full mode on
their own (`by_business_type` in `results/studio_metro_coverage.json`). Limited mode is shown, not hidden: results
carry the mode and its reason, factors without data are UNKNOWN or ASSUMPTION and never silently filled, and scores
and suggestions still run where the data allows. A per-business-type mode is an open design question (S1
walkthrough, section 5).

### 8.4 How to add a sixth metro

The next metro is rank 6 on the same file and vintage: CBSA 12060, Atlanta-Sandy Springs-Roswell, GA, population
6,482,182 (`results/studio_metro_ranking.json`, `top`). Ranks 7 and 8 are close behind (Washington-Arlington-Alexandria,
DC-VA-MD-WV, 6,465,724; Miami-Fort Lauderdale-West Palm Beach, FL, 6,391,072), so a later vintage could change the
order; that is why [metros.md](metros.md) proposes freezing the vintage for v1 (a proposal for the owners to confirm).
Choosing to add it is an owner decision. The steps:

1. **Config.** Add `config/studio/metros/<id>.yaml`, copying the shape of an existing real metro: `id`,
   `data_label: PUBLIC`, `verified: false`, `cbsa_title_prefix` (the start of the official title), no counties,
   `trade_area.ring_multiplier` with `status: DESIGN_CHOICE` and a note, `enhancements`, `snapshot_date: null`,
   `coverage_threshold: 0.8`. Do not type a CBSA code or county FIPS code by hand. Enable a `traffic_state` adapter
   only for a state that has one: `studio/data/traffic_state.py` lists NY, NJ, PA, CA, IL, IN and TX, and each still
   raises NotAvailable until a documented dataset is recorded (the national FHWA HPMS layer is used instead). City
   open-data datasets exist only for `chicago` and `nyc` (`studio/data/city_open_data.py`).
2. **Fill it from the Census file.** `make studio-metros` finds the metro by its title prefix in the July 2023
   delineation file, writes the official title, CBSA code, states, every county and the delineation record (URL,
   date, SHA-256), sets `verified: true`, disables state adapters for states the file does not list, and rewrites
   `results/studio_metro_ranking.json`. The script compares the configured metros with its `TOP_N = 5`, so with six
   metros `configured_equals_top_five` becomes false: change the rule to six (`TOP_N`, the rule text and the key name)
   in the same change, and update [metros.md](metros.md) and the README "Supported metros" paragraph.
3. **Download its data.** `make studio-data METRO=<id>` builds the snapshot: Census Gazetteer tracts, ACS 5-year
   tract tables and County Business Patterns (need `CENSUS_API_KEY` in `.env`), LEHD LODES for each of its states,
   an Overture places extract for its bounding box (the pinned release must still be public; Overture keeps a release
   for at most 60 days), the FHWA HPMS road layer per county, BLS QCEW wages, the FRED prime rate, and one step per
   enabled enhancement (`studio/data/snapshot.py`). A failed source never stops the others; the command exits 1 and
   names each unreachable host or missing key. Snapshots live under `data/` (gitignored) with SHA-256 checksums.
4. **Measure coverage.** `make studio-coverage`. The metro runs in full mode only when every business type reaches
   the 0.8 threshold; otherwise it runs in limited mode and the app says so. Do not lower the threshold to get full
   mode: that is a policy change for the owners.
5. **Code and tests that list the metros.** Add the id to `STARTUP_METRO_IDS` in `src/uwcopilot/agent/applications.py`
   (a closed list so the application's metro field can only name a known metro; `tests/agent/test_startup_application.py`
   keeps it equal to the registry). Update the `REAL` set in `tests/studio/test_metros.py`; add the new CBSA's rows to
   the recorded delineation excerpt `tests/studio/fixtures/delineation/list1_2023_rows.json` by re-recording it
   (`make studio-fixtures`), not by typing FIPS codes, and update `test_parse_recorded_rows_finds_the_five_metros` in
   `tests/studio/test_metros_delineation.py`; update the golden count in `tests/studio/test_narrative_golden.py`
   (6 types x 7 metros = 42 instead of 36). Re-record the web mock fixtures (`make web-mock-fixtures`), which list the
   metros. Then `make studio-test`, `make web-test` and `make test`.
6. **Re-run the evaluations.** The golden scenarios come from `list_metros()`, so they grow to 42 and the real-metro
   subset to 36. That changes the population defined in [s12_baseline.md](s12_baseline.md) ("5 real metros", "30"),
   so it needs a new commit to that file saying what changed and why, before re-running. Then `make studio-paths`,
   `make studio-handoff`, `make studio-business-metrics` and `make reports`.

Apart from step 2's top-N rule in the ranking script, step 5's closed list and the tests, no code changes are
needed, unless the metro needs a new state traffic adapter or a city dataset.

## 9. Likely interview questions

Both owners should be able to answer all 20. They are split by interview angle, not by who built what.

### 9.1 Rahul Vajja: lender, risk and evaluation angle

1. **What is the baseline B0, and why is it a construction instead of real plans?**
   B0 is what a plain plan would print on the same SYNTHETIC inputs if SiteIQ, the simulation and the ledger were
   missing: the founder's typed figures, the untyped inputs at their most likely value, and years 1-3 revenue, NOI,
   debt service and DSCR from one deterministic run. We had no real founders' plans to sample, and describing "a
   typical plan" in words would have been a guess. Building B0 in code from the same inputs means only what is
   printed and what can be cited differ, not the numbers. The cost is that the comparison shows what the ledger
   records, not that real plans lack sources, and the results file says so.

2. **How do you know the metric definitions were not tuned to the results?**
   `docs/studio/s12_baseline.md` was committed in `4a19865` at 18:58 UTC, before the script (`1958f6e`, 19:07 UTC)
   and the measured results (`b44b581`, run at `e68b88d`). Only that one commit touches the file. The results file
   records the file's SHA-256 under `provenance.eval_sets`, so an edit after the run would show. If a definition had
   to change, the rule is a new commit to that file that says what changed and why.

3. **0.8482 against 0.0251 looks like a huge win. Is it?**
   Mostly not, and we say so. Rule S credits model estimates because the ledger records the model run behind them,
   and 10,745 of the Studio figures are MODEL_ESTIMATE claims of kind model; B0 cannot print such a record by
   definition. Rule E counts only a document page, a public-data row or a program or target section: Studio 0.057
   [0.0545, 0.0597] against B0 0.0251 [0.0182, 0.032], a paired difference of 0.0319 [0.0259, 0.038]. That smaller
   gap is the honest statement about external evidence.

4. **Why is "founder figures backed by a document" equal for both plans?**
   The same documents and the same tolerance (dollar figures within $1.00, others exact) decide both, so they must
   be equal, and the baseline file said so before the run. The result is 33/251 for both, 0.1315 [0.0958, 0.1693],
   equal in every scenario. It shows the ledger does not make a founder's figure better evidenced; it only records
   whether a document backs it. What Studio adds is the comparison: 12 founder figures were flagged CONFLICTING,
   while B0 has no comparison step and surfaces none by definition.

5. **How did you compute the intervals, and what do you do when every case passes?**
   Percentile bootstrap with 1,000 resamples and seed 42, resampling whole scenarios, because the figures inside one
   package share one plan, one simulation and one set of documents. Each resample recomputes the pooled share, and
   the Studio minus B0 difference is paired: both shares come from the same resampled scenarios. When every case
   passes, the bootstrap collapses to [1.0, 1.0], so we also give a Wilson 95% interval: 36/36 complete packages
   gives [0.9036, 1.0], so with 36 cases a true pass rate as low as 0.9036 is still consistent with what we saw.
   No p-values.

6. **What does "median 7.199 seconds to a package" mean? Can you claim Studio saves time?**
   It is wall clock on a shared 4-CPU machine from a scenario's inputs to an audited PDF: simulation, quick path
   search, SiteIQ where tracts exist, building the package, rendering the PDF and reading it back. Over 36 scenarios
   the median is 7.199 s [6.767, 7.494] and the 95th percentile 8.964 s [7.81, 9.248]. It is not a user time:
   nobody was observed, the founder's time to gather inputs is excluded, and B0's time was not measured. So we make
   no time-saving claim.

7. **Can anything in this pipeline approve or decline a founder's loan?**
   No. `handoff` runs the existing agent only up to the human checkpoint and never resumes it. The cited S6 file
   (`results/studio_handoff_metrics.json`) reports 0 decisions recorded without a human for the 36 golden packages
   (Wilson 95% [0.0, 0.0964]) and for all 71 cases. This run repeated the check on its own 36 packages: 36/36
   reached the checkpoint and 0 decisions were recorded without a human. Studio itself never approves, declines or
   recommends a lender.

8. **What is the path-found rate, and why is it low?**
   It is cited from `results/studio_path_metrics.json` (S5): of the 536 SYNTHETIC plans that did not already meet
   the three targets, the search found a one- or two-lever change inside the default bounds for 0.1026 [0.0784,
   0.1306]; 0.0392 [0.0243, 0.0578] also met the SYNTHETIC program's rules. The rate depends on how the plans were
   generated and on the editable targets and lever bounds, so it describes the search on these plans, not anyone's
   chances. Among this run's 36 golden scenarios, 33 had no path within bounds, 2 already met the target and 1 had a
   path, which is a description, not an estimate. The S5 file records `git_dirty: true`, and we report that.

9. **Why cite S1, S5 and S6 instead of re-measuring, and how do you know which version you cited?**
   Re-measuring in a second script could give two numbers for one metric, and those phases already have their own
   evaluation designs. Each cited block names the file and key and carries that file's provenance: script, time,
   commit and dirty flag. That is how we can say the S6 file was written at `cf60df5` with a clean tree, while the S1
   and S5 files say `git_dirty: true`. The S12 provenance also stores the SHA-256 of every cited file
   (`data_snapshots.cited_results_sha256`), which pins exactly which version of each file was read.

10. **What would you need before saying anything about real lenders or real founders?**
    Real outcome data, or at least a lender's review of the package format; every plan here is SYNTHETIC and
    real-world accuracy is "not measured". A usability test with real people that reports n, the protocol and raw
    times. Real-metro runs with snapshots on the evaluation machine, because in this run no real-metro scenario had a
    site score. And the privacy rules of `docs/studio/privacy.md` in place before any real founder data; any
    regulatory reference would cite the primary source and its date, and applicability depends on the institution
    and its risk profile.

### 9.2 Bhavani Adula: product, data and founder angle

1. **What does S12 measure, in one minute?**
   It runs the 36 golden SYNTHETIC founder scenarios end to end, from inputs to an audited PDF package and on to the
   lender copilot's human checkpoint, and compares them with a baseline defined beforehand. All 36 packages were
   complete (36/36, Wilson 95% [0.9036, 1.0]), with a median of 7.199 s of machine wall clock per package. Evidence coverage is
   measured under two rules, and founder figures backed by a document come out equal by construction. Data coverage,
   path-found rate and handoff success are cited from earlier phases. LLM cost is 0 by construction; compute cost and
   a usability test are not measured.

2. **Why these five cities?**
   The Studio plan's rule is "the five largest US metropolitan statistical areas by population in the latest Census
   Bureau estimates". It is a design choice, but an objective one that anyone can check. Our script ranked 387
   metropolitan areas by POPESTIMATE2025 in the Census file `cbsa-est2025-alldata.csv` and recorded
   `configured_equals_top_five: true` in `results/studio_metro_ranking.json`: New York (20,112,448), Los Angeles
   (12,844,441), Chicago (9,434,123), Dallas-Fort Worth (8,477,157) and Houston (7,904,627). We do not claim they are
   better markets; the plan's reasons about density or tourism are still marked UNVERIFIED in `metros.md`.

3. **How would you add a sixth?**
   Take rank 6 from the same file and vintage: CBSA 12060, Atlanta-Sandy Springs-Roswell, GA (6,482,182). Write its
   config with a title prefix and design choices only, run `make studio-metros` to fill the CBSA code and counties
   from the Census file, `make studio-data METRO=<id>` for the snapshot (Census key in `.env`) and `make
   studio-coverage`. Then add the id to the closed metro list in `agent/applications.py`, update the metro tests, the
   recorded delineation rows, the golden count (36 becomes 42) and the ranking script's top-N. Before re-running
   S12, commit a change to `s12_baseline.md` for the larger population, then re-run S5, S6, S12 and the README. If any
   business type is below 0.8 coverage, the metro runs in limited mode and the app says so.

4. **What is limited mode, and what does a founder see?**
   A real metro is in full mode only when every business type's data coverage meets the threshold of 0.8. All five
   are limited today because short-term rental is at 0.6779 to 0.6826 and motel at 0.745 to 0.7722, even though the
   other four business types are at 0.8675 to 0.9415 (`results/studio_metro_coverage.json`). The founder sees the
   mode and its reason, and factors without data show as UNKNOWN or ASSUMPTION instead of being filled in. In this
   S12 run, the real-metro scenarios also had no site score at all (0 of 6 in each metro). The results file records
   that, not the cause; the likely cause is that the machine had no real-metro tract snapshot.

5. **The prompt said 30 scenarios. Why did you run 36?**
   The prompt's 30 are 6 business types x 5 real metros. The golden set from S4 and S6 is 36: those 30 plus six in
   the SYNTHETIC metro. Reusing it unchanged means S12 runs the same scenarios S6 measured (the packages are rebuilt
   at the S12 commit, so their ids differ from the S6 file's), and the SYNTHETIC
   metro was the only one where SiteIQ scored a site in this run (6 of 6). We still report the 30 on their own: for
   example rule S on the 30 is 0.8362 [0.8304, 0.842] for Studio against 0.0246 [0.0173, 0.0319] for B0.

6. **What does "evidence coverage" mean to a founder?**
   For each number printed in the package, can a reader see where it came from? Rule S counts a figure when the
   package names a document page, a public-data row, a program or target section, a named calculation with its
   inputs, a model run with its seed and input hash, or an assumption with a named source. The founder's own word
   without a document does not count: 1,332 printed figures were USER_STATED. Over the 36 packages, 12,222 of 14,410
   figures had a checkable source.

7. **What happens when a founder's document disagrees with what they typed?**
   The golden scenarios cycle three document variants by scenario index: no documents, matching statements and
   contradicting statements. When every document about a field agrees within tolerance, the founder claim is
   VERIFIED and cites the page; that happened for 33 of 251 typed figures. When a document disagrees, both values are
   printed as CONFLICTING so the lender sees it; that happened for 12 founder figures. B0 would print the typed
   figure with no flag, because it never compares the two.

8. **What does a package cost to make?**
   LLM cost is 0.0 USD per package by construction: the narrative is the template provider and the lender memo the
   deterministic template. The script checked every package, and 36 of 36 had provider "template", no model, no
   fallback and no LLM attempt; had one failed, the cost would not be reported as 0. Compute cost is not measured,
   because no cloud resource was used and the machine's cost is not metered. What an LLM narrative would add to cost
   or time is also not measured.

9. **Where are the live demo and the demo GIF the prompt asks for?**
   They do not exist. The Space's `Dockerfile` and the deploy workflow (`.github/workflows/deploy-space.yml`) are in
   the repository, but no deploy has run, so there is no public URL, and no GIF was recorded. What exists are
   screenshots that `scripts/web_metrics.py` captured on 2026-10-14 (`results/web_metrics.json`, run
   `local-container`) from a LOCAL container signed in with the dev login, showing SYNTHETIC data only. We would
   rather show that gap than a mock-up presented as live.

10. **Did any real founder use it? What would a usability test report?**
    No. The results file says "not measured: no usability test with real people was done, so there is no n,
    protocol or raw time". A real test would report n, the protocol (the tasks, what counts as done, how time is
    measured) and every person's raw times, not only a median, and it would run under `docs/studio/privacy.md`.
    Until then the only time we have is machine wall clock, which is not a person's time.

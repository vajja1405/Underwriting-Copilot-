# Studio S12: the baseline and the metric definitions (written before measuring)

This file was written and committed **before** `scripts/eval_studio.py` existed and before any S12 number was
produced. The commit that adds it is the record of that order. It defines the baseline, every metric, its unit, its
population and its interval method, so the results cannot shape the definitions afterwards. If a definition has to
change after the first run, the change is a new commit to this file that says what changed and why.

Everything evaluated here is **SYNTHETIC**: the founder plans, owners, documents, the SYNTHETIC metro and the
Northwind startup program. Probabilities are "simulated, based on stated assumptions". Nothing here measures real
founders, real lenders, real loan outcomes, real-world savings or production accuracy.
"Educational planning tool. Not financial, legal or lending advice. Lenders make their own decisions."

## 1. What is evaluated

The **golden founder scenarios** of Studio S4/S6 (`studio/narrative/golden.py`, `studio/package/golden.py`): 6
business types x (5 real metros + the SYNTHETIC metro) = 36 seeded SYNTHETIC plans. The S12 prompt asks for "the 30
synthetic founder scenarios" (6 types x 5 real metros); those 30 are a subset of the 36, and the results report the 36
and the 30 real-metro scenarios separately. A real-metro scenario is still a SYNTHETIC plan; only the metro's data (if
this machine has its snapshot) is public.

For each scenario the S12 script runs the existing S2-S6 code end to end, unchanged: the golden scenario (SiteIQ when
the metro has tracts on this machine, the S3 simulation with 1,000 paths and 36 months, the S5 path search in quick
mode), `project_from_golden`, `build_package`, `render_pdf`, the PDF round-trip audit and `handoff`.

## 2. The baseline B0: a plan made without SiteIQ, the simulation and the ledger

B0 is a **construction**, not a sample of real plans: no real founder's plan was collected. It answers one question:
on the same SYNTHETIC inputs, what would a plain plan print, and which of its figures could cite a source, if the
three Studio parts were missing? B0 is built per golden scenario, from the same founder inputs and the same documents
the Studio package receives, by this recipe (implemented in code, so it is computed, not assumed):

1. **Founder figures.** Every plan and loan figure the founder typed: the fields of the founder intake list
   (`studio/narrative/founder_claims.FIELD_SPECS`: owner equity, loan amount, loan term, note rate, rate spread,
   build-out cost, price, monthly rent, paid hours per month, hourly wage, capacity, days open per week, owner draw)
   that hold a value. The owner draw is printed only when it is not zero (the same rule as the ledger).
2. **Inputs the founder did not type.** One figure per input of the one-forecast projection, at its most likely value:
   every input of the S3 setup (`feasibility.setup`, built with `path_to_approval.evaluate.setup_for_case`) that the
   founder did not type, except the ones a one-forecast plan does not use (the shock probabilities, the monthly noise,
   and the competitor-entry and one-off-shock sizes, which only act when a shock happens). The note rate is printed
   here when the founder did not type it.
3. **One-forecast results.** For years 1, 2 and 3 of the opening: revenue, net operating income, debt service (the
   loan's payments plus any other debt's monthly payments x 12) and the debt service coverage ratio (NOI / debt
   service). They come from `feasibility.model.single_point_run`: the simulator's path model run as one
   deterministic path with every input at its most likely value, no shocks and no noise (the code calls it "the plan
   a spreadsheet would show"). This is not a simulation: no random draws, no percentiles, no probabilities.

What B0 does **not** have, by definition:
- no SiteIQ: no site score, no location factors, no competition counts, no market data, no candidate-site comparison;
- no simulation: no P10/P50/P90, no probabilities, no stress results, no tornado, no path-to-approval search;
- no ledger: no claim statuses, no provenance records, no formula ids, no input hashes, no comparison of a typed
  figure with a document (so no conflict flag), no round-trip audit.

**B0's source rule.** A B0 figure carries a source only when it is a founder figure and at least one document the
founder holds is about that field and every such document prints the same value, with the tolerance the ledger uses
(`founder_claims.same_value`: dollar figures within $1.00, other figures exactly). Then the plan can cite the
document's page. Every other B0 figure carries no source: B0 has no place to record where a default value came from
or how a result was calculated. This follows from the definition of B0; it is not an observation about how real
founders write plans, and the evaluation must say so wherever the comparison is shown.

B0 uses the **same input values** as the Studio run on purpose: only what is printed and what can be cited differ,
not the numbers. The evidence-coverage metric depends on which figures are printed and whether each carries a source,
not on the figures' values.

## 3. The metrics

Every metric below is computed by code in the repository and written to `results/studio_business_metrics.json` by
`scripts/eval_studio.py`. A metric this run does not measure is written as "not measured" with the reason; a metric
already measured by an earlier Studio script is **read from that results file and cited** (file, key, its own
provenance block), not recomputed.

| # | Metric | Unit | Population | Studio value | B0 value |
|---|---|---|---|---|---|
| M1 | Time to a complete package | seconds (wall clock, this machine) | golden scenarios | measured by this run | not measured |
| M2 | Evidence coverage (rule S) | share 0-1 | printed figures, pooled over scenarios | measured by this run | computed by B0's rule |
| M3 | External-evidence coverage (rule E) | share 0-1 | printed figures, pooled over scenarios | measured by this run | computed by B0's rule |
| M4 | Founder figures backed by a document | share 0-1 | the founder's typed figures | measured by this run | computed by B0's rule |
| M5 | Printed numbers that trace to a ledger claim | share 0-1 | numbers read back from the package text and PDF text | cited (S6) | not applicable |
| M6 | Data-coverage score by metro | share 0-1 | metro x business type | cited (S1) | not applicable |
| M7 | Path-found rate | share 0-1 | SYNTHETIC plans that miss a target | cited (S5) | not applicable |
| M8 | Agent handoff success | share 0-1 | golden packages | cited (S6) | not applicable |
| M9 | Cost per package | USD | one package | LLM: 0 by construction; compute: not measured | not measured |
| M10 | Usability test with real people | n, protocol, raw times | people | not measured | not measured |

### M1. Time to a complete package (machine-dependent)

- **Definition.** Wall-clock seconds on the machine that ran the script, from a scenario's inputs to an audited PDF:
  (a) the golden scenario (SiteIQ when tracts exist, the simulation, the quick path search), (b) `build_package`,
  (c) `render_pdf`, (d) the PDF round-trip audit (`audit_pdf`) and the page-label check (`pages_missing_labels`).
  Total = (a) + (b) + (c) + (d). The handoff to the lender copilot is timed separately and is not part of it.
- **Unit and summary.** Seconds per scenario: median and 95th percentile, each with a percentile bootstrap 95%
  interval over scenarios (1,000 resamples, seed 42), plus min, max, n and the first scenario's time on its own (it
  also loads configs and caches once).
- **Label.** A machine-dependent measurement on a shared machine, **not a user time**: it excludes the founder's
  time to gather inputs and documents, and nobody's time was observed. The machine's CPU count and load average are
  in the provenance block.
- **B0.** Not measured: no one was timed writing a plan.

### M2. Evidence coverage (rule S): share of printed figures that carry a checkable source

- **Printed figure.** Studio: every claim of the package's founder ledger with a numeric display (`usd0`, `usd2`,
  `ratio2`, `ratio2x`, `pct1`, `int`, `months`, `years`); text claims (names, labels) are not figures. Every claim is
  printed in the package's evidence appendix (section 11). An UNKNOWN claim prints "not available" and counts as a
  figure without a source. B0: every figure of section 2.
- **Rule S (Studio claim).** A claim carries a checkable source when the package states where its value came from and
  that origin is something other than the founder's word alone or an unsourced default:
  - VERIFIED, MODEL_ESTIMATE or CONFLICTING with provenance kind `document`, `public_data`, `policy`,
    `calculation` or `model` (a document page, a public-data row, a program or target section, a named calculation
    with its inputs recorded, or a model run with its name, version, seed and input hash): **yes**;
  - ASSUMPTION whose provenance source is not "unsourced" (a named publisher, config file, generator or the
    calculation it came from): **yes**; ASSUMPTION marked "unsourced": **no**;
  - USER_STATED (the founder's own figure, no document): **no**; a CONFLICTING claim of kind `user_input` (the
    founder's figure a document contradicts): **no**; UNKNOWN: **no**.
- **Rule S (B0 figure).** Section 2's source rule.
- **Estimate.** Pooled share: figures with a source / all figures, summed over scenarios. Also per scenario (min,
  median, max), by metro and for the 30 real-metro scenarios.
- **Interval.** Percentile bootstrap 95% over scenarios (resample the scenarios with replacement and recompute the
  pooled share; 1,000 resamples, seed 42). The difference Studio - B0 uses a paired bootstrap: both shares are
  recomputed on the same resampled scenarios.
- **Reading.** Most of the Studio - B0 gap follows from the definitions: model estimates and calculations count for
  Studio because the ledger records their run or formula and inputs, and B0 prints no such record. M3 and M4 show the
  comparison without that credit.

### M3. External-evidence coverage (rule E, stricter)

- **Definition.** Share of printed figures backed by a document page, a public-data row or a program or target
  section: Studio claims with status VERIFIED or CONFLICTING and provenance kind `document`, `public_data` or
  `policy`. Model estimates, calculations, assumptions and founder statements do not count. In the SYNTHETIC metro
  the public-data rows are SYNTHETIC city rows (labelled so in the ledger). `policy` covers the SYNTHETIC startup
  program's sections and the Studio's own editable targets (design choices of the build plan, sourced as such in the
  ledger): this is a stated rule, not a judgement of their quality.
- **B0.** The same as rule S: B0's only sources are the founder's documents.
- **Population, estimate, interval.** As M2.

### M4. Founder figures backed by a document (same population for both)

- **Population.** The founder's typed plan and loan figures (section 2, item 1), the same set for Studio and B0.
- **Studio.** The figure's founder claim (`FOUNDER.*`) has status VERIFIED. **B0.** Section 2's source rule.
- **Expected result, stated in advance.** Equal by construction: the same documents and the same tolerance decide
  both. It is reported to show that the ledger does not make a founder's figure better evidenced; it records whether
  a document backs it. **Conflicts surfaced:** Studio counts founder figures whose claim is CONFLICTING (a document
  prints another value); B0 has no step that compares a figure with a document, so it surfaces none by definition
  (not a measurement).
- **Interval.** As M2.

### M5. Printed numbers that trace to a ledger claim (cited, S6)

- **Source.** `results/studio_handoff_metrics.json`, `golden_founder_scenarios`: `package_numbers_read_back`,
  `untraced_numbers_package_text`, `untraced_numbers_pdf_text`, `package_text_audit_empty`, `pdf_text_audit_empty`
  (the round-trip audit of the package text and of the PDF's own text). Not recomputed.
- **B0.** Not applicable: B0 has no ledger to trace a number to.

### M6. Data-coverage score by metro (cited, S1)

- **Source.** `results/studio_metro_coverage.json`: per metro, `coverage_share` per business type (the weighted
  share of a business type's required factors backed by data, times the share of tracts covered, as defined in that
  file's `description`), the minimum over business types, the mode (full, limited or synthetic) and the threshold.
  Not recomputed.
- **This run.** Per scenario: the metro mode the run used and whether SiteIQ scored a site. These depend on which
  metro snapshots exist on the machine that runs the script, and are reported as observed.
- **B0.** Not applicable: B0 uses no location data, so it has no data-coverage score.

### M7. Path-found rate (cited, S5)

- **Source.** `results/studio_path_metrics.json`: `headline.path_found_rate_full` (share of SYNTHETIC plans that do
  not meet the three targets for which the search returns at least one change-set of one or two levers inside the
  default bounds that meets all three), the program-compliant variant, the counts and the per-metro rates, with the
  bootstrap intervals reported there. Not recomputed.
- **This run.** The path-search status of each golden scenario (counts only; a description of these 36 plans, not
  an estimate of the rate).
- **B0.** Not applicable: B0 has no search.

### M8. Agent handoff success (cited, S6)

- **Source.** `results/studio_handoff_metrics.json`, `golden_founder_scenarios`: `reached_human_checkpoint`,
  `decisions_recorded_without_a_human` (must be 0), `memo_check_passed`, `unsupported_numbers_in_memos`, with the
  bootstrap and Wilson intervals reported there; and `all_cases` (the golden packages plus the 35 startup
  applications). Not recomputed.
- **This run.** `handoff` runs on every package again as a consistency check; the results report its k/n next to
  the cited value. The headline is the cited file.
- **B0.** Not applicable: `to_application` needs the package's ledger.

### M9. Cost per package

- **LLM cost.** USD 0 **by construction**: the narrative is the template provider and the lender memo is the
  deterministic template (the fake LLM's template writer, "FAKE LLM - test mode"); no language model is called. The
  script checks, per package, that the narrative provider is "template" with no model and that no fallback was used,
  and records the check; if the check fails, the cost is not reported as 0.
- **Compute cost.** Not measured: no cloud resource was used and the machine's cost is not metered.
- **B0.** Not measured.

### M10. Usability test with real people

- Not measured: no usability test with real people was done, so there is no n, protocol or raw time. If one is done
  later, report n, the protocol and the raw times, not only a summary.

## 4. Statistics

- Bootstrap: percentile method, 1,000 resamples, seed 42, the scenario as the resampling unit (figures of one
  scenario are not independent, so they are resampled together).
- When every scenario has the same outcome, a bootstrap interval collapses to one point; the cited S6 rates also
  carry Wilson 95% intervals, which stay informative there.
- No p-values. No statement of "significance".

## 5. Threats to validity

- The golden scenarios are SYNTHETIC and were generated by the same authors who wrote the Studio code; the document
  variants follow the scenario index (none, matching statements, contradicting statements), so the share of
  document-backed figures is a property of the generator, not of founders.
- B0 is a construction (section 2). The gap between Studio and B0 shows what the ledger records, not that real
  founders' plans lack sources.
- Real-metro scenarios on a machine without the metros' snapshots run without SiteIQ (limited mode); the results
  record, per scenario, what this machine had.
- Time is machine-dependent and the machine is shared.

# Phase 8 walkthrough: resume lines, the README headline table and the final polish

Phase 8 is the last phase of the build plan (Prompt 8, "Final polish and resume lines", in
[build_prompts.md](build_prompts.md)). It adds two things that read what the earlier phases measured and put it in
front of a reader: a script that prints resume bullets built only from `results/*.json` and says on stderr what it
left out and why, and a headline table at the end of the README RESULTS block with one row per headline metric of each
results file the README Status tables cite (apart from the three files the existing rows already read). The Founder
Studio S12 evaluation is merged into this phase ("Studio S12 is merged with Phase 8" in the build plan); section 6
summarizes it and [walkthrough_s12.md](studio/walkthrough_s12.md) explains it.
This walkthrough is written so both owners (Rahul Vajja and Bhavani Adula) can explain the work in their own words.

Every number in this project comes from public datasets or SYNTHETIC data, and some runs are SIMULATED. Phase 8
measures nothing new apart from the S12 evaluation: the resume script and the README table only read results files.
This is a demonstration, not a compliance program: nothing here claims compliance with any regulation, real-world
accuracy, savings or production readiness. Founder Studio is an educational planning tool: not financial, legal or
lending advice; lenders make their own decisions.

**What ran and what did not.**
- Ran: `scripts/resume_lines.py` at commit `1928a19` with no changes to tracked files on 2026-10-26, exit code 0
  (the full output is in section 3). The README results block was regenerated from the results files (`ac325fb`),
  and a test checks that the committed README equals a fresh render (section 9). The S12 evaluation
  (`scripts/eval_studio.py`, the script behind `make studio-business-metrics`) ran at commit `e68b88d` with a clean
  tree (`results/studio_business_metrics.json`, `provenance.git_sha` and `provenance.git_dirty: false`) and was
  committed in `b44b581`. The Phase 8 offline tests ran too (section 9).
- Not done: Prompt 8 step 4 (final CI, tag `v1.0`, pull request) waits for the owners (section 10).
- Not part of Phase 8, and not run anywhere in this project: any call to Anthropic or OpenAI (no API key was
  configured), any cloud service, and any deployment. The only real LLM calls in the project are the small local
  Ollama smoke runs listed in the README section "Language models: what has actually run". The Hugging Face Space is
  not deployed, so there is no live URL. Web and container measurements were made on a LOCAL container.

**How to read the numbers.** Every number below is copied from a results file, which is named each time (with the
key where it helps), or from the output of `scripts/resume_lines.py`, which itself reads only results files. Values are
shown as the script and the README print them (4 decimals for rates and scores, 1 decimal for alerts per 1,000
customers, 3 decimals for seconds). Intervals are 95% intervals: percentile bootstrap with 1,000 resamples and seed
42, unless marked Wilson (Wilson score interval) or exact (Clopper-Pearson).

## 1. What Prompt 8 asked for, and what exists now

| Item | Prompt 8 asked for | Where it is | State |
|---|---|---|---|
| 1. README | One-paragraph summary, architecture diagram, a results table generated only from `results/*.json` with CIs, datasets and licenses, limitations (synthetic documents, public datasets, no production users, not a compliance program), how to run in 3 commands, and a 3-minute demo script (`docs/demo_script.md`) | `README.md`: summary at the top, Status tables, Architecture (Mermaid diagram), Results, Datasets and licenses, Limitations, Run the demo. New in this phase: the headline table (`governance/readme_headlines.py`, rendered by `scripts/render_reports.py`), the S12 status row, the supported-metros paragraph, the run steps for S12 and the resume lines, the `src/uwcopilot/resume` package row, and links to the study guide and the demo script | Headline table built, tested and rendered. "Run the demo" is `cp .env.example .env` followed by three make commands (`make setup`, `make web-install`, `make web-dev`). The demo script is a separate document of this phase, linked from the README and not described here |
| 2. Resume lines | `scripts/resume_lines.py` reads `results/*.json` and prints 4-6 honest bullets using only measured numbers, omitting any bullet whose metric was not measured; six templates | `scripts/resume_lines.py`, `src/uwcopilot/resume/`, `config/contributions.yaml`, `tests/resume/`, `make resume-lines`, `make resume-lines-test` | Built and run: 6 Lender Desk bullets (inside 4-6) plus 6 Founder Studio bullets for Prompt S12. Every template was changed or partly dropped; section 4 says how and why |
| 3. Study guide | `docs/INTERVIEW_GUIDE.md` merges all walkthroughs into one study guide | Linked from the README (`1928a19`) | A separate document of this phase, not described here |
| 4. Release | Final CI green, tag v1.0, open the PR | - | **Not done.** Waits for the owners (section 10) |
| Studio S12 (merged) | `scripts/eval_studio.py` with business metrics against a baseline written down first; README; S12 walkthrough; per-person resume lines | Section 6, [walkthrough_s12.md](studio/walkthrough_s12.md), [s12_baseline.md](studio/s12_baseline.md) | Built + measured on 36 SYNTHETIC scenarios. Per-person lines come from `config/contributions.yaml`, which stays empty until the owners fill it in (section 5). No live URL and no demo GIF: the Space is not deployed; the README shows two screenshots taken from a local container |

## 2. Files

### Resume lines (`scripts/resume_lines.py`, `src/uwcopilot/resume`)

| File | What it does | Why it is built this way |
|---|---|---|
| `scripts/resume_lines.py` | Entry point. Loads `config/contributions.yaml`, builds the report, prints the "Projects" bullets and the per-person section to stdout, and every left-out bullet or clause with its reason to stderr. Options: `--results-dir`, `--contributions`, `--repo-root`. Exit 0 when printed, 2 when `config/contributions.yaml` has a bad shape (for example `owners` is not a mapping of names to lists) or names an unknown component. | Read-only: it runs no model and writes no file. Stdout and stderr are split so `> resume.txt` keeps only the bullets while the omissions stay on screen. |
| `__init__.py` | Package docstring only. | |
| `results.py` | `ResultsFolder` loads each JSON file once. `dig` and `number_at` walk keys and return None when a step is missing. `read_estimate` turns one entry into an `Estimate` (value, low, high, method, n) from the four layouts it knows (`ci_low`/`ci_high`, `ci95_low`/`ci95_high`, a `wilson_ci95` list, a `ci95` list). `missing_reason` says why a value is not quoted: the file is absent, the file's own "not measured" text, or the key is absent. | Nothing here computes a metric. An entry without an interval is not an `Estimate`, so a value read with `read_estimate` always comes with its interval. A few values are read with `number_at` and printed without one: counts, the two group means next to a paired difference that carries the interval (requests per case, decision-error rate), the coverage-share range and the 0.7984 coverage reference. |
| `text.py` | `BulletDraft` assembles one bullet from measured clauses (a number and its source file), context clauses (no number) and omissions; `finish()` drops the whole bullet when its core metric is missing or nothing is measured. Number formats: `num` (4 decimals), `signed`, `count` (thousands separators), `interval` (writes "Wilson" inside the brackets for a Wilson interval). `FORBIDDEN_WORDING` and `check_wording` (the wording guard), and `STATIC_PHRASES` (names and dates with digits that are not metrics, such as "SR 26-2"). | One place for formats, so no clause rounds a value to a friendlier figure. A context clause ("SHAP reason codes") is printed only next to a measured clause, so a bullet cannot be all adjectives. |
| `lender_platform.py` | The evidence-platform bullet (template 1) and the document-extraction bullet (template 4), with the injection clauses. | Section 4.1 and 4.4. |
| `lender_models.py` | The credit bullet (template 2), the insurance bullet (template 3), the sanctions-screening bullet (template 5) and the SR 26-2 footnote. | Sections 4.2, 4.3 and 4.5. The model-card clause is written only when `docs/model_cards/credit_model_card.md` exists. |
| `lender_governance.py` | The governance bullet (template 6): release-gate counts and the drift-monitor validation, plus the recorded reasons why tracing cost, p95, cloud and IaC are left out. | Section 4.6. "In the CI workflow" is written only when `.github/workflows/ci.yml` contains `make release-gate`. |
| `studio.py` | The six Founder Studio bullets (platform, feasibility, provenance, path to approval, SiteIQ, and the S12 business bullet) and `studio_notes`, which says why the local-model narrative file is not quoted. | Section 4.7. Every Studio bullet says SYNTHETIC or SIMULATED in its text (not every clause does; the Studio heading says it too). |
| `report.py` | `build_report` builds all twelve bullets in print order and collects the omissions, footnotes and notes. It adds a note when fewer than 4 lender bullets are measured ("do not pad them"). `render_projects` and `render_omissions` produce the text. | The headings and the note about brackets live here, so the output explains its own intervals. |
| `contributions.py` | `COMPONENTS` (one id per bullet), `load_contributions`, `parse_contributions` (an unknown id or a bad shape raises `ContributionsError`), `render_per_person`. | Section 5. |
| `config/contributions.yaml` | Who owns which component. Ships with both owners listed and no component (`Rahul Vajja: []`, `Bhavani Adula: []`), and a comment that explains why. | Empty by design until the owners fill it in. |

| Test file | What it covers |
|---|---|
| `tests/resume/resume_fixtures.py` | A complete fixture results folder with distinctive values, a fake repo root (with or without the model card and the CI gate), and the helpers `allowed_numbers` and `numbers_in`. |
| `tests/resume/test_resume_results.py` | Each interval layout, missing values and keys, missing files, and the reason text. |
| `tests/resume/test_resume_text.py` | Formats, the Wilson label, year ranges, the wording guard (what it rejects and what it accepts), a draft with only context is dropped, a missing core metric drops the bullet, `finish()` refuses forbidden wording. |
| `tests/resume/test_resume_report.py` | Six lender and six Studio bullets from the full fixture; every number in a bullet appears in the results folder; values appear with their intervals; a not-measured clause or core metric is dropped and reported; the circular agreement is never quoted; attack success is quoted per layer only from a measured full run, never from a partial one; no forbidden wording in the output; a local container is never called a deployment; the model-card and CI clauses follow the repo files; the S12 bullet; and the committed results give 4-6 lender bullets whose numbers all appear in `results/`. |
| `tests/resume/test_resume_contributions.py` | The committed file is empty and explains why; the component ids match the bullets; an empty or missing file prints the fill-in note; each person gets only the listed components; a listed component without a bullet goes to stderr; unknown ids and bad shapes are errors. |
| `tests/resume/test_resume_lines_script.py` | The script end to end, including exit code 2 on an unknown component. |

Make targets: `make resume-lines` (passes `ARGS`) and `make resume-lines-test`.

### README headline table

| File | What changed | Why |
|---|---|---|
| `src/uwcopilot/governance/readme_headlines.py` (new) | Reads the results files the README Status tables cite (apart from `risk_metrics.json`, `docintel_metrics.json` and `evidence_metrics.json`, which the existing rows already read) and builds one row per headline metric in four sections: Lender Desk, Governance, Founder Studio, and Web app and deployment. Most rows are a `Spec`: the JSON paths it reads and a function that turns the values into text (the Studio data-coverage row is built by its own function, `coverage_row`). `interval_text` picks the interval (section 7); `not_measured_reason` understands the three "not measured" layouts the files use; smoke runs print counts only. | Every cell is read at render time, and the Source column links the file, so a reader can check any cell. It sits next to the governance packs and reuses their helpers (`lookup`, `table`, `integer`, `NOT_MEASURED`). |
| `scripts/render_reports.py` (changed) | `load_report_data` loads the headline files under the key `readme_headline_sources` (`--headline-results-dir`, default `results/`); `readme_block` appends `headline_section(...)` after the existing Phase 1, 2 and 4 rows and their notes. | The existing rows stay byte-identical (tested). `headline_results_dir=None` leaves the table out. |
| `tests/governance/test_readme_headlines.py` (new) | With no results file every row is "not measured" and the table text holds no metric; hand-made results render with their own interval; not-measured rows give the file's reason; runs on a handful of cases show counts only; the checks catch a typed number, a number borrowed from another results file and an invented reason, and find the SYNTHETIC / SIMULATED label a row must repeat (no test feeds them a row that lacks the label); the committed results pass every check. | Ground rule 1, checked per row against the row's own file. |
| `tests/test_render_reports.py` (two new tests) | The existing rows stay byte-identical when the table is appended; `load_report_data` adds the headline sources. The existing `test_committed_documents_equal_a_fresh_render` now covers the table too. | |
| `Makefile` | `make readme-headlines-test`. No new script: `make reports` renders the table. | |
| `README.md` | Results block regenerated, together with the hand-written S12 status row, the run steps for S12 and the resume lines and the sentence on which script writes which table (`ac325fb`). Hand-written in `1928a19`: the supported metros, the package row and the links to the study guide and the demo script. | The Status tables are copied by hand from results files and are not checked by a unit test (the README says so). The Results tables are generated and tested. |

### Studio S12 (summary; the S12 walkthrough has the details)

`docs/studio/s12_baseline.md` (the baseline and every metric definition, committed in `4a19865` before the first
run), `src/uwcopilot/studio/business_metrics/` (`baseline.py`, `coverage.py`, `cited.py`, `intervals.py`,
`scenario_run.py`, `report.py`), `scripts/eval_studio.py`, `make studio-business-metrics`,
`tests/studio/test_business_metrics_*.py`, and `results/studio_business_metrics.json`.

## 3. The current output of `scripts/resume_lines.py`

Command: `PYTHONPATH=src .venv/bin/python scripts/resume_lines.py`, run at commit `1928a19` (no changes to tracked
files) on 2026-10-26. Exit code 0. Copied verbatim.

Stdout:

```text
Projects
Brackets hold the 95% interval written in the results file (percentile bootstrap with 1,000 resamples and seed 42, unless marked Wilson). Every number comes from results/*.json. Re-run the producing scripts on your own machine before putting a number on a resume (docs/build_prompts.md, Notes for Rahul).

Fundproof Lender Desk (Underwriting Copilot): portfolio project on public datasets and SYNTHETIC documents
- Evidence-driven underwriting copilot (LangGraph agent, FastAPI) for small-business loan and commercial insurance files: links claims to a source page or a calculation; cross-document contradiction recall 0.9412 [0.8723, 1.0000] and precision 0.7500 [0.6027, 0.8868] on 60 SYNTHETIC packets; a value-of-information request policy asked 0.3838 evidence requests per case vs 1.0703 for a fixed checklist (difference -0.6865 [-0.7784, -0.5946], 370 SIMULATED cases) with a decision-error rate of 0.0270 vs 0.0135 (difference +0.0135 [+0.0000, +0.0270]); all 65 SYNTHETIC applications stopped at a human checkpoint and 0 decisions were recorded without a human.
- Credit default model on Lending Club loans (public Kaggle data): out-of-time test (train 2007-2015, validation 2016, test 2017-2018); LightGBM test AUC 0.7151 [0.7123, 0.7177] vs 0.7010 [0.6982, 0.7038] for a logistic scorecard baseline (paired bootstrap difference +0.0141 [+0.0130, +0.0152]); KS 0.3122 [0.3076, 0.3176]; PSI drift checks by issue year; SHAP reason codes; one-feature scenario sanity checks; a model card aligned to the structure of SR 26-2 (April 17, 2026; see footnote).
- Insurance frequency-severity models on freMTPL2 (public French motor data): LightGBM claim-frequency model (chosen on validation) with test mean Poisson deviance 0.4493 [0.4395, 0.4583] vs 0.4584 [0.4488, 0.4672] for a Poisson GLM; Lorenz Gini 0.3208 [0.3065, 0.3365] vs 0.2767 [0.2619, 0.2928]; expected loss (frequency x severity) observed/predicted 1.0185 [0.9569, 1.0822] on the test split.
- Document extraction with page citations on SYNTHETIC packets: rules extractor and OCR, no LLM call in the measured run; field F1 0.9936 [0.9889, 0.9971] on 60 packets; 0.9984 [0.9975, 0.9992] of extracted values supported by the printed text of their cited page; a pattern tripwire flags 16 of 60 frozen attack payloads after text normalization (0.2667 [Wilson 0.1713, 0.3901]); injected text cannot change the recommendation by construction (the policy never reads free text), enforced by 17 containment tests.
- Sanctions screening against the OFAC SDN list and OpenSanctions (CC BY-NC 4.0) for SYNTHETIC customers: recall of true matches 0.6889 [0.5476, 0.8182] at 103.8 [89.9, 118.9] alerts per 1,000 screened (rules-only matcher, threshold set on validation for a budget of 100 alerts per 1,000, 45 test true matches); false-positive rate on clean customers 0.0887 [0.0760, 0.1030].
- Model governance on public and SYNTHETIC data: a release gate in the CI workflow compares 15 results rows with a reviewed baseline and fails on a regression or a missing row (latest run: 11 pass, 0 fail, 4 not measured); drift monitoring checked on SIMULATED freMTPL2 streams: false alerts in 1 of 300 no-drift streams, claim-rate multiplier 1.25 detected in 298 of 300 streams, claim-rate multiplier 1.1 detected in 49 of 300 streams.

Fundproof Founder Studio: SYNTHETIC founder plans and cities; probabilities are SIMULATED
- Founder Studio, the borrower side of Fundproof (Next.js and FastAPI): site scoring, a Monte Carlo feasibility check, a path-to-approval search and a lender-ready package: covers 6 business types in the 5 largest US metro areas by Census Bureau population estimates (ranked among 387) plus a SYNTHETIC test city; in each real metro the lowest data-coverage share of any business type's required factors is between 0.6779 and 0.6826; on a local container, 5 of 5 Playwright end-to-end flows passed and axe-core found 0 serious or critical violations in 32 page scans.
- Monte Carlo feasibility simulator for founder plans (probabilities are SIMULATED from stated assumptions): on 2,400 SYNTHETIC validation worlds whose truth differs from the model, Brier score of P(year-2 DSCR meets the target) 0.1433 [0.1351, 0.1523] vs 0.4938 [0.4729, 0.5154] for a single most-likely forecast and 0.1557 [0.1460, 0.1654] for an in-sample base rate; P10-P90 revenue bands covered 0.6211 [0.6074, 0.6349] of months against 0.7984 expected for a correct model, so the bands are too narrow when the model is wrong.
- Provenance-locked founder narrative and lender package: 1,673 of 1,673 narrative numbers traced to the ledger claim their sentence cites (1.0000 [Wilson 0.9977, 1.0000]) across 36 SYNTHETIC scenarios (template writer, no LLM); 0 untraced numbers among 30,681 read back from the package text and PDFs; all 71 handoff cases stopped at a human checkpoint and 0 decisions were recorded without a human.
- Path-to-approval search over one- and two-lever plan changes: found a change meeting all 3 SIMULATED loan targets for 0.1026 [0.0784, 0.1306] of 536 SYNTHETIC plans that needed one; 0.0392 [0.0243, 0.0578] when every startup-program rule must also hold.
- SiteIQ site scoring from public data: on SYNTHETIC cities with a hidden true demand, Spearman correlation 0.0719 to 0.7910 across 6 business types; better than the single-factor baseline for 1, worse for 1, no clear difference for 4 (paired bootstrap 95% intervals).
- Founder Studio end-to-end evaluation against a baseline defined before the first run: 0.8482 [0.8388, 0.8587] of printed figures carry a checkable source vs 0.0251 [0.0182, 0.0320] for a plan made without the site scoring, the simulation and the ledger, on 36 SYNTHETIC scenarios; median 7.199 [6.767, 7.494] seconds from inputs to an audited PDF package (machine wall clock, not a user time); no language model is called (template narrative and memo).

Footnote: SR 26-2 = Revised Guidance on Model Risk Management (Federal Reserve SR 26-2, OCC Bulletin 2026-13, FDIC FIL-15-2026), April 17, 2026. The model card follows its structure only; applicability depends on the institution and its risk profile, and this is a demonstration, not a compliance claim.

Per-person lines (from config/contributions.yaml)
Per-person lines need the owners to fill in config/contributions.yaml: list the component ids each of you owns (git history cannot say who built what, so this script never guesses). Until then, use the shared bullets above.
```

Stderr:

```text
Left out (not measured or not claimable):
- evidence_platform / next-best-evidence top-3 accuracy: not used: results/evidence_metrics.json labels it CIRCULAR
- credit_model / stress testing: not measured: results/risk_metrics.json has no credit stress-test result (the scoring API's what-if scenarios are not evaluated there); the measured one-feature scenario sanity checks are named instead
- insurance_models / expected-loss stress tests under frequency and severity shocks: not measured: results/risk_metrics.json has no insurance stress-test result
- document_extraction / prompt-injection attack success by defense layer: not measured: results/injection_metrics.json does not exist; results/injection/injection_metrics_detector_only.json has status 'partial (detector only; the model layers were not run)' and layers L0: not measured (no model run for this layer), L1: not measured (no model run for this layer), L2: not measured (no model run for this layer)
- governance / LLM/tool-call tracing with cost and p95 per packet: results/agent_metrics.json cost: not measured: no LLM provider was used (no ANTHROPIC_API_KEY or OPENAI_API_KEY configured, or --provider none); no LLM call was made; tracing: spans created, not exported; results/docintel_metrics.json LLM calls: 0
- governance / deployment, cloud and IaC: results/deployment_metrics.json: targets local-container; results/web_metrics.json: targets local-container; a local container measurement is not a deployment, and no cloud or IaC run exists in results/
Notes:
- results/studio_evidence_metrics_ollama.json not quoted: a small local-model run (6 of 6 narratives fell back to the template)
```

Where each bullet reads its numbers:

| Bullet id | Results file and keys |
|---|---|
| `evidence_platform` | `docintel_metrics.json` (`rules.contradictions.recall` and `.precision`, `evaluation_set.n_packets`); `evidence_metrics.json` (`simulator.results.headline.voi_minus_checklist.n_requests` and `.decision_error`); `agent_metrics.json` (`deterministic_path`) |
| `credit_model` | `risk_metrics.json` (`credit.results`: `split`, `models.lightgbm.test.auc` and `.ks`, `models.logistic_scorecard.test.auc`, `comparison.test_auc_difference`, `stability`, `explainability`, `scenario_sanity_checks`) |
| `insurance_models` | `risk_metrics.json` (`insurance_frequency.results.preferred_model` and `.test_metrics`, `insurance_severity.results.expected_loss.served_pair.observed_to_predicted`) |
| `document_extraction` | `docintel_metrics.json` (`rules.fields.overall.f1`, `rules.citations.supported_all_values`); `injection/injection_metrics_detector_only.json` (`detector.recall_normalized.all`, `action_level`) |
| `kyc_screening` | `kyc_metrics.json` (`selected_operating_point`, the matching entry of `operating_points`, `data.counts_by_label_and_split`) |
| `governance` | `governance_metrics.json` (`release_gate.results.counts`, `monitoring.results.validation` and `.scenarios`) |
| `studio_platform` | `studio_metro_ranking.json`, `studio_path_metrics.json` (`settings.business_types`), `studio_metro_coverage.json` (`summary`), `web_metrics.json` (`runs.local-container`) |
| `studio_feasibility` | `studio_feasibility_metrics.json` (the `misspecified` row of `headline`, `method.coverage_reference_if_well_specified`) |
| `studio_provenance` | `studio_evidence_metrics.json` (`headline.narrative_numbers_traced_bound`, `golden.scenarios_run`); `studio_handoff_metrics.json` (`golden_founder_scenarios`, `all_cases`) |
| `studio_path` | `studio_path_metrics.json` (`headline`) |
| `studio_siteiq` | `studio_siteiq_metrics.json` (`by_business_type`) |
| `studio_business` | `studio_business_metrics.json` (`headline`, `cost_per_package`) |

Twelve bullets is too many for one resume. Pick 4-6, keep the numbers, intervals and SYNTHETIC / SIMULATED labels as
printed, and say the caveats in section 10 out loud when you quote them. The Founder Studio bullets describe an
educational planning tool: not financial, legal or lending advice; lenders make their own decisions.

## 4. Template by template: what changed and why

Prompt 8 gives six templates. None could be filled in as written without claiming something that was not measured,
so each one was changed. The rule the code follows: a clause with a rate or score is printed only when the results
file holds the value and its interval (counts, and the group means next to a paired difference, are printed as
recorded); otherwise the clause is dropped and stderr says why. No bullet starts with "Built" or "Developed":
the bullets describe the project, not who typed the code (section 5).

### 4.1 Evidence platform (template 1)

- **Template:** "Built an evidence-driven underwriting platform (LangGraph, FastAPI) that links every conclusion to a
  source page or calculation, detects cross-document contradictions (recall {crec} [CI]) and ranks the next evidence
  to request (top-3 accuracy {nbe3}); every decline goes to a human."
- **Kept:** contradiction recall 0.9412 [0.8723, 1.0000], now with precision 0.7500 [0.6027, 0.8868] next to it, on
  60 SYNTHETIC packets (`docintel_metrics.json`, `rules.contradictions`). Recall alone hides false alarms.
- **Dropped: top-3 accuracy.** `evidence_metrics.json` records a top-3 agreement of 1.0 (26 of 26 eligible problems,
  `packets.results.modes.oracle.next_best_evidence_agreement_circular.top_3`) and labels it CIRCULAR: "the generator's
  resolving_request and the engine's request rules were written by the same author, so agreement mainly shows that
  two hand-written tables match." The label names the innovation S5 simulator as the non-circular evaluation.
- **Substitute:** the simulator result, with its cost. On 370 SIMULATED cases the value-of-information policy asked
  0.3838 requests per case against 1.0703 for a fixed checklist, difference -0.6865 [-0.7784, -0.5946]; its
  decision-error rate was 0.0270 against 0.0135, difference +0.0135 [+0.0000, +0.0270]
  (`simulator.results.headline.voi_minus_checklist`). Fewer requests, and slightly more decision errors.
- **Changed:** "links every conclusion" became "links claims to a source page or a calculation" (a context clause):
  no results file measures "every conclusion". "Every decline goes to a human" became a measured, wider statement: all
  65 SYNTHETIC applications stopped at a human checkpoint and 0 decisions were recorded without a human
  (`agent_metrics.json`, `deterministic_path`).

### 4.2 Credit model (template 2)

- **Template:** "Credit default model on Lending Club with out-of-time testing: LightGBM AUC {auc} vs {base}
  logistic baseline (paired bootstrap), KS {ks}, PSI drift monitoring, SHAP reason codes, stress testing, and a
  model card aligned to SR 26-2 (the 2026 interagency model-risk guidance)."
- **Kept:** the out-of-time split read from the file's issue years (train 2007-2015, validation 2016, test
  2017-2018); test AUC 0.7151 [0.7123, 0.7177] vs 0.7010 [0.6982, 0.7038] for the logistic scorecard, paired bootstrap
  difference +0.0141 [+0.0130, +0.0152]; KS 0.3122 [0.3076, 0.3176]; SHAP reason codes (named because
  `credit.results.explainability.reason_codes` exists).
- **Dropped: stress testing.** `risk_metrics.json` has no credit stress-test result. The one-feature scenario sanity
  checks that are in the file (`credit.results.scenario_sanity_checks`) are named instead.
- **Changed:** "PSI drift monitoring" became "PSI drift checks by issue year". The PSI is computed once per issue year
  in the evaluation (`stability.score_psi_by_vintage`); ongoing monitoring of the credit model is not measured
  (`governance_metrics.json`, section `monitoring`, `credit_model`). "Aligned to SR 26-2" became "aligned to the
  structure of SR 26-2 (April 17, 2026; see footnote)", and the footnote names the guidance and says applicability
  depends on the institution and its risk profile and that this is not a compliance claim (section 11).

### 4.3 Insurance models (template 3)

- **Template:** "Insurance frequency-severity models (freMTPL2): Poisson deviance {dev}, Gini {gini}; expected-loss
  stress tests under frequency and severity shocks."
- **Kept, with a comparison:** the LightGBM claim-frequency model was chosen on validation; test mean Poisson deviance
  0.4493 [0.4395, 0.4583] vs 0.4584 [0.4488, 0.4672] for the Poisson GLM, and Lorenz Gini 0.3208 [0.3065, 0.3365] vs
  0.2767 [0.2619, 0.2928] (`insurance_frequency.results.test_metrics`). A number with no comparison says little.
- **Dropped: expected-loss stress tests.** `risk_metrics.json` has no insurance stress-test result.
- **Substitute:** the measured expected-loss check: observed / predicted 1.0185 [0.9569, 1.0822] on the test split
  for the pair the API serves (`insurance_severity.results.expected_loss.served_pair`).

### 4.4 Document extraction and prompt injection (template 4)

- **Template:** "Document extraction with page citations: field F1 {f1} [CI], citation accuracy {cite};
  prompt-injection attack success measured by defense layer on {n} frozen payloads (L0 {asr_l0}, L1 {asr_l1}, L2
  {asr_l2}, Wilson 95% CIs, with the not-found rate under attack, from results/injection_metrics.json) and
  decision-level impact 0 by construction, enforced by a test."
- **Kept:** field F1 0.9936 [0.9889, 0.9971] on 60 packets, with the context "rules extractor and OCR, no LLM call in
  the measured run" (written only because `docintel_metrics.json` records no provider and 0 LLM calls).
- **Renamed:** "citation accuracy" became what the file measures: 0.9984 [0.9975, 0.9992] of extracted values are
  supported by the printed text of their cited page (`rules.citations.supported_all_values`).
- **Dropped: attack success by layer.** It needs a full scored run against a model. `results/injection_metrics.json`
  does not exist; the detector-only file has status "partial (detector only; the model layers were not run)" and
  records L0, L1 and L2 as "not measured (no model run for this layer)". The only injection run against a model is
  the local Ollama smoke run, 3 of 240 planned calls (`injection/smoke/injection_metrics_smoke.json`,
  `run.n_calls_made` and `run.n_planned_calls`), which is never quoted as a rate. The code for the full clause exists
  and is tested: when a run with status `measured` scores every layer, the bullet prints L0, L1 and L2 with their
  intervals and not-found rates.
- **Substitute:** what was measured without a model. The pattern tripwire flags 16 of 60 frozen attack payloads after
  text normalization, 0.2667 [Wilson 0.1713, 0.3901] (`detector.recall_normalized.all`). On raw text it flags 12 of 60
  (`detector.recall_raw.all`), which is the number the README status row quotes; say which one you mean. Decision-level
  containment is quoted as what it is: injected text cannot change the recommendation by construction, enforced by the
  17 containment tests listed in `action_level.tests` (the file's status says "enforced by
  tests/security/test_containment.py; not an empirical rate").
- **Never:** "100% of injected prompt-injection attempts blocked". Innovation S2 retired that template, and the
  wording guard rejects "100%" and "blocked".

### 4.5 Sanctions screening (template 5)

- **Template:** "Sanctions/KYC screening over OFAC + OpenSanctions: recall {recall} at {alerts} alerts per 1,000
  customers."
- **Kept, with context:** recall of true matches 0.6889 [0.5476, 0.8182] at 103.8 [89.9, 118.9] alerts per 1,000
  screened, rules-only matcher, threshold set on validation for a budget of 100 alerts per 1,000, 45 test true matches
  (`kyc_metrics.json`). The count explains why the recall interval is wide. On the test split the alert rate came out
  above the validation budget; the interval includes 100.
- **Added:** false-positive rate on clean customers 0.0887 [0.0760, 0.1030]. "OpenSanctions (CC BY-NC 4.0)" carries
  the license, as the dataset's terms ask for attribution.
- **Changed:** "Sanctions/KYC" became "Sanctions screening": what is measured is name screening of SYNTHETIC
  customers. The adverse-media memo needs an LLM and is not measured (`kyc_metrics.json`, as the README table shows).

### 4.6 Governance (template 6)

- **Template:** "Traced every LLM/tool call: ${cost} and {p95}s p95 per packet; release gate in CI blocks metric
  regressions; deployed on {cloud} with {iac}."
- **Kept, reworded:** the release gate. "A release gate in the CI workflow compares 15 results rows with a reviewed
  baseline and fails on a regression or a missing row (latest run: 11 pass, 0 fail, 4 not measured)"
  (`governance_metrics.json`, `release_gate.results.counts`). "Fails" instead of "blocks", because the wording guard
  rejects "block" (the retired injection wording).
- **Added:** the drift monitor's validation on SIMULATED freMTPL2 streams: false alerts in 1 of 300 no-drift streams,
  a claim-rate multiplier of 1.25 detected in 298 of 300 streams and 1.1 in 49 of 300
  (`monitoring.results.validation`). The weak case (1.1) is printed too.
- **Dropped: tracing, cost and p95 per packet.** `agent_metrics.json` records the cost as "not measured: no LLM
  provider was used ..." and tracing as "spans created, not exported"; `docintel_metrics.json` records 0 LLM calls.
  "Traced every call" would be false: spans exist in code but were never exported, and nothing was costed.
- **Dropped: deployment, cloud and IaC.** `deployment_metrics.json` and `web_metrics.json` list only
  `local-container` targets. A local container is not a deployment, the Space is not deployed, and no cloud or
  infrastructure-as-code run exists. The container's request latency (in `deployment_metrics.json`, quoted in the
  README table) is a local measurement of HTTP requests, not a per-packet latency, so it is not quoted here. If a
  non-local target ever appears in those files, stderr asks for a line written by hand after checking it.

### 4.7 Founder Studio bullets (Prompt S12)

Prompt S12 asks for Studio resume lines with numbers only from `results/*.json`. The six Studio bullets follow the
same rules as the lender ones and each names SYNTHETIC or SIMULATED in its text. Things they print that are not
flattering, on purpose: SiteIQ beats its single-factor baseline for 1 business type, is worse for 1 and shows no clear
difference for 4; the path search finds a change for 0.1026 [0.0784, 0.1306] of 536 SYNTHETIC plans that needed one;
the revenue bands cover 0.6211 [0.6074, 0.6349] of months against 0.7984 expected for a correct model, so they are too
narrow when the model is wrong. The S12 business bullet (added in `d13225b`) quotes rule S (section 6) and says the time is
machine wall clock, not a user time. `results/studio_evidence_metrics_ollama.json` is deliberately not quoted: a small
local-model run in which 6 of 6 narratives fell back to the template (stderr note).

### 4.8 The wording guard

`FORBIDDEN_WORDING` in `text.py` is a list of regular expressions, each with its reason: "100%" and "block" /
"blocked" / "blocks" (retired injection wording), "deploy", "production", "real-world", "savings", "compliant" / "compliance with" /
"complies", "SR 11-7" (superseded by SR 26-2), cloud and infrastructure-as-code names, "$" (no cost was measured),
"p95", and hosted-LLM names (no hosted LLM ran). `BulletDraft.finish()` runs it on every bullet and raises
`WordingError` instead of quietly editing the text, so a bad template fails the script and the tests.

## 5. Per-person lines: why `config/contributions.yaml` and not git history

Prompt S12 asks for "separate, accurate bullets for each person based on git history of who built what". Git history
cannot answer that here. Claude Code is the implementer, so almost every commit is authored by "Claude". Rahul Vajja
and Bhavani Adula wrote the specs, build prompts and evaluation protocol and review the work, and none of that shows up
as commit authorship. A script that counted commits or lines would either credit nearly everything to the implementer
or invent a split between the owners. Both would be inaccurate.

So the script never infers ownership. `config/contributions.yaml` lists component ids per owner (one id per bullet,
for example `evidence_platform` or `studio_feasibility`). The rules in `contributions.py`:
- a person gets a line only for a component listed under their name, and the line is the shared bullet text, not a
  rewritten one;
- a component both owners list is marked `[joint with ...]`;
- a listed component that has no bullet (its metric is not measured) goes to stderr with the reason;
- an unknown id or a bad shape stops the script with exit code 2;
- an empty or missing file prints the fill-in note and no per-person bullet, as in section 3.

The file ships empty on purpose (`owners: Rahul Vajja: [], Bhavani Adula: []`), and a test checks that. Only the owners
can fill it in. Until then the README's "How it was built" sentence is the accurate description. Once the file is
filled in, the script prints a line that repeats it (`ROLE_NOTE` in `contributions.py`) above the per-person bullets.
No component in this repo is attributed to one owner.

## 6. Studio S12 evaluation (summary)

Founder Studio is an educational planning tool: not financial, legal or lending advice; lenders make their own
decisions. Every figure below is from SYNTHETIC scenarios, and the probabilities inside the packages are SIMULATED.

S12 ran `scripts/eval_studio.py` on the 36 golden SYNTHETIC founder scenarios (6 business types x (5 real metros + the
SYNTHETIC metro); the prompt's 30 are the real-metro subset, reported separately in the file), at commit `e68b88d`
with a clean tree, 1,000 simulated paths per plan, and the full OFAC SDN + OpenSanctions lists for the handoff check
(`results/studio_business_metrics.json`, `provenance` and `configuration`). The baseline B0 was written down in
[s12_baseline.md](studio/s12_baseline.md) and committed (`4a19865`) before the S12 script existed (`1958f6e`) and
before any S12 result, and the file has not changed since. B0 is a plan made from the same SYNTHETIC inputs without
SiteIQ, the simulation and the ledger. It is a construction, not a sample of real plans.

Results (`headline`, and `time_to_complete_package.complete_packages` for the package count): 36 of 36 complete
packages (Wilson [0.9036, 1.0]); median 7.199
[6.767, 7.494] seconds from inputs to an audited PDF, a wall clock on a shared machine, not a user time (nobody's time
was observed); printed figures with a checkable source (rule S) 0.8482 [0.8388, 0.8587] (12,222 of 14,410) against
0.0251 [0.0182, 0.0320] (33 of 1,316) for B0, difference 0.8231 [0.8118, 0.8352]. The file itself says most of that gap
follows from the definitions (`evidence_coverage.reading`): model estimates and calculations count for Studio because
the ledger records their run or formula. Under rule E, which counts only a document page, a public-data row or a
program or target section, it is 0.0570 [0.0545, 0.0597] against 0.0251 [0.0182, 0.0320], difference 0.0319 [0.0259,
0.0380]. Language-model cost is 0 USD per package by construction (template narrative and memo); compute cost and a
usability test with real people are not measured (the file records why). Intervals are percentile bootstrap over
scenarios (1,000 resamples, seed 42), unless marked Wilson. Everything else, including "Why these five cities?" and
"How would you add a sixth?", is in [walkthrough_s12.md](studio/walkthrough_s12.md).

## 7. How the README headline table works

`make reports` runs `scripts/render_reports.py`, which writes the README block between `<!-- RESULTS:START -->` and
`<!-- RESULTS:END -->`. The Phase 1, 2 and 4 rows come first, unchanged. Then `headline_section()` adds an introduction
and one table per section (Lender Desk, Governance, Founder Studio, Web app and deployment), with the columns
Component, Metric, Value [95% CI] and Source.

For each row built from a `Spec` (every row but the Studio data-coverage row, which `coverage_row()` builds with its own
checks), `spec_row()` looks up the row's JSON paths in its file:
- file missing: "not measured (no results file)";
- path missing: "not measured (no `<path>` in the file)", as for the Hugging Face Space rows of `web_metrics.json` and
  `deployment_metrics.json`;
- the file marks the value not measured: "not measured (<the file's reason>)", for example "not measured (no LLM
  key)" for RAG faithfulness in `rag_metrics.json`. `short_reason` cuts a longer reason at its first clause: the
  credit model's drift-monitor row in `governance_metrics.json` keeps only the first sentence of the recorded reason;
- otherwise the row's format function writes the value.

`interval_text()` chooses the interval the file records, in this order:
1. the bootstrap interval, unless it has zero width or the file flags it as degenerate or too narrow;
2. then the exact Clopper-Pearson interval, labelled "exact", for example the monitor's false alerts, "0.0033 exact
   [0.0001, 0.0184] (1/300)" (`governance_metrics.json`);
3. then a Wilson interval, labelled "Wilson", for example "1.0000 [0.9036, 1.0000] (36/36, Wilson)" for the S6
   handoff (`studio_handoff_metrics.json`);
4. otherwise the value with an honest note, for example "1.0000 (n=60, zero-width bootstrap interval, no other
   interval recorded)" for the agent's required escalations (`agent_metrics.json`).

Runs on a handful of cases print counts only, never a rate: "smoke only: 3 of 240 planned calls made, no rate quoted"
for the injection smoke run, and "counts only" for the local-model narrative run.

The tests check four things per row, on hand-made results and on the committed ones: every number in the row appears
in that row's own source file (a number that exists only in another results file fails); a value its file marks
"not measured" is never printed as a number; a "not measured" reason comes from the file; and a row whose data the
file labels SYNTHETIC or SIMULATED says so. A separate test checks that the committed README equals a fresh render.

## 8. Design choices

- **Read, never compute a metric.** The resume script and the README table only read `results/*.json`. The most
  they do with the values is count or pick (the length of a list, the sum of two counts, the lowest and highest of a
  set, how many intervals lie above or below zero). A missing value is "not measured" with the file's reason, never a
  guess.
- **Drop a clause, not just a bullet, and say why.** Prompt 8 says to omit a bullet whose metric was not measured.
  The script goes one step further: a missing secondary clause (for example attack success by layer) is dropped while
  the measured clauses stay, and every drop is listed on stderr. A missing core metric drops the whole bullet.
- **Quote the cost next to the gain.** The evidence bullet prints the higher decision-error rate next to the fewer
  requests; the governance bullet prints the 49 of 300 detection next to the 298 of 300; the Studio bullets print the
  weak SiteIQ and path results.
- **Fail loudly on bad wording.** The guard raises an error instead of rewriting a sentence, so a template that drifts
  into a forbidden claim breaks the tests.
- **Claims about files check the files.** "A model card aligned to the structure of SR 26-2" is printed only when the
  model card exists; "a release gate in the CI workflow" only when `.github/workflows/ci.yml` contains
  `make release-gate`.
- **Labels inside the sentence.** SYNTHETIC, SIMULATED and "on a local container" are part of each bullet, so a bullet
  copied on its own keeps them.
- **Fixed precision.** 4 decimals for rates and scores, as the README prints them (1 for alerts per 1,000, 3 for
  seconds), so no value is rounded to a nicer figure.
- **No authorship verbs, no inferred ownership.** Bullets describe the project; per-person lines come only from a
  file the owners write (section 5).
- **The README table checks each number against its own file.** That is stricter than "somewhere in results/" and
  catches a number pasted from the wrong file.
- **The existing README rows stay byte-identical.** The table is appended after them, so earlier phases' tested
  output is not disturbed.
- **The S12 baseline was written before measuring.** Its commit (`4a19865`) precedes the S12 script (`1958f6e`) and
  the S12 results (`b44b581`), and the file has not changed since, so the committed results could not shape the
  definitions.

## 9. How it was built, and checks

The work ran as three branches, merged as `p8/resume-lines` (`cab3597`), `p8/eval-studio` (`5175e3d`) and
`p8/readme-table` (`e68b88d`):
1. Resume lines: the results reader and text helpers (`8a92a73`), the Lender Desk bullets (`8feefe4`), the Studio
   bullets (`04a2213`), the report with omissions on stderr and the fixture tests (`cd39d7b`), the per-person lines
   and `make resume-lines` (`b35ce48`).
2. S12: the definitions first (`4a19865`), then the baseline and coverage rules (`e226b6c`), intervals and cited
   results (`fb55f4c`), the script and Make target (`1958f6e`), the first-handoff split (`9cd07fd`), shared test
   helpers (`00b0cfe`).
3. README table: the module (`19dc7ad`), rendering at the end of the RESULTS block (`e561ed1`), the Make target
   (`b13ee29`).

After the merges, S12 was measured at the merge commit `e68b88d` with a clean tree (`b44b581`); the S12 rows were
added to the headline table (`83f9f80`) and the S12 bullet to the resume lines, together with the "after text
normalization" wording of the tripwire clause (`d13225b`); the README was regenerated and gained the S12 status row
and the run steps (`ac325fb`); a Makefile comment was fixed (`fc3075e`); and the README gained the supported metros,
the links and the package row (`1928a19`).

These are checks, not metrics, so they are not in `results/*.json`.

| Check | Result |
|---|---|
| Offline tests at `1928a19` on 2026-10-26: `pytest -o addopts='' -m 'not integration'` on `tests/resume`, `tests/governance/test_readme_headlines.py` and `tests/test_render_reports.py` | 95 passed |
| `scripts/resume_lines.py` at `1928a19` | Exit 0; 6 Lender Desk and 6 Founder Studio bullets, 6 omissions and 1 note on stderr (section 3) |
| GitHub Actions on the Phase 8 commits | Not checked in this walkthrough; it is part of step 4 |

## 10. Limits, open items and the status of step 4

**Step 4 is not done.** Final CI, the `v1.0` tag and the pull request wait for the owners. Before they do it:
- run `make all` (or the producing targets) on their own machine and check that the numbers still match, as the build
  plan's "Notes for Rahul" says and the script repeats;
- fill in `config/contributions.yaml` if they want per-person lines;
- check that the documents the README links exist: `docs/INTERVIEW_GUIDE.md`, `docs/demo_script.md` and
  `docs/studio/walkthrough_s12.md` were not yet committed at `1928a19`, when this walkthrough was written;
- check that CI is green on the final commit, then tag and open the PR with a summary and a results table.

**Limits of the resume lines.**
- **The number test for bullets is folder-wide.** `test_bullets_use_only_numbers_from_the_results_folder` checks that
  each number appears somewhere in the results folder, not in the bullet's own source file (each `Bullet` records its
  sources, but the test does not use them). A coincidental match in another file would pass. It also accepts every
  whole number from 0 to 10 and the length of any list or mapping in the folder (`SMALL_COUNTS` and `json_numbers` in
  `tests/resume/resume_fixtures.py`), so small counts such as "better for 1" are not traced to a key. The README
  table's test is per file.
- **The wording guard only knows its list.** It catches known bad phrases, not a misleading sentence built from
  allowed words. Caveats the bullets do not carry, which you must say when you quote them:
  - the provenance bullet's 1,673 of 1,673 traced narrative numbers is by construction for the template writer and
    was measured before the S4 review fixes; after them, the 6 SYNTHETIC-city scenarios gave 285 of 285
    (`studio_evidence_metrics_synthetic.json`) and the 30 real-city scenarios were not re-run (README status row, S4
    walkthrough);
  - "0 untraced numbers among 30,681" is partly by construction too: every printed number is a claim by design, so
    the audit guards against regressions (S6 walkthrough);
  - the S12 rule S gap (0.8482 against 0.0251) is mostly a consequence of the definitions; rule E gives 0.0570
    against 0.0251 (section 6).
- **Provenance of the results files.** Many results files the bullets read were written from a working tree with
  uncommitted changes (README, Limitations), so their commit alone does not pin the code. Re-run before quoting.
- **Hard-coded key paths.** If a producing script renames a key, the clause is dropped with a reason rather than
  crashing. That is safe, but a bullet can quietly get shorter; read stderr after any results change.
- **Not measured, so not in any bullet:** prompt-injection attack success by layer, every LLM quality, cost and latency
  metric, tracing, per-packet p95, deployment, cloud and IaC, credit and insurance stress tests, compute cost per
  package, and a usability test with real people.

**Limits of the README.**
- The Status tables are copied by hand and are not checked by a unit test (the README says so); only the Results
  tables are generated and tested.
- The README status row quotes the tripwire on raw text (12 of 60) and the resume line quotes it after normalization
  (16 of 60). Both come from `injection/injection_metrics_detector_only.json`.
- `short_reason` cuts a "not measured" reason at its first clause to keep cells short; the full reason is in the file.
- No live URL and no demo GIF (Prompt S12 asked for both): the Space is not deployed. The screenshots were taken on a
  local container.

## 11. Regulatory references

Only one regulatory reference appears in the Phase 8 output: the credit bullet's "aligned to the structure of SR 26-2"
and its footnote.

- **SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026**, "Revised Guidance on Model Risk Management", issued jointly
  by the Federal Reserve, OCC and FDIC on **April 17, 2026**; it supersedes SR 11-7 (2011) and SR 21-8
  ([Federal Reserve](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm),
  [OCC](https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html),
  [FDIC](https://www.fdic.gov/news/financial-institution-letters/2026/agencies-revise-interagency-model-risk-management-guidance)).
  As summarized in the build plan (checked against primary sources on 2026-10-10), it is risk-based and proportionate,
  it is "expected to be most relevant to banking organizations with over $30 billion in total assets", and its
  footnote 3 places generative and agentic AI outside its scope, which is why the resume lines apply it only to the
  credit model card and never to the agent or the LLM code.

Applicability depends on the institution and its risk profile. This project is a demonstration, not a compliance
program: the model card follows the structure of the guidance, and nothing claims compliance with it. The wording guard
rejects "compliant" and the superseded "SR 11-7".

## 12. Likely interview questions

1. **How do you know every number in a resume bullet is real?**
   The script never computes a metric. Each clause reads one entry of a results file and prints the value with the
   interval written next to it, at 4 decimals; the most it does is count or pick (a list length, the lowest and
   highest of a set). If the entry is missing, has no interval, or the file says "not measured", the clause is dropped
   and stderr says why. A test collects every number in every bullet and checks that it appears in the results
   folder, on a fixture folder and on the committed `results/`. Two honest gaps: that test is folder-wide, not per
   source file, and it lets any whole number from 0 to 10 through. And the script itself tells you to re-run the
   producing scripts on your own machine before you put a number on a resume.

2. **Why don't you quote the next-best-evidence top-3 accuracy?**
   Because it is circular. `results/evidence_metrics.json` records top-3 agreement of 1.0 (26 of 26 eligible
   problems) but labels it CIRCULAR: the generator's correct answer and the engine's rules were written by the same
   author, so agreement mostly shows two hand-written tables match. The non-circular test is the S5 simulator, so the
   bullet quotes it: 0.3838 against 1.0703 requests per case, difference -0.6865 [-0.7784, -0.5946] on 370 SIMULATED
   cases. It also quotes the cost: a decision-error rate of 0.0270 against 0.0135, difference +0.0135 [+0.0000,
   +0.0270]. Fewer requests, slightly more errors, and the interval for the extra errors reaches 0.

3. **What do you say about prompt injection instead of "100% blocked"?**
   A finite set of payloads can never show 100% protection, so that wording was retired. The honest version is attack
   success per defense layer with intervals, and that needs a full run against a model, which does not exist: the
   detector-only file says the model layers were not run, and the Ollama smoke run made 3 of 240 planned calls. So
   the bullet leaves that clause out with the reason, and quotes what was measured without a model: the tripwire flags
   16 of 60 frozen payloads after text normalization, 0.2667 [Wilson 0.1713, 0.3901], and the decision cannot be
   changed by injected text by construction, enforced by 17 containment tests. That last part is a design property
   checked by tests, not a measured rate.

4. **The template mentions cost, p95, a cloud and infrastructure as code. Where did they go?**
   None of them was measured. `agent_metrics.json` says the cost was not measured because no LLM provider was used,
   and tracing spans were created but not exported. The deployment and web results files only list a local container,
   and a local container is not a deployment; the Hugging Face Space has not been deployed. So those clauses are on
   stderr with their reasons, and the wording guard would reject "deploy", "cloud", "$" and "p95" anyway. The
   governance bullet quotes what was measured: the release gate (15 rows; 11 pass, 0 fail, 4 not measured in the
   latest run) and the drift monitor's validation on SIMULATED streams.

5. **Why do per-person lines come from a YAML file and not git history?**
   Git history cannot say who built what here. Claude Code is the implementer, so almost every commit is authored by
   "Claude", and the owners' work (specs, build prompts, evaluation protocol, reviews) is not commit authorship.
   Counting commits would either credit the implementer with everything or invent a split. So the owners list their
   components in `config/contributions.yaml`, and the script prints the shared bullet for each listed component, marks
   joint ones, and refuses unknown ids. The file is empty until the owners fill it in, and a test checks that.

6. **What happened to the stress tests in the credit and insurance templates?**
   There is no stress-test result for either model in `risk_metrics.json`, so the clause is dropped and stderr says
   so. The credit bullet names the one-feature scenario sanity checks that are in the file. The insurance bullet quotes
   a measured check instead: expected loss observed / predicted 1.0185 [0.9569, 1.0822] on the test split, which says
   the frequency-times-severity pair is about right on average, with an interval that includes 1.

7. **Why "aligned to the structure of SR 26-2", with a footnote?**
   SR 26-2 is the April 17, 2026 interagency guidance on model risk management that replaced SR 11-7. It is guidance,
   it is risk-based, and its applicability depends on the institution and its risk profile; the build plan notes it
   is expected to be most relevant to banking organizations with over $30 billion in total assets. A portfolio project
   can follow its structure in a model card but cannot be compliant with it. The footnote names the documents and the
   date and says this. The guidance also puts generative and agentic AI out of scope, so the bullet applies it only to
   the credit model card.

8. **Several bullets show weak results. Why keep them?**
   Because they are the measured results, and leaving them out would make the rest less believable. Sanctions recall
   is 0.6889 [0.5476, 0.8182] on only 45 test true matches. SiteIQ beats its single-factor baseline for 1 of 6
   business types and is worse for 1. The path search finds a change for 0.1026 of the SYNTHETIC plans that need one.
   The feasibility bands cover 0.6211 of months when the model is wrong, against 0.7984 expected for a correct model.
   In an interview, a weak result you can explain is worth more than a perfect number nobody believes.

9. **How does the README headline table pick an interval, and what do its tests check?**
   It uses the interval the file records. The bootstrap interval comes first, unless it has zero width (as when every
   case passes) or the file flags it as degenerate or too narrow; then the exact Clopper-Pearson interval, marked
   "exact"; then a Wilson interval, marked "Wilson"; otherwise the value with a note that no other interval is
   recorded. Smoke runs show counts only. The tests check
   that every number in a row appears in that row's own source file, that nothing marked "not measured" is printed as
   a number, that "not measured" reasons come from the file, and that SYNTHETIC or SIMULATED rows say so. Another test
   checks that the committed README equals a fresh render.

10. **What does the S12 result 0.8482 against 0.0251 mean, and how do you quote it honestly?**
    It is the share of printed figures that carry a checkable source: the Studio package against a baseline plan B0
    made from the same SYNTHETIC inputs without SiteIQ, the simulation and the ledger, on 36 SYNTHETIC scenarios.
    B0 was defined in writing before the first run, and it is a construction, not real founders' plans. The results
    file itself says most of the gap comes from the definitions: Studio gets credit for model estimates and
    calculations because the ledger records them. Under the stricter rule E (a document page, a public-data row or a
    program or target section only) it is 0.0570 against 0.0251. So say: the ledger makes most figures traceable to a
    run, a formula or a source, and the gain in figures backed by outside evidence is much smaller. The 7.199-second median is machine
    time from inputs to an audited PDF, not a user's time.

# Phase 1 walkthrough: risk models, stress-test interface, model-risk pack

> Rendered by `scripts/render_reports.py` from `docs/templates/walkthrough_phase1.md`. Every result below is
> filled in from `results/risk_metrics.json` when `make reports` runs; a result that does not exist yet reads
> "not measured". Edit the template, not this file.

Phase 1 builds one reusable risk-modelling framework and applies it to three problems: credit default
(Lending Club), motor insurance claim frequency and severity (freMTPL2), and a fairness demonstration (UCI German
Credit). It adds a scenario (what-if) interface used later by the Phase 4 stress tests, a scoring API, and a
model-risk pack (model cards and a validation report) generated from the results file.

## Honest status

- **Insurance (freMTPL2): measured** on the real public data. Model bundle `freq-lgbm v4 / sev-lgbm v4 @ bbedb8d30fa8`,
  results generated 2026-10-11T00:41:27+00:00.
- **Fairness demo (German Credit): measured** on the real public data, with a documented data-quality problem
  in the dataset's own documentation (see Q&A 10 and the validation report).
- **Credit (Lending Club): measured** on the real public data; see the credit model card.
- Nothing here has been independently validated, and nothing claims compliance with any regulation.

## How to run it

```bash
make phase1   # train-insurance, fairness, train-credit (writes "not measured" without the Kaggle zip), reports
make serve    # scoring API on http://127.0.0.1:8000 (interactive docs at /docs)
make test     # every test runs on synthetic data, so CI needs no downloads
```

Example request (the feature values are an illustrative policy, not data from any person):

```bash
curl -s localhost:8000/score -H 'content-type: application/json' -d '{
  "product": "insurance_frequency",
  "features": {"VehPower": 6, "VehAge": 2, "DrivAge": 45, "BonusMalus": 50, "Density": 1200,
               "Area": "C", "VehGas": "Regular", "VehBrand": "B12", "Region": "R24"},
  "overrides": {"BonusMalus": 80}}'
```

The response holds the baseline prediction (claims per policy-year), its risk band, the top-4 reason codes, the
model version (here insurance_frequency_lgbm v4 (MLflow registry)) and a `scenario` block with the baseline and the
scenario side by side. `GET /health` lists which models are loaded, their versions and their risk-band cut-offs. Every
input is checked against the training data: a missing feature, an out-of-range value, an unseen category or a
fraction of a whole-number feature gets HTTP 422 naming the problem.

## Results at a glance

| Model | Test metric | Value [95% bootstrap CI] |
|---|---|---|
| Frequency, Poisson GLM | Mean Poisson deviance | 0.4584 [0.4488, 0.4672] |
| Frequency, LightGBM | Mean Poisson deviance | 0.4493 [0.4395, 0.4583] |
| Frequency, LightGBM minus GLM | Paired difference | -0.0091 [-0.0107, -0.0075] |
| Frequency, GLM / LightGBM | Lorenz Gini | 0.2767 [0.2619, 0.2928] / 0.3208 [0.3065, 0.3365] |
| Severity, Gamma GLM / LightGBM | Mean Gamma deviance | 1.1678 [1.0855, 1.2565] / 1.1655 [1.0822, 1.2574] |
| Severity, LightGBM minus GLM | Paired difference | -0.0024 [-0.0135, 0.0089] |
| Expected loss, GLM pair / LightGBM pair | Observed / predicted | 1.0187 [0.9570, 1.0812] / 1.0437 [0.9808, 1.1093] |
| Expected loss, served pair (LightGBM frequency x GLM severity) | Observed / predicted | 1.0185 [0.9569, 1.0822] |
| Frequency, split grouped on rating features | LightGBM minus GLM deviance | -0.0108 [-0.0123, -0.0093] |
| German Credit, logistic / LightGBM | AUC | 0.793 [0.736, 0.847] / 0.776 [0.720, 0.833] |
| Credit PD (Lending Club) | AUC, KS, Gini, Brier, ECE | not measured (see status) |

Bootstrap settings: 1,000 resamples, seed
42, percentile intervals; paired differences resample the same rows for both
models.

## Every file, what it does and why

### Framework (`src/uwcopilot/risk/`)

| File | What | Why it is built this way |
|---|---|---|
| `metrics.py` | AUC, KS, Gini, Brier, ECE, calibration table, Poisson and Gamma deviance, Lorenz curve and Gini | Written out plainly and tested against scikit-learn and SciPy, so every metric can be explained line by line. NaN input raises instead of producing a silent number. |
| `bootstrap.py` | `bootstrap_ci` and `paired_bootstrap_diff` (percentile method) | Ground rule 1 asks for CIs on headline metrics. Paired resampling answers "is model A better than B on the same cases?", which two separate intervals cannot. |
| `stability.py` | PSI for numeric and categorical variables, bands and flag | Quantile bins of the reference period, a floor for empty bins, NaN as its own bin. Thresholds 0.10 / 0.25 are the usual industry convention and are constants in one place. |
| `fairness.py` | Selection rates, adverse impact ratio, equal-opportunity gap | Small functions on arrays, reused by the German Credit demo and reusable for any model with group labels. |
| `scenario.py` | Feature ranges learned from training data, override and input validation, `monotonic_check` | Out-of-range overrides are rejected, never clipped (Q&A 10); the base applicant or policy gets the same checks, plus "no missing key" and "null only where training had missing values". The monotonic check reports the share of violations instead of asserting zero. |
| `explain.py` | SHAP for tree models, reason codes, global importance, a reusable explainer | Reason codes are the top contributions in one direction, mapped to plain language; an unmapped feature gets a visible generic reason. |
| `bands.py` | Risk-band cut-offs from training-score quantiles | Cut-offs are computed, stored with the model and shown by the API, never typed by hand. |
| `results.py` | Writes `results/*.json` sections with provenance (git commit, dirty flag, package versions, dataset SHA-256) | Every number in every document traces back to one file and one run. "not measured" is a first-class value. |
| `tracking.py` | MLflow helpers (local SQLite store, telemetry off) | Runs, metrics, artifacts and registered model versions are kept locally, so they can be inspected with `mlflow ui`. |
| `data/fremtpl2.py` | Load, clean, link and split freMTPL2; synthetic generator for tests | Every cleaning step counts what it changed and reports it (see `docs/insurance_data_notes.md`). |
| `data/german_credit.py` | Parse UCI German Credit, derive age groups and sex mappings | Keeps both the documented and the corrected sex coding so the problem stays visible. |
| `data/lending_club.py` | Stream the Kaggle zip in chunks, map loan status to the target, data-quality report | Fails loudly on a missing column or an unknown status instead of guessing. |
| `leakage.py` | One decision and reason for each Lending Club column; `assert_no_leakage` | The guard runs on raw names, encoded names and booster inputs, at fit time and at score time. `docs/leakage.md` holds the same table and a test keeps them in sync. |
| `insurance.py` | Poisson / Gamma GLMs, LightGBM models, metrics, expected loss, SHAP, contributions, save / load | GLMs are banded so coefficients read as relativities; GLM and SHAP contributions both add up to the log prediction, so either model can give reason codes. |
| `credit.py` | Scorecard and LightGBM, out-of-time split, champion/challenger rule, PSI by vintage, reason codes, `score()` | The model choice is fixed before the test set is scored; the scorecard wins unless LightGBM is clearly better. |
| `fairness_demo.py` | The German Credit fairness pipeline: models, approval threshold, AIR, equal opportunity, proxy check | A separate, clearly labelled demonstration because Lending Club has no protected attributes. |
| `service.py` | FastAPI `/score` and `/health` | Serves the model preferred on validation for each product, validates every input, and answers 503 with a reason when a model is missing. |

### Scripts, docs and outputs

| File | What | Why |
|---|---|---|
| `scripts/train_insurance.py` | Trains the four insurance models, writes metrics, figures, models, MLflow runs and the data notes | One command reproduces every insurance number. |
| `scripts/fairness_german.py` | Runs the fairness demo and writes its section | Same pattern: one script, one results section. |
| `scripts/train_credit.py` | Runs the credit pipeline, or writes "not measured" with the reason when the zip is missing | A missing download is recorded honestly instead of skipped silently. |
| `scripts/render_reports.py` | Renders the model cards, validation report, this walkthrough and the README table from the results JSON | No number in a document is typed by hand, and data-dependent claims are worded by code from the intervals. Tests check that every number exists in the JSON and that the committed documents equal a fresh render of the committed results. |
| `docs/model_cards/credit_model_card.md` | Credit model card aligned to the structure of SR 26-2 / OCC Bulletin 2026-13 | Shows the documentation a bank model inventory expects, with "not measured" where it applies. |
| `docs/model_cards/insurance_model_card.md` | Insurance model card referencing the NAIC AI Model Bulletin and NY DFS Circular Letter No. 7 | Same sections, insurance-specific governance and fairness-testing references. |
| `docs/model_cards/validation_report.md` | Tests, backtests, sensitivity analyses and numbered findings | The "effective challenge" record: what was checked, what failed, what was done about it. |
| `docs/leakage.md`, `docs/insurance_data_notes.md` | Column decisions; freMTPL2 cleaning decisions | Reviewers can check every exclusion and data fix without reading code. |
| `results/risk_metrics.json`, `results/figures/` | All Phase 1 numbers with provenance; Lorenz and calibration plots | The single source of truth for every document. |
| `models/`, `mlruns.db`, `mlruns/` | Saved models and MLflow store (gitignored) | Rebuilt by the training scripts. |
| `tests/risk/*.py`, `tests/test_render_reports.py` | One test module per source module, plus the API and the renderer | Every test builds small synthetic data itself, so CI needs no downloads and no saved models. |
| `Makefile` | `train-insurance`, `fairness`, `train-credit`, `reports`, `phase1`, `serve` | One command per step (ground rule 3). |

## Design choices worth defending

1. **One framework, three models.** Metrics, bootstrap, PSI, fairness, scenarios, explanations and results
   writing are shared, so the credit, insurance and fairness code only contains what is specific to each problem.
2. **Champion/challenger rules fixed in advance.** Insurance: the model with the lower validation deviance is
   preferred (LightGBM for frequency,
   GLM for severity). Credit: LightGBM only if its validation AUC
   gain is large enough and its paired interval excludes zero.
3. **The API serves what the results report.** Frequency is served by
   LightGBM, severity by the
   GLM. Serving a model other than the validated one would be a
   governance gap, so the GLM got its own contribution-based reason codes instead.
4. **Risk bands from training quantiles.** The 50th, 80th and 95th percentiles of the served model's training
   predictions. For frequency the cut-offs are 0.0611,
   0.0958 and 0.1886 claims
   per policy-year. A band says where a case sits in the training portfolio; it is not a decision rule.
5. **Data problems change the model, and are documented.** The freMTPL2 claims without amounts and the
   German Credit coding error were both found by checking the data against itself, and both changed what was built.
6. **"Not measured" over a guess.** The credit section says why it is empty; the insurance PSI and out-of-time
   test say why they cannot exist for freMTPL2.

## Interview questions and answers

**1. Why an out-of-time split?**
A lender scores applications today with a model trained on the past. A random split lets the model learn from
loans issued after the ones it is tested on, so it looks better than it can ever be in use. The credit pipeline
trains on loans issued in 2015 and earlier, chooses and recalibrates on 2016, and touches 2017-2018 once.
Its out-of-time results are in the credit model card. freMTPL2 has no date field, so the insurance split is
random and stratified, and the results file says so: "freMTPL2 has no date or policy-year field, so an out-of-time test is impossible. Train, validation and test are random draws from the same period; the test metrics do not show how the models would hold up on a later year." That
limitation is also why insurance PSI is not measured.

**2. Why KS as well as AUC?**
AUC is the probability that a random bad loan scores riskier than a random good one: a ranking measure over all
cut-offs. KS is the largest gap between the cumulative score distributions of good and bad loans, which is the
best separation at a single cut-off; credit scorecard teams have reported it for decades, so validators expect
it. Neither says anything about calibration. Credit KS on Lending Club (scorecard):
0.2922 [0.2872, 0.2977].

**3. Why PSI?**
Population stability compares the distribution of a score or feature now with the training population, and needs
no outcomes, so it warns months before defaults arrive. The bins are quantiles of the reference period; PSI below
0.10 is read as stable, 0.10 to 0.25 as a moderate shift, above 0.25 as significant and flagged (the thresholds
are constants in `uwcopilot.risk.stability`). For credit, PSI runs per issue-year vintage on the score and the
top-10 features (flag threshold in the results: 0.2500). For
freMTPL2 it is not measured (PSI needs a time field. freMTPL2 has no date or policy-year field, so an out-of-time test is impossible. Train, validation and test are random draws from the same period; the test metrics do not show how the models would hold up on a later year.).

**4. Give examples of leakage you guarded against.**
`recoveries`, `total_pymnt`, `last_pymnt_d`, `out_prncp` and `last_fico_range_low` are only known after the loan
was issued; `loan_status` is the outcome itself. `int_rate`, `grade` and `sub_grade` are Lending Club's own
underwriting outputs: known at origination, but using them would mostly copy Lending Club's model. On the
synthetic test data, `recoveries` alone separates bad from good loans perfectly, which is what a leak looks like.
The guard runs at fit time, at score time, and in the API before a model is even loaded; a test fails if any
banned column reaches a model. `sub_grade` is reported only as a benchmark (test AUC
0.6842 [0.6813, 0.6867]).

**5. Calibration vs discrimination?**
Discrimination is ranking: does the model put riskier cases above safer ones (AUC, Gini, Lorenz Gini).
Calibration is level: do predicted rates match observed rates. On the freMTPL2 test split,
LightGBM ranks policies better than the GLM (Lorenz Gini 0.3208 [0.3065, 0.3365] for
LightGBM vs 0.2767 [0.2619, 0.2928] for the GLM; paired difference
0.0441 [0.0350, 0.0534]), while both are calibrated in total
(observed / predicted claims 1.0052 [0.9785, 1.0330] for
LightGBM and 1.0060 [0.9792, 1.0349] for the GLM). A model can
rank perfectly and still be off by a factor of two in level. For credit, class weighting inflates LightGBM's
probabilities, so they are Platt-recalibrated on 2016 without changing the ranking.

**6. What does the Poisson offset do?**
Claim counts grow with exposure: a policy on cover for half a year should expect half the claims. The GLM models
log(expected claims) = log(exposure) + Xβ with the log(exposure) coefficient fixed at 1, so Xβ is a rate per
policy-year. A test checks that this equals a Poisson regression of claims / exposure weighted by exposure. In
LightGBM the offset goes in as `init_score` = log(exposure) + log(training claim rate) (base
-2.6102); LightGBM does not store `init_score`, so prediction
adds the base back and returns claims per year. Early stopping kept
325 trees.

**7. Why split frequency and severity, and how is expected loss built?**
They have different drivers and different noise. Frequency uses every policy
(406,807 in training); severity only uses policies with claim
amounts (14,966 training rows), and amounts are heavy-tailed.
Claims are capped at 34,328.39 (the
0.995 quantile of training claims), which caps
0.51% of claims but removes
29.3% of the training amount, so
expected loss is on a capped basis and pricing would need a large-loss loading. Expected loss per policy-year is
frequency x severity. On the test split the GLM pair predicts
8,871,883 against
9,037,991 observed (capped); observed / predicted
1.0187 [0.9570, 1.0812]. The API serves
LightGBM frequency with
GLM severity, and that exact pair is validated
too: observed / predicted 1.0185 [0.9569, 1.0822].
Severity ranks claims much less sharply than frequency ranks policies (severity Lorenz Gini 0.0358 [0.0033, 0.0699] for the served
GLM, frequency 0.3208 [0.3065, 0.3365] for the
served LightGBM), so the pure-premium ranking comes mostly from frequency (pure-premium
Lorenz Gini 0.3303 [0.2989, 0.3628] for the served pair).

**8. What are the caveats of SHAP?**
SHAP splits one prediction into additive contributions; for trees it is exact (largest additivity error here
1.6e-14). It describes how the model uses a feature, not
what would happen if the feature changed: correlated features share or swap credit, and the baseline is the
average training case, so "increased the risk" means "relative to that average".
BonusMalus has the largest mean absolute SHAP
(0.3362 on the log scale).
The scenario check shows the difference between explanation and behaviour: adding
10 to
BonusMalus lowers LightGBM's predicted frequency for
5.37% of test policies, and the
GLM's for 0.00%. A monotone
constraint would fix that if the business requires it.

**9. SR 26-2 vs SR 11-7: what changed, and why is generative AI out of scope?**
On April 17, 2026 the Federal Reserve, OCC and FDIC issued SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026,
which supersedes SR 11-7 (2011) and SR 21-8. It keeps the core (model inventory, documentation, effective
challenge, outcomes analysis, ongoing monitoring, vendor-model oversight) but is explicitly risk-based and
proportionate, says it is expected to be most relevant to banking organizations with over $30 billion in assets,
and excludes simple arithmetic and deterministic rule-based processes from the model definition. Footnote 3 says
generative AI and agentic AI models are not within its scope because they are novel and evolving. So the credit
card is "aligned to the structure of SR 26-2" (never "SR 11-7-style", never "compliant"), and the LLM extraction
and agent in later phases get their own evaluation, monitoring, release gate and human approval rather than a
stretched SR 26-2.

**10. Why validate scenario overrides instead of clipping them?**
A stress test answers "what if this value were X?". If X is outside what the model saw in training, clipping
silently answers a different question ("what if it were the maximum?") and reports it as X. The API instead
returns HTTP 422 naming the feature and its training range, so the user knows the scenario is outside the model's
evidence. The same idea runs through the project: the German Credit documentation (german.doc) labels the
personal-status-and-sex codes wrongly, as the corrected code table published with the South German Credit data
shows. The demo neither quietly "fixes" nor quietly uses the documented mapping: it reports the documented
mapping with a warning recorded in the results file (`fairness_demo.data_quality_warning`) plus a
corrected-code sensitivity check.

## Talking about Phase 1 with a financial-services consultancy

These points connect the work to what banks and insurers ask advisers for. They describe this repo only; they
make no claim about any firm's practice.

- **Model risk management deliverables.** A model card per model with purpose, materiality, data, methodology,
  validation results, outcomes analysis, monitoring thresholds, owner and version, plus a validation report with
  numbered findings. All generated from the results file, so documentation never drifts from the evidence.
- **Effective challenge with evidence.** Two data problems were found and acted on: freMTPL2 claims without
  amounts (26.8% of claims,
  concentrated in one IDpol block; a GLM fitted on the recorded claim counts (capped at
  4, claims without amounts kept) showed observed / predicted claims of
  0.743
  outside that block, against
  1.007
  for the chosen treatment), and the German Credit documentation's sex coding.
- **Fair lending and insurance fairness testing.** Adverse impact ratios with intervals (age under 25, logistic
  regression: 0.855 [0.669, 1.051]), equal
  opportunity, and a proxy check showing that dropping protected attributes does not hide them (age under 25 is predictable from the remaining features with AUC 0.810 [0.750, 0.863]). Every interval that excludes "no difference" is raised as a
  validation finding with a multiple-comparisons caveat, rather than explained away.
- **Challenging the split, not only the model.** freMTPL2 has no customer identifier, so
  26.5%
  of test policies share every rating feature with a training policy. Refitting on a split grouped on those
  features gives a LightGBM minus GLM deviance difference of
  -0.0108 [-0.0123, -0.0093], against
  -0.0091 [-0.0107, -0.0075] on the headline split.
- **Explainability for adverse action.** Top-4 reason codes in Regulation B style for credit, and per-policy
  reasons for insurance, from SHAP or from GLM / scorecard contributions.
- **Scenario analysis through an API.** Validated what-if overrides with baseline vs scenario, risk bands, model
  versions and 503 / 422 answers that say why.
- **Generative AI governance gap.** SR 26-2 leaves generative and agentic AI out of scope, so those systems need
  their own evaluation suites, release gates and human approval: the subject of Phases 5 and 6.

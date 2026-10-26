# Phase 6 walkthrough: model risk governance, monitoring, feedback loop, release gate

Phase 6 adds the parts of the Underwriting Copilot that look after the models once they exist: a record of what
stood behind each decision (lineage), checks that say when a model stops behaving like it did in testing
(monitoring), a record of what reviewers do with the recommendations and why (feedback), a join of later outcomes to
earlier decisions, a controlled way for a retrained model to replace the one in use (champion/challenger), a release
gate in CI that blocks a build whose critical metrics got worse, and two governance packs that put all of it on
paper. The code is in `src/uwcopilot/governance/`. This walkthrough is written so both owners (Rahul Vajja and
Bhavani Adula) can explain the work in their own words.

Everything measured here is SYNTHETIC or SIMULATED, or public data with SYNTHETIC injected drift. This is a
demonstration, not a compliance program: nothing here claims compliance with any regulation, real-world accuracy or
production readiness.

**What ran and what did not.**
- Ran: `make governance-results` at commit `50acabe` with a clean working tree (every section of
  `results/governance_metrics.json` records that commit and `git_worktree_dirty: false`; the results were committed
  in `2d0a0b5`). That target runs, in order: the SIMULATED reviewer on the 65 SYNTHETIC agent applications through
  the real agent graph and checkpoint (offline); champion/challenger and the outcome join on SYNTHETIC Lending
  Club-shaped loans; the monitor on SIMULATED streams of public freMTPL2 policies with SYNTHETIC injected drift; the
  release gate against the reviewed baseline; and the rendering of the two packs. The release-gate CI job ran on
  GitHub Actions (section 7).
- Not run, so **not measured**: any LLM call (the memo is the deterministic template stamped `FAKE LLM — test
  mode`), so the LLM memo citation accuracy, the unsupported-claim rate and the prompt-injection attack success rates
  are not measured; the Lending Club data and the trained credit model are absent from this checkout (they need a
  Kaggle key), so the real-data champion/challenger, monitoring of the credit model and outcome analysis on real
  loans are not measured; freMTPL2 has no date field, so there is no real out-of-time stream of new business; fairness
  monitoring is not measured; no independent validation was performed. No cloud model, data or compute service was
  used for any result; the only run off this machine is the CI on GitHub Actions.

**How to read the numbers.** Every number below is copied from `results/governance_metrics.json`, and the section is
named each time (the release gate section quotes the numbers it read from the other results files). Values are
rounded to 4 decimals for display (delays to 2, as the packs print them; one exact bound below 0.0001 is shown to 5
decimals, and the release gate's reasons are quoted as the JSON prints them); the JSON holds full precision. Intervals are
95% percentile bootstrap intervals with 1,000 resamples and seed 42 unless stated otherwise.

## 1. What Phase 6 asked for, and what exists now

The spec is Prompt 6 in [build_prompts.md](build_prompts.md).

| Part | Phase 6 asked for | Where it is | State |
|---|---|---|---|
| A. Versioning | Every result and decision record carries the model version (MLflow), dataset checksum, prompt version and eval-set version; a script prints the full lineage of any decision record | `governance/lineage.py`; the record node of `agent/graph.py` writes a `lineage` block on every decision record; `scripts/lineage.py`, `make lineage`. Results files already carried provenance blocks before Phase 6 (`governance/provenance.py`, `risk/results.py` metadata) | Built + measured on the SIMULATED decision log (section `lineage`). The model version is copied from `results/risk_metrics.json` (its MLflow block); the risk service was not running, so each record names the configured model and says it did not answer |
| B. Monitoring | PSI per feature, score drift, calibration drift (ECE), extraction quality, agent unsupported-claim and escalation rates, override rate and reasons; thresholds in one config file; a breach writes an alert and marks the model "under review"; an HTML report | `governance/monitoring*.py`, `config/monitoring.yaml`, `scripts/monitor.py`, `results/monitoring_report.html` | Built + measured on a SIMULATED stream (section `monitoring`). The monitored model is a separate demonstration Poisson GLM on freMTPL2, not an inventory model. The credit model is not monitored (data absent). Extraction and agent indicators are single values from the last evaluation runs, not a stream of fresh packets. The unsupported-claim rate is not measured (no LLM) |
| C. Human feedback and outcomes | Accept / override, reason from a fixed list plus free text, reviewer role; join outcomes to decisions; champion/challenger on the same out-of-time window; promotion only by a model-validator action | `config/override_reasons.yaml`, `agent/hitl.py`, `governance/feedback.py`, `scripts/feedback_report.py`, `governance/outcomes.py`, `governance/champion_challenger.py`, `governance/model_status.py`, `scripts/champion_challenger.py`, `scripts/promote_model.py` | Built + measured on SYNTHETIC / SIMULATED data (sections `feedback`, `outcomes`, `champion_challenger`). Outcomes come from SYNTHETIC Lending Club-shaped loans, not the held-out real Lending Club loans the spec names (data absent), and the decisions they join to come from a stated SIMULATED rule |
| D. Release gate | `scripts/release_gate.py` compares the latest `results/*.json` with a committed baseline and fails CI on a regression beyond a stated tolerance; per-layer injection attack success rate gated against the baseline plus a tolerance, never "injection resistance = 100%"; a CI job | `governance/release_gate.py`, the `gate:` section of `config/release_gate.yaml`, `results/baseline/`, `scripts/release_gate.py`, CI job `release-gate` | Built and run (section `release_gate`): verdict PASS. The injection and LLM rows are NOT_MEASURED |
| E. Governance packs | `docs/governance/bank_model_risk.md` mapped to SR 26-2 with a separate section for the LLM extraction and the agent; `docs/governance/insurance_ai_program.md` mapped to the NAIC AI Model Bulletin; both rendered from `results/*.json` | `governance/packs*.py`, `scripts/render_reports.py`, [bank_model_risk.md](governance/bank_model_risk.md), [insurance_ai_program.md](governance/insurance_ai_program.md) | Rendered from the committed results; a test checks that the committed copies equal a fresh render |
| F. Results, README, walkthrough, tests | `results/governance_metrics.json`, README update, this walkthrough with 10 Q&As, tests | `results/governance_metrics.json` (six sections), the README status row, this file, `tests/governance/`, two agent test files, and new tests in `tests/web/test_web_lender.py` and `tests/test_render_reports.py` | Done. Opening the pull request is a separate step |

## 2. Files

### Governance package (`src/uwcopilot/governance`)

`provenance.py`, `freeze_proof.py` and `migration_gate.py` existed before Phase 6 (innovation S1) and are unchanged.

| File | What it does | Why it is built this way |
|---|---|---|
| `model_status.py` | The model status registry (`data/governance/model_status.json`, gitignored): each model version is `champion` (in use), `challenger` (a candidate, never used for decisions), `under_review` (a monitoring alert fired) or `retired`. `register`, `mark_under_review`, `promote`, `close_review`; every change is appended to the model's history with who, when and why. | The one place that says which model is in use. Monitoring may only mark the champion under review; `promote` and `close_review` need a principal allowed to take `promote_model` in `security/rbac.py` (model_validator only). A family has at most one model in use. Writes hold an exclusive file lock and go through a uniquely named temporary file, so two scripts cannot erase each other's change. |
| `lineage.py` | `build_decision_lineage()` writes the `lineage` block of a decision record: code commit and dirty flag, the model behind each `score_risk` call (copied from `results/risk_metrics.json`: MLflow registered model and version, run id, the commit that measured it, and whether it answered), the dataset checksum lines, the SHA-256 of the prompt file, the eval-set hashes, the SHA-256 of the results files, and the SHA-256 of the decision-logic files. `verify_lineage()` compares each value with the working tree: `ok`, `STALE` or `not_checkable`. | A decision can be traced back to the exact rules, prompt, data and model behind it. The memo prompt lives in code (`agent/memo.py`), so the file is hashed. The rule files are hashed because the policy and graph version labels are fixed strings that stay the same when a rule changes. The code commit is shown but not compared: every later commit would flag every record. |
| `feedback.py` | Loads the fixed override-reason list, maps pre-Phase 6 records (single `override_reason`) to the new shape, and summarizes a decision log: override rate with a bootstrap interval (unit = decision), counts per reason, role, action, line and reference recommendation. | It describes whatever log it is given and never decides whether a rate is too high; thresholds live in `config/monitoring.yaml`. |
| `monitoring_config.py` | Validates `config/monitoring.yaml` with pydantic (unknown keys refused, warn before alert, a no-drift scenario required) and turns each rule into a typed `LevelRule` that reads a value as ok / warn / alert. | A typo cannot silently switch a check off. |
| `monitoring.py` | The checks for one window: PSI of each of nine rating features and of the predicted claim frequency (the bins and formula of `risk/stability.py`), observed / predicted claims with an exact (Garwood) Poisson interval, and the ECE of P(claim > 0). A breach becomes an `AlertRecord` appended to `data/governance/monitoring_alerts.jsonl`, then the model is marked under review. | Same PSI as the model cards (a test checks equality), computed once per pool row so a window is one count. Monitoring can do nothing more than flag. |
| `monitoring_model.py` | Trains (or reloads) the monitored Poisson GLM of claim frequency on the seeded freMTPL2 training split and registers it as champion of its family the first time. | The cache is plain JSON, not a pickle, so loading it can never run code; it is reused only when the data checksums, split seed and row count match. |
| `monitoring_stream.py` | Builds the SIMULATED streams (random windows from the held-out split; covariate shift or claim-rate drift injected from a set window) and summarizes, per scenario: false alert rate and warn rate (no-drift streams), detection rate, detection delay and pre-drift false alerts (drifted streams), the alert and warn rates also by check; bootstrap intervals for every rate and the mean delay, and exact (Clopper-Pearson) intervals for the rates. | The monitor itself is tested against known truth. Clean windows use the same random numbers in every scenario, so differences between scenarios come from the drift alone. |
| `monitoring_indicators.py` | Reads the extraction indicators (citation support, OCR page share, low-confidence field share) from `results/docintel_metrics.json`, the agent indicators (required escalations, escalation share, unsupported-claim rate) from `results/agent_metrics.json`, and the override rate and reasons from section `feedback` of `results/governance_metrics.json`. Missing values are "not available" or "not measured". Its `override_indicator_problems()` says when the monitoring section did not read the feedback section the same file now holds (a test runs it on the committed results). | Reads JSON only and imports no other track's code; nothing is recomputed or guessed. |
| `monitoring_report.py` | Renders `results/monitoring_report.html` from the `monitoring` payload only: summary, validation table, demonstration windows, indicators, thresholds with rationale, alerts, what was not measured. | A static page: no JavaScript, a strict Content-Security-Policy, every value escaped, three small inline PNG charts. |
| `outcomes.py` | Joins outcome records to decision records by application id; reports the default rate per decision with a bootstrap interval, approved-and-defaulted counts, and the calibration of the recorded PD (ECE, Brier, observed / expected, deciles). `SimulatedDecisionRule` is the stated rule behind the demonstration's SIMULATED decisions. | Repeated ids and unknown decisions are refused, never silently resolved; decisions without an outcome stay "pending". |
| `champion_challenger.py` | Fits the champion (issue years <= 2014, validation 2015) and the challenger (<= 2015, validation 2016) with the credit model's own `fit_lightgbm`, scores both on the same finished 2017-2018 loans, reports AUC, KS, Gini, Brier and ECE with intervals plus paired bootstrap differences, and applies a `PromotionRule` fixed in code before any result was seen. | The training code is the credit model's own; only the years differ (the challenger's years are the credit model's own split). It registers the champion and challenger and never promotes. |
| `release_gate.py` | Reads one number per gate row from the latest results and from the reviewed baseline, checks the row's preconditions, and returns PASS, FAIL, MISSING or NOT_MEASURED; checks the baseline copies against a SHA-256 manifest; lists results files written from a dirty tree as warnings. | The gate measures nothing itself. FAIL and MISSING block; NOT_MEASURED is listed, never counted as passing. |
| `packs.py` | What the two packs share: the results files they read, value lookup, number formats ("not measured" for a missing value), provenance per table, and the regulatory citations. | Ground rule 1 in code: every number in a pack is read from a results file at render time. |
| `packs_inventory.py` | The model inventory and the materiality tiering rule (a design choice of this project). | See question 6. |
| `packs_models.py` | Model facts, validation tables with intervals and the German Credit fairness rows, from `results/risk_metrics.json`. | |
| `packs_governance.py` | The Phase 6 sections of `results/governance_metrics.json` as Markdown blocks, and the registry rules. | A section not generated yet prints "not measured yet" with the make target that writes it. |
| `packs_genai.py` | The controls on the agent and the LLM extraction with their measured values, and the third-party pretrained, non-generative models (Tesseract OCR, the fastembed retrieval models) with their recorded use. | SR 26-2 places generative and agentic AI outside its scope, so these get their own section. |
| `packs_bank.py`, `packs_insurance.py` | Render the bank model risk pack and the insurance AIS Program outline. | |

### Changed agent and web files

| File | What changed |
|---|---|
| `agent/hitl.py` | `HumanDecision` takes `reason_codes` (approved codes only, each once) and an optional `reason_text`; an override (an approve or decline that differs from the reference recommendation) needs at least one code. The older single `override_reason` is still accepted and kept in step. |
| `agent/graph.py` | The record node writes the `lineage` block; the stored decision gains `review_action` (accept or override) and `feedback_version`; the record gains `reviewer_label` ("human" by default, "SIMULATED" when a script plays the reviewer). |
| `agent/decision_record.py` | Docstring only: the new optional parts of a record. Older records still load. |
| `scripts/run_agent.py` | `--reason-code` (repeatable) and `--reason-text`. |
| `web/schemas_lender.py`, `web/services/lender_demo.py`, `web/routers/lender.py` | The Lender Desk API accepts `reason_codes` and `reason_text`, shows the review action and the reason options, refuses SSN- or account-like numbers in `reason_text`, and adds a short Lineage section to the Markdown record. The frontend is unchanged. |

### Scripts

| Script | What it does | Make target |
|---|---|---|
| `scripts/lineage.py` | Prints one record's chain (record, versions, models and the results that validated them, dataset checksum lines, prompt hashes, eval sets, decision-logic hashes) with OK / STALE / NOT CHECKABLE; `--strict` exits 3 on STALE. | `make lineage LOG=... RECORD=...` |
| `scripts/feedback_report.py` | Runs the 65 SYNTHETIC applications through the real graph to the checkpoint (designed screening outcome, retrieval off, risk tool unavailable, template memo; no LLM), lets a SIMULATED reviewer decide each one through the real checkpoint, and writes sections `feedback` and `lineage`. It refuses to delete any log that is not its own SIMULATED log. | `make feedback-report` |
| `scripts/champion_challenger.py` | SYNTHETIC champion/challenger and the outcome join; writes sections `champion_challenger` and `outcomes`; registers both models; promotes nothing. | `make champion-challenger` |
| `scripts/promote_model.py` | The model validator's promotion step. Exit 0 promoted, 1 refused (role may not promote; nothing written), 2 bad request. | `make promote-model` |
| `scripts/monitor.py` | The monitor end to end; writes section `monitoring` and `results/monitoring_report.html`. | `make monitor` |
| `scripts/release_gate.py` | Prints the gate table, warnings and verdict; exits 0 PASS, 1 FAIL or MISSING (or a baseline that fails its manifest check), 2 invalid configuration. `--write-results` writes section `release_gate`; `--update-baseline` copies the gated results files into `results/baseline/` with a manifest. | `make release-gate`, `make release-gate-baseline` |
| `scripts/render_reports.py` (changed) | Also renders the two packs; `--governance-packs-only` renders only them. The other documents it writes are unchanged. | `make reports`, `make governance-packs` |

### Configuration, results, Makefile and CI

| Item | What it is |
|---|---|
| `config/monitoring.yaml` | The only place monitoring thresholds live, each with its rationale, plus the stream design and the SYNTHETIC drift scenarios. Its comment says the values are design choices written before the first validation run and not changed since, and that the extraction and agent thresholds were written while their current values were visible (so they are reasonable round values, not validated limits). |
| `config/override_reasons.yaml` | The approved override reason list (five codes, the ones `agent/hitl.py` already accepted; NBC-8.2 and HM-10.2 of the SYNTHETIC guidelines require one). Add codes; never rename or reuse one. |
| `config/release_gate.yaml`, section `gate:` | 15 rows (file, JSON path of the value and of its conservative CI bound, which one is compared, direction, kind, tolerance, required) and the preconditions per results file. The older `migration:` section is unchanged. |
| `results/baseline/` | Byte copies of the five results files the gate reads that exist (agent, docintel, risk, Studio evidence, Studio hand-off) and `baseline_manifest.json` with the SHA-256 of each copy, made at commit `fb7eb1d`. The manifest records `injection_metrics.json` as absent when it was made. |
| `results/governance_metrics.json` | Six sections: `monitoring`, `lineage`, `feedback`, `champion_challenger`, `outcomes`, `release_gate`, each with its own metadata (commit, dirty flag, time, package versions). |
| `results/monitoring_report.html` | The static monitoring report. |
| Makefile | `monitor`, `monitor-test`, `lineage`, `feedback-report`, `champion-challenger`, `promote-model`, `champion-challenger-test`, `release-gate`, `release-gate-baseline`, `release-gate-test`, `governance-packs`, `governance-packs-test`, and `governance-results`, which runs the producers in dependency order (feedback-report, champion-challenger, monitor, release-gate `--write-results`, governance-packs) and exits with the gate's code. |
| `.github/workflows/ci.yml`, job `release-gate` | Runs `make release-gate` after `lint-and-test` (so the containment tests in `tests/security/test_containment.py` have passed), offline, from the locked environment. |
| `uv.lock` | Upgraded packages with advisories published during the phase (pillow, pypdf, certifi, gitpython, filelock, mlflow, and fastembed because its older version capped pillow); not governance code, but it landed in the same range of commits. |

### Tests

All offline, with SYNTHETIC or hand-made data or temporary copies of the committed results files; no download, model
download or LLM.

| Test file | What it covers |
|---|---|
| `tests/governance/test_model_status.py` | Only a model validator promotes or closes a review; monitoring only flags the champion; one model in use per family; two threads and two processes writing at once. |
| `tests/governance/test_lineage.py`, `test_lineage_script.py`, `lineage_helpers.py`, `tests/agent/test_agent_lineage.py` | Building and checking the lineage block; a changed rule file shows as STALE under `--strict`; a partly recorded record is never "current"; older records still load. |
| `tests/governance/test_feedback.py`, `test_feedback_report.py`, `tests/agent/test_agent_review_feedback.py` | The reason list, legacy mapping, override statistics, the SIMULATED reviewer, and the refusal to delete a log that is not its own. |
| `tests/governance/test_monitoring*.py`, `monitoring_test_helpers.py` | PSI equals the model-card functions, levels, breaches and the alert log, the config validation, streams and the validation summary (exact intervals at 0, 1, 299 and 300 of 300), indicators and "not available", the HTML report (static, escaped, strict CSP), and the script end to end, including the run-order check on the feedback section. |
| `tests/governance/test_outcomes.py` | The join, pending and unmatched counts, rates by decision, PD calibration, refused duplicates. |
| `tests/governance/test_champion_challenger.py`, `test_champion_challenger_promote.py` | Windows, the promotion rule, that nothing promotes itself, and that every role except model_validator is refused. |
| `tests/governance/test_release_gate.py` | Fault injection on temporary copies of the committed results: a regressed AUC bound, tolerance edges, escalation 0.98, decisions without a human, missing files, NaN and null, a hand-edited baseline, partial and subset runs, a flipped model selection, real `rate()` blocks for the LLM rows, malformed YAML. Also: the committed results pass against the committed baseline. |
| `tests/web/test_web_lender.py` (new tests) | The Lender Desk API records reason codes, the note and the lineage; lists the reason options; refuses an override without a known reason and private numbers in the note; still reads a record stored before Phase 6. |
| `tests/governance/test_packs.py`, `tests/test_render_reports.py` | Packs rendered from hand-made SYNTHETIC results: every number traces to the JSON, absent sections say "not measured yet", LLM measures say "not measured", the tiering rule and the applicability statements; the committed packs equal a fresh render. An integration test (excluded by default) checks the quoted SR 26-2 and NY DFS wording against the local primary sources. |

## 3. How each part works

**A. Lineage.** When a reviewer's decision is accepted, the graph's record node calls `build_decision_lineage()` and
stores the block inside the hash-chained decision record. Later, `scripts/lineage.py <log> <record id>` recomputes
every recorded hash from the files as they are now and prints each one as OK, STALE (the file, a rule, the results or
the model version changed since the decision) or NOT CHECKABLE (the file is not here, for example the Lending Club
data, or the part was never recorded). A record is "current" only when at least one check ran and all are OK; a
record with nothing STALE but some part not checkable or not recorded is "current where checkable". In this checkout no risk model answers (the credit model is absent
and no risk service is started), so each record names the configured model version and says `tool_status:
unavailable`.

**B. Monitoring.** The monitor watches a Poisson GLM of claim frequency trained on the seeded freMTPL2 training split
(406,807 policies, section `monitoring`, `monitored_model.training_split.n_train_policies`). freMTPL2 has no date
field, so a "stream" is 12 windows of 5,000 policies drawn at random from the 135,603 held-out policies
(`stream_design`), and drift is injected from window 6 by a SYNTHETIC scenario: a covariate shift (resampling so that
drivers under 26, or region R93, make up a set share of each window) or a claim-rate drift (SIMULATED extra claims).
Each window gets four kinds of checks, each read as ok, warn or alert by a rule in `config/monitoring.yaml`:

- input drift: PSI of each of nine rating features against the training split (warn >= 0.1, alert > 0.25);
- score drift: PSI of the predicted claim frequency (same bands);
- calibration ratio: observed / predicted claims with an exact Poisson interval; warn when the point estimate leaves
  [0.9, 1.1], alert only when the whole interval lies outside it;
- calibration ECE of P(claim > 0) with 10 equal-count bins (warn >= 0.015, alert > 0.025).

Only "alert" is a breach. A breach appends an alert record and marks the model `under_review`; nothing else happens
automatically. The monitor is validated on 300 SIMULATED streams per scenario, then one demonstration stream is
checked window by window and its breaches are really written to the alert log and the registry. Finally it reads
the extraction, agent and reviewer indicators from the results files, writes section `monitoring` and the HTML report.

**C. Feedback, outcomes and champion/challenger.** At the human checkpoint a reviewer's decision now carries the
action, the role (claimed in this demo, no identity provider), the rationale, reason codes from the fixed list and
optional free text; an override without a code is refused. `scripts/feedback_report.py` has a SIMULATED reviewer
decide the 65 SYNTHETIC applications through that real checkpoint using made-up probabilities (seed 42); a refused
choice is retried as a senior underwriter, then referred, and the refusals stay in the record. `feedback.py` then
counts overrides and reasons. Separately, `scripts/champion_challenger.py` builds 60,000 SYNTHETIC Lending
Club-shaped loans, fits champion and challenger, compares them on the same out-of-time loans and applies the
promotion rule; then the champion's PDs and a stated SIMULATED decision rule decide every 2017-2018 application, the
SYNTHETIC outcomes of the finished loans "arrive", and `outcomes.py` joins them back by application id. The
challenger stays a challenger until a model validator runs `scripts/promote_model.py`.

**D. Release gate.** Each row of the `gate:` section names one critical number. `baseline_relative` rows pass when the
current value is no worse than the reviewed baseline minus (or plus) the tolerance; `absolute` rows compare with a
fixed target (for example 1.0 for escalation correctness, 0 for decisions without a human, tolerance 0). Before
comparing, preconditions must hold: the run covered the full evaluation set, the same set as the baseline, and the
model the risk service serves. A missing current value where the baseline had one fails; a value neither file has is
NOT_MEASURED when the row is not required (listed, never a pass) and MISSING, which blocks, when it is required; a new
measurement with no reviewed baseline value is MISSING too. A baseline copy that does not match its manifest blocks.
The CI job runs this on every push and pull request, after the tests.

**E. Governance packs.** `scripts/render_reports.py` renders both packs from the results files only. Each table names
its results file, section and the commit recorded in it. The bank pack follows the structure of SR 26-2 and governs
the LLM extraction and the agent in a separate section; the insurance outline follows the program elements of the
NAIC Model Bulletin as summarized in the build plan. Both state that applicability depends on the institution.

**F. Run order.** The monitor reads section `feedback`, and the packs read every section, so
`make governance-results` runs feedback-report, champion-challenger, monitor, release-gate `--write-results` and
governance-packs in that order. A check (`override_indicator_problems()` in `monitoring_indicators.py`, run by a test
on the committed results) flags a feedback section that was rewritten after the monitor ran.

## 4. Design choices

- **Monitoring flags; people decide.** A breach can only mark the champion `under_review`. Promoting a challenger or
  closing a review needs the `model_validator` role, and every status change is kept with who, when and why.
- **Validate the monitor, not just compute PSI.** A drift check is only useful if its false alarms and its blind spots
  are known, so the monitor is run on streams where the truth is known (injected drift) and its false alert rate,
  detection rate and delay are reported with intervals.
- **Alert on calibration only when the whole interval is outside the band.** At 5,000 policies per window the point
  estimate wanders outside a ±10% band by chance (see the warn rate in section 5.1); alerting on it would cry wolf.
- **Exact intervals at the edges.** When 0 or all of 300 streams count, a bootstrap interval has zero width, so every
  rate also carries the exact Clopper-Pearson interval.
- **Thresholds in one file, written down as design choices.** PSI bands match the model cards; the others are round
  values with a stated reason, not fitted limits.
- **Hash the files that decide, not the commit.** The prompt file and the decision-logic files are hashed per record,
  so a changed approval limit shows as STALE while an unrelated commit does not.
- **The promotion rule is fixed before the comparison.** Promotion is recommended only if the AUC gain's paired
  interval is above 0 and calibration is not worse by more than 0.01 in ECE; the recommendation is advice.
- **Gate the conservative end of the interval.** The credit AUC row gates the lower CI bound, the deviance rows the
  upper bound, so a release must keep the cautious end, not just the point estimate.
- **NOT_MEASURED never passes, and run scope is checked.** A partial injection run, an agent run on the first N
  applications or a dev-split document run fails instead of being compared with the full baseline (review finding
  RG-2).
- **Dirty-tree provenance is a warning, not a failure.** Most older results files were written from a dirty tree;
  failing on that would block everything, so the gate lists them for regeneration instead.
- **The SIMULATED reviewer goes through the real checkpoint.** The refusals and authority rules it hits are the
  agent's real ones; only its choices are made up.
- **Packs are rendered, never typed.** A test checks that every number in a rendered pack traces to the JSON and that
  the committed packs equal a fresh render.

## 5. Results

All from `results/governance_metrics.json`, commit `50acabe`, clean tree.

### 5.1 Monitoring (section `monitoring`; public freMTPL2 policies, SIMULATED streams, SYNTHETIC injected drift)

The monitored GLM on the whole held-out pool: 5,295 observed claims against 5,263.45 predicted, observed / predicted
1.0060 with exact Poisson interval [0.9791, 1.0335] (`monitored_model.held_out_calibration`).

Validation of the monitor: 300 streams per scenario, 12 windows of 5,000 policies, drift from window 6
(`validation`). A detection is an alert in a drifted window; the delay is the number of windows from the start of the
drift to the first alert, averaged over **detected streams only** (it cannot exceed 5).

| Scenario | Drift | Measure | Rate [95% bootstrap CI] (streams) | Exact 95% interval | Mean delay among detected streams, windows [95% CI] | Detected streams by delay |
|---|---|---|---|---|---|---|
| `no_drift` | none | false alert rate | 0.0033 [0.0000, 0.0100] (1/300) | [0.00008, 0.0184] | not applicable | not applicable |
| `covariate_shift_young_drivers` (30% under 26) | SYNTHETIC | detection rate | 1.0000 [1.0000, 1.0000] (300/300) | [0.9878, 1.0000] | 0.00, zero-width interval (300 of 300 detected) | 0: 300 |
| `covariate_shift_young_drivers_mild` (15% under 26) | SYNTHETIC | detection rate | 0.0000 [0.0000, 0.0000] (0/300) | [0.0000, 0.0122] | not applicable (no stream was detected) | not applicable |
| `covariate_shift_region` (35% region R93) | SYNTHETIC | detection rate | 1.0000 [1.0000, 1.0000] (300/300) | [0.9878, 1.0000] | 0.00, zero-width interval (300 of 300 detected) | 0: 300 |
| `claim_rate_up_25pct` | SYNTHETIC | detection rate | 0.9933 [0.9833, 1.0000] (298/300) | [0.9761, 0.9992] | 0.80 [0.67, 0.93] (298 of 300 detected) | 0: 173, 1: 59, 2: 33, 3: 22, 4: 9, 5: 2 |
| `claim_rate_up_10pct` | SYNTHETIC | detection rate | 0.1633 [0.1233, 0.2100] (49/300) | [0.1233, 0.2101] | 2.47 [1.94, 3.00] (49 of 300 detected) | 0: 11, 1: 8, 2: 7, 3: 6, 4: 4, 5: 13 |

Which check caught it (streams with an alert from that check; `false_alert_rate_by_check` for `no_drift`,
`detection_rate_by_check` for the others):

| Scenario | Input drift (PSI) | Score drift (PSI) | Calibration ratio | Calibration ECE |
|---|---|---|---|---|
| `no_drift` (false alerts) | 0/300 | 0/300 | 1/300 | 0/300 |
| `covariate_shift_young_drivers` | 300/300 | 0/300 | 4/300 | 0/300 |
| `covariate_shift_young_drivers_mild` | 0/300 | 0/300 | 0/300 | 0/300 |
| `covariate_shift_region` | 300/300 | 0/300 | 2/300 | 0/300 |
| `claim_rate_up_25pct` | 0/300 | 0/300 | 298/300 | 0/300 |
| `claim_rate_up_10pct` | 0/300 | 0/300 | 49/300 | 0/300 |

Warn level on no-drift streams (`validation.no_drift.warn_rate`): 262 of 300 streams, 0.8733 [0.8333, 0.9100], had at
least one window at warn or worse, almost all from the calibration ratio (261/300). Warn is noise at this window
size, which is why only alert is a breach.

Demonstration stream (`demonstration_stream`: scenario `covariate_shift_young_drivers`, stream 0):

| Window | Drifted | Level | PSI of DrivAge | PSI of predicted frequency | Observed / predicted claims [exact 95% interval] | ECE of P(claim > 0) | Alert |
|---|---|---|---|---|---|---|---|
| 0 | no | ok | 0.0023 | 0.0020 | 0.9961 [0.8605, 1.1470] | 0.0060 | - |
| 1 | no | ok | 0.0017 | 0.0023 | 1.0792 [0.9385, 1.2351] | 0.0057 | - |
| 2 | no | warn | 0.0032 | 0.0018 | 0.8948 [0.7650, 1.0403] (warn) | 0.0059 | - |
| 3 | no | ok | 0.0019 | 0.0026 | 0.9985 [0.8626, 1.1497] | 0.0075 | - |
| 4 | no | warn | 0.0025 | 0.0012 | 1.1107 [0.9668, 1.2699] (warn) | 0.0085 | - |
| 5 | no | ok | 0.0009 | 0.0021 | 1.0225 [0.8851, 1.1753] | 0.0088 | - |
| 6 | yes | alert | 0.3712 (alert) | 0.1122 (warn) | 0.9419 [0.8202, 1.0766] | 0.0066 | `MON-20261026T181527Z-0001` |
| 7 | yes | alert | 0.3750 (alert) | 0.1159 (warn) | 0.9812 [0.8574, 1.1178] | 0.0084 | `MON-20261026T181527Z-0002` |
| 8 | yes | alert | 0.3839 (alert) | 0.1220 (warn) | 0.9048 [0.7846, 1.0382] | 0.0080 | `MON-20261026T181527Z-0003` |
| 9 | yes | alert | 0.3759 (alert) | 0.1271 (warn) | 0.9249 [0.8043, 1.0584] | 0.0065 | `MON-20261026T181527Z-0004` |
| 10 | yes | alert | 0.3649 (alert) | 0.1055 (warn) | 1.0776 [0.9461, 1.2223] | 0.0058 | `MON-20261026T181527Z-0005` |
| 11 | yes | alert | 0.3679 (alert) | 0.1155 (warn) | 0.9230 [0.8035, 1.0553] | 0.0105 | `MON-20261026T181527Z-0006` |

The run wrote 6 alert records (`alerts.n_alerts_this_run`). The model `fremtpl2-frequency-glm-monitoring-v1` was
already `under_review` from an earlier run's alert and stayed so (`model_status.status_before_run`,
`status_after_run`): a re-run never clears a review; only a model validator can.

Indicators read from the results files (`indicators.values`; single values from the last evaluation runs, not a
stream):

| Indicator | Source | Value | Level |
|---|---|---|---|
| Citation support (extracted values whose cited page prints the value) | `docintel_metrics.json` | 0.9984 [0.9975, 0.9992]; 58 citation failures in 36,288 values | ok |
| OCR page share (pages with no text layer read by OCR) | `docintel_metrics.json` | 0.1675 (159 of 949 pages) | ok |
| Low-confidence field share | `docintel_metrics.json` | not available (the file has no field-confidence metric) | - |
| Required escalations escalated | `agent_metrics.json` | 1.0 (60 cases requiring escalation) | ok |
| Escalation share | `agent_metrics.json` | 0.9231 (60 of 65); reported only, no rule (the SYNTHETIC set's mix is designed) | - |
| Unsupported-claim rate | `agent_metrics.json` | not measured (no LLM call was made) | - |
| Override rate (SIMULATED reviewer) | section `feedback` | 0.1385 [0.0615, 0.2308] (9 of 65) | warn |

Credit model: not measured (`credit_model.status`; the Lending Club data and every trained credit model are absent).

### 5.2 Lineage (section `lineage`; SYNTHETIC applications, SIMULATED reviewer actions)

65 records read, 65 with a lineage block, all 65 `current_where_checkable` (`checks.records_by_status`): 1,628 checks
ok and 130 not checkable (`checks.checks_by_status`), none STALE. The 130 are the Lending Club zip (absent, no
checksum line) and the freMTPL2 `.arff.gz` files (not kept in this checkout) named in the models' checksum lines
(`checks.not_checkable_items`). The check ran right after the records were written, so matching hashes are expected:
this shows the mechanism, not that anything stayed unchanged over time.

### 5.3 Reviewer feedback (section `feedback`; SIMULATED reviewer, SYNTHETIC applications)

These rates describe the simulation (an override probability of 0.15 that was made up, `simulation.override_probability`,
and the checkpoint's rules), not real underwriters.

| Measure | Value |
|---|---|
| Decisions / overrides / accepts | 65 / 9 / 56 (`summary`) |
| Override rate | 0.1385 [0.0615, 0.2308] (9/65; unit = decision) |
| Override reasons cited (an override may cite two) | `referral_resolved_in_writing` 6, `data_error_in_model_input` 2, `information_not_available_to_model` 2, `documented_compensating_factor` 1, `policy_exception_approved` 1 |
| Overrides without a reason / with two or more reasons / with free text | 0 / 3 / 4 |
| By reviewer role | senior_underwriter 9 of 58 (0.1552); underwriter 0 of 7 (0.0) |
| By line | credit 4 of 32 (0.125); insurance 5 of 33 (0.1515) |
| By reference recommendation | approve 4 of 15 (0.2667); refer 4 of 41 (0.0976); request_more_information 1 of 5 (0.2); decline_for_review 0 of 4 (0.0) |
| Final actions | approve 12, decline 12, refer 37, request_more_information 4 |
| Checkpoint (`checkpoint`) | an override was drawn for 11 files, impossible for 2 of them; 6 first choices were refused by the checkpoint (5 of them override attempts), all 6 were accepted when retried as a senior underwriter, 0 fell back to a referral |
| Decision log | 65 records, 0 chain problems (`decision_log`) |

### 5.4 Champion/challenger (section `champion_challenger`; SYNTHETIC Lending Club-shaped loans)

60,000 SYNTHETIC rows (seed 42), 58,211 prepared loans, 39,620 finished (`synthetic_data`). Champion trained on
18,953 finished loans of 2013-2014, challenger on 26,904 of 2013-2015; both scored on the same 6,611 finished loans of
2017-2018 (1,933 bad), which neither saw (`windows`).

| Metric (out of time) | Champion [95% CI] | Challenger [95% CI] | Challenger minus champion, paired [95% CI] |
|---|---|---|---|
| AUC | 0.7461 [0.7321, 0.7591] | 0.7458 [0.7320, 0.7588] | -0.0003 [-0.0029, 0.0025] |
| KS | 0.3751 [0.3520, 0.4021] | 0.3673 [0.3474, 0.3955] | -0.0077 [-0.0167, 0.0041] |
| Gini | 0.4922 [0.4643, 0.5182] | 0.4915 [0.4641, 0.5176] | not computed |
| Brier (lower is better) | 0.1736 [0.1688, 0.1785] | 0.1738 [0.1690, 0.1786] | 0.0002 [-0.0006, 0.0009] |
| ECE (lower is better) | 0.0138 [0.0099, 0.0261] | 0.0128 [0.0098, 0.0249] | -0.0010 [-0.0086, 0.0078] |

Recommendation: `keep_champion`, because the AUC difference's lower bound (-0.0029) is not above 0; the ECE condition
(upper bound 0.0078 <= 0.01) was met (`recommendation.conditions`). Promoted by this run: no. The SYNTHETIC generator
draws every issue year from the same distribution, so a retrain is not expected to help here; this shows the
procedure, not how much a retrain helps on real loans.

### 5.5 Outcomes (section `outcomes`; SYNTHETIC outcomes joined to SIMULATED decisions)

18,786 applications issued 2017-2018 decided by the champion's PD and the SIMULATED rule; 6,611 outcomes arrived,
12,175 still pending, 0 outcomes without a decision (`summary.join`).

| Decision (SIMULATED rule) | Decisions | With outcome | Pending | Default rate [95% CI] (defaults / with outcome) | Mean recorded PD (with outcome) |
|---|---|---|---|---|---|
| `approve` | 13,037 | 4,632 | 8,405 | 0.2180 [0.2060, 0.2310] (1,010/4,632) | 0.2123 |
| `refer` | 2,156 | 759 | 1,397 | 0.5679 [0.5310, 0.6034] (431/759) | 0.5339 |
| `decline_for_review` | 888 | 304 | 584 | 0.7138 [0.6645, 0.7664] (217/304) | 0.6865 |
| `request_more_information` | 2,705 | 916 | 1,789 | 0.3002 [0.2696, 0.3297] (275/916) | 0.3162 |

Recorded PD calibration on the 6,611 rows with an outcome (`summary.recorded_pd_calibration`): observed default rate
0.2924 against mean recorded PD 0.2854 (observed / expected 1.0245), ECE 0.0138 [0.0099, 0.0261], Brier 0.1736
[0.1688, 0.1785]; the same rows as the champion's ECE in 5.4, so the two agree. Declines have outcomes here only
because every SYNTHETIC row is an issued loan; in a real book a declined application produces no outcome (selective
labels).

### 5.6 Release gate (section `release_gate`)

Verdict **PASS**, exit code 0: 15 rows, 11 PASS, 0 FAIL, 0 MISSING, 4 NOT_MEASURED (`counts`). Baseline made at commit
`fb7eb1d`; no baseline problems. The reasons are copied from the JSON (`rows[].reason`). The gate measures nothing
itself: the risk rows read numbers measured earlier on the public Lending Club and freMTPL2 data and kept in
`results/risk_metrics.json`, the other rows read evaluations on SYNTHETIC data.

| Row | Gated number | Required | Status | Reason |
|---|---|---|---|---|
| `credit_auc_ci_low` | Credit PD model test AUC, lower CI bound (`risk_metrics.json`) | yes | PASS | 0.712266 >= 0.707266 (baseline - tolerance 0.005) |
| `insurance_frequency_deviance_ci_high` | Claim-frequency LightGBM test mean Poisson deviance, upper CI bound | yes | PASS | 0.458274 <= 0.463274 (baseline + tolerance 0.005) |
| `insurance_severity_deviance_ci_high` | Claim-severity Gamma GLM test mean Gamma deviance, upper CI bound | yes | PASS | 1.25651 <= 1.27651 (baseline + tolerance 0.02) |
| `agent_required_escalations` | Share of required escalations that were escalated (`agent_metrics.json`) | yes | PASS | 1 >= 1 (target 1 - tolerance 0) |
| `agent_reached_human_checkpoint` | Applications that reached the human checkpoint | yes | PASS | 1 >= 1 (target 1 - tolerance 0) |
| `agent_decisions_without_human` | Decisions recorded without a human (count) | yes | PASS | 0 <= 0 (target 0 + tolerance 0) |
| `studio_decisions_without_human` | Studio hand-off decisions without a human (count) | yes | PASS | 0 <= 0 (target 0 + tolerance 0) |
| `llm_citation_accuracy` | LLM memo citation accuracy | no | NOT_MEASURED | not measured: no LLM provider was used; no LLM call was made |
| `llm_unsupported_claim_rate` | LLM memo unsupported-claim rate | no | NOT_MEASURED | not measured: no LLM provider was used; no LLM call was made |
| `docintel_citation_support_ci_low` | Rules extractor citation support, lower CI bound (`docintel_metrics.json`) | yes | PASS | 0.9975 >= 0.9955 (baseline - tolerance 0.002) |
| `injection_asr_l1` | Injection attack success rate, layer L1, all frozen payloads | no | NOT_MEASURED | injection_metrics.json is absent |
| `injection_asr_l2` | Injection attack success rate, layer L2 (containment prompt) | no | NOT_MEASURED | injection_metrics.json is absent |
| `studio_package_untraced_numbers` | Founder package text, untraced numbers (count) | yes | PASS | 0 <= 0 (target 0 + tolerance 0) |
| `studio_pdf_untraced_numbers` | Founder package PDF, untraced numbers (count) | yes | PASS | 0 <= 0 (target 0 + tolerance 0) |
| `studio_narrative_untraced_numbers` | Founder narrative, numbers not bound to a cited claim (count) | yes | PASS | 0 <= 0 (target 0 + tolerance 0) |

The two LLM reasons are shortened here; the JSON adds that no `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` was configured
or `--provider none` was used. The gate also recorded 5 warnings (`warnings`): the risk, agent, docintel and Studio
evidence results it reads were written from a working tree with uncommitted changes, so their commits alone do not
pin the code. These warnings do not fail the gate today.

## 6. How it was built and reviewed

The commit range is `2e99439..2d0a0b5` (`git log 2e99439..HEAD`).

1. **Shared base.** The model status registry (`98e9edb`) came first, because both monitoring and champion/challenger
   change model statuses.
2. **Build tracks.** The work was split into tracks in separate git worktrees, merged as `p6/monitoring` (part B),
   `p6/lineage-feedback` (parts A and C feedback), `p6/champion-challenger` (part C), `p6/release-gate` (part D) and
   `p6/packs` (part E): four ran through in one pass, and one was interrupted by a usage limit and resumed. After
   the merges, the results were regenerated at one clean commit (`b8eef4b`, the last merge) and the packs re-rendered;
   both were committed in `2f9f4d9`.
3. **Adversarial review.** Reviewers attacked the release gate, the statistics of the monitor, lineage and feedback,
   and the packs: 27 findings, 23 kept and 4 refuted by a second reviewer who tried to reproduce each one (RG-1:
   updating the baseline in the same change; P6-STAT-5: ECE interval bias; P6-STAT-6: the ECE check could not fire;
   P6-PACKS-7: the insurance outline's materiality rule). Examples of what was kept and fixed:
   - RG-2: a partial or subset run (an aborted injection run, `eval_agent.py --limit N`, a dev-split document run) was
     compared with the full baseline, so a rate over a few scored payloads could pass against the full set's rate.
     Fixed with preconditions.
   - RG-3: the LLM rows pointed at the whole `rate()` block, so they would have failed forever once an LLM ran.
     RG-4: the risk rows hard-coded the model, so a retrain that changed the selected model kept gating the old one.
     RG-5: malformed YAML exited 1 ("regression") instead of 2 ("invalid configuration").
   - P6-STAT-1 and 2: the monitor had run before the feedback section existed, so the committed override indicator was
     "not available", and the reasons were not read. Fixed with the run order (`make governance-results`) and a
     staleness check. P6-STAT-3 and 4: the detection delay was a mean over detected streams only without saying so,
     and 0/300 or 300/300 rates had zero-width intervals. Fixed with the labels, the cap and exact intervals.
   - P6-LF-1 to 5: concurrent registry writers could erase each other's change (now locked); monitoring could mark a
     challenger under review (now champion only); a changed rule file did not show in lineage (now hashed); a partly
     recorded lineage could read "current" (now "current where checkable"); `feedback_report.py` deleted whatever
     `--log` pointed at (now it refuses any log that is not its own SIMULATED log).
   - P6-PACKS (10 findings): the packs implied inventory models were monitored; quoted SR 26-2 footnote 3 only in
     part; left out the third-party pretrained OCR and retrieval models; called the LLM migration certificate
     enforced when it is a manual step; let the Tier 3 rule claim monitoring that no code does; misstated why the LLM
     measures are not measured; and said no real records were involved although the public datasets are
     de-identified real records.
4. **Fix round with independent verifiers.** Four fixers worked in separate worktrees (`p6fix/*`); verifiers who had
   not written the fixes re-checked each finding.
5. **Second round** (`50acabe`, `0bed57e`): what the verifiers found in the fixes. A row that already failed its
   absolute target was reported as MISSING when a precondition could not be checked (it now stays FAIL); the
   decision-logic hashes left out the calculations, the vocabulary, `security/rbac.py` and the override reason list
   (now included); `register` allowed a second model in use in one family (now refused); the override indicator now
   names the key it read, the packs' NAIC row mentions the reviewer indicator, and the OCR count names its run.
6. **Regeneration in dependency order** with `make governance-results` at `50acabe`, clean tree; packs re-rendered;
   committed in `2d0a0b5`.

## 7. Checks

These are checks, not metrics, so they are not in `results/*.json`.

| Check | Result |
|---|---|
| Phase 6 tests offline at `2d0a0b5` on 2026-10-26 (`uv run pytest -o addopts='' -m 'not integration'` on `tests/governance` (which also holds the pre-Phase 6 freeze proof, provenance and migration gate tests), the two agent test files, `tests/web/test_web_lender.py` and `tests/test_render_reports.py`) | 376 passed, 2 deselected (integration) |
| `uv run python scripts/release_gate.py` at `2d0a0b5` (reads committed JSON; writes nothing) | exit 0; 11 PASS, 0 FAIL, 0 MISSING, 4 NOT_MEASURED |
| GitHub Actions on the push of `2f9f4d9` (run 40362304729) and of `2d0a0b5` (run 40377561388) | `lint-and-test`, `release-gate`, `audit-deps` and `scan-secrets` all succeeded in both runs |

## 8. Limits and open items

- **No LLM.** The LLM memo citation accuracy, the unsupported-claim rate and the per-layer injection attack success
  rates are not measured; their gate rows are NOT_MEASURED. Once a run exists, a row is MISSING (which blocks) until a
  reviewed baseline includes the new value; from then on it is compared.
- **No Lending Club data or credit model in this checkout.** Champion/challenger and the outcome join ran on
  SYNTHETIC Lending Club-shaped loans whose years share one distribution; real-data champion/challenger, monitoring
  of the credit model and outcome analysis on real loans are not measured.
- **No real time axis for insurance.** freMTPL2 has no dates, so streams are random draws from one held-out pool and
  every drift is injected. Outcomes are assumed known at the end of each window (no claim reporting lag), which a real
  book would add to every calibration delay.
- **The inventory models are not monitored.** The monitor watches a separate demonstration GLM, and it computes no
  discrimination metric.
- **Blind spots of the monitor at this window size.** The mild covariate shift was never detected (0/300) and a 10%
  claim-rate rise was detected in 49 of 300 streams by the end of the stream (at most 5 windows after the drift
  started). Score drift never reached alert in any scenario.
- **Indicators are single values**, read from the last evaluation runs; they cannot show drift over time. The
  low-confidence field share has no source metric.
- **SIMULATED reviewers and claimed roles.** The override numbers describe made-up probabilities; roles are strings in
  the request (no identity provider), and one person holds every role. Overrides are not joined to outcomes (the
  SYNTHETIC agent applications have none).
- **No command to close a review.** `close_review` exists in `model_status.py` (model validator only) but has no
  script, so the demonstration GLM stays `under_review`.
- **Local state.** The registry, the alert log and the SIMULATED decision log live under `data/governance/`
  (gitignored), so they are per machine, not shared.
- **Release gate.** Dirty-tree provenance is only a warning (5 today); evidence measured on another prompt, corpus or
  model than HEAD (STALE) is not checked; a change of LLM or prompt needs a migration certificate signed by a model
  validator, but that is a manual step no CI job checks.
- **Lineage.** The code commit is recorded, not compared; files outside the decision logic (for example the evidence
  engine) are not hashed.
- **No independent validation** and no fairness monitoring (question 8).
- **Regulatory text.** The NAIC bulletin text and the OCC and FDIC identifiers were not checked against local copies
  (section 9).

## 9. Regulatory references

Taken from the packs, whose regulatory facts were checked as stated there.

- **SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026**, "Revised Guidance on Model Risk Management" (Federal
  Reserve, OCC and FDIC, April 17, 2026), which supersedes SR 11-7 and SR 21-8
  ([Federal Reserve](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm),
  [OCC](https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html),
  [FDIC](https://www.fdic.gov/news/financial-institution-letters/2026/agencies-revise-interagency-model-risk-management-guidance)).
  The date, the supersession, the applicability statement, the model definition and footnote 3 were checked against
  the local copy of the Federal Reserve PDF (`data/raw/regulatory/frb_sr_26_2.pdf`); the OCC and FDIC identifiers come
  from the agencies' pages listed in the build plan and were not checked against that copy.
- **NAIC Model Bulletin, "Use of Artificial Intelligence Systems by Insurers"** (adopted December 4, 2023;
  [bulletin](https://content.naic.org/sites/default/files/cmte-h-big-data-artificial-intelligence-wg-ai-model-bulletin.pdf.pdf)).
  Its text was not checked against a local copy (the scripted download returned HTTP 403); the insurance outline uses
  only the summary in the build plan and quotes no section of it. Adopted wording varies by state.
- **New York DFS Insurance Circular Letter No. 7** (July 11, 2024,
  [circular](https://www.dfs.ny.gov/industry-guidance/circular-letters/cl2024-07)), checked against its local copy;
  used for fairness testing.

Applicability of any of these depends on the institution and its risk profile. This project is a demonstration, not a
compliance program; the packs' "status here" columns describe the demonstration and are not an assessment against
any guidance.

## 10. Likely interview questions

1. **What is the difference between drift and calibration decay, and why monitor both?**
   Drift means the inputs change: a different mix of applicants arrives. Calibration decay means the link between
   inputs and outcomes changes, so the model's predicted level is wrong even for the same inputs. Drift is visible
   at once from the inputs (PSI); calibration needs outcomes, which arrive months later. The SIMULATED validation
   shows they are different signals (section `monitoring`, `validation`): when young drivers made up 30% of each
   window, input drift alerted in 300 of 300 streams but the calibration ratio in only 4 of 300, because the GLM uses
   driver age (in bands) as an input and stays roughly calibrated on the new mix; when claims rose 25% with unchanged inputs, input and
   score drift alerted in 0 of 300 streams and the calibration ratio in 298 of 300. Drift alone is a reason to look,
   not to retrain.

2. **Why version prompts and decision logic, not only the model?**
   A decision here comes from a model, a prompt, rule files, data and an evaluation set. Changing the memo prompt or an
   approval limit in `hitl.py` can change behaviour as much as a new model, and an audit question ("what produced this
   decision?") cannot be answered without them. So each record stores the SHA-256 of the prompt file, the
   decision-logic files, the dataset checksum lines, the eval sets and the results files, and `scripts/lineage.py`
   flags anything that changed since. Hashing files rather than comparing the commit means only relevant changes are
   flagged. On the SIMULATED log all 65 records were current where checkable (section `lineage`), which is expected
   right after writing; it shows the mechanism works.

3. **How does champion/challenger work here, and why does nothing promote itself?**
   Both models use the same code; the challenger sees one more year of loans. Both are scored on the same 6,611
   finished SYNTHETIC loans of 2017-2018 that neither saw, and a paired bootstrap gives the difference: AUC -0.0003
   [-0.0029, 0.0025] (section `champion_challenger`). The rule, fixed before the run, needs the AUC gain's lower
   bound above 0 and ECE not worse by more than 0.01, so the recommendation is `keep_champion`. Even a passing
   comparison is one window; a validator also looks at stability across windows and segments and at what changed in
   features, data and code. A person with the authority signs: `promote_model.py` refuses every role except
   `model_validator`, and every status change is kept with who, when and why.

4. **What does the release gate check, and why never "injection resistance = 100%"?**
   It compares the latest results with a reviewed baseline and blocks a regression beyond a stated tolerance: the
   conservative CI bound for model quality (credit AUC lower bound, deviance upper bounds), fixed targets for controls
   (every required escalation, every application at the human checkpoint, zero decisions without a human, zero
   untraced numbers). It checks run scope first and never counts a missing measurement as a pass. Result: 11 PASS,
   0 FAIL, 0 MISSING, 4 NOT_MEASURED (section `release_gate`). For injection, a finite payload set can only show the
   attack success rate on those payloads for that model; new attacks are not in it, and even zero successes leave an
   interval above zero. So the gate asks that the measured per-layer rate not rise above the baseline's rate plus a
   tolerance (0.05, three of the 60 frozen payloads), and the containment tests (injected text cannot make a decision)
   must pass in CI. Those rows are NOT_MEASURED today because no model run exists.

5. **What does override analysis tell you, and what did you measure?**
   An override is an approve or decline against the reference recommendation; a referral never counts. Every override
   needs a code from a fixed list (so overrides can be counted and compared) plus optional free text. The analysis
   looks for concentrations: one reviewer, one segment, one reason. `data_error_in_model_input` points at the data
   pipeline, `information_not_available_to_model` at a missing feature. The strongest test, whether overridden files
   later perform better or worse than the model predicted, needs outcomes and is not measured here. The measured rate,
   0.1385 [0.0615, 0.2308] (section `feedback`), comes from a SIMULATED reviewer with a made-up override probability
   of 0.15, so it shows the recording and counting, not real reviewer behaviour; the monitor reads it at level warn.

6. **How did you tier the models?**
   SR 26-2 (section III) ties materiality to purpose and exposure. The rule here is a design choice: a model whose
   output feeds a decision about one applicant (credit PD, claim frequency, claim severity) is Tier 1 as designed, a
   portfolio-risk model Tier 2, a method demonstration (German Credit fairness) Tier 3. With no exposure (no
   portfolio, no production use) every model is Tier 3 now. For immaterial models SR 26-2 says risk management may
   consist of identifying them and monitoring their performance and the conditions under which they may become
   material; only the identifying is done here. The tier as designed says what a real deployment would need first:
   independent validation, monitoring, outcomes analysis, champion/challenger, gate rows, yearly review.

7. **What do regulators expect?**
   For banks, SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026 (April 17, 2026) describes risk-based model risk
   management. Its sections, as mapped in the bank pack, cover the model definition and scope, materiality (purpose
   and exposure), effective challenge by independent experts, model development and use, validation (conceptual
   soundness, outcomes analysis, ongoing monitoring), governance and controls with a model inventory and
   documentation, and vendor and other third-party products. It says it "does not
   set forth enforceable standards or prescriptive requirements", the agencies expect it to be most relevant to
   banking organizations with over $30 billion in total assets, and its footnote 3 places generative and agentic AI
   outside its scope, which is why the LLM extraction and the agent have their own section in the bank pack. For
   insurers, the NAIC Model Bulletin (adopted December 4, 2023) expects a written AIS Program covering governance,
   risk management and internal controls, and third-party AI systems, in proportion to the potential harm to
   consumers (from the build plan's summary; the text was not checked against a local copy). Whether any of this
   applies depends on the institution and its risk profile; this is a demonstration and claims no compliance.

8. **Do you monitor fairness?**
   No; fairness monitoring is not measured. What exists is the Phase 1 method demonstration on UCI German Credit
   (adverse impact ratio, equal-opportunity difference and a proxy check, each with a bootstrap interval; section
   `fairness_demo` of `results/risk_metrics.json`), a small 1970s German dataset. freMTPL2 and Lending Club have no
   protected attributes, so an adverse impact ratio cannot be computed for the insurance models and a proxy
   assessment on freMTPL2's geographic features (Region, Area, Density) is not measured. Doing it for real needs
   protected-class data or a reasonable imputation (NY DFS Circular Letter No. 7 limits quantitative assessments to
   protected classes for which data are available or may be reasonably imputed), then per-window rates by group with
   intervals and thresholds in `config/monitoring.yaml`, and override rates by group.

9. **How do you know the monitor works and is not just noise?**
   It was run on streams where the truth is known (section `monitoring`, `validation`). With no drift, 1 of 300
   streams raised a false alert: 0.0033 [0.0000, 0.0100], exact [0.00008, 0.0184]. The two strong covariate shifts were
   caught at the first drifted window in 300 of 300 streams each, and a 25% claim rise in 298 of 300 streams, with a mean
   delay of 0.80 [0.67, 0.93] windows among detected streams. Its blind spots are reported too: the mild shift 0 of
   300, a 10% claim rise 49 of 300. The warn level fired in 262 of 300 no-drift streams, which is why warn is shown
   but never acted on. The streams share one pool and the thresholds are design choices, so this holds for this
   model, window size and these scenarios only.

10. **How do outcomes get back to decisions, and what is the catch?**
    Outcomes are joined by application id; a decision without an outcome stays pending (12,175 of 18,786 here,
    section `outcomes`). Then: default rate per decision (approve 0.2180 [0.2060, 0.2310]), approved-and-defaulted
    counts, and whether the PD recorded at decision time matched reality (observed / expected 1.0245, ECE 0.0138
    [0.0099, 0.0261]). The catch is selective labels: in a real book a declined or referred file that is never booked
    has no outcome, so you cannot measure how good declines were. Here every decision type has outcomes only because
    the SYNTHETIC rows are all issued loans, and the decisions come from a stated SIMULATED rule. Real outcome
    analysis needs the Lending Club data and real decisions, and is not measured.

# Phase 4 walkthrough: the Evidence-to-Action Engine

Written on 2026-10-14 so that both Rahul Vajja and Bhavani Adula can explain this phase in their own words.

Phase 2 turns a packet of business documents into checked, cited facts. Phase 4 answers the next question a reviewer
asks: **what does the recommendation rest on, how solid is it, and which single piece of evidence should we ask for
next?** For one case it builds:

- an **evidence ledger**: every value the recommendation depends on, each with a status (VERIFIED, CONFLICTING,
  UNKNOWN, MODEL_ESTIMATE, plus the Founder Studio statuses USER_STATED and ASSUMPTION) and its provenance;
- the **reference policy's calculations** (DSCR, loss ratios, ...) as ledger claims, with an input hash;
- **stress tests and counterfactuals** (revenue -10/-20/-30%, rate +200/+400 bps, claim frequency and severity
  +10/+25%, stated revenue := verified, stated TIV := schedule TIV) and the **decision stability** they imply;
- the **open items** (open CONFLICTING or UNKNOWN problems) and a **ranking of evidence requests** by a rules-based,
  scenario-based value-of-information score (VOI);
- an append-only, hash-chained **decision record** with Markdown and static HTML views.

Nothing in Phase 4 approves, declines or decides. The recommendation is the deterministic reference policy's
(`agent/policy.py`), and a person makes every decision. No language model runs in this phase. Every case is
**SYNTHETIC**: Northwind Business Credit and Harborline Mutual are fictional institutions.

## Honest status

- **Measured** on the 60 SYNTHETIC test packets of Phase 2 (30 credit, 30 insurance), in two modes: the generator's
  perfect (oracle) extraction, which isolates the engine, and the Phase 2 pipeline (rules extractor + Tesseract OCR),
  which is end to end. Results: section `packets` of `results/evidence_metrics.json`, written by
  `scripts/eval_evidence.py`. The innovation S5 simulator writes section `simulator` of the same file.
- **Provenance caveat.** Both sections record `git_sha` `95fbb2a` (the Phase 4 commit) with `git_dirty: true`
  (simulator 2026-10-14T07:32:49+00:00, packets 2026-10-14T07:36:54+00:00): they were re-run from the working tree
  that holds the two review rounds below, before those changes were committed. The second round changed the
  claim-status numbers (a check outcome over an unread value is now UNKNOWN; see the table below), left every
  other packet metric identical, and added the simulator's `empirical_dev_smoothed` and `oracle_shares` rows and
  the `latent_without_prior` diagnostic; every earlier simulator number is identical. A commit hash alone does not
  reproduce them until `make simulate-requests` and then `make eval-evidence` are re-run on a clean tree after the
  next commit. **That clean-tree re-run is still pending.**
- **The claim-status table was revised after a test run.** The first test run used table v1. An independent review
  then showed that the ledger printed calculations over imputed defaults as VERIFIED and that v1 copied that rule.
  The ledger was fixed and table v2 adds dependency-aware expectations. v2 was checked on the dev split before the
  test split was re-run, but the v1 test numbers had been seen, so the test split is no longer untouched for the
  table. The results file says so in `protocol.development_note`.
- **Not evaluated by design:** model PD and expected loss. The Phase 1 models do not apply to these commercial
  packets (the credit model uses consumer Lending Club features; the insurance models use personal motor freMTPL2
  rating features), so both are UNKNOWN with the reason "not evaluated: no Phase 1 model is trained on features
  these SYNTHETIC commercial packets contain". A real model can be plugged in through the `ModelEstimator`
  protocol; tests use a fake estimator labeled fake.
- **Not used:** no LLM, no cloud service. 0 LLM calls.

## How one case is processed

```
PacketResult (Phase 2) / PacketExtraction / CaseState (a typed application)
   |
 1 state.from_packet        docintel.to_application -> application JSON + field provenance + unread paths;
   |                        failing checks -> DocumentCheckFlag ("confirmed"); embedded instructions -> InjectionFlag
   |
 2 state.evaluate           agent/policy.py evaluate_policy with the SAME PolicyInputs every time
   |                        (clean screening assumption, no risk model, document check flags)
   |
 3 ledger.build_ledger      facts (ledger_facts), calculations (CALC-*), unread values (UNKNOWN), missing documents
   |                        (DOC-*), checks (CHK-*), model claims (MODEL-*), assumptions (ASSUME-*), assertions
   |
 4 scenarios.run_scenarios  levers (scenario_levers) -> state.apply -> the same policy -> deltas and drivers
   |                        -> decision_stability: smallest flipping shock per family, or stable / undetermined
   |
 5 open_items               open CONFLICTING / UNKNOWN problems, each tied to a catalog request and its template
   |
 6 voi.rank_requests        each plausible resolution applied, the policy re-run, VOI per request, a reason sentence
   |
 7 supplementary            display-only views (monthly revenue, stress grids, yearly loss ratios, trends)
   |
 8 record                   one hash-chained decision record (DecisionLog) + Markdown / HTML views
```

## Every file, what it does and why

### Case state and policy integration

- `src/uwcopilot/evidence/state.py`: the `CaseState` (application JSON, document check flags, injection flags,
  screening outcomes, check statuses, field provenance, unread paths, substituted paths). `evaluate()` builds the
  same `PolicyInputs` for every baseline, scenario and resolution, so every comparison is like for like.
  `apply()` applies resolution or scenario levers (the `docintel/levers.py` vocabulary plus `check:<id>`); every
  application path a lever changes loses its document provenance and is recorded in `substituted_paths`.
  `from_packet()` maps a Phase 2 packet; `check_indicators()` marks a name that differs between two bank statements.
- `src/uwcopilot/agent/policy.py` (edited minimally): `PolicyInputs.document_checks` (empty by default; then the
  output is byte-identical, which a test checks on every committed application). Confirmed checks raise findings:
  balance continuity and tax return vs P&L -> R-NBC-13 Fraud Operations (NBC-9.1); name and FEIN -> R-NBC-09 KYC
  Operations (NBC-6.2); ownership -> R-NBC-08 (NBC-6.1); loss-run totals -> R-HM-16 SIU (HM-11.1); insurance
  identity checks -> R-HM-12 (HM-8.1). A flag's `indicators` add guideline-specific findings: a business name that
  differs between two statements is also an NBC-9.1 integrity indicator (R-NBC-13), on top of R-NBC-09.

### The evidence ledger (shared with Founder Studio)

- `ledger_models.py`: `Claim`, `Assertion`, `Ledger`, the builder (`Ledger.builder().add_claim(...).build()`) and
  the Studio helpers `user_stated_claim`, `assumption_claim`, `unknown_claim`. Validation ties status to
  provenance (VERIFIED only from a document, calculation, policy or sourced public data; USER_STATED only from user
  input or the application; ASSUMPTION only from an assumption), requires finite numbers for numeric displays,
  refuses a probability shown as `pct1`, and keeps an assumption inside its own range.
- `ledger_facts.py`: application facts, values the extraction could not read, location fields no document prints,
  values a lever substituted, missing checklist documents and document-check claims. A document value is VERIFIED
  only with at least one citation (document, page, line); one whose citation the mapping did not record is UNKNOWN
  with that reason (the value read is kept in `provenance.details.uncited_value`). A check's claim (CHK-*) is a
  rule's outcome, so its provenance kind is "calculation", and like a calculation it is UNKNOWN when it compared a
  value that is UNKNOWN (`ledger_tables.CHECK_INPUT_PATTERNS` lists what each of the five comparison checks reads).
  `test_ledger.py` checks, on one generated packet for each of the 17 problem families the generator can plant
  (9 credit, 8 insurance, `missing_document` on both lines), that no VERIFIED document claim lacks a citation.
- `ledger_assertions.py`: one assertion per policy outcome (passed, finding, not evaluated) with its three phrases.
- `ledger_tables.py`: data only: which facts become claims, unit and display formats, which claims a calculation
  or a comparison check reads, which checks are gaps, the phrases, and which unread values a passed rule rests on
  (including the GL limits for the HM-1.1 capacity and HM-7.1 GL rules).
- `ledger.py`: calculation claims (with `depends_on_conflicting`, `depends_on_unknown`, `depends_on_assumption`),
  model claims and `build_ledger`. It satisfies the renderer protocol of `provenance/renderer.py`, so a memo slot
  `{{claim:CALC-dscr}}` is filled from the ledger.
- `src/uwcopilot/provenance/renderer.py` (one line of behavior added): an UNKNOWN claim that carries a value is
  printed with the marker "(imputed, not read from the documents)".

### Calculations, scenarios and the supplementary views

- `calculations.py`: re-exports the agent formulas (DSCR, NOI, debt service, verified revenue, loss ratios, TIV) and
  adds only what was missing: `stressed_dscr` (any revenue shock; equal to the policy at 20%), `rate_stressed_service`
  and `rate_stressed_dscr` (any bps; equal to the policy at +200), the monthly revenue view, stated-vs-verified DSCR,
  yearly loss ratios, frequency and severity by year with a least-squares trend, TIV reconciliation, and a stress
  multiplier labeled "a stress multiplier, not a model".
- `scenario_levers.py`: the exact mechanics of each scenario (which application fields change and how).
- `scenarios.py`: runs each scenario through the same policy, reports the decision metric (DSCR, or the highest
  5-year loss ratio of the quoted lines), the recommendation, and the drivers (trigger, rule and calculation
  differences). `decision_stability` gives the smallest flipping shock per family. A shock that changes nothing or
  leaves the decision metric not computable is "not tested", so a case with no tested shock is "undetermined".
- `supplementary.py`: display-only views for the reviewer (monthly eligible revenue, the annualized estimate, the
  stress grids, yearly loss ratios, trends, the stress-multiplier view). Withheld when the ledger cannot verify the
  figure they build on. Not ledger claims, so a memo cannot cite them.

### Open items and next-best evidence

- `open_items.py`, `open_items_insurance.py`, `open_item_models.py`, `open_item_params.py`: one open item per open
  problem (missing month, missing documents, revenue gap, name / FEIN / owner mismatch, balance discontinuity, debt
  payments, loss-run totals, paid/reserve, TIV, claims history, missing loss run, embedded instructions), each with
  its catalog request, its resolution template and the case values that fill the template.
- `requests.py`: reads `config/evidence_requests.yaml` (the only requests the engine may recommend, with cost
  weights) and `config/resolution_templates.yaml` (plausible resolutions with prior weights). Priors and cost
  weights are planning assumptions written by the project author. An option whose `check:` lever no policy rule
  reads is left out (today: a missing document that arrives "inconsistent").
- `voi.py`, `voi_models.py`, `voi_reasons.py`: the VOI score, the ranking and the one-sentence reasons.

### Records, the engine and the scripts

- `record.py`, `record_views.py`: the decision record (packet hash, ledger, contradiction graph, calculations,
  scenarios, stability, open items, next-best evidence, versions, reviewer fields, timestamps,
  `supersedes_record_id`), appended with `agent.decision_record.DecisionLog`. Views escape every value.
- `engine.py`: `run_engine()` runs steps 1 to 8 for one case. `EngineResult.calculations` is a read-only property
  that returns the policy's calculations (one copy, from `policy_result`), not a second stored field.
- `scripts/run_evidence.py` (`make run-evidence PACKET=PKT-C-024`): one case, a readable report, the record.
- `scripts/eval_evidence.py` (`make eval-evidence`) with `evaluation.py`, `evaluation_packet.py`,
  `evaluation_requests.py`, `evaluation_calcs.py`, `evaluation_summary.py`: the Phase 4 F evaluation.
- `scripts/simulate_requests.py` (`make simulate-requests`) with `simulator.py`, `simulator_cases.py`,
  `simulator_models.py`, `simulator_summary.py`, `simulator_diagnostics.py`: innovation S5 (see
  `docs/walkthrough_innovations.md`). These simulator modules and `simulate_requests.py` read the generator's hidden
  truth; they are evaluation code, allow-listed by name in `tests/docintel/test_synthetic_imports.py`, and no engine
  module imports them.
- Tests: `tests/evidence/` (one file per module, plus `evidence_case_helpers.py`, which builds JSON-only packets
  with master seed 4040, outside every evaluation split). `test_evidence_module_units.py` imports the four modules
  that the other files only reach through the engine (`voi_reasons`, `ledger_assertions`, `supplementary`,
  `open_items_insurance`) and tests them directly; `test_ledger.py` imports `ledger_facts` directly.

## Design choices worth defending

1. **Every calculation is deterministic.** SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026 ("Revised Guidance
   on Model Risk Management", Federal Reserve, OCC and FDIC, April 17, 2026) excludes "simple arithmetic
   calculations" and "deterministic rule-based processes" from its model definition. Keeping DSCR, NOI, loss
   ratios and the reconciliations plain arithmetic is one reason they stay outside model risk management, and it
   makes every figure reproducible from its inputs (the claim's `inputs_sha256`). The guidance is "expected to be
   most relevant to banking organizations with over $30 billion in total assets"; whether it applies depends on the
   institution and its risk profile. This project is a demonstration, not a compliance program.
2. **One reference policy for everything.** Baseline, scenarios, resolutions and the simulator all call the same
   `evaluate_policy` with the same `PolicyInputs`. A second rule implementation would drift.
3. **Nothing is imputed silently.** A value the documents did not provide is UNKNOWN. When the agent's model
   needed a default, the claim says "imputed" and keeps the default out of its value, so a memo prints "not
   available". A calculation the policy ran over such a value is UNKNOWN too, and a rule that passed on such a
   value is shown as "not evaluated". This came from the review: before it, leverage over a $0 imputed debt balance
   printed as a VERIFIED figure.
4. **A value is VERIFIED only when it can be traced to a page.** A document value without a recorded citation is
   UNKNOWN, and the insurance GL limits now record their source like every other form field. An empty GL limit is
   "not requested" only when the form's coverage was read and has no general liability; on a GL or package request
   it is an unread value (UNKNOWN), and a capacity pass that rests on it is shown as not evaluated.
5. **A hypothetical is never data.** A value a resolution or scenario substituted is an ASSUMPTION with no
   document citation; the contradiction it replaced is closed; calculations over it list it.
6. **One ledger for both products.** Founder Studio builds ledgers with the builder API and the USER_STATED and
   ASSUMPTION statuses; the status-provenance rules mean an assumption can never be presented as data.
7. **VOI is a decision-value score, not an economic model.**
   VOI(request) = sum over resolutions r of p(r) x 1[recommendation(r) != current] x size(r) / cost weight, with
   size(r) = 1 + min(|change in the decision metric|, 1.0) (DSCR as is; a loss ratio as a fraction). It is exactly
   0 when every plausible resolution keeps the recommendation, however interesting the answer would be. The
   canonical example is a debt-payment mismatch: NBC-4.1 already uses the higher of the scheduled and observed
   payments, so neither answer can move the DSCR.
8. **Stability is only claimed when something was tested.** A shock that adds zero claims, or leaves the DSCR /
   loss ratio not computable before and after, is "not tested"; a case with no tested shock is "undetermined".
9. **Records are append-only and corrections are new records.** A correction needs a reason, must supersede a
   record that is in the log, and cannot change identity fields; the hash chain detects any edit.
10. **Document text is untrusted.** Views escape every value; the Markdown view breaks every GitHub auto-link
    shape ("://", "@", "www."), checked with pandoc in a test; the HTML view has a script-forbidding CSP.

## What was measured

All numbers are copied from `results/evidence_metrics.json`. Intervals are packet-level (or case-level) percentile
bootstrap 95% intervals, 1,000 resamples, seed 42.

### Packets (section `packets`, 60 SYNTHETIC test packets)

| Metric | Oracle extraction | Pipeline extraction |
|---|---|---|
| Packets the engine could evaluate | 60 / 60 | 50 / 60 (0.8333 [0.75, 0.9167]) |
| Claim-status agreement (table v2, same-author rules, not held out) | 0.9935 [0.9857, 0.9987] (2,309 / 2,324) | 0.9703 [0.95, 0.9852] (1,926 / 1,985) |
| ... on claims expected CONFLICTING or UNKNOWN | 0.9864 [0.9696, 1.0] (218 / 221) | 0.9052 [0.8305, 0.9621] (191 / 211) |
| Benign vs unsupported explanations | 0.975 [0.9167, 1.0] (39 / 40) | 0.9 [0.7872, 0.9768] (36 / 40) |
| Calculation agreement with the truth | 1.0, exact interval [0.9938, 1.0] (590 / 590) | 0.8271 [0.7332, 0.9078] (488 / 590) |
| Top request, answered with the truth, changes the recommendation | 0.4186 [0.2856, 0.5714] (18 / 43) | 0.3529 [0.2121, 0.5173] (12 / 34) |
| ... when that request's VOI was 0 | 0 / 19 | 0 / 19 |
| Next-best evidence top-1 agreement (CIRCULAR) | 26 / 26 | 17 / 17 |
| Stable / unstable / undetermined | 40 / 10 / 10 | 33 / 8 / 9 (of 50) |
| Stable share | 0.6667 [0.5333, 0.7833] | 0.66 [0.5098, 0.7843] |

- **Claim-status agreement is agreement with the documented status rules, written by the same author as the
  ledger.** A rule that is wrong in both tables is scored as correct. The review showed exactly that, which is why
  table v2 exists. Excluded by design (counted, not scored), oracle mode: 60 MODEL-* claims, 60 ASSUME-* claims and
  408 undocumented location-field claims.
- **The remaining oracle mismatches** (15): 3 embedded instructions the scanner missed (expected CONFLICTING, shown
  VERIFIED); 8 claims of one packet (PKT-C-116) whose planted balance problem changes a printed statement total, so
  the extraction marks that month unreliable and the ledger withholds every revenue figure built on it, and the
  revenue check's pass too; and 4 claims-history checks (PKT-I-028, PKT-I-073, PKT-I-080, PKT-I-084) that passed
  against one of the two readings of a loss run whose summary differs from its claim lines, so the ledger shows the
  pass as UNKNOWN. Table v2 expects VERIFIED for all 12: it has no rule for these variants (none occurred on the dev
  split), and it was not tuned on the test split.
- **Effect of the second review round.** Making a check outcome over an unread value UNKNOWN (it was VERIFIED)
  moved 5 oracle claims and 6 pipeline claims from VERIFIED to UNKNOWN where table v2 expects VERIFIED: oracle
  agreement went from 0.9957 [0.9894, 0.9996] (2,314 / 2,324) to the value above, pipeline from 0.9733
  [0.954, 0.9875] (1,932 / 1,985). Recommendations and every other packet metric are identical. The table was left
  frozen; these are scored as disagreements, not re-tuned.
- **Next-best-evidence agreement is CIRCULAR:** the generator's resolving request and the engine's request rules
  were written by the same author. It shows that two hand-written tables match, not that the ranking is useful.
  Innovation S5 is the non-circular evaluation.
- **Stability.** Shocks not tested in oracle mode: 24 with a decision metric that is not computable before or after,
  15 that change nothing, 20 that cannot be built (for example no verified revenue). The committed results before
  the review fixes (commit `bf33e26`) read 50 stable / 10 unstable / 0 undetermined in oracle mode; the 10 packets
  that moved to "undetermined" had no shock that could test anything.
- **Paired, pipeline minus oracle:** claim-status agreement -0.0233 [-0.0422, -0.0093]; calculation agreement
  -0.1729 [-0.2668, -0.0922].

### Simulator (section `simulator`; innovation S5 summary)

On the 370 test cases without the held-out family, documented priors: the VOI policy made 0.3838 requests per case
against the checklist's 1.0703, a paired cost-weighted difference of -1.2689 [-1.4515, -1.0932], with a decision
error difference of +0.0135 [0.0, 0.027]. The S5 headline metrics: requests to a stable decision, VOI minus
checklist, -0.0529 [-0.0808, -0.0279] (n = 359), and a zero-value share of requests of 0.0 for VOI (by construction)
against 0.6136 [0.5631, 0.6595] for the checklist. Three rows replace the documented priors with measured shares
of the true answers. `empirical_dev` (development-split shares, unsmoothed) is NOT a correct-prior reference: an
answer never seen on the small development sample gets no prior, which removes the true answer for 19 of the 413
main test problems (`diagnostics.latent_without_prior`); its decision error difference was +0.0189 [0.0027,
0.0378]. With add-one smoothing (`empirical_dev_smoothed`) no answer is removed and the rows equal the documented
ones. The correct-prior reference is `oracle_shares` (each group's own shares, which no underwriter has): +0.0162
[0.0027, 0.0324]; VOI's errors other than undetected planted problems were all stops at VOI 0 (7), while the
checklist's 5 were all undetected problems, so better priors did not remove them. Two departures from
the S5 design are disclosed in the results: the cases are not capped at 4 open items (2 test cases start with 5
open requests; the oracle skips them), and the x0.5 / x1.5 prior rows cannot change which requests VOI makes. The
full story, with its caveats, is in `docs/walkthrough_innovations.md`.

## What is not measured, and what is not claimable

- Real-world accuracy of anything: the packets are SYNTHETIC and written by the project author.
- Model PD and expected loss (UNKNOWN by design; no Phase 1 model applies).
- That the ranking is optimal or that VOI is an economic model; real time or cost savings.
- Extraction accuracy in the simulator (it uses the oracle extraction).
- That a memo built from this ledger is correct or useful: no memo is generated in this phase.

## Review fixes (independent review, 2026-10-14)

| Finding | Verdict | Fix |
|---|---|---|
| Calculations over imputed (UNKNOWN) inputs labeled VERIFIED | fixed | `depends_on_unknown`; such a calculation is UNKNOWN, imputed, its number kept in details |
| Rules passed on undocumented location defaults | fixed | UNKNOWN claims per location field; a pass resting on them becomes "not evaluated" |
| Unread values outside the facts table had no claim | fixed | one UNKNOWN `APP-<path>` claim per unread path; a test checks every path |
| VOI crashed on 10+ grouped loss-run lines | fixed | `computable=False` with the reason; the engine degrades |
| Stability counted shocks that tested nothing | fixed | `not_evaluated_code` (no_change, metric_not_computable); "undetermined" |
| "Provided inconsistent" missing-document option had no effect | fixed | option left out (no policy rule reads its lever); priors renormalized |
| Names differing between statements went only to KYC | fixed | indicator adds the NBC-9.1 R-NBC-13 referral on top of R-NBC-09 |
| Ledger after `apply()` cited old pages / kept resolved conflicts | fixed | substituted values are ASSUMPTION, provenance dropped, contradictions closed |
| Annual debt service UNKNOWN with a wrong reason | fixed | its own accurate reason |
| Claim-status table copied the ledger's choices | fixed | same-author label, table v2 with dependency-aware expectations |
| Simulator latents not drawn from the priors, undisclosed | fixed (disclosed) | `diagnostics.latent_vs_prior`, reading notes, reworded variants, CPA cases apart |
| Reveals rewrite the stated claim history (coupling) | fixed (disclosed) | `diagnostics.coupling` lists the held-out cases; reading note; reveal definition |
| Held-out VOI gap is myopia on co-occurring problems | fixed (disclosed) | planted problems per case and `decision_errors_by_kind` |
| Results from untracked code | deferred | clean-tree re-run after the commit (lead) |
| Markdown view auto-linked www. and email | fixed | "@" and "www." broken with entities; pandoc test |
| Claim validation let assumptions pass as data | fixed | status-provenance, numeric, probability and range validators |
| Imputed UNKNOWN printed as a bare figure | fixed | imputed values kept out of value; renderer marker for other producers |
| Three mutations survived the tests | fixed | hand-computed insurance VOI, zero-value clause, oracle cost-weight tests |
| Correction contract not enforced | fixed | reason required, superseded id must exist, identity fields refused |
| Calculation helpers unused | fixed | display-only supplementary views in the record (stated-vs-verified DSCR was already a policy calc) |

Completeness review (2026-10-14, after the Phase 4 commit):

| Finding | Verdict | Fix |
|---|---|---|
| README still described Phase 4 as planned and showed the circular top-3 row | fixed | `scripts/render_reports.py` renders the measured Phase 4 and S5 rows from `results/evidence_metrics.json`; prose updated |
| Six of the ten Prompt 4 G interview topics missing | fixed | the Q&A above follows the ten topics; VOI limits are in the VOI answer |
| VERIFIED document claims without a page (insurance GL limits) | fixed | GL limits recorded through `builder.value`; a document claim without a citation is UNKNOWN; an empty debt schedule cites its total line; check claims are "calculation" claims |
| S5 cap of 4 open items not enforced | disclosed | `design.case_cap` in the results and the S5 walkthrough |
| No correct-prior reference for the robustness rows | fixed (round 2) | `oracle_shares` (each group's own shares) is the reference; `empirical_dev` relabelled; `empirical_dev_smoothed` added |
| Claim-status agreement no longer held out | disclosed | the results note, the README note and this walkthrough say so |
| Prompt 4 C's Phase 1 scenario interface not used | explained | question 4 and follow-up D |
| `EngineResult` has no calculations field | explained | a read-only property returns the policy's calculations |
| Five modules without a direct-import test | fixed | `test_evidence_module_units.py`; `test_ledger.py` imports `ledger_facts` |
| Results produced by uncommitted code | deferred | clean-tree re-run after the commit (lead) |
| Tax return vs P&L item has no resolutions | handed over | `open_items.py` now belongs to another workflow |

Completeness review, round 2 (2026-10-14):

| Finding | Verdict | Fix |
|---|---|---|
| `empirical_dev` called the correct-prior reference, but it removes the true answer for 19 of 413 test problems | fixed | relabelled; `diagnostics.latent_without_prior` counts it; `empirical_dev_smoothed` and the `oracle_shares` reference added; conclusion reworded |
| README showed a zero-width interval [1, 1] for 590 / 590 | fixed | a degenerate bootstrap share prints its exact Clopper-Pearson interval, labelled "exact" |
| CHK-* claims had no dependency links; a pass over an unread value was VERIFIED | fixed | `CHECK_INPUT_PATTERNS`; such a check is UNKNOWN with `depends_on_unknown`; claim-status numbers re-measured |
| README said Phase 2 "not built" and "not measured" | fixed | the document rows are rendered from `results/docintel_metrics.json`; the package table says built |
| DSCR answer described the guideline, not the code | fixed | question 3 now says the code takes the higher of the two totals |
| GL limits optional even on a GL request | fixed | optional only when the coverage was read and has no GL; the capacity and GL passes rest on them |
| The citation guard skipped `missing_document` | fixed | the test runs on one packet per family the generator can plant |
| Nothing committed; results from a dirty tree | deferred | the lead commits and re-runs on a clean tree (see "Honest status") |

## Limitations

- Priors and cost weights are the author's planning assumptions; the S5 robustness rows show what happens when they
  are wrong, around priors that are already not the truths' distribution, and the `oracle_shares` row shows the
  comparison with priors equal to the truths' own shares (see the S5 walkthrough).
- Annual debt service takes the higher of the scheduled and observed payment TOTALS, not per debt as NBC-4.1
  words it (question 3), so it can understate debt service.
- The VOI score is myopic (one request at a time). `voi_lookahead` is an addition that helps; it is not in the
  headline.
- The supplementary views are not ledger claims, so a memo cannot cite them yet.
- The decision records log is local (`data/processed/evidence/decision_records.jsonl`, gitignored); there is no
  access control in this phase (Phase 5 adds roles).

## How to run

```
make packets             # the SYNTHETIC packets (Phase 2), needed by run-evidence and eval-evidence
make run-evidence PACKET=PKT-C-024
make simulate-requests   # innovation S5 (builds its own JSON-only cases; about 2 minutes)
make eval-evidence       # the 60 test packets, both modes (about 3 minutes with 4 workers)
uv run pytest tests/evidence tests/agent -q
```

## Likely interview questions

The first ten follow the ten topics of Prompt 4 G, in its order; the follow-ups cover questions reviewers asked.

1. **How did you design the evidence statuses?** Every value the recommendation rests on is a ledger claim with a
   status and its provenance: VERIFIED (read from a document AND cited to a document, page and line, and not in an
   open contradiction; or a deterministic calculation or rule outcome: one over a disputed value stays VERIFIED as
   arithmetic and lists the dispute in `depends_on_conflicting`, one over an unknown value is UNKNOWN; this holds
   for a document check's outcome too, through the values `ledger_tables.CHECK_INPUT_PATTERNS` says it compares),
   CONFLICTING (an open contradiction involves it; it links the contradiction ids), UNKNOWN (required by the
   policy or a model but not provided; it carries the reason, and an imputed default is kept out of its value),
   MODEL_ESTIMATE (a risk model's output with its name, version and uncertainty; none today), plus Founder Studio's
   USER_STATED and ASSUMPTION.
   Validation ties status to provenance, so an assumption can never be shown as data, and a document value whose
   citation was not recorded is UNKNOWN, never VERIFIED (it cannot be traced to a page). A document check's own
   claim (CHK-*) is a rule's outcome, so its provenance kind is "calculation", not "document".

2. **Why are the calculations deterministic and not a model?** Because they are arithmetic over documented values
   with written guideline rules, and keeping them that way makes every figure reproducible from its inputs (each
   claim stores `inputs_sha256`). SR 26-2 (April 17, 2026) excludes simple arithmetic and deterministic rule-based
   processes from its model definition, which is one reason to keep them out of model risk management.
   Applicability depends on the institution and its risk profile; this is a demonstration, not a compliance
   program.

3. **What is DSCR and how is it computed here (NBC-4.1)?** DSCR = net operating income / annual debt service. NOI
   is the trailing-twelve-month EBITDA from the P&L, reduced dollar for dollar when verified revenue (NBC-3.3
   eligible deposits) is below the P&L revenue; expenses are never reduced. Annual debt service is existing plus
   proposed debt service. NBC-4.1 words the existing part per debt: 12 times the monthly payments on all business
   debt that remains after closing, "taking for each debt the higher of the payment on the debt schedule and the
   payment seen in the bank statements", with MCA remittances at their observed total. The code
   (`agent/credit_calculations.existing_annual_debt_service`) cannot do it per debt, because the application model
   (`DebtServiceInputs`) holds only totals: the schedule's payments summed over its rows, and one observed monthly
   total from the statements (`docintel/deposits.py`). It takes the higher of the two totals,
   `max(existing_monthly_payment_schedule, existing_monthly_payment_observed)`, adds the MCA remittance, and
   subtracts a refinanced payment only when its payoff letter is in the file. The higher of two totals is never
   more than the sum of the per-debt maxima, so this approximation can UNDERSTATE debt service (and overstate the
   DSCR) when one debt's observed payment is higher and another's scheduled payment is. The proposed part: a term
   or equipment loan, 12 fully amortizing payments at the note rate and term; a line of credit, 12 months of
   interest on the full commitment plus 10% of it. The DSCR is computed twice: with verified revenue (the decision
   DSCR, compared with the NBC-4.2 product minimum) and with stated revenue (information only). The engine does not
   compute it itself: it re-exports the agent's formulas and reads the policy's CALC-dscr. If the debt schedule was
   not read, the DSCR is UNKNOWN, not computed on a $0 default.

4. **How did you choose the stress scenarios and their sizes?** The families come from the build plan and the
   guidelines; the sizes bracket the guideline's own stress. NBC-4.5 stresses a loan with a 20% revenue cut (NOI cut
   by the same dollars) and +2.00 percentage points on the rate, so the engine tests 10%, 20% and 30% and +200 and
   +400 bps: the guideline value plus one milder and one harsher step, which shows whether the decision is near its
   edge. Insurance has no single stress figure in the guideline (HM-4.1 / HM-4.3 judge loss ratios, frequency and
   severity), so claim frequency and severity are raised by 10% and 25%, applied to the experience-period claims
   so the policy's own loss-ratio, frequency and trend rules see them. Two counterfactuals ask "what if the
   document is right": stated revenue := verified revenue, stated TIV := schedule TIV. Shock sizes are planning
   choices, not calibrated to any portfolio. Prompt 4 C asked for these to run "through the Phase 1 scenario
   interface" (`risk/scenario.py`); they do not, because no Phase 1 model applies to these commercial packets (see
   follow-up D), so every scenario is judged by the reference policy alone and the risk band is "not evaluated".

5. **What is decision stability, and when do you not claim it?** The smallest tested shock (by size within each
   family) that changes the reference recommendation, or "stable across all tested scenarios". A shock is "tested"
   only when it could show something: a frequency shock that adds zero whole claims, or a rate shock on a case with
   no computable DSCR, is "not tested", and a case with no tested shock is "undetermined", never "stable". In
   oracle mode that moved 10 packets from "stable" to "undetermined" during the review.

6. **How does the value-of-information score work, and what are its limits?** For each plausible resolution of an
   open problem (from `config/resolution_templates.yaml`), the engine applies it, re-runs the same policy and checks
   whether the recommendation changes. VOI = sum over resolutions of prior x 1[recommendation changes] x size of
   the change in the decision metric, divided by the request's cost weight; it is exactly zero when no resolution
   can change the recommendation (a debt-payment mismatch: NBC-4.1 already uses the higher payment). Limits:
   - it is **myopic**: it values one request at a time, so two open problems that each keep a referral both score
     zero although resolving both could change it (`voi_lookahead` is an addition that helps; it is not the
     headline);
   - the **priors** and cost weights are the author's planning assumptions. The generator's hidden truths do not
     follow those priors, which the S5 simulator shows (`diagnostics.latent_vs_prior`), and its `oracle_shares`
     reference row re-runs the comparison with priors equal to the truths' own shares (VOI's extra errors stay:
     they come from its one-step stopping rule);
   - it is a rules-based, scenario-based score, not an optimal or economic model, and it only ranks requests from
     the fixed catalog.

7. **Why not let an LLM decide, or even rank the requests?** Because the decision must be reproducible, explainable
   from written rules and safe against the documents themselves. Packet text is untrusted (it can carry embedded
   instructions; ground rule 8), so nothing a model reads can become an action. The recommendation is the
   deterministic reference policy's, the numbers come from the calculations, and the agent can never approve or
   decline on its own; a person decides. Where an LLM is used later (the memo), it writes prose into slots that the
   ledger fills (provenance-locked generation), so it cannot introduce a number. No LLM runs in Phase 4.

8. **How is the audit trail designed?** One decision record per engine run, appended to a JSON Lines log by
   `agent.decision_record.DecisionLog`. Each record stores the hash of the record before it and its own SHA-256, so
   `verify_chain()` detects an edit, a deletion or a reordering. A record is never edited: a correction or a
   reviewer's action is a NEW record that names the old one in `supersedes_record_id`, must give a reason, must
   point at a record that is in the log, and cannot change identity fields. The record id is a hash of the content
   without the timestamp, so writing the same output twice is a no-op. The record holds the packet and inputs
   hashes, the ledger, the contradiction graph, calculations, scenarios, open items, the ranking, all versions
   (policy, engine, catalog, templates; model "not evaluated"; prompts "none") and the reviewer fields.

9. **What is a benign discrepancy, and how is it told apart from an unsupported one?** Each contradiction gets a
   CANDIDATE explanation from deterministic rules over the numbers the check recorded
   (`docintel/contradiction_rules.py`): benign when the documents themselves explain it (non-revenue deposits
   counted in the stated revenue, a fiscal-year timing difference, a one-digit transcription error in a FEIN, a
   debt-payment difference the NBC-4.1 rule already resolves, an updated schedule mentioned in the cover email);
   unsupported when no document supports the stated value (stated revenue more than 10% above every revenue figure
   the packet has); unknown otherwise, including every integrity indicator. The explanation never decides: it is
   shown to the reviewer, and the open item still asks for the document that would confirm it. Measured as
   benign-vs-unsupported accuracy in section `packets` (table above).

10. **How would an underwriter use the output?** Open the decision record view of a case. Read the reference
    recommendation and, before anything else, the CONFLICTING and UNKNOWN claims: they are what the recommendation
    does not yet rest on. Check decision stability to see how close the case is to its edge. Send the top-ranked
    evidence request, whose one-sentence reason says what it could change and how likely that is; skip a request
    with zero decision value unless policy requires it for other reasons. When the document arrives, re-run the
    engine on the updated packet, and record the decision as a person in a new record that supersedes the old one.

### Follow-up questions

A. **Why is next-best-evidence top-1 agreement 26 / 26 not a result?** The generator's "resolving request" and the
   engine's request rules were written by the same author, so agreement only shows that two tables match. The
   results label it CIRCULAR, the README leaves it out, and innovation S5 evaluates the ranking with a simulator and
   a hidden truth instead.

B. **Why is claim-status agreement 0.9935 not "99.4% accurate"?** It is agreement with the documented status rules,
   written by the same author as the ledger, on SYNTHETIC packets where most claims are expected VERIFIED. It is
   also not a held-out number: table v2 was written after the v1 test-split numbers had been seen
   (`protocol.development_note`; the results note and the README row say so).

C. **What happens when a document is missing but the policy needs a number?** The value is UNKNOWN. If the agent's
   model forced a default (for example $0 of scheduled debt), the claim says "imputed", the default stays out of
   its value, and every calculation over it (DSCR, leverage) is UNKNOWN too, so a memo prints "not available".

D. **How would a real risk model fit in, including stressed estimates?** Through the `ModelEstimator` protocol: it
   maps a validated application to model features and returns MODEL_ESTIMATE claims with the model name, version
   and uncertainty, plus an UNKNOWN claim for every missing or imputed feature. Today it scores only the baseline
   application. Stressed PD or expected loss would need one more step: for each scenario, map the stressed
   application to features and re-score through `risk/scenario.py` (`apply_overrides` / `validate_input_features`),
   which rejects an override outside the model's training range instead of clipping it. That step is not built,
   because no Phase 1 model is trained on features these commercial packets contain; model PD and expected loss stay
   UNKNOWN with that reason, and "SHAP on the stressed input" is not evaluated for the same reason.

E. **How does the ledger stay honest when someone plugs in a "what if"?** `apply()` records every substituted path
   and drops its document provenance. The ledger shows the value as an ASSUMPTION sourced to the lever, closes the
   contradiction it replaced, and lists it in `depends_on_assumption` of every calculation that uses it.

F. **How is document text kept from becoming instructions or links?** It never reaches a decision: findings are
   built from ids and counts, the policy is deterministic, and the views escape every value. The Markdown view also
   breaks the three auto-link shapes (scheme URLs, email addresses, www. hosts), which a test proves with a real GFM
   renderer; the HTML view forbids scripts with a Content-Security-Policy.

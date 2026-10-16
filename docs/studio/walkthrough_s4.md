# Walkthrough: Studio S4, the founder ledger and the provenance-locked narrative

Written on 2026-10-14 so that both Rahul Vajja and Bhavani Adula can explain this phase in their own words. (The
Studio addendum in CLAUDE.md is still a proposal until both owners sign it; this phase follows it anyway.)

**What S4 is.** A founder's plan is full of numbers: what they typed (loan, equity, a rent quote), what Studio
assumed for them (cost shares, wages, the spread over prime), what the public data says about the site, what the
simulation estimates (chances, medians, break-even), and what the SYNTHETIC startup program requires. S4 puts every
one of those numbers into the project's ONE evidence ledger (`uwcopilot.evidence`, built in Phase 4) as a claim that
says how much to trust it, and writes the plan's summary so that it cannot contain a number the ledger does not hold:
the writer (a fixed template, or an LLM) may only place slots like `{{claim:SIM.p_dscr_year2}}`, and a deterministic
renderer fills in the values. Every probability is labelled "simulated, based on stated assumptions", every text ends
with the Studio disclaimer ("Educational planning tool. Not financial, legal or lending advice. Lenders make their
own decisions."), and nothing in S4 approves, declines or recommends a lender.

**What was done and what was not.**
- Done: `build_founder_ledger` (also exported under the build plan's name `ledger_from_plan`); claims for founder
  inputs, uploaded-document values, assumptions, public data, SiteIQ, the simulation, the path search, program
  thresholds and Studio targets; program arithmetic with a "never more certain than its inputs" status rule; checks
  against the SYNTHETIC startup program (NSP sections) and the Studio targets; the deterministic template narrative;
  the LLM path (prompt registry, validation, one retry with the validator's findings, template fallback); the
  `hf_inference` provider in the LLM layer (disabled by default, tested only against a mocked endpoint); a bound
  checker and its mutation test on founder narratives; the 36 golden scenarios; `scripts/eval_studio_evidence.py`;
  tests; Make targets.
- Fixed in the review of the first draft (2026-10-14), each with a test: (1) an edited Studio target (the web form
  lets a founder change them) was checked against a probability the simulation had measured for the default target,
  for example a 24-month cash chance against an 18-month target; now each target uses the probability measured for
  exactly that target (`target_measures.py`); (2) a range the founder edited (`Plan.assumption_overrides`) had no
  claim at all, and the program arithmetic crashed on it; it is now a USER_STATED claim with its range; (3) the
  NSP-6.1 driver check silently used the site demand for any driver it could not find; (4) the break-even month was
  printed as a duration ("after 1 months") although it is a month number; (5) a generated plan's demand range in a
  real metro was labelled "SYNTHETIC city data"; (6) a model run in which the model never answered would have
  reported a fallback rate of 100%; it is now "not measured"; (7) `make studio-narrative-test` deselected the
  hf_inference tests by its `-k narrative` filter; (8) the hf_inference client cited a documentation page with no
  record of reading it; the page was read again and its retrieval time and SHA-256 are now in the docstring.
- Not done here (S6 owns them): the PDF package, `to_application`, the agent handoff and the startup items of
  `evidence/open_items.py`. S4's "share of package numbers with provenance" is measured on the rendered narrative and
  on an evidence appendix of every claim; the PDF itself is measured by S6.
- Never run: the `hf_inference` provider. It can cost money and the owners decided on no extra spending. No document
  in this repository may say it ran. Only its public documentation page was read (section 1).

## 1. Files

### Package `src/uwcopilot/studio/narrative`

| File | What it does |
|---|---|
| `__init__.py` | The public API: `build_founder_ledger`, `ledger_from_plan`, `render_narrative`, `NarrativeResult`, `FounderDocumentValue`, `template_draft`, `check_draft`, `all_prompt_hashes`, `plan_sha256`, `PlanMismatch`. |
| `ids.py` | Every claim and assertion id in one place (prefixes PLAN, FOUNDER, DOC, ASSUME, RATE, SITE, SIM, TORNADO, STRESS, PATH, PROGRAM, TARGET, CALC; assertions start with `A-`). Ids fit the slot grammar. |
| `units.py` | One rule for units and display formats: shares and probabilities become percents (`pct1`), dollars `usd0`/`usd2`, months `months`, counts `int`, small values `ratio2`. |
| `derive.py` | The derived-number status rule (section 3) and small constructors: `derived_claim`, `policy_claim`, `model_claim`, `assumption_range_claim`, `public_data_claim`. |
| `models.py` | `FounderDocumentValue` (a value read from an uploaded document, with page and line), `AttemptRecord` and `NarrativeResult`. |
| `founder_claims.py` | What the founder typed: USER_STATED, or VERIFIED / CONFLICTING when a document backs or contradicts it; owner facts (UNKNOWN when not entered); other debt. |
| `assumption_claims.py` | Every simulator input the founder did not type (profile and metro ranges, public data), a range the founder edited, the build-out assumption, the note rate, and the site's demand range. |
| `site_claims.py` | SiteIQ: overall score (MODEL_ESTIMATE), coverage (VERIFIED calculation), each factor with its locator or its UNKNOWN reason, and the metro inputs. |
| `simulation_claims.py` | The S3 outputs as MODEL_ESTIMATE claims with the run identity (simulator version, seed, paths, months, plan SHA-256). |
| `path_claims.py` | The S5 search: status, probabilities before and after each change-set, each lever's new value. |
| `program_thresholds.py` | The SYNTHETIC program's thresholds (imported from `agent/policy_startup.py` and `policy_credit.py`, never retyped) and the Studio targets as given, as VERIFIED policy claims with their comparator. |
| `target_measures.py` | Which simulated probability answers each Studio target: the simulation's, the path search's (for an edited target), or an UNKNOWN claim that says why. |
| `program_calcs.py` | The program arithmetic (project cost, equity injection, debt service, fixed costs, reserve, projected and stressed DSCR) with the program's own rounding. |
| `program_checks.py` | The checks (assertions) with their NSP sections, trigger ids and the phrases the renderer prints. |
| `founder_ledger.py` | `build_founder_ledger`: assembles the claims with `Ledger.builder()`, checks that plan, simulation, SiteIQ and path search describe the same plan (`PlanMismatch`) and that the simulation has a second year (`SimulationTooShort`). |
| `prose.py` | The plain words around the slots (business type, metro, inputs, factors), each checked with the grammar validator before use. |
| `template.py` | The deterministic slot-only draft (the default writer and the fallback); `required_refs` and `allowed_sections` for the validator. |
| `prompts.py` | The prompt registry (`founder_narrative_v1`, `founder_narrative_retry_v1`, and the template's own id and hash) and the request the LLM gets. |
| `generate.py` | `render_narrative`: template by default; for a model, validate, retry once with the findings, render, audit, fall back to the template. |
| `checker.py` | The bound check (each number must equal a claim its own sentence cites) and the mutation test on founder sentences. |
| `golden.py` | The 36 golden SYNTHETIC scenarios (6 business types x the SYNTHETIC metro and the 5 real metros in their current mode). |
| `evaluation.py` | Per-scenario measurements, ledger totals, and pooled rates with Wilson and scenario-bootstrap intervals. |

### LLM layer

- `src/uwcopilot/llm/hf_inference_client.py`: the Hugging Face Inference Providers client (the OpenAI-compatible
  chat-completions router). The base URL, path, header and token name come from the public page
  https://huggingface.co/docs/inference-providers/index, section "OpenAI-Compatible Chat Completions Endpoint (Chat
  Only)", retrieved 2026-10-14 (the page's SHA-256 is in the module docstring). Off unless
  `UWC_HF_INFERENCE_ENABLED=1`, `HF_TOKEN` and a model are set, and the model has a verified price in
  `config/llm_pricing.yaml` (otherwise the budget layer refuses the call before any request).
- `src/uwcopilot/llm/factory.py`: `get_llm_client("hf_inference", ...)` builds it as `Cached(Budgeted(client))`, like
  every paid provider. It is not in `settings.PROVIDERS`, so `UWC_LLM_PROVIDER` cannot select it by accident.
- `.env.example` documents `UWC_HF_INFERENCE_ENABLED`, `UWC_HF_INFERENCE_MODEL` and `UWC_HF_INFERENCE_BASE_URL`
  (commented out; the project test that every setting the code reads is documented needs them).

### Script, results, tests, Make targets

- `scripts/eval_studio_evidence.py` writes `results/studio_evidence_metrics.json` (template provider) or
  `results/studio_evidence_metrics_<provider>.json` (an LLM provider), each with a `uwcopilot.governance.provenance`
  block (git SHA and dirty flag, prompt hashes, config file hashes, metro snapshot fingerprints, model digest for a
  local model).
- Tests: `tests/studio/test_narrative_ledger.py` (statuses, provenance, documents, conflicts, unknowns, edited
  ranges, arithmetic against the lender side), `test_narrative_targets.py` (each target checked against the
  probability measured for it; the driver check; a result without a second year), `test_narrative_render.py`
  (template validity for every business type, the LLM path with the fake client: retry, fallback, unreachable model,
  hf_inference refused; the prompt holds no values and no founder text; the registry hashes),
  `test_narrative_checker.py` (bound check vs round-trip audit, the mutation test), `test_narrative_golden.py` (36
  scenarios, reproducibility, a real metro in its current mode, the script, an LLM run, a provider that never
  answers), `tests/llm/test_hf_inference_client.py` (disabled by default, request shape, errors, cost cap; every HTTP
  call goes to `httpx.MockTransport`).
- Make: `make studio-evidence`, `make studio-evidence-ollama`, `make studio-narrative-test`.

## 2. Statuses: what each founder number becomes

| Where the number comes from | Status | Provenance |
|---|---|---|
| Typed by the founder (loan, equity, term, a quote) | USER_STATED | kind `user_input`, the plan field |
| Typed AND printed the same by an uploaded document | VERIFIED | kind `document`, citation doc id / page / line |
| Typed, but a document prints a different value | CONFLICTING (both) | each links the other (`conflict_ids`) |
| Marked "verified" by the founder without a document in the ledger | USER_STATED | a note says why it stays USER_STATED |
| A range the founder edited (`Plan.assumption_overrides`) | USER_STATED | kind `user_input`, the edited range; the founder's note stays in the details |
| Profile or simulator range (cost shares, ramp-up, shocks) | ASSUMPTION | source "unsourced" (the profile's note says what should replace it), range low-high |
| Profile value from a cited publication | ASSUMPTION | the publication as source, range |
| Public data row (Census, BLS, HPMS, Overture via SiteIQ) | VERIFIED | kind `public_data`, locator: publisher, dataset, URL, retrieval date, response SHA-256, geography |
| SYNTHETIC city value (the demo metro) | VERIFIED | kind `public_data`, label and source say SYNTHETIC |
| Public data converted with an assumption (weekly wage / hours) | ASSUMPTION | the data source, range |
| The site demand handed to the simulator | MODEL_ESTIMATE (SiteIQ), USER_STATED (founder's range) or ASSUMPTION (a SYNTHETIC or assumed range) | |
| Simulation output (chances, medians, break-even, tornado, stress) | MODEL_ESTIMATE | model `studio-feasibility` and version, seed, paths, months, plan SHA-256 |
| SiteIQ score | MODEL_ESTIMATE | model `siteiq`, uncertainty "not measured per site" |
| Path search output | MODEL_ESTIMATE | model `studio-path-to-approval` and version |
| Program threshold, Studio target | VERIFIED | kind `policy`, the NSP section or the target's source, its comparator |
| Needed but absent (credit score not entered, a factor without data, an edited target nobody measured) | UNKNOWN | the reason; nothing is filled in |

## 3. Design choices

1. **One ledger, not a second one.** The Phase 4 ledger already had the six statuses (USER_STATED and ASSUMPTION
   were added for Studio), the validators that tie a status to its provenance kind, and the renderer protocol.
   `Ledger.builder()` was made for callers like Studio. S4 only adds constructors and ids.
2. **A derived number is never more certain than its least certain input.** Order: UNKNOWN < CONFLICTING <
   MODEL_ESTIMATE < ASSUMPTION < USER_STATED < VERIFIED. The equity injection computed from the founder's figures is
   USER_STATED; the required reserve, which uses assumed wages and rent, is an ASSUMPTION with a range computed from
   its inputs' ranges; the projected DSCR, which uses the simulated median NOI, is a MODEL_ESTIMATE. This is stricter
   than the lender ledger, which calls its arithmetic VERIFIED: a founder must never read a number computed from their
   own guesses as if it had been checked. A test fails if any CALC claim of a golden plan is VERIFIED.
3. **The program's own arithmetic.** Ratios and percentages use `policy_startup.program_ratio` / `program_pct` (exact
   decimals, rounded half up) and the NBC-4.1 payment, so the founder sees the number the lender copilot will compute.
4. **Slots only, values never in the prompt.** The model sees slot ids and what each one means, never a value, so it
   has nothing to copy. The renderer types every number. The grammar (provenance/memo_grammar.py) rejects digits
   (and any other numeric character, such as "½"), number words (including ordinals from "third", "twofold",
   "several"), currency and percent symbols, unknown or malformed slots, comparative words the model typed anywhere
   (only an assert slot's own phrase may judge), a claim or section of a different check in a sentence with an assert
   slot (V10, added after the S4 review), omitted findings or open claims, decision wording and unknown citations.
5. **One retry, then the template.** A rejected draft goes back once with the validator's findings (code and the text
   that broke it). If the second draft fails too, or the model cannot be reached, or the rendered text fails the
   audit or the bound check, the deterministic template is used and the result says so (`fallback_used`, `fallback_reason`). The template
   is the default because it always works, costs nothing and gives the same text for the same ledger.
6. **Founder free text never reaches a prompt or a code field.** Site labels, the names of other debt and the notes on
   edited ranges stay in claim details; the words around the slots come from fixed tables, and anything read from
   config (a metro name, a lever label) is run through the grammar validator before it is used.
7. **Assertion phrases are written for the checker.** The phrases the renderer prints ("meets the program's minimum",
   "falls short of the stress minimum") hold no digits, no decision words, and no comparator except "minimum" right
   before the threshold it describes. Otherwise the bound checker would read, say, "below" as the comparator of the
   founder's own number.
8. **Two checks, and why.** The round-trip audit (Phase 4 / SIG) asks "does this number equal some claim of the
   ledger?". It cannot see a right number in the wrong place. The bound check asks "does this number equal a claim
   that THIS sentence cites through its footnote marker, with the threshold's comparator?". Rendered text always has
   the markers, so the binding is free.
9. **A target is checked against the probability measured for it.** The founder may edit the targets (a 1.40 DSCR,
   an 18-month cash check). The S3 simulation measures its own DSCR target, the program's revenue stress and a
   24-month cash window; the S5 search measures exactly the target it was given. `target_measures.py` uses the
   simulation's number when it answers the target, the search's number when it does not, and otherwise an UNKNOWN
   claim, so the check is "not evaluated" rather than wrong. The template prints whichever probability the check
   used, so sentence and check always agree.
10. **Golden scenarios reuse the S5 generator.** The plan, loan and demand range come from
    `path_to_approval/synthetic_plans.py` (anchored so plans span short / near / past the lender's stress), plus
    SYNTHETIC founder facts. SiteIQ scores a seeded tract centroid when the metro has tracts here. The simulation uses
    the generator's demand range (recorded as a SYNTHETIC ASSUMPTION), not SiteIQ's estimate. A metro without a prime
    rate in this environment gets a SYNTHETIC quoted rate, noted on the scenario.
11. **hf_inference: three locks.** Disabled flag, token and model, verified price (cost cap). A refusal in
    `render_narrative` is a template fallback with the reason, never an error for the founder.

## 4. How a narrative is made (and how the web layer calls it)

```python
from uwcopilot.studio.narrative import build_founder_ledger, render_narrative

# Inputs: a PlanCase plus the outputs of simulate(), score_site() and find_paths().
ledger = build_founder_ledger(case, feasibility, site_score, path_result)
# The default provider is "template".
result = render_narrative(ledger)
result.text, result.audit, result.fallback_used, result.prompt_id, result.prompt_sha256
# Both are JSON-serializable.
ledger.model_dump(mode="json"), result.model_dump(mode="json")
```

1. `build_founder_ledger` builds the claims (sections 2-3) and the checks. `site_score` and `path_result` are
   optional; `documents` adds uploaded-document values; `data_label` defaults to SYNTHETIC (the web app passes its
   own label for real founder inputs under docs/studio/privacy.md).
2. `template_draft(ledger)` writes the slot-only draft from the ledger: the plan and eligibility checks; the site; the
   simulated chances against the targets, the median second year and the break-even month; the program's coverage
   checks; the top drivers; the path search (with all three chances after the change-set); open items (every UNKNOWN
   or CONFLICTING claim); a closing reminder that the lender runs every rule and decides.
3. With a model, `prompts.build_request` sends the rules, the slots with their meanings and statuses, the checks with
   their sections, the slots it must use and the template draft as an example of the style.
4. `check_draft` validates; `render` fills the slots and adds one footnote per claim (label, provenance, inputs
   hash); `round_trip_audit` must find every number of the body in the ledger.
5. The text ends with "Every probability above is simulated, based on stated assumptions." and the disclaimer.

## 5. How the evaluation works (`scripts/eval_studio_evidence.py`)

For each golden scenario (seeded), the script builds the ledger, renders the template narrative, counts the numbers
and the ones each check cannot trace, renders every claim of the ledger on its own line (the evidence appendix) and
counts again, and runs the mutation test: every sentence of the rendered narrative that holds a number becomes a
reference sentence whose "section" is the evidence text of the claims its markers point to, and
`provenance.mutations.build_mutations` breaks one number (value_perturb, magnitude_x10, unit_swap, comparator_flip,
cross_section with a donor from another sentence of the same narrative) or keeps it (unmutated,
equivalent_rewrite). wrong_row does not apply: a founder narrative has no tables. With an LLM provider it also asks
the model for each narrative and records first-try validity, validity after the retry, the fallback and its reason,
the validator's findings and latency; if the model never answered, the LLM rates are "not measured".

The wrong-slot test (added after the S4 review) measures what none of the checks can see: up to 10 drafts per
scenario in which ONE claim slot of the template draft points to another claim with the same display format (a rent
sentence that shows the loan amount). Each check reports whether it flags the draft. Rates are pooled
over scenarios with Wilson 95% intervals and bootstrap 95% intervals that resample scenarios (1,000 resamples, seed
42).

## 6. Results

Every number below is copied from `results/studio_evidence_metrics.json` (template run) and
`results/studio_evidence_metrics_ollama.json` (local-model run). All plans are SYNTHETIC; nothing here is a claim
about real lending outcomes or about how a model behaves on real founders' plans.

### 6.1 Template narrative on the golden scenarios (`results/studio_evidence_metrics.json`)

Run: git `330ce3d439e2b7a3b81a5acb39709a777068623f` (dirty: True), generated 2026-10-14T10:31:43+00:00. Scenarios run: 36 of 36 (seed 4404). Metro modes: limited 30, synthetic 6. SiteIQ scored the site in 36 scenarios; SYNTHETIC quoted rate needed in 0. Path search status: already_meets_target 2, no_path_within_bounds 33, paths_found 1.

| Measure | k / n | Rate | Wilson 95% | Bootstrap 95% (scenarios) |
|---|---|---|---|---|
| Narrative numbers that trace (round-trip audit) | 1673 / 1673 | 1.0 | [0.9977, 1.0] | [1.0, 1.0] |
| Narrative numbers that trace (bound check) | 1673 / 1673 | 1.0 | [0.9977, 1.0] | [1.0, 1.0] |
| Evidence-appendix numbers that trace (round-trip audit) | 13289 / 13289 | 1.0 | [0.9997, 1.0] | [1.0, 1.0] |
| Evidence-appendix numbers that trace (bound check) | 13289 / 13289 | 1.0 | [0.9997, 1.0] | [1.0, 1.0] |
| Template drafts that pass the narrative grammar | 36 / 36 | 1.0 | [0.9036, 1.0] | [1.0, 1.0] |

LLM fallback rate in this run: not measured: the template provider makes no LLM call; an LLM run (--provider ollama ...) writes results/studio_evidence_metrics_<provider>.json

Ledgers: 380 to 432 claims per plan (median 402.5). Claims by status over all scenarios: VERIFIED 963, MODEL_ESTIMATE 11619, CONFLICTING 0, UNKNOWN 22, USER_STATED 625, ASSUMPTION 1258.

How often each check was a finding (the plan does not meet it) and how often it could not be evaluated:

| Check | Scenarios with a finding | Scenarios not evaluated |
|---|---|---|
| `A-NSP-2.1` | 10 | 0 |
| `A-NSP-2.2` | 9 | 0 |
| `A-NSP-2.3` | 12 | 0 |
| `A-NSP-3.1-statements` | 36 | 0 |
| `A-NSP-4.1` | 8 | 0 |
| `A-NSP-4.2-rate` | 5 | 0 |
| `A-NSP-4.2-revenue` | 31 | 0 |
| `A-NSP-5.1` | 15 | 0 |
| `A-NSP-6.1-r1` | 36 | 0 |
| `A-NSP-6.1-r2` | 36 | 0 |
| `A-NSP-6.1-r3` | 36 | 0 |
| `A-NSP-7.1` | 18 | 0 |
| `A-NSP-8.1-amount` | 19 | 0 |
| `A-TARGET-cash` | 32 | 0 |
| `A-TARGET-dscr` | 15 | 0 |
| `A-TARGET-stress` | 34 | 0 |

No golden plan carries an uploaded document, so the bank-statement check (NSP-3.1) is a finding in every scenario, and a top driver (NSP-6.1) is backed only when it is public data. The path search runs in quick mode with the default lever bounds; its status counts above are S5's results, reported as they are.

Checker on the founder sentence set (686 sentences with a number, from 36 narratives). Recall = share of mutated sentences flagged:

| Mutation type | Round-trip audit | Bound check |
|---|---|---|
| value_perturb | 654 / 686 = 0.9534 (Wilson [0.9349, 0.9668]) | 686 / 686 = 1.0 (Wilson [0.9944, 1.0]) |
| magnitude_x10 | 612 / 686 = 0.8921 (Wilson [0.8667, 0.9132]) | 686 / 686 = 1.0 (Wilson [0.9944, 1.0]) |
| unit_swap | 320 / 321 = 0.9969 (Wilson [0.9826, 0.9994]) | 321 / 321 = 1.0 (Wilson [0.9882, 1.0]) |
| comparator_flip | 0 / 164 = 0.0 (Wilson [0.0, 0.0229]) | 164 / 164 = 1.0 (Wilson [0.9771, 1.0]) |
| cross_section | 0 / 686 = 0.0 (Wilson [0.0, 0.0056]) | 686 / 686 = 1.0 (Wilson [0.9944, 1.0]) |
| wrong_row | not applicable (n = 0) | not applicable (n = 0) |

False-flag rate on the controls (share of unchanged or reworded sentences flagged):

| Control | Round-trip audit | Bound check |
|---|---|---|
| unmutated | 0 / 686 = 0.0 (Wilson [0.0, 0.0056]) | 0 / 686 = 0.0 (Wilson [0.0, 0.0056]) |
| equivalent_rewrite | 0 / 383 = 0.0 (Wilson [0.0, 0.0099]) | 0 / 383 = 0.0 (Wilson [0.0, 0.0099]) |

### 6.2 Local model run (`results/studio_evidence_metrics_ollama.json`)

Run: git `9415cfc5494c4cfaa5e7eb8f838b77a7c3809788` (dirty: False), generated 2026-10-14T11:20:39+00:00; provider ollama, model qwen2.5-1.5b-instruct-f16 (Ollama digest 75a307d2fa0d, ollama 0.40.3); the first 6 golden scenarios (limit 6).

| Measure | k / n | Rate | Wilson 95% | Bootstrap 95% (scenarios) |
|---|---|---|---|---|
| Narratives that fell back to the template | 6 / 6 | 1.0 | [0.6097, 1.0] | [1.0, 1.0] |
| First drafts that passed the grammar | 0 / 6 | 0.0 | [0.0, 0.3903] | [0.0, 0.0] |
| Retry drafts that passed the grammar | 0 / 6 | 0.0 | [0.0, 0.3903] | [0.0, 0.0] |
| Final-text numbers that trace (round-trip audit) | 285 / 285 | 1.0 | [0.9867, 1.0] | [1.0, 1.0] |
| Final-text numbers that trace (bound check) | 285 / 285 | 1.0 | [0.9867, 1.0] | [1.0, 1.0] |

Model calls: 12 (0 answered from the cache); calls that failed: 0. Latency per call: median 210.686 s, maximum 885.644 s (wall time on a shared 4-CPU machine; cached calls take milliseconds).

Validator findings over all drafts, by code: V3_SYMBOL 687, V5_UNSLOTTED_COMPARATIVE 40, V6_OMISSION 20.

Fallback reasons: the draft broke the narrative grammar twice.

These numbers were measured before the S4 review fixes; the stricter grammar can only add findings, and every
narrative already fell back. What the drafts show: the model copies the example's sentences but types a dollar sign in front of most claim slots, including slots that are not dollar amounts (months, a credit score), so V3_SYMBOL is by far its most frequent finding; the retry with the validator's findings repeats the same pattern. Every final text is therefore the template, which is why the final-text numbers trace. The run started on an uncommitted tree and a WIP checkpoint was committed while it ran, so the commit its provenance block names is not the code that ran: the file's per-scenario ledger blocks lack the "findings" and "not_evaluated" keys that commit writes. No results file records a re-run at that commit, so treat this file as approximate until `make studio-evidence-ollama` is re-run at a clean commit. Since the S4 review, a results file names the commit its run STARTED on, and a change during the run marks it dirty.

### 6.3 After the S4 review (`results/studio_evidence_metrics_synthetic.json`)

The review fixes (section 7, "Review fixes") changed the grammar, one template sentence and the equity check's
evidence list. This run re-measures the first 6 golden scenarios (the SYNTHETIC city; the real cities need the data
snapshots of 2026-10-13, which are not on the machine that made the fixes). Run: git
`0d03cdcfcb9f217003891f81a42e9dbcc5879c7e` (dirty: False), generated 2026-10-16T15:16:06+00:00.

- Every template and ledger count of the 6 scenarios equals the matching rows of section 6.1, so those rows still
  hold after the fixes. The 30 real-city rows of section 6.1 were measured before the fixes and have not been re-run.
- **The measured blind spot (wrong slot).** Share of the 60 wrong-slot drafts each check flags:

| Check | k / n | Rate | Wilson 95% | Bootstrap 95% (scenarios) |
|---|---|---|---|---|
| Grammar (V10 catches a slot owned by another check) | 3 / 60 | 0.05 | [0.0171, 0.137] | [0.0167, 0.0833] |
| Round-trip audit | 0 / 60 | 0.0 | [0.0, 0.0602] | [0.0, 0.0] |
| Bound check | 2 / 60 | 0.0333 | [0.0092, 0.1136] | [0.0, 0.0667] |
| Any of the three | 5 / 60 | 0.0833 | [0.0361, 0.1807] | [0.0333, 0.1333] |

  A model that picks the wrong slot of the right kind produces a true ledger value, cited to the claim it came from,
  so the checks miss it almost every time. This is the limit of slot-level provenance, stated rather than hidden;
  the human reviewer and the evidence appendix (every claim with its label) are the defence.

### 6.4 hf_inference

not measured: the hf_inference provider is disabled by default (UWC_HF_INFERENCE_ENABLED, HF_TOKEN and a verified price are all required) and was never run in this build; the owners decided on no extra spending

## 7. Limits and open issues

**Review fixes (independent S4 review, 2026-10-16).** Fixed: on the model path a draft could pair one check's verdict
with another check's fact ("experience of 4 months ... meets the program's minimum") or type its own comparison next
to an assert slot ("well below 1.00 and meets the stress minimum"), and every gate passed. Now V5 checks
comparatives in every sentence, V10 binds an assert slot to its own claims and sections, the number-word list is
wider, and the bound check gates every model draft. A target the founder edited is USER_STATED (it was VERIFIED);
simulated figures list a CONFLICTING founder figure they rest on (depends_on_conflicting). Not fixed yet, and needed
before S6 relies on them: per-claim metro and snapshot date (most claims leave `metro_id` and `snapshot_date` empty,
and the run identity records no snapshot fingerprint); a structured P10-P90 range on MODEL_ESTIMATE claims (the
uncertainty is free text today); a fixed phrase for document claims (the narrative says "the item listed in the
evidence appendix"); golden scenarios with documents (CONFLICTING appears only in unit tests).

- **Closed word lists (known limits of the grammar).** "first", "second", "most" and "least" are allowed because
  ordinary prose needs them; a bare "over" or "under" is allowed and only flagged next to a threshold word ("over the
  target"); Roman numerals typed in ASCII letters ("IV") are not detected.
- **Wrong slot of the right kind.** Measured in section 6.3: the checks flag 5 of 60 such drafts. The grammar checks
  which words the model typed, the audits check that each number is a true, cited ledger value; none can tell that a
  rent sentence points to the loan amount's slot.

- **The template's numbers trace by construction.** The renderer types every number from the ledger, so a 100% traced
  share for the template is a check that the construction holds on every scenario, not evidence about how often a
  model would write a wrong number. The local-model run (section 6.2) is the measurement of a model, on
  6 scenarios only.
- **The mutation test uses sentences we wrote.** The founder sentences come from the template, the same author wrote
  the mutations' rules, and the bound check's recall on cross_section follows from its design (the donor value is
  drawn so that it is absent from the cited claims). It shows the implementation has no gap (sentence splitting,
  marker mapping, comparators); it is not a held-out benchmark.
- **Display rounding.** The renderer has one percent format with one decimal (`pct1`), so a note rate of 8.25% prints
  as 8.2%. The claim keeps the exact value and the template does not print the rate; S6 should print rates from the
  claim value with two decimals or add a display format to the shared renderer. Negative dollars print as "$-53,735"
  (the shared renderer's format).
- **"(stated by the applicant)" on computed figures.** A CALC claim computed only from the founder's figures is
  USER_STATED, so the renderer prints that suffix (for example after the equity injection); its footnote says
  "calculated from the founder's figures". The suffix text belongs to the shared renderer and was not changed.
- **Text claims hold no checked numbers.** The round-trip audit ignores claims displayed as text, so a text value with
  a digit (a founder's site label "Lot 7") would show as untraced; the template therefore never prints a site label.
- **Prompt registry.** The founder prompts are registered in `studio/narrative/prompts.py` with the same id + SHA-256
  convention as `rag/prompts.py`, not inside `rag.prompts.PROMPTS` (whose builders take a question and guideline
  hits). The hash of `founder_narrative_v1` covers the system prompt and the instruction; the template's hash covers
  the template source (the example the model sees); the data-block builder code in `prompts.py` is not hashed yet
  (the LLM cache key does cover every full request). Phase 6's release gate should read
  `narrative.prompts.all_prompt_hashes()` too.
- **Golden scenarios.** The demand comes from the S5 generator, not from SiteIQ; real metros run in this machine's
  mode (section 6.1 lists the modes); none of the golden plans carries a document, so CONFLICTING appears only in the
  unit tests; with the generator's facts every owner fact is entered, so UNKNOWN claims come from SiteIQ factors
  without data.
- **The local 1.5B model never wrote a usable draft** (section 6.2): it types a dollar sign before slots. Options for
  the owners, none built here: a larger local model (the 3B model is installed but slower on this CPU), a prompt
  change measured as a new prompt id, or a deterministic repair that deletes a "$" or "%" typed right before a slot
  (the renderer prints the unit itself), counted and reported as a repair rather than hidden. Until one of them is
  measured, the template is the only writer that passes, which is why it is the default.
- **hf_inference never ran.** It has no price in `config/llm_pricing.yaml`, so even with the flag and a token the
  budget layer refuses the call.
- **Ledger size.** Between 380 and 432 (median 402.5) claims per plan, most of them monthly and yearly simulation bands. A ledger-wide
  check gets weaker as the ledger grows (more values to match by accident), which is one reason for the bound check.

## 8. Likely interview questions

**1. Why put every number in a ledger instead of printing the simulation's results?**
Because a founder (and later a lender) has to know how much to trust each number. A typed loan amount, an assumed
cost share, a Census row and a simulated chance look the same on a page. In the ledger each one carries its status
and where it came from: the field the founder typed, the profile range and "unsourced", the dataset URL with the
retrieval date and the response's SHA-256, or the simulator version, seed, number of paths and the plan's hash. The
narrative and the package can only show what the ledger holds.

**2. How does a founder's number become VERIFIED?**
Only with a locator. When the founder uploads a document and its value (with page and line) matches the typed figure
(dollars within $1.00), the claim becomes VERIFIED with that citation. If the document prints a different value, both
become CONFLICTING and link each other, and the narrative lists them as open items. Ticking "verified" without a
document leaves the figure USER_STATED, with a note saying why.

**3. Why is the projected DSCR a "model estimate" and the reserve requirement an "assumption"?**
A computed number is never more certain than its least certain input. The projected DSCR divides the simulated median
NOI, a model estimate, so it is a MODEL_ESTIMATE. The required reserve uses assumed wages and rent, so it is an
ASSUMPTION, with a range computed from its inputs' ranges. The equity injection uses only the founder's figures, so
it stays USER_STATED. A test fails if any computed figure of a golden plan is VERIFIED.

**4. How can a model write a narrative full of numbers if it may not type a digit?**
It writes slots, `{{claim:SIM.p_dscr_year2}}`, and checks, `{{assert:A-NSP-4.1}}`. The prompt lists each slot with
what it means, never its value. The validator rejects digits, number words, currency and percent signs, unknown
slots, any comparative word the model typed (only a check's own phrase may judge), a check stated with another
check's fact or section, missing findings and decision wording. The renderer then fills every slot from the ledger
and adds a footnote, and both the round-trip audit and the bound check re-read every number. The word lists are
closed, so section 7 lists what they allow on purpose.

**5. What happens when the model breaks the rules?**
It gets one retry with the validator's findings ("V1_DIGIT: 250000"). If the second draft fails too, the model cannot
be reached, or the rendered text fails the audit, the deterministic template is used and the result says so
(`fallback_used`, `fallback_reason`). In the local run (qwen2.5-1.5b-instruct-f16 on the first 6 SYNTHETIC golden scenarios) the template was used for 6 of 6 narratives (section 6.2).

**6. The round-trip audit already exists. Why add a bound check?**
The audit asks whether a number equals some claim of the ledger. With hundreds of claims per plan, a wrong number
often equals some other claim. On the founder sentences of the 36 golden narratives the round-trip audit flagged 0 of 686 values moved between sentences and 0 of 164 flipped comparators (section 6.1). The bound check uses the footnote markers the renderer already writes: a
number must equal a claim that its own sentence cites, with the threshold's comparator ("minimum 1.25").

**7. Is a recall of 1.0 a real result?**
It shows the implementation holds on 686 sentences from 36 SYNTHETIC narratives, for the error types we can generate (bound check: value_perturb 686/686, magnitude_x10 686/686, unit_swap 321/321, comparator_flip 164/164, cross_section 686/686). It is not a claim about real errors: the mutations break the template's own sentences, the same author
wrote both, and for cross_section the result follows from the design. We report the round-trip audit on the same
mutations so the difference is visible, and wrong_row is reported as not applicable because a founder narrative has
no tables. The error the checks cannot see is measured too: a slot pointing to the wrong claim of the same kind is
flagged in 5 of 60 drafts (section 6.3).

**8. The founder edited a target. Which probability does the check use?**
The one measured for that exact target. The simulation measures its own DSCR target, the program's 20% revenue stress
and a 24-month cash window; if the founder asks for an 18-month cash window, the simulation's number answers a
different question. The ledger then uses the path search's "before" probability, which S5 computes for the edited
target; with no path search it records an UNKNOWN claim with the reason, and the check is "not evaluated". The
sentence in the narrative prints the same probability the check used.

**9. Why is hf_inference disabled, and how would the owners turn it on?**
It can cost money and the owners decided on no extra spending. It needs `UWC_HF_INFERENCE_ENABLED=1`, `HF_TOKEN`, a
model id, and the model's verified price in `config/llm_pricing.yaml`; without the price the cost-cap layer refuses
every call before a request is sent. It goes through the same Cached(Budgeted(...)) wrapper as the other paid
providers, and `UWC_LLM_PROVIDER` cannot select it. It is tested only against a mocked endpoint and never ran.

**10. Could a founder's text inject instructions into the model, and why is the template the default?**
Founder free text never reaches the prompt: the prompt holds slot ids, fixed meanings from code tables, statuses,
section ids and the template draft, all built from code and config, and config phrases are checked with the grammar
validator. A test checks that the site labels and the loan value are not in the prompt; the system prompt also says
the data block is data, not instructions, and the validator would reject decision wording anyway. The template is the
default because it always works, costs nothing, needs no network and gives the same text for the same ledger, which
is what the Space needs; a model can make the prose read better, but on CPU it is slow and a small model often breaks
the grammar.

# Interview guide: Fundproof, the Lender Desk and Founder Studio

One study guide that merges every walkthrough in this repository, so both owners (Rahul Vajja and Bhavani Adula) can
explain the whole project in their own words. It does not replace the walkthroughs. Each section names the
walkthrough that holds the detail, and section 10 lists them all.

**Who built what.** Rahul Vajja and Bhavani Adula wrote the specs, the build prompts and the evaluation protocol, and
they review the work. Claude Code is the implementer. Git history cannot say more than that (almost every commit is
authored by the implementer), so this guide never attributes a component to one owner. `config/contributions.yaml`
is empty by design until the owners fill it in.

> **What this project is and is not.** A portfolio project, not a product. It uses public datasets and **SYNTHETIC**
> (computer-generated) documents, applications, customers, founder plans and cities. Some runs are **SIMULATED**
> (a simulated reviewer, simulated decisions, simulated drift). No real personal data, no production users, not
> deployed: the Hugging Face Space exists but no deploy has run, so there is no live URL. Web and container numbers
> were measured on a LOCAL container. Anthropic and OpenAI were never called (no API key was configured); the only real
> language-model calls were small local Ollama smoke runs (README, "Language models: what has actually run"). No cloud
> service ran. Founder Studio is an educational planning tool: not financial, legal or lending advice; lenders make
> their own decisions. This is a demonstration, not a compliance program.

**How to read the numbers in this guide.**
- Every measured number is copied from a `results/*.json` file, named next to it (with the key where it helps). Rates
  are shown at 4 decimals, as the README prints them; the JSON holds full precision. A few values are shown as the
  JSON prints them (for example `0.842`). Dates and figures in section 7 come from the primary sources as the build
  plan (and, for OWASP, the S2 walkthrough) summarizes them.
- Intervals are 95% intervals. Unless marked otherwise they are percentile bootstrap intervals: 1,000 resamples, seed
  42. "Wilson" marks a Wilson score interval and "exact" a Clopper-Pearson interval. Some files (SIG, the injection
  tripwire, Studio S4 and S6) report Wilson intervals for counted rates; "exact" is shown where a bootstrap interval
  collapses to one point (every case passes) or the file notes it is too narrow (1 of 300).
- "Smoke only" means a run on a handful of cases. Quote no rate from it.
- "Not measured" always comes with the reason the results file records.
- Many results files were written from a working tree with uncommitted changes (`git_dirty: true` in their provenance
  block). Check the block before you quote a number, and re-run the producing script on your own machine before a
  number goes on a resume (`docs/build_prompts.md`, "Notes for Rahul").

## Contents

1. [Architecture in 60 seconds](#1-architecture-in-60-seconds)
2. [The interview pitch](#2-the-interview-pitch)
3. [Every headline metric explained](#3-every-headline-metric-explained)
4. [Trade-offs](#4-trade-offs)
5. [What failed and how it was fixed](#5-what-failed-and-how-it-was-fixed)
6. [What we would do next](#6-what-we-would-do-next)
7. [Regulatory references, if asked](#7-regulatory-references-if-asked)
8. [Cross-cutting interview questions](#8-cross-cutting-interview-questions)
9. [Ten things to say in your own words](#9-ten-things-to-say-in-your-own-words)
10. [Where to read more](#10-where-to-read-more)
11. [Numbers you must not say](#11-numbers-you-must-not-say)

## 1. Architecture in 60 seconds

### 1.1 The spoken version

Fundproof has two sides that share one evidence ledger, one set of loan formulas and one human checkpoint.

The **Lender Desk** (the Underwriting Copilot) reads a small-business loan or commercial insurance packet. It extracts
every value with a page citation, checks the documents against each other, screens names against sanctions lists,
runs the reference policy's calculations and stress tests, and ranks the next piece of evidence worth asking for. A
LangGraph agent with three read-only tools drafts a memo from a deterministic template and then stops at a human
checkpoint. Only a person can record a decision, and every decision is a hash-chained record.

**Founder Studio** gives the borrower the same lens. SiteIQ scores candidate sites from public data and shows how much
of the score rests on data. A Monte Carlo simulation checks the plan against the lender's own stress test. A search
finds the smallest plan changes that reach the simulated targets. A lender-ready package, in which every number traces
to a ledger claim, hands the plan to the same agent, which again stops at the human checkpoint.

### 1.2 The picture

```
FOUNDER STUDIO (borrower side)                       LENDER DESK (Underwriting Copilot)

founder plan: metro, business type,                  document packet (PDF)
money, 1-3 candidate sites                              |
   |                                                 docintel: text layer or Tesseract OCR, cited fields,
 S2 SiteIQ: site score + data coverage                 cross-document checks, contradictions, tripwire
   |                                                    |
 S3 Monte Carlo feasibility                          Evidence-to-Action Engine: ledger, calculations,
    (with the lender's revenue stress)                 stress tests, value-of-information requests,
   |                                                   hash-chained decision record   [command line]
 S5 path to approval: smallest 1-2 lever changes
   |                                                 structured application
 S4 founder ledger + slot-locked narrative              |
   |                                                 LangGraph agent, allow-listed read-only tools:
 S6 lender-ready package: JSON + PDF + application     guideline search (RAG), sanctions screening,
   |                                                   risk score (unavailable for these files)
   '--- handoff [command line only] ----------------->  |
                                                     HUMAN CHECKPOINT  (LangGraph interrupt)
                                                        |
                                                     decision record, made by a person
```

| Side | Step | Code | Walkthrough |
|---|---|---|---|
| Lender Desk | Risk models and `/score` API | `src/uwcopilot/risk` | [Phase 1](walkthrough_phase1.md) |
| Lender Desk | Document intelligence | `src/uwcopilot/docintel` | [Phase 2](walkthrough_phase2.md) |
| Lender Desk | Sanctions screening | `src/uwcopilot/kyc` | [Phase 3](walkthrough_phase3.md) |
| Lender Desk | Evidence-to-Action Engine | `src/uwcopilot/evidence` | [Phase 4](walkthrough_phase4.md), [innovations](walkthrough_innovations.md) |
| Lender Desk | Agent and human checkpoint | `src/uwcopilot/agent` | [Phase 5](walkthrough_phase5.md), [agent design](agent_design.md) |
| Lender Desk | LLM layer, RAG, tools, MCP | `src/uwcopilot/llm`, `rag`, `tools`, `mcp_server` | [GenAI](walkthrough_genai.md) |
| Lender Desk | Numeric provenance (SIG) | `src/uwcopilot/provenance` | [S1b](walkthrough_innovations_s1.md), [plan](innovations.md) |
| Lender Desk | Injection containment | `src/uwcopilot/security` | [S2](walkthrough_innovations_s2.md) |
| Both | Governance | `src/uwcopilot/governance` | [Phase 6](walkthrough_phase6.md) |
| Founder Studio | Data fabric, SiteIQ, feasibility, ledger, path, package | `src/uwcopilot/studio` | [S0](studio/walkthrough_s0.md) to [S6](studio/walkthrough_s6.md) |
| Both | Web app (local container) | `src/uwcopilot/web`, `frontend/` | [S7](studio/walkthrough_s7.md), [S8](studio/walkthrough_s8.md) |
| Both | End-to-end evaluation, README, resume lines | `src/uwcopilot/studio/business_metrics`, `src/uwcopilot/resume` | [S12](studio/walkthrough_s12.md), [Phase 8](walkthrough_phase8.md) |

### 1.3 How the two sides connect: the S6 handoff

- `build_package` puts every number of a founder plan into ONE founder ledger (the Phase 4 ledger, with the Studio
  statuses USER_STATED and ASSUMPTION). The PDF and the JSON only arrange claim ids; no section builder formats a
  number.
- `to_application` re-runs the audit, then maps the ledger and the applicant facts onto `StartupCreditApplication`,
  the schema the agent already reads. An equity figure that a document contradicts goes in at its lowest value.
- `handoff` runs the existing agent graph unchanged (injection screen, sanctions screening, the SYNTHETIC startup
  program's policy, the template memo, the claim checker), plus the evidence engine for open items and ranked
  requests. The graph always interrupts at `human_review`, and `handoff` never resumes it.
- Today the handoff runs from the command line only (`make studio-handoff`) on SYNTHETIC packages. The web app does
  not call it.

### 1.4 The human checkpoint

- The agent graph pauses with LangGraph's `interrupt()` at `human_review`. The only way past it is a resume value
  from a person. There is no code path that writes a decision without one.
- `agent/hitl.py` (`validate_decision`) applies the guidelines' authority rules: only a senior underwriter may
  decline, approval limits by role, nothing approved or declined while a blocking referral is open, and an override
  needs a reason code from a fixed list. A refused decision is recorded and the checkpoint asks again.
- In the command-line demo the role is claimed in the resume payload, not authenticated. In the web app it comes from
  the signed session.
- Decisions are appended to a JSON Lines log chained by SHA-256, so an edit, deletion or reordering is detectable.
- Measured: 0 decisions were recorded without a human for the 65 SYNTHETIC agent applications
  (`results/agent_metrics.json`, `deterministic_path.decisions_recorded_without_a_human`) and for all 71 handoff cases
  (`results/studio_handoff_metrics.json`, `all_cases`).

### 1.5 What is not wired yet

- The web Lender Desk runs the agent on SYNTHETIC **structured** applications. The packet path (document intelligence,
  then the Evidence-to-Action Engine) runs from the command line (`make run-evidence`).
- The founder-to-lender handoff runs from the command line only.
- The Phase 1 risk models do not apply to the SYNTHETIC commercial files (consumer loan and French motor features), so
  every risk call in the agent run is recorded as unavailable (`results/agent_metrics.json`,
  `deterministic_path.tool_status_counts`), and model PD stays UNKNOWN in the evidence engine.

## 2. The interview pitch

The two versions per owner below follow the two angles of the S12 walkthrough's questions (lender, risk and
evaluation; product, data and founder). The split is by angle, not by who built what. Each owner should be able to give
both. The intervals in square brackets and the file names are for you; out loud, say the value with its label
(SYNTHETIC, SIMULATED), and give the interval when asked.

The one-sentence version from the build plan ("Notes for Rahul" in `docs/build_prompts.md`) starts with "I built".
Claude Code implemented the code, so say it this way instead: "We specified and reviewed, and Claude Code implemented,
an underwriting intelligence platform that connects source-level evidence, predictive risk models, and financial
scenario analysis to find what is uncertain, decide which additional evidence matters, and support auditable human
decisions."

### 2.1 Rahul Vajja: 30 seconds (lender, risk and evaluation angle)

"Fundproof is a two-sided lending copilot for small-business loans. Bhavani Adula and I wrote its specs, build prompts
and evaluation protocol and reviewed the work; Claude Code implemented it. The lender side turns a document packet
into a cited, stress-tested recommendation and stops at a human checkpoint: in our SYNTHETIC evaluations no decision
was ever recorded without a person. The borrower side runs the lender's own stress test on a founder's plan before
they apply. Every number comes from a results file, with a 95% interval for each headline rate, on public or synthetic
data, and anything we did not run, including every language-model quality metric, says 'not measured'."

### 2.2 Rahul Vajja: 2 minutes

1. **The problem (20 seconds).** An underwriter has to trust a recommendation before acting on it. That needs three
   things: every figure traced to a page or a calculation, the conflicts between documents surfaced, and a way to know
   which missing document actually matters.
2. **What it does (40 seconds).** Document intelligence reads the packet with page citations; on 60 SYNTHETIC test
   packets the rules extractor scored field F1 0.9936 [0.9889, 0.9971], but only 0.9159 [0.8736, 0.9566] on scanned
   pages (`results/docintel_metrics.json`). Cross-document checks found 48 of 51 planted problems. The
   Evidence-to-Action Engine puts every value into a ledger with a status (VERIFIED, CONFLICTING, UNKNOWN), runs the
   reference policy and its stress tests, and scores each possible evidence request by whether its answer could change
   the recommendation. Sanctions screening runs against the full OFAC and OpenSanctions lists.
3. **How we know (40 seconds).** In a simulator with a hidden truth (SIMULATED cases on SYNTHETIC packets), that
   value-of-information policy made fewer cost-weighted requests than a checklist, -1.2689 [-1.4515, -1.0932] per
   case, at a small decision-error cost, +0.0135 [0.0000, 0.0270] (`results/evidence_metrics.json`, section
   `simulator`). Sanctions recall on SYNTHETIC test customers is 0.6889 [0.5476, 0.8182] at 103.8 alerts per 1,000
   (`results/kyc_metrics.json`). The credit model on public Lending Club data, tested out of time on 2017-2018 loans,
   has AUC 0.7151 [0.7123, 0.7177] against 0.7010 for a logistic scorecard (`results/risk_metrics.json`). The agent
   never decided: 0 decisions without a human on 65 SYNTHETIC applications (`results/agent_metrics.json`).
4. **What did not work (20 seconds).** The keyword injection tripwire flagged only 12 of 60 frozen attack payloads
   (`results/injection/injection_metrics_detector_only.json`), so we made the decision path independent of document
   text instead: injected text cannot change the recommendation, and tests enforce that. And the number checker has a
   measured blind spot: a value from the wrong row of the same table is caught 6 times in 55
   (`results/provenance_metrics.json`).

### 2.3 Bhavani Adula: 30 seconds (product, data and founder angle)

"Fundproof gives a small-business founder the lender's view before they apply. They pick a city, a business type and up
to three sites. Studio scores the sites from public data and shows how much of each score rests on data, simulates
whether the plan can cover its loan payments under the lender's revenue stress, shows the smallest plan changes that
reach the simulated targets, and builds a package in which every printed number traces to a ledger claim that says
where it came from, or that it is the founder's own figure or a labelled assumption. That package goes to the
lender's copilot, where a person decides. It is an educational planning tool, tested on synthetic plans. Rahul Vajja and
I wrote the specs and evaluation protocol and reviewed the work; Claude Code implemented it."

### 2.4 Bhavani Adula: 2 minutes

1. **The problem (20 seconds).** A founder writes a plan in a spreadsheet with one forecast and no sources. The lender
   then runs a stress test the founder never saw. Most of the back-and-forth is about where the numbers came from.
2. **What it does (40 seconds).** Studio covers six business types in the five largest US metro areas by Census
   population estimates, plus a SYNTHETIC test city (`results/studio_metro_ranking.json`). Every factor without data
   stays UNKNOWN; all five real metros run in limited mode because short-term rental (0.6779 to 0.6826) and motel
   (0.745 to 0.7722) data coverage is below the 0.8 threshold (`results/studio_metro_coverage.json`). The simulator
   runs many futures instead of one forecast, and every probability is labelled "simulated, based on stated
   assumptions".
3. **How we know (40 seconds).** On 2,400 well-specified SYNTHETIC worlds, the simulator's Brier score for "year-2
   DSCR meets the target" was 0.1405 [0.1321, 0.1493] against 0.4796 [0.4596, 0.4992] for a single forecast
   (`results/studio_feasibility_metrics.json`). In 36 SYNTHETIC packages, 30,681 printed numbers were read back and 0
   were untraced (`results/studio_handoff_metrics.json`). In the end-to-end evaluation on the same 36 SYNTHETIC
   scenarios, against a baseline B0 written before the first run, 0.8482 [0.8388, 0.8587] of printed figures had a
   checkable source (rule S) against 0.0255 [0.0185, 0.0325] for B0; counting only a document page, a public-data row
   or a program or target section (rule E) it is 0.0570 [0.0545, 0.0597] against 0.0255. B0 is a construction from
   the same SYNTHETIC inputs, not a sample of real plans, and both gaps follow mostly from the definitions: rule S
   credits model runs and formulas, rule E program and target sections, and B0 prints neither
   (`results/studio_business_metrics.json`).
4. **What did not work (20 seconds).** SiteIQ beats a single-factor baseline for only 1 of 6 business types and is worse
   for 1 (`results/studio_siteiq_metrics.json`). A local 1.5B model never wrote a narrative that passed the grammar, so
   all 6 fell back to the template (`results/studio_evidence_metrics_ollama.json`). We report both.

Studio results always carry the disclaimer: "Educational planning tool. Not financial, legal or lending advice.
Lenders make their own decisions."

## 3. Every headline metric explained

There is one entry per headline row of the README tables (the Status tables, the Phase 1/2/4 rows, the "Headline metric
per results file" tables and the GenAI table). Rows of one component share an entry, with every row's value listed.
Each entry answers the same six questions:

- **Value**: the number, its interval and the results file (and key).
- **What it measures**: in one or two plain sentences.
- **Data**: what it was measured on.
- **Compared with**: the baseline or comparison, if any.
- **The interval**: how to read it here.
- **What it does not show**: the claim it cannot support.

**How to read an interval, in general.** A percentile bootstrap resamples the cases (packets, customers, scenarios,
worlds, streams) 1,000 times with seed 42, recomputes the metric each time, and takes the 2.5th and 97.5th percentiles.
Read it as the range of values consistent with this evaluation set. When a difference's interval includes 0, we have not
shown a difference. When every case passes, the bootstrap interval is a single point; the Wilson or exact interval then
says what the number of cases can rule out (for example 36 of 36 is consistent with a true rate as low as 0.9036).

**Index of entries.**

| Area | Entries |
|---|---|
| Lender Desk: risk models | [L1](#l1-credit-default-model) [L2](#l2-insurance-claim-frequency) [L3](#l3-insurance-severity-and-expected-loss) [L4](#l4-fairness-demonstration) |
| Lender Desk: documents and the engine | [L5](#l5-document-extraction) [L6](#l6-contradiction-detection) [L7](#l7-claim-status-agreement) [L8](#l8-calculation-agreement) [L9](#l9-top-request-changes-the-recommendation) [L10](#l10-decision-stability) [L11](#l11-evidence-request-simulator) |
| Lender Desk: retrieval, screening, agent, MCP | [L12](#l12-rag-retrieval) [L13](#l13-sanctions-screening) [L14](#l14-agent-and-human-checkpoint) [L15](#l15-mcp-server) |
| Lender Desk: numbers and injection | [L16](#l16-numeric-provenance-checker) [L17](#l17-prompt-injection) |
| Governance | [G1](#g1-drift-monitor) [G2](#g2-champion-and-challenger) [G3](#g3-reviewer-override-rate) [G4](#g4-outcomes) [G5](#g5-release-gate) |
| Founder Studio | [F1](#f1-data-coverage-and-supported-metros) [F2](#f2-siteiq) [F3](#f3-feasibility-simulator) [F4](#f4-founder-narrative) [F5](#f5-path-to-approval) [F6](#f6-lender-ready-package-and-handoff) [F7](#f7-end-to-end-evaluation-against-a-baseline) |
| Web app and deployment | [W1](#w1-browser-flows-and-accessibility) [W2](#w2-container-smoke-test) [W3](#w3-deployment) |
| Status rows with no metric | [N1](#n1-status-rows-with-no-metric) |

### 3.1 Lender Desk: risk models

#### L1 Credit default model

README rows: "Credit-default model (Lending Club), chosen model" and the Status row "Risk models".

- **Value.** Test AUC 0.7151 [0.7123, 0.7177] for LightGBM, the chosen model (`results/risk_metrics.json`,
  `credit.results.models.lightgbm.test.auc`). The logistic scorecard: 0.7010 [0.6982, 0.7038]. Paired difference
  +0.0141 [+0.0130, +0.0152] (`credit.results.comparison.test_auc_difference`). KS 0.3122 [0.3076, 0.3176]. Lending
  Club's own sub-grade, used only as a benchmark: 0.6842 [0.6813, 0.6867] (`benchmark_lending_club_sub_grade`).
- **What it measures.** AUC is the chance that a random defaulted loan gets a higher risk score than a random repaid
  one. It measures ranking, not calibration.
- **Data.** Public Lending Club accepted loans (Kaggle). Out of time: train on issue years up to 2015, choose and
  recalibrate on 2016, test once on 205,520 finished loans of 2017-2018 (`credit.results.split`). A leakage guard bans
  columns known only after origination ([leakage.md](leakage.md)).
- **Compared with.** A logistic scorecard, under a rule fixed before the test set was scored: LightGBM only if its
  validation AUC gain is at least 0.005 and the paired interval's lower end is above 0
  (`credit.results.model_selection`).
- **The interval.** Tight, because the test set is large. The paired difference excludes 0, so on these years
  LightGBM ranks better than the scorecard. The gain is small.
- **What it does not show.** Anything about the SYNTHETIC commercial files: the model uses consumer loan features, so
  the agent and the engine leave model PD UNKNOWN (the domain gap, [risk_service.md](risk_service.md)). Credit-model
  monitoring is not measured. Fairness on Lending Club cannot be measured (no protected attributes). The section was
  measured once, on 2026-10-12 at commit `700a5b0` with a clean tree (`credit.metadata`); the Lending Club data and
  the trained model are not in the current checkout, so a re-run needs a Kaggle key.

#### L2 Insurance claim frequency

README rows: the three frequency deviance rows and the Lorenz Gini row.

| Row | Value | Source key |
|---|---|---|
| Poisson GLM, mean Poisson deviance (lower is better) | 0.4584 [0.4488, 0.4672] | `insurance_frequency.results.test_metrics.glm` |
| LightGBM, mean Poisson deviance | 0.4493 [0.4395, 0.4583] | `insurance_frequency.results.test_metrics` |
| LightGBM minus GLM, paired | -0.0091 [-0.0107, -0.0075] | same file |
| LightGBM Lorenz Gini (GLM: 0.2767 [0.2619, 0.2928]) | 0.3208 [0.3065, 0.3365] | same file |

- **What it measures.** Deviance says how well predicted claim counts fit observed counts, given each policy's
  exposure. Lorenz Gini says how well the model ranks policies by risk.
- **Data.** freMTPL2 (OpenML 41214/41215), French personal motor policies. The split is random and stratified because
  the data has no date field (`insurance_frequency.results.out_of_time_test`).
- **Compared with.** A Poisson GLM with an exposure offset. LightGBM was preferred on validation deviance.
- **The interval.** The paired difference excludes 0: LightGBM fits better on this split, by a small amount.
- **What it does not show.** How the model holds up on a later year (no dates, so no out-of-time test and no PSI), or
  anything about commercial insurance. Written on 2026-10-11 from a dirty tree (`insurance_frequency.metadata`).

#### L3 Insurance severity and expected loss

README rows: the severity rows and the three expected-loss rows.

| Row | Value |
|---|---|
| Severity, Gamma GLM, mean Gamma deviance | 1.1678 [1.0855, 1.2565] |
| Severity, LightGBM minus GLM, paired | -0.0024 [-0.0135, 0.0089] |
| Expected loss, GLM pair, observed / predicted capped loss | 1.0187 [0.9570, 1.0812] |
| Expected loss, LightGBM pair | 1.0437 [0.9808, 1.1093] |
| Expected loss, the pair the API serves (LightGBM frequency x GLM severity) | 1.0185 [0.9569, 1.0822] |

All from `results/risk_metrics.json` (`insurance_severity.results`).

- **What it measures.** Severity deviance is the fit of claim amounts. Observed over predicted total loss says whether
  frequency x severity is right on average; 1.0 is exact.
- **Compared with.** GLM against LightGBM, and the API's served pair on its own.
- **The interval.** The severity difference includes 0, so LightGBM is not shown to be better and the GLM is kept. All
  three loss ratios include 1.0: no evidence of a total mis-calibration on this split.
- **What it does not show.** Large losses (amounts are capped, so expected loss is on a capped basis), per-policy
  accuracy, or stress tests: `results/risk_metrics.json` has no insurance stress-test result, which is why the resume
  lines leave that clause out.

#### L4 Fairness demonstration

README row: "Fairness demo (UCI German Credit), logistic regression".

- **Value.** Adverse impact ratio, age under 25 against 25 and over: 0.855 [0.669, 1.051] (`results/risk_metrics.json`,
  section `fairness_demo`).
- **What it measures.** The approval rate of the younger group divided by the older group's at the model's approval
  threshold. The file records the four-fifths threshold of 0.8 as the usual rule of thumb.
- **Data.** UCI Statlog German Credit, a small public dataset, used because Lending Club has no protected attributes.
  The file carries a warning about the dataset's documented sex coding (`fairness_demo.data_quality_warning`).
- **Compared with.** 1.0 (equal rates) and the 0.8 rule of thumb.
- **The interval.** It runs from below 0.8 to above 1.0, so this data cannot tell "no disparity" from "a disparity
  below the four-fifths line".
- **What it does not show.** Fairness of the credit or insurance models, fairness monitoring (not measured), or any
  legal conclusion.

### 3.2 Lender Desk: documents and the Evidence-to-Action Engine

#### L5 Document extraction

README rows: "Document extraction, rules extractor on the PDF" and the Status row "Document intelligence".

- **Value.** Field F1 0.9936 [0.9889, 0.9971] on 60 SYNTHETIC test packets (`results/docintel_metrics.json`,
  `rules.fields.overall.f1`). Text-layer pages 1.0; scanned pages 0.9159 [0.8736, 0.9566]. Extracted values supported
  by the printed text of their cited page: 0.9984 [0.9975, 0.9992], 36,230 of 36,288
  (`rules.citations.supported_all_values`).
- **What it measures.** Micro-averaged F1 over every extracted value. A wrong value counts as one false positive and
  one miss.
- **Data.** 30 credit and 30 insurance SYNTHETIC packets; some pages are image-only "scans", read with Tesseract. Rules
  extractor only: 0 LLM calls and 0.0 USD (`rules.cost`).
- **Compared with.** No outside baseline. Inside the file, the same clean pages forced through OCR give 0.9878; the
  paired difference 0.0122 [0.0077, 0.0192] is the cost of OCR.
- **The interval.** Packet-level bootstrap, so values from one packet stay together. Bank-statement transaction rows are
  most of the values, so the overall F1 is mostly a transaction-table metric.
- **What it does not show.** Real documents: the text layer prints exactly what the generator wrote, so clean pages are
  easy by construction, and the scanned-page number is the informative one. The LLM (hybrid) extractor: not measured.
  The file was written from a dirty tree at `271ca4d` and the clean-tree re-run is pending (`provenance`).

#### L6 Contradiction detection

README rows: contradiction recall and precision.

- **Value.** Recall 0.9412 [0.8723, 1.0000] (48 of 51 planted problems found); precision 0.7500 [0.6027, 0.8868] (51 of
  68 contradictions match a planted problem) (`results/docintel_metrics.json`, `rules.contradictions`; exact intervals
  [0.8376, 0.9877] and [0.6302, 0.8471] in the same block).
- **What it measures.** Recall: of the problems the generator planted, how many became a contradiction. Precision: of
  the contradictions raised, how many match a planted problem.
- **Data.** The same 60 packets; each planted problem has a known cause.
- **Compared with.** No baseline. Next to it: benign-vs-unsupported explanation accuracy 0.9, 36 of 40
  (`rules.benign_vs_unsupported`).
- **The interval.** Only 51 planted problems, so it is wide. The 3 misses are planted instructions: the tripwire caught
  the wording it knows and none of the new wording (`rules.injections.by_technique`).
- **What it does not show.** Real-world fraud detection. Precision depends on the generator's mix of problems.

#### L7 Claim-status agreement

README rows: "Claim-status agreement with the documented status rules (same author; not held out)", oracle and pipeline.

- **Value.** Oracle extraction 0.9935 [0.9857, 0.9987] (2,309 of 2,324 claims); pipeline extraction 0.9703 [0.9500,
  0.9852] (1,926 of 1,985) (`results/evidence_metrics.json`, section `packets`).
- **What it measures.** Whether each ledger claim's status (VERIFIED, CONFLICTING, UNKNOWN ...) matches a documented
  status table.
- **Data.** The 60 SYNTHETIC test packets. "Oracle" uses the generator's perfect extraction, which isolates the engine;
  "pipeline" uses the Phase 2 extraction from the PDFs, end to end.
- **Compared with.** Status table v2, written by the same author as the ledger.
- **The interval.** Narrow, but the agreement is not independent and not held out: table v2 was written after the v1
  test numbers had been seen (`packets.results.protocol.development_note`).
- **What it does not show.** Accuracy against an independent truth. It shows that the code follows its own written
  rules.

#### L8 Calculation agreement

README rows: "Calculation agreement with the as-documented application", oracle and pipeline.

- **Value.** Oracle 1.0000, exact interval [0.9938, 1.0000] (590 of 590); pipeline 0.8271 [0.7332, 0.9078] (488 of
  590). Paired, pipeline minus oracle: -0.1729 [-0.2668, -0.0922] (`results/evidence_metrics.json`, `packets`).
- **What it measures.** Whether the engine's calculations (DSCR, loss ratios and the rest) equal the values computed
  from the application as the documents state it.
- **Compared with.** The as-documented application from the generator's truth.
- **The interval.** The oracle passes every case, so its bootstrap interval has zero width and the README prints the
  exact interval instead. The pipeline drop is extraction errors flowing into the calculations.
- **What it does not show.** That the formulas match any real lender's policy (they implement the SYNTHETIC Northwind
  guideline). The DSCR's debt service takes the higher of two payment totals, not the per-debt maximum the guideline
  words, so it can understate debt service ([Phase 4](walkthrough_phase4.md), question 3).

#### L9 Top request changes the recommendation

README rows: "Top-ranked request answered with its hidden truth changes the recommendation", oracle and pipeline.

- **Value.** Oracle 0.4186 [0.2856, 0.5714] (18 of 43); pipeline 0.3529 [0.2121, 0.5173] (12 of 34)
  (`results/evidence_metrics.json`, `packets`).
- **What it measures.** When the engine's top-ranked evidence request is answered with the generator's hidden truth,
  how often the reference recommendation changes. When that request's value-of-information score was 0, it never
  changed (0 of 19 in each mode).
- **Compared with.** Nothing here; the simulator (L11) is the comparison with a checklist.
- **What it does not show.** That the ranking is optimal. The "next-best evidence" top-3 agreement in the same file is
  labelled CIRCULAR (the generator's resolving request and the engine's rules were written by the same author), so the
  README leaves it out.

#### L10 Decision stability

README rows: "Share of evaluable packets stable across every tested shock", oracle and pipeline.

- **Value.** Oracle 0.6667 [0.5333, 0.7833] (40 of 60); pipeline 0.6600 [0.5098, 0.7843] (33 of 50)
  (`results/evidence_metrics.json`, `packets`).
- **What it measures.** The share of packets whose reference recommendation did not change under any tested shock
  (revenue cuts, rate rises, claim increases, and two "what if the document is right" counterfactuals).
- **The interval.** Wide; 60 packets.
- **What it does not show.** A real portfolio stress test; shock sizes are planning choices bracketing the guideline's
  own stress. Packets where no shock could test anything are "undetermined", not stable (10 of 60 in oracle mode).

#### L11 Evidence-request simulator

README rows: the four "Evidence-request simulator (innovation S5)" rows.

| Row | Value | n |
|---|---|---|
| VOI minus checklist, cost-weighted requests per case (paired) | -1.2689 [-1.4515, -1.0932] | 370 |
| VOI minus checklist, decision error vs the full-information recommendation | 0.0135 [0.0000, 0.0270] | 370 |
| Held-out family (claims history mismatch), cost-weighted requests | -2.2167 [-2.8004, -1.7496] | 30 |
| Held-out family, decision error | 0.1667 [0.0667, 0.3333] | 30 |

All from `results/evidence_metrics.json`, section `simulator`.

- **What it measures.** Two paired differences per case: how many cost-weighted evidence requests each policy made, and
  how often it ended on a different recommendation than the one it would give with every fact revealed.
- **Data.** SIMULATED cases on SYNTHETIC packets with planted problems and hidden true answers, perfect extraction.
- **Compared with.** A checklist that asks for everything (the file also has a random policy and an oracle). Requests
  per case: VOI 0.3838, checklist 1.0703. Decision error: VOI 0.027, checklist 0.0135.
- **The interval.** The cost difference is clearly below 0: VOI asks for less. The error difference touches 0 at its
  lower end: VOI may cost a little accuracy. On the held-out family the error interval excludes 0, so VOI was worse
  there; the lookahead variant (an addition, not the headline) brought it to 0.0333 [0.0, 0.1].
- **What it does not show.** Real time or cost savings (cost weights and priors are the author's assumptions), that the
  score is optimal or an economic model, or anything about extraction. The diagnostics show the priors are not the
  hidden truths' distribution (`diagnostics`).

### 3.3 Lender Desk: retrieval, screening, agent, MCP

#### L12 RAG retrieval

README rows: the two hybrid + rerank rows, the "RAG answers" rows and the four mode rows of the GenAI table.

| Mode | Recall@5 | MRR@10 |
|---|---|---|
| dense | 0.8786 [0.8155, 0.9369] | 0.7416 [0.6737, 0.8077] |
| bm25 | 0.8107 [0.7379, 0.8835] | 0.6349 [0.5591, 0.7065] |
| hybrid | 0.9029 [0.8447, 0.9563] | 0.7788 [0.7170, 0.8380] |
| hybrid_rerank (default) | 0.9078 [0.8544, 0.9563] | 0.8285 [0.7683, 0.8846] |
| hybrid_rerank minus dense, recall@5, paired | 0.0291 [-0.0291, 0.0874] | |

All from `results/rag_metrics.json`.

- **What it measures.** Recall@5: the share of answerable questions with a gold section in the top 5 hits. MRR@10: the
  average of 1 / rank of the first gold hit.
- **Data.** 113 SYNTHETIC questions, 103 answerable (`question_set`), over the two SYNTHETIC guideline documents and two
  public regulatory documents, in a local Qdrant index with fastembed models.
- **Compared with.** Dense only, BM25 only and hybrid.
- **The interval.** The reranker's recall gain over dense includes 0. It also costs time: p50 3215.49 ms per query
  against 37.97 ms for hybrid, on a loaded machine. The default was kept as designed before the evaluation.
- **What it does not show.** Answer quality: faithfulness and citation accuracy are "not measured (no LLM key)"
  (`generation`). The README row "RAG answers by local Ollama models" is smoke only (3 questions per model,
  `results/generation/smoke/generation_metrics_smoke.json`); quote no rate. The same team wrote the corpus and the
  questions.

#### L13 Sanctions screening

README rows: sanctions recall, alerts per 1,000, adverse-media memo, and the GenAI rows for false-positive rate and
namesakes.

- **Value.** Recall of true matches 0.6889 [0.5476, 0.8182] at 103.8 [89.9, 118.9] alerts per 1,000 screened;
  false-positive rate on clean customers 0.0887 [0.0760, 0.1030]; exact-name namesakes (a separate stratum, n = 648)
  alerted at 0.7855 [0.7546, 0.8149]. Adverse-media memo: not measured (no LLM key). All from
  `results/kyc_metrics.json`.
- **What it measures.** At a threshold chosen on validation for a budget of 100 alerts per 1,000 customers, the share
  of true matches that alert, and how many alerts that costs.
- **Data.** The full OFAC SDN and OpenSanctions lists (no subsampling) against SYNTHETIC customers; the test split has
  45 true matches.
- **Compared with.** Rules plus an embedding reranker. A rule fixed in advance picks the reranker only if its
  validation recall is strictly higher; it tied (0.511 each), so the simpler rules-only matcher is used
  (`selected_operating_point`).
- **The interval.** Wide, because only 45 true matches. The test alert rate came out above the validation budget, but
  its interval includes 100.
- **What it does not show.** Real-world precision (it depends on how rare true matches are). The clean false-positive
  rate is a lower bound: exact-name namesakes were set aside, and they alert most of the time. Names only: no dates of
  birth or ID numbers. A match is never cleared by code; a person decides.

#### L14 Agent and human checkpoint

README rows: the three agent rows, the memo citation row, and the GenAI agent rows (injection detector, latency, LLM
cost, screening final set, namesakes-kept sensitivity run).

| Row | Value | Source |
|---|---|---|
| Required escalations that reached the right human queue | 1.0000 (n = 60, zero-width bootstrap interval) | `results/agent_metrics.json` |
| End-to-end routing agreement with the designed labels | 0.7538 [0.6462, 0.8462] (n = 65) | same |
| Decisions recorded without a human | 0 | same |
| Injection detector on the generator's own phrasings | 0.8571 [0.5714, 1.0000] (n = 7) | same |
| Latency p95 per application, no LLM call | 657.0 ms [580.9, 701.7] | same |
| Clean parties flagged by screening | 0.1917 [0.1399, 0.2487] (n = 193) | same |
| Sensitivity run, namesakes kept: routing / clean parties flagged | 0.7077 [0.6000, 0.8154] / 0.3368 [0.2746, 0.4041] | `results/agent_metrics_homonyms_kept.json` |
| Memo citation accuracy, LLM cost and latency | not measured: no LLM provider was used | `results/agent_metrics.json` |

- **What it measures.** Whether each of 65 SYNTHETIC applications reached the human checkpoint, went to the right
  review queue, and matched its designed label end to end.
- **Data.** 65 SYNTHETIC structured applications with designed labels, real screening against the full lists, real
  hybrid retrieval, the template memo. Every risk call was unavailable.
- **Compared with.** The designed labels, written per scenario separately from the policy code.
- **The interval.** The escalation rate passes every case, so its bootstrap interval has zero width and no other
  interval was recorded. Routing misses come mainly from screening false alerts on clean parties.
- **What it does not show.** Memo quality: the memo is the deterministic template stamped "FAKE LLM - test mode". Roles
  in the command-line run are claimed, not authenticated. The namesake redraw in the generator was decided after the
  first results, which is why the sensitivity run is reported next to it. And one planted injection in new wording
  (APP-C-029) was missed by the tripwire, so no referral was added and the reference recommendation was approve where
  the designed label was refer (`deterministic_path` and `injection_detector` blocks). Only the human checkpoint stood
  between that file and an approval.

#### L15 MCP server

README rows: "MCP server, stdio" and "MCP server, streamable HTTP".

- **Value.** 17 of 17 smoke checks over stdio and 17 of 17 over streamable HTTP (`results/mcp_smoke_stdio.json`,
  `results/mcp_smoke_streamable_http.json`), 0 LLM calls.
- **What it measures.** That the server starts as a real subprocess and serves the same tool allow-list, resources and
  prompt to the official MCP Python SDK client.
- **What it does not show.** Use by any MCP desktop client (none was run) or any agent quality. It is a smoke test.

### 3.4 Lender Desk: numeric provenance and prompt injection

#### L16 Numeric provenance checker

README rows: the eight SIG mutation rows, the "Unsupported numbers in memos" row and the Status row "Numeric provenance
checker + slot grammar (SIG)".

| Mutation of a SYNTHETIC guideline sentence | SIG checker (Wilson 95%) | Incumbent agent claim checker |
|---|---|---|
| Changed value | 179 / 179, 1.0000 [0.9790, 1.0000] | 170 / 179 |
| Value scaled x10 | 184 / 184, 1.0000 [0.9795, 1.0000] | 170 / 184 |
| Unit swap | 89 / 89, 1.0000 [0.9586, 1.0000] | 0 / 89 |
| Value from another section | 175 / 175, 1.0000 [0.9785, 1.0000] | 161 / 175 |
| Comparator flip | 88 / 108, 0.8148 [0.7312, 0.8768] | 0 / 108 |
| Value from another row of the same table (known blind spot) | 6 / 55, 0.1091 [0.0510, 0.2183] | 0 / 55 |
| False flags on unmutated sentences | 0 / 203, 0.0000 [0.0000, 0.0186] | 0 / 203 |
| False flags on equivalent rewrites | 0 / 87, 0.0000 [0.0000, 0.0423] | 17 / 87 |
| Unsupported numbers in memos | not measured (needs the Phase 4 evidence ledger and LLM runs) | |

From `results/provenance_metrics.json` (`numeric_checker.mutation_test` and `numeric_checker.incumbent_claim_checker`).

- **What it measures.** Whether the checker flags a sentence whose number no longer matches its cited section, and
  leaves alone a sentence that is unchanged or only reworded.
- **Data.** Reference sentences built from the SYNTHETIC guidelines, with seeded mutations (seed 42).
- **Compared with.** The agent's existing claim checker, which matches numbers without units or comparators.
- **The interval.** Wilson, because most rows pass every case: 179 of 179 rules out rates below 0.9790.
- **What it does not show.** Memos: it was tested on guideline sentences, not on memos. It is not held out: the checker
  was debugged on these sentences (`mutation_test.development_note`). The mutation set was regenerated on 2026-10-13
  (`mutation_set_note`), so earlier and later results are not comparable. The wrong-row blind spot is measured, not
  fixed.

#### L17 Prompt injection

README rows: the tripwire row, the two attack-success rows, and the Status row "Prompt-injection containment".

- **Value.** The tripwire flags 12 of 60 frozen payloads on raw text, 0.2000 [Wilson 0.1183, 0.3178], and 16 of 60
  after text normalization, 0.2667 [Wilson 0.1713, 0.3901] (`results/injection/injection_metrics_detector_only.json`,
  `detector`). Attack success at prompt layer L2: not measured (no model run for this layer). Attack success on a local
  Ollama model: smoke only, 3 of 240 planned calls made, no rate quoted
  (`results/injection/smoke/injection_metrics_smoke.json`).
- **What it measures.** The tripwire is a fixed pattern detector. Attack success would be the share of model answers
  that carry an attacker's canary, per prompt layer.
- **Data.** 60 frozen payloads: 40 public ones from `deepset/prompt-injections` at a pinned revision, plus 20
  hand-written ones.
- **Compared with.** Raw text against normalized text (invisible characters, lookalike letters).
- **The interval.** Wilson; the public payloads include near-duplicates, so their intervals are somewhat too narrow.
- **What it does not show.** A protection rate. The decision-level claim is different: injected text cannot change the
  recommendation by construction, enforced by the 17 tests listed in `action_level.tests` ("enforced by
  tests/security/test_containment.py; not an empirical rate"). The memo prompt is not a measured layer.

### 3.5 Governance

#### G1 Drift monitor

README rows: the three drift rows, "Drift monitor of the credit model" and "Monitoring of LLM memos".

| Scenario (SIMULATED streams) | Measure | Value |
|---|---|---|
| No injected drift | Streams with a false alert | 0.0033 exact [0.0001, 0.0184] (1 of 300) |
| SYNTHETIC claim rate 25% higher | Streams where drift was detected | 0.9933 [0.9833, 1.0000] (298 of 300) |
| SYNTHETIC claim rate 10% higher | Streams where drift was detected | 0.1633 [0.1233, 0.2100] (49 of 300) |
| Credit model | Drift and calibration checks | not measured: the Lending Club data and every trained credit model are absent from this checkout |
| LLM memos | Unsupported-claim rate | not measured (needs an LLM run) |

All from `results/governance_metrics.json`, section `monitoring` (`results.validation`).

- **What it measures.** Whether the monitor raises an alert (a breach, not a warning) on streams where the truth is
  known because the drift was injected.
- **Data.** Public freMTPL2 held-out policies drawn into SIMULATED streams; the monitored model is a separate
  demonstration claim-frequency GLM, not an inventory model.
- **Compared with.** The known injected drift.
- **The interval.** The exact interval for 1 of 300 shows a false-alert rate up to 0.0184 is still consistent.
- **What it does not show.** That it catches mild drift: a 10% claim rise was caught in 49 of 300 streams and a mild
  covariate shift in 0 of 300 (`validation.covariate_shift_young_drivers_mild`). Nothing here monitors a model that
  makes decisions, and there is no real time axis (freMTPL2 has no dates).

#### G2 Champion and challenger

README rows: "AUC, challenger minus champion (paired)" and "Recommendation".

- **Value.** -0.0003 [-0.0029, 0.0025]; recommendation `keep_champion` (`results/governance_metrics.json`, section
  `champion_challenger`).
- **What it measures.** Whether a model retrained on one more year ranks out-of-time loans better than the one in use.
- **Data.** SYNTHETIC Lending Club-shaped loans; both models scored on the same out-of-time loans neither saw.
- **Compared with.** The champion, under a promotion rule fixed in code before the run.
- **The interval.** Includes 0, so the rule keeps the champion. Even a pass would only be advice: only the
  `model_validator` role can promote.
- **What it does not show.** Anything about real loans. The generator draws every issue year from one distribution, so
  a retrain is not expected to help; this shows the procedure.

#### G3 Reviewer override rate

README row: "Reviewer feedback, SIMULATED reviewer actions".

- **Value.** 0.1385 [0.0615, 0.2308] (9 of 65) (`results/governance_metrics.json`, section `feedback`).
- **What it measures.** The share of decisions where the reviewer approved or declined against the reference
  recommendation. Every override needs a reason code from a fixed list.
- **Data.** A SIMULATED reviewer deciding the 65 SYNTHETIC applications through the real checkpoint, with a made-up
  override probability.
- **What it does not show.** Real reviewer behaviour. It describes the simulation and shows that recording and counting
  work.

#### G4 Outcomes

README row: "Outcomes, SYNTHETIC outcomes joined to SIMULATED decisions".

- **Value.** Default rate of approved loans with an outcome: 0.2180 [0.2060, 0.2310] (1,010 of 4,632)
  (`results/governance_metrics.json`, section `outcomes`).
- **What it measures.** That outcomes can be joined back to decisions by application id, and the default rate per
  decision type.
- **What it does not show.** A real portfolio. In a real book a declined application has no outcome (selective labels);
  here every row is a SYNTHETIC issued loan and the decisions come from a stated SIMULATED rule.

#### G5 Release gate

README row: "Release gate over the SYNTHETIC and public-data results".

- **Value.** Verdict PASS: 15 rows, 11 PASS, 0 FAIL, 0 MISSING, 4 NOT_MEASURED (`results/governance_metrics.json`,
  section `release_gate`, `results.counts`).
- **What it measures.** Nothing new. It compares the latest results with a reviewed baseline and fails CI on a
  regression beyond a stated tolerance, or a missing required value.
- **The four NOT_MEASURED rows.** `llm_citation_accuracy`, `llm_unsupported_claim_rate`, `injection_asr_l1` and
  `injection_asr_l2`: no LLM call and no full injection run exist. NOT_MEASURED is listed, never counted as a pass.
- **What it does not show.** That the results are current: results files written from a dirty tree are only warnings,
  and a model or prompt change needs a manual migration certificate that no CI job checks.

### 3.6 Founder Studio

Educational planning tool. Not financial, legal or lending advice. Lenders make their own decisions. Every Studio
probability below is "simulated, based on stated assumptions".

#### F1 Data coverage and supported metros

README rows: "Data fabric (Studio S1)", the Status row "S1 data fabric" and "Supported metros".

- **Value.** The lowest business-type coverage per real metro is 0.6779 to 0.6826 (short-term rental), against a
  threshold of 0.8, so every real metro is in limited mode. Motel is 0.745 to 0.7722; car wash, laundromat,
  restaurant/cafe and retail storefront are 0.8675 to 0.9415. The SYNTHETIC metro's lowest is 0.896
  (`results/studio_metro_coverage.json`).
- **What it measures.** The weighted share of a business type's required factors that are backed by data, over the
  metro's tracts. A metro runs in full mode only when every business type meets the threshold.
- **Data.** Public Census, LEHD, Overture, FHWA HPMS, BLS, FRED and HUD data in per-metro snapshots.
- **Supported metros.** The five largest US metropolitan statistical areas by POPESTIMATE2025, ranked among 387 by a
  script that checks the rule (`results/studio_metro_ranking.json`, `configured_equals_top_five: true`): New York
  (20,112,448), Los Angeles (12,844,441), Chicago (9,434,123), Dallas-Fort Worth (8,477,157), Houston (7,904,627). The
  sixth would be Atlanta (6,482,182).
- **What it does not show.** Data quality, or that these are good markets: the plan's reasons for these cities are
  still marked UNVERIFIED in [metros.md](studio/metros.md).

#### F2 SiteIQ

README rows: the six SiteIQ rows and the Status row "S2 SiteIQ".

| Business type | SiteIQ Spearman | Single-factor baseline (factor) | SiteIQ minus baseline, paired |
|---|---|---|---|
| car wash | 0.0719 [0.0281, 0.1210] | 0.0905 [0.0474, 0.1358] (traffic) | -0.0186 [-0.0434, 0.0043] |
| laundromat | 0.7910 [0.7642, 0.8119] | 0.8128 [0.7857, 0.8339] (renter households) | -0.0217 [-0.0496, 0.0036] |
| motel | 0.1568 [0.0901, 0.2195] | 0.2077 [0.0919, 0.3230] (tourism) | -0.0509 [-0.1268, 0.0273] |
| restaurant / cafe | 0.7454 [0.7063, 0.7766] | 0.7447 [0.7105, 0.7752] (resident population) | 0.0007 [-0.0146, 0.0164] |
| retail storefront | 0.3995 [0.3556, 0.4384] | 0.5940 [0.5446, 0.6398] (resident population) | -0.1945 [-0.2362, -0.1494] |
| short-term rental | 0.5925 [0.5489, 0.6283] | 0.5072 [0.4578, 0.5571] (tourism) | 0.0853 [0.0542, 0.1153] |

From `results/studio_siteiq_metrics.json`, `by_business_type`.

- **What it measures.** The rank correlation between SiteIQ's overall score and the hidden true demand, over the tracts
  of 10 SYNTHETIC cities.
- **Data.** 10 SYNTHETIC cities whose true demand uses a different formula from SiteIQ's (gravity decay, saturating
  competition, interactions, a hidden appeal field, noise).
- **Compared with.** The profile's single highest-weight factor used alone, and a random ranking.
- **The interval.** Two-level bootstrap (cities, then tracts), paired. Plainly: SiteIQ beats random for every type, but
  it beats the simple baseline for only 1 of 6 (short-term rental), is clearly worse for retail, and the interval
  includes 0 for the other 4.
- **Why.** For retail, income and competition point the wrong way in this world. For car wash, every tract centroid's
  nearest road is the local street in all 10 cities (`diagnostics`), so traffic measures the wrong road.
- **What it does not show.** Real-world accuracy. The weights were deliberately not tuned on these cities.

#### F3 Feasibility simulator

README rows: the two feasibility rows and the Status row "S3 Monte Carlo feasibility".

| Condition | Simulator Brier | Single-point forecast Brier | In-sample base rate Brier |
|---|---|---|---|
| Well specified | 0.1405 [0.1321, 0.1493] | 0.4796 [0.4596, 0.4992] | 0.1567 [0.1473, 0.1664] |
| Misspecified | 0.1433 [0.1351, 0.1523] | 0.4938 [0.4729, 0.5154] | 0.1557 [0.1460, 0.1654] |

From `results/studio_feasibility_metrics.json`, `headline` (2,400 SYNTHETIC worlds per condition).

- **What it measures.** The Brier score of P(year-2 DSCR meets the lender target): the mean squared error of a
  probability against the true 0/1 outcome. Lower is better. It is "simulated, based on stated assumptions".
- **Data.** 400 SYNTHETIC worlds per business type. Well specified: the truth comes from the simulator's own model (a
  check of the code). Misspecified: the truth adds effects the simulator does not model.
- **Compared with.** A single forecast with every input at its most likely value (the spreadsheet plan) and the
  in-sample base rate (optimistic).
- **The interval.** The simulator beats the single forecast by a wide margin: the single forecast said "yes" in 0.5883
  of worlds while 0.1946 got there (`conditions.well_specified.pooled.dscr_year2`). It beats the base rate only
  narrowly. When the model is wrong, P10-P90 revenue bands cover 0.6211 [0.6074, 0.6349] of months against 0.7984
  expected for a correct model (`method.coverage_reference_if_well_specified`): the bands are too narrow.
- **What it does not show.** Real-world accuracy: no real outcome data exists. Every simulator range is the author's
  ASSUMPTION.

#### F4 Founder narrative

README rows: the five "Founder narrative" rows and the Status row "S4 founder ledger + provenance-locked narrative".

| Row | Value | Source |
|---|---|---|
| Template, 36 golden scenarios: narrative numbers traced to the ledger | 1.0000 [0.9977, 1.0000] (1,673 / 1,673, Wilson) | `results/studio_evidence_metrics.json` |
| Narrative written by an LLM: fallback to the template | not measured (the template provider makes no LLM call) | same |
| Template, 6 SYNTHETIC-city scenarios after the review fixes | 1.0000 [0.9867, 1.0000] (285 / 285, Wilson) | `results/studio_evidence_metrics_synthetic.json` |
| Wrong-slot drafts flagged by any of the three checks (known blind spot) | 0.0833 [0.0361, 0.1807] (5 / 60, Wilson) | same |
| Local model `qwen2.5-1.5b-instruct-f16`: drafts that passed the grammar | counts only: 6 of 6 fell back; 0 of 6 valid first try, 0 of 6 after a retry (12 calls) | `results/studio_evidence_metrics_ollama.json` |

- **What it measures.** Whether every number in the founder's narrative equals the ledger claim that its own sentence
  cites. The writer may only place slots like `{{claim:SIM.p_dscr_year2}}`; code fills the values.
- **Compared with.** The older round-trip audit, on the same mutated founder sentences: values moved between sentences
  were caught 0 of 686 times by the audit and 686 of 686 by the bound check; flipped comparators 0 of 164 against 164
  of 164 (`results/studio_evidence_metrics.json`).
- **The interval.** Wilson, because every case passes.
- **What it does not show.** For the template, "100% traced" is by construction: it is a regression guard, not evidence
  about a model. The 1,673 run was measured before the review fixes from a dirty tree; after the fixes only the 6
  SYNTHETIC-city scenarios were re-run. The one model measurement is the local 1.5B smoke-sized run, whose provenance
  the S4 walkthrough says to treat as approximate. A slot that points to the wrong claim of the same kind is caught 5 of
  60 times.

#### F5 Path to approval

README rows: the two path rows and the Status row "S5 path to approval".

- **Value.** A one- or two-lever path was found for 0.1026 [0.0784, 0.1306] of the 536 SYNTHETIC plans that needed a
  change; a path that also meets the SYNTHETIC startup program's equity and loan rules for 0.0392 [0.0243, 0.0578]
  (`results/studio_path_metrics.json`, `headline`).
- **What it measures.** For plans that miss the three default targets (P(year-2 DSCR >= 1.25) at least 0.80, P(year-2
  DSCR >= 1.00 after the lender's 20% revenue stress) at least 0.80, P(cash < 0 within 24 months) at most 0.10;
  `settings.targets`), whether a change of one or two plan choices inside the default bounds reaches all three.
- **Data.** 540 seeded SYNTHETIC plans: 4 already met the targets, 55 found a path, 481 had none.
- **Compared with.** A brute-force search on 240 plans: it found paths on the same 54 plans as the heuristic, of 236
  that needed a change (`heuristic_vs_brute_force`).
- **The interval.** The rate is low on purpose and depends on the plans: by starting band it runs from 0.0059 to
  0.2558 (`by_band`). Without the stress target, 0.749 [0.6963, 0.8057] of the shown changes would fail the lender's
  own NSP-4.2 check (`stress_target_effect`).
- **What it does not show.** Any founder's chance of approval. Targets and bounds are editable design choices.

#### F6 Lender-ready package and handoff

README rows: the four S6 rows and the Status row "S6 lender-ready package".

| Row | Value |
|---|---|
| Printed numbers not traced to the ledger, 36 golden packages | 0 in the package text and 0 in the PDF text, of 30,681 read back |
| Packages that reached the agent's human checkpoint | 1.0000 [0.9036, 1.0000] (36 / 36, Wilson) |
| Decisions recorded without a human | 0.0000 [0.0000, 0.0964] (0 / 36, Wilson) |
| 35 SYNTHETIC startup applications: recommendation agrees with hand-written labels | 1.0000 [0.9011, 1.0000] (35 / 35, Wilson) |

From `results/studio_handoff_metrics.json`.

- **What it measures.** That every printed number in the package and in the PDF's own text is a ledger claim, and that
  every handoff stops at the human checkpoint.
- **Data.** 36 golden SYNTHETIC founder packages (6 business types x the SYNTHETIC metro and the 5 real metros), plus 35
  SYNTHETIC startup applications with designed labels.
- **The interval.** Wilson, because every case passes.
- **What it does not show.** "0 untraced" is partly by construction, so the audit is a guard against regressions. The
  labels were written against the same SYNTHETIC program. The 30 real-metro packages ran in limited mode. No lender
  has seen a package.

#### F7 End-to-end evaluation against a baseline

README rows: the S12 rows of the headline table (ten "Founder Studio end to end (Studio S12)" rows and "Founder Studio
with real people") and the Status row "S12 end-to-end evaluation".

| Row | Value |
|---|---|
| Seconds from inputs to an audited PDF package, median (machine wall clock on a shared machine, varies by run; not a user time) | 6.227 [6.106, 6.456] (n = 36) |
| Scenarios with a complete package (audited PDF, no untraced number in the package or PDF text, every page labelled) | 1.0000 [0.9036, 1.0000] (36 / 36, Wilson) |
| Scenarios whose package had a SiteIQ site score in this run | SYNTHETIC metro 6 of 6; each of the five real metros 0 of 6 |
| Printed figures with a checkable source (rule S): Studio vs baseline B0 | 0.8482 [0.8388, 0.8587] (12,222 / 14,410) vs 0.0255 [0.0185, 0.0325] (33 / 1,296) |
| Rule S, Studio minus B0, paired | 0.8227 [0.8113, 0.8347] |
| Printed figures backed by a document page, a public-data row or a program or target section (rule E): Studio vs B0 | 0.0570 [0.0545, 0.0597] (821 / 14,410) vs 0.0255 [0.0185, 0.0325] (33 / 1,296) |
| Rule E, Studio minus B0, paired | 0.0315 [0.0254, 0.0377] |
| The founder's typed figures backed by a document, one population for both plans | 0.1315 [0.0958, 0.1693] (33 / 251) for both; "expected equal by construction" |
| Language-model cost per package | 0 USD, by construction (template narrative and memo), checked per package |
| Compute cost per package | not measured: no cloud resource was used and the machine's cost is not metered |
| Usability test with real people | not measured: no usability test with real people was done, so there is no n, protocol or raw time |

From `results/studio_business_metrics.json`, `headline`, `time_to_complete_package.complete_packages` and
`data_coverage_by_metro.this_run`. The run is at `6fcfad9` with a clean tree (`provenance.git_dirty: false`), generated
2026-10-26T21:35:23 UTC and committed in `667db85`. It replaces the first run (at `e68b88d`, committed in `b44b581`)
after the review fixed B0 (section 5.3). Every S12 row that compares Studio with B0 carries this caveat
(`docs/studio/s12_baseline.md` section 2 requires saying, wherever the comparison is shown, that B0's sources follow
from its definition, not from how real founders write plans): **B0 is a construction from the same SYNTHETIC inputs,
not a sample of real plans, and the gap under both rules follows mostly from the definitions** (rule S credits model
runs and formulas, rule E program and target sections; B0 prints neither).

- **What it measures.** The whole Studio pipeline on the 36 golden scenarios: time to an audited package, and how many
  printed figures carry a source a reader can check.
- **Data.** 36 SYNTHETIC scenarios: 6 business types x the SYNTHETIC metro and the 5 real metros. The prompt's 30
  real-metro ones are also reported on their own (`real_metro_scenarios` under
  `evidence_coverage.rule_s_checkable_source` and `evidence_coverage.rule_e_external_evidence`): rule S 0.8362
  [0.8304, 0.842] against 0.025 [0.0175, 0.0324] for B0; rule E 0.0539 [0.0527, 0.0551] against the same B0 share
  (27 / 1,080), with the same caveat. The handoff consistency check screened against "full OFAC + OpenSanctions
  lists (sdn.csv, alt.csv, targets.simple.csv)" (`configuration.handoff_consistency_check`), and the SHA-256 of each
  list file is recorded (`provenance.data_snapshots.screening_list_snapshots`).
- **Compared with.** B0, a plan made from the same SYNTHETIC inputs and documents without SiteIQ, the simulation and
  the ledger: the founder's typed figures, the untyped inputs at their most likely value (the shock inputs and the
  monthly noise left out) and years 1-3 of the one-forecast run. A B0 figure has a source only when it is a founder
  figure that every document about it agrees with. B0 was committed in `docs/studio/s12_baseline.md` (commit
  `4a19865`) before the script and before any S12 number, and that file has not been edited since (its SHA-256 in
  `provenance.eval_sets` matches the committed file).
- **The interval.** Scenario bootstrap: whole scenarios are resampled because figures in one package are not
  independent. The rule S gap is large, but the file's own reading says why: "Most of the Studio - B0 gap follows from
  the definitions: model estimates and calculations count for Studio because the ledger records their run or formula
  and inputs, and B0 prints no such record." 10,745 of the Studio figures are model estimates
  (`evidence_coverage.studio_figures_by_status_kind_and_verdict`). Rule E removes that credit, and the gap shrinks to
  0.0315. Rule E has a definitional credit of its own: 724 of Studio's 821 rule E figures are VERIFIED policy claims
  (sections of the SYNTHETIC program and the Studio's own targets), which B0 by definition never prints. So the rule E
  gap also follows mostly from the definitions. The founder-figure row uses one population for both plans and is equal
  in every scenario (`equal_in_every_scenario: true`); Studio surfaced 12 conflicts between a typed figure and a
  document, and B0 has no step that could.
- **What it does not show.** A time saving (nobody was timed; B0's time is "not measured: no one was timed writing a
  plan"), anything about real founders' plans, or usability. The time is machine wall clock on a shared machine, so it
  varies by run. SiteIQ is not part of every package: in this run SiteIQ scored a site only in the 6 SYNTHETIC-metro
  scenarios, 0 of 30 real-metro ones (`data_coverage_by_metro.this_run`). The file only says this "depends on which
  metro snapshots exist on the machine that ran the script"; the S12 walkthrough names the likely cause: that machine
  had no real-metro tract snapshot.

### 3.7 Web app and deployment

#### W1 Browser flows and accessibility

README rows: "Playwright end-to-end flows", "axe-core scans", "Web app on the Hugging Face Space", and the Status row
"Web app v0.1".

- **Value.** On a LOCAL container (dev login, SYNTHETIC inputs): 5 of 5 Playwright flows passed; axe-core found a
  serious or critical issue in 0 of 32 scans (`results/web_metrics.json`, `runs.local-container`). On the Space: not
  measured (no `runs.hf-space.e2e.flows` in the file).
- **What it measures.** That the main founder and lender flows work in a headless browser, and what automated
  accessibility checks can see.
- **What it does not show.** WCAG conformance (axe left items in 20 of the 32 scans for a person to review),
  performance (the Studio results page had a median Lighthouse performance score of 44 over 3 runs with the mobile
  preset, `lighthouse.pages`), Hugging Face sign-in (never run for real), or anything on the Space.

#### W2 Container smoke test

README rows: "Smoke checks", "Request latency p95" and "Hugging Face Space deployment".

- **Value.** 9 of 9 checks passed; request latency p95 over every path, one client, 5.73 ms (n = 100, no interval
  recorded) (`results/deployment_metrics.json`, `runs.local-container`). On the Space: not measured.
- **What it does not show.** Load (one client, one request at a time), Space hardware, or a per-packet latency.

#### W3 Deployment

Status row "Deployment (Hugging Face Docker Space)": **Not deployed.** The Space and the deploy workflow exist; no
deploy has run, so there is no public URL and no number from the Space.

### 3.8 Status rows with no metric

#### N1 Status rows with no metric

| Status row | What exists | What to say |
|---|---|---|
| LLM provider layer (Anthropic, OpenAI, Ollama, Hugging Face Inference, fake; cost cap, cache) | Built; live calls smoke only | Anthropic and OpenAI were never called (no key). Hugging Face Inference is off by default and never ran. Ollama smoke runs only. |
| Security (Phase 5 B): roles, packet permissions, encryption at rest, hash-chained audit events | Built, tests only | Several controls are written and tested but not yet called by an entry point a user runs ([security.md](security.md)). |
| Observability: OpenTelemetry / OpenInference spans with PII redaction | Built, tests only | Spans were created, not exported (`results/agent_metrics.json`). |
| Governance: results provenance blocks; LLM migration gate and freeze proofs | Provenance built; gate in progress | The full champion and challenger generation runs have not run, so no gate decision exists ([S1b](walkthrough_innovations_s1.md)). |
| S8 privacy and security | Built + checked | Checks, not measurements: the offline suite passes, the secret scan found no leaks and the dependency audit no known vulnerabilities (2026-10-20). No upload route exists. Privacy and Terms are drafts pending the owners' approval ([S8](studio/walkthrough_s8.md)). |

## 4. Trade-offs

Each trade-off: what we chose, the alternative, why, and what it costs, with the evidence.

### 4.1 Deterministic template memo vs an LLM memo

- **Chose.** The memo (lender side) and the narrative (founder side) default to deterministic templates. On the
  founder side a model may only write prose with slots, and code fills every number from the ledger. On the lender
  side an LLM memo would be a structured draft (reasons with citations) checked afterwards by the claim checker; the
  slot-filled lender memo of the SIG plan (run R3 in [innovations.md](innovations.md)) is planned, not built into the
  agent and not run.
- **Alternative.** Let a language model write the memo.
- **Why.** No hosted API key was configured (and the owners decided on no extra spending, which is why the paid
  Hugging Face Inference provider never ran; [S4](studio/walkthrough_s4.md)). The template always works, costs
  nothing, gives the same text for the same inputs, and its numbers trace by construction. A model cannot be audited
  line by line, and a wrong number in a credit memo is the most expensive error.
- **Cost.** The prose is plain, and we cannot say anything about LLM memo quality: citation accuracy, unsupported-claim
  rate, cost and latency are all not measured. The one local test was poor: the 1.5B model's drafts all broke the
  grammar and fell back (`results/studio_evidence_metrics_ollama.json`).

### 4.2 Rules extractor vs an LLM extractor

- **Chose.** A rules extractor with Tesseract OCR; the LLM (hybrid) extractor exists with the same schema and citation
  rules but is tested only with a fake.
- **Alternative.** An LLM reading every document.
- **Why.** Deterministic, free, explainable line by line, and a value that is not printed on its cited line is
  rejected anyway. Long tables (bank statements, loss runs) stay with rules in either design.
- **Cost.** The rules extractor fits this generator's layouts; real documents have far more. F1 is 1.0 on text-layer
  pages by construction and 0.9159 on scans (`results/docintel_metrics.json`). Rules vs hybrid is not measured.

### 4.3 Limited mode vs filling the gaps

- **Chose.** A factor without data stays UNKNOWN, is left out of the score and is named. A real metro runs in full mode
  only when every business type meets the coverage threshold; otherwise it runs in "limited mode" and says so.
- **Alternative.** Fill gaps with national averages or typical values, or compute the mode per business type.
- **Why.** Studio rule G ("coverage is shown, not hidden") and rule D (never invent a benchmark). A filled value would
  look like data.
- **Cost.** All five real metros are in limited mode because two business types are below the threshold: short-term
  rental (0.6779 to 0.6826; no comparables source) and motel (0.745 to 0.7722; tourism and anchor tags partly
  unmapped, [S1](studio/walkthrough_s1.md)), even though the other four business types meet it on their own
  (`results/studio_metro_coverage.json`). A per-business-type mode is an open design question.

### 4.4 Synthetic data vs real data

- **Chose.** Public datasets for the models and SYNTHETIC documents, customers, applications, plans and cities for
  everything else, each labelled.
- **Alternative.** Real packets or real founder plans.
- **Why.** Ground rule 2: no real personal data. A generator also knows the truth, so recall, calibration and hidden
  demand can be measured at all.
- **Cost.** The same author wrote the generators, the rules and often the labels, so results describe this world, not
  real files. We say "SYNTHETIC" with every number and claim no real-world accuracy.

### 4.5 Value-of-information requests vs a checklist

- **Chose.** Ask only for evidence whose plausible answers could change the recommendation.
- **Alternative.** A checklist that asks for every open item.
- **Why.** Many open items cannot move the decision (the debt-payment rule already uses the higher payment, so either
  answer leaves the DSCR unchanged).
- **Cost.** Measured: fewer cost-weighted requests, -1.2689 [-1.4515, -1.0932], at a decision-error difference of
  +0.0135 [0.0000, 0.0270]; on the held-out family VOI was worse, +0.1667 [0.0667, 0.3333]
  (`results/evidence_metrics.json`, `simulator`). The score is myopic: two problems that each keep a referral both score
  0.

### 4.6 SiteIQ vs the single-factor baseline

- **Chose.** A multi-factor score, normalized as percentiles within the metro, with a coverage score.
- **Alternative.** Rank by the profile's single highest-weight factor.
- **The honest result.** SiteIQ mostly does NOT beat the simple baseline. On 10 SYNTHETIC cities it is better for 1 of
  6 business types (short-term rental, +0.0853 [0.0542, 0.1153]), clearly worse for retail (-0.1945 [-0.2362,
  -0.1494]), and not different for 4 (`results/studio_siteiq_metrics.json`).
- **Why keep it.** It explains itself factor by factor, shows coverage, and the validation found two concrete design
  problems (the competition sign and the wrong road for drive-by businesses). Tuning the weights to the synthetic city
  would only fit the author's own generator, so they stay untuned until real outcome data exists.
- **What to say.** "A multi-factor score has to earn its complexity, and on our synthetic validation it mostly did not.
  We report that and we know why."

### 4.7 Monte Carlo vs a single forecast

- **Chose.** 1,000 simulated paths over 36 months per plan (`results/studio_feasibility_metrics.json`, `settings`),
  every probability labelled as simulated.
- **Alternative.** One spreadsheet forecast at the most likely values.
- **Evidence.** The single forecast is a poor probability: Brier 0.4796 against the simulator's 0.1405
  (`results/studio_feasibility_metrics.json`). The cost: when reality has effects the model lacks, the bands are too
  narrow (0.6211 coverage against 0.7984 expected), so read them as a minimum spread.

### 4.8 Containment vs detection for prompt injection

- **Chose.** Make the decision path independent of document text: tools come from structured fields, numbers from
  code, the policy never reads free text, no tool can approve, and a person decides.
- **Alternative.** Rely on an injection detector.
- **Evidence.** The detector caught 12 of 60 frozen payloads on raw text
  (`results/injection/injection_metrics_detector_only.json`) and missed every new-wording injection in the document
  packets. The cost: containment protects the decision, not the words; whether a model would follow injected text is
  not measured.

### 4.9 Rules-only sanctions screening vs an embedding reranker

- **Chose.** Rules only (token-aligned Jaro-Winkler, token-set ratio, phonetic blocking), because a pre-declared rule
  uses the reranker only if it wins on validation, and it tied (`results/kyc_metrics.json`).
- **Cost.** Recall 0.6889 at the selected point; common-name false alerts from large exclusion and PEP lists. A real
  program would add secondary identifiers.

### 4.10 Hybrid retrieval with a reranker as the default

- **Chose.** Dense + BM25 with reciprocal rank fusion, then a cross-encoder, fixed before the evaluation.
- **Cost.** The reranker's recall gain over dense is not significant (0.0291 [-0.0291, 0.0874]) and it is much slower
  per query (`results/rag_metrics.json`). Changing the default after seeing the results would be tuning on test.

### 4.11 Cite, do not re-measure, and a baseline written first (S12)

- **Chose.** S12 copies data coverage, path-found rate and handoff success from the S1, S5 and S6 results files, with
  their provenance and SHA-256, and builds B0 in code from the same inputs.
- **Why.** Two scripts measuring one metric could disagree. A baseline described in words would be a guess.
- **Cost.** B0 is a construction, not a sample of real plans, and the gap under both rules is mostly a consequence of
  the definitions: rule S credits model runs and formulas, rule E program and target sections (724 of Studio's 821 rule
  E figures, `results/studio_business_metrics.json`), and B0 prints neither. That is why rule E is reported next to
  rule S, the founder-figure comparison (one population, equal by construction) next to both, and the caveat with
  every comparison. A baseline in code also has to match its written definition: the first run's B0 printed one input
  the definition leaves out, and the review fixed the code, not the definition (section 5.3).

### 4.12 Local Ollama only, no hosted model

- **Chose.** Real calls only to a local model; Anthropic and OpenAI adapters are built and tested with fakes, never
  called. Explicit configuration only: the platform's ambient base URL is never read.
- **Cost.** Every LLM quality, cost and latency metric is not measured. The local runs are smoke runs.

## 5. What failed and how it was fixed

Only items documented in the walkthroughs. "Status now" says whether it is fixed, measured but not fixed, or
disclosed.

### 5.1 Lender Desk

| What went wrong | What changed | Status now | Source |
|---|---|---|---|
| CI installed with `--frozen`, which does not check that the lockfile matches `pyproject.toml` | `uv sync --locked` in CI | Fixed | [Phase 0](walkthrough_phase0.md) |
| freMTPL2 claims without recorded amounts, and the German Credit documentation's wrong sex coding | Found by checking the data against itself; the modelling target and the fairness demo changed, both documented | Fixed and documented | [Phase 1](walkthrough_phase1.md) |
| Card-processor names invented for the v1 packet generator turned out to be real companies | Replaced in v2 | Fixed | [Phase 2](walkthrough_phase2.md) |
| The seed-42 test split was inspected during a review, so it was no longer a clean test | v2 generated with a new seed; error analysis on dev only; test split run once | Fixed | [Phase 2](walkthrough_phase2.md) |
| The injection tripwire missed every planted instruction in new wording | Not tuned on the packets; a hit only routes the file to a human, and containment is the defense | Disclosed | [Phase 2](walkthrough_phase2.md), [S2](walkthrough_innovations_s2.md) |
| The KYC customer file was not reproducible (a Faker function depended on Python's hash seed) | Removed; a test generates under two hash seeds and requires identical bytes; the run on the old file was discarded | Fixed | [Phase 3](walkthrough_phase3.md) |
| Clipping match scores at 1.0 tied many near-identical names, so no threshold could meet a budget | Linear rescaling instead of clipping | Fixed | [Phase 3](walkthrough_phase3.md) |
| Exact-name namesakes inflated alerts; the agent's namesake redraw was decided after the first results | Namesakes scored as a separate stratum; the agent set without the redraw is reported side by side | Disclosed | [Phase 3](walkthrough_phase3.md), [Phase 5](walkthrough_phase5.md) |
| Calculations over imputed defaults were printed VERIFIED, and status table v1 copied that rule | `depends_on_unknown`; table v2 with dependency-aware expectations; claim-status agreement is no longer held out, and the file says so | Fixed and disclosed | [Phase 4](walkthrough_phase4.md) |
| Stability counted shocks that tested nothing | "Not tested" shocks; a packet with none is "undetermined" | Fixed | [Phase 4](walkthrough_phase4.md) |
| The README showed a zero-width interval [1, 1] for 590 of 590 | A degenerate bootstrap share prints its exact Clopper-Pearson interval | Fixed | [Phase 4](walkthrough_phase4.md) |
| A robustness row (`empirical_dev`) was called the correct-prior reference, but it removed the true answer for 19 of 413 test problems (`results/evidence_metrics.json`, `diagnostics.latent_without_prior`) | Relabelled; `oracle_shares` added as the real reference | Fixed | [innovations](walkthrough_innovations.md) |
| A novel-wording injection (APP-C-029) was missed, and the reference recommendation came out approve | Not fixed in the policy; the human checkpoint is the last line; a measured run of whether a model follows such text is not done | Disclosed | [Phase 5](walkthrough_phase5.md) |
| The agent and the MCP server read the risk URL with two meanings | One risk client, one meaning, a test that both send to the same URL | Fixed | [GenAI](walkthrough_genai.md) |
| Cached LLM calls would look free and instant in evaluations | Cache off by default in evaluation scripts; cached calls left out of cost and latency | Fixed | [GenAI](walkthrough_genai.md) |
| Frozen key facts misread correct answers | Corrected before the first measured run | Fixed | [S1b](walkthrough_innovations_s1.md) |
| The SIG checker misread several kinds of correct sentences (qualified durations, header comparators, comparator ownership, coordinated values, fraction words) | Each fixed with a regression test; the mutation set was regenerated, so older SIG results are not comparable | Fixed and disclosed | [S1b](walkthrough_innovations_s1.md) |
| SIG's wrong-row blind spot: a value from another row of the same table is caught 6 of 55 times (`results/provenance_metrics.json`) | Row binding for tables is planned as an extension in [innovations.md](innovations.md); not built | Measured, not fixed | [innovations.md](innovations.md) |
| A sanctioned name written with an invisible character screened as clear | The screened name is normalized before matching; a test runs each obfuscation through the real matcher | Fixed | [S2](walkthrough_innovations_s2.md) |
| The product tripwire read raw text only; memo validators missed obfuscated decision wording; a memo echoing "DECISION: APPROVE" passed the claim checker | Raw and normalized text; validators search normalized text; new decision patterns | Fixed | [S2](walkthrough_innovations_s2.md) |
| The resume template claimed "100% of injected prompt-injection attempts blocked" | Retired. The honest form is attack success per layer with intervals, plus "decision-level impact 0 by construction, enforced by a test"; the wording guard rejects "100%" and "blocked" | Fixed (the per-layer run itself is not done) | [S2](walkthrough_innovations_s2.md), [Phase 8](walkthrough_phase8.md) |
| The planned fake citation used a real section id, which would test misattribution | A made-up id is used; misattribution is not covered by the frozen set | Disclosed | [S2](walkthrough_innovations_s2.md) |

### 5.2 Governance and release gate

| What went wrong | What changed | Status now | Source |
|---|---|---|---|
| A partial or subset run could be compared with the full baseline and pass | Run-scope preconditions on every gate row | Fixed | [Phase 6](walkthrough_phase6.md) |
| The LLM gate rows would have failed forever once an LLM ran; a retrain could keep gating the old model; malformed YAML exited as a regression | Rows point at the right value; the selected model is read; configuration errors exit with their own code | Fixed | [Phase 6](walkthrough_phase6.md) |
| The monitor ran before the feedback section existed, so the override indicator was "not available" | One make target runs the producers in dependency order, plus a staleness check | Fixed | [Phase 6](walkthrough_phase6.md) |
| Concurrent registry writers could erase each other's change; monitoring could mark a challenger under review; a changed rule file did not show in lineage | Locks; champion only; rule files hashed | Fixed | [Phase 6](walkthrough_phase6.md) |
| The governance packs implied inventory models were monitored and quoted guidance only in part | Packs re-rendered with the corrections | Fixed | [Phase 6](walkthrough_phase6.md) |
| The release gate passes with 4 rows NOT_MEASURED (`results/governance_metrics.json`, `release_gate`) | NOT_MEASURED is listed and never counted as a pass; once a run exists a row is MISSING (which blocks) until a reviewed baseline includes it | By design | [Phase 6](walkthrough_phase6.md) |
| Most results files were written from a dirty tree | The gate lists them as warnings; the Phase 2 and Phase 4 clean-tree re-runs are pending | Disclosed, not fixed | README Limitations |

### 5.3 Founder Studio and web

| What went wrong | What changed | Status now | Source |
|---|---|---|---|
| Every Studio data host was blocked in the build environment at first | Facts marked UNVERIFIED with the page to check, instead of guesses | Partly resolved later | [S0](studio/walkthrough_s0.md) |
| A Census "invalid key" page was cached under the keyless cache key and replayed after the key was fixed | Every connector checks an answer before caching; old bad entries are deleted | Fixed | [S1](studio/walkthrough_s1.md) |
| Coverage counted "some road segment in each county" as covering every tract | Coverage is the share of tracts with a value; live-only layers count 0 | Fixed | [S1](studio/walkthrough_s1.md) |
| The OpenStreetMap API was unreachable | A pinned Overture places release with a reviewed category map | Fixed | [S1](studio/walkthrough_s1.md) |
| SiteIQ loses to its single-factor baseline for retail and is near random for car wash | Diagnosed (competition sign; nearest road is the local street); weights not tuned; a "busiest road within a frontage distance" measure is proposed, not built | Measured, not fixed | [S2](studio/walkthrough_s2.md) |
| The simulator sampled a triangle over SiteIQ's hard demand bounds, whose median sat far above the most likely demand | SiteIQ passes the percentiles of its formula and the simulator samples those | Fixed | [S3](studio/walkthrough_s3.md) |
| A year with fewer than 12 loan payments could get a DSCR probability | No DSCR or probability for such a year, with a note | Fixed | [S3](studio/walkthrough_s3.md) |
| S4 first review: an edited target was checked against a probability measured for the default target; edited ranges had no claim; the break-even month was printed as a duration | Each fixed with a test | Fixed | [S4](studio/walkthrough_s4.md) |
| S4 second review: a draft could pair one check's verdict with another check's fact and pass every gate | New grammar rule (V10) and wider checks; the bound check gates every model draft | Fixed | [S4](studio/walkthrough_s4.md) |
| Wrong-slot blind spot: a slot pointing to another claim of the same kind is flagged 5 of 60 times (`results/studio_evidence_metrics_synthetic.json`) | The human reviewer and the evidence appendix are the defense | Measured, not fixed | [S4](studio/walkthrough_s4.md) |
| The local 1.5B model's narratives all fell back: 0 of 6 first drafts and 0 of 6 retries passed; it typed "$" before most slots (`results/studio_evidence_metrics_ollama.json`) | Options listed (a larger model, a new prompt id, a counted "$" repair); none built; the run's provenance is approximate | Measured, not fixed | [S4](studio/walkthrough_s4.md) |
| Quick mode used fewer simulated paths and a re-check, so the website said "no path" where full mode found one | Same paths and seed, coarser grid | Fixed | [S5](studio/walkthrough_s5.md) |
| A change could make a rule the plan already failed worse; explanations were orders; "no path" claimed to be exhaustive; the budget counted only cache misses | Refusals and listed rules; conditional scenarios; an honest "what was tried" message; every variant counts | Fixed | [S5](studio/walkthrough_s5.md) |
| With only the two original targets, 0.749 of the shown changes would fail the lender's own NSP-4.2 check (`results/studio_path_metrics.json`, `stress_target_effect`) | A third target: the lender's revenue stress | Fixed | [S5](studio/walkthrough_s5.md) |
| `render_pdf` trusted the package's stored audit; label masks could hide a real figure; a conflicting equity figure reached the application as typed; the package id was not reproducible | Re-audit before and after printing; bounded labels; lowest figure; the id hashes ledger and narrative, and (since `408596c`) leaves out the path search's run time and cache counts, so the same plan always gets the same id | Fixed | [S6](studio/walkthrough_s6.md) |
| "Apply this change" could miss the card's numbers because each version had its own seed; a very large body blocked the event loop | One seed per plan; a body size cap | Fixed | [S7](studio/walkthrough_s7.md) |
| S8 review: a revoked share link could come back after a restart; the access log's cost grew with its size; delete-all said "done" while a plan stayed on the Hub; a quadratic regex in the upload screen; a scanned PDF passed unscreened | Fixed; verifiers then found the fixes broke some upload patterns, and those were fixed with their examples as tests | Fixed | [S8](studio/walkthrough_s8.md) |
| In both S12 runs no real-metro scenario had a site score (0 of 30 in the committed run, `data_coverage_by_metro.this_run`; likely cause, per the S12 walkthrough: the machine had no real-metro tract snapshot); two cited files (S1 coverage, S5 paths) were written from a dirty tree | Reported in the results file and the walkthrough; the README now has a row of site-scored scenarios by metro | Disclosed, not fixed | [S12](studio/walkthrough_s12.md) |
| Every Prompt 8 resume template claimed something not measured (circular top-3 accuracy, stress tests, "traced every call", a cloud deployment) | Each clause is printed only from a measured value; dropped clauses go to stderr with the reason | Fixed | [Phase 8](walkthrough_phase8.md) |
| Phase 8 / S12 code review (17 findings confirmed, 3 refuted, by the review's own tally; fixes in `git log --oneline c6407dd..c7e389c`). B0 counted the rate-shock size (`rate_shock_size_pct_points`) as an unsourced input in 20 of the 36 scenarios (the 20 more figures of the first run, `git show b44b581:results/studio_business_metrics.json`, `per_scenario`), although `docs/studio/s12_baseline.md` section 2 leaves out the inputs a one-forecast plan does not use, including the shock sizes "which only act when a shock happens" | `baseline.py` leaves out every input of the three shocks by prefix, with tests (`99b7a93`). S12 was re-measured at `6fcfad9` with a clean tree, committed in `667db85`: B0 now prints 1,296 figures instead of 1,316, and its share is 0.0255 [0.0185, 0.0325] instead of the first run's 0.0251 [0.0182, 0.032] (`git show b44b581:results/studio_business_metrics.json`). The definitions file was not edited | Fixed | [S12](studio/walkthrough_s12.md) |
| The README rule S rows showed Studio vs B0 without the caveat the baseline file requires wherever the comparison is shown (B0 is a construction; the gap follows mostly from the definitions); rule E, the founder figures, complete packages and the SiteIQ coverage were missing; a value recorded as null was reported as a missing key | Every README row comparing Studio with B0 carries the caveat; rows for rule E (pair and difference), founder figures (equal by construction), complete packages and SiteIQ site scores by metro; a null value shows the file's reason (`9af06cd`, `3eb096e`) | Fixed | [S12](studio/walkthrough_s12.md), [Phase 8](walkthrough_phase8.md) |
| Verifier advisory on that fix: the caveat covered rule S only, but 724 of Studio's 821 rule E figures are VERIFIED policy claims (sections of the SYNTHETIC program and the Studio's own targets) that B0 never prints (`results/studio_business_metrics.json`, `evidence_coverage.studio_figures_by_status_kind_and_verdict`) | The caveat covers both rules in the README rows, the S12 status row and the resume bullet: rule S credits model runs and formulas, rule E program and target sections, and B0 prints neither (`6fcfad9`, README regenerated in `c7e389c`) | Fixed | [S12](studio/walkthrough_s12.md) |
| Resume wording overclaimed: the S12 bullet quoted rule S alone, without the B0 caveat, and named SiteIQ as if every scenario had a site score; the narrative trace rate read like a measurement; the path clause said "every startup-program rule". It also misreported: a measured handoff checkpoint rate below 1.0 would have printed as "not measured", the governance clause ignored the gate's verdict and MISSING rows, and a zero-width or flagged bootstrap interval was printed as a bootstrap interval | The S12 bullet quotes rule S only next to rule E, with the caveat for both rules and the number of scenarios that had a SiteIQ score (`f48c467`, `6fcfad9`); "by construction for the template" (`829d733`); "the SYNTHETIC startup program's equity and loan rules" (`1ccd1c9`); a measured rate is quoted with its interval (`0b846fd`); the gate's verdict and MISSING count are read, and a gate that is not a clean PASS leaves the bullet out (`231b97d`); the exact or Wilson interval is used instead (`22fd31d`) | Fixed | [Phase 8](walkthrough_phase8.md), [S12](studio/walkthrough_s12.md) |
| The handoff check's label was fixed text ("full OFAC SDN + OpenSanctions lists") and only `sdn.csv` was hashed, whatever lists were loaded; a malformed `config/contributions.yaml` gave a traceback or a silently empty result | The label names the lists the matcher loaded and every loaded file's SHA-256 is recorded (`provenance.data_snapshots.screening_list_snapshots`, `976bb7c`); a malformed file exits with code 2 (`6b93885`) | Fixed | [S12](studio/walkthrough_s12.md) |

## 6. What we would do next

Only things that are not done.

1. **Real LLM measurements.** Run the frozen generation evaluation (R1 champion, R2 challenger, R5 reproducibility) and
   the migration gate; measure memo citation accuracy, the unsupported-claim rate, LLM cost and latency per application
   with the cost cap on; compare the rules extractor with the hybrid extractor (`scripts/eval_docintel.py --provider
   ollama`); measure the adverse-media memo. All of these are "not measured" today.
2. **Prompt-injection attack success by layer.** The full R4 run: 60 payloads x layers L0, L1, L2 plus benign calls, 240
   planned calls on the local model (`results/injection/planned_requests.json`). Report per-layer attack success with
   Wilson intervals, the not-found rate under attack, and the L1-vs-L2 McNemar test. Then add the memo prompt as a
   measured layer.
3. **Lending Club re-run with a Kaggle key.** Download the data again, re-run the credit model, then the things that
   need it: credit-model monitoring, champion/challenger on real loans, and outcome analysis on real loans.
4. **A deployment.** Push the first `v*` tag so the workflow deploys the Space, then measure on it: Playwright flows,
   axe, Lighthouse, the smoke checks, Hugging Face sign-in in the iframe and a new tab, and the dataset store against
   the real Hub. Nothing has run on the Space.
5. **A usability test with real people,** under an approved `docs/studio/privacy.md`, reporting n, the protocol and every
   raw time.
6. **Clean-tree re-runs.** `make eval-docintel` (Phase 2), `make simulate-requests` and `make eval-evidence` (Phase 4),
   `make studio-evidence-ollama` (S4) and the 30 real-city S4 scenarios after the review fixes.
7. **Real-metro data on the evaluation machine.** Build the snapshots there and re-run S6 and S12, so the real-metro
   packages have site scores; add a short-term rental comparables source and map motel's missing tourism and anchor
   tags, or decide a per-business-type mode.
8. **Measured fixes for the known blind spots.** Row binding for SIG's wrong-row case; a "busiest road within a
   frontage distance" measure for drive-by businesses; for the narrative, a larger local model, a new prompt id or a
   counted "$" repair, each measured as its own run.
9. **Owner decisions.** Sign the Studio addendum in CLAUDE.md, approve `docs/studio/privacy.md` and the Terms, fill in
   `config/contributions.yaml`, confirm the `deepset/prompt-injections` dataset and its committed rows, then run final
   CI, tag `v1.0` and open the pull request (Prompt 8 step 4).
10. **Things a real institution would need,** none started: independent validation, fairness monitoring with
    protected-class data, an upload route behind the built upload screen, an external security review, and the SIG
    section of the innovations walkthrough, which is not written yet.

## 7. Regulatory references, if asked

The first three were checked against primary sources on 2026-10-10 in the build plan (`docs/build_prompts.md`,
"Regulatory references"); the OWASP page was checked on 2026-10-13 ([S2](walkthrough_innovations_s2.md)). Re-check
before quoting; this area changes. Applicability of any of these depends on the institution and its risk
profile. This project is a demonstration, not a compliance program, and nothing in it claims compliance.

- **SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026**, "Revised Guidance on Model Risk Management", Federal Reserve,
  OCC and FDIC, **April 17, 2026**
  ([Federal Reserve](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm),
  [OCC](https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html),
  [FDIC](https://www.fdic.gov/news/financial-institution-letters/2026/agencies-revise-interagency-model-risk-management-guidance)).
  It supersedes SR 11-7 (2011) and SR 21-8. What to say: it is risk-based and proportionate; it says it is expected to
  be most relevant to banking organizations with over $30 billion in total assets; it excludes simple arithmetic and
  deterministic rule-based processes from the model definition (one reason the Phase 4 calculations stay
  deterministic); and its footnote 3 places generative and agentic AI out of scope, which is why the bank pack governs
  the LLM extraction and the agent in their own section. Say "aligned to the structure of SR 26-2", never "compliant"
  and never "SR 11-7-style".
- **NAIC Model Bulletin, "Use of Artificial Intelligence Systems by Insurers"**, adopted **December 4, 2023**
  ([bulletin](https://content.naic.org/sites/default/files/cmte-h-big-data-artificial-intelligence-wg-ai-model-bulletin.pdf.pdf)).
  Insurers are expected to keep a written AIS Program in proportion to the potential harm to consumers; adopted wording
  varies by state. The insurance outline uses the build plan's summary; the bulletin's text was not checked against a
  local copy.
- **New York DFS Insurance Circular Letter No. 7**, **July 11, 2024**
  ([circular](https://www.dfs.ny.gov/industry-guidance/circular-letters/cl2024-07)): AI and external data in
  underwriting and pricing, including quantitative fairness testing such as the adverse impact ratio.
- **OWASP Top 10 for LLM Applications, 2025 edition**, page dated November 17, 2024
  ([page](https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/)), used in S2 for LLM01 Prompt
  Injection, LLM05 Improper Output Handling, LLM06 Excessive Agency and LLM09 Misinformation
  (`owasp_source.ids_used` in `eval/injection_payloads_manifest.json`); LLM08 Vector and Embedding Weaknesses is left
  to a Tier 2 extension and is not exercised. A newer edition, "OWASP GenAI LLM Top 10 2026" (dated August 3, 2026),
  is listed on the same site; the mapping was not re-checked against it. Whether any item applies depends on the
  institution and its risk profile.

## 8. Cross-cutting interview questions

1. **Did you build this yourselves?**
   We wrote the specs, the build prompts and the evaluation protocol, and we reviewed every phase; Claude Code
   implemented it. What we can explain is why each part is built the way it is and what each number does and does not
   show. That is why every phase has a walkthrough and why the per-person resume lines wait for a file we fill in, not
   for git history.

2. **What one idea ties the project together?**
   The model reads and writes; deterministic code computes and decides what the policy says; a person makes the
   decision. In the founder package every printed number is a ledger claim with a status, and the founder narrative
   can only place slots that code fills. On the lender side the memo is a deterministic template today; an LLM memo
   would be checked afterwards by the claim checker, and a slot-filled lender memo (SIG) is planned, not run.

3. **How do you know every number in the README is real?**
   Every metric is written by a script to `results/*.json` with a provenance block. Two scripts render the README
   Results tables from those files, and unit tests fail if the README and the files disagree. The Status tables are
   copied by hand and are not checked by a test, and the README says so. Many files were written from a dirty tree, so
   we re-run before quoting.

4. **Everything is synthetic. What can you actually claim?**
   How the pipeline behaves on a world whose truth we know: recall, calibration, traceability, that no decision is made
   without a person. Not real-world accuracy, not savings, not production readiness. We label SYNTHETIC and SIMULATED
   in every row.

5. **Which result are you least happy with?**
   SiteIQ: it beats its single-factor baseline for 1 of 6 business types and is worse for retail. Close behind: the
   path search finds a change for 0.1026 of the SYNTHETIC plans that need one, and the number checker misses most
   wrong-row values. We report all three, with the reasons.

6. **Why didn't you use an LLM more?**
   No hosted API key was configured (and the owners decided on no extra spending, so the paid Hugging Face Inference
   provider never ran either), so real calls went only to a local model, and only as smoke runs. The design does not depend on the model: templates are the default and the model is
   a measured option. The cost is that every LLM quality metric is "not measured".

7. **What does "0 by construction" mean, and why test it anyway?**
   It means the design leaves no path for the thing to happen: the policy never reads free text, no tool can decide,
   the graph always interrupts. Tests still check it over every frozen payload, and the evaluations count decisions
   recorded without a human, because a design claim only becomes a fact once something checks it.

8. **Why is the agent's routing agreement only 0.7538?**
   Mostly because real name screening against the full lists raised false alerts on 0.1917 of clean parties, which sends
   files to a review queue the designed label did not expect (`results/agent_metrics.json`). Every required escalation
   still reached the right queue. A name-only screener cannot fix this; secondary identifiers such as date of birth
   are the usual remedy, and none is built or measured here.

9. **How do the two sides stay consistent?**
   They share code, not copies: one ledger, the lender's loan formulas, the lender's stress constants imported (never
   retyped), the program's own rounding. The founder sees the number the lender copilot will compute.

10. **Why bootstrap intervals, and why Wilson sometimes?**
    The bootstrap needs no distribution assumption and resamples the right unit (a packet, a scenario, a world), so
    values that belong together stay together. When every case passes, the bootstrap collapses to a point, so a Wilson
    or exact interval shows what the sample size can still rule out.

11. **Why is there no live demo?**
    The Space and the deploy workflow exist, but no deploy has run, so there is no URL. Every web number is from a local
    container, and the files say so. We would rather show that gap than a mock-up presented as live.

12. **What would a bank need before using any of this?**
    Real data and independent validation, outcome analysis, fairness testing with protected-class data, the LLM and the
    agent governed separately (SR 26-2 places them out of scope), a security review, and a decision on whether the
    guidance applies to that institution at all.

13. **Studio sources 0.8482 of its printed figures and the baseline 0.0255. Isn't that the headline?**
    No. The review fixes put the caveat next to every Studio vs B0 comparison in the README and the resume bullet,
    and it is the answer here too. B0 is a construction from the same SYNTHETIC inputs, not a sample of real plans,
    and the gap under both rules follows mostly from the definitions. Under rule S, Studio gets credit for model runs
    and formulas because its ledger records them, and B0 has no ledger. Under the stricter rule E it is 0.0570
    [0.0545, 0.0597] against 0.0255 [0.0185, 0.0325], but 724 of Studio's 821 rule E figures are program and target
    sections, which B0 never prints. On the one population both plans share, the founder's typed figures, both are
    0.1315 [0.0958, 0.1693], equal by construction (`results/studio_business_metrics.json`). What Studio adds by
    design is a recorded status for every printed figure (a source, the founder's own figure or a labelled
    assumption), and it surfaced 12 conflicts between a typed figure and a document, which B0 has no step to do. The
    baseline was committed before the first run, and when the review found the code printing one input the definition
    leaves out (the rate-shock size), we fixed the code and measured again.

## 9. Ten things to say in your own words

1. "A human decides. The graph always stops at the checkpoint, and we count decisions without a human: zero."
2. "In the founder package every printed number is a ledger claim with a status: a source, the founder's own figure,
   or a labelled assumption."
3. "Not measured is an answer. We write it with the reason instead of a guess."
4. "Our strongest-looking numbers are often by construction. We say which ones."
5. "Containment over detection: the detector is a tripwire, the architecture is the protection."
6. "We wrote the baseline before we measured, and the commit order shows it. When the code did not match it, we fixed
   the code, not the definition, and measured again."
7. "SiteIQ mostly does not beat a simple baseline on our synthetic cities, and we know why."
8. "The simulator's probabilities are simulated, based on stated assumptions, and its bands are too narrow when the
   model is wrong."
9. "We measured blind spots on purpose: wrong row in tables, wrong slot in narratives."
10. "We specified and reviewed; Claude Code implemented."

## 10. Where to read more

| Walkthrough | Covers | Status of its numbers |
|---|---|---|
| [walkthrough_phase0.md](walkthrough_phase0.md) | Repo setup, lockfile, CI, downloads with checksums | No metrics |
| [walkthrough_phase1.md](walkthrough_phase1.md) | Credit, insurance and fairness models, `/score` API, model cards | `results/risk_metrics.json` |
| [walkthrough_phase2.md](walkthrough_phase2.md) | Document intelligence | `results/docintel_metrics.json` (dirty tree, re-run pending) |
| [walkthrough_phase3.md](walkthrough_phase3.md) | Sanctions screening | `results/kyc_metrics.json` |
| [walkthrough_phase4.md](walkthrough_phase4.md) | Evidence-to-Action Engine | `results/evidence_metrics.json` (dirty tree, re-run pending) |
| [walkthrough_phase5.md](walkthrough_phase5.md) | Agent and human checkpoint | `results/agent_metrics.json` |
| [walkthrough_genai.md](walkthrough_genai.md) | LLM adapter, RAG, tools, MCP | `results/rag_metrics.json`, `results/mcp_smoke_*.json` |
| [walkthrough_innovations.md](walkthrough_innovations.md) | S2 Q&As and innovation S5 (evidence-request simulator) | `results/evidence_metrics.json`, `simulator` |
| [walkthrough_innovations_s1.md](walkthrough_innovations_s1.md) | S1b frozen generation eval, migration gate, SIG checker fixes | Generation: smoke only; SIG: `results/provenance_metrics.json` |
| [walkthrough_innovations_s2.md](walkthrough_innovations_s2.md) | Injection containment, payloads, scoring | Detector only; attack success not measured |
| [walkthrough_phase6.md](walkthrough_phase6.md) | Lineage, monitoring, feedback, champion/challenger, release gate, packs | `results/governance_metrics.json` |
| [walkthrough_phase8.md](walkthrough_phase8.md) | Resume lines, README headline table, final polish | Reads results files only |
| [studio/walkthrough_s0.md](studio/walkthrough_s0.md) | Studio plan aligned with the repo | No metrics |
| [studio/walkthrough_s1.md](studio/walkthrough_s1.md) | Profiles, metros, data fabric, coverage | `results/studio_metro_coverage.json`, `results/studio_metro_ranking.json` |
| [studio/walkthrough_s2.md](studio/walkthrough_s2.md) | SiteIQ | `results/studio_siteiq_metrics.json` |
| [studio/walkthrough_s3.md](studio/walkthrough_s3.md) | Monte Carlo feasibility | `results/studio_feasibility_metrics.json` |
| [studio/walkthrough_s4.md](studio/walkthrough_s4.md) | Founder ledger and narrative | `results/studio_evidence_metrics*.json` |
| [studio/walkthrough_s5.md](studio/walkthrough_s5.md) | Path to approval | `results/studio_path_metrics.json` |
| [studio/walkthrough_s6.md](studio/walkthrough_s6.md) | Package and handoff | `results/studio_handoff_metrics.json` |
| [studio/walkthrough_s7.md](studio/walkthrough_s7.md) | Web app v0.1 | `results/web_metrics.json`, `results/deployment_metrics.json` (local container) |
| [studio/walkthrough_s8.md](studio/walkthrough_s8.md) | Privacy, security, trust | Checks, no metric |
| [studio/walkthrough_s12.md](studio/walkthrough_s12.md) | End-to-end evaluation, baseline, supported cities | `results/studio_business_metrics.json` |

A 3-minute demo script is in [demo_script.md](demo_script.md). Design documents worth knowing:
[agent_design.md](agent_design.md), [rag_design.md](rag_design.md), [mcp.md](mcp.md), [security.md](security.md),
[innovations.md](innovations.md), [studio/architecture.md](studio/architecture.md),
[studio/s12_baseline.md](studio/s12_baseline.md), the [bank pack](governance/bank_model_risk.md) and the
[insurance outline](governance/insurance_ai_program.md).

## 11. Numbers you must not say

Each line is a claim this project cannot make, with what to say instead.

1. **"100% of prompt injections blocked"**, "injection-proof", or any attack success rate. Attack success is not
   measured; the only model run is a smoke run of 3 of 240 planned calls. Say: "Decision-level impact is 0 by
   construction, enforced by tests; attack success per layer is not measured yet."
2. **Calling the claim-status agreement 0.9935 "accuracy".** It is agreement with rules written by the same author,
   and it is not held out.
3. **"100% next-best-evidence accuracy"** or "top-3 accuracy 1.0". That number is labelled CIRCULAR. Quote the
   simulator instead, with its decision-error cost.
4. **Quoting field F1 0.9936 as accuracy on real documents**, or "production-ready extraction". It is on SYNTHETIC
   packets, and 0.9159 on scanned pages.
5. **Any time or cost saving**: "a package in seconds instead of days", "saves underwriters hours", "cuts evidence
   requests in practice". The 6.227 s median is machine wall clock on a shared machine and varies by run; it is not a
   user time; nobody was timed; the VOI result is simulated.
6. **"Studio plans are many times better sourced"**, or any ratio built from 0.8482 or 0.0570 and 0.0255. B0 is a
   construction from the same SYNTHETIC inputs, not a sample of real plans, and the gap under both rules follows mostly
   from the definitions (rule S credits model runs and formulas, rule E program and target sections; B0 prints
   neither). If you quote rule S, quote rule E (0.0570 against 0.0255) with it, and the caveat with both.
7. **"SiteIQ predicts good sites"** or "SiteIQ beats the baseline". It beats it for 1 of 6 business types on SYNTHETIC
   cities and is worse for 1.
8. **"A founder has an X% chance of approval."** Studio probabilities are simulated, based on stated assumptions, and
   Studio never approves or promises approval.
9. **Quoting the path-found rate 0.1026 as a share of real founders' plans.** It is a share of SYNTHETIC plans under
   editable targets and bounds.
10. **Any LLM quality, cost or latency number**: memo citation accuracy, faithfulness, unsupported-claim rate, LLM cost
    per application, LLM p95. All not measured.
11. **"Powered by Claude"**, "uses GPT", or any hosted model. Anthropic and OpenAI were never called. Hugging Face
    Inference never ran. The only real model calls were local Ollama smoke runs.
12. **"Deployed"**, "live on Hugging Face", "has users", or a URL. Nothing is deployed and there are no users.
13. **Any cloud provider, cloud model service or infrastructure-as-code claim.** No cloud service ran.
14. **"5.73 ms p95 in production"** or "load tested". It is one client on a local container.
15. **"WCAG compliant"** or "accessible". 0 serious or critical axe findings in 32 automated scans on a local container
    is not conformance.
16. **"Compliant with SR 26-2"**, "SR 11-7 compliant", "NAIC compliant", "fair-lending compliant". Say "aligned to the
    structure of SR 26-2", with the date and the applicability statement.
17. **"We monitor the credit model"** or "drift monitoring in production". The monitor watches a separate demonstration
    GLM on SIMULATED streams; credit-model monitoring is not measured.
18. **Quoting the override rate 0.1385 as how often underwriters override.** It describes a SIMULATED reviewer.
19. **Quoting the default rate 0.2180 as a real default rate of approved loans.** It comes from SYNTHETIC outcomes
    joined to SIMULATED decisions.
20. **"The retrained model was better (or worse) on real loans."** Champion/challenger ran on SYNTHETIC loans.
21. **Quoting sanctions recall 0.6889 or the false-positive rate 0.0887 as real-world rates.** Both are on SYNTHETIC
    customers; the false-positive rate is a lower bound.
22. **"The agent uses a credit score"** or the Lending Club AUC as if it applied to the SYNTHETIC commercial files. It
    does not (the domain gap); every risk call was unavailable.
23. **"The reranker improved retrieval."** Its recall gain over dense includes 0.
24. **"The monitor catches drift."** A 10% claim rise was caught in 49 of 300 streams and a mild covariate shift in 0
    of 300.
25. **"Fairness tested on our credit and insurance models."** Only a demonstration on UCI German Credit, with an
    interval from below 0.8 to above 1.0.
26. **"The narrative cannot contain a wrong number."** The template's numbers trace by construction, but a slot pointing
    to the wrong claim of the same kind is caught only 5 of 60 times.
27. **"The SIG checker catches every number error."** Wrong-row values are caught 6 of 55 times, and it was tested on
    guideline sentences, not memos.
28. **"I built X"** for any one component, or a split of the work between the owners. Say: "We wrote the specs, build
    prompts and evaluation protocol and reviewed the work; Claude Code implemented it."
29. **Real-metro accuracy for Studio**, or "every S12 package has a SiteIQ score". All five real metros run in limited
    mode, no real outcome data exists, and in the S12 run SiteIQ scored a site in 0 of the 30 real-metro scenarios (6
    of 6 SYNTHETIC-metro ones; `results/studio_business_metrics.json`, `data_coverage_by_metro.this_run`).
30. **Any number not in a `results/*.json` file**, or a rounded "about" version of one. Quote the file's value with
    its interval and its label, or say "not measured".

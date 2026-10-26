# Demo script: Fundproof in 3 minutes (Founder Studio + Lender Desk DEMO)

Written on 2026-10-26 so that Rahul Vajja and Bhavani Adula can each give the same live demo on a laptop and explain
it in their own words. This is step 1 of Prompt 8 in [build_prompts.md](build_prompts.md) (the README links it).

The demo uses only things that run today:

- the web app from `make web-dev`, or `make web-mock` if Python is not available (README, "Run the demo");
- one lender-ready package PDF, built from the command line with `make studio-package`, because the web app does
  not build packages yet;
- numbers copied from `results/*.json`, each with its file and key.

> **Everything on screen is SYNTHETIC.** The city, the founder plan, the lender files, the guideline and the startup
> program are computer-generated. Probabilities are "simulated, based on stated assumptions". Founder Studio is an
> educational planning tool: not financial, legal or lending advice, and lenders make their own decisions. The app
> runs locally. It is **not deployed**: the Hugging Face Space exists, but no deploy has run, so there is no public
> URL. The memo is a deterministic template. No language model runs anywhere in this demo.

**What runs, and what does not.**

- Runs: the FastAPI + Next.js app on this laptop, signed in with the local DEV LOGIN; the Studio wizard, site scores,
  the Monte Carlo simulation and the path search on the SYNTHETIC metro; the Lender Desk agent on SYNTHETIC structured
  applications, stopping at its human checkpoint; the package builder on the command line.
- Does not run: Hugging Face sign-in (never exercised end to end), any hosted language model (Anthropic and OpenAI
  were never called, because no key exists), any cloud service. The only real LLM calls in the project were small
  local Ollama smoke runs, listed in the README section "Language models: what has actually run". None of them is
  part of this demo.

## 1. Files, screens and commands the demo uses

Every route below is a page in `frontend/app/` (`/` is `frontend/app/page.tsx`). Every label in quotes was checked
in `frontend/components/` or `frontend/lib/`.

| Item | What it is used for in the demo | Needs |
|---|---|---|
| `make setup`, `make web-install` | Python environment from `uv.lock`; exact frontend packages (`npm ci`) | Python 3.11 + uv; Node 22 + npm |
| `make web-dev` | The live app: API with DEV LOGIN on 127.0.0.1:8000, Next dev server on http://localhost:3000 (Makefile) | the two lines above |
| `make web-build`, `make web-mock` | Offline fallback: the built pages plus a mock API that replays answers recorded from the real app, on http://127.0.0.1:4174 | Node only |
| `make studio-package PACKAGE_METRO=synthetic_metro PACKAGE_TYPE=laundromat` | The lender-ready package PDF for one golden SYNTHETIC scenario, written to `data/studio_packages/synthetic_metro_laundromat.pdf` (gitignored). Exits with an error if any number in the PDF text is untraced | Python only; offline |
| `make agent-demo` | Optional, command line: the agent on `eval/applications/APP-C-027.json` stops at the human checkpoint, then a scripted Refer by a senior underwriter | `make index eval-kyc` first (README) |
| `make run-evidence` | Optional, command line: the document path (text layer, OCR, extraction, contradictions) and the Evidence-to-Action Engine on packet `PKT-C-024` (the Makefile default `PACKET`); writes a decision record and its Markdown and HTML views under `data/processed/evidence/` | `make packets` once |
| `make studio-business-metrics` | Writes `results/studio_business_metrics.json`: the first closing number (rule S, with rule E beside it) and the package time. The other two closing numbers come from `make studio-feasibility` and `make eval-agent`. Do not run any of them during the demo: the committed S12 run, at commit `6fcfad9` (`provenance.git_sha`, `git_dirty` false), took 317.7 s (`results/studio_business_metrics.json`, `configuration.runtime_s`; machine wall clock, it varies by run). It names the screening lists its handoff check loaded and the SHA-256 of each (`configuration.handoff_consistency_check`, `provenance.data_snapshots.screening_list_snapshots`) | `make data` for the handoff check, or `ARGS=--skip-handoff` |
| `make resume-lines` | Preparation: prints the resume bullets the results files support; a metric that was not measured or cannot be claimed is left out and listed with its reason under "Left out" | committed `results/` |
| `/` | Landing page: opening line | - |
| `/signin/` | DEV LOGIN form: "Username", "Role", "Sign in (dev)" | `make web-dev` (sets `UWC_DEV_AUTH=1`) |
| `/studio/new/` | The wizard. Steps: City, Business type, Your idea, Money, Sites, Review. Buttons "Continue", "Create plan" | signed in |
| `/studio/` | "Your plans", "Open plan", and the "Coming in a later version" cards, including "Lender-ready package" | signed in |
| `/studio/project/?id=` | Results: "The numbers of your plan", "Step 1 · SiteIQ", "Step 2 · Simulation", "Step 3 · Path to approval" | a plan |
| `/lender/` | "Demo queue": "Line" filter, "Search by applicant or file id" | signed in |
| `/lender/file/?id=APP-C-008` | One SYNTHETIC file: findings with citation chips, "Memo", "Calculations", "Sanctions screening", "Tools the copilot used", "Your decision" | signed in |
| `docs/web/screenshots/e2e/` | Fallback screenshots (section 6) | - |
| `docs/web/screenshots/mock/` | More fallback screenshots, taken on the mock API (for example `studio-path-edited-1280-light.png`, `lender-drawer-1280-light.png`) | - |
| `results/studio_business_metrics.json`, `results/studio_feasibility_metrics.json`, `results/agent_metrics.json` | The three closing numbers (section 4) | - |
| `results/studio_path_metrics.json`, `results/studio_handoff_metrics.json`, `results/web_metrics.json`, `results/studio_siteiq_metrics.json` | Numbers said during the demo, and the setup inputs | - |

## 2. Setup checklist

### 2.1 Once, the day before (needs the network)

```bash
cp .env.example .env              # the demo needs no API key
make setup                        # Python environment from uv.lock
make web-install                  # frontend dependencies (npm ci)

# Lender Desk: the guideline search tool needs the index (README, "Run the demo")
make data-regulatory index

# Lender Desk: sanctions screening reads the downloaded lists (both by default; UWC_SCREENING_LISTS unset).
# If a configured list is missing, the screening rows show "error". Either the two lists only:
uv run python scripts/download_data.py --datasets ofac_sdn opensanctions
# or every public dataset (Lending Club needs a Kaggle key):
# make data

# Offline fallback: build the static pages for the mock API
make web-build

# The lender package PDF for the Studio part (offline; a golden SYNTHETIC scenario)
make studio-package PACKAGE_METRO=synthetic_metro PACKAGE_TYPE=laundromat
```

Only if you also want the command-line demos: `make data index kyc-data eval-kyc` before `make agent-demo`, and
`make packets` before `make run-evidence`.

### 2.2 Before the audience arrives

1. Start `make web-dev`. Wait until http://localhost:3000 loads.
2. Sign in at `/signin/` with DEV LOGIN. "Username": any made-up name, for example `demo-presenter`. "Role":
   **Lender reviewer**. Click "Sign in (dev)". Why this role: every signed-in user is also a founder, so the same
   session can make a plan, and sign-in lands on the Lender Desk. On the Lender Desk a lender reviewer acts as an
   underwriter. On APP-C-008 the checkpoint locks Approve and Decline (the file has an open blocking referral, and
   only a senior underwriter may decline), and the page shows the reasons.
3. Make the plan before the demo: "Founder Studio", then "Start a plan". Use the inputs that `scripts/web_metrics.py`
   typed in its measured run (`results/web_metrics.json`, `runs.local-container.demo_inputs`, labelled there as
   SYNTHETIC test inputs and design choices, not data):
   - City: Synthetic Metro (SYNTHETIC). Business type: Laundromat.
   - Your idea: a made-up sentence (the page asks for a made-up plan).
   - Money: "Build-out and equipment budget" 400000, "Your own cash (equity)" 150000, "Loan amount" 350000, "Loan
     term" 120 months, "Interest rate per year" 9.
   - Sites: "Suggest areas for a laundromat", then "Use this area" on the first suggestion; and/or "Or pick a census
     tract from the list", then "Add as a site".
   - Review, then "Create plan". Keep the results page open in a tab.
4. Read your path-to-approval answer now. In the measured run the search answered `no_path_within_bounds` with the
   default targets and `paths_found` after the three targets were edited to 50, 30 and 15 percent
   (`results/web_metrics.json`: `runs.local-container.e2e.flows`, flow `founder_synthetic_plan`, `facts`; and
   `runs.local-container.demo_inputs.target_edits_percent`).
   Your plan gets its own simulation seed (it comes from the plan id), so your screen can differ. Pick the spoken
   line in section 3 that matches what you see.
5. Warm up the Lender Desk: open `/lender/file/?id=APP-C-008` once and wait for "Your decision". The first file after
   a start loads the screening lists and the retriever. In the measured local-container run (OFAC lists only) the
   first file took 12953.1 ms (`results/web_metrics.json`, flow `lender_demo_decision`, step `open_first_file`,
   `file_answer_ms`). Loading the full lists took 74.69 s in the agent evaluation (`results/agent_metrics.json`,
   `configuration.warm_up_s.screening_load_s`).
6. Do **not** record a decision on APP-C-008 while rehearsing with the demo username. The server keeps one DEMO
   decision per file per user: once one exists, the page shows "Your decision record (DEMO)" instead of the buttons,
   and the API answers 409 to a second one. Rehearse with a different username.
7. Open the package PDF in a viewer tab, and the folder `docs/web/screenshots/e2e/` in an image viewer (hidden, for
   section 6).
8. Tab order: `/`, `/studio/new/`, the plan results, the PDF, `/lender/`.
9. Do not click "Delete plan", "Delete all my data" or "Create share link" during the demo. Deleting removes the plan
   you prepared. The S8 pages (share links, "Delete all my data") were not part of the Playwright or axe runs (README,
   S8 row).

### 2.3 What needs data or an index

| Part of the demo | Works with nothing downloaded? | What it needs |
|---|---|---|
| Founder Studio in the SYNTHETIC metro | Yes (README: works offline) | - |
| Founder Studio in a real metro | Limited mode: sites without data are not scored, and the page says when the simulation and the path search do not run | snapshots from `make studio-data METRO=all`; all five real metros run in limited mode even with them (README, S1 row) |
| Lender Desk findings, calculations, template memo, the checkpoint | Yes: deterministic policy code and the template memo. The citation chips open section text read from the SYNTHETIC guideline files, not from the index | - |
| The guideline search tool ("Tools the copilot used") | Without the index the tool reports "unavailable" instead of failing | `make data-regulatory index` |
| Sanctions screening | No: without a configured list file the screening rows show "error" ("sanctions screening failed on the server; the details are in the server log") | the OFAC SDN and OpenSanctions downloads (2.1), or `UWC_SCREENING_LISTS=ofac` in `.env` with OFAC only |
| Risk model on a lender file | Always "unavailable" in this demo (`UWC_RISK_API_URL` is not set) | not applied to these SYNTHETIC commercial files ([domain gap](risk_service.md#the-domain-gap-why-the-agent-still-records-no-model-score)) |
| Lender package PDF | Yes | `make studio-package` (Python) |
| `make web-mock` | Yes | Node and `make web-build` |
| `make agent-demo` | No | `make index eval-kyc` |
| `make run-evidence` | No | `make packets` |

### 2.4 Offline fallback: `make web-mock`

`make web-install web-build web-mock` serves the built pages on http://127.0.0.1:4174 with a mock API. The mock
replays answers recorded from the real app on SYNTHETIC inputs (`frontend/mock-api/fixtures/`, written by
`make web-mock-fixtures`); no number in it was typed by hand. It is **not** the real API (see the top of
`frontend/mock-api/server.mjs` and [frontend.md](web/frontend.md), section 8):

- Sign in with DEV LOGIN as before. Use the plan already in "Your plans" ("Demo laundromat (SYNTHETIC)"). Every plan
  gets that recorded plan's results.
- Only the target edits recorded in `frontend/mock-api/fixtures/path_demo_targets.json` are answered; any other edit
  gets a 501. Type one of those sets if you show "Edit the targets".
- Only one decision can be recorded: "Request more information" on APP-S-001. On APP-C-008, show the locked buttons and
  their reasons, then open APP-S-001 to record the decision.
- The recorded screening used the OFAC lists only.
- Say it out loud: "This is a mock that replays answers recorded from the real app."

## 3. The script

### 3.1 Timeline

| Time | Screen (route) | What you do |
|---|---|---|
| 0:00-0:20 | Landing `/` | Nothing. Say the problem. |
| 0:20-0:35 | `/studio/new/`, then "Your plans", then "Open plan" | Show the City step and its data-mode badges. Open the prepared plan. |
| 0:35-0:50 | `/studio/project/?id=...`, "Step 1 · SiteIQ" | Point at the site cards, the coverage badge, "Top reasons". |
| 0:50-1:05 | "Step 2 · Simulation" | Point at the three gauges and the simulation label. |
| 1:05-1:25 | "Step 3 · Path to approval" | Read the status line. Optionally "Edit the targets". |
| 1:25-1:40 | PDF tab | Cover, contents, executive summary, evidence appendix. |
| 1:40-1:50 | `/lender/` | Banners. Search APP-C-008. Open it. |
| 1:50-2:05 | `/lender/file/?id=APP-C-008` | Policy suggestion, instruction screen, findings, open one citation chip. |
| 2:05-2:15 | Same page | "Sanctions screening", then "Memo" and "Calculations". |
| 2:15-2:30 | "Your decision" | Locked buttons with reasons. "Refer", a rationale, "Record: Refer". |
| 2:30-3:00 | Stay on the record, or switch to the README "Results" | Three measured numbers and what they do not show. |

Lines in [brackets] are optional. Drop them if you are behind.

### 3.2 0:00-0:20 The problem (landing page `/`)

> "A small-business loan has two sides. The founder plans without seeing the lender's stress test. The lender has to
> trace every figure back to its source. Fundproof shows both sides on SYNTHETIC data, running on this laptop. It is a
> portfolio project, not a product, and a person makes every decision."

### 3.3 0:20-0:35 Founder Studio: a plan in the SYNTHETIC metro

**Do.** Open `/studio/new/`. The City step shows each metro with its mode badge: "SYNTHETIC demo city" or "Limited
data". Click "Your plans" (bottom left), then "Open plan" on the plan you made.

> "A founder starts with six short steps: city, business type, idea, money, sites, review. Each city shows its data
> mode. The five real metros run in limited mode here, so I use the SYNTHETIC demo city, which is generated and
> describes no real place. I made this laundromat plan before the demo with the same steps."

If someone asks about the "DEV" mark in the header: it is the local dev login. Hugging Face sign-in has not run end to
end.

### 3.4 0:35-0:50 Results page: site comparison

**Do.** At the top, point at the disclaimer box and the SYNTHETIC banner. In "The numbers of your plan", point at the
chips under the figures (user stated, assumption, calculated). Scroll to "Step 1 · SiteIQ", "How your sites
compare". Point at a site card: the score, the data-coverage badge, "Top reasons" and the "Watch:" line. [Open "How
it was measured, and the source" under one factor.]

> "Every figure says who wrote it: the founder, an assumption, or a calculation. Step 1 scores each site against the
> rest of this city and shows how much of the score had data. Missing factors stay unknown; nothing is filled in.
> [On SYNTHETIC test cities, this score was not shown to rank laundromat sites better than one factor alone, so I do
> not call it validated.]"

Source of the bracketed line: `results/studio_siteiq_metrics.json`, `by_business_type.laundromat.siteiq_minus_baseline_spearman`
(the interval includes 0).

### 3.5 0:50-1:05 Simulated feasibility

**Do.** Scroll to "Step 2 · Simulation", "Feasibility at site N". Point at the three gauges: "Loan payments covered
with room to spare in year 2", "Still covered if revenue is ... lower (lender stress ...)", "Cash runs out (below $0)
within 24 months". Point at the label "simulated, based on stated assumptions". [Open "Show the numbers as a table" on
"Cash on hand: the runway".]

> "Step 2 runs a thousand simulated futures. Each gauge counts in how many of them something happens: year-two debt
> coverage holds, coverage still holds under the lender's revenue stress, or cash runs out. Every block says
> 'simulated, based on stated assumptions'. These are not approval odds, and they are not a forecast of a real
> business."

The page prints its own count of simulated futures. The golden runs used `n_paths` 1000 and `months` 36
(`results/studio_business_metrics.json`, `configuration`).

### 3.6 1:05-1:25 Path to approval

**Do.** Scroll to "Step 3 · Path to approval". Read the status line in the box. Point at "The three targets (planning
targets you can edit, not lender rules)".

If the box says "The search found no path within your bounds.":

> "Step 3 looks for the smallest change to the founder's own choices, like cash, loan size or build-out, that reaches
> three planning targets. Here it found none, and it says what it tried. That is the usual answer: across 536
> SYNTHETIC plans that needed a change, it found a path for 0.1026 of them, interval 0.0784 to 0.1306. [The targets
> are planning targets, not lender rules. If I loosen them, it looks again.]"

[If you have time: "Edit the targets", type the edited targets from section 2.2 step 4, "Search again with these
targets", then show the change-set card.]

If the box says "Found N ways to reach the simulated targets." (the page prints the count):

> "Step 3 found a change to the founder's own choices that reaches the planning targets. The card shows every figure
> that changes and what a lender would need to see, like a written quote. 'Apply this change' saves a new plan
> version, marked as a suggested change. Reaching the targets does not mean a lender will approve the loan."

If the box says "Your plan already reaches the three simulated targets.": say that, and add "Reaching the targets
does not mean a lender will approve the loan."

Source of the rate: `results/studio_path_metrics.json`, `overall_quick.path_found_rate_among_plans_needing_a_change`
(quick mode, the mode the website runs; `n` 536).

### 3.7 1:25-1:40 The lender package (PDF tab)

**Do.** Show the cover (the SYNTHETIC EXAMPLE note and the disclaimer), the contents (the SBA business-plan section
each package section covers, and the SBA sections the package does not cover; the risks, path-history and
evidence-appendix sections cover none), the executive summary (its paragraphs cite evidence-appendix rows as
bracketed numbers), and one row in "11. Evidence appendix". [On `/studio/`, point at the "Lender-ready package" card
under "Coming in a later version".]

> "The web app does not build the lender package yet. This PDF comes from the same code on the command line, for a
> golden SYNTHETIC laundromat, not the plan you just saw. Every printed value links to its row in the evidence
> appendix. Across the 36 golden packages, a script read back 30,681 printed numbers and found 0 it could not trace."

Source: `results/studio_handoff_metrics.json`, `golden_founder_scenarios.package_numbers_read_back` and
`golden_founder_scenarios.untraced_numbers_pdf_text` (also 0 in `untraced_numbers_package_text`).

### 3.8 1:40-1:50 Lender Desk DEMO: the queue

**Do.** Open `/lender/`. Point at "The copilot never decides. A person does." and the "SYNTHETIC DEMO." banner. Type
`APP-C-008` into "Search by applicant or file id" and open the file.

> "Now the lender's side. These are SYNTHETIC structured applications in a demo queue. My decisions go to my own
> demo sandbox, not to any lender."

### 3.9 1:50-2:05 A SYNTHETIC file: findings and citations

**Do.** Point at the line "The policy suggests: ... you decide." Point at the box "Instruction-like text found in the
application". In "Findings", click a guideline-section chip (for example `NBC-4.2`) to open the citation drawer, then
close it.

> "The reference policy suggests; I decide. Each finding cites a section of a SYNTHETIC guideline from a fictional
> lender; this chip opens the section text. A sentence in this file's notes says something was already approved. The
> screen flagged it as instruction-like text and treated it as data, not as an instruction. [In our evaluation that
> flag counts as a false positive. A person still looks at it.]"

Source of the bracketed line: `results/agent_metrics.json`, `injection_detector.false_positives` (APP-C-008, field
`notes`, pattern `approval_claim`).

### 3.10 2:05-2:15 Sanctions screening and the template memo

**Do.** In "Sanctions screening", point at the lists searched and their download dates. In "Memo", point at the
"template memo" and "Claim check passed" badges and the note under them. Point at the line under "Calculations":
"Computed by the policy code from the application, never by a language model."

> "Screening shows the lists it searched and the date each was downloaded. It only finds possible matches; a person
> clears or confirms them. The memo is a template: no language model wrote it, and it says so. The calculations come
> from policy code."

If the screening panel shows a possible match: with the full lists (OFAC + OpenSanctions), the agent evaluation also
raised one on this file, on an owner the SYNTHETIC ground truth marks as clean (`results/agent_per_application.jsonl`,
APP-C-008: `potential_match_parties` `["O2"]`, `alert_sources` OpenSanctions, `planted_parties` empty). Say: "That is
a false alert, and it is why a person reviews every possible match." With the OFAC lists only (as in the mock),
screening says clear.

### 3.11 2:15-2:30 The human checkpoint and a DEMO decision

**Do.** In "Your decision", point at the locked "Approve" and "Decline with a reason" buttons and the "Not available
for you here:" reasons under them. Click "Refer". Type a rationale (the hint gives the minimum length), for example
"SYNTHETIC demo: open referral, send to the queue that must clear it." Click "Record: Refer". The box "Your decision
record (DEMO)" appears. [Open "Read the record".]

> "This panel is the human checkpoint. Before I typed anything, the server asked the checkpoint which actions my role
> may take. Approve and decline are locked, with the reasons. I refer, with a written reason. That is a DEMO record
> with my role and a record id. The copilot decided nothing."

### 3.12 2:30-3:00 Close: three measured numbers

> "Three numbers from the results files, all on SYNTHETIC data. Printed figures with a checkable source: 0.8482,
> interval 0.8388 to 0.8587, against 0.0255 for a baseline plan we built from the same inputs. That baseline is a
> construction, not real plans, and most of the gap comes from how a source is defined; under the stricter rule it is
> 0.057 against 0.0255, and that gap also comes mostly from the definitions. The simulator's Brier score, lower is
> better: 0.1405 against 0.4796 for a single-point forecast, on SYNTHETIC worlds only. And across 65 SYNTHETIC lender
> files, no decision was recorded without a person. Nothing here was tested with real users or real loans."

Section 4 has the source of each number and what it does not show. Know it before you say the line.

## 4. The closing numbers: sources and what they do not show

All intervals in the table are 95% percentile bootstrap intervals, 1,000 resamples, seed 42, as the results files
record them. Everything here is SYNTHETIC; Founder Studio numbers are simulated, based on stated assumptions.
Founder Studio is an educational planning tool: not financial, legal or lending advice, and lenders make their own
decisions.

| Number said | Value [95% interval] | File and key | What it does not show |
|---|---|---|---|
| Printed figures with a checkable source (rule S), Studio vs baseline B0; rule E beside it | Rule S: Studio 0.8482 [0.8388, 0.8587] (12222 of 14410 figures, 36 scenarios); B0 0.0255 [0.0185, 0.0325] (33 of 1296 figures); difference, paired over scenarios, 0.8227 [0.8113, 0.8347]. Rule E: Studio 0.057 [0.0545, 0.0597] (821 of 14410); B0 0.0255 [0.0185, 0.0325] (33 of 1296); difference 0.0315 [0.0254, 0.0377] | `results/studio_business_metrics.json`, `headline.evidence_coverage_rule_s` and `headline.evidence_coverage_rule_e` | B0 is a construction from the same SYNTHETIC inputs and documents, not a sample of real plans, and the gap under both rules follows mostly from the definitions ([s12_baseline.md](studio/s12_baseline.md), section 2, M2 and M3). Rule S credits model estimates and calculations because the ledger records their run or formula; B0 prints no such record. Rule E removes that credit but has its own: program and target sections. 724 of Studio's 821 rule E figures are VERIFIED policy claims, sections of the SYNTHETIC startup program and the Studio's own targets (`evidence_coverage.studio_figures_by_status_kind_and_verdict`, `VERIFIED/policy/sourced`), and B0 prints none. The one comparison on the same population, the founder's typed figures backed by a document, is 0.1315 [0.0958, 0.1693] (33 of 251) for both, equal by construction (`headline.founder_figures_backed_by_a_document`). B0 is "a plan made without SiteIQ", yet SiteIQ scored a site in only the 6 SYNTHETIC-metro scenarios and in 0 of the 30 real-metro ones in this run (`data_coverage_by_metro.this_run`, `site_scored`; it depends on which metro snapshots exist on the machine that ran the script). No usability test with real people was run (`headline.usability_test`: "not measured"). |
| Brier score for P(year-2 DSCR >= 1.25), simulator vs a single-point forecast | 0.1405 [0.1321, 0.1493] vs 0.4796 [0.4596, 0.4992] (2,400 well-specified SYNTHETIC worlds) | `results/studio_feasibility_metrics.json`, `headline[0]` (`condition` `well_specified`): `brier_dscr_year2_simulator`, `brier_dscr_year2_single_forecast` | Nothing about real businesses: the worlds are SYNTHETIC, with known ground truth. The in-sample base rate, a forecast that ignores the plan, scores 0.1567 [0.1473, 0.1664] (`brier_dscr_year2_base_rate_in_sample`), close to the simulator. Misspecified worlds: 0.1433 [0.1351, 0.1523] vs 0.4938 [0.4729, 0.5154] (`headline[1]`). |
| Decisions recorded without a person (Lender Desk agent) | 0 of 65 SYNTHETIC applications; all 60 required escalations escalated (value 1.0, bootstrap interval [1.0, 1.0], n 60) | `results/agent_metrics.json`, `deterministic_path.decisions_recorded_without_a_human`, `deterministic_path.cases`, `deterministic_path.escalation.all_required_escalations` | A bootstrap interval collapses to one point when every case passes, so it says little about the true rate. End-to-end routing agreed with the designed labels on only 0.7538 [0.6462, 0.8462] (n 65, `end_to_end_routing_agreement_with_designed_labels`). The labels were designed by the same project. LLM memo quality, citation accuracy and LLM cost are "not measured" (`llm_dependent`). |

Other numbers you may be asked for (all in the README Results section, generated from the results files):

- Time from inputs to an audited PDF package: median 6.227 s [6.106, 6.456] over 36 scenarios
  (`results/studio_business_metrics.json`, `headline.time_to_complete_package_median_s`). It is a machine wall-clock
  time on a shared machine, measured at commit `6fcfad9`, and it varies by run; it is not a user's time: nobody's time
  was observed (`headline.time_label`).
- Complete packages (an audited PDF, no untraced number in the package or PDF text, every page labelled): 36 of 36,
  Wilson 95% [0.9036, 1.0] (`results/studio_business_metrics.json`, `time_to_complete_package.complete_packages`).
- Golden packages that reached the agent's human checkpoint: 36 of 36, Wilson 95% [0.9036, 1.0]
  (`results/studio_handoff_metrics.json`, `golden_founder_scenarios.reached_human_checkpoint`).
- Web app on the LOCAL container: 5 of 5 Playwright flows passed; axe found 0 serious or critical issues in 32 scans
  (`results/web_metrics.json`, `runs.local-container.e2e.all_flows_passed` and `runs.local-container.axe.totals`).
  That is not a statement of WCAG conformance.

## 5. What not to say

| Do not say | Say instead | Why |
|---|---|---|
| "It's live, try it at ..." / "It's on Hugging Face." | "It runs locally. The Space is set up, but no deploy has run, so there is no public URL." | README, "Web app and deployment": Not deployed. |
| "Production-ready", "lenders use it", "our customers". | "A demonstrator on SYNTHETIC data, with no production users." | README synthetic data notice. |
| "The AI wrote this memo" / "an LLM reviewed the file." | "A deterministic template wrote the memo. No language model ran in this demo." | `results/agent_metrics.json`, `llm_dependent`; README, "Language models: what has actually run". |
| "There's an X% chance this loan is approved." (any gauge read as approval odds) | "In this many of the simulated futures ... simulated, based on stated assumptions." | Rule E of the Studio addendum in CLAUDE.md (not the evidence rule E of section 4). The gauges are simulation counts, not approval odds. |
| "You should take this plan" / "go to this lender." | "Educational planning tool. Not financial, legal or lending advice. Lenders make their own decisions." | Rule C of the Studio addendum in CLAUDE.md. |
| "The path to approval gets you approved." | "It reaches planning targets the founder can edit. A lender runs its own rules." | The page says so in "Step 3". |
| "The copilot approved / declined / referred the file." | "A person decided at the checkpoint. This is a DEMO record." | Rule 8 in CLAUDE.md. |
| "Screening cleared this applicant." | "It found no possible match in the lists it searched. Only a person clears or confirms a match." | The screening note on the page. |
| "The Lender Desk reads the bank statements and cites the pages." | "The web demo uses SYNTHETIC structured applications. The document path with page citations runs on the command line (`make run-evidence`)." | README: the packet path is not wired into the web app. |
| "Our credit model scored this file." | "The risk model shows 'unavailable' here. It is not applied to these SYNTHETIC commercial files." | The domain gap in [risk_service.md](risk_service.md). |
| "SiteIQ finds the best site." | "It ranks sites on public-data factors and shows its data coverage. On SYNTHETIC cities it beat a one-factor baseline for 1 of 6 business types." | README S2 row; `results/studio_siteiq_metrics.json`, `by_business_type`. |
| "This is real Dallas (or any city) data." about the demo city | "The SYNTHETIC demo city is generated and describes no real place." | Rule D of the Studio addendum in CLAUDE.md; README synthetic notice. |
| "Studio plans are far better sourced than real founders' plans." | "B0 is a plan we built from the same SYNTHETIC inputs, not a sample of real plans. Under both rules, S and E, most of the gap follows from how a source is defined." | `results/studio_business_metrics.json`: `evidence_coverage.reading` (rule S) and `evidence_coverage.studio_figures_by_status_kind_and_verdict` (rule E: 724 of Studio's 821 rule E figures are `VERIFIED/policy/sourced`, program and target sections B0 never prints); README, S12 row; [s12_baseline.md](studio/s12_baseline.md), section 2. |
| "It saves underwriters hours" / "a founder gets a package in seconds." | "Time savings were not measured. The 6.227 s median is a script's machine time on one run, not a person's." | `results/studio_business_metrics.json`, `headline.time_label`. |
| "It complies with SR 26-2 / fair-lending rules / WCAG." | "It is a demonstration, not a compliance program." See section 9. | Rule 10 in CLAUDE.md. |
| "We ran it in the cloud." | "Everything in this demo runs locally. No cloud deployment and no hosted language model ran." | README, "Web app and deployment" and "Language models: what has actually run". |
| "We tested it with founders." | "No usability test with real people has been run." | `results/studio_business_metrics.json`, `headline.usability_test`. |
| Any split like "Rahul built X, Bhavani built Y." | "Rahul Vajja and Bhavani Adula wrote the specs, build prompts and evaluation protocol and review the work; Claude Code is the implementer." | Git history cannot say who built what; `config/contributions.yaml` is empty until the owners fill it. |

## 6. If something breaks

| What happens | What you do | What you say |
|---|---|---|
| `make web-dev` does not start, or a port is taken | Switch to the prebuilt mock: `make web-mock`, http://127.0.0.1:4174 (section 2.4) | "This is a mock that replays answers recorded from the real app." |
| A results block says "The server is busy or this took too long" ("Nothing was lost. Try again in a moment.") | Click "Try again" once: the job keeps running and its result is cached. If it fails again, show `docs/web/screenshots/e2e/studio-results-1280-light.png` | "The simulation timed out. Here is a screenshot from our measured run." |
| The first lender file takes a long time | Keep talking over the queue page. If it is still loading at 2:00, show `docs/web/screenshots/e2e/lender-decision-1280-light.png` | "The first file loads the screening lists and the retriever." |
| "Guideline search: unavailable", or screening rows that say "error" | Carry on | "The guideline index (or a sanctions list) is not on this machine, and the page says so instead of guessing. A person would have to screen these names." |
| "The checkpoint refused this decision" | Pick an action that is not locked | "That is the checkpoint doing its job. Nothing was recorded." |
| "Your decision" already shows "Your decision record (DEMO)" on APP-C-008 (you recorded one with this username) | Show that record ("Read the record") instead of recording a new one. Or sign out, sign in with DEV LOGIN under a new username (role Lender reviewer) and open APP-C-008 again: decisions are kept per user | "I already recorded one on that file; the server keeps one DEMO decision per file per user." |
| The package PDF is missing | Skip the PDF and say the 3.7 line without it | "The web app does not build packages yet; the command line does." |
| Projector or laptop trouble | Show the screenshots in `docs/web/screenshots/e2e/` (below) | "These screenshots were taken by our test script, not live." |

**The fallback screenshots** (`docs/web/screenshots/e2e/`). `scripts/web_metrics.py` captured them on 2026-10-14
from a LOCAL container (commit `271ca4d`), signed in with the dev login, not the Space. The script typed the plan and
the decision. Everything shown is SYNTHETIC.

- `studio-results-1280-light.png`: the results page of a SYNTHETIC laundromat plan in the SYNTHETIC metro: site
  comparison, simulated feasibility, and the path search with "no path within your bounds" and the closest result.
- `lender-decision-1280-light.png`: file APP-C-001 after the script recorded a DEMO "Approve" as an underwriter, with
  the template memo, calculations, sanctions screening (OFAC lists), tools used and rules not evaluated.
- `error-unknown-plan-1280-light.png`: an unknown plan id shows "Not found".

The mock screenshots in `docs/web/screenshots/mock/` cover what the e2e set does not: the wizard steps, the path
section after editing the targets (`studio-path-edited-1280-light.png`), the citation drawer
(`lender-drawer-1280-light.png`) and a recorded decision (`lender-decision-recorded-1280-light.png`). Say they come
from the mock.

**Command-line fallback for the Lender Desk part.** `make agent-demo` prints the agent's review of APP-C-027, stops at
the human checkpoint, then resumes with a scripted Refer by a senior underwriter. Every printed section is stamped
"FAKE LLM — test mode". It needs `make index eval-kyc` (section 2.1).

### If you have two more minutes

- `make run-evidence` (after `make packets` once): the Evidence-to-Action Engine on SYNTHETIC packet PKT-C-024. It
  reads the PDFs (text layer, OCR where needed), runs the reference policy, the stress scenarios and the
  next-best-evidence ranking, and appends a decision record. It prints ids, codes and numbers only; the readable record
  is in `data/processed/evidence/records/`. Say: "This is the document path. It is not wired into the web app yet."
- `make resume-lines`: the bullets the results files support. A metric that was not measured or cannot be claimed is
  left out, and the script lists it with its reason under "Left out".

## 7. Design choices

- **The plan is made before the demo.** The six wizard steps take longer than the time budget. Showing the City step
  for a few seconds keeps the honest data-mode badges in view, and the prepared plan opens straight on its results.
- **The SYNTHETIC metro, not a real city.** It works offline and runs the whole flow. On this laptop the real metros
  run in limited mode, where the simulation and the path search may not run; the page says so.
- **The Lender reviewer role.** It acts as an underwriter, and sign-in lands on the Lender Desk. On APP-C-008 the
  checkpoint locks Approve and Decline for this role (an open blocking referral, and only a senior underwriter may
  decline), so the audience sees the server's reasons instead of a claim that "a person decides".
- **File APP-C-008.** One file shows findings with guideline citations, a blocking referral, the instruction screen and
  locked actions. It is also one of the three files recorded for the mock, so the fallback shows the same file. Its
  instruction flag is a known false positive in our own evaluation, which is a good honest example.
- **The package PDF from the command line.** The web app does not build packages yet. Showing the real artifact from
  the package builder, and saying where it came from, is better than implying the web app made it.
- **Three closing numbers, one per side plus the human-decision guarantee.** Each comes with the sentence that limits
  it. A number whose caveat cannot be said in one sentence is left for questions.
- **Short spoken lines.** Each segment is a few sentences, with optional parts in brackets, so the demo fits in three
  minutes even when a page loads slowly.

## 8. Limits of this demo

- The app runs locally with the dev login. Hugging Face sign-in, the Space and anything on the Space are not
  measured.
- The memo is a deterministic template. LLM memo quality, citation accuracy, LLM cost and LLM latency are not measured.
- The web Lender Desk runs the agent on SYNTHETIC structured applications only. Documents and page citations appear
  only in the command-line path.
- The web app does not build a package and does not call the handoff. The PDF shown is a golden SYNTHETIC scenario,
  not the plan made in the browser.
- The mock replays recorded answers: every plan shows the same recorded results, and only recorded edits and one
  recorded decision work.
- The e2e screenshots are from commit `271ca4d` (2026-10-14). Later commits added the "use a made-up plan" notice and
  the S8 pages, so the live screens can differ from them in small ways.
- Not measured: a usability test with real people, a load test, compute cost, and any real-world accuracy of a Studio
  probability.

## 9. Regulatory references (only if asked)

Checked against primary sources on 2026-10-10 (see "Regulatory references" in [build_prompts.md](build_prompts.md);
re-check before quoting). This project is a demonstration, not a compliance program. Whether any of this applies
depends on the institution and its risk profile.

- **SR 26-2 / OCC Bulletin 2026-13 / FDIC FIL-15-2026**, "Revised Guidance on Model Risk Management", issued jointly
  by the Federal Reserve, OCC and FDIC on April 17, 2026. It replaces SR 11-7. It is risk-based guidance, and the
  agencies say which banking organizations it is expected to be most relevant to. It excludes generative and agentic
  AI (footnote 3), so the agent needs its own governance. Source:
  https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm. Say "aligned to the structure of SR 26-2",
  never "compliant".
- **NAIC Model Bulletin**, "Use of Artificial Intelligence Systems by Insurers", adopted December 4, 2023; adopted
  wording varies by state. Source:
  https://content.naic.org/sites/default/files/cmte-h-big-data-artificial-intelligence-wg-ai-model-bulletin.pdf.pdf

Data sources, if screening comes up: the OFAC SDN list (treasury.gov) and OpenSanctions (CC BY-NC 4.0, attribution
in the README "Datasets and licenses").

## 10. Likely questions after the demo

**1. Can I try it myself? Is there a link?**
Not yet. The Hugging Face Space and the deploy workflow exist, but no deploy has run, so there is no public URL. You
can run it locally with `make setup`, `make web-install` and `make web-dev`. Every web number we have was measured on
a local container.

**2. Where is the AI? Did a language model write the memo?**
No. The memo in the demo is a deterministic template that restates the reference policy's result with its citations,
and it is stamped "FAKE LLM — test mode". Anthropic and OpenAI were never called, because no key exists. The only real
LLM calls in the project were small local Ollama smoke runs, and we quote no rates from them. The design rule is that
code computes the numbers and a person decides. For example, the Studio plan summary may only place `{{claim:...}}`
slots, and code fills every number from the ledger (README, "What each side does").

**3. How do you know the simulated probabilities are right?**
We do not know that for real businesses. We tested the simulator on 2,400 SYNTHETIC worlds where the truth is known.
Its Brier score was 0.1405 [0.1321, 0.1493] against 0.4796 [0.4596, 0.4992] for a single-point forecast
(`results/studio_feasibility_metrics.json`, `headline[0]`). A plain base rate scored 0.1567 [0.1473, 0.1664], close to
the simulator. That is why every number on the page says "simulated, based on stated assumptions".

**4. What does "the copilot never decides" mean in code?**
The only way to record a decision is to resume the agent graph at its human checkpoint. There, `validate_decision`
applies the guideline rules: who may decline, approval limits, open blocking referrals, override reasons. The role
comes from the signed session, not from the request. The page asks the same function in advance to lock buttons, and
the server checks again on submit. Across 65 SYNTHETIC applications, 0 decisions were recorded without a person
(`results/agent_metrics.json`, `deterministic_path`).

**5. 0.8482 against 0.0255 sounds too good. What is the catch?**
Most of the gap comes from the definitions, and that is true under both rules. The baseline B0 is a construction from
the same SYNTHETIC inputs and documents, not a sample of real founders' plans. Under rule S a model estimate or a
calculation counts as sourced for Studio, because the ledger records the run or the formula and its inputs, and B0
prints no such record. The stricter rule E counts only document pages, public-data rows and program or target
sections: there it is 0.057 [0.0545, 0.0597] against 0.0255 [0.0185, 0.0325], a difference of 0.0315 [0.0254,
0.0377]. That gap also comes mostly from the definitions: 724 of Studio's 821 rule E figures are sections of the
SYNTHETIC startup program and the Studio's own targets, which B0 never prints
(`evidence_coverage.studio_figures_by_status_kind_and_verdict`). The one comparison on the same population, the
founder's typed figures backed by a document, is 0.1315 for both, equal by construction
(`results/studio_business_metrics.json`, `headline`). The baseline was defined in writing before the first run
([s12_baseline.md](studio/s12_baseline.md)). A code review found that the first run's B0 still printed the size of a
rate rise for a variable-rate plan. That input only acts when a rate shock happens, so a one-forecast plan does not use
it, and section 2 of that definition leaves such inputs out. The code was fixed (commit `99b7a93`) and S12 was
re-measured at commit `6fcfad9` (`provenance.git_sha`, `git_dirty` false), which is where the numbers above come from.

**6. What happens with a real city?**
The five largest US metros are configured. All five run in limited mode, because their lowest business type,
short-term rental, has data coverage below the 0.80 threshold (`results/studio_metro_coverage.json`, each metro's
`mode_reason`; README, S1 row). Factors without data show as unknown and
are never filled in. Without data snapshots on the machine, real-metro sites get no score, and the page says the
simulation does not run there.

**7. Why did you show a PDF instead of a button in the app?**
Because the web app does not build packages yet; the card on the Studio home says "Coming in a later version". The
package builder runs on the command line, and across the 36 golden SYNTHETIC packages a script read back 30,681
printed numbers with 0 untraced (`results/studio_handoff_metrics.json`). We did not want to imply the web app does
something it does not.

**8. Who built this?**
Rahul Vajja and Bhavani Adula wrote the specs, the build prompts and the evaluation protocol, and they review the
work. Claude Code is the implementer. Git history cannot say more than that, so we do not split components between
the two owners.

**9. Is this compliant with model risk guidance or fair-lending rules?**
No, and we do not claim it is. It is a demonstration. The governance pack follows the structure of SR 26-2 / OCC
Bulletin 2026-13 / FDIC FIL-15-2026 (April 17, 2026). That guidance excludes generative and agentic AI, and whether it
applies at all depends on the institution and its risk profile (section 9).

**10. What would you do next?**
Things the results files list as not measured or not done: deploy the Space and measure it there, run a usability
test with real people (`headline.usability_test` is "not measured"), and measure LLM memo quality once a model is
connected (`results/agent_metrics.json`, `llm_dependent`). The web app should also build the package and call the
handoff, which today only the command line does.

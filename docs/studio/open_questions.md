# Founder Studio: risks and open questions before S1 (Phase S0)

Written on 2026-10-12 for Rahul Vajja and Bhavani Adula. Each item needs an answer from the owners. Where this file
makes a proposal, it says "proposal"; nothing here is decided.

**Status update, 2026-10-13.** Two kinds of status are used below, and only the first one is an owner answer:
- **CLOSED**: the owners acted, and the row names the evidence: A1, A2 and C7.
- **Proposal implemented; pending owner sign-off**: the build follows a proposal, but no owner answer is recorded,
  so the item stays open until the owners confirm or change it: A4, B3, C6 and F2.

B1 and D3 are partly answered (what is still open is said in the row). Every other item is still open.

## A. Access and accounts

| # | Question | Why it matters | Needed before |
|---|---|---|---|
| A1 | **CLOSED (2026-10-13).** The owners switched the build environment's network to "Full" on 2026-10-12; `make studio-data METRO=all` now runs here. Measured reachability (Census, LEHD, Overture, HPMS, BLS, FRED reachable; overpass-api.de and download.geofabrik.de not) is in [data_sources.md](data_sources.md); every step's outcome is in each snapshot manifest. Original question: where does S1 download data? | Without it, S1 can build and test connectors on SYNTHETIC fixtures only, and no metro, ranking or license can be confirmed. | S1 |
| A2 | **CLOSED (2026-10-13).** The owners registered CENSUS_API_KEY, BLS_API_KEY, FRED_API_KEY, HUD_USER_TOKEN, ORS_API_KEY and KAGGLE_API_TOKEN; they are set as environment variables (never in git; `key_status` in each snapshot manifest records only whether each is set). Measured here: api.census.gov refused the Census key as `invalid_key` earlier on 2026-10-13 and answered HTTP 200 with it later the same day; the 2026-10-13 snapshots' acs_tracts steps succeeded with it. Nothing in the repo records what changed in between. Original question: who registers the free API keys? | Connectors cannot run for real without them. | S1 (S2 for OpenRouteService) |
| A3 | **Who owns the accounts** for Supabase (auth + Postgres), Stripe (test mode only) and the Hugging Face team account (Space, and Inference if used)? Who pays? | S7 and S11 depend on them. The Stripe and Supabase hosts are also blocked here. | S7, S11 |
| A4 | **Proposal implemented; pending owner sign-off (2026-10-13).** This is a licensing question for the owners (CLAUDE.md rule 2). What the build does now: small recorded excerpts are committed under `tests/studio/fixtures/` with `fixture_origin`, the URL (never a key) and SHA-256 values; hand-written fixtures keep their "NOT recorded" label. The Overture sample keeps only rows whose first source is CC0-1.0 and ships the CDLA Permissive 2.0 text (its rows also list CDLA-licensed contributions). No raw dataset is committed. Still unverified for these excerpts: the terms of the LODES files (no license text on the LEHD data page), the HPMS service and the Apache 2.0 terms (see D3). Original question: can recorded API responses be committed as test fixtures? | Decides whether CI tests use real or synthetic shapes. | S1 |

## B. Data, storage and order

| # | Question | Why it matters | Needed before |
|---|---|---|---|
| B1 | **Partly answered (2026-10-13).** Each snapshot manifest now records what each layer holds (Overture places kept, HPMS segments and pages, LODES rows per state) and the wall-clock time of that run's places extract (`layers.places.extract_seconds`); file sizes and other download times are still not measured by a script, and what goes into the Space image is still open (S11): the current image ships no snapshot, so every real metro runs in limited mode there. Note for S11: a snapshot's `places.json` mixes CDLA/Apache/CC0 data and must ship with the license texts and the Foursquare NOTICE if it is put in a public image, and Overture's 60-day retention rule applies to it (data_sources.md 2c). Original question: data volume and storage per metro. | Decides what is prebuilt into the image, downloaded on first start, or stored outside (S11), and how big the Space gets. | Measure in S1; decide in S11 |
| B2 | **Which metro to finish first?** Proposal for discussion: one single-state metro end to end first (fewer adapters), then a multi-state one to test cross-state joins. The ranking itself is still UNVERIFIED ([metros.md](metros.md)). | The plan builds the national layer for all five metros first, then enhancements one metro at a time. | S1 |
| B3 | **Proposal implemented; pending owner sign-off (2026-10-12).** The ranking uses the PEP file `cbsa-est2025-alldata.csv` (POPESTIMATE2025) and the counties use the July 2023 delineation file, both recorded with URL, date and SHA-256 in `results/studio_metro_ranking.json` and the metro configs ([metros.md](metros.md)). Re-ranking at each release remains the proposal. Original question: which Census vintage, and when to re-rank? | Keeps "why these cities?" stable and repeatable. | S1 |
| B4 | **Weather data** for seasonality (car wash, dessert shop) is not in the plan's source list. Add a source, or keep seasonality as ASSUMPTION? | Otherwise the seasonality curves for these types stay unsourced. | S1 |

## C. Fit with the lender side

| # | Question | Why it matters | Needed before |
|---|---|---|---|
| C1 | **How does the lender side treat a new business?** The SYNTHETIC Northwind reference policy needs a full run of bank statements and has minimum time-in-business rules, so every pre-revenue founder file ends in "request more information" or "refer". Options: (a) add a SYNTHETIC "new business" section to the guideline with rules on projections, labeled as projection-based; (b) keep the policy and show the founder that the lender would refer; (c) limit v1 to expansions by existing operators. | The two-sided handoff (I5) is only meaningful if the lender side can evaluate the file. | S6 (design in S3) |
| C2 | **New application schema.** `CreditApplication` forbids unknown fields and has no projection fields. Four of the six business types have no SYNTHETIC industry code. A versioned schema change is needed. | Changing the schema can break the 65 committed agent applications and their checksums if done carelessly. | S6 |
| C3 | **Ledger statuses.** Studio needs USER_STATED and ASSUMPTION in the shared Phase 4 ledger. Agree with whoever builds Phase 4 now, so there is one status list. | Avoids a second ledger or a later migration. | Phase 4 / S4 |
| C4 | **Website roles.** Proposal: `lender_reviewer` = `underwriter`, `lender_senior` = `senior_underwriter`; `founder` and `admin` can never decide. | `agent/hitl.py` knows only the two reviewer roles today. | S7 |
| C5 | **Who are the lenders in the deployed app?** Addendum rule C forbids recommending a specific lender. Proposal: the lender portal is a demo with the SYNTHETIC Northwind lender until the business and legal items in section E are done. | Avoids implying a real lending relationship. | S7, S11 |
| C6 | **Proposal implemented in S3; pending owner sign-off (2026-10-12).** The feasibility default DSCR target is 1.25 cited to the SYNTHETIC Northwind guideline NBC-4.2 and labeled "lender policy (SYNTHETIC Northwind guideline NBC-4.2), editable" (docs/studio/walkthrough_s3.md); SBA SOP 50 10 is not cited. Original question: DSCR threshold in S3. | No unverified number is hard-coded. | S3 |
| C7 | **CLOSED (2026-10-13).** The owners created the Hugging Face Space astra6/FundProof and its HF_TOKEN secret; the deploy workflow targets it (commit 84d67cc, docs/deployment.md). Original question: deployment target for Phase 7 C. | Avoids two deployments and two security write-ups. | S11 |

## D. Licenses that matter once money is involved

| # | Question | Why it matters | Needed before |
|---|---|---|---|
| D1 | **OpenSanctions is used under CC BY-NC 4.0** (non-commercial, per the README). A paid app is commercial use. Options: no OpenSanctions in the paid product, OFAC only, or a commercial license. | The lender side screens names with it. | Before any paid use |
| D2 | **qwen2.5 3B license.** `docs/innovations.md` says the model's upstream Hugging Face card listed the Qwen Research License (checked by the plan author on 2026-10-10). The lead engineer's environment notes say the Docker Hub artifact used here carries an Apache-2.0 license file. Resolve before a public or paid deployment, or use a model with a clear license. | A model license can restrict commercial use. | S11 |
| D3 | **Partly answered (2026-10-13).** Recorded with page and date: Overture places (mixed CDLA Permissive 2.0 / Apache 2.0 / CC0 1.0 by source, attribution wording) and the LEHD citation format (no license text on the LEHD data page); see [data_sources.md](data_sources.md) section 2c. Still unverified: every other source in section 2b, the Apache 2.0 terms, and whether commercial use is allowed. Original question: data source terms. | A paid product must respect each source's terms. | S1 (record), S7 (show attribution) |
| D4 | **Which LLM runs in the deployed app?** Local Ollama inside the Space, a Hugging Face Inference endpoint (needs a key and has a cost), or the deterministic template only. The slot design works with no LLM. | Cost cap ($10 per run, CLAUDE.md rule 4), license (D2), cold start. | S4, S11 |

## E. Owner decisions (not engineering)

These come from the Studio plan's "Before charging real money" list. No code depends on them before S7, and Stripe
stays in test mode until all are done.

- Form the business entity and agree ownership in writing.
- Bhavani: confirm OPT rules for self-employment with the school DSO before earning from it.
- Check state loan-broker rules before taking any lender referral fees.

## F. Process

| # | Question | Needed before |
|---|---|---|
| F1 | **CLAUDE.md sign-off.** The Studio track addendum was appended to CLAUDE.md as "pending Rahul's sign-off". Both owners must agree before it is final. | S1 |
| F2 | **Proposal implemented; pending owner sign-off.** Commits and docs say "Studio S1-S3", "Studio S11" and "Innovation S1b" (for example commits 8e4954f, 84d67cc, 90ed5d7). Original question: naming of S-numbered items. | S1 |
| F3 | **Node toolchain in CI.** S7 adds a Next.js app, so CI needs Node and a committed npm lockfile. | S7 |
| F4 | **Walkthrough owners.** Addendum rule A asks that both owners can explain each Studio phase. Agree who reviews each walkthrough. | S1 |

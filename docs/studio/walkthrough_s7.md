# Walkthrough: Studio S7, website v0.1 (Founder Studio + Lender Desk demo)

Written on 2026-10-14 so that both Rahul Vajja and Bhavani Adula can explain this phase in their own words. (The
Studio addendum in CLAUDE.md is still a proposal until both owners sign it; this phase follows it anyway.)

**What v0.1 is.** The first public prototype of Fundproof (working name). One container serves two things:

- **Founder Studio**: a founder describes a small-business plan (city, business type, idea, money, 1 to 3 sites),
  and the app shows site scores, a simulated feasibility check and a "path to approval" (the smallest plan changes
  that reach the simulated targets). It prepares and explains. It never approves, declines, promises approval or
  recommends a lender. Every results page shows: "Educational planning tool. Not financial, legal or lending
  advice. Lenders make their own decisions."
- **Lender Desk DEMO**: a reviewer opens a SYNTHETIC application, sees what the existing underwriting agent found
  (findings with guideline citations, calculations, screening, a template memo), and records a DEMO decision. The
  copilot never decides; a person does, and only through the agent's own checkpoint.

**Commits.** The code landed in WIP checkpoints 10-12 (`38b6ca3`, `5d68c83`, `271ca4d`). `39da937` closed a review
with three lenses (security, product / accessibility / honesty, integration): 27 findings, all fixed with tests, plus
the Studio S5 changes (the engine's own status message, PLAN_CHANGE instead of USER_STATED, the owner-cash bound,
program rules not met). It also added the metrics script, the two results files and the deploy docs. `aa2260b`
added the "use a made-up plan" notice (below).

**Status.**

- Built, reviewed and committed. Measured only on a **LOCAL container** (image built from commit `271ca4d`, the
  same app as `39da937`). The v0.1.0 deploy target `aa2260b` adds the "use a made-up plan" notice after that
  measurement, so the Studio home and new-plan pages with the notice are **not measured**.
- **Not deployed yet.** The owners created the Hugging Face Space `astra6/FundProof` and the `HF_TOKEN` repository
  secret on 2026-10-13. The first deploy runs when an owner pushes a `v*` tag (or starts the workflow by hand).
  **Nothing has been measured on the Space.**
- The privacy notice (`docs/studio/privacy.md`) is a draft for the owners to approve, so the Studio pages ask
  visitors to use a made-up plan.
- In the image, the five real metros run in **limited mode** (no data snapshots inside). Building real-metro
  snapshots into the image is **planned for v0.1.1**; it is not part of v0.1.
- Files that other work is changing for v0.1.1 right now (`Dockerfile`, `.dockerignore`, `Makefile`,
  `scripts/deploy_space.py`, `scripts/web_metrics.py`, `docs/deployment.md`): this walkthrough describes their
  committed v0.1 versions.
- Not in v0.1, although the S7 build prompt names them: Supabase Auth (v0.1 uses Hugging Face sign-in), Stripe
  (there is no payment code; `/api/config` says `payments_enabled: false`), uploads, PDF download (v0.1 downloads
  the results as JSON), "share with a lender" links, and the founder-to-lender handoff (not built yet; when it comes
  is for the owners to decide).

## 1. Architecture: one container

```
 Browser  (static Next.js pages; they call /api with fetch, same origin)
    |
    v
 uvicorn uwcopilot.web.app:app   ONE process, port 7860, user uid 1000
    |
    |-- middlewares, outermost first:
    |     access log (one redacted line) -> security headers (CSP) -> rate limit -> body size limit -> CSRF
    |
    |-- /api/*  FastAPI routers
    |     system   /api/health, /api/config
    |     auth     /api/me, /api/auth/csrf, login, callback, logout, dev login (local only)
    |     studio   /api/studio/options, projects, sites/score, feasibility, path-to-approval, areas, tracts
    |     lender   /api/lender/queue, files/{id}, sections/{id}, files/{id}/decision, files/{id}/record
    |        |
    |        '-- services (thin adapters; every number comes from uwcopilot.studio or uwcopilot.agent)
    |              projects.py      plans: validation, versions, seed, storage per user
    |              studio_runs.py   SiteIQ, the Monte Carlo simulation, find_paths (QUICK mode), "Apply this change"
    |              lender_demo.py   the agent graph to its human checkpoint; decisions via validate_decision
    |              compute.py       worker threads, 60 s timeout, per-user cap, result cache
    |              geocoding.py     Census Geocoder, own pool, per-user budget, no cache
    |              text_checks.py   refuse SSN / account numbers; injection screen of the idea text
    |
    |-- Store: LocalFileStore (Fernet-encrypted files on the container disk)
    |          or HFDatasetStore (an encrypted mirror in a PRIVATE Hugging Face dataset)
    |
    '-- everything else: the Next.js static export (frontend/out), one CSP per HTML page
```

Why one container: the Space runs one Docker image on one port, and a static export needs no Node server at run
time. The browser and the API share one origin, so cookies and CSRF stay simple and no CORS is ever approved in
production.

## 2. Files

### Backend (`src/uwcopilot/web/`)

| File | What it does | Why it is built this way |
|---|---|---|
| `settings.py` | Every setting from the environment, checked once at startup. Refuses unsafe combinations: the dev login inside a Space, a Space without `UWC_SESSION_SECRET`, the dataset store without an encryption key. Holds `DISCLAIMER`. | A wrong setting stops the app loudly instead of running unsafely. Every default is a design choice for a small prototype, not a measured value. |
| `app.py` | `create_app(settings)`: middlewares, routers, OpenAPI at `/api/docs`, the store opened at startup, retention purges (at startup, then every `UWC_PURGE_EVERY_S`). | Tests build their own app with their own settings. |
| `auth.py`, `routers/auth.py`, `cookies.py` | Hugging Face sign-in (OpenID Connect + PKCE), dev login, signed session cookies, roles, `require_role`. Cookie attributes for the Space iframe. | Written by hand because `huggingface_hub`'s helper did not fit (its cookie is not Partitioned, it keeps the access token in a readable cookie, it has an open redirect; details in `docs/web/auth.md`). |
| `security.py` | CSP and other headers, per-IP and per-user rate limits, the body size limit, the CSRF check. | Small ASGI middlewares, so every route gets them, and a 429, 413 or 403 still gets the headers and a log line. |
| `access_log.py` | One line per request: method, path with personal patterns masked, the NAMES of query parameters, status, time. | No bodies, cookies, headers, IP addresses or usernames in logs. |
| `crypto.py`, `store.py`, `hf_store.py` | Fernet encryption at rest; `LocalFileStore`; `HFDatasetStore` (private dataset, batched commits, history squashed after a deletion, `DeletionPending` when the Hub cannot confirm). | One `Store` interface, two implementations. A user can only read objects stored under their own id. |
| `static.py` | Serves `frontend/out` (exact file, then `<path>/index.html`, then the 404 page). | Matches Next.js `output: "export"` with `trailingSlash: true`. |
| `routers/system.py` | `/api/health` (status, version, git sha) and `/api/config` (product name, disclaimer, labels, roles, which sign-in methods exist, `payments_enabled: false`). | The frontend reads its labels from the server, so they cannot drift. |
| `routers/studio.py`, `schemas_studio.py` | The Founder Studio API. Every response with numbers carries `disclaimer`; every simulated probability block carries `label` = "simulated, based on stated assumptions". | Engine models are reused as they are (FeasibilityResult, SourceRecord, path-search Outcome), so the API cannot drift from the engine. |
| `routers/lender.py`, `schemas_lender.py` | The Lender Desk DEMO API. Every response says "SYNTHETIC DEMO" and "The copilot never decides — a person does." | The agent's own models (findings, calculations, screening snapshots) are reused, so the page shows exactly what the agent produced. |
| `services/projects.py` | Plans: validation, versions, the seed, who wrote each figure (USER_STATED, ASSUMPTION, PLAN_CHANGE, DERIVED), at most 20 plans per user, deletion. | Studio rule D: an assumption or a suggestion is never shown as the founder's own number. |
| `services/studio_runs.py` | Calls SiteIQ, the S3 simulation (1,000 paths, 36 months) and the S5 path search in QUICK mode; `apply_patch` for "Apply this change". | Pure functions of the stored plan version, so their results can be cached per version. |
| `services/studio_options.py` | Metros with their honest mode (synthetic, limited, full), business types, levers, defaults; all read from the Studio config and loaders. | A real metro's mode comes from the snapshot actually present where the app runs; nothing is typed in. |
| `services/lender_demo.py` | Runs the existing agent graph on SYNTHETIC files to its human checkpoint; the template memo; tool caching; the per-user DEMO decision sandbox. | There is no other way to record a decision than `agent.hitl.validate_decision` via `agent.runner.resume_run`. |
| `services/compute.py` | `ComputeRunner`: heavy work in worker threads, a timeout, a per-user cap, de-duplication of identical jobs, an LRU result cache. | A Monte Carlo run or an agent run must never block the event loop for everyone else. |
| `services/geocoding.py` | Address lookups for real metros through the keyless Census Geocoder, in their own small pool, with per-user limits, one request per second server-wide, no cache. | A slow geocoder can never make simulations answer "busy"; an address is personal data, so it is not cached. |
| `services/text_checks.py` | Refuses an SSN-shaped number, a labeled account number or a run of 8-17 digits (422, without repeating the text). Screens the idea text with the agent's injection tripwire after normalization. | Studio rule B (data minimization). Flagged idea text is stored but marked, and v0.1 gives no idea text to any language model. |
| `services/http.py`, `services/errors.py`, `services/settings.py` | Error shapes (validation errors never echo the input), the store dependency, `Cache-Control: no-store`; `ServiceError` with its HTTP status; the compute settings. | The services do not import FastAPI, so they are easy to test. |

### Frontend (`frontend/`, Next.js static export, TypeScript)

| Folder | What it holds |
|---|---|
| `app/` | Pages: `/` (landing), `/about`, `/signin`, `/studio` (your plans), `/studio/new` (the wizard), `/studio/project?id=` (results), `/lender` (demo queue), `/lender/file?id=` (one file), the 404 page. Record pages use a query string because a static export cannot have dynamic routes. |
| `components/studio/wizard/` | The six steps: city (each metro with its mode badge), business type, idea, money, sites (map pins or addresses), review. |
| `components/studio/project/` | The results dashboard: site cards with factor tables and sources, feasibility (probability gauges, revenue and cash fan charts with P10/P50/P90, tornado chart, every sampled input with its status), path-to-approval cards with "Apply this change", target editor, plan editor, "Download results (JSON)" and "Delete plan". |
| `components/studio/map/` | MapLibre map of tract centroids from the API's GeoJSON. No basemap and no third-party tiles. Every map action is also in a keyboard-accessible tract list. |
| `components/studio/prototype-data-notice.tsx` | "Prototype: please use a made-up plan" (see section 6). |
| `components/lender/` | Queue, file view, citation drawer (guideline section text), decision panel (buttons disabled with the reason when the checkpoint would refuse), decision record. |
| `components/evidence/`, `components/layout/`, `components/session/`, `components/ui/` | Evidence and data-status chips, SYNTHETIC banner, results disclaimer, header and footer, sign-in prompt with `?next=`, dev login form, shared UI parts. |
| `lib/` | API clients (`api.ts` fetches the CSRF token and sends it on every state-changing call), types, formatting, roles. |
| `mock-api/`, `scripts/` | A mock API with recorded fixtures for frontend work and screenshots; the contrast check; the MapLibre worker copy. Not in the image. |

### Container, deploy and measurement

| File | What it does |
|---|---|
| `Dockerfile` (v0.1) | Four stages: Node builds `frontend/out`; Python 3.11 + uv installs the locked dependencies; an `assets` stage downloads the OFAC SDN and alternate-name lists, builds the Qdrant guideline index and runs one real guideline search (the build fails if it finds nothing); the runtime stage copies only what the app needs, sets `UWC_SCREENING_LISTS=ofac` and runs as uid 1000. Base images pinned by digest. |
| `scripts/deploy_space.py` | `bundle`: copy an allow-list into an empty folder, add the Space card as `README.md`, stamp the commit into the Dockerfile. `push`: create the Space if missing, set a fresh `UWC_SESSION_SECRET` Space secret, upload the bundle. `wait`: poll until the Space's `/api/health` names the pushed commit. Reads `HF_TOKEN` and `HF_SPACE_ID` from the environment and never prints them. |
| `scripts/smoke_deploy.py` | Readiness, checks (9 when it is given the container) and request latency against a running deployment; writes `results/deployment_metrics.json`, one entry per target. |
| `scripts/web_metrics.py` | Playwright flows, axe-core scans and Lighthouse runs against a LOCAL container started with the dev login; writes `results/web_metrics.json` and screenshots to `docs/web/screenshots/e2e/`. Refuses to write results for an image that names no commit (unless marked unreproducible). |
| `.github/workflows/deploy-space.yml` | Runs by hand or on a pushed `v*` tag. Job `verify` (ruff, pytest, frontend checks and build, Docker build, container smoke test), then job `deploy` (only if `verify` passed and `HF_TOKEN` exists). Every action pinned to a commit SHA. |
| `docs/deployment.md` | How the container and the deploy work, the measured numbers, what to check on the first deploy, and the owners' open decisions. |
| `docs/studio/privacy.md` | Draft privacy notice: what is collected and refused, who else sees what, storage, deletion and retention, logs, and what S8 still has to build. |
| `tests/web/` | Offline tests of the routes (auth, CSRF, ownership, validation, timeouts, deletion, the decision checkpoint), the stores, the deploy script with a fake Hub, the smoke and metrics scripts with fakes. |

## 3. Sign-in, CSRF, rate limits, body cap

**Sign-in.**

- In the Space: "Sign in with Hugging Face". The Space README sets `hf_oauth: true`, and Hugging Face then injects
  the OAuth settings. The app runs OpenID Connect with PKCE, reads the profile once with the access token, and then
  drops the token. The session cookie holds only the sign-in mode, the stable account id, the username and the
  display name, signed, for 8 hours. Only the `openid profile` scopes are asked for, so no e-mail address.
- Locally: a DEV LOGIN (type any username, pick a role), only when `UWC_DEV_AUTH=1`. Because it lets anyone pick
  any role, the app refuses to start when `SPACE_ID` is set (every Space sets it), the endpoint checks again on
  every call, and a dev session cookie is ignored once the dev login is off.
- Roles: every signed-in user is a founder. `lender_reviewer`, `lender_senior` and `admin` come only from the
  `UWC_ROLE_ALLOWLIST` setting (an entry can be pinned to the account id) and are recomputed on every request.
- Inside the Space iframe the app's cookies are third-party cookies, so there they are
  `Secure; SameSite=None; Partitioned`, and the sign-in page offers "open in a new tab". None of this has run on the
  real Space yet.

**CSRF (cross-site request forgery).** A double-submit token. `GET /api/auth/csrf` sets the `uwc_csrf` cookie and
returns the same value. Every POST, PUT, PATCH or DELETE under `/api` must send it back in the `X-CSRF-Token`
header, or it gets 403. Another site cannot read our cookie, and it cannot send a custom header without a CORS
preflight, which the app never approves.

**Rate limits.** Token buckets per client IP and per signed-in user on `/api` (not `/api/health`): 120 requests per
minute with a burst of 60 each, by default. They live in the process's memory, which is enough for one container.
Address lookups have their own, smaller per-user budget.

**Body cap.** A JSON body over 64 KiB on `/api` is refused with 413 before it is read further. The largest real body
(a plan with a 2,000-character idea and three addresses) is a few KiB. Before this cap, a very large body of list
items blocked the event loop for a long time (found in review; not measured in a results file). Validation errors
never echo the rejected input.

## 4. Heavy work: the compute runner

Simulations, path searches and agent runs go through `ComputeRunner`:

- **Worker threads** (2 by default), so the event loop keeps answering other requests.
- **Timeout**: a request waits at most 60 s (`UWC_COMPUTE_TIMEOUT_S`). Then it gets 503 with a plain message.
  Python cannot stop a thread, so the job finishes in the background and its result is cached: the next try is
  instant.
- **Caps**: at most 8 jobs running or queued in total, and at most 3 per user, so one account cannot fill the queue
  and make everyone else's calculations answer "busy".
- **Cache**: results are kept in memory per plan version (256 entries, least recently used out). Two identical
  requests share one job. Changing or deleting a plan drops its results; a deleted plan's late result is not
  cached.

All of these are design choices for a small prototype, not measured values.

## 5. The encrypted store

- Every plan and every DEMO decision record is encrypted with Fernet before it touches disk.
- Objects live under the first 32 hex characters of SHA-256 of the account id, so folder names do not show who owns
  them. Inside the encrypted bytes a header names the owner, and reading checks it: a file copied into another
  user's folder is refused.
- Another user's plan id answers 404, the same as a plan that does not exist.
- Retention: 30 days by default (`UWC_RETENTION_DAYS`, a design choice). Expired objects are hidden at once and
  deleted at startup and every hour. "Delete plan" deletes on request.
- Without the dataset store everything is on the container disk and is lost when the Space restarts. With
  `UWC_STORE=hf_dataset` the encrypted files are mirrored to a private dataset; after a deletion the dataset's history
  is squashed so old revisions do not keep the file. If the Hub cannot be reached, the app says the deletion is
  pending instead of claiming it is done. This store is tested with a fake Hub only; it has not run against the
  real Hub.

## 6. The Founder Studio flow

1. **Wizard** (`/studio/new`): city -> business type -> idea -> money -> sites -> review. Each city shows its honest
   mode: "SYNTHETIC demo city", "Limited data" or "Full data". The SYNTHETIC metro takes map pins (within 3 miles of
   one of its tracts); a real metro takes street addresses (geocoded on the server) or map pins.
   `POST /api/studio/projects` stores version 1 of the plan.
2. **Results** (`/studio/project?id=`): the page calls three endpoints.
   - `sites/score`: SiteIQ score 0-100 (or none), a coverage score, every factor with its status and source, and a
     demand estimate (low, P50, high).
   - `feasibility`: the S3 simulation, 1,000 paths over 36 months. It reports P(DSCR >= 1.25 in year 1 and year 2),
     P(stressed DSCR >= 1.00 in year 2) with revenue 20% lower, P(cash < 0 within 24 months), the break-even month,
     monthly P10/P50/P90 revenue and net income, a tornado chart, and every sampled input with its status.
   - `path-to-approval`: the S5 search in QUICK mode. Up to three change-sets, each with before and after
     probabilities, every figure that changes, what a lender would need to back it ("a written quote at or below
     $X"), and the program rules the changed plan still fails. A "no path" answer is the engine's own message: it
     says what was tried and that the search is not exhaustive. The founder can bound the extra owner cash; by
     default it is up to 20% of the project cost more (`config/studio/levers.yaml`, a design choice).
   Every probability block says "simulated, based on stated assumptions", and every results page shows the
   disclaimer.
3. **"Apply this change"**: the card sends `PUT /api/studio/projects/{id}` with the current version, only the
   fields the change-set changes, and a `plan_change` block. The server saves version + 1 and marks those figures
   PLAN_CHANGE (with the evidence a lender would need), never USER_STATED, until the founder types them again. Then
   the page re-runs the path search.

**Why the re-run shows exactly the card's numbers: one seed per plan.** The simulation seed is derived from the
plan id (the first 8 hex digits of its SHA-256), so it is the same for every version of the plan. The path search
uses the same seed. So the applied plan is simulated on the same random draws as the card's "after" outcome and
gives the same probabilities. An earlier version used a seed per version; then a change-set that "reaches all three
targets" could miss them on the next screen: same plan, different draw. This is the method of common random
numbers: when two versions are compared, the difference comes from the change, not from the dice.

**The "use a made-up plan" notice.** Studio addendum rule B says the deployed app may take real founder input only
under `docs/studio/privacy.md`. That document is a draft until both owners approve it. So the Studio home page and
the wizard show "Prototype: please use a made-up plan": use invented figures and public addresses only, do not
describe real people, and plans can be deleted when the server restarts (the Space uses the local store unless the
owners turn on the dataset store). `privacy.md` says to remove the notice once it is approved.

## 7. The Lender Desk demo

- **Files**: the 100 SYNTHETIC structured applications in `eval/applications` (32 credit, 33 insurance) and
  `eval/startup_applications` (35 startup credit). Each file's ground-truth label is never sent to the agent or the
  browser.
- **The real agent graph.** Opening a file runs the existing LangGraph agent to its human checkpoint. The memo
  writer is the deterministic **template memo** (`agent/fake_memo.py`): it restates the reference policy's result
  with that rule's citations, no language model is called, and every memo is stamped "FAKE LLM — test mode". The
  page says "template memo". Guideline search uses the Qdrant index built into the image; screening uses only the
  OFAC lists in the image (`UWC_SCREENING_LISTS=ofac`; OpenSanctions is not in the image), and the page names the
  lists and their snapshot date. A tool that is not available reports "unavailable" instead of crashing. The run is
  cached per file per process.
- **Decisions only through `hitl.validate_decision`.** A decision is `POST /api/lender/files/{id}/decision`. The
  server re-runs the file on the user's own thread in a fresh in-memory checkpointer and resumes the graph with the
  decision (`agent.runner.resume_run`). The graph's `human_review` node calls `validate_decision`, which enforces the
  guideline rules: only a senior underwriter may decline, approval limits by role, nothing approved or declined
  while a blocking referral is open, an override needs an approved reason. A refused decision answers 422 with the
  reasons and is not stored. An accepted one is stored in that user's encrypted store, labelled DEMO.
- **The role comes from the session**, never from the request body: `lender_senior` -> `senior_underwriter`;
  `lender_reviewer`, `founder` and `admin` -> `underwriter` (demo files only; there are no real files in v0.1).
- **Disabled buttons with reasons.** For each action the server asks `validate_decision` in advance, with your role,
  whether it would accept it (`action_checks`). The page disables the buttons it would refuse and shows why. The
  real decision is still checked again by the graph.

## 8. How deploy works

```
 an owner pushes a tag v*  (or "Run workflow" in GitHub Actions)
   '-- verify: ruff, pytest (offline suite), frontend lint + typecheck + contrast + build,
   |           docker build, run the container, scripts/smoke_deploy.py
   '-- deploy (only if verify passed; ends with "Deploy skipped" if the HF_TOKEN secret is missing):
         deploy_space.py bundle   allow-listed files + Space card, commit stamped into the Dockerfile
         deploy_space.py push     create the Space if missing, set a fresh UWC_SESSION_SECRET, upload the bundle
         (the Space builds the same Dockerfile itself)
         deploy_space.py wait     until https://<space host>/api/health names this commit
         smoke_deploy.py --target hf-space   results kept as the workflow artifact "space-smoke"
```

- The first deploy has not run. It waits on an owner pushing a `v*` tag.
- Every deploy replaces the Space's files with the bundle and signs everyone out (a new session secret). To roll
  back, run the workflow from an older tag.
- `.env`, `data/`, other results and the git history never reach the build: `.dockerignore` and the bundle are
  allow-lists.

## 9. What was measured (LOCAL container only)

### Container smoke test (`results/deployment_metrics.json`, entry `runs.local-container`)

Measured 2026-10-14T00:25:57Z by `scripts/smoke_deploy.py` (`make docker-smoke`), on image `fundproof:dev` built from
commit `271ca4d`, on the build machine over 127.0.0.1 with the dev login OFF. **Not the Space**: no internet hop, no
Space proxy, not Space hardware. It is the app of `39da937` (between the two commits only
`src/uwcopilot/governance/freeze_proof.py` changed among the image's files, and the web app does not import it), not
of `aa2260b`: the "use a made-up plan" notice is not in it.

| What | Value |
|---|---|
| Checks | 9 of 9 passed |
| Container start to first healthy `/api/health` | 2.3 s |
| First request to `/api/config` | 131.06 ms |
| 100 requests after the first (5 paths x 20), p50 / p95 | 1.93 ms / 5.73 ms |
| Image size on disk / compressed content | 2.57GB / 659MB |

One client sending one request at a time: a smoke test, not a load test.

### Browser flows, accessibility, Lighthouse (`results/web_metrics.json`, entry `runs.local-container`)

Measured 2026-10-14T00:48:23Z by `scripts/web_metrics.py` against the same image, started **with the dev login**
(`UWC_DEV_AUTH=1`) on 127.0.0.1:7862, because a script cannot sign in with Hugging Face. The server named commit
`271ca4d` (`reproducible: true` in the file, under the script's rule at the time; the stricter rule added for
v0.1.1 would refuse this image, which has no build label: see `docs/deployment.md` section 4b). Headless Chromium
141.0.7390.37, Playwright 1.56.0, axe-core 4.13.0, Lighthouse 13.5.0.

End-to-end flows: **5 of 5 passed**, with no unexpected console error or API error.

| Flow | Steps | What it shows |
|---|---|---|
| signed_out | 3 of 3 | public pages load; a signed-in page asks to sign in; an unknown page is the 404 page |
| founder_synthetic_plan | 10 of 10 | dev login, the wizard on the SYNTHETIC metro, results with the labels, every chart table opened; path search `no_path_within_bounds` with the default targets and `paths_found` with edited targets; "Apply this change" saved version 2 and re-ran |
| lender_demo_decision | 3 of 3 | queue, file `APP-C-001` (template memo, memo check passed, guideline search `ok`, OFAC `sdn.csv` and `alt.csv`, screening `clear`), a DEMO `approve` as `underwriter`, recorded as `approved_by_human` |
| error_paths | 2 of 2 | an unknown plan id says "Not found"; the wizard will not continue without a city |
| founder_delete_plan | 1 of 1 | "Delete plan", then the plan's page says "Not found" |

Timings inside the flows (one run, on a machine busy with other jobs):

| What | Value |
|---|---|
| "Create plan" click to the plan / site scores / simulation / path search answers | 80.6 / 189.6 / 531.8 / 916.5 ms |
| "Create plan" click to the path-to-approval result on screen | 1099.2 ms |
| "Apply this change": PUT answer / re-run path answer | 86.8 / 489.1 ms |
| First lender file after the container started (agent run, retriever and matcher load) | 12953.1 ms |
| Recording the DEMO decision | 302.0 ms |

axe-core: 32 scans (16 page states x light and dark, at 1280 px): **0 critical and 0 serious violations**. 20 scans
have a serious item axe could not decide ("incomplete"), which needs a person to review. Zero automated
violations is not a statement of WCAG conformance.

Lighthouse, median of 3 runs per page and preset (0-100):

| Page | Preset | Performance | Accessibility | Best practices |
|---|---|---|---|---|
| Landing `/` | mobile | 76 | 100 | 100 |
| Landing `/` | desktop | 98 | 100 | 100 |
| Studio results | mobile | 44 | 100 | 100 |
| Studio results | desktop | 86 | 100 | 100 |
| Lender file | mobile | 77 | 100 | 100 |
| Lender file | desktop | 93 | 100 | 100 |

Screenshots from the measured run: [Studio results](../web/screenshots/e2e/studio-results-1280-light.png),
[Lender decision](../web/screenshots/e2e/lender-decision-1280-light.png),
[Unknown plan](../web/screenshots/e2e/error-unknown-plan-1280-light.png).

## 10. What is not measured

- **Anything on the Space.** It is not deployed. Not checked yet: Hugging Face sign-in in the iframe and in a new
  tab, the OAuth callback, how many proxies append to `X-Forwarded-For` (rate limits depend on it), cold start and
  latency on Space hardware, whether the Space builder can download the OFAC lists, the Census Geocoder from the
  Space, and the dataset store against the real Hub. `docs/deployment.md` section 8 lists them.
- **Hugging Face OAuth** has not run for real anywhere (tests use a mock OpenID provider; the measured browser runs
  used the dev login).
- **Load.** No load test. The smoke timings come from one client sending one request at a time.
- **Memo quality with a language model.** The demo uses the template memo; LLM memo quality stays "not measured".
- **Real-world accuracy of any Studio probability.** They are simulation estimates on stated assumptions (Studio
  rule E).

## 11. Known limitations

- Studio results on mobile score 44 for Lighthouse performance (long main-thread work while the charts and the map
  render). The results page shifts layout on mobile and desktop, and the lender file on desktop (CLS above 0.1).
  Both are open frontend issues.
- One process: rate limits, the result cache and a generated session secret are per process, and a restart forgets
  them.
- The image is large (2.57GB on disk) because it installs the project's full dependency set; a smaller `web`
  dependency group is an owners' decision.
- Real metros run in limited mode in the image; real-metro snapshots in the image are planned for v0.1.1.
- The first lender file after a start is slow (12953.1 ms in the local run) while the retriever and the screening
  matcher load.
- The privacy notice is a draft; no Terms of Use page, no "delete all my data" button, no audit log yet (Studio S8).

## 12. Likely interview questions

**1. Why one container instead of a separate frontend and backend?**
The Hugging Face Space runs one Docker image on one port. The frontend is a static export, so it needs no Node
server at run time; FastAPI serves the files and the API from one origin. That keeps cookies, CSRF and the security
headers simple, and there is no cross-origin traffic to allow.

**2. How does sign-in work, and why can the dev login never run in the Space?**
In the Space, "Sign in with Hugging Face" uses OpenID Connect with PKCE; we keep only the account id, username and
display name in a signed 8-hour cookie and drop the access token. Locally, a dev login lets you type any name and
pick any role, which is why it is dangerous: the app refuses to start if it is turned on where `SPACE_ID` is set,
and the endpoint checks again on every call.

**3. What is CSRF and how does the app stop it?**
Cross-site request forgery is another website making your browser send a request to our app with your cookies. We
use a double-submit token: the app gives the page a token in a cookie and in the response body, and every
state-changing request must send it back in a custom header. Another site cannot read our cookie and cannot add a
custom header without a CORS preflight, which we never approve.

**4. What happens when a simulation is slow, or one user sends many?**
Heavy work runs in worker threads. A request waits at most 60 seconds, then gets 503 with a plain message; the job
keeps running and its result is cached, so a retry is instant. At most 8 jobs run or wait in total and at most 3 per
user, so one account cannot block everyone. Rate limits (120 per minute, burst 60, per IP and per user) and a
64 KiB body cap protect the API itself.

**5. How is a founder's data protected?**
Data minimization first: no uploads, and the server refuses SSN-shaped numbers and account numbers before storing
or sending anything. Plans are encrypted with Fernet, stored under a digest of the account id, and readable only by
their owner; another user gets 404. They expire after 30 days by default and can be deleted at any time. No
language model reads them. A street address goes only to the Census Geocoder and is not cached. Logs hold no bodies,
cookies, IPs or usernames.

**6. The Lender Desk says "the copilot never decides". How is that enforced in code?**
The only way to record a decision is to resume the agent graph at its human checkpoint, where
`hitl.validate_decision` applies the guideline rules (who may decline, approval limits, open blocking referrals,
override reasons). The role comes from the signed session, not the request. A refused decision returns 422 with the
reasons and is not stored. The page asks the same function in advance, so it can disable a button and say why, but
the graph checks again.

**7. Why one simulation seed per plan rather than per version?**
So that "Apply this change" shows exactly the numbers on the card. The path search simulated the changed plan on
the plan's seed; the applied version is simulated on the same seed, so it gets the same draws and the same
probabilities. With a seed per version, a change that reached all three targets on the card could miss them on the
next screen. Same draws for both versions also means a comparison shows the effect of the change, not of luck.

**8. Why does the Studio ask visitors to use a made-up plan?**
Studio addendum rule B allows real founder input only under an approved privacy notice. Ours is a draft until both
owners approve it. Also, without the dataset store, plans live on the container disk and disappear when the Space
restarts. So until then we ask for invented figures and public addresses only, and say so on the page.

**9. How does a deploy work, and why is nothing measured on the Space yet?**
A `v*` tag (or a manual run) starts the workflow. It first runs the same checks as CI plus a Docker build and a smoke
test of the container. Then it bundles an allow-list of files, pushes them to the Space with a fresh session secret,
waits until the Space's health endpoint names the new commit, and smoke-tests the Space. The first deploy has not
run yet, so every number we have is from a local container, and our files say so.

**10. What do the Lighthouse and axe numbers tell you, and what not?**
They tell us that 32 automated accessibility scans found no critical or serious violation, and that accessibility
and best practices score 100 on the three measured pages, locally. They do not prove WCAG conformance: axe left 20
scans with "incomplete" items for a person to check. Performance is the weak spot: the Studio results page scores 44
on the mobile preset. And all of it was measured on a local container on a busy machine, not on the Space.

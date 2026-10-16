# Fundproof web frontend (Studio S7 shell + v0.1 feature pages)

Written 2026-10-12 for Bhavani Adula and Rahul Vajja; updated 2026-10-13 with the v0.1 feature pages. This page
explains every file in `frontend/`, the design choices, how to run and check it, and what is not built yet.

The **shell** (layout, sign-in, landing, About) came first. The **v0.1 feature pages** plug into it:

| Route | What it does |
|---|---|
| `/studio/` | Your plans (newest first) and "Start a plan". |
| `/studio/new/` | The plan wizard: city, business type, idea, money, candidate sites (map, tract list, suggested areas, or addresses), review. |
| `/studio/project/?id=` | Results of one plan: the numbers you gave, site scores with data coverage, the simulation (gauges, revenue and cash bands, tornado, break-even), the path to approval with "Apply this change" and "Edit the targets", JSON download, delete. |
| `/lender/` | The DEMO queue of SYNTHETIC files: line filter, search, paging. |
| `/lender/file/?id=` | One SYNTHETIC file: the policy's suggestion, findings, memo with citations, calculations, sanctions screening, tools used, rules not evaluated, the citation drawer, and "Your decision" (checked by the server, recorded in a demo sandbox). |

> Educational planning tool. Not financial, legal or lending advice. Lenders make their own decisions.
> Every example number in the frontend is SYNTHETIC and labeled so on the page. The pages never compute a
> result: every number shown comes from the API (or, in the mock, from answers recorded from the real API).

## 1. What it is, in one paragraph

A Next.js app (App Router, TypeScript) built as a **static export**: `npm run build` writes plain HTML, CSS, JS and
font files to `frontend/out/`. There is no Node server in production. The FastAPI app (`uwcopilot.web.app`)
serves `frontend/out/` for every non-API path and `/api/*` for data, from one origin, inside one container on port
7860. The browser talks only to that origin. Sign-in is "Sign in with Hugging Face" (handled by the server);
locally a clearly labeled DEV LOGIN replaces it. Charts use Recharts and the site map uses MapLibre GL; both are
loaded on demand, only on the pages that draw them (section 9).

## 2. How to run it

Requires Node 22 and npm 10 (checked here: Node v22.22.0, npm 10.9.4).

| Command | What it does |
|---|---|
| `make web-install` | `npm ci`: installs the exact versions in `frontend/package-lock.json`. |
| `make web-build` | `next build`: static export to `frontend/out/` (the `prebuild` step copies MapLibre's worker, section 4). |
| `make web-serve` | (backend target) FastAPI on http://127.0.0.1:7860 serving the API and `frontend/out/`. |
| `make web-dev` | FastAPI with `UWC_DEV_AUTH=1` on :8000 plus `next dev` on http://localhost:3000. `next dev` proxies `/api/*` to :8000, so cookies and CSRF behave as in production. Both listen on 127.0.0.1 only: the DEV LOGIN lets anyone pick any role. |
| `make web-mock` | The **mock API** (section 8) on http://127.0.0.1:4174: serves `frontend/out/` and replays answers recorded from the real app. Needs only Node; build first. |
| `make web-mock-fixtures` | Re-record the mock's answers from the real app, in-process (about 25 s here). |
| `make web-lint` | ESLint (`--max-warnings=0`), TypeScript (`next typegen && tsc --noEmit`), and the contrast check. |
| `make web-test` | `pytest tests/web` (backend) and `make web-lint`. |
| `make web-screenshots` | Playwright screenshots of `frontend/out/` without an API (offline states). Add `LIVE_URL=http://127.0.0.1:8000` to also drive a running dev server through DEV LOGIN. |
| `make web-screenshots-mock` | Playwright screenshots of every feature page on the mock API, 1280 and 375 px, light and dark, into `docs/web/screenshots/mock/`. |

Inside `frontend/` the same steps are `npm run build`, `npm run lint`, `npm run typecheck`,
`npm run check:contrast`, `npm run dev`, `npm run mock`. `build`, `dev` and `typecheck` run Next.js through
`scripts/run-next.mjs`, which switches telemetry off and does not pass secret-looking environment variables (names
containing KEY, TOKEN, SECRET, ...) to Next.js: Turbopack keeps a snapshot of its environment in `.next/cache`.

**Static preview without the API**: `cd frontend/out && python -m http.server 4173`. Every `/api/*` request gets
the file server's HTML 404, and the pages show a friendly "server is not reachable" state (section 6).

## 3. The static export contract (shared with the backend)

- `next.config.ts` sets `output: "export"` and `trailingSlash: true`. Each route is written as
  `<route>/index.html` and links are rendered as `/studio/`. This works with any plain static file server.
- The backend's `src/uwcopilot/web/static.py` serves the exact file first, then `<path>/index.html`, then
  `<path>.html`, else `out/404.html` with status 404. It caches `/_next/static/*` for a year and sends `no-cache`
  for HTML. It serves `.mjs` as `text/javascript` (MapLibre's worker is an ES module).
- **No dynamic route segments.** Record pages use query strings: `/studio/project/?id=...`,
  `/lender/file/?id=...`. `lib/location.ts` reads the query after hydration.
- **CSP.** The backend hashes each HTML file's inline bootstrap scripts and sends `script-src 'self'
  'sha256-...'`; `worker-src 'self' blob:` allows MapLibre's worker; `connect-src 'self'` keeps every request on
  our origin (the map draws only our own GeoJSON, no tile server). The live runs in section 10 showed no CSP
  violation in the browser console.
- In development only, `next.config.ts` drops the export and proxies `/api/:path*` to `$UWC_API_DEV_ORIGIN`
  (default http://127.0.0.1:8000). `agentRules: false` stops `next dev` from writing `AGENTS.md`/`CLAUDE.md`.

## 4. Every file

### Configuration
| File | Purpose |
|---|---|
| `package.json` | Exact versions (no `^`/`~`), scripts. `predev`/`prebuild` run `scripts/copy-maplibre-worker.mjs`; `mock` starts the mock API. `.npmrc` sets `save-exact=true`. |
| `package-lock.json` | Commit it. `npm ci` (Docker, CI) installs exactly this. |
| `next.config.ts` | Static export vs dev proxy (section 3). |
| `tsconfig.json` | Strict TypeScript, `noUncheckedIndexedAccess`, path alias `@/*`. |
| `eslint.config.mjs` | `eslint-config-next` core-web-vitals + TypeScript; ignores `public/vendor/**` (copied MapLibre files). |
| `postcss.config.mjs` | Runs Tailwind CSS v4. |
| `.gitignore` | `node_modules/`, `.next/`, `out/`, `next-env.d.ts`, `*.tsbuildinfo`, `/public/vendor/`. |

### `app/` (routes)
| File | Route | Purpose |
|---|---|---|
| `layout.tsx` | all | Fonts, `globals.css`, `<SessionProvider>`, skip link, header, `<main id="main">`, footer with the disclaimer. |
| `globals.css` | all | Design tokens (section 5) for light and dark, the chart tokens `--viz-*`, map control styles. |
| `page.tsx` | `/` | Landing. Each side's card says what the web app does **today** (v0.1) and what comes later. |
| `signin/page.tsx` | `/signin/` | `SignInPanel`. |
| `studio/page.tsx` | `/studio/` | `StudioHome`. |
| `studio/new/page.tsx` | `/studio/new/` | `PlanWizard`. |
| `studio/project/page.tsx` | `/studio/project/?id=` | `ProjectDashboard`. |
| `lender/page.tsx` | `/lender/` | `LenderHome`. |
| `lender/file/page.tsx` | `/lender/file/?id=` | `LenderFileView`. |
| `about/page.tsx`, `not-found.tsx`, `icon.svg` | | About, 404, favicon. |

Page files are server components so they can export `metadata`; the interactive part is a client component.

### `lib/` (logic, no UI)
| File | Purpose |
|---|---|
| `config.ts`, `auth.tsx`, `sign-in.ts`, `location.ts`, `roles.ts`, `evidence.ts`, `sample-file.ts`, `options.ts`, `use-is-framed.ts`, `utils.ts` | Shell (unchanged): product name and disclaimer, session provider, sign-in links, query params, roles, evidence words, the landing sample, config labels, iframe check, `cn()`. |
| `api.ts` | Typed fetch wrapper: same-origin `/api`, cookies, `X-CSRF-Token` on every non-GET (refreshed once on a 403), `ApiError` with status, `detail` and Retry-After. `errorMessages(detail)` turns the server's error bodies (`{"detail": "...", "reasons": [...]}`, FastAPI's 422 list, `{message, reasons}`) into plain sentences. JSON includes `application/*+json` (the tract layer is `application/geo+json`). |
| `api-errors.ts` | `describeProblem(error, action)`: offline, signed out (401), not allowed (403), not found (404), conflict (409), input not accepted (413/422), too many requests (429, with the wait), busy or too long (503), server error. Each page shows the server's own reasons. |
| `use-api.ts` | `useApiResource(key, load, action)`: one request per key with abort, `status` (`idle`/`loading`/`ready`/`error`), `problem`, `refreshing` (the last data stays on screen while a new key loads), `reload`. A `null` key waits (for example, the path search waits for the simulation). |
| `studio-types.ts`, `studio-api.ts` | Types that mirror `src/uwcopilot/web/schemas_studio.py`, and the client for `/api/studio/*` (section 7). |
| `lender-types.ts`, `lender-api.ts` | Types that mirror `schemas_lender.py`, and the client for `/api/lender/*`. |
| `format.ts` | Display only, never arithmetic on results: dollars, compact dollars, numbers, shares (0-1) as percents, ratios, scores, `formatWithUnit` for the simulator's units, UTC dates, "2027-01" as "Jan 2027", ranges, `splitFactorReason` ("renter_households: ..." shown as "Renter households: ..."), `joinWords`. Missing values show "Not available". |
| `use-media-query.ts` | `matchMedia` through `useSyncExternalStore` (compact charts on phones). |

### `components/` (shell, unchanged in role)
`ui/*` (Radix-based primitives; Tabs, Slider and Progress are still unused), `evidence/evidence-chip.tsx`,
`evidence/figure.tsx`, `evidence/evidence-legend.tsx`, `landing/*`, `layout/*` (header, footer with the
disclaimer, SYNTHETIC tape, page intro), `session/*` (sign-in state, offline and error notices, dev login),
`workspace/never-decides-notice.tsx`, `workspace/planned-sections.tsx`, `workspace/workspace-skeleton.tsx`,
`about/system-status.tsx`. The old empty `workspace/studio-home.tsx` and `workspace/lender-home.tsx` were replaced
by the feature versions below.

### `components/` (added for the feature pages)
| File | Purpose |
|---|---|
| `evidence/data-status-chip.tsx` | Chip for the backend's data statuses: DATA, SOURCED, SYNTHETIC, DATA_X_ASSUMPTION (a data value times an assumed rate), and FOUNDER_INPUT shown as USER_STATED. Evidence statuses (VERIFIED, ASSUMPTION, ...) still use `EvidenceChip`. |
| `layout/results-disclaimer.tsx` | The disclaimer box at the top of every results page (text from the server's config). |
| `workspace/problem-notice.tsx` | Shows a `Problem` with its reasons, "Try again", and a sign-in link for 401. |
| `studio/simulation-label.tsx` | "simulated, based on stated assumptions" (sigma icon), on every simulated number block and chart. |
| `studio/metro-mode-badge.tsx` | Full data / Limited data / SYNTHETIC demo city. |
| `studio/reason-text.tsx` | One SiteIQ reason with its factor code as words. |
| `studio/studio-home.tsx` | Plan list (`GET /api/studio/projects`): city, business type, sites, auto-delete date, a note when the idea text was flagged; "Start a plan"; "Coming in a later version" cards. |
| `studio/wizard/draft.ts` | The wizard's draft state, its six steps, the per-step checks (server limits from `/api/studio/options`), and `toProjectInput` (empty budget is sent as `null`; the server then uses the business type's stated range as an assumption). |
| `studio/wizard/plan-wizard.tsx` | Step list, error summary (links to fields), review with "Edit" per row; focus moves to the step heading; POST `/api/studio/projects`. |
| `studio/wizard/choice-cards.tsx`, `amount-field.tsx` | Radio cards (city, business type) and the labeled number field (prefix/suffix, hint, error, chip). |
| `studio/wizard/sites-step.tsx` | Up to the server's site limit: map pins (map-only cities), "Pick a census tract from the list", "Suggest areas", or address fields for cities with street addresses. |
| `studio/map/site-map.tsx` | MapLibre with **no basemap**: a plain background, tract centers (gray), suggested areas (orange rings), numbered site pins. A click near a tract center snaps to it; elsewhere the pin goes where you clicked. The map is `aria-hidden`; the tract list is the keyboard path. Colors come from the CSS tokens and repaint when the color scheme changes. |
| `studio/map/tract-list.tsx` | A `<select>` of every tract, grouped by district, plus "Add as a site". |
| `studio/map/suggested-areas.tsx` | `POST /api/studio/areas/suggest`: rank, tract, score, coverage, the main reason, a weak factor, "Use this area"; tracts left out and why. |
| `studio/charts/chart-frame.tsx` | Figure with title, description, the simulation label, a legend, and "Show the numbers as a table" (`<details>` with a real `<table>` in a focusable, named scroll region, so a keyboard can scroll it). |
| `studio/charts/band-chart.tsx` | Revenue and cash: P10-P90 band (14% wash) + P50 line (2 px), zero line on the cash chart, crosshair tooltip. |
| `studio/charts/tornado-chart.tsx` | "What moves the result most": diverging bars around the base case, square at the baseline and rounded at the outer end, symmetric round ticks, one-line labels cut at a word (full text in the tooltip and table). |
| `studio/project/project-dashboard.tsx` | The results page (call order in its docstring). Opens on the plan's stored `selected_site_index`. "Apply this change" PUTs `{version, ...apply.project_fields, plan_change}` (the changed fields only, marked as a suggested change), then re-runs everything for `apply.site_index`; focus moves to the status line that says what happened. The plan's figures show who wrote each one: user stated, assumption (the assumed build-out is shown, not "a range"), suggested change (with the evidence a lender would need) or calculated. |
| `studio/project/plan-editor.tsx` | "Edit the numbers": budget, own cash, loan, term and rate after the plan exists (a plan the simulation refuses can be fixed instead of deleted). Sends only the changed fields with the plan version; a field typed here is the founder's own figure again. |
| `studio/project/site-panels.tsx` | Site cards (score, coverage badge, top factors, a weak factor), the selected site's factors (raw value, unit, area, status, score, weight, how it was measured, sources) and the demand estimate (P10/P50/P90, model estimate). |
| `studio/project/feasibility-panel.tsx` | Three gauges from the response's `headline` ("684 of 1,000 simulated futures ...", each with the simulation label), the target used by the path search, the charts (the cash chart says each future opens with the planned reserve minus the sampled build-out overrun, read from the inputs), break-even month, the loan as simulated (a rate left empty shows the prime rate's status plus an ASSUMPTION spread), every sampled input (6 shown, the rest behind "Show all"). |
| `studio/project/probability-gauge.tsx` | `role="meter"` bar with the target tick and the count of simulated futures. |
| `studio/project/path-panel.tsx` | Status (the engine's own message: what was tried, "not exhaustive", or stopped early), guardrail note, the three targets (ASSUMPTION, or USER_STATED once edited) with their sources, the bound on extra owner cash (the default shown, and a field for the founder's own answer), change-set cards (every figure that changes, including the balancing ones and the calculated reserve; outcome table; what a lender would need; startup-program rules still not met; explanation; Apply when `apply.supported`), the closest result, changes not tried, guardrails, notes. |
| `studio/project/target-editor.tsx` | "Edit the targets": the three shares of simulated futures (planning assumptions) as percents; sends only the shares that differ from the defaults; "Back to the default targets". The DSCR thresholds and the revenue stress (from the SYNTHETIC guideline) are not editable here, so the gauges above keep their meaning. |
| `studio/project/plan-actions.tsx` | "Download results (JSON)" (exactly what the page received) and "Delete plan" (confirm dialog; a 202 "pending" answer is shown as such). |
| `lender/lender-home.tsx` | The DEMO queue: line filter with the server's counts, debounced search, 25 rows then "Show more". |
| `lender/file-view.tsx` | One file (layout in section 7). |
| `lender/decision-panel.tsx`, `decision-rules.ts` | Your decision: four actions in a fixed order; each one is enabled or locked with the **server's** reasons (`action_checks`, the same check the server runs on submit); a rationale; an override reason from the server's list when the action differs from the suggestion; a 422 refusal shown with its reasons; the recorded decision with "Download the record (JSON)". |
| `lender/citations.tsx`, `citation-drawer.tsx` | Citation chips by kind (guideline section, calculation, tool, application fact, check); the drawer loads a guideline section or shows the file's evidence item, and gives focus back to the chip. |
| `lender/plain-markdown.tsx` | A safe renderer for guideline text and decision records: headings, lists (wrapped items continue on indented lines), pipe tables, `>` quotes, fenced blocks (line breaks kept), inline code and paragraphs, all as React text. No HTML is ever injected. |
| `lender/labels.ts`, `demo-banner.tsx` | Plain words for the agent's codes; the SYNTHETIC DEMO banner and tag. |

### `scripts/` and `mock-api/`
| File | Purpose |
|---|---|
| `run-next.mjs`, `check-contrast.mjs` | Shell. The contrast check now covers 27 text/background pairs per theme (section 5). |
| `copy-maplibre-worker.mjs` | Copies MapLibre's ES-module worker into `public/vendor/maplibre/` (gitignored) before `dev` and `build`; the map calls `setWorkerUrl` with that path. |
| `record_mock_fixtures.py` | Runs the real FastAPI app in-process (TestClient, dev login, throw-away store) and saves its answers to `mock-api/fixtures/` (section 8). |
| `mock-api/server.mjs` | Node HTTP server: serves `out/` and replays the fixtures; keeps sessions, plans and demo decisions in memory. |
| `screenshots.py` | Offline screenshots of `out/`; with `--live-url`, DEV LOGIN on a running server. Live mode now waits for "Your plans" and the "Demo queue". |
| `screenshots_mock.py` | Every feature page on the mock API, both widths and schemes; fails on sideways overflow, a browser console error, or a missing element. `--small` saves 256-color PNGs. |

## 5. Visual language

The design is a paper ledger: warm paper, ink text, one ink-blue accent, and the six evidence statuses as stamps
(label + icon, never color alone). SYNTHETIC data wears a hatched "specimen tape". Inter for UI and Source Serif 4
for headings, self-hosted. Dark mode follows the operating system.

**Charts.** One hue per job: `--viz-1` (blue: the median line, "input at its high end", site pins) and `--viz-2`
(orange: "input at its low end", suggested-area rings). Bands are a 14% wash of `--viz-1`. Grid and axes use
`--viz-grid` and `--viz-axis`; all text stays in ink tokens. Every chart has a legend, a tooltip and a table twin.
The pair was checked with the dataviz skill's palette validator (`validate_palette.js`, 2026-10-13):
light `#3d55c9,#e0622f` on `#fffefb` and dark `#6f84f0,#d95926` on `#1a1e26` both "ALL CHECKS PASS"; worst
color-vision-deficiency separation ΔE 27.5 (light) and 28.4 (dark), normal-vision ΔE 35.2 and 30.1.

**Contrast**: `npm run check:contrast` (2026-10-13) passes all 27 pairs in both themes. The lowest ratios are
5.48:1 in light (`--ink-muted` on `--paper-sunken`) and 5.34:1 in dark (`--accent-ink` on `--viz-1`, the site
numbers on pins). New pairs cover the simulation label, "Watch" notes, screening text, "not available" text and
pin numbers.

## 6. States every page handles

| State | What the user sees |
|---|---|
| loading | Skeletons where the content will go (site cards, results blocks, queue rows). |
| offline | "The server is not reachable right now" with Try again (also when a sleeping Space answers with HTML). |
| signed out | Sign-in prompt that returns to the same page (`?next=`). A 401 in the middle of a page says "You are signed out" with a sign-in link. |
| not found / not allowed | "Not found" or "Not allowed for your account", with the server's reason as sent. |
| busy (429) | "Too many requests right now", with the server's Retry-After wait. |
| too long (503) | "The server is busy or this took too long. Nothing was lost." with Try again (screenshot `studio-results-timeout-*`). |
| input not accepted (422/413) | The server's reasons, as a list. A refused decision shows its reasons next to the buttons. |
| conflict (409) | "Applying the change is not available", with the server's reason (the plan version changed). |
| partial results | Each block has its own state: a failed simulation does not hide the site scores. |
| limited city | The metro badge says "Limited data"; factors without data are UNKNOWN and left out of the score, never filled in. |

## 7. What the pages read from the API (the contract)

All calls go through `request()` (cookies, CSRF). Shapes are the types in `lib/studio-types.ts` and
`lib/lender-types.ts`, which mirror the backend's Pydantic schemas.

- `GET /api/studio/options`: metros (mode, site input, tract count, prime rate), business types (build-out range
  with its status), levers, `default_target`, `limits`, disclaimer and labels.
- `GET /api/studio/projects`, `POST /api/studio/projects`, `GET|PUT|DELETE /api/studio/projects/{id}` (PUT carries
  `version`; DELETE answers 200 or 202 pending).
- `POST /api/studio/projects/{id}/sites/score`, `/feasibility` `{site_index}`, `/path-to-approval`
  `{site_index, targets|null}`.
- `POST /api/studio/areas/suggest` `{metro_id, business_type_id}`; `GET /api/studio/metros/{id}/tracts.geojson`.
- `GET /api/lender/queue?line=&q=`, `GET /api/lender/files/{id}`, `GET /api/lender/sections/{id}`,
  `POST /api/lender/files/{id}/decision` `{action, rationale, override_reason?}`, `GET /api/lender/files/{id}/record`.

The lender file view shows, top to bottom: header (file id, requested amount, review queue, required role), the
disclaimer, the SYNTHETIC DEMO banner, "The policy suggests: X. You decide.", the instruction screen (injection
hits, or "no instruction-like text was found"), findings with citations, the memo (template memo; claim check),
calculations, your decision, sanctions screening (lists searched with their download dates, possible matches only),
tools the copilot used, rules not evaluated, and the policy and graph versions.

## 8. The mock API (pages without a backend)

`make web-mock-fixtures` runs the real app in-process with dev login and a throw-away store, signs in as a SYNTHETIC
demo founder, and saves what the routes answer: config, options, the SYNTHETIC metro's tracts and suggested areas,
a demo laundromat plan with three candidate sites (its scores, simulation and path search per site), the path
search with edited targets (tried in a fixed order until one set finds a plan change and one is already met; every
set tried is saved), "Apply this change" and the version-2 results, the lender queue, three demo files, their cited
sections, a refused decision (422) and a recorded one. 27 files, all computed by the project's code on SYNTHETIC
inputs; the plan inputs are the demo founder's stated inputs. Re-recording changes the plan id (and so the
simulation seed), so numbers in the fixtures and screenshots change between recordings.

`mock-api/server.mjs` replays them. It is **not** the real API: every plan gets the demo plan's recorded results
(relabeled with its id and version); a wizard-created plan echoes its inputs and marks derived fields "mock";
only the recorded apply and the recorded target edits are replayed (other target edits get a 501); a plan id
starting with `timeout` answers 503; only the recorded decision can be replayed.

## 9. Dependencies, versions and bundle size

Pinned exactly; versions are unchanged from the shell (next 16.3.6, react 19.3.0, typescript 5.9.3, tailwindcss
4.3.3, radix-ui 1.6.7, lucide-react 1.48.0, recharts 3.10.1, maplibre-gl 6.11.2, eslint 9.39.5; see
`package.json`). No package was added for the feature pages.

Recharts and MapLibre are imported only inside components loaded with `next/dynamic` (`ssr: false`). Measured on
the 2026-10-13 build by listing the scripts each page's HTML loads and searching them for `recharts-wrapper` and
`maplibregl`: neither library is in the initial scripts of any page. Initial scripts (raw / gzip): landing 700 KB /
221 KB, `/studio/project/` 781 KB / 244 KB, `/lender/file/` 741 KB / 233 KB. The Recharts chunk is 370 KB / 105 KB
and the MapLibre chunk 1,007 KB / 271 KB, fetched only when a chart or map is drawn; MapLibre's worker files in
`out/vendor/` total 532 KB. `out/` is 8.0 MB in all (`du -sh`), 4.0 MB of it font files (all subsets; browsers
download only the ones a page uses).

## 10. Screenshots and what the runs checked

- `docs/web/screenshots/` (30 PNGs, `make web-screenshots` with a live dev server, 2026-10-13): the shell pages
  offline, the menu and drawer, and `*-devserver-*` signed in through DEV LOGIN. Printed "no horizontal overflow
  and no browser console errors on the live server".
- `docs/web/screenshots/mock/` (54 PNGs, 6.0 MB, 256 colors, `make web-screenshots-mock`, 2026-10-13): plans, the
  wizard (city, money errors, sites with map and suggestions, review), results (pages taller than 8,000 px are
  saved in parts), the path section after editing the targets, the 503 state, the queue, a file, the citation
  drawer, a recorded decision, and a signed-out file, each at 1280 and 375 px in light and dark. Printed "no
  horizontal overflow and no browser console errors (mock API)".
- A separate manual check (a scratch script, not in the repo) ran the real app with dev login on the built export,
  created a SYNTHETIC plan through the API, and opened `/studio/project/` (map, charts, path), `/lender/file/` and
  `/lender/` at 1280 and 375 px: no browser console errors, so no CSP violation from the map worker or the charts.

## 11. Adding a feature page

1. Add the types (mirroring the backend schema) and a client function that uses `request` (`lib/*-api.ts`).
2. Create `app/<route>/page.tsx` (server component with `metadata`) rendering a client component.
3. Load data with `useApiResource`; show `ProblemNotice` for its `problem` and a skeleton while loading.
4. Show every number with its status chip from the API; label simulated values with `SimulationLabel`; wrap
   SYNTHETIC data in `SyntheticBanner`; put `ResultsDisclaimer` on results pages.
5. Record ids go in the query string. Record the new answers in `record_mock_fixtures.py`, route them in
   `mock-api/server.mjs`, add the page to `screenshots_mock.py`, and look at the 375 px images.

## 12. Honest limits and open items

- Lighthouse and axe-core results of the LOCAL container (dev login on, not the Space) are in
  `results/web_metrics.json`, written by `scripts/web_metrics.py` (`make web-metrics`); `docs/deployment.md`,
  section 4b, copies them. They are not repeated here.
- Hugging Face sign-in has not been tried end to end (it needs the Space). Only DEV LOGIN was exercised.
- Real-metro sites typed as addresses depend on the server's geocoder; the screenshots use the SYNTHETIC metro's
  tracts only.
- The planning targets are editable, but other assumption ranges (rent, wages, capture rate) are not editable from
  the page yet; the results list each one with its range and status.
- No frontend unit tests: the checks are lint, types, contrast, the build, and the screenshot runs. CI does not
  run the frontend checks yet.
- `docs/web/screenshots/` is 13 MB of PNGs (`du -sh`), 6.0 MB of it in `mock/`.

## 13. Questions you may be asked (and short answers)

1. **Why a static export instead of a Next.js server?** One container, one origin, no Node runtime in production;
   cookies, CSRF and CSP stay in FastAPI.
2. **Does the frontend compute any result?** No. It formats what the API returns. Probabilities, scores, ranges and
   the decision checks all come from the server; the page only adds labels.
3. **How do the lender's buttons know what is allowed?** The file comes with `action_checks`, made by the same
   function the server runs on submit. The page locks an action and shows the server's reasons; the server checks
   again when the decision is sent, and a refusal (422) is shown with its reasons.
4. **Why is there no street map?** A tile server would add a third-party origin, a license and tracking. The map
   draws only our own SYNTHETIC or census GeoJSON on a plain background, and a list gives keyboard users the same
   choices.
5. **How is a chart made accessible?** Legend with words, a tooltip, and a table twin with every number; the
   palette passed a color-vision check; meaning never depends on color alone.
6. **What is the mock API, and is it honest?** It replays answers recorded from the real app on SYNTHETIC inputs, so
   no number is typed by hand. Its limits (same results for every plan, only recorded edits) are written at the top
   of `server.mjs` and in section 8.
7. **What happens when the simulation takes too long?** The server answers 503; the page says "busy or took too
   long, nothing was lost" and offers Try again. The site scores above stay on screen.
8. **Why can the founder edit only the shares in the targets?** The shares are planning assumptions. The DSCR
   thresholds and the stress come from the SYNTHETIC guideline; changing them here would make the gauges above,
   which use those thresholds, mean something else.
9. **How do you keep Recharts and MapLibre off the landing page?** `next/dynamic` with `ssr: false`, and a grep of
   each page's initial scripts after the build (section 9).
10. **Why does "Apply this change" send a version?** Optimistic locking: if the plan changed in another tab the
    server answers 409, and nothing is overwritten.

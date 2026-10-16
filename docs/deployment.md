# Deployment: one container, a Hugging Face Docker Space

> **Status: not deployed yet.** The owners created the Space `astra6/FundProof` and added the `HF_TOKEN` repository
> secret on 2026-10-13; the first deploy runs when an owner pushes a `v*` tag or starts the workflow by hand
> (section 5).
> There is no public URL. This page gets the URL only after a real deploy has run.

Fundproof (working name; rename in `src/uwcopilot/web/settings.py` and `frontend/lib/config.ts`) is deployed as a
**prototype** on a Hugging Face **Docker Space**. Nothing in this repository pays for anything: no payment code, no
paid hardware or storage is requested (the deploy script calls `create_repo` without `space_hardware` or
`space_storage`, so the Space gets whatever the account's default is; check it in the Space settings).

Measured numbers on this page are copied from `results/deployment_metrics.json` (section 4, written by
`scripts/smoke_deploy.py`) and `results/web_metrics.json` (section 4b, written by `scripts/web_metrics.py`); each file
says which machine and which target each number comes from. Every other number is a design setting (a time limit, a
wait) or a dated development check, and is labelled as such where it appears.

## 1. What runs where

```
 Browser
   |  https://huggingface.co/spaces/<owner>/<space>  (the app inside an iframe)
   |  or https://<owner>-<space>.hf.space            (the app on its own, "open in a new tab")
   v
 Hugging Face Space front end (TLS ends here; how many proxies append X-Forwarded-For is NOT verified yet)
   |  plain HTTP, port 7860 (README metadata app_port: 7860)
   v
 Docker container built from ./Dockerfile, running as uid 1000
   uvicorn uwcopilot.web.app:app   (ONE process: rate limits, the compute cache and a generated session secret
                                    are per process)
     |-- /api/*            FastAPI: /api/health, /api/config, /api/me, /api/auth/*,
     |      |                       /api/studio/* (Founder Studio), /api/lender/* (Lender Desk DEMO)
     |      |-- sign-in -> huggingface.co OpenID Connect (OAUTH_* variables injected because hf_oauth: true)
     |      |-- request bodies over 64 KiB -> 413; validation answers never echo the input
     |      |-- heavy work (simulation, path search, agent run) -> worker threads, 60 s per request, then 503;
     |      |                  at most 3 jobs per user running or queued
     |      |-- lender demo -> the LangGraph agent with the template memo (no language model), the guideline
     |      |                  index in /app/qdrant_storage, OFAC lists in /app/data/raw/ofac (UWC_SCREENING_LISTS=ofac)
     |      |-- address sites -> Census Geocoder (keyless, real metros only; needs the Space's internet access;
     |      |                  its own 2-thread pool, one lookup batch per user, 10 s timeout, no cache)
     |      '-- Store   -> /app/data on the container disk (lost on restart), or, with UWC_STORE=hf_dataset,
     |                     an encrypted mirror in a PRIVATE Hugging Face dataset (Hub commits never block a
     |                     request: they hold only the Hub lock; store calls run in worker threads)
     '-- everything else  the Next.js static export in /app/frontend/out (served by uwcopilot.web.static)
```

What v0.1 can do in the image (section 4b has the measurements):

- **Founder Studio** works end to end on the SYNTHETIC demo metro: wizard, site scores, the 1,000-path simulation,
  the path to approval and "Apply this change". The five real metros have data only when the image was built with
  the `CENSUS_API_KEY` build secret (section 5b): the build then puts their Studio data snapshots
  (`data/studio/snapshots/`) into the image. Without it, every real metro runs in **limited mode** ("no data
  snapshot"): real-metro sites can be added by address, their factors show as UNKNOWN, and the simulation, the path
  search and area suggestions answer 409 "limited mode".
- **Lender Desk DEMO** shows the SYNTHETIC structured applications of `eval/applications` and
  `eval/startup_applications` (credit, insurance and startup credit). Each file
  runs the agent graph to the human checkpoint with the deterministic template memo, real guideline search and OFAC
  screening; a person records a DEMO decision through `agent.hitl.validate_decision`.

The deploy pipeline (`.github/workflows/deploy-space.yml`):

```
 "Run workflow" in GitHub Actions, or a pushed tag v*
   '-- job verify: ruff, pytest (offline suite), frontend lint + typecheck + contrast + build,
   |              docker build of ./Dockerfile, run it, scripts/smoke_deploy.py --target local-container
   '-- job deploy (only if verify passed):
          secret HF_TOKEN set?  no -> a notice "Deploy skipped", nothing is pushed (Space id: secret or variable
          HF_SPACE_ID, else the default astra6/FundProof)
          yes -> scripts/deploy_space.py bundle   copy the allow-listed files, stamp the commit into the Dockerfile
              -> scripts/deploy_space.py push     create_repo(space, docker, exist_ok, public), set a fresh
                                                  UWC_SESSION_SECRET Space secret, upload_folder
              -> (the Space builds the same Dockerfile itself, with the Space's build secrets if set: section 5b)
              -> scripts/deploy_space.py wait     until https://<host>/api/health names this commit (at most 3600 s)
              -> scripts/smoke_deploy.py --target hf-space   results kept as the workflow artifact "space-smoke"
```

## 2. Files

| File | What it does |
|---|---|
| `Dockerfile` | Five stages: `node:22-slim` runs `npm ci` + `npm run build` (frontend/out); `python:3.11-slim` + uv runs `uv sync --locked --no-dev` into /app/.venv; `assets` downloads the OFAC SDN + alternate-name lists (required, two retries) and the public regulatory documents (optional), builds the Qdrant guideline index with `scripts/build_index.py` and runs one real guideline search, which also downloads the reranker (the build fails if it finds nothing); `studio-snapshots` runs `scripts/build_studio_snapshots.py` with the optional build secrets `CENSUS_API_KEY` and `FRED_API_KEY` (section 5b); the runtime stage copies only the virtualenv, `src/`, `config/`, `corpus/`, `frontend/out`, the SYNTHETIC demo applications, `results/kyc_metrics.json` (the screening threshold), the assets (lists, `checksums.jsonl`, fastembed models, index, index manifest) and `data/studio/snapshots/` (empty without the Census secret; read-only), compiles `src/` to bytecode, sets `UWC_SCREENING_LISTS=ofac`, and runs as `user` (uid 1000; only `data/` and `qdrant_storage/` are writable). Base images are pinned by digest. `HEALTHCHECK` calls `/api/health`. `CMD` is uvicorn on 0.0.0.0:7860 with `--no-access-log` (the app logs its own redacted line) and `--no-proxy-headers` (client IPs come only from the app's `UWC_TRUSTED_PROXY_HOPS` rule). |
| `.dockerignore` | An allow-list: everything is excluded, then only `pyproject.toml`, `uv.lock`, `README.md`, `src/`, `config/`, `corpus/`, `frontend/`, `eval/applications/`, `eval/startup_applications/`, `results/kyc_metrics.json`, `scripts/download_data.py`, `scripts/build_index.py` and `scripts/build_studio_snapshots.py` come back, minus `node_modules`, `.next`, `out`, the mock API and screenshot helpers, caches and any `.env`. So `.env`, `data/`, the other results, `.venv` and the git history never reach the build. |
| `deploy/space/README.md` | The Space card. Its YAML header is the Space configuration: `sdk: docker`, `app_port: 7860`, `hf_oauth: true` (Sign in with Hugging Face), title, colors, `short_description`. No `license` key (see Owner decisions). The body has the disclaimer and the SYNTHETIC note. It becomes `README.md` in the Space. |
| `scripts/build_studio_snapshots.py` | Runs inside the `studio-snapshots` build step (section 5b): reads the build secrets from `/run/secrets` into the snapshot builder's environment only, runs it with a time limit, keeps only complete snapshot folders whose tract and ACS steps succeeded, deletes the HTTP cache, searches the kept files (and the members of zip files) and its own printed lines for the key values, and prints the names of failed steps. Never fails the build. |
| `scripts/deploy_space.py` | `bundle`: copies the allow-list (including the three build-time scripts) into an empty folder, puts the card in as `README.md`, writes the commit into `ARG UWC_GIT_SHA=`. `push`: creates the Space if missing and replaces its files (`delete_patterns="*"`; the Hub keeps `.gitattributes`). `wait`: waits (default 3600 s, the workflow's value) until the Space's `/api/health` names the pushed commit, failing early on a build or runtime error. Reads `HF_TOKEN` and `HF_SPACE_ID` from the environment and never prints them. |
| `scripts/smoke_deploy.py` | Checks a running deployment and times it (see section 4). Writes `results/deployment_metrics.json`, one entry per target, each with a provenance block. |
| `.github/workflows/deploy-space.yml` | The pipeline above. Manual or on tags `v*`; one deploy at a time. The deploy job waits up to 3600 s for the Space build and has a 90-minute limit (design choices; section 5b). Every action is pinned to a full commit SHA (with its release in a comment, resolved 2026-10-13), uv is pinned to the Dockerfile's version (0.11.32), and checkouts do not keep the GitHub token (`persist-credentials: false`): the deploy job runs code with `HF_TOKEN` in its environment, so a moved tag must not be able to change that code. `ci.yml` uses the same pins. |
| `scripts/web_metrics.py` | Drives a LOCAL server or container that runs WITH the dev login in headless Chromium (Playwright): the signed-out pages, the founder flow on the SYNTHETIC metro (wizard, results, edited targets, "Apply this change", delete), one Lender Desk DEMO decision, the error paths, a keyboard-only walk (`keyboard_only`) and 10 page states at 375 x 812 px (`small_screen_375`); axe-core scans of every page state in light and dark, per viewport; Lighthouse (mobile and desktop presets) on the landing page, the results page and the lender file. Before the browser starts it checks that the run is reproducible (section 4b) and records the server's Studio metro list (`server.studio_metros`). Writes `results/web_metrics.json` (one entry per target, with a provenance block) and screenshots to `docs/web/screenshots/e2e/`. |
| `tools/web-metrics/` | `package.json` + `package-lock.json` pinning the Node tools the script uses: Lighthouse 13.5.0 (released 2026-09-18) and axe-core 4.13.0 (2026-08-05). Not part of the app or the image. |
| `scripts/optimize_pngs.py` | Re-encodes PNGs losslessly (oxipng when installed, else Pillow) and keeps a file only when it is smaller and decodes to the same pixels. |
| `Makefile` | `docker-build`, `docker-run`, `docker-smoke`, `space-bundle`; `docker-build-stamped` (stamped with HEAD, `-dirty` for an uncommitted tree), `docker-build-archive` (built from `git archive HEAD`) and `docker-build-bundle` (built from the Space bundle, optionally with the Studio build secrets, never reusing a cached snapshot step), each with the build-method label `web-metrics` checks; `web-metrics-tools`, `web-metrics`. |
| `tests/web/test_deploy_space.py`, `tests/web/test_build_studio_snapshots.py`, `tests/web/test_smoke_deploy.py`, `tests/web/test_web_metrics_script.py`, `tests/web/test_optimize_pngs.py` | Offline tests: a fake Hub, `httpx.MockTransport`, a fake clock, a fake `docker`, hand-written axe and Lighthouse results, generated PNGs. |

## 3. Run it locally

```bash
make docker-build     # docker build -t fundproof:dev .  (no build label: fine to run, refused by web-metrics)
make docker-run       # http://127.0.0.1:7860, dev login OFF, data lost when the container stops
make docker-smoke     # start the image, smoke-test it, stop it -> results/deployment_metrics.json (runs.local-container)
make space-bundle     # write what the workflow would upload to build/space, to inspect it
make docker-build-bundle                         # build DOCKER_IMAGE from that bundle, as the Space does (no Studio data)
make docker-build-bundle STUDIO_BUILD_SECRETS=1  # ... with CENSUS_API_KEY and FRED_API_KEY from your shell as build
                                                 #     secrets: real-metro snapshots in the image (section 5b)
make docker-build-archive  # build DOCKER_IMAGE from `git archive HEAD` (committed files only), stamped and labelled
make web-metrics-tools  # once: npm ci in tools/web-metrics (Lighthouse + axe-core)
make web-metrics      # start the image WITH the dev login on 127.0.0.1:7862, run scripts/web_metrics.py, stop it
                      # -> results/web_metrics.json (runs.local-container) + docs/web/screenshots/e2e/
uv run --with pyoxipng==9.1.1 python scripts/optimize_pngs.py docs/web/screenshots   # lossless, smaller PNGs
```

`make web-metrics` measures only an image that says how it was built: build it with `make docker-build-archive` (or
`make docker-build-stamped` / `make docker-build-bundle` on a clean tree). A plain `make docker-build` image has no
build label, and `scripts/web_metrics.py` refuses it (section 4b). `make docker-build-bundle` always rebuilds the
`studio-snapshots` stage (`--no-cache-filter studio-snapshots`): BuildKit leaves secrets out of its cache key, so
otherwise a build with `STUDIO_BUILD_SECRETS=1` right after one without them (same commit) reused the "skipped" step.

The image needs network access while it builds (OFAC lists, regulatory documents, fastembed models from
huggingface.co). At runtime it needs the internet only for Hugging Face sign-in, the Census Geocoder (real-metro
addresses) and, with `UWC_STORE=hf_dataset`, the dataset repo: the containers measured in sections 4 and 4b could not
resolve any internet host (checked from inside the image: `huggingface.co` and `geocoding.geo.census.gov` gave "Name or
service not known"), and the Studio and Lender Desk flows still ran.

Behind a TLS-inspecting proxy (the machine these numbers were measured on has one), pass the proxy's CA bundle as
an optional BuildKit secret: `make docker-build EXTRA_CA_CERTS=/path/to/ca-bundle.crt`. It is mounted only while
`npm ci`, `pip install uv`, `uv sync` and the asset stage download and is not stored in the image. Without it
(GitHub Actions, Hugging Face) the base images' normal certificate stores are used. On the machine these numbers were
measured on, that proxy listens on the host's loopback, which build steps cannot reach on Docker's default network,
so the image was built with:

```bash
docker build --network=host --build-arg HTTPS_PROXY="$HTTPS_PROXY" --build-arg HTTP_PROXY="$HTTP_PROXY" \
    --build-arg NO_PROXY="$NO_PROXY" --secret id=extra_ca_certs,src=/root/.ccr/ca-bundle.crt -t fundproof:dev .
```

(Docker's predefined proxy build arguments are not stored in the image or its history.)

## 4. Measured: local container (NOT the Space)

From `results/deployment_metrics.json`, entry `runs.local-container`, measured 2026-10-14T00:25:57Z by
`scripts/smoke_deploy.py --url http://127.0.0.1:7860 --target local-container --docker-image fundproof:dev --container
fundproof-smoke` (run by `make docker-smoke`). **This is the image of commit `271ca4d` running on the build machine,
reached over 127.0.0.1, with the dev login OFF: no internet, no Space proxy, not Space hardware.** It is the app of
`39da937` (between the two commits, the only file of the image that changed is
`src/uwcopilot/governance/freeze_proof.py`, which the web app does not import), but NOT exactly the v0.1.0 deploy
target `aa2260b`: that commit adds the "use a made-up plan" notice
(`frontend/components/studio/prototype-data-notice.tsx` on the Studio home and new-plan pages), so those pages as
deployed are **not measured**. The image was built from commit `271ca4d` exactly (`git archive 271ca4d | docker build
... --build-arg UWC_GIT_SHA=271ca4d... -`, the proxy flags of section 3, so `/api/health` names that commit); the
checkout itself had uncommitted result files while measuring (`git_dirty: true` in the provenance), which are not part
of the image. A 4-CPU machine shared with other jobs (load average 3.41 at the start of the run).

| What | Value | How it was measured |
|---|---|---|
| Image size on disk | 2.57GB | `docker image ls`, DISK USAGE column (2572087031 bytes from `docker image inspect`) |
| Image content size (compressed) | 659MB | `docker image ls`, CONTENT SIZE column |
| Container start to first healthy `/api/health` | 2.3 s | Docker's `State.StartedAt` to the first 200, polling every 0.1 s |
| First request to `/api/config` | 131.06 ms | the first call loads the Studio metro and business-type YAML files |
| All paths, 100 requests after the first | p50 1.93 ms, p95 5.73 ms | 20 sequential GETs each to `/api/health`, `/`, `/studio`, `/api/config`, `/api/me` |
| Checks | 9 of 9 passed | see the list below |

The checks: `/api/health` is ok; `/` and `/studio` are HTML with a Content-Security-Policy and `nosniff`;
`/api/config` has the product name, `payments_enabled: false` and `dev_auth: false`; `/api/me` is signed out; an
unknown `/api` path is a JSON 404; no request was rate limited or failed; the container runs the measured image.

These are smoke-test timings from one client sending one request at a time, not a load test, and they say nothing
about the Space: its hardware, network and cold start (including waking from sleep) are different and are
**not measured** until a real deploy runs (the deploy job then measures them and keeps them as the
`space-smoke` artifact). The previous shell-only image measured 2.16GB on disk and 1.104 s to healthy (this file's
earlier version, commit `017667d`); v0.1 adds the guideline index, the fastembed models, the OFAC lists and the
feature routers. Section 4b has the browser measurements of the same image.

## 4b. Measured: browser flows, accessibility and Lighthouse (local container WITH the dev login)

From `results/web_metrics.json`, entry `runs.local-container`, measured 2026-10-14T00:48:23Z by `make web-metrics`
(`scripts/web_metrics.py` against the same image, id `sha256:20b3b04d9a69...`, started with `UWC_DEV_AUTH=1` on
127.0.0.1:7862). **The dev login is on only because a script cannot sign in with Hugging Face; the Space refuses it,
so none of this was measured on the Space.** Like section 4, this is the image of `271ca4d`, without `aa2260b`'s
notice. The entry's `reproducibility` block says `reproducible: true` under the rule the script had then: the server
named commit `271ca4d`, not stamped "-dirty". The script's rule is stricter now. Before the browser starts it also
requires the image's build-method label (`org.fundproof.build.method`, set by `make docker-build-stamped`, `make
docker-build-archive`, or `make docker-build-bundle` on a clean tree), the server's stamp to equal the image's own
`UWC_GIT_SHA` (and, for `git-archive`, its commit label), and a `--container` that runs the `--docker-image`; a
`--target local-server` run has no image and is never reproducible. Otherwise it refuses to write results unless
`--allow-unreproducible` marks them so. The `271ca4d` image has no label, so **this run would now be refused**: re-run
`make web-metrics` on a labelled v0.1.1 image before quoting any v0.1.1 number. Since then the script also runs the
`keyboard_only` and `small_screen_375` flows, records every axe scan's viewport (`axe.viewports` and
`totals_by_viewport` replace `axe.viewport`) and records `server.studio_metros`; none of that is in the committed
results file yet: **not measured**. Same machine as section 4, load average 3.43 at the start. Headless Chromium
141.0.7390.37 (Playwright 1.56.0), axe-core 4.13.0, Lighthouse 13.5.0.

End-to-end flows, all passed, with no unexpected console error or failed API answer (the expected ones are the 404s
of the error paths):

| Flow | Steps | What it shows |
|---|---|---|
| signed_out | 3 of 3 | public pages load; a signed-in page asks to sign in with a `?next=` link; an unknown page is the 404 page |
| founder_synthetic_plan | 10 of 10 | dev login form, the six wizard steps on the SYNTHETIC metro (one suggested area, one tract from the list), results page with the disclaimer, the "simulated, based on stated assumptions" label and the SYNTHETIC label, every chart table opened; with the default targets the path search answered `no_path_within_bounds`; with edited targets `paths_found`, and "Apply this change" saved version 2 and re-ran |
| lender_demo_decision | 3 of 3 | queue, file `APP-C-001` (template memo, memo check passed, guideline search `ok`, OFAC `sdn.csv` and `alt.csv` with snapshot date 2026-10-14, screening `clear`), a DEMO `approve` by the mapped `underwriter` role, recorded as `approved_by_human` |
| error_paths | 2 of 2 | an unknown plan id gets the API's 404 and the page says "Not found"; the wizard refuses to continue without a city |
| founder_delete_plan | 1 of 1 | "Delete plan" -> DELETE 200 -> the plan's page then says "Not found" |

Timings inside those flows (one run, milliseconds from the click; the machine was busy with other jobs):

| What | Value |
|---|---|
| "Create plan" click to the plan answer / site scores / simulation / path search answers | 80.6 / 189.6 / 531.8 / 916.5 ms |
| "Create plan" click to the path-to-approval result on screen | 1099.2 ms |
| "Apply this change": PUT answer / re-run path result | 86.8 / 489.1 ms |
| First lender file after the container started (agent run, retriever and matcher load) | 12953.1 ms to the API answer |
| Recording the DEMO decision | 302.0 ms |

axe-core: 32 scans (16 page states x light and dark, at 1280 px, including the results page with every chart table
open and the lender file with the decision record open): **0 critical and 0 serious violations, and no violation of
any impact**. 20 scans have a "serious" item axe could not decide (`incomplete`), which needs a person to review.
Zero violations is what automated checks can find, not a statement of WCAG conformance.

Lighthouse, median of 3 runs per page and preset (scores as Lighthouse shows them, 0-100):

| Page | Preset | Performance (3 runs) | Accessibility | Best practices | LCP (median) | TBT (median) | CLS (median) |
|---|---|---|---|---|---|---|---|
| Landing `/` | mobile | 76 (75, 81, 76) | 100 | 100 | 5740 ms | 94 ms | 0.001 |
| Landing `/` | desktop | 98 (98, 98, 98) | 100 | 100 | 1153 ms | 0 ms | 0.003 |
| Studio results | mobile | 44 (47, 44, 42) | 100 | 100 | 7600 ms | 1602 ms | 0.112 |
| Studio results | desktop | 86 (88, 86, 86) | 100 | 100 | 1537 ms | 134 ms | 0.139 |
| Lender file | mobile | 77 (80, 77, 69) | 100 | 100 | 4780 ms | 307 ms | 0.001 |
| Lender file | desktop | 93 (93, 95, 93) | 100 | 100 | 1114 ms | 0 ms | 0.139 |

"mobile" is Lighthouse's default configuration (simulated throttling of a mid-range phone); "desktop" is
`--preset=desktop`. The HTTP cache was cleared before each run; the session cookie was kept. The results page is the
slow one on mobile (long main-thread work while the charts and the map render); the results page shifts layout on
mobile and desktop, and the lender file on desktop (CLS above 0.1); both are open frontend issues. LCP, TBT and CLS
values are rounded here for reading; the results file has the exact values. Screenshots of the run:
`docs/web/screenshots/e2e/`.

**Why the image is large.** The runtime installs the project's full dependency set from `uv.lock` (`uv sync
--locked --no-dev`), as the build spec says: MLflow, LightGBM, SHAP, SciPy, pandas, pyarrow, onnxruntime and others.
The v0.1 routers import the agent (LangGraph), the Studio engine and the retrieval stack (fastembed, onnxruntime,
qdrant-client), but still not everything (MLflow, SHAP and the document-intelligence stack are unused by the web
app); the image also carries the fastembed models and the guideline index. Making the image smaller needs a `web`
dependency group or extra in `pyproject.toml`; that is an owners' decision (below) because it changes the shared
dependency file.

## 5. Deploy to the Space

One-time setup (an owner does this; nothing here can do it for you):

1. **Create the Space** (done by the owners: `astra6/FundProof`, Docker SDK). On huggingface.co: New Space, SDK
   "Docker", blank template, under the account that will own it. The app refuses to start in a Space without
   `UWC_SESSION_SECRET`; `scripts/deploy_space.py push` sets that Space secret to a fresh random value on every
   deploy (so each deploy signs everyone out; pass `--keep-session-secret` if the owners manage it themselves).
   Note: `create_repo(private=False)` does not change an existing Space, so a private Space stays private.
2. **Create a Hugging Face access token** with write access that can update that Space (a fine-grained token
   limited to it is the safest choice).
3. **Add the GitHub repository secret** `HF_TOKEN` (GitHub: Settings -> Secrets and variables -> Actions); done
   by the owners on 2026-10-13. The Space id defaults to `astra6/FundProof`; set a repository secret or variable
   `HF_SPACE_ID` (`owner/name`) only to deploy somewhere else.
4. **Run the workflow**: Actions -> "Deploy to Hugging Face Space" -> Run workflow (pick a branch or tag), or push a
   tag: `git tag v0.2.0 && git push origin v0.2.0`.

Every run after that replaces the Space's files with the bundle (edits made directly in the Space repo are lost)
and the Space rebuilds the Dockerfile. To roll back, run the workflow from an older tag.

## 5b. Studio data: real-metro snapshots built into the image

The Founder Studio's five real metros need a data snapshot each (Census ACS tracts and County Business Patterns,
BLS QCEW wages, the FRED prime rate, FHWA HPMS roads, Overture places, LEHD LODES jobs; sources and terms in
`docs/studio/data_sources.md`). The image builds them while it builds, in stage `studio-snapshots`, but only when
the build gets the Census key:

1. **To turn it on** (an owner, once): in the Space settings, "Variables and secrets", add a **secret** (not a
   variable) named `CENSUS_API_KEY` with a free Census Data API key, and optionally a secret `FRED_API_KEY` (without
   it the keyless FRED CSV download gives the prime rate). Hugging Face passes Space *variables* to the build as
   build arguments (Docker Spaces docs, "Variables / Buildtime": source `docs/hub/spaces-sdks-docker.md` of
   github.com/huggingface/hub-docs, read 2026-10-14), which the Dockerfile never declares for keys; a key added as a
   variable is not used by the build.
   The snapshot builder reads no other key: the BLS, HUD and OpenRouteService keys are not build secrets.
2. **Then rebuild**: deploy a new commit (the stage re-runs for every commit, because it uses the stamped
   `UWC_GIT_SHA`), or use "Factory rebuild" in the Space settings. BuildKit leaves secrets out of its cache key
   (checked locally on 2026-10-14: a rebuild with the same arguments reused the result of a build that had a
   different secret), so a plain rebuild of the same commit may reuse the earlier "skipped" step. Whether a Space
   "Restart" rebuilds at all is not verified. (Locally, `make docker-build-bundle` passes `--no-cache-filter
   studio-snapshots`, so it never reuses that step.)
3. **Without the secret** the step prints `Studio snapshots skipped: the build secret CENSUS_API_KEY was not
   provided.`, builds nothing, and the image builds as before; every real metro runs in limited mode ("no data
   snapshot"). Without the Census key a snapshot would have no ACS values, so demand would be UNKNOWN for most
   business types; that is why the step does not run on the keyless sources alone. For the same reason a snapshot
   whose `acs_tracts` (or `tract_geography`) step did not succeed is removed: a key the Census API refuses (wrong,
   or not activated yet) or a Census outage during the build gives `removed incomplete snapshot <metro>/<date>:
   required step acs_tracts is failed` in the log, and that metro stays at "no data snapshot" instead of offering
   area suggestions and simulations that answer 409.

What the step does (`scripts/build_studio_snapshots.py`, tests in `tests/web/test_build_studio_snapshots.py`):

- The secrets are mounted as files in `/run/secrets` for that one `RUN` only (`--mount=type=secret,id=NAME,
  required=false`; `required=false` lets the build go on without them, checked locally). The script reads them into
  the environment of one child process, the existing snapshot builder (`python -m uwcopilot.studio.data.snapshot
  --metro all`, what `make studio-data METRO=all` runs). They are never `ARG` or `ENV`, never printed, never written
  to a file, and never mounted into any other step; no runtime secret is ever mounted into the build.
- The builder's own output is not shown (it can carry request details). The build log shows only: whether each
  build secret was provided, the exception class name if the builder crashed (a name that does not look like an
  exception class is printed as "unknown"), and per real metro whether a snapshot was kept and the NAMES of the
  steps that failed or were not available. Each step's detail stays in the snapshot's `manifest.json`, as for a
  local run. Every printed line is checked for the key values first (the forms listed below); a line that holds one
  is replaced by a notice that names the variable.
- Time limit: `STUDIO_SNAPSHOT_TIMEOUT_S` seconds (default 1500, a design choice: see the checks below; a Space
  variable of that name overrides it), and `timeout` bounds the whole step at that plus 180 s, then kills it 30 s
  later: with the default, the step alone may take up to 1710 s. The log prints when the builder started, how long
  it ran and when each metro's manifest was written, to tune the limit. Metros finished before the limit are kept;
  a folder that is incomplete, whose layer files do not match the SHA-256 in its manifest, or whose `tract_geography`
  or `acs_tracts` step did not succeed is deleted, so that metro is honestly limited rather than half-filled. A
  failed source or a failed step never fails the image build; if the script itself fails, `data/studio` is emptied.
- In the same `RUN`, everything under `data/studio` except `snapshots/` is deleted: the HTTP response cache
  (`data/studio/cache`, the largest part of a local `data/studio`) and DuckDB's extension download. Only
  `data/studio/snapshots/` is copied into the runtime image, owned by root and read-only for the app.
- The script then searches every kept file, and the members of every zip file (the `.npz` road layer is a
  compressed zip), for each secret value: as is, URL-encoded, and base64 in the standard and URL-safe alphabets at
  any byte position inside a longer encoded text. The data fabric already keeps keys out of cache keys, stored URLs,
  `SourceRecord`s and error messages (`studio/data/http.py`, `provenance.py`); this check verifies it on every
  build. On a hit every snapshot is deleted and only the variable's name is printed.

What the app shows with the snapshots: each real metro's mode follows the existing rule (`metro_mode` in
`studio/metros/models.py`): "full" only when the LOWEST data coverage across the six business types reaches the
metro's `coverage_threshold` (0.80 in `config/studio/metros/*.yaml`). With the snapshots of 2026-10-13 that rule
gives "limited" for all five real metros, because two business types stay below 0.80 in every one of them
(`results/studio_metro_coverage.json`, `by_business_type`): short-term rental (0.6779 to 0.6826; it has no
comparable listings source), which sets the metro-level minimum (`min_coverage_share`), and motel (0.745 to 0.7722).
Adding a comparables source alone would therefore not make a metro "full". The other four types reach 0.80 in all
five metros. Limited with a snapshot is not limited without one: the city picker shows the coverage reason instead
of "no data snapshot", sites are scored on factors with data (each with its source), and the simulation, the path to
approval, area suggestions and the tract map run. `GET /api/config` does not compute coverage (every page loads it):
for a real metro with a snapshot it gives mode `null` and a reason that points to `GET /api/studio/options`, which
the wizard uses; without a snapshot both say "no data snapshot".

**Overture release pin.** The places step reads the ONE Overture release pinned in
`config/studio/overture_places.yaml` (2026-09-23.1, released 2026-09-25). The code refuses it after its 60-day
public window, i.e. after 2026-11-24 (`ReleasePin.retire_on`, `overture_places.check_retention`): a build after that
day records `overture_places: not_available`, so competition, anchors and tourism are UNKNOWN and coverage drops,
until the pin is moved to a current release (and the category map re-reviewed).

**Build-time secrets are separate from runtime secrets.** `CENSUS_API_KEY` and `FRED_API_KEY` are used only while
the image builds; the running app makes no keyed data request (the Census Geocoder it calls is keyless). The app's
runtime secrets (`UWC_SESSION_SECRET`, and with the dataset store `HF_TOKEN` and `UWC_FERNET_KEY`) are never
mounted into the build. Hugging Face also puts every Space secret into the running container's environment (Docker
Spaces docs, "Secrets / Runtime": source `docs/hub/spaces-sdks-docker.md` of github.com/huggingface/hub-docs, read
2026-10-14), so the two build keys are present there too, unused. The
GitHub repository secrets (`HF_TOKEN` for the deploy job) are a third, separate set: the deploy workflow never
handles the Studio keys.

**Checked locally on 2026-10-14** (development checks on the build machine of section 3, through its proxy; not a
results file), with images built from the Space bundle of `git archive aa2260b` plus this change's files as they
were then (HEAD moved on to `1d9f34a` and `3dca486` while the checks ran), the dev login on, local only:

- Without the build secrets: the step printed the skip line. `/api/studio/options` listed every real metro as
  limited, "no data snapshot", with no tracts; a Houston plan's site factors were UNKNOWN, and the simulation, the
  path to approval and area suggestions answered 409 "limited mode".
- With `CENSUS_API_KEY` and `FRED_API_KEY` passed from the shell (`--secret id=CENSUS_API_KEY,env=CENSUS_API_KEY`,
  the same for FRED) and the earlier limit of 1200 s: the builder reached the limit while it was building New York,
  the last and largest metro. Chicago, Dallas-Fort Worth, Houston and Los Angeles were kept with no failed step and
  New York had no snapshot (the builder writes a metro's folder only when that metro is done, so nothing
  half-written was left). That is why the default is now 1500 s.
- The same with the final files (default 1500 s): all five real metros were kept with no failed step.
  `/api/studio/options` gave each of them the coverage reason (still "limited", by the rule above) with its tracts.
  A Houston restaurant/cafe plan and a New York retail storefront plan scored their sites on DATA factors with their
  Census ACS, LODES, HPMS and Overture sources, and the simulation (labelled "simulated, based on stated
  assumptions", with the disclaimer), the path to approval and area suggestions answered 200.
- Every key value in the build machine's environment (`CENSUS_API_KEY`, `BLS_API_KEY`, `FRED_API_KEY`,
  `HUD_USER_TOKEN`, `ORS_API_KEY`, `KAGGLE_API_TOKEN`) was searched for, as is, URL-encoded and base64-encoded, in
  both images built with the secrets: their file systems (`docker export`), all their layers and their configuration
  (`docker save`, the compressed layers decompressed) and their history. None was found. The build logs, the local
  Studio HTTP cache (`data/studio/cache`, filled by keyed runs on 2026-10-12 and 2026-10-13) and the cache of a
  keyed New York run made the same day were searched the same way: none was found.
- The builds with the secrets above ran the script before the later fixes (the required `acs_tracts` step, the
  wider search, the checked log lines). After them, one image was built from the Space bundle of `git archive
  3dca486` plus the changed files, WITHOUT the build secrets: the step printed the skip lines, `/api/config` and
  `/api/studio/options` both listed every real metro as limited, "no data snapshot", `scripts/smoke_deploy.py`
  passed all its checks (its output kept outside `results/`), no key name appeared in the image's environment or
  history, and no key value in its file system (`docker export`). A build of the fixed files WITH the secrets was not
  run. Applied to copies of the local snapshots, the new check removed the five snapshots of 2026-10-12, whose
  `acs_tracts` step had failed, and kept the five of 2026-10-13.

How long the step takes on the Space's builder is not measured. The deploy job waits for the new version at most
3600 s (`deploy_space.py wait --timeout-s 3600` in `.github/workflows/deploy-space.yml`, inside a 90-minute job
limit; both design choices): the snapshot step alone may take up to 1710 s with the default limit, and the other
stages come on top (their time on the Space builder is not measured either). If you raise
`STUDIO_SNAPSHOT_TIMEOUT_S`, raise the wait too (`tests/web/test_deploy_space.py` checks that the wait is longer
than the step's own bound), or accept a "wait" timeout while the Space finishes building on its own.

## 6. Secrets and settings

Space secrets are set by an owner in the Space settings; the only one the workflow sets is `UWC_SESSION_SECRET`
(a fresh random value per deploy, never printed). The full list with comments is at
the end of `.env.example`; the meaning of each is in `src/uwcopilot/web/settings.py`.

| Name | Where | Required? | Purpose |
|---|---|---|---|
| `HF_TOKEN` | GitHub repository secret | to deploy | write token used by the deploy job |
| `HF_SPACE_ID` | GitHub repository secret or variable | no (default `astra6/FundProof`) | `owner/name` of the Space |
| `UWC_SESSION_SECRET` | Space secret | **yes** (startup refuses without it in a Space); set by the deploy job | signs session cookies; 32+ characters |
| `UWC_ROLE_ALLOWLIST` | Space secret or variable | for lender/admin roles | `hfuser@<id>:role,...` (pin each entry to the account id, `subject` in `/api/me`; see `docs/web/auth.md`, Roles); everyone else is a founder |
| `UWC_RETENTION_DAYS`, `UWC_PURGE_EVERY_S` | Space variables | no | how long stored objects live (default 30 days) and how often expired ones are deleted (default 3600 s, plus once at startup) |
| `UWC_STORE=hf_dataset`, `UWC_HF_DATASET_REPO` | Space variables | for data that survives restarts | private dataset repo, created private if missing |
| `HF_TOKEN` | Space secret | with `UWC_STORE=hf_dataset` | write access to that dataset (a different token from the deploy one is fine) |
| `UWC_FERNET_KEY` | Space secret | with `UWC_STORE=hf_dataset` | encrypts stored data; comma-separated for key rotation |
| `UWC_DEV_AUTH` | never in a Space | no | dev login; startup refuses it when `SPACE_ID` is set |
| `CENSUS_API_KEY` | Space secret, **build time** (section 5b) | for real-metro data; without it every real metro runs in limited mode | the Census Data API key the snapshot builder needs (ACS tracts, County Business Patterns); mounted only into the snapshot build step |
| `FRED_API_KEY` | Space secret, **build time** | no | the FRED API for the prime rate; without it the keyless FRED CSV is used |
| `STUDIO_SNAPSHOT_TIMEOUT_S` | Space variable, build time | no (default 1500) | the snapshot builder's time limit in seconds (a whole number); raise the deploy job's wait with it (section 5b) |
| `SPACE_ID`, `SPACE_HOST`, `OAUTH_*`, `OPENID_PROVIDER_URL` | set by Hugging Face | - | never set them by hand |

Without `UWC_STORE=hf_dataset`, everything users store lives on the container disk and is lost whenever the
Space restarts or rebuilds (the Hugging Face Docker Spaces docs say data written on disk is lost on restart;
read 2026-10-12 at huggingface.co/docs/hub/spaces-sdks-docker).

**Deletion and retention** (`src/uwcopilot/web/hf_store.py`, `src/uwcopilot/web/app.py`):

- A dataset repo is a git repo, so a plain delete would leave the file in earlier revisions. After a deletion the
  next flush (at most `UWC_HF_FLUSH_EVERY_S`, default 300 s, later; also at shutdown) squashes the dataset's
  history into one commit (`super_squash_history`) and permanently deletes LFS objects no current file uses
  (`permanently_delete_lfs_files`). The history is also squashed once after every startup.
- If the Hub cannot be reached, the deletion is kept as "pending" (in memory and in
  `<store dir>/.pending_deletes.json`), retried at every flush and re-applied at startup, and the caller gets
  `DeletionPending` instead of a success, so the app must not tell the user the data is gone. If the container
  restarts before the Hub is reachable again, the pending deletion is lost with the disk; the user was told it was
  pending and has to delete again.
- Expired objects (older than `UWC_RETENTION_DAYS`) are hidden at once and deleted once at startup and then every
  `UWC_PURGE_EVERY_S` seconds, in both stores.
- All of this is tested with a fake Hub that keeps every revision and LFS object (`tests/web/test_web_hf_store.py`);
  it has not run against the real Hub yet (section 8).

## 7. The iframe and cookies

A Space shows the app inside an iframe on huggingface.co. For the browser the app's cookies are then third-party
cookies, so in a Space the session cookie is `Secure; SameSite=None; Partitioned`, the CSP allows
`frame-ancestors 'self' https://huggingface.co`, and the sign-in page offers "open in a new tab" (the Space's own
`*.hf.space` address), because some browsers still block sign-in cookies inside the iframe. The iframe and the new
tab keep separate cookies. Details, with the Hugging Face docs quote: `docs/web/auth.md`, "Why `SameSite=None;
Partitioned` in a Space".

## 8. Check on the first deploy

None of these can be checked without a real Space:

- Sign in with Hugging Face inside the iframe and in a new tab (Chrome, Firefox, Safari).
- The OAuth callback URL (`https://<SPACE_HOST>/api/auth/callback`) is accepted.
- How many proxies append to `X-Forwarded-For` in front of the app. `UWC_TRUSTED_PROXY_HOPS` defaults to 1 in a
  Space; if that is wrong, rate limits may count every user as one client. (Several `X-Forwarded-For` header
  lines are joined in order before counting, so a client cannot hide the proxy's entry behind its own line.)
- Cold start and latency on the Space (the deploy job's `space-smoke` artifact), including waking from sleep.
- Whether the Space honors the Dockerfile `HEALTHCHECK` (not documented on the Docker Spaces page read
  2026-10-12; `/api/health` works either way).
- With `UWC_STORE=hf_dataset` and a SYNTHETIC test object: data survives a restart; after deleting it and waiting
  one flush, the dataset has a single commit (`list_repo_commits`), the object's files are gone, and
  `list_lfs_files` lists no object that the current files do not use.
- v0.1: the Space's build log shows the OFAC lists downloaded and a `guideline search check: N hits` line with N above 0 (the build fails
  if `www.treasury.gov` refuses the Space's builder three times; whether it does is not known yet). The regulatory
  documents are optional: if they are missing, `results/rag_index_manifest.json` in the image lists them.
- v0.1: a Lender Desk file shows "guideline search: ok" and the OFAC snapshot date; the first file opened after a
  start is slow (the retriever and the screening matcher load), and its time on Space hardware is not measured.
- v0.1: a real-metro plan with a street address geocodes through the Census Geocoder from the Space (not tested
  from the local container, which had no internet access) and then shows the limited-mode messages.
- v0.1.1, with the `CENSUS_API_KEY` Space secret: the build log's `studio-snapshots` step says `build secret
  CENSUS_API_KEY: provided` and lists each real metro with a kept snapshot; how long the step takes on the Space's
  builder, and whether the whole build fits inside the deploy job's 3600 s wait, is not measured. Then
  `/api/studio/options` shows the coverage reason for each real metro instead of "no data snapshot".
- The browser flows of `scripts/web_metrics.py` need the dev login, so they cannot run against the Space; check the
  same pages there by hand after signing in with Hugging Face.

## 9. Owner decisions (open)

- **Privacy notice.** Studio addendum rule B lets the deployed app take real founder input only under
  `docs/studio/privacy.md`. That file is a draft written from the v0.1 code on 2026-10-13 (what is collected, the
  Census Geocoder, encryption, deletion and retention, logs, and what S8 still has to build); the owners review and
  approve it before the first public deploy.
- **License.** The repository has no LICENSE file, so the Space card has no `license` key. Choosing one is the
  owners' decision.
- **Space visibility.** The workflow creates the Space public (`private=False`), as planned for a usable prototype.
  The files of a public Space are public too: the bundle (`src/`, `config/`, `corpus/`, the frontend source,
  `Dockerfile`, `pyproject.toml`, `uv.lock`) becomes readable on huggingface.co even if the GitHub repository is
  private.
- **Persistent storage.** Container disk only (lost on restart) or the private dataset store (needs
  `UWC_FERNET_KEY` and a token), and the retention period (`UWC_RETENTION_DAYS`, default 30 days, a design choice).
- **Image size.** Keep the full dependency set, or add a `web` dependency group so the image installs only what the
  web app imports.
- **Real metros in the Space.** Without the `CENSUS_API_KEY` Space secret (not set up) every real metro runs in
  limited mode. With it the build adds the snapshots (section 5b). If the owners add it: the snapshot data then
  ships inside the image; whether Hugging Face lets others pull the built image of a public Space is not verified
  here, so check the redistribution terms in `docs/studio/data_sources.md` (Overture places carry per-source
  licenses and attribution).
- **Who may deploy.** Any branch can be deployed with "Run workflow"; restrict it (for example to tags only) if
  wanted.
- **`/api/docs`.** Swagger UI loads one pinned script and one pinned stylesheet (`swagger-ui-dist@5.33.0`) from
  cdn.jsdelivr.net, each with a Subresource Integrity hash, and that page's CSP allows exactly those two URLs, so
  a changed file is refused by the browser. Keep the page public, or serve it only in development.
- **Final product name** (two constants, see the top of this page).

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Deploy job ends with "Deploy skipped" | `HF_TOKEN` repository secret missing |
| Space shows "Runtime error", log says `UWC_SESSION_SECRET is required in a Space` | the push ran with `--keep-session-secret` and the secret is missing: add it, then restart the Space |
| Space log says `UWC_DEV_AUTH=1 is refused inside a Hugging Face Space` | remove `UWC_DEV_AUTH` from the Space settings |
| Space log says `UWC_STORE=hf_dataset needs ...` | add `UWC_HF_DATASET_REPO`, `HF_TOKEN` and `UWC_FERNET_KEY` |
| `wait` step times out | the Space build takes longer than the 3600 s wait or is queued; see the Space's build log |
| Build log: `build secret CENSUS_API_KEY: not provided` although the owners added it | added as a Space *variable* or under another name (it must be a secret named exactly `CENSUS_API_KEY`) |
| Build log shows no `studio-snapshots` output at all, only `CACHED` | the step was reused from the build cache (secrets are not part of the cache key): deploy a new commit or use "Factory rebuild" |
| A real metro still says "no data snapshot" after a build with the secret | the step's log says why: the time limit was reached before that metro (raise the Space variable `STUDIO_SNAPSHOT_TIMEOUT_S` and the deploy wait), its folder was incomplete, its `acs_tracts` step failed, or a key value was found in the files and every snapshot was removed (the log names the variable) |
| Build log: `removed incomplete snapshot <metro>/<date>: required step acs_tracts is failed` for every metro | the Census API refused the key (wrong, or not activated yet) or was down during the build: check the key, then deploy a new commit or use "Factory rebuild" |
| `overture_places` listed as not available | after 2026-11-24 the pinned Overture release is refused: move the pin in `config/studio/overture_places.yaml` |
| Sign-in fails inside the Space page | use "open in a new tab" (section 7) |

# Fundproof web app: ONE container that serves the FastAPI API (/api/*) and the Next.js static export (every
# other path) on port 7860. A Hugging Face Docker Space builds this same file (README.md metadata: sdk docker,
# app_port 7860). See docs/deployment.md.
#
#   docker build -t fundproof:dev .
#   docker run --rm -p 7860:7860 fundproof:dev          # then open http://127.0.0.1:7860
#
# Five stages:
#   1. frontend          node:22-slim builds frontend/out with `npm ci` + `npm run build` (exact versions from the
#                        lockfile)
#   2. python-deps       python:3.11-slim + uv installs the locked Python dependencies into /app/.venv (no dev group)
#   3. assets            public data the Lender Desk demo needs, made at BUILD time (network, no keys): the OFAC SDN +
#                        alternate-name lists, the public regulatory documents, the Qdrant guideline index built from
#                        them and corpus/guidelines, and the fastembed models the guideline search loads
#   4. studio-snapshots  the real metros' Founder Studio data snapshots (public data), built ONLY when the build gets
#                        the CENSUS_API_KEY build secret (a Space secret; optional FRED_API_KEY); without it this stage
#                        builds nothing and the real metros run in limited mode
#   5. runtime           python:3.11-slim with only the virtualenv, the code, config/, corpus/, frontend/out, the
#                        SYNTHETIC demo applications, the assets and the snapshots above; runs as the non-root user
#                        "user" (uid 1000, the Hugging Face Spaces convention)
#
# Behind a TLS-inspecting proxy (like the machine this file was first built on), pass the proxy's CA bundle as an
# OPTIONAL build secret; it is only mounted while packages and data download and never ends up in the image:
#   docker build --secret id=extra_ca_certs,src=/path/to/ca-bundle.crt -t fundproof:dev .
# If that proxy listens on the build machine's own loopback (127.0.0.1), the build steps can only reach it on the
# host network; Docker's predefined proxy build arguments are not stored in the image:
#   docker build --network=host --build-arg HTTPS_PROXY="$HTTPS_PROXY" --build-arg HTTP_PROXY="$HTTP_PROXY" \
#       --build-arg NO_PROXY="$NO_PROXY" --secret id=extra_ca_certs,src=/path/to/ca-bundle.crt -t fundproof:dev .
# Without that secret (GitHub Actions, Hugging Face) the default certificate stores are used.
#
# Real-metro data (stage 4) needs the Census key as a BuildKit secret; a Hugging Face Space passes its Space secret of
# that name. Locally, from the environment (the value is never shown or stored in the image):
#   docker build --secret id=CENSUS_API_KEY,env=CENSUS_API_KEY --secret id=FRED_API_KEY,env=FRED_API_KEY ...

# Base images pinned by digest (the tag is kept for readers). Reproducible: the same bytes on every build.
ARG NODE_IMAGE=node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG PYTHON_IMAGE=python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce
# The commit shown by GET /api/health (stage 5); stage 4 uses it so every new commit re-runs the snapshot step. The
# deploy workflow writes the real hash into this default in the bundle it uploads (scripts/deploy_space.py); a plain
# local build shows null.
ARG UWC_GIT_SHA=

# ---------------------------------------------------------------------------------------------------------------
# Stage 1: the static frontend (frontend/out)
# ---------------------------------------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS frontend

ENV NEXT_TELEMETRY_DISABLED=1 \
    npm_config_update_notifier=false

WORKDIR /build/frontend

# Install first (cached while package.json and package-lock.json do not change), then copy the source.
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN --mount=type=secret,id=extra_ca_certs,required=false \
    if [ -f /run/secrets/extra_ca_certs ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca_certs; fi \
    && npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------------------------------------------
# Stage 2: the locked Python dependencies (/app/.venv)
# ---------------------------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS python-deps

# Same uv version as the lockfile was written with. Python itself comes from the base image, never downloaded.
ARG UV_VERSION=0.11.32
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN --mount=type=secret,id=extra_ca_certs,required=false \
    if [ -f /run/secrets/extra_ca_certs ]; then export PIP_CERT=/run/secrets/extra_ca_certs; fi \
    && pip install "uv==${UV_VERSION}"

# Dependencies only (cached while pyproject.toml and uv.lock do not change). --locked fails if uv.lock is stale.
# README.md is copied because pyproject.toml names it as the package readme (any Markdown file works).
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=extra_ca_certs,required=false \
    if [ -f /run/secrets/extra_ca_certs ]; then export SSL_CERT_FILE=/run/secrets/extra_ca_certs; fi \
    && uv sync --locked --no-dev --no-install-project

# Then the project itself, installed in editable mode: the virtualenv points at /app/src.
COPY src ./src
RUN --mount=type=secret,id=extra_ca_certs,required=false \
    if [ -f /run/secrets/extra_ca_certs ]; then export SSL_CERT_FILE=/run/secrets/extra_ca_certs; fi \
    && uv sync --locked --no-dev

# ---------------------------------------------------------------------------------------------------------------
# Stage 3: build-time assets for the Lender Desk demo (public data only, no keys)
# ---------------------------------------------------------------------------------------------------------------
FROM python-deps AS assets

# UWCOPILOT_ROOT=/app: the scripts write data/raw/, qdrant_storage/, .cache/models/ and results/ under /app.
ENV PATH=/app/.venv/bin:$PATH \
    UWCOPILOT_ROOT=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_DISABLE_TELEMETRY=1

COPY corpus ./corpus
COPY scripts/download_data.py scripts/build_index.py ./scripts/

# 1. OFAC SDN + alternate names (US government public data). Required: the build fails without them, after two
#    retries (15 s and 60 s later) for a passing network error. Each file's SHA-256 and download date go to
#    data/raw/checksums.jsonl; the UI shows that date as the list snapshot date.
#    OpenSanctions is NOT downloaded (about 445 MB, CC BY-NC 4.0); the runtime sets UWC_SCREENING_LISTS=ofac.
# 2. The public regulatory documents. Optional: some government sites refuse scripted requests from cloud build
#    machines, so a failed download leaves them out of the index instead of failing the build; build_index.py then
#    lists them under missing_regulatory_files in results/rag_index_manifest.json, which is kept in the image.
# 3. The guideline index (embedded Qdrant in /app/qdrant_storage) with the dense and sparse fastembed models.
# 4. One real search (dense + sparse + the cross-encoder reranker): it downloads the reranker into the image and
#    fails the build if the index cannot answer.
# 5. Make the model cache readable for uid 1000: the Hub client writes some cache files as owner-only (0600).
RUN --mount=type=secret,id=extra_ca_certs,required=false \
    if [ -f /run/secrets/extra_ca_certs ]; then \
        export REQUESTS_CA_BUNDLE=/run/secrets/extra_ca_certs SSL_CERT_FILE=/run/secrets/extra_ca_certs; \
    fi \
    && (python scripts/download_data.py --datasets ofac_sdn \
        || (sleep 15 && python scripts/download_data.py --datasets ofac_sdn) \
        || (sleep 60 && python scripts/download_data.py --datasets ofac_sdn)) \
    && (python scripts/download_data.py --datasets regulatory_docs \
        || echo "WARNING: regulatory documents not downloaded; the index holds the SYNTHETIC guidelines only") \
    && python scripts/build_index.py --threads 2 \
    && python -c "from uwcopilot.rag.search import search_guidelines; \
from uwcopilot.tools.schemas import GuidelineQuery; \
result = search_guidelines(GuidelineQuery(query='minimum debt service coverage ratio', top_k=3)); \
assert result.hits, 'the guideline index returned no hits'; \
print(f'guideline search check: {len(result.hits)} hits')" \
    && chmod -R a+rX /app/.cache/models

# ---------------------------------------------------------------------------------------------------------------
# Stage 4: the real metros' Studio data snapshots (public data; only with the CENSUS_API_KEY build secret)
# ---------------------------------------------------------------------------------------------------------------
FROM python-deps AS studio-snapshots

# BuildKit leaves secrets out of its cache key, so a build that gets the secrets could reuse the "skipped" result
# of an earlier build that did not. Using the commit here re-runs this stage for every new commit. A local rebuild of
# the SAME commit with other secrets needs `--no-cache-filter studio-snapshots` (make docker-build-bundle adds it).
ARG UWC_GIT_SHA
# The snapshot builder's time limit in seconds, a whole number (a Space VARIABLE of this name overrides it: Hugging
# Face passes Space variables as build arguments). A design choice: long enough for the five real metros on the
# machine this was built on (docs/deployment.md, section 5b). The whole step is also bounded by `timeout` (this plus
# 180 s, then killed 30 s later), so with the default this step alone may take up to 1710 s; the other stages come
# on top. The deploy job's wait for the whole Space build is set separately (.github/workflows/deploy-space.yml).
ARG STUDIO_SNAPSHOT_TIMEOUT_S=1500

ENV PATH=/app/.venv/bin:$PATH \
    UWCOPILOT_ROOT=/app \
    PYTHONDONTWRITEBYTECODE=1

COPY config ./config
COPY scripts/build_studio_snapshots.py ./scripts/

# The Space secrets CENSUS_API_KEY (needed) and FRED_API_KEY (optional) are files in /run/secrets during this one
# RUN only; required=false lets the build go on without them. scripts/build_studio_snapshots.py puts them into the
# environment of the snapshot builder alone (`python -m uwcopilot.studio.data.snapshot --metro all`, what
# `make studio-data METRO=all` runs), never prints them, keeps only complete snapshot folders, deletes the HTTP
# response cache (data/studio/cache) and everything else under data/studio, removes a snapshot whose tracts or ACS
# step failed (a wrong key), searches the kept files and its own printed lines for the values, and prints the names
# of the steps that failed. Without CENSUS_API_KEY it builds nothing. If the script itself fails or hits the outer
# time limit, data/studio is emptied, so no unchecked file is kept. The image build never fails because of this
# step; a metro without a snapshot runs in limited mode and says so.
RUN --mount=type=secret,id=CENSUS_API_KEY,required=false \
    --mount=type=secret,id=FRED_API_KEY,required=false \
    --mount=type=secret,id=extra_ca_certs,required=false \
    if [ -f /run/secrets/extra_ca_certs ]; then \
        export REQUESTS_CA_BUNDLE=/run/secrets/extra_ca_certs SSL_CERT_FILE=/run/secrets/extra_ca_certs; \
    fi \
    && ( (timeout --kill-after=30 "$((STUDIO_SNAPSHOT_TIMEOUT_S + 180))" \
            python scripts/build_studio_snapshots.py --secrets-dir /run/secrets \
                --timeout-s "${STUDIO_SNAPSHOT_TIMEOUT_S}") \
        || (echo "WARNING: the Studio snapshot step did not finish; every real metro runs in limited mode" \
            && rm -rf /app/data/studio) ) \
    && mkdir -p /app/data/studio/snapshots

# ---------------------------------------------------------------------------------------------------------------
# Stage 5: the runtime image
# ---------------------------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# The commit shown by GET /api/health (its default is set at the top of this file).
ARG UWC_GIT_SHA

LABEL org.opencontainers.image.title="Fundproof" \
      org.opencontainers.image.description="Fundproof prototype (Founder Studio + Lender Desk). Educational planning tool. Not financial, legal or lending advice." \
      org.opencontainers.image.source="https://github.com/vajja1405/Underwriting-Copilot-"

# UWCOPILOT_ROOT tells uwcopilot.common.paths where config/, corpus/ and data/ are.
# UWC_SCREENING_LISTS=ofac: sanctions screening loads only the OFAC lists that are in the image (stage 3).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH \
    UWCOPILOT_ROOT=/app \
    HOME=/home/user \
    HF_HUB_DISABLE_TELEMETRY=1 \
    UWC_SCREENING_LISTS=ofac \
    UWC_GIT_SHA=${UWC_GIT_SHA}

# Hugging Face runs the container as uid 1000; create that user so files and $HOME have an owner.
RUN useradd --create-home --uid 1000 user

WORKDIR /app

# Code and data files are owned by root and read-only for the app; only data/ (the local store, data/web) and
# qdrant_storage/ are writable.
COPY --from=python-deps /app/.venv /app/.venv
COPY src ./src
COPY config ./config
COPY corpus ./corpus
COPY --from=frontend /build/frontend/out ./frontend/out
# The Lender Desk demo files (SYNTHETIC structured applications) and the screening threshold chosen by
# scripts/eval_kyc.py (results/kyc_metrics.json; without it screening refuses to run instead of guessing).
COPY eval/applications ./eval/applications
COPY eval/startup_applications ./eval/startup_applications
COPY results/kyc_metrics.json ./results/kyc_metrics.json
# Stage 3's assets. Read-only for the app, except the Qdrant folder: embedded Qdrant writes a lock file there.
COPY --from=assets /app/data/raw/ofac ./data/raw/ofac
COPY --from=assets /app/data/raw/checksums.jsonl ./data/raw/checksums.jsonl
COPY --from=assets /app/.cache/models ./.cache/models
COPY --from=assets /app/results/rag_index_manifest.json ./results/rag_index_manifest.json
COPY --from=assets --chown=user:user /app/qdrant_storage ./qdrant_storage
# Stage 4's real-metro snapshots (an empty folder when the build had no CENSUS_API_KEY secret). Read-only for the app;
# GET /api/studio/options reports each metro's mode from what is here.
COPY --from=studio-snapshots /app/data/studio/snapshots ./data/studio/snapshots
# Compile the project's own modules now (the dependencies were compiled by uv), because the app cannot write
# __pycache__ into the root-owned src/ at runtime; this saves compiling them on every start.
RUN python -m compileall -q /app/src \
    && mkdir -p /app/data && chown user:user /app/data

USER user

EXPOSE 7860

# Liveness: /api/health touches nothing but settings and is exempt from rate limiting.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=4).status == 200 else 1)"]

# One worker (the rate limiter and a generated session secret are per process). --no-access-log: the app writes
# its own redacted access log (uwcopilot.web.access_log); uvicorn's would record raw query strings.
# --no-proxy-headers: client IPs come only from the app's documented X-Forwarded-For rule (UWC_TRUSTED_PROXY_HOPS).
CMD ["uvicorn", "uwcopilot.web.app:app", "--host", "0.0.0.0", "--port", "7860", "--no-access-log", "--no-proxy-headers"]

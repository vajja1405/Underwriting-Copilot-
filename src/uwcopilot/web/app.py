"""The FastAPI application: /api routers, OpenAPI docs at /api/docs, and the static frontend for everything else.

    uv run uvicorn uwcopilot.web.app:app --port 7860 --no-access-log     # module-level app from the environment
    app = create_app(settings)                                            # tests build their own

Startup refuses unsafe settings (see settings.py): the dev login inside a Space, a Space without a session
secret, or the dataset store without an encryption key. The store is opened in the lifespan (startup) and the
dataset store flushes its last batch at shutdown.

Retention: expired objects are deleted once at startup and then every UWC_PURGE_EVERY_S seconds (default one
hour) by a background task, so data does not outlive UWC_RETENTION_DAYS by more than that interval while the app
runs. (Expired objects are already hidden from get and list before they are purged.)
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, Response

from uwcopilot.web.access_log import AccessLogMiddleware, configure_web_logging
from uwcopilot.web.auth import HfOAuthClient, rate_limit_user_key, session_serializer
from uwcopilot.web.cookies import CookiePolicy
from uwcopilot.web.crypto import encryptor_for_settings
from uwcopilot.web.hf_store import HFDatasetStore, HubApi
from uwcopilot.web.routers import auth as auth_router
from uwcopilot.web.routers import lender as lender_router
from uwcopilot.web.routers import studio as studio_router
from uwcopilot.web.routers import system as system_router
from uwcopilot.web.security import (
    API_MAX_BODY_BYTES,
    BodySizeLimitMiddleware,
    CsrfMiddleware,
    RateLimiter,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    build_csp,
    inline_script_hashes,
)
from uwcopilot.web.services import install_services
from uwcopilot.web.settings import DEV_FRONTEND_ORIGIN, WebSettings, load_web_settings
from uwcopilot.web.static import StaticSite
from uwcopilot.web.store import LocalFileStore, Store

logger = logging.getLogger(__name__)

OPENAPI_URL = "/api/openapi.json"
DOCS_URL = "/api/docs"
# Swagger UI (the /api/docs page) loads ONE pinned script and ONE pinned stylesheet from the jsDelivr CDN. Each tag
# carries a Subresource Integrity hash, so the browser refuses a file that differs from the one checked here, and
# that page's CSP allows exactly these two URLs (not the whole CDN). The hashes are SHA-384 of the files in the npm
# tarball swagger-ui-dist-5.33.0.tgz, which matched the CDN copies byte for byte (checked 2026-10-13). To upgrade:
# change the version, recompute both hashes (openssl dgst -sha384 -binary FILE | openssl base64 -A) and update them.
SWAGGER_UI_VERSION = "5.33.0"
SWAGGER_JS_URL = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui-bundle.js"
SWAGGER_CSS_URL = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}/swagger-ui.css"
SWAGGER_JS_SRI = "sha384-YDALVcy8kj8yltLBVi1vBiBAUqdxvus673gM8XKwiy6aDUJFXivF/KCufekjYbVf"
SWAGGER_CSS_SRI = "sha384-Ov4/wv3j2bmct8cDc5X4ngJZohVPzEmc6uDPH8WeljUxO5vtoykvMEfbu9Vh6RaW"
# The frontend's own icon (no third-party image).
SWAGGER_FAVICON_URL = "/icon.svg"


def open_store(settings: WebSettings, hub_api: HubApi | None = None) -> Store:
    """The configured store. The dataset store is restored from the Hub and starts its flush thread."""
    encryptor = encryptor_for_settings(settings.fernet_key_bytes, settings.is_space, settings.store_dir)
    local = LocalFileStore(settings.store_dir, encryptor, default_retention_days=settings.retention_days)
    if settings.store_backend == "local":
        return local
    if hub_api is None:
        from huggingface_hub import HfApi  # only the dataset store needs the Hub client

        hub_api = HfApi(token=settings.hf_token.get_secret_value() if settings.hf_token else None)
    store = HFDatasetStore(local, hub_api, str(settings.hf_dataset_repo), flush_every_s=settings.hf_flush_every_s)
    store.restore()
    store.start()
    return store


def purge_expired_objects(store: Store) -> int:
    """Delete every expired object once. Returns how many were deleted; a failure is logged, never raised."""
    try:
        purged = store.purge_expired()
    except Exception:
        logger.exception("retention purge failed; it runs again at the next interval")
        return 0
    if purged:
        logger.info("retention purge deleted %d expired object(s)", len(purged))
    return len(purged)


async def purge_periodically(store: Store, every_s: float) -> None:
    """Run the retention purge every every_s seconds until cancelled (in a worker thread: it may call the Hub)."""
    while True:
        await asyncio.sleep(every_s)
        await asyncio.to_thread(purge_expired_objects, store)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the store (unless a test injected one), purge expired data now and periodically, close at shutdown."""
    opened_here = app.state.store is None
    if opened_here:
        app.state.store = open_store(app.state.settings, app.state.hub_api)
    store: Store = app.state.store
    purge_expired_objects(store)
    purge_task = asyncio.create_task(purge_periodically(store, app.state.settings.purge_every_s))
    try:
        yield
    finally:
        purge_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await purge_task
        if opened_here and isinstance(app.state.store, HFDatasetStore):
            app.state.store.close()


def get_store(request: Request) -> Store:
    """FastAPI dependency: the app's Store (503 before startup has opened it)."""
    store: Store | None = request.app.state.store
    if store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="storage is not ready")
    return store


def add_integrity(html: str, attribute: str, url: str, integrity: str) -> str:
    """Add integrity and crossorigin attributes to the one tag whose `attribute` (src or href) is url."""
    tag_part = f'{attribute}="{url}"'
    if html.count(tag_part) != 1:
        raise RuntimeError(f"the Swagger UI page does not load {url} exactly once; FastAPI's template changed")
    return html.replace(tag_part, f'{tag_part} integrity="{integrity}" crossorigin="anonymous"')


def swagger_page(title: str) -> HTMLResponse:
    """FastAPI's Swagger UI page, pinned with SRI, with its own CSP: the two pinned files and its inline script."""
    page = get_swagger_ui_html(
        openapi_url=OPENAPI_URL,
        title=title,
        swagger_js_url=SWAGGER_JS_URL,
        swagger_css_url=SWAGGER_CSS_URL,
        swagger_favicon_url=SWAGGER_FAVICON_URL,
    )
    html = bytes(page.body).decode("utf-8")
    html = add_integrity(html, "src", SWAGGER_JS_URL, SWAGGER_JS_SRI)
    html = add_integrity(html, "href", SWAGGER_CSS_URL, SWAGGER_CSS_SRI)
    csp = build_csp(
        script_hashes=inline_script_hashes(html),
        extra_script_src=[SWAGGER_JS_URL],
        extra_style_src=[SWAGGER_CSS_URL],
    )
    return HTMLResponse(html, headers={"Content-Security-Policy": csp})


def add_middlewares(app: FastAPI, settings: WebSettings) -> None:
    """Add the middlewares. Starlette runs the LAST added first, so this list is innermost first."""
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        prefix_limits=[("/api/", API_MAX_BODY_BYTES)],
    )
    app.add_middleware(
        RateLimitMiddleware,
        ip_limiter=RateLimiter(settings.rate_limit_ip_per_minute, settings.rate_limit_ip_burst),
        user_limiter=RateLimiter(settings.rate_limit_user_per_minute, settings.rate_limit_user_burst),
        user_key=rate_limit_user_key,
        trusted_proxy_hops=settings.trusted_proxy_hops,
    )
    if settings.dev_auth:
        # Only for `next dev` on localhost:3000 talking to this API on another port. Production is same-origin.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[DEV_FRONTEND_ORIGIN],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(AccessLogMiddleware)


def create_app(
    settings: WebSettings | None = None,
    *,
    oauth_transport: httpx.BaseTransport | None = None,
    store: Store | None = None,
    hub_api: HubApi | None = None,
    extra_routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """Build the app. settings=None reads the environment.

    oauth_transport, store and hub_api let tests replace the network and the disk. extra_routers are included
    before the static catch-all route (feature routers plug in here; tests add probe routes).
    """
    settings = settings if settings is not None else load_web_settings()
    configure_web_logging()
    app = FastAPI(
        title=f"{settings.product_name} API",
        version=settings.app_version,
        description=settings.disclaimer,
        docs_url=None,
        redoc_url=None,
        openapi_url=OPENAPI_URL,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.cookie_policy = CookiePolicy.for_environment(settings.is_space)
    app.state.session_serializer = session_serializer(settings)
    app.state.oauth_client = HfOAuthClient(settings.oauth, transport=oauth_transport) if settings.oauth else None
    app.state.static_site = StaticSite(settings.static_dir, settings.product_name, settings.disclaimer)
    app.state.store = store
    app.state.hub_api = hub_api
    install_services(app)

    app.include_router(system_router.router)
    app.include_router(auth_router.router)
    app.include_router(studio_router.router)
    app.include_router(lender_router.router)
    for router in extra_routers:
        app.include_router(router)

    @app.get(DOCS_URL, include_in_schema=False)
    def api_docs() -> HTMLResponse:
        """Interactive API documentation (Swagger UI)."""
        return swagger_page(f"{settings.product_name} API")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def frontend(request: Request, full_path: str) -> Response:
        """Unknown /api paths get a JSON 404; everything else is served from the static export."""
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=status.HTTP_404_NOT_FOUND)
        return app.state.static_site.respond(request, full_path)

    add_middlewares(app, settings)
    if settings.dev_auth:
        logger.warning("DEV LOGIN is ON (UWC_DEV_AUTH=1): anyone can sign in with any role. Local development only.")
    if not app.state.static_site.available:
        logger.warning("frontend export not found at %s; pages show a 'not built' note", settings.static_dir)
    return app


app = create_app()

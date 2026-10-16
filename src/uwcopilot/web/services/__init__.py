"""Thin adapters between the web routers and the domain packages (uwcopilot.studio, uwcopilot.agent).

    services = install_services(app)        # create_app() does this; tests may replace parts afterwards
    services.compute.run(work, key=..., owner=...)  # heavy work in a worker thread with a timeout (compute.py)
    services.geocoding.prefetch(owner, addresses)    # address lookups, apart from the heavy work (geocoding.py)

Modules:
- settings.py       timeouts, worker threads, cache sizes, the demo folders (UWC_COMPUTE_* variables);
- compute.py        ComputeRunner: worker threads, per-request timeout, per-key de-duplication, result cache;
- errors.py         ServiceError and its HTTP-status subclasses;
- http.py           FastAPI glue: error handlers (ServiceError -> its status, timeouts -> 503), store dependency;
- text_checks.py    no SSNs / account numbers in free text; the injection screen of the idea text;
- geocoding.py      the Census Geocoder for real-metro addresses: its own small pool, per-user limits, no cache;
- studio_options.py metros with their honest mode, business types, levers, defaults (GET /api/studio/options);
- projects.py       a founder's plans: validation, storage per user (encrypted Store), versions, deletion;
- studio_runs.py    SiteIQ, feasibility, path to approval, area suggestions, the tract map layer;
- lender_demo.py    the Lender Desk demo: the agent to its human checkpoint, DEMO decisions per user.

Nothing in this package approves, declines or recommends: Studio prepares and explains, the agent stops at its
human checkpoint, and a person decides.
"""

from dataclasses import dataclass

from fastapi import FastAPI, Request

from uwcopilot.web.services.compute import ComputeRunner
from uwcopilot.web.services.geocoding import GeocodeService
from uwcopilot.web.services.http import add_error_handlers
from uwcopilot.web.services.lender_demo import LenderDemoService
from uwcopilot.web.services.settings import ServiceSettings, load_service_settings


@dataclass
class WebServices:
    """Everything the feature routers use, kept on app.state.services."""

    settings: ServiceSettings
    compute: ComputeRunner
    geocoding: GeocodeService
    lender: LenderDemoService


def build_services(settings: ServiceSettings | None = None) -> WebServices:
    """The default services (settings=None reads the UWC_COMPUTE_* variables)."""
    chosen = settings or load_service_settings()
    return WebServices(
        settings=chosen,
        compute=ComputeRunner(chosen),
        geocoding=GeocodeService(),
        lender=LenderDemoService(chosen.demo_dirs),
    )


def install_services(app: FastAPI, settings: ServiceSettings | None = None) -> WebServices:
    """Build the services, keep them on the app and register the services' error handlers."""
    services = build_services(settings)
    app.state.services = services
    add_error_handlers(app)
    return services


def get_services(request: Request) -> WebServices:
    """FastAPI dependency: the app's services."""
    return request.app.state.services

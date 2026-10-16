"""System endpoints: health and the public app configuration.

GET /api/health -> 200
    {"status": "ok", "version": "0.1.0", "git_sha": "abc1234" | null}

GET /api/config -> 200
    {
      "product_name": "Fundproof",
      "disclaimer": "Educational planning tool. Not financial, legal or lending advice. ...",
      "sides": ["founder", "lender"],
      "data_labels": {"synthetic": "SYNTHETIC", "public": "PUBLIC",
                      "simulation": "simulated, based on stated assumptions"},
      "roles": ["founder", "lender_reviewer", "lender_senior", "admin"],
      "hf_oauth": true,      # "Sign in with Hugging Face" is available
      "dev_auth": false,     # the DEV LOGIN form may be shown (never true in a Space)
      "is_space": true,
      "space_url": "https://astra6-fundproof.hf.space" | null,   # direct (non-iframe) address, for "open in a new tab"
      "payments_enabled": false,
      "metros": [{"id": "houston_tx", "name": "...", "data_label": "PUBLIC",
                  "mode": "limited" | "synthetic" | null, "mode_reason": "..."}],
      "metros_note": null | "Metro configs are not available yet.",
      "business_types": [{"id": "car_wash", "display_name": "Car wash"}],
      "business_types_note": null | "Business types are not available yet."
    }

Metros and business types come from the Studio loaders (uwcopilot.studio). Those are still being built, so they are
loaded inside a function that returns [] and a "not available yet" note when the import or the YAML fails. The
lists are loaded on the first request and kept for the life of the process.

A metro's mode here never computes data coverage, because every page loads this endpoint and coverage needs the
whole snapshot (GET /api/studio/options computes it, once per process). So: the SYNTHETIC metro is "synthetic"; a
real metro without a data snapshot in this container is "limited" ("no data snapshot"), as in /api/studio/options;
a real metro WITH a snapshot has mode null and a reason that points to /api/studio/options, because its mode ("full"
or "limited") depends on that coverage.
"""

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from uwcopilot.web.settings import ROLES, WebSettings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["system"])

SYNTHETIC_LABEL = "SYNTHETIC"
PUBLIC_LABEL = "PUBLIC"
SIMULATION_LABEL = "simulated, based on stated assumptions"
METROS_NOT_AVAILABLE = "Metro configs are not available yet."
BUSINESS_TYPES_NOT_AVAILABLE = "Business types are not available yet."
MODE_IN_STUDIO_OPTIONS = (
    "a data snapshot of {date} is in this container; the metro's mode depends on its data coverage, which GET "
    "/api/studio/options reports (this endpoint does not compute coverage)"
)


class HealthResponse(BaseModel):
    """Liveness and build identity."""

    status: Literal["ok"]
    version: str
    git_sha: str | None


class MetroItem(BaseModel):
    """One metro the founder can pick."""

    id: str
    name: str
    data_label: str
    mode: str | None  # None: decided by data coverage, see GET /api/studio/options
    mode_reason: str


class BusinessTypeItem(BaseModel):
    """One business type the founder can pick."""

    id: str
    display_name: str


class ConfigResponse(BaseModel):
    """Public, non-secret configuration for the frontend."""

    product_name: str
    disclaimer: str
    sides: list[str]
    data_labels: dict[str, str]
    roles: list[str]
    hf_oauth: bool
    dev_auth: bool
    is_space: bool
    space_url: str | None
    payments_enabled: bool
    metros: list[MetroItem]
    metros_note: str | None
    business_types: list[BusinessTypeItem]
    business_types_note: str | None


def config_metro_mode(metro: Any, snapshots_dir: Path | None = None) -> tuple[str | None, str]:
    """(mode or None, reason) of a metro config without computing data coverage (see the module docstring).

    The SYNTHETIC metro, a real metro without a data snapshot in snapshots_dir (default: the app's
    data/studio/snapshots) and a real metro whose county list is not verified get exactly what metro_mode gives
    without coverage, which is also what GET /api/studio/options shows. A verified real metro with a snapshot gets
    None and MODE_IN_STUDIO_OPTIONS.
    """
    from uwcopilot.studio.data.fabric_snapshot import latest_snapshot
    from uwcopilot.studio.data.paths import SNAPSHOTS_DIR
    from uwcopilot.studio.metros import metro_mode

    snapshot = None
    if metro.data_label != SYNTHETIC_LABEL:
        snapshot = latest_snapshot(metro.id, snapshots_dir or SNAPSHOTS_DIR, pinned=metro.snapshot_date)
    if snapshot is not None and metro.verified:
        return None, MODE_IN_STUDIO_OPTIONS.format(date=snapshot.snapshot_date.isoformat())
    return metro_mode(metro, coverage_share=None, snapshot_date=snapshot.snapshot_date if snapshot else None)


def load_metro_items() -> tuple[list[MetroItem], str | None]:
    """Every metro config with its mode, or ([], note) if the Studio metro loader is not usable yet."""
    try:
        from uwcopilot.studio.metros import load_all_metros

        items = []
        for metro_id, metro in sorted(load_all_metros().items()):
            mode, reason = config_metro_mode(metro)
            items.append(
                MetroItem(id=metro_id, name=metro.name, data_label=metro.data_label, mode=mode, mode_reason=reason)
            )
    except Exception:
        logger.error("Studio metro configs could not be loaded; /api/config lists none", exc_info=True)
        return [], METROS_NOT_AVAILABLE
    return items, None


def load_business_type_items() -> tuple[list[BusinessTypeItem], str | None]:
    """Every business-type profile, or ([], note) if the Studio profile loader is not usable yet."""
    try:
        from uwcopilot.studio.profiles import load_all_profiles

        items = [
            BusinessTypeItem(id=profile_id, display_name=profile.display_name)
            for profile_id, profile in sorted(load_all_profiles().items())
        ]
    except Exception:
        logger.error("Studio business types could not be loaded; /api/config lists none", exc_info=True)
        return [], BUSINESS_TYPES_NOT_AVAILABLE
    return items, None


def build_config(settings: WebSettings) -> ConfigResponse:
    """The /api/config body (the Studio lists are loaded here)."""
    metros, metros_note = load_metro_items()
    business_types, business_types_note = load_business_type_items()
    return ConfigResponse(
        product_name=settings.product_name,
        disclaimer=settings.disclaimer,
        sides=["founder", "lender"],
        data_labels={"synthetic": SYNTHETIC_LABEL, "public": PUBLIC_LABEL, "simulation": SIMULATION_LABEL},
        roles=list(ROLES),
        hf_oauth=settings.oauth is not None,
        dev_auth=settings.dev_auth,
        is_space=settings.is_space,
        space_url=settings.space_url,
        payments_enabled=False,
        metros=metros,
        metros_note=metros_note,
        business_types=business_types,
        business_types_note=business_types_note,
    )


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Liveness probe for Docker HEALTHCHECK and smoke tests; touches nothing but settings."""
    settings: WebSettings = request.app.state.settings
    return HealthResponse(status="ok", version=settings.app_version, git_sha=settings.git_sha)


@router.get("/config", response_model=ConfigResponse)
def config(request: Request) -> ConfigResponse:
    """Public app configuration (built on the first call, then reused)."""
    cached: ConfigResponse | None = getattr(request.app.state, "config_response", None)
    if cached is None:
        cached = build_config(request.app.state.settings)
        request.app.state.config_response = cached
    return cached

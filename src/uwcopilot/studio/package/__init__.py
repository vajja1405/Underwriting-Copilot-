"""Founder Studio S6: the lender-ready package and its handoff into the lender copilot.

The API the web layer (website v0.2) and the scripts call:

    from uwcopilot.studio.package import FounderProject, build_package, render_pdf, to_application, handoff
    package = build_package(project)          # Package, schema "founder-package-v1", JSON-serializable
    pdf = render_pdf(package)                 # bytes (%PDF...), every page: product, disclaimer, page x/y
    application = to_application(package)    # agent.applications.StartupCreditApplication (startup-application-v1)
    result = handoff(package)                 # the lender copilot run to its human checkpoint, never decided

A FounderProject holds one plan's S2-S5 results (PlanCase, candidate sites, SiteScore, FeasibilityResult,
PathResult) plus the owners, entity, documents and other-debt kinds a lender's application needs. Every number of
the package is a claim of ONE founder ledger (S4 build_founder_ledger, with the package's own claims added); the
sections arrange claim ids, the provenance renderer prints the values, and the round-trip audit re-reads the text
(audit_package) and the PDF (audit_pdf). Section names follow the SBA's traditional business plan sections, as read
from the SBA page (config/studio/sba_business_plan_sections.json, docs/studio/sources.md).

Modules: project (inputs), golden (the 36 SYNTHETIC golden projects), claims_sites / claims_finance (the package's
own claims), assumptions (the material assumption list), blocks / models (the package as data), sections_* (the
eleven sections), mapdata (the map's GeoJSON), runs (model runs in the appendix), text / cover (printed words),
audit, pdf / pdf_styles / pdf_figures (reportlab and matplotlib), application (to_application), handoff, sba,
constants, build (build_package).
"""

from uwcopilot.studio.package.application import ApplicationNotReady, application_id_for, to_application
from uwcopilot.studio.package.audit import audit_package, audit_pdf, package_text, pdf_page_count, pdf_text
from uwcopilot.studio.package.build import build_package
from uwcopilot.studio.package.constants import PACKAGE_SCHEMA_VERSION, PRODUCT_NAME
from uwcopilot.studio.package.golden import project_from_golden
from uwcopilot.studio.package.handoff import HandoffResult, LenderReviewRequest, handoff
from uwcopilot.studio.package.models import Package
from uwcopilot.studio.package.pdf import PackageNotAudited, render_pdf
from uwcopilot.studio.package.project import FounderProject, PackageOwner

__all__ = [
    "PACKAGE_SCHEMA_VERSION",
    "PRODUCT_NAME",
    "ApplicationNotReady",
    "FounderProject",
    "HandoffResult",
    "LenderReviewRequest",
    "Package",
    "PackageNotAudited",
    "PackageOwner",
    "application_id_for",
    "audit_package",
    "audit_pdf",
    "build_package",
    "handoff",
    "package_text",
    "pdf_page_count",
    "pdf_text",
    "project_from_golden",
    "render_pdf",
    "to_application",
]

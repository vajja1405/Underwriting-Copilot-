"""Fixed names and labels of the founder package (Founder Studio S6), in one place.

Nothing here is a number about a business or a place: these are the package's version, its product name, the labels
the Studio addendum requires on every page (rule C disclaimer, rule E simulation label, the SYNTHETIC banner), and
the plain words used for claim statuses.
"""

from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL, SYNTHETIC_LABEL

PACKAGE_SCHEMA_VERSION = "founder-package-v1"
PACKAGE_BUILDER_VERSION = "studio-package-1.0"
PRODUCT_NAME = "Fundproof"
PACKAGE_TITLE = "Lender-ready business plan package"
# The short footer line on every PDF page (rule C, shortened as the S6 plan words it) and the full disclaimer.
FOOTER_DISCLAIMER = "Educational planning tool. Not financial, legal or lending advice."
FULL_DISCLAIMER = DISCLAIMER
SIMULATION_NOTE = f"Every probability in this package is {SIMULATION_LABEL}."
SYNTHETIC_BANNER = (
    "SYNTHETIC EXAMPLE: generated plan, people and documents for demonstration. Not a real business, founder or "
    "loan request."
)
NO_DECISION_NOTE = (
    "Fundproof prepares and explains a plan. It does not approve, decline or promise a loan and does not recommend "
    "a lender; the lender runs its own rules and makes its own decision."
)
# Plain words for the six ledger statuses (shown in tables and the evidence appendix).
STATUS_WORDS: dict[str, str] = {
    "VERIFIED": "Verified",
    "USER_STATED": "Stated by founder",
    "ASSUMPTION": "Assumption",
    "MODEL_ESTIMATE": "Simulated",
    "CONFLICTING": "Conflicting",
    "UNKNOWN": "Unknown",
}
STATUS_MEANINGS: dict[str, str] = {
    "VERIFIED": "backed by a document with its page, a public dataset row with its locator, or a program rule",
    "USER_STATED": "typed by the founder; not checked against a document yet",
    "ASSUMPTION": "a planning range with its source (or marked unsourced); editable",
    "MODEL_ESTIMATE": f"an output of the simulation or SiteIQ, {SIMULATION_LABEL}",
    "CONFLICTING": "a document disagrees with the founder's figure; both are shown",
    "UNKNOWN": "needed but not available yet; the reason is given",
}
__all__ = [
    "FOOTER_DISCLAIMER",
    "FULL_DISCLAIMER",
    "NO_DECISION_NOTE",
    "PACKAGE_BUILDER_VERSION",
    "PACKAGE_SCHEMA_VERSION",
    "PACKAGE_TITLE",
    "PRODUCT_NAME",
    "SIMULATION_LABEL",
    "SIMULATION_NOTE",
    "STATUS_MEANINGS",
    "STATUS_WORDS",
    "SYNTHETIC_BANNER",
    "SYNTHETIC_LABEL",
]

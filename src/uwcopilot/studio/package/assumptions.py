"""The plan's material assumptions as the startup program lists them (NSP-4.3 assumption list, NSP-6.1 top drivers).

One row per simulator input (FeasibilityResult.distributions), read from the founder ledger:
- value and status are the claim's: the founder's figure (USER_STATED), a document-backed one (VERIFIED), public
  data (VERIFIED), a profile or metro range (ASSUMPTION) or SiteIQ's demand estimate (MODEL_ESTIMATE). A CONFLICTING
  figure is listed as USER_STATED (the founder's figure, not verified); an input without a value is left out;
- unit is a short code built from the ledger unit ("USD/hour", "%", "cars/day"), never from the founder's text;
- source is the claim's source in words ("unsourced planning range" when no publication backs it, as NSP-4.3 asks);
- supporting_document_types are the supporting-document types (quote, lease or letter of intent, comparable data)
  of the documents that VERIFY the figure; to_application counts them only when the document is in the file;
- tornado_rank is the input's rank in the sensitivity analysis (1 = moves year-2 net income the most), unique,
  so ranks 1, 2 and 3 are exactly the top drivers. An input that is not in the tornado has no rank.
"""

import re

from uwcopilot.agent.policy_startup import SUPPORTING_DOCUMENTS
from uwcopilot.evidence.ledger_models import UNSOURCED, Claim, Ledger
from uwcopilot.studio.feasibility.result import FeasibilityResult
from uwcopilot.studio.narrative import ids
from uwcopilot.studio.narrative.assumption_claims import SITE_DEMAND_INPUT
from uwcopilot.studio.narrative.program_calcs import input_claim_of
from uwcopilot.studio.package.models import MaterialAssumption

UNIT_LIMIT = 24
UNIT_SAFE = re.compile(r"[^A-Za-z0-9/%$._()-]")
UNSOURCED_TEXT = "unsourced planning range"
SOURCE_LIMIT = 300
SPECIAL_UNITS: dict[str, str] = {
    "USD": "USD",
    "MONTHS": "months",
    "DAYS_PER_WEEK": "days/week",
    "PAID_STAFF_HOURS_PER_MONTH": "hours/month",
    "HOURS": "hours/month",
}


def short_unit(claim: Claim) -> str:
    """A short unit code from the ledger unit (module docstring)."""
    if claim.display == "pct1":
        return "%"
    if claim.unit in SPECIAL_UNITS:
        return SPECIAL_UNITS[claim.unit]
    if claim.unit.startswith("USD_PER_"):
        return "USD/" + claim.unit.removeprefix("USD_PER_").split("_")[0].lower()
    if "_PER_" in claim.unit:
        before, after = claim.unit.lower().split("_per_", 1)
        code = f"{before.split('_')[-1]}/{after.split('_')[0]}"
    elif claim.display == "ratio2":
        code = "ratio"
    else:
        code = claim.unit.lower()
    return UNIT_SAFE.sub("_", code)[:UNIT_LIMIT] or "unit"


def application_status(claim: Claim) -> str | None:
    """The program's assumption status for a claim (None: no value to list)."""
    if claim.value is None or claim.status == "UNKNOWN":
        return None
    if claim.status == "CONFLICTING":
        return "USER_STATED"
    return claim.status


def source_words(claim: Claim) -> str:
    """Where the figure comes from, in words (at most SOURCE_LIMIT characters)."""
    origin = claim.provenance
    if claim.status == "USER_STATED" or claim.status == "CONFLICTING":
        words = "stated by the founder"
    elif origin.kind == "document":
        words = "document " + ", ".join(citation.text() for citation in origin.citations)
    elif claim.status == "MODEL_ESTIMATE":
        words = f"model estimate: {origin.model_name} {origin.model_version or ''}".strip()
    elif origin.source and origin.source != UNSOURCED:
        words = origin.source
    else:
        words = UNSOURCED_TEXT
    return words[:SOURCE_LIMIT]


def supporting_types(claim: Claim) -> list[str]:
    """Supporting-document types of the documents that verify the claim (empty when none do)."""
    if claim.status != "VERIFIED" or claim.provenance.kind != "document":
        return []
    listed = str(claim.provenance.details.get("document_types", "")).split(", ")
    return [code for code in SUPPORTING_DOCUMENTS if code in listed]


def input_claim(name: str, claims: dict[str, Claim]) -> Claim | None:
    """The claim behind one simulator input (the site's demand for mature_demand)."""
    if name == SITE_DEMAND_INPUT:
        return claims.get(ids.SITE_DEMAND_MODE)
    return input_claim_of(name, claims)


def material_assumptions(ledger: Ledger, result: FeasibilityResult) -> list[MaterialAssumption]:
    """One row per simulator input with a value (module docstring), in the simulator's order."""
    claims = {item.id: item for item in ledger.claims}
    ranks = {bar.name: bar.rank for bar in result.tornado.bars} if result.tornado is not None else {}
    rows = []
    for distribution in result.distributions:
        item = input_claim(distribution.name, claims)
        if item is None:
            continue
        status = application_status(item)
        if status is None or not isinstance(item.value, int | float) or isinstance(item.value, bool):
            continue
        rows.append(
            MaterialAssumption(
                name=distribution.name,
                claim_id=item.id,
                value=float(item.value),
                unit=short_unit(item),
                status=status,
                source=source_words(item),
                supporting_document_types=supporting_types(item),
                tornado_rank=ranks.get(distribution.name),
            )
        )
    return rows

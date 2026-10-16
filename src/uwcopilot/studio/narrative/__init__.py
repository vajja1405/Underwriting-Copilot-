"""Founder Studio S4: the founder ledger and the provenance-locked founder narrative.

The API the web layer and the package (S6) call:

    from uwcopilot.studio.narrative import build_founder_ledger, render_narrative
    ledger = build_founder_ledger(case, feasibility, site_score, path_result)    # evidence.Ledger, SYNTHETIC label
    result = render_narrative(ledger)                       # NarrativeResult; provider "template" (default)
    result.text, result.audit, result.fallback_used, result.prompt_id, result.prompt_sha256

Every founder-facing number is a claim of the ONE shared evidence ledger (uwcopilot.evidence), with a status that
says how much to trust it: USER_STATED (typed by the founder), ASSUMPTION (a profile or metro range, sourced or
"unsourced"), MODEL_ESTIMATE (a simulation or SiteIQ output, with the model, version, seed and inputs hash),
VERIFIED (a public data row with its locator, a document value with its page, or a program threshold),
CONFLICTING (a document disagrees with the founder) or UNKNOWN (needed but absent). The narrative is written with
slots only and rendered from the ledger, so it cannot contain a number the ledger does not hold.

Modules: ids (claim and assertion ids), units (display formats), derive (the derived-number status rule and claim
constructors), founder_claims, assumption_claims, site_claims, simulation_claims, path_claims, program_thresholds,
program_calcs, program_checks (the claims and checks), founder_ledger (build_founder_ledger), prose (safe words),
target_measures (which probability answers each Studio target), template (the deterministic draft), prompts (the
registry and the LLM request), generate (render_narrative), checker (the bound check and the mutation test), golden
(the 36 golden scenarios), evaluation (the measurements), models (documents, results).
"""

from uwcopilot.studio.narrative.founder_ledger import (
    FOUNDER_LEDGER_VERSION,
    PlanMismatch,
    SimulationTooShort,
    build_founder_ledger,
    ledger_from_plan,
    plan_sha256,
)
from uwcopilot.studio.narrative.generate import UnknownNarrativeProvider, check_draft, render_narrative
from uwcopilot.studio.narrative.models import AttemptRecord, FounderDocumentValue, NarrativeResult
from uwcopilot.studio.narrative.prompts import NARRATIVE_PROMPT_ID, RETRY_PROMPT_ID, all_prompt_hashes
from uwcopilot.studio.narrative.template import TEMPLATE_ID, required_refs, template_draft

__all__ = [
    "FOUNDER_LEDGER_VERSION",
    "NARRATIVE_PROMPT_ID",
    "RETRY_PROMPT_ID",
    "TEMPLATE_ID",
    "AttemptRecord",
    "FounderDocumentValue",
    "NarrativeResult",
    "PlanMismatch",
    "SimulationTooShort",
    "UnknownNarrativeProvider",
    "all_prompt_hashes",
    "build_founder_ledger",
    "check_draft",
    "ledger_from_plan",
    "plan_sha256",
    "render_narrative",
    "required_refs",
    "template_draft",
]

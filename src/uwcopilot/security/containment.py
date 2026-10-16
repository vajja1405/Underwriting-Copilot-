"""L3 containment: the structural controls that make decision-level injection success 0 by construction (S2).

A payload can at most change the TEXT a model writes. It cannot change a decision, because:

1. the answer format has no decision field. answer_output_schema() is the JSON schema for a grounded answer
   ({"answer", "citations"}): citations are an enum of the retrieved POLICY ids only (never an applicant APP-* id),
   and no property can carry an approval, a decline or a status. It is the schema the local model's `format`
   option would enforce in an L3 run (not run through the model in Tier 1);
2. no tool on the allow-list (tools/registry.py) can approve, decline, clear or decide anything, and an LLM may only
   request the read-only guideline search (agent/tool_runner.py);
3. the recommendation comes from the deterministic reference policy (agent/policy.py), which never reads free text
   except through the tripwire's yes/no flag, and a flag can only add a blocking referral to a human;
4. the memo validators reject decision wording (agent/claim_checker.py, provenance/memo_grammar.py), and the
   final decision is made only by a human reviewer at the checkpoint (agent/hitl.py, agent/graph.py).

tests/security/test_containment.py asserts each of these over every payload. The action-level success rate is
therefore not an empirical rate; it is enforced by tests.

The word lists below are what those tests search for. They are deliberately broad: a false alarm costs a code
review, a miss would hide a path from text to decision.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from uwcopilot.rag.prompt import is_applicant_id

# Property names that would let a model's output (or a tool's) carry a decision.
DECISION_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "decision",
        "decisions",
        "recommendation",
        "disposition",
        "approve",
        "approved",
        "approval",
        "decline",
        "declined",
        "verdict",
        "final_status",
        "action",
        "override",
        "bind",
        "bound",
    }
)
# Verbs a tool name must not contain: a tool that does any of these could change a decision.
DECISION_VERBS: tuple[str, ...] = (
    "approve",
    "decline",
    "deny",
    "reject",
    "bind",
    "fund",
    "clear",
    "dismiss",
    "override",
    "decide",
    "disposition",
    "record_decision",
    "set_recommendation",
)
ANSWER_SCHEMA_TITLE = "GroundedAnswer"


def answer_output_schema(policy_ids: Sequence[str]) -> dict[str, Any]:
    """The L3 answer schema: {"answer": str, "citations": [one of policy_ids]}, nothing else.

    Raises ValueError when policy_ids is empty or contains an applicant id (APP-...): an applicant's own document
    can never be a citable policy source.
    """
    ids = list(dict.fromkeys(policy_ids))
    if not ids:
        raise ValueError("an answer schema needs at least one policy id to cite")
    applicant = [identifier for identifier in ids if is_applicant_id(identifier)]
    if applicant:
        raise ValueError(f"applicant ids cannot be policy citations: {applicant}")
    return {
        "title": ANSWER_SCHEMA_TITLE,
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "string", "enum": ids}},
        },
        "required": ["answer", "citations"],
        "additionalProperties": False,
    }


def schema_property_names(schema: Any) -> set[str]:
    """Every property name anywhere in a JSON schema (nested objects, arrays, $defs, anyOf ...)."""
    names: set[str] = set()
    if isinstance(schema, Mapping):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, Mapping):
                names.update(str(name) for name in value)
            names.update(schema_property_names(value))
    elif isinstance(schema, list):
        for item in schema:
            names.update(schema_property_names(item))
    return names


def decision_fields_in(schema: Any) -> list[str]:
    """The property names of a schema that could carry a decision (sorted; empty when none)."""
    found = []
    for name in schema_property_names(schema):
        if name.lower() in DECISION_FIELD_NAMES:
            found.append(name)
    return sorted(found)


def decision_verbs_in(name: str) -> list[str]:
    """The DECISION_VERBS contained in a tool name (case-insensitive; empty when none)."""
    lowered = name.lower()
    return [verb for verb in DECISION_VERBS if verb in lowered]


def tools_with_decision_verbs(tool_names: Iterable[str]) -> dict[str, list[str]]:
    """{tool name: decision verbs it contains} for the names that contain any."""
    found: dict[str, list[str]] = {}
    for name in tool_names:
        verbs = decision_verbs_in(name)
        if verbs:
            found[name] = verbs
    return found

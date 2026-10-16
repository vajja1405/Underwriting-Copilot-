"""Assertions of the evidence ledger (Phase 4, SIG step 3; ledger.py builds the whole ledger).

One assertion per rule outcome of the reference policy: findings (result False), passed checks (True) and rules
the policy could not evaluate (None), with the trigger id, the guideline section and the three phrases the memo
renderer prints for {{assert:ID}} (ledger_tables.TOPIC_PHRASES; no digits, so no number enters a memo this way).

A rule that rests on a value no document provided (ledger_tables.ASSERTION_UNKNOWN_PATTERNS over the ledger's
UNKNOWN claims) lists those claims in depends_on_unknown, and a PASS that rests on one is shown as not evaluated
(result None): the policy passed it on a default, so the memo must not say it passed.
"""

import re
from typing import Any

from uwcopilot.agent.policy import DOCUMENT_RULE_PREFIX, PolicyResult
from uwcopilot.evidence import ledger_tables as tables
from uwcopilot.evidence.ledger_models import Assertion

NOT_CONFIRMED_NOTE = (
    " Not shown as passed: the rule passed on values no document provided ({ids}), so it is not evaluated."
)


def rule_phrases(rule: str) -> tuple[str, str, str, str]:
    """(expression, phrase_true, phrase_false, phrase_unknown) for a rule (TOPIC_PHRASES, else the defaults)."""
    if rule.startswith(DOCUMENT_RULE_PREFIX):
        topic = tables.DOCUMENT_RULE_TOPIC
    else:
        topic = tables.RULE_TOPIC.get(rule, "")
    if topic:
        return tables.TOPIC_PHRASES[topic]
    return (f"rule {rule}", *tables.DEFAULT_PHRASES)


def assertion_id(rule: str, used: set[str]) -> str:
    """'A-<rule>', with '-2', '-3' ... when the rule occurs more than once."""
    base = f"A-{rule}"
    candidate = base
    number = 2
    while candidate in used:
        candidate = f"{base}-{number}"
        number += 1
    used.add(candidate)
    return candidate


def unknown_dependencies(rule: str, unknown_ids: list[str]) -> tuple[str, ...]:
    """The UNKNOWN claims a rule rests on (tables.ASSERTION_UNKNOWN_PATTERNS)."""
    patterns = [re.compile(pattern) for pattern in tables.ASSERTION_UNKNOWN_PATTERNS.get(rule, ())]
    return tuple(claim_id for claim_id in unknown_ids if any(pattern.search(claim_id) for pattern in patterns))


def with_unknown_dependencies(assertion: Assertion, unknown_ids: list[str]) -> Assertion:
    """The assertion with the UNKNOWN claims it rests on; a PASS that rests on one becomes not evaluated (None)."""
    found = unknown_dependencies(assertion.rule, unknown_ids)
    if not found:
        return assertion
    update: dict[str, Any] = {"depends_on_unknown": found}
    if assertion.result is True:
        update["result"] = None
        update["detail"] = assertion.detail + NOT_CONFIRMED_NOTE.format(ids=", ".join(found))
    return assertion.model_copy(update=update)


def policy_assertions(policy_result: PolicyResult, unknown_ids: list[str] | None = None) -> list[Assertion]:
    """Findings (result False), passed checks (True) and not-evaluated rules (None), in that order. unknown_ids (the
    ledger's UNKNOWN claims) mark the rules that rest on values no document provided (with_unknown_dependencies)."""
    assertions = base_assertions(policy_result)
    return [with_unknown_dependencies(assertion, unknown_ids or []) for assertion in assertions]


def base_assertions(policy_result: PolicyResult) -> list[Assertion]:
    """The policy result's outcomes as assertions, before the unread-value check."""
    used: set[str] = set()
    assertions = []
    for finding in policy_result.findings:
        expression, phrase_true, phrase_false, phrase_unknown = rule_phrases(finding.rule)
        assertions.append(
            Assertion(
                id=assertion_id(finding.rule, used),
                rule=finding.rule,
                rule_ref=finding.trigger_ids[0] if finding.trigger_ids else None,
                section_id=finding.section_ids[0] if finding.section_ids else None,
                section_ids=tuple(finding.section_ids),
                expression=expression,
                result=False,
                phrase_true=phrase_true,
                phrase_false=phrase_false,
                phrase_unknown=phrase_unknown,
                detail=finding.detail,
                trigger_ids=tuple(finding.trigger_ids),
                evidence_ids=tuple(finding.evidence_ids),
                outcome=finding.outcome,
                blocking=finding.blocking,
            )
        )
    for check in policy_result.checks_passed:
        expression, phrase_true, phrase_false, phrase_unknown = rule_phrases(check.rule)
        assertions.append(
            Assertion(
                id=assertion_id(check.rule, used),
                rule=check.rule,
                section_id=check.section_ids[0] if check.section_ids else None,
                section_ids=tuple(check.section_ids),
                expression=expression,
                result=True,
                phrase_true=phrase_true,
                phrase_false=phrase_false,
                phrase_unknown=phrase_unknown,
                detail=check.detail,
                evidence_ids=tuple(check.evidence_ids),
            )
        )
    for item in policy_result.not_evaluated:
        expression, phrase_true, phrase_false, phrase_unknown = rule_phrases(item.rule)
        assertions.append(
            Assertion(
                id=assertion_id(item.rule, used),
                rule=item.rule,
                section_id=item.section_ids[0] if item.section_ids else None,
                section_ids=tuple(item.section_ids),
                expression=expression,
                result=None,
                phrase_true=phrase_true,
                phrase_false=phrase_false,
                phrase_unknown=phrase_unknown,
                detail=item.reason,
            )
        )
    return assertions

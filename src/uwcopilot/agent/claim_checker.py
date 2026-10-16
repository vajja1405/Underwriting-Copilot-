"""Deterministic checks on a drafted memo. No model is involved, so the checks cannot be talked out of anything.

For each memo:
- invalid_memo: the answer was not a valid memo JSON object, or no memo could be drafted (set by the graph);
- recommendation_mismatch: the memo's recommendation differs from the reference policy's;
- no_reasons: the memo gives no reasons at all;
- no_citation: a reason cites nothing;
- unknown_citation: a reason cites an id that is not in the evidence registry (a fabricated citation);
- unsupported_number: a number in a reason does not appear in any item that reason cites (for example
  "DSCR 1.45 [CALC-dscr]" when the calculation says 1.18), or a number in the summary appears in no cited item;
- finding_not_covered: a finding of the reference policy is not covered by any reason (a memo may not hide a
  sanctions hit or an embedded instruction);
- decision_language: wording that states a decision ("is approved", "has been declined", "final decision"), a
  label with a verdict ("DECISION: APPROVE", "Status: declined") or a verdict stamp in capitals ("APPROVED-7731").
  The last two, and "cleared" in the passive form ("the alert was cleared"), were added by the S2 containment tests
  (tests/security/test_containment.py), which found that a memo echoing an injected "DECISION: APPROVE" passed.
  The search runs on the normalized text (uwcopilot.security.normalize.detection_text), so wording hidden with
  zero-width characters, lookalike letters, fullwidth forms or Unicode tag characters is found too: a reviewer
  reads "a<zero-width space>pproved" as "approved".

A number is supported when an evidence number rounds to the same value at the precision the memo wrote it with
("1.18" matches 1.1834; "12.5%" matches 12.47). Signs are ignored (a gap of -3.5% is written "3.5%" too).
Passing these checks does not prove the memo is faithful; a reason can cite the right item and still misread
it. Faithfulness needs a judge model and is "not measured" without an LLM key.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.evidence import EvidenceItem
from uwcopilot.agent.memo import MemoDraft
from uwcopilot.agent.policy import PolicyResult
from uwcopilot.rag.citations import extract_cited_ids
from uwcopilot.security.normalize import detection_text

IssueKind = Literal[
    "invalid_memo",
    "recommendation_mismatch",
    "no_reasons",
    "no_citation",
    "unknown_citation",
    "unsupported_number",
    "finding_not_covered",
    "decision_language",
]
REASON_LEVEL_KINDS = frozenset({"no_citation", "unknown_citation", "unsupported_number"})
# Ids and codes whose digits are not claims: section ids, trigger ids, tool-result ids, calc ids, party ids.
ID_TOKENS = re.compile(
    r"\[[^\[\]]*\]|\b(?:R-)?[A-Z]{1,4}-\d+(?:\.\d+)?\b|\bCALC-[a-z0-9_]+\b|\bAPP-[a-z0-9_]+\b|\bCHK-[a-z0-9_]+\b"
    r"|\b(?:APP|O|L)\d+\b|\bTR-\d+\b"
)
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")
DECISION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:is|are|has been|have been|was|were|hereby|now)\s+"
        r"(?:approved|declined|denied|rejected|bound|funded|cleared)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bfinal decision\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+(?:approve|decline|deny)\b", re.IGNORECASE),
    re.compile(r"\b(?:cleared|dismissed)\s+(?:the\s+)?(?:sanctions|screening|alert|match)\b", re.IGNORECASE),
    re.compile(r"\bfalse positive\b", re.IGNORECASE),
    # A label followed by a verdict: "Decision: approve", "Status - declined", "Outcome = funded" (added in S2).
    re.compile(
        r"\b(?:decision|status|outcome|disposition|verdict|determination)\s*[:=-]\s*"
        r"(?:approved?|declined?|denied|deny|rejected?|bound|funded|cleared)\b",
        re.IGNORECASE,
    ),
    # A verdict stamp written in capitals: "APPROVED", "APPROVED-7731", "DECLINED" (added in S2; case-sensitive).
    re.compile(r"\b(?:APPROVED|DECLINED|DENIED|REJECTED)\b"),
)


class ClaimIssue(BaseModel):
    """One problem found in a memo. reason_index is None for memo-level problems."""

    model_config = ConfigDict(extra="forbid")

    kind: IssueKind
    reason_index: int | None = None
    detail: str


class MemoCheck(BaseModel):
    """The checker's verdict on one memo."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[ClaimIssue] = Field(default_factory=list)
    total_reasons: int = 0
    unsupported_reasons: int = 0
    total_citations: int = 0
    valid_citations: int = 0

    def feedback(self) -> list[str]:
        """The issues as short sentences, for the regeneration prompt and the reviewer."""
        return [
            f"{issue.kind}"
            + (f" (reason {issue.reason_index + 1})" if issue.reason_index is not None else "")
            + f": {issue.detail}"
            for issue in self.issues
        ]


def parse_number(raw: str) -> tuple[float, int]:
    """'1,234.50' -> (1234.5, 2): the value and how many decimals were written."""
    cleaned = raw.replace(",", "")
    decimals = len(cleaned.split(".")[1]) if "." in cleaned else 0
    return float(cleaned), decimals


def numbers_in(text: str) -> list[str]:
    """Every number written in text, after removing citation brackets and ids whose digits are not claims."""
    return NUMBER.findall(ID_TOKENS.sub(" ", text))


def evidence_numbers(items: list[EvidenceItem]) -> list[float]:
    """Every number in the given evidence items (ids removed first, as for claims)."""
    values = []
    for item in items:
        values.extend(abs(parse_number(raw)[0]) for raw in numbers_in(item.text))
    return values


def is_supported(raw: str, values: list[float]) -> bool:
    """True when some evidence value rounds to the claimed value at the claimed precision."""
    claimed, decimals = parse_number(raw)
    target = round(abs(claimed), decimals)
    return any(round(value, decimals) == target for value in values)


def decision_language(text: str) -> list[str]:
    """Phrases that state a decision instead of recommending one, searched in the normalized text (see above)."""
    readable = detection_text(text)
    found = []
    for pattern in DECISION_PATTERNS:
        found.extend(match.group(0) for match in pattern.finditer(readable))
    return found


def reason_citations(text: str, listed: list[str]) -> list[str]:
    """The reason's cited ids: its citations list plus any [ID] written inside its text, without duplicates."""
    return list(dict.fromkeys([*listed, *extract_cited_ids(text)]))


def check_memo(memo: MemoDraft, evidence: dict[str, EvidenceItem], policy: PolicyResult) -> MemoCheck:
    """Run every check and count citations and unsupported reasons."""
    issues: list[ClaimIssue] = []
    if memo.recommendation != policy.recommendation:
        issues.append(
            ClaimIssue(
                kind="recommendation_mismatch",
                detail=f"memo says {memo.recommendation!r}, the reference policy says {policy.recommendation!r}",
            )
        )
    if not memo.reasons:
        issues.append(ClaimIssue(kind="no_reasons", detail="the memo gives no reasons"))

    total_citations = 0
    valid_citations = 0
    unsupported_reasons = 0
    all_cited: set[str] = set()
    for index, reason in enumerate(memo.reasons):
        cited = reason_citations(reason.text, reason.citations)
        total_citations += len(cited)
        known = [citation for citation in cited if citation in evidence]
        unknown = [citation for citation in cited if citation not in evidence]
        valid_citations += len(known)
        all_cited.update(known)
        reason_issues: list[ClaimIssue] = []
        if not cited:
            reason_issues.append(ClaimIssue(kind="no_citation", reason_index=index, detail="the reason cites nothing"))
        if unknown:
            reason_issues.append(
                ClaimIssue(kind="unknown_citation", reason_index=index, detail=f"unknown id(s): {', '.join(unknown)}")
            )
        values = evidence_numbers([evidence[citation] for citation in known])
        unsupported = [raw for raw in numbers_in(reason.text) if not is_supported(raw, values)]
        if unsupported:
            reason_issues.append(
                ClaimIssue(
                    kind="unsupported_number",
                    reason_index=index,
                    detail=f"number(s) {', '.join(unsupported)} not found in the cited evidence",
                )
            )
        if reason_issues:
            unsupported_reasons += 1
        issues.extend(reason_issues)

    summary_values = evidence_numbers([evidence[citation] for citation in all_cited])
    summary_unsupported = [raw for raw in numbers_in(memo.summary) if not is_supported(raw, summary_values)]
    if summary_unsupported:
        issues.append(
            ClaimIssue(
                kind="unsupported_number",
                detail=f"summary number(s) {', '.join(summary_unsupported)} not found in any cited evidence",
            )
        )
    for finding in policy.findings:
        finding_ids = set(finding.section_ids) | set(finding.evidence_ids)
        if not finding_ids & all_cited:
            issues.append(
                ClaimIssue(
                    kind="finding_not_covered", detail=f"no reason cites the evidence for finding {finding.rule!r}"
                )
            )
    wording = decision_language(" ".join([memo.summary, *(reason.text for reason in memo.reasons)]))
    if wording:
        issues.append(
            ClaimIssue(kind="decision_language", detail=f"decision wording: {', '.join(sorted(set(wording)))}")
        )
    return MemoCheck(
        passed=not issues,
        issues=issues,
        total_reasons=len(memo.reasons),
        unsupported_reasons=unsupported_reasons,
        total_citations=total_citations,
        valid_citations=valid_citations,
    )

"""Contradictions between documents, with a deterministic explanation, and the contradiction graph (Phase 2).

build_contradictions(extraction, checks) turns every issue of a failing check into one Contradiction:

- type: the problem type the check detects (CONTRADICTION_TYPE_BY_CHECK). The names are the problem types of
  docintel/truth.py except the missing_* ones (copied here as CONTRADICTION_TYPES, because the pipeline must
  never import truth.py). Checks about missing documents or pages, account conduct, loss-run coverage and
  valuation age find gaps, not disagreements, so they create no contradiction. A failing check without issues
  creates none either (for example application ownership percentages that do not add up to 100%: a problem
  inside one document, not a disagreement between two).
- sides: the cited values on both sides (sides[:side_a_count] is the first side: the application, the majority
  of documents, or the printed total; the rest is the other side). One-sided issues (an owner missing from one
  document, embedded instructions) have an empty second side. supporting holds evidence that is on neither side
  but was used to explain (deposit rows, the P&L, the tax returns, the cover email).
- explanation, explanation_code, explanation_detail: from contradiction_rules.explain (deterministic; no LLM),
  then reviewed by contradiction_ocr.review: a disagreement that an OCR misread may explain (a low-confidence OCR
  value on a side, a loss run read by OCR said to miss a claim, an OCR owners table that does not add up) becomes
  unknown / possible_ocr_misread and its severity drops one level.

The contradiction graph is a networkx MultiDiGraph. Nodes (attribute "kind"):
- entity: "business", "owner:O1", "location:L1", "carrier:<carrier name, casefolded>";
- document: the document id ("D01"), with its doc_type when the extraction lists it;
- page: "p<packet page number>" ("p3");
- value: one cited value, id "value:<doc_id>|p<page>|L<line>|<field>|<key>", with field, key, value, doc_id,
  page and line attributes.
Edges (attribute "relation", and the same text as the edge key): entity -has_value-> value, value -cited_on->
page, page -in-> document, and value -conflicts_with-> value for every pair across the two sides of a
contradiction (edge key and attribute contradiction_id = the contradiction id). Every document and page of the
extraction's document list is in the graph even without contradictions. graph_json is networkx node_link_data
with edges under "edges"; it is plain JSON (nx.node_link_graph(graph_json, directed=True, multigraph=True,
edges="edges") reads it back).
"""

from typing import Any, Literal

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.docintel.check_models import CheckIssue, CheckResult, Evidence, Severity, unique_evidence
from uwcopilot.docintel.contradiction_ocr import review
from uwcopilot.docintel.contradiction_rules import Explanation, explain
from uwcopilot.docintel.schemas import PacketExtraction

# truth.PROBLEM_TYPES without missing_document and missing_pages (duplicated on purpose; see the module docstring).
CONTRADICTION_TYPES: tuple[str, ...] = (
    "name_mismatch",
    "fein_mismatch",
    "owner_mismatch",
    "prompt_injection",
    "revenue_gap",
    "balance_discontinuity",
    "debt_payment_mismatch",
    "loss_run_total_mismatch",
    "paid_reserve_mismatch",
    "tiv_mismatch",
    "claims_history_mismatch",
)
ContradictionType = Literal[CONTRADICTION_TYPES]  # type: ignore[valid-type]

CONTRADICTION_TYPE_BY_CHECK: dict[str, str] = {
    "revenue_vs_deposits": "revenue_gap",
    "tax_return_vs_pnl": "revenue_gap",
    "balance_continuity": "balance_discontinuity",
    "name_consistency": "name_mismatch",
    "fein_consistency": "fein_mismatch",
    "owner_consistency": "owner_mismatch",
    "debt_payment_consistency": "debt_payment_mismatch",
    "loss_run_line_balance": "paid_reserve_mismatch",
    "loss_run_totals": "loss_run_total_mismatch",
    "claims_history_consistency": "claims_history_mismatch",
    "tiv_consistency": "tiv_mismatch",
    "embedded_instructions": "prompt_injection",
}


class Contradiction(BaseModel):
    """Two (or more) documents that disagree, with the cited values and a candidate explanation."""

    model_config = ConfigDict(extra="forbid")

    contradiction_id: str = Field(pattern=r"^CT-\d{2,}$")
    type: ContradictionType
    severity: Severity
    check_id: str
    issue: str
    entity: str
    fields: list[str]
    doc_ids: list[str]
    sides: list[Evidence]
    side_a_count: int = Field(ge=0)
    supporting: list[Evidence] = Field(default_factory=list)
    explanation: Explanation
    explanation_code: str
    explanation_detail: str
    guideline_refs: list[str]
    message: str
    values: dict[str, Any] = Field(default_factory=dict)

    def side_a(self) -> list[Evidence]:
        """The first side's evidence."""
        return self.sides[: self.side_a_count]

    def side_b(self) -> list[Evidence]:
        """The other side's evidence (empty for a one-sided issue)."""
        return self.sides[self.side_a_count :]


class ContradictionReport(BaseModel):
    """Every contradiction in a packet and the contradiction graph as JSON."""

    model_config = ConfigDict(extra="forbid")

    contradictions: list[Contradiction]
    graph_json: dict[str, Any]


def doc_ids_in(evidence: list[Evidence]) -> list[str]:
    """Distinct document ids in the evidence, in first-seen order."""
    seen: list[str] = []
    for item in evidence:
        if item.doc_id is not None and item.doc_id not in seen:
            seen.append(item.doc_id)
    return seen


def make_contradiction(
    number: int, result: CheckResult, issue: CheckIssue, extraction: PacketExtraction
) -> Contradiction:
    """One contradiction from one failing check issue."""
    verdict = explain(result.check_id, issue, extraction)
    verdict, severity = review(result.check_id, issue, verdict, result.severity, extraction)
    sides = issue.side_a + issue.side_b
    return Contradiction(
        contradiction_id=f"CT-{number:02d}",
        type=CONTRADICTION_TYPE_BY_CHECK[result.check_id],
        severity=severity,
        check_id=result.check_id,
        issue=issue.issue,
        entity=issue.entity,
        fields=issue.fields,
        doc_ids=doc_ids_in(sides),
        sides=sides,
        side_a_count=len(issue.side_a),
        supporting=unique_evidence(issue.supporting + verdict.supporting),
        explanation=verdict.explanation,
        explanation_code=verdict.code,
        explanation_detail=verdict.detail,
        guideline_refs=result.guideline_refs,
        message=issue.message,
        values=issue.data,
    )


def contradictions_from_checks(extraction: PacketExtraction, checks: list[CheckResult]) -> list[Contradiction]:
    """A contradiction for every issue of every failing check that detects a disagreement, in check order."""
    contradictions: list[Contradiction] = []
    for result in checks:
        if result.status != "fail" or result.check_id not in CONTRADICTION_TYPE_BY_CHECK:
            continue
        for issue in result.issues:
            contradictions.append(make_contradiction(len(contradictions) + 1, result, issue, extraction))
    return contradictions


def page_node(page: int) -> str:
    """Graph node id of a packet page."""
    return f"p{page}"


def value_node(evidence: Evidence) -> str:
    """Graph node id of one cited value (see the module docstring)."""
    return f"value:{evidence.doc_id}|p{evidence.page}|L{evidence.line}|{evidence.field}|{evidence.key or ''}"


def add_document(graph: nx.MultiDiGraph, doc_id: str, doc_type: str | None = None) -> None:
    """Add a document node (keeping a known doc_type when the node already exists)."""
    if doc_id in graph and doc_type is None:
        return
    graph.add_node(doc_id, kind="document", doc_type=doc_type)


def add_page(graph: nx.MultiDiGraph, page: int, doc_id: str | None) -> str:
    """Add a page node and its page -in-> document edge; returns the page node id."""
    node = page_node(page)
    graph.add_node(node, kind="page", page=page)
    if doc_id is not None:
        add_document(graph, doc_id)
        graph.add_edge(node, doc_id, key="in", relation="in")
    return node


def add_value(graph: nx.MultiDiGraph, entity: str, evidence: Evidence) -> str:
    """Add a value node with its entity -has_value-> value and value -cited_on-> page edges; returns its id."""
    node = value_node(evidence)
    graph.add_node(
        node,
        kind="value",
        field=evidence.field,
        key=evidence.key,
        value=evidence.value,
        doc_id=evidence.doc_id,
        page=evidence.page,
        line=evidence.line,
    )
    graph.add_node(entity, kind="entity")
    graph.add_edge(entity, node, key="has_value", relation="has_value", field=evidence.field)
    if evidence.page is not None:
        page = add_page(graph, evidence.page, evidence.doc_id)
        graph.add_edge(node, page, key="cited_on", relation="cited_on")
    elif evidence.doc_id is not None:
        add_document(graph, evidence.doc_id)
    return node


def build_graph(extraction: PacketExtraction, contradictions: list[Contradiction]) -> nx.MultiDiGraph:
    """The contradiction graph (see the module docstring)."""
    graph = nx.MultiDiGraph(packet_id=extraction.packet_id, line=extraction.line)
    for info in extraction.documents:
        add_document(graph, info.doc_id, info.doc_type)
        for page in info.pages:
            add_page(graph, page, info.doc_id)
    for contradiction in contradictions:
        first = [add_value(graph, contradiction.entity, item) for item in contradiction.side_a()]
        second = [add_value(graph, contradiction.entity, item) for item in contradiction.side_b()]
        for source in first:
            for target in second:
                if source == target:
                    continue
                graph.add_edge(
                    source,
                    target,
                    key=contradiction.contradiction_id,
                    relation="conflicts_with",
                    contradiction_id=contradiction.contradiction_id,
                    type=contradiction.type,
                )
    return graph


def graph_to_json(graph: nx.MultiDiGraph) -> dict[str, Any]:
    """networkx node-link JSON of the graph, with edges under "edges"."""
    return nx.node_link_data(graph, edges="edges")


def build_contradictions(extraction: PacketExtraction, checks: list[CheckResult]) -> ContradictionReport:
    """All contradictions of a packet (from its check results) and the contradiction graph."""
    contradictions = contradictions_from_checks(extraction, checks)
    graph = build_graph(extraction, contradictions)
    return ContradictionReport(contradictions=contradictions, graph_json=graph_to_json(graph))

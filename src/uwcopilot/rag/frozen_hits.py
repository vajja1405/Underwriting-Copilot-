"""Frozen retrieval results: the file format shared by the generation and injection evaluations.

scripts/freeze_retrieval.py runs retrieval ONCE for every evaluation question and writes the top hits to
eval/generation/frozen_hits.jsonl (committed). The generation evaluation (scripts/eval_generation.py) and the
injection evaluation read only this file, so a change of model or prompt changes generation and nothing else: the
documents in the prompt are byte-for-byte the same for every configuration that is compared.

One JSON line per question:

    {"question_id": "NB01", "question": "...", "category": "single_section",
     "question_file": "eval/rag_questions.jsonl", "retrieval_mode": "hybrid_rerank", "top_k": 5,
     "hits": [GuidelineHit, ...],                      # rank order, best first
     "hit_locations": [{"doc_kind": "guideline", "pages": [], "paragraphs": []}, ...],
     "gold_ranks": [1]}                                # per gold reference: 1-based rank in hits, or null

gold_ranks are computed at freeze time with rag.eval_set.gold_first_ranks on the full stored payloads (regulatory
gold needs the page and paragraph numbers, which a GuidelineHit does not carry). A question's gold is "in context"
for a prompt with the first k hits when every gold reference has a rank <= k.

The manifest next to it (frozen_hits_manifest.json) records how the hits were made: retrieval mode, the corpus
checksum, the question files and their sha256, counts, and the sha256 of the hits file itself.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.rag.eval_set import QUESTIONS_FILE, Category, EvalQuestion, leaf_refs, load_questions
from uwcopilot.tools.schemas import GuidelineHit

GENERATION_EVAL_DIR: Path = REPO_ROOT / "eval" / "generation"
FROZEN_HITS_FILE: Path = GENERATION_EVAL_DIR / "frozen_hits.jsonl"
FROZEN_MANIFEST_FILE: Path = GENERATION_EVAL_DIR / "frozen_hits_manifest.json"
EXTRA_UNANSWERABLE_FILE: Path = REPO_ROOT / "eval" / "rag_unanswerable_extra.jsonl"
KEY_FACTS_FILE: Path = REPO_ROOT / "eval" / "rag_key_facts.jsonl"
QUESTION_FILES: tuple[Path, ...] = (QUESTIONS_FILE, EXTRA_UNANSWERABLE_FILE)

MODE_HYBRID_RERANK = "hybrid_rerank"
MODE_BM25_GUIDELINES_ONLY = "bm25_offline_guidelines_only"
FrozenMode = Literal["hybrid_rerank", "bm25_offline_guidelines_only"]
FROZEN_MODES: tuple[str, ...] = (MODE_HYBRID_RERANK, MODE_BM25_GUIDELINES_ONLY)


class HitLocation(BaseModel):
    """Where a hit came from: document kind plus the PDF pages / letter paragraphs it covers (empty for guidelines)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_kind: Literal["guideline", "regulatory"]
    pages: list[int] = Field(default_factory=list)
    paragraphs: list[int] = Field(default_factory=list)


class FrozenHitsRecord(BaseModel):
    """The frozen retrieval result for one question (one line of frozen_hits.jsonl)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    question: str
    category: Category
    question_file: str
    retrieval_mode: FrozenMode
    top_k: int = Field(ge=1)
    hits: list[GuidelineHit]
    hit_locations: list[HitLocation]
    gold_ranks: list[int | None]

    @model_validator(mode="after")
    def locations_match_hits(self) -> "FrozenHitsRecord":
        """Every hit has a location, ranks run 1..n, and there are at most top_k hits."""
        if len(self.hit_locations) != len(self.hits):
            raise ValueError(f"{self.question_id}: {len(self.hits)} hits but {len(self.hit_locations)} locations")
        if len(self.hits) > self.top_k:
            raise ValueError(f"{self.question_id}: more hits than top_k")
        expected_ranks = list(range(1, len(self.hits) + 1))
        if [hit.rank for hit in self.hits] != expected_ranks:
            raise ValueError(f"{self.question_id}: hit ranks must be 1..n in order")
        return self

    @property
    def answerable(self) -> bool:
        """True when the question has gold references."""
        return self.category != "unanswerable"

    def context_hits(self, k: int) -> list[GuidelineHit]:
        """The first k hits: what goes into the prompt for a configuration with this k."""
        if k < 1:
            raise ValueError("k must be at least 1")
        return list(self.hits[:k])

    def gold_in_context(self, k: int) -> bool | None:
        """True when every gold reference is within the first k hits; None for an unanswerable question."""
        if not self.answerable:
            return None
        for rank in self.gold_ranks:
            if rank is None or rank > k:
                return False
        return True


def needs_regulatory(question: EvalQuestion) -> bool:
    """True when any gold reference (including any_of alternatives) points into a regulatory document."""
    for ref in question.gold:
        for leaf in leaf_refs(ref):
            if leaf.doc is not None:
                return True
    return False


def load_eval_questions(files: Sequence[Path] = QUESTION_FILES) -> list[tuple[EvalQuestion, Path]]:
    """Every question from the given files, in file order, with the file it came from. Ids must be unique."""
    questions: list[tuple[EvalQuestion, Path]] = []
    seen: dict[str, Path] = {}
    for path in files:
        for question in load_questions(path):
            if question.id in seen:
                raise ValueError(f"question id {question.id} appears in both {seen[question.id].name} and {path.name}")
            seen[question.id] = path
            questions.append((question, path))
    return questions


def read_frozen_hits(path: Path = FROZEN_HITS_FILE) -> list[FrozenHitsRecord]:
    """Parse frozen_hits.jsonl; question ids must be unique."""
    records: list[FrozenHitsRecord] = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = FrozenHitsRecord.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"{path.name} line {line_number}: {error}") from error
        if record.question_id in seen:
            raise ValueError(f"{path.name}: duplicate question id {record.question_id}")
        seen.add(record.question_id)
        records.append(record)
    return records


def frozen_hits_line(record: FrozenHitsRecord) -> str:
    """One JSONL line for a record (stable key order, so the file hash only changes when the content does)."""
    return json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def read_manifest(path: Path = FROZEN_MANIFEST_FILE) -> dict:
    """The frozen-hits manifest as a dict."""
    return json.loads(path.read_text(encoding="utf-8"))

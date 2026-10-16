"""The screening pipeline for one name: normalize -> block -> pre-rank -> rules score -> rerank -> explain.

Stages and why each exists:
1. normalize (normalize.py): make spellings comparable.
2. block (blocking.py): cut ~2.6 million list names down to at most a few thousand candidates.
3. pre-rank: rapidfuzz scores every blocked name in C++ (average of token_set_ratio and token_sort_ratio) and
   keeps the best `prerank_top_names`. This is cheap and lets the slower Python scorer run on fewer names.
4. rules score (scoring.py): token-aligned Jaro-Winkler + token-set ratio + small attribute adjustment. Each list
   entry keeps its best-scoring name (primary name or alias).
5. rerank (reranker.py): the top `rerank_top_k` entries get an embedding cosine similarity; the final score
   blends rules and cosine with `reranker_weight` (0 means rules only).
6. alert: entries whose final score is at or above `threshold` become candidates on the ScreeningResult, each
   with an explanation. Any candidate makes the status "potential_match", which always goes to a human.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process

from uwcopilot.kyc.blocking import BlockingConfig, BlockingIndex, BlockingResult
from uwcopilot.kyc.countries import normalize_country
from uwcopilot.kyc.normalize import NormalizedName, normalize_name, normalize_query_name
from uwcopilot.kyc.reranker import TextEmbedder, blend, cosine_similarities
from uwcopilot.kyc.scoring import (
    AttributeCheck,
    NameScores,
    ScoringWeights,
    attribute_check,
    name_scores,
    rules_score,
    token_explanations,
)
from uwcopilot.kyc.watchlist import Watchlist
from uwcopilot.tools.schemas import ScreeningCandidate, ScreeningRequest, ScreeningResult

HUMAN_REVIEW_NOTE = (
    "potential match only: a human compliance reviewer must decide; this system never clears or confirms a match"
)


@dataclass(frozen=True)
class MatcherConfig:
    """Pipeline settings. threshold and reranker_weight come from results/kyc_metrics.json after evaluation."""

    blocking: BlockingConfig = field(default_factory=BlockingConfig)
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    prerank_top_names: int = 300
    rerank_top_k: int = 10
    reranker_weight: float = 0.0
    threshold: float = 0.9
    max_reported_candidates: int = 10


@dataclass
class CandidateMatch:
    """One list entry scored against the query, through its best-matching name."""

    entry_row: int
    name_id: int
    list_tokens: tuple[str, ...]
    name: NameScores
    attributes: AttributeCheck
    rules_score: float
    embedding_similarity: float | None = None

    def final_score(self, reranker_weight: float) -> float:
        """Rules score blended with the embedding similarity (rules only when the weight is 0)."""
        return blend(self.rules_score, self.embedding_similarity, reranker_weight)


@dataclass
class MatchOutcome:
    """Everything the pipeline found for one query, including stage sizes and timings for evaluation."""

    query: NormalizedName
    entity_type: str | None
    country: str | None
    blocking: BlockingResult
    prerank_name_ids: np.ndarray
    candidates: list[CandidateMatch]
    seconds_rules: float
    seconds_rerank: float

    def best_score(self, reranker_weight: float) -> float:
        """Highest final score over the reranked candidates (0.0 when blocking found nothing)."""
        return max((c.final_score(reranker_weight) for c in self.candidates), default=0.0)


def normalized_list_names(entries: pd.DataFrame) -> Iterator[tuple[int, tuple[str, ...]]]:
    """Yield (row, tokens) for the primary name and every alias of every entry, skipping duplicates per entry."""
    for row, (name, aliases, entity_type) in enumerate(
        zip(entries["name"], entries["aliases"], entries["entity_type"], strict=True)
    ):
        seen: set[tuple[str, ...]] = set()
        for text in (name, *aliases):
            tokens = normalize_name(text, entity_type).tokens
            if tokens and tokens not in seen:
                seen.add(tokens)
                yield row, tokens


class SanctionsMatcher:
    """Screens names against one Watchlist. Build once (the index takes about a minute at full size), reuse."""

    def __init__(
        self,
        watchlist: Watchlist,
        config: MatcherConfig | None = None,
        embedder: TextEmbedder | None = None,
        index: BlockingIndex | None = None,
    ) -> None:
        """Build the blocking index unless one is passed in. embedder=None disables the reranker."""
        self.watchlist = watchlist
        self.config = config or MatcherConfig()
        self.embedder = embedder
        start = time.perf_counter()
        self.index = index or BlockingIndex(normalized_list_names(watchlist.entries), self.config.blocking)
        self.index_build_seconds = time.perf_counter() - start
        entries = watchlist.entries
        self.entry_ids: list[str] = entries["list_entry_id"].tolist()
        self.entry_types: list[str] = entries["entity_type"].tolist()
        self.entry_countries: list[tuple[str, ...]] = entries["countries"].tolist()

    def prerank(self, query: NormalizedName, name_ids: np.ndarray) -> np.ndarray:
        """Keep the `prerank_top_names` blocked names with the best quick rapidfuzz score."""
        if len(name_ids) <= self.config.prerank_top_names:
            return name_ids
        choices = [self.index.name_texts[name_id] for name_id in name_ids]
        token_set = process.cdist([query.text], choices, scorer=fuzz.token_set_ratio)[0]
        token_sort = process.cdist([query.text], choices, scorer=fuzz.token_sort_ratio)[0]
        quick_score = (token_set + token_sort) / 2.0
        best = np.argsort(-quick_score, kind="stable")[: self.config.prerank_top_names]
        return name_ids[best]

    def score_names(
        self, query: NormalizedName, entity_type: str | None, country: str | None, name_ids: np.ndarray
    ) -> list[CandidateMatch]:
        """Rules-score every name; keep each entry's best name; return entries sorted by rules score."""
        best_by_entry: dict[int, CandidateMatch] = {}
        attribute_cache: dict[int, AttributeCheck] = {}
        weights = self.config.weights
        for name_id in name_ids:
            entry_row = int(self.index.name_entry[name_id])
            list_tokens = self.index.tokens_of_name(int(name_id))
            scores = name_scores(query.tokens, list_tokens, weights)
            if entry_row not in attribute_cache:
                attribute_cache[entry_row] = attribute_check(
                    entity_type, country, self.entry_types[entry_row], self.entry_countries[entry_row], weights
                )
            attributes = attribute_cache[entry_row]
            score = rules_score(scores, attributes, weights)
            current = best_by_entry.get(entry_row)
            if current is None or score > current.rules_score:
                best_by_entry[entry_row] = CandidateMatch(
                    entry_row=entry_row,
                    name_id=int(name_id),
                    list_tokens=list_tokens,
                    name=scores,
                    attributes=attributes,
                    rules_score=score,
                )
        return sorted(best_by_entry.values(), key=lambda c: (-c.rules_score, self.entry_ids[c.entry_row]))

    def match(self, name: str, entity_type: str | None, country: str | None = None) -> MatchOutcome:
        """Run the full pipeline for one name and return the top candidates with their scores."""
        start = time.perf_counter()
        query = normalize_query_name(name, entity_type)  # the screened name is untrusted: step 0 first
        country_code = normalize_country(country)
        blocking = self.index.candidates(query.tokens)
        prerank_ids = self.prerank(query, blocking.name_ids)
        candidates = self.score_names(query, entity_type, country_code, prerank_ids)[: self.config.rerank_top_k]
        seconds_rules = time.perf_counter() - start

        start = time.perf_counter()
        if self.embedder is not None and candidates:
            texts = [self.index.name_texts[c.name_id] for c in candidates]
            for candidate, similarity in zip(
                candidates, cosine_similarities(self.embedder, query.text, texts), strict=True
            ):
                candidate.embedding_similarity = float(similarity)
        seconds_rerank = time.perf_counter() - start
        return MatchOutcome(
            query=query,
            entity_type=entity_type,
            country=country_code,
            blocking=blocking,
            prerank_name_ids=prerank_ids,
            candidates=candidates,
            seconds_rules=seconds_rules,
            seconds_rerank=seconds_rerank,
        )

    def original_name(self, candidate: CandidateMatch) -> tuple[str, bool]:
        """The list's own spelling of the matched name, and whether it is an alias (not the primary name)."""
        entries = self.watchlist.entries
        entity_type = self.entry_types[candidate.entry_row]
        primary = entries["name"].iat[candidate.entry_row]
        if normalize_name(primary, entity_type).tokens == candidate.list_tokens:
            return primary, False
        for alias in entries["aliases"].iat[candidate.entry_row]:
            if normalize_name(alias, entity_type).tokens == candidate.list_tokens:
                return alias, True
        return primary, False

    def explain(self, outcome: MatchOutcome, candidate: CandidateMatch) -> list[str]:
        """Plain sentences an analyst can check: what matched, what agreed or disagreed, and the scores."""
        entries = self.watchlist.entries
        row = candidate.entry_row
        matched_name, is_alias = self.original_name(candidate)
        lines = [f"customer name normalized to '{outcome.query.text}'"]
        if outcome.query.removed:
            lines.append("stripped before matching: " + ", ".join(outcome.query.removed))
        if is_alias:
            lines.append(f"matched alias '{matched_name}' of list entry '{entries['name'].iat[row]}'")
        else:
            lines.append(f"matched primary list name '{matched_name}'")
        lines.extend(token_explanations(outcome.query.tokens, candidate.list_tokens, self.config.weights))
        lines.extend(candidate.attributes.notes)
        weight = self.config.reranker_weight
        score_line = (
            f"scores: token-aligned Jaro-Winkler {candidate.name.jaro_winkler_aligned:.3f}, "
            f"token-set ratio {candidate.name.token_set:.3f}, rules score {candidate.rules_score:.3f}"
        )
        if weight > 0 and candidate.embedding_similarity is not None and self.embedder is not None:
            score_line += (
                f", embedding cosine {candidate.embedding_similarity:.3f} ({self.embedder.model_name}, weight "
                f"{weight:.2f})"
            )
        score_line += f", final {candidate.final_score(weight):.3f} (alert threshold {self.config.threshold:.3f})"
        lines.append(score_line)
        programs = ", ".join(entries["programs"].iat[row]) or "none listed"
        lines.append(f"list: {entries['list_name'].iat[row]}; programs: {programs}")
        lines.append(HUMAN_REVIEW_NOTE)
        return lines

    def screen(self, request: ScreeningRequest) -> ScreeningResult:
        """Screen one name. Status is "potential_match" when any entry reaches the threshold, else "clear"."""
        outcome = self.match(request.name, request.entity_type, request.country)
        weight = self.config.reranker_weight
        alerts = [c for c in outcome.candidates if c.final_score(weight) >= self.config.threshold]
        alerts.sort(key=lambda c: -c.final_score(weight))
        entries = self.watchlist.entries
        screening_candidates = []
        for candidate in alerts[: self.config.max_reported_candidates]:
            row = candidate.entry_row
            matched_name, _ = self.original_name(candidate)
            screening_candidates.append(
                ScreeningCandidate(
                    list_entry_id=self.entry_ids[row],
                    list_name=entries["list_name"].iat[row],
                    source=entries["source"].iat[row],
                    matched_name=matched_name,
                    score=round(min(1.0, max(0.0, candidate.final_score(weight))), 4),
                    explanation=self.explain(outcome, candidate),
                )
            )
        status = "potential_match" if screening_candidates else "clear"
        return ScreeningResult(
            request=request,
            candidates=screening_candidates,
            status=status,
            list_snapshots=list(self.watchlist.snapshots),
        )

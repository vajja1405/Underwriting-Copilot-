"""A small pure-Python BM25 retriever over the guideline sections: the OFFLINE FALLBACK only.

The project's real retriever is Qdrant hybrid search with a cross-encoder reranker (rag/retriever.py, mode
"hybrid_rerank"). It needs the built index and the fastembed models. On a machine that has neither, the frozen
retrieval script (scripts/freeze_retrieval.py, mode "bm25_offline_guidelines_only") can still produce hits with
this module, so the generation evaluation can run. Hits made this way are labelled with that mode everywhere and
are never reported as hybrid_rerank results. The committed frozen hits come from hybrid_rerank.

What it indexes: one document per guideline section (title + text), from load_guideline_chunks(). The regulatory
documents are not included, which is why the mode name says "guidelines only".

Tokenizer: lowercase, then the word pattern of rag/eval_set.py (letters and digits, keeping inner dots and hyphens,
so "nbc-4.2" and "1.25" stay one token), with the same small stopword list removed. No stemming.

Scoring (Okapi BM25 with the Lucene-style non-negative idf), with k1 = 1.2 and b = 0.75:

    idf(t)      = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
    score(q, d) = sum over query terms t of idf(t) * tf(t, d) * (k1 + 1) / (tf(t, d) + k1 * (1 - b + b * |d| / avgdl))

N is the number of sections, df(t) the number of sections containing t, tf(t, d) the count of t in section d,
|d| the section length in tokens and avgdl the mean length. A repeated query word counts once per repetition.
Sections with score 0 (no shared word) are never returned; ties keep the corpus order.
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from uwcopilot.rag.chunks import Chunk
from uwcopilot.rag.eval_set import STOPWORDS, WORD
from uwcopilot.rag.guidelines import load_guideline_chunks
from uwcopilot.tools.schemas import GuidelineHit

DEFAULT_K1 = 1.2
DEFAULT_B = 0.75


@dataclass(frozen=True)
class BM25Params:
    """The two BM25 constants (fixed, not tuned on any evaluation set)."""

    k1: float = DEFAULT_K1
    b: float = DEFAULT_B


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens without stopwords (see the module docstring)."""
    tokens: list[str] = []
    for word in WORD.findall(text.lower()):
        if word not in STOPWORDS:
            tokens.append(word)
    return tokens


class BM25Index:
    """BM25 over a list of texts; documents are referred to by their position in that list."""

    def __init__(self, texts: Sequence[str], params: BM25Params | None = None) -> None:
        """Tokenize every text and precompute term counts, lengths and document frequencies."""
        if not texts:
            raise ValueError("BM25 needs at least one document")
        self.params = params or BM25Params()
        self.term_counts: list[Counter[str]] = [Counter(tokenize(text)) for text in texts]
        self.lengths: list[int] = [sum(counts.values()) for counts in self.term_counts]
        self.average_length = sum(self.lengths) / len(self.lengths)
        self.document_frequency: Counter[str] = Counter()
        for counts in self.term_counts:
            self.document_frequency.update(counts.keys())

    def idf(self, term: str) -> float:
        """Non-negative inverse document frequency of a term (0 for a term no document contains)."""
        df = self.document_frequency.get(term, 0)
        if df == 0:
            return 0.0
        n_documents = len(self.term_counts)
        return math.log(1.0 + (n_documents - df + 0.5) / (df + 0.5))

    def score(self, query: str, index: int) -> float:
        """BM25 score of one document for the query."""
        counts = self.term_counts[index]
        length_norm = 1.0 - self.params.b + self.params.b * self.lengths[index] / self.average_length
        total = 0.0
        for term in tokenize(query):
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            total += self.idf(term) * tf * (self.params.k1 + 1.0) / (tf + self.params.k1 * length_norm)
        return total

    def top_k(self, query: str, k: int) -> list[tuple[int, float]]:
        """(document position, score) of the k best documents with a positive score, best first."""
        if k < 1:
            raise ValueError("k must be at least 1")
        scored: list[tuple[int, float]] = []
        for index in range(len(self.term_counts)):
            value = self.score(query, index)
            if value > 0.0:
                scored.append((index, value))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:k]


class GuidelineBM25Retriever:
    """BM25 search over whole guideline sections, returning the same GuidelineHit objects as the real retriever."""

    def __init__(self, chunks: Sequence[Chunk] | None = None, params: BM25Params | None = None) -> None:
        """Index the given sections (default: every section of corpus/guidelines/*.md)."""
        self.chunks: list[Chunk] = list(chunks) if chunks is not None else load_guideline_chunks()
        texts = [f"{chunk.title}\n{chunk.text}" for chunk in self.chunks]
        self.index = BM25Index(texts, params)

    def search(self, query: str, top_k: int = 5) -> list[GuidelineHit]:
        """The top_k sections for the query as GuidelineHits (rank 1 = best)."""
        hits: list[GuidelineHit] = []
        for rank, (position, score) in enumerate(self.index.top_k(query, top_k), start=1):
            chunk = self.chunks[position]
            hits.append(
                GuidelineHit(
                    section_id=chunk.chunk_id,
                    title=chunk.title,
                    source_doc=chunk.doc_id,
                    text=chunk.text,
                    score=round(score, 6),
                    rank=rank,
                )
            )
        return hits

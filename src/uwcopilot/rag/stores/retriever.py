"""The four retrieval modes of rag/retriever.py, run on any vector store, with fusion and reranking in Python.

Retriever (rag/retriever.py) asks Qdrant to do the fusion (query API with two prefetches + RrfQuery). Other
backends do not offer that exact query, so StoreRetriever does the same steps itself:

- "dense": dense_store.dense_search(query embedding).
- "bm25": bm25_store.sparse_search(BM25 query vector); an empty result when the query has no indexable words.
- "hybrid": both searches, each `prefetch_limit` deep, fused with fusion.reciprocal_rank_fusion (k = rrf_k) on
  the point ids. That function is the reference the Qdrant tests check Qdrant's own RRF against, so the order and
  the fused scores are the same as Qdrant's for the same two candidate lists.
- "hybrid_rerank": the top `rerank_candidates` hybrid results reordered by the same cross-encoder.

Results are de-duplicated by chunk_id and returned as retriever.RetrievedChunk, so evaluation.run_mode() works
unchanged. Parameters are the same RetrievalParams defaults as the Qdrant retriever (chosen before evaluation).
"""

from collections.abc import Sequence

from uwcopilot.rag.chunks import chunk_from_payload
from uwcopilot.rag.embeddings import DenseEmbedder, SparseEmbedder
from uwcopilot.rag.fusion import reciprocal_rank_fusion
from uwcopilot.rag.rerank import Reranker
from uwcopilot.rag.retriever import RETRIEVAL_MODES, RetrievalMode, RetrievalParams, RetrievedChunk
from uwcopilot.rag.stores.base import DenseStore, SparseStore, StoreHit


class StoreRetriever:
    """Runs searches on a dense store and a BM25 store (often the same backend) with the given models."""

    def __init__(
        self,
        dense_store: DenseStore,
        bm25_store: SparseStore,
        dense: DenseEmbedder,
        sparse: SparseEmbedder,
        reranker: Reranker | None = None,
        params: RetrievalParams | None = None,
    ) -> None:
        """Store the components."""
        self.dense_store = dense_store
        self.bm25_store = bm25_store
        self.dense = dense
        self.sparse = sparse
        self.reranker = reranker
        self.params = params or RetrievalParams()

    def search(
        self,
        query: str,
        mode: RetrievalMode = "hybrid_rerank",
        top_k: int = 5,
        source_docs: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Return the top_k chunks for query (best first), optionally limited to some document ids."""
        if mode not in RETRIEVAL_MODES:
            raise ValueError(f"Unknown retrieval mode {mode!r}; use one of {RETRIEVAL_MODES}.")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        fetch = top_k + self.params.dedup_margin
        if mode == "dense":
            hits = self._dense(query, fetch, source_docs)
        elif mode == "bm25":
            hits = self._bm25(query, fetch, source_docs)
        elif mode == "hybrid":
            hits = self._hybrid(query, fetch, source_docs)
        else:
            candidates = self._hybrid(query, max(self.params.rerank_candidates, fetch), source_docs)
            hits = self._rerank(query, candidates)
        return rank_unique_hits(hits, top_k)

    def _dense(self, query: str, limit: int, source_docs: Sequence[str] | None) -> list[StoreHit]:
        """Nearest neighbours of the query embedding."""
        return self.dense_store.dense_search(self.dense.embed_query(query), limit, source_docs)

    def _bm25(self, query: str, limit: int, source_docs: Sequence[str] | None) -> list[StoreHit]:
        """BM25 results (an empty list when the query has no indexable words, as in the Qdrant retriever)."""
        vector = self.sparse.embed_query(query)
        if not vector.indices:
            return []
        return self.bm25_store.sparse_search(vector, limit, source_docs)

    def _hybrid(self, query: str, limit: int, source_docs: Sequence[str] | None) -> list[StoreHit]:
        """Dense and BM25 candidate lists fused by reciprocal rank fusion on the point ids."""
        depth = self.params.prefetch_limit
        candidate_lists = [self._dense(query, depth, source_docs), self._bm25(query, depth, source_docs)]
        payloads = {hit.id: hit.payload for hits in candidate_lists for hit in hits}
        rankings = [[hit.id for hit in hits] for hits in candidate_lists if hits]
        fused = reciprocal_rank_fusion(rankings, k=self.params.rrf_k)
        return [StoreHit(id=str(item), score=score, payload=payloads[str(item)]) for item, score in fused[:limit]]

    def _rerank(self, query: str, candidates: list[StoreHit]) -> list[StoreHit]:
        """Reorder candidates by cross-encoder score (ties keep the hybrid order)."""
        if self.reranker is None:
            raise ValueError("mode 'hybrid_rerank' needs a reranker")
        texts = [chunk_from_payload(hit.payload).embedding_text() for hit in candidates]
        scores = self.reranker.score(query, texts)
        order = sorted(range(len(candidates)), key=lambda index: -scores[index])
        return [StoreHit(id=candidates[i].id, score=scores[i], payload=candidates[i].payload) for i in order]


def rank_unique_hits(hits: Sequence[StoreHit], top_k: int) -> list[RetrievedChunk]:
    """Keep the first hit of each chunk_id, number them 1..n and cut at top_k (same rule as retriever.rank_unique)."""
    results: list[RetrievedChunk] = []
    seen: set[str] = set()
    for hit in hits:
        chunk_id = str(hit.payload.get("chunk_id"))
        if chunk_id in seen:
            continue
        seen.add(chunk_id)
        results.append(RetrievedChunk(payload=hit.payload, score=float(hit.score), rank=len(results) + 1))
        if len(results) == top_k:
            break
    return results

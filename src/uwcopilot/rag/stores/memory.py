"""An exact, brute-force vector store in plain Python: the reference every other backend is compared with.

dense_search computes the cosine similarity of the query with every stored vector; sparse_search computes the
IDF-weighted dot product (base.SparseIdf) with every stored sparse vector that shares at least one token. Nothing
is approximate, so a backend that returns the same lists is searching the same vectors in the same way.

It has two jobs:
- the BM25 leg for a backend that cannot store the project's sparse vectors (Weaviate), labelled "app-side" in the
  results;
- the offline fake in the unit tests (no Docker, no network).

Ties keep insertion order. Fine for the ~100 points of this corpus; not meant for large collections.
"""

import math
from collections.abc import Sequence

from uwcopilot.rag.embeddings import SparseVector
from uwcopilot.rag.stores.base import SparseIdf, StoreHit, StoreRecord


class InMemoryStore:
    """Exact cosine and IDF-weighted sparse search over records held in a dict (insertion order kept)."""

    name = "memory"

    def __init__(self, records: Sequence[StoreRecord] = ()) -> None:
        """Start empty, or with the given records."""
        self.records: dict[str, StoreRecord] = {}
        self.idf = SparseIdf(n_documents=0, document_frequency={})
        if records:
            self.upsert(records)

    def upsert(self, records: Sequence[StoreRecord]) -> int:
        """Add or replace records by id, then recount the document frequencies."""
        for record in records:
            self.records[record.id] = record
        self.idf = SparseIdf.from_vectors([record.sparse for record in self.records.values()])
        return len(records)

    def count(self) -> int:
        """Number of stored records."""
        return len(self.records)

    def _candidates(self, source_docs: Sequence[str] | None) -> list[StoreRecord]:
        """All records, or only those from the given documents."""
        if not source_docs:
            return list(self.records.values())
        wanted = set(source_docs)
        return [record for record in self.records.values() if record.doc_id in wanted]

    def dense_search(
        self, vector: Sequence[float], limit: int, source_docs: Sequence[str] | None = None
    ) -> list[StoreHit]:
        """The limit records with the highest cosine similarity to vector."""
        query_norm = math.sqrt(sum(value * value for value in vector))
        scored = []
        for record in self._candidates(source_docs):
            norm = math.sqrt(sum(value * value for value in record.dense))
            dot = sum(a * b for a, b in zip(vector, record.dense, strict=True))
            similarity = dot / (query_norm * norm) if query_norm and norm else 0.0
            scored.append(StoreHit(id=record.id, score=similarity, payload=record.payload))
        return sorted(scored, key=lambda hit: -hit.score)[:limit]

    def sparse_search(
        self, vector: SparseVector, limit: int, source_docs: Sequence[str] | None = None
    ) -> list[StoreHit]:
        """The limit records with the highest IDF-weighted dot product; records sharing no token are skipped."""
        weighted = self.idf.weight_query(vector)
        query = dict(zip(weighted.indices, weighted.values, strict=True))
        scored = []
        for record in self._candidates(source_docs):
            pairs = zip(record.sparse.indices, record.sparse.values, strict=True)
            shared = [query[token] * value for token, value in pairs if token in query]
            if shared:
                scored.append(StoreHit(id=record.id, score=sum(shared), payload=record.payload))
        return sorted(scored, key=lambda hit: -hit.score)[:limit]

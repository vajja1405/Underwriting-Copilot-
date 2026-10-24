"""What every vector-store backend stores and returns, and BM25's IDF computed the way Qdrant computes it.

A StoreRecord is one point of the Qdrant index (index.py): the same deterministic id, the same payload, the dense
BGE vector and the BM25 sparse vector (term-frequency weights only; the IDF part is applied at query time). The
evaluation copies the records out of the Qdrant index (records_from_qdrant), so every backend searches exactly the
same numbers; tests build them from chunks with the fake embedders (records_from_chunks).

Two small protocols describe what a backend must do. A backend that cannot store the project's sparse vectors
(Weaviate) implements only DenseStore, and the BM25 leg runs on another store (see retriever.StoreRetriever).

BM25 IDF. The Qdrant collection uses modifier=IDF: at query time Qdrant multiplies each query token's weight by

    idf(t) = ln((N - df(t) + 0.5) / (df(t) + 0.5) + 1)

(N = points with a sparse vector, df(t) = points whose sparse vector contains token t) and takes the dot product
with the stored weights. Pinecone's sparse index only takes the dot product, so SparseIdf weights the query
before it is sent; the score is then the same number Qdrant computes.
"""

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from qdrant_client import QdrantClient

from uwcopilot.rag.chunks import Chunk
from uwcopilot.rag.embeddings import DenseEmbedder, SparseEmbedder, SparseVector
from uwcopilot.rag.index import DENSE_VECTOR, SPARSE_VECTOR, point_id, require_collection


@dataclass(frozen=True)
class StoreRecord:
    """One stored point: id, payload (chunk fields + text_sha256), dense vector and BM25 sparse vector."""

    id: str
    payload: dict[str, Any] = field(repr=False)
    dense: list[float] = field(repr=False)
    sparse: SparseVector = field(repr=False)

    @property
    def doc_id(self) -> str:
        """The source document id (used by the source_docs filter)."""
        return str(self.payload["doc_id"])


@dataclass(frozen=True)
class StoreHit:
    """One search result from a backend: point id, the backend's score (higher is better) and the payload."""

    id: str
    score: float
    payload: dict[str, Any] = field(repr=False)


class DenseStore(Protocol):
    """A backend that stores records and finds nearest neighbours of a dense query vector (cosine)."""

    name: str

    def upsert(self, records: Sequence[StoreRecord]) -> int:
        """Write (or overwrite) records; return how many were written."""
        ...

    def count(self) -> int:
        """Number of stored points."""
        ...

    def dense_search(
        self, vector: Sequence[float], limit: int, source_docs: Sequence[str] | None = None
    ) -> list[StoreHit]:
        """The limit most similar points (cosine similarity as the score), optionally only from some documents."""
        ...


class SparseStore(Protocol):
    """A backend that scores BM25 sparse vectors the way Qdrant's modifier=IDF collection does."""

    name: str

    def sparse_search(
        self, vector: SparseVector, limit: int, source_docs: Sequence[str] | None = None
    ) -> list[StoreHit]:
        """The limit best points by IDF-weighted dot product; points sharing no token are never returned."""
        ...


@dataclass(frozen=True)
class SparseIdf:
    """Document frequencies of sparse token ids, and BM25 IDF with Qdrant's formula (module docstring)."""

    n_documents: int
    document_frequency: dict[int, int]

    @classmethod
    def from_vectors(cls, vectors: Sequence[SparseVector]) -> "SparseIdf":
        """Count, for every token id, how many of the stored vectors contain it."""
        counts: Counter[int] = Counter()
        for vector in vectors:
            counts.update(set(vector.indices))
        return cls(n_documents=len(vectors), document_frequency=dict(counts))

    def idf(self, token: int) -> float:
        """ln((N - df + 0.5) / (df + 0.5) + 1), the same expression as Qdrant's local and server code."""
        df = self.document_frequency.get(token, 0)
        return math.log((self.n_documents - df + 0.5) / (df + 0.5) + 1.0)

    def weight_query(self, vector: SparseVector) -> SparseVector:
        """The query vector with every weight multiplied by its token's IDF."""
        return SparseVector(
            indices=list(vector.indices),
            values=[value * self.idf(token) for token, value in zip(vector.indices, vector.values, strict=True)],
        )


def records_from_chunks(
    chunks: Sequence[Chunk], dense: DenseEmbedder, sparse: SparseEmbedder, batch_size: int = 16
) -> list[StoreRecord]:
    """Embed chunks the way index.index_chunks does (same text, same batches) and return them as records."""
    records: list[StoreRecord] = []
    for start in range(0, len(chunks), batch_size):
        batch = list(chunks[start : start + batch_size])
        texts = [chunk.embedding_text() for chunk in batch]
        dense_vectors = dense.embed_documents(texts)
        sparse_vectors = sparse.embed_documents(texts)
        for chunk, dense_vector, sparse_vector in zip(batch, dense_vectors, sparse_vectors, strict=True):
            records.append(
                StoreRecord(id=point_id(chunk), payload=chunk.payload(), dense=dense_vector, sparse=sparse_vector)
            )
    return records


def records_from_qdrant(client: QdrantClient, collection: str, page_size: int = 256) -> list[StoreRecord]:
    """Every point of a Qdrant collection with its payload and both vectors, exactly as stored."""
    require_collection(client, collection)
    records: list[StoreRecord] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, limit=page_size, offset=offset, with_payload=True, with_vectors=True
        )
        for point in points:
            vectors: Any = point.vector
            sparse = vectors[SPARSE_VECTOR]
            records.append(
                StoreRecord(
                    id=str(point.id),
                    payload=dict(point.payload or {}),
                    dense=[float(value) for value in vectors[DENSE_VECTOR]],
                    sparse=SparseVector(indices=list(sparse.indices), values=[float(v) for v in sparse.values]),
                )
            )
        if offset is None:
            return records

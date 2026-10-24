"""Weaviate backend: self-provided vectors (no vectorizer module), plus Weaviate's own BM25 and hybrid search.

Collection layout ("UwcGuidelines"): one object per Qdrant point with the same UUID, and
- a named vector "dense": the BGE vector we computed (self-provided), HNSW index with cosine distance;
- embedding_text: the text that was embedded (the only property Weaviate's own BM25 searches);
- doc_id, doc_kind, chunk_id: filterable, tokenization "field" (the whole value is one token);
- payload_json: the full Qdrant payload as JSON (not indexed), so search results carry the exact payload.

Weaviate has no field for our own BM25 sparse vectors, so in the four standard modes (stores.retriever) only the
dense search runs in Weaviate; the BM25 leg runs on the exact in-memory store over the same sparse vectors (labelled
"app-side" in the results). Weaviate's own keyword search is measured separately, as extra modes:

- "weaviate_bm25": Weaviate's BM25 over embedding_text (its own tokenizer: word tokenization, lowercase, English
  stopword preset, no stemming; k1 = 1.2, b = 0.75). Not the same BM25 as Qdrant/bm25, which stems.
- "weaviate_hybrid": Weaviate's hybrid query (its BM25 + our query vector) with rankedFusion and alpha 0.5, the
  closest Weaviate setting to our RRF (rankedFusion scores each list by 1 / (rank + 60)).
- "weaviate_hybrid_relative": the same with Weaviate's default fusion, relativeScoreFusion (min-max normalised
  scores, then a weighted sum).

The weaviate-client package is optional (`uv sync --extra vectorstores`) and is imported only inside the
functions that need it. WeaviateStore only calls methods on the collection object it is given, so the unit tests
pass a fake collection.
"""

import json
from collections.abc import Sequence
from typing import Any

from uwcopilot.rag.chunks import chunk_from_payload
from uwcopilot.rag.embeddings import DenseEmbedder
from uwcopilot.rag.retriever import RetrievalParams, RetrievedChunk
from uwcopilot.rag.stores.base import StoreHit, StoreRecord
from uwcopilot.rag.stores.retriever import rank_unique_hits

DEFAULT_COLLECTION_NAME = "UwcGuidelines"
VECTOR_NAME = "dense"
TEXT_PROPERTY = "embedding_text"
FILTER_FIELDS = ("doc_id", "doc_kind", "chunk_id")
HYBRID_ALPHA = 0.5
# mode name -> Weaviate hybrid fusion ("" means plain BM25, no vector).
WEAVIATE_NATIVE_MODES: dict[str, str] = {
    "weaviate_bm25": "",
    "weaviate_hybrid": "ranked",
    "weaviate_hybrid_relative": "relative_score",
}


def connect_weaviate(host: str = "localhost", http_port: int = 8080, grpc_port: int = 50051) -> Any:
    """A client for a Weaviate server without authentication (the Docker container used here)."""
    import weaviate

    return weaviate.connect_to_local(host=host, port=http_port, grpc_port=grpc_port)


def create_weaviate_collection(client: Any, name: str = DEFAULT_COLLECTION_NAME, recreate: bool = True) -> Any:
    """Create (or reuse) the collection described in the module docstring; return its handle."""
    from weaviate.classes.config import Configure, DataType, Property, Tokenization, VectorDistances

    if client.collections.exists(name):
        if not recreate:
            return client.collections.get(name)
        client.collections.delete(name)
    filter_properties = [
        Property(name=field_name, data_type=DataType.TEXT, tokenization=Tokenization.FIELD, index_searchable=False)
        for field_name in FILTER_FIELDS
    ]
    return client.collections.create(
        name=name,
        vector_config=Configure.Vectors.self_provided(
            name=VECTOR_NAME,
            vector_index_config=Configure.VectorIndex.hnsw(distance_metric=VectorDistances.COSINE),
        ),
        properties=[
            Property(name=TEXT_PROPERTY, data_type=DataType.TEXT, tokenization=Tokenization.WORD),
            *filter_properties,
            Property(name="payload_json", data_type=DataType.TEXT, index_filterable=False, index_searchable=False),
        ],
    )


def weaviate_filter(source_docs: Sequence[str] | None) -> Any:
    """A filter on doc_id, or None to search everything."""
    if not source_docs:
        return None
    from weaviate.classes.query import Filter

    return Filter.by_property("doc_id").contains_any(list(source_docs))


def weaviate_properties(record: StoreRecord, embedding_text: str) -> dict[str, Any]:
    """The object properties stored for one record (see the module docstring)."""
    properties: dict[str, Any] = {name: str(record.payload[name]) for name in FILTER_FIELDS}
    properties[TEXT_PROPERTY] = embedding_text
    properties["payload_json"] = json.dumps(record.payload, sort_keys=True)
    return properties


class WeaviateStore:
    """Dense search on Weaviate with self-provided vectors, plus Weaviate's own BM25 and hybrid queries."""

    name = "weaviate"

    def __init__(self, collection: Any) -> None:
        """Use the given collection handle (from create_weaviate_collection, or a fake in tests)."""
        self.collection = collection

    def upsert(self, records: Sequence[StoreRecord]) -> int:
        """Insert every record with its UUID and dense vector; raise if Weaviate reports any failed object."""
        from weaviate.classes.data import DataObject

        objects = [
            DataObject(
                uuid=record.id,
                properties=weaviate_properties(record, chunk_from_payload(record.payload).embedding_text()),
                vector={VECTOR_NAME: list(record.dense)},
            )
            for record in records
        ]
        result = self.collection.data.insert_many(objects)
        if result.has_errors:
            raise RuntimeError(f"Weaviate rejected {len(result.errors)} objects: {list(result.errors.values())[:3]}")
        return len(objects)

    def count(self) -> int:
        """Objects in the collection."""
        return int(self.collection.aggregate.over_all(total_count=True).total_count)

    def dense_search(
        self, vector: Sequence[float], limit: int, source_docs: Sequence[str] | None = None
    ) -> list[StoreHit]:
        """Nearest neighbours; Weaviate returns cosine distance, so the score is 1 - distance (cosine similarity)."""
        from weaviate.classes.query import MetadataQuery

        response = self.collection.query.near_vector(
            near_vector=list(vector),
            target_vector=VECTOR_NAME,
            limit=limit,
            filters=weaviate_filter(source_docs),
            return_metadata=MetadataQuery(distance=True),
        )
        return [to_hit(item, 1.0 - float(item.metadata.distance)) for item in response.objects]

    def keyword_search(self, query: str, limit: int, source_docs: Sequence[str] | None = None) -> list[StoreHit]:
        """Weaviate's own BM25 over embedding_text."""
        from weaviate.classes.query import MetadataQuery

        response = self.collection.query.bm25(
            query=query,
            query_properties=[TEXT_PROPERTY],
            limit=limit,
            filters=weaviate_filter(source_docs),
            return_metadata=MetadataQuery(score=True),
        )
        return [to_hit(item, float(item.metadata.score)) for item in response.objects]

    def hybrid_search(
        self,
        query: str,
        vector: Sequence[float],
        limit: int,
        fusion: str = "ranked",
        alpha: float = HYBRID_ALPHA,
        source_docs: Sequence[str] | None = None,
    ) -> list[StoreHit]:
        """Weaviate's hybrid query: its BM25 on the text + our query vector, fused by Weaviate."""
        from weaviate.classes.query import HybridFusion, MetadataQuery

        fusion_type = HybridFusion.RANKED if fusion == "ranked" else HybridFusion.RELATIVE_SCORE
        response = self.collection.query.hybrid(
            query=query,
            vector=list(vector),
            target_vector=VECTOR_NAME,
            alpha=alpha,
            fusion_type=fusion_type,
            query_properties=[TEXT_PROPERTY],
            limit=limit,
            filters=weaviate_filter(source_docs),
            return_metadata=MetadataQuery(score=True),
        )
        return [to_hit(item, float(item.metadata.score)) for item in response.objects]


def to_hit(item: Any, score: float) -> StoreHit:
    """A Weaviate result object as a StoreHit with the original payload."""
    return StoreHit(id=str(item.uuid), score=score, payload=json.loads(item.properties["payload_json"]))


class WeaviateNativeRetriever:
    """The extra modes that use Weaviate's own keyword search (WEAVIATE_NATIVE_MODES); same result type as Retriever."""

    def __init__(self, store: WeaviateStore, dense: DenseEmbedder, params: RetrievalParams | None = None) -> None:
        """The store, the dense embedder for the hybrid query vector, and the dedup margin from params."""
        self.store = store
        self.dense = dense
        self.params = params or RetrievalParams()

    def search(
        self,
        query: str,
        mode: str = "weaviate_hybrid",
        top_k: int = 5,
        source_docs: Sequence[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Return the top_k chunks for query (best first) with one of Weaviate's own keyword or hybrid searches."""
        if mode not in WEAVIATE_NATIVE_MODES:
            raise ValueError(f"Unknown Weaviate mode {mode!r}; use one of {tuple(WEAVIATE_NATIVE_MODES)}.")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        fetch = top_k + self.params.dedup_margin
        fusion = WEAVIATE_NATIVE_MODES[mode]
        if not fusion:
            hits = self.store.keyword_search(query, fetch, source_docs)
        else:
            vector = self.dense.embed_query(query)
            hits = self.store.hybrid_search(query, vector, fetch, fusion=fusion, source_docs=source_docs)
        return rank_unique_hits(hits, top_k)

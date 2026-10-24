"""Vector-store backends (stores/): the in-memory reference, StoreRetriever, and the Pinecone/Weaviate adapters.

Everything here runs offline: the fake embedders, an in-memory Qdrant, and fake Pinecone indexes / a fake Weaviate
collection that record what the adapters send. The Weaviate adapter tests need the optional weaviate-client
package (they build its filter and data objects) and are skipped without it. Runs against the real services are in
test_rag_vectorstores_integration.py (integration marker).
"""

import json
import math
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from rag_helpers import SAMPLE_CHUNKS, TEST_COLLECTION, build_fake_retriever, make_chunk

import eval_rag_vectorstores
from uwcopilot.rag.embeddings import HashingDenseEmbedder, HashingSparseEmbedder, SparseVector
from uwcopilot.rag.evaluation import QuestionResult
from uwcopilot.rag.fusion import reciprocal_rank_fusion
from uwcopilot.rag.rerank import TermOverlapReranker
from uwcopilot.rag.retriever import RETRIEVAL_MODES, RetrievalParams
from uwcopilot.rag.stores import pinecone_store
from uwcopilot.rag.stores.base import SparseIdf, StoreRecord, records_from_chunks, records_from_qdrant
from uwcopilot.rag.stores.memory import InMemoryStore
from uwcopilot.rag.stores.pinecone_store import PineconeStore, create_pinecone_indexes, pinecone_filter
from uwcopilot.rag.stores.retriever import StoreRetriever

QUERIES = [
    "minimum debt service coverage ratio for a line of credit",
    "coastal windstorm deductible and loss ratio referral",
    "sanctions screening OFAC compliance",
    "loss ratio senior underwriter",
]


def make_records(chunks: Sequence = SAMPLE_CHUNKS) -> list[StoreRecord]:
    """Records for the sample chunks with the fake embedders used by build_fake_retriever (dim 128)."""
    return records_from_chunks(list(chunks), HashingDenseEmbedder(dim=128), HashingSparseEmbedder())


def memory_retriever(chunks: Sequence = SAMPLE_CHUNKS, params: RetrievalParams | None = None) -> StoreRetriever:
    """StoreRetriever over an InMemoryStore with the fake embedders and reranker."""
    store = InMemoryStore(make_records(chunks))
    dense, sparse = HashingDenseEmbedder(dim=128), HashingSparseEmbedder()
    return StoreRetriever(store, store, dense, sparse, TermOverlapReranker(), params)


def scores_by_chunk(hits: Sequence[Any]) -> dict[str, float]:
    """chunk_id -> score for a list of StoreHits or Qdrant ScoredPoints."""
    return {hit.payload["chunk_id"]: hit.score for hit in hits}


# ---------------------------------------------------------------------------------------------------------------
# IDF, the in-memory reference, and StoreRetriever
# ---------------------------------------------------------------------------------------------------------------


def test_sparse_idf_counts_documents_and_uses_qdrants_formula() -> None:
    vectors = [
        SparseVector(indices=[1, 2], values=[1.0, 1.0]),
        SparseVector(indices=[1], values=[2.0]),
        SparseVector(indices=[3], values=[1.0]),
    ]
    idf = SparseIdf.from_vectors(vectors)
    assert idf.n_documents == 3
    assert idf.document_frequency == {1: 2, 2: 1, 3: 1}
    assert idf.idf(1) == pytest.approx(math.log((3 - 2 + 0.5) / (2 + 0.5) + 1))
    assert idf.idf(99) == pytest.approx(math.log((3 + 0.5) / 0.5 + 1))
    weighted = idf.weight_query(SparseVector(indices=[1, 3], values=[1.0, 2.0]))
    assert weighted.indices == [1, 3]
    assert weighted.values == pytest.approx([idf.idf(1), 2.0 * idf.idf(3)])


def test_records_from_qdrant_equal_records_from_chunks() -> None:
    qdrant = build_fake_retriever()
    from_qdrant = {record.id: record for record in records_from_qdrant(qdrant.client, TEST_COLLECTION)}
    from_chunks = {record.id: record for record in make_records()}
    assert from_qdrant.keys() == from_chunks.keys()
    for record_id, record in from_chunks.items():
        copied = from_qdrant[record_id]
        assert copied.payload == record.payload
        assert copied.sparse.indices == record.sparse.indices
        assert copied.sparse.values == pytest.approx(record.sparse.values)
        assert copied.dense == pytest.approx(record.dense, abs=1e-6)


@pytest.mark.parametrize("query", QUERIES)
def test_memory_store_scores_every_chunk_like_qdrant(query: str) -> None:
    """Cosine and IDF-weighted BM25 give each chunk the same score as Qdrant (limit > corpus, so ties cannot hide)."""
    qdrant = build_fake_retriever()
    memory = InMemoryStore(make_records())
    dense_vector = qdrant.dense.embed_query(query)
    sparse_vector = qdrant.sparse.embed_query(query)
    expected_dense = scores_by_chunk(qdrant._dense(query, 10, None))
    expected_bm25 = scores_by_chunk(qdrant._bm25(query, 10, None))
    assert scores_by_chunk(memory.dense_search(dense_vector, 10)) == pytest.approx(expected_dense, abs=1e-5)
    assert scores_by_chunk(memory.sparse_search(sparse_vector, 10)) == pytest.approx(expected_bm25, abs=1e-5)


@pytest.mark.parametrize("mode", RETRIEVAL_MODES)
def test_store_retriever_ranks_the_matching_section_first(mode: str) -> None:
    results = memory_retriever().search(QUERIES[0], mode=mode, top_k=3)
    assert results[0].chunk_id == "NBC-4.2"
    assert [result.rank for result in results] == list(range(1, len(results) + 1))


def test_store_retriever_hybrid_is_reference_rrf_of_its_two_lists() -> None:
    params = RetrievalParams(prefetch_limit=4, rrf_k=60)
    retriever = memory_retriever(params=params)
    query = QUERIES[1]
    dense_ids = [hit.id for hit in retriever._dense(query, params.prefetch_limit, None)]
    bm25_ids = [hit.id for hit in retriever._bm25(query, params.prefetch_limit, None)]
    expected = reciprocal_rank_fusion([dense_ids, bm25_ids], k=params.rrf_k)
    hybrid = retriever._hybrid(query, 10, None)
    assert [(hit.id, hit.score) for hit in hybrid] == [(item, pytest.approx(score)) for item, score in expected]


def test_store_retriever_filters_dedups_and_checks_arguments() -> None:
    retriever = memory_retriever()
    for mode in RETRIEVAL_MODES:
        results = retriever.search(
            "referred to a senior underwriter", mode=mode, source_docs=["harborline_mutual_commercial"]
        )
        assert results and {result.payload["doc_id"] for result in results} == {"harborline_mutual_commercial"}
    parts = [
        make_chunk("HM-9.3", "Referral triggers part one: loss ratio and windstorm.", part=1, part_count=2),
        make_chunk("HM-9.3", "Referral triggers part two: loss ratio and sanctions.", part=2, part_count=2),
        make_chunk("NBC-4.2", "Coverage minimums."),
    ]
    split_retriever = memory_retriever(parts)
    for mode in RETRIEVAL_MODES:
        ids = [result.chunk_id for result in split_retriever.search("referral triggers loss ratio", mode=mode, top_k=3)]
        assert ids.count("HM-9.3") == 1
    assert retriever.search("zzz qqq", mode="bm25") == []
    with pytest.raises(ValueError, match="Unknown retrieval mode"):
        retriever.search("x", mode="keyword")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="needs a reranker"):
        StoreRetriever(retriever.dense_store, retriever.bm25_store, retriever.dense, retriever.sparse).search("x")


# ---------------------------------------------------------------------------------------------------------------
# Pinecone adapter (fake index objects)
# ---------------------------------------------------------------------------------------------------------------


class FakePineconeIndex:
    """Records upserts and answers queries like a Pinecone index: cosine for dense, plain dot product for sparse.

    Like Pinecone Local, a sparse query also returns vectors that share no token, with score 0.
    """

    def __init__(self) -> None:
        self.vectors: dict[str, dict[str, Any]] = {}
        self.queries: list[dict[str, Any]] = []

    def upsert(self, vectors: list[dict[str, Any]], namespace: str = "", show_progress: bool = True) -> None:
        for vector in vectors:
            self.vectors[vector["id"]] = vector

    def describe_index_stats(self) -> SimpleNamespace:
        return SimpleNamespace(total_vector_count=len(self.vectors))

    def query(self, top_k: int, filter: dict | None = None, include_metadata: bool = False, **kwargs: Any) -> Any:
        self.queries.append({"top_k": top_k, "filter": filter, **kwargs})
        allowed = set(filter["doc_id"]["$in"]) if filter else None
        matches = []
        for vector in self.vectors.values():
            if allowed is not None and vector["metadata"]["doc_id"] not in allowed:
                continue
            if "vector" in kwargs:
                query, stored = kwargs["vector"], vector["values"]
                norms = math.sqrt(sum(v * v for v in query)) * math.sqrt(sum(v * v for v in stored))
                score = sum(a * b for a, b in zip(query, stored, strict=True)) / norms
            else:
                weights = dict(zip(kwargs["sparse_vector"]["indices"], kwargs["sparse_vector"]["values"], strict=True))
                sparse = vector["sparse_values"]
                score = sum(weights.get(i, 0.0) * v for i, v in zip(sparse["indices"], sparse["values"], strict=True))
            matches.append(SimpleNamespace(id=vector["id"], score=score, metadata=vector["metadata"]))
        matches.sort(key=lambda match: -match.score)
        return SimpleNamespace(matches=matches[:top_k])


def make_pinecone_store() -> tuple[PineconeStore, FakePineconeIndex, FakePineconeIndex, list[StoreRecord]]:
    dense_index, sparse_index = FakePineconeIndex(), FakePineconeIndex()
    store = PineconeStore(dense_index, sparse_index)
    records = make_records()
    assert store.upsert(records, batch_size=2) == len(records)
    return store, dense_index, sparse_index, records


def test_pinecone_upsert_writes_both_indexes_with_exact_payload() -> None:
    store, dense_index, sparse_index, records = make_pinecone_store()
    assert dense_index.vectors.keys() == sparse_index.vectors.keys() == {record.id for record in records}
    assert store.counts() == {"dense": len(records), "sparse": len(records)}
    record = records[0]
    dense_vector, sparse_vector = dense_index.vectors[record.id], sparse_index.vectors[record.id]
    assert dense_vector["values"] == record.dense and "sparse_values" not in dense_vector
    assert sparse_vector["sparse_values"] == {"indices": record.sparse.indices, "values": record.sparse.values}
    assert "values" not in sparse_vector
    metadata = dense_vector["metadata"]
    assert json.loads(metadata["payload_json"]) == record.payload
    assert {name: metadata[name] for name in ("doc_id", "doc_kind", "chunk_id")} == {
        name: record.payload[name] for name in ("doc_id", "doc_kind", "chunk_id")
    }
    assert all(value is not None for value in metadata.values())


@pytest.mark.parametrize("query", QUERIES)
def test_pinecone_scores_match_the_exact_reference(query: str) -> None:
    """Dense = cosine; BM25 = Qdrant's IDF applied to the query, zero-score (no shared token) matches dropped."""
    store, _dense_index, sparse_index, records = make_pinecone_store()
    memory = InMemoryStore(records)
    dense, sparse = HashingDenseEmbedder(dim=128), HashingSparseEmbedder()
    dense_vector, sparse_vector = dense.embed_query(query), sparse.embed_query(query)
    assert scores_by_chunk(store.dense_search(dense_vector, 10)) == pytest.approx(
        scores_by_chunk(memory.dense_search(dense_vector, 10))
    )
    assert scores_by_chunk(store.sparse_search(sparse_vector, 10)) == pytest.approx(
        scores_by_chunk(memory.sparse_search(sparse_vector, 10))
    )
    sent = sparse_index.queries[-1]["sparse_vector"]
    assert sent["values"] == pytest.approx(store.idf.weight_query(sparse_vector).values)


def test_pinecone_filter_and_missing_idf() -> None:
    store, dense_index, _sparse_index, _records = make_pinecone_store()
    hits = store.dense_search([1.0] * 128, 10, source_docs=["harborline_mutual_commercial"])
    assert hits and {hit.payload["doc_id"] for hit in hits} == {"harborline_mutual_commercial"}
    assert dense_index.queries[-1]["filter"] == {"doc_id": {"$in": ["harborline_mutual_commercial"]}}
    assert pinecone_filter(None) is None and pinecone_filter([]) is None
    with pytest.raises(ValueError, match="no IDF table"):
        PineconeStore(FakePineconeIndex(), FakePineconeIndex()).sparse_search(SparseVector([1], [1.0]), 5)


def test_create_pinecone_indexes_sends_the_2025_01_control_plane_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = {"uwc-guidelines-dense": {"status": {"ready": True}, "host": "localhost:5081"}}
    calls: list[tuple[str, str, dict | None]] = []

    def fake_control_call(host: str, api_key: str, method: str, path: str, body: dict | None = None) -> dict | None:
        calls.append((method, path, body))
        name = path.rsplit("/", 1)[-1]
        if method == "GET":
            return existing.get(name)
        if method == "DELETE":
            existing.pop(name, None)
            return {}
        port = 5081 if body and body["vector_type"] == "dense" else 5082
        existing[body["name"]] = {"status": {"ready": True}, "host": f"localhost:{port}"}  # type: ignore[index]
        return existing[body["name"]]  # type: ignore[index]

    monkeypatch.setattr(pinecone_store, "control_call", fake_control_call)
    client = SimpleNamespace(index=lambda host, grpc: SimpleNamespace(host=host, grpc=grpc))
    dense_index, sparse_index = create_pinecone_indexes(client, dim=384)
    assert (dense_index.host, dense_index.grpc) == ("localhost:5081", True)
    assert sparse_index.host == "localhost:5082"
    assert ("DELETE", "/indexes/uwc-guidelines-dense", None) in calls
    bodies = {body["name"]: body for method, _path, body in calls if method == "POST"}
    assert bodies["uwc-guidelines-dense"]["dimension"] == 384
    assert bodies["uwc-guidelines-dense"]["metric"] == "cosine"
    assert "dimension" not in bodies["uwc-guidelines-sparse"]
    assert bodies["uwc-guidelines-sparse"]["metric"] == "dotproduct"
    assert bodies["uwc-guidelines-sparse"]["vector_type"] == "sparse"


# ---------------------------------------------------------------------------------------------------------------
# Weaviate adapter (fake collection; needs the weaviate-client package for its filter and data classes)
# ---------------------------------------------------------------------------------------------------------------


class FakeWeaviateCollection:
    """Records inserted objects and answers near_vector / bm25 / hybrid calls (bm25 and hybrid return a fixed list)."""

    def __init__(self) -> None:
        self.objects: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.data = SimpleNamespace(insert_many=self._insert_many)
        self.aggregate = SimpleNamespace(over_all=lambda total_count: SimpleNamespace(total_count=len(self.objects)))
        self.query = SimpleNamespace(near_vector=self._near_vector, bm25=self._keyword, hybrid=self._keyword)

    def _insert_many(self, objects: list[Any]) -> SimpleNamespace:
        for item in objects:
            self.objects[str(item.uuid)] = item
        return SimpleNamespace(has_errors=False, errors={})

    def _result(self, item: Any, **metadata: float) -> SimpleNamespace:
        return SimpleNamespace(uuid=item.uuid, properties=item.properties, metadata=SimpleNamespace(**metadata))

    def _allowed(self, item: Any, filters: Any) -> bool:
        return filters is None or item.properties[filters.target] in filters.value

    def _near_vector(self, near_vector: list[float], limit: int, filters: Any = None, **kwargs: Any) -> Any:
        self.calls.append(("near_vector", {"limit": limit, "filters": filters, **kwargs}))
        results = []
        for item in self.objects.values():
            if not self._allowed(item, filters):
                continue
            stored = item.vector[kwargs["target_vector"]]
            norms = math.sqrt(sum(v * v for v in near_vector)) * math.sqrt(sum(v * v for v in stored))
            cosine = sum(a * b for a, b in zip(near_vector, stored, strict=True)) / norms
            results.append(self._result(item, distance=1.0 - cosine))
        results.sort(key=lambda result: result.metadata.distance)
        return SimpleNamespace(objects=results[:limit])

    def _keyword(self, query: str, limit: int, filters: Any = None, **kwargs: Any) -> Any:
        self.calls.append(("keyword", {"query": query, "limit": limit, "filters": filters, **kwargs}))
        items = [item for item in self.objects.values() if self._allowed(item, filters)]
        return SimpleNamespace(
            objects=[self._result(item, score=1.0 / (i + 1)) for i, item in enumerate(items)][:limit]
        )


def make_weaviate_store() -> tuple[Any, FakeWeaviateCollection, list[StoreRecord]]:
    pytest.importorskip("weaviate")
    from uwcopilot.rag.stores.weaviate_store import WeaviateStore

    collection = FakeWeaviateCollection()
    store = WeaviateStore(collection)
    records = make_records()
    assert store.upsert(records) == len(records)
    return store, collection, records


def test_weaviate_upsert_stores_uuid_named_vector_and_payload() -> None:
    store, collection, records = make_weaviate_store()
    assert store.count() == len(records)
    record = records[0]
    stored = collection.objects[record.id]
    assert stored.vector == {"dense": record.dense}
    assert json.loads(stored.properties["payload_json"]) == record.payload
    assert stored.properties["chunk_id"] == record.payload["chunk_id"]
    assert stored.properties["embedding_text"].startswith(record.payload["doc_title"])


@pytest.mark.parametrize("query", QUERIES)
def test_weaviate_dense_scores_are_cosine_similarity(query: str) -> None:
    store, _collection, records = make_weaviate_store()
    vector = HashingDenseEmbedder(dim=128).embed_query(query)
    expected = scores_by_chunk(InMemoryStore(records).dense_search(vector, 10))
    assert scores_by_chunk(store.dense_search(vector, 10)) == pytest.approx(expected, abs=1e-9)


def test_weaviate_filter_and_native_modes() -> None:
    store, collection, _records = make_weaviate_store()
    from weaviate.classes.query import HybridFusion

    from uwcopilot.rag.stores.weaviate_store import WEAVIATE_NATIVE_MODES, WeaviateNativeRetriever, weaviate_filter

    hits = store.dense_search([1.0] * 128, 10, source_docs=["harborline_mutual_commercial"])
    assert hits and {hit.payload["doc_id"] for hit in hits} == {"harborline_mutual_commercial"}
    sent = collection.calls[-1][1]["filters"]
    assert (sent.target, sent.value) == ("doc_id", ["harborline_mutual_commercial"])
    assert weaviate_filter(None) is None

    native = WeaviateNativeRetriever(store, HashingDenseEmbedder(dim=128))
    for mode in WEAVIATE_NATIVE_MODES:
        results = native.search("loss ratio", mode=mode, top_k=3)
        assert [result.rank for result in results] == [1, 2, 3]
    keyword_calls = [arguments for name, arguments in collection.calls if name == "keyword"]
    assert "fusion_type" not in keyword_calls[0]
    assert keyword_calls[1]["fusion_type"] == HybridFusion.RANKED and keyword_calls[1]["alpha"] == 0.5
    assert keyword_calls[2]["fusion_type"] == HybridFusion.RELATIVE_SCORE
    assert all(arguments["query_properties"] == ["embedding_text"] for arguments in keyword_calls)
    with pytest.raises(ValueError, match="Unknown Weaviate mode"):
        native.search("x", mode="hybrid")


def test_weaviate_insert_errors_are_raised() -> None:
    store, collection, records = make_weaviate_store()
    collection.data = SimpleNamespace(insert_many=lambda objects: SimpleNamespace(has_errors=True, errors={0: "bad"}))
    with pytest.raises(RuntimeError, match="rejected 1 objects"):
        store.upsert(records)


# ---------------------------------------------------------------------------------------------------------------
# The comparison script's helpers
# ---------------------------------------------------------------------------------------------------------------


def question_result(question_id: str, retrieved: list[str], gold_ranks: list[int | None]) -> QuestionResult:
    return QuestionResult(question_id, "single_section", "hybrid", retrieved, 1.0, gold_ranks, 5.0)


def test_script_defaults_and_agreement() -> None:
    args = eval_rag_vectorstores.parse_args([])
    assert args.backends == ["qdrant", "memory", "pinecone", "weaviate"]
    assert args.modes == list(RETRIEVAL_MODES)
    assert args.pinecone_host == "http://localhost:5080" and args.pinecone_api_key == "pclocal"
    assert args.output.name == "rag_vectorstore_comparison.json"
    reference = [question_result("Q1", ["A", "B"], [1]), question_result("Q2", ["C", "D"], [2])]
    candidate = [question_result("Q1", ["A", "B"], [1]), question_result("Q2", ["D", "C"], [1])]
    match = eval_rag_vectorstores.agreement(candidate, reference)
    assert match["identical_top10"] == 1 and match["identical_top10_as_set"] == 2
    assert match["identical_gold_ranks"] == 1 and match["questions_with_different_top10"] == ["Q2"]
    assert eval_rag_vectorstores.runs_in("weaviate", "bm25") == "BM25: Python (app-side)"
    assert eval_rag_vectorstores.runs_in("pinecone", "hybrid") == "dense: Pinecone; BM25: Pinecone; RRF: Python"
    assert eval_rag_vectorstores.runs_in("qdrant", "hybrid_rerank").endswith("RRF: Qdrant; rerank: Python")

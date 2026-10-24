"""Integration test: the Pinecone and Weaviate adapters against the real services in Docker.

Excluded from `make test`. Start the services with `make vectorstores-up`, then run `make test-integration` (or
`pytest -m integration tests/rag/test_rag_vectorstores_integration.py`). A service that is not reachable on its
default local port, or a missing optional package (`uv sync --extra vectorstores`), skips its test.

The fake embedders are enough here: the test checks that each backend stores and scores the same vectors as the
exact in-memory store, not retrieval quality (that is scripts/eval_rag_vectorstores.py). Test indexes and
collections use their own names and are deleted at the end.
"""

import urllib.request

import pytest
from rag_helpers import SAMPLE_CHUNKS

from uwcopilot.rag.embeddings import HashingDenseEmbedder, HashingSparseEmbedder
from uwcopilot.rag.rerank import TermOverlapReranker
from uwcopilot.rag.retriever import RETRIEVAL_MODES
from uwcopilot.rag.stores.base import records_from_chunks
from uwcopilot.rag.stores.memory import InMemoryStore
from uwcopilot.rag.stores.retriever import StoreRetriever

pytestmark = pytest.mark.integration

PINECONE_HOST = "http://localhost:5080"
WEAVIATE_READY_URL = "http://localhost:8080/v1/.well-known/ready"
QUERIES = ["minimum debt service coverage ratio for a line of credit", "loss ratio senior underwriter"]


def reachable(url: str, headers: dict[str, str] | None = None) -> bool:
    """True when url answers within two seconds."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=2):
            return True
    except OSError:
        return False


def scores_by_chunk(hits: list) -> dict[str, float]:
    """chunk_id -> score."""
    return {hit.payload["chunk_id"]: hit.score for hit in hits}


def test_pinecone_local_scores_like_the_exact_store() -> None:
    pytest.importorskip("pinecone")
    if not reachable(f"{PINECONE_HOST}/indexes", {"Api-Key": "pclocal"}):
        pytest.skip(f"Pinecone Local not reachable at {PINECONE_HOST} (run `make vectorstores-up`)")
    from uwcopilot.rag.stores.pinecone_store import (
        PineconeStore,
        connect_pinecone,
        create_pinecone_indexes,
        delete_pinecone_indexes,
    )

    dense, sparse = HashingDenseEmbedder(dim=128), HashingSparseEmbedder()
    records = records_from_chunks(SAMPLE_CHUNKS, dense, sparse)
    memory = InMemoryStore(records)
    dense_index, sparse_index = create_pinecone_indexes(connect_pinecone(PINECONE_HOST), dense.dim, name="uwc-test")
    try:
        store = PineconeStore(dense_index, sparse_index)
        store.upsert(records)
        assert store.wait_for_count(len(records)) == {"dense": len(records), "sparse": len(records)}
        for query in QUERIES:
            dense_vector, sparse_vector = dense.embed_query(query), sparse.embed_query(query)
            assert scores_by_chunk(store.dense_search(dense_vector, 10)) == pytest.approx(
                scores_by_chunk(memory.dense_search(dense_vector, 10)), abs=1e-5
            )
            assert scores_by_chunk(store.sparse_search(sparse_vector, 10)) == pytest.approx(
                scores_by_chunk(memory.sparse_search(sparse_vector, 10)), abs=1e-4
            )
        retriever = StoreRetriever(store, store, dense, sparse, TermOverlapReranker())
        for mode in RETRIEVAL_MODES:
            assert retriever.search(QUERIES[0], mode=mode, top_k=3)[0].chunk_id == "NBC-4.2"
    finally:
        delete_pinecone_indexes("uwc-test", host=PINECONE_HOST)


def test_weaviate_scores_like_the_exact_store() -> None:
    pytest.importorskip("weaviate")
    if not reachable(WEAVIATE_READY_URL):
        pytest.skip(f"Weaviate not reachable at {WEAVIATE_READY_URL} (run `make vectorstores-up`)")
    from uwcopilot.rag.stores.weaviate_store import (
        WEAVIATE_NATIVE_MODES,
        WeaviateNativeRetriever,
        WeaviateStore,
        connect_weaviate,
        create_weaviate_collection,
    )

    dense, sparse = HashingDenseEmbedder(dim=128), HashingSparseEmbedder()
    records = records_from_chunks(SAMPLE_CHUNKS, dense, sparse)
    memory = InMemoryStore(records)
    client = connect_weaviate()
    try:
        store = WeaviateStore(create_weaviate_collection(client, "UwcTestGuidelines"))
        assert store.upsert(records) == store.count() == len(records)
        for query in QUERIES:
            vector = dense.embed_query(query)
            assert scores_by_chunk(store.dense_search(vector, 10)) == pytest.approx(
                scores_by_chunk(memory.dense_search(vector, 10)), abs=1e-5
            )
        hits = store.dense_search(dense.embed_query(QUERIES[1]), 10, source_docs=["harborline_mutual_commercial"])
        assert {hit.payload["doc_id"] for hit in hits} == {"harborline_mutual_commercial"}
        retriever = StoreRetriever(store, memory, dense, sparse, TermOverlapReranker())
        for mode in RETRIEVAL_MODES:
            assert retriever.search(QUERIES[0], mode=mode, top_k=3)[0].chunk_id == "NBC-4.2"
        native = WeaviateNativeRetriever(store, dense)
        for mode in WEAVIATE_NATIVE_MODES:
            assert native.search(QUERIES[1], mode=mode, top_k=3)[0].chunk_id == "HM-4.2"
    finally:
        client.collections.delete("UwcTestGuidelines")
        client.close()

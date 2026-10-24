"""Other vector databases for the same retrieval: a small store interface plus Pinecone and Weaviate backends.

The Qdrant index (index.py, retriever.py) stays the project's main index. This package lets the SAME points
(ids, payloads, dense and BM25 vectors), query embeddings, fusion and reranker run on other vector databases, so
their retrieval quality and latency can be measured side by side (scripts/eval_rag_vectorstores.py,
docs/rag_vectorstores.md).

- base.py: StoreRecord (one stored point), StoreHit (one search result), the DenseStore / SparseStore protocols,
  and SparseIdf (BM25's inverse document frequency, computed with Qdrant's formula).
- memory.py: InMemoryStore, an exact brute-force store in plain Python (the reference, and the offline fake).
- retriever.py: StoreRetriever, the four retrieval modes of rag/retriever.py with fusion and reranking in Python.
- pinecone_store.py: Pinecone with one dense and one sparse index (measured against Pinecone Local in Docker).
- weaviate_store.py: Weaviate with self-provided vectors, plus Weaviate's own BM25 and hybrid search.

The pinecone and weaviate-client packages are optional (`uv sync --extra vectorstores`) and are imported only
where a real client is created, so the offline tests run without them.
"""

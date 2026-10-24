"""Retrieval-augmented generation (RAG) over the underwriting guidelines and public regulatory documents.

The pieces, in the order data flows through them:

- guidelines.py / regulatory.py: turn source files into Chunk objects (one per guideline section; token
  windows with overlap for regulatory PDFs and HTML), keeping ids, titles, headings and page or paragraph numbers.
- corpus.py: loads every source, with SHA-256 checksums, so an index can be tied to the exact files it came from.
- embeddings.py / rerank.py: fastembed models (dense BGE, sparse BM25, cross-encoder) plus deterministic fakes
  for offline tests.
- index.py: writes chunks into a Qdrant collection with a named dense vector and a named sparse BM25 vector.
- retriever.py: dense, BM25, hybrid (Qdrant prefetch + reciprocal rank fusion) and hybrid + rerank search.
- search.py: the `search_guidelines` tool handler used by the tool registry, MCP server and agent.
- prompt.py / citations.py / answer.py: grounded answering with delimited documents and a citation checker.
- eval_set.py / metrics.py / evaluation.py: the retrieval evaluation used by scripts/eval_rag.py.
- stores/: the same points, fusion and reranker on Pinecone and Weaviate, for scripts/eval_rag_vectorstores.py.

Retrieved text is always data, never instructions (ground rule 8).
"""

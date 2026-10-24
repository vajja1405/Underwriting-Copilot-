"""Pinecone backend: one dense index and one sparse index, measured against Pinecone Local in Docker.

Layout (one index per vector type; Pinecone also offers a single dotproduct index holding both, not used here):
- "<name>-dense": vector_type "dense", dimension 384, metric "cosine"; the BGE vector of every Qdrant point;
- "<name>-sparse": vector_type "sparse", metric "dotproduct" (the only metric for sparse indexes); the same BM25
  term-frequency weights as the Qdrant "bm25" vector;
- the same point id in both, and metadata: doc_id, doc_kind and chunk_id (for filters) plus payload_json, the full
  Qdrant payload as one JSON string. Pinecone metadata cannot hold nulls or lists of numbers (the payload has both:
  issuer, pages), so storing the payload as a string keeps it exact.

BM25 IDF: Qdrant applies the IDF itself (modifier=IDF); a Pinecone sparse index only takes the dot product. So
sparse_search multiplies the query weights by Qdrant's IDF (base.SparseIdf) first, and the score is the same
number Qdrant computes. The IDF table is counted from the records given to upsert(), so it lives in this process;
a store opened on an index written by another process needs set_idf() with the same records before BM25 search.

Pinecone Local (ghcr.io/pinecone-io/pinecone-local) is Pinecone's in-memory emulator for development: no
persistence, any API key (conventionally "pclocal"), plain HTTP on localhost. Its latency says nothing about the
hosted Pinecone service, and nothing here was run against hosted Pinecone.

Two compatibility notes, both found by running against Pinecone Local v1.0.0.rc0 (the image behind the "latest"
tag) with the pinecone 10.x SDK:
- the SDK's create/describe calls use a newer control-plane format (a "schema" field) that Pinecone Local rejects,
  so indexes are created, described and deleted with the documented REST API at version 2025-01 (control_call);
- over HTTP the SDK sends `"values": []` with a sparse-only vector, which Pinecone Local rejects as the wrong
  vector type, so the data plane (upsert, query, stats) uses the SDK's gRPC client, where an empty list is simply
  absent.

The pinecone package is optional (`uv sync --extra vectorstores`) and is imported only in connect_pinecone();
PineconeStore itself only calls methods on the index objects it is given, so the unit tests pass fakes.
"""

import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

from uwcopilot.rag.embeddings import SparseVector
from uwcopilot.rag.stores.base import SparseIdf, StoreHit, StoreRecord

PINECONE_LOCAL_HOST = "http://localhost:5080"
PINECONE_LOCAL_API_KEY = "pclocal"
CONTROL_API_VERSION = "2025-01"
DEFAULT_INDEX_NAME = "uwc-guidelines"
FILTER_FIELDS = ("doc_id", "doc_kind", "chunk_id")


def connect_pinecone(host: str = PINECONE_LOCAL_HOST, api_key: str = PINECONE_LOCAL_API_KEY) -> Any:
    """A Pinecone client for the data plane; gRPC over plain HTTP when host is an http:// URL (Pinecone Local)."""
    from pinecone import Pinecone

    return Pinecone(api_key=api_key, host=host, grpc_scheme="http" if host.startswith("http://") else "https")


def control_call(
    host: str, api_key: str, method: str, path: str, body: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """One control-plane REST call (API version 2025-01). Returns the JSON reply, or None for 404 Not Found."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{host.rstrip('/')}{path}",
        data=data,
        method=method,
        headers={"Api-Key": api_key, "Content-Type": "application/json", "X-Pinecone-API-Version": CONTROL_API_VERSION},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(f"Pinecone {method} {path} failed: {error.code} {error.read()[:300]!r}") from error
    return json.loads(text) if text.strip() else {}


def create_pinecone_indexes(
    client: Any,
    dim: int,
    name: str = DEFAULT_INDEX_NAME,
    host: str = PINECONE_LOCAL_HOST,
    api_key: str = PINECONE_LOCAL_API_KEY,
    recreate: bool = True,
    timeout_seconds: float = 60.0,
) -> tuple[Any, Any]:
    """Create (or reuse) the dense and the sparse index; return their gRPC data-plane handles (dense, sparse)."""
    handles = []
    for index_name, vector_type, metric, dimension in (
        (f"{name}-dense", "dense", "cosine", dim),
        (f"{name}-sparse", "sparse", "dotproduct", None),
    ):
        if recreate:
            delete_index(host, api_key, index_name, timeout_seconds)
        if control_call(host, api_key, "GET", f"/indexes/{index_name}") is None:
            body: dict[str, Any] = {
                "name": index_name,
                "metric": metric,
                "vector_type": vector_type,
                # Pinecone Local accepts and ignores the serverless spec; hosted Pinecone needs one.
                "spec": {"serverless": {"cloud": "aws", "region": "us-east-1"}},
            }
            if dimension is not None:
                body["dimension"] = dimension
            control_call(host, api_key, "POST", "/indexes", body)
        description = wait_until_ready(host, api_key, index_name, timeout_seconds)
        handles.append(client.index(host=description["host"], grpc=True))
    return handles[0], handles[1]


def wait_until_ready(host: str, api_key: str, index_name: str, timeout_seconds: float) -> dict[str, Any]:
    """Poll the index description until its status is ready (Pinecone Local is ready at once)."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        description = control_call(host, api_key, "GET", f"/indexes/{index_name}")
        if description and description.get("status", {}).get("ready"):
            return description
        if time.monotonic() > deadline:
            raise TimeoutError(f"Pinecone index {index_name!r} not ready after {timeout_seconds} s")
        time.sleep(0.5)


def delete_pinecone_indexes(
    name: str = DEFAULT_INDEX_NAME, host: str = PINECONE_LOCAL_HOST, api_key: str = PINECONE_LOCAL_API_KEY
) -> None:
    """Delete both indexes if they exist."""
    for suffix in ("dense", "sparse"):
        delete_index(host, api_key, f"{name}-{suffix}")


def delete_index(host: str, api_key: str, index_name: str, timeout_seconds: float = 60.0) -> None:
    """Delete one index if it exists and wait until it is gone (deletion is asynchronous: the API answers 202)."""
    if control_call(host, api_key, "GET", f"/indexes/{index_name}") is None:
        return
    control_call(host, api_key, "DELETE", f"/indexes/{index_name}")
    deadline = time.monotonic() + timeout_seconds
    while control_call(host, api_key, "GET", f"/indexes/{index_name}") is not None:
        if time.monotonic() > deadline:
            raise TimeoutError(f"Pinecone index {index_name!r} still exists {timeout_seconds} s after delete")
        time.sleep(0.5)


def pinecone_filter(source_docs: Sequence[str] | None) -> dict[str, Any] | None:
    """A metadata filter on doc_id, or None to search everything."""
    if not source_docs:
        return None
    return {"doc_id": {"$in": list(source_docs)}}


def pinecone_metadata(record: StoreRecord) -> dict[str, Any]:
    """Filterable fields plus the whole payload as JSON (see the module docstring)."""
    metadata: dict[str, Any] = {name: str(record.payload[name]) for name in FILTER_FIELDS}
    metadata["payload_json"] = json.dumps(record.payload, sort_keys=True)
    return metadata


class PineconeStore:
    """Dense and BM25 search on a pair of Pinecone indexes (dense: cosine; sparse: IDF-weighted dot product)."""

    name = "pinecone"

    def __init__(self, dense_index: Any, sparse_index: Any, namespace: str = "") -> None:
        """Use the given index handles (from create_pinecone_indexes, or fakes in tests)."""
        self.dense_index = dense_index
        self.sparse_index = sparse_index
        self.namespace = namespace
        self.idf = SparseIdf(n_documents=0, document_frequency={})

    def set_idf(self, records: Sequence[StoreRecord]) -> None:
        """Count document frequencies from the records that are in the sparse index."""
        self.idf = SparseIdf.from_vectors([record.sparse for record in records])

    def upsert(self, records: Sequence[StoreRecord], batch_size: int = 50) -> int:
        """Write every record to both indexes (same id) and set the IDF table from them."""
        for start in range(0, len(records), batch_size):
            batch = records[start : start + batch_size]
            dense_vectors = [
                {"id": record.id, "values": list(record.dense), "metadata": pinecone_metadata(record)}
                for record in batch
            ]
            sparse_vectors = [
                {
                    "id": record.id,
                    "sparse_values": {"indices": list(record.sparse.indices), "values": list(record.sparse.values)},
                    "metadata": pinecone_metadata(record),
                }
                for record in batch
            ]
            self.dense_index.upsert(vectors=dense_vectors, namespace=self.namespace, show_progress=False)
            self.sparse_index.upsert(vectors=sparse_vectors, namespace=self.namespace, show_progress=False)
        self.set_idf(records)
        return len(records)

    def count(self) -> int:
        """Vectors in the dense index (the sparse index holds the same ids; see counts())."""
        return self.counts()["dense"]

    def counts(self) -> dict[str, int]:
        """Vectors in each index, from describe_index_stats."""
        return {
            "dense": int(self.dense_index.describe_index_stats().total_vector_count),
            "sparse": int(self.sparse_index.describe_index_stats().total_vector_count),
        }

    def wait_for_count(self, expected: int, timeout_seconds: float = 60.0) -> dict[str, int]:
        """Poll until both indexes report `expected` vectors (writes are eventually consistent in Pinecone)."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            counts = self.counts()
            if counts["dense"] == expected and counts["sparse"] == expected:
                return counts
            if time.monotonic() > deadline:
                raise TimeoutError(f"Pinecone has {counts} vectors, expected {expected} in each index")
            time.sleep(0.5)

    def dense_search(
        self, vector: Sequence[float], limit: int, source_docs: Sequence[str] | None = None
    ) -> list[StoreHit]:
        """Nearest neighbours by cosine similarity (Pinecone's score for metric "cosine")."""
        response = self.dense_index.query(
            vector=list(vector),
            top_k=limit,
            filter=pinecone_filter(source_docs),
            include_metadata=True,
            namespace=self.namespace,
        )
        return [to_hit(match) for match in response.matches]

    def sparse_search(
        self, vector: SparseVector, limit: int, source_docs: Sequence[str] | None = None
    ) -> list[StoreHit]:
        """BM25: the IDF-weighted query on the sparse index; matches with score 0 share no token and are dropped."""
        if self.idf.n_documents == 0:
            raise ValueError("PineconeStore has no IDF table: call upsert() or set_idf() with the indexed records")
        weighted = self.idf.weight_query(vector)
        response = self.sparse_index.query(
            sparse_vector={"indices": list(weighted.indices), "values": list(weighted.values)},
            top_k=limit,
            filter=pinecone_filter(source_docs),
            include_metadata=True,
            namespace=self.namespace,
        )
        return [to_hit(match) for match in response.matches if match.score > 0.0]


def to_hit(match: Any) -> StoreHit:
    """A Pinecone match (id, score, metadata) as a StoreHit with the original payload."""
    return StoreHit(id=str(match.id), score=float(match.score), payload=json.loads(match.metadata["payload_json"]))

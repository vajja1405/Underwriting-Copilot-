# Vector stores: the same retrieval on Qdrant, Pinecone and Weaviate

> **Scope and honesty notes.** Pinecone was run as **Pinecone Local**, Pinecone's in-memory emulator for
> development, in Docker on a laptop. Nothing here ran against the hosted Pinecone service, and Pinecone Local's
> speed says nothing about it. Weaviate was run as the open-source server in Docker. Every number below is copied
> from `results/rag_vectorstore_comparison.json`, written by `scripts/eval_rag_vectorstores.py`. The corpus and
> the questions are the ones in `docs/rag_design.md` (the guideline documents are **SYNTHETIC**). Latency is not
> comparable across backends (see section 5).

## 1. What this adds

The project's index is Qdrant (`docs/rag_design.md`). This adds Pinecone and Weaviate as alternative backends
for the same credit-policy retrieval, and measures them with the same 113 questions (103 answerable).

The comparison is built so that only one thing changes: **where the candidate lists come from**. Every backend
gets the same points (copied out of the Qdrant index: id, payload, 384-d BGE vector, BM25 sparse vector), the
same query embeddings, the same reciprocal rank fusion (k = 60, 20 candidates per list), the same cross-encoder
and the same metrics code. So if a backend searches its vectors correctly, its numbers must equal Qdrant's.

| Entry point | What it is |
|---|---|
| `src/uwcopilot/rag/stores/base.py` | `StoreRecord`, `StoreHit`, the `DenseStore` / `SparseStore` protocols, `SparseIdf` (BM25 IDF with Qdrant's formula), `records_from_qdrant()` |
| `src/uwcopilot/rag/stores/memory.py` | `InMemoryStore`: exact brute-force cosine and BM25 in plain Python (the control, and the offline fake) |
| `src/uwcopilot/rag/stores/retriever.py` | `StoreRetriever`: the four modes of `rag/retriever.py` on any store, fusion and rerank in Python |
| `src/uwcopilot/rag/stores/pinecone_store.py` | `PineconeStore`: a dense index + a sparse index |
| `src/uwcopilot/rag/stores/weaviate_store.py` | `WeaviateStore` (self-provided vectors) and `WeaviateNativeRetriever` (Weaviate's own BM25 / hybrid) |
| `scripts/eval_rag_vectorstores.py` (`make eval-rag-vectorstores`) | Runs everything; writes `results/rag_vectorstore_comparison.json` |
| `tests/rag/test_rag_vectorstores.py` | Offline tests with fake Pinecone indexes and a fake Weaviate collection |
| `tests/rag/test_rag_vectorstores_integration.py` | Same checks against the real containers (`integration` marker) |

## 2. How each backend stores and searches

| | Qdrant (reference) | Pinecone | Weaviate |
|---|---|---|---|
| Server | local embedded mode (in-process) | Pinecone Local `ghcr.io/pinecone-io/pinecone-local:latest` (= `v1.0.0.rc0`, amd64) | `cr.weaviate.io/semitechnologies/weaviate:1.40.2` |
| Dense vectors | named vector `dense`, cosine | index `uwc-guidelines-dense`, cosine, 384 | named vector `dense`, self-provided (no vectorizer), HNSW, cosine |
| BM25 | sparse vector `bm25`, `modifier=IDF` | index `uwc-guidelines-sparse`, dotproduct; the query weights are multiplied by Qdrant's IDF first | Weaviate cannot store our sparse vectors: BM25 runs app-side on `InMemoryStore` |
| Payload | Qdrant payload | metadata `doc_id`, `doc_kind`, `chunk_id` + `payload_json` | properties `doc_id`, `doc_kind`, `chunk_id`, `embedding_text` + `payload_json` |
| Fusion and rerank | RRF inside Qdrant; rerank in Python | RRF and rerank in Python (`StoreRetriever`) | RRF and rerank in Python (`StoreRetriever`) |
| Filter `source_docs` | payload filter on `doc_id` | `{"doc_id": {"$in": [...]}}` | `Filter.by_property("doc_id").contains_any([...])` |

**BM25 IDF.** Qdrant's sparse vector uses `modifier=IDF`: at query time it multiplies each query token's weight
by `idf(t) = ln((N - df(t) + 0.5) / (df(t) + 0.5) + 1)` and takes the dot product with the stored term-frequency
weights. A Pinecone sparse index only takes the dot product, so `PineconeStore` multiplies the query weights by
the same IDF (counted from the records it wrote) before it queries. The score is then the same number Qdrant
computes; the tests check this chunk by chunk.

**Why the payload is a JSON string.** Pinecone metadata cannot hold `null` or lists of numbers, and the payload has
both (`issuer`, `pages`). Storing it as one string keeps it exact; the three filter fields are stored separately.

**Weaviate's own search, as extra modes.** `weaviate_bm25` is Weaviate's BM25 over `embedding_text` (word
tokenization, English stopwords, no stemming, k1 = 1.2, b = 0.75). `weaviate_hybrid` is Weaviate's hybrid query
(its BM25 + our query vector) with `rankedFusion` and alpha 0.5, the closest setting to our RRF.
`weaviate_hybrid_relative` is the same with Weaviate's default `relativeScoreFusion`. These use a different BM25
(no stemming, its own tokenizer), so they are expected to differ from the four standard modes.

## 3. How to run

```bash
uv sync --extra vectorstores        # pinecone + weaviate-client (optional extras)
make index                          # the Qdrant index (needs `make data-regulatory`)
make vectorstores-up                # Pinecone Local on :5080-5090, Weaviate on :8080 / gRPC :50051
make eval-rag-vectorstores          # writes results/rag_vectorstore_comparison.json
uv run --extra vectorstores pytest -m integration tests/rag/test_rag_vectorstores_integration.py
make vectorstores-down              # stop and remove both containers
```

Options: `--backends qdrant memory pinecone weaviate`, `--modes dense bm25 hybrid hybrid_rerank`,
`--no-weaviate-native`, `--pinecone-host`, `--weaviate-host/--weaviate-http-port/--weaviate-grpc-port`. A backend
that cannot be reached is written as `"status": "not measured"` with the error, never as a number.

Two compatibility problems were found by running against Pinecone Local `v1.0.0.rc0` with the `pinecone` 10.2 SDK,
and are worked around in `pinecone_store.py`:

- the SDK's create/describe calls send and expect a newer control-plane format (a `schema` field) that Pinecone
  Local rejects (`422 missing field dimension`), so indexes are created, described and deleted with the REST API
  at version `2025-01`;
- over HTTP the SDK sends `"values": []` with a sparse-only vector and Pinecone Local answers `400 wrong vector
  type`, so the data plane uses the SDK's gRPC client.

## 4. Results

Run: 2026-10-24T02:41:30+00:00 (UTC), 113 questions (103 answerable), 108 points,
bootstrap 1000 resamples (seed 42). Machine: arm64, 8 CPUs, ONNX threads 2,
load average [5.98, 8.69, 8.49] at the start and [20.61, 18.3, 14.47] at the end (other jobs were running).
Pinecone Local image
`ghcr.io/pinecone-io/pinecone-local@sha256:0f944585830a30c37260ac6534c7c206280490947e1ddbb716474166b517b6c7`
(amd64, built 2025-02-27), SDK `pinecone` 10.2.0. Weaviate server 1.40.2, image
`cr.weaviate.io/semitechnologies/weaviate@sha256:0083587dc01f33112e5406ce9b471df1abea480c71a0407af903929a389ae08a`
(arm64), SDK `weaviate-client` 4.24.0. Qdrant: `qdrant-client` 1.19.1, local embedded mode.

"Same top 10 as Qdrant" counts the questions (of 113, answerable and unanswerable) whose ten retrieved chunk ids,
in order, equal the Qdrant run of the same mode.

| Backend | Mode | Where it runs | recall@1 | recall@3 | recall@5 | MRR@10 | nDCG@10 | p50 ms | p95 ms | same top 10 as Qdrant |
|---|---|---|---|---|---|---|---|---|---|---|
| qdrant | `dense` | dense: Qdrant | 0.5631 | 0.8010 | 0.8786 | 0.7416 | 0.7774 | 8.64 | 11.41 | n/a |
| qdrant | `bm25` | BM25: Qdrant | 0.4466 | 0.7282 | 0.8107 | 0.6349 | 0.7002 | 1.53 | 1.68 | n/a |
| qdrant | `hybrid` | dense: Qdrant; BM25: Qdrant; RRF: Qdrant | 0.6214 | 0.8738 | 0.9029 | 0.7788 | 0.8090 | 10.48 | 13.96 | n/a |
| qdrant | `hybrid_rerank` | dense: Qdrant; BM25: Qdrant; RRF: Qdrant; rerank: Python | 0.6845 | 0.8689 | 0.9078 | 0.8285 | 0.8441 | 1650.84 | 2009.28 | n/a |
| memory | `dense` | dense: Python (exact) | 0.5631 | 0.8010 | 0.8786 | 0.7416 | 0.7774 | 10.56 | 13.62 | 113/113 |
| memory | `bm25` | BM25: Python (exact) | 0.4466 | 0.7282 | 0.8107 | 0.6349 | 0.7002 | 0.3 | 0.33 | 112/113 |
| memory | `hybrid` | dense: Python (exact); BM25: Python (exact); RRF: Python | 0.6214 | 0.8738 | 0.9029 | 0.7788 | 0.8090 | 11.25 | 14.08 | 112/113 |
| memory | `hybrid_rerank` | dense: Python (exact); BM25: Python (exact); RRF: Python; rerank: Python | 0.6845 | 0.8689 | 0.9078 | 0.8285 | 0.8441 | 1769.93 | 2015.77 | 113/113 |
| pinecone | `dense` | dense: Pinecone | 0.5631 | 0.8010 | 0.8786 | 0.7416 | 0.7774 | 11.84 | 15.87 | 113/113 |
| pinecone | `bm25` | BM25: Pinecone | 0.4466 | 0.7282 | 0.8107 | 0.6349 | 0.7002 | 0.6 | 1.33 | 112/113 |
| pinecone | `hybrid` | dense: Pinecone; BM25: Pinecone; RRF: Python | 0.6214 | 0.8738 | 0.9029 | 0.7788 | 0.8090 | 14.25 | 18.78 | 112/113 |
| pinecone | `hybrid_rerank` | dense: Pinecone; BM25: Pinecone; RRF: Python; rerank: Python | 0.6845 | 0.8689 | 0.9078 | 0.8285 | 0.8441 | 1853.32 | 2233.1 | 113/113 |
| weaviate | `dense` | dense: Weaviate | 0.5631 | 0.8010 | 0.8786 | 0.7416 | 0.7774 | 11.23 | 16.78 | 113/113 |
| weaviate | `bm25` | BM25: Python (app-side) | 0.4466 | 0.7282 | 0.8107 | 0.6349 | 0.7002 | 0.27 | 0.3 | 112/113 |
| weaviate | `hybrid` | dense: Weaviate; BM25: Python (app-side); RRF: Python | 0.6214 | 0.8738 | 0.9029 | 0.7788 | 0.8090 | 12.0 | 17.4 | 112/113 |
| weaviate | `hybrid_rerank` | dense: Weaviate; BM25: Python (app-side); RRF: Python; rerank: Python | 0.6845 | 0.8689 | 0.9078 | 0.8285 | 0.8441 | 1932.18 | 2257.26 | 113/113 |
| weaviate | `weaviate_bm25` | Weaviate BM25 | 0.4417 | 0.6359 | 0.7670 | 0.6051 | 0.6612 | 1.35 | 3.33 | n/a |
| weaviate | `weaviate_hybrid` | Weaviate hybrid (its BM25 + our query vector, its fusion) | 0.6019 | 0.8204 | 0.8835 | 0.7611 | 0.7955 | 15.66 | 22.58 | n/a |
| weaviate | `weaviate_hybrid_relative` | Weaviate hybrid (its BM25 + our query vector, its fusion) | 0.5874 | 0.8301 | 0.8786 | 0.7463 | 0.7882 | 15.4 | 21.26 | n/a |

Means with bootstrap 95% CIs (answerable questions). For `dense`, `bm25`, `hybrid` and `hybrid_rerank`, memory,
Pinecone and Weaviate have **exactly the same** means, CIs and per-category means as Qdrant (checked in the file),
so only Qdrant's rows are shown:

| Backend | Mode | recall@5 [95% CI] | MRR@10 [95% CI] | nDCG@10 [95% CI] |
|---|---|---|---|---|
| qdrant | `dense` | 0.8786 [0.8155, 0.9369] | 0.7416 [0.6737, 0.8077] | 0.7774 [0.7226, 0.8330] |
| qdrant | `bm25` | 0.8107 [0.7379, 0.8835] | 0.6349 [0.5591, 0.7065] | 0.7002 [0.6368, 0.7582] |
| qdrant | `hybrid` | 0.9029 [0.8447, 0.9563] | 0.7788 [0.7170, 0.8380] | 0.8090 [0.7538, 0.8605] |
| qdrant | `hybrid_rerank` | 0.9078 [0.8544, 0.9563] | 0.8285 [0.7683, 0.8846] | 0.8441 [0.7891, 0.8937] |
| weaviate | `weaviate_bm25` | 0.7670 [0.6796, 0.8447] | 0.6051 [0.5257, 0.6825] | 0.6612 [0.5910, 0.7302] |
| weaviate | `weaviate_hybrid` | 0.8835 [0.8155, 0.9369] | 0.7611 [0.6952, 0.8316] | 0.7955 [0.7391, 0.8547] |
| weaviate | `weaviate_hybrid_relative` | 0.8786 [0.8155, 0.9320] | 0.7463 [0.6838, 0.8172] | 0.7882 [0.7328, 0.8469] |

Weaviate's own modes against our Qdrant `hybrid` (paired bootstrap over the 103 answerable questions;
`paired_vs_qdrant_hybrid` in the file):

| Weaviate mode | recall@5 difference [95% CI] | MRR@10 difference [95% CI] |
|---|---|---|
| `weaviate_bm25` | -0.1359 [-0.2233, -0.0583] | -0.1737 [-0.2465, -0.1001] |
| `weaviate_hybrid` | -0.0194 [-0.0777, 0.0388] | -0.0178 [-0.0755, 0.0361] |
| `weaviate_hybrid_relative` | -0.0243 [-0.0874, 0.0388] | -0.0325 [-0.092, 0.0218] |

## 5. Reading the results

**Same vectors, same answers.** With the same points, query vectors, fusion and reranker, Pinecone and Weaviate
gave exactly the same recall@1/3/5, MRR@10 and nDCG@10 (and the same bootstrap CIs and per-category means) as
Qdrant in all four modes. Qdrant's own numbers equal `results/rag_metrics.json` (the committed evaluation).
The corpus checksum is not the same (`168fa126...` here, `6bd55a03...` there): the local copy of
`data/raw/regulatory/nydfs_circular_letter_2024_07.html` has different bytes from the one that run used (the other
three source files hash the same). The chunk counts per document are identical, and so are all metrics.
Every one of the 113 questions got the same gold ranks on every backend. Only two questions have a different
top-10 order, and both are exact score ties, the same on every app-side backend (memory, Pinecone, Weaviate):

- `RG13`, `bm25`: two chunks have the same BM25 score (4.545634) at ranks 10 and 11; Qdrant and the app-side
  code break the tie the other way, so a different chunk is 10th.
- `HM21`, `hybrid`: two chunks tie in the BM25 list (3.033078) exactly at the 20-candidate cut-off. Qdrant keeps
  one inside its 20 and the app-side code the other, so one chunk gets an RRF contribution from both lists in Qdrant
  and from one list elsewhere; the order of ranks 4-9 changes. The gold chunk stays at the same rank. After the
  cross-encoder reorders the candidates, the `hybrid_rerank` top 10 is identical on every backend (113/113).

The memory backend is the control: it uses the same Python fusion as Pinecone and Weaviate but no database, and it
shows the same two differences. So they come from tie-breaking in the fusion step, not from Pinecone or Weaviate.
Weaviate's HNSW index returned exact nearest neighbours here (113/113 identical dense top 10s), which is expected
for 108 vectors with default HNSW settings; at a much larger scale an approximate index can miss neighbours.

**Weaviate's own keyword search is a different BM25.** `weaviate_bm25` (no stemming, its own tokenizer) is lower
than the project's stemmed `Qdrant/bm25` on this set: recall@5 0.7670 vs 0.8107 and MRR@10 0.6051 vs 0.6349
(not tested as a pair). Weaviate's hybrid with that BM25 (`weaviate_hybrid`, `weaviate_hybrid_relative`) is a little
below our `hybrid` on every metric in the table, but the paired 95% CIs of the differences include 0, so this
set cannot tell them apart. Nothing here was tuned (alpha 0.5, Weaviate defaults otherwise).

**Latency is not a fair race here.** Qdrant ran in-process (local embedded mode, no network); the memory store is
plain Python; Pinecone Local and Weaviate were Docker containers reached over localhost, and Pinecone Local's image
is amd64-only, so it ran under emulation on this arm64 machine. The machine was shared (load average rose from
5.98 to 20.61 during the run). On every backend, `dense` and `hybrid` p50 were 8.64-14.25 ms while `bm25` (no dense
query embedding) was 0.27-1.53 ms, which suggests most of the dense time is embedding the query; that split was not
timed separately. `hybrid_rerank` p50 was 1650.84-1932.18 ms, almost all of it the cross-encoder (`hybrid` alone is
at most 14.25 ms p50). These numbers say nothing about hosted Pinecone or a production Weaviate.

## 6. Not measured

- Hosted Pinecone (serverless) and Weaviate Cloud: not run. Only Pinecone Local and a local Weaviate container.
- Pinecone's single hybrid index (dense + sparse values in one dotproduct index): not built.
- Weaviate with our own BM25 inside Weaviate: not possible with self-provided sparse vectors; BM25 for the Weaviate
  standard modes ran app-side, and Weaviate's own BM25 was measured separately.
- Scale: 108 points only. No recall-versus-speed trade-off of the approximate indexes was measured.
- Generation (answers from an LLM): not part of this comparison.

## 7. Questions you may be asked

1. *Why copy the vectors out of Qdrant instead of re-embedding?* So that every backend searches the same numbers.
   Re-embedding can change the last digits of a float, and then a difference could come from the model, not the
   database.
2. *How did you make Pinecone's BM25 equal Qdrant's?* Qdrant applies IDF at query time (`modifier=IDF`). Pinecone's
   sparse index only takes a dot product, so the code multiplies each query token's weight by the same IDF formula
   before the query. The unit tests check every chunk's score against the exact in-memory store.
3. *Why is there a "memory" backend?* It is the control: exact search with the same Python fusion as Pinecone and
   Weaviate. It showed the only two differences from Qdrant come from tie-breaking in fusion, not from a database.
4. *Why does Weaviate's own BM25 score lower?* It is a different BM25: Weaviate tokenizes words without stemming,
   while `Qdrant/bm25` stems ("approved" and "approval" match). On this question set the stemmed version found more
   gold passages.
5. *Which database is faster?* This setup cannot say: one ran in-process, one under CPU emulation, and the machine
   was busy. The latency columns are recorded for completeness, not as a benchmark.
6. *What broke along the way?* The `pinecone` 10.x SDK does not fully speak Pinecone Local's older API: index
   creation and sparse upserts over HTTP fail. Index management uses the REST API at version 2025-01 and data uses
   the gRPC client (section 3).

"""Run the retrieval evaluation on Pinecone and Weaviate next to Qdrant; write results/rag_vectorstore_comparison.json.

What it does:
1. Opens the Qdrant index built by scripts/build_index.py and checks that it matches the current source files (the
   same check as scripts/eval_rag.py). Qdrant is the reference: the project's own Retriever on that index, with the
   fusion done inside Qdrant.
2. Copies every point out of Qdrant (id, payload, dense vector, BM25 sparse vector) and writes exactly those records
   into each other backend, so every backend searches the same numbers.
3. Runs the same questions (eval/rag_questions.jsonl) in the same modes with the same query embeddings, the same
   RRF fusion (k = 60, 20 candidates per list) and the same cross-encoder, through stores.StoreRetriever:
   - memory: the exact in-memory store for both legs (checks the Python fusion against Qdrant's own fusion);
   - pinecone: dense and BM25 both in Pinecone (Pinecone Local in Docker; BM25 IDF applied to the query);
   - weaviate: dense in Weaviate; BM25 app-side on the in-memory store (Weaviate cannot store our sparse vectors).
4. Runs Weaviate's own BM25 and hybrid search as extra modes (stores.weaviate_store.WEAVIATE_NATIVE_MODES).
5. Writes, per backend and mode: recall@1/3/5, MRR@10 and nDCG@10 with bootstrap 95% CIs (1,000 resamples of
   questions, seed 42), latency p50/p95, and how many questions got exactly the same top 10 as Qdrant in the same
   mode; plus the backend and SDK versions and the Docker images (from `docker inspect`).

Latency is the wall-clock time of one search() call, as in scripts/eval_rag.py. The backends are NOT on equal
footing: Qdrant runs in-process (local embedded mode, no network), the memory store is plain Python, and Pinecone
Local and Weaviate are Docker containers reached over localhost (Pinecone Local's image is amd64-only and runs under
emulation on Apple silicon). Use the latency numbers only to compare like with like.

Needs `make index`, the optional packages (`uv sync --extra vectorstores`) and the services
(`make vectorstores-up`). A backend that cannot be reached is reported as "not measured" with the error. This
script never calls an LLM.

Usage:
    python scripts/eval_rag_vectorstores.py
    python scripts/eval_rag_vectorstores.py --backends pinecone --modes dense hybrid
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.rag.chunking import ChunkingParams
from uwcopilot.rag.corpus import load_corpus
from uwcopilot.rag.embeddings import FastEmbedBM25, FastEmbedDense
from uwcopilot.rag.eval_set import QUESTIONS_FILE, EvalQuestion, load_questions, validate_gold
from uwcopilot.rag.evaluation import EVAL_DEPTH, QuestionResult, paired_comparison, run_mode, summarize_mode
from uwcopilot.rag.index import describe_qdrant, index_matches_corpus, make_qdrant_client
from uwcopilot.rag.rerank import FastEmbedReranker
from uwcopilot.rag.retriever import RETRIEVAL_MODES, RetrievalParams, Retriever
from uwcopilot.rag.settings import load_rag_settings
from uwcopilot.rag.stores.base import StoreRecord, records_from_qdrant
from uwcopilot.rag.stores.memory import InMemoryStore
from uwcopilot.rag.stores.pinecone_store import (
    CONTROL_API_VERSION,
    PINECONE_LOCAL_API_KEY,
    PINECONE_LOCAL_HOST,
    PineconeStore,
    connect_pinecone,
    create_pinecone_indexes,
)
from uwcopilot.rag.stores.retriever import StoreRetriever
from uwcopilot.rag.stores.weaviate_store import (
    DEFAULT_COLLECTION_NAME,
    HYBRID_ALPHA,
    WEAVIATE_NATIVE_MODES,
    WeaviateNativeRetriever,
    WeaviateStore,
    connect_weaviate,
    create_weaviate_collection,
)

OUTPUT_FILE = RESULTS_DIR / "rag_vectorstore_comparison.json"
BACKENDS = ("qdrant", "memory", "pinecone", "weaviate")
PINECONE_CONTAINER = "fundproof-pinecone-local"
WEAVIATE_CONTAINER = "fundproof-weaviate"
TABLE_METRICS = ("recall@1", "recall@3", "recall@5", "mrr@10", "ndcg@10")
IMAGE_FORMAT = "{{json .RepoDigests}}|{{.Architecture}}|{{.Created}}"
# Where each leg of a search runs, per backend (for the "runs_in" column of the table).
DENSE_LEG = {"qdrant": "Qdrant", "memory": "Python (exact)", "pinecone": "Pinecone", "weaviate": "Weaviate"}
BM25_LEG = {"qdrant": "Qdrant", "memory": "Python (exact)", "pinecone": "Pinecone", "weaviate": "Python (app-side)"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--questions", type=Path, default=QUESTIONS_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument("--backends", nargs="+", choices=BACKENDS, default=list(BACKENDS))
    parser.add_argument("--modes", nargs="+", choices=RETRIEVAL_MODES, default=list(RETRIEVAL_MODES))
    parser.add_argument("--no-weaviate-native", action="store_true", help="skip Weaviate's own BM25/hybrid modes")
    parser.add_argument("--pinecone-host", default=PINECONE_LOCAL_HOST)
    parser.add_argument("--pinecone-api-key", default=PINECONE_LOCAL_API_KEY)
    parser.add_argument("--pinecone-container", default=PINECONE_CONTAINER, help="container name for docker inspect")
    parser.add_argument("--weaviate-host", default="localhost")
    parser.add_argument("--weaviate-http-port", type=int, default=8080)
    parser.add_argument("--weaviate-grpc-port", type=int, default=50051)
    parser.add_argument("--weaviate-container", default=WEAVIATE_CONTAINER, help="container name for docker inspect")
    parser.add_argument("--threads", type=int, default=2, help="ONNX threads for the models (default 2)")
    parser.add_argument("--resamples", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args(argv)


def load_average() -> list[float] | None:
    """The machine's 1/5/15-minute load average (context for latency numbers), or None where unavailable."""
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except OSError:
        return None


def package_version(name: str) -> str:
    """The installed version of a package, or "not installed" (the vector-store SDKs are an optional extra)."""
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def docker_image(container: str) -> dict[str, Any] | None:
    """Image name, digest, architecture and creation date of a running container, from `docker inspect`."""
    try:
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{.Config.Image}}|{{.Image}}", container],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        image_name, image_id = inspected.stdout.strip().split("|")
        details = subprocess.run(
            ["docker", "image", "inspect", "--format", IMAGE_FORMAT, image_id],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"WARNING: docker inspect {container} failed: {error}")
        return None
    digests, architecture, created = details.stdout.strip().split("|")
    return {
        "container": container,
        "image": image_name,
        "repo_digests": json.loads(digests),
        "architecture": architecture,
        "image_created": created,
    }


def run_modes(
    retriever: Any, questions: Sequence[EvalQuestion], modes: Sequence[str], args: argparse.Namespace, label: str
) -> tuple[dict[str, list[QuestionResult]], dict[str, dict[str, Any]]]:
    """run_mode + summarize_mode for each mode; prints one line per mode."""
    results: dict[str, list[QuestionResult]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for mode in modes:
        started = time.perf_counter()
        results[mode] = run_mode(retriever, questions, mode)  # type: ignore[arg-type]
        summaries[mode] = summarize_mode(results[mode], args.resamples, args.seed)
        print(
            f"{label:9s} {mode:25s} recall@5={summaries[mode]['metrics']['recall@5']['mean']:.4f} "
            f"mrr@10={summaries[mode]['metrics']['mrr@10']['mean']:.4f} "
            f"p50={summaries[mode]['latency_ms']['p50']} ms ({time.perf_counter() - started:.1f} s)"
        )
    return results, summaries


def agreement(results: Sequence[QuestionResult], reference: Sequence[QuestionResult]) -> dict[str, Any]:
    """How many questions got the same top 10 (ids and order) and the same gold ranks as the reference run."""
    by_id = {result.question_id: result for result in reference}
    pairs = [(result, by_id[result.question_id]) for result in results if result.question_id in by_id]
    differing = [result.question_id for result, ref in pairs if result.retrieved_ids != ref.retrieved_ids]
    return {
        "n_questions": len(pairs),
        "identical_top10": sum(1 for result, ref in pairs if result.retrieved_ids == ref.retrieved_ids),
        "identical_top10_as_set": sum(1 for r, ref in pairs if set(r.retrieved_ids) == set(ref.retrieved_ids)),
        "identical_gold_ranks": sum(1 for result, ref in pairs if result.gold_ranks == ref.gold_ranks),
        "questions_with_different_top10": differing,
    }


def add_agreement(
    summaries: dict[str, dict[str, Any]],
    results: dict[str, list[QuestionResult]],
    reference: dict[str, list[QuestionResult]],
) -> None:
    """Attach agreement with the Qdrant run of the same mode to every summary (when Qdrant ran that mode)."""
    for mode, mode_results in results.items():
        if mode in reference:
            summaries[mode]["agreement_with_qdrant"] = agreement(mode_results, reference[mode])


def runs_in(backend: str, mode: str) -> str:
    """A short description of where the parts of one search run, for example "dense: Pinecone"."""
    if mode == "weaviate_bm25":
        return "Weaviate BM25"
    if mode in WEAVIATE_NATIVE_MODES:
        return "Weaviate hybrid (its BM25 + our query vector, its fusion)"
    if mode == "dense":
        return f"dense: {DENSE_LEG[backend]}"
    if mode == "bm25":
        return f"BM25: {BM25_LEG[backend]}"
    fusion = "Qdrant" if backend == "qdrant" else "Python"
    text = f"dense: {DENSE_LEG[backend]}; BM25: {BM25_LEG[backend]}; RRF: {fusion}"
    return text + "; rerank: Python" if mode == "hybrid_rerank" else text


def table_rows(backends: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One flat row per backend and mode with the mean metrics and latency (the table in docs/rag_vectorstores.md)."""
    rows = []
    for backend, report in backends.items():
        all_modes = {**report.get("modes", {}), **report.get("native_modes", {})}
        for mode, summary in all_modes.items():
            row: dict[str, Any] = {"backend": backend, "mode": mode, "runs_in": runs_in(backend, mode)}
            row.update({name: summary["metrics"][name]["mean"] for name in TABLE_METRICS})
            row["latency_p50_ms"] = summary["latency_ms"]["p50"]
            row["latency_p95_ms"] = summary["latency_ms"]["p95"]
            match = summary.get("agreement_with_qdrant")
            row["identical_top10_vs_qdrant"] = f"{match['identical_top10']}/{match['n_questions']}" if match else "n/a"
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    """Run the comparison and write the results file. Returns a process exit code."""
    args = parse_args(argv)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    load_start = load_average()
    settings = load_rag_settings()
    cache = settings.model_cache_dir
    dense = FastEmbedDense(cache_dir=cache, threads=args.threads)
    sparse = FastEmbedBM25(cache_dir=cache, threads=args.threads)
    reranker = FastEmbedReranker(cache_dir=cache, threads=args.threads) if "hybrid_rerank" in args.modes else None
    params = RetrievalParams()
    questions = load_questions(args.questions)

    client = make_qdrant_client(settings)
    try:
        records: list[StoreRecord] = records_from_qdrant(client, settings.collection)
        payloads = [record.payload for record in records]
        corpus = load_corpus(count_tokens=dense.count_tokens, params=ChunkingParams())
        differences = index_matches_corpus(payloads, corpus.chunks)
        if differences:
            print("ERROR: the Qdrant index does not match the current source files; run `make index`.")
            print("\n".join(differences[:20]))
            return 1
        problems = validate_gold(questions, payloads)
        if problems:
            print("ERROR: gold references that match no indexed chunk:\n" + "\n".join(problems))
            return 1
        print(f"Copied {len(records)} points (payload + dense + BM25 vectors) out of Qdrant {settings.collection!r}.")

        backends: dict[str, dict[str, Any]] = {}
        qdrant_results: dict[str, list[QuestionResult]] = {}
        if "qdrant" in args.backends:
            retriever = Retriever(client, settings.collection, dense, sparse, reranker, params)
            qdrant_results, summaries = run_modes(retriever, questions, args.modes, args, "qdrant")
            backends["qdrant"] = {
                "role": "reference: the project's Retriever (rag/retriever.py); RRF fusion inside Qdrant",
                "server": describe_qdrant(client, settings),
                "dense_leg": "qdrant (exact search in local embedded mode)",
                "bm25_leg": "qdrant sparse vector with modifier=IDF",
                "modes": summaries,
            }
    finally:
        client.close()

    if "memory" in args.backends:
        memory = InMemoryStore(records)
        retriever = StoreRetriever(memory, memory, dense, sparse, reranker, params)
        results, summaries = run_modes(retriever, questions, args.modes, args, "memory")
        add_agreement(summaries, results, qdrant_results)
        backends["memory"] = {
            "role": "control: exact brute-force search in Python with the same fusion code as pinecone/weaviate",
            "dense_leg": "in-memory exact cosine",
            "bm25_leg": "in-memory exact IDF-weighted dot product (Qdrant's IDF formula)",
            "modes": summaries,
        }

    if "pinecone" in args.backends:
        backends["pinecone"] = run_pinecone(args, records, questions, dense, sparse, reranker, params, qdrant_results)

    if "weaviate" in args.backends:
        backends["weaviate"] = run_weaviate(args, records, questions, dense, sparse, reranker, params, qdrant_results)

    answerable = [question for question in questions if question.answerable]
    report = {
        "generated_at": started_at,
        "generated_by": "scripts/eval_rag_vectorstores.py",
        "status": "measured",
        "what_is_compared": (
            "the same points (copied out of the Qdrant index), query embeddings, RRF fusion and cross-encoder on "
            "each backend; only where the dense and BM25 candidate lists come from changes"
        ),
        "corpus": {
            "checksum_sha256": corpus.checksum(),
            "points": len(records),
            "chunks_by_doc": corpus.chunk_counts(),
        },
        "models": {
            "dense": {"id": dense.model_id, "dim": dense.dim, "query_prefix": dense.query_prefix},
            "sparse": {"id": sparse.model_id, "idf": "Qdrant modifier=IDF formula"},
            "reranker": {"id": reranker.model_id} if reranker else None,
            "fastembed_version": version("fastembed"),
        },
        "retrieval_params": {**asdict(params), "eval_depth": EVAL_DEPTH},
        "question_set": {
            "path": "eval/rag_questions.jsonl",
            "sha256": sha256_of_file(args.questions),
            "n_total": len(questions),
            "n_answerable": len(answerable),
        },
        "bootstrap": {
            "resamples": args.resamples,
            "seed": args.seed,
            "confidence": 0.95,
            "unit": "question",
            "method": "percentile",
        },
        "backends": backends,
        "table": table_rows(backends),
        "environment": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "onnx_threads": args.threads,
            "load_average_start": load_start,
            "load_average_end": load_average(),
            "latency_note": (
                "wall-clock per query on a shared machine; qdrant is in-process, pinecone/weaviate are Docker "
                "containers on localhost (Pinecone Local's amd64 image runs under emulation on arm64)"
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


def run_pinecone(
    args: argparse.Namespace,
    records: list[StoreRecord],
    questions: Sequence[EvalQuestion],
    dense: FastEmbedDense,
    sparse: FastEmbedBM25,
    reranker: FastEmbedReranker | None,
    params: RetrievalParams,
    qdrant_results: dict[str, list[QuestionResult]],
) -> dict[str, Any]:
    """Write the records to Pinecone (dense + sparse index) and run every mode; "not measured" if unreachable."""
    report: dict[str, Any] = {
        "server": {"host": args.pinecone_host, "docker": docker_image(args.pinecone_container)},
        "sdk": {"package": "pinecone", "version": package_version("pinecone")},
        "transport": f"data plane: SDK gRPC client; control plane: REST API version {CONTROL_API_VERSION}",
        "index_layout": "two indexes: <name>-dense (cosine, 384) and <name>-sparse (dotproduct); same ids",
        "dense_leg": "pinecone dense index",
        "bm25_leg": "pinecone sparse index; query weights multiplied by Qdrant's IDF before the query",
    }
    try:
        client = connect_pinecone(args.pinecone_host, args.pinecone_api_key)
        dense_index, sparse_index = create_pinecone_indexes(
            client, dense.dim, host=args.pinecone_host, api_key=args.pinecone_api_key
        )
        store = PineconeStore(dense_index, sparse_index)
        started = time.perf_counter()
        store.upsert(records)
        report["counts_after_upsert"] = store.wait_for_count(len(records))
        report["upsert_seconds"] = round(time.perf_counter() - started, 2)
    except Exception as error:  # noqa: BLE001 - any connection or API error means the backend was not measured
        print(f"WARNING: Pinecone not measured: {error}")
        return {**report, "status": "not measured", "error": f"{type(error).__name__}: {error}"}
    retriever = StoreRetriever(store, store, dense, sparse, reranker, params)
    results, summaries = run_modes(retriever, questions, args.modes, args, "pinecone")
    add_agreement(summaries, results, qdrant_results)
    return {**report, "status": "measured", "modes": summaries}


def run_weaviate(
    args: argparse.Namespace,
    records: list[StoreRecord],
    questions: Sequence[EvalQuestion],
    dense: FastEmbedDense,
    sparse: FastEmbedBM25,
    reranker: FastEmbedReranker | None,
    params: RetrievalParams,
    qdrant_results: dict[str, list[QuestionResult]],
) -> dict[str, Any]:
    """Write the records to Weaviate and run every mode plus Weaviate's own BM25/hybrid; "not measured" if down."""
    report: dict[str, Any] = {
        "server": {
            "host": f"{args.weaviate_host}:{args.weaviate_http_port} (gRPC {args.weaviate_grpc_port})",
            "docker": docker_image(args.weaviate_container),
        },
        "sdk": {"package": "weaviate-client", "version": package_version("weaviate-client")},
        "collection": DEFAULT_COLLECTION_NAME,
        "vector_index": "HNSW, cosine distance, self-provided vectors (no vectorizer module), default HNSW settings",
        "dense_leg": "weaviate near_vector",
        "bm25_leg": "app-side: in-memory exact BM25 over the same sparse vectors (Weaviate cannot store them)",
    }
    client = None
    try:
        client = connect_weaviate(args.weaviate_host, args.weaviate_http_port, args.weaviate_grpc_port)
        report["server"]["version"] = client.get_meta().get("version")
        store = WeaviateStore(create_weaviate_collection(client))
        started = time.perf_counter()
        store.upsert(records)
        report["count_after_upsert"] = store.count()
        report["upsert_seconds"] = round(time.perf_counter() - started, 2)
        if report["count_after_upsert"] != len(records):
            raise RuntimeError(f"Weaviate holds {report['count_after_upsert']} objects, expected {len(records)}")
        retriever = StoreRetriever(store, InMemoryStore(records), dense, sparse, reranker, params)
        results, summaries = run_modes(retriever, questions, args.modes, args, "weaviate")
        add_agreement(summaries, results, qdrant_results)
        report["modes"] = summaries
        if not args.no_weaviate_native:
            native = WeaviateNativeRetriever(store, dense, params)
            native_modes = list(WEAVIATE_NATIVE_MODES)
            native_results, native_summaries = run_modes(native, questions, native_modes, args, "weaviate")
            for mode, summary in native_summaries.items():
                if "hybrid" in qdrant_results:
                    summary["paired_vs_qdrant_hybrid"] = [
                        paired_comparison(native_results[mode], qdrant_results["hybrid"], metric)
                        for metric in ("recall@5", "mrr@10")
                    ]
            report["native_modes"] = native_summaries
            report["native_modes_settings"] = {
                "weaviate_bm25": "Weaviate BM25 over embedding_text (word tokenization, no stemming, k1=1.2, b=0.75)",
                "weaviate_hybrid": f"Weaviate hybrid, rankedFusion, alpha={HYBRID_ALPHA}, our query vector",
                "weaviate_hybrid_relative": f"Weaviate hybrid, relativeScoreFusion, alpha={HYBRID_ALPHA}",
            }
    except Exception as error:  # noqa: BLE001 - any connection or API error means the backend was not measured
        print(f"WARNING: Weaviate not measured: {error}")
        return {**report, "status": "not measured", "error": f"{type(error).__name__}: {error}"}
    finally:
        if client is not None:
            client.close()
    return {**report, "status": "measured"}


if __name__ == "__main__":
    sys.exit(main())

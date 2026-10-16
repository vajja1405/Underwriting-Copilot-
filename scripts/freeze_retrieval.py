"""Freeze retrieval for the generation evaluation: run search once per question and commit the hits.

Usage:
    uv run python scripts/freeze_retrieval.py                      # hybrid_rerank (needs `make index`); default
    uv run python scripts/freeze_retrieval.py --mode auto           # hybrid_rerank if possible, else BM25 fallback
    uv run python scripts/freeze_retrieval.py --mode bm25_offline_guidelines_only

Inputs: eval/rag_questions.jsonl (113 questions) and eval/rag_unanswerable_extra.jsonl (20 unanswerables).
Outputs (committed, docs/innovations.md S1):
- eval/generation/frozen_hits.jsonl: top-5 hits per question (format in uwcopilot.rag.frozen_hits);
- eval/generation/frozen_hits_manifest.json: retrieval mode and why, the corpus checksum the hits were made from,
  question files and their sha256, counts by category, how often the gold is in the top 1/3/5, and the sha256 of
  the hits file.

Modes:
- hybrid_rerank (default): the project's real retriever, built with rag.search.build_retriever() over the Qdrant
  index from `make index`. Before searching, the script checks that the index holds exactly the chunks of the
  current source files (rag.index.index_matches_corpus) and that every gold reference resolves, as
  scripts/eval_rag.py does; otherwise it stops, because the recorded corpus checksum would be wrong.
- bm25_offline_guidelines_only: the pure-Python BM25 fallback in rag/bm25.py over the 60 SYNTHETIC guideline
  sections only. For machines without the index or the models. The regulatory questions cannot be answered from
  these hits; the generation evaluation excludes them in this mode and says so.
- auto: hybrid_rerank when the index and models load, otherwise the BM25 fallback (the reason is recorded).

The default is hybrid_rerank and not auto on purpose: the committed hits come from hybrid_rerank, and replacing them
with fallback hits must be an explicit choice. An existing manifest with a different mode is never overwritten
without --force. No LLM is called.
"""

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from uwcopilot.common.files import sha256_of_file
from uwcopilot.governance.provenance import display_path, load_average, package_versions
from uwcopilot.rag.bm25 import BM25Params, GuidelineBM25Retriever
from uwcopilot.rag.corpus import Corpus, load_corpus
from uwcopilot.rag.eval_set import EvalQuestion, gold_first_ranks, validate_gold
from uwcopilot.rag.frozen_hits import (
    FROZEN_HITS_FILE,
    FROZEN_MANIFEST_FILE,
    MODE_BM25_GUIDELINES_ONLY,
    MODE_HYBRID_RERANK,
    QUESTION_FILES,
    FrozenHitsRecord,
    HitLocation,
    frozen_hits_line,
    load_eval_questions,
    needs_regulatory,
)
from uwcopilot.rag.index import describe_qdrant, index_matches_corpus, read_all_payloads
from uwcopilot.rag.retriever import Retriever, to_guideline_hits

DEFAULT_TOP_K = 5
CONTEXT_DEPTHS: tuple[int, ...] = (1, 3, 5)
MODE_CHOICES: tuple[str, ...] = (MODE_HYBRID_RERANK, MODE_BM25_GUIDELINES_ONLY, "auto")


class FreezeError(RuntimeError):
    """The hits cannot be frozen honestly (index out of date, gold labels broken, mode change without --force)."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=MODE_CHOICES, default=MODE_HYBRID_RERANK, help="retrieval mode")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="hits stored per question (default 5)")
    parser.add_argument("--threads", type=int, default=2, help="ONNX threads for the embedding models")
    parser.add_argument("--output", type=Path, default=FROZEN_HITS_FILE, help="frozen hits JSONL")
    parser.add_argument("--manifest", type=Path, default=FROZEN_MANIFEST_FILE, help="manifest JSON")
    parser.add_argument(
        "--questions", type=Path, nargs="+", default=list(QUESTION_FILES), help="question files (JSONL), in order"
    )
    parser.add_argument("--force", action="store_true", help="allow replacing hits made with a different mode")
    return parser.parse_args(argv)


def hybrid_records(
    questions: Sequence[tuple[EvalQuestion, Path]], top_k: int, threads: int
) -> tuple[list[FrozenHitsRecord], dict[str, Any]]:
    """Search every question with hybrid_rerank and return the records plus a description of the retriever.

    Raises FreezeError when the index does not match the current source files or a gold reference resolves to
    no indexed chunk. Raises the retriever's own errors (IndexNotBuiltError, model loading errors) unchanged.
    """
    from uwcopilot.rag.search import build_retriever  # imported here: loads fastembed / qdrant only in this mode
    from uwcopilot.rag.settings import load_rag_settings

    settings = load_rag_settings()
    retriever: Retriever = build_retriever(settings, threads=threads)
    try:
        payloads = read_all_payloads(retriever.client, retriever.collection)
        corpus = load_corpus(count_tokens=retriever.dense.count_tokens)
        differences = index_matches_corpus(payloads, corpus.chunks)
        if differences:
            listed = "; ".join(differences[:10])
            raise FreezeError(f"the index does not match the current source files; run `make index`: {listed}")
        problems = validate_gold([question for question, _ in questions], payloads)
        if problems:
            raise FreezeError("gold references that match no indexed chunk: " + "; ".join(problems[:10]))
        records: list[FrozenHitsRecord] = []
        started = time.perf_counter()
        for question, path in questions:
            results = retriever.search(question.question, mode=MODE_HYBRID_RERANK, top_k=top_k)
            hits = to_guideline_hits(results)
            locations = [
                HitLocation(
                    doc_kind=result.payload["doc_kind"],
                    pages=list(result.payload.get("pages", [])),
                    paragraphs=list(result.payload.get("paragraphs", [])),
                )
                for result in results
            ]
            gold_ranks = gold_first_ranks(question, [result.payload for result in results]) if question.gold else []
            records.append(make_record(question, path, MODE_HYBRID_RERANK, top_k, hits, locations, gold_ranks))
        seconds = round(time.perf_counter() - started, 1)
        description = {
            "corpus": corpus_block(corpus),
            "index": {
                "points": len(payloads),
                "matches_current_source_files": True,
                "qdrant": describe_qdrant(retriever.client, settings),
            },
            "models": {
                "dense": {
                    "id": getattr(retriever.dense, "model_id", None),
                    "query_prefix": getattr(retriever.dense, "query_prefix", None),
                },
                "sparse": {"id": getattr(retriever.sparse, "model_id", None)},
                "reranker": {"id": getattr(retriever.reranker, "model_id", None)},
            },
            "retrieval_params": vars(retriever.params) | {"mode": MODE_HYBRID_RERANK, "top_k": top_k},
            "search_seconds": seconds,
        }
    finally:
        retriever.client.close()
    return records, description


def bm25_records(
    questions: Sequence[tuple[EvalQuestion, Path]], top_k: int
) -> tuple[list[FrozenHitsRecord], dict[str, Any]]:
    """Search every question with the offline BM25 fallback over guideline sections only."""
    retriever = GuidelineBM25Retriever(params=BM25Params())
    records: list[FrozenHitsRecord] = []
    for question, path in questions:
        hits = retriever.search(question.question, top_k=top_k)
        locations = [HitLocation(doc_kind="guideline") for _ in hits]
        payloads = [{"chunk_id": hit.section_id, "doc_id": hit.source_doc, "text": hit.text} for hit in hits]
        gold_ranks = gold_first_ranks(question, payloads) if question.gold else []
        records.append(make_record(question, path, MODE_BM25_GUIDELINES_ONLY, top_k, hits, locations, gold_ranks))
    corpus = load_corpus(include_regulatory=False)
    description = {
        "corpus": corpus_block(corpus),
        "index": {"points": len(retriever.chunks), "kind": "in-memory BM25 over whole guideline sections"},
        "models": {"bm25": {"k1": retriever.index.params.k1, "b": retriever.index.params.b}},
        "retrieval_params": {"mode": MODE_BM25_GUIDELINES_ONLY, "top_k": top_k},
    }
    return records, description


def make_record(
    question: EvalQuestion,
    path: Path,
    mode: str,
    top_k: int,
    hits: list,
    locations: list[HitLocation],
    gold_ranks: list[int | None],
) -> FrozenHitsRecord:
    """Build and validate one frozen record."""
    return FrozenHitsRecord(
        question_id=question.id,
        question=question.question,
        category=question.category,
        question_file=display_path(path),
        retrieval_mode=mode,
        top_k=top_k,
        hits=hits,
        hit_locations=locations,
        gold_ranks=gold_ranks,
    )


def corpus_block(corpus: Corpus) -> dict[str, Any]:
    """Checksum and source files of the corpus the hits were retrieved from."""
    return {
        "checksum_sha256": corpus.checksum(),
        "source_files": [{"path": item.path, "doc_id": item.doc_id, "sha256": item.sha256} for item in corpus.files],
        "missing_regulatory_files": list(corpus.missing_regulatory),
    }


def gold_in_top(records: Sequence[FrozenHitsRecord]) -> dict[str, Any]:
    """For answerable questions: how many have every gold reference within the first 1, 3 and 5 hits."""
    answerable = [record for record in records if record.answerable]
    counts: dict[str, Any] = {"n_answerable": len(answerable)}
    for depth in CONTEXT_DEPTHS:
        counts[f"all_gold_in_top_{depth}"] = sum(1 for record in answerable if record.gold_in_context(depth))
    return counts


def build_manifest(
    records: Sequence[FrozenHitsRecord],
    questions: Sequence[tuple[EvalQuestion, Path]],
    mode: str,
    reason: str,
    description: dict[str, Any],
    hits_path: Path,
    load_start: list[float] | None,
) -> dict[str, Any]:
    """The manifest JSON (see the module docstring)."""
    question_files: list[dict[str, Any]] = []
    counts_by_file = Counter(display_path(path) for _, path in questions)
    for path in dict.fromkeys(path for _, path in questions):
        shown = display_path(path)
        question_files.append({"path": shown, "sha256": sha256_of_file(path), "n": counts_by_file[shown]})
    regulatory_ids = [question.id for question, _ in questions if needs_regulatory(question)]
    guideline_only = load_corpus(include_regulatory=False).checksum()
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "generated_by": "scripts/freeze_retrieval.py",
        "retrieval_mode": mode,
        "reason": reason,
        "top_k": records[0].top_k if records else None,
        "corpus_checksum": description["corpus"]["checksum_sha256"],
        "guideline_only_corpus_checksum": guideline_only,
        "corpus": description["corpus"],
        "index": description["index"],
        "models": description["models"],
        "retrieval_params": description["retrieval_params"],
        "question_files": question_files,
        "n_questions": len(records),
        "n_by_category": dict(sorted(Counter(record.category for record in records).items())),
        "questions_needing_regulatory_documents": {"n": len(regulatory_ids), "ids": regulatory_ids},
        "gold_in_top_k": gold_in_top(records),
        "frozen_hits_path": display_path(hits_path),
        "frozen_hits_sha256": sha256_of_file(hits_path),
        "environment": {
            "packages": package_versions(("fastembed", "qdrant-client", "onnxruntime", "pydantic")),
            "load_average_start": load_start,
            "load_average_end": load_average(),
        },
        "note": (
            "No LLM was called. The hits are retrieval output over SYNTHETIC guidelines and the public regulatory "
            "documents; the generation evaluation reads only this file."
        ),
    }


def check_mode_change(manifest_path: Path, mode: str, force: bool) -> None:
    """Refuse to replace hits made with another mode unless --force was given."""
    if not manifest_path.exists() or force:
        return
    previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("retrieval_mode")
    if previous and previous != mode:
        raise FreezeError(
            f"{display_path(manifest_path)} records mode {previous!r}; refusing to overwrite it with {mode!r} "
            "hits. Pass --force if that is really intended."
        )


def run(args: argparse.Namespace) -> tuple[list[FrozenHitsRecord], dict[str, Any]]:
    """Freeze the hits for the chosen mode and return (records, manifest); writes both files."""
    load_start = load_average()
    questions = load_eval_questions(args.questions)
    mode = args.mode
    reason = f"requested: --mode {args.mode}"
    records: list[FrozenHitsRecord]
    if mode in (MODE_HYBRID_RERANK, "auto"):
        try:
            records, description = hybrid_records(questions, args.top_k, args.threads)
            mode = MODE_HYBRID_RERANK
            if args.mode == "auto":
                reason = "auto: the Qdrant index and the fastembed models loaded"
        except FreezeError:
            raise
        except Exception as error:  # noqa: BLE001 - any loading failure means: no hybrid retrieval on this machine
            if args.mode != "auto":
                raise
            mode = MODE_BM25_GUIDELINES_ONLY
            reason = f"auto: hybrid_rerank unavailable ({type(error).__name__}: {str(error)[:200]})"
            records, description = bm25_records(questions, args.top_k)
    else:
        records, description = bm25_records(questions, args.top_k)
    check_mode_change(args.manifest, mode, args.force)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lines = [frozen_hits_line(record) for record in records]
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = build_manifest(records, questions, mode, reason, description, args.output, load_start)
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return records, manifest


def main(argv: list[str] | None = None) -> int:
    """Run the freeze and print a short summary. Returns a process exit code."""
    args = parse_args(argv)
    try:
        records, manifest = run(args)
    except FreezeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Mode: {manifest['retrieval_mode']} ({manifest['reason']})")
    print(f"Questions: {manifest['n_questions']} {manifest['n_by_category']}")
    print(f"Corpus checksum: {manifest['corpus_checksum']}")
    print(f"Gold in top k: {manifest['gold_in_top_k']}")
    print(f"Wrote {display_path(args.output)} (sha256 {manifest['frozen_hits_sha256']})")
    print(f"Wrote {display_path(args.manifest)}")
    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main())

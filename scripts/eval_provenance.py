"""Evaluate numeric provenance (SIG part 1, no LLM) and write results/provenance_metrics.json.

Usage: uv run python scripts/eval_provenance.py

Measures, on the committed frozen inputs in eval/provenance/:
- the free-form numeric checker's recall per mutation type and its false-flag rate on controls;
- the incumbent agent claim checker on the same mutations (baseline);
- the memo grammar validator on the hand-written adversarial set.
Before scoring it checks that regenerating the mutations from the reference sentences with the recorded seed gives
exactly the committed file, so the seed in the results is verified, not just stated.

LLM-dependent SIG metrics (memo drafts, repair and fallback rates, RAG answers, KYC memos) are written as
"not measured" with the reason; they come from later runs.
"""

import argparse
import json
from pathlib import Path

from uwcopilot.common.files import sha256_of_file
from uwcopilot.common.paths import REPO_ROOT, RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance
from uwcopilot.provenance.evaluation import (
    AdversarialRow,
    incumbent_flags,
    mutation_report,
    score_by_type,
    sig_checker_flags,
    sig_checker_unverifiable,
    validator_report,
)
from uwcopilot.provenance.mutations import Mutation, build_mutations
from uwcopilot.provenance.reference import ReferenceSentence, section_texts

EVAL_DIR = REPO_ROOT / "eval" / "provenance"
REFERENCES = EVAL_DIR / "reference_sentences.jsonl"
MUTATIONS = EVAL_DIR / "mutations.jsonl"
ADVERSARIAL = EVAL_DIR / "validator_adversarial.jsonl"
MUTATION_SEED = 42
PENDING = "not measured: needs the Phase 4 evidence ledger and LLM runs (pending)"
DEVELOPMENT_NOTE = (
    "The checker was debugged on these reference sentences during development (sentence splitting around "
    "citations, soft line wraps, table header binding). The fixes were general, but the mutation set is not a "
    "held-out test, and the same author wrote the guidelines, the reference-sentence rules and the mutations."
)
MUTATION_SET_NOTE = (
    "Regenerated with scripts/make_mutations.py (seed 42) on 2026-10-13: the number normalizer now reads duration "
    "qualifiers ('12 consecutive months' is 12 months, not a count of 12), which changes the units of some section "
    "numbers and so the seeded draws. The reference sentences did not change. The checker fixes made at the same time "
    "(duration qualifiers, comparator ownership, table header binding) came from answer sentences found in the S1b "
    "review, and the later ones (coordinated values share a comparator, fraction words before a scale word) from a "
    "code review of those fixes; none came from this mutation set. Results written before 2026-10-13 were measured on "
    "the previous set (sha256 480af16a17cbfa4cd8fecca8fd6e9003cdb55175fcd5ae7d073a09ad3889e1d1, 791 mutations), so a "
    "before/after comparison across that date mixes a set change with checker changes. A checker-only comparison on "
    "one set is not measured by any committed script."
)


def read_jsonl(path: Path) -> list[dict]:
    """Every non-empty line of a JSON-lines file."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_inputs() -> tuple[list[ReferenceSentence], list[Mutation], list[AdversarialRow], dict]:
    """The frozen reference sentences, mutations and adversarial rows (plus the adversarial context)."""
    references = [ReferenceSentence(**row) for row in read_jsonl(REFERENCES)]
    mutations = [Mutation(**row) for row in read_jsonl(MUTATIONS)]
    adversarial = read_jsonl(ADVERSARIAL)
    context = adversarial[0]["_meta"]["context"]
    rows = [
        AdversarialRow(row["kind"], row["text"], row.get("expected_code"), row.get("required_refs", []))
        for row in adversarial[1:]
    ]
    return references, mutations, rows, context


def comparator_unverifiable_share(mutations: list[Mutation], sections: dict[str, str]) -> dict:
    """How many comparator flips land on a value the cited section pairs with several comparator classes."""
    flips = [mutation for mutation in mutations if mutation.mutation_type == "comparator_flip"]
    unverifiable = sum(sig_checker_unverifiable(mutation, sections) for mutation in flips)
    return {"n_comparator_flips": len(flips), "unverifiable": unverifiable}


def build_report(run: RunProvenance) -> dict:
    """Run every measurement and assemble the results JSON."""
    references, mutations, adversarial_rows, context = load_inputs()
    sections = section_texts()
    regenerated = build_mutations(references, sections, seed=MUTATION_SEED)
    regenerated_matches = [m.to_json() for m in regenerated] == [m.to_json() for m in mutations]
    sig = mutation_report(score_by_type(mutations, sections, sig_checker_flags))
    incumbent = mutation_report(score_by_type(mutations, sections, incumbent_flags))
    mutation_test = {
        "n_reference_sentences": len(references),
        "n_mutations": sum(1 for m in mutations if m.expect_flag),
        "n_controls": sum(1 for m in mutations if not m.expect_flag),
        "seed": MUTATION_SEED,
        "regenerated_from_seed_matches_file": regenerated_matches,
        **sig,
        "comparator_unverifiable": comparator_unverifiable_share(mutations, sections),
        "known_blind_spot": "wrong_row: a value from another row of the same cited table looks traceable",
        "development_note": DEVELOPMENT_NOTE,
        "mutation_set_note": MUTATION_SET_NOTE,
    }
    return {
        "data_label": "SYNTHETIC guidelines (corpus/guidelines); no LLM call was made by this script",
        "numeric_checker": {
            "mutation_test": mutation_test,
            "incumbent_claim_checker": {
                "description": "uwcopilot.agent.claim_checker: a number is supported when some number in the cited "
                "section rounds to it at the written precision; no units, no comparators",
                **incumbent,
            },
            "on_rag_answers": PENDING,
        },
        "validator_adversarial": {
            "file": "eval/provenance/validator_adversarial.jsonl",
            "file_sha256": sha256_of_file(ADVERSARIAL),
            "authorship": "hand-written by the same author as the validator (a SEEN set), frozen before scoring",
            **validator_report(adversarial_rows, context),
        },
        "memo": PENDING,
        "kyc_memo": PENDING,
        "provenance": run.finish(eval_sets=[REFERENCES, MUTATIONS, ADVERSARIAL]),
    }


def main() -> None:
    """Write results/provenance_metrics.json and print the headline numbers."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "provenance_metrics.json")
    args = parser.parse_args()
    run = RunProvenance.start("scripts/eval_provenance.py")
    report = build_report(run)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    test = report["numeric_checker"]["mutation_test"]
    print(f"wrote {args.out}")
    for kind, row in test["recall_by_type"].items():
        print(f"  checker recall {kind:16s} {row['detected']}/{row['n']} = {row['rate']} {row['wilson_ci95']}")
    for kind, row in test["false_flag_rate"].items():
        print(f"  false flags    {kind:16s} {row['flagged']}/{row['n']} = {row['rate']} {row['wilson_ci95']}")
    validator = report["validator_adversarial"]
    print(f"  validator recall {validator['recall_overall']}, false rejects {validator['false_reject_rate']}")


if __name__ == "__main__":
    main()

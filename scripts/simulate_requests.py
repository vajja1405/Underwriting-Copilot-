"""Evidence-request simulator (innovation S5): VOI vs checklist vs random vs oracle on SYNTHETIC JSON-only cases.

Usage (the `make simulate-requests` target):
    uv run python scripts/simulate_requests.py
    uv run python scripts/simulate_requests.py --split dev --no-write    # development runs (dev split only)

What it does:
1. builds 300 JSON-only cases per line (credit, insurance) with the Phase 2 generator: build_packet(draw=False),
   the code path of `scripts/make_packets.py --json-only`, master seed 4242, packet ids PKT-C-001..300 and
   PKT-I-001..300. The test split (200 per line) is the generator's own seeded split (generate.test_split); the
   other 100 per line are the development split. Cases are built in memory; nothing is written under data/packets.
   The seed differs from the Phase 2 evaluation packets (seed 42) and the engine's test packets (seed 4040), so the
   same packet id names a different case here.
2. runs every policy of evidence/simulator.py (voi, voi_lookahead, checklist, random, oracle) on every case, under
   every priors variant (documented, latent_x0.5, latent_x1.5, latent_removed, empirical_dev,
   empirical_dev_smoothed, oracle_shares; the oracle policy under documented only). The three table variants take
   their priors from latent shares measured BEFORE any case is simulated and written into the design block:
   empirical_dev = the development split's shares, unsmoothed (an option never latent there gets no prior, which
   removes the true option of some test problems: diagnostics.latent_without_prior); empirical_dev_smoothed = the
   same counts with add-one smoothing (no option removed); oracle_shares = each evaluated group's own shares (an
   in-sample oracle reference that never removes a true option; no real underwriter has these priors);
3. writes the "simulator" section (see "Results files" below) and one JSON line per run to
   data/processed/evidence/simulator_runs.jsonl (gitignored). The section also holds "diagnostics"
   (evidence/simulator_diagnostics.py): the latent option vs the documented prior per problem family, the coupled
   reveals of insurance problems that also rewrite the stated claim history, the planted problems per case, and
   the amount-driven cpa_reviewed_financials cases reported apart.

READ THE DIAGNOSTICS BEFORE THE RESULTS. The generator does NOT draw each problem's latent (true) resolution from the
template priors: it takes the option the cause implies or the option nearest the hidden value (the note at the top
of docintel/synthetic/credit_records.py), so for several families the latent option is the same in every case. The
"documented" priors are therefore not the distribution the truths come from, and the x0.5 / x1.5 variants scale the
prior of the generator's latent option, not of a draw from the priors.

HELD-OUT PROBLEM FAMILY: claims_history_mismatch (insurance). It was chosen and written down on 2026-10-14, BEFORE
any simulation was run, following the example in the Phase 4 spec. Every case whose truth contains it is held out
of all development: `--split dev` never simulates it, and the final run reports the test-split cases that contain it
separately ("held_out_family"), never mixed into the main test metrics. Development-split cases that contain it are
never simulated at all. Disclosure: the engine's detection of this problem (evidence/open_items_insurance.py
claims_history_items) was written and unit-tested by the engine build, on other generated packets (seed 4040),
before the simulator existed; the hold-out covers the simulator's development and every simulation result.

Development used the development split only (`--split dev --no-write`). The main results are the test split without
the held-out family. Intervals: percentile bootstrap over cases, 1,000 resamples, seed 42; the headline is the
paired VOI-minus-checklist difference on the same cases.

Results files (two copies of one payload, so the packet evaluation can never overwrite this section):
- results/evidence_metrics_simulator.json: written only by this script, with section "simulator";
- results/evidence_metrics.json: the same section merged in with uwcopilot.risk.results.update_results, which holds
  an exclusive lock on results/evidence_metrics.json.lock while it reads the file, replaces ONLY section
  "simulator" and writes it back (atomic rename). scripts/eval_evidence.py writes section "packets"; if it rewrites
  the whole file instead of using update_results, it must copy the "simulator" section of
  evidence_metrics_simulator.json. The two copies hold the same results; each metadata block is stamped when its
  file is written.
Writing to the default results files needs the default design (seed, case counts, both lines, all cases); other
designs must pass --no-write or other --results / --standalone paths.

Everything here is SYNTHETIC. Extraction is an oracle (the generator's perfect extraction), so no number here says
anything about extraction accuracy. Priors and cost weights are planning assumptions written by the project author.
Not claimable: real time or cost savings, or that the VOI score is optimal or an economic model.
"""

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

from uwcopilot.common.paths import DATA_DIR, RESULTS_DIR
from uwcopilot.docintel.synthetic import build_packet
from uwcopilot.docintel.synthetic.common import packet_seed
from uwcopilot.docintel.synthetic.generate import packet_ids, test_split
from uwcopilot.docintel.truth import GENERATOR_VERSION, PacketTruth, truth_sha256
from uwcopilot.evidence.requests import catalog_version, templates_version
from uwcopilot.evidence.simulator import run_context, simulate
from uwcopilot.evidence.simulator_cases import prepare_case
from uwcopilot.evidence.simulator_diagnostics import (
    case_row,
    coupling_summary,
    is_structural_cpa_case,
    latent_shares,
    latent_vs_prior,
    latent_without_prior,
    planted_problem_counts,
    smoothed_shares,
)
from uwcopilot.evidence.simulator_models import (
    MAX_REQUESTS,
    ORACLE_MAX_OPEN_REQUESTS,
    ORACLE_MAX_POSITIONS,
    POLICIES,
    PRIORS_VARIANTS,
    EmpiricalPriors,
    PriorTables,
    SimulationResult,
)
from uwcopilot.evidence.simulator_summary import summarize
from uwcopilot.evidence.voi import SCORE_NAME
from uwcopilot.governance.provenance import RunProvenance
from uwcopilot.risk.results import update_results

SEED = 4242
CASES_PER_LINE = 300
TEST_PER_LINE = 200
LINES: tuple[str, ...] = ("credit", "insurance")
HELD_OUT_FAMILY = "claims_history_mismatch"
HELD_OUT_CHOSEN = "2026-10-14, before any simulation was run (the Phase 4 spec's example)"
RESULTS_FILE = RESULTS_DIR / "evidence_metrics.json"
STANDALONE_FILE = RESULTS_DIR / "evidence_metrics_simulator.json"
SECTION = "simulator"
RUNS_FILE = DATA_DIR / "processed" / "evidence" / "simulator_runs.jsonl"
GROUPS: tuple[str, ...] = ("test_main", "held_out_family", "development")
HEADLINE_METRICS: tuple[str, ...] = (
    "cost_weighted_requests",
    "n_requests",
    "n_zero_value_requests",
    "decision_error",
    "requests_to_stable",
)
POLICY_DESCRIPTIONS: dict[str, str] = {
    "voi": "highest nonzero VOI first; stop when no open request has a VOI above zero (innovation S5 rule)",
    "voi_lookahead": "voi, then the zero-VOI request with the highest nonzero lookahead value (an addition)",
    "checklist": "every open request in the fixed severity order of evidence.open_items.PROBLEM_ORDER (the catalog "
    "has no severity field)",
    "random": "every open request in a seeded shuffled order",
    "oracle": "knows the truth: cheapest cost-weighted request sequence that reaches the full-information "
    f"recommendation (exhaustive uniform-cost search over request orders; only cases with at most "
    f"{ORACLE_MAX_OPEN_REQUESTS} open requests at the start)",
}
VARIANT_DESCRIPTIONS: dict[str, str] = {
    "documented": "template priors of config/resolution_templates.yaml (NOT the distribution the latent truths come "
    "from: see diagnostics.latent_vs_prior)",
    "latent_x0.5": "prior of the generator's latent option (the option the cause implies or nearest the hidden value; "
    "not drawn from the priors) x0.5, renormalized",
    "latent_x1.5": "prior of the generator's latent option x1.5, renormalized; where the documented prior of that "
    "option is below its latent share, this moves the priors TOWARD the truths",
    "latent_removed": "the generator's latent option removed from the option set, renormalized",
    "empirical_dev": "development-split latent shares, UNSMOOTHED: every option's prior is the share of "
    "development-split problems of its resolution template whose latent option it was (design.empirical_dev_priors). "
    "Options never latent there (some templates have only 4 development problems) get no prior and leave the set, "
    "which removes the TRUE option of some test problems (diagnostics.latent_without_prior.empirical_dev): for those "
    "problems this variant acts like latent_removed. NOT a correct-prior reference. A template without development "
    "problems keeps the documented priors",
    "empirical_dev_smoothed": "development-split latent counts with add-one (Laplace) smoothing: (count + 1) / "
    "(development problems of the template + its number of options) (design.empirical_dev_smoothed_priors); every "
    "option keeps a prior above zero, so none leaves the set. An estimate a practitioner could make from past cases",
    "oracle_shares": "in-sample ORACLE reference: priors equal to the latent shares of the evaluated group itself "
    "(test_main, held_out_family or development; design.oracle_shares_priors). Options never latent in the group "
    "leave the set, but the true option of every problem in the group keeps a prior above zero. No real underwriter "
    "has these priors: the row shows what the VOI policy does when its template-level priors match the truths",
}
CASE_CAP_NOTE = (
    "innovations.md S5 caps each case at 4 open items with 3 resolutions each. The resolution templates comply (2 or "
    "3 options each), but the cases are NOT capped: the generator plants every problem its seeded draw gives, so "
    "some cases carry 5 planted problems and start with 5 open requests (diagnostics.planted_problems and the "
    "open_requests_at_start counts). Those cases are simulated like the others; only the oracle is not computed on "
    f"them (it brute-forces request orders only up to {ORACLE_MAX_OPEN_REQUESTS} open requests)."
)
READING_NOTES: tuple[str, ...] = (
    "The voi policy only makes requests whose VOI is above zero, so its zero-value request count is 0 by "
    "construction; the comparison is how many zero-value requests the other policies make.",
    "Scaling the latent option's prior by 0.5 or 1.5 keeps every prior above zero, so it cannot change WHICH "
    "requests have a VOI above zero, only their values and order; for the voi policy, differences between the "
    "documented, x0.5 and x1.5 rows come only from the order of several nonzero requests. Removing the latent "
    "option can change which requests have value. voi_lookahead also depends on which option is most likely.",
    "Checklist, random and oracle make the same requests under every priors variant; only the VOI they are valued "
    "at, and so the zero-value count, can change.",
    "The full-information recommendation reveals every planted problem. A planted problem that no open item "
    "stands for cannot be requested by any policy; decision_error_rate_when_every_planted_problem_detected leaves "
    "those cases out.",
    "requests_to_stable_when_reached averages only the cases where the run ends on the full-information "
    "recommendation, so its case set differs between policies; the paired comparison uses the cases both reached.",
    "The oracle stops at the first state whose recommendation equals the full-information recommendation. That "
    "state need not be the full-information state: when a planted problem is undetected, another open problem can "
    "give the same recommendation, so 'oracle reached' does not mean every problem was revealed.",
    "The full-information recommendation is what the SYNTHETIC reference policy (agent/policy.py) gives once every "
    "planted problem is revealed; it is the benchmark here, not a claim about the right lending or underwriting "
    "decision.",
    "Latent truths do not follow the documented priors (diagnostics.latent_vs_prior): the generator takes the "
    "option the cause implies or the nearest one, so for several families one option is latent in every case and "
    "another never. The 'documented' rows are therefore not a correct-prior baseline, and the x0.5 / x1.5 rows "
    "measure robustness around priors that are already wrong (x1.5 can move a family toward its truth). Three table "
    "variants replace the priors with measured latent shares, so, unlike x0.5 / x1.5, they CAN change which "
    "requests have a VOI above zero. empirical_dev uses the development split's shares UNSMOOTHED: an option never "
    "latent on a small development sample gets no prior, so for some test problems the true option is removed "
    "(diagnostics.latent_without_prior counts them); it is an estimate with that defect, not a correct-prior "
    "reference. empirical_dev_smoothed is the same estimate with add-one smoothing (no option removed). "
    "oracle_shares is the correct-prior reference: the evaluated group's own shares, which no real underwriter has "
    "and which never remove a problem's true option.",
    CASE_CAP_NOTE,
    "A reveal applies the generator's latent substitutions, which are not always the template option the engine "
    "values: insurance problems that change the claims of the latent world also set the stated claim count and "
    "total incurred to the true history (diagnostics.coupling). Revealing such a problem can therefore change the "
    "recommendation although the engine's own option for it did not (a VOI of 0 followed by a change), and in the "
    "held-out family it can remove the claims-history finding without the claims-history request; the cases where "
    "one coupled reveal alone does so are listed in diagnostics.coupling.held_out_family.",
    "The held-out family's cases carry more co-planted problems than the main test split "
    "(diagnostics.planted_problems): part of its larger VOI decision error is the myopia of a one-step score on "
    "several co-occurring problems, not a family-specific weakness. decision_errors_by_kind splits every policy's "
    "errors into undetected planted problems, voi stops with every open request at VOI 0, and other.",
    "Above $500,000 every credit case carries the amount-driven missing cpa_reviewed_financials document (latent "
    "always provided_consistent); diagnostics.structural_cpa reports those cases apart from the rest of the credit "
    "test split.",
)
NOT_CLAIMABLE: tuple[str, ...] = (
    "real time or cost savings (cost weights and priors are planning assumptions)",
    "that the VOI score is optimal or an economic model",
    "extraction accuracy (the simulator uses the generator's oracle extraction)",
    "anything about real applications: every case is SYNTHETIC",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line arguments."""
    parser = argparse.ArgumentParser(description="Simulate evidence-request policies on SYNTHETIC cases (S5).")
    parser.add_argument("--seed", type=int, default=SEED, help="master seed of the case generator")
    parser.add_argument("--cases-per-line", type=int, default=CASES_PER_LINE, help="cases per line")
    parser.add_argument("--test-per-line", type=int, default=TEST_PER_LINE, help="test-split cases per line")
    parser.add_argument("--lines", nargs="+", choices=LINES, default=list(LINES), help="lines to simulate")
    parser.add_argument("--split", choices=("dev", "test", "all"), default="all", help="which split to simulate")
    parser.add_argument("--limit", type=int, default=None, help="at most this many cases per line and split")
    parser.add_argument("--results", type=Path, default=RESULTS_FILE, help="shared results JSON (section merged)")
    parser.add_argument("--standalone", type=Path, default=STANDALONE_FILE, help="this script's own results JSON")
    parser.add_argument("--runs-out", type=Path, default=RUNS_FILE, help="per-run JSONL (gitignored)")
    parser.add_argument("--no-write", action="store_true", help="print the summary; write no file")
    return parser.parse_args(argv)


def write_problem(args: argparse.Namespace) -> str | None:
    """Why this run must not write its results (None when it may)."""
    if args.split != "all" or args.limit is not None:
        return "Partial runs (--split dev/test or --limit) are not written; pass --no-write."
    default_paths = args.results == RESULTS_FILE or args.standalone == STANDALONE_FILE
    default_design = (
        args.seed == SEED
        and args.cases_per_line == CASES_PER_LINE
        and args.test_per_line == TEST_PER_LINE
        and tuple(args.lines) == LINES
    )
    if default_paths and not default_design:
        return "Only the default design writes the default results files; pass --no-write or other paths."
    return None


# --------------------------------------------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------------------------------------------


def has_held_out_family(truth: PacketTruth) -> bool:
    """True when a planted problem belongs to the held-out family."""
    return any(problem.problem_type == HELD_OUT_FAMILY for problem in truth.problems)


def build_cases(line: str, args: argparse.Namespace) -> list[PacketTruth]:
    """The line's JSON-only cases of the requested split (development cases with the held-out family skipped)."""
    ids = packet_ids(line, args.cases_per_line)
    test_ids = test_split(ids, args.test_per_line, args.seed, line)
    counts = {"dev": 0, "test": 0}
    truths = []
    for packet_id in ids:
        split = "test" if packet_id in test_ids else "dev"
        if args.split != "all" and split != args.split:
            continue
        if args.limit is not None and counts[split] >= args.limit:
            continue
        _, truth = build_packet(packet_id, line, packet_seed(args.seed, packet_id), split, draw=False)
        if split == "dev" and has_held_out_family(truth):
            continue
        counts[split] += 1
        truths.append(truth)
    return truths


def case_rng(seed: int, case_id: str) -> random.Random:
    """The random policy's generator for one case (the same order under every priors variant)."""
    digest = hashlib.sha256(f"{seed}|{case_id}|random-policy".encode()).hexdigest()
    return random.Random(int(digest[:8], 16))


def development_truths(args: argparse.Namespace) -> list[PacketTruth]:
    """Every development-split case of the design for the requested lines (ignores --split and --limit), with the
    held-out family skipped as always: the cases the empirical_dev priors are measured on."""
    full = argparse.Namespace(**{**vars(args), "split": "dev", "limit": None})
    return [truth for line in args.lines for truth in build_cases(line, full)]


def group_of(truth: PacketTruth) -> str:
    """The case's reporting group: development, held_out_family (test cases with it) or test_main."""
    if truth.split == "dev":
        return "development"
    return "held_out_family" if has_held_out_family(truth) else "test_main"


def development_tables(development: list[PacketTruth]) -> PriorTables:
    """The priors tables measured on the development split: empirical_dev (latent shares, unsmoothed) and
    empirical_dev_smoothed (add-one smoothing) (simulator_diagnostics)."""
    table = latent_vs_prior(development)
    return {"empirical_dev": latent_shares(table), "empirical_dev_smoothed": smoothed_shares(table)}


def oracle_tables(truths: list[PacketTruth]) -> dict[str, EmpiricalPriors]:
    """Group -> the latent shares of the group's own cases (the oracle_shares priors of its cases)."""
    return {
        group: latent_shares(latent_vs_prior([truth for truth in truths if group_of(truth) == group]))
        for group in GROUPS
    }


def case_tables(truth: PacketTruth, development: PriorTables, oracle: dict[str, EmpiricalPriors]) -> PriorTables:
    """The three priors tables one case is simulated with (oracle_shares: its own group's shares)."""
    return {**development, "oracle_shares": oracle[group_of(truth)]}


def run_case(truth: PacketTruth, seed: int, tables: PriorTables) -> tuple[list[SimulationResult], dict[str, Any]]:
    """Every policy under every priors variant on one case (the oracle under the documented priors only), and the
    case's diagnostics row (simulator_diagnostics.case_row). tables: the case's priors tables (case_tables)."""
    case = prepare_case(truth)
    row = case_row(case)
    try:
        context = run_context(case)
    except ValueError as error:
        reason = f"{type(error).__name__}: the case could not be evaluated"
        print(f"{truth.packet_id}: not computed ({reason})", file=sys.stderr)
        context = None
    results = []
    for variant in PRIORS_VARIANTS:
        for policy in POLICIES:
            if policy == "oracle" and variant != "documented":
                continue
            rng = case_rng(seed, truth.packet_id) if policy == "random" else None
            results.append(simulate(truth, policy, variant, rng, prepared=case, context=context, tables=tables))
    return results, row


# --------------------------------------------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------------------------------------------


def case_set(truths: list[PacketTruth]) -> dict[str, Any]:
    """Counts and SHA-256 (over the sorted truth hashes) of the simulated cases per line and split."""
    entries: dict[str, Any] = {}
    for line in LINES:
        for split in ("dev", "test"):
            chosen = [truth for truth in truths if truth.line == line and truth.split == split]
            if not chosen:
                continue
            hashes = sorted(truth_sha256(truth) for truth in chosen)
            entries[f"{line}_{split}"] = {
                "n_cases": len(chosen),
                "n_with_held_out_family": sum(1 for truth in chosen if has_held_out_family(truth)),
                "truth_sha256": hashlib.sha256("\n".join(hashes).encode()).hexdigest(),
            }
    return entries


def group_summaries(results: list[SimulationResult]) -> dict[str, Any]:
    """Summaries per line and for all lines together."""
    summaries: dict[str, Any] = {}
    for line in (*LINES, "all"):
        chosen = [result for result in results if line == "all" or result.line == line]
        if chosen:
            summaries[line] = summarize(chosen, POLICIES, PRIORS_VARIANTS)
    return summaries


def design(
    args: argparse.Namespace, development: PriorTables, oracle: dict[str, EmpiricalPriors], n_development: int
) -> dict[str, Any]:
    """The fixed design of the run (written before the results)."""
    return {
        "seed": args.seed,
        "cases_per_line": args.cases_per_line,
        "test_cases_per_line": args.test_per_line,
        "development_cases_per_line": args.cases_per_line - args.test_per_line,
        "lines": list(args.lines),
        "generator": f"docintel.synthetic.build_packet(draw=False) = make_packets.py --json-only; {GENERATOR_VERSION}",
        "extraction": "oracle: truth.oracle_extraction through the real checks and contradiction builder, with the "
        "oracle page text scanned for embedded instructions (no OCR, no PDF)",
        "held_out_family": {
            "problem_type": HELD_OUT_FAMILY,
            "chosen": HELD_OUT_CHOSEN,
            "rule": "cases containing it were never simulated during development (development-split cases with it "
            "are never simulated); test-split cases with it are reported only under held_out_family",
            "disclosure": "the engine's detection of this problem (open_items_insurance.claims_history_items) was "
            "written and unit-tested by the engine build on other generated packets (seed 4040) before the "
            "simulator existed; the hold-out covers simulator development and every simulation result",
        },
        "score_name": SCORE_NAME,
        "policies": POLICY_DESCRIPTIONS,
        "headline_policy": "voi (the specified S5 stopping rule); voi_lookahead is reported as an addition",
        "priors_variants": VARIANT_DESCRIPTIONS,
        "empirical_dev_priors": {
            "what": "latent share per resolution template and option on the development split (the empirical_dev "
            "variant's priors), measured before any test case was simulated",
            "development_cases": n_development,
            "smoothing": "none: an option never latent on the development split gets no prior and leaves the set",
            "shares": development["empirical_dev"],
        },
        "empirical_dev_smoothed_priors": {
            "what": "the empirical_dev_smoothed variant's priors, from the same development-split counts",
            "development_cases": n_development,
            "smoothing": "add-one: (latent count + 1) / (development problems of the template + its options)",
            "shares": development["empirical_dev_smoothed"],
        },
        "oracle_shares_priors": {
            "what": "the oracle_shares variant's priors: the latent shares of each reporting group's own cases (an "
            "in-sample oracle reference, not available to a real underwriter)",
            "shares_by_group": oracle,
        },
        "case_cap": CASE_CAP_NOTE,
        "max_requests_per_run": MAX_REQUESTS,
        "oracle_search_guard_positions": ORACLE_MAX_POSITIONS,
        "metrics": {
            "requests_per_case": "number of requests the policy made",
            "cost_weighted_requests_per_case": "sum of the catalog cost weights of those requests",
            "zero_value_requests_per_case": "requests whose VOI was 0 just before (under the variant's priors) and "
            "whose reveal did not change the recommendation",
            "decision_error_rate": "share of cases whose final recommendation differs from the full-information "
            "recommendation (every planted problem revealed)",
            "requests_to_stable_when_reached": "requests after which the recommendation equals the "
            "full-information recommendation and stays; cases ending on another recommendation are left out",
        },
        "reading_notes": list(READING_NOTES),
        "bootstrap": {"n_resamples": 1000, "seed": 42, "method": "percentile over cases"},
        "evidence_request_catalog": catalog_version(),
        "resolution_templates": templates_version(),
    }


def diagnostics(
    results: list[SimulationResult],
    truths: list[PacketTruth],
    rows: list[dict[str, Any]],
    held_out: set[str],
    tables: tuple[PriorTables, dict[str, EmpiricalPriors]],
) -> dict[str, Any]:
    """The case diagnostics per group (simulator_diagnostics.py), and the structural CPA cases reported apart.

    tables: (the development-split tables, the oracle_shares table per group), for latent_without_prior.
    """
    development, oracle = tables
    groups = {
        "test_main": lambda case_id, split: split == "test" and case_id not in held_out,
        "held_out_family": lambda case_id, split: split == "test" and case_id in held_out,
        "development": lambda case_id, split: split == "dev",
    }
    section: dict[str, Any] = {"latent_vs_prior": {}, "coupling": {}, "planted_problems": {}}
    section["latent_without_prior"] = {
        "note": "planted problems whose TRUE option a table variant's priors leave out (the variant then acts like "
        "latent_removed for that problem), per group; counted whether or not an open item stands for the problem",
        "empirical_dev": {},
        "empirical_dev_smoothed": {},
        "oracle_shares": {},
    }
    for name, chosen in groups.items():
        group_truths = [truth for truth in truths if chosen(truth.packet_id, truth.split)]
        group_rows = [row for row in rows if chosen(row["case_id"], row["split"])]
        section["latent_vs_prior"][name] = latent_vs_prior(group_truths)
        for variant, table in (*development.items(), ("oracle_shares", oracle[name])):
            section["latent_without_prior"][variant][name] = latent_without_prior(group_truths, table)
        section["coupling"][name] = coupling_summary(group_rows)
        section["planted_problems"][name] = planted_problem_counts(group_rows)
    cpa = {truth.packet_id for truth in truths if truth.split == "test" and is_structural_cpa_case(truth)}
    credit_test = [r for r in results if r.split == "test" and r.line == "credit" and r.case_id not in held_out]
    section["structural_cpa"] = {
        "note": "credit test cases (held-out family excluded) with the amount-driven missing cpa_reviewed_financials "
        "document, summarized apart from the other credit test cases",
        "n_cases": len(cpa),
        "with_structural_cpa": group_summaries([r for r in credit_test if r.case_id in cpa]),
        "without_structural_cpa": group_summaries([r for r in credit_test if r.case_id not in cpa]),
    }
    return section


def headline(test_summaries: dict[str, Any]) -> dict[str, Any]:
    """The paired VOI-minus-checklist differences on the test split, all lines, documented priors (copied)."""
    summary = test_summaries.get("all")
    if summary is None:
        return {"status": "not measured", "reason": "no test-split cases were simulated"}
    paired = summary["paired_voi_minus_checklist"]["documented"]
    return {
        "group": "test split without the held-out family, all lines, documented priors",
        "comparison": "voi minus checklist, per case, paired bootstrap (1,000 resamples, seed 42)",
        "n_cases": paired["n_cases"],
        "voi_minus_checklist": {metric: paired[metric] for metric in HEADLINE_METRICS},
    }


def build_payload(
    results: list[SimulationResult],
    truths: list[PacketTruth],
    rows: list[dict[str, Any]],
    args: argparse.Namespace,
    run: RunProvenance,
    tables: tuple[PriorTables, dict[str, EmpiricalPriors]],
    n_development: int,
) -> dict[str, Any]:
    """The "simulator" section. tables: (the development-split tables, the oracle_shares table per group)."""
    started = time.perf_counter()
    held_out = {truth.packet_id for truth in truths if has_held_out_family(truth)}
    development = [result for result in results if result.split == "dev"]
    test_main = [result for result in results if result.split == "test" and result.case_id not in held_out]
    test_held_out = [result for result in results if result.split == "test" and result.case_id in held_out]
    cases = case_set(truths)
    test = group_summaries(test_main)
    payload = {
        "data_label": "SYNTHETIC",
        "description": "Innovation S5: evidence-request policies on SYNTHETIC JSON-only cases with a hidden latent "
        "truth. Replaces the circular 'next-best-evidence top-1 vs resolving document' headline.",
        "design": design(args, tables[0], tables[1], n_development),
        "case_set": cases,
        "headline": headline(test),
        "test": test,
        "held_out_family": group_summaries(test_held_out),
        "development": group_summaries(development),
        "diagnostics": diagnostics(results, truths, rows, held_out, tables),
        "not_claimable": list(NOT_CLAIMABLE),
        "provenance": run.finish(data_snapshots={"simulator_case_set": cases}),
    }
    payload["summary_seconds"] = round(time.perf_counter() - started, 1)
    return payload


def headline_lines(payload: dict[str, Any]) -> list[str]:
    """A few lines copied from the payload for the console."""
    lines = []
    for group in ("test", "held_out_family", "development"):
        summary = payload[group].get("all")
        if summary is None:
            continue
        cases = summary["cases"]["n_computed"]
        lines.append(f"{group}: {cases} cases (all lines), documented priors")
        metrics = summary["by_priors_variant"]["documented"]
        for policy in ("voi", "voi_lookahead", "checklist", "random"):
            row = metrics[policy]
            cost = row["cost_weighted_requests_per_case"]
            error = row["decision_error_rate"]
            lines.append(
                f"  {policy:14s} requests {row['requests_per_case']['value']}  cost {cost['value']} "
                f"[{cost['ci_low']}, {cost['ci_high']}]  decision error {error['value']} "
                f"[{error['ci_low']}, {error['ci_high']}]"
            )
        paired = summary["paired_voi_minus_checklist"]["documented"]
        for metric in ("cost_weighted_requests", "decision_error", "n_zero_value_requests"):
            row = paired[metric]
            lines.append(
                f"  voi - checklist {metric}: {row['mean_difference']} [{row['ci_low']}, {row['ci_high']}] "
                f"(n={row['n']})"
            )
    return lines


def write_runs(path: Path, results: list[SimulationResult]) -> None:
    """One JSON line per run (gitignored audit trail of every request made)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.model_dump(mode="json"), sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Build the cases, run every policy, summarize and write the results."""
    args = parse_args(argv)
    if args.test_per_line > args.cases_per_line:
        print("The test split cannot be larger than the number of cases.", file=sys.stderr)
        return 2
    problem = None if args.no_write else write_problem(args)
    if problem is not None:
        print(problem, file=sys.stderr)
        return 2
    run = RunProvenance.start()
    started = time.perf_counter()
    development = development_truths(args)
    development_priors = development_tables(development)
    truths = [truth for line in args.lines for truth in build_cases(line, args)]
    oracle_priors = oracle_tables(truths)
    results: list[SimulationResult] = []
    rows: list[dict[str, Any]] = []
    for truth in truths:
        case_results, row = run_case(truth, args.seed, case_tables(truth, development_priors, oracle_priors))
        results.extend(case_results)
        rows.append(row)
    simulation_seconds = round(time.perf_counter() - started, 1)
    tables = (development_priors, oracle_priors)
    payload = build_payload(results, truths, rows, args, run, tables, len(development))
    payload["simulation_seconds"] = simulation_seconds
    for text in headline_lines(payload):
        print(text)
    if args.no_write:
        return 0
    write_runs(args.runs_out, results)
    update_results(args.standalone, SECTION, payload)
    update_results(args.results, SECTION, payload)
    print(
        f"Wrote section '{SECTION}' of {args.standalone.name} and {args.results.name}, "
        f"and {len(results)} runs to {args.runs_out.name}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

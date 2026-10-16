"""Diagnostics of the evidence-request simulator's cases (innovation S5): what the hidden truths are made of.

This is EVALUATION code: it reads the generator's hidden truth, like simulator_cases.py. No engine module imports it.
Every number it produces is written next to the simulator results (scripts/simulate_requests.py), so a reader can
judge how the S5 results should be read. Three things are measured:

1. Latent option vs documented prior (latent_vs_prior). The generator does NOT draw each problem's latent (true)
   resolution from the template priors of config/resolution_templates.yaml: it takes the option the problem's cause
   implies, or the option nearest the hidden true value (see the notes at the top of
   docintel/synthetic/credit_records.py and insurance_records.py). So for several problem families the latent
   option is the same in every case. The table counts, per template and option, how often the option is the latent
   one, next to its documented prior. Consequences: the "documented" priors are not the distribution the truths
   come from, and the x0.5 / x1.5 robustness variants scale the prior of the generator's latent option (its
   nearest option), not of a draw from the priors.

2. Coupled reveals (coupling). Every insurance problem that changes the claims of the latent world (a hidden
   claim, a missing loss run, a corrected claim line) ALSO sets the application's stated claim count and total
   incurred to the true history (insurance_records.true_history). That is the claims_history_mismatch fix, so
   revealing one of those problems can remove a claims-history finding without the claims-history request. A
   problem is "coupled" when its latent substitutions set stated_claim_count or stated_total_incurred and its type
   is not claims_history_mismatch. For cases that also hold a claims_history_mismatch problem, the reference policy
   is run with each coupled problem revealed ALONE, and the case is counted when that removes the HM-3.3
   reconciliation finding (rule loss_run_reconciliation) that the starting state has.

3. Structural CPA cases (is_structural_cpa_case). Above $500,000 the generator always plants a missing
   cpa_reviewed_financials document (no packet document type can satisfy it), with the latent option always
   provided_consistent. The generator asks for these cases to be reported apart from drawn missing documents.

Plus planted_problem_counts: the mean number of planted problems per case of a group (the held-out family's cases
carry more co-planted problems than the main test split, which explains part of its larger VOI error).

4. Priors tables of the simulator's table variants, and their coverage (latent_shares, smoothed_shares,
   latent_without_prior). latent_shares turns a latent_vs_prior table into priors (template -> option -> share);
   smoothed_shares adds one to every option's latent count first, (count + 1) / (n + number of options), so no
   option has a zero prior. latent_without_prior counts the planted problems whose TRUE option a table gives no
   prior, i.e. the problems for which that variant behaves like latent_removed (a template missing from the table,
   or with no option above zero, keeps the documented priors and is not counted).
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from uwcopilot.docintel.truth import InjectedProblem, PacketTruth
from uwcopilot.evidence.simulator_cases import SimCase, revealed_state
from uwcopilot.evidence.simulator_models import EmpiricalPriors
from uwcopilot.evidence.state import IncompleteApplicationError, evaluate

STATED_HISTORY_PATHS: frozenset[str] = frozenset({"stated_claim_count", "stated_total_incurred"})
CLAIMS_HISTORY = "claims_history_mismatch"
RECONCILIATION_RULE = "loss_run_reconciliation"
STRUCTURAL_CPA_DOCUMENT = "cpa_reviewed_financials"
MISSING_LOSS_RUN_REQUEST = "missing_loss_run"
DIGITS = 4


def template_key(problem: InjectedProblem) -> str:
    """The resolution template of a problem: missing_loss_run for a missing loss run, else its problem type."""
    if problem.resolving_request == MISSING_LOSS_RUN_REQUEST:
        return MISSING_LOSS_RUN_REQUEST
    return problem.problem_type


# --------------------------------------------------------------------------------------------------------------
# 1. Latent option vs documented prior
# --------------------------------------------------------------------------------------------------------------


def latent_vs_prior(truths: Iterable[PacketTruth]) -> dict[str, Any]:
    """Per template: how many planted problems, and per option its documented prior, its latent count and share."""
    counts: dict[str, Counter] = {}
    priors: dict[str, dict[str, float]] = {}
    for truth in truths:
        for problem in truth.problems:
            key = template_key(problem)
            counts.setdefault(key, Counter())[problem.latent_option_id] += 1
            options = priors.setdefault(key, {})
            for option in problem.resolution_options:
                options.setdefault(option.option_id, round(option.prior, DIGITS))
    table: dict[str, Any] = {}
    for key in sorted(counts):
        total = sum(counts[key].values())
        table[key] = {
            "n_problems": total,
            "options": {
                option_id: {
                    "documented_prior": prior,
                    "latent_count": counts[key][option_id],
                    "latent_share": round(counts[key][option_id] / total, DIGITS),
                }
                for option_id, prior in priors[key].items()
            },
        }
    return table


# --------------------------------------------------------------------------------------------------------------
# 2. Coupled reveals
# --------------------------------------------------------------------------------------------------------------


def is_coupled(problem: InjectedProblem) -> bool:
    """True when revealing the problem also rewrites the stated claim history (and it is not the history problem)."""
    return problem.problem_type != CLAIMS_HISTORY and bool(STATED_HISTORY_PATHS & set(problem.latent_substitutions))


def has_reconciliation_finding(case: SimCase, problem_ids: list[str]) -> bool | None:
    """Whether the HM-3.3 reconciliation finding is open after revealing these problems (None: not evaluable)."""
    try:
        result = evaluate(revealed_state(case, problem_ids))
    except (IncompleteApplicationError, ValueError):
        return None
    return RECONCILIATION_RULE in result.rule_codes()


def coupling(case: SimCase) -> dict[str, Any]:
    """The coupled problems of a case and, when it also holds a claims-history problem, whether one of them alone
    removes the reconciliation finding the starting state has."""
    coupled = [problem for problem in case.problems if is_coupled(problem)]
    has_history = any(problem.problem_type == CLAIMS_HISTORY for problem in case.problems)
    removes = False
    if coupled and has_history and has_reconciliation_finding(case, []):
        removes = any(has_reconciliation_finding(case, [problem.problem_id]) is False for problem in coupled)
    return {
        "coupled_problem_types": sorted({problem.problem_type for problem in coupled}),
        "has_claims_history_problem": has_history,
        "coupled_reveal_removes_reconciliation_finding": removes,
    }


# --------------------------------------------------------------------------------------------------------------
# 3. Structural CPA cases, and per-case rows
# --------------------------------------------------------------------------------------------------------------


def is_structural_cpa_case(truth: PacketTruth) -> bool:
    """True when the case holds the amount-driven missing cpa_reviewed_financials document."""
    return any(
        problem.problem_type == "missing_document"
        and problem.resolving_request_params.get("document") == STRUCTURAL_CPA_DOCUMENT
        for problem in truth.problems
    )


def case_row(case: SimCase) -> dict[str, Any]:
    """The diagnostics of one prepared case (JSON-simple)."""
    return {
        "case_id": case.case_id,
        "line": case.truth.line,
        "split": case.truth.split,
        "n_planted_problems": len(case.problems),
        "structural_cpa": is_structural_cpa_case(case.truth),
        **coupling(case),
    }


def planted_problem_counts(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Mean planted problems per case and the distribution, for one group of case rows."""
    counts = [row["n_planted_problems"] for row in rows]
    return {
        "n_cases": len(counts),
        "mean_planted_problems_per_case": round(sum(counts) / len(counts), DIGITS) if counts else None,
        "planted_problems_per_case": dict(sorted(Counter(counts).items())),
    }


def coupling_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """How many cases have a coupled reveal, by problem type, and the cases where one alone removes the finding."""
    coupled = [row for row in rows if row["coupled_problem_types"]]
    by_type: Counter = Counter()
    for row in coupled:
        by_type.update(row["coupled_problem_types"])
    removing = [row["case_id"] for row in rows if row["coupled_reveal_removes_reconciliation_finding"]]
    return {
        "n_cases": len(rows),
        "n_cases_with_coupled_problem": len(coupled),
        "coupled_problems_by_type": dict(sorted(by_type.items())),
        "n_cases_with_claims_history_problem": sum(1 for row in rows if row["has_claims_history_problem"]),
        "cases_where_a_coupled_reveal_alone_removes_the_reconciliation_finding": removing,
    }


# --------------------------------------------------------------------------------------------------------------
# 4. Priors tables of the table variants, and their coverage
# --------------------------------------------------------------------------------------------------------------


def latent_shares(table: dict[str, Any]) -> EmpiricalPriors:
    """Template -> option -> latent share, from a latent_vs_prior table (the empirical_dev and oracle_shares
    priors)."""
    return {
        key: {option_id: row["latent_share"] for option_id, row in entry["options"].items()}
        for key, entry in table.items()
    }


def smoothed_shares(table: dict[str, Any]) -> EmpiricalPriors:
    """Template -> option -> (latent count + 1) / (problems + options), from a latent_vs_prior table (add-one
    smoothing: every option of the template keeps a prior above zero; the empirical_dev_smoothed priors)."""
    smoothed: EmpiricalPriors = {}
    for key, entry in table.items():
        options = entry["options"]
        denominator = entry["n_problems"] + len(options)
        smoothed[key] = {
            option_id: round((row["latent_count"] + 1) / denominator, DIGITS) for option_id, row in options.items()
        }
    return smoothed


def removes_latent_option(problem: InjectedProblem, shares: EmpiricalPriors) -> bool:
    """True when the table gives the problem's true option no prior while giving one to another of its options."""
    options = shares.get(template_key(problem))
    if options is None:
        return False  # the template keeps the documented priors
    kept = [option.option_id for option in problem.resolution_options if options.get(option.option_id, 0.0) > 0]
    return bool(kept) and problem.latent_option_id not in kept


def latent_without_prior(truths: Iterable[PacketTruth], shares: EmpiricalPriors) -> dict[str, Any]:
    """How many planted problems of a group a priors table leaves without a prior on their true option, per
    template and option (whether or not an open item stands for the problem)."""
    total = 0
    removed: Counter = Counter()
    for truth in truths:
        for problem in truth.problems:
            total += 1
            if removes_latent_option(problem, shares):
                removed[f"{template_key(problem)}.{problem.latent_option_id}"] += 1
    return {
        "n_problems": total,
        "n_true_option_without_prior": sum(removed.values()),
        "by_template_option": dict(sorted(removed.items())),
    }

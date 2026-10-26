"""SYNTHETIC champion/challenger and outcome analysis for the credit PD model (`make champion-challenger`).

Writes sections "champion_challenger" and "outcomes" of results/governance_metrics.json (risk/results.py
update_results), the two models under data/governance/champion_challenger/<model id>/, and their entries in the model
status registry data/governance/model_status.json (data/ is gitignored). It promotes nothing.

What it does:
1. Builds SYNTHETIC Lending Club-shaped loans (risk/data/lending_club.py synthetic_accepted_loans; 60,000 rows, seed
   42, issue years 2013-2018) and prepares them with the credit model's own loader (prepare_loans). The Lending Club
   data is not in this checkout (it needs a Kaggle key), so every number written describes SYNTHETIC data.
2. Champion: LightGBM fitted on loans issued <= 2014 (validation 2015). Challenger: the same code on loans issued
   <= 2015 (validation 2016). Both score the same finished loans of 2017-2018, which neither saw. Bootstrap CIs and
   paired bootstrap differences (1,000 resamples, seed 42); the PromotionRule gives a recommendation
   (governance/champion_challenger.py).
3. Saves both models and registers the champion as "champion" and the challenger as "challenger". A model already in
   the registry keeps its status. Only scripts/promote_model.py, run by a model_validator, promotes.
4. Outcomes: the champion (the model in use) records a PD for every application issued in 2017-2018, finished or not,
   and a SIMULATED decision rule (governance/outcomes.py SimulatedDecisionRule; cut-offs from the risk bands of the
   champion's training PDs) decides each one. The SYNTHETIC outcomes of the finished loans "arrive"; unfinished loans
   stay pending. governance/outcomes.py joins them by application id and reports the default rate by decision,
   approved-and-defaulted and the calibration of the recorded PD.

These are synthetic outcomes, not real loan performance. Real outcome analysis needs the Lending Club data and real
decisions.

Usage:
    uv run python scripts/champion_challenger.py
    uv run python scripts/champion_challenger.py --n-rows 20000 --n-resamples 1000
"""

import argparse
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from uwcopilot.common.paths import DATA_DIR, RESULTS_DIR
from uwcopilot.governance.champion_challenger import (
    DATA_LABEL,
    FAMILY,
    OUT_OF_TIME_YEARS,
    ChampionChallengerRun,
    model_fingerprint,
    model_id_for,
    register_model,
    run_champion_challenger,
    save_model_bundle,
)
from uwcopilot.governance.model_status import ModelStatusStore
from uwcopilot.governance.outcomes import SimulatedDecisionRule, summarize_outcomes
from uwcopilot.governance.provenance import display_path
from uwcopilot.risk.bands import band_cutoffs
from uwcopilot.risk.credit import CreditConfig
from uwcopilot.risk.data.lending_club import finished_loans, prepare_loans, synthetic_accepted_loans
from uwcopilot.risk.leakage import TARGET_COLUMN
from uwcopilot.risk.results import update_results

GOVERNANCE_DIR = DATA_DIR / "governance"
DEFAULT_RESULTS = RESULTS_DIR / "governance_metrics.json"
DEFAULT_REGISTRY = GOVERNANCE_DIR / "model_status.json"
DEFAULT_MODELS_DIR = GOVERNANCE_DIR / "champion_challenger"
DEFAULT_N_ROWS = 60_000
DEFAULT_DATA_SEED = 42
APPLICATION_ID_PREFIX = "SYNTHETIC-LC-"
DECIDED_BY = "SIMULATED rule"
OUTCOMES_LABEL = "SYNTHETIC outcomes joined to SIMULATED decisions on SYNTHETIC Lending Club-shaped loans"
OUTCOMES_INTERPRETATION = (
    "These are synthetic outcomes, not real loan performance, and the decisions come from a stated SIMULATED rule, "
    "not from underwriters or the agent. They show that outcomes are joined to decisions by application id and how "
    "default rates and calibration are reported. Real outcome analysis needs the Lending Club data and real decisions."
)
SELECTIVE_LABELS = (
    "Every decision has outcomes here only because the SYNTHETIC rows are all issued loans. In a real portfolio a "
    "declined application produces no outcome, so default rates for non-approved decisions exist only for files that "
    "were later booked (selective labels)."
)


@dataclass(frozen=True)
class RunPaths:
    """Where the run writes: the results file, the model status registry and the saved models."""

    results: Path
    registry: Path
    models_dir: Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n-rows", type=int, default=DEFAULT_N_ROWS, help="SYNTHETIC loans to generate")
    parser.add_argument("--data-seed", type=int, default=DEFAULT_DATA_SEED, help="seed of the SYNTHETIC generator")
    parser.add_argument("--n-resamples", type=int, default=1000, help="bootstrap resamples (ground rule: 1000)")
    parser.add_argument("--num-threads", type=int, default=4, help="LightGBM threads")
    parser.add_argument("--results-path", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help="model status registry (JSON)")
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR, help="where the two models are saved")
    return parser.parse_args(argv)


def frame_sha256(frame: pd.DataFrame) -> str:
    """SHA-256 of a table's values and index (pandas row hashes), to pin exactly which rows were used."""
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes()).hexdigest()


def synthetic_loans(n_rows: int, seed: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Prepared SYNTHETIC loans with an application id per row, and a description of how they were made."""
    raw = synthetic_accepted_loans(n_rows=n_rows, seed=seed)
    loans = prepare_loans(raw)
    loans.insert(0, "application_id", [f"{APPLICATION_ID_PREFIX}{index:06d}" for index in range(1, len(loans) + 1)])
    info = {
        "label": "SYNTHETIC",
        "why": "the Lending Club data is not in this checkout (it needs a Kaggle key)",
        "generator": "uwcopilot.risk.data.lending_club.synthetic_accepted_loans",
        "n_rows_generated": n_rows,
        "seed": seed,
        "prepared_with": "risk/data/lending_club.py prepare_loans (individual applications; unfinished loans kept)",
        "n_loans_prepared": int(len(loans)),
        "n_finished": int(loans["is_finished"].sum()),
        "issue_years": sorted(int(year) for year in loans["issue_year"].unique()),
        "prepared_loans_sha256": frame_sha256(loans),
        "application_ids": f"{APPLICATION_ID_PREFIX}NNNNNN, assigned in row order after preparation",
    }
    return loans, info


def save_and_register(run: ChampionChallengerRun, paths: RunPaths) -> dict[str, Any]:
    """Save both models and register them with their roles (nothing already registered is changed)."""
    store = ModelStatusStore(paths.registry)
    entries: dict[str, Any] = {}
    for role, model, window in (
        ("champion", run.champion, run.champion_window),
        ("challenger", run.challenger, run.challenger_window),
    ):
        fingerprint = model_fingerprint(model)
        model_id = model_id_for(window, fingerprint)
        files = save_model_bundle(model, model_id, window, paths.models_dir)
        reason = f"scripts/champion_challenger.py: {role} of the SYNTHETIC comparison ({window.describe()})"
        try:
            entry = register_model(store, model_id, role, fingerprint[:12], reason)
        except ValueError as error:
            raise SystemExit(
                f"{error}. The registry {display_path(paths.registry)} already holds another model in use for family "
                f"{FAMILY} (an earlier run with other settings?). Use another --registry file, or have a model "
                "validator promote that run's challenger first."
            ) from error
        entries[role] = {
            **entry,
            "fingerprint_sha256": fingerprint,
            "files": {name: display_path(path) for name, path in files.items()},
        }
    challenger_id = entries["challenger"]["model_id"]
    return {
        "registry": display_path(paths.registry),
        "family": FAMILY,
        "models": entries,
        "promotion": "nothing is promoted by this script; a model_validator decides with scripts/promote_model.py",
        "how_to_promote": f"uv run python scripts/promote_model.py {challenger_id} --role model_validator "
        "--principal <id> --reason '<why>'",
    }


def outcome_records(
    run: ChampionChallengerRun, loans: pd.DataFrame, champion_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], SimulatedDecisionRule, dict[str, Any]]:
    """SIMULATED decisions by the champion on every 2017-2018 application, and the SYNTHETIC outcomes that arrived.

    Returns (decision records, outcome records, the decision rule, the champion's training PD bands).
    """
    champion_train, _ = run.champion_window.split(finished_loans(loans))
    bands = band_cutoffs(
        run.champion.predict_pd(champion_train),
        "probability of default (champion)",
        f"finished SYNTHETIC loans in the champion's training years (<= {run.champion_window.train_last_year})",
    )
    rule = SimulatedDecisionRule.from_bands(bands)
    window = loans.loc[loans["issue_year"].isin(OUT_OF_TIME_YEARS)].reset_index(drop=True)
    pds = run.champion.predict_pd(window)
    decisions = rule.decide(window, pds)
    decision_records = [
        {
            "application_id": application_id,
            "decision": str(decision),
            "recorded_pd": float(pd_value),
            "model_id": champion_id,
            "decided_by": DECIDED_BY,
        }
        for application_id, decision, pd_value in zip(window["application_id"], decisions, pds, strict=True)
    ]
    finished = window.loc[window["is_finished"]]
    outcome_records_list = [
        {"application_id": application_id, "defaulted": int(defaulted)}
        for application_id, defaulted in zip(finished["application_id"], finished[TARGET_COLUMN], strict=True)
    ]
    return decision_records, outcome_records_list, rule, bands


def outcomes_payload(
    run: ChampionChallengerRun, loans: pd.DataFrame, champion_id: str, config: CreditConfig
) -> dict[str, Any]:
    """The "outcomes" section."""
    decisions, outcomes, rule, bands = outcome_records(run, loans, champion_id)
    years = list(OUT_OF_TIME_YEARS)
    return {
        "data_label": OUTCOMES_LABEL,
        "status": "measured on SYNTHETIC data with SIMULATED decisions",
        "interpretation": OUTCOMES_INTERPRETATION,
        "window": {
            "issue_years": years,
            "applications": len(decisions),
            "outcomes_arrived": len(outcomes),
            "pending": "unfinished SYNTHETIC loans have no outcome yet and stay pending",
        },
        "decision_model": {
            "model_id": champion_id,
            "why": "the champion is the model in use; a challenger never makes decisions",
        },
        "decision_rule": {**rule.describe(), "bands_from": bands},
        "decided_by": DECIDED_BY,
        "summary": summarize_outcomes(decisions, outcomes, n_resamples=config.n_resamples, seed=config.seed),
        "caveats": [
            SELECTIVE_LABELS,
            "The recorded PD is the champion's PD; its calibration here uses the same finished 2017-2018 rows as the "
            "champion's ECE in section champion_challenger, so the two must agree.",
        ],
    }


def run(loans: pd.DataFrame, data_info: dict[str, Any], config: CreditConfig, paths: RunPaths) -> dict[str, Any]:
    """Compare, save and register, analyse outcomes, and write both sections. Returns {section: payload}."""
    comparison = run_champion_challenger(loans, config)
    registry = save_and_register(comparison, paths)
    champion_id = registry["models"]["champion"]["model_id"]
    champion_section = {
        "data_label": DATA_LABEL,
        "status": "measured on SYNTHETIC data",
        "interpretation": "Both models were fitted and compared on SYNTHETIC Lending Club-shaped loans. The numbers "
        "show the champion/challenger procedure, not how the real credit model would behave.",
        "synthetic_data": data_info,
        **comparison.payload,
        "registry": registry,
    }
    sections = {
        "champion_challenger": champion_section,
        "outcomes": outcomes_payload(comparison, loans, champion_id, config),
    }
    for name, payload in sections.items():
        update_results(paths.results, name, payload)
    return sections


def format_interval(result: dict[str, Any]) -> str:
    """'0.7461 [0.7321, 0.7591]' (4 decimals, printed table only; the JSON keeps full precision)."""
    return f"{result['estimate']:.4f} [{result['ci_low']:.4f}, {result['ci_high']:.4f}]"


def print_summary(sections: dict[str, Any], paths: RunPaths) -> None:
    """Print the headline numbers."""
    section = sections["champion_challenger"]
    comparison = section["comparison"]
    print(f"Out-of-time window: {section['windows']['out_of_time']['n_rows']} finished SYNTHETIC loans (2017-2018)")
    print(f"{'metric':<8}{'champion':<28}{'challenger':<28}{'challenger - champion (paired)':<30}")
    for metric in ("auc", "ks", "brier", "ece"):
        difference = comparison["paired_difference_challenger_minus_champion"][metric]
        print(
            f"{metric:<8}{format_interval(comparison['champion'][metric]):<28}"
            f"{format_interval(comparison['challenger'][metric]):<28}{format_interval(difference):<30}"
        )
    recommendation = section["recommendation"]
    print(f"Recommendation: {recommendation['recommendation']} ({recommendation['reason']}); nothing was promoted")
    for role, entry in section["registry"]["models"].items():
        print(
            f"  {role}: {entry['model_id']} -> registry status {entry['status_in_registry']} ({entry['registration']})"
        )
    summary = sections["outcomes"]["summary"]
    print(f"Outcomes (SYNTHETIC, SIMULATED decisions): join {summary['join']}")
    for decision, entry in summary["default_rate_by_decision"].items():
        rate = entry["default_rate"]
        text = format_interval(rate) if "estimate" in rate else rate["status"]
        print(
            f"  {decision:<26} decisions {entry['n_decisions']:<6} with outcome {entry['n_with_outcome']:<6} "
            f"default rate {text}"
        )
    print(f"  approved and defaulted: {summary['approved_and_defaulted']['n_approved_and_defaulted']}")
    calibration = summary["recorded_pd_calibration"]
    if "ece" in calibration:
        print(
            f"  recorded PD: ECE {format_interval(calibration['ece'])}, Brier {format_interval(calibration['brier'])}"
        )
    else:
        print(f"  recorded PD calibration: {calibration['status']} ({calibration['reason']})")
    print(f"Wrote sections 'champion_challenger' and 'outcomes' of {display_path(paths.results)}")


def main(argv: list[str] | None = None) -> int:
    """Generate the SYNTHETIC loans, run everything and print the summary."""
    args = parse_args(argv)
    started = time.perf_counter()
    loans, data_info = synthetic_loans(args.n_rows, args.data_seed)
    config = CreditConfig(n_resamples=args.n_resamples)
    config.lgbm_params["num_threads"] = args.num_threads
    paths = RunPaths(results=args.results_path, registry=args.registry, models_dir=args.models_dir)
    sections = run(loans, data_info, config, paths)
    print_summary(sections, paths)
    print(f"Took {time.perf_counter() - started:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

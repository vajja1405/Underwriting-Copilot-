"""Continuous monitoring of a freMTPL2 claim-frequency model on a SIMULATED stream (Phase 6, part B).

Usage:
    uv run python scripts/monitor.py                 # full run
    uv run python scripts/monitor.py --n-streams 20  # quicker run with fewer validation streams

What it does, in order:
 1. Read config/monitoring.yaml (every threshold and the stream design) and freMTPL2, split 60/20/20 with seed 42
    (risk/data/fremtpl2.py). freMTPL2 has no date field, so there is no out-of-time split: the stream is SIMULATED
    from the held-out (test) split and every drift is INJECTED by a SYNTHETIC scenario.
 2. Train a Poisson GLM of claim frequency on the training split, or reuse the JSON copy cached in
    data/governance/monitoring/ when it was trained on the same files with the same split (monitoring_model.py).
 3. Bin the held-out split against the training split (PSI bins) and score it (monitoring.py).
 4. Validate the monitor: n_streams SIMULATED streams per scenario; false alert rate on no-drift streams, detection
    rate and delay (among detected streams, capped by the stream length) on drifted ones, each with a bootstrap 95%
    CI over streams and, for rates, the exact interval too (monitoring_stream.py).
 5. Demonstration stream (named in the config): every window is reported; each breach is appended to
    data/governance/monitoring_alerts.jsonl and the model is marked under review in data/governance/model_status.json.
 6. Indicators read from the latest results files (document extraction, agent, reviewer overrides and their reasons;
    monitoring_indicators.py). A breach is an alert record too; it changes no model status (not registered models).
 7. Write results/governance_metrics.json, section "monitoring" (risk/results.py update_results), and the HTML report
    results/monitoring_report.html (monitoring_report.py), which reads only that section's payload.

Run order: step 6 reads the "feedback" section of results/governance_metrics.json, so run scripts/feedback_report.py
first. `make governance-results` runs every governance producer in order (feedback-report, champion-challenger,
monitor, release-gate --write-results, governance-packs).

The Lending Club credit model cannot be monitored here (its data and models are absent from this checkout): the
section says "not measured" for it. Nothing here is a production monitoring system or a compliance programme.
"""

import argparse
import hashlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from uwcopilot.common.paths import DATA_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.governance.model_status import ModelStatusStore
from uwcopilot.governance.monitoring import (
    SCORE_NAME,
    AlertRecord,
    MonitoredPool,
    WindowResult,
    alert_records,
    build_monitored_pool,
    new_run_id,
    poisson_count_interval,
    record_alerts,
    utc_now,
)
from uwcopilot.governance.monitoring_config import DEFAULT_CONFIG_PATH, MonitoringConfig, load_monitoring_config
from uwcopilot.governance.monitoring_indicators import Indicator, read_indicators
from uwcopilot.governance.monitoring_model import (
    ensure_registered,
    file_checksums,
    load_or_train_frequency_glm,
)
from uwcopilot.governance.monitoring_report import render_monitoring_report
from uwcopilot.governance.monitoring_stream import (
    SubgroupRows,
    evaluate_stream,
    run_scenario,
    simulate_stream,
    subgroup_rows,
    summarize_scenario,
)
from uwcopilot.risk.data import fremtpl2
from uwcopilot.risk.results import CHECKSUM_LOG, not_measured, update_results

SECTION = "monitoring"
DATASET_NAME = "fremtpl2"
MODEL_COMPONENT = "fremtpl2_frequency_model"
GOVERNANCE_DIR = DATA_DIR / "governance"
NO_TIME_FIELD = (
    "freMTPL2 has no date or policy-year field, so no real stream of new business and no out-of-time split exist. "
    "Windows are random draws from the held-out split and every drift is INJECTED (SYNTHETIC scenarios)."
)
LIMITATIONS = [
    NO_TIME_FIELD,
    "All streams draw from one held-out pool of policies, so they are not independent samples of the world; the "
    "bootstrap intervals describe stream-to-stream variation for this pool and this model.",
    "A window is an independent random draw (without replacement inside the window), so a policy can appear in two "
    "windows of one stream.",
    "Outcomes are assumed known at the end of each window (no claim reporting lag, no IBNR); a real book sees claims "
    "months later, which adds that lag to every calibration detection delay.",
    "Thresholds are design choices (config/monitoring.yaml), not fitted limits. The detection results hold for these "
    "scenarios, this window size and this model only.",
    "The extraction, agent and reviewer indicators are single values read from the latest results files, not a "
    "stream; they cannot show drift over time.",
    "Demonstration only: not a production monitoring system and not a compliance programme. Whether and how any "
    "monitoring expectation applies depends on the institution and its risk profile.",
]
CREDIT_NOT_MEASURED = (
    "The Lending Club data and every trained credit model are absent from this checkout (downloading them needs a "
    "Kaggle key). results/risk_metrics.json keeps the credit results measured earlier, but no credit application "
    "stream could be scored here, so the credit model is not monitored."
)


@dataclass(frozen=True)
class RunPaths:
    """Where the run reads and writes."""

    config: Path
    data_dir: Path
    results_dir: Path
    results: Path
    report: Path
    cache_dir: Path
    alert_log: Path
    registry: Path
    checksum_log: Path


def relative(path: Path) -> str:
    """A path relative to the repo root when it is inside it (keeps machine paths out of the results)."""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def score(model: Any, frame: pd.DataFrame) -> np.ndarray:
    """Predicted claims per policy-year."""
    return np.asarray(model.predict_rate(frame), dtype=float)


def held_out_calibration(pool: MonitoredPool, confidence: float) -> dict[str, Any]:
    """Observed / predicted claims on the whole held-out pool, with the exact Poisson interval."""
    observed = int(pool.recorded_claims.sum())
    predicted = float(pool.expected_claims.sum())
    low, high = poisson_count_interval(observed, confidence)
    return {
        "observed_claims": observed,
        "predicted_claims": predicted,
        "observed_to_predicted": observed / predicted,
        "interval": [low / predicted, high / predicted],
        "interval_method": f"exact (Garwood) Poisson {confidence:.0%} interval of the observed count / predicted",
    }


def shift_rows_by_scenario(config: MonitoringConfig, pool_frame: pd.DataFrame) -> dict[str, SubgroupRows | None]:
    """Subgroup rows for each covariate-shift scenario (None for the others)."""
    rows: dict[str, SubgroupRows | None] = {}
    for name, scenario in config.stream.scenarios.items():
        shift = scenario.covariate_shift
        rows[name] = subgroup_rows(pool_frame, shift.subgroup) if shift else None
    return rows


def scenario_settings(config: MonitoringConfig, pool_frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """What each SYNTHETIC scenario injects, with the subgroup's share of the held-out pool."""
    described = {}
    for name, scenario in config.stream.scenarios.items():
        entry: dict[str, Any] = {
            "description": scenario.description,
            "data_label": "SYNTHETIC",
            "drifted": scenario.drifted,
            "claim_rate_multiplier": scenario.claim_rate_multiplier,
            "covariate_shift": None,
        }
        if scenario.covariate_shift is not None:
            groups = subgroup_rows(pool_frame, scenario.covariate_shift.subgroup)
            entry["covariate_shift"] = {
                "subgroup": scenario.covariate_shift.subgroup.describe(),
                "target_share_of_drifted_windows": scenario.covariate_shift.target_share,
                "share_of_held_out_pool": groups.inside.size / len(pool_frame),
            }
        described[name] = entry
    return described


def window_payload(result: WindowResult, drifted: bool, alert_ids: list[str]) -> dict[str, Any]:
    """One demonstration window as plain values."""
    return {
        "window": result.window,
        "drifted": drifted,
        "n_policies": result.n_policies,
        "exposure_years": result.exposure_years,
        "observed_claims": result.observed_claims,
        "predicted_claims": result.predicted_claims,
        "level": result.level,
        "alert_ids": alert_ids,
        "checks": [
            {
                "check": check.check,
                "subject": check.subject,
                "value": check.value,
                "interval": list(check.interval) if check.interval else None,
                "level": check.level,
            }
            for check in result.checks
        ],
    }


def run_demonstration(
    pool: MonitoredPool,
    config: MonitoringConfig,
    shift_rows: dict[str, SubgroupRows | None],
    run_id: str,
    model_id: str | None,
    store: ModelStatusStore | None,
    alert_log: Path,
) -> tuple[dict[str, Any], list[AlertRecord]]:
    """Check the demonstration stream window by window; record every breach (alert log, then model status)."""
    name = config.stream.demonstration.scenario
    stream_index = config.stream.demonstration.stream
    scenario = config.stream.scenarios[name]
    windows = simulate_stream(pool, scenario, config.stream, stream_index, shift_rows[name])
    results = evaluate_stream(pool, windows, config.thresholds)
    all_records: list[AlertRecord] = []
    per_window = []
    for window, result in zip(windows, results, strict=True):
        records = alert_records(
            result.alerts,
            run_id,
            first_number=len(all_records) + 1,
            component=MODEL_COMPONENT,
            model_id=model_id,
            window=result.window,
            scenario=name,
        )
        record_alerts(records, alert_log, store)
        all_records.extend(records)
        per_window.append(window_payload(result, window.drifted, [record.alert_id for record in records]))
    payload = {
        "scenario": name,
        "stream": stream_index,
        "data_label": "SIMULATED",
        "note": "Stream 0 of this scenario's validation streams (same seed), checked window by window.",
        "windows": per_window,
    }
    return payload, all_records


def indicator_alerts(indicators: list[Indicator], run_id: str, first_number: int) -> list[AlertRecord]:
    """Alert records for indicators at level alert (record only: they are not registered models)."""
    records: list[AlertRecord] = []
    for indicator in indicators:
        if indicator.level != "alert":
            continue
        records += alert_records(
            [indicator.as_check()], run_id, first_number + len(records), component=indicator.component, model_id=None
        )
    return records


def model_status_payload(store: ModelStatusStore, model_id: str, before: str | None, note: str) -> dict[str, Any]:
    """The monitored model's registry entry after the run."""
    entry = store.entry(model_id)
    return {
        "registry": relative(store.path),
        "model_id": model_id,
        "registration": note,
        "status_before_run": before,
        "status_after_run": entry.status,
        "history": [change.model_dump(mode="json") for change in entry.history],
    }


def validation_payload(
    pool: MonitoredPool, config: MonitoringConfig, shift_rows: dict[str, SubgroupRows | None]
) -> dict[str, Any]:
    """Validation summary of every scenario."""
    summaries = {}
    for name, scenario in config.stream.scenarios.items():
        outcomes = run_scenario(pool, scenario, config.stream, config.thresholds, shift_rows[name])
        summaries[name] = summarize_scenario(outcomes, scenario, config.stream, config.bootstrap)
        print(f"  validated {name}: {len(outcomes)} streams", flush=True)
    return summaries


def run(config: MonitoringConfig, policies: pd.DataFrame, data_files: list[Path], paths: RunPaths) -> dict[str, Any]:
    """The whole monitoring run on cleaned freMTPL2-shaped policies; returns the results payload."""
    started = time.perf_counter()
    stages: dict[str, float] = {}

    def lap(stage: str, since: float) -> float:
        now = time.perf_counter()
        stages[stage] = round(now - since, 2)
        return now

    split_seed = fremtpl2.SPLIT_SEED
    splits = fremtpl2.split_policies(policies, seed=split_seed)
    reference, pool_frame = splits["train"], splits["test"]
    fingerprint = {"data_files_sha256": file_checksums(data_files), "split_seed": split_seed}
    model, cache = load_or_train_frequency_glm(reference, paths.cache_dir, fingerprint)
    clock = lap("split and model (train or load cache)", started)

    pool = build_monitored_pool(
        reference,
        score(model, reference),
        pool_frame,
        score(model, pool_frame),
        fremtpl2.RATING_FEATURES,
        config.thresholds,
    )
    shift_rows = shift_rows_by_scenario(config, pool_frame)
    clock = lap("score and bin the held-out pool", clock)

    validation = validation_payload(pool, config, shift_rows)
    clock = lap("validation streams", clock)

    run_id = new_run_id()
    store = ModelStatusStore(paths.registry)
    model_id: str | None = config.monitored_model.model_id
    try:
        status_before, registration_note = ensure_registered(store, config.monitored_model)
    except ValueError as error:
        status_before, registration_note, model_id = None, f"not registered: {error}", None
    demonstration, model_alerts = run_demonstration(
        pool, config, shift_rows, run_id, model_id, store if model_id else None, paths.alert_log
    )
    indicators, sources = read_indicators(paths.results_dir, config.thresholds.indicators)
    other_alerts = indicator_alerts(indicators, run_id, first_number=len(model_alerts) + 1)
    record_alerts(other_alerts, paths.alert_log, None)
    clock = lap("demonstration stream, indicators and alerts", clock)

    all_alerts = model_alerts + other_alerts
    payload: dict[str, Any] = {
        "data_label": "SYNTHETIC",
        "status": (
            "measured on a SIMULATED stream: PUBLIC freMTPL2 held-out policies with SYNTHETIC injected drift; "
            "indicators read from the latest results files"
        ),
        "description": (
            "Continuous monitoring demonstration (Phase 6 B): input drift (PSI per feature), score drift (PSI of the "
            "predicted frequency), calibration drift (observed/predicted claims with an exact Poisson interval, ECE "
            "of P(claim > 0)) of a Poisson GLM, validated on SIMULATED streams; extraction, agent and reviewer "
            "indicators read from results files."
        ),
        "config": {
            "path": relative(paths.config),
            "sha256": hashlib.sha256(paths.config.read_bytes()).hexdigest(),
            "thresholds": config.thresholds.model_dump(mode="json"),
            "stream": config.stream.model_dump(mode="json"),
            "bootstrap": config.bootstrap.model_dump(mode="json"),
        },
        "monitored_model": {
            **config.monitored_model.model_dump(mode="json"),
            "type": "Poisson GLM, log link, offset log(Exposure) (risk/insurance.py fit_frequency_glm)",
            "training_split": {
                "method": "random, stratified on ClaimNb > 0 (risk/data/fremtpl2.py split_policies)",
                "seed": split_seed,
                "n_train_policies": int(len(reference)),
                "n_held_out_policies": int(len(pool_frame)),
                "validation_split": "not used by the monitor",
            },
            "score": f"{SCORE_NAME}: predicted claims per policy-year",
            "cache": {**cache, "cache_file": relative(cache["cache_file"])},
            "held_out_calibration": held_out_calibration(pool, config.thresholds.calibration_ratio.confidence),
            "psi_reference": "the training split (the model's development sample)",
        },
        "stream_design": {
            "pool": "the held-out (test) split of freMTPL2",
            "pool_size": pool.size,
            "window_size": config.stream.window_size,
            "n_windows": config.stream.n_windows,
            "drift_start_window": config.stream.drift_start_window,
            "n_streams_per_scenario": config.stream.n_streams,
            "seed": config.stream.seed,
            "random_numbers": "stream i uses numpy default_rng([seed, i]); clean windows match across scenarios",
            "no_time_field": NO_TIME_FIELD,
        },
        "scenarios": scenario_settings(config, pool_frame),
        "validation": validation,
        "demonstration_stream": demonstration,
        "indicators": {"sources": sources, "values": [indicator.to_dict() for indicator in indicators]},
        "alerts": {
            "log": relative(paths.alert_log),
            "run_id": run_id,
            "n_alerts_this_run": len(all_alerts),
            "records": [record.model_dump(mode="json") for record in all_alerts],
        },
        "model_status": (
            model_status_payload(store, model_id, status_before, registration_note)
            if model_id
            else {"registry": relative(paths.registry), "registration": registration_note}
        ),
        "credit_model": not_measured(CREDIT_NOT_MEASURED),
        "llm_dependent": {
            "unsupported_claim_rate": not_measured("needs an LLM run; no LLM is available in this checkout"),
            "citation_accuracy_of_llm_memos": not_measured("needs an LLM run; no LLM is available in this checkout"),
        },
        "limitations": LIMITATIONS,
        "report": relative(paths.report),
    }
    stages["total"] = round(time.perf_counter() - started, 2)
    payload["runtime_seconds"] = stages
    update_results(
        paths.results, SECTION, payload, datasets=[DATASET_NAME], checksum_log=paths.checksum_log, repo_root=REPO_ROOT
    )
    paths.report.parent.mkdir(parents=True, exist_ok=True)
    paths.report.write_text(render_monitoring_report(payload, generated_at=utc_now()), encoding="utf-8")
    return payload


def print_summary(payload: dict[str, Any]) -> None:
    """A short console summary of the validation and the demonstration."""
    for name, summary in payload["validation"].items():
        key = "detection_rate" if summary["drifted"] else "false_alert_rate"
        rate = summary[key]
        line = f"{name}: {key} {rate['estimate']:.3f} [{rate['ci_low']:.3f}, {rate['ci_high']:.3f}]"
        delay = summary.get("detection_delay_windows", {})
        mean = delay.get("mean")
        if mean:
            line += (
                f", mean delay among {delay['n_detected_streams']} detected streams {mean['estimate']:.2f} windows "
                f"[{mean['ci_low']:.2f}, {mean['ci_high']:.2f}] (capped at {delay['max_possible_delay']})"
            )
        print(line)
    for indicator in payload["indicators"]["values"]:
        if indicator["status"] != "measured":
            print(f"indicator {indicator['name']}: {indicator['status']} ({indicator['note']})")
    print(f"alerts this run: {payload['alerts']['n_alerts_this_run']} -> {payload['alerts']['log']}")
    status = payload["model_status"]
    before, after = status.get("status_before_run"), status.get("status_after_run")
    print(f"model status: {before} -> {after} ({status['registration']})")
    print(f"runtime: {payload['runtime_seconds']['total']} s; report: {payload['report']}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--data-dir", type=Path, default=fremtpl2.DEFAULT_DATA_DIR)
    parser.add_argument("--results-path", type=Path, default=RESULTS_DIR / "governance_metrics.json")
    parser.add_argument("--report-path", type=Path, default=RESULTS_DIR / "monitoring_report.html")
    parser.add_argument(
        "--indicator-dir", type=Path, default=RESULTS_DIR, help="where the indicator results files are read"
    )
    parser.add_argument("--cache-dir", type=Path, default=GOVERNANCE_DIR / "monitoring")
    parser.add_argument("--alert-log", type=Path, default=GOVERNANCE_DIR / "monitoring_alerts.jsonl")
    parser.add_argument("--registry", type=Path, default=GOVERNANCE_DIR / "model_status.json")
    parser.add_argument("--n-streams", type=int, default=None, help="override stream.n_streams (quicker runs)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the monitor on freMTPL2 and print a summary."""
    args = parse_args(argv)
    config = load_monitoring_config(args.config)
    if args.n_streams is not None:
        document = config.model_dump(mode="json")
        document["stream"]["n_streams"] = args.n_streams
        config = MonitoringConfig.model_validate(document)
    data = fremtpl2.load_fremtpl2(args.data_dir)
    paths = RunPaths(
        config=args.config,
        data_dir=args.data_dir,
        results_dir=args.indicator_dir,
        results=args.results_path,
        report=args.report_path,
        cache_dir=args.cache_dir,
        alert_log=args.alert_log,
        registry=args.registry,
        checksum_log=CHECKSUM_LOG,
    )
    data_files = [args.data_dir / fremtpl2.FREQUENCY_FILE, args.data_dir / fremtpl2.SEVERITY_FILE]
    payload = run(config, data.policies, data_files, paths)
    print_summary(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())

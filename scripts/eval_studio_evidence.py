"""Write results/studio_evidence_metrics.json: the founder ledger and the provenance-locked narrative (Studio S4).

    uv run python scripts/eval_studio_evidence.py                     # template provider, 36 golden scenarios
    uv run python scripts/eval_studio_evidence.py --provider ollama --model qwen2.5-1.5b-instruct-f16 --limit 6

On the golden scenarios (studio/narrative/golden.py: 6 business types x the SYNTHETIC metro and the 5 real metros in
their current local mode, seeded SYNTHETIC plans) it measures:
1. the share of rendered numbers that trace to the ledger, by the round-trip audit and by the bound check, for the
   template narrative and for the evidence appendix (every claim rendered on its own line);
2. the validity of the template draft (the narrative grammar);
3. the recall of each check per mutation type on the founder sentence set (provenance.mutations on the rendered
   narratives' sentences) and its false-flag rate on the unmutated and equivalent-rewrite controls;
4. with --provider fake|ollama|hf_inference: the LLM fallback rate, first-try and after-retry validity, the
   validator's findings, and the traced share of the final text. With the template provider no LLM is called and
   these are "not measured". hf_inference is disabled by default (it can cost money); this build never ran it.
The template run writes results/studio_evidence_metrics.json; any other provider writes its own file
(results/studio_evidence_metrics_<provider>.json), so an LLM run never overwrites the template run.
Rates carry a Wilson 95% interval and a bootstrap 95% interval over scenarios (1,000 resamples, seed 42). Every
number in the file is computed here; no plan is real and nothing here is a claim about real lending outcomes.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from uwcopilot.common.paths import CONFIG_DIR, REPO_ROOT, RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance
from uwcopilot.llm.base import LLMClient
from uwcopilot.llm.factory import cost_tracker_from_settings, wrap_provider_client
from uwcopilot.llm.ollama_client import OllamaClient, OllamaOptions
from uwcopilot.llm.settings import load_llm_settings
from uwcopilot.studio import DISCLAIMER, SIMULATION_LABEL, SYNTHETIC_LABEL
from uwcopilot.studio.data.cache import write_atomic
from uwcopilot.studio.feasibility.assumptions import ASSUMPTIONS_FILE
from uwcopilot.studio.narrative.evaluation import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CHECKS,
    evaluate_scenario,
    ledger_totals,
    llm_block,
    pooled,
    recall_block,
    traced_share,
    wrong_slot_block,
)
from uwcopilot.studio.narrative.founder_ledger import FOUNDER_LEDGER_VERSION
from uwcopilot.studio.narrative.generate import (
    LLM_PROVIDERS,
    OLLAMA_NUM_CTX,
    OLLAMA_SEED,
    TEMPLATE_PROVIDER,
    default_client,
)
from uwcopilot.studio.narrative.golden import GOLDEN_SEED, build_golden_scenario, golden_specs
from uwcopilot.studio.narrative.prompts import all_prompt_hashes
from uwcopilot.studio.path_to_approval.levers import LEVERS_FILE
from uwcopilot.studio.path_to_approval.search import fabric_for, mode_for
from uwcopilot.studio.profiles.loader import PROFILES_DIR

GENERATED_BY = "scripts/eval_studio_evidence.py"
DEFAULT_OUTPUT: Path = RESULTS_DIR / "studio_evidence_metrics.json"
PROGRAM_FILE: Path = REPO_ROOT / "corpus" / "programs" / "northwind_startup_program.md"
GENERATION_CONFIG: Path = CONFIG_DIR / "generation_eval.yaml"
OLLAMA_TIMEOUT_S = 1800.0
NOT_MEASURED_TEMPLATE = (
    "not measured: the template provider makes no LLM call; an LLM run (--provider ollama ...) writes "
    "results/studio_evidence_metrics_<provider>.json"
)
NOT_MEASURED_HF = (
    "not measured: the hf_inference provider is disabled by default (UWC_HF_INFERENCE_ENABLED, HF_TOKEN and a verified "
    "price are all required) and was never run in this build; the owners decided on no extra spending"
)
SCOPE_NOTE = (
    "Seeded SYNTHETIC founder plans (studio/narrative/golden.py), 6 business types x the SYNTHETIC metro and the real "
    "metros in their current local mode. The template narrative passes the grammar and traces by construction (its "
    "numbers are typed by the renderer from the ledger); these numbers check that the construction holds on every "
    "scenario, they are not evidence about how often a model would write a wrong number."
)
CHECKER_NOTE = (
    "Both checks read the same mutated sentences. The round-trip audit accepts any number that equals SOME claim of "
    "the ledger, so a value moved to another sentence (cross_section) or a flipped comparator is invisible to it by "
    "design. The bound check accepts a number only if it equals a claim its own sentence cites through a footnote "
    "marker, with the threshold's comparator; cross_section mutations draw values absent from the cited claims, so the "
    "bound "
    "check's recall there follows from its design. The mutation sentences come from the template narratives the same "
    "author wrote, so this is a test of the implementation, not a held-out benchmark."
)
WRONG_SLOT_NOTE = (
    "The measured blind spot. One claim slot of each template draft is pointed at another claim with the same display "
    "format (up to 10 per scenario, seeded), the way a model could pick the wrong slot. The rendered number is a true "
    "ledger value cited to the claim it came from, so the round-trip audit and the bound check cannot see it; the "
    "grammar catches it only when the slot belongs to a different check in a sentence with an assert slot (V10). "
    "A low recall here is the expected, disclosed limit of slot-level provenance."
)


def config_inputs() -> list[Path]:
    """The config files that shape the numbers (hashed into the provenance block)."""
    files = [ASSUMPTIONS_FILE, LEVERS_FILE, PROGRAM_FILE]
    files += sorted(PROFILES_DIR.glob("*.yaml"))
    files += sorted((CONFIG_DIR / "studio" / "metros").glob("*.yaml"))
    return files


def metro_block(metro_ids: list[str]) -> dict[str, Any]:
    """Each metro's mode, data label and snapshot fingerprint in this environment."""
    block = {}
    for metro_id in metro_ids:
        fabric = fabric_for(metro_id)
        mode, reason = mode_for(metro_id)
        snapshot = getattr(fabric, "snapshot", None)
        block[metro_id] = {
            "mode": mode,
            "mode_reason": reason,
            "data_label": fabric.data_label,
            "snapshot_date": snapshot.snapshot_date.isoformat() if snapshot is not None else None,
            "snapshot_fingerprint": snapshot.fingerprint() if snapshot is not None else None,
        }
    return block


class ReusedClient:
    """Builds the LLM client once and hands the same one to every scenario (one connection, one cache).

    For ollama the client gets a longer read timeout (OLLAMA_TIMEOUT_S) than the library default: on a shared CPU a
    1.5B model can need more than ten minutes for one long draft, and a timeout would count as a failed call.
    """

    def __init__(self) -> None:
        self.client: LLMClient | None = None

    def __call__(self, provider: str, model: str | None) -> LLMClient:
        if self.client is None:
            self.client = ollama_client(model) if provider == "ollama" else default_client(provider, model)
        return self.client


def ollama_client(model: str | None) -> LLMClient:
    """Cached(Budgeted(OllamaClient)) with the narrative's context size and seed and a long read timeout."""
    settings = load_llm_settings()
    options = OllamaOptions(num_ctx=OLLAMA_NUM_CTX, seed=OLLAMA_SEED)
    chosen = model or settings.model_for("ollama")
    inner = OllamaClient(settings.ollama_base_url, chosen, options=options, timeout_s=OLLAMA_TIMEOUT_S)
    return wrap_provider_client(inner, cost_tracker_from_settings(settings), settings, use_cache=True)


def recorded_license(model: str) -> str | None:
    """The license text recorded for a local model in config/generation_eval.yaml (None when not recorded)."""
    document = yaml.safe_load(GENERATION_CONFIG.read_text(encoding="utf-8")) or {}
    entry = (document.get("models") or {}).get(model) or {}
    return entry.get("license")


def model_records(provider: str, factory: ReusedClient) -> list[dict[str, Any]]:
    """The model entry of the provenance block for a local model that answered (empty otherwise)."""
    client = factory.client
    inner = getattr(getattr(client, "inner", None), "inner", None)
    if provider != "ollama" or inner is None or not hasattr(inner, "model_record"):
        return []
    return [inner.model_record(recorded_license(inner.model))]


def build_report(rows: list[dict[str, Any]], provider: str, model: str | None, limit: int | None) -> dict[str, Any]:
    """The results document (without the provenance block)."""
    llm = llm_block(rows)
    template_valid = pooled([int(row["template"]["valid"]) for row in rows], [1] * len(rows))
    report: dict[str, Any] = {
        "description": "Studio S4: founder evidence ledger and provenance-locked narrative on the golden scenarios",
        "data_label": SYNTHETIC_LABEL,
        "label": SIMULATION_LABEL,
        "disclaimer": DISCLAIMER,
        "scope": SCOPE_NOTE,
        "ledger_version": FOUNDER_LEDGER_VERSION,
        "provider": provider,
        "model": model,
        "golden": {
            "seed": GOLDEN_SEED,
            "scenarios_available": len(golden_specs()),
            "scenarios_run": len(rows),
            "limit": limit,
            "by_metro_mode": count_by(rows, "metro_mode"),
            "site_scored": sum(int(row["site_scored"]) for row in rows),
            "with_synthetic_quoted_rate": sum(int(bool(row["notes"])) for row in rows),
            "path_status": count_by(rows, "path_status"),
        },
        "ledgers": ledger_totals(rows),
        "headline": {
            "narrative_numbers_traced_round_trip": traced_share(rows, "template", "round_trip"),
            "narrative_numbers_traced_bound": traced_share(rows, "template", "bound"),
            "appendix_numbers_traced_round_trip": traced_share(rows, "appendix", "round_trip"),
            "appendix_numbers_traced_bound": traced_share(rows, "appendix", "bound"),
            "template_draft_valid": template_valid,
            "llm_fallback_rate": llm["fallback_rate"] if llm["measured"] else llm.get("reason", NOT_MEASURED_TEMPLATE),
            "hf_inference": NOT_MEASURED_HF,
        },
        "checker": {
            "note": CHECKER_NOTE,
            "sentences": sum(row["sentences"] for row in rows),
            **{check: recall_block(rows, check) for check in CHECKS},
            "wrong_slot_blind_spot": {"note": WRONG_SLOT_NOTE, **wrong_slot_block(rows)},
        },
        "llm": llm if llm["measured"] or "reason" in llm else {"measured": False, "reason": NOT_MEASURED_TEMPLATE},
        "statistics": {
            "intervals": "Wilson 95% and bootstrap 95% over scenarios",
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
        "runtime": {
            "seconds_build_total": round(sum(row["seconds_build"] for row in rows), 1),
            "seconds_evaluate_total": round(sum(row["seconds"] for row in rows), 1),
            "seconds_evaluate_max": max((row["seconds"] for row in rows), default=None),
            "note": (
                "wall time on a shared machine; build = the scenario's simulation, SiteIQ and path search; evaluate = "
                "ledger, template narrative, appendix, mutation test and any LLM calls"
            ),
        },
        "per_scenario": [slim(row) for row in rows],
    }
    return report


def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    """How many rows have each value of one field."""
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row[key])
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def slim(row: dict[str, Any]) -> dict[str, Any]:
    """A per-scenario row without the mutation counts (aggregated above) and with short LLM attempts."""
    kept = {key: value for key, value in row.items() if key != "mutations"}
    if kept["llm"] is not None:
        kept["llm"] = {**kept["llm"], "attempts": [slim_attempt(item) for item in kept["llm"]["attempts"]]}
    return kept


def slim_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    """One attempt with at most 12 validator findings listed."""
    violations = attempt["violations"]
    return {**attempt, "violations": violations[:12], "violations_total": len(violations)}


def default_output(provider: str) -> Path:
    """results/studio_evidence_metrics.json for the template; a provider's own file otherwise."""
    if provider == TEMPLATE_PROVIDER:
        return DEFAULT_OUTPUT
    return RESULTS_DIR / f"studio_evidence_metrics_{provider}.json"


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Evaluate every chosen scenario and return the report (with provenance)."""
    provenance = RunProvenance.start(GENERATED_BY)
    factory = ReusedClient()
    rows = []
    specs = golden_specs()
    for metro_id, profile_id in specs[: args.limit] if args.limit is not None else specs:
        started = time.perf_counter()
        scenario = build_golden_scenario(metro_id, profile_id, n_paths=args.n_paths)
        built = time.perf_counter() - started
        row = evaluate_scenario(scenario, args.provider, args.model, factory)
        row["seconds_build"] = round(built, 3)
        print(f"{row['scenario_id']}: {time.perf_counter() - started:.1f} s", file=sys.stderr)
        rows.append(row)
    report = build_report(rows, args.provider, args.model, args.limit)
    metros = sorted({row["metro_id"] for row in rows})
    report["metros"] = metro_block(metros)
    report["provenance"] = provenance.finish(
        prompts=all_prompt_hashes(),
        corpus_checksum="not applicable (no retrieval corpus is used)",
        eval_sets=config_inputs(),
        models=model_records(args.provider, factory),
        data_snapshots={
            "studio_snapshots": {metro: block["snapshot_fingerprint"] for metro, block in report["metros"].items()},
            "golden_seed": GOLDEN_SEED,
        },
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments, run, write the JSON and print the headline."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", default=TEMPLATE_PROVIDER, choices=[TEMPLATE_PROVIDER, *LLM_PROVIDERS])
    parser.add_argument("--model", default=None, help="model id for an LLM provider (default: the provider's)")
    parser.add_argument("--limit", type=int, default=None, help="only the first N golden scenarios")
    parser.add_argument("--n-paths", type=int, default=1000, help="simulated paths per scenario (default 1,000)")
    parser.add_argument("--output", type=Path, default=None, help="results file (default depends on the provider)")
    args = parser.parse_args(argv)
    output = args.output or default_output(args.provider)
    if args.provider == "ollama":
        from uwcopilot.llm.ollama_queue import ollama_queue_lock

        with ollama_queue_lock(f"{GENERATED_BY} --provider ollama --model {args.model}"):
            report = run(args)
    else:
        report = run(args)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(output, (json.dumps(report, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(report["headline"], indent=2))
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

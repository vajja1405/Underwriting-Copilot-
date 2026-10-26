"""Founder Studio bullets, built only from results/studio_*.json and results/web_metrics.json.

Every Studio number describes SYNTHETIC plans, SYNTHETIC cities or SIMULATED probabilities, and the bullets say so.
One results file is deliberately not quoted (see studio_notes): results/studio_evidence_metrics_ollama.json, a small
local-model run whose narratives all fell back to the template.
"""

from typing import Any

from uwcopilot.resume.lender_governance import LOCAL_TARGET_KIND, WEB
from uwcopilot.resume.results import ResultsFolder, dig, estimate_at, missing_reason, number_at, read_estimate
from uwcopilot.resume.text import STUDIO, Bullet, BulletDraft, Omission, count, interval, num

RANKING = "studio_metro_ranking.json"
COVERAGE = "studio_metro_coverage.json"
FEASIBILITY = "studio_feasibility_metrics.json"
EVIDENCE = "studio_evidence_metrics.json"
EVIDENCE_OLLAMA = "studio_evidence_metrics_ollama.json"
HANDOFF = "studio_handoff_metrics.json"
PATHS = "studio_path_metrics.json"
SITEIQ = "studio_siteiq_metrics.json"
BUSINESS = "studio_business_metrics.json"
MISSPECIFIED = "misspecified"

Built = tuple[Bullet | None, list[Omission]]


def platform_bullet(results: ResultsFolder) -> Built:
    """What Founder Studio covers (metros, business types, data coverage) and the local web checks."""
    draft = BulletDraft(
        "studio_platform",
        STUDIO,
        "Founder Studio, the borrower side of Fundproof (Next.js and FastAPI): site scoring, a Monte Carlo "
        "feasibility check, a path-to-approval search and a lender-ready package",
    )
    ranking = results.load(RANKING)
    metros = dig(ranking, "configured_metros")
    ranked = number_at(ranking, "metropolitan_areas_ranked")
    business_types = dig(results.load(PATHS), "settings", "business_types")
    if dig(ranking, "configured_equals_top_five") is True and isinstance(metros, list) and ranked is not None:
        types_text = f"{count(len(business_types))} business types in " if isinstance(business_types, list) else ""
        draft.add(
            f"covers {types_text}the {count(len(metros))} largest US metro areas by Census Bureau population estimates "
            f"(ranked among {count(ranked)}) plus a SYNTHETIC test city",
            RANKING,
        )
    else:
        draft.omit("supported metros", missing_reason(ranking, RANKING, "configured_equals_top_five"))
    add_coverage(draft, results.load(COVERAGE))
    add_local_web_checks(draft, results.load(WEB))
    return draft.finish()


def add_coverage(draft: BulletDraft, coverage: dict[str, Any] | None) -> None:
    """The range of the lowest data-coverage share per real (public-data) metro."""
    summary = dig(coverage, "summary")
    rows = [row for row in summary if isinstance(row, dict)] if isinstance(summary, list) else []
    public = [number_at(row, "min_coverage_share") for row in rows if row.get("data_label") == "PUBLIC"]
    shares = [share for share in public if share is not None]
    if not shares:
        draft.omit("public-data coverage", missing_reason(coverage, COVERAGE, "summary"))
        return
    draft.add(
        f"in each real metro the lowest data-coverage share of any business type's required factors is between "
        f"{num(min(shares))} and {num(max(shares))}",
        COVERAGE,
    )


def add_local_web_checks(draft: BulletDraft, web: dict[str, Any] | None) -> None:
    """End-to-end flows and axe-core results of the local container run (a local measurement, nothing more)."""
    run = dig(web, "runs", LOCAL_TARGET_KIND)
    flows = dig(run, "e2e", "flows")
    totals = dig(run, "axe", "totals")
    scans = number_at(totals, "scans")
    serious = number_at(totals, "serious_violation_rules")
    critical = number_at(totals, "critical_violation_rules")
    if not isinstance(flows, list) or not flows or scans is None or serious is None or critical is None:
        draft.omit("local web checks", missing_reason(web, WEB, "runs", LOCAL_TARGET_KIND))
        return
    passed = sum(1 for flow in flows if flow.get("passed") is True)
    draft.add(
        f"on a local container, {count(passed)} of {count(len(flows))} Playwright end-to-end flows passed and "
        f"axe-core found {count(serious + critical)} serious or critical violations in {count(scans)} page scans",
        WEB,
    )


def feasibility_bullet(results: ResultsFolder) -> Built:
    """Brier score of the SIMULATED DSCR probability vs two baselines, and band coverage, on misspecified worlds."""
    draft = BulletDraft(
        "studio_feasibility",
        STUDIO,
        "Monte Carlo feasibility simulator for founder plans (probabilities are SIMULATED from stated assumptions)",
    )
    metrics = results.load(FEASIBILITY)
    headline = dig(metrics, "headline")
    rows = [row for row in headline if isinstance(row, dict)] if isinstance(headline, list) else []
    row = next((item for item in rows if item.get("condition") == MISSPECIFIED), None)
    simulator = estimate_at(row, "brier_dscr_year2_simulator")
    single = estimate_at(row, "brier_dscr_year2_single_forecast")
    base_rate = estimate_at(row, "brier_dscr_year2_base_rate_in_sample")
    worlds = number_at(row, "n_worlds")
    year = number_at(metrics, "settings", "dscr_year")
    if simulator is None or single is None or base_rate is None or worlds is None or year is None:
        draft.omit("Brier score on misspecified worlds", missing_reason(metrics, FEASIBILITY, "headline"), core=True)
        return draft.finish()
    draft.add(
        f"on {count(worlds)} SYNTHETIC validation worlds whose truth differs from the model, Brier score of "
        f"P(year-{count(year)} DSCR meets the target) {interval(simulator)} vs {interval(single)} for a single "
        f"most-likely forecast and {interval(base_rate)} for an in-sample base rate",
        FEASIBILITY,
    )
    band = estimate_at(row, "band_coverage_revenue")
    reference = number_at(metrics, "method", "coverage_reference_if_well_specified", "value")
    if band is None or reference is None:
        draft.omit("band coverage", missing_reason(metrics, FEASIBILITY, "method"))
    else:
        draft.add(
            f"P10-P90 revenue bands covered {interval(band)} of months against {num(reference)} expected for a "
            "correct model, so the bands are too narrow when the model is wrong",
            FEASIBILITY,
        )
    return draft.finish()


def provenance_bullet(results: ResultsFolder) -> Built:
    """Narrative numbers traced to the ledger, package numbers read back, and the human checkpoint of the handoff."""
    draft = BulletDraft("studio_provenance", STUDIO, "Provenance-locked founder narrative and lender package")
    evidence = results.load(EVIDENCE)
    traced = dig(evidence, "headline", "narrative_numbers_traced_bound")
    rate = read_estimate(traced)
    hits, total = number_at(traced, "k"), number_at(traced, "n")
    scenarios = number_at(evidence, "golden", "scenarios_run")
    if rate is None or hits is None or total is None or scenarios is None:
        draft.omit("narrative numbers traced", missing_reason(evidence, EVIDENCE, "headline"), core=True)
        return draft.finish()
    # The template writer types every number from the ledger, so its numbers trace by construction (the results
    # file's scope says so); the rate shows the construction holds, not how often a model writes a wrong number.
    if evidence.get("provider") == "template":
        writer = "the template writer and no LLM (by construction for the template)"
    else:
        writer = f"narrative provider {evidence.get('provider')}"
    draft.add(
        f"{count(hits)} of {count(total)} narrative numbers traced to the ledger claim their sentence cites "
        f"({interval(rate)}) across {count(scenarios)} SYNTHETIC scenarios with {writer}",
        EVIDENCE,
    )
    add_handoff(draft, results.load(HANDOFF))
    return draft.finish()


def add_handoff(draft: BulletDraft, handoff: dict[str, Any] | None) -> None:
    """Untraced numbers in the package text and PDFs, and the human checkpoint over every handoff case."""
    golden = dig(handoff, "golden_founder_scenarios")
    read_back = number_at(golden, "package_numbers_read_back")
    untraced_text = number_at(golden, "untraced_numbers_package_text")
    untraced_pdf = number_at(golden, "untraced_numbers_pdf_text")
    if read_back is None or untraced_text is None or untraced_pdf is None:
        draft.omit("package numbers read back", missing_reason(handoff, HANDOFF, "golden_founder_scenarios"))
    else:
        draft.add(
            f"{count(untraced_text + untraced_pdf)} untraced numbers among {count(read_back)} read back from the "
            "package text and PDFs",
            HANDOFF,
        )
    reached = estimate_at(handoff, "all_cases", "reached_human_checkpoint")
    without_human = number_at(handoff, "all_cases", "decisions_recorded_without_a_human")
    if reached is None or reached.n is None or without_human is None or reached.value != 1.0:
        draft.omit("handoff human checkpoint", missing_reason(handoff, HANDOFF, "all_cases"))
        return
    draft.add(
        f"all {count(reached.n)} handoff cases stopped at a human checkpoint and {count(without_human)} decisions "
        "were recorded without a human",
        HANDOFF,
    )


def path_bullet(results: ResultsFolder) -> Built:
    """Share of SYNTHETIC plans needing a change for which the search finds one, with and without program rules."""
    draft = BulletDraft("studio_path", STUDIO, "Path-to-approval search over one- and two-lever plan changes")
    metrics = results.load(PATHS)
    found = estimate_at(metrics, "headline", "path_found_rate_full")
    compliant = estimate_at(metrics, "headline", "path_found_and_program_compliant_rate_full")
    targets = dig(metrics, "settings", "targets")
    if found is None or found.n is None or not isinstance(targets, list):
        draft.omit("path-found rate", missing_reason(metrics, PATHS, "headline", "path_found_rate_full"), core=True)
        return draft.finish()
    draft.add(
        f"found a change meeting all {count(len(targets))} SIMULATED loan targets for {interval(found)} of "
        f"{count(found.n)} SYNTHETIC plans that needed one",
        PATHS,
    )
    if compliant is None:
        draft.omit("program-compliant path rate", missing_reason(metrics, PATHS, "headline"))
    else:
        draft.add(f"{interval(compliant)} when every startup-program rule must also hold", PATHS)
    return draft.finish()


def siteiq_bullet(results: ResultsFolder) -> Built:
    """SiteIQ rank correlation with hidden demand on SYNTHETIC cities, and how it compares with one factor alone."""
    draft = BulletDraft("studio_siteiq", STUDIO, "SiteIQ site scoring from public data")
    metrics = results.load(SITEIQ)
    by_type = dig(metrics, "by_business_type")
    rows = list(by_type.values()) if isinstance(by_type, dict) else []
    correlations = [estimate_at(row, "siteiq_spearman") for row in rows]
    differences = [estimate_at(row, "siteiq_minus_baseline_spearman") for row in rows]
    if not rows or any(item is None for item in correlations + differences):
        draft.omit("SiteIQ rank correlation", missing_reason(metrics, SITEIQ, "by_business_type"), core=True)
        return draft.finish()
    values = [item.value for item in correlations if item is not None]
    better = sum(1 for item in differences if item is not None and item.low > 0)
    worse = sum(1 for item in differences if item is not None and item.high < 0)
    draft.add(
        f"on SYNTHETIC cities with a hidden true demand, Spearman correlation {num(min(values))} to "
        f"{num(max(values))} across {count(len(rows))} business types; better than the single-factor baseline "
        f"for {count(better)}, worse for {count(worse)}, no clear difference for {count(len(rows) - better - worse)} "
        "(paired bootstrap 95% intervals)",
        SITEIQ,
    )
    return draft.finish()


def business_bullet(results: ResultsFolder) -> Built:
    """S12 end to end: printed figures with a checkable source vs the baseline B0, and the time to an audited PDF."""
    draft = BulletDraft(
        "studio_business",
        STUDIO,
        "Founder Studio end-to-end evaluation against a baseline defined before the first run",
    )
    metrics = results.load(BUSINESS)
    studio = estimate_at(metrics, "headline", "evidence_coverage_rule_s", "studio")
    baseline = estimate_at(metrics, "headline", "evidence_coverage_rule_s", "baseline_b0")
    scenarios = number_at(metrics, "headline", "evidence_coverage_rule_s", "studio", "scenarios")
    if studio is None or baseline is None or scenarios is None:
        draft.omit("evidence coverage vs baseline", missing_reason(metrics, BUSINESS, "headline"), core=True)
        return draft.finish()
    draft.add(
        f"{interval(studio)} of printed figures carry a checkable source vs {interval(baseline)} for a plan made "
        f"without the site scoring, the simulation and the ledger, on {count(scenarios)} SYNTHETIC scenarios",
        BUSINESS,
    )
    median = estimate_at(metrics, "headline", "time_to_complete_package_median_s")
    if median is None:
        draft.omit("time to a complete package", missing_reason(metrics, BUSINESS, "headline"))
    else:
        draft.add(
            f"median {interval(median, decimals=3)} seconds from inputs to an audited PDF package (machine "
            "wall clock, not a user time)",
            BUSINESS,
        )
    llm_cost = number_at(metrics, "cost_per_package", "llm_cost_usd_per_package")
    if llm_cost == 0:
        draft.add_context("no language model is called (template narrative and memo)")
    return draft.finish()


def studio_notes(results: ResultsFolder) -> list[str]:
    """Studio results files present in the folder that the bullets deliberately do not quote, and why."""
    notes = []
    ollama = results.load(EVIDENCE_OLLAMA)
    if ollama is not None:
        fallback = dig(ollama, "llm", "fallback_rate")
        hits, total = number_at(fallback, "k"), number_at(fallback, "n")
        detail = "see the file"
        if hits is not None and total is not None:
            detail = f"{count(hits)} of {count(total)} narratives fell back to the template"
        notes.append(f"results/{EVIDENCE_OLLAMA} not quoted: a small local-model run ({detail})")
    return notes

"""Lender Desk bullets for the evidence platform and the document pipeline (Prompt 8 templates 1 and 4).

Honest changes to the templates:
- next-best-evidence "top-3 accuracy" is not quoted: results/evidence_metrics.json labels that agreement CIRCULAR
  (the same author wrote the generator's resolving request and the engine's rules). The non-circular
  value-of-information simulator comparison is quoted instead, including its decision-error cost;
- prompt-injection attack success is quoted per defense layer (L0, L1, L2, Wilson intervals, not-found rate under
  attack) only when results/injection_metrics.json scored every layer. The retired "100% ... blocked" wording is
  never used. The measured tripwire recall and the containment tests are quoted either way.
"""

from typing import Any

from uwcopilot.resume.results import ResultsFolder, dig, estimate_at, missing_reason, number_at, read_estimate
from uwcopilot.resume.text import LENDER, Bullet, BulletDraft, Omission, count, interval, num

AGENT = "agent_metrics.json"
DOCINTEL = "docintel_metrics.json"
EVIDENCE = "evidence_metrics.json"
INJECTION_FULL = "injection_metrics.json"
INJECTION_DETECTOR_ONLY = "injection/injection_metrics_detector_only.json"
INJECTION_LAYERS: tuple[str, ...] = ("L0", "L1", "L2")

Built = tuple[Bullet | None, list[Omission]]


def evidence_bullet(results: ResultsFolder) -> Built:
    """Contradiction detection, the value-of-information request policy, and the human checkpoint."""
    draft = BulletDraft(
        "evidence_platform",
        LENDER,
        "Evidence-driven underwriting copilot (LangGraph agent, FastAPI) for small-business loan and commercial "
        "insurance files",
    )
    docintel = results.load(DOCINTEL)
    recall = estimate_at(docintel, "rules", "contradictions", "recall")
    packets = number_at(docintel, "evaluation_set", "n_packets")
    if recall is None or packets is None:
        reason = missing_reason(docintel, DOCINTEL, "rules", "contradictions", "recall")
        draft.omit("cross-document contradiction recall", reason, core=True)
        return draft.finish()
    draft.add_context("links claims to a source page or a calculation")
    clause = f"cross-document contradiction recall {interval(recall)}"
    precision = estimate_at(docintel, "rules", "contradictions", "precision")
    if precision is None:
        draft.omit("contradiction precision", missing_reason(docintel, DOCINTEL, "rules", "contradictions"))
    else:
        clause += f" and precision {interval(precision)}"
    draft.add(f"{clause} on {count(packets)} SYNTHETIC packets", DOCINTEL)
    add_request_policy(draft, results.load(EVIDENCE))
    add_human_checkpoint(draft, results.load(AGENT))
    return draft.finish()


def add_request_policy(draft: BulletDraft, evidence: dict[str, Any] | None) -> None:
    """The value-of-information policy vs the fixed checklist (requests per case and decision errors)."""
    circular = dig(evidence, "packets", "results", "modes", "oracle", "next_best_evidence_agreement_circular")
    label = dig(circular, "label") if isinstance(circular, dict) else None
    draft.omit(
        "next-best-evidence top-3 accuracy",
        f"not used: results/{EVIDENCE} labels it {label.split(':')[0]}"
        if isinstance(label, str)
        else f"not used: no non-circular top-3 result in results/{EVIDENCE}",
    )
    headline = dig(evidence, "simulator", "results", "headline")
    requests = dig(headline, "voi_minus_checklist", "n_requests")
    difference = read_estimate(requests)
    voi, checklist = number_at(requests, "mean_first"), number_at(requests, "mean_second")
    cases = number_at(headline, "n_cases")
    comparison = dig(headline, "comparison")
    if (
        difference is None
        or voi is None
        or checklist is None
        or cases is None
        or not isinstance(comparison, str)
        or not comparison.startswith("voi minus checklist")
    ):
        draft.omit("value-of-information request policy", missing_reason(evidence, EVIDENCE, "simulator", "results"))
        return
    clause = (
        f"a value-of-information request policy asked {num(voi)} evidence requests per case vs {num(checklist)} for "
        f"a fixed checklist (difference {interval(difference, with_sign=True)}, {count(cases)} SIMULATED cases)"
    )
    errors = dig(headline, "voi_minus_checklist", "decision_error")
    error_difference = read_estimate(errors)
    voi_errors, checklist_errors = number_at(errors, "mean_first"), number_at(errors, "mean_second")
    if error_difference is None or voi_errors is None or checklist_errors is None:
        draft.omit("decision error of the request policy", missing_reason(evidence, EVIDENCE, "simulator"))
    else:
        clause += (
            f" with a decision-error rate of {num(voi_errors)} vs {num(checklist_errors)} "
            f"(difference {interval(error_difference, with_sign=True)})"
        )
    draft.add(clause, EVIDENCE)


def add_human_checkpoint(draft: BulletDraft, agent: dict[str, Any] | None) -> None:
    """Applications that stopped at the human checkpoint, and decisions recorded without a human."""
    reached = estimate_at(agent, "deterministic_path", "reached_human_checkpoint")
    without_human = number_at(agent, "deterministic_path", "decisions_recorded_without_a_human")
    if reached is None or reached.n is None or without_human is None:
        draft.omit("human checkpoint", missing_reason(agent, AGENT, "deterministic_path"))
        return
    if reached.value == 1.0:
        draft.add(
            f"all {count(reached.n)} SYNTHETIC applications stopped at a human checkpoint and "
            f"{count(without_human)} decisions were recorded without a human",
            AGENT,
        )
    else:
        draft.add(
            f"{interval(reached)} of {count(reached.n)} SYNTHETIC applications stopped at a human checkpoint; "
            f"{count(without_human)} decisions were recorded without a human",
            AGENT,
        )


def documents_bullet(results: ResultsFolder) -> Built:
    """Field F1 and cited-page support of the document pipeline, then the injection measurements."""
    draft = BulletDraft("document_extraction", LENDER, "Document extraction with page citations on SYNTHETIC packets")
    docintel = results.load(DOCINTEL)
    f1 = estimate_at(docintel, "rules", "fields", "overall", "f1")
    packets = number_at(docintel, "evaluation_set", "n_packets")
    if f1 is None or packets is None:
        draft.omit("field F1", missing_reason(docintel, DOCINTEL, "rules", "fields", "overall", "f1"), core=True)
        return draft.finish()
    if dig(docintel, "run", "provider") is None and number_at(docintel, "rules", "cost", "llm_calls") == 0:
        draft.add_context("rules extractor and OCR, no LLM call in the measured run")
    draft.add(f"field F1 {interval(f1)} on {count(packets)} packets", DOCINTEL)
    supported = estimate_at(docintel, "rules", "citations", "supported_all_values")
    if supported is None:
        draft.omit("citation accuracy", missing_reason(docintel, DOCINTEL, "rules", "citations"))
    else:
        draft.add(
            f"{interval(supported)} of extracted values supported by the printed text of their cited page", DOCINTEL
        )
    add_injection(draft, results)
    return draft.finish()


def add_injection(draft: BulletDraft, results: ResultsFolder) -> None:
    """Attack success by layer (only from a full run), the tripwire recall and the containment tests."""
    full = results.load(INJECTION_FULL)
    detector_only = results.load(INJECTION_DETECTOR_ONLY)
    add_attack_success(draft, full, detector_only)
    source_name = INJECTION_FULL if full is not None else INJECTION_DETECTOR_ONLY
    source = full if full is not None else detector_only
    tripwire = dig(source, "detector", "recall_normalized", "all")
    recall = read_estimate(tripwire)
    caught, total = number_at(tripwire, "successes"), number_at(tripwire, "n")
    if recall is None or caught is None or total is None:
        draft.omit("injection tripwire recall", missing_reason(source, source_name, "detector"))
    else:
        draft.add(
            f"a pattern tripwire flags {count(caught)} of {count(total)} frozen attack payloads ({interval(recall)})",
            source_name,
        )
    status = dig(source, "action_level", "status")
    tests = dig(source, "action_level", "tests")
    if isinstance(status, str) and status.startswith("enforced by") and isinstance(tests, list) and tests:
        draft.add(
            "injected text cannot change the recommendation by construction (the policy never reads free text), "
            f"enforced by {count(len(tests))} containment tests",
            source_name,
        )
    else:
        draft.omit("decision-level impact of injections", missing_reason(source, source_name, "action_level"))


def add_attack_success(draft: BulletDraft, full: dict[str, Any] | None, detector_only: dict[str, Any] | None) -> None:
    """Attack success rate per defense layer with the not-found rate under attack, from a full scored run only."""
    part = "prompt-injection attack success by defense layer"
    if full is None:
        reason = f"not measured: results/{INJECTION_FULL} does not exist"
        if detector_only is not None:
            layers = dig(detector_only, "layers") or {}
            reason += (
                f"; results/{INJECTION_DETECTOR_ONLY} has status '{detector_only.get('status')}' and layers "
                + ", ".join(f"{name}: {layers.get(name)}" for name in INJECTION_LAYERS)
            )
        draft.omit(part, reason)
        return
    blocks = {name: dig(full, "layers", name) for name in INJECTION_LAYERS}
    rates = {name: read_estimate(dig(block, "asr", "all")) for name, block in blocks.items()}
    if full.get("status") != "measured" or any(rate is None for rate in rates.values()):
        draft.omit(part, f"not measured: results/{INJECTION_FULL} has status '{full.get('status')}'")
        return
    pieces = []
    for name in INJECTION_LAYERS:
        piece = f"{name} {interval(rates[name])}"
        not_found = read_estimate(dig(blocks[name], "under_attack", "not_found"))
        if not_found is not None:
            piece += f" with not-found rate {interval(not_found)}"
        pieces.append(piece)
    scored = number_at(blocks["L0"], "n_scored")
    payloads = f" on {count(scored)} frozen payloads" if scored is not None else ""
    draft.add(f"prompt-injection attack success by defense layer{payloads}: {'; '.join(pieces)}", INJECTION_FULL)

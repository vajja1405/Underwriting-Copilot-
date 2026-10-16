"""Stress tests, counterfactuals and decision stability (Phase 4 C), judged by the reference policy.

For each scenario (the mechanics are in scenario_levers.py) the engine:
1. applies the scenario's lever substitutions to the case state (evidence.state.apply; the application is
   validated by parse_application);
2. runs the SAME reference policy with the SAME PolicyInputs (evidence.state.evaluate);
3. compares with the baseline: the decision metric (decision DSCR for a loan, CALC-dscr; the projected DSCR for a
   startup loan, CALC-startup_projected_dscr; the highest 5-year loss ratio of the quoted lines for insurance,
   CALC-loss_ratio_5yr_*), the recommendation, and the drivers: the trigger ids and finding rules that appear or
   disappear and the calculations whose value changes.

Risk band and model PD / expected loss are "not evaluated": no Phase 1 model is trained on the features of these
SYNTHETIC commercial cases. For the same reason "SHAP on the stressed input" is not evaluated; the drivers are the
deterministic rule and calculation differences instead.

decision_stability(results) reports, per scenario family (revenue, rate, frequency, severity), the smallest tested
shock that flips the reference recommendation, or "stable across all tested scenarios". Counterfactuals (stated
revenue := verified, stated TIV := schedule TIV) are not shocks: they are listed separately when they change the
recommendation. Note the policy's precedence: a blocking finding (sanctions, embedded instructions, identity,
loss-run integrity) keeps the recommendation at "refer" whatever a shock does, so such a case reads as stable.

A shock counts as TESTED only when it could have shown something. It is "not_evaluated" (with not_evaluated_code
and the reason) when it cannot be built (not_applicable), cannot be applied (not_applied), changes nothing (no_change:
a frequency shock that rounds to zero whole claims, or a severity shock with no claims to scale), or leaves the
decision metric not computable before AND after while the recommendation stays the same (metric_not_computable: no
verified revenue for a DSCR, no loss ratio for a line). Such a shock keeps its deltas for the record, but it never
makes a case read as "stable". The last two rules do not apply to counterfactuals, which are reported apart.

All scenarios are what-ifs on SYNTHETIC data, not predictions.
"""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from uwcopilot.agent.policy import PolicyResult
from uwcopilot.docintel.levers import LeverError
from uwcopilot.evidence import scenario_levers as levers
from uwcopilot.evidence.state import CaseState, IncompleteApplicationError, apply, evaluate

NOT_EVALUATED_BAND = "not evaluated (no applicable model)"
SHAP_NOT_EVALUATED = (
    "not evaluated: no Phase 1 model applies to these SYNTHETIC commercial cases, so there is no SHAP on the stressed "
    "input; the drivers are the trigger, rule and calculation differences"
)
STABLE_TEXT = "stable across all tested scenarios"
CREDIT_METRIC = ("decision DSCR", "CALC-dscr", "ratio")
STARTUP_METRIC = ("projected DSCR", "CALC-startup_projected_dscr", "ratio")
LOSS_RATIO_CALCS = ("CALC-loss_ratio_5yr_property", "CALC-loss_ratio_5yr_general_liability")
INSURANCE_METRIC_NAME = "5-year loss ratio"
VERIFIED_REVENUE_CALC = "CALC-verified_annual_revenue"
SCHEDULE_TIV_CALC = "CALC-schedule_tiv"
SHOCK_FAMILIES: tuple[str, ...] = ("revenue", "rate", "frequency", "severity")
# Why a scenario was not tested (ScenarioResult.not_evaluated_code); see the module docstring.
NOT_APPLICABLE = "not_applicable"
NOT_APPLIED = "not_applied"
NO_CHANGE = "no_change"
METRIC_NOT_COMPUTABLE = "metric_not_computable"
NO_CHANGE_REASON = "the scenario changes nothing in the application, so it equals the baseline"
METRIC_REASON = (
    "the {metric} is not computable before or after the scenario and the recommendation is unchanged, so the "
    "scenario cannot show how stable the decision is"
)


@dataclass(frozen=True)
class ScenarioSpec:
    """One tested scenario: id, family, size (a share, or basis points for rate) and label."""

    scenario_id: str
    family: str
    magnitude: float
    label: str
    is_shock: bool = True


CREDIT_SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec("revenue_minus_10", "revenue", 0.10, "revenue -10%"),
    ScenarioSpec("revenue_minus_20", "revenue", 0.20, "revenue -20%"),
    ScenarioSpec("revenue_minus_30", "revenue", 0.30, "revenue -30%"),
    ScenarioSpec("rate_plus_200bps", "rate", 200.0, "rate +200 bps"),
    ScenarioSpec("rate_plus_400bps", "rate", 400.0, "rate +400 bps"),
    ScenarioSpec("stated_revenue_is_verified", "counterfactual", 0.0, "stated revenue := verified", False),
)
STARTUP_SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec("rate_plus_200bps", "rate", 200.0, "rate +200 bps"),
    ScenarioSpec("rate_plus_400bps", "rate", 400.0, "rate +400 bps"),
)
INSURANCE_SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec("frequency_plus_10", "frequency", 0.10, "claim frequency +10%"),
    ScenarioSpec("frequency_plus_25", "frequency", 0.25, "claim frequency +25%"),
    ScenarioSpec("severity_plus_10", "severity", 0.10, "claim severity +10%"),
    ScenarioSpec("severity_plus_25", "severity", 0.25, "claim severity +25%"),
    ScenarioSpec("stated_tiv_is_schedule", "counterfactual", 0.0, "stated TIV := schedule TIV", False),
)
SCENARIOS_BY_LINE: dict[str, tuple[ScenarioSpec, ...]] = {
    "credit": CREDIT_SCENARIOS,
    "startup_credit": STARTUP_SCENARIOS,
    "insurance": INSURANCE_SCENARIOS,
}


class Frozen(BaseModel):
    """Base for scenario models: unknown fields are an error and objects are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DecisionMetric(Frozen):
    """The figure the recommendation mostly turns on, read from a policy result."""

    name: str
    calc_id: str | None
    value: float | None
    unit: str


class CalcDelta(Frozen):
    """A calculation whose value differs between the baseline and the scenario."""

    calc_id: str
    baseline: float | None
    scenario: float | None


class ScenarioResult(Frozen):
    """One scenario's outcome next to the baseline (see the module docstring)."""

    scenario_id: str
    family: str
    label: str
    magnitude: float
    is_shock: bool
    status: str  # "evaluated" | "not_evaluated"
    reason: str | None = None
    not_evaluated_code: str | None = None  # not_applicable | not_applied | no_change | metric_not_computable
    notes: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    metric_name: str
    metric_calc_id: str | None
    baseline_metric: float | None = None
    scenario_metric: float | None = None
    baseline_recommendation: str
    recommendation: str | None = None
    recommendation_changed: bool = False
    risk_band: str = NOT_EVALUATED_BAND
    drivers_note: str = SHAP_NOT_EVALUATED
    triggers_added: tuple[str, ...] = ()
    triggers_removed: tuple[str, ...] = ()
    rules_added: tuple[str, ...] = ()
    rules_removed: tuple[str, ...] = ()
    calc_deltas: tuple[CalcDelta, ...] = ()


class FamilyStability(Frozen):
    """Decision stability within one shock family (shocks ordered by magnitude)."""

    family: str
    tested: tuple[str, ...]
    not_evaluated: tuple[str, ...] = ()
    first_flip: str | None = None
    first_flip_label: str | None = None
    flips_to: str | None = None


class DecisionStability(Frozen):
    """The smallest tested shock that flips the recommendation in each family, or stable across all of them."""

    baseline_recommendation: str
    stable: bool | None
    summary: str
    families: tuple[FamilyStability, ...] = ()
    counterfactual_changes: tuple[str, ...] = Field(default_factory=tuple)


# --------------------------------------------------------------------------------------------------------------
# Reading a policy result
# --------------------------------------------------------------------------------------------------------------


def calc_values(result: PolicyResult) -> dict[str, float | None]:
    """calc id -> value of every calculation in a policy result."""
    return {item.calc_id: item.value for item in result.calculations}


def decision_metric(result: PolicyResult) -> DecisionMetric:
    """The line's decision metric (module docstring). Insurance: the highest quoted line's 5-year loss ratio (%)."""
    values = calc_values(result)
    if result.line in ("credit", "startup_credit"):
        name, calc_id, unit = CREDIT_METRIC if result.line == "credit" else STARTUP_METRIC
        return DecisionMetric(name=name, calc_id=calc_id, value=values.get(calc_id), unit=unit)
    present = [(values[calc_id], calc_id) for calc_id in LOSS_RATIO_CALCS if values.get(calc_id) is not None]
    if not present:
        return DecisionMetric(name=INSURANCE_METRIC_NAME, calc_id=None, value=None, unit="pct")
    value, calc_id = max(present)
    return DecisionMetric(name=INSURANCE_METRIC_NAME, calc_id=calc_id, value=value, unit="pct")


def fired_triggers(result: PolicyResult) -> set[str]:
    """Every trigger id of the findings."""
    return {trigger for finding in result.findings for trigger in finding.trigger_ids}


def calc_deltas(baseline: PolicyResult, scenario: PolicyResult) -> list[CalcDelta]:
    """Calculations whose value differs (present in either result), in the scenario's order then the baseline's."""
    before, after = calc_values(baseline), calc_values(scenario)
    ordered = list(after) + [calc_id for calc_id in before if calc_id not in after]
    return [
        CalcDelta(calc_id=calc_id, baseline=before.get(calc_id), scenario=after.get(calc_id))
        for calc_id in ordered
        if before.get(calc_id) != after.get(calc_id)
    ]


# --------------------------------------------------------------------------------------------------------------
# Running scenarios
# --------------------------------------------------------------------------------------------------------------


def scenario_plan(spec: ScenarioSpec, state: CaseState, baseline: PolicyResult) -> levers.ScenarioPlan:
    """The lever substitutions of a scenario (ScenarioNotApplicable when it cannot be built)."""
    values = calc_values(baseline)
    application = state.application
    if spec.family == "revenue":
        return levers.revenue_shock_plan(application, values.get(VERIFIED_REVENUE_CALC), spec.magnitude)
    if spec.family == "rate":
        return levers.rate_shock_plan(application, spec.magnitude)
    if spec.family == "frequency":
        return levers.frequency_plan(application, spec.magnitude)
    if spec.family == "severity":
        return levers.severity_plan(application, spec.magnitude)
    if spec.scenario_id == "stated_revenue_is_verified":
        return levers.stated_revenue_plan(values.get(VERIFIED_REVENUE_CALC))
    if spec.scenario_id == "stated_tiv_is_schedule":
        return levers.stated_tiv_plan(values.get(SCHEDULE_TIV_CALC))
    raise ValueError(f"unknown scenario {spec.scenario_id}")


def not_evaluated(spec: ScenarioSpec, baseline: PolicyResult, reason: str, code: str) -> ScenarioResult:
    """A scenario that could not be built or evaluated, with the reason and its code."""
    metric = decision_metric(baseline)
    return ScenarioResult(
        scenario_id=spec.scenario_id,
        family=spec.family,
        label=spec.label,
        magnitude=spec.magnitude,
        is_shock=spec.is_shock,
        status="not_evaluated",
        reason=reason,
        not_evaluated_code=code,
        metric_name=metric.name,
        metric_calc_id=metric.calc_id,
        baseline_metric=metric.value,
        baseline_recommendation=baseline.recommendation,
    )


def compare(
    spec: ScenarioSpec, plan: levers.ScenarioPlan, baseline: PolicyResult, scenario: PolicyResult
) -> ScenarioResult:
    """The evaluated scenario next to the baseline."""
    before, after = decision_metric(baseline), decision_metric(scenario)
    triggers_before, triggers_after = fired_triggers(baseline), fired_triggers(scenario)
    rules_before, rules_after = set(baseline.rule_codes()), set(scenario.rule_codes())
    return ScenarioResult(
        scenario_id=spec.scenario_id,
        family=spec.family,
        label=spec.label,
        magnitude=spec.magnitude,
        is_shock=spec.is_shock,
        status="evaluated",
        notes=tuple(plan.notes),
        changed_paths=tuple(plan.substitutions),
        metric_name=before.name,
        metric_calc_id=after.calc_id or before.calc_id,
        baseline_metric=before.value,
        scenario_metric=after.value,
        baseline_recommendation=baseline.recommendation,
        recommendation=scenario.recommendation,
        recommendation_changed=scenario.recommendation != baseline.recommendation,
        triggers_added=tuple(sorted(triggers_after - triggers_before)),
        triggers_removed=tuple(sorted(triggers_before - triggers_after)),
        rules_added=tuple(sorted(rules_after - rules_before)),
        rules_removed=tuple(sorted(rules_before - rules_after)),
        calc_deltas=tuple(calc_deltas(baseline, scenario)),
    )


def untested(result: ScenarioResult, code: str, reason: str) -> ScenarioResult:
    """An evaluated scenario that could not show anything, marked not_evaluated (its deltas are kept)."""
    return result.model_copy(update={"status": "not_evaluated", "not_evaluated_code": code, "reason": reason})


def run_scenario(spec: ScenarioSpec, state: CaseState, baseline: PolicyResult) -> ScenarioResult:
    """Build, apply and evaluate one scenario (not_evaluated with the reason when it cannot be built or applied, or
    when it cannot show anything: module docstring)."""
    try:
        plan = scenario_plan(spec, state, baseline)
    except levers.ScenarioNotApplicable as error:
        return not_evaluated(spec, baseline, str(error), NOT_APPLICABLE)
    try:
        scenario = evaluate(apply(state, plan.substitutions))
    except (LeverError, IncompleteApplicationError) as error:
        reason = f"the scenario could not be applied: {type(error).__name__}"
        return not_evaluated(spec, baseline, reason, NOT_APPLIED)
    result = compare(spec, plan, baseline, scenario)
    if not spec.is_shock:  # a counterfactual is reported apart from stability, whatever its metric
        return result
    if not plan.substitutions:
        notes = "; ".join(plan.notes)
        return untested(result, NO_CHANGE, f"{NO_CHANGE_REASON} ({notes})" if notes else NO_CHANGE_REASON)
    if result.baseline_metric is None and result.scenario_metric is None and not result.recommendation_changed:
        return untested(result, METRIC_NOT_COMPUTABLE, METRIC_REASON.format(metric=result.metric_name))
    return result


def scenario_specs(line: str) -> tuple[ScenarioSpec, ...]:
    """The scenarios tested for a line."""
    return SCENARIOS_BY_LINE[line]


def run_scenarios(state: CaseState, baseline: PolicyResult | None = None) -> list[ScenarioResult]:
    """Every scenario of the case's line, in the documented order (baseline defaults to evaluate(state))."""
    reference = baseline if baseline is not None else evaluate(state)
    return [run_scenario(spec, state, reference) for spec in scenario_specs(reference.line)]


# --------------------------------------------------------------------------------------------------------------
# Decision stability
# --------------------------------------------------------------------------------------------------------------


def family_stability(family: str, results: list[ScenarioResult]) -> FamilyStability:
    """The first (smallest) evaluated shock of a family that changes the recommendation."""
    ordered = sorted(results, key=lambda item: item.magnitude)
    tested = [item for item in ordered if item.status == "evaluated"]
    skipped = tuple(item.scenario_id for item in ordered if item.status != "evaluated")
    flip = next((item for item in tested if item.recommendation_changed), None)
    return FamilyStability(
        family=family,
        tested=tuple(item.scenario_id for item in tested),
        not_evaluated=skipped,
        first_flip=flip.scenario_id if flip else None,
        first_flip_label=flip.label if flip else None,
        flips_to=flip.recommendation if flip else None,
    )


def stability_summary(baseline: str, families: list[FamilyStability]) -> tuple[bool | None, str]:
    """(stable, summary text) over the families."""
    tested = [family for family in families if family.tested]
    skipped = [name for family in families for name in family.not_evaluated]
    suffix = f" (not tested: {', '.join(skipped)})" if skipped else ""
    if not tested:
        return None, "not evaluated: no stress scenario could be tested" + suffix
    flips = [family for family in tested if family.first_flip]
    if not flips:
        return True, STABLE_TEXT + suffix
    parts = [f"{family.first_flip_label} ({baseline} -> {family.flips_to})" for family in flips]
    return False, "smallest flipping shock per family: " + "; ".join(parts) + suffix


def decision_stability(results: list[ScenarioResult], baseline_recommendation: str) -> DecisionStability:
    """Stability over the shock families; counterfactuals that change the recommendation are listed apart."""
    families = []
    for family in SHOCK_FAMILIES:
        members = [item for item in results if item.family == family]
        if members:
            families.append(family_stability(family, members))
    stable, summary = stability_summary(baseline_recommendation, families)
    changes = tuple(
        item.scenario_id
        for item in results
        if not item.is_shock and item.status == "evaluated" and item.recommendation_changed
    )
    return DecisionStability(
        baseline_recommendation=baseline_recommendation,
        stable=stable,
        summary=summary,
        families=tuple(families),
        counterfactual_changes=changes,
    )

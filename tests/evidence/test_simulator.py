"""Tests of the evidence-request simulator (innovation S5): reveals, policies, stopping rules, the oracle, priors
variants, determinism, summaries and the simulate_requests script.

Cases are JSON-only generated packets with master seed 4040 (ids PKT-*-401..440 from evidence_case_helpers, and
PKT-*-001..012 for the script's split tests): outside the simulator's own cases (seed 4242), so no test touches the
simulator's development or test split. The ground
truth is used to choose packets, as the simulator itself does. Everything is SYNTHETIC.
"""

import ast
import json
import random
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
from evidence_case_helpers import CASE_NUMBERS, LINE_PREFIX, application_dict, generated_truth, single_problem_packet

import simulate_requests
from uwcopilot.agent.applications import parse_application
from uwcopilot.common.paths import REPO_ROOT
from uwcopilot.docintel.truth import PacketTruth
from uwcopilot.evidence import simulator
from uwcopilot.evidence.open_item_models import make_item
from uwcopilot.evidence.requests import ResolutionChoice, cost_weight
from uwcopilot.evidence.simulator import (
    Position,
    adjusted_choices,
    advance,
    empirical_choices,
    open_requests,
    oracle_path,
    request_key,
    run_context,
    simulate,
    start,
    variant_choices,
)
from uwcopilot.evidence.simulator_cases import SimCase, full_information_state, latent_option_for, prepare_case
from uwcopilot.evidence.simulator_diagnostics import (
    case_row,
    coupling_summary,
    is_coupled,
    latent_shares,
    latent_vs_prior,
    latent_without_prior,
    smoothed_shares,
    template_key,
)
from uwcopilot.evidence.simulator_models import SimulationResult, requests_to_stable
from uwcopilot.evidence.simulator_summary import decision_errors_by_kind, paired, policy_metrics, ratio_ci
from uwcopilot.evidence.state import apply, evaluate, from_application
from uwcopilot.evidence.voi import request_values, resolution_choices

EVIDENCE_DIR = REPO_ROOT / "src" / "uwcopilot" / "evidence"
# Packets chosen from the seed-4040 set by their ground truth (see the scan in each test's docstring).
THREE_REQUESTS_TWO_NEEDED = ("PKT-I-434", "insurance")  # 3 open requests; the oracle needs 2 of them
THREE_REQUESTS_OTHER = ("PKT-I-436", "insurance")
STOPS_WITH_REQUESTS_LEFT = ("PKT-C-403", "credit")  # voi makes one request, then every open request has VOI 0


@cache
def prepared(packet_id: str, line: str) -> SimCase:
    """The simulator case of one generated packet (cached: preparation is deterministic)."""
    return prepare_case(generated_truth(packet_id, line))


def truth_of(packet: tuple[str, str]) -> PacketTruth:
    """The truth of a (packet id, line) pair."""
    return generated_truth(*packet)


def run(packet: tuple[str, str], policy: str, variant: str = "documented", seed: int | None = None) -> SimulationResult:
    """One simulated run on a prepared case."""
    case = prepared(*packet)
    rng = random.Random(seed) if seed is not None else None
    return simulate(case.truth, policy, variant, rng, prepared=case)


def replay(case: SimCase, result: SimulationResult) -> Position:
    """The position a run ended at, rebuilt by requesting its steps' items in order."""
    position = start(case)
    for step in result.steps:
        _, groups = open_requests(case, position)
        group = next(group for group in groups if tuple(item.item_id for item in group) == step.item_ids)
        position = advance(case, position, group)
    return position


def brute_force_cheapest(case: SimCase, position: Position, target: str) -> tuple[float, int] | None:
    """(cost, number of requests) of the cheapest request order that reaches target, by trying EVERY order.

    Independent of the simulator's uniform-cost search: plain recursion over every open request at every step.
    """
    if position.result.recommendation == target:
        return (0.0, 0)
    best: tuple[float, int] | None = None
    _, groups = open_requests(case, position)
    for group in groups:
        rest = brute_force_cheapest(case, advance(case, position, group), target)
        if rest is None:
            continue
        candidate = (round(cost_weight(str(group[0].request_id)) + rest[0], 6), rest[1] + 1)
        if best is None or candidate < best:
            best = candidate
    return best


# --------------------------------------------------------------------------------------------------------------
# Reveals
# --------------------------------------------------------------------------------------------------------------


def test_revealing_every_planted_problem_rebuilds_the_generators_latent_application() -> None:
    """The start state is application_as_documented and the full-information state is application_latent, on
    every seed-4040 packet (both validated by the agent's models)."""
    for line in ("credit", "insurance"):
        for number in CASE_NUMBERS:
            case = prepared(f"PKT-{LINE_PREFIX[line]}-{number}", line)
            start_app = parse_application(case.initial.application).model_dump(mode="json")
            full_app = parse_application(full_information_state(case).application).model_dump(mode="json")
            assert start_app == parse_application(case.truth.application_as_documented).model_dump(mode="json")
            assert full_app == parse_application(case.truth.application_latent).model_dump(mode="json")


def test_the_state_after_several_requests_does_not_depend_on_their_order() -> None:
    case = prepared(*THREE_REQUESTS_TWO_NEEDED)
    first = start(case)
    _, groups = open_requests(case, first)
    assert len(groups) == 3
    forward = first
    for group in groups:
        forward = advance(case, forward, group)
    backward = first
    for group in reversed(groups):
        backward = advance(case, backward, group)
    assert forward.state.application == backward.state.application
    assert set(forward.revealed) == set(backward.revealed)
    assert forward.result.recommendation == backward.result.recommendation


# --------------------------------------------------------------------------------------------------------------
# Stopping rules
# --------------------------------------------------------------------------------------------------------------


def test_voi_stops_when_no_open_request_has_a_value_above_zero() -> None:
    """PKT-C-403 (revenue gap + FEIN mismatch): one request has value; after it, the other has none."""
    case = prepared(*STOPS_WITH_REQUESTS_LEFT)
    result = run(STOPS_WITH_REQUESTS_LEFT, "voi")
    assert result.stop_reason == "no_nonzero_voi"
    assert result.n_requests == 1
    assert all(step.voi_before > 0 for step in result.steps)
    final = replay(case, result)
    items, groups = open_requests(case, final)
    assert groups, "the stop must leave open requests behind for this case"
    assert all(value.voi == 0 for value in request_values(final.state, items, final.result))


def test_checklist_and_random_stop_only_when_no_open_request_is_left() -> None:
    case = prepared(*STOPS_WITH_REQUESTS_LEFT)
    for policy, seed in (("checklist", None), ("random", 3)):
        result = run(STOPS_WITH_REQUESTS_LEFT, policy, seed=seed)
        assert result.stop_reason == "list_exhausted"
        assert result.n_requests == 2
        _, groups = open_requests(case, replay(case, result))
        assert groups == []


def test_debt_payment_mismatch_is_a_zero_value_request_voi_skips_and_the_checklist_makes() -> None:
    truth = single_problem_packet("credit", "debt_payment_mismatch")
    packet = (truth.packet_id, "credit")
    voi_run = run(packet, "voi")
    assert voi_run.n_requests == 0
    assert voi_run.stop_reason == "no_nonzero_voi"
    checklist_run = run(packet, "checklist")
    assert [step.request_id for step in checklist_run.steps] == ["debt_schedule_update"]
    step = checklist_run.steps[0]
    assert step.voi_before == 0.0
    assert step.recommendation_before == step.recommendation_after
    assert step.zero_value
    assert checklist_run.n_zero_value_requests == 1
    assert checklist_run.decision_error is False and voi_run.decision_error is False


def test_requests_to_stable_counts_requests_until_the_target_is_reached_and_kept() -> None:
    assert requests_to_stable(["approve"], "approve") == 0
    assert requests_to_stable(["refer", "approve", "approve"], "approve") == 1
    assert requests_to_stable(["refer", "approve", "refer"], "refer") == 2
    assert requests_to_stable(["refer", "approve"], "refer") is None
    assert requests_to_stable([], "refer") is None


# --------------------------------------------------------------------------------------------------------------
# Oracle
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("packet", [THREE_REQUESTS_TWO_NEEDED, THREE_REQUESTS_OTHER])
def test_the_oracle_equals_a_brute_force_search_over_every_request_order(packet: tuple[str, str]) -> None:
    case = prepared(*packet)
    context = run_context(case)
    assert context.n_open_requests == 3
    assert context.first.result.recommendation != context.target
    expected = brute_force_cheapest(case, context.first, context.target)
    assert expected is not None and expected[1] == 2
    search = oracle_path(case, context.first, context.target)
    assert not search.guard_reached and search.path is not None
    result = run(packet, "oracle")
    assert result.stop_reason == "oracle_reached"
    assert (result.cost_weighted_requests, result.n_requests) == expected
    assert result.final_recommendation == context.target
    assert result.decision_error is False
    assert replay(case, result).result.recommendation == context.target


def test_the_oracle_makes_no_request_when_the_start_already_has_the_full_information_recommendation() -> None:
    truth = single_problem_packet("credit", "debt_payment_mismatch")
    result = run((truth.packet_id, "credit"), "oracle")
    assert result.initial_recommendation == result.full_information_recommendation
    assert result.n_requests == 0 and result.stop_reason == "oracle_reached"


def test_the_oracle_is_not_computed_above_the_open_request_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(simulator, "ORACLE_MAX_OPEN_REQUESTS", 2)
    result = run(THREE_REQUESTS_TWO_NEEDED, "oracle")
    assert not result.computed
    assert "3 open requests" in str(result.not_computed_reason)


def test_myopic_voi_can_stop_where_the_oracle_needs_two_requests() -> None:
    """PKT-I-434: each request alone keeps the recommendation, so VOI asks for nothing (a documented limit)."""
    voi_run = run(THREE_REQUESTS_TWO_NEEDED, "voi")
    assert voi_run.n_requests == 0
    assert voi_run.decision_error is True
    assert run(THREE_REQUESTS_TWO_NEEDED, "checklist").decision_error is False


# --------------------------------------------------------------------------------------------------------------
# Priors variants
# --------------------------------------------------------------------------------------------------------------


def choices(*priors: float) -> list[ResolutionChoice]:
    """Options a, b, c, ... with the given priors and no substitutions."""
    return [
        ResolutionChoice(option_id=name, description=name, prior=prior, substitutions={})
        for name, prior in zip("abcdef", priors, strict=False)
    ]


def priors_of(chosen: list[ResolutionChoice]) -> dict[str, float]:
    """{option id: prior}."""
    return {choice.option_id: choice.prior for choice in chosen}


def test_the_latent_prior_is_scaled_or_removed_and_the_priors_renormalized() -> None:
    options = choices(0.6, 0.2, 0.2)
    # x0.5: 0.30, 0.20, 0.20 over 0.70
    assert priors_of(adjusted_choices(options, "a", "latent_x0.5")) == {"a": 0.428571, "b": 0.285714, "c": 0.285714}
    # x1.5: 0.90, 0.20, 0.20 over 1.30
    assert priors_of(adjusted_choices(options, "a", "latent_x1.5")) == {"a": 0.692308, "b": 0.153846, "c": 0.153846}
    # removed: 0.20, 0.20 over 0.40
    assert priors_of(adjusted_choices(options, "a", "latent_removed")) == {"b": 0.5, "c": 0.5}
    assert adjusted_choices(choices(1.0), "a", "latent_removed") == []


def test_variants_change_only_the_options_of_items_that_stand_for_a_planted_problem() -> None:
    case = prepared(*THREE_REQUESTS_TWO_NEEDED)
    items, _ = open_requests(case, start(case))
    assert variant_choices(items, case.truth, "documented") is None
    removed = variant_choices(items, case.truth, "latent_removed")
    assert removed is not None
    for item in items:
        latent = latent_option_for(item, case.truth)
        template_ids = [choice.option_id for choice in resolution_choices(item)[0]]
        if latent is None or latent not in template_ids:
            assert item.item_id not in removed
            continue
        kept = [choice.option_id for choice in removed[item.item_id]]
        assert kept == [option_id for option_id in template_ids if option_id != latent]
        assert round(sum(choice.prior for choice in removed[item.item_id]), 5) == 1.0
    with pytest.raises(ValueError):
        variant_choices(items, case.truth, "latent_x3")


def test_empirical_choices_use_the_development_shares_and_drop_options_never_latent() -> None:
    options = choices(0.6, 0.2, 0.2)
    # Shares a 0.25, b 0.75, c never latent: c leaves the set; a and b renormalized over 1.0.
    assert priors_of(empirical_choices(options, {"a": 0.25, "b": 0.75, "c": 0.0}) or []) == {"a": 0.25, "b": 0.75}
    # Only b was ever latent: b alone with prior 1.
    assert priors_of(empirical_choices(options, {"b": 1.0}) or []) == {"b": 1.0}
    # Shares of options this item does not have are left out before renormalizing: a 0.2 and d 0.6 -> a alone.
    assert priors_of(empirical_choices(options, {"a": 0.2, "d": 0.6}) or []) == {"a": 1.0}
    # No option of the item has a share: None (the documented priors stay).
    assert empirical_choices(options, {"d": 1.0}) is None


def test_the_empirical_variant_changes_only_items_that_stand_for_a_planted_problem() -> None:
    case = prepared(*THREE_REQUESTS_TWO_NEEDED)
    items, _ = open_requests(case, start(case))
    with pytest.raises(ValueError):
        variant_choices(items, case.truth, "empirical_dev")  # the shares are required
    # Shares that make every template's FIRST option the only latent one.
    empirical = {}
    for problem in case.truth.problems:
        first = problem.resolution_options[0].option_id
        empirical[template_key(problem)] = {first: 1.0}
    replaced = variant_choices(items, case.truth, "empirical_dev", {"empirical_dev": empirical})
    assert replaced is not None and replaced
    for item in items:
        if latent_option_for(item, case.truth) is None:
            assert item.item_id not in replaced
    for chosen in replaced.values():
        assert len(chosen) == 1 and chosen[0].prior == 1.0
    with pytest.raises(ValueError):
        simulate(case.truth, "voi", "empirical_dev", prepared=case)
    with pytest.raises(ValueError):  # each table variant needs its own table
        simulate(case.truth, "voi", "oracle_shares", prepared=case, tables={"empirical_dev": empirical})


def test_checklist_requests_do_not_depend_on_the_priors_variant() -> None:
    documented = run(THREE_REQUESTS_OTHER, "checklist")
    case = prepared(*THREE_REQUESTS_OTHER)
    empirical = {template_key(problem): {problem.latent_option_id: 1.0} for problem in case.truth.problems}
    tables = {"empirical_dev": empirical, "empirical_dev_smoothed": empirical, "oracle_shares": empirical}
    for variant in tables:
        table_run = simulate(case.truth, "checklist", variant, prepared=case, tables=tables)
        assert [step.item_ids for step in table_run.steps] == [step.item_ids for step in documented.steps]
    for variant in ("latent_x0.5", "latent_x1.5", "latent_removed"):
        other = run(THREE_REQUESTS_OTHER, "checklist", variant)
        assert [step.item_ids for step in other.steps] == [step.item_ids for step in documented.steps]
        assert other.final_recommendation == documented.final_recommendation


def test_an_unknown_policy_or_variant_is_an_error() -> None:
    truth = truth_of(STOPS_WITH_REQUESTS_LEFT)
    with pytest.raises(ValueError):
        simulate(truth, "greedy")
    with pytest.raises(ValueError):
        simulate(truth, "voi", "latent_x2")
    with pytest.raises(ValueError):
        simulate(truth, "random", rng=None, prepared=prepared(*STOPS_WITH_REQUESTS_LEFT))


# --------------------------------------------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------------------------------------------


def test_runs_are_reproducible_with_the_same_seed() -> None:
    truth = truth_of(THREE_REQUESTS_TWO_NEEDED)
    for policy, seed in (("voi", None), ("voi_lookahead", None), ("checklist", None), ("oracle", None), ("random", 11)):
        first = simulate(truth, policy, rng=random.Random(seed) if seed is not None else None)
        again = simulate(truth, policy, rng=random.Random(seed) if seed is not None else None)
        assert first == again
    tables = simulate_requests.case_tables(
        truth, simulate_requests.development_tables([truth]), simulate_requests.oracle_tables([truth])
    )
    assert set(tables) == {"empirical_dev", "empirical_dev_smoothed", "oracle_shares"}
    assert simulate_requests.run_case(truth, 4040, tables) == simulate_requests.run_case(truth, 4040, tables)


def test_the_random_policy_order_follows_its_seed() -> None:
    orders = {
        tuple(step.request_id for step in run(THREE_REQUESTS_TWO_NEEDED, "random", seed=seed).steps)
        for seed in range(8)
    }
    assert len(orders) > 1
    assert all(len(order) == 3 for order in orders)


def test_the_script_seeds_the_random_policy_per_case() -> None:
    first = simulate_requests.case_rng(4242, "PKT-C-001").random()
    assert first == simulate_requests.case_rng(4242, "PKT-C-001").random()
    assert first != simulate_requests.case_rng(4242, "PKT-C-002").random()


# --------------------------------------------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------------------------------------------


def hand_result(case_id: str, policy: str, cost: float, requests: int, zero: int, error: bool) -> SimulationResult:
    """A hand-built computed run with the metrics the summary reads."""
    return SimulationResult(
        case_id=case_id,
        line="credit",
        split="test",
        problem_types=(),
        policy=policy,
        priors_variant="documented",
        computed=True,
        initial_recommendation="refer",
        full_information_recommendation="approve",
        final_recommendation="refer" if error else "approve",
        stop_reason="list_exhausted",
        n_requests=requests,
        cost_weighted_requests=cost,
        n_zero_value_requests=zero,
        requests_to_stable=None if error else requests,
        decision_error=error,
    )


def test_the_paired_difference_is_the_mean_per_case_difference_on_shared_cases() -> None:
    voi_runs = [
        hand_result("A", "voi", 1.0, 1, 0, False),
        hand_result("B", "voi", 0.0, 0, 0, True),
        hand_result("C", "voi", 2.0, 1, 0, False),
    ]
    checklist_runs = [
        hand_result("A", "checklist", 2.0, 2, 1, False),
        hand_result("B", "checklist", 2.0, 1, 1, False),
        hand_result("C", "checklist", 2.0, 1, 0, False),
        hand_result("D", "checklist", 9.0, 5, 5, False),  # no voi run: left out of the pairs
    ]
    summary = paired(voi_runs, checklist_runs)
    assert summary["n_cases"] == 3
    # cost differences -1, -2, 0 -> mean -1.0
    cost = summary["cost_weighted_requests"]
    assert cost["mean_difference"] == -1.0
    assert cost["ci_low"] <= -1.0 <= cost["ci_high"]
    # decision error differences 0, 1, 0 -> mean 1/3
    assert summary["decision_error"]["mean_difference"] == round(1 / 3, 4)
    # requests to stable: only A and C reached the target in both runs; differences -1 and 0
    assert summary["requests_to_stable"]["n"] == 2
    assert summary["requests_to_stable"]["mean_difference"] == -0.5


def test_the_zero_value_share_is_a_ratio_of_sums() -> None:
    # zero-value requests 0 + 1 + 1 = 2 over requests 1 + 2 + 1 = 4
    share = ratio_ci([0, 1, 1], [1, 2, 1])
    assert share["value"] == 0.5
    assert share["ci_low"] <= 0.5 <= share["ci_high"]
    assert ratio_ci([0, 0], [0, 0])["value"] is None


def test_policy_metrics_count_runs_that_could_not_be_computed() -> None:
    runs = [
        hand_result("A", "voi", 1.0, 1, 0, False),
        SimulationResult(
            case_id="B",
            line="credit",
            split="test",
            problem_types=(),
            policy="voi",
            priors_variant="documented",
            computed=False,
            not_computed_reason="IncompleteApplicationError",
        ),
    ]
    metrics = policy_metrics(runs)
    assert metrics["n_cases"] == 1 and metrics["n_not_computed"] == 1
    assert metrics["decision_error_rate"]["value"] == 0.0


# --------------------------------------------------------------------------------------------------------------
# Isolation and the script
# --------------------------------------------------------------------------------------------------------------


def imported_modules(path: Path) -> set[str]:
    """Every module a file imports (absolute names)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_engine_modules_never_import_the_truth_or_the_simulator() -> None:
    """Only evaluation code (simulator*.py, evaluation*.py) may read the generator's hidden truth."""
    forbidden = ("uwcopilot.docintel.truth", "uwcopilot.docintel.synthetic", "uwcopilot.evidence.simulator")
    engine_files = [path for path in EVIDENCE_DIR.glob("*.py") if not path.name.startswith(("simulator", "evaluation"))]
    assert len(engine_files) > 10
    for path in engine_files:
        bad = [name for name in imported_modules(path) if name.startswith(forbidden)]
        assert not bad, f"{path.name} imports {bad}"


def test_development_cases_never_contain_the_held_out_family() -> None:
    """Seed 4040, 12 insurance cases (6 test): two development cases contain claims_history_mismatch; both are
    skipped, and every other development case is kept."""
    args = simulate_requests.parse_args(
        ["--seed", "4040", "--cases-per-line", "12", "--test-per-line", "6", "--lines", "insurance", "--split", "dev"]
    )
    kept = simulate_requests.build_cases("insurance", args)
    ids = simulate_requests.packet_ids("insurance", 12)
    test_ids = simulate_requests.test_split(ids, 6, 4040, "insurance")
    development = [packet_id for packet_id in ids if packet_id not in test_ids]
    with_family = [
        packet_id
        for packet_id in development
        if simulate_requests.has_held_out_family(generated_truth(packet_id, "insurance"))
    ]
    assert len(with_family) == 2
    assert [truth.packet_id for truth in kept] == [
        packet_id for packet_id in development if packet_id not in with_family
    ]
    assert all(truth.split == "dev" and not simulate_requests.has_held_out_family(truth) for truth in kept)


def test_the_script_merges_its_section_and_keeps_the_others(tmp_path: Path) -> None:
    shared = tmp_path / "evidence_metrics.json"
    shared.write_text(json.dumps({"packets": {"results": {"kept": True}, "metadata": {}}}), encoding="utf-8")
    standalone = tmp_path / "evidence_metrics_simulator.json"
    argv = ["--seed", "4040", "--cases-per-line", "4", "--test-per-line", "2", "--lines", "credit"]
    argv += ["--results", str(shared), "--standalone", str(standalone), "--runs-out", str(tmp_path / "runs.jsonl")]
    assert simulate_requests.main(argv) == 0
    merged = json.loads(shared.read_text(encoding="utf-8"))
    own = json.loads(standalone.read_text(encoding="utf-8"))
    assert merged["packets"]["results"] == {"kept": True}
    assert merged["simulator"]["results"] == own["simulator"]["results"]
    results = own["simulator"]["results"]
    assert results["data_label"] == "SYNTHETIC"
    assert results["design"]["held_out_family"]["problem_type"] == "claims_history_mismatch"
    assert results["case_set"]["credit_test"]["n_cases"] == 2
    lines = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4 * (4 * 7 + 1)  # 4 cases x (4 policies x 7 variants + the oracle once)
    # The table variants' priors are written into the design block before any case is simulated.
    design = results["design"]
    empirical = design["empirical_dev_priors"]
    assert empirical["development_cases"] == 2 and "empirical_dev" in design["priors_variants"]
    for shares in empirical["shares"].values():
        assert round(sum(shares.values()), 3) == 1.0
    for shares in design["empirical_dev_smoothed_priors"]["shares"].values():
        assert all(value > 0 for value in shares.values()) and round(sum(shares.values()), 2) == 1.0
    assert set(design["oracle_shares_priors"]["shares_by_group"]) == {"test_main", "held_out_family", "development"}
    # The in-sample oracle priors never leave a problem's true option out; the coverage of each table is reported.
    coverage = results["diagnostics"]["latent_without_prior"]
    assert coverage["oracle_shares"]["test_main"]["n_true_option_without_prior"] == 0
    assert set(coverage) == {"note", "empirical_dev", "empirical_dev_smoothed", "oracle_shares"}
    assert "NOT capped" in results["design"]["case_cap"]


def test_the_script_refuses_to_write_the_default_files_with_another_design(tmp_path: Path) -> None:
    argv = ["--seed", "4040", "--cases-per-line", "4", "--test-per-line", "2", "--runs-out", str(tmp_path / "r.jsonl")]
    assert simulate_requests.main(argv) == 2
    assert simulate_requests.main([*argv[:6], "--split", "dev", "--results", str(tmp_path / "a.json")]) == 2
    assert not (tmp_path / "r.jsonl").exists()


def test_request_keys_name_the_request_and_its_params() -> None:
    case = prepared(*THREE_REQUESTS_TWO_NEEDED)
    _, groups = open_requests(case, start(case))
    keys = [request_key(group) for group in groups]
    assert len(set(keys)) == len(keys)
    assert all(key.split("|")[0] == group[0].request_id for key, group in zip(keys, groups, strict=True))


# --------------------------------------------------------------------------------------------------------------
# Review findings: the zero-value clause, the oracle's cost weights, diagnostics and error kinds
# --------------------------------------------------------------------------------------------------------------


def positions_with_two_recommendations() -> tuple[Position, Position]:
    """APP-I-008 as documented (refer: 57.6% property loss ratio) and with one claim lowered (approve: 41.4%)."""
    before = from_application(application_dict("APP-I-008"))
    after = apply(
        before,
        {
            "loss_runs.years[2].claims[0].paid": 2_130.0,
            "loss_runs.years[2].claims[0].incurred": 2_130.0,
            "stated_total_incurred": 29_863.0,
        },
    )
    first = Position(answered=(), revealed=(), state=before, result=evaluate(before))
    second = Position(answered=("OI-x",), revealed=("PB-01",), state=after, result=evaluate(after))
    assert (first.result.recommendation, second.result.recommendation) == ("refer", "approve")
    return first, second


def test_a_zero_voi_request_whose_reveal_changes_the_recommendation_is_not_zero_value() -> None:
    first, second = positions_with_two_recommendations()
    item = make_item("paid_reserve_mismatch", "CONFLICTING", request_id="corrected_loss_run", template_key=None)
    pick = simulator.Pick(group=[item], voi=0.0, p_change=0.0)
    changed = simulator.step_after(first, pick, second, None)  # type: ignore[arg-type]
    assert changed.voi_before == 0.0 and changed.recommendation_after != changed.recommendation_before
    assert changed.zero_value is False
    kept = simulator.step_after(first, pick, first, None)  # type: ignore[arg-type]
    assert kept.zero_value is True


@dataclass(frozen=True)
class ToyResult:
    """A policy result with only a recommendation (for the toy request graph)."""

    recommendation: str


def toy_position(answered: tuple[str, ...]) -> Position:
    """A toy position: the target is reached after A alone, or after both B and C."""
    reached = "A" in answered or {"B", "C"} <= set(answered)
    return Position(answered=answered, revealed=(), state=None, result=ToyResult("approve" if reached else "refer"))  # type: ignore[arg-type]


def test_the_oracle_pays_cost_weights_not_request_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    # Toy graph: request A alone reaches the target at cost 3.0; B then C reach it at cost 1.0 + 1.0 = 2.0. The
    # cheapest path (B, C) is LONGER than the shortest one (A): an oracle that paid 1.0 per request would pick A.
    groups = {
        name: [make_item("missing_document", "UNKNOWN", name, request_id=name, template_key=None)] for name in "ABC"
    }
    weights = {"A": 3.0, "B": 1.0, "C": 1.0}

    def toy_open_requests(case: object, position: Position) -> tuple[list, list]:
        open_groups = [groups[name] for name in "ABC" if name not in position.answered]
        return [group[0] for group in open_groups], open_groups

    def toy_advance(case: object, position: Position, group: list) -> Position:
        return toy_position((*position.answered, group[0].request_id))

    monkeypatch.setattr(simulator, "open_requests", toy_open_requests)
    monkeypatch.setattr(simulator, "advance", toy_advance)
    monkeypatch.setattr(simulator, "cost_weight", lambda request_id: weights[request_id])
    search = oracle_path(None, toy_position(()), "approve")  # type: ignore[arg-type]
    assert search.path is not None and [group[0].request_id for group in search.path] == ["B", "C"]


def test_diagnostics_count_latent_options_against_the_documented_priors() -> None:
    truths = [generated_truth(f"PKT-C-{number}", "credit") for number in CASE_NUMBERS]
    table = latent_vs_prior(truths)
    planted = sum(len(truth.problems) for truth in truths)
    assert sum(entry["n_problems"] for entry in table.values()) == planted
    for entry in table.values():
        options = entry["options"]
        assert sum(option["latent_count"] for option in options.values()) == entry["n_problems"]
        assert sum(option["documented_prior"] for option in options.values()) == pytest.approx(1.0, abs=1e-3)


def test_smoothed_shares_add_one_to_every_option_count() -> None:
    table = {
        "fam": {
            "n_problems": 4,
            "options": {
                "a": {"documented_prior": 0.6, "latent_count": 4, "latent_share": 1.0},
                "b": {"documented_prior": 0.4, "latent_count": 0, "latent_share": 0.0},
            },
        }
    }
    assert latent_shares(table) == {"fam": {"a": 1.0, "b": 0.0}}
    # (4 + 1) / (4 + 2) and (0 + 1) / (4 + 2): an option never latent keeps a prior above zero.
    assert smoothed_shares(table) == {"fam": {"a": round(5 / 6, 4), "b": round(1 / 6, 4)}}


def test_latent_without_prior_counts_the_problems_whose_true_option_a_table_leaves_out() -> None:
    truths = [generated_truth(f"PKT-C-{number}", "credit") for number in CASE_NUMBERS]
    planted = sum(len(truth.problems) for truth in truths)
    # Shares measured on the same cases (the oracle_shares construction) never leave a true option out.
    own = latent_shares(latent_vs_prior(truths))
    assert latent_without_prior(truths, own) == {
        "n_problems": planted,
        "n_true_option_without_prior": 0,
        "by_template_option": {},
    }
    # A table that keeps only one option that is NOT the true one of a revenue_gap problem leaves it out.
    gap = next(problem for truth in truths for problem in truth.problems if problem.problem_type == "revenue_gap")
    other = next(option.option_id for option in gap.resolution_options if option.option_id != gap.latent_option_id)
    gaps = [problem for truth in truths for problem in truth.problems if problem.problem_type == "revenue_gap"]
    found = latent_without_prior(truths, {"revenue_gap": {other: 1.0}})
    assert found["n_true_option_without_prior"] == sum(1 for problem in gaps if problem.latent_option_id != other)
    # A template missing from the table keeps the documented priors: nothing is counted.
    assert latent_without_prior(truths, {})["n_true_option_without_prior"] == 0


def test_diagnostics_find_insurance_reveals_that_also_rewrite_the_stated_history() -> None:
    truth = single_problem_packet("insurance", "paid_reserve_mismatch")
    problem = next(item for item in truth.problems if item.problem_type == "paid_reserve_mismatch")
    assert is_coupled(problem) == bool(
        {"stated_claim_count", "stated_total_incurred"} & set(problem.latent_substitutions)
    )
    history = single_problem_packet("insurance", "claims_history_mismatch")
    assert not any(is_coupled(item) for item in history.problems if item.problem_type == "claims_history_mismatch")
    row = case_row(prepared(truth.packet_id, "insurance"))
    assert set(row) >= {"n_planted_problems", "coupled_problem_types", "coupled_reveal_removes_reconciliation_finding"}
    summary = coupling_summary([row])
    assert summary["n_cases"] == 1


def test_decision_errors_are_split_by_kind() -> None:
    def errored(case_id: str, undetected: tuple[str, ...], stop: str) -> SimulationResult:
        return hand_result(case_id, "voi", 0.0, 0, 0, True).model_copy(
            update={"undetected_problem_ids": undetected, "stop_reason": stop}
        )

    runs = [
        errored("a", ("PB-01",), "no_nonzero_voi"),
        errored("b", (), "no_nonzero_voi"),
        errored("c", (), "list_exhausted"),
        hand_result("d", "voi", 0.0, 0, 0, False),
    ]
    assert decision_errors_by_kind(runs) == {
        "undetected_planted_problem": 1,
        "stopped_with_every_open_request_at_zero_voi": 1,
        "other": 1,
    }

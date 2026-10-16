"""evidence/engine.py and scripts/run_evidence.py: the whole engine on one SYNTHETIC case (committed applications,
a JSON-only generated packet through its oracle extraction, a startup loan, and an application that does not
validate). No PDF is read and nothing touches the network."""

import json
from datetime import UTC, datetime
from pathlib import Path

from evidence_case_helpers import APPLICATIONS_DIR, STARTUP_DIR, application_dict, oracle_case, single_problem_packet

import run_evidence
from uwcopilot.agent.decision_record import DecisionLog
from uwcopilot.evidence.engine import run_engine
from uwcopilot.evidence.open_items import packet_context
from uwcopilot.evidence.state import CaseState, evaluate, from_application


def fixed_clock() -> datetime:
    """A fixed clock for reproducible records."""
    return datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def test_the_engine_on_an_application_runs_every_step_and_keeps_the_policys_recommendation() -> None:
    state = from_application(application_dict("APP-C-001"))
    result = run_engine(state, clock=fixed_clock)
    assert result.evaluable and result.case_id == "APP-C-001" and result.line == "credit"
    assert result.recommendation == evaluate(state).recommendation  # the engine never decides on its own
    assert result.ledger is not None and result.ledger.subject_id == "APP-C-001"
    assert [item.scenario_id for item in result.scenarios][:3] == [
        "revenue_minus_10",
        "revenue_minus_20",
        "revenue_minus_30",
    ]
    assert result.stability is not None and result.stability.baseline_recommendation == result.recommendation
    assert result.open_items == [] and result.next_best_evidence == []
    assert result.calculations and result.record["recommendation"] == result.recommendation
    assert result.record["packet_sha256"] == {"pdf": None, "extraction": None}


def test_the_engine_on_a_packet_extraction_runs_the_checks_and_ranks_the_requests() -> None:
    truth = single_problem_packet("credit", "fein_mismatch")
    case = oracle_case(truth)
    result = run_engine(case.extraction, clock=fixed_clock)
    assert result.evaluable
    assert [item.item_id for item in result.open_items] == ["OI-fein_mismatch"]
    top = result.next_best_evidence[0]
    assert top.request_id == truth.problems[0].resolving_request
    assert top.voi > 0 and top.reason.startswith('Requesting "Corrected application"')
    record = result.record
    assert record["contradiction_graph"]["edges"] and record["contradictions"]
    assert record["packet_sha256"]["extraction"] is not None
    claim = result.ledger.claim("CHK-fein_consistency") if result.ledger else None
    assert claim is not None and claim.status == "CONFLICTING"


def test_the_engine_on_a_state_with_its_packet_context_matches_the_extraction_path() -> None:
    case = oracle_case(single_problem_packet("insurance", "tiv_mismatch"))
    context = packet_context(case.extraction, case.checks, case.report)
    from_state = run_engine(case.state, context=context, clock=fixed_clock)
    from_extraction = run_engine(case.extraction, clock=fixed_clock)
    assert [item.item_id for item in from_state.open_items] == [item.item_id for item in from_extraction.open_items]
    assert [item.voi for item in from_state.next_best_evidence] == [
        item.voi for item in from_extraction.next_best_evidence
    ]


def test_answered_items_are_left_out() -> None:
    case = oracle_case(single_problem_packet("credit", "fein_mismatch"))
    result = run_engine(case.extraction, answered=("OI-fein_mismatch",), clock=fixed_clock)
    assert result.open_items == [] and result.next_best_evidence == []


def test_a_startup_loan_runs_with_the_rate_scenarios() -> None:
    state = from_application(application_dict("APP-S-001"))
    result = run_engine(state, clock=fixed_clock)
    assert result.evaluable and result.line == "startup_credit"
    assert [item.scenario_id for item in result.scenarios] == ["rate_plus_200bps", "rate_plus_400bps"]
    assert result.ledger is not None and result.ledger.line == "startup_credit"


def test_an_application_that_does_not_validate_is_reported_not_guessed() -> None:
    application = application_dict("APP-C-001")
    application["applicant"]["state"] = "N3"  # an OCR misread of a state code
    state = CaseState(case_id="PKT-C-999", application=application)
    result = run_engine(state, clock=fixed_clock)
    assert not result.evaluable and result.recommendation is None
    assert "string_pattern_mismatch" in str(result.not_evaluable_reason)
    assert "N3" not in str(result.not_evaluable_reason)  # values are never echoed
    assert result.record["recommendation"] is None and result.record["not_evaluable_reason"]


def test_the_record_is_appended_only_when_a_log_is_given_and_runs_are_reproducible(tmp_path: Path) -> None:
    state = from_application(application_dict("APP-I-001"))
    first = run_engine(state, clock=fixed_clock)
    assert first.record == run_engine(state, clock=fixed_clock).record
    log = DecisionLog(tmp_path / "records.jsonl")
    stored = run_engine(state, clock=fixed_clock, record_log=log).record
    assert stored["record_id"] == first.record["record_id"] and "record_sha256" in stored
    assert len(log.records()) == 1 and log.verify_chain() == []


def test_the_script_runs_an_application_and_writes_the_record_and_views(tmp_path: Path, capsys) -> None:
    records = tmp_path / "records.jsonl"
    views = tmp_path / "views"
    code = run_evidence.main(
        [
            "--application",
            str(APPLICATIONS_DIR / "APP-C-001.json"),
            "--records",
            str(records),
            "--views-dir",
            str(views),
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "Reference recommendation:" in output and "Decision stability:" in output
    assert "Record chain: intact" in output
    record = json.loads(records.read_text(encoding="utf-8").splitlines()[0])
    assert (views / f"{record['record_id']}.md").is_file() and (views / f"{record['record_id']}.html").is_file()


def test_the_script_handles_a_startup_file_and_bad_input(tmp_path: Path, capsys) -> None:
    code = run_evidence.main(["--application", str(STARTUP_DIR / "APP-S-001.json"), "--no-record"])
    assert code == 0 and "startup_credit" in capsys.readouterr().out
    assert run_evidence.main(["--packet", "NOT-A-PACKET", "--no-record"]) == 1
    assert run_evidence.main(["--packet", "PKT-C-001", "--packets-dir", str(tmp_path), "--no-record"]) == 1
    assert "make packets" in capsys.readouterr().err

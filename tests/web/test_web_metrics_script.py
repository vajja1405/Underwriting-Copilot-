"""Tests for scripts/web_metrics.py: the parts that need no browser, no Node and no network.

The browser session itself (Playwright, axe-core, Lighthouse) is exercised by running the script against a local
server; here the result summaries, the decision choice, the step log, the argument checks and the results file are
tested with small hand-written inputs.
"""

import argparse
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

import web_metrics
from web_metrics import (
    CheckFailed,
    FlowLog,
    FlowStopped,
    MetricsConfigError,
    NodeTools,
    Observer,
    axe_totals,
    check_arguments,
    choose_action,
    find_node_tools,
    lighthouse_command,
    median_of_runs,
    merge_run,
    mirror_math_round,
    needs_override,
    read_server_config,
    require,
    run_flow,
    since_ms,
    summarize_axe,
    summarize_lighthouse,
)


def arguments(**overrides: Any) -> argparse.Namespace:
    """Parsed arguments with defaults, then the overrides."""
    args = web_metrics.parse_args(["--url", "http://127.0.0.1:7862", "--target", "local-container"])
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def axe_rule(rule_id: str, impact: str | None, nodes: int) -> dict[str, Any]:
    """One axe violation or incomplete item with `nodes` flagged elements."""
    return {
        "id": rule_id,
        "impact": impact,
        "help": f"help for {rule_id}",
        "tags": ["wcag2aa"],
        "nodes": [{"target": [f"#node-{index}"]} for index in range(nodes)],
    }


def lighthouse_report(performance: float | None = 0.9, accessibility: float = 1.0) -> dict[str, Any]:
    """A minimal Lighthouse result (lhr) with the fields the summary reads."""
    return {
        "lighthouseVersion": "13.5.0",
        "configSettings": {"formFactor": "mobile", "throttlingMethod": "simulate"},
        "categories": {
            "performance": {"score": performance, "auditRefs": []},
            "accessibility": {
                "score": accessibility,
                "auditRefs": [{"id": "color-contrast", "weight": 7}, {"id": "landmark-one-main", "weight": 0}],
            },
            "best-practices": {"score": 1.0, "auditRefs": [{"id": "errors-in-console", "weight": 1}]},
        },
        "audits": {
            "first-contentful-paint": {"numericValue": 1200.5},
            "largest-contentful-paint": {"numericValue": 2400.0},
            "total-blocking-time": {"numericValue": 30.0},
            "cumulative-layout-shift": {"numericValue": 0.01},
            "speed-index": {"numericValue": 1500.0},
            "color-contrast": {"score": 0 if accessibility < 1 else 1},
            "landmark-one-main": {"score": 0},
            "errors-in-console": {"score": 1},
        },
        "runWarnings": [],
    }


# ---- arguments and tools ------------------------------------------------------------------------------------------


def test_check_arguments_accepts_a_loopback_url() -> None:
    assert check_arguments(arguments()) == "http://127.0.0.1:7862"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"url": "https://example.hf.space"}, "loopback"),
        ({"lighthouse_runs": 0}, "--lighthouse-runs"),
        ({"step_timeout_s": 0}, "--step-timeout-s"),
        ({"container": "fundproof-metrics"}, "--docker-image"),
        ({"url": "http://127.0.0.1:7862/studio"}, "base URL"),
        ({"target": "local-server", "docker_image": "fundproof:dev"}, "runs no image"),
        ({"target": "local-server", "docker_image": "fundproof:dev", "container": "x"}, "runs no image"),
    ],
)
def test_check_arguments_refuses_what_does_not_fit(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(MetricsConfigError, match=message):
        check_arguments(arguments(**overrides))


def test_the_space_is_not_a_target() -> None:
    with pytest.raises(SystemExit):
        web_metrics.parse_args(["--url", "https://x.hf.space", "--target", "hf-space"])


def test_find_node_tools_names_the_install_command(tmp_path: Path) -> None:
    with pytest.raises(MetricsConfigError, match="npm ci"):
        find_node_tools(tmp_path, need_lighthouse=True)


def test_find_node_tools_reads_the_installed_versions(tmp_path: Path) -> None:
    modules = tmp_path / "node_modules"
    (modules / "axe-core").mkdir(parents=True)
    (modules / "axe-core" / "axe.min.js").write_text("/* axe */", encoding="utf-8")
    (modules / "axe-core" / "package.json").write_text('{"version": "4.13.0"}', encoding="utf-8")
    (modules / "lighthouse" / "cli").mkdir(parents=True)
    (modules / "lighthouse" / "cli" / "index.js").write_text("// cli", encoding="utf-8")
    (modules / "lighthouse" / "package.json").write_text('{"version": "13.5.0"}', encoding="utf-8")
    if web_metrics.shutil.which("node") is None:
        pytest.skip("node is not installed on this machine")
    tools = find_node_tools(tmp_path, need_lighthouse=True)
    assert (tools.axe_version, tools.lighthouse_version) == ("4.13.0", "13.5.0")


def test_axe_alone_is_enough_when_lighthouse_is_skipped(tmp_path: Path) -> None:
    axe_dir = tmp_path / "node_modules" / "axe-core"
    axe_dir.mkdir(parents=True)
    (axe_dir / "axe.min.js").write_text("/* axe */", encoding="utf-8")
    (axe_dir / "package.json").write_text('{"version": "4.13.0"}', encoding="utf-8")
    if web_metrics.shutil.which("node") is None:
        pytest.skip("node is not installed on this machine")
    tools = find_node_tools(tmp_path, need_lighthouse=False)
    assert tools.lighthouse_version == "not installed"


def test_lighthouse_command_keeps_the_session_and_picks_the_preset() -> None:
    tools = NodeTools("node", Path("cli/index.js"), Path("axe.min.js"), "13.5.0", "4.13.0")
    mobile = lighthouse_command(tools, "http://127.0.0.1:7862/", 9222, "mobile")
    desktop = lighthouse_command(tools, "http://127.0.0.1:7862/", 9222, "desktop")
    assert "--disable-storage-reset" in mobile and "--port=9222" in mobile
    assert "--only-categories=performance,accessibility,best-practices" in mobile
    assert "--preset=desktop" not in mobile and "--preset=desktop" in desktop
    with pytest.raises(MetricsConfigError):
        lighthouse_command(tools, "http://127.0.0.1:7862/", 9222, "tablet")


# ---- the server check ---------------------------------------------------------------------------------------------


STUDIO_METRO = {
    "id": "houston_tx",
    "data_label": "PUBLIC",
    "mode": "limited",
    "mode_reason": "data coverage 0.68 is below the threshold 0.80",
    "n_tracts": 1615,
}


def config_server(dev_auth: bool, options_status: int = 200) -> httpx.MockTransport:
    """A fake app answering /api/health, /api/config and /api/studio/options."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.1.0", "git_sha": None})
        if request.url.path == "/api/studio/options":
            metro = {**STUDIO_METRO, "coverage_note": "not copied", "prime_rate": {"value": 7.5}}
            return httpx.Response(options_status, json={"metros": [metro], "levers": []})
        body = {"product_name": "Fundproof", "disclaimer": "Not advice.", "dev_auth": dev_auth, "is_space": False}
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_read_server_config_needs_the_dev_login() -> None:
    with pytest.raises(MetricsConfigError, match="UWC_DEV_AUTH=1"):
        read_server_config("http://127.0.0.1:7862", config_server(dev_auth=False))
    facts = read_server_config("http://127.0.0.1:7862", config_server(dev_auth=True))
    assert facts["dev_auth"] is True and facts["disclaimer"] == "Not advice."


def test_the_studio_metros_the_image_has_are_recorded_and_nothing_else() -> None:
    facts = read_server_config("http://127.0.0.1:7862", config_server(dev_auth=True))
    assert facts["studio_metros"] == [STUDIO_METRO] and facts["studio_metros_note"] is None
    broken = read_server_config("http://127.0.0.1:7862", config_server(dev_auth=True, options_status=500))
    assert broken["studio_metros"] is None and broken["studio_metros_note"].endswith("HTTP 500")


def test_read_server_config_reports_a_server_that_stays_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    waits: list[float] = []
    with pytest.raises(MetricsConfigError, match="did not become healthy within 0 s"):
        read_server_config("http://127.0.0.1:7862", httpx.MockTransport(handler), wait_s=0, sleep=waits.append)
    assert waits == []


def test_read_server_config_waits_for_a_starting_container() -> None:
    attempts = {"health": 0}
    ready = config_server(dev_auth=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            attempts["health"] += 1
            if attempts["health"] < 3:
                raise httpx.ConnectError("not listening yet", request=request)
        return ready.handle_request(request)

    waits: list[float] = []
    facts = read_server_config("http://127.0.0.1:7862", httpx.MockTransport(handler), wait_s=60, sleep=waits.append)
    assert facts["version"] == "0.1.0"
    assert waits == [web_metrics.HEALTH_POLL_S, web_metrics.HEALTH_POLL_S]


# ---- axe ----------------------------------------------------------------------------------------------------------


def test_summarize_axe_counts_rules_and_nodes_per_impact() -> None:
    result = {
        "violations": [axe_rule("color-contrast", "serious", 3), axe_rule("region", "moderate", 2)],
        "incomplete": [axe_rule("color-contrast", "serious", 1)],
    }
    summary = summarize_axe(result)
    assert summary["violations_by_impact"] == {"critical": 0, "serious": 1, "moderate": 1, "minor": 0}
    assert summary["nodes_by_impact"] == {"critical": 0, "serious": 3, "moderate": 2, "minor": 0}
    assert summary["incomplete_by_impact"]["serious"] == 1
    assert [rule["id"] for rule in summary["serious_or_critical_rules"]] == ["color-contrast"]
    assert summary["serious_or_critical_rules"][0]["first_targets"] == [["#node-0"], ["#node-1"], ["#node-2"]]
    assert [rule["id"] for rule in summary["other_violation_rules"]] == ["region"]
    assert summary["other_violation_rules"][0]["nodes"] == 2


def test_summarize_axe_of_a_clean_page_is_all_zero() -> None:
    summary = summarize_axe({"violations": [], "incomplete": []})
    assert set(summary["violations_by_impact"].values()) == {0}
    assert summary["serious_or_critical_rules"] == []


def test_axe_totals_add_up_serious_and_critical() -> None:
    scans = [
        summarize_axe({"violations": [axe_rule("a", "critical", 1), axe_rule("b", "serious", 2)]}),
        summarize_axe({"violations": [axe_rule("c", "minor", 4)]}),
        summarize_axe({"violations": [axe_rule("d", "serious", 1)]}),
    ]
    totals = axe_totals(scans)
    assert totals == {
        "scans": 3,
        "critical_violation_rules": 1,
        "critical_nodes": 1,
        "serious_violation_rules": 2,
        "serious_nodes": 3,
        "scans_with_serious_or_critical": 2,
    }


# ---- Lighthouse ---------------------------------------------------------------------------------------------------


def test_mirror_math_round_rounds_halves_up_like_javascript() -> None:
    assert [mirror_math_round(value) for value in (96.5, 97.4999, 0.5, 89.0)] == [97, 97, 1, 89]


def test_summarize_lighthouse_reads_scores_metrics_and_failed_audits() -> None:
    summary = summarize_lighthouse(lighthouse_report(performance=0.875, accessibility=0.96))
    assert summary["scores"] == {"performance": 0.875, "accessibility": 0.96, "best-practices": 1.0}
    assert summary["scores_0_100"] == {"performance": 88, "accessibility": 96, "best-practices": 100}
    assert summary["metrics"]["first-contentful-paint"] == 1200.5
    # landmark-one-main failed but has weight 0, so only the weighted failure is listed
    assert summary["failed_weighted_audits"] == ["color-contrast"]
    assert (summary["form_factor"], summary["throttling_method"]) == ("mobile", "simulate")


def test_summarize_lighthouse_keeps_a_missing_score_as_none() -> None:
    report = lighthouse_report(performance=None)
    report["runtimeError"] = {"code": "NO_FCP", "message": "The page did not paint any content."}
    summary = summarize_lighthouse(report)
    assert summary["scores"]["performance"] is None and summary["scores_0_100"]["performance"] is None
    assert summary["runtime_error"] == "NO_FCP"


def test_median_of_runs_ignores_runs_without_scores() -> None:
    runs = [
        summarize_lighthouse(lighthouse_report(performance=0.8)),
        summarize_lighthouse(lighthouse_report(performance=0.9)),
        summarize_lighthouse(lighthouse_report(performance=0.7)),
        {"error": "Lighthouse did not finish"},
    ]
    median = median_of_runs(runs)
    assert median["scores"]["performance"] == 0.8
    assert median["scores_0_100"]["performance"] == 80
    assert median["runs_with_scores"] == 3
    assert median_of_runs([{"error": "x"}])["scores"]["performance"] is None


# ---- the decision choice ------------------------------------------------------------------------------------------


def checks(*allowed: str, override: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """action_checks as the lender file answer has them."""
    return [
        {"action": action, "allowed": action in allowed, "reasons": [], "needs_override_reason": action in override}
        for action in ("approve", "refer", "request_more_information", "decline")
    ]


def test_choose_action_follows_the_policy_when_allowed() -> None:
    assert choose_action("approve", checks("approve", "refer")) == "approve"
    assert choose_action("decline_for_review", checks("decline", "refer")) == "decline"


def test_choose_action_falls_back_to_the_first_allowed_action() -> None:
    assert choose_action("decline_for_review", checks("refer", "request_more_information")) == "refer"
    assert choose_action("approve", checks()) is None


def test_needs_override_reads_the_server_answer() -> None:
    assert needs_override("approve", checks("approve", override=("approve",))) is True
    assert needs_override("refer", checks("approve", "refer", override=("approve",))) is False


# ---- the step log -------------------------------------------------------------------------------------------------


class FakeResponse:
    """The parts of a Playwright Response the observer reads."""

    def __init__(self, method: str, url: str, status: int) -> None:
        self.request = type("Request", (), {"method": method})()
        self.url = url
        self.status = status


def test_a_failed_step_stops_its_flow_and_is_recorded() -> None:
    observer = Observer()
    log = FlowLog("demo", observer, (CheckFailed,))

    def body(flow: FlowLog) -> None:
        with flow.step("first"):
            pass
        with flow.step("second"):
            require(False, "the page did not say Not found")
        with flow.step("never_runs"):
            pass

    summary = run_flow(log, body)
    assert [step["name"] for step in summary["steps"]] == ["first", "second"]
    assert summary["steps"][0]["passed"] is True
    assert summary["steps"][1]["passed"] is False
    assert "Not found" in summary["steps"][1]["detail"]
    assert summary["passed"] is False


def test_other_errors_are_not_swallowed() -> None:
    log = FlowLog("demo", Observer(), (CheckFailed,))
    with pytest.raises(ZeroDivisionError), log.step("broken"):
        _ = 1 / 0


def test_expected_404s_do_not_fail_the_flow_but_unexpected_ones_do() -> None:
    observer = Observer()
    log = FlowLog("errors", observer, (CheckFailed,))

    def body(flow: FlowLog) -> None:
        with flow.step("unknown_plan", ("status of 404",), (404,)):
            observer.on_response(FakeResponse("GET", "http://127.0.0.1:7862/api/studio/projects/0000", 404))
            observer.add_console("Failed to load resource: the server responded with a status of 404 (Not Found)")
        with flow.step("next"):
            observer.on_response(FakeResponse("GET", "http://127.0.0.1:7862/studio/", 200))

    summary = run_flow(log, body)
    assert summary["passed"] is True
    assert summary["api_errors"][0]["expected"] is True
    assert summary["console_errors"][0]["expected"] is True

    later = FlowLog("later", observer, (CheckFailed,))

    def failing_api(flow: FlowLog) -> None:
        with flow.step("feasibility"):
            observer.on_response(FakeResponse("POST", "http://127.0.0.1:7862/api/studio/projects/x/feasibility", 409))

    assert run_flow(later, failing_api)["unexpected_api_errors"] == 1
    assert run_flow(later, failing_api)["passed"] is False


def test_first_answer_and_since_ms_time_api_answers() -> None:
    observer = Observer()
    observer.api_answers = [
        {"method": "POST", "path": "/api/studio/projects", "status": 201, "at": 10.0},
        {"method": "POST", "path": "/api/studio/projects/abc/feasibility", "status": 200, "at": 10.4},
    ]
    answer = observer.first_answer("POST", r"/feasibility$", since=9.9)
    assert answer is not None and since_ms(9.9, answer) == 500.0
    assert observer.first_answer("POST", r"/feasibility$", since=10.5) is None
    assert since_ms(9.9, None) is None


def test_flow_stopped_is_an_exception_not_a_check() -> None:
    assert not issubclass(FlowStopped, AssertionError)


# ---- the results file ---------------------------------------------------------------------------------------------


def test_merge_run_replaces_only_its_own_target(tmp_path: Path) -> None:
    output = tmp_path / "web_metrics.json"
    output.write_text(json.dumps({"runs": {"local-server": {"old": True}, "local-container": {"old": True}}}))
    document = merge_run(output, "local-container", {"new": True})
    assert document["runs"] == {"local-container": {"new": True}, "local-server": {"old": True}}
    assert "LOCAL" in document["description"]


def test_merge_run_refuses_a_broken_file(tmp_path: Path) -> None:
    output = tmp_path / "web_metrics.json"
    output.write_text("{not json")
    with pytest.raises(MetricsConfigError, match="not valid JSON"):
        merge_run(output, "local-container", {})


def test_main_stops_before_the_browser_on_bad_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert web_metrics.main(["--url", "http://10.0.0.5:7860", "--target", "local-container"]) == 2
    assert "loopback" in capsys.readouterr().err


def test_every_target_label_says_local_and_not_the_space() -> None:
    for label in web_metrics.TARGET_LABELS.values():
        assert "LOCAL" in label and "NOT the Hugging Face Space" in label and "UWC_DEV_AUTH=1" in label


def test_optimize_png_reports_what_it_did(tmp_path: Path) -> None:
    image_module = pytest.importorskip("PIL.Image")
    path = tmp_path / "shot.png"
    image_module.new("RGB", (64, 64), (240, 236, 228)).save(path, format="PNG", compress_level=0)
    assert "re-encoded losslessly" in web_metrics.optimize_png(path)
    assert "was not smaller" in web_metrics.optimize_png(path)


def fake_git(head: str, status: str, commits: tuple[str, ...] = ()) -> Any:
    """A stand-in for web_metrics.git_output: a fixed HEAD, `git status --porcelain` text and known commits."""

    def run(*arguments: str) -> str | None:
        if arguments[:1] == ("rev-parse",):
            return head + "\n"
        if arguments[:1] == ("status",):
            return status
        if arguments[:1] == ("cat-file",):
            return "" if arguments[2].removesuffix("^{commit}") in (head, *commits) else None
        return "diff --git a/x b/x\n"

    return run


def labelled_build(method: str | None, image_sha: str | None, commit_label: str | None = None) -> dict[str, Any]:
    """A build record as image_build_facts reads it from `docker image inspect` (labels and the image's stamp)."""
    labels: dict[str, str] = {}
    if method is not None:
        labels[web_metrics.BUILD_METHOD_LABEL] = method
    if commit_label is not None:
        labels[web_metrics.BUILD_COMMIT_LABEL] = commit_label
    env = ["PATH=/app/.venv/bin:/usr/bin"] + ([f"UWC_GIT_SHA={image_sha}"] if image_sha is not None else [])
    return web_metrics.build_facts_from_config({"Labels": labels, "Env": env})


STAMPED = "make-docker-build-stamped"


def test_an_image_stamped_with_a_commit_is_reproducible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, ""))
    checked = web_metrics.reproducibility("a" * 40, labelled_build(STAMPED, "a" * 40), container_runs_image=True)
    assert checked["reproducible"] is True and checked["reasons"] == [] and checked["server_is_head"] is True
    assert checked["working_tree_digest_sha256"] is None and checked["build_method"] == STAMPED
    assert checked["note"].startswith("Reproducible") and STAMPED in checked["note"]
    # An older commit is fine too, and later edits to the checkout do not change a committed image.
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, " M notes.md\n", commits=("c" * 40,)))
    older_build = labelled_build("git-archive", "c" * 40, commit_label="c" * 40)
    older = web_metrics.reproducibility("c" * 40, older_build, container_runs_image=True)
    assert older["reproducible"] is True and older["server_is_head"] is False and older["working_tree_dirty"] is True
    assert "depend on the build date" in older["note"]


def test_an_image_without_a_container_check_is_not_reproducible(monkeypatch: pytest.MonkeyPatch) -> None:
    # --docker-image without --container: any server (a uvicorn process on a dirty tree started with
    # UWC_GIT_SHA=<HEAD>) could be credited to a correctly labelled image.
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, ""))
    checked = web_metrics.reproducibility("a" * 40, labelled_build(STAMPED, "a" * 40))
    assert checked["reproducible"] is False and checked["container_runs_image"] is None
    assert any("no --container" in reason for reason in checked["reasons"])


@pytest.mark.parametrize(
    ("server_sha", "expected"),
    [
        (None, "does not name its commit"),
        ("b" * 40, "not a commit of this repository"),
        ("a" * 40 + "-dirty", "uncommitted tree"),
    ],
)
def test_an_unreproducible_build_says_why(
    monkeypatch: pytest.MonkeyPatch, server_sha: str | None, expected: str
) -> None:
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, " M src/app.py\n"))
    checked = web_metrics.reproducibility(server_sha, labelled_build(STAMPED, server_sha))
    assert checked["reproducible"] is False and any(expected in reason for reason in checked["reasons"])
    assert checked["note"].startswith("UNREPRODUCIBLE") and len(checked["working_tree_digest_sha256"]) == 64


def test_an_unknown_build_method_is_never_reproducible(monkeypatch: pytest.MonkeyPatch) -> None:
    # The stamp names a clean commit, but nothing says how the image was built: it could be a plain
    # `docker build --build-arg UWC_GIT_SHA=<HEAD> .` of a dirty checkout.
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, ""))
    plain_build = web_metrics.reproducibility("a" * 40, labelled_build(None, "a" * 40))
    assert plain_build["reproducible"] is False and plain_build["build_method"] is None
    assert any("build method is unknown" in reason for reason in plain_build["reasons"])
    assert any("docker-build-archive" in reason for reason in plain_build["reasons"])
    no_image = web_metrics.reproducibility("a" * 40, None)
    assert no_image["reproducible"] is False and no_image["build"] is None
    assert any("no --docker-image" in reason for reason in no_image["reasons"])
    made_up = web_metrics.reproducibility("a" * 40, labelled_build("my-laptop", "a" * 40))
    assert made_up["reproducible"] is False and made_up["build"]["method_known"] is False


def test_the_server_stamp_must_be_the_images_own(monkeypatch: pytest.MonkeyPatch) -> None:
    # e.g. `docker run -e UWC_GIT_SHA=<HEAD>` on an image built from something else
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, ""))
    checked = web_metrics.reproducibility("a" * 40, labelled_build(STAMPED, "a" * 40 + "-dirty"))
    assert checked["reproducible"] is False
    assert any("not the image's own UWC_GIT_SHA" in reason for reason in checked["reasons"])


def test_a_git_archive_build_must_name_the_same_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, "", commits=("c" * 40,)))
    checked = web_metrics.reproducibility("a" * 40, labelled_build("git-archive", "a" * 40, commit_label="c" * 40))
    assert checked["reproducible"] is False
    assert any(web_metrics.BUILD_COMMIT_LABEL in reason for reason in checked["reasons"])


def test_the_container_must_run_the_measured_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, ""))
    checked = web_metrics.reproducibility("a" * 40, labelled_build(STAMPED, "a" * 40), container_runs_image=False)
    assert checked["reproducible"] is False and checked["container_runs_image"] is False
    assert any("does not run the --docker-image" in reason for reason in checked["reasons"])


def test_build_facts_come_from_docker_image_inspect_and_copy_nothing_else() -> None:
    config = {
        "Labels": {
            web_metrics.BUILD_METHOD_LABEL: "git-archive",
            web_metrics.BUILD_COMMIT_LABEL: "d" * 40,
            "org.opencontainers.image.title": "Fundproof",
        },
        "Env": ["PATH=/usr/bin", "UWC_GIT_SHA=" + "d" * 40, "SOME_TOKEN=do-not-copy"],
    }
    calls: list[list[str]] = []

    def fake_run(command: list[str], **options: Any) -> Any:
        calls.append(command)
        return web_metrics.subprocess.CompletedProcess(command, 0, stdout=json.dumps(config) + "\n", stderr="")

    facts = web_metrics.image_build_facts("fundproof:dev", run=fake_run)
    assert calls == [["docker", "image", "inspect", "--format", "{{json .Config}}", "fundproof:dev"]]
    assert facts["method"] == "git-archive" and facts["method_known"] is True
    assert facts["commit_label"] == "d" * 40 and facts["image_git_sha"] == "d" * 40
    assert "do-not-copy" not in json.dumps(facts) and "Fundproof" not in json.dumps(facts)
    unlabelled = web_metrics.build_facts_from_config({"Labels": None, "Env": None})
    assert unlabelled["method"] is None and unlabelled["image_git_sha"] is None
    assert unlabelled["method_description"].startswith("unknown")


def test_the_makefile_sets_the_build_label_the_script_reads() -> None:
    makefile = (web_metrics.REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert web_metrics.BUILD_METHOD_LABEL in makefile and web_metrics.BUILD_COMMIT_LABEL in makefile
    for method in [*web_metrics.BUILD_METHODS, *web_metrics.REFUSED_BUILD_METHODS]:
        # the whole name, not a part of a longer one ("space-bundle" inside "space-bundle-dirty-tree")
        assert re.search(rf"(?<![\w-]){re.escape(method)}(?![\w-])", makefile), method
    assert "docker-build-archive:" in makefile and "git archive" in makefile


def test_main_refuses_an_unreproducible_build_without_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, " M src/app.py\n"))
    monkeypatch.setattr(web_metrics, "find_node_tools", lambda tools_dir, need_lighthouse: None)
    monkeypatch.setattr(web_metrics, "read_server_config", lambda base_url, wait_s: {"git_sha": "a" * 40 + "-dirty"})
    arguments = [
        "--url",
        "http://127.0.0.1:7862",
        "--target",
        "local-container",
        "--output",
        str(tmp_path / "out.json"),
    ]
    assert web_metrics.main(arguments) == 2 and "unreproducible" in capsys.readouterr().err
    assert not (tmp_path / "out.json").exists()


def test_main_refuses_an_image_without_a_build_label(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(web_metrics, "git_output", fake_git("a" * 40, ""))
    monkeypatch.setattr(web_metrics, "find_node_tools", lambda tools_dir, need_lighthouse: None)
    monkeypatch.setattr(web_metrics, "read_server_config", lambda base_url, wait_s: {"git_sha": "a" * 40})
    monkeypatch.setattr(web_metrics, "docker_image_facts", lambda image: {"image": image, "id": "sha256:1"})
    monkeypatch.setattr(web_metrics, "image_build_facts", lambda image: labelled_build(None, "a" * 40))
    monkeypatch.setattr(web_metrics, "container_facts", lambda name: {"name": name, "image_id": "sha256:1"})
    arguments = ["--url", "http://127.0.0.1:7862", "--target", "local-container", "--output", str(tmp_path / "o.json")]
    arguments += ["--docker-image", "fundproof:dev", "--container", "fundproof-metrics"]
    assert web_metrics.main(arguments) == 2
    error = capsys.readouterr().err
    assert "build method is unknown" in error and "docker-build-archive" in error
    assert not (tmp_path / "o.json").exists()


# ---- axe per viewport ----------------------------------------------------------------------------------------------


def test_axe_totals_are_also_given_per_viewport_widest_first() -> None:
    desktop = {**summarize_axe({"violations": [axe_rule("a", "serious", 2)]}), "viewport": "1280x800"}
    phone = {**summarize_axe({"violations": [axe_rule("b", "critical", 1)]}), "viewport": "375x812"}
    phone_clean = {**summarize_axe({"violations": []}), "viewport": "375x812"}
    by_viewport = web_metrics.axe_totals_by_viewport([phone, desktop, phone_clean])
    assert list(by_viewport) == ["1280x800", "375x812"]
    assert by_viewport["1280x800"]["serious_violation_rules"] == 1 and by_viewport["1280x800"]["scans"] == 1
    assert by_viewport["375x812"]["critical_nodes"] == 1 and by_viewport["375x812"]["scans"] == 2
    assert web_metrics.viewport_key(web_metrics.MOBILE_VIEWPORT) == "375x812"
    assert web_metrics.viewport_key(None) == "unknown"


# ---- the keyboard walk helpers ---------------------------------------------------------------------------------------


def focus(tag: str, **fields: Any) -> dict[str, Any]:
    """A READ_FOCUS_JS answer: an element with tag, then the given fields."""
    answer: dict[str, Any] = {
        "tag": tag,
        "id": "",
        "name": "",
        "text": "",
        "label": "",
        "list": "",
        "pressed": None,
        "in_dialog": False,
    }
    answer.update(fields)
    return answer


class FakeKeyboard:
    """A page whose focus walks through `ring` on Tab (backwards on Shift+Tab), wrapping like a browser does."""

    def __init__(self, ring: list[dict[str, Any]]) -> None:
        """Start with nothing focused (position -1 = the page body)."""
        self.ring = ring
        self.position = -1
        self.keys: list[str] = []

    def press(self, key: str) -> None:
        """Shift+Tab moves back one element; every other key moves forward (Tab, ArrowDown in a group)."""
        self.keys.append(key)
        step = -1 if key == "Shift+Tab" else 1
        self.position = (self.position + step) % len(self.ring)

    def read_focus(self) -> dict[str, Any]:
        """The element that has the focus now."""
        if self.position < 0:
            return focus("body")
        return self.ring[self.position]


WIZARD_RING = [
    focus("a", text="Skip to content"),
    focus("button", text="Back"),
    focus("select", label="Or pick a census tract from the list", text="Choose one of 300 tracts"),
    focus("button", text="Add as a site"),
    focus("button", text="Continue"),
]


def test_tab_until_counts_the_presses_to_the_target() -> None:
    page = FakeKeyboard(WIZARD_RING)
    target = web_metrics.FocusTarget("the Continue button", tag="button", text="Continue")
    assert web_metrics.tab_until(page.press, page.read_focus, target) == 5
    back = web_metrics.FocusTarget("the tract list", tag="select", label_prefix="Or pick a census tract")
    assert web_metrics.tab_until(page.press, page.read_focus, back, key="Shift+Tab") == 2
    assert page.keys == ["Tab"] * 5 + ["Shift+Tab"] * 2


def test_tab_until_fails_with_what_had_the_focus() -> None:
    page = FakeKeyboard(WIZARD_RING)
    missing = web_metrics.FocusTarget("the Create plan button", tag="button", text_prefix="Create plan")
    with pytest.raises(CheckFailed, match="Create plan button was not reached with Tab in 7 presses") as caught:
        web_metrics.tab_until(page.press, page.read_focus, missing, max_presses=7)
    assert "button 'Back'" in str(caught.value) and len(page.keys) == 7


def test_focus_target_checks_every_field_it_sets() -> None:
    chip = focus("button", text="Open guideline section NBC-4.2", list="Citations")
    assert web_metrics.FocusTarget("chip", tag="button", text_prefix="Open ", list="Citations").matches(chip)
    assert not web_metrics.FocusTarget("chip", tag="button", text_prefix="Open ", list="Findings").matches(chip)
    assert not web_metrics.FocusTarget("chip", tag="a", text_prefix="Open ").matches(chip)
    assert not web_metrics.FocusTarget("exact", text="Open").matches(chip)
    radio = focus("input", id="metro-synthetic_metro", name="metro")
    assert web_metrics.FocusTarget("cities", tag="input", name="metro").matches(radio)
    assert web_metrics.describe_focus(radio) == "input#metro-synthetic_metro"
    assert web_metrics.describe_focus(chip) == "button 'Open guideline section NBC-4.2'"


def test_arrow_until_moves_inside_a_radio_group() -> None:
    page = FakeKeyboard([focus("input", id=f"metro-{name}", name="metro") for name in ("a", "b", "c")])
    page.position = 0
    assert web_metrics.arrow_until(page.press, page.read_focus, "metro-c") == 2
    assert web_metrics.arrow_until(page.press, page.read_focus, "metro-c") == 0
    with pytest.raises(CheckFailed, match="metro-z"):
        web_metrics.arrow_until(page.press, page.read_focus, "metro-z", max_presses=4)


def test_focus_leaks_counts_tab_presses_that_leave_a_dialog() -> None:
    inside = [focus("button", text="Close", in_dialog=True), focus("div", text="NBC-4.2", in_dialog=True)]
    trapped = FakeKeyboard(inside)
    assert web_metrics.focus_leaks(trapped.press, trapped.read_focus, 6) == 0
    leaky = FakeKeyboard([*inside, focus("button", text="Approve")])
    assert web_metrics.focus_leaks(leaky.press, leaky.read_focus, 6) == 2


def test_the_new_flows_run_in_the_session() -> None:
    import inspect

    source = inspect.getsource(web_metrics.BrowserSession.run)
    assert '"keyboard_only"' in source and '"small_screen_375"' in source
    assert source.index('"small_screen_375"') < source.index("self.lighthouse()")
    assert "Escape" in inspect.getsource(web_metrics.BrowserSession.keyboard)
    assert "MOBILE_VIEWPORT" in inspect.getsource(web_metrics.BrowserSession.small_screen)

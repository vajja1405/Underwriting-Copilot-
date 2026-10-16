"""Tests for scripts/smoke_deploy.py against a fake server (httpx.MockTransport), a fake clock and a fake docker.

No network, no real waiting, no docker: every outside effect is injected.
"""

import json
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

import smoke_deploy
from smoke_deploy import (
    HEALTH_PATH,
    Sample,
    SmokeConfigError,
    container_start_to_ready_s,
    docker_image_facts,
    main,
    parse_docker_time,
    percentile,
    summarize_ms,
    validate_target,
)

CSP = "default-src 'self'; script-src 'self'"
IMAGE_ID = "sha256:" + "a" * 64


class FakeClock:
    """A clock that moves 10 ms per reading; sleep() moves it forward without waiting."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def fake_server(
    *,
    dev_auth: bool = False,
    unhealthy_first: int = 0,
    always_down: bool = False,
    rate_limit_config: bool = False,
    unknown_api_html: bool = False,
) -> tuple[Callable[[httpx.Request], httpx.Response], dict[str, int]]:
    """A handler that answers like the Fundproof app, with switches for the failure cases."""
    calls = {"health": 0, "total": 0}
    html_headers = {"content-security-policy": CSP, "x-content-type-options": "nosniff"}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["total"] += 1
        path = request.url.path
        if always_down:
            raise httpx.ConnectError("connection refused", request=request)
        if path == HEALTH_PATH:
            calls["health"] += 1
            if calls["health"] <= unhealthy_first:
                return httpx.Response(503, text="starting")
            return httpx.Response(200, json={"status": "ok", "version": "0.1.0", "git_sha": "abc1234"})
        if path in ("/", "/studio"):
            return httpx.Response(200, html="<!DOCTYPE html><html><body>Fundproof</body></html>", headers=html_headers)
        if path == "/api/config":
            if rate_limit_config:
                return httpx.Response(429, json={"detail": "Too Many Requests"}, headers={"retry-after": "1"})
            body = {"product_name": "Fundproof", "payments_enabled": False, "dev_auth": dev_auth, "metros": []}
            return httpx.Response(200, json=body)
        if path == "/api/me":
            return httpx.Response(200, json={"signed_in": False, "roles": [], "auth_mode": "none"})
        if unknown_api_html:
            return httpx.Response(404, html="<html>not found</html>", headers=html_headers)
        return httpx.Response(404, json={"detail": "Not Found"})

    return handler, calls


def fake_docker(command: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
    """Answers the four docker commands the script runs, like Docker 29 does."""
    if command[:3] == ["docker", "image", "inspect"]:
        stdout = f"{IMAGE_ID} 1234567890"
    elif command[:3] == ["docker", "image", "ls"] and "json" in command:
        stdout = json.dumps({"Repository": "fundproof", "Tag": "dev", "Size": "1.23GB"})
    elif command[:3] == ["docker", "image", "ls"]:
        stdout = "IMAGE   ID   DISK USAGE   CONTENT SIZE\nfundproof:dev   aaaaaaaaaaaa   1.23GB   400MB"
    elif command[:2] == ["docker", "version"]:
        stdout = "29.8.2"
    elif command[:3] == ["docker", "container", "inspect"]:
        stdout = f"{IMAGE_ID} running 2026-10-12T19:59:58.123456789Z"
    else:
        raise AssertionError(f"unexpected command {command}")
    return subprocess.CompletedProcess(list(command), 0, stdout=stdout, stderr="")


def run_main(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], *extra: str, target: str = "local-server"
) -> tuple[int, Path, FakeClock]:
    """main() against the fake server, writing into tmp_path."""
    output = tmp_path / "deployment_metrics.json"
    clock = FakeClock()
    argv = ["--url", "http://127.0.0.1:7860", "--target", target, "--requests", "3", "--wait-s", "5"]
    argv += ["--output", str(output), *extra]
    code = main(argv, transport=httpx.MockTransport(handler), clock=clock, sleep=clock.sleep, docker_run=fake_docker)
    return code, output, clock


def test_all_checks_pass_and_the_run_is_written_with_its_label(tmp_path: Path) -> None:
    handler, calls = fake_server()
    code, output, _ = run_main(tmp_path, handler)

    assert code == 0
    run = json.loads(output.read_text())["runs"]["local-server"]
    assert run["all_checks_passed"] is True
    assert "NOT the Hugging Face Space" in run["target"]["label"]
    assert run["target"]["url"] == "http://127.0.0.1:7860"
    assert {check["name"] for check in run["checks"]} >= {"health_ok", "config_safe_for_deploy", "me_signed_out"}
    assert run["latency_ms"]["/api/health"]["n"] == 3
    assert run["latency_ms"]["all_paths"]["n"] == 15
    assert set(run["first_request_ms"]) == {"/api/health", "/", "/studio", "/api/config", "/api/me"}
    assert run["server"] == {"version": "0.1.0", "git_sha": "abc1234"}
    assert run["docker_image"] is None
    assert run["provenance"]["generated_by"] == "scripts/smoke_deploy.py"
    # 1 readiness + 5 first requests + 1 unknown path + 3 rounds of 5 paths
    assert calls["total"] == 22


def test_a_deployment_with_the_dev_login_on_fails(tmp_path: Path) -> None:
    handler, _ = fake_server(dev_auth=True)
    code, output, _ = run_main(tmp_path, handler)

    assert code == 1
    checks = {check["name"]: check for check in json.loads(output.read_text())["runs"]["local-server"]["checks"]}
    assert checks["config_safe_for_deploy"]["passed"] is False
    assert "dev_auth=True" in checks["config_safe_for_deploy"]["detail"]


def test_rate_limited_requests_fail_the_run(tmp_path: Path) -> None:
    handler, _ = fake_server(rate_limit_config=True)
    code, output, _ = run_main(tmp_path, handler)

    assert code == 1
    run = json.loads(output.read_text())["runs"]["local-server"]
    checks = {check["name"]: check for check in run["checks"]}
    assert checks["no_rate_limited_requests"]["passed"] is False
    assert run["status_counts"]["/api/config"] == {"429": 3}


def test_an_unknown_api_path_answered_with_html_fails(tmp_path: Path) -> None:
    handler, _ = fake_server(unknown_api_html=True)
    code, output, _ = run_main(tmp_path, handler)

    assert code == 1
    checks = {check["name"]: check for check in json.loads(output.read_text())["runs"]["local-server"]["checks"]}
    assert checks["unknown_api_is_json_404"]["passed"] is False


def test_readiness_polls_until_healthy(tmp_path: Path) -> None:
    handler, calls = fake_server(unhealthy_first=2)
    code, output, _ = run_main(tmp_path, handler)

    assert code == 0
    readiness = json.loads(output.read_text())["runs"]["local-server"]["readiness"]
    assert readiness["ready"] is True
    assert readiness["attempts"] == 3
    assert readiness["seconds_until_ready"] >= 2 * smoke_deploy.POLL_INTERVAL_S["local-server"]


def test_a_server_that_never_answers_writes_nothing(tmp_path: Path) -> None:
    handler, _ = fake_server(always_down=True)
    code, output, clock = run_main(tmp_path, handler)

    assert code == 1
    assert not output.exists()
    assert clock.now >= 1000.0 + 5  # waited the full --wait-s on the fake clock


def test_a_new_run_keeps_the_other_targets(tmp_path: Path) -> None:
    output = tmp_path / "deployment_metrics.json"
    output.write_text(json.dumps({"description": "old", "runs": {"hf-space": {"marker": True}}}))
    handler, _ = fake_server()
    code, _, _ = run_main(tmp_path, handler)

    runs = json.loads(output.read_text())["runs"]
    assert code == 0
    assert runs["hf-space"] == {"marker": True}
    assert runs["local-server"]["all_checks_passed"] is True


def test_local_container_records_the_image_and_checks_the_container(tmp_path: Path) -> None:
    handler, _ = fake_server()
    code, output, _ = run_main(
        tmp_path, handler, "--docker-image", "fundproof:dev", "--container", "smoke", target="local-container"
    )

    run = json.loads(output.read_text())["runs"]["local-container"]
    assert code == 0
    assert run["docker_image"]["size_bytes_from_docker_image_inspect"] == 1234567890
    assert run["docker_image"]["size_from_docker_image_ls"] == "1.23GB"
    assert run["docker_image"]["docker_server_version"] == "29.8.2"
    checks = {check["name"]: check for check in run["checks"]}
    assert checks["container_runs_image"]["passed"] is True
    assert run["readiness"]["container_started_at"] == "2026-10-12T19:59:58.123456789Z"
    assert "container_start_to_ready_s" in run["readiness"]


def test_container_cold_start_is_measured_from_dockers_start_time() -> None:
    facts = {"name": "smoke", "image_id": IMAGE_ID, "state": "running", "started_at": "2026-10-12T19:59:58.5Z"}
    readiness = {"ready": True, "ready_at_utc": "2026-10-12T20:00:00.250000+00:00"}
    assert container_start_to_ready_s(facts, readiness) == 1.75
    assert container_start_to_ready_s(facts, {"ready": False}) is None


def test_docker_times_with_nanoseconds_are_parsed() -> None:
    parsed = parse_docker_time("2026-10-12T19:59:58.123456789Z")
    assert parsed == datetime(2026, 10, 12, 19, 59, 58, 123456, tzinfo=UTC)
    assert parse_docker_time("2026-10-12T19:59:58Z") == datetime(2026, 10, 12, 19, 59, 58, tzinfo=UTC)


def test_container_without_image_is_a_usage_error(tmp_path: Path) -> None:
    handler, _ = fake_server()
    code, output, _ = run_main(tmp_path, handler, "--container", "smoke", target="local-container")

    assert code == 2
    assert not output.exists()


@pytest.mark.parametrize(
    ("url", "target"),
    [
        ("https://astra6-fundproof.hf.space", "local-container"),  # a local label on a remote URL
        ("http://127.0.0.1:7860", "hf-space"),  # a Space label on a local URL
        ("http://astra6-fundproof.hf.space", "hf-space"),  # not https
        ("https://example.com", "hf-space"),  # not a Space host
        ("http://127.0.0.1:7860/studio", "local-server"),  # not a base URL
        ("127.0.0.1:7860", "local-server"),  # no scheme
    ],
)
def test_target_must_match_the_url(url: str, target: str) -> None:
    with pytest.raises(SmokeConfigError):
        validate_target(url, target)


def test_valid_targets_are_normalized() -> None:
    assert validate_target("http://localhost:7860/", "local-container") == "http://localhost:7860"
    assert validate_target("https://owner-app.hf.space", "hf-space") == "https://owner-app.hf.space"


def test_percentile_is_nearest_rank() -> None:
    values = [float(value) for value in range(1, 21)]
    assert percentile(values, 50) == 10.0
    assert percentile(values, 95) == 19.0
    assert percentile(values, 100) == 20.0
    assert percentile([7.0], 95) == 7.0
    with pytest.raises(ValueError):
        percentile([], 50)


def test_summary_in_milliseconds() -> None:
    summary = summarize_ms([0.010, 0.020, 0.030])
    assert summary == {"n": 3, "p50_ms": 20.0, "p95_ms": 30.0, "min_ms": 10.0, "max_ms": 30.0}
    assert summarize_ms([])["n"] == 0


def test_docker_facts_fail_clearly_without_docker() -> None:
    def missing_docker(command: Sequence[str], **_: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("docker")

    with pytest.raises(SmokeConfigError, match="docker image inspect"):
        docker_image_facts("fundproof:dev", missing_docker)


def test_sample_json_is_none_for_html() -> None:
    assert Sample(path="/", status=200, seconds=0.1, body=b"<html></html>").json() is None

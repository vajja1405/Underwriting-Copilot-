"""Smoke test and latency check of a running Fundproof deployment; writes results/deployment_metrics.json.

    # the local container (see `make docker-run`, or docker run -d --name fundproof-smoke -p 7860:7860 fundproof:dev)
    uv run python scripts/smoke_deploy.py --url http://127.0.0.1:7860 --target local-container \
        --docker-image fundproof:dev --container fundproof-smoke
    # the Hugging Face Space, once a real deploy exists
    uv run python scripts/smoke_deploy.py --url https://<owner>-<space>.hf.space --target hf-space

Steps:
1. Readiness. GET /api/health every 0.1 s (local targets) or 2 s (hf-space) until it answers 200 with status
   "ok", for at most --wait-s seconds. seconds_until_ready counts from the start of this script. With --container,
   container_start_to_ready_s counts from the container's start time as Docker recorded it (State.StartedAt) to the
   first healthy answer: the container's cold start, to within one poll interval. A sleeping Space wakes up here.
2. First requests. One GET to each path in CHECKED_PATHS, in that order. Their latencies are reported on their own
   ("first_request_ms"): the first /api/config, for example, loads the Studio YAML files.
3. Checks, each recorded as passed or failed with a detail:
   - /api/health is 200 with status "ok";
   - / and /studio are 200 HTML pages with a Content-Security-Policy header and X-Content-Type-Options: nosniff;
   - /api/config is 200 with a product name, payments_enabled false and dev_auth false (a deployment never runs
     with the dev login);
   - /api/me is 200 and signed out (auth_mode "none");
   - an unknown /api path is a JSON 404, not the frontend's HTML;
   - no request was rate limited (HTTP 429);
   - with --container: that container is running and was started from the --docker-image image.
4. Latency. --requests more GETs per path, round robin, one at a time, from this one client. p50 and p95 use the
   nearest-rank method. This is not a load test: one client, sequential requests, over whatever network lies
   between this machine and the URL, on whatever else the machine is doing (the load average is in provenance).

Rate limits: /api/config and /api/me count toward the per-IP token bucket (default burst 60, see
uwcopilot.web.settings), so keep 2 * (--requests + 1) below the burst. The default of 20 requests does.

--target says WHAT was measured, and its label is written next to the numbers so a local run is never mistaken for
the Space: local-container and local-server need a loopback URL; hf-space needs an https://*.hf.space URL.
--docker-image records the image id and size from `docker image inspect` and `docker image ls`.

The results file keeps one entry per target (runs.local-container, runs.hf-space, ...), each with its own
provenance block (uwcopilot.governance.provenance); a new run replaces only its own target's entry. Nothing is
written when the server never became ready. Exit code: 0 when every check passed, 1 otherwise, 2 for bad arguments.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from uwcopilot.common.paths import RESULTS_DIR
from uwcopilot.governance.provenance import RunProvenance, display_path
from uwcopilot.web.settings import PRODUCT_NAME

GENERATED_BY = "scripts/smoke_deploy.py"
DEFAULT_OUTPUT = RESULTS_DIR / "deployment_metrics.json"
DEFAULT_REQUESTS = 20
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_WAIT_S = 120.0
# Seconds between readiness polls: fine-grained on this machine, gentle on the Space.
POLL_INTERVAL_S: dict[str, float] = {"local-container": 0.1, "local-server": 0.1, "hf-space": 2.0}
DOCKER_TIMEOUT_S = 60

HEALTH_PATH = "/api/health"
CONFIG_PATH = "/api/config"
ME_PATH = "/api/me"
HTML_PATHS: tuple[str, ...] = ("/", "/studio")
# The order of the first requests and of each latency round.
CHECKED_PATHS: tuple[str, ...] = (HEALTH_PATH, "/", "/studio", CONFIG_PATH, ME_PATH)
UNKNOWN_API_PATH = "/api/smoke-test-unknown-path"

TARGET_LABELS: dict[str, str] = {
    "local-container": (
        "LOCAL Docker container on the machine that ran this script. NOT the Hugging Face Space: no internet hop, "
        "no Space proxy, not Space hardware."
    ),
    "local-server": (
        "LOCAL uvicorn process on the machine that ran this script (no container). NOT the Hugging Face Space."
    ),
    "hf-space": "The deployed Hugging Face Space, measured over the internet from the machine that ran this script.",
}
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# Docker's State.StartedAt: seconds, an optional fraction of up to 9 digits, then Z or an offset.
DOCKER_TIME_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$")
SPACE_HOST_SUFFIX = ".hf.space"

RESULTS_DESCRIPTION = (
    f"Smoke checks and request latency of a running {PRODUCT_NAME} deployment, written by scripts/smoke_deploy.py. "
    "One entry per target; each entry says what was measured (target.label) and has its own provenance block. "
    "A target that is not listed here was not measured."
)
CAVEATS: tuple[str, ...] = (
    "One client sending one request at a time: this is a smoke test with timings, not a load test.",
    "Latency includes the network between the measuring machine and the URL, and whatever else that machine was "
    "doing (see provenance.environment.load_average_*).",
    "seconds_until_ready counts from the start of the script, not of the server. container_start_to_ready_s "
    "(local-container with --container only) counts from Docker's State.StartedAt, to within one poll interval.",
    "p50 and p95 use the nearest-rank method over the samples listed in n.",
)


class SmokeConfigError(ValueError):
    """Bad arguments: a target that does not match its URL, or Docker facts that cannot be read."""


@dataclass(frozen=True)
class Sample:
    """One GET request: what came back and how long it took (status None when no response arrived)."""

    path: str
    status: int | None
    seconds: float
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    error: str | None = None

    @property
    def content_type(self) -> str:
        """The Content-Type header, lowercased ('' when absent)."""
        return self.headers.get("content-type", "").lower()

    def json(self) -> Any:
        """The body parsed as JSON, or None when it is not JSON."""
        try:
            return json.loads(self.body)
        except ValueError:
            return None


@dataclass
class CheckLog:
    """Named pass/fail checks, in the order they ran."""

    checks: list[dict[str, Any]] = field(default_factory=list)

    def record(self, name: str, passed: bool, detail: str) -> None:
        """Add one check."""
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail})

    @property
    def all_passed(self) -> bool:
        """True when at least one check ran and none failed."""
        return bool(self.checks) and all(check["passed"] for check in self.checks)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Command-line arguments (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="base URL of the running app, e.g. http://127.0.0.1:7860")
    parser.add_argument("--target", required=True, choices=sorted(TARGET_LABELS), help="what is being measured")
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS, help="latency requests per path")
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S, help="timeout of each request")
    parser.add_argument("--wait-s", type=float, default=DEFAULT_WAIT_S, help="how long to wait for /api/health")
    parser.add_argument("--docker-image", help="local image name to record (id and size), e.g. fundproof:dev")
    parser.add_argument("--container", help="running container to check against --docker-image")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="results file to update")
    return parser.parse_args(argv)


def validate_target(url: str, target: str) -> str:
    """The base URL without a trailing slash, after checking it fits the target; SmokeConfigError otherwise."""
    if target not in TARGET_LABELS:
        raise SmokeConfigError(f"Unknown target {target!r}; choose one of {sorted(TARGET_LABELS)}.")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise SmokeConfigError(f"--url must be an absolute http(s) URL (got {url!r}).")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise SmokeConfigError(f"--url must be the base URL with no path or query (got {url!r}).")
    if target.startswith("local-") and host not in LOOPBACK_HOSTS:
        raise SmokeConfigError(f"Target {target} needs a loopback URL (127.0.0.1, localhost or ::1), got {host!r}.")
    if target == "hf-space" and (parts.scheme != "https" or not host.endswith(SPACE_HOST_SUFFIX)):
        raise SmokeConfigError(f"Target hf-space needs an https://<owner>-<space>{SPACE_HOST_SUFFIX} URL.")
    return url.rstrip("/")


def percentile(values: Sequence[float], percent: float) -> float:
    """Nearest-rank percentile: the smallest sample with at least `percent` % of the samples at or below it."""
    if not values:
        raise ValueError("percentile of an empty list")
    if not 0 < percent <= 100:
        raise ValueError(f"percent must be in (0, 100], got {percent}")
    ordered = sorted(values)
    rank = math.ceil(percent / 100 * len(ordered))
    return ordered[rank - 1]


def summarize_ms(seconds: Sequence[float]) -> dict[str, Any]:
    """{n, p50_ms, p95_ms, min_ms, max_ms} for durations in seconds; the values are None when there are none."""
    if not seconds:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "min_ms": None, "max_ms": None}
    milliseconds = [value * 1000 for value in seconds]
    return {
        "n": len(milliseconds),
        "p50_ms": round(percentile(milliseconds, 50), 2),
        "p95_ms": round(percentile(milliseconds, 95), 2),
        "min_ms": round(min(milliseconds), 2),
        "max_ms": round(max(milliseconds), 2),
    }


def timed_get(client: httpx.Client, path: str, clock: Callable[[], float] = time.perf_counter) -> Sample:
    """GET path and time it, including reading the whole body. A transport error becomes status None."""
    started = clock()
    try:
        response = client.get(path)
        body = response.content
    except httpx.HTTPError as error:
        return Sample(path=path, status=None, seconds=clock() - started, error=f"{type(error).__name__}: {error}")
    headers = {name.lower(): value for name, value in response.headers.items()}
    return Sample(path=path, status=response.status_code, seconds=clock() - started, headers=headers, body=body)


def is_healthy(sample: Sample) -> bool:
    """True for a 200 /api/health answer with status "ok"."""
    data = sample.json()
    return sample.status == 200 and isinstance(data, dict) and data.get("status") == "ok"


def wait_until_ready(
    client: httpx.Client,
    wait_s: float,
    poll_s: float,
    started_at: float,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll /api/health every poll_s until it is healthy or wait_s has passed since started_at (at least one try).

    ready_at_utc is the wall-clock time of the first healthy answer, used to compute the container's cold start.
    """
    attempts = 0
    while True:
        attempts += 1
        sample = timed_get(client, HEALTH_PATH, clock)
        if is_healthy(sample):
            return {
                "ready": True,
                "seconds_until_ready": round(clock() - started_at, 3),
                "attempts": attempts,
                "ready_at_utc": datetime.now(UTC).isoformat(timespec="microseconds"),
            }
        if clock() - started_at >= wait_s:
            last = sample.error or f"HTTP {sample.status}"
            return {"ready": False, "seconds_until_ready": None, "attempts": attempts, "last_answer": last}
        sleep(poll_s)


def check_health(sample: Sample) -> tuple[bool, str]:
    """/api/health is 200 with status ok."""
    data = sample.json()
    body_status = data.get("status") if isinstance(data, dict) else None
    return is_healthy(sample), f"HTTP {sample.status}, status={body_status!r}"


def check_html_page(sample: Sample) -> tuple[bool, str]:
    """A 200 HTML page with a CSP header and nosniff."""
    is_html = sample.content_type.startswith("text/html") and b"<html" in sample.body.lower()
    has_csp = bool(sample.headers.get("content-security-policy"))
    nosniff = sample.headers.get("x-content-type-options", "").lower() == "nosniff"
    passed = sample.status == 200 and is_html and has_csp and nosniff
    detail = f"HTTP {sample.status}, html={is_html}, content-security-policy={has_csp}, nosniff={nosniff}"
    return passed, detail


def check_config(sample: Sample) -> tuple[bool, str]:
    """/api/config: product name present, no payments, dev login off."""
    data = sample.json()
    if sample.status != 200 or not isinstance(data, dict):
        return False, f"HTTP {sample.status}, body is not a JSON object"
    product_name = data.get("product_name")
    passed = bool(product_name) and data.get("payments_enabled") is False and data.get("dev_auth") is False
    detail = (
        f"product_name={product_name!r}, payments_enabled={data.get('payments_enabled')!r}, "
        f"dev_auth={data.get('dev_auth')!r}, hf_oauth={data.get('hf_oauth')!r}, is_space={data.get('is_space')!r}, "
        f"metros={len(data.get('metros') or [])}, business_types={len(data.get('business_types') or [])}"
    )
    return passed, detail


def check_signed_out(sample: Sample) -> tuple[bool, str]:
    """/api/me without a cookie is signed out."""
    data = sample.json()
    if sample.status != 200 or not isinstance(data, dict):
        return False, f"HTTP {sample.status}, body is not a JSON object"
    passed = data.get("signed_in") is False and data.get("auth_mode") == "none"
    return passed, f"signed_in={data.get('signed_in')!r}, auth_mode={data.get('auth_mode')!r}"


def check_unknown_api(sample: Sample) -> tuple[bool, str]:
    """An unknown /api path is a JSON 404 (the static catch-all must not answer it with HTML)."""
    passed = sample.status == 404 and sample.content_type.startswith("application/json")
    return passed, f"HTTP {sample.status}, content-type={sample.content_type or None!r}"


def run_checks(first: Mapping[str, Sample], unknown: Sample, all_samples: Sequence[Sample]) -> CheckLog:
    """Every endpoint check plus the 'never rate limited' check over all samples."""
    log = CheckLog()
    log.record("health_ok", *check_health(first[HEALTH_PATH]))
    for path in HTML_PATHS:
        log.record(f"html_page {path}", *check_html_page(first[path]))
    log.record("config_safe_for_deploy", *check_config(first[CONFIG_PATH]))
    log.record("me_signed_out", *check_signed_out(first[ME_PATH]))
    log.record("unknown_api_is_json_404", *check_unknown_api(unknown))
    limited = sum(1 for sample in all_samples if sample.status == 429)
    failed = sum(1 for sample in all_samples if sample.status is None)
    log.record("no_rate_limited_requests", limited == 0, f"{limited} of {len(all_samples)} requests got HTTP 429")
    log.record("no_failed_requests", failed == 0, f"{failed} of {len(all_samples)} requests got no response")
    return log


def measure_latency(
    client: httpx.Client, paths: Sequence[str], rounds: int, clock: Callable[[], float] = time.perf_counter
) -> list[Sample]:
    """`rounds` rounds of one GET per path, one request at a time."""
    samples: list[Sample] = []
    for _ in range(rounds):
        for path in paths:
            samples.append(timed_get(client, path, clock))
    return samples


def latency_report(samples: Sequence[Sample], paths: Sequence[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """(latency summaries per path and over all paths, HTTP status counts per path). Failed requests are excluded
    from the timings and counted as status "no response"."""
    latency: dict[str, Any] = {}
    status_counts: dict[str, dict[str, int]] = {}
    for path in paths:
        mine = [sample for sample in samples if sample.path == path]
        latency[path] = summarize_ms([sample.seconds for sample in mine if sample.status is not None])
        counts = Counter("no response" if sample.status is None else str(sample.status) for sample in mine)
        status_counts[path] = dict(sorted(counts.items()))
    latency["all_paths"] = summarize_ms([sample.seconds for sample in samples if sample.status is not None])
    return latency, status_counts


def run_docker(command: Sequence[str], run: Callable[..., subprocess.CompletedProcess[str]]) -> str:
    """stdout of a docker command; SmokeConfigError when docker is missing or the command fails."""
    try:
        completed = run(list(command), capture_output=True, text=True, check=True, timeout=DOCKER_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as error:
        raise SmokeConfigError(f"`{' '.join(command)}` failed: {error}") from error
    return completed.stdout.strip()


def docker_image_facts(
    image: str, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
) -> dict[str, Any]:
    """The image id and size as Docker reports them (copied from the commands, not computed)."""
    image_id, size_bytes = run_docker(
        ["docker", "image", "inspect", "--format", "{{.Id}} {{.Size}}", image], run
    ).split()
    listing = run_docker(["docker", "image", "ls", "--format", "json", image], run)
    rows = [json.loads(line) for line in listing.splitlines() if line.strip()]
    table = run_docker(["docker", "image", "ls", image], run)
    return {
        "image": image,
        "id": image_id,
        "size_bytes_from_docker_image_inspect": int(size_bytes),
        "size_from_docker_image_ls": rows[0].get("Size") if rows else None,
        "docker_image_ls_output": table.splitlines(),
        "docker_server_version": run_docker(["docker", "version", "--format", "{{.Server.Version}}"], run),
        "note": (
            "size_from_docker_image_ls is the DISK USAGE column (unpacked size on this machine); the CONTENT SIZE "
            "column in docker_image_ls_output is the compressed size of the image content."
        ),
    }


def parse_docker_time(text: str) -> datetime:
    """Parse Docker's RFC 3339 time with nanoseconds (2026-10-12T19:59:58.123456789Z) to an aware datetime."""
    match = DOCKER_TIME_PATTERN.match(text.strip())
    if match is None:
        raise SmokeConfigError(f"Cannot read Docker time {text!r}.")
    seconds, fraction, zone = match.groups()
    microseconds = (fraction or "").ljust(6, "0")[:6]  # Python keeps 6 digits; Docker writes up to 9
    offset = "+00:00" if zone == "Z" else zone
    return datetime.fromisoformat(f"{seconds}.{microseconds}{offset}")


def container_facts(
    container: str, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
) -> dict[str, str]:
    """The container's image id, state and start time, from `docker container inspect`."""
    template = "{{.Image}} {{.State.Status}} {{.State.StartedAt}}"
    image_id, state, started_at = run_docker(
        ["docker", "container", "inspect", "--format", template, container], run
    ).split()
    return {"name": container, "image_id": image_id, "state": state, "started_at": started_at}


def check_container(facts: Mapping[str, str], image_id: str) -> tuple[bool, str]:
    """The container is running and was created from image_id."""
    matches = facts["image_id"] == image_id
    passed = facts["state"] == "running" and matches
    return passed, f"container {facts['name']}: state={facts['state']}, image matches --docker-image: {matches}"


def container_start_to_ready_s(facts: Mapping[str, str], readiness: Mapping[str, Any]) -> float | None:
    """Seconds from Docker's State.StartedAt to the first healthy answer (None when not ready)."""
    if not readiness.get("ready_at_utc"):
        return None
    ready_at = datetime.fromisoformat(readiness["ready_at_utc"])
    return round((ready_at - parse_docker_time(facts["started_at"])).total_seconds(), 3)


def merge_run(output: Path, target: str, run_entry: dict[str, Any]) -> dict[str, Any]:
    """The results document with this target's entry replaced and other targets kept."""
    document: dict[str, Any] = {"description": RESULTS_DESCRIPTION, "runs": {}}
    if output.is_file():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
        except ValueError as error:
            raise SmokeConfigError(f"{display_path(output)} is not valid JSON; move it away first.") from error
        if isinstance(existing, dict) and isinstance(existing.get("runs"), dict):
            document["runs"] = dict(existing["runs"])
    document["runs"][target] = run_entry
    document["runs"] = dict(sorted(document["runs"].items()))
    return document


def write_json_atomically(path: Path, document: Mapping[str, Any]) -> None:
    """Write to a temporary file next to path, then rename it over path (readers never see half a file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def smoke(
    args: argparse.Namespace,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
    docker_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(run entry, readiness). The run entry is None when the server never became ready."""
    base_url = validate_target(args.url, args.target)
    if args.requests < 1:
        raise SmokeConfigError("--requests must be at least 1.")
    if args.container and not args.docker_image:
        raise SmokeConfigError("--container needs --docker-image (the image it must have been started from).")
    provenance_run = RunProvenance.start(GENERATED_BY)
    started_at = clock()
    headers = {"User-Agent": "uwcopilot-smoke-deploy/1"}
    with httpx.Client(
        base_url=base_url, timeout=args.timeout_s, transport=transport, headers=headers, follow_redirects=False
    ) as client:
        readiness = wait_until_ready(client, args.wait_s, POLL_INTERVAL_S[args.target], started_at, clock, sleep)
        if not readiness["ready"]:
            return None, readiness
        first = {path: timed_get(client, path, clock) for path in CHECKED_PATHS}
        unknown = timed_get(client, UNKNOWN_API_PATH, clock)
        samples = measure_latency(client, CHECKED_PATHS, args.requests, clock)

    checks = run_checks(first, unknown, [*first.values(), unknown, *samples])
    docker_facts = docker_image_facts(args.docker_image, docker_run) if args.docker_image else None
    container = container_facts(args.container, docker_run) if args.container else None
    if container is not None and docker_facts is not None:
        checks.record("container_runs_image", *check_container(container, docker_facts["id"]))
        readiness["container_started_at"] = container["started_at"]
        readiness["container_start_to_ready_s"] = container_start_to_ready_s(container, readiness)
    latency, status_counts = latency_report(samples, CHECKED_PATHS)
    health = first[HEALTH_PATH].json() or {}
    entry = {
        "target": {"kind": args.target, "label": TARGET_LABELS[args.target], "url": base_url},
        "measured_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": {
            "requests_per_path": args.requests,
            "paths": list(CHECKED_PATHS),
            "timeout_s": args.timeout_s,
            "wait_s": args.wait_s,
            "poll_interval_s": POLL_INTERVAL_S[args.target],
            "client": f"httpx {httpx.__version__}, one client, one request at a time",
            "percentile": "nearest rank",
        },
        "all_checks_passed": checks.all_passed,
        "checks": checks.checks,
        "readiness": readiness,
        "first_request_ms": {path: round(sample.seconds * 1000, 2) for path, sample in first.items()},
        "latency_ms": latency,
        "status_counts": status_counts,
        "server": {"version": health.get("version"), "git_sha": health.get("git_sha")},
        "docker_image": docker_facts,
        "caveats": list(CAVEATS),
    }
    entry["provenance"] = provenance_run.finish(
        corpus_checksum="not applicable (no retrieval corpus is used)",
        data_snapshots={"deployment_target": {"kind": args.target, "url": base_url}},
    )
    return entry, readiness


def print_summary(entry: Mapping[str, Any]) -> None:
    """A short human-readable summary of one run."""
    print(f"Target: {entry['target']['kind']} ({entry['target']['url']})")
    print(f"  {entry['target']['label']}")
    readiness = entry["readiness"]
    print(f"Ready {readiness['seconds_until_ready']} s after the script started ({readiness['attempts']} attempts)")
    if readiness.get("container_start_to_ready_s") is not None:
        print(f"Container start to first healthy answer: {readiness['container_start_to_ready_s']} s")
    for check in entry["checks"]:
        print(f"  [{'PASS' if check['passed'] else 'FAIL'}] {check['name']}: {check['detail']}")
    for path, summary in entry["latency_ms"].items():
        print(f"  {path:<12} n={summary['n']} p50={summary['p50_ms']} ms p95={summary['p95_ms']} ms")
    if entry["docker_image"]:
        image = entry["docker_image"]
        print(f"Image {image['image']}: docker image ls size {image['size_from_docker_image_ls']}")


def main(
    argv: Sequence[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
    docker_run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    """Run the smoke test, update the results file, and return the exit code."""
    args = parse_args(argv)
    try:
        entry, readiness = smoke(args, transport, clock, sleep, docker_run)
        if entry is None:
            print(
                f"Not ready: {HEALTH_PATH} did not answer 200 within {args.wait_s} s "
                f"({readiness['attempts']} attempts, last: {readiness['last_answer']}). Nothing was written.",
                file=sys.stderr,
            )
            return 1
        document = merge_run(args.output, args.target, entry)
    except SmokeConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    write_json_atomically(args.output, document)
    print_summary(entry)
    print(f"Wrote {display_path(args.output)} (runs.{args.target})")
    return 0 if entry["all_checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

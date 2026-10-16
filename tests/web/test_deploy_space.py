"""Tests for scripts/deploy_space.py, the Space card, the Dockerfile and .dockerignore.

The Hub is a fake object and the Space's health endpoint is an httpx.MockTransport: no network, no Docker.
"""

import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

import deploy_space
from deploy_space import (
    DeployError,
    DeployInputError,
    build_bundle,
    push_bundle,
    stamp_git_sha,
    wait_for_version,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_SHA = "0123456789abcdef0123456789abcdef01234567"
OLD_SHA = "fedcba9876543210fedcba9876543210fedcba98"
SPACE_ID = "owner/fundproof"
CARD_COLORS = {"red", "yellow", "green", "blue", "indigo", "purple", "pink", "gray"}
DEMO_AND_BUILD_FILES = (
    "eval/applications/APP-C-001.json",
    "eval/startup_applications/APP-S-001.json",
    "results/kyc_metrics.json",
    "scripts/download_data.py",
    "scripts/build_index.py",
    "scripts/build_studio_snapshots.py",
)


def write(path: Path, text: str = "x") -> None:
    """Create a file and its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A small repo with the files a bundle needs and the ones it must leave out."""
    root = tmp_path / "repo"
    write(root / "Dockerfile", "FROM python\nARG UWC_GIT_SHA=\nENV UWC_GIT_SHA=${UWC_GIT_SHA}\n")
    for name in (".dockerignore", "pyproject.toml", "uv.lock", "src/uwcopilot/app.py", "config/a.yaml"):
        write(root / name)
    for name in ("corpus/guide.md", "frontend/package.json", "frontend/app/page.tsx"):
        write(root / name)
    # The Lender Desk demo inputs and the three build-time scripts (stages "assets" and "studio-snapshots").
    for name in DEMO_AND_BUILD_FILES:
        write(root / name)
    write(root / "deploy/space/README.md", "---\ntitle: Fundproof\nsdk: docker\n---\n# Card\n")
    excluded = (
        "src/uwcopilot/__pycache__/app.cpython-311.pyc",
        "frontend/node_modules/next/index.js",
        "frontend/.next/cache/x",
        "frontend/out/index.html",
        "frontend/.env.local",
        "frontend/tsconfig.tsbuildinfo",
        "frontend/scripts/screenshots.py",
        "frontend/scripts/screenshots_mock.py",
        "frontend/scripts/record_mock_fixtures.py",
        "frontend/mock-api/fixtures/queue.json",
        "data/web/store/secret.bin",
        "data/raw/ofac/sdn.csv",
        "data/studio/cache/ab/abc.body",
        "data/studio/snapshots/houston_tx/2026-10-13/manifest.json",
        "data/studio/reference/list1_2023.xlsx",
        "scripts/studio_fetch_delineation.py",
        "results/risk_metrics.json",
        "scripts/eval_agent.py",
        "eval/generation/questions.jsonl",
        ".env",
        "README.md",
    )
    for name in excluded:
        write(root / name, "must not be bundled")
    # npm puts symlinks in node_modules/.bin; the folder is excluded, so they must not matter.
    (root / "frontend/node_modules/.bin").mkdir(parents=True)
    (root / "frontend/node_modules/.bin/next").symlink_to(root / "frontend/node_modules/next/index.js")
    return root


class FakeHub:
    """Records calls like HfApi would receive them; runtime stages and hosts are scripted."""

    def __init__(self, stages: list[str] | None = None, host: str | None = "https://owner-fundproof.hf.space") -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.stages = list(stages or ["RUNNING"])
        self.host = host

    def create_repo(self, repo_id: str, **kwargs: Any) -> str:
        self.calls.append(("create_repo", (repo_id,), kwargs))
        return f"https://huggingface.co/spaces/{repo_id}"

    def upload_folder(self, **kwargs: Any) -> None:
        self.calls.append(("upload_folder", (), kwargs))

    def add_space_secret(self, repo_id: str, key: str, value: str, **kwargs: Any) -> None:
        self.calls.append(("add_space_secret", (repo_id, key, value), kwargs))

    def get_space_runtime(self, repo_id: str) -> Any:
        stage = self.stages.pop(0) if len(self.stages) > 1 else self.stages[0]
        return type("Runtime", (), {"stage": stage})()

    def space_info(self, repo_id: str) -> Any:
        return type("Info", (), {"host": self.host})()


class FakeClock:
    """Moves forward only when sleep() is called."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def health_client(shas: list[str]) -> httpx.Client:
    """A client whose /api/health answers with each git sha in turn (then the last one)."""
    remaining = list(shas)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/health"
        sha = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return httpx.Response(200, json={"status": "ok", "version": "0.1.0", "git_sha": sha})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_bundle_has_the_allow_listed_files_only(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    files = {path.as_posix() for path in build_bundle(fake_repo, out, GIT_SHA)}

    expected = {
        "Dockerfile",
        ".dockerignore",
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "src/uwcopilot/app.py",
        "config/a.yaml",
        "corpus/guide.md",
        "frontend/package.json",
        "frontend/app/page.tsx",
        *DEMO_AND_BUILD_FILES,
    }
    assert files == expected
    on_disk = {path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()}
    assert on_disk == expected


def test_bundle_readme_is_the_space_card_and_the_dockerfile_names_the_commit(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    build_bundle(fake_repo, out, GIT_SHA)

    assert (out / "README.md").read_text().startswith("---\ntitle: Fundproof")
    assert f"ARG UWC_GIT_SHA={GIT_SHA}\n" in (out / "Dockerfile").read_text()


def test_bundle_refuses_a_non_empty_folder(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    write(out / "old.txt")
    with pytest.raises(DeployInputError, match="not empty"):
        build_bundle(fake_repo, out, GIT_SHA)


def test_bundle_refuses_symlinks(fake_repo: Path, tmp_path: Path) -> None:
    (fake_repo / "config" / "link.yaml").symlink_to(fake_repo / ".env")
    with pytest.raises(DeployInputError, match="symlink"):
        build_bundle(fake_repo, tmp_path / "bundle", GIT_SHA)
    assert not (tmp_path / "bundle").exists()  # nothing is copied when the file list is refused


@pytest.mark.parametrize("bad_sha", ["", "abc", "ABCDEF1", "0123456789abcdef; rm -rf /", "g" * 40])
def test_git_sha_must_be_hex(bad_sha: str) -> None:
    with pytest.raises(DeployInputError):
        stamp_git_sha("ARG UWC_GIT_SHA=\n", bad_sha)


def test_a_bad_git_sha_stops_the_bundle_before_anything_is_copied(fake_repo: Path, tmp_path: Path) -> None:
    with pytest.raises(DeployInputError, match="git-sha"):
        build_bundle(fake_repo, tmp_path / "bundle", "not-a-sha")
    assert not (tmp_path / "bundle").exists()


def test_the_real_dockerfile_has_exactly_one_git_sha_argument() -> None:
    stamped = stamp_git_sha((REPO_ROOT / "Dockerfile").read_text(encoding="utf-8"), GIT_SHA)
    assert stamped.count(f"ARG UWC_GIT_SHA={GIT_SHA}") == 1
    with pytest.raises(DeployInputError, match="exactly one"):
        stamp_git_sha("FROM python\n", GIT_SHA)


def test_push_creates_a_public_docker_space_and_replaces_its_files(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    build_bundle(fake_repo, out, GIT_SHA)
    hub = FakeHub()

    url = push_bundle(hub, SPACE_ID, out, GIT_SHA)

    assert url == "https://huggingface.co/spaces/owner/fundproof"
    create_call, secret_call, upload_call = hub.calls
    (create_name, create_args, create_kwargs), (secret_name, secret_args, _) = create_call, secret_call
    upload_name, _, upload_kwargs = upload_call
    assert create_name == "create_repo" and create_args == (SPACE_ID,)
    assert secret_name == "add_space_secret" and secret_args[:2] == (SPACE_ID, "UWC_SESSION_SECRET")
    assert len(secret_args[2]) >= 32
    assert create_kwargs == {"repo_type": "space", "space_sdk": "docker", "exist_ok": True, "private": False}
    assert upload_name == "upload_folder"
    assert upload_kwargs["repo_id"] == SPACE_ID and upload_kwargs["repo_type"] == "space"
    assert upload_kwargs["delete_patterns"] == "*"
    assert upload_kwargs["folder_path"] == str(out)
    assert upload_kwargs["commit_message"] == f"Deploy {GIT_SHA[:12]}"


def test_push_without_secrets_stops_before_any_hub_call(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    created: list[str] = []

    def factory(token: str) -> FakeHub:
        created.append(token)
        return FakeHub()

    code = deploy_space.main(
        ["push", "--bundle", str(tmp_path)], api_factory=factory, environ={"HF_SPACE_ID": SPACE_ID}
    )

    assert code == 2
    assert created == []
    assert "HF_TOKEN is not set" in capsys.readouterr().err


def test_a_bad_space_id_is_refused_and_the_token_is_never_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {"HF_TOKEN": "hf_secret_value_123", "HF_SPACE_ID": "not a space id"}
    code = deploy_space.main(["push", "--bundle", str(tmp_path)], api_factory=lambda token: FakeHub(), environ=environ)

    captured = capsys.readouterr()
    assert code == 2
    assert "hf_secret_value_123" not in captured.out + captured.err


def test_wait_returns_the_host_once_the_space_serves_the_commit() -> None:
    clock = FakeClock()
    hub = FakeHub(stages=["RUNNING", "BUILDING", "RUNNING"])
    with health_client([OLD_SHA, OLD_SHA, GIT_SHA]) as client:
        host = wait_for_version(
            hub, SPACE_ID, GIT_SHA, client, timeout_s=600, poll_s=20, clock=clock, sleep=clock.sleep
        )

    assert host == "owner-fundproof.hf.space"
    assert clock.now == 40


def test_wait_ignores_an_old_error_stage_during_the_grace_period_then_fails_on_it() -> None:
    clock = FakeClock()
    hub = FakeHub(stages=["BUILD_ERROR"])
    with health_client([OLD_SHA]) as client, pytest.raises(DeployError, match="BUILD_ERROR"):
        wait_for_version(hub, SPACE_ID, GIT_SHA, client, timeout_s=600, poll_s=20, clock=clock, sleep=clock.sleep)

    assert clock.now >= deploy_space.ERROR_GRACE_S


def test_wait_times_out() -> None:
    clock = FakeClock()
    hub = FakeHub(stages=["BUILDING"], host=None)
    with health_client([OLD_SHA]) as client, pytest.raises(DeployError, match="Timed out"):
        wait_for_version(hub, SPACE_ID, GIT_SHA, client, timeout_s=100, poll_s=20, clock=clock, sleep=clock.sleep)


def test_space_card_metadata() -> None:
    text = (REPO_ROOT / "deploy" / "space" / "README.md").read_text(encoding="utf-8")
    _, front_matter, body = text.split("---\n", 2)
    card = yaml.safe_load(front_matter)

    assert card["title"] == "Fundproof"
    assert card["sdk"] == "docker"
    assert card["app_port"] == 7860
    assert card["hf_oauth"] is True
    assert card["pinned"] is False
    assert card["colorFrom"] in CARD_COLORS and card["colorTo"] in CARD_COLORS
    assert 0 < len(card["short_description"]) <= 60
    assert "license" not in card  # choosing a license is the owners' decision
    assert "Not financial, legal or lending advice" in body
    assert "SYNTHETIC" in body


def test_dockerignore_starts_from_nothing_and_never_lets_secrets_or_data_back_in() -> None:
    rules = [
        line.strip()
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert rules[0] == "*"
    allowed = {rule[1:].rstrip("/") for rule in rules if rule.startswith("!")}
    assert allowed == {
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "src",
        "config",
        "corpus",
        "frontend",
        # the Lender Desk demo's SYNTHETIC applications, the screening threshold and the three build-time scripts
        "eval/applications",
        "eval/startup_applications",
        "results/kyc_metrics.json",
        "scripts/download_data.py",
        "scripts/build_index.py",
        "scripts/build_studio_snapshots.py",
    }
    assert {"**/.env", "frontend/node_modules", "frontend/out", "frontend/mock-api"} <= set(rules)


def dockerfile_copy_sources(dockerfile: str) -> list[str]:
    """The sources of every COPY instruction that copies from the build context (a COPY --from=<stage> does not)."""
    sources: list[str] = []
    for line in dockerfile.replace("\\\n", " ").splitlines():
        words = line.split()
        if not words or words[0] != "COPY" or any(word.startswith("--from=") for word in words):
            continue
        paths = [word for word in words[1:] if not word.startswith("--")]
        sources.extend(paths[:-1])  # the last one is the destination
    return sources


def test_every_file_the_dockerfile_copies_is_in_the_space_bundle() -> None:
    # The Space builds from the bundle, but CI builds the repo root (filtered by .dockerignore): a COPY source left out
    # of the bundle's allow-list would pass CI and fail only on Hugging Face.
    sources = dockerfile_copy_sources((REPO_ROOT / "Dockerfile").read_text(encoding="utf-8"))
    # build_bundle adds the Space card as README.md (pyproject.toml names a README; any Markdown file works).
    bundled = {path.as_posix() for path in deploy_space.bundle_file_list(REPO_ROOT)} | {"README.md"}
    assert "scripts/build_studio_snapshots.py" in sources and "src" in sources
    for source in sources:
        name = source.rstrip("/")
        assert name in bundled or any(path.startswith(name + "/") for path in bundled), source


def test_the_deploy_job_waits_longer_than_the_snapshot_step_alone_can_take() -> None:
    # Hugging Face builds the image after the push; with the CENSUS_API_KEY Space secret the studio-snapshots step
    # may run up to STUDIO_SNAPSHOT_TIMEOUT_S + 180 s + the 30 s kill grace before the other stages finish.
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    limit = re.search(r"^ARG STUDIO_SNAPSHOT_TIMEOUT_S=(\d+)$", dockerfile, flags=re.MULTILINE)
    outer = re.search(r'timeout --kill-after=(\d+) "\$\(\(STUDIO_SNAPSHOT_TIMEOUT_S \+ (\d+)\)\)"', dockerfile)
    assert limit and outer
    step_bound_s = int(limit.group(1)) + int(outer.group(2)) + int(outer.group(1))
    workflow_text = (REPO_ROOT / ".github" / "workflows" / "deploy-space.yml").read_text(encoding="utf-8")
    wait = re.search(r"deploy_space\.py wait [^\n]*--timeout-s (\d+)", workflow_text)
    assert wait
    wait_s = int(wait.group(1))
    deploy_job = yaml.safe_load(workflow_text)["jobs"]["deploy"]
    assert wait_s > step_bound_s  # the other stages must fit in the rest; their time on the Space is not measured
    assert deploy_job["timeout-minutes"] * 60 > wait_s
    assert deploy_space.DEFAULT_WAIT_TIMEOUT_S == wait_s


def test_the_image_builds_the_lender_demo_assets_and_screens_with_ofac_only() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "python scripts/download_data.py --datasets ofac_sdn" in dockerfile
    assert "python scripts/build_index.py" in dockerfile
    assert "UWC_SCREENING_LISTS=ofac" in dockerfile
    assert "--datasets opensanctions" not in dockerfile  # 445 MB and CC BY-NC: never in the image
    for copied in ("eval/applications", "eval/startup_applications", "results/kyc_metrics.json", "qdrant_storage"):
        assert f"COPY {copied}" in dockerfile or f"/app/{copied}" in dockerfile


def test_push_sets_a_fresh_session_secret_each_time_and_never_prints_it(
    fake_repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "bundle"
    build_bundle(fake_repo, out, GIT_SHA)
    hub = FakeHub()
    environ = {"HF_TOKEN": "hf_secret_value_123", "HF_SPACE_ID": SPACE_ID}

    assert deploy_space.main(["push", "--bundle", str(out)], api_factory=lambda token: hub, environ=environ) == 0
    assert deploy_space.main(["push", "--bundle", str(out)], api_factory=lambda token: hub, environ=environ) == 0

    values = [args[2] for name, args, _ in hub.calls if name == "add_space_secret"]
    assert len(values) == 2 and values[0] != values[1]
    printed = capsys.readouterr()
    assert all(value not in printed.out + printed.err for value in values)


def test_keep_session_secret_skips_the_secret(fake_repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "bundle"
    build_bundle(fake_repo, out, GIT_SHA)
    hub = FakeHub()
    environ = {"HF_TOKEN": "hf_secret_value_123", "HF_SPACE_ID": SPACE_ID}

    code = deploy_space.main(
        ["push", "--bundle", str(out), "--keep-session-secret"], api_factory=lambda token: hub, environ=environ
    )

    assert code == 0
    assert [name for name, _, _ in hub.calls] == ["create_repo", "upload_folder"]

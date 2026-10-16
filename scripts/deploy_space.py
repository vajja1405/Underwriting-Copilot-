"""Build the Hugging Face Space bundle, push it, and wait for the new version. Used by deploy-space.yml.

    uv run python scripts/deploy_space.py bundle --out build/space --git-sha "$(git rev-parse HEAD)"
    HF_TOKEN=... HF_SPACE_ID=owner/name uv run python scripts/deploy_space.py push --bundle build/space
    HF_TOKEN=... HF_SPACE_ID=owner/name uv run python scripts/deploy_space.py wait --git-sha "$(git rev-parse HEAD)"

bundle: copies an explicit allow-list into an EMPTY folder: Dockerfile, .dockerignore, pyproject.toml, uv.lock,
    src/, config/, corpus/, the frontend SOURCE (no node_modules, .next or out: the Space builds them; no mock API or
    screenshot helpers), the SYNTHETIC demo applications (eval/applications, eval/startup_applications),
    results/kyc_metrics.json and the three build-time scripts (scripts/download_data.py, scripts/build_index.py,
    scripts/build_studio_snapshots.py), plus deploy/space/README.md as README.md (the Space card). The Studio
    snapshot builder, the metro and business-type configs it reads and the Overture settings are in src/ and
    config/; it reads no other file (no data/ file is ever bundled). The commit hash is written into the bundle's
    Dockerfile line `ARG UWC_GIT_SHA=`, so GET /api/health on the Space names the commit it runs.
push: create_repo(repo_type="space", space_sdk="docker", exist_ok=True, private=False), then sets the Space secret
    UWC_SESSION_SECRET to a fresh random value (the app refuses to start in a Space without it; a new value on each
    deploy signs everyone out, which is acceptable for this prototype; --keep-session-secret skips this when the
    owners manage the secret themselves), then upload_folder with delete_patterns="*": the Space ends up with exactly
    the bundle's files (the Hub always keeps .gitattributes). No other Space secret is ever set here; the owners set
    the rest in the Space settings (docs/deployment.md). The generated value is never printed or written anywhere.
wait: polls the Space's runtime stage and https://<space host>/api/health until the health answer names the pushed
    commit. Fails early on a build/runtime error stage (after a grace period, because right after a push the Hub can
    still report the previous version's stage). Prints space_host=<host> for the smoke test.

HF_TOKEN and HF_SPACE_ID come from the environment and are never printed. Exit code 0 on success, 1 on failure,
2 on bad input or missing secrets.
"""

import argparse
import os
import re
import secrets
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Protocol

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SPACE_CARD = Path("deploy/space/README.md")
# Files and folders copied into the bundle, as they are in the repo: everything the Dockerfile copies. The eval/
# folders are the Lender Desk demo's SYNTHETIC applications; results/kyc_metrics.json is the screening threshold; the
# first two scripts download the public OFAC lists and build the guideline index while the Space builds the image;
# build_studio_snapshots.py builds the real metros' Studio snapshots then (only with the CENSUS_API_KEY build secret).
BUNDLE_FILES: tuple[str, ...] = (
    "Dockerfile",
    ".dockerignore",
    "pyproject.toml",
    "uv.lock",
    "results/kyc_metrics.json",
    "scripts/download_data.py",
    "scripts/build_index.py",
    "scripts/build_studio_snapshots.py",
)
BUNDLE_DIRS: tuple[str, ...] = ("src", "config", "corpus", "frontend", "eval/applications", "eval/startup_applications")
# Never copied, at any depth (file or folder names), whatever folder they are in.
EXCLUDED_NAMES: tuple[str, ...] = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".env",
    ".env.*",
    ".DS_Store",
    "node_modules",
    ".next",
    "*.tsbuildinfo",
    "next-env.d.ts",
    "npm-debug.log*",
)
# Never copied: paths relative to the repo root.
EXCLUDED_PATHS: tuple[str, ...] = (
    "frontend/out",
    "frontend/scripts/screenshots.py",
    "frontend/scripts/screenshots_mock.py",
    "frontend/scripts/record_mock_fixtures.py",
    "frontend/mock-api",
)
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,40}$")
SPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
DOCKERFILE_SHA_LINE = re.compile(r"^ARG UWC_GIT_SHA=.*$", re.MULTILINE)
ERROR_STAGES = frozenset({"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"})
# How long `wait` waits for the Space to serve the pushed commit (the deploy workflow passes the same value): the
# whole image build, including the Studio snapshot step when the Space has the CENSUS_API_KEY secret. A design choice.
DEFAULT_WAIT_TIMEOUT_S = 3600.0
DEFAULT_POLL_S = 20.0
# Right after a push the Hub can still report the PREVIOUS version's stage; errors count only after this.
ERROR_GRACE_S = 120.0


class DeployError(RuntimeError):
    """The bundle cannot be built or pushed, or the Space did not come up (exit code 1)."""


class DeployInputError(DeployError):
    """Bad arguments, a missing secret, or a repo that lacks a file the bundle needs (exit code 2)."""


class HubApi(Protocol):
    """The part of huggingface_hub.HfApi this script uses (tests pass a fake)."""

    def create_repo(self, repo_id: str, **kwargs: Any) -> Any:
        """Create the repo (exist_ok=True makes this a no-op when it exists)."""

    def upload_folder(self, **kwargs: Any) -> Any:
        """Upload a folder in one commit."""

    def add_space_secret(self, repo_id: str, key: str, value: str, **kwargs: Any) -> Any:
        """Create or overwrite one Space secret."""

    def get_space_runtime(self, repo_id: str) -> Any:
        """The Space's runtime (stage, hardware, ...)."""

    def space_info(self, repo_id: str) -> Any:
        """The Space's info (host, ...)."""


def is_excluded(relative: Path) -> bool:
    """True when a repo-relative path must stay out of the bundle."""
    as_posix = relative.as_posix()
    if any(as_posix == path or as_posix.startswith(path + "/") for path in EXCLUDED_PATHS):
        return True
    return any(fnmatch(part, pattern) for part in relative.parts for pattern in EXCLUDED_NAMES)


def iter_folder(repo_root: Path, folder: str) -> Iterable[Path]:
    """Repo-relative files under folder, skipping excluded paths without entering them (node_modules is large).

    A symlink that is not excluded is refused: it could point outside the repo.
    """
    for current, dirnames, filenames in os.walk(repo_root / folder):
        current_path = Path(current)
        kept: list[str] = []
        for name in sorted(dirnames):
            relative = (current_path / name).relative_to(repo_root)
            if is_excluded(relative):
                continue
            if (current_path / name).is_symlink():
                raise DeployInputError(f"Refusing to bundle a symlink: {relative}")
            kept.append(name)
        dirnames[:] = kept  # os.walk only enters the folders left in this list
        for name in sorted(filenames):
            relative = (current_path / name).relative_to(repo_root)
            if is_excluded(relative):
                continue
            if (current_path / name).is_symlink():
                raise DeployInputError(f"Refusing to bundle a symlink: {relative}")
            yield relative


def bundle_file_list(repo_root: Path) -> list[Path]:
    """Every repo-relative file that goes into the bundle (the Space card is added separately)."""
    files: list[Path] = []
    for name in BUNDLE_FILES:
        if not (repo_root / name).is_file():
            raise DeployInputError(f"Missing {name} in {repo_root}")
        files.append(Path(name))
    for folder in BUNDLE_DIRS:
        if not (repo_root / folder).is_dir():
            raise DeployInputError(f"Missing folder {folder}/ in {repo_root}")
        files.extend(iter_folder(repo_root, folder))
    return files


def stamp_git_sha(dockerfile_text: str, git_sha: str) -> str:
    """The Dockerfile with `ARG UWC_GIT_SHA=<git_sha>`; exactly one such line must exist."""
    if not GIT_SHA_PATTERN.match(git_sha):
        raise DeployInputError(f"--git-sha must be 7 to 40 lowercase hex characters (got {git_sha!r}).")
    stamped, count = DOCKERFILE_SHA_LINE.subn(f"ARG UWC_GIT_SHA={git_sha}", dockerfile_text)
    if count != 1:
        raise DeployInputError(f"Expected exactly one 'ARG UWC_GIT_SHA=' line in the Dockerfile, found {count}.")
    return stamped


def build_bundle(repo_root: Path, out_dir: Path, git_sha: str) -> list[Path]:
    """Copy the allow-listed files into out_dir (which must be empty or missing); returns the bundle's files."""
    if out_dir.exists() and any(out_dir.iterdir()):
        raise DeployInputError(f"{out_dir} is not empty; give an empty or new folder.")
    card = repo_root / SPACE_CARD
    if not card.is_file():
        raise DeployInputError(f"Missing the Space card {SPACE_CARD}")
    # Everything is checked before anything is copied: the file list, then the commit stamp.
    files = bundle_file_list(repo_root)
    stamped_dockerfile = stamp_git_sha((repo_root / "Dockerfile").read_text(encoding="utf-8"), git_sha)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for relative in files:
        destination = out_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo_root / relative, destination)
        written.append(relative)
    shutil.copyfile(card, out_dir / "README.md")
    written.append(Path("README.md"))
    (out_dir / "Dockerfile").write_text(stamped_dockerfile, encoding="utf-8")
    return sorted(written)


def require_env(name: str, environ: Mapping[str, str]) -> str:
    """A required environment variable (the value is never printed)."""
    value = (environ.get(name) or "").strip()
    if not value:
        raise DeployInputError(f"{name} is not set. Add it as a GitHub repository secret (see docs/deployment.md).")
    return value


def check_space_id(space_id: str) -> str:
    """'owner/name' or DeployInputError."""
    if not SPACE_ID_PATTERN.match(space_id):
        raise DeployInputError("HF_SPACE_ID must look like 'owner/name'.")
    return space_id


SESSION_SECRET_NAME = "UWC_SESSION_SECRET"
SESSION_SECRET_BYTES = 48  # secrets.token_urlsafe(48) gives 64 characters; the app needs at least 32


def set_session_secret(api: HubApi, space_id: str, make_secret: Callable[[int], str] = secrets.token_urlsafe) -> None:
    """Give the Space a fresh random UWC_SESSION_SECRET (the value is never printed or stored anywhere else)."""
    api.add_space_secret(
        space_id,
        SESSION_SECRET_NAME,
        make_secret(SESSION_SECRET_BYTES),
        description="Signs session cookies. Set by scripts/deploy_space.py on each deploy (new value = sign-out).",
    )


def push_bundle(
    api: HubApi, space_id: str, bundle: Path, git_sha: str | None, rotate_session_secret: bool = True
) -> str:
    """Create the Space if needed, set its session secret, and replace its files with the bundle. Returns the URL."""
    if not (bundle / "README.md").is_file() or not (bundle / "Dockerfile").is_file():
        raise DeployInputError(f"{bundle} does not look like a bundle (README.md and Dockerfile are required).")
    if git_sha is not None and not GIT_SHA_PATTERN.match(git_sha):
        raise DeployInputError(f"--git-sha must be 7 to 40 lowercase hex characters (got {git_sha!r}).")
    api.create_repo(space_id, repo_type="space", space_sdk="docker", exist_ok=True, private=False)
    if rotate_session_secret:
        set_session_secret(api, space_id)
    message = f"Deploy {git_sha[:12]}" if git_sha else "Deploy"
    api.upload_folder(
        repo_id=space_id, repo_type="space", folder_path=str(bundle), delete_patterns="*", commit_message=message
    )
    return f"https://huggingface.co/spaces/{space_id}"


def space_host(api: HubApi, space_id: str) -> str | None:
    """The Space's own host name (for example owner-name.hf.space), or None while the Hub does not report one."""
    host = getattr(api.space_info(space_id), "host", None)
    if not host:
        return None
    return str(host).removeprefix("https://").removeprefix("http://").rstrip("/")


def health_git_sha(client: httpx.Client, host: str) -> str | None:
    """git_sha from https://<host>/api/health, or None when it does not answer 200 with JSON."""
    try:
        response = client.get(f"https://{host}/api/health")
        data = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return data.get("git_sha") if isinstance(data, dict) else None


def wait_for_version(
    api: HubApi,
    space_id: str,
    git_sha: str,
    client: httpx.Client,
    timeout_s: float = DEFAULT_WAIT_TIMEOUT_S,
    poll_s: float = DEFAULT_POLL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Wait until the Space serves git_sha; returns its host. DeployError on an error stage or timeout."""
    started = clock()
    last_stage = "unknown"
    while True:
        elapsed = clock() - started
        last_stage = str(getattr(api.get_space_runtime(space_id), "stage", "unknown"))
        if last_stage in ERROR_STAGES and elapsed >= ERROR_GRACE_S:
            raise DeployError(f"The Space reports stage {last_stage}; see its build/container logs on the Hub.")
        host = space_host(api, space_id)
        if host and health_git_sha(client, host) == git_sha:
            print(f"The Space serves {git_sha[:12]} after {elapsed:.0f} s (stage {last_stage}).")
            return host
        if elapsed >= timeout_s:
            raise DeployError(f"Timed out after {timeout_s:.0f} s; last stage {last_stage}.")
        sleep(poll_s)


def write_github_output(name: str, value: str) -> None:
    """Append name=value to $GITHUB_OUTPUT when running in GitHub Actions (always also printed)."""
    print(f"{name}={value}")
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def make_hub_api(token: str) -> HubApi:
    """The real Hub client (imported here so `bundle` works without huggingface_hub)."""
    from huggingface_hub import HfApi

    return HfApi(token=token)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Command-line arguments (see the module docstring)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("bundle", help="copy the deployable files into an empty folder")
    bundle.add_argument("--out", type=Path, required=True)
    bundle.add_argument("--git-sha", required=True)
    push = commands.add_parser("push", help="create the Space if needed and upload the bundle")
    push.add_argument("--bundle", type=Path, required=True)
    push.add_argument("--git-sha", default=None, help="only used in the commit message")
    push.add_argument(
        "--keep-session-secret",
        action="store_true",
        help="do not set UWC_SESSION_SECRET (use when the owners manage that Space secret themselves)",
    )
    wait = commands.add_parser("wait", help="wait until the Space serves this commit")
    wait.add_argument("--git-sha", required=True)
    wait.add_argument("--timeout-s", type=float, default=DEFAULT_WAIT_TIMEOUT_S)
    wait.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    api_factory: Callable[[str], HubApi] = make_hub_api,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run one command; returns the exit code."""
    args = parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        if args.command == "bundle":
            files = build_bundle(REPO_ROOT, args.out, args.git_sha)
            size = sum((args.out / path).stat().st_size for path in files)
            print(f"Bundle {args.out}: {len(files)} files, {size} bytes, Dockerfile stamped with {args.git_sha[:12]}")
            return 0
        token = require_env("HF_TOKEN", env)
        space_id = check_space_id(require_env("HF_SPACE_ID", env))
        api = api_factory(token)
        if args.command == "push":
            url = push_bundle(api, space_id, args.bundle, args.git_sha, not args.keep_session_secret)
            print(f"Uploaded the bundle to {url}")
            return 0
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            host = wait_for_version(api, space_id, args.git_sha, client, args.timeout_s, args.poll_s)
        write_github_output("space_host", host)
        return 0
    except DeployInputError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except DeployError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

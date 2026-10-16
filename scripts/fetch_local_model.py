"""Import a pinned open-weight model into a local Ollama server from a Docker Hub model artifact.

Why Docker Hub: when the models were imported on the build machine (2026-10-12), the Ollama registry and Hugging
Face were not reachable, but Docker Hub publishes the same open-weight models as GGUF "model artifacts"
(repository ai/qwen2.5). The weights file is content-addressed (its SHA-256 is its name), so the download can be
verified byte for byte. The artifact's license layer (Apache-2.0) is not the only license statement: the config
also records the upstream Hugging Face model card license, and the script prints both.

What it does for each model listed under `models:` in config/generation_eval.yaml:
1. Asks auth.docker.io for an anonymous pull token for the repository.
2. Reads the OCI manifest of the tag (GET registry-1.docker.io/v2/<repository>/manifests/<tag>) and checks that
   its GGUF layer digest equals the pinned gguf_sha256. A different digest stops the script: the tag now points
   at different weights, which needs a migration review, not a silent update. The license layer is checked
   against the pinned license_layer_sha256 the same way (also with --check-only): a different license text
   means the recorded license facts must be reviewed before anything is imported.
3. Downloads the GGUF layer and the license layer, hashing while writing, and verifies SHA-256 and size before the
   file is moved into place. A file that is already present and verifies is not downloaded again.
4. Writes a Modelfile `FROM <blob path>` and runs `ollama create <name> -f <Modelfile>`.
5. Reads GET /api/tags and prints the Ollama digest, which should equal ollama_digest in the config (Ollama
   computes it; a different Ollama version may produce a different value, so a mismatch is reported, not hidden).

Docker Hub limits anonymous pulls per IP address. An HTTP 429 answer stops the script with a clear message;
wait (the limit resets over hours) or log in with a Docker Hub account and retry.

Getting the ollama binary itself:
- Normal install: follow https://ollama.com/download (Linux, macOS or Windows), then run `ollama serve`.
- Without access to ollama.com: the binary is in the Docker Hub image ollama/ollama. Run the image
  (`docker run -d -p 127.0.0.1:11434:11434 ollama/ollama`), or copy usr/bin/ollama and the CPU backend libraries
  in usr/lib/ollama (libggml-*.so, libllama*.so) out of its layers and start it with
  `OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=<folder> ./usr/bin/ollama serve`. The build machine used the
  linux/amd64 image manifest sha256:44833c52df2a164cc1238a96ce100123c8e54082b85c5e235a9cb5a7adc55f27, which reports
  version 0.40.3 at GET /api/version.

This script downloads gigabytes and needs a running Ollama server, so it never runs in CI (it refuses when the CI
environment variable is set). Nothing it downloads is committed: blobs go to .cache/ollama/blobs (gitignored).

Usage:
    python scripts/fetch_local_model.py                                   # the champion model
    python scripts/fetch_local_model.py --model all --ollama-bin /path/to/ollama
    python scripts/fetch_local_model.py --check-only                      # verify the pinned digest, no download
"""

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

from uwcopilot.common.files import human_size, sha256_of_file
from uwcopilot.common.paths import CACHE_DIR, CONFIG_DIR
from uwcopilot.llm.ollama_client import find_model_entry, strip_digest_prefix
from uwcopilot.llm.settings import SettingsError, load_llm_settings, validate_base_url

CONFIG_FILE = CONFIG_DIR / "generation_eval.yaml"
DEFAULT_BLOB_DIR = CACHE_DIR / "ollama" / "blobs"
DOCKER_HUB_PREFIX = "docker.io/"
REGISTRY_URL = "https://registry-1.docker.io"
AUTH_URL = "https://auth.docker.io/token"
MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
INDEX_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
GGUF_MEDIA_TYPE = "application/vnd.docker.ai.gguf.v3"
LICENSE_MEDIA_TYPE = "application/vnd.docker.ai.license"
HTTP_TIMEOUT_S = 120.0
CHUNK_BYTES = 1024 * 1024
PROGRESS_EVERY_BYTES = 256 * 1024 * 1024
OLLAMA_CREATE_TIMEOUT_S = 1800


class FetchError(RuntimeError):
    """Anything that stops the import: a registry error, a digest mismatch, a failed `ollama create`."""


class RegistryRateLimited(FetchError):
    """Docker Hub answered HTTP 429 (anonymous pull rate limit)."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="qwen2.5-3b-instruct-q4km", help="model name from the config, or 'all'")
    parser.add_argument("--config", type=Path, default=CONFIG_FILE, help="YAML file with the pinned models")
    parser.add_argument("--blob-dir", type=Path, default=DEFAULT_BLOB_DIR, help="where verified blobs are kept")
    parser.add_argument("--ollama-bin", default="ollama", help="path to the ollama executable")
    parser.add_argument("--ollama-url", default=None, help="Ollama base URL (default: UWC_OLLAMA_BASE_URL)")
    parser.add_argument("--check-only", action="store_true", help="verify the pinned digest; download nothing")
    parser.add_argument("--skip-create", action="store_true", help="download and verify, but do not run ollama")
    return parser.parse_args(argv)


def load_pinned_models(path: Path) -> dict[str, dict[str, Any]]:
    """The `models:` section of config/generation_eval.yaml: {local name: {source, gguf_sha256, ...}}."""
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    models = document.get("models") or {}
    for name, entry in models.items():
        for key in ("source", "gguf_sha256", "gguf_size_bytes", "license_layer_sha256"):
            if key not in entry:
                raise FetchError(f"{path.name}: model {name!r} has no {key!r}.")
    return models


def parse_source(source: str) -> tuple[str, str]:
    """Split 'docker.io/ai/qwen2.5:3B-Q4_K_M' into ('ai/qwen2.5', '3B-Q4_K_M'). Only Docker Hub is supported."""
    if not source.startswith(DOCKER_HUB_PREFIX):
        raise FetchError(f"Only Docker Hub sources ({DOCKER_HUB_PREFIX}...) are supported, got {source!r}.")
    reference = source[len(DOCKER_HUB_PREFIX) :]
    repository, separator, tag = reference.rpartition(":")
    if not separator or not repository or not tag:
        raise FetchError(f"Source {source!r} must look like docker.io/<repository>:<tag>.")
    return repository, tag


def check_reply(reply: httpx.Response, what: str) -> None:
    """Raise RegistryRateLimited on HTTP 429 and FetchError on any other non-200 answer."""
    if reply.status_code == 429:
        remaining = reply.headers.get("ratelimit-remaining", "not given")
        raise RegistryRateLimited(
            f"Docker Hub rate limit reached while fetching {what} (HTTP 429; ratelimit-remaining: {remaining}). "
            "Anonymous pulls are limited per IP address. Wait a few hours, or log in with a Docker Hub account, "
            "then run the script again; verified files already downloaded are kept."
        )
    if reply.status_code != 200:
        raise FetchError(f"Fetching {what} failed with HTTP {reply.status_code}: {reply.text[:200]}")


def registry_token(client: httpx.Client, repository: str) -> str:
    """An anonymous pull token for one Docker Hub repository."""
    params = {"service": "registry.docker.io", "scope": f"repository:{repository}:pull"}
    reply = client.get(AUTH_URL, params=params)
    check_reply(reply, f"a pull token for {repository}")
    token = reply.json().get("token") or reply.json().get("access_token")
    if not token:
        raise FetchError("auth.docker.io answered without a token.")
    return str(token)


def fetch_manifest(client: httpx.Client, repository: str, tag: str, token: str) -> dict[str, Any]:
    """The OCI manifest of repository:tag. A multi-platform index is refused (model artifacts have one manifest)."""
    headers = {"Authorization": f"Bearer {token}", "Accept": MANIFEST_ACCEPT}
    reply = client.get(f"{REGISTRY_URL}/v2/{repository}/manifests/{tag}", headers=headers)
    check_reply(reply, f"the manifest of {repository}:{tag}")
    manifest = reply.json()
    if manifest.get("mediaType") in INDEX_MEDIA_TYPES:
        raise FetchError(f"{repository}:{tag} is a multi-platform index, not a single model artifact manifest.")
    return manifest


def find_layer(manifest: dict[str, Any], media_type: str) -> dict[str, Any]:
    """The one layer with this media type, or FetchError."""
    layers = [layer for layer in manifest.get("layers") or [] if layer.get("mediaType") == media_type]
    if len(layers) != 1:
        raise FetchError(f"Expected exactly one {media_type} layer in the manifest, found {len(layers)}.")
    return layers[0]


def check_pinned_layer(layer: dict[str, Any], pinned_sha256: str, pinned_size: int) -> None:
    """Stop when the registry's GGUF layer is not the pinned file (different digest or size)."""
    digest = strip_digest_prefix(str(layer.get("digest", "")))
    if digest != pinned_sha256:
        raise FetchError(
            f"The registry now serves GGUF layer {digest}, but the config pins {pinned_sha256}. "
            "The tag points at different weights; review the change (migration gate) before updating the pin."
        )
    if int(layer.get("size", -1)) != pinned_size:
        raise FetchError(f"GGUF layer size {layer.get('size')} differs from the pinned {pinned_size} bytes.")


def check_pinned_license_layer(layer: dict[str, Any], pinned_sha256: str) -> str:
    """Return the license layer's sha256, or stop when it is not the pinned license text."""
    digest = strip_digest_prefix(str(layer.get("digest", "")))
    if digest != pinned_sha256:
        raise FetchError(
            f"The registry now serves license layer {digest}, but the config pins {pinned_sha256}. The artifact's "
            "license text may have changed: read the new text, then update license_layer_sha256 and the recorded "
            "license facts (license_docker_artifact, license) together in config/generation_eval.yaml."
        )
    return digest


def verified_existing(path: Path, sha256_hex: str) -> bool:
    """True when the file exists and its SHA-256 matches."""
    return path.is_file() and sha256_of_file(path) == sha256_hex


def download_blob(
    client: httpx.Client, repository: str, token: str, sha256_hex: str, size: int, blob_dir: Path
) -> Path:
    """Download one blob to <blob_dir>/<sha256>, verifying SHA-256 and size before moving it into place.

    The file is written as <sha256>.partial first, so an interrupted download never looks complete.
    """
    blob_dir.mkdir(parents=True, exist_ok=True)
    target = blob_dir / sha256_hex
    if verified_existing(target, sha256_hex):
        print(f"  {sha256_hex[:12]}: already present and verified ({human_size(size)})")
        return target
    partial = blob_dir / f"{sha256_hex}.partial"
    digest = hashlib.sha256()
    written = 0
    next_report = PROGRESS_EVERY_BYTES
    url = f"{REGISTRY_URL}/v2/{repository}/blobs/sha256:{sha256_hex}"
    # httpx drops the Authorization header when the registry redirects to its storage host.
    with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as reply:
        if reply.status_code != 200:
            reply.read()
        check_reply(reply, f"blob {sha256_hex[:12]}")
        with partial.open("wb") as handle:
            for chunk in reply.iter_bytes(CHUNK_BYTES):
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                if written >= next_report:
                    print(f"  {sha256_hex[:12]}: {human_size(written)} of {human_size(size)}")
                    next_report += PROGRESS_EVERY_BYTES
    if digest.hexdigest() != sha256_hex or written != size:
        partial.unlink(missing_ok=True)
        raise FetchError(
            f"Download of {sha256_hex[:12]} failed verification (got sha256 {digest.hexdigest()[:12]}, "
            f"{written} bytes; expected {size} bytes). The partial file was deleted."
        )
    partial.replace(target)
    print(f"  {sha256_hex[:12]}: downloaded and verified ({human_size(size)})")
    return target


def license_title(path: Path) -> str:
    """The 'title:' line of a license file (Docker AI artifacts use front matter), or its first non-empty line."""
    first_line = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("title:"):
            return stripped[len("title:") :].strip()
        if not first_line and stripped and stripped != "---":
            first_line = stripped
    return first_line


def write_modelfile(blob_path: Path, name: str, blob_dir: Path) -> Path:
    """A Modelfile that imports the GGUF file as it is (no parameters or template overrides)."""
    modelfile = blob_dir / f"Modelfile.{name}"
    modelfile.write_text(f"FROM {blob_path.resolve()}\n", encoding="utf-8")
    return modelfile


def ollama_create(ollama_bin: str, name: str, modelfile: Path, base_url: str) -> None:
    """Run `ollama create <name> -f <Modelfile>` against the server at base_url."""
    environment = dict(os.environ)
    environment["OLLAMA_HOST"] = base_url
    command = [ollama_bin, "create", name, "-f", str(modelfile)]
    try:
        subprocess.run(command, check=True, env=environment, timeout=OLLAMA_CREATE_TIMEOUT_S)
    except FileNotFoundError as error:
        raise FetchError(f"ollama executable not found: {ollama_bin!r}. Pass --ollama-bin.") from error
    except subprocess.SubprocessError as error:
        raise FetchError(f"`ollama create {name}` failed: {error}") from error


def installed_digest(base_url: str, name: str) -> str | None:
    """The digest Ollama reports for an installed model (GET /api/tags), or None if it is not installed."""
    with httpx.Client(timeout=30.0, trust_env=False) as client:
        reply = client.get(f"{base_url}/api/tags")
    if reply.status_code != 200:
        raise FetchError(f"Ollama GET /api/tags returned HTTP {reply.status_code}.")
    entry = find_model_entry(reply.json(), name)
    if entry is None:
        return None
    return strip_digest_prefix(str(entry.get("digest", "")))


def fetch_model(client: httpx.Client, name: str, pinned: dict[str, Any], args: argparse.Namespace) -> None:
    """Verify, download and import one model, printing what was checked."""
    repository, tag = parse_source(str(pinned["source"]))
    print(f"{name}: {repository}:{tag}")
    token = registry_token(client, repository)
    manifest = fetch_manifest(client, repository, tag, token)
    gguf = find_layer(manifest, GGUF_MEDIA_TYPE)
    check_pinned_layer(gguf, str(pinned["gguf_sha256"]), int(pinned["gguf_size_bytes"]))
    print(f"  manifest GGUF layer matches the pinned sha256 {pinned['gguf_sha256']}")
    license_layer = find_layer(manifest, LICENSE_MEDIA_TYPE)
    license_sha = check_pinned_license_layer(license_layer, str(pinned["license_layer_sha256"]))
    print(f"  manifest license layer matches the pinned sha256 {license_sha}")
    if args.check_only:
        print("  nothing downloaded (--check-only)")
        return

    license_path = download_blob(client, repository, token, license_sha, int(license_layer["size"]), args.blob_dir)
    print(f"  Docker artifact license layer: {license_title(license_path)!r}")
    if pinned.get("license_upstream_card"):
        print(f"  upstream model card license (recorded in the config): {pinned['license_upstream_card']}")
    blob_path = download_blob(
        client, repository, token, str(pinned["gguf_sha256"]), int(pinned["gguf_size_bytes"]), args.blob_dir
    )
    if args.skip_create:
        print("  not imported into Ollama (--skip-create)")
        return

    base_url = args.ollama_url or load_llm_settings().ollama_base_url
    modelfile = write_modelfile(blob_path, name, args.blob_dir)
    ollama_create(args.ollama_bin, name, modelfile, base_url)
    digest = installed_digest(base_url, name)
    expected = pinned.get("ollama_digest")
    print(f"  Ollama digest {digest} (config: {expected})")
    if expected and digest != expected:
        print(
            "  NOTE: the Ollama digest differs from the config. Ollama computes it from its own manifest, so a "
            "different Ollama version can change it although the GGUF file is identical (verified above).",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    """Run the import. Returns 0 on success, 1 on a failure, 2 when refused (CI, bad --ollama-url, unknown model)."""
    args = parse_args(argv)
    if os.environ.get("CI"):
        print("fetch_local_model.py downloads gigabytes and needs a local Ollama server; it does not run in CI.")
        return 2
    if args.ollama_url:
        try:
            args.ollama_url = validate_base_url(args.ollama_url)
        except SettingsError as error:
            print(f"ERROR: --ollama-url: {error}", file=sys.stderr)
            return 2
    pinned_models = load_pinned_models(args.config)
    names = sorted(pinned_models) if args.model == "all" else [args.model]
    unknown = [name for name in names if name not in pinned_models]
    if unknown:
        print(f"Unknown model(s) {unknown}; pinned in {args.config.name}: {sorted(pinned_models)}", file=sys.stderr)
        return 2
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True) as client:
            for name in names:
                fetch_model(client, name, pinned_models[name], args)
    except (FetchError, httpx.HTTPError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

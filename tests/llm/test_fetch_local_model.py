"""Tests for scripts/fetch_local_model.py against a pretend Docker Hub (httpx.MockTransport). No network, no ollama."""

import argparse
import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

import fetch_local_model as fetch
from uwcopilot.common.paths import CONFIG_DIR

GGUF_BYTES = b"GGUF pretend weights " * 100
LICENSE_BYTES = b"---\ntitle: Apache License 2.0\nspdx-id: Apache-2.0\n---\nLicense text\n"
GGUF_SHA = hashlib.sha256(GGUF_BYTES).hexdigest()
LICENSE_SHA = hashlib.sha256(LICENSE_BYTES).hexdigest()
REPOSITORY = "ai/qwen2.5"
TAG = "3B-Q4_K_M"


def manifest_json(
    gguf_sha: str = GGUF_SHA,
    media_type: str = "application/vnd.oci.image.manifest.v1+json",
    license_sha: str = LICENSE_SHA,
    license_size: int = len(LICENSE_BYTES),
) -> dict:
    """An OCI manifest shaped like a Docker Hub model artifact (one GGUF layer and one license layer)."""
    return {
        "schemaVersion": 2,
        "mediaType": media_type,
        "config": {"mediaType": "application/vnd.docker.ai.model.config.v0.1+json", "size": 1, "digest": "x"},
        "layers": [
            {"mediaType": fetch.GGUF_MEDIA_TYPE, "size": len(GGUF_BYTES), "digest": f"sha256:{gguf_sha}"},
            {"mediaType": fetch.LICENSE_MEDIA_TYPE, "size": license_size, "digest": f"sha256:{license_sha}"},
        ],
    }


class FakeRegistry:
    """Answers the token, manifest and blob endpoints; blobs can be corrupted or rate limited."""

    def __init__(self, manifest: dict | None = None, blobs: dict[str, bytes] | None = None, status: int = 200) -> None:
        """status applies to every endpoint (for example 429)."""
        self.manifest = manifest or manifest_json()
        self.blobs = blobs if blobs is not None else {GGUF_SHA: GGUF_BYTES, LICENSE_SHA: LICENSE_BYTES}
        self.status = status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Route by host and path."""
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, headers={"ratelimit-remaining": "0;w=3600"}, text="too many")
        if request.url.host == "auth.docker.io":
            assert request.url.params["scope"] == f"repository:{REPOSITORY}:pull"
            return httpx.Response(200, json={"token": "anon-token"})
        assert request.headers["Authorization"] == "Bearer anon-token"
        path = request.url.path
        if path == f"/v2/{REPOSITORY}/manifests/{TAG}":
            return httpx.Response(200, json=self.manifest)
        if path.startswith(f"/v2/{REPOSITORY}/blobs/sha256:"):
            digest = path.rsplit(":", 1)[1]
            return httpx.Response(200, content=self.blobs[digest])
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        """An httpx.Client that only talks to this pretend registry."""
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def blob_requests(self) -> list[str]:
        """Paths of the blob downloads so far."""
        return [request.url.path for request in self.requests if "/blobs/" in request.url.path]


def pinned(gguf_sha: str = GGUF_SHA) -> dict[str, Any]:
    """A pinned model record like the config's models section."""
    return {
        "source": f"docker.io/{REPOSITORY}:{TAG}",
        "gguf_sha256": gguf_sha,
        "gguf_size_bytes": len(GGUF_BYTES),
        "license_layer_sha256": LICENSE_SHA,
        "license_upstream_card": "Qwen Research License (per plan)",
    }


def make_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    """Arguments for fetch_model with a temporary blob folder and no Ollama import."""
    values: dict[str, Any] = {
        "blob_dir": tmp_path / "blobs",
        "check_only": False,
        "skip_create": True,
        "ollama_bin": "ollama",
        "ollama_url": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_parse_source_accepts_only_docker_hub() -> None:
    assert fetch.parse_source("docker.io/ai/qwen2.5:3B-Q4_K_M") == ("ai/qwen2.5", "3B-Q4_K_M")
    with pytest.raises(fetch.FetchError):
        fetch.parse_source("ghcr.io/ai/qwen2.5:3B")
    with pytest.raises(fetch.FetchError):
        fetch.parse_source("docker.io/ai/qwen2.5")


def test_real_config_pins_both_models() -> None:
    models = fetch.load_pinned_models(CONFIG_DIR / "generation_eval.yaml")
    assert models["qwen2.5-3b-instruct-q4km"]["gguf_size_bytes"] == 1929903008
    assert models["qwen2.5-1.5b-instruct-f16"]["gguf_size_bytes"] == 3093669376
    for record in models.values():
        assert record["license_layer_sha256"] == "609e2cb599f84aaa41d8ef29d8fdb04d164fab22e8d9292ca34a599d0f56a338"
    assert fetch.parse_source(models["qwen2.5-3b-instruct-q4km"]["source"]) == ("ai/qwen2.5", "3B-Q4_K_M")


def test_fetch_downloads_and_verifies_blobs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    registry = FakeRegistry()
    fetch.fetch_model(registry.client(), "qwen-test", pinned(), make_args(tmp_path))
    blob_dir = tmp_path / "blobs"
    assert (blob_dir / GGUF_SHA).read_bytes() == GGUF_BYTES
    assert (blob_dir / LICENSE_SHA).read_bytes() == LICENSE_BYTES
    assert not list(blob_dir.glob("*.partial"))
    output = capsys.readouterr().out
    assert "Apache License 2.0" in output
    assert "Qwen Research License" in output


def test_verified_existing_blobs_are_not_downloaded_again(tmp_path: Path) -> None:
    first = FakeRegistry()
    fetch.fetch_model(first.client(), "qwen-test", pinned(), make_args(tmp_path))
    second = FakeRegistry()
    fetch.fetch_model(second.client(), "qwen-test", pinned(), make_args(tmp_path))
    assert second.blob_requests() == []


def test_check_only_downloads_nothing(tmp_path: Path) -> None:
    registry = FakeRegistry()
    fetch.fetch_model(registry.client(), "qwen-test", pinned(), make_args(tmp_path, check_only=True))
    assert registry.blob_requests() == []
    assert not (tmp_path / "blobs").exists()


def test_a_different_registry_digest_stops_before_download(tmp_path: Path) -> None:
    registry = FakeRegistry(manifest=manifest_json(gguf_sha="c" * 64))
    with pytest.raises(fetch.FetchError, match="migration gate"):
        fetch.fetch_model(registry.client(), "qwen-test", pinned(), make_args(tmp_path))
    assert registry.blob_requests() == []


@pytest.mark.parametrize("check_only", [True, False])
def test_a_different_license_layer_stops_before_download(tmp_path: Path, check_only: bool) -> None:
    other_license = b"---\ntitle: Qwen RESEARCH LICENSE AGREEMENT\n---\nResearch use only\n"
    other_sha = hashlib.sha256(other_license).hexdigest()
    registry = FakeRegistry(
        manifest=manifest_json(license_sha=other_sha, license_size=len(other_license)),
        blobs={GGUF_SHA: GGUF_BYTES, other_sha: other_license},
    )
    with pytest.raises(fetch.FetchError, match="license layer"):
        fetch.fetch_model(registry.client(), "qwen-test", pinned(), make_args(tmp_path, check_only=check_only))
    assert registry.blob_requests() == []


def test_a_config_without_a_license_pin_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "generation_eval.yaml"
    record = {key: value for key, value in pinned().items() if key != "license_layer_sha256"}
    config.write_text(yaml.safe_dump({"models": {"qwen-test": record}}), encoding="utf-8")
    with pytest.raises(fetch.FetchError, match="license_layer_sha256"):
        fetch.load_pinned_models(config)


def test_corrupted_download_is_rejected_and_removed(tmp_path: Path) -> None:
    registry = FakeRegistry(blobs={GGUF_SHA: b"x" * len(GGUF_BYTES), LICENSE_SHA: LICENSE_BYTES})
    with pytest.raises(fetch.FetchError, match="failed verification"):
        fetch.fetch_model(registry.client(), "qwen-test", pinned(), make_args(tmp_path))
    assert not (tmp_path / "blobs" / GGUF_SHA).exists()
    assert not (tmp_path / "blobs" / f"{GGUF_SHA}.partial").exists()


def test_rate_limit_gives_a_clear_message(tmp_path: Path) -> None:
    registry = FakeRegistry(status=429)
    with pytest.raises(fetch.RegistryRateLimited, match="rate limit"):
        fetch.fetch_model(registry.client(), "qwen-test", pinned(), make_args(tmp_path))


def test_multi_platform_index_is_refused(tmp_path: Path) -> None:
    registry = FakeRegistry(manifest=manifest_json(media_type="application/vnd.oci.image.index.v1+json"))
    with pytest.raises(fetch.FetchError, match="index"):
        fetch.fetch_model(registry.client(), "qwen-test", pinned(), make_args(tmp_path))


def test_modelfile_imports_the_blob_as_is(tmp_path: Path) -> None:
    blob = tmp_path / GGUF_SHA
    blob.write_bytes(GGUF_BYTES)
    modelfile = fetch.write_modelfile(blob, "qwen-test", tmp_path)
    assert modelfile.read_text() == f"FROM {blob.resolve()}\n"


def test_license_title_reads_front_matter(tmp_path: Path) -> None:
    path = tmp_path / "license"
    path.write_bytes(LICENSE_BYTES)
    assert fetch.license_title(path) == "Apache License 2.0"


def test_script_refuses_to_run_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    assert fetch.main(["--check-only"]) == 2


def test_unknown_model_name_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    assert fetch.main(["--model", "not-a-pinned-model", "--check-only"]) == 2


def test_plain_http_ollama_url_to_another_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    assert fetch.main(["--check-only", "--ollama-url", "http://10.0.0.5:11434"]) == 2

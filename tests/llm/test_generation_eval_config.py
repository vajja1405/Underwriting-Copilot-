"""Consistency checks for config/generation_eval.yaml: pinned digests, options, prices and both license sources."""

from typing import Any

import pytest
import yaml

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.llm.budget import load_prices

CONFIG_FILE = CONFIG_DIR / "generation_eval.yaml"
SHARED_SETTINGS = {
    "provider": "ollama",
    "prompt_id": "rag_answer_v1",
    "k": 3,
    "num_predict": 200,
    "num_ctx": 4096,
    "temperature": 0,
    "seed": 42,
}


def load_config() -> dict[str, Any]:
    """The parsed YAML file."""
    return yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("config_id", "model", "digest"),
    [
        (
            "qwen3b_v1_k3",
            "qwen2.5-3b-instruct-q4km",
            "fae2485a8df6d917bc97274961a6fb59820bdd058fbf5849157fa2e8a0abcd6b",
        ),
        (
            "qwen15b_v1_k3",
            "qwen2.5-1.5b-instruct-f16",
            "75a307d2fa0d2d6d5158a9d22a4bad38836b9b998ebb106fd9ea1e8c1a9d33d3",
        ),
    ],
)
def test_named_configs_pin_model_digest_prompt_and_options(config_id: str, model: str, digest: str) -> None:
    config = load_config()["configs"][config_id]
    for key, value in SHARED_SETTINGS.items():
        assert config[key] == value, key
    assert config["model"] == model
    assert config["expected_digest"] == digest
    assert load_config()["models"][model]["ollama_digest"] == digest


def test_every_configured_model_has_a_price_and_a_source_record() -> None:
    document = load_config()
    prices = load_prices(CONFIG_DIR / "llm_pricing.yaml")
    for config in document["configs"].values():
        assert prices[config["model"]].provider == "ollama"
        record = document["models"][config["model"]]
        assert record["source"].startswith("docker.io/ai/qwen2.5:")
        assert len(record["gguf_sha256"]) == 64


def test_both_license_sources_are_recorded() -> None:
    models = load_config()["models"]
    for record in models.values():
        assert "Apache-2.0" in record["license_docker_artifact"]
        # The free-text record names the same layer that scripts/fetch_local_model.py checks.
        assert f"sha256:{record['license_layer_sha256']}" in record["license_docker_artifact"]
        assert record["license_upstream_card"]
        assert record["upstream_card"].startswith("https://huggingface.co/Qwen/")
        assert "Docker Hub artifact" in record["license"]
        assert "upstream Hugging Face card" in record["license"]
    # Upstream card facts as re-checked on 2026-10-12: the 3B card says qwen-research, the 1.5B card apache-2.0.
    assert "Qwen Research License" in models["qwen2.5-3b-instruct-q4km"]["license"]
    assert "qwen-research" in models["qwen2.5-3b-instruct-q4km"]["license_upstream_card"]
    assert "apache-2.0" in models["qwen2.5-1.5b-instruct-f16"]["license_upstream_card"]


def test_challenger_note_says_quantization_differs_too() -> None:
    note = load_config()["configs"]["qwen15b_v1_k3"]["note"]
    assert "quantization" in note
    assert "F16" in note

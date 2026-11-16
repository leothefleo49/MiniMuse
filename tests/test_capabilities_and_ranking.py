from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.connectors.api_registry import preset_list
from nanomuse.llm import MockLLM
from nanomuse.server import create_app
from nanomuse.server.capabilities import assess_local_ai
from nanomuse.server.connections import rank_models
from nanomuse.server.service import MuseService


@pytest.fixture()
def server(settings: Settings) -> Iterator[tuple[TestClient, MuseService]]:
    settings.server.token = "secret-token"
    service = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, service)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer secret-token"
    with client:
        yield client, service


def test_assess_local_ai_weak_mobile_vs_workstation() -> None:
    mobile_host = {
        "os": "Linux",
        "arch": "aarch64",
        "cpu_cores": 8,
        "total_ram_gb": 4.0,
        "free_ram_gb": 1.1,
        "is_android": True,
        "is_mobile": True,
        "apple_silicon": False,
        "gpu_name": "",
        "vram_gb": 0.0,
    }
    weak_eval = assess_local_ai(mobile_host, [], client_ram_gb=4.0, client_cores=8, client_mobile=True)
    assert weak_eval["verdict"]["tier"] == "weak_device"
    assert weak_eval["verdict"]["worth_it"] == "cloud_recommended"
    assert "OpenRouter" in weak_eval["verdict"]["tradeoff_summary"]

    desktop_host = {
        "os": "Windows",
        "arch": "AMD64",
        "cpu_cores": 16,
        "total_ram_gb": 32.0,
        "free_ram_gb": 22.0,
        "is_android": False,
        "is_mobile": False,
        "apple_silicon": False,
        "gpu_name": "NVIDIA RTX 4080",
        "vram_gb": 16.0,
    }
    workstation_eval = assess_local_ai(
        desktop_host,
        [{"id": "ollama", "name": "Ollama", "running": True, "models": ["qwen3:8b"]}],
    )
    assert workstation_eval["verdict"]["tier"] == "workstation"
    assert workstation_eval["verdict"]["worth_it"] == "worth_it"
    fits_map = {m["id"]: m["fit"] for m in workstation_eval["recommended_models"]}
    assert fits_map.get("qwen3:8b") == "fits"


def test_rank_models_puts_popular_first_and_tail_alphabetical() -> None:
    raw = [
        "zz-vendor/zeta-model",
        "01-ai/yi-large",
        "openai/gpt-4o-mini",
        "ab-lab/alpha-model",
        "anthropic/claude-sonnet-4",
        "google/gemini-2.5-flash",
    ]
    ranked, popular = rank_models(raw, preset_id="openrouter", curated=["anthropic/claude-sonnet-4"])
    # Popular models come first before alphabetically sorted rare models like 01-ai/yi-large
    assert ranked.index("anthropic/claude-sonnet-4") < ranked.index("01-ai/yi-large")
    assert ranked.index("openai/gpt-4o-mini") < ranked.index("01-ai/yi-large")
    assert ranked.index("google/gemini-2.5-flash") < ranked.index("01-ai/yi-large")
    tail = [m for m in ranked if m not in popular]
    assert tail == sorted(tail, key=str.lower)


def test_preset_list_sorts_popular_first_then_alphabetical() -> None:
    presets = preset_list()
    assert len(presets) >= 35
    popular_flags = [bool(p.get("popular")) for p in presets]
    # All True popular entries appear strictly before all False non-popular entries
    first_non_popular = popular_flags.index(False)
    assert all(popular_flags[:first_non_popular])
    assert not any(popular_flags[first_non_popular:])
    rare_names = [str(p["name"]).lower() for p in presets[first_non_popular:]]
    assert rare_names == sorted(rare_names)


def test_system_capabilities_endpoint(server: tuple[TestClient, MuseService]) -> None:
    client, _svc = server
    with patch(
        "nanomuse.server.capabilities.probe_local_runtimes",
        return_value=[{"id": "ollama", "name": "Ollama", "base_url": "http://127.0.0.1:11434/v1", "running": False, "models": [], "install_url": "https://ollama.com"}],
    ):
        resp = client.get("/api/system/capabilities?client_ram_gb=4&client_cores=8&client_mobile=true")
    assert resp.status_code == 200
    body = resp.json()
    assert "host" in body and "verdict" in body and "recommended_models" in body
    assert body["client"]["is_mobile"] is True

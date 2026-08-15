"""Tests de integración real con Ollama."""

from __future__ import annotations

import os

import pytest


def _ollama_available() -> bool:
    try:
        from core.providers import get as get_provider

        provider = get_provider("ollama")
        hc = provider.health_check()
        return hc.get("healthy") is True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    import core.providers.registry as registry_module

    registry_module._registry = None
    yield
    registry_module._registry = None


@pytest.mark.skipif(not _ollama_available(), reason="Ollama no disponible")
def test_real_ollama_call() -> None:
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
    os.environ["OLLAMA_MODEL"] = "qwen-agent:latest"
    try:
        from core.providers import get as get_provider

        provider = get_provider("ollama")
        result = provider.generate(
            "Responde exactamente: SPACE LAIR OLLAMA OK",
            model="qwen-agent:latest",
            max_tokens=50,
            temperature=0.0,
        )
        assert "SPACE LAIR OLLAMA OK" in result.text
    finally:
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("OLLAMA_BASE_URL", None)
        os.environ.pop("OLLAMA_MODEL", None)


@pytest.mark.skipif(not _ollama_available(), reason="Ollama no disponible")
def test_real_hermes_ollama_call() -> None:
    os.environ["LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"
    os.environ["OLLAMA_MODEL"] = "qwen-agent:latest"
    try:
        from core.central_ai import _call_router_llm

        result = _call_router_llm(
            "Responde exactamente: HERMES OLLAMA OK",
            system="Responde exactamente lo que se te pide, sin acortar.",
        )
        assert result is not None
        assert result["module_id"] == "HERMES OLLAMA OK"
    finally:
        os.environ.pop("LLM_PROVIDER", None)
        os.environ.pop("OLLAMA_BASE_URL", None)
        os.environ.pop("OLLAMA_MODEL", None)

"""Tests del registro de proveedores LLM."""

from __future__ import annotations

import os

import pytest

from core.providers import get as get_provider
from core.providers.ollama import OllamaProvider


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    import core.providers.registry as registry_module

    registry_module._registry = None
    yield
    registry_module._registry = None


def test_registry_auto_registers_ollama() -> None:
    from core.providers import registry

    assert "ollama" in registry.names()


def test_get_ollama_returns_ollama_provider() -> None:
    provider = get_provider("ollama")
    assert isinstance(provider, OllamaProvider)
    assert provider.name == "ollama"


def test_ollama_env_config() -> None:
    base_url = "http://localhost:11434"
    model = "qwen-agent:latest"
    os.environ["OLLAMA_BASE_URL"] = base_url
    os.environ["OLLAMA_MODEL"] = model
    try:
        provider = get_provider("ollama")
        assert isinstance(provider, OllamaProvider)
        assert provider.base_url == base_url.rstrip("/")
        assert provider.model == model
    finally:
        os.environ.pop("OLLAMA_BASE_URL", None)
        os.environ.pop("OLLAMA_MODEL", None)


def test_default_provider_is_ollama() -> None:
    from core.providers import get_registry

    assert get_registry().default() == "ollama"

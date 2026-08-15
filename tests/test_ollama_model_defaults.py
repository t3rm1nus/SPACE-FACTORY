"""Tests específicos para evitar regresiones en la resolución de modelos Ollama."""

from __future__ import annotations

import importlib
import os
from typing import Any

import pytest


MODULES_UNDER_TEST = [
    "modules.book_planner.main",
    "modules.chapter_writer.main",
    "modules.editor.main",
    "modules.fact_checker.main",
    "modules.image_planner.main",
    "modules.translator.main",
    "modules.text_summarizer.main",
]


def _reload_module(module_name: str, env: dict[str, str | None]) -> Any:
    """Importa un módulo con un entorno específico y lo recarga si ya estaba cargado."""
    original: dict[str, str | None] = {}
    for key, value in env.items():
        original[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        module = importlib.import_module(module_name)
        importlib.reload(module)
        return module
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.mark.parametrize("module_name", MODULES_UNDER_TEST)
def test_ollama_model_env_overrides_default(module_name: str) -> None:
    """OLLAMA_MODEL=qwen-agent:latest debe prevalecer sobre el default."""
    module = _reload_module(
        module_name,
        {
            "OLLAMA_MODEL": "qwen-agent:latest",
            "ROUTER_MODEL": None,
            "LLM_PROVIDER": None,
        },
    )
    assert getattr(module, "DEFAULT_MODEL") == "qwen-agent:latest"


@pytest.mark.parametrize("module_name", MODULES_UNDER_TEST)
def test_router_model_env_overrides_default(module_name: str) -> None:
    """ROUTER_MODEL=qwen-agent:latest debe prevalecer sobre el default."""
    module = _reload_module(
        module_name,
        {
            "OLLAMA_MODEL": None,
            "ROUTER_MODEL": "qwen-agent:latest",
            "LLM_PROVIDER": None,
        },
    )
    assert getattr(module, "DEFAULT_ROUTER_MODEL") == "qwen-agent:latest"


@pytest.mark.parametrize("module_name", MODULES_UNDER_TEST)
def test_runtime_default_is_qwen_agent(module_name: str) -> None:
    """Sin variables de entorno, el default runtime debe ser qwen-agent:latest."""
    module = _reload_module(
        module_name,
        {
            "OLLAMA_MODEL": None,
            "ROUTER_MODEL": None,
            "LLM_PROVIDER": None,
        },
    )
    assert getattr(module, "DEFAULT_MODEL") == "qwen-agent:latest"
    assert getattr(module, "DEFAULT_ROUTER_MODEL") == "qwen-agent:latest"


def test_ollama_provider_default_model() -> None:
    """El proveedor Ollama debe tener default qwen-agent:latest."""
    import core.providers.ollama as ollama

    assert ollama.DEFAULT_MODEL == "qwen-agent:latest"


def test_ollama_provider_env_config_precedence() -> None:
    """OLLAMA_MODEL debe prevalecer sobre DEFAULT_MODEL."""
    import core.providers.ollama as ollama

    for mod in [
        ollama.OllamaProvider(),
        ollama.OllamaProvider(model=None),
    ]:
        assert mod.model == "qwen-agent:latest"

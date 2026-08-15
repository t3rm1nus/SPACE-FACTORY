"""Capa de abstracción de proveedores LLM de Space Lair.

Permite que los módulos usen una interfaz única (LLMProvider) sin acoplarse a
ningún SDK concreto. Los proveedores disponibles son:

- ollama:             local, sin API key (por defecto).
- anthropic:          opcional, requiere SDK + ANTHROPIC_API_KEY.
- openai_compatible:  genérico para servidores estilo OpenAI (locales o no).

Uso típico:

    from core.providers import get

    provider = get("ollama")          # o get() para usar LLM_PROVIDER
    result = provider.generate("Hola")
    print(result.text, result.cost)
"""

from __future__ import annotations

from core.providers.base import (
    LLMConnectionError,
    LLMError,
    LLMInvalidResponseError,
    LLMModelNotFoundError,
    LLMProvider,
    LLMProviderNotFoundError,
    LLMResult,
    LLMTimeoutError,
)
from core.providers.registry import (
    ProviderRegistry,
    get,
    get_registry,
    names,
    register,
    unregister,
)

__all__ = [
    "LLMProvider",
    "LLMResult",
    "LLMError",
    "LLMConnectionError",
    "LLMTimeoutError",
    "LLMInvalidResponseError",
    "LLMModelNotFoundError",
    "LLMProviderNotFoundError",
    "ProviderRegistry",
    "get",
    "get_registry",
    "register",
    "unregister",
    "names",
]

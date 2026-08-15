"""Proveedor Anthropic (opcional).

Requiere:
    pip install anthropic
    export ANTHROPIC_API_KEY=...

Uso:
    from core.providers import get
    provider = get("anthropic")
    result = provider.generate("Hola")
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from core.providers.base import LLMProvider, LLMResult, _env, map_urlerror
from core.providers.registry import register

logger = logging.getLogger(__name__)


def extract_anthropic_usage(response: Any) -> tuple[int, int]:
    """Extrae input_tokens y output_tokens del response de Anthropic.

    El response puede ser:
    - Un objeto con .usage (SDK oficial)
    - Un dict con 'usage' (JSON)
    - Un dict plano con 'usage'
    """
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage", {})

    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
    elif hasattr(usage, "input_tokens"):
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
    else:
        input_tokens = 0
        output_tokens = 0

    return int(input_tokens), int(output_tokens)


class AnthropicProvider(LLMProvider):
    """Proveedor LLM que habla con Anthropic."""

    name = "anthropic"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        super().__init__(model=model, timeout=timeout, max_retries=max_retries)
        self.api_key = api_key or _env("ANTHROPIC_API_KEY")
        if self.model is None:
            self.model = _env("ANTHROPIC_MODEL", "claude-sonnet-5")

    @classmethod
    def env_config(cls) -> dict:
        return {
            "api_key": _env("ANTHROPIC_API_KEY"),
            "model": _env("ANTHROPIC_MODEL"),
        }

    def _generate_once(
        self,
        prompt: str,
        *,
        system: Optional[str],
        model: str,
        max_tokens: Optional[float],
        temperature: Optional[float],
        **kwargs: Any,
    ) -> LLMResult:
        if not self.api_key:
            raise LLMInvalidResponseError(
                "ANTHROPIC_API_KEY no configurada para el proveedor anthropic"
            )
        try:
            import anthropic
        except ImportError as e:
            raise LLMInvalidResponseError(
                "SDK de Anthropic no instalado. Ejecuta: pip install anthropic"
            ) from e

        client = anthropic.Anthropic(api_key=self.api_key)
        messages = [{"role": "user", "content": prompt}]
        try:
            response = client.messages.create(
                model=model,
                max_tokens=int(max_tokens) if max_tokens else 1024,
                system=system or "",
                messages=messages,
            )
        except Exception as e:
            raise map_urlerror(e, provider=self.name) from e

        input_tokens, output_tokens = extract_anthropic_usage(response)
        text = ""
        for block in getattr(response, "content", []):
            if hasattr(block, "text"):
                text += block.text
        if not text:
            text = str(getattr(response, "content", ""))

        return LLMResult(
            text=text,
            provider=self.name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=self._cost(self.name, model, input_tokens, output_tokens),
            raw_response=response,
        )

    def health_check(self) -> dict:
        healthy = bool(self.api_key)
        return {
            "healthy": healthy,
            "provider": self.name,
            "model": self.model,
            "checks": {"anthropic_api_key": healthy},
            "status": "🟢 healthy (Anthropic)" if healthy else "🔴 unhealthy (falta ANTHROPIC_API_KEY)",
        }

    def available_models(self) -> list:
        return [self.model] if self.model else []


try:
    register(AnthropicProvider)
except Exception:
    pass

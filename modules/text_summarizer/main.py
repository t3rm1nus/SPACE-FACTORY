"""Módulo text_summarizer: resume texto usando un LLM.

Capability: ``summarize_text``.

Usa la interfaz única de proveedores de Space Lair (``core.providers.get``)
en lugar de acoplarse a un SDK concreto (Anthropic u Ollama HTTP directo). El
proveedor se resuelve vía ``LLM_PROVIDER`` y, por defecto, cae en Ollama local
(``ollama``, sin API key). Si el proveedor no está disponible, se devuelve un
resumen determinista extraído del propio texto.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from core.logger import get_logger, log
from core.metrics import calculate_cost
from core.providers import get as get_provider
from core.schemas import SummarizePayload, validate_payload

logger = get_logger(__name__)

# Modelo router por defecto (configurable vía entorno).
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", DEFAULT_MODEL)


def health_check() -> dict:
    """Verifica que el proveedor LLM configurado esté disponible.

    Usa la interfaz de proveedores (``get_provider``) sin acoplarse a un SDK
    concreto. Si el proveedor falla al instanciar, el módulo se marca como
    no sano pero no lanza.
    """
    checks: dict[str, Any] = {}
    provider = None
    try:
        provider = get_provider()
        checks["provider"] = provider.name
        checks["model"] = provider.model
        hc = provider.health_check()
        checks["provider_health"] = hc.get("healthy")
    except Exception as e:
        checks["error"] = str(e)

    healthy = provider is not None and checks.get("provider_health") is not False
    status = "🟢 healthy" if healthy else "🔴 unhealthy"
    if provider:
        status += f" ({provider.name})"
    return {
        "healthy": healthy,
        "dependencies": checks,
        "status": status,
    }


def _build_prompt(validated: dict, max_words: Optional[int]) -> str:
    """Construye el prompt de resumen a partir del payload validado."""
    text = validated.get("text", "")
    word_limit = f" con un máximo de {max_words} palabras" if max_words else ""
    return (
        "Eres un redactor editorial experto. Resume el siguiente texto en 2-3 "
        f"frases concisas y precisas{word_limit}, preservando los hechos clave.\n\n"
        f"Texto:\n{text}\n\nResumen:"
    )


def _fallback_summary(validated: dict, max_words: Optional[int]) -> str:
    """Resumen determinista sin LLM: primera oración del texto.

    Se usa como último recurso cuando ningún proveedor LLM está disponible.
    """
    text = (validated.get("text") or "").strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    first = sentences[0].strip() if sentences else text
    summary = first.rstrip(".")
    if max_words:
        summary = " ".join(summary.split()[:max_words])
    return summary or text[:200]


def execute(payload: dict, capability: str = "summarize_text") -> dict:
    """Resume el texto del payload en 2-3 frases.

    Usa el proveedor LLM configurado (Ollama local por defecto) a través de la
    interfaz ``core.providers.get``. Si falla, devuelve un resumen determinista
    basado en el texto. Valida el payload con ``SummarizePayload`` (incluye
    ``max_words`` opcional) y reporta ``tokens_input``, ``tokens_output`` y
    ``cost`` en el resultado.
    """
    validated = validate_payload(capability, payload)
    provider = None
    raw = None
    input_tokens = 0
    output_tokens = 0
    provider_name = "none"
    model_name = ""

    summary = ""
    try:
        provider = get_provider()
        provider_name = provider.name
        prompt = _build_prompt(validated, validated.get("max_words"))
        result = provider.generate(
            prompt,
            system="Resume en 2-3 frases concisas. Devuelve solo el resumen.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=300,
            temperature=0.2,
        )
        raw = result.raw_response
        model_name = result.model or DEFAULT_MODEL
        input_tokens = int(result.input_tokens or 0)
        output_tokens = int(result.output_tokens or 0)
        summary = (result.text or "").strip()
    except Exception as e:
        if provider is not None:
            provider_name = provider.name
        log(
            logger,
            logging.WARNING,
            f"Fallo al resumir con LLM ({provider_name}): {e}. "
            "Usando fallback determinista.",
        )

    if not summary:
        summary = _fallback_summary(validated, validated.get("max_words"))
        provider_name = "fallback"

    cost = 0.0
    try:
        cost = float(
            calculate_cost(
                provider_name or DEFAULT_MODEL,
                model_name or DEFAULT_MODEL,
                input_tokens,
                output_tokens,
            )
            or 0.0
        )
    except Exception:
        cost = 0.0

    log(
        logger,
        logging.INFO,
        f"Resumen completado ({provider_name}): "
        f"{input_tokens}i/{output_tokens}o tokens, coste ${cost:.6f}",
    )

    return {
        "summary": summary,
        "provider": provider_name,
        "model": model_name,
        "original_length": len(validated.get("text", "") or ""),
        "max_words": validated.get("max_words"),
        "tokens_input": input_tokens,
        "tokens_output": output_tokens,
        "cost": cost,
    }

"""IA Central de Space Lair.

Selecciona el módulo más adecuado para ejecutar una capability
cuando hay más de un candidato.
"""

import json
import logging
import os
from typing import Any, Optional

from core.logger import get_logger, log
from core.providers import get as get_provider

logger = get_logger(__name__)

# Modelo por defecto para el router (se resuelve dinámicamente en cada llamada).


def _build_module_descriptions(modules: list[dict[str, Any]]) -> str:
    """Construye el texto descriptivo de los módulos candidatos."""
    lines = []
    for module in modules:
        manifest = module["manifest"]
        lines.append(
            f"- {manifest['id']}: {manifest['name']} — {manifest['description']}"
        )
    return "\n".join(lines)


def _call_router_llm(
    prompt: str,
    *,
    system: str = "Responde solo con el id del módulo más adecuado.",
) -> Optional[dict]:
    """Llama al proveedor LLM configurado para elegir el módulo.

    Retorna dict con {module_id, cost, input_tokens, output_tokens} o None
    si no hay proveedor disponible o falla.
    """
    provider = None
    try:
        provider = get_provider()
        router_model = os.environ.get("ROUTER_MODEL") or os.environ.get("OLLAMA_MODEL") or "qwen-agent:latest"
        result = provider.generate(
            prompt,
            system=system,
            model=router_model,
            max_tokens=50,
            temperature=0.0,
        )
        text = (result.text or "").strip().strip('"').strip("'")
        first_line = text.split("\n")[0].strip()
        log(
            logger,
            logging.INFO,
            f"Router ({provider.name}): {result.input_tokens}i/{result.output_tokens}o tokens, coste ${result.cost:.6f}",
        )
        if not first_line:
            return None
        return {
            "module_id": first_line,
            "cost": result.cost,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }
    except Exception as e:
        provider_name = provider.name if provider is not None else "unknown"
        log(
            logger,
            logging.ERROR,
            f"Error llamando al router LLM ({provider_name}): {e}",
        )
        return None


def _fallback_by_priority(modules: list[dict[str, Any]]) -> str:
    """Selecciona el módulo con mayor prioridad (menor número = mayor prioridad)."""
    best = min(
        modules,
        key=lambda m: m["manifest"].get("config", {}).get("priority", 999),
    )
    return best["manifest"]["id"]


def choose_module(
    capability: str,
    modules: list[dict[str, Any]],
    payload: dict,
) -> str:
    """Elige el módulo que ejecutará una capability.

    - Si hay 1 módulo → lo retorna directamente.
    - Si hay varios → consulta al router LLM configurado (Hermes).
    - Si el router falla o no hay proveedor → usa priority del module.json como fallback.
    """
    if not modules:
        raise ValueError(f"No hay módulos que soporten la capability '{capability}'")

    if len(modules) == 1:
        module_id = modules[0]["manifest"]["id"]
        log(
            logger,
            logging.INFO,
            f"Un solo módulo disponible: {module_id}",
            module_id=module_id,
            capability=capability,
        )
        return module_id

    descriptions = _build_module_descriptions(modules)
    payload_preview = json.dumps(payload, ensure_ascii=False)[:500]

    prompt = (
        f"Dados estos módulos:\n{descriptions}\n\n"
        f"¿Cuál es el mejor para ejecutar la capability '{capability}' "
        f"con este payload: {payload_preview}?\n"
        f"Responde SOLO con el id del módulo."
    )

    result = _call_router_llm(prompt)

    if result and any(m["manifest"]["id"] == result["module_id"] for m in modules):
        module_id = result["module_id"]
        log(
            logger,
            logging.INFO,
            f"Router eligió: {module_id} (coste ${result['cost']:.6f})",
            module_id=module_id,
            capability=capability,
        )
        return module_id

    log(
        logger,
        logging.WARNING,
        "Respuesta del router inválida. Usando fallback por prioridad.",
        capability=capability,
    )
    module_id = _fallback_by_priority(modules)
    log(
        logger,
        logging.INFO,
        f"Fallback por prioridad: {module_id}",
        module_id=module_id,
        capability=capability,
    )
    return module_id

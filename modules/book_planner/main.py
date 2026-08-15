"""Módulo book_planner: transforma una idea de libro en un plan editorial.

Capability: create_book_plan
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from core.schemas import BookPlanChapter, BookPlanOutput
from core.logger import get_logger, log
from core.metrics import calculate_cost, extract_anthropic_usage
from core.providers import get as get_provider
from core.schemas import BookPlanPayload, validate_payload

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen-agent:latest")
DEFAULT_IMAGE_REQUIREMENTS = 3


def _coerce_image_requirements(value: Any) -> int:
    """Normaliza el campo image_requirements a un int válido antes de la validación.

    El esquema exige ``image_requirements: int`` (default 3, rango 0-20). El LLM
    puede devolver tipos imperfectos; esta función los convierte de forma segura
    y determinista:

    - int válido      -> se mantiene (recortado a 0-20)
    - lista           -> len(lista)
    - lista vacía     -> 0
    - string numérico -> int(string)
    - string inválido -> default
    - None            -> default
    - bool            -> False->0, True->1
    - otro tipo       -> default

    Returns:
        int en rango [0, 20].
    """
    if value is None:
        return DEFAULT_IMAGE_REQUIREMENTS
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return max(0, min(value, 20))
    if isinstance(value, float):
        return max(0, min(int(value), 20))
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return DEFAULT_IMAGE_REQUIREMENTS
        try:
            return max(0, min(int(s), 20))
        except (TypeError, ValueError):
            return DEFAULT_IMAGE_REQUIREMENTS
    if isinstance(value, (list, tuple)):
        return max(0, min(len(value), 20))
    return DEFAULT_IMAGE_REQUIREMENTS


def _resolve_explicit_image_count(payload: dict) -> Any:
    """Devuelve la configuración explícita de imágenes del workflow, si existe.

    Reconoce, por orden de prioridad:
    - ``image_count`` (int)
    - ``num_images`` (int)
    - ``images`` (bool / int / string representativo)

    Si el usuario declara explícitamente no querer imágenes (0 / False / "0" /
    "false"), devuelve 0 para que el planner las fuerce a 0. Si declara un
    número positivo, lo utiliza como requisito por capítulo. Si no hay
    configuración explícita, devuelve None (se respeta la sugerencia del LLM
    normalizada).
    """
    for key in ("image_count", "num_images"):
        if key in payload and payload[key] is not None:
            v = payload[key]
            if isinstance(v, bool):
                return 0 if not v else None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

    if "images" in payload and payload["images"] is not None:
        v = payload["images"]
        if isinstance(v, bool):
            return 0 if not v else None
        if isinstance(v, int):
            return max(0, v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("0", "false", "no", "none", "off"):
                return 0
            if s in ("1", "true", "yes", "on"):
                return None
            try:
                return max(0, int(s))
            except ValueError:
                return None
    return None


def _normalize_plan(plan_data: dict, payload: dict) -> dict:
    """Normaliza el plan crudo del LLM ANTES de construir BookPlanOutput.

    Aplica la conversión de ``image_requirements`` por capítulo y respeta la
    configuración explícita de imágenes del workflow (si la hay), que tiene
    prioridad sobre la sugerencia del LLM.

    Además, corrige ``estimated_words`` inválidos: si el LLM devuelve un valor
    numérico menor que 500, lo eleva a 500 y deja traza en el log.
    """
    plan_data = dict(plan_data or {})
    explicit = _resolve_explicit_image_count(payload)
    chapters = plan_data.get("chapters")
    if not isinstance(chapters, list):
        return plan_data

    normalized: list[Any] = []
    for ch in chapters:
        if not isinstance(ch, dict):
            normalized.append(ch)
            continue
        ch = dict(ch)
        if explicit is not None:
            ch["image_requirements"] = max(0, min(int(explicit), 20))
        else:
            ch["image_requirements"] = _coerce_image_requirements(
                ch.get("image_requirements")
            )

        ew = ch.get("estimated_words")
        if isinstance(ew, (int, float)) and ew < 500:
            log(
                logger,
                logging.WARNING,
                f"estimated_words inválido ({ew}) en capítulo; corregido a 500",
            )
            ch["estimated_words"] = 500

        normalized.append(ch)
    plan_data["chapters"] = normalized
    return plan_data


def health_check() -> dict:
    """Verifica que el proveedor LLM configurado esté disponible."""
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

    # Compatibilidad: si el proveedor no soporta health_check, damos por bueno
    # siempre que la instanciación haya funcionado.
    healthy = provider is not None and checks.get("provider_health") is not False
    status = "🟢 healthy" if healthy else "🔴 unhealthy"
    if provider:
        status += f" ({provider.name})"
    return {
        "healthy": healthy,
        "dependencies": checks,
        "status": status,
    }


def _build_prompt(validated: BookPlanPayload) -> str:
    """Construye un prompt estructurado para generar el plan editorial."""
    return (
        "Eres un editor profesional. Genera un plan editorial JSON estricto a partir de la idea dada.\n\n"
        f"Idea: {validated.idea}\n"
        f"Capítulos objetivo: {validated.target_chapters}\n"
        f"Idioma: {validated.language}\n"
        f"Público objetivo: {validated.target_audience or 'No especificado'}\n"
        f"Longitud deseada: {validated.desired_length or 'No especificada'}\n"
        f"Estilo: {validated.style or 'No especificado'}\n"
        f"Restricciones temáticas: {validated.subject_constraints or 'Ninguna'}\n\n"
        "REGLAS:\n"
        "- Objetivo recomendado: 30 capítulos; permitir entre 20 y 40.\n"
        "- Progresión lógica; cada capítulo aporta información nueva.\n"
        "- Evitar capítulos redundantes.\n"
        "- No inventar hechos; separar hechos de hipótesis cuando aplique.\n"
        "- IMPORTANTE: 'estimated_words' es la cantidad de palabras POR CAPÍTULO.\n"
        "- Regla estricta: estimated_words >= 500 (entero). Nunca devuelvas valores inferiores a 500.\n"
        "- Si 'desired_length' es un total del libro, reparte palabras entre capítulos manteniendo el mínimo 500 por capítulo.\n"
        "- Coherencia: la suma de estimated_words debe ser razonable frente a desired_length y target_chapters.\n"
        "- No confundas 'target_words' (total del libro) con 'estimated_words' (por capítulo).\n\n"
        "Salida JSON válida con exactamente estas claves:\n"
        '{"title":"...","subtitle":"...","description":"...","target_audience":"...",'
        '"chapters":[{"number":1,"title":"...","objective":"...","key_questions":[],'
        '"estimated_words":3000,"research_requirements":[],"image_requirements":3}]}\n\n'
        "Devuelve SOLO el JSON, sin texto adicional."
    )


def _fallback_plan(validated: BookPlanPayload) -> dict[str, Any]:
    """Plan básico determinista cuando no hay LLM disponible."""
    title = validated.idea.strip()
    subtitle = "Plan editorial"
    chapters = []
    base_words = 3000
    for i in range(1, validated.target_chapters + 1):
        chapters.append(
            {
                "number": i,
                "title": f"Capítulo {i}: {title}",
                "objective": f"Desarrollar el núcleo del capítulo {i}.",
                "key_questions": [f"Pregunta clave {i}"],
                "estimated_words": base_words,
                "research_requirements": [],
                "image_requirements": 3,
            }
        )
    return {
        "title": title,
        "subtitle": subtitle,
        "description": validated.idea,
        "target_audience": validated.target_audience or "",
        "chapters": chapters,
    }


def _extract_json(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON del texto de respuesta del LLM."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No se encontró JSON en la respuesta")
    return json.loads(text[start : end + 1])


def execute(payload: dict) -> dict:
    """Genera un plan editorial estructurado a partir de una idea de libro.

    Usa el proveedor LLM configurado. Si no hay proveedor disponible, devuelve
    un plan determinista básico (fallback) sin invocar al LLM.
    """
    validated = validate_payload("create_book_plan", payload)
    model_validated = BookPlanPayload(**validated)
    provider = None
    raw = None
    input_tokens = 0
    output_tokens = 0
    provider_name = "none"
    model_name = ""

    try:
        provider = get_provider()
        prompt = _build_prompt(model_validated)
        result = provider.generate(
            prompt,
            system="Devuelve solo JSON válido, sin texto adicional.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=2000,
            temperature=0.4,
        )
        raw = result.raw_response
        provider_name = result.provider
        model_name = result.model
        input_tokens = result.input_tokens
        output_tokens = result.output_tokens
        plan_data = _extract_json(result.text)
    except Exception as e:
        if provider is not None:
            provider_name = provider.name
        log(
            logger,
            logging.WARNING,
            f"Fallo al generar plan con LLM ({provider_name}): {e}. Usando fallback.",
        )
        plan_data = _fallback_plan(model_validated)

    # Normalizar respuestas imperfectas del LLM ANTES de validar el esquema
    plan_data = _normalize_plan(plan_data, payload)

    try:
        validated_output = BookPlanOutput(**plan_data)
    except Exception as e:
        raise ValueError(f"Plan editorial inválido: {e}") from e

    # Limitar a 40 si el LLM excede el máximo
    chapters = validated_output.chapters or []
    if len(chapters) > 40:
        chapters = chapters[:40]
    validated_output.chapters = chapters

    cost = 0.0
    if provider is not None:
        try:
            cost = float(
                calculate_cost(provider_name or provider.name, model_name or provider.model or DEFAULT_MODEL, input_tokens, output_tokens)
                or 0.0
            )
        except Exception:
            cost = 0.0

    log(
        logger,
        logging.INFO,
        f"Plan editorial generado: {validated_output.title} ({len(chapters)} capítulos)",
    )

    return {
        "title": validated_output.title,
        "subtitle": validated_output.subtitle,
        "description": validated_output.description,
        "target_audience": validated_output.target_audience,
        "chapters": [c.model_dump() for c in validated_output.chapters],
        "provider": provider_name,
        "model": model_name,
        "tokens_input": input_tokens,
        "tokens_output": output_tokens,
        "cost": cost,
    }


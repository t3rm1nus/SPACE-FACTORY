"""Módulo editor: revisión y mejora editorial de capítulos.

Capability: edit_chapter
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from core.logger import get_logger, log
from core.metrics import calculate_cost, extract_anthropic_usage
from core.providers import get as get_provider
from core.schemas import EditorPayload, validate_payload

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen-agent:latest")

# --- Límites para la generación de la edición -----------------------------
# El scheduler aplica un timeout externo (modules/editor/module.json ->
# `timeout_seconds: 180`). Para que un LLM lento o bloqueado NO deje que ese
# timeout externo mate la tarea sin activar el fallback existente, acotamos el
# horizonte de la llamada al proveedor dentro del editor (instancia local, que
# `core.providers.registry.get()` crea nueva por llamada -> seguro de mutar).
EDITOR_PROVIDER_TIMEOUT = 60
EDITOR_MAX_RETRIES = 1

# Presupuesto de tokens para la edición: el editor debe devolver el capítulo
# completo (~input_words palabras) con margen para correcciones, sin pedir
# miles de tokens innecesarios. Política determinista:
#   min(MAX_EDITOR_TOKENS, max(MIN_EDITOR_TOKENS, int(input_words * 1.25)))
MAX_EDITOR_TOKENS = 16000
MIN_EDITOR_TOKENS = 1024
EDITOR_MAX_TOKENS_FACTOR = 1.25

_PLACEHOLDER_PATTERNS = [
    r"Desarrollar el n[úu]cleo",
    r"contenido de prueba",
    r"texto de ejemplo",
    r"Lorem ipsum",
    r"\[pendiente\]",
    r"\bTODO\b",
    r"\bINSERT TEXT\b",
    r"\[([^()]*?)\](?!\s*\()",
    r"\{\{.*?\}\}",
]


def _detect_placeholder(text: str) -> bool:
    if not text or not text.strip():
        return True
    for pat in _PLACEHOLDER_PATTERNS:
        # `TODO` and `INSERT TEXT` are technical tokens: only match uppercase
        # and as whole words, so lowercase `todo/todos/toda` in normal text is ignored.
        flags = 0 if pat in (r"\bTODO\b", r"\bINSERT TEXT\b") else re.IGNORECASE
        if re.search(pat, text, flags):
            return True
    return False


# Copia local de los patrones de rechazo del LLM (duplicada a propósito para no
# crear dependencia cruzada editor -> chapter_writer; misma lista que
# modules/chapter_writer/main.py::REFUSAL_PATTERNS).
_REFUSAL_PATTERNS = [
    r"no puedo ayudar",
    r"lo siento, pero",
    r"como modelo de lenguaje",
    r"no puedo generar",
    r"no puedo continuar con esa solicitud",
    r"as an ai language model",
    r"i cannot assist",
    r"i'm sorry, but i can't",
]


def _detect_refusal(text: str) -> bool:
    """True si ``text`` es un rechazo (refusal) del LLM."""
    if not text or not text.strip():
        return False
    for pat in _REFUSAL_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _build_prompt(validated: dict, *, input_words: int | None = None, minimum_words: int | None = None, target_words: int | None = None) -> str:
    """Construye el prompt de edición editorial con control explícito de longitud."""
    if input_words is None:
        input_words = len((validated.get("chapter_text") or "").split())
    if minimum_words is None:
        minimum_words = max(1500, int(input_words * 0.75))
    if target_words is None:
        target_words = max(input_words, 1500)
    chapter_text = validated.get("chapter_text", "")
    style = validated.get("style_guide") or "neutral, clear and professional"
    protected = validated.get("protected_terms") or []
    facts = validated.get("facts") or []
    references = validated.get("references") or []
    protected_text = "\n".join(f"- {t}" for t in protected[:50]) or "Ninguno"
    facts_text = "\n".join(f"- {f}" for f in facts[:50]) or "Ninguno"
    refs_text = "\n".join(f"- {r}" for r in references[:50]) or "Ninguna"

    return (
        "Eres un editor editorial profesional. Revisa el capítulo y devuelve la versión editada.\n\n"
        f"Capítulo original:\n{chapter_text}\n\n"
        f"Guía de estilo:\n{style}\n\n"
        "Términos protegidos (NO modificar nombres propios):\n"
        f"{protected_text}\n\n"
        "Hechos verificados (NO cambiar hechos verificados bajo ninguna circunstancia):\n"
        f"{facts_text}\n\n"
        "Referencias obligatorias (NO eliminar referencias necesarias):\n"
        f"{refs_text}\n\n"
        "ÁMBITOS A REVISAR:\n"
        "- Gramática y ortografía.\n"
        "- Estilo y claridad.\n"
        "- Estructura y organización de ideas.\n"
        "- Redundancias y repeticiones.\n"
        "- Transiciones entre párrafos y secciones.\n"
        "- Consistencia terminológica (usar siempre el mismo término para el mismo concepto).\n"
        "- Tono coherente con la guía de estilo.\n"
        "- Ritmo y flujo de la prosa.\n\n"
        "REGLAS ESTRICTAS DE LONGITUD:\n"
        f"- Devuelve el capítulo COMPLETO editado.\n"
        f"- NO hagas un resumen.\n"
        f"- NO reducescas sustancialmente la longitud.\n"
        f"- NO inventar información.\n"
        f"- Debes conservar todas las secciones, párrafos, hechos, fechas, nombres, cifras y explicaciones del original.\n"
        f"- Mantén aproximadamente la misma cantidad de palabras que el texto de entrada.\n"
        f"- La salida debe tener como objetivo entre 90% y 110% de las palabras originales, salvo que exista una razón editorial excepcional.\n"
        f"- Si el original tiene {input_words} palabras, una salida de 320 palabras es INACEPTABLE.\n"
        f"- No elimines contenido simplemente para hacer el texto más conciso.\n"
        f"- No conviertas párrafos desarrollados en listas breves.\n"
        f"- No sustituyas explicaciones por resúmenes.\n"
        f"- Conserva la estructura y profundidad del capítulo.\n"
        f"- Mejora redacción, gramática, cohesión y estilo SIN reducir sustancialmente el contenido.\n"
        f"- edited_text debe contener el capítulo completo, no una sinopsis.\n"
        f"- No introduzcas comentarios del editor dentro de edited_text.\n"
        f"- NO inventar información.\n"
        f"- Conserva todas las ideas, hechos, fechas, nombres y afirmaciones del original.\n"
        f"- Nunca finalices prematuramente por ahorrar tokens.\n"
        f"- No elimines secciones ni párrafos para abreviar.\n"
        f"- No sustituyas varios párrafos por un único resumen.\n\n"
        "CONTROL DE LONGITUD:\n"
        f"Palabras aproximadas del original: {input_words}\n"
        f"Longitud mínima esperada de edited_text: {minimum_words}\n"
        f"Longitud objetivo aproximada: {target_words}\n"
        f"Si el capítulo original tiene {input_words} palabras, la salida debe mantenerse en ese orden de magnitud, no en ~300.\n\n"
        "Una respuesta considerablemente inferior a esa longitud será considerada incorrecta.\n\n"
        "Devuelve SOLO JSON válido con estas claves:\n"
        '{"edited_text":"...","editorial_notes":["..."],"changes_summary":["..."]}\n'
        "- edited_text: el capítulo COMPLETO editado (nunca un resumen).\n"
        "- editorial_notes: observaciones para el autor (errores corregidos, sugerencias).\n"
        "- changes_summary: lista breve de los cambios realizados.\n"
        "IMPORTANTE: La respuesta debe contener SIEMPRE el JSON COMPLETO. No cortes edited_text.\n"
        "No incluyas explicaciones ni texto fuera del JSON. edited_text debe contener el capítulo completo, no un resumen."
    )


def _parse_llm_output(text: str) -> dict[str, Any]:
    """Extrae el JSON de la respuesta del LLM."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("La salida no es un objeto JSON")
        # Normaliza campos de lista: el modelo a veces los devuelve como string.
        # No reconstruye JSON truncado; si indica texto vacío el fallback se activa.
        for key in ("editorial_notes", "changes_summary"):
            val = data.get(key)
            if isinstance(val, str):
                data[key] = [val] if val.strip() else []
        return data
    except (json.JSONDecodeError, ValueError):
        return {
            "edited_text": "",
            "editorial_notes": [],
            "changes_summary": [],
        }


def _fallback_edit(validated: dict) -> dict[str, Any]:
    """Resultado determinista cuando no hay LLM disponible: devuelve el texto
    sin cambios y notas indicando que no se aplicó edición automática."""
    chapter_text = validated.get("chapter_text", "")
    return {
        "edited_text": chapter_text,
        "editorial_notes": [
            "No se pudo invocar la edición con LLM. El texto se devuelve sin cambios.",
            "Revisar manualmente gramática, ortografía, estilo y estructura.",
        ],
        "changes_summary": [
            "Sin cambios aplicados (fallback por indisponibilidad del proveedor)."
        ],
    }


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

    healthy = provider is not None and checks.get("provider_health") is not False
    status = "🟢 healthy" if healthy else "🔴 unhealthy"
    if provider:
        status += f" ({provider.name})"
    return {
        "healthy": healthy,
        "dependencies": checks,
        "status": status,
    }


def _build_retry_prompt(validated: dict, *, input_words: int, minimum_words: int, target_words: int, previous_output_words: int) -> str:
    chapter_text = validated.get("chapter_text", "")
    style = validated.get("style_guide") or "neutral, clear and professional"
    protected = validated.get("protected_terms") or []
    facts = validated.get("facts") or []
    references = validated.get("references") or []
    protected_text = "\n".join(f"- {t}" for t in protected[:50]) or "Ninguno"
    facts_text = "\n".join(f"- {f}" for f in facts[:50]) or "Ninguno"
    refs_text = "\n".join(f"- {r}" for r in references[:50]) or "Ninguna"

    return (
        "Eres un editor editorial profesional. Devuelve el capítulo COMPLETO editado en JSON.\n\n"
        "La respuesta anterior fue demasiado corta.\n\n"
        f"El capítulo original tiene aproximadamente {input_words} palabras.\n"
        f"La respuesta anterior tuvo solamente {previous_output_words} palabras.\n\n"
        "DEBES devolver el capítulo completo editado.\n"
        "NO hagas un resumen.\n"
        "NO omitas secciones.\n"
        "NO reduzcas el contenido.\n\n"
        f"Longitud mínima obligatoria: {minimum_words} palabras.\n\n"
        f"Capítulo original:\n{chapter_text}\n\n"
        f"Guía de estilo:\n{style}\n\n"
        "Términos protegidos (NO modificar nombres propios):\n"
        f"{protected_text}\n\n"
        "Hechos verificados (NO cambiar hechos verificados bajo ninguna circunstancia):\n"
        f"{facts_text}\n\n"
        "Referencias obligatorias (NO eliminar referencias necesarias):\n"
        f"{refs_text}\n\n"
        "IMPORTANTE:\n"
        "Esto NO es una tarea de resumen.\n"
        "NO reduzcas la longitud del capítulo.\n"
        "NO conviertas el capítulo en una síntesis.\n"
        "NO elimines secciones ni párrafos por brevedad.\n"
        "La salida debe contener el capítulo COMPLETO editado.\n"
        "Devuelve ÚNICAMENTE el JSON con edited_text, editorial_notes y changes_summary.\n"
    )


def execute(payload: dict, capability: str = "edit_chapter") -> dict:
    """Edita editorialmente el capítulo respetando hechos y referencias."""
    validated = validate_payload(capability, payload)
    chapter_text = validated.get("chapter_text") or ""
    input_words = len(chapter_text.split())
    target_words = max(input_words, 1500)
    minimum_words = max(1500, int(input_words * 0.75))
    max_tokens = min(
        MAX_EDITOR_TOKENS,
        max(MIN_EDITOR_TOKENS, int(input_words * EDITOR_MAX_TOKENS_FACTOR)),
    )
    # Umbral anti-resumen: si el original es grande (>=1000 palabras), una salida
    # inferior al 60% de las palabras originales se considera truncada/resumida.
    ratio_floor = int(input_words * 0.60) if input_words >= 1000 else 0
    result_data = {}
    context = None
    retry = 0
    execution_mode = 'real'

    while True:
        try:
            provider = get_provider()
            # Acotar el horizonte de la llamada (local a esta instancia) para que
            # un LLM lento o bloqueado lance un timeout interno y active el
            # fallback existente antes de que el timeout del scheduler (180s)
            # mate la tarea. `registry.get()` devuelve una instancia nueva por
            # llamada, por lo que mutarla aquí no altera configuración global.
            provider.timeout = EDITOR_PROVIDER_TIMEOUT
            provider.max_retries = EDITOR_MAX_RETRIES
            provider_name = provider.name
            if retry == 0:
                prompt = _build_prompt(
                    validated,
                    input_words=input_words,
                    minimum_words=minimum_words,
                    target_words=target_words,
                )
            else:
                prompt = _build_retry_prompt(
                    validated,
                    input_words=input_words,
                    minimum_words=minimum_words,
                    target_words=target_words,
                    previous_output_words=len((result_data.get("edited_text") or "").split()),
                )
            result = provider.generate(
                prompt,
                system="Eres un editor editorial profesional. Devuelve solo JSON.",
                model=DEFAULT_ROUTER_MODEL,
                max_tokens=max_tokens,
                temperature=0.2,
                num_predict=max_tokens,
                context=context,
            )
            if hasattr(result, "raw_response") and isinstance(result.raw_response, dict):
                context = result.raw_response.get("context")
            result_data = _parse_llm_output(result.text)
            # Un rechazo del LLM ("Lo siento, pero no puedo ayudar con eso.")
            # se trata igual que salida inválida: fallback determinista que
            # conserva el capítulo original sin cambios.
            if not result_data.get("edited_text") or _detect_refusal(
                str(result_data.get("edited_text"))
            ):
                execution_mode = "fallback"
                result_data = _fallback_edit(validated)
                log(logger, logging.WARNING, "Salida del LLM detectada como inválida o rechazo (refusal); aplicado fallback.")
        except Exception as e:
            execution_mode = "fallback"
            result_data = _fallback_edit(validated)
            log(logger, logging.WARNING, f"Fallo en edición con LLM: {e}")

        edited_text = str(result_data.get("edited_text") or "").strip()
        output_words = len(edited_text.split())
        too_short = output_words < minimum_words or (ratio_floor and output_words < ratio_floor)
        if too_short and retry < 1 and "fallback" not in str(result_data):
            retry += 1
            context = None
            continue
        break

    notes = [str(n) for n in (result_data.get("editorial_notes") or [])]
    changes = [str(c) for c in (result_data.get("changes_summary") or [])]

    if too_short:
        edited_text = chapter_text
        output_words = len(edited_text.split())
        notes.append(
            f"Salida demasiado corta ({output_words} palabras, mínimo {minimum_words}). Se conserva el original."
        )
        changes.append("Fallback por longitud insuficiente.")
        execution_mode = "fallback"


    placeholder_detected = _detect_placeholder(edited_text)
    quality_gate = "PASS"
    if placeholder_detected or not edited_text or output_words == 0 or output_words < minimum_words:
        quality_gate = "FAIL"

    log(logger, logging.INFO, f"Edición finalizada ({len(edited_text.split())} palabras, {len(changes)} cambios)")
    return {
        "edited_text": edited_text,
        "editorial_notes": notes,
        "changes_summary": changes,
        "input_words": input_words,
        "output_words": len(edited_text.split()),
        "placeholder_detected": placeholder_detected,
        "quality_gate": quality_gate,
        "execution_mode": execution_mode,
    }


"""Módulo translator: traducción editorial entre español e inglés.

Capabilities: translate_es_en, translate_en_es
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
from core.schemas import TranslatorPayload, validate_payload

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen-agent:latest")


def _languages(capability: str) -> tuple[str, str]:
    """Devuelve (idioma_origen, idioma_destino) según la capability."""
    if capability == "translate_es_en":
        return "es", "en"
    return "en", "es"

def _build_translation_prompt(validated: dict, source_lang: str, target_lang: str) -> str:
    """Construye el prompt de traducción editorial."""
    source_text = validated.get("source_text", "")
    style = validated.get("style_guide") or "natural and professional"
    protected = validated.get("protected_terms") or []
    protected_text = "\n".join(f"- {p}" for p in protected[:50]) or "Ninguno"

    return (
        "Eres un traductor editorial profesional. Produce una traducción que parezca "
        "escrita originalmente en el idioma destino, no una traducción literal.\n\n"
        f"Texto fuente en {source_lang.upper()}:\n{source_text}\n\n"
        f"Idioma de destino: {target_lang.upper()}\n\n"
        f"Guía de estilo:\n{style}\n\n"
        "Términos protegidos (nombres propios, NO traducir ni modificar):\n"
        f"{protected_text}\n\n"
        "MANTENER TAL CUAL:\n"
        "- Estructura y headings del documento.\n"
        "- Citas textuales, referencias y notas.\n"
        "- Nombres propios y términos protegidos.\n"
        "- Números, fechas y unidades cuando corresponda.\n\n"
        "ADAPTAR DE FORMA NATURAL:\n"
        "- Expresiones idiomáticas a sus equivalentes idiomáticos.\n"
        "- Construcciones gramaticales naturales del idioma destino.\n"
        "- Puntuación y tono.\n\n"
        "REGLAS:\n"
        "- NO omitir párrafos, frases, citas ni referencias.\n"
        "- NO cambiar el significado ni los datos.\n"
        "- NO inventar información.\n"
        "- Devolver SOLO el texto traducido completo, sin metatexto."
    )


def _build_review_prompt(validated: dict, translated_text: str, source_lang: str, target_lang: str) -> str:
    """Construye el prompt de auditoría automática post-traducción."""
    source_text = validated.get("source_text", "")
    return (
        "Eres un control de calidad de traducción editorial. Compara el texto original "
        "con su traducción y detecta cualquier discrepancia.\n\n"
        f"Texto ORIGINAL en {source_lang.upper()}:\n{source_text}\n\n"
        f"Traducción en {target_lang.upper()}:\n{translated_text}\n\n"
        "Detecta y reporta como issues JSON:\n"
        "- Omissions: párrafos o frases del original ausentes en la traducción.\n"
        "- Numbers: números, fechas o unidades que difieren entre original y traducción.\n"
        "- Names: nombres propios o términos protegidos modificados.\n"
        "- Paragraphs: párrafos faltantes.\n"
        "- Quotes: citas textuales faltantes o alteradas.\n"
        "- Meaning: cambios de significado.\n\n"
        "Devuelve SOLO JSON válido:\n"
        '{"status":"PASS|WARNING","issues":[{"issue_type":"...","severity":"INFO|WARNING|ERROR","description":"..."}]}\n'
        "- Si no hay discrepancia, status es PASS y issues vacío.\n"
        "- severity ERROR para cambios de significado u omisiones graves."
    )



def _extract_json(text: str) -> dict[str, Any]:
    """Extrae un objeto JSON de la respuesta del LLM."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("La salida no es un objeto JSON")
        return data
    except (json.JSONDecodeError, ValueError):
        # Intentar localizar el primer objeto JSON dentro del texto
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass
        return {}


def _extract_numbers(text: str) -> set[str]:
    """Extrae números, fechas y porcentajes para comparar entre original y traducción."""
    return set(re.findall(r"\b\d[\d,\.]*(?:%|º|ª)?", text))


def _fallback_review(source_text: str, translated_text: str) -> dict[str, Any]:
    """Auditoría heurística determinista cuando no hay LLM.

    Detecta discrepancias de números y omisiones evidentes.
    """
    issues: list[dict[str, Any]] = []
    src_nums = _extract_numbers(source_text)
    tgt_nums = _extract_numbers(translated_text)
    missing = src_nums - tgt_nums
    if missing:
        issues.append({
            "issue_type": "Numbers",
            "severity": "ERROR",
            "description": f"Números del original ausentes en la traducción: {', '.join(sorted(missing))}",
        })
    if not translated_text.strip():
        issues.append({
            "issue_type": "Omissions",
            "severity": "ERROR",
            "description": "La traducción está vacía.",
        })
    status = "WARNING" if issues else "PASS"
    return {"status": status, "issues": issues}


def _fallback_translate(validated: dict) -> dict[str, Any]:
    """Fallback determinista: devuelve el texto original sin cambios y avisa."""
    source_text = validated.get("source_text", "")
    return {
        "translated_text": source_text,
        "review_status": "WARNING",
        "review_issues": [
            {
                "issue_type": "Omissions",
                "severity": "ERROR",
                "description": "No se pudo invocar la traducción con LLM. El texto se devuelve sin cambios.",
            }
        ],
        "changes_summary": ["Sin traducción aplicada (fallback por indisponibilidad del proveedor)."],
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




def execute(payload: dict, capability: str = "translate_es_en") -> dict:
    """Traduce editorialmente y audita la traducción automáticamente."""
    validated = validate_payload(capability, payload)
    source_lang, target_lang = _languages(capability)
    provider = None
    input_tokens = 0
    output_tokens = 0
    provider_name = "none"

    # Paso 1: traducción
    translated_text = ""
    try:
        provider = get_provider()
        provider_name = provider.name
        prompt = _build_translation_prompt(validated, source_lang, target_lang)
        result = provider.generate(
            prompt,
            system=f"Traduce al {target_lang.upper()}. Devuelve solo el texto traducido.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=4000,
            temperature=0.2,
        )
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        translated_text = result.text.strip()
    except Exception as e:
        log(
            logger,
            logging.WARNING,
            f"Fallo en traducción con LLM ({provider_name}): {e}. Usando fallback.",
        )

    if not translated_text:
        result = _fallback_translate(validated)
        log(
            logger,
            logging.INFO,
            "Traducción no disponible: se devuelve el texto original sin cambios.",
        )
        return result

    # Paso 2: auditoría automática post-traducción
    review: dict[str, Any] = {}
    try:
        review_prompt = _build_review_prompt(validated, translated_text, source_lang, target_lang)
        res = provider.generate(
            review_prompt,
            system="Audita la calidad de la traducción. Devuelve solo JSON.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=2000,
            temperature=0.0,
        )
        input_tokens += res.input_tokens
        output_tokens += res.output_tokens
        review = _extract_json(res.text)
    except Exception as e:
        log(
            logger,
            logging.WARNING,
            f"Fallo en auditoría post-traducción ({provider_name}): {e}. Usando heurística.",
        )

    if not review.get("issues"):
        # Si el LLM no aportó issues, corroborar con la auditoría heurística
        heuristic = _fallback_review(validated.get("source_text", ""), translated_text)
        if heuristic["issues"] and not review.get("issues"):
            review = heuristic

    issues_raw = review.get("issues") or []
    normalized_issues: list[dict[str, Any]] = []
    for issue in issues_raw:
        if not isinstance(issue, dict):
            continue
        normalized_issues.append({
            "issue_type": issue.get("issue_type", "Unknown"),
            "severity": issue.get("severity", "INFO"),
            "description": issue.get("description", ""),
        })

    review_status = str(review.get("status", "PASS")).upper()
    if review_status not in ("PASS", "WARNING"):
        review_status = "WARNING"
    if any(i.get("severity") == "ERROR" for i in normalized_issues):
        review_status = "WARNING"

    log(
        logger,
        logging.INFO,
        f"Traducción {source_lang}->{target_lang} finalizada. Revisión: {review_status} "
        f"({len(normalized_issues)} issues)",
    )
    return {
        "translated_text": translated_text,
        "review_status": review_status,
        "review_issues": normalized_issues,
        "changes_summary": [
            f"Traducción editorial {source_lang.upper()} -> {target_lang.upper()}.",
            f"Auditoría automática: {review_status} con {len(normalized_issues)} issue(s).",
        ],
    }

    healthy = provider is not None and checks.get("provider_health") is not False
    status = "🟢 healthy" if healthy else "🔴 unhealthy"
    if provider:
        status += f" ({provider.name})"
    return {
        "healthy": healthy,
        "dependencies": checks,
        "status": status,
    }


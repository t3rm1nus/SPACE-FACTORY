"""Modulo fact_checker: verifica afirmaciones de un capitulo contra fuentes.

Capability: fact_check_chapter
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
from core.schemas import FactCheckPayload, validate_payload

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen-agent:latest")

_NUM_PATTERN = re.compile(r"\b(?:\d+[\d,\.]*%|\d{4}|\$\d+|\d+ millones|\d+ billion|\d+ trillion)\b", re.IGNORECASE)
_QUOTE_PATTERN = re.compile(r'"[^"]+"|' + r"'[^']+'")
_DATE_PATTERN = re.compile(r"\b(?:(?:19|20)\d{2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b")

# §17 #20 (PASO 1): detección estructural de fabricación factual.
# Una claim SIN SOPORTE que combina (a) fecha/año concreto, (b) nombre propio
# y (c) cifra numérica tiene la firma de un hecho fabricado con apariencia
# factual y debe clasificarse ERROR, no WARNING.
# Nota de diseño: un año de 4 dígitos cuenta simultáneamente como fecha y como
# cifra ("establecido en 1942" ya es una cuantificación concreta del hecho).
_UNSUPPORTED_MARKER = re.compile(
    r"sin\s+(fuente|soporte|respaldo)|no\s+tiene\s+una\s+fuente|not\s+supported|"
    r"does\s+not\s+(?:mention|provide)|no\s+se\s+menciona|no\s+proporciona|"
    r"unsupported|sin\s+fuentes?",
    re.IGNORECASE,
)
_PROPER_NOUN_PATTERN = re.compile(r"(?<!^)(?<![.!?\x22\x27]\s)(?<!\n)\b[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]{2,}\b")
# Bigrama capitalizado (ej. "Adolf Eichmann", "Majdal Shams"): entidad propia
# concreta y verificable. Una claim sin soporte que la nombra es fabricación
# potencial aunque no incluya fecha ni cifra (caso book_59 claim Eichmann).
_PROPER_NOUN_PAIR_PATTERN = re.compile(
    r"\b[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+\s+(?:[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+|de\s+[A-ZÁÉÍÓÚÜÑ][a-záéíóúüñ]+)"
)


def _is_unsupported_issue(issue: dict[str, Any]) -> bool:
    """True si el issue indica que la claim carece de soporte en las fuentes."""
    if issue.get("source_url"):
        return False
    text = f"{issue.get('claim', '')} {issue.get('reason', '')}"
    return bool(_UNSUPPORTED_MARKER.search(text))


def _has_fabrication_signature(claim_text: str) -> bool:
    """True si la claim tiene especificidad factual verificable sin soporte.

    Firma aceptada (cualquiera de):
    - fecha/año + cifra numérica (el año cuenta como cifra) + nombre propio;
    - nombre propio COMPUESTO (bigrama capitalizado: "Adolf Eichmann",
      "Majdal Shams"), que identifica una entidad concreta verificable.
    """
    has_date = bool(_DATE_PATTERN.search(claim_text))
    # La cifra puede ser la propia fecha (año de 4 dígitos) u otro número.
    has_number = bool(re.search(r"\d[\d,.]*", claim_text))
    if has_date and has_number and _PROPER_NOUN_PATTERN.search(claim_text):
        return True
    return bool(_PROPER_NOUN_PAIR_PATTERN.search(claim_text))


def _escalate_fabrication_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Eleva a ERROR una claim sin soporte con firma de fabricación factual.

    §17 #20: el LLM clasificaba estas claims como WARNING ("sin soporte"),
    dejando pasar hechos fabricados (ej. book_59 cap.2: campos de concentración
    en Palestina atribuidos a Eichmann, 1942-1948, sin ninguna base en las
    fuentes). La combinación fecha + nombre propio + cifra SIN soporte es una
    señal estructural de fabricación, independiente de lo grave que sea el tema.
    """
    if str(issue.get("severity", "")).upper() == "ERROR":
        return issue
    claim = str(issue.get("claim", ""))
    if _has_fabrication_signature(claim) and _is_unsupported_issue(issue):
        issue = dict(issue)
        issue["severity"] = "ERROR"
        issue["reason"] = (
            f"{issue.get('reason') or ''} "
            "[Elevado a ERROR: afirmación sin soporte en fuentes que combina "
            "fecha + nombre propio + cifra numérica — patrón de fabricación factual]"
        ).strip()
        if not issue.get("suggestion"):
            issue["suggestion"] = (
                "Eliminar o reformular la afirmación: no puede publicarse "
                "especificidad factual (fechas, nombres, cifras) sin fuente."
            )
    return issue



def _heuristic_issues(text: str, sources: list[dict]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    nums = _NUM_PATTERN.findall(text)
    if nums:
        issues.append({
            "claim": f"Números detectados: {', '.join(nums[:5])}",
            "severity": "INFO",
            "reason": "Se detectaron valores numéricos; verificar precisión y contexto.",
            "source_url": None,
            "suggestion": "Confirmar cifras contra fuentes oficiales o primarias.",
        })
    quotes = _QUOTE_PATTERN.findall(text)
    if quotes:
        issues.append({
            "claim": f"Citas detectadas: {', '.join(quotes[:5])}",
            "severity": "WARNING",
            "reason": "Citas textuales sin fuente asociada explícitamente en el texto.",
            "source_url": None,
            "suggestion": "Añadir la fuente original entre paréntesis o en 'Sources used'.",
        })
    dates = _DATE_PATTERN.findall(text)
    if dates:
        issues.append({
            "claim": f"Fechas detectadas: {', '.join(dates[:5])}",
            "severity": "INFO",
            "reason": "Verificar que las fechas no estén obsoletas o mal interpretadas.",
            "source_url": None,
            "suggestion": "Actualizar si existe información más reciente disponible.",
        })
    if not sources:
        issues.append({
            "claim": "Sin fuentes proporcionadas",
            "severity": "ERROR",
            "reason": "No hay fuentes para contrastar las afirmaciones del capítulo.",
            "source_url": None,
            "suggestion": "Aportar al menos una fuente verificable por afirmación factual.",
        })
    return issues


def _build_prompt(validated: dict) -> str:
    chapter_text = validated.get("chapter_text", "")
    sources = validated.get("sources") or []
    sources_text = "\n".join(
        f"- {s.get('title') or s.get('url')} ({s.get('source_type') or 'web'}): {s.get('url')}"
        for s in sources[:20]
    )
    return (
        "Eres un verificador de hechos editorial. Analiza el capítulo y las fuentes.\n\n"
        f"Capítulo:\n{chapter_text}\n\n"
        f"Fuentes permitidas:\n{sources_text or 'Ninguna'}\n\n"
        "REGLAS:\n"
        "- Extrae afirmaciones verificables del capítulo.\n"
        "- Asocia cada afirmación con una fuente SOLO si la fuente aparece en la lista permitida.\n"
        "- Detectar afirmaciones sin fuente.\n"
        "- Detectar contradicciones internas en el capítulo.\n"
        "- Detectar datos potencialmente obsoletos (fechas, cifras antiguas).\n"
        "- Detectar números sospechosos o sin contexto.\n"
        "- Detectar citas no verificadas.\n"
        "- Clasificar cada problema como INFO, WARNING o ERROR.\n"
        "- Proponer correcciones concretas.\n"
        "- NUNCA inventes fuentes. Si no hay fuente, no la inventes.\n"
        "- Devuelve SOLO JSON válido con estas claves:\n"
        '{"status":"PASS|WARNING|FAIL","claims_checked":0,"issues":[],"corrections":[],"unsupported_claims":[]}\n'
        "- Cada issue debe tener: claim, severity, reason, source_url (o null), suggestion (o null)."
    )



def _parse_llm_output(text: str) -> dict[str, Any]:
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
        return {
            "status": "WARNING",
            "claims_checked": 0,
            "issues": [],
            "corrections": ["No se pudo parsear la verificación detallada. Revisar manualmente."],
            "unsupported_claims": [],
        }


def _fallback_result(validated: dict) -> dict[str, Any]:
    issues = _heuristic_issues(validated.get("chapter_text", ""), validated.get("sources") or [])
    unsupported = [i["claim"] for i in issues if i["severity"] == "ERROR"]
    status = "FAIL" if any(i["severity"] == "ERROR" for i in issues) else (
        "WARNING" if issues else "PASS"
    )
    return {
        "status": status,
        "claims_checked": max(1, len(issues)),
        "issues": issues,
        "corrections": [
            "Verificación heurística: revisar manualmente las afirmaciones flagged."
        ] if issues else [],
        "unsupported_claims": unsupported,
    }


def health_check() -> dict:
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


def execute(payload: dict, capability: str = "fact_check_chapter") -> dict:
    validated = validate_payload(capability, payload)
    provider = None
    input_tokens = 0
    output_tokens = 0
    provider_name = "none"
    model_name = ""
    execution_mode = "real"

    result_data: dict[str, Any] = {}
    try:
        provider = get_provider()
        provider_name = provider.name
        prompt = _build_prompt(validated)
        result = provider.generate(
            prompt,
            system="Eres un verificador de hechos editorial. Devuelve solo JSON.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=4000,
            temperature=0.1,
        )
        input_tokens = result.input_tokens
        output_tokens = result.output_tokens
        model_name = result.model
        result_data = _parse_llm_output(result.text)
        if not result_data:
            execution_mode = "fallback"
    except Exception as e:
        execution_mode = "fallback"
        log(
            logger,
            logging.WARNING,
            f"Fallo en fact-checking con LLM ({provider_name}): {e}. Usando fallback.",
        )
        result_data = _fallback_result(validated)

    if not result_data:
        execution_mode = "fallback"
        result_data = _fallback_result(validated)

    issues_raw = result_data.get("issues") or []
    normalized_issues: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    for issue in issues_raw:
        if not isinstance(issue, dict):
            continue
        # Dedupe por texto de claim normalizado (lowercase + strip + espacios
        # colapsados): el LLM a veces repite la misma claim; se conserva la
        # primera aparición (mismo patrón que _dedupe_by_path en autopilot).
        claim_key = " ".join(str(issue.get("claim", "")).lower().split())
        if claim_key in seen_claims:
            continue
        seen_claims.add(claim_key)
        normalized_issues.append(
            _escalate_fabrication_issue({
                "claim": issue.get("claim", ""),
                "severity": issue.get("severity", "INFO"),
                "reason": issue.get("reason", ""),
                "source_url": issue.get("source_url"),
                "suggestion": issue.get("suggestion"),
            })
        )

    unsupported = result_data.get("unsupported_claims") or []
    corrections = result_data.get("corrections") or []

    status = str(result_data.get("status", "WARNING")).upper()
    if status not in ("PASS", "WARNING", "FAIL"):
        status = "WARNING"
    if any(i.get("severity") == "ERROR" for i in normalized_issues):
        status = "FAIL"
    elif status == "PASS" and normalized_issues:
        status = "WARNING"

    # Quality gate: si research_required=true y no hay fuentes, FAIL.
    # Si no hay contenido suficiente para verificar, FAIL.
    sources = validated.get("sources") or []
    research_required = validated.get("research_required", True)
    chapter_text = (validated.get("chapter_text") or "").strip()

    quality_gate = "PASS"
    quality_reasons: list[str] = []
    if research_required and not sources:
        quality_gate = "FAIL"
        quality_reasons.append("research_required=true y no hay fuentes para verificar")
    if not chapter_text or len(chapter_text.split()) < 10:
        quality_gate = "FAIL"
        quality_reasons.append("no hay contenido suficiente para verificar")
    # §17 #20 (PASO 1): un issue ERROR (incluidas claims de fabricación factual
    # escaladas) debe bloquear el gate AQUÍ, en el módulo, para que el contenido
    # fabricado no llegue al DOCX. El gate compuesto de autopilot.py no se toca.
    if any(i.get("severity") == "ERROR" for i in normalized_issues):
        quality_gate = "FAIL"
        quality_reasons.append("claims con severidad ERROR sin soporte en fuentes")

    # Métricas
    supported = int(result_data.get("supported_claims", 0))
    conflicting = int(result_data.get("conflicting_claims", 0))

    llm_claims_checked = int(result_data.get("claims_checked", 0))
    if normalized_issues:
        claims_checked = max(1, len(normalized_issues))
    else:
        claims_checked = 0
        if llm_claims_checked > 0:
            log(
                logger,
                logging.WARNING,
                f"Fact-check: el LLM reportó claims_checked={llm_claims_checked} pero "
                f"issues está vacío. Se fuerza claims_checked=0 para mantener trazabilidad.",
            )

    if quality_gate == "FAIL":
        status = "FAIL"

    output = {
        "status": status,
        "claims_checked": claims_checked,
        "issues": normalized_issues,
        "corrections": corrections,
        "unsupported_claims": unsupported,
        "supported_claims": supported,
        "conflicting_claims": conflicting,
        "quality_gate": quality_gate,
        "execution_mode": execution_mode,
    }

    log(
        logger,
        logging.INFO if status != "FAIL" else logging.WARNING,
        f"Fact-check finalizado: {status} ({output['claims_checked']} claims, gate={quality_gate})",
    )
    return output


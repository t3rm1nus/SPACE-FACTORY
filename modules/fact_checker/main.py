"""Modulo fact_checker: verifica afirmaciones de un capitulo contra fuentes.

Capability: fact_check_chapter
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from core.logger import get_logger, log
from core.metrics import calculate_cost, extract_anthropic_usage
from core.providers import get as get_provider
from core.schemas import FactCheckPayload, validate_payload

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen-agent:latest")
# §17 #28: seed fijo → determinismo reproductible (T=0.0 sin seed no basta en este despliegue).
FACT_CHECK_SEED = 1337

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
# §17 #47 (2026-08-31): determinantes/artículos excluidos como primera palabra
# válida del bigrama. El patrón anterior aceptaba 1 sola minúscula en la
# primera palabra (`+`), por lo que artículos de 2 letras a inicio de frase
# ("El", "La", "Los", "Un", "The", "A", "An") formaban bigramas falsos con
# cualquier palabra capitalizada siguiente ("El Imperio", "La Cancionero de
# Palacio", "Los Serrano", "El boom", "Los años") → falsos positivos
# fabrication_structural confirmados en book_77/78/80/84.
_DETERMINANTS_FIRST_WORD = frozenset(
    {"El", "La", "Los", "Las", "Un", "Una", "Unos", "Unas", "The", "A", "An"}
)
# Artículo inmediatamente anterior al match (p.ej. "La Cancionero de Palacio"
# también matchea arrancando en "Cancionero de Palacio"; y "de los Reyes
# Católicos" arranca en "Reyes Católicos" — artículos en minúscula a mitad de
# frase incluidos vía IGNORECASE; se descarta si la palabra previa es un
# determinante).
_PRECEDING_DETERMINANT = re.compile(
    r"\b(?:El|La|Los|Las|Un|Una|Unos|Unas|The|A|An)\s+$", re.IGNORECASE
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
      "Majdal Shams"), que identifica una entidad concreta verificable;
      §17 #47: los matches cuya primera palabra es un determinante/artículo
      (ES: El/La/Los/Las/Un/Una/Unos/Unas; EN: The/A/An), o que van
      precedidos inmediatamente de uno, se descartan como falso positivo
      ("El Imperio", "La Cancionero de Palacio" no son entidades propias).
    """
    has_date = bool(_DATE_PATTERN.search(claim_text))
    # La cifra puede ser la propia fecha (año de 4 dígitos) u otro número.
    has_number = bool(re.search(r"\d[\d,.]*", claim_text))
    if has_date and has_number and _PROPER_NOUN_PATTERN.search(claim_text):
        return True
    for match in _PROPER_NOUN_PAIR_PATTERN.finditer(claim_text):
        first_word = match.group(0).split()[0]
        if first_word in _DETERMINANTS_FIRST_WORD:
            continue
        if match.start() > 0 and _PRECEDING_DETERMINANT.search(claim_text[: match.start()]):
            continue
        return True
    return False


_REANCHOR_NGRAM_WORDS = 8  # §17 #32-P3: tamaño del n-grama de re-anclaje


def _find_reanchor_source(claim_text: str, sources: list[dict[str, Any]]) -> str | None:
    """Re-anclaje determinista claim→fuente por n-gramas de 8 palabras (§17 #32-P3).

    Normaliza claim y content (minúsculas, espacios/saltos colapsados, strip),
    genera los n-gramas de 8 palabras consecutivas del claim y devuelve la URL
    de la PRIMERA fuente (en orden) cuyo content normalizado contiene
    CUALQUIERA de esos n-gramas como substring literal. Si el claim tiene
    menos de 8 palabras, o ninguna fuente matchea (o su content está vacío y
    se salta), devuelve None.
    """
    claim_norm = " ".join(str(claim_text or "").lower().split())
    words = claim_norm.split()
    if len(words) < _REANCHOR_NGRAM_WORDS:
        return None
    n = _REANCHOR_NGRAM_WORDS
    ngrams = {
        " ".join(words[i : i + n]) for i in range(len(words) - n + 1)
    }
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        content = " ".join(str(source.get("content") or "").lower().split())
        if not content:
            continue
        for ngram in ngrams:
            if ngram in content:
                return source.get("url")
    return None


def _escalate_fabrication_issue(
    issue: dict[str, Any],
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Eleva a ERROR una claim sin soporte con firma de fabricación factual.

    §17 #20: el LLM clasificaba estas claims como WARNING ("sin soporte"),
    dejando pasar hechos fabricados (ej. book_59 cap.2: campos de concentración
    en Palestina atribuidos a Eichmann, 1942-1948, sin ninguna base en las
    fuentes). La combinación fecha + nombre propio + cifra SIN soporte es una
    señal estructural de fabricación, independiente de lo grave que sea el tema.

    §17 #32-P3: si se proveen ``sources`` y el claim matchea verbatim (n-grama
    de 8 palabras) el ``content`` de una fuente permitida, NO es fabricación:
    se re-ancla ``source_url`` a esa fuente y se conserva la severity del LLM
    sin escalar (fallback determinista para claims no ancladas por el LLM).
    """
    if str(issue.get("severity", "")).upper() == "ERROR":
        return issue
    claim = str(issue.get("claim", ""))
    if _has_fabrication_signature(claim) and _is_unsupported_issue(issue):
        # §17 #32-P3: re-anclaje determinista ANTES de escalar. Si el claim
        # aparece literalmente (n-grama de 8 palabras) en el content de una
        # fuente permitida, el "sin soporte" era un fallo de anclaje del LLM,
        # no fabricación: se re-ancla y NO se escala.
        if sources:
            reanchor_url = _find_reanchor_source(claim, sources)
            if reanchor_url:
                issue = dict(issue)
                issue["source_url"] = reanchor_url
                return issue
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


# ---------------------------------------------------------------------------
# §17 #22 (fix book_65/book_64): verificación de consistencia para ERROR
# "puros" del LLM (no estructurales).
#
# Problema real (book_65 cap.431 "café Liberica", book_64 cafés de Madrid): el
# LLM del fact_checker clasifica como ERROR —bloqueante, dispara
# quality_gate=FAIL— claims por juicio subjetivo de EXACTITUD ("no es tan raro
# como se describe"), sin pasar por _has_fabrication_signature() ni
# _is_unsupported_issue(). El veredicto es INESTABLE entre reintentos (la misma
# claim cambia de severidad en cada intento) y agota los reintentos de fase.
#
# FIX: toda severity=ERROR que NO sea estructural (no escalada por
# _escalate_fabrication_issue) pasa por una segunda pasada LLM binaria y
# estricta. Si no confirma el ERROR -> se degrada a WARNING (fail-safe hacia
# MENOS bloqueo). Timeout/error/salida inválida también degradan.
#
# Presupuesto: solo se dispara para claims ERROR no estructurales (pocas por
# capítulo en la práctica). Peor caso: N claims × FACT_CHECK_CONSISTENCY_TIMEOUT
# (20s default). Techo AGREGADO por ejecución de execute():
# FACT_CHECK_CONSISTENCY_TOTAL_BUDGET (120s default, env-overridable) acota el
# tiempo TOTAL de todas las verificaciones de consistencia de una misma
# ejecución, dejando ~33% de holgura bajo timeout_seconds=180 del scheduler
# para fact_check_chapter (modules/fact_checker/module.json), mismo criterio
# que WRITER_TOTAL_TIME_BUDGET / RESEARCH_TOTAL_TIME_BUDGET. Caso real task 895
# (book 65): 10 claims ERROR con source_url × 20s secuenciales = 200s > 180s ->
# timeout definitivo del scheduler tras agotar reintentos. Con el techo
# agregado, el peor caso es budget (120s) + una última llamada (≤20s) = 140s
# < 180s, INDEPENDIENTEMENTE del número de claims.
FACT_CHECK_CONSISTENCY_TIMEOUT = float(
    os.environ.get("FACT_CHECK_CONSISTENCY_TIMEOUT", "20")
)
FACT_CHECK_CONSISTENCY_TOTAL_BUDGET = float(
    os.environ.get("FACT_CHECK_CONSISTENCY_TOTAL_BUDGET", "120")
)

_CONSISTENCY_DOWNGRADE_NOTE = (
    "[Degradado a WARNING: verificación de consistencia no confirmó ERROR]"
)
_CONSISTENCY_BUDGET_NOTE = (
    "[Degradado a WARNING: presupuesto de verificación de consistencia agotado]"
)


def _verify_error_consistency(claim_text: str, reason: str, context: str = "") -> bool:
    """Segunda pasada binaria para un ERROR subjetivo del LLM.

    Pregunta de forma estricta y acotada si la claim es un error factual claro
    que requiere corrección obligatoria o una afirmación defendible / sin
    verificación concluyente. Devuelve True SOLO si la segunda pasada confirma
    el ERROR de forma inequívoca (respuesta normalizada que empieza por
    "ERROR").

    Fail-safe: cualquier excepción, timeout o salida inválida devuelve False
    (degrada a WARNING; nunca mantenemos un bloqueo por un fallo de la propia
    verificación).
    """
    try:
        provider = get_provider()
        provider.timeout = FACT_CHECK_CONSISTENCY_TIMEOUT
        provider.max_retries = 0
        prompt = (
            "Evalúa esta afirmación marcada como ERROR factual:\n\n"
            f"Afirmación: {claim_text}\n"
            f"Motivo del rechazo: {reason}\n"
            + (f"Contexto del capítulo: {context}\n" if context else "")
            + "\n¿Es un error factual CLARO que requiere corrección obligatoria, "
            "o es una afirmación defendible / sin verificación concluyente?\n"
            "Responde EXACTAMENTE con una sola palabra:\n"
            "- \"ERROR\" si es un error factual claro y verificable que exige corrección.\n"
            "- \"DEFENDIBLE\" si es defendible, opinable o no hay verificación concluyente."
        )
        result = provider.generate(
            prompt,
            system=(
                "Eres un verificador de hechos editorial. Responde únicamente "
                "con la palabra ERROR o DEFENDIBLE."
            ),
                        model=DEFAULT_ROUTER_MODEL,
            max_tokens=8,
            temperature=0.0,
            seed=FACT_CHECK_SEED,
        )
        answer = " ".join(str(result.text or "").upper().split())
        if answer.startswith("DEFENDIBLE"):
            return False
        return answer.startswith("ERROR")
    except Exception as e:
        log(
            logger,
            logging.WARNING,
            f"Verificación de consistencia falló ({e}); fail-safe: degrada a WARNING.",
        )
        return False


def _apply_error_consistency_pass(
    issues: list[dict[str, Any]], context: str = ""
) -> list[dict[str, Any]]:
    """Segunda fase del pipeline: re-verifica ERRORs NO estructurales.

    - Las claims escaladas por _escalate_fabrication_issue son estructurales
      (firma de fabricación factual) y NUNCA pasan por esta verificación.
    - Solo aplica con ejecución LLM real (execution_mode == "real"); en
      fallback/backstop determinista no hay nada que re-verificar.
    - Ajuste de diseño (book_65 "café Liberica", tasks 890/891): un ERROR del
      LLM SIN source_url no se somete a segunda pasada: no tiene sentido
      preguntar "¿es verificable?" cuando no existe fuente contra la que
      verificar. Se degrada DIRECTAMENTE a WARNING (fail-safe hacia MENOS
      bloqueo) sin gastar la llamada LLM extra. Con source_url presente se
      mantiene el comportamiento anterior (segunda pasada binaria).
    - Un ERROR confirmado se mantiene intacto; uno no confirmado (o con
      verificación fallida/timeout) se degrada a WARNING con nota en reason.
    - Las claves internas que empiezan por "_" se eliminan antes de devolver.
    """
    out: list[dict[str, Any]] = []
    # §17 #35 F1: clasificación persistente de error_type (sin prefijo _) sobre la
    # lista ORIGINAL de issues (todavía conserva claves internas _) antes de que el
    # filtro {k:v for k,v in ... if not k.startswith("_")} las elimine. error_type
    # no lleva prefijo _ por lo que sobrevive al filtrado y se expone a la orquestación.
    for _issue in issues:
        if _issue.get("severity") == "ERROR":
            _issue["error_type"] = (
                "fabrication_structural" if _issue.get("_fabrication_structural") else "accuracy_partial"
            )
    budget_start = time.perf_counter()
    for issue in issues:
        if (
            issue.get("severity") == "ERROR"
            and not issue.get("_fabrication_structural")
            and issue.get("_llm_original_error")
        ):
            issue = dict(issue)
            if not issue.get("source_url"):
                # Sin fuente asociada no hay nada que verificar: downgrade
                # directo sin llamada LLM adicional.
                issue["severity"] = "WARNING"
                issue["reason"] = (
                    f"{issue.get('reason') or ''} "
                    "[Degradado a WARNING: ERROR sin source_url y sin firma de "
                    "fabricación estructural]"
                ).strip()
                issue["consistency_check"] = "SKIPPED_NO_SOURCE"
            elif (
                FACT_CHECK_CONSISTENCY_TOTAL_BUDGET
                - (time.perf_counter() - budget_start)
            ) < FACT_CHECK_CONSISTENCY_TIMEOUT:
                # Techo agregado de la ejecución (fix task 895): sin presupuesto
                # para otra llamada completa (≤ FACT_CHECK_CONSISTENCY_TIMEOUT),
                # se degrada DIRECTAMENTE a WARNING sin invocar al provider
                # (mismo criterio fail-safe que timeout/error individual).
                issue["severity"] = "WARNING"
                issue["reason"] = (
                    f"{issue.get('reason') or ''} {_CONSISTENCY_BUDGET_NOTE}"
                ).strip()
                issue["consistency_check"] = "SKIPPED_BUDGET_EXHAUSTED"
            else:
                confirmed = _verify_error_consistency(
                    str(issue.get("claim", "")),
                    str(issue.get("reason", "")),
                    context,
                )
                if confirmed:
                    issue["consistency_check"] = "CONFIRMED"
                else:
                    issue["severity"] = "WARNING"
                    issue["reason"] = (
                        f"{issue.get('reason') or ''} {_CONSISTENCY_DOWNGRADE_NOTE}"
                    ).strip()
                    issue["consistency_check"] = "DOWNGRADED"
        out.append({k: v for k, v in issue.items() if not k.startswith("_")})
    return out



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
    # §17 #32-P2: exponer el content de cada fuente en el prompt para que el LLM
    # pueda asociar claims->fuentes reales. Truncado a _PROMPT_CONTENT_TRUNC (200
    # chars/fuente) para no disparar el tamaño del prompt. Sin content -> se omite
    # la línea extra, sin romper el formato existente.
    _PROMPT_CONTENT_TRUNC = 200
    lines = []
    for s in sources[:20]:
        base = f"- {s.get('title') or s.get('url')} ({s.get('source_type') or 'web'}): {s.get('url')}"
        content = s.get("content") or ""
        if content:
            lines.append(f"{base}\n  Contenido: {content[:_PROMPT_CONTENT_TRUNC]}")
        else:
            lines.append(base)
    sources_text = "\n".join(lines)
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
        "- Cada issue debe tener: claim, severity, reason, source_url, suggestion (o null).\n"
        "  source_url: la URL exacta de la fuente permitida cuyo \'Contenido\' respalda la afirmaci\u00f3n, si existe una; si ninguna fuente permitida la respalda, usa null."
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
        # §17 #28: temperature=0.0 (antes 0.1, sin justificación documentada) — fix no-determinismo producción:
        # mismo chapter_text producía ERROR/PASS distinto entre llamadas (book_69 cap.2 ES, tasks 1476/1477).
        result = provider.generate(
            prompt,
            system="Eres un verificador de hechos editorial. Devuelve solo JSON.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=4000,
            temperature=0.0,
            seed=FACT_CHECK_SEED,
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
        original_severity = str(issue.get("severity", "INFO")).upper()
        normalized = _escalate_fabrication_issue(
            {
                "claim": issue.get("claim", ""),
                "severity": issue.get("severity", "INFO"),
                "reason": issue.get("reason", ""),
                "source_url": issue.get("source_url"),
                "suggestion": issue.get("suggestion"),
            },
            validated.get("sources") or [],
        )
        # Marcas internas para la pasada de consistencia (§17 #22):
        # - _fabrication_structural: el ERROR lo puso la escalada estructural
        #   (firma de fabricación factual) -> NUNCA se re-verifica.
        # - _llm_original_error: el ERROR ya venía del LLM (subjetivo) ->
        #   candidato a segunda pasada binaria.
        if original_severity != "ERROR" and normalized.get("severity") == "ERROR":
            normalized["_fabrication_structural"] = True
        elif normalized.get("severity") == "ERROR":
            normalized["_llm_original_error"] = True
        normalized_issues.append(normalized)

    # §17 #22: segunda pasada solo para ERROR subjetivos del LLM, SOLO si hubo
    # llamada LLM real (execution_mode == "real"); en fallback determinista no
    # hay nada que re-verificar.
    if execution_mode == "real":
        normalized_issues = _apply_error_consistency_pass(normalized_issues)

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

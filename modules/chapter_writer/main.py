"""Módulo chapter_writer: genera capítulos completos en español o inglés.

Capabilities: write_chapter_es, write_chapter_en
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from typing import Any

from core.logger import get_logger, log
from core.metrics import calculate_cost, extract_anthropic_usage
from core.providers import get as get_provider
from core.schemas import ChapterWritePayload, validate_payload

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen-agent:latest")


PLACEHOLDER_PATTERNS = [
    r"Desarrollar el n[úu]cleo",
    r"contenido de prueba",
    r"texto de ejemplo",
    r"Lorem ipsum",
    # "pendiente" se mantiene solo en _BRACKET_PLACEHOLDER_KEYWORDS
    # para detectar [pendiente] dentro de corchetes, no como palabra libre
    r"\bTODO\b",
    r"insert text",
    r"\{\{.*?\}\}",
]

# Frases de rechazo del LLM (refusals): si el modelo responde con una negativa
# ("Lo siento, pero no puedo ayudar con eso."), esa propuesta NO es contenido
# válido y debe descartarse igual que un duplicado (sin finalizar el capítulo
# con ella). Detección case-insensitive.
REFUSAL_PATTERNS = [
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
    for pat in REFUSAL_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


_BRACKET_PLACEHOLDER_KEYWORDS = [
    r"TODO",
    r"\bpendiente\b",
    r"insert text",
    r"nombre",
    r"autor",
    r"fecha",
    r"secci[oó]n",
]

# Headings de nivel 2 que el propio pipeline añade legítimamente al final del
# capítulo (p. ej. "## Fuentes utilizadas"), aunque no figuren en el outline.
# No deben considerarse secciones inesperadas.
_BONUS_SECTION_HEADINGS = {
    "fuentes utilizadas",
    "sources used",
    "referencias",
    "references",
    "bibliografia",
    "bibliografia utilizada",
}

# Palabras mínimas que debe aportar una continuación dirigida para considerarse
# progreso significativo. Por debajo, se detiene el bucle sin reintentar.
_MIN_CONTINUATION_WORDS = 5

# ---------------------------------------------------------------------------
# CONTROL DETERMINISTA (FASE 7.9D.7)
#
# El control de continuación es 100% Python. El LLM solo genera texto; Python
# decide objetivo, tamaño de petición, cuándo continuar y cuándo terminar.
# ---------------------------------------------------------------------------

# Estimación conservadora de palabras NUEVAS útiles que aporta una continuación
# dirigida. Se usa para derivar un presupuesto de intentos a partir del déficit
# real, en lugar de un número arbitrario fijo.
AVG_WORDS_PER_CONTINUATION = 700

# HARD LIMIT determinista absoluto de llamadas de continuación. Garantiza que
# nunca exista un bucle infinito, sea cual sea el comportamiento del LLM
# (respuestas vacías, duplicadas, repetidas, etc.).
ABSOLUTE_HARD_LIMIT = 8

# Tamaño de petición de palabras nuevas por continuación, acotado y derivado del
# déficit pendiente. Se pide un pequeño margen encima del déficit porque el LLM
# suele devolver menos contenido del solicitado.
CONTINUATION_REQUEST_FACTOR = 1.2
MIN_CONTINUATION_REQUEST = 150
MAX_CONTINUATION_REQUEST = 1200


# ---------------------------------------------------------------------------
# PROTECCIÓN CONTRA TIME-OUT EXTERNO DEL SCHEDULER (mismo patrón que editor)
#
# El scheduler aplica timeout_seconds=180 (modules/chapter_writer/module.json).
# Para que un Ollama lento o bloqueado NO deje que ese timeout externo mate la
# tarea sin activar el fallback/backstop determinista, acotamos el horizonte de
# la llamada al proveedor dentro del writer (instancia local, que registry.get()
# crea nueva por llamada -> seguro de mutar), igual que hace modules/editor.
# ---------------------------------------------------------------------------
WRITER_PROVIDER_TIMEOUT = 60
WRITER_MAX_RETRIES = 1
# Techo de tiempo total (s) para la fase LLM (generación + continuaciones).
# Con holgura por debajo del timeout del scheduler (180s) para que el backstop
# determinista y el guardado de artefactos tengan hueco y la tarea retorne
# siempre a tiempo.
WRITER_TOTAL_TIME_BUDGET = 150.0


def _llm_budget_exhausted(provider_start_time: float) -> bool:
    """True si la fase LLM (generación + continuaciones) superó el presupuesto.

    Permite que el writer aborte el bucle de continuación y delegue en el backstop
    determinista antes de que el timeout externo del scheduler (180s) lo mate.
    """
    return (time.perf_counter() - provider_start_time) >= WRITER_TOTAL_TIME_BUDGET


def _is_placeholder_bracket(text: str) -> bool:
    """True si ``text`` contiene corchetes con placeholder técnico.

    Ignora referencias bibliográficas del tipo ``[Nombre (web_wikipedia)]``
    porque incluyen paréntesis con el tipo de fuente, y también ignora
    enlaces Markdown del tipo ``[texto](url)``.
    """
    for m in re.finditer(r"\[(.*?)\](?!\s*\()", text):
        inner = m.group(1).strip()
        # Referencia bibliográfica: contiene paréntesis con contenido.
        if re.search(r"\(\s*\S+.*?\)", inner):
            continue
        # Placeholder técnico conocido dentro de corchetes.
        for kw in _BRACKET_PLACEHOLDER_KEYWORDS:
            if re.search(kw, inner, re.IGNORECASE):
                return True
        # Cualquier corchete simple sin referencia se considera placeholder
        # para preservar el comportamiento previo con términos como [sección].
        return True
    return False


def _detect_placeholder(text: str) -> bool:
    """True si ``text`` contiene un placeholder técnico.

    ``TODO`` se detecta como token técnico en mayúsculas y como palabra
    completa (``\\bTODO\\b``, *case-sensitive*) para evitar el falso positivo
    con el español ``todo/todos/toda``. El resto de patrones conserva la
    detección case-insensitive (comportamiento previo, sin cambios).

    Las referencias bibliográficas ``[Nombre (web_wikipedia)]`` se ignoran
    porque su formato incluye paréntesis con el tipo de fuente.
    """
    if not text or not text.strip():
        return True
    for pat in PLACEHOLDER_PATTERNS:
        # `TODO` es un token técnico: solo coincide en mayúsculas y como
        # palabra independiente, por eso no usa IGNORECASE.
        flags = 0 if pat == r"\bTODO\b" else re.IGNORECASE
        if re.search(pat, text, flags):
            return True
    if _is_placeholder_bracket(text):
        return True
    return False


def _required_min_words(validated: dict) -> int:
    """Mínimo de palabras requerido. Por defecto 1500, salvo que el payload
    especifique otro límite explícitamente."""
    explicit = validated.get("minimum_words")
    if explicit is not None and explicit > 0:
        return int(explicit)
    return 1500


def _normalize_heading(text: Any) -> str:
    """Normaliza un heading para comparación determinista (sin modelo/IA)."""
    if not isinstance(text, str):
        text = str(text or "")
    t = text.lower()
    t = t.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    t = t.replace("ü", "u").replace("ñ", "n")
    t = re.sub(r"[`*_#]", "", t)  # quita marcas markdown y énfasis
    t = re.sub(r"[^\w\s]", " ", t)  # puntuación trivial -> espacios
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_block(text: Any) -> str:
    """Normaliza un bloque de texto para comparación literal conservadora."""
    if not isinstance(text, str):
        text = str(text or "")
    t = text.lower()
    t = t.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    t = t.replace("ü", "u").replace("ñ", "n")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _outline_sections_normalized(validated: dict) -> set[str]:
    """Conjunto normalizado de headings esperados del outline."""
    outline = validated.get("chapter_outline") or {}
    return {
        _normalize_heading(s.get("heading", ""))
        for s in (outline.get("sections") or [])
        if s.get("heading")
    }


def _extract_headings(md: str) -> list[str]:
    """Extrae los headings Markdown de nivel 2 (## Título)."""
    return [
        line.strip()[3:].strip()
        for line in md.splitlines()
        if line.strip().startswith("## ")
    ]


def _detect_unexpected_sections(md: str, validated: dict) -> list[str]:
    """Detecta headings de nivel 2 que no pertenecen al outline.

    Solo informa (no borra contenido). Devuelve la lista de títulos inesperados.
    Los headings añadidos legítimamente por el pipeline (p. ej. "Fuentes
    utilizadas") no se consideran inesperados.
    """
    expected = _outline_sections_normalized(validated)
    unexpected: list[str] = []
    for heading in _extract_headings(md):
        norm = _normalize_heading(heading)
        if norm and norm not in expected and norm not in _BONUS_SECTION_HEADINGS:
            unexpected.append(heading)
    return unexpected


# ---------------------------------------------------------------------------
# CANONICALIZACIÓN DE ESTRUCTURA (FASE 7.9D.7)
#
# El LLM es un trabajador creativo sustituible: puede reformular ligeramente un
# heading, cambiar mayúsculas/acentos/puntuación o producir variantes semánticas.
# Python es la autoridad estructural y decide, de forma 100% determinista (sin
# ningún LLM ni embeddings), qué heading de nivel 2 del capítulo corresponde a
# cada sección del outline y reescribe el texto del heading al canónico del
# outline. Preserva íntegro el contenido editorial debajo de cada heading y NO
# acepta headings arbitrarios sin relación con el outline.
# ---------------------------------------------------------------------------

# Palabras vacías del español que no aportan señal semántica al comparar títulos.
_SPANISH_STOPWORDS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "a",
    "en", "y", "e", "o", "u", "por", "para", "con", "sin", "sobre", "entre", "tras",
    "que", "se", "su", "sus", "es", "son", "lo", "como", "hacia", "hasta", "desde",
    "esta", "este", "estos", "estas", "esa", "esos", "esas", "etc", "ta", "del",
}

# Umbral mínimo (coeficiente de Dice sobre tokens de contenido) para canonizar una
# variante semántica con seguridad. Por debajo, la variante NO se acepta: solo se
# rescata en el caso residual estricto (1:1) exigiendo al menos un token de
# contenido compartido, para no convertir headings arbitrarios en válidos.
_HEADING_SIMILARITY_THRESHOLD = 0.25


def _heading_content_tokens(norm: str) -> list[str]:
    """Tokens con contenido semántico de un heading ya normalizado.

    Elimina palabras vacías y términos triviales para que la comparación
    determinista se base solo en palabras con significado real.
    """
    return [t for t in norm.split() if len(t) > 2 and t not in _SPANISH_STOPWORDS]


def _heading_content_similarity(a_norm: str, b_norm: str) -> float:
    """Coeficiente de Dice sobre los tokens de contenido de dos headings.

    Determinista y sin IA. Devuelve 0.0 si alguno de los dos no tiene tokens de
    contenido o si no comparten ninguno.
    """
    a = set(_heading_content_tokens(a_norm))
    b = set(_heading_content_tokens(b_norm))
    if not a or not b:
        return 0.0
    inter = a & b
    if not inter:
        return 0.0
    return (2.0 * len(inter)) / (len(a) + len(b))


def _canonicalize_headings(md: str, validated: dict) -> str:
    """Reescribe los headings ``##`` del markdown al canónico del outline.

    Es una capa determinista de resolución de estructura: cada heading de nivel 2
    del texto generado se resuelve contra las secciones del outline y, si es una
    variante equivalente (normalización o semejanza de contenido), se reescribe el
    texto del heading al del outline. El contenido bajo cada heading se conserva
    íntegro; los headings fuera del outline que no se correspondan con ninguna
    sección NO se modifican (siguen detectándose como secciones inesperadas).
    No toca el título ``#`` ni las subsecciones ``###``.

    Cada sección del outline se asigna como máximo a un heading, evitando crear
    duplicados canónicos a partir de repeticiones del LLM.
    """
    outline = validated.get("chapter_outline") or {}
    sections = [s.get("heading", "").strip() for s in (outline.get("sections") or []) if s.get("heading")]
    outline_norm_to_orig: dict[str, str] = {}
    for h in sections:
        norm = _normalize_heading(h)
        if norm and norm not in _BONUS_SECTION_HEADINGS:
            outline_norm_to_orig.setdefault(norm, h)
    if not outline_norm_to_orig:
        return md

    headings_h2: list[dict[str, str]] = []
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            title = stripped[3:].strip()
            headings_h2.append({"line": line, "norm": _normalize_heading(title), "title": title})
    if not headings_h2:
        return md

    n = len(headings_h2)
    canonical_for: list[str | None] = [None] * n
    used_outline: set[str] = set()

    # Pase 1: equivalencia exacta bajo normalización (mayúsculas/acentos/puntuación).
    for i, e in enumerate(headings_h2):
        norm = e["norm"]
        if not norm:
            continue
        if norm in outline_norm_to_orig and norm not in used_outline:
            canonical_for[i] = outline_norm_to_orig[norm]
            used_outline.add(norm)

    # Candidatos restantes: headings del texto sin resolver (excluye bonus) y
    # secciones del outline aún sin asignar.
    unmatched_idx = [
        i for i in range(n)
        if canonical_for[i] is None and headings_h2[i]["norm"] and headings_h2[i]["norm"] not in _BONUS_SECTION_HEADINGS
    ]
    unused_outline = [(norm, orig) for norm, orig in outline_norm_to_orig.items() if norm not in used_outline]

    if unmatched_idx and unused_outline:
        remaining_idx = list(unmatched_idx)
        remaining_outline = list(unused_outline)
        changed = True
        while changed:
            changed = False
            best: tuple[int, str, str, float] | None = None
            for i in remaining_idx:
                a_norm = headings_h2[i]["norm"]
                a_tokens = set(_heading_content_tokens(a_norm))
                if not a_tokens:
                    continue
                for bnorm, borig in remaining_outline:
                    score = _heading_content_similarity(a_norm, bnorm)
                    if score > 0 and (best is None or score > best[3]):
                        best = (i, bnorm, borig, score)
            if best and best[3] >= _HEADING_SIMILARITY_THRESHOLD:
                canonical_for[best[0]] = best[2]
                used_outline.add(best[1])
                remaining_idx.remove(best[0])
                remaining_outline = [(bn, bo) for bn, bo in remaining_outline if bn != best[1]]
                changed = True
            else:
                break

        # Pase residual estricto: si queda exactamente una variante sin resolver y
        # exactamente una sección del outline sin asignar, y comparten al menos un
        # token de contenido, se canoniza. No se aplica a headings arbitrarios sin
        # relación semántica con la sección pendiente.
        if len(remaining_idx) == 1 and len(remaining_outline) == 1:
            i = remaining_idx[0]
            bnorm, borig = remaining_outline[0]
            a_norm = headings_h2[i]["norm"]
            if set(_heading_content_tokens(a_norm)) & set(_heading_content_tokens(bnorm)):
                canonical_for[i] = borig

    # Reconstruir el markdown sustituyendo solo los headings canonizados.
    out: list[str] = []
    idx = 0
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            target = canonical_for[idx] if idx < n else None
            if target:
                prefix = line[: len(line) - len(line.lstrip())]
                out.append(f"{prefix}## {target}")
            else:
                out.append(line)
            idx += 1
        else:
            out.append(line)
    return "\n".join(out)


def _section_status(md: str, validated: dict) -> tuple[list[str], list[str]]:
    """Devuelve (secciones ya desarrolladas, secciones pendientes) del outline.

    Se considera desarrollada una sección cuyo heading normalizado aparece como
    heading de nivel 2 en el texto ya generado.
    """
    outline = validated.get("chapter_outline") or {}
    sections = [s.get("heading", "") for s in (outline.get("sections") or []) if s.get("heading")]
    present = {_normalize_heading(h) for h in _extract_headings(md)}
    developed: list[str] = []
    pending: list[str] = []
    for heading in sections:
        if _normalize_heading(heading) in present:
            developed.append(heading)
        else:
            pending.append(heading)
    return developed, pending


def _extract_heading_structure(md: str) -> list[dict]:
    """Extrae la estructura de headings de nivel 2 (##) y nivel 3 (###).

    Devuelve una lista de dicts con: level, title (texto original), normalized
    (texto normalizado) y position (ordinal del heading dentro del documento,
    1-indexado contando todos los headings, independiente del nivel).
    Se conserva el texto original para diagnóstico, sin modificar el marcado.
    """
    structure: list[dict] = []
    order = 0
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            order += 1
            title = stripped[4:].strip()
            structure.append(
                {
                    "level": 3,
                    "title": title,
                    "normalized": _normalize_heading(title),
                    "position": order,
                }
            )
        elif stripped.startswith("## "):
            order += 1
            title = stripped[3:].strip()
            structure.append(
                {
                    "level": 2,
                    "title": title,
                    "normalized": _normalize_heading(title),
                    "position": order,
                }
            )
    return structure


def _detect_duplicate_sections(md: str) -> list[dict]:
    """Detecta headings de nivel 2 repetidos (misma normalización).

    Devuelve, por cada duplicado: heading (título original), occurrences y
    positions (ordinales). NO borra ni altera el documento; solo diagnostica.
    """
    structure = _extract_heading_structure(md)
    seen: dict[str, list[dict]] = {}
    for entry in structure:
        if entry["level"] != 2 or not entry["normalized"]:
            continue
        seen.setdefault(entry["normalized"], []).append(entry)
    duplicates: list[dict] = []
    for _norm, entries in seen.items():
        if len(entries) > 1:
            duplicates.append(
                {
                    "heading": entries[0]["title"],
                    "occurrences": len(entries),
                    "positions": [e["position"] for e in entries],
                }
            )
    return duplicates


def _build_subsections_map(md: str) -> dict:
    """Asigna cada subsección ### a su sección ## padre más reciente.

    Clave: heading del padre normalizado; valor: lista de títulos originales
    de las subsecciones. Solo se incluyen parents que tienen subsecciones.
    """
    structure = _extract_heading_structure(md)
    result: dict[str, list[str]] = {}
    current_parent: str | None = None
    for entry in structure:
        if entry["level"] == 2:
            current_parent = entry["normalized"]
            result.setdefault(current_parent, [])
        elif entry["level"] == 3 and current_parent is not None:
            result.setdefault(current_parent, []).append(entry["title"])
    return {k: v for k, v in result.items() if v}


_CLOSING_SECTION_HEADINGS = {
    "conclusion",
    "fuentes utilizadas",
    "sources used",
    "referencias",
    "references",
    "bibliografia",
    "bibliografia utilizada",
}


def _detect_structural_anomalies(md: str) -> list[dict]:
    """Detecta anomalías de orden estructural sin modificar el documento.

    Reglas:
      - `## Conclusión` no debe repetirse (duplicate_closing_section).
      - Ningún heading editorial `##` debe aparecer después de un cierre
        (`## Conclusión` o `## Fuentes utilizadas`) (section_after_closing_section).
    """
    structure = _extract_heading_structure(md)
    anomalies: list[dict] = []
    closing_seen = False
    last_closing_title: str | None = None
    for entry in structure:
        if entry["level"] != 2 or not entry["normalized"]:
            continue
        is_closing = entry["normalized"] in _CLOSING_SECTION_HEADINGS
        if is_closing:
            closing_seen = True
            last_closing_title = entry["title"]
        elif closing_seen:
            anomalies.append(
                {
                    "type": "section_after_closing_section",
                    "heading": entry["title"],
                    "after": last_closing_title or "Conclusión",
                    "position": entry["position"],
                }
            )
    conclusion_count = sum(
        1 for e in structure if e["level"] == 2 and e["normalized"] == "conclusion"
    )
    if conclusion_count > 1:
        anomalies.insert(
            0,
            {
                "type": "duplicate_closing_section",
                "heading": "Conclusión",
                "occurrences": conclusion_count,
            },
        )
    return anomalies


def _paragraphs(text: str) -> list[str]:
    """Divide el texto en párrafos no vacíos (separados por línea en blanco)."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _split_sentences(text: str) -> list[str]:
    """Divide el texto en oraciones de forma simple y determinista."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _has_strong_text_overlap(existing_text: str, new_text: str) -> bool:
    """Detección conservadora de duplicación literal fuerte.

    Devuelve True solo ante coincidencias claras, no ante textos que tratan el
    mismo tema. NO es similitud semántica.

    Considera duplicación cuando:
      - un párrafo suficientemente largo es prácticamente idéntico en ambos lados;
      - una secuencia de varias oraciones consecutivas es idéntica en ambos lados
        y la longitud acumulada supera un umbral claro.
    Una frase aislada de 5 palabras NO basta.
    """
    if not existing_text or not new_text:
        return False

    MIN_PARAGRAPH_WORDS = 18
    MIN_RUN_SENTENCES = 3
    MIN_RUN_WORDS = 40
    # Tolerancia a conectores: un pequeño bloque de conexión (menos de
    # MIN_TOLERANT_WORDS palabras o menos de MIN_TOLERANT_SENTENCES oraciones)
    # no cuenta como duplicación. Evita falsos positivos cuando el modelo
    # reenvía solo un fragmento puente ("como vimos...", "pasando ahora a...").
    MIN_TOLERANT_WORDS = 30
    MIN_TOLERANT_SENTENCES = 3

    # Nota: no hacemos early-return aquí cuando `new_text` está contenido dentro de
    # `existing_text`: aunque sea señal de posible repetición literal, dejamos que
    # el análisis por párrafos/oraciones decida para no marcar falsos positivos en
    # textos cortos.

    # 1) Párrafos prácticamente idénticos.
    ex_paras = [_normalize_block(p) for p in _paragraphs(existing_text)]
    new_paras = [_normalize_block(p) for p in _paragraphs(new_text)]
    for np_ in new_paras:
        if len(np_.split()) < MIN_PARAGRAPH_WORDS:
            continue
        if np_ in ex_paras:
            sample = np_[:120]
            log(
                logger,
                logging.WARNING,
                f"CHAPTER_WRITER OVERLAP_MATCH type=paragraph words={len(np_.split())} sample={sample!r}",
            )
            return True

    # 2) Secuencia de oraciones consecutivas idénticas.
    ex_sentences = [_normalize_block(s) for s in _split_sentences(existing_text)]
    new_sentences = [_normalize_block(s) for s in _split_sentences(new_text)]
    if ex_sentences and new_sentences:
        ex_set = set(ex_sentences)
        run_len = 0
        run_words = 0
        run_samples: list[str] = []
        for s in new_sentences:
            if s in ex_set:
                run_len += 1
                run_words += len(s.split())
                run_samples.append(s)
                if run_len >= MIN_RUN_SENTENCES and run_words >= MIN_RUN_WORDS:
                    sample = " ".join(run_samples)[:120]
                    log(
                        logger,
                        logging.WARNING,
                        f"CHAPTER_WRITER OVERLAP_MATCH type=sentence_run words={run_words} sentences={run_len} sample={sample!r}",
                    )
                    return True
            else:
                run_len = 0
                run_words = 0
                run_samples = []
    return False


def _get_section_word_counts(md: str, validated: dict) -> dict:
    """Cuenta las palabras del contenido de cada seccion `##` del outline.

    Solo lectura: no modifica ``md``. Excluye el propio heading y los headings de
    subsecciones ``###`` (su contenido cuenta como parte de la seccion padre).
    Ignora las secciones de cierre no editoriales (``## Fuentes utilizadas``,
    ``## Sources used``, etc.).
    Devuelve un diccionario {heading_original: word_count}.
    """
    outline = validated.get("chapter_outline") or {}
    sections = [s.get("heading", "") for s in (outline.get("sections") or []) if s.get("heading")]
    norm_to_original: dict[str, str] = {}
    for h in sections:
        norm = _normalize_heading(h)
        if norm:
            norm_to_original.setdefault(norm, h)

    counts: dict[str, int] = {}
    current_norm: str | None = None
    buffer: list[str] = []
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_norm is not None:
                counts[current_norm] = len(" ".join(buffer).split())
            current_norm = _normalize_heading(stripped[3:].strip())
            buffer = []
        elif stripped.startswith("### "):
            continue
        else:
            buffer.append(line)
    if current_norm is not None:
        counts[current_norm] = len(" ".join(buffer).split())

    result: dict[str, int] = {}
    for h in sections:
        norm = _normalize_heading(h)
        if not norm or norm in _BONUS_SECTION_HEADINGS:
            continue
        result[h] = counts.get(norm, 0)
    return result


def _choose_target_section(word_counts: dict, section_target: int) -> str | None:
    """Selecciona deterministicamente la seccion editorial mas corta a desarrollar.

    No prioriza ``Conclusion`` mientras existan otras secciones no-conclusion por
    desarrollar (o que sigan siendo las mas cortas). ``Fuentes utilizadas`` no
    entra en ``word_counts``.
    """
    if not word_counts:
        return None

    def _is_conclusion(h: str) -> bool:
        return _normalize_heading(h) == "conclusion"

    below = [h for h, w in word_counts.items() if not _is_conclusion(h) and w < section_target]
    if below:
        return min(below, key=lambda h: word_counts[h])
    non_conclusion = [h for h in word_counts if not _is_conclusion(h)]
    if non_conclusion:
        return min(non_conclusion, key=lambda h: word_counts[h])
    return min(word_counts, key=lambda h: word_counts[h])


def _get_section_text(md: str, section_heading: str) -> str:
    """Devuelve el texto actual (sin el heading) de ``## section_heading``."""
    target = f"## {section_heading}"
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == target.strip():
            start = i
            break
    if start is None:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("## "):
            end = j
            break
    return "\n".join(lines[start + 1:end]).strip()


def _insert_into_section(md: str, section_heading: str, new_text: str) -> str:
    """Inserta ``new_text`` al final del contenido de una secci\u00f3n ``## `` existente.

    La inserci\u00f3n se anexa al final del contenido actual de la secci\u00f3n
    (antes del siguiente heading ``##``), preservando intacto el heading objetivo,
    el contenido que ya exist\u00eda en la secci\u00f3n y el resto del cap\u00edtulo
    (secciones siguientes incluidas). Si la secci\u00f3n no existe en ``md``,
    devuelve ``md`` sin modificar.
    """
    if not new_text or not new_text.strip():
        return md
    target = f"## {section_heading}"
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == target.strip():
            start = i
            break
    # Change A: si no hay heading exacto, anclar al heading que coincida bajo
    # normalización (el LLM puede variar acentos, mayúsculas o puntuación
    # respecto del outline). Preserva el heading original del markdown.
    if start is None:
        norm_target = _normalize_heading(section_heading)
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("## ") and not stripped.startswith("### "):
                if _normalize_heading(stripped[3:].strip()) == norm_target:
                    start = i
                    break
    if start is None:
        return md
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        # El siguiente heading de nivel 2 (##) cierra la sección. Los ###
        # pertenecen a la sección actual y no la cierran.
        if stripped.startswith("##") and not stripped.startswith("###"):
            end = j
            break
    # Se preserva el contenido existente de la sección tal cual, incluyendo
    # trailing space significativo (p. ej. "word " repetido), eliminando sólo
    # los espacios en blanco iniciales (lstrip). new_text se conserva igual para
    # preservar su trailing space en los asserts de los tests.
    existing = "\n".join(lines[start + 1:end])
    if existing.strip():
        combined = existing.lstrip() + "\n\n" + new_text
    else:
        combined = new_text
    return "\n".join(lines[:start + 1]) + "\n" + combined + "\n" + "\n".join(lines[end:])


def _strip_markdown_headings(text: str) -> str:
    """Elimina las líneas de heading Markdown (## / ###) del texto.

    Sirve para limpiar respuestas del modelo que, a pesar de la instrucción del
    prompt, reenvían el esquema del outline; el texto restante se inserta en la
    sección objetivo sin crear headings duplicados.
    """
    return "\n".join(
        line for line in text.splitlines()
        if not (line.strip().startswith("## ") or line.strip().startswith("### "))
    )


def _contains_markdown_heading(text: str) -> bool:
    """True si ``text`` contiene cualquier heading Markdown ``##``/``###``."""
    return any(
        t.strip().startswith("## ") or t.strip().startswith("### ")
        for t in text.splitlines()
    )


def _build_section_continuation_prompt(
    target_section: str,
    current_section_words: int,
    target_continuation_words: int,
    section_text: str,
    research: str,
    language: str,
    existing_chapter: str = "",
) -> str:
    """Construye un prompt de continuacion dirigido a una seccion concreta.

    ``existing_chapter`` es el contenido completo del capítulo ya redactado; se
    incluye como contexto prohibido (lo que no debe repetirse) para evitar que el
    modelo re-escriba el capítulo o la sección objetivo.
    """
    research = (research or "").strip()
    if language and str(language).lower().startswith("en"):
        return (
            f"Generate ONLY new content for the existing section:\n\n"
            f"Generate ONLY new content for the existing section: '{target_section}' (write only plain paragraphs, NO markdown headings, NO '##', NO '###').\n\n"
            f"The section already contains approximately {current_section_words} words.\n"
            f"Your task is to add approximately {target_continuation_words} NEW words to this section.\n\n"
            f"STRICT ANTI-REPETITION RULES:\n"
            f"- DO NOT reproduce ANY literal fragment of the current section content.\n"
            f"- DO NOT repeat facts, examples, dates, explanations, arguments or ideas already present in this section.\n"
            f"- DO NOT repeat ANY paragraph, sentence or idea that appears in other sections of the chapter. The full chapter already exists; your contribution must be 100% new.\n"
            f"- DO NOT write the section heading again.\n"
            f"- DO NOT write any other `##` heading.\n"
            f"- DO NOT write `###` headings.\n"
            f"- DO NOT write an introduction or a conclusion.\n"
            f"- DO NOT write Sources.\n"
            f"- DO NOT discuss another outline section.\n"
            f"- DO NOT summarize the existing text.\n"
            f"- Do not introduce concepts that belong to the conclusion; the conclusion must synthesize already developed content.\n"
            f"- Do not add artificial lists merely to increase length.\n"
            f"- The outline is a closed, mandatory structure; do not create new main sections.\n\n"
            f"Existing section content:\n\n{section_text or '(empty)'}\n\n"
            f"FULL chapter already written (do NOT reproduce ANY of this):\n\n{existing_chapter or '(none)'}\n\n"
            f"Relevant background/research:\n\n{research or '(none)'}\n\n"
            f"Only output the NEW paragraphs that should be inserted into this existing section. If you repeat content from the chapter, your response will be rejected."
        )
    return (
        f"Genera SOLO contenido nuevo para la seccion existente:\n\n"
                                        f"Genera SOLO contenido nuevo para la seccion existente: '{target_section}' (escribe solo parrafos planos, NADA de encabezados Markdown, NADA de '##', NADA de '###').\n\n"
        f"EXPANDIENDO EXCLUSIVAMENTE '{target_section}': tu contribución trata ÚNICAMENTE su tema. No menciones ni desarrolles datos, fechas, ejemplos o explicaciones de otras secciones del outline (consulta el capítulo completo para saber qué NO repetir). Si no tienes contenido nuevo y fresco para '{target_section}', entrega texto vacío; no reescribas ni encabezes otra sección.\n\n"
        f"La seccion ya contiene aproximadamente {current_section_words} palabras.\n"
        f"Tu tarea es anadir aproximadamente {target_continuation_words} palabras NUEVAS a esta seccion.\n\n"
        f"REGLAS ESTRICTAS ANTIRREPETICION:\n"
        f"- NO reproduzcas NINGUN fragmento literal del contenido actual de esta seccion.\n"
        f"- NO repitas datos, ejemplos, fechas, explicaciones, argumentos o ideas ya presentes en esta seccion.\n"
        f"- NO repitas NINGUN párrafo, frase o idea que aparezca en otras secciones del capítulo. El capítulo completo ya existe; tu contribución debe ser 100% nueva.\n"
        f"- NO escribas de nuevo el encabezado de la seccion.\n"
        f"- NO escribas ningun otro encabezado `##`.\n"
        f"- NO escribas encabezados `###`.\n"
        f"- NO escribas una introduccion ni una conclusion.\n"
        f"- NO escribas Fuentes.\n"
        f"- NO trates otra seccion del outline.\n"
        f"- NO resumas el texto existente.\n"
        f"- No introduzcas conceptos propios de la conclusion; la conclusion debe sintetizar solo lo ya desarrollado.\n"
        f"- No anadas listas artificiales solo para aumentar la longitud.\n"
        f"- El outline es una estructura cerrada y obligatoria; no crees nuevas secciones principales.\n\n"
        f"\n"
        f"- Si no puedes generar contenido nuevo para esta sección, entrega texto vacío en lugar de repetir.\n"
        f"- Incluso una frase corta reutilizada del capítulo existente provocará el rechazo de la respuesta.\n"
        f"- Tu contribución debe ampliar la sección con información NUEVA, no repetir lo ya escrito.\n"
        f"REGLAS ADICIONALES DE PROGRESIÓN:\n"
        f"Contenido actual de la seccion:\n\n{section_text or '(vacio)'}\n\n"
        f"CAPITULO COMPLETO YA REDACTADO (NO reproduzcas NADA de esto):\n\n{existing_chapter or '(ninguno)'}\n\n"
        f"Contexto/investigacion relevante:\n\n{research or '(ninguno)'}\n\n"
        f"Entrega SOLO los parrafos nuevos que deben insertarse en esta seccion existente. Si repites contenido del capítulo, tu respuesta sera rechazada."
    )
def _plan_continuation_deficit(
    current_words: int,
    minimum_words: int,
    target_word_count: int | None = None,
) -> dict[str, int]:
    """Calcula el presupuesto determinista de continuaciones a partir del déficit.

    El nº de continuaciones obligatorias (``min_budget``) deriva del déficit real
    respecto a ``minimum_words`` dividido por el rendimiento esperado por llamada
    (``AVG_WORDS_PER_CONTINUATION``), acotado por un HARD LIMIT absoluto. El
    presupuesto best-effort (``target_budget``) intenta aproximarse a
    ``target_word_count`` solo con lo que quede del HARD LIMIT, evitando que el
    target se convierta en una obligación infinita.

    El LLM jamás decide cuántas veces continuar: esto es puro cálculo Python.
    """
    minimum_words = int(minimum_words or 0)
    target = int(target_word_count or 0)
    current = int(current_words or 0)

    deficit = max(minimum_words - current, 0)
    min_attempts = 0
    if deficit > 0:
        min_attempts = max(1, math.ceil(deficit / AVG_WORDS_PER_CONTINUATION))
    min_budget = min(min_attempts, ABSOLUTE_HARD_LIMIT)

    target_budget = 0
    if target > minimum_words and current < target:
        target_deficit = target - current
        target_attempts = max(0, math.ceil(target_deficit / AVG_WORDS_PER_CONTINUATION))
        remaining_hard = max(0, ABSOLUTE_HARD_LIMIT - min_budget)
        target_budget = min(target_attempts, remaining_hard)

    return {
        "deficit": deficit,
        "min_budget": min_budget,
        "target_budget": target_budget,
        "hard_limit": ABSOLUTE_HARD_LIMIT,
    }


def _continuation_request_words(goal: int, current_words: int) -> int:
    """Tamaño de la petición de palabras nuevas derivado del déficit pendiente."""
    pending = max(int(goal) - int(current_words), 0)
    if pending <= 0:
        return 0
    return max(
        MIN_CONTINUATION_REQUEST,
        min(MAX_CONTINUATION_REQUEST, round(pending * CONTINUATION_REQUEST_FACTOR)),
    )


def _validate_quality(md: str, validated: dict) -> dict:
    """Valida la calidad del capítulo generado."""
    errors: list[str] = []
    words = len(md.split())
    minimum = _required_min_words(validated)

    if not md or not md.strip():
        errors.append("capítulo vacío")
    else:
        if _detect_placeholder(md):
            errors.append("contiene texto placeholder")
        if words < minimum:
            errors.append(f"menos de {minimum} palabras ({words})")

        outline = validated.get("chapter_outline") or {}
        sections = outline.get("sections") or []
        required_headings = [s.get("heading", "") for s in sections if s.get("heading")]
        for heading in required_headings:
            if heading and heading.strip() not in md:
                errors.append(f"falta la sección del outline: {heading}")

    return {
        "quality_gate": "FAIL" if errors else "PASS",
        "quality_errors": errors,
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


def _build_prompt(validated: dict | ChapterWritePayload, language: str = "es") -> str:
    """Construye un prompt editorial para redactar el capítulo."""
    data = validated.model_dump() if not isinstance(validated, dict) else validated
    outline = data.get("chapter_outline") or {}
    sections = outline.get("sections", [])
    sections_text = "\n".join(
        f"- {s.get('heading', '')}: {s.get('objective', '')}" for s in sections
    )
    sources_text = "\n".join(
        f"- {s.get('title') or s.get('url')} ({s.get('source_type') or 'web'})"
        for s in (data.get("sources") or [])[:20]
    )
    previous_text = "\n".join(
        f"- {summary}" for summary in (data.get("previous_chapter_summaries") or [])[:10]
    )

    book_meta = data.get("book_metadata") or {}
    style = data.get("style_guide") or "neutral, clear and fluid"
    research = data.get("research") or "None"
    target_word_count = data.get("target_word_count", 3000)

    if language == "en":
        writer_line = (
            "You are a senior editorial writer. Write a complete, "
            "publish-ready chapter in natural, professional English."
        )
        rules = [
            "Write as if the text was originally authored in English. Avoid literal translation patterns from Spanish.",
            "Preserve all provided facts, structure, references, citations, meaning, tone, and approximate length.",
            "Do not invent information, statistics, quotes, sources, or people.",
            "Only state facts supported by the provided research or listed sources.",
            "When evidence is mixed or incomplete, acknowledge uncertainty explicitly instead of fabricating certainty.",
            "Follow the chapter outline structure exactly and avoid thematic drift.",
            "Adapt idioms and culturally specific expressions into natural English equivalents when needed.",
            "Avoid repetition, redundancy, and awkward phrasing.",
            "Treat each outline section as a self-contained unit with its own content; do not repeat information already explained in previous sections.",
            "If a concept was already explained, a later section may only reference it briefly to connect ideas, never re-define or re-explain it.",
            "Do not repeat examples, lists, arguments, or complete explanations across sections.",
            "Do not turn the conclusion into a detailed summary of all sections.",
            "The conclusion must not reproduce the body of the chapter. It should synthesize the central thesis in a concise closing perspective and may mention previously discussed concepts only briefly. Do not introduce a second detailed treatment of topics already covered.",
            "Especially avoid repeating: definitions, chronologies, examples, technology lists, causes and consequences, technical explanations, recommendations, and conclusions already developed earlier.",
            "Write an engaging introduction and a strong, conclusive ending.",
            "Use natural transitions between sections.",
            "Keep the tone consistent with the book metadata and style guide.",
            "Do not mention AI, LLMs, drafts, placeholders, or internal editorial process.",
            "If research is insufficient, state it explicitly and proceed only with supported content.",
            "Return Markdown with clear headings and sections.",
            "Include a '## Sources used' section at the end, listing only valid provided URLs.",
            f"Generate a complete chapter of approximately {target_word_count} words. Operational minimum: 1800 words.",
            "Develop ALL sections of the outline in detail without summarizing or truncating.",
            "Do not end prematurely; extend as needed to reach the target length.",
            "Do not use phrases like 'etc.', 'and so on', or leave sections incomplete.",
            "Do not substitute sections with brief instructions, outlines, or bullet lists instead of prose.",
            "Deliver only the final chapter in Markdown, without metadata or comments.",
            "The provided outline is a CLOSED and MANDATORY structure.",
            "Do not create new main sections `##` that are not present in the outline.",
            "Do not turn secondary topics into new main sections; integrate them inside the matching outline section.",
            "Respect exactly the order of the outline sections.",
            "Do not add any section after `Conclusion`, and do not add editorial content after `Sources used`.",
            "The `Conclusion` section must appear exactly once and be the last editorial section of the chapter.",
            "The conclusion must synthesize only the points already developed; do not introduce new concepts, examples, technologies, dates, or arguments in the conclusion.",
            "Do not repeat complete paragraphs from earlier sections.",
            "Subsections `###` are allowed only when truly necessary and must belong to an existing `##` outline section; do not create chains of subsections to extend length.",
            "Prioritize precision, coverage, and coherence over length. Do not repeat information to increase the word count.",
            "Do not add lists of technologies, companies, platforms, protocols, or examples merely to increase length, and do not invent information to reach the word objective.",
            "If the pipeline already adds the sources section, do not generate a second '## Sources used' section.",
            "Format: exactly one occurrence of each main outline section, exactly one `## Conclusion`, no editorial `##` after `Conclusion` or `Sources used`, and no invented main heading.",
            "Before writing each section, consider what has already been covered; do not re-explain an idea with the same level of detail in several sections; each section must add new information; the conclusion must synthesize, not re-develop.",
        ]
    else:
        writer_line = (
            "Eres un redactor editorial profesional en español. Redacta un capítulo completo "
            "y listo para publicar."
        )
        rules = [
            "Escribe en español natural y editorial, sin traducciones literales.",
            "No inventes información, estadísticas, citas ni fuentes.",
            "Solo afirma hechos respaldados por la investigación o fuentes listadas.",
            "Distingue claramente hechos de inferencias cuando sea relevante.",
            "Respeta EXACTAMENTE el outline proporcionado; no inventes secciones adicionales ni desvíos temáticos.",
            "Cada sección debe aportar información nueva; si un concepto ya se explicó antes, solo haz una referencia breve, no lo vuelvas a definir desde cero.",
            "Prohibido repetir párrafos completos o bloques de texto con cambios mínimos de palabras.",
            "Prohibido utilizar estructuras de plantilla repetitivas del tipo: 'Fue lanzado por... se ha convertido en uno de los más populares... Su capacidad para... lo ha hecho muy popular entre los usuarios.'",
            "Prohibido rellenar longitud con listas enumeradas de productos, servicios o tecnologías usando la misma estructura.",
            "Evita frases comodín como: 'Como hemos visto', 'Es importante destacar', 'En este sentido', 'A medida que... crecía y se expandía, se dieron cuenta de...', 'Los ingenieros necesitaban...'.",
            "No repitas literalmente fechas, nombres o acontecimientos sin aportar contexto nuevo.",
            "No repitas información ya explicada en secciones anteriores; cada sección debe ser una unidad con contenido propio.",
            "Si un concepto ya fue explicado, en una sección posterior solo puede mencionarse de forma breve cuando sea necesario para conectar ideas, nunca volver a definirlo o explicarlo en profundidad.",
            "No repitas ejemplos, listas, argumentos ni explicaciones completas.",
            "No conviertas la conclusión en un resumen detallado de todas las secciones del capítulo.",
            "Evita especialmente repetir: definiciones, cronologías, ejemplos, listas de tecnologías, causas y consecuencias, explicaciones técnicas, recomendaciones y contenidos ya desarrollados previamente.",
            "Prohibido reexplicar en la conclusión los mismos temas desarrollados en el cuerpo (como el futuro de Internet, la IA, la realidad virtual/aumentada o el acceso a Internet); la conclusión solo puede mencionarlos de forma breve para aportar cierre.",
            "Crea una introducción atractiva y una conclusión que cierre el capítulo sin repetir la introducción ni recitar el índice.",
            "La conclusión debe cerrar el capítulo de forma sintética. No debe repetir detalladamente las secciones anteriores ni crear nuevas secciones. No debe volver a desarrollar conceptos ya explicados.",
            "La conclusión debe sintetizar la tesis central con una perspectiva de cierre breve, no reexplicar el cuerpo ni introducir un segundo desarrollo detallado de temas ya cubiertos.",
            "Genera transiciones naturales entre secciones.",
            "Mantén el tono coherente con el libro y la guía de estilo.",
            "No menciones que eres una IA ni incluyas metacomentarios.",
            "No hagas referencia a borradores, drafts o proceso interno.",
            "Si la investigación es insuficiente, indícalo explícitamente.",
            "Devuelve el resultado en formato Markdown con título y secciones claras.",
            "Incluye al final una sección '## Fuentes utilizadas' con cada fuente en formato [Nombre (web_wikipedia)] usando siempre paréntesis con el tipo de fuente. Nunca uses corchetes sin paréntesis de tipo, p. ej. [RFC 826] o [Implementaciones de TCP/IP] son placeholders y deben evitarse; usa siempre [Nombre (web_wikipedia)].",
            f"Genera un capítulo completo de aproximadamente {target_word_count} palabras. Objetivo operativo mínimo: 1800 palabras.",
            "Desarrolla TODAS las secciones del outline con detalle, sin resumir.",
            "No termines prematuramente; extiéndete lo necesario para alcanzar la longitud objetivo.",
            "No uses frases como 'etc.', 'y así sucesivamente' ni dejes secciones incompletas.",
            "No sustituyas secciones por indicaciones, esquemas o listas breves en lugar de prosa.",
            "Entrega únicamente el capítulo final en Markdown, sin metadatos ni comentarios.",
            "Evita mezclar inglés innecesario en texto en español; usa términos naturalmente aceptados en español.",
            "El outline proporcionado es una estructura CERRADA y OBLIGATORIA.",
            "El título del capítulo es un heading H1 (`# Título`). No lo escribas como `##`.",
            "Todas las secciones del outline son headings H2 (`## Sección`). No las escribas como `###`.",
            "No inventes un heading `##` con el título del capítulo; el título va como H1.",
            "No crees nuevas secciones principales `##` que no aparezcan en el outline.",
            "No conviertas temas secundarios en nuevas secciones principales; intégralos dentro de la sección del outline que corresponda.",
            "Respeta exactamente el orden de las secciones del outline.",
            "No añadas ninguna sección después de `Conclusión` ni contenido editorial después de `Fuentes utilizadas`.",
            "La sección `Conclusión` debe aparecer una sola vez y ser la última sección editorial del capítulo.",
            "La conclusión debe sintetizar únicamente los puntos ya desarrollados; no introduzcas en ella conceptos, ejemplos, tecnologías, fechas o argumentos nuevos.",
            "No repitas párrafos completos de secciones anteriores.",
            "Las subsecciones `###` solo se permiten cuando son necesarias y deben pertenecer a una sección `##` del outline; no crees cadenas de subsecciones para alargar.",
            "Prioriza precisión, cobertura y coherencia sobre longitud. No repitas información para aumentar el número de palabras.",
            "No añadas listas de tecnologías, empresas, plataformas, protocolos o ejemplos únicamente para aumentar longitud, ni inventes información para alcanzar el objetivo de palabras.",
            "Si el pipeline ya añade la sección de fuentes, no generes una segunda sección `## Fuentes utilizadas`.",
            "Formato: una única aparición de cada sección principal del outline, exactamente una `## Conclusión`, ninguna sección editorial `##` después de `Conclusión` o `Fuentes utilizadas`, y ningún heading principal inventado.",
            "Antes de escribir cada sección, considera qué información ya se ha cubierto; no vuelvas a explicar una idea con el mismo nivel de detalle en varias secciones; cada sección debe aportar información nueva; la conclusión debe sintetizar, no volver a desarrollar.",
        ]

    rules_text = "\n".join(f"- {rule}" for rule in rules)
    return (
        f"{writer_line}\n\n"
        f"Book metadata: {json.dumps(book_meta, ensure_ascii=False)}\n\n"
        f"Chapter outline:\n{json.dumps(outline, ensure_ascii=False)}\n\n"
        f"Target sections:\n{sections_text}\n\n"
        f"Available research:\n{research}\n\n"
        f"Allowed sources:\n{sources_text or 'None'}\n\n"
        f"Previous chapter summaries (for continuity):\n{previous_text or 'None'}\n\n"
        f"Target word count: {target_word_count}\n"
        "El objetivo de longitud es un REQUISITO, no una sugerencia. Desarrolla el capítulo hasta aproximadamente "
        f"{target_word_count} palabras y NO des por terminada la respuesta solo porque todas las secciones ya tienen algo de contenido.\n"
        "Cada sección principal debe recibir un desarrollo sustancial y original de contenido antes de finalizar el capítulo; "
        "no termines cuando las secciones estén solo superficialmente cubiertas.\n"
        "Las reglas antirrepetición prohíben la repetición literal o casi literal; NO autorizan a reducir el capítulo a unas pocas "
        "frases por sección para acortarlo.\n"
        "No añadas relleno, repeticiones ni listas artificiales para inflar la longitud; aporta contenido original y sustantivo.\n"
        "Alcanza, siempre que sea posible, al menos el mínimo operativo de "
        f"{_required_min_words(validated)} palabras y continúa desarrollando contenido original hasta aproximarte al objetivo de "
        f"{target_word_count} palabras.\n"
        "Devuelve únicamente el capítulo Markdown final. No expliques que no puedes alcanzar la longitud y no devuelvas un resumen.\n"
        f"Style guide: {style}\n\n"
        f"STRICT RULES:\n{rules_text}\n\n"
        "Return ONLY the chapter Markdown, no extra text."
    )

# ---------------------------------------------------------------------------
# GENERACIÓN DETERMINISTA DE RESPALDO (FASE 7.9D.7)
#
# El pipeline no depende del comportamiento del LLM: si el capítulo no alcanza
# el mínimo operativo tras la generación LLM + continuaciones, un motor
# determinista amplía las secciones existentes usando el outline y el research,
# garantizando el mínimo de palabras sin placeholders ni continuaciones
# duplicadas. El LLM queda como generador opcional / mejorador, nunca como
# autoridad de finalización.
# ---------------------------------------------------------------------------

# Conectores de apertura para párrafos deterministas de desarrollo.
_DET_OPENERS_ES = [
    "En este punto, cabe precisar que",
    "Conviene destacar aquí que",
    "Resulta relevante comprender que",
    "Un elemento fundamental es que",
    "Hay que tener presente que",
    "Desarrollando esta idea, cabe señalar que",
    "A modo de aclaración, es útil recordar que",
    "Teniendo en cuenta lo anterior, se añade que",
    "Dentro de este marco, conviene subrayar que",
    "En relación con lo expuesto, cabe añadir que",
    "Siguiendo con este hilo, resulta pertinente notar que",
    "Completa el cuadro anterior el hecho de que",
]
_DET_OPENERS_EN = [
    "At this point, it is worth clarifying that",
    "It should be highlighted here that",
    "It is relevant to understand that",
    "A fundamental element is that",
    "It must be kept in mind that",
    "Developing this idea, it should be noted that",
    "By way of clarification, it is useful to recall that",
    "Taking the above into account, it follows that",
    "Within this framework, it is worth stressing that",
    "In connection with the above, it may be added that",
    "Following this thread, it is pertinent to observe that",
    "Completing the previous picture is the fact that",
]

_DET_CLOSERS_ES = [
    "Así, este aspecto refuerza la argumentación general del capítulo.",
    "En suma, esto contribuye a contextualizar el tema tratado.",
    "Por tanto, se trata de un matiz que enriquece el desarrollo.",
    "De este modo, la exposición gana en profundidad y matices.",
    "Se trata, en definitiva, de una consideración relevante para el conjunto.",
    "Este detalle completa la perspectiva general del apartado.",
    "Con esto queda ilustrada la dimensión práctica del punto tratado.",
]
_DET_CLOSERS_EN = [
    "Thus, this aspect reinforces the general argument of the chapter.",
    "In sum, this contributes to contextualising the topic discussed.",
    "Therefore, this is a nuance that enriches the development.",
    "In this way, the exposition gains depth and nuance.",
    "Ultimately, this is a relevant consideration for the whole.",
    "This detail rounds out the overall perspective of the section.",
    "With this, the practical dimension of the point is illustrated.",
]

# Puentes para párrafos que combinan DOS hechos (re-fix regresión book_43:
# amplía el espacio combinatorio de forma no lineal cuando el pool tiene >=2
# hechos; con 1 solo hecho el backstop cae al comportamiento de un hecho).
_DET_BRIDGES_ES = [
    "Asimismo,",
    "En esta misma línea,",
    "Por añadidura,",
    "Al mismo tiempo,",
    "Como complemento de lo anterior,",
]
_DET_BRIDGES_EN = [
    "Likewise,",
    "Along the same lines,",
    "Furthermore,",
    "At the same time,",
    "As a complement to the above,",
]


def _extract_research_facts(
    research: str | None, sources: list | None, max_facts: int = 24
) -> list[str]:
    """Extrae hechos únicos (oraciones) del research y las fuentes.

    Determinista, sin modelos. Cada hecho se usa una sola vez por pasada para
    evitar duplicación literal dentro del backstop. Prioriza el research y,
    si está vacío, las fuentes.
    """
    facts: list[str] = []
    text = research or ""
    for line in text.splitlines():
        seg = line.strip()
        if not seg:
            continue
        if seg.startswith("-"):
            seg = seg[1:].strip()
        # Quita un prefijo "Título: ..." conservando el contenido real.
        seg = re.sub(r"^[^:]{1,80}:\s*", "", seg)
        seg = seg.strip(" -").strip()
        if seg and len(seg.split()) >= 6 and seg not in facts:
            facts.append(seg)
        if len(facts) >= max_facts:
            break
    if not facts:
        for s in (sources or []):
            c = (s.get("content") or s.get("title") or "").strip()
            if c and len(c.split()) >= 6 and c not in facts:
                facts.append(c)
            if len(facts) >= max_facts:
                break
    return facts


def _elaborate_fact_deterministic(fact: str, language: str, seed: int) -> str:
    """Redacta de forma determinista una oración de desarrollo a partir de un hecho."""
    es = language != "en"
    openers = _DET_OPENERS_ES if es else _DET_OPENERS_EN
    closers = _DET_CLOSERS_ES if es else _DET_CLOSERS_EN
    op = openers[seed % len(openers)]
    cl = closers[(seed // 3) % len(closers)]
    fact = fact.rstrip(" .").strip()
    return f"{op} {fact}. {cl}"


def _elaborate_fact_pair_deterministic(
    fact_a: str, fact_b: str, language: str, seed: int
) -> str:
    """Redacta un párrafo determinista que COMBINA dos hechos (re-fix book_43).

    Multiplica el espacio combinatorio del backstop cuando el pool tiene >=2
    hechos: opener (12) x puente (5) x closer (7) x par ordenado (N*(N-1)).
    Determinista: mismo seed → mismo párrafo.
    """
    es = language != "en"
    openers = _DET_OPENERS_ES if es else _DET_OPENERS_EN
    closers = _DET_CLOSERS_ES if es else _DET_CLOSERS_EN
    bridges = _DET_BRIDGES_ES if es else _DET_BRIDGES_EN

    s = seed
    op = openers[s % len(openers)]
    s //= len(openers)
    br = bridges[s % len(bridges)]
    s //= len(bridges)
    cl = closers[s % len(closers)]

    fa = fact_a.rstrip(" .").strip()
    fb = fact_b.rstrip(" .").strip()
    return f"{op} {fa}. {br} {fb}. {cl}"


def _deterministic_section_paragraphs(
    section: str,
    objective: str,
    facts: list[str],
    language: str,
    target_words: int,
    seed: int,
) -> str:
    """Genera párrafos deterministas para una sección hasta alcanzar ``target_words``.

    No produce headings (`#`/`##`/`###`), no introduce placeholders y varía la
    redacción por ``seed`` para evitar repetición literal entre párrafos.
    """
    es = language != "en"
    paragraphs: list[str] = []
    if es:
        paragraphs.append(
            f"En este apartado, dedicado a «{section}», se desarrolla el siguiente eje: {objective}. "
            "A continuación se exponen las consideraciones más relevantes sobre este punto."
        )
    else:
        paragraphs.append(
            f"This section, devoted to «{section}», develops the following axis: {objective}. "
            "Below are the most relevant considerations on this point."
        )
    anchor = objective or section
    i = 0
    wc = sum(len(p.split()) for p in paragraphs)
    while wc < target_words and i < ABSOLUTE_HARD_LIMIT * 6:
        if len(facts) >= 2:
            # Re-fix book_43: con pool >= 2 hechos se combinan PARES ordenados
            # (multiplica el espacio combinatorio; con 1 hecho, ruta simple).
            n = len(facts)
            ia = (seed + i) % n
            ib = (ia + 1 + ((seed + i) // n) % (n - 1)) % n
            paras = _elaborate_fact_pair_deterministic(
                facts[ia], facts[ib], language, seed + i
            )
        elif facts:
            fact = facts[(seed + i) % len(facts)]
            paras = _elaborate_fact_deterministic(fact, language, seed + i)
        else:
            paras = _elaborate_fact_deterministic(anchor, language, seed + i)
        paragraphs.append(paras)
        wc += len(paras.split())
        i += 1
    return "\n\n".join(paragraphs)


def _deterministic_complete(
    md: str,
    words: int,
    minimum_words: int,
    validated: dict,
    language: str,
    facts: list[str] | None = None,
) -> tuple[str, int]:
    """Garantiza el mínimo de palabras ampliando deterministamente las secciones.

    Trabaja sobre las secciones ya existentes (el LLM o ``_fallback_chapter``
    crean los headings); el backstop solo añade párrafos planos, evitando
    duplicación fuerte con el contenido actual. Es 100% Python, sin dependencia
    del comportamiento del modelo.
    """
    if words >= minimum_words:
        return md, words
    if facts is None:
        facts = _extract_research_facts(validated.get("research"), validated.get("sources"))
    outline = validated.get("chapter_outline") or {}
    sections = outline.get("sections") or []
    objective_by_heading = {
        (s.get("heading") or "").strip(): (s.get("objective") or "")
        for s in sections
    }
    body_sections = [s.get("heading", "").strip() for s in sections if s.get("heading")]
    if not body_sections:
        return md, words

    current = md
    wc = words
    # Seed por capítulo (fix §17 #7): mismo book_id + mismo chapter_number +
    # mismos hechos → mismo resultado, pero capítulos distintos ya no recorren
    # el pool compartido de hechos desde el mismo punto (evita párrafos
    # literales idénticos entre capítulos del mismo libro, caso book_37).
    # Re-fix (regresión book_43): el offset +1 era insuficiente — con el
    # incremento +1 por iteración los rangos de seed de capítulos distintos
    # se solapaban casi por completo y, con pool de hechos pobre, el mismo
    # seed_total producía párrafos verbatim idénticos. Factor 1000 garantiza
    # rangos disjuntos (ABSOLUTE_HARD_LIMIT=8 continuaciones ⇒ decenas de
    # seeds usados por capítulo como máximo, muy lejos del siguiente bloque).
    seed = int(((validated.get("chapter_outline") or {}).get("number") or 1)) * 1000
    hard = 0
    attempted_no_progress: set[str] = set()
    while wc < minimum_words and hard < ABSOLUTE_HARD_LIMIT * 8:
        counts = _get_section_word_counts(current, validated)
        # Change B: excluir secciones ya intentadas sin progreso para probar
        # las siguientes antes de abandonar el bucle.
        available = {k: v for k, v in counts.items() if k not in attempted_no_progress}
        if not available:
            break
        target = max(250, round(minimum_words / max(len(available), 1)))
        section = _choose_target_section(available, target)
        if not section:
            section = next(
                (h for h in body_sections
                 if _normalize_heading(h) not in _CLOSING_SECTION_HEADINGS
                 and h not in attempted_no_progress),
                body_sections[0],
            )
        objective = objective_by_heading.get(section.strip(), "")
        new_text = _deterministic_section_paragraphs(
            section,
            objective,
            facts,
            language,
            target_words=max(
                MIN_CONTINUATION_REQUEST,
                min(MAX_CONTINUATION_REQUEST, (minimum_words - wc) * CONTINUATION_REQUEST_FACTOR),
            ),
            seed=seed,
        )
        # El backstep NO bloquea por overlap contra el md completo: los párrafos
        # planos comparten estructura abierta/cierre y unigram-overlap contra todo
        # el capítulo es indefundado. La no repetición literal se garantiza por la
        # rotación de hechos (por semilla) y por el límite duro ABSOLUTE_HARD_LIMIT*8.
        if not new_text:
            seed += 1
            hard += 1
            continue
        inserted = _insert_into_section(current, section, new_text)
        new_wc = len(inserted.split())
        if new_wc <= wc:
            # Change B: no abortar al primer new_wc <= wc; registrar la sección
            # como sin progreso y probar las siguientes antes de abandonar.
            attempted_no_progress.add(section)
            seed += 1
            hard += 1
            continue
        current = inserted
        wc = new_wc
        seed += 1
        hard += 1
    return current, wc


def _fallback_chapter(validated: ChapterWritePayload, language: str = "es") -> dict[str, Any]:
    """Borrador editorial de respaldo (100% Python, sin LLM).

    Usa EXCLUSIVAMENTE las secciones del outline. Si el outline ya define
    Introducción/Conclusión como secciones, no se crean headings duplicados;
    en caso contrario, se aportan como cierre abierto. Garantiza un esqueleto
    válido que el backstop determinista ampliará hasta el mínimo operativo.
    """
    outline = validated.get("chapter_outline") or {}
    title = outline.get("title") or ("Untitled Chapter" if language == "en" else "Capítulo sin título")
    objective = outline.get("objective") or ""
    sections = outline.get("sections") or []
    if language == "en":
        intro_heading = "Introduction"
        concl_heading = "Conclusion"
        sources_heading = "## Sources used"
        no_sources = "(No sources provided)"
        intro = objective or "This chapter develops the topic proposed in the outline."
        conclusion = "The chapter synthesizes the key points covered and sets up the transition to the next chapter."
    else:
        intro_heading = "Introducción"
        concl_heading = "Conclusión"
        sources_heading = "## Fuentes utilizadas"
        no_sources = "(Sin fuentes proporcionadas)"
        intro = objective or "Este capítulo desarrolla el tema propuesto en el outline."
        conclusion = "El capítulo sintetiza los puntos abordados y prepara la transición hacia el siguiente."

    # Si el outline ya define Introducción/Conclusión como secciones, no se duplican
    # los headings (el outline real los incluye explícitamente).
    norm_headings = {(s.get("heading") or "").strip().lower() for s in sections}
    has_intro = intro_heading.lower() in norm_headings
    has_concl = concl_heading.lower() in norm_headings

    parts: list[str] = [f"# {title}", ""]
    if not has_intro:
        parts += [f"## {intro_heading}", intro, ""]
    for s in sections:
        heading = s.get("heading") or (intro_heading if not has_intro else "Sección")
        parts.append(f"## {heading}")
        parts.append(s.get("objective", "") or "")
    if not has_concl:
        parts += [f"## {concl_heading}", conclusion, ""]
    parts.append(sources_heading)
    md = "\n".join(parts) + "\n"
    if validated.get("sources"):
        md += "\n".join(f"- {s.get('url')}" for s in validated.get("sources") if s.get("url"))
    else:
        md += f"- {no_sources}"
    return {
        "chapter_md": md,
        "word_count": max(validated.get("target_word_count", 3000), 500),
        "sources_used": [s.get("url") for s in (validated.get("sources") or []) if s.get("url")],
    }



def _write_artifacts(book_id: str | None, chapter_number: int, md: str, meta: dict[str, Any]) -> str:
    """Guarda el draft del capítulo como artefacto independiente."""
    base_dir = os.path.join("data", "artifacts")
    if book_id:
        base_dir = os.path.join(base_dir, str(book_id))
    chapter_dir = os.path.join(base_dir, f"chapter_{chapter_number}")
    os.makedirs(chapter_dir, exist_ok=True)
    md_path = os.path.join(chapter_dir, "chapter.md")
    meta_path = os.path.join(chapter_dir, "metadata.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return md_path


def execute(payload: dict, capability: str = "write_chapter_es") -> dict:
    """Genera un capítulo completo a partir de outline, investigación y fuentes."""
    validated = validate_payload(capability, payload)
    language = "en" if capability == "write_chapter_en" else "es"
    md = ""
    execution_mode = "real"
    provider = None
    raw = None
    input_tokens = 0
    output_tokens = 0
    provider_name = "none"
    model_name = ""
    target_wc = validated.get("target_word_count", 3000)
    max_tokens = max(8000, int(target_wc * 3))
    minimum_words = _required_min_words(validated)

    _use_llm = os.environ.get("CHAP_USE_LLM", "1") == "1"
    _force_min = os.environ.get("CHAP_FORCE_MIN", "0") == "1"
    log(logger, logging.INFO, f"CHAPTER_WRITER execute DIAG CHAP_USE_LLM={_use_llm} CHAP_FORCE_MIN={_force_min}")

    try:
        # El proveedor LLM es opcional: si se deshabilita (CHAP_USE_LLM=0), el
        # pipeline completa el capítulo de forma determinista sin depender del
        # comportamiento del modelo.
        provider = get_provider() if _use_llm else None
        if provider is None:
            raise RuntimeError("[CHAP] Modo sin LLM habilitado (CHAP_USE_LLM=0)")
        # Acotar el horizonte de la llamada (local a esta instancia) para que un
        # LLM lento o bloqueado lance un timeout interno y active el fallback
        # antes de que el timeout del scheduler (180s) mate la tarea.
        # `registry.get()` devuelve una instancia nueva por llamada, por lo que
        # mutarla aquí no altera configuración global (mismo patrón que editor).
        provider.timeout = WRITER_PROVIDER_TIMEOUT
        provider.max_retries = WRITER_MAX_RETRIES
        # Marca el inicio de la fase LLM para el presupuesto de tiempo total.
        _llm_budget_start = time.perf_counter()
        provider_name = provider.name
        prompt = _build_prompt(validated, language=language)
        log(
            logger,
            logging.INFO,
            f"CHAPTER_WRITER generate START model={DEFAULT_ROUTER_MODEL} target={target_wc} max_tokens={max_tokens}",
        )
        _gen_start = time.perf_counter()
        result = provider.generate(
            prompt,
            system="Return ONLY the chapter Markdown, no extra text.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=max_tokens,
            temperature=0.3,
        )
        _gen_duration = time.perf_counter() - _gen_start
        raw = result.raw_response
        model_name = result.model
        input_tokens = result.input_tokens
        output_tokens = result.output_tokens
        md = result.text
        log(
            logger,
            logging.INFO,
            f"CHAPTER_WRITER generate END duration={_gen_duration:.2f}s chars={len(md)} words={len(md.split())} provider={provider_name}",
        )
        if not md:
            execution_mode = "fallback"
    except Exception as e:
        execution_mode = "fallback"
        provider_name = provider.name if provider else "none"
        log(
            logger,
            logging.WARNING,
            f"Fallo al generar capítulo con LLM ({provider_name}): {e}. Usando fallback.",
        )
        fallback = _fallback_chapter(validated, language=language)
        md = fallback["chapter_md"]

    if not md:
        execution_mode = "fallback"
        fallback = _fallback_chapter(validated, language=language)
        md = fallback["chapter_md"]

    # Canonicalización determinista de estructura (FASE 7.9D.7): si el LLM reformuló
    # un heading del outline (variante semántica, mayúsculas, acentos o puntuación),
    # Python lo resuelve y reescribe al heading canónico del outline. Preserva el
    # contenido; no acepta headings arbitrarios. Se aplica antes del conteo, del
    # bucle de continuación y del backstop para que todo el pipeline opere sobre
    # estructura canónica.
    md = _canonicalize_headings(md, validated)

    words = max(len(md.split()), 1)

    # Diagnóstico editorial anti-redundancia (no modifica el texto generado).
    unexpected_sections: list[str] = []
    duplicate_detected = False
    continuation_rejected_as_duplicate = False

    # ------------------------------------------------------------------
    # CONTROL DETERMINISTA (FASE 7.9D.7)
    # El programa manda, el LLM genera, Python valida y decide cuándo
    # continuar y cuándo terminar. No existe while True y el LLM jamás es
    # la autoridad de finalización.
    # ------------------------------------------------------------------
    initial_word_count = words
    total_continuation_attempts = 0
    successful_continuations = 0
    rejected_continuations = 0
    duplicate_rejections = 0
    repeated_continuation = False
    total_added_words = 0

    context = raw.get("context") if isinstance(raw, dict) else None

    # Presupuestos deterministas derivados del déficit real.
    _plan = _plan_continuation_deficit(words, minimum_words, target_wc)
    min_budget = int(_plan["min_budget"])
    target_budget = int(_plan["target_budget"])
    log(
        logger,
        logging.INFO,
        f"CHAPTER_WRITER continuation PLAN current_words={words} minimum_words={minimum_words} "
        f"target_wc={target_wc} deficit={_plan['deficit']} min_budget={min_budget} "
        f"target_budget={target_budget} hard_limit={ABSOLUTE_HARD_LIMIT}",
    )

    previous_proposal_norm = ""

    # FASE MÍNIMO (obligatoria) + FASE TARGET (best-effort acotada).
    _stop_phases = False
    _budget_exhausted = False
    for _phase_name, _goal, _phase_budget in (
        ("min", minimum_words, min_budget),
        ("target", target_wc, target_budget),
    ):
        if _phase_budget <= 0 or _goal <= 0:
            continue
        _phase_attempts = 0
        while (
            execution_mode == "real"
            and provider is not None
            and words < int(_goal)
            and _phase_attempts < int(_phase_budget)
            and total_continuation_attempts < ABSOLUTE_HARD_LIMIT
            and not _budget_exhausted
        ):
            # Si la fase LLM lleva demasiado, abortar las continuaciones y delegar
            # en el backstop determinista antes de que el scheduler (180s) lo mate.
            if _llm_budget_exhausted(_llm_budget_start):
                _budget_exhausted = True
                log(
                    logger,
                    logging.WARNING,
                    "CHAPTER_WRITER continuations budget exhausted "
                    f"at {time.perf_counter() - _llm_budget_start:.1f}s "
                    "-> switching to deterministic backstop",
                )
                break
            _step = _continuation_step(
                md,
                words,
                int(_goal),
                validated,
                provider,
                context,
                input_tokens,
                output_tokens,
                max_tokens,
                total_continuation_attempts,
                previous_proposal_norm,
            )
            total_continuation_attempts += 1
            _phase_attempts += 1
            md = _step["md"]
            words = _step["words"]
            previous_proposal_norm = _step["proposal_norm"]
            if _step["duplicate_detected"]:
                duplicate_detected = True
            if _step["continuation_rejected"]:
                continuation_rejected_as_duplicate = True
            _status = _step["status"]
            if _status == "inserted":
                # Tokens contabilizados SOLO cuando el contenido se inserta de
                # verdad (misma semántica que el pipeline previo: las llamadas
                # rechazadas no alteran el total reportado).
                input_tokens = _step["input_tokens"]
                output_tokens = _step["output_tokens"]
                successful_continuations += 1
                total_added_words += _step["added_words"]
            elif _status == "repeated":
                repeated_continuation = True
                rejected_continuations += 1
                duplicate_rejections += 1
            elif _status in ("rejected_duplicate", "rejected_full_chapter", "rejected_heading", "rejected_refusal"):
                rejected_continuations += 1
                duplicate_rejections += 1

            # Decisiones de control (100% Python):
            # - una propuesta duplicada NO es finalización: se descarta solo
            #   esa propuesta y, si queda presupuesto, se vuelve a solicitar.
            if _status in ("rejected_duplicate", "rejected_heading", "rejected_refusal"):
                continue
            # - repetición exacta, capítulo completo repetido, progreso nulo,
            #   error o ausencia de sección: terminación controlada de la fase.
            if _status in ("stop_insignificant", "rejected_full_chapter", "repeated", "error", "no_target"):
                _stop_phases = True
                break
            # - se alcanzó el objetivo de esta fase: detenerla.
            if words >= int(_goal):
                break
        if _stop_phases:
            break

    log(
        logger,
        logging.INFO,
        f"CHAPTER_WRITER continuation SUMMARY initial_word_count={initial_word_count} "
        f"final_word_count={words} total_attempts={total_continuation_attempts} "
        f"successful={successful_continuations} rejected={rejected_continuations} "
        f"duplicate_rejections={duplicate_rejections} repeated={repeated_continuation} "
        f"total_added_words={total_added_words}",
    )

    # Determinismo garantizado: si el pipeline está en modo sin LLM o bien se
    # solicita forzar el mínimo (CHAP_FORCE_MIN=1), el motor determinista amplía
    # las secciones existentes hasta alcanzar minimum_words SIN depender del LLM.
    deterministic_used = False
    if (not _use_llm) or (_force_min and words < minimum_words) or _budget_exhausted:
        _md_before_det = md
        md, words = _deterministic_complete(md, words, minimum_words, validated, language)
        deterministic_used = md != _md_before_det
        if deterministic_used:
            execution_mode = "deterministic"
            log(
                logger,
                logging.INFO,
                f"CHAPTER_WRITER deterministic backstop applied final_words={words}",
            )

    # Quality gate
    quality = _validate_quality(md, validated)
    quality_gate = quality["quality_gate"]
    quality_errors = quality["quality_errors"]

    # Diagnóstico anti-redundancia sobre el texto final (no modifica contenido).
    unexpected_sections = _detect_unexpected_sections(md, validated)
    duplicate_sections = _detect_duplicate_sections(md)
    subsections_map = _build_subsections_map(md)
    structural_anomalies = _detect_structural_anomalies(md)
    if unexpected_sections or duplicate_sections or structural_anomalies:
        log(
            logger,
            logging.INFO,
            f"CHAPTER_WRITER structural anomalies: "
            f"unexpected_sections={len(unexpected_sections)} "
            f"duplicate_sections={len(duplicate_sections)} "
            f"structural_anomalies={len(structural_anomalies)}",
        )
    if unexpected_sections:
        log(
            logger,
            logging.WARNING,
            f"CHAPTER_WRITER unexpected_sections: {unexpected_sections}",
        )
        quality_errors.append(
            f"secciones fuera del outline detectadas: {', '.join(unexpected_sections)}"
        )
    if duplicate_sections:
        log(
            logger,
            logging.WARNING,
            f"CHAPTER_WRITER duplicate_sections: {duplicate_sections}",
        )
    if structural_anomalies:
        log(
            logger,
            logging.WARNING,
            f"CHAPTER_WRITER structural_anomalies: {structural_anomalies}",
        )
    if duplicate_detected:
        quality_errors.append("duplicación potencial detectada en una continuación")
    if continuation_rejected_as_duplicate:
        quality_errors.append("una continuación fue rechazada por ser una repetición")

    # Research requirement: si el workflow exige investigación pero no hay fuentes
    # ni contenido investigado, el capítulo no puede considerarse completado.
    if validated.get("research_required", True):
        has_research = bool((validated.get("research") or "").strip()) or bool(validated.get("sources"))
        if not has_research:
            quality_gate = "FAIL"
            quality_errors.append("research_required=true pero no hay investigación ni fuentes")

    book_id = ((validated.get("book_metadata") or {}).get("book_id"))
    chapter_number = ((validated.get("chapter_outline") or {}).get("number") or 1)
    meta = {
        "book_metadata": validated.get("book_metadata"),
        "chapter_outline": validated.get("chapter_outline"),
        "research": validated.get("research"),
        "sources": validated.get("sources"),
        "previous_chapter_summaries": validated.get("previous_chapter_summaries"),
        "target_word_count": validated.get("target_word_count",3000),
        "style_guide": validated.get("style_guide"),
        "provider": provider_name,
        "model": model_name,
        "tokens_input": input_tokens,
        "tokens_output": output_tokens,
        "word_count": words,
        "text": md,
        "unexpected_sections": unexpected_sections,
        "duplicate_sections": duplicate_sections,
        "subsections_map": subsections_map,
        "structural_anomalies": structural_anomalies,
        "duplicate_detected": duplicate_detected,
        "continuation_rejected_as_duplicate": continuation_rejected_as_duplicate,
        "initial_word_count": initial_word_count,
        "final_word_count": words,
        "minimum_words": minimum_words,
        "target_word_count": target_wc,
        "total_continuation_attempts": total_continuation_attempts,
        "successful_continuations": successful_continuations,
        "rejected_continuations": rejected_continuations,
        "duplicate_rejections": duplicate_rejections,
        "repeated_continuation": repeated_continuation,
        "total_added_words": total_added_words,
        "final_quality_gate": quality_gate,
        "deterministic_used": deterministic_used,
    }
    md_path = _write_artifacts(book_id, chapter_number, md, meta)

    try:
        cost = float(
            calculate_cost(provider_name or (provider.name if provider else "ollama"), model_name or DEFAULT_MODEL, input_tokens, output_tokens)
            or 0.0
        )
    except Exception:
        cost = 0.0

    sources_used = [s.get("url") for s in (validated.get("sources") or []) if s.get("url")]

    log(
        logger,
        logging.INFO,
        f"Capítulo generado: {md_path} ({words} palabras)",
    )

    return {
        "chapter_md_path": md_path,
        "metadata": meta,
        "word_count": words,
        "sources_used": sources_used,
        "quality_gate": quality_gate,
        "quality_errors": quality_errors,
        "execution_mode": execution_mode,
        "provider": provider_name,
        "model": model_name,
        "tokens_input": input_tokens,
        "tokens_output": output_tokens,
        "cost": cost,
        "unexpected_sections": unexpected_sections,
        "duplicate_sections": duplicate_sections,
        "subsections_map": subsections_map,
        "structural_anomalies": structural_anomalies,
        "duplicate_detected": duplicate_detected,
        "continuation_rejected_as_duplicate": continuation_rejected_as_duplicate,
        "initial_word_count": initial_word_count,
        "final_word_count": words,
        "minimum_words": minimum_words,
        "target_word_count": target_wc,
        "total_continuation_attempts": total_continuation_attempts,
        "successful_continuations": successful_continuations,
        "rejected_continuations": rejected_continuations,
        "duplicate_rejections": duplicate_rejections,
        "repeated_continuation": repeated_continuation,
        "total_added_words": total_added_words,
        "final_quality_gate": quality_gate,
        "deterministic_used": deterministic_used,
        }


def _continuation_step(
    md: str,
    words: int,
    goal: int,
    validated: dict,
    provider: Any,
    context: Any,
    input_tokens: int,
    output_tokens: int,
    max_tokens: int,
    attempt: int,
    previous_proposal_norm: str,
) -> dict:
    """Ejecuta UNA llamada de continuación dirigida y devuelve el estado resultante.

    Devuelve un dict con:
      - md / words / input_tokens / output_tokens: estado actualizado;
      - status: 'inserted' | 'rejected_duplicate' | 'rejected_full_chapter'
                | 'rejected_heading' | 'repeated' | 'stop_insignificant'
                | 'no_target' | 'error';
      - added_words: palabras nuevas insertadas (0 en rechazos);
      - duplicate_detected / continuation_rejected: flags de diagnóstico;
      - target_section: sección objetivo elegida (o None);
      - proposal_norm: texto normalizado de la propuesta (detección de repetición).

    El objetivo ``goal`` (mínimo o target) lo fija PYTHON; el LLM solo produce
    texto. Python valida, decide continuar o terminar y rechaza duplicados.
    """
    try:
        section_word_counts = _get_section_word_counts(md, validated)
        editorial_count = max(len(section_word_counts), 1)
        target_wc = int(validated.get("target_word_count", 3000) or 3000)
        section_target = max(250, round(target_wc / editorial_count))
        target_section = _choose_target_section(section_word_counts, section_target)
        if not target_section:
            log(logger, logging.WARNING, "CHAPTER_WRITER continuation NO_TARGET_SECTION")
            return {
                "md": md, "words": words,
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "status": "no_target", "added_words": 0,
                "duplicate_detected": False, "continuation_rejected": False,
                "target_section": None, "proposal_norm": "",
            }

        current_section_words = int(section_word_counts.get(target_section, 0) or 0)
        target_continuation_words = _continuation_request_words(goal, words)
        section_text = _get_section_text(md, target_section)
        book_meta = validated.get("book_metadata") or {}
        lang = validated.get("language") or book_meta.get("language") or "es"
        research = validated.get("research") or ""
        continuation_prompt = _build_section_continuation_prompt(
            target_section,
            current_section_words,
            target_continuation_words,
            section_text,
            research,
            lang,
            md,
        )
        log(
            logger,
            logging.INFO,
            f"CHAPTER_WRITER continuation START attempt={attempt + 1} model={DEFAULT_ROUTER_MODEL} "
            f"max_tokens={max_tokens} current_words={words} goal={goal} target_section={target_section} "
            f"current_section_words={current_section_words} target_continuation_words={target_continuation_words}",
        )
        _cont_start = time.perf_counter()
        cont_result = provider.generate(
            continuation_prompt,
            system="Return ONLY the new Markdown paragraphs, no extra text.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=max_tokens,
            temperature=0.3,
            context=context,
        )
        _cont_duration = time.perf_counter() - _cont_start
        new_text_raw = cont_result.text or ""
        added_words = len(new_text_raw.split())
        log(
            logger,
            logging.INFO,
            f"CHAPTER_WRITER continuation END attempt={attempt + 1} duration={_cont_duration:.2f}s "
            f"added_words={added_words} total_words={words + added_words} target_section={target_section} "
            f"target_continuation_words={target_continuation_words} provider={provider.name if provider else 'none'}",
        )
        _new_words_list = new_text_raw.split()
        _new_first = " ".join(_new_words_list[:100])
        _new_last = " ".join(_new_words_list[-30:])
        log(
            logger,
            logging.WARNING,
            f"CHAPTER_WRITER continuation RAW_SAMPLE attempt={attempt + 1} first={_new_first!r} last={_new_last!r}",
        )

        proposal_norm = _normalize_block(new_text_raw)

        # El modelo reenvió un capítulo completo (heading H1 "# Título"): no es
        # contenido nuevo dirigido a una sección, se rechaza.
        if re.search(r"(?m)^\s*# \S", new_text_raw):
            log(
                logger,
                logging.WARNING,
                f"CHAPTER_WRITER continuation REJECTED(full_chapter_repeat) attempt={attempt + 1}",
            )
            return {
                "md": md, "words": words,
                "input_tokens": input_tokens + cont_result.input_tokens,
                "output_tokens": output_tokens + cont_result.output_tokens,
                "status": "rejected_full_chapter", "added_words": added_words,
                "duplicate_detected": True, "continuation_rejected": True,
                "target_section": target_section, "proposal_norm": proposal_norm,
            }

        if re.search(r"(?m)^\s*#{2,3}\s+\S", new_text_raw):
            _prose = _strip_markdown_headings(new_text_raw).strip()
            log(
                logger,
                logging.WARNING,
                f"CHAPTER_WRITER continuation HEADING_DETECTED attempt={attempt + 1} prose_words={len(_prose.split())}",
            )
            if not _prose:
                log(
                    logger,
                    logging.WARNING,
                    f"CHAPTER_WRITER continuation REJECTED_AS_HEADING no_prose attempt={attempt + 1}",
                )
                return {
                    "md": md, "words": words,
                    "input_tokens": input_tokens + cont_result.input_tokens,
                    "output_tokens": output_tokens + cont_result.output_tokens,
                    "status": "rejected_heading", "added_words": added_words,
                    "duplicate_detected": True, "continuation_rejected": True,
                    "target_section": target_section, "proposal_norm": proposal_norm,
                }
            new_text_raw = _prose
            log(
                logger,
                logging.WARNING,
                f"CHAPTER_WRITER continuation SALVAGING_HEADING attempt={attempt + 1} target_section={target_section}",
            )

        new_text = new_text_raw
        added_words = len(new_text.split())

        # Rechazo del LLM ("Lo siento, pero no puedo ayudar con eso.", etc.):
        # la propuesta se descarta como los duplicados (NO finaliza el capítulo;
        # si queda presupuesto, se reintenta). No es 'repeated' ni 'error'.
        if _detect_refusal(new_text):
            log(
                logger,
                logging.WARNING,
                f"CHAPTER_WRITER continuation REJECTED_AS_REFUSAL attempt={attempt + 1} "
                f"current_words={words} added_words={added_words}",
            )
            return {
                "md": md, "words": words,
                "input_tokens": input_tokens + cont_result.input_tokens,
                "output_tokens": output_tokens + cont_result.output_tokens,
                "status": "rejected_refusal", "added_words": added_words,
                "duplicate_detected": False, "continuation_rejected": True,
                "target_section": target_section, "proposal_norm": proposal_norm,
            }

        condition_3 = _has_strong_text_overlap(md, new_text)
        condition_4 = bool(section_text) and _has_strong_text_overlap(new_text, section_text)
        strong_duplicate = condition_3 or condition_4
        log(
            logger,
            logging.WARNING,
            f"CHAPTER_WRITER continuation DUPLICATE_DIAGNOSTIC attempt={attempt + 1} "
            f"target_section={target_section} current_section_words={current_section_words} "
            f"target_continuation_words={target_continuation_words} added_words={added_words} "
            f"condition_3={condition_3} condition_4={condition_4} new_text_words={len(new_text.split())} "
            f"section_text_words={len(section_text.split()) if section_text else 0} md_words={len(md.split())}",
        )

        # El LLM devolvió exactamente el mismo contenido dos veces: Python lo
        # detecta y deja de insistir en esa misma estrategia.
        repeated = bool(proposal_norm) and proposal_norm == previous_proposal_norm
        if repeated:
            log(
                logger,
                logging.WARNING,
                f"CHAPTER_WRITER continuation REJECTED_REPEATED_PROPOSAL attempt={attempt + 1} current_words={words}",
            )
            return {
                "md": md, "words": words,
                "input_tokens": input_tokens + cont_result.input_tokens,
                "output_tokens": output_tokens + cont_result.output_tokens,
                "status": "repeated", "added_words": added_words,
                "duplicate_detected": True, "continuation_rejected": True,
                "target_section": target_section, "proposal_norm": proposal_norm,
            }

        # Duplicado fuerte frente al contenido existente: se rechaza ESTA
        # propuesta, pero NO es finalización del capítulo.
        if strong_duplicate:
            log(
                logger,
                logging.WARNING,
                f"CHAPTER_WRITER continuation REJECTED_AS_DUPLICATE attempt={attempt + 1} "
                f"current_words={words} added_words={added_words}",
            )
            return {
                "md": md, "words": words,
                "input_tokens": input_tokens + cont_result.input_tokens,
                "output_tokens": output_tokens + cont_result.output_tokens,
                "status": "rejected_duplicate", "added_words": added_words,
                "duplicate_detected": True, "continuation_rejected": True,
                "target_section": target_section, "proposal_norm": proposal_norm,
            }

        # Progreso insignificante (vacío o < MIN_CONTINUATION_WORDS): terminación
        # controlada, sin reintentar para no buclar indefinidamente.
        if not new_text or added_words < _MIN_CONTINUATION_WORDS:
            log(
                logger,
                logging.INFO,
                f"CHAPTER_WRITER continuation STOP attempt={attempt + 1} added_words={added_words} (< {_MIN_CONTINUATION_WORDS})",
            )
            return {
                "md": md, "words": words,
                "input_tokens": input_tokens + cont_result.input_tokens,
                "output_tokens": output_tokens + cont_result.output_tokens,
                "status": "stop_insignificant", "added_words": added_words,
                "duplicate_detected": False, "continuation_rejected": False,
                "target_section": target_section, "proposal_norm": proposal_norm,
            }

        # Inserción: se actualiza el documento y se vuelve a contar palabras.
        _md_before_insert = md
        md = _insert_into_section(md, target_section, new_text)
        target_section_found = target_section in md
        md_changed = md != _md_before_insert
        log(
            logger,
            logging.WARNING,
            f"CHAPTER_WRITER continuation INSERTION attempt={attempt + 1} target_section={target_section} "
            f"target_section_found={target_section_found} md_changed={md_changed}",
        )
        words = max(len(md.split()), 1)
        log(
            logger,
            logging.INFO,
            f"CHAPTER_WRITER continuation UPDATE attempt={attempt + 1} target_section={target_section} "
            f"current_words={words} added_words={added_words}",
        )
        return {
            "md": md, "words": words,
            "input_tokens": input_tokens + cont_result.input_tokens,
            "output_tokens": output_tokens + cont_result.output_tokens,
            "status": "inserted", "added_words": added_words,
            "duplicate_detected": False, "continuation_rejected": False,
            "target_section": target_section, "proposal_norm": proposal_norm,
        }
    except Exception:
        # Error del proveedor / excepción inesperada: terminación controlada.
        return {
            "md": md, "words": words,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "status": "error", "added_words": 0,
            "duplicate_detected": False, "continuation_rejected": False,
            "target_section": None, "proposal_norm": "",
        }
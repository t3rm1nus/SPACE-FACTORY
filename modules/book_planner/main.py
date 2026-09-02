"""Módulo book_planner: transforma una idea de libro en un plan editorial.

Capability: create_book_plan
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from core.schemas import BookPlanChapter, BookPlanOutput
from core.logger import get_logger, log
from core.metrics import calculate_cost, extract_anthropic_usage
from core.providers import get as get_provider
from core.schemas import BookPlanPayload, validate_payload

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen-agent:latest")
DEFAULT_IMAGE_REQUIREMENTS = 3

# §17 #22: presupuesto de salida dinámico para la llamada LLM del plan.
# max_tokens=2000 fijo truncaba el JSON con ~20 capítulos (~90-130
# tokens/capítulo observados) y el parseo caía al fallback determinista.
PLANNER_BASE_TOKENS = 400        # campos del libro + margen fijo
# 2026-09-02: el modo expandido (3 sections/capítulo, cada una con heading y
# objective) pesa sustancialmente más que el compacto observado (~90-130
# tokens/cap). Con 150/cap y target_chapters=10 el presupuesto daba 2000 y el
# LLM truncó a mitad del capítulo 8 (caía a fallback). Se sube el margen por
# capítulo a 400, que escala PROPORCIONALMENTE (tc=5→2400, tc=10→4400,
# tc=20→8000) sin hardcodear un valor fijo para ningún conteo.
PLANNER_TOKENS_PER_CHAPTER = 400  # margen holgado para el modo expandido (3 sections)
MAX_PLANNER_TOKENS = 8000        # techo: permite quepan target_chapters=20 expandidos
MIN_PLANNER_TOKENS = 2000        # preserva el valor actual como piso


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


# Secciones canónicas de respaldo por defecto (fallback determinista de outline).
# Se usan SOLO cuando el LLM no devuelve secciones o las devuelve vacías/inválidas,
# garantizando que la fase outline SIEMPRE emita una lista de secciones no vacía
# (mismo patrón de fallback determinista que writer/editor). Nunca se inventan
# fuentes; estas secciones solo estructuran el capítulo por defecto.
_DEFAULT_SECTION_HEADINGS: dict[str, list[tuple[str, str]]] = {
    "es": [
        ("Introducción", "Presentar el capítulo, el tema y su objetivo."),
        ("Desarrollo", "Desarrollar los puntos clave del tema de manera ordenada y rigurosa."),
        ("Conclusión", "Sintetizar las ideas principales y preparar la transición."),
    ],
    "en": [
        ("Introduction", "Present the chapter, its topic and purpose."),
        ("Development", "Develop the chapter's key points clearly and rigorously."),
        ("Conclusion", "Synthesize the main ideas and set up the transition."),
    ],
}


def _default_sections(language: Optional[str]) -> list[dict]:
    """Lista de secciones canónicas por defecto deterministas para un capítulo."""
    lang = (language or "es").lower()
    if lang.startswith("en") or lang.startswith("ing"):
        headings = _DEFAULT_SECTION_HEADINGS["en"]
    else:
        headings = _DEFAULT_SECTION_HEADINGS["es"]
    return [{"heading": heading, "objective": objective} for heading, objective in headings]


def _ensure_sections(ch: dict, language: str) -> dict:
    """Garantiza que un capítulo tenga una lista de secciones no vacía y bien formada.

    Si el LLM devolvió ``sections`` como lista utilizable (con ``heading`` presente),
    la conserva (filtrando entradas sin heading). En caso contrario (ausente, None,
    vacía o inválida) inyecta las secciones canónicas por defecto de forma
    determinista, para que el writer SIEMPRE disponga de un outline no vacío.
    """
    raw_sections = ch.get("sections")
    if isinstance(raw_sections, list):
        valid = [
            {
                "heading": str(s.get("heading", "")).strip(),
                "objective": s.get("objective") or "",
            }
            for s in raw_sections
            if isinstance(s, dict) and str(s.get("heading", "")).strip()
        ]
        if valid:
            ch["sections"] = valid
            return ch
        # lista vacía o todo inválido: se falla a las secciones por defecto
    ch["sections"] = _default_sections(language)
    return ch


def _normalize_plan(plan_data: dict, payload: dict) -> dict:
    """Normaliza el plan crudo del LLM ANTES de construir BookPlanOutput.

    Aplica la conversión de ``image_requirements`` por capítulo y respeta la
    configuración explícita de imágenes del workflow (si la hay), que tiene
    prioridad sobre la sugerencia del LLM.

    Además, corrige ``estimated_words`` inválidos: si el LLM devuelve un valor
    numérico menor que 500, lo eleva a 500 y deja traza en el log.

    Por último, garantiza que cada capítulo tenga una lista de ``sections`` no
    vacía (fallback determinista de outline), ya que el writer depende de ella
    para estructurar contenido y continuar (de otro modo dispara
    NO_TARGET_SECTION y nunca alcanza el mínimo de palabras).
    """
    plan_data = dict(plan_data or {})
    explicit = _resolve_explicit_image_count(payload)
    language = plan_data.get("language") or payload.get("language") or "es"
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
                f"estimated_words inválido (<500) en capítulo; corregido a 500",
            )
            ch["estimated_words"] = 500

        ch = _ensure_sections(ch, language)

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


def _build_translation_prompt(
    book_title: str,
    book_description: str,
    chapters: list[Any],
) -> str:
    """Prompt de la llamada única de traducción ES→EN del plan completo."""
    ch_payload = []
    for c in chapters:
        ch_payload.append({
            "title": c.title,
            "sections": [
                {"heading": (s or {}).get("heading"), "objective": (s or {}).get("objective")}
                for s in (c.sections or [])
            ],
        })
    payload_json = json.dumps({
        "book_title": (book_title or "")[:200],
        "book_description": (book_description or "")[:_TRANSLATE_DESC_MAX_CHARS],
        "chapters": ch_payload,
    }, ensure_ascii=False)
    return (
        "You are a professional editorial translator. Translate the following "
        "Spanish book plan into natural, professional English.\n\n"
        f"{payload_json}\n\n"
        "RULES:\n"
        "- Return ONLY valid JSON, no extra text.\n"
        '- Exact JSON shape: {"title_en":"...","description_en":"...",'
        '"chapters":[{"title_en":"...","sections":[{"heading_en":"...",'
        '"objective_en":"..."}]}]}\n'
        '- The returned "chapters" array MUST have exactly the same number of '
        "chapters, in the same order, and each chapter MUST have exactly the same "
        "number of sections as the input.\n"
        "- Every string must be non-empty and actually translated to English "
        "(never copy the Spanish text unchanged).\n"
    )


def _validate_translation(
    raw: dict[str, Any],
    book_title: str,
    book_description: str,
    chapters: list[Any],
) -> Optional[tuple[str, str, list[dict]]]:
    """Validación all-or-nothing de la traducción. None = descartar TODO.

    Reglas duras: estructura/tipos correctos; nº de capítulos idéntico índice a
    índice; por capítulo, nº de secciones idéntico índice a índice; ningún
    title_en/heading_en/description_en vacío; y el resultado no puede ser
    byte-idéntico (strip/lower) al original ES (LLM no tradujo nada).
    """
    try:
        t_en = str(raw.get("title_en") or "").strip()
        d_en = str(raw.get("description_en") or "").strip()
        chs = raw.get("chapters")
        if not t_en or not d_en or not isinstance(chs, list):
            return None
        if len(chs) != len(chapters):
            return None
        translated: list[dict] = []
        for src_ch, tr_ch in zip(chapters, chs):
            if not isinstance(tr_ch, dict):
                return None
            tr_title = str(tr_ch.get("title_en") or "").strip()
            if not tr_title:
                return None
            tr_secs_raw = tr_ch.get("sections")
            src_secs = [
                {
                    "heading": (s or {}).get("heading"),
                    "objective": (s or {}).get("objective"),
                }
                for s in (src_ch.sections or [])
            ]
            if not isinstance(tr_secs_raw, list) or len(tr_secs_raw) != len(src_secs):
                return None
            tr_secs: list[dict] = []
            for tr_s in tr_secs_raw:
                if not isinstance(tr_s, dict):
                    return None
                h_en = str(tr_s.get("heading_en") or "").strip()
                o_en = str(tr_s.get("objective_en") or "").strip()
                if not h_en or not o_en:
                    return None
                tr_secs.append({"heading": h_en, "objective": o_en})
            translated.append({"title_en": tr_title, "sections": tr_secs})
        # Anti no-op: si TODO lo devuelto coincide byte-idéntico (strip/lower)
        # con el original ES, el LLM no tradujo nada → descartar.
        all_pairs = [(book_title, t_en), (book_description, d_en)]
        for src_ch, tr_ch in zip(chapters, translated):
            all_pairs.append((src_ch.title, tr_ch["title_en"]))
        identical = sum(1 for a, b in all_pairs if _norm_cmp(a) == _norm_cmp(b))
        if identical == len(all_pairs):
            return None
        return t_en, d_en, translated
    except Exception:
        return None


def _translate_plan(
    validated_output: BookPlanOutput,
) -> tuple[Optional[str], Optional[str], dict[int, dict]]:
    """Llama al LLM UNA vez para traducir el plan ES completo (§17 #21).

    Devuelve ``(title_en, description_en, {chapter_number: {title_en,
    outline_en}})``; todo None/vacío si la traducción falla o no valida
    (all-or-nothing, nunca parcial). Timeout interno propio
    (PLANNER_TRANSLATE_PROVIDER_TIMEOUT) sobre instancia de proveedor local.
    """
    chapters = validated_output.chapters or []
    if not chapters:
        return None, None, {}
    try:
        provider = get_provider()
        # Instancia local nueva por llamada (registry.get) → seguro mutar el
        # timeout sin afectar a otros módulos (mismo patrón que editor).
        provider.timeout = PLANNER_TRANSLATE_PROVIDER_TIMEOUT
        prompt = _build_translation_prompt(
            validated_output.title or "",
            validated_output.description or "",
            chapters,
        )
        result = provider.generate(
            prompt,
            system="Devuelve solo JSON válido, sin texto adicional.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=1500,
            temperature=0.2,
        )
        raw = _extract_json(result.text)
        validated_tr = _validate_translation(
            raw,
            validated_output.title or "",
            validated_output.description or "",
            chapters,
        )
        if validated_tr is None:
            log(logger, logging.WARNING,
                "[§17 #21] Traducción EN del plan inválida o desalineada → "
                "descartada (all-or-nothing); campos _en quedan None.")
            return None, None, {}
        t_en, d_en, translated = validated_tr
        per_chapter: dict[int, dict] = {}
        for src_ch, tr_ch in zip(chapters, translated):
            per_chapter[src_ch.number] = {
                "title_en": tr_ch["title_en"],
                # outline_en como LISTA [{heading, objective}] en inglés.
                "outline_en": tr_ch["sections"],
            }
        return t_en, d_en, per_chapter
    except Exception as e:
        log(logger, logging.WARNING,
            f"[§17 #21] Falla la traducción EN del plan ({e}) → campos _en None.")
        return None, None, {}


def _build_prompt(validated: BookPlanPayload) -> str:
    """Construye un prompt estructurado para generar el plan editorial."""
    # §17 #20 PASO 3: si hay fuentes reales, incluirlas (título + resumen) y
    # pedir anclaje. Sin fuentes, el prompt queda EXACTAMENTE igual que antes
    # (cero regresión para ficción / libros sin research).
    sources_block = ""
    if getattr(validated, "sources", None):
        source_lines = "\n".join(
            f"- {s.get('title') or s.get('url')}: {(s.get('summary') or '')[:300]}"
            for s in validated.sources[:10]
            if isinstance(s, dict)
        )
        if source_lines:
            sources_block = (
                "\nFuentes disponibles:\n"
                f"{source_lines}\n\n"
                "REGLA DE ANCLAJE A FUENTES: Si dispones de fuentes, ancla los headings y "
                "objectives de cada sección a temas que ellas soporten. Si las fuentes no "
                "cubren un tema necesario para completar la estructura, formúlalo en términos "
                "generales, sin inventar hechos, nombres propios ni cifras específicas que no "
                "estén en las fuentes.\n"
            )
    return (
        "Eres un editor profesional. Genera un plan editorial JSON estricto a partir de la idea dada.\n\n"
        f"Idea: {validated.idea}\n"
        f"Capítulos objetivo: {validated.target_chapters}\n"
        f"Idioma: {validated.language}\n"
        f"Público objetivo: {validated.target_audience or 'No especificado'}\n"
        f"Longitud deseada: {validated.desired_length or 'No especificada'}\n"
        f"Estilo: {validated.style or 'No especificado'}\n"
        f"Restricciones temáticas: {validated.subject_constraints or 'Ninguna'}\n"
        f"{sources_block}\n"
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
        '"estimated_words":3000,"research_requirements":[],"image_requirements":3,'
        '"sections":[{"heading":"...","objective":"..."}]}]}\n\n'
        "- IMPORTANTE: cada capítulo DEBE incluir el campo \"sections\" con al menos 2-3 secciones.\n"
        "- Cada sección debe ser un objeto con \"heading\" (título) y \"objective\" (objetivo).\n"
        "- Nunca omitas sections ni las dejes vacías. Esto es obligatorio.\n"
        "Devuelve SOLO el JSON, sin texto adicional."
        # 2026-09-02 (fix json_no_extraible): instrucción explícita y genérica
        # prohibiendo abreviar/truncar/aparcar capítulos. El LLM qwen-agent
        # metía comentarios "// Capítulos N a 10 siguen..." dentro del arreglo
        # en vez de generar los target_chapters completos; esto lo prohíbe
        # expresamente (la tolerancia del parser es la Parte 3, red de
        # seguridad, no la solución).
        "- IMPORTANTE: debes generar los {validated.target_chapters} capítulos COMPLETOS, numerados del 1 al {validated.target_chapters}, TODOS dentro del arreglo \"chapters\". Cada capítulo debe ser un objeto JSON completo, con todas sus claves (incluida \"sections\") pobladas con contenido real y propio de ese capítulo.\n"
        "- PROHIBIDO abreviar o truncar: NO generes comentarios de código (\"//\" o \"/* */\") dentro del JSON bajo ninguna circunstancia. NO uses \"...\" de relleno. NO uses frases tipo \"los capítulos siguen un patrón similar\", \"los capítulos N a 10 continúan igual\" ni equivalentes.\n"
        "- El JSON debe cerrar todos sus arrays y objetos hasta el último capítulo ({validated.target_chapters}). Si lo entregas incompleto o abreviado, se rechazará y te pedirán regenerarlo.\n"
        "Devuelve SOLO el JSON completo y cerrado, sin texto adicional ni comentarios."
    )


# Keywords para inferencia determinista de género desde la idea.
# Ordenadas de más específica a menos específica. Solo devuelve un género
# cuando encuentra un término clave reconocible en la idea; si no, None
# (nunca inventa).
_GENRE_KEYWORDS: list[tuple[str, str]] = [
    ("ciencia ficción", "Ciencia ficción"),
    ("science fiction", "Ciencia ficción"),
    ("sci-fi", "Ciencia ficción"),
    ("ciência ficção", "Ciencia ficción"),
    ("novela negra", "Novela negra"),
    ("noir", "Novela negra"),
    ("fantasía", "Fantasía"),
    ("fantasy", "Fantasía"),
    ("misterio", "Misterio"),
    ("suspense", "Suspenso"),
    ("terror", "Terror"),
    ("horror", "Terror"),
    ("romance", "Romance"),
    ("romántica", "Romance"),
    ("autoayuda", "Autoayuda"),
    ("self-help", "Autoayuda"),
    ("self help", "Autoayuda"),
    ("biografía", "Biografía"),
    ("biography", "Biografía"),
    ("ensayo", "Ensayo"),
    ("essay", "Ensayo"),
    ("política", "Política"),
    ("negocio", "Negocio"),
    ("business", "Negocio"),
]


def _infer_genre(idea: str) -> Optional[str]:
    """Infiere un género editorial desde la idea mediante coincidencia de keywords.

    Es determinista y conservador: solo devuelve un género cuando la idea
    contiene un término clave reconocible. Si no hay coincidencia clara,
    devuelve None — nunca inventa un género.
    """
    if not idea:
        return None
    text = idea.lower().strip()
    for keyword, genre in _GENRE_KEYWORDS:
        if keyword in text:
            return genre
    return None


def _short_idea_title(idea: str, max_words: int = 8) -> str:
    """Acorta una idea a un título corto de capítulo, cortando en límite de palabra."""
    words = (idea or "").strip().split()
    if not words:
        return "Capítulo"
    short = " ".join(words[:max_words])
    if len(words) > max_words:
        short += "..."
    return short


def _extract_named_entities(text: str) -> list[str]:
    """Extrae candidatos a entidad nombrada de un texto (determinista, sin NLP).

    Dos criterios complementarios, sin dependencias ni heurísticas de POS:

    1) Secuencias de 2+ palabras capitalizadas consecutivas, tolerando
       conectores minúsculos intermedios (de, del, la, el, von, ...).
       Ej.: "Reyes Católicos, Imperio Español y Guerra Civil" → 3 entidades;
       "Estados Unidos de América" → 1 entidad.

    2) Nombres propios SIMPLES (una sola palabra capitalizada) cuando van
       precedidos de preposición/artículo: patrón típico de topónimos y
       personas en español (p.ej. "de Magallanes", "la Antártida",
       "El último tango en París" → "París"). Se descartan si ya son parte
       de una secuencia más larga del criterio 1 (evita duplicados).

    NO cuenta una única palabra capitalizada al inicio de frase sin
    preposición precedente ("Grandes expediciones...", "Novela corta...") —
    es inicio descriptivo, no entidad.

    Fail-safe: lista vacía si no hay coincidencias (nunca inventa).
    """
    if not text:
        return []
    cap = r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúñü]+"
    # Conectores que SÍ unen palabras de un mismo nombre propio.
    connector = r"(?:de|del|la|el|los|las|von|van|da|do|das|dos)"
    # Criterio 1: secuencias Title-Case (mismo comportamiento histórico).
    multi = re.compile(
        cap + r"(?:\s+" + cap + r"|\s+" + connector + r"\s+" + cap + r")+(?:\s+"
        + connector + r"\s+" + cap + r")*"
    )
    # Criterio 2: preposiciones/artículos que preceden a un nombre propio simple.
    prep = (
        r"(?:a|al|de|desde|del|hasta|hacia|en|por|con|para|entre|tras|sobre"
        r"|la|el|los|las|un|una)"
    )
    single = re.compile(r"(?<![A-Za-zÁÉÍÓÚÑÜáéíóúñü])(" + prep + r")\s+(" + cap + r")")

    spans: list[tuple[int, int, str]] = []
    for m in multi.finditer(text):
        if m.group(0).strip():
            spans.append((m.start(), m.end(), m.group(0).strip()))
    for m in single.finditer(text):
        token = m.group(2)
        if token:
            spans.append((m.start(2), m.end(2), token))

    # Unificar por posición de aparición, descartando solapamientos
    # (una palabra capitalizada ya absorbida por una secuencia más larga).
    spans.sort(key=lambda s: s[0])
    merged: list[str] = []
    covered_until = -1
    for start, end, txt in spans:
        if start <= covered_until:
            continue
        merged.append(txt)
        covered_until = end
    return list(dict.fromkeys(merged))


def _fallback_plan(validated: BookPlanPayload) -> dict[str, Any]:
    """Plan básico determinista cuando no hay LLM disponible."""
    title = validated.idea.strip()
    short_title = _short_idea_title(title)
    subtitle = "Plan editorial"
    chapters = []
    base_words = 3000
    # 2026-09-01: si la idea contiene entidades nombradas reconocibles
    # (secuencias de 2+ palabras capitalizadas), se usan como título de los
    # primeros capítulos (en orden de aparición) en vez del genérico
    # "Parte N". Si hay menos entidades que capítulos, el resto conserva el
    # título genérico; si no hay ninguna, comportamiento histórico intacto.
    entities = _extract_named_entities(validated.idea)
    if entities:
        logger.warning(
            "[planner][fallback] %d entidad(es) nombrada(s) detectadas en la "
            "idea; se usan como títulos de capítulo: %s",
            len(entities),
            entities[:10],
        )
    for i in range(1, validated.target_chapters + 1):
        if i <= len(entities):
            chapter_title = entities[i - 1]
        else:
            # Sin prefijo "Capítulo N:" (lo añade document_builder/_add_toc).
            # Título corto derivado de la idea, NO la idea completa.
            chapter_title = f"{short_title} - Parte {i}"
        chapters.append(
            {
                "number": i,
                "title": chapter_title,
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
        "language": validated.language,
        "genre": _infer_genre(validated.idea),
    }


def _strip_json_comments(text: str) -> str:
    """Elimina líneas de comentario ``//...`` que el LLM pueda colar en el JSON.

    Red de seguridad genérica de la Parte 3 (fix json_no_extraible): cuando el
    modelo (p.ej. qwen-agent) inserta abreviaciones tipo
    ``// Capítulos 8 a 10 siguen el mismo patrón`` dentro del arreglo, esa
    línea rompe ``json.loads``. Este preprocesado descarta cualquier línea cuyo
    primer token (fuera de string) sea ``//``.

    Es string-aware: recorre el texto línea a línea vigilando si estamos
    dentro de un string JSON (respetando escapes) para NO tocar ``//`` legítimos
    (p.ej. URLs ``https://`` dentro de una descripción u objective). Como el
    JSON válido no permite saltos de línea dentro de strings, en la práctica
    ``in_string`` solo queda activo a mitad de línea en textos ya corruptos; la
    protección es meramente defensiva y no sustituye a la Parte 1.

    Args:
        text: porción del JSON extraído (entre el primer ``{`` y el último ``}``).

    Returns:
        El mismo texto sin las líneas que sean comentarios ``//`` fuera de string.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for line in text.split("\n"):
        stripped = line.lstrip()
        # Una línea ES comentario si empieza por '//' y en esa posición no
        # estamos dentro de un string heredado de la línea anterior.
        is_comment = (not in_string) and stripped.startswith("//")
        if is_comment:
            continue
        out.append(line)
        # Actualizar el estado de string recorriendo la línea conservada.
        for ch in line:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                escaped = False
    return "\n".join(out)


def _extract_json(text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON del texto de respuesta del LLM."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No se encontró JSON en la respuesta")
    # Parte 3 (fix json_no_extraible): antes de json.loads, eliminar líneas de
    # comentario "//..." que el LLM pueda colar (fuera de strings). Si el resto
    # del JSON es válido y completo, un comentario residual ya no tumba el parseo.
    candidate = _strip_json_comments(text[start : end + 1])
    return json.loads(candidate)


# ---------------------------------------------------------------------------
# §17 #21 (Opción A): traducción EN del plan para libros bilingües ("es,en").
# UNA sola llamada LLM adicional que traduce título+descripción del libro y
# títulos+secciones de TODOS los capítulos en un único payload/respuesta.
# Validación all-or-nothing: cualquier desalineación, cadena vacía, resultado
# idéntico al ES o excepción → descarte completo (campos None, fase PASS).
# ---------------------------------------------------------------------------
PLANNER_TRANSLATE_PROVIDER_TIMEOUT = int(
    os.environ.get("PLANNER_TRANSLATE_TIMEOUT", "60")
)
# Truncado de descripción larga en el prompt de traducción (mismo criterio
# conservador que _short_idea_title: acotar input, no inventar). 600 chars
# cubren holgadamente una contraportada sin inflar el payload.
_TRANSLATE_DESC_MAX_CHARS = 600


def _plan_languages(lang_value: Any) -> list[str]:
    """Parsea el campo ``language`` del planner ("es", "es,en", ...) a lista.

    Misma semántica que frontend.editorial._resolve_book_languages pero sobre
    el string crudo que llega en el payload (el planner no recibe el book dict).
    """
    if isinstance(lang_value, (list, tuple)):
        parts = [str(p).strip().lower() for p in lang_value]
    else:
        parts = [p.strip().lower() for p in str(lang_value or "").split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return ["es"]
    # Orden canónico: "es" primero si presente (idioma primario).
    ordered = ["es"] if "es" in parts else []
    ordered += [p for p in parts if p != "es"]
    return ordered


def _norm_cmp(text: Any) -> str:
    """Normaliza para comparación idéntico-al-original (strip + lower)."""
    return str(text or "").strip().lower()


def _deterministic_outline_en(sections: list[dict]) -> Optional[list[dict]]:
    """Mapea headings canónicos ES→EN SIN red (fallback del fallback).

    Solo aplica cuando TODAS las secciones del capítulo coinciden exactamente
    con _DEFAULT_SECTION_HEADINGS["es"]; en ese caso devuelve las EN. Cualquier
    otro caso → None (no se inventa traducción).
    """
    es_map = {h: o for h, o in _DEFAULT_SECTION_HEADINGS["es"]}
    if not sections:
        return None
    # Mapa posicional ES→EN: misma posición en las listas canónicas.
    en_by_pos = {
        h_es: h_en
        for (h_es, _), (h_en, _) in zip(_DEFAULT_SECTION_HEADINGS["es"], _DEFAULT_SECTION_HEADINGS["en"])
    }
    out: list[dict] = []
    for s in sections:
        heading = str((s or {}).get("heading") or "").strip()
        if heading not in es_map:
            return None
        out.append({"heading": en_by_pos[heading], "objective": (s or {}).get("objective")})
    return out


def _planner_max_tokens(target_chapters: int) -> int:
    """Presupuesto de salida dinámico para la llamada LLM del plan (§17 #22).

    Fórmula (2026-09-02, tras el fallo json_no_extraible):
        min(MAX_PLANNER_TOKENS, max(MIN_PLANNER_TOKENS,
            PLANNER_BASE_TOKENS + PLANNER_TOKENS_PER_CHAPTER * target_chapters))
        = min(8000, max(2000, 400 + 400*target_chapters))

    ANTES era 150 tokens/cap (tc=10 → 2000) y el modo expandido de 3 sections
    por capítulo truncaba a mitad del capítulo 8; ahora con 400 tokens/cap el
    presupuesto queda: tc=5→2400, tc=10→4400, tc=20→8000. Escala
    linealmente con techo y piso; nunca un valor fijo para un conteo concreto.
    """
    return min(
        MAX_PLANNER_TOKENS,
        max(MIN_PLANNER_TOKENS, PLANNER_BASE_TOKENS + PLANNER_TOKENS_PER_CHAPTER * target_chapters),
    )


def execute(payload: dict) -> dict:
    """Genera un plan editorial estructurado a partir de una idea de libro.

    Usa el proveedor LLM configurado. Si no hay proveedor disponible, devuelve
    un plan determinista básico (fallback) sin invocar al LLM.
    """
    validated = validate_payload("create_book_plan", payload)
    model_validated = BookPlanPayload(**validated)
    provider = None
    raw = None
    raw_text: Optional[str] = None
    input_tokens = 0
    output_tokens = 0
    provider_name = "none"
    model_name = ""
    plan_data: Optional[dict[str, Any]] = None
    used_fallback = True  # asume fallback; el éxito del LLM lo desactiva
    # Retry único (2026-09-01): ante fallo del LLM (cualquiera de las 4 causas
    # instrumentadas), se reintenta UNA sola vez la MISMA llamada (mismo prompt,
    # mismo provider, sin backoff ni timeouts nuevos — el provider ya tiene su
    # timeout propio) antes de caer a _fallback_plan. Aplica a las 4 causas
    # incluida provider_ausente: si el provider sigue ausente, el intento 2
    # falla rápido y sin llamadas de red.
    llm_attempts = 2
    last_cause = "error_llm"
    last_exc: Optional[BaseException] = None

    for llm_attempt in range(1, llm_attempts + 1):
        try:
            provider = get_provider()
            prompt = _build_prompt(model_validated)
            result = provider.generate(
                prompt,
                system="Devuelve solo JSON válido, sin texto adicional.",
                model=DEFAULT_ROUTER_MODEL,
                # §17 #22: presupuesto dinámico — 2000 fijo truncaba el JSON con
                # ~20 capítulos (~90-130 tokens/capítulo) y caía al fallback.
                max_tokens=_planner_max_tokens(int(model_validated.target_chapters or 0)),
                temperature=0.4,
            )
            raw = result.raw_response
            raw_text = result.text
            provider_name = result.provider
            model_name = result.model
            input_tokens = result.input_tokens
            output_tokens = result.output_tokens
            plan_data = _extract_json(result.text)
            # Validación de conteo: un JSON válido pero incompleto (menos capítulos
            # de los pedidos) se rechaza y cae al fallback, en vez de aceptar un
            # libro con menos capítulos de los solicitados.
            returned_chapters = plan_data.get("chapters") if isinstance(plan_data, dict) else None
            target = int(model_validated.target_chapters or 0)
            if target > 0 and (not isinstance(returned_chapters, list) or len(returned_chapters) < target):
                got = len(returned_chapters) if isinstance(returned_chapters, list) else 0
                raise ValueError(
                    f"Plan incompleto: se pidieron {target} capítulos, el LLM devolvió {got}"
                )
            used_fallback = False
            if llm_attempt > 1:
                logger.warning(
                    "[planner] Retry %d/%d del LLM tuvo éxito: plan válido obtenido "
                    "sin caer a fallback.",
                    llm_attempt,
                    llm_attempts,
                )
            break
        except Exception as e:
            last_exc = e
            if provider is not None:
                provider_name = provider.name
            # Instrumentación prospectiva (2026-09-01): registrar la CAUSA
            # CONCRETA del fallo a nivel WARNING (visible en stdout con
            # LOG_LEVEL=INFO).
            if provider is None:
                cause = "provider_ausente"
            elif isinstance(e, json.JSONDecodeError):
                cause = "json_no_extraible"
            elif isinstance(e, ValueError) and "No se encontró JSON" in str(e):
                cause = "json_no_extraible"
            elif isinstance(e, ValueError) and str(e).startswith("Plan incompleto"):
                cause = "capitulos_incompletos"
            else:
                cause = "error_llm"
            last_cause = cause
            if llm_attempt < llm_attempts:
                logger.warning(
                    "[planner] Intento %d/%d falló (causa: %s): %s. "
                    "Reintentando UNA vez antes de caer a fallback.",
                    llm_attempt,
                    llm_attempts,
                    cause,
                    e,
                )
            else:
                logger.warning(
                    "[planner] Intento %d/%d falló (causa: %s): %s. "
                    "Sin más reintentos: cae a fallback.",
                    llm_attempt,
                    llm_attempts,
                    cause,
                    e,
                )

    if used_fallback:
        # El bucle agotó los intentos sin un plan válido del LLM (o no hubo
        # break de éxito): cae al fallback determinista. NOTA: la condición es
        # used_fallback (flag de éxito), NO plan_data is None — un intento puede
        # haber obtenido un JSON parseable pero incompleto (capitulos_
        # incompletos), y ese plan_data NO debe sobrevivir al fallback.
        log(
            logger,
            logging.WARNING,
            f"Fallo al generar plan con LLM ({provider_name}): {last_exc}. "
            f"Causa fallback: {last_cause}. Usando fallback.",
        )
        if raw_text:
            # §17 #22: conserva el texto crudo (truncado) para diagnosticar
            # truncamientos. WARNING desde 2026-09-01 (antes DEBUG, se perdía
            # con LOG_LEVEL=INFO). Nota: raw_text corresponde al último intento
            # que llegó a obtener respuesta del LLM (intento 1 o 2).
            logger.warning(
                "[§17 #22] Respuesta cruda del planner LLM (primeros 2000 chars): %r",
                raw_text[:2000],
            )
        plan_data = _fallback_plan(model_validated)
        used_fallback = True

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

    # ---- §17 #21 (Opción A): plan bilingüe. Si el libro declara MÁS de un
    # idioma (p.ej. "es,en"), traducir título+descripción del libro y
    # títulos+secciones de todos los capítulos con UNA llamada LLM extra
    # (validación all-or-nothing). En fallback determinista NO hay llamada LLM:
    # outline_en solo si las secciones son las canónicas (mapeo sin red);
    # title_en/description_en quedan None.
    plan_langs = _plan_languages(model_validated.language)
    title_en: Optional[str] = None
    description_en: Optional[str] = None
    per_chapter_en: dict[int, dict] = {}
    if len(plan_langs) > 1 and "en" in plan_langs and validated_output.chapters:
        if used_fallback:
            log(logger, logging.INFO,
                "[§17 #21] Plan generado por fallback determinista: se intenta "
                "mapeo EN sin red para secciones canónicas (sin llamada LLM).")
            for c in validated_output.chapters:
                det = _deterministic_outline_en(c.sections or [])
                if det:
                    per_chapter_en[c.number] = {
                        "title_en": None, "outline_en": det,
                    }
        else:
            title_en, description_en, per_chapter_en = _translate_plan(
                validated_output
            )

    cost = 0.0
    if provider is not None:
        try:
            cost = float(
                calculate_cost(provider_name or provider.name, model_name or provider.model or DEFAULT_MODEL, input_tokens, output_tokens)
                or 0.0
            )
        except Exception:
            cost = 0.0

        # Propagar language (del payload, default "es") y genre (inferido de la idea).
    # author se omite: no hay forma honesta de derivarlo de una idea.
    plan_language = model_validated.language
    plan_genre = plan_data.get("genre") or _infer_genre(model_validated.idea)

    log(
        logger,
        logging.INFO,
        f"Plan editorial generado: {validated_output.title} ({len(chapters)} capítulos)",
    )

    out_chapters = []
    for c in validated_output.chapters:
        d = c.model_dump()
        tr = per_chapter_en.get(c.number) or {}
        # §17 #21: campos _en SIEMPRE presentes (None explícito si no hubo
        # traducción válida) para consumo/persistencia sin ambigüedad.
        d["title_en"] = tr.get("title_en")
        d["outline_en"] = tr.get("outline_en")
        out_chapters.append(d)

    return {
        "title": validated_output.title,
        "subtitle": validated_output.subtitle,
        "description": validated_output.description,
        "title_en": title_en,
        "description_en": description_en,
        "target_audience": validated_output.target_audience,
        "chapters": out_chapters,
        "language": plan_language,
        "genre": plan_genre,
        "provider": provider_name,
        "model": model_name,
        "tokens_input": input_tokens,
        "tokens_output": output_tokens,
        "cost": cost,
    }


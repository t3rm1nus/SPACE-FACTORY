"""Módulo book_planner: transforma una idea de libro en un plan editorial.

Capability: create_book_plan
"""

from __future__ import annotations

import json
import logging
import os
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
        '"estimated_words":3000,"research_requirements":[],"image_requirements":3,'
        '"sections":[{"heading":"...","objective":"..."}]}]}\n\n'
        "- IMPORTANTE: cada capítulo DEBE incluir el campo \"sections\" con al menos 2-3 secciones.\n"
        "- Cada sección debe ser un objeto con \"heading\" (título) y \"objective\" (objetivo).\n"
        "- Nunca omitas sections ni las dejes vacías. Esto es obligatorio.\n"
        "Devuelve SOLO el JSON, sin texto adicional."
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
        "language": validated.language,
        "genre": _infer_genre(validated.idea),
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

        # Propagar language (del payload, default "es") y genre (inferido de la idea).
    # author se omite: no hay forma honesta de derivarlo de una idea.
    plan_language = model_validated.language
    plan_genre = plan_data.get("genre") or _infer_genre(model_validated.idea)

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
        "language": plan_language,
        "genre": plan_genre,
        "provider": provider_name,
        "model": model_name,
        "tokens_input": input_tokens,
        "tokens_output": output_tokens,
        "cost": cost,
    }


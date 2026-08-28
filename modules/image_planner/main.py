"""Módulo image_planner: genera un plan de imágenes para un capítulo.

Capability: create_chapter_image_plan

Genera EXACTAMENTE 3 imágenes por capítulo (salvo configuración explícita
diferente) con funciones distintas, no redundantes y prompts compatibles
con generadores de imágenes locales.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from core.logger import get_logger, log
from core.metrics import calculate_cost, extract_anthropic_usage
from core.providers import get as get_provider
from core.schemas import ImagePlanPayload, validate_payload

logger = get_logger(__name__)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen-agent:latest")
DEFAULT_ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "qwen-agent:latest")
DEFAULT_NUM_IMAGES = 3

# Funciones distintas por defecto (evita 3 imágenes casi iguales)
_DEFAULT_ROLES = (
    ("hero", "apertura", "ambientación general y concepto del capítulo", "16:9"),
    ("diagram", "explicación", "esquema o diagrama que aclara un concepto", "4:3"),
    ("scene", "ilustración", "escena específica que apoya un punto del texto", "3:2"),
)

_LOCAL_NEGATIVE = (
    "text, watermark, logo, signature, brand name, low quality, blurry, "
    "deformed, mutated, extra fingers, bad anatomy, jpeg artifacts, oversaturated"
)

# Mapeo género → estilo visual, usando exactamente las 12 categorías que produce
# book_planner._infer_genre. Solo afecta al fallback/default cuando no hay un
# visual_style explícito en el payload.
_GENRE_STYLE_MAP = {
    "Ciencia ficción": "Fotografía cyberpunk, neón azulado, luces de neón, atmósfera futurista.",
    "Novela negra": "Fotografía noir, alto contraste, luces de neón rojas, sombras profundas.",
    "Fantasía": "Ilustración fantasy, acuarelas, colores cálidos, atmósfera mágica.",
    "Misterio": "Fotografía de investigación, luz natural tenue, atmósfera de suspenso.",
    "Suspenso": "Fotografía cinematográfica, diagonales dinámicas, tensión visual.",
    "Terror": "Fotografía oscura, alto contraste, sombras dramáticas, atmósfera inquietante.",
    "Romance": "Fotografía editorial romántica, tonos cálidos, luz suave, atmósfera íntima.",
    "Autoayuda": "Fotografía de inspiración, luz natural brillante, colores limpios y positivos.",
    "Biografía": "Fotografía documental, natural, colores fieles, atmósfera realista.",
    "Ensayo": "Fotografía conceptual, limpieza visual, composición minimalista, tonos neutros.",
    "Política": "Fotografía de reportaje, luz natural, atmósfera de debate público.",
    "Negocio": "Fotografía corporativa, iluminación profesional, colores corporativos limpios.",
}


def _resolve_num_images(validated: dict) -> int:
    """Devuelve la cantidad de imágenes: configuración explícita o 3 por defecto."""
    n = validated.get("num_images")
    if n is None:
        return DEFAULT_NUM_IMAGES
    return max(0, int(n))


# Palabras con mayúscula inicial que NO son términos temáticos (ruido en la
# extracción de topics).
_GENERIC_CAPS_WORDS = {
    "Esta", "Este", "Estas", "Estos", "Ese", "Esa", "Esos", "Esas", "Aquel",
    "Aquella", "Aquellos", "Aquellas", "Una", "Un", "Unas", "Unos",
}

# Frases/tokens con mayúscula inicial: "Pong", "Atari", "Space Invaders",
# "Grand Theft Auto", "Magic: The Gathering".
_CAPITALIZED_PHRASE = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:[:\s][A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñ]+)*)"
)


def _extract_chapter_topics(
    chapter_text: str, exclude: Optional[str] = None, max_terms: int = 2
) -> list[str]:
    """Extrae 1-2 términos/frases concretos del texto del capítulo (100% Python).

    Heurística basada en regex que captura palabras o frases con mayúscula
    inicial (propios, nombres de obras/consolas/personajes...), ignorando:
      - la primera palabra de cada oración (mayúscula por gramática, no por ser
        un nombre propio);
      - los términos que aparecen en ``exclude`` (normalmente el título del
        capítulo), para no alimentar el guard de título literal ni repetir el
        título;
      - palabras genéricas de uso corriente.

    Devuelve [] si no encuentra nada (el fallback vuelve entonces al
    comportamiento previo: título + estilo).
    """
    if not chapter_text:
        return []

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").strip().lower())

    excluded_norms = {_norm(m.group(0)) for m in _CAPITALIZED_PHRASE.finditer(exclude or "")}
    topics: list[str] = []
    seen: set[str] = set()
    sentences = re.split(r"(?<=[.!?])\s+", chapter_text.strip())
    for sent in sentences:
        # Quitar la primera palabra de la oración (inicio de frase) y signos.
        remainder = re.sub(r"^\S+\s", "", sent.strip()).rstrip(".,;:()")
        for m in _CAPITALIZED_PHRASE.finditer(remainder):
            term = m.group(0).strip().rstrip(".,;:")
            norm = _norm(term)
            if not norm or norm in seen or norm in excluded_norms:
                continue
            if term in _GENERIC_CAPS_WORDS:
                continue
            # Descartar siglas/términos de 1 palabra y <= 3 letras.
            if len(term.split()) == 1 and len(norm) <= 3:
                continue
            topics.append(term)
            seen.add(norm)
            if len(topics) >= max_terms:
                return topics
    return topics


def _make_image(
    role: str,
    index: int,
    title: str,
    style: str,
    chapter_text: str,
    topic: str = "",
) -> dict[str, Any]:
    """Construye una especificación de imagen para un rol concreto.

    ``topic`` es una extracción determinista de términos concretos del texto del
    capítulo (ver ``_extract_chapter_topics``). Al estar presente, el prompt se
    ancla en contenido real en vez de quedar genérico.
    """
    if role == "hero":
        purpose = f"Imagen de apertura que presenta el capítulo '{title}' y su ambiente visual."
        subject = f"Tema central del capítulo ({topic}), sin texto." if topic else "El tema central y el entorno definidos por el capítulo, sin texto."
        environment = "Escenario general sugerido por el contenido del capítulo."
        lighting = "Luz natural suave, con contraste moderado y profundidad de campo."
        composition = "Composición amplia, punto de interés centrado, horizonte en tercios."
    elif role == "diagram":
        purpose = "Esquema conceptual que aclara una relación, proceso o estructura del capítulo."
        subject = f"Representación conceptual de {topic}, organizada y legible." if topic else "Elementos abstractos y organizativos que representan conceptos clave."
        environment = "Fondo neutro limpio para legibilidad del esquema."
        lighting = "Iluminación plana y uniforme, sin sombras que distraigan."
        composition = "Estructura centrada y equilibrada, elementos bien separados."
    else:  # scene
        purpose = "Escena ilustrativa que apoya un punto concreto del texto sin sustituirlo."
        subject = f"Instante concreto del capítulo ({topic}), sin texto." if topic else "Una situación o instante concreto aludido en el capítulo."
        environment = "Entorno contextual que refuerza la escena descrita."
        lighting = "Luz direccional expresiva acorde a la atmósfera del capítulo."
        composition = "Primer plano o plano medio, líneas guía que dirigen la mirada."

    prompt = (
        f"{subject}, {environment}, {composition}. "
        f"Estilo visual: {style}. {lighting} "
        "Alta calidad, imagen editorial coherente con la identidad visual del libro."
    )
    return {
        "image_id": f"img_{index:02d}_{role}",
        "purpose": purpose,
        "description": f"{purpose} {subject}",
        "composition": composition,
        "subject": subject,
        "environment": environment,
        "lighting": lighting,
        "visual_style": style,
        "aspect_ratio": role_ar(role),
        "prompt": prompt,
        "negative_prompt": _LOCAL_NEGATIVE,
        "caption": f"Figura {index}. {purpose}",
        "placement": f"Sección de apertura / figura {index} del capítulo",
    }


def role_ar(role: str) -> str:
    """Devuelve el aspect ratio configurado para el rol."""
    for r, _, _, ar in _DEFAULT_ROLES:
        if r == role:
            return ar
    return "4:3"


def _build_fallback_plan(validated: dict) -> dict[str, Any]:
    """Plan determinista: exactamente num_images con funciones distintas."""
    num = _resolve_num_images(validated)
    title = validated.get("chapter_title") or "el capítulo"
    # Estilo visual: prioriza visual_style explícito; si no hay, intenta mapear
    # genre a un estilo; si no hay genre o no está en el mapeo, usa el default
    # actual preservado exactamente (sin regresión).
    explicit_style = validated.get("visual_style")
    if explicit_style:
        style = explicit_style
    else:
        genre = validated.get("genre")
        style = _GENRE_STYLE_MAP.get(genre, "realistic")
    chapter_text = validated.get("chapter_text", "")

    # Términos concretos extraídos del texto: el fallback (usado cuando el LLM es
    # rechazado por el guard de título literal) ancla su prompt en contenido real
    # y deja de ser idéntico y genérico en todos los capítulos. Se excluye el
    # chapter_title para no alimentar el guard ni repetir el título literal.
    topic = ", ".join(_extract_chapter_topics(chapter_text, exclude=title))

    roles = [r[0] for r in _DEFAULT_ROLES]
    images = []
    for i in range(num):
        role = roles[i % len(roles)]
        images.append(_make_image(role, i + 1, title, style, chapter_text, topic=topic))

    return {
        "images": images,
        "visual_style": style,
        "identity_notes": [
            "Mantener la misma paleta de color, iluminación y estilo en todas las figuras.",
            "Reutilizar esta guía de estilo en los demás capítulos para consistencia visual.",
        ],
    }


def _build_prompt_es(validated: dict) -> str:
    """Construye el prompt del plan de imágenes (variante ES)."""
    num = _resolve_num_images(validated)
    title = validated.get("chapter_title") or "el capítulo"
    style = validated.get("visual_style") or "Fotografía editorial, paleta coherente, detalle realista"
    chapter_text = validated.get("chapter_text", "")
    return (
        "Eres un director de arte editorial. Planifica las imágenes de un capítulo.\n\n"
        f"Capítulo: {title}\n"
        f"Guía de estilo visual: {style}\n"
        f"Cantidad exacta de imágenes: {num}\n\n"
        f"Texto del capítulo:\n{chapter_text}\n\n"
        "REGLAS:\n"
        f"- Genera EXACTAMENTE {num} imágenes.\n"
        "- Cada imagen debe tener una función DIFERENTE (apertura, explicación, ilustración...).\n"
        "- NO generar tres imágenes casi iguales.\n"
        "- Deben complementar el texto, no sustituir información que debe aparecer escrita.\n"
        "- Evitar material visual redundante.\n"
        "- Prompts compatibles con generadores locales (sin marcas de agua, sin texto en la imagen).\n"
        "- El SUJETO de cada imagen debe ser una ESCENA VISUAL concreta (objetos, entorno, "
        "personajes, acción) relacionada con el tema del capítulo.\n"
        "- NO usar el título del capítulo de forma literal como sujeto de la imagen.\n"
        "- NO describir la imagen como una página, portada, revista, artículo, maquetación "
        "editorial, diagrama con texto ni ningún contenido que implique texto legible.\n"
                "- Mantener una identidad visual consistente entre imágenes y capítulos.\n\n"
        "REGLAS ESTRICTAS:\n"
        "- NO cambiar hechos verificables.\n"
        "- NO eliminar referencias necesarias.\n"
        "- NO inventar información, datos, citas ni fuentes.\n"
        "- NO modificar nombres propios.\n"
        "- NO alterar el significado.\n"
        "- Mantener la extensión y estructura del plan.\n\n"
        "Cada imagen requiere estas claves: image_id, purpose, description, composition, subject, "
        "environment, lighting, visual_style, aspect_ratio, prompt, negative_prompt, caption, placement.\n"
        "- aspect_ratio debe ser uno de: 16:9, 3:2, 4:3, 1:1, 2:3, 9:16.\n\n"
        "Devuelve SOLO JSON válido:\n"
        '{"images":[{...13 claves por imagen...}],"visual_style":"...","identity_notes":["..."]}\n'
        "- visual_style: la guía de estilo visual para este capítulo.\n"
        "- identity_notes: pautas para mantener consistencia visual entre capítulos."
    )


def _build_prompt_en(validated: dict) -> str:
    """Builds the image plan prompt (EN variant).

    §17 #24: same role/structure as the ES skeleton; the no-watermark/no-text
    rule is translated AND reinforced for English-language books.
    """
    num = _resolve_num_images(validated)
    title = validated.get("chapter_title") or "the chapter"
    style = validated.get("visual_style") or "Editorial photography, coherent palette, realistic detail"
    chapter_text = validated.get("chapter_text", "")
    return (
        "You are an editorial art director. Plan the images for a chapter.\n\n"
        f"Chapter: {title}\n"
        f"Visual style guide: {style}\n"
        f"Exact number of images: {num}\n\n"
        f"Chapter text:\n{chapter_text}\n\n"
        "RULES:\n"
        f"- Generate EXACTLY {num} images.\n"
        "- Each image must have a DIFFERENT purpose (opening, explanation, illustration...).\n"
        "- Do NOT generate three nearly identical images.\n"
        "- They must complement the text, not replace information that must appear in writing.\n"
        "- Avoid redundant visual material.\n"
        "- Prompts must be compatible with local generators (no watermarks, NO text in the image).\n"
        "- ABSOLUTELY NO readable text, letters, words, logos or brand marks anywhere in the image.\n"
        "- The SUBJECT of each image must be a concrete VISUAL SCENE (objects, setting, "
        "characters, action) related to the chapter topic.\n"
        "- Do NOT use the chapter title literally as the subject of the image.\n"
        "- Do NOT depict the image as a page, cover, magazine, article, editorial layout, "
        "text diagram or any content implying legible text.\n"
        "- Keep a consistent visual identity across images and chapters.\n\n"
        "STRICT RULES:\n"
        "- Do NOT change verifiable facts.\n"
        "- Do NOT remove necessary references.\n"
        "- Do NOT invent information, data, quotes or sources.\n"
        "- Do NOT modify proper names.\n"
        "- Do NOT alter the meaning.\n"
        "- Keep the plan's length and structure.\n\n"
        "Each image requires these keys: image_id, purpose, description, composition, subject, "
        "environment, lighting, visual_style, aspect_ratio, prompt, negative_prompt, caption, placement.\n"
        "- aspect_ratio must be one of: 16:9, 3:2, 4:3, 1:1, 2:3, 9:16.\n\n"
        "Return ONLY valid JSON:\n"
        '{"images":[{...13 keys per image...}],"visual_style":"...","identity_notes":["..."]}\n'
        "- visual_style: the visual style guide for this chapter.\n"
        "- identity_notes: guidelines to keep visual consistency across chapters."
    )


def _build_prompt(validated: dict) -> str:
    """Wrapper: elige la variante ES o EN según el campo ``language`` del payload
    (default "es" si ausente, mismo criterio que el resto del proyecto)."""
    if str((validated or {}).get("language") or "es").lower().startswith("en"):
        return _build_prompt_en(validated)
    return _build_prompt_es(validated)


_IMAGE_KEYS = (
    "image_id", "purpose", "description", "composition", "subject",
    "environment", "lighting", "visual_style", "aspect_ratio",
    "prompt", "negative_prompt", "caption", "placement",
)


def _parse_llm_output(text: str) -> dict[str, Any]:
    """Extrae el JSON del plan generado por el LLM."""
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
        return {}


def _normalize_title(text: Optional[str]) -> str:
    """Normaliza un texto para comparación case-insensitive y de espacios."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _normalize_images(result_data: dict[str, Any], validated: dict) -> dict[str, Any]:
    """Valida, recorta/rellena imágenes al número exacto y completa campos.

    - El ``negative_prompt`` final SIEMPRE incluye ``_LOCAL_NEGATIVE`` (determinista, en
      Python): si el LLM propone un negativo adicional se concatena, pero la base robusta de
      ``_LOCAL_NEGATIVE`` nunca es opcional (no depende de que el LLM \"se acuerde\").
    - Si el ``prompt`` propuesto por el LLM contiene el título del capítulo como substring
      (comparación case-insensitive y con espacios normalizados), ESA imagen se descarta y
      se sustituye por su homóloga del plan fallback (patrón de rechazo del duplicate-guard
      del writer).
    """
    num = _resolve_num_images(validated)
    raw_images = result_data.get("images")
    fallback_plan = _build_fallback_plan(validated)
    if not isinstance(raw_images, list) or not raw_images:
        return fallback_plan

    title_norm = _normalize_title(validated.get("chapter_title") or "el capítulo")
    fb_images = fallback_plan["images"]

    images = []
    for raw in raw_images[:num]:
        if not isinstance(raw, dict):
            continue
        img = {
            key: (str(raw.get(key, "")).strip() if raw.get(key) is not None else "")
            for key in _IMAGE_KEYS
        }
        if img["aspect_ratio"] not in ("16:9", "3:2", "4:3", "1:1", "2:3", "9:16"):
            img["aspect_ratio"] = "4:3"

        # Cambio 1: negative_prompt determinista (siempre _LOCAL_NEGATIVE presente).
        llm_neg = img["negative_prompt"].strip().strip(",")
        combined_neg = _LOCAL_NEGATIVE
        if llm_neg and llm_neg not in _LOCAL_NEGATIVE:
            combined_neg = f"{_LOCAL_NEGATIVE}, {llm_neg}"
        img["negative_prompt"] = combined_neg

        # Guard: prompt del LLM con el título literal del capítulo -> rechazar ESA imagen.
        if title_norm and title_norm in _normalize_title(img["prompt"]):
            fb_img = fb_images[len(images) % len(fb_images)]
            log(
                logger,
                logging.WARNING,
                f"image_planner: prompt del LLM para '{img.get('image_id')}' contiene el "
                f"título del capítulo (texto literal); se usa el plan fallback en su lugar. "
                f"Prompt descartado: {img['prompt'][:120]!r}",
            )
            images.append(fb_img)
            continue

        images.append(img)

    if len(images) < num:
        # Completar con el fallback para alcanzar la cantidad exacta
        for extra in fb_images:
            if len(images) >= num:
                break
            images.append(extra)

    # Estilo visual: prioriza visual_style explícito; si no, mapea genre; si no, default.
    explicit_style = result_data.get("visual_style") or validated.get("visual_style")
    if explicit_style:
        final_style = explicit_style
    else:
        genre = validated.get("genre")
        final_style = _GENRE_STYLE_MAP.get(genre, "realistic")
    return {
        "images": images,
        "visual_style": str(final_style),
        "identity_notes": [str(n) for n in (result_data.get("identity_notes") or [])]
        or ["Mantener paleta, iluminación y estilo consistentes entre capítulos."],
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

    unhealthy = checks.get("provider_health") is False
    healthy = provider is not None and not unhealthy
    status = "🟢 healthy" if healthy else "🔴 unhealthy"
    if provider:
        status += f" ({provider.name})"
    return {
        "healthy": healthy,
        "dependencies": checks,
        "status": status,
    }


def execute(payload: dict, capability: str = "create_chapter_image_plan") -> dict:
    """Genera el plan de imágenes para un capítulo y valida la estructura."""
    validated = validate_payload(capability, payload)
    num = _resolve_num_images(validated)
    provider = None
    provider_name = "none"
    model_name = ""

    result_data: dict[str, Any] = {}
    try:
        provider = get_provider()
        provider_name = provider.name
        prompt = _build_prompt(validated)
        result = provider.generate(
            prompt,
            system="Eres un director de arte editorial. Devuelve solo JSON.",
            model=DEFAULT_ROUTER_MODEL,
            max_tokens=4000,
            temperature=0.2,
        )
        model_name = result.model
        result_data = _parse_llm_output(result.text)
    except Exception as e:
        log(
            logger,
            logging.WARNING,
            f"Fallo en generación de plan de imágenes con LLM ({provider_name}): {e}. Usando fallback.",
        )

    plan = _normalize_images(result_data, validated)
    images = plan["images"]

    # Garantía final: la cantidad exacta de imágenes
    if len(images) < num:
        fallback = _build_fallback_plan(validated)
        images = (images + fallback["images"])[:num]
    if len(images) > num:
        images = images[:num]

    out = {
        "images": images,
        "visual_style": plan["visual_style"],
        "identity_notes": plan["identity_notes"],
    }

    # Validación Pydantic con el esquema de salida
    try:
        from core.schemas import validate_output

        validate_output(capability, out)
    except Exception as e:
        log(logger, logging.WARNING, f"Validación de salida falló: {e}")

    log(
        logger,
        logging.INFO,
        f"Plan de imágenes generado: {len(out['images'])} imágenes (capability={capability})",
    )
    return out


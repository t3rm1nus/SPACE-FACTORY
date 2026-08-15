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
from typing import Any

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


def _resolve_num_images(validated: dict) -> int:
    """Devuelve la cantidad de imágenes: configuración explícita o 3 por defecto."""
    n = validated.get("num_images")
    if n is None:
        return DEFAULT_NUM_IMAGES
    return max(0, int(n))


def _make_image(role: str, index: int, title: str, style: str, chapter_text: str) -> dict[str, Any]:
    """Construye una especificación de imagen para un rol concreto."""
    if role == "hero":
        purpose = f"Imagen de apertura que presenta el capítulo '{title}' y su ambiente visual."
        subject = "El tema central y el entorno definidos por el capítulo, sin texto."
        environment = "Escenario general sugerido por el contenido del capítulo."
        lighting = "Luz natural suave, con contraste moderado y profundidad de campo."
        composition = "Composición amplia, punto de interés centrado, horizonte en tercios."
    elif role == "diagram":
        purpose = "Esquema conceptual que aclara una relación, proceso o estructura del capítulo."
        subject = "Elementos abstractos y organizativos que representan conceptos clave."
        environment = "Fondo neutro limpio para legibilidad del esquema."
        lighting = "Iluminación plana y uniforme, sin sombras que distraigan."
        composition = "Estructura centrada y equilibrada, elementos bien separados."
    else:  # scene
        purpose = "Escena ilustrativa que apoya un punto concreto del texto sin sustituirlo."
        subject = "Una situación o instante concreto aludido en el capítulo."
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
    style = validated.get("visual_style") or "Fotografía editorial, paleta coherente, detalle realista"
    chapter_text = validated.get("chapter_text", "")

    roles = [r[0] for r in _DEFAULT_ROLES]
    images = []
    for i in range(num):
        role = roles[i % len(roles)]
        images.append(_make_image(role, i + 1, title, style, chapter_text))

    return {
        "images": images,
        "visual_style": style,
        "identity_notes": [
            "Mantener la misma paleta de color, iluminación y estilo en todas las figuras.",
            "Reutilizar esta guía de estilo en los demás capítulos para consistencia visual.",
        ],
    }


def _build_prompt(validated: dict) -> str:
    """Construye el prompt del plan de imágenes."""
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


def _normalize_images(result_data: dict[str, Any], validated: dict) -> dict[str, Any]:
    """Valida, recorta/rellena imágenes al número exacto y completa campos."""
    num = _resolve_num_images(validated)
    raw_images = result_data.get("images")
    if not isinstance(raw_images, list) or not raw_images:
        return _build_fallback_plan(validated)

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
        images.append(img)

    if len(images) < num:
        # Completar con el fallback para alcanzar la cantidad exacta
        fallback = _build_fallback_plan(validated)
        for extra in fallback["images"]:
            if len(images) >= num:
                break
            images.append(extra)

    return {
        "images": images,
        "visual_style": str(result_data.get("visual_style") or validated.get("visual_style")
                           or "Fotografía editorial, paleta coherente, detalle realista"),
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


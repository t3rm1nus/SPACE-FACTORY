"""Módulo image_generator: genera las imágenes de un capítulo.

Capabilities: generate_image, generate_chapter_images

Usa la infraestructura de proveedores de imágenes de ``core.image_providers``
(por defecto el proveedor local, que genera PNG placeholder sin dependencias),
persiste los archivos, los metadatos y la asociación al capítulo.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.image_providers import get as get_provider
from core.schemas import ImageSpec, validate_output

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3

# Presupuesto total (s) de la fase image_gen para TODO el loop de imágenes del
# capítulo. Por debajo del timeout_seconds=360 del scheduler, dejando ~30s de
# margen para la escritura de metadata y el retorno del loop. Mismo patrón de
# backstop que WRITER_TOTAL_TIME_BUDGET / RESEARCH_TOTAL_TIME_BUDGET
# (configurable vía env var, estilo os.environ.get del proyecto).
IMAGE_TOTAL_TIME_BUDGET = float(os.environ.get("IMAGE_TOTAL_TIME_BUDGET", "330.0"))
# Margen (s): si antes de empezar una imagen posterior queda MENOS que esto dentro
# del presupuesto, se fuerza el fallback local en esa imagen y las siguientes en vez
# de intentar el provider real (evita que el timeout duro del scheduler mate la tarea
# a mitad del loop por generaciones legítimamente lentas, ~80s/imagen a 25 steps).
IMAGE_BUDGET_FALLBACK_MARGIN = 90.0
IMAGE_FALLBACK_REASON = "time_budget_exhausted"


def _storage_root() -> str:
    """Directorio raíz de almacenamiento de imágenes."""
    return os.getenv("IMAGE_STORAGE_ROOT") or os.path.join("data", "images")


def _images_dir(book_id: int, chapter_number: int) -> str:
    root = Path(_storage_root())
    path = root / "books" / str(book_id) / "chapters" / str(chapter_number) / "images"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def get_image_provider(provider_name: Optional[str] = None, model: Optional[str] = None):
    """Resuelve el proveedor de imágenes (por defecto el local).

    ``model`` se aplica vía variable de entorno para que el proveedor lo use.
    """
    if model:
        os.environ["IMAGE_MODEL"] = model
    return get_provider(provider_name)





def _load_metadata(images_dir: str, image_id: str) -> Optional[dict]:
    path = os.path.join(images_dir, f"{image_id}.metadata.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_metadata(images_dir: str, data: dict) -> str:
    os.makedirs(images_dir, exist_ok=True)
    path = os.path.join(images_dir, f"{data['image_id']}.metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _generate_single_image(
    provider,
    spec: ImageSpec,
    *,
    book_id: int,
    chapter_number: int,
    language: str,
    images_dir: str,
    attempts: int,
) -> dict:
    """Genera una sola imagen y devuelve su metadato (o el error)."""
    last_error: Optional[str] = None
    for attempt in range(1, attempts + 1):
        try:
            result = provider.generate(
                spec.prompt,
                negative_prompt=spec.negative_prompt,
                aspect_ratio=spec.aspect_ratio,
                model=provider.model,
            )
            width = int(result.metadata.get("width", 1024))
            height = int(result.metadata.get("height", 576))
            metadata = {
                "image_id": spec.image_id,
                "provider": result.provider or provider.name,
                "model": result.model or provider.model,
                "seed": result.seed,
                "width": width,
                "height": height,
                "steps": int(result.metadata.get("steps", 20)),
                "aspect_ratio": spec.aspect_ratio,
                "prompt": spec.prompt,
                "negative_prompt": spec.negative_prompt,
                "image_path": result.image_path,
                "thumbnail_paths": [],
                "status": "ok",
                "attempts": attempt,
                "error": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "extra": {
                    "book_id": book_id,
                    "chapter_number": chapter_number,
                    "language": language,
                    "caption": spec.caption,
                    "placement": spec.placement,
                    "purpose": spec.purpose,
                },
            }
            _write_metadata(images_dir, metadata)
            return metadata
        except Exception as exc:  # noqa: BLE001 - registrar y reintentar
            last_error = str(exc)
            logger.warning(
                "Intento %d/%d para '%s' falló: %s",
                attempt,
                attempts,
                spec.image_id,
                exc,
            )
    return {
        "image_id": spec.image_id,
        "provider": provider.name,
        "model": getattr(provider, "model", ""),
        "seed": 0,
        "width": 1024,
        "height": 576,
        "steps": 1,
        "aspect_ratio": spec.aspect_ratio,
        "prompt": spec.prompt,
        "negative_prompt": spec.negative_prompt,
        "image_path": f"__error__/{spec.image_id}.png",
        "thumbnail_paths": [],
        "status": "error",
        "attempts": attempts,
        "error": last_error,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "extra": {},
    }


    specs = []
    for raw in (image_plan or {}).get("images", []) or []:
        try:
            specs.append(ImageSpec(**raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Especificación de imagen inválida ignorada: %s", exc)
    return specs


def _normalize_specs(image_plan: Optional[dict]) -> list[ImageSpec]:
    specs = []
    for raw in (image_plan or {}).get("images", []) or []:
        try:
            specs.append(ImageSpec(**raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Especificación de imagen inválida ignorada: %s", exc)
    return specs


def _build_simple_plan(chapter_text: str, chapter_title: str, num_images: int) -> list[ImageSpec]:
    """Plan determinista de imágenes cuando no hay plan LLM (mock)."""
    num = max(0, min(int(num_images) if num_images is not None else 3, 20))
    roles = ["hero", "detail", "closing"]
    specs = []
    for i in range(num):
        role = roles[i % len(roles)]
        image_id = f"img_{i + 1:02d}_{role}"
        subject = (chapter_title or "Tema del capítulo").strip() or "Tema central"
        specs.append(
            ImageSpec(
                image_id=image_id,
                purpose=f"Imagen {i + 1} de apertura/apoyo.",
                description=f"Ilustración editorial sobre: {subject}.",
                composition="Composición equilibrada y limpia.",
                subject=subject,
                environment="Escenario general coherente con el texto.",
                lighting="Luz suave y uniforme.",
                visual_style="editorial",
                aspect_ratio="16:9",
                prompt=f"{subject}, entorno editorial. Estilo visual: editorial.",
                negative_prompt="text, watermark, low quality",
                caption=f"Figura {i + 1}: {subject}.",
                placement="Apertura." if role == "hero" else "Apoyo.",
            )
        )
    return specs



def generate_image(payload: dict[str, Any]) -> dict[str, Any]:
    """Genera las imágenes descritas en el plan para un capítulo."""
    from core.schemas import ImageGeneratePayload

    validated = ImageGeneratePayload(**payload)
    provider = get_image_provider(validated.provider or None, validated.model or None)

    specs = _normalize_specs(validated.image_plan)
    images_dir = _images_dir(validated.book_id, validated.chapter_number)
    max_attempts = validated.max_attempts

    results: list[dict] = []
    requested = len(specs)
    generated = 0
    skipped = 0
    failed = 0

    loop_start = time.perf_counter()
    local_provider = None
    budget_exhausted = False

    for idx, spec in enumerate(specs):
        if validated.skip_existing:
            existing = _load_metadata(images_dir, spec.image_id)
            if existing and existing.get("status") == "ok" and existing.get("image_path") and os.path.isfile(existing["image_path"]):
                results.append(existing)
                skipped += 1
                continue

        # Guard de presupuesto total (backstop): la primera imagen se intenta SIEMPRE
        # con el provider real; antes de cada imagen posterior, si no queda margen
        # suficiente dentro de IMAGE_TOTAL_TIME_BUDGET, se fuerza el fallback local
        # para esta y las restantes del loop en vez de arriesgar que el timeout duro
        # del scheduler (360s) mate la tarea a mitad de generación.
        if idx > 0 and not budget_exhausted:
            remaining = IMAGE_TOTAL_TIME_BUDGET - (time.perf_counter() - loop_start)
            if remaining < IMAGE_BUDGET_FALLBACK_MARGIN:
                budget_exhausted = True
                logger.warning(
                    "image_generator: presupuesto total casi agotado "
                    "(remaining=%.1fs < %.1fs); fallback local para '%s' y "
                    "restantes del capítulo",
                    remaining, IMAGE_BUDGET_FALLBACK_MARGIN, spec.image_id,
                )

        provider_used = provider
        if budget_exhausted:
            if local_provider is None:
                local_provider = get_provider("local")
            provider_used = local_provider

        meta = _generate_single_image(
            provider_used,
            spec,
            book_id=validated.book_id,
            chapter_number=validated.chapter_number,
            language=validated.language,
            images_dir=images_dir,
            attempts=max_attempts,
        )
        if budget_exhausted and meta.get("status") == "ok":
            # Marca el fallback por presupuesto para consistencia con el shape que
            # ya usa metadata["fallback"]/["fallback_reason"] del provider.
            meta["fallback"] = True
            meta["fallback_reason"] = IMAGE_FALLBACK_REASON
            _write_metadata(images_dir, meta)
        results.append(meta)
        if meta["status"] == "ok":
            generated += 1
        else:
            failed += 1

    out = {
        "book_id": validated.book_id,
        "chapter_number": validated.chapter_number,
        "language": validated.language,
        "images_dir": images_dir,
        "results": results,
        "requested": requested,
        "generated": generated,
        "skipped": skipped,
        "failed": failed,
    }
    try:
        validate_output("generate_image", out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Validación de salida falló: %s", exc)
    return out


def generate_chapter_images(payload: dict[str, Any]) -> dict[str, Any]:
    """Genera N imágenes para un capítulo, creando un plan simple si falta."""
    data = dict(payload)
    image_plan = data.get("image_plan")
    if not image_plan or not image_plan.get("images"):
        chapter_text = data.get("chapter_text", "")
        chapter_title = data.get("chapter_title") or ""
        _num = data.get("num_images")
        num_images = int(_num) if _num is not None else 3
        specs = _build_simple_plan(chapter_text, chapter_title, num_images)
        image_plan = {"images": [s.model_dump() for s in specs]}
        data["image_plan"] = image_plan

    return generate_image(data)


# §17 #28 — capabilities ES/EN nativas (mismo patrón que chapter_writer
# write_chapter_es/_en e image_planner _es/_en): cada variante fija el idioma
# del payload (y por tanto el plan/metadatos de SU idioma) y delega en
# generate_chapter_images. Sin cambio de proveedor ni de shape.
_CAPABILITY_LANGUAGES = {
    "generate_chapter_images_es": "es",
    "generate_chapter_images_en": "en",
}


def generate_chapter_images_lang(
    payload: dict[str, Any], language: str
) -> dict[str, Any]:
    """Variante nativa por idioma: normaliza ``language`` y delega.

    El image_plan ya viene separado por idioma desde image_planner (_es/_en);
    aquí solo se garantiza que la salida/metadata queden etiquetadas con el
    idioma de la capability invocada."""
    data = dict(payload)
    data["language"] = language
    return generate_chapter_images(data)


def execute(payload: dict, capability: str = "generate_image") -> dict:
    """Wrapper de ejecución: genera imágenes de un capítulo.

    Capabilities soportadas: generate_image, generate_chapter_images,
    generate_chapter_images_es, generate_chapter_images_en (§17 #28).
    """
    if capability == "generate_chapter_images":
        return generate_chapter_images(payload)
    lang = _CAPABILITY_LANGUAGES.get(capability)
    if lang is not None:
        return generate_chapter_images_lang(payload, lang)
    return generate_image(payload)


def health_check() -> dict[str, Any]:
    try:
        from core.image_providers import get as _get

        provider = _get()
        provider.health_check()
        return {
            "healthy": True,
            "dependencies": {"core.image_providers": "ok"},
            "provider": provider.name,
        }
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "error": str(exc), "dependencies": {"core.image_providers": "error"}}


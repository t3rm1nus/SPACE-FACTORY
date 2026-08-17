"""Módulo image_search: busca imágenes de un capítulo en la web (SearXNG, sin LLM).

Capability: search_chapter_images

Sin dependencia de un modelo LLM: construye una query determinista a partir del
título del capítulo (fallback: primeras palabras del texto), consulta SearXNG
(GET /search?categories=images&format=json), descarga cada `img_src` con
timeout corto y persiste los archivos y su `*.metadata.json` en el MISMO
patrón de ruta que ``modules/image_generator``.

El shape de retorno es idéntico al de ``generate_image`` (mismo dict top-level
y mismo shape por imagen, validable con ``validate_output("generate_image", ..)``),
de modo que el módulo sea plug-compatible con un futuro adaptador sin cambios.

Reglas de resiliencia (patrón research/writer):
  - Si SearXNG no responde (timeout/error), NO se lanza excepción: se devuelve
    un resultado con las imágenes que se pudieron obtener y las faltantes
    marcadas como ``status=error``.
  - Si la descarga de una imagen concreta falla, se salta esa imagen y se
    continúa con el resto del lote (nunca aborta el lote completo).
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# Timeouts cortos para la búsqueda y por descarga (sin LLM, horizonte acotado
# muy por debajo de timeout_seconds=120 del scheduler). Configurable vía env,
# mismo estilo os.environ.get del proyecto.
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8081")
SEARCH_TIMEOUT = float(os.environ.get("IMAGE_SEARCH_TIMEOUT", "15"))
DOWNLOAD_TIMEOUT = float(os.environ.get("IMAGE_DOWNLOAD_TIMEOUT", "10"))
MAX_IMAGES = 20

_USER_AGENT = "SpaceLair/1.0 (image_search agent)"

# Tokens de aspect_ratio permitidos por ImageMetadata (core.schemas), para que el
# shape de salida sea validable de forma idéntica a image_generator.
_ASPECT_TOKENS = [
    (16 / 9, "16:9"),
    (3 / 2, "3:2"),
    (4 / 3, "4:3"),
    (1.0, "1:1"),
    (2 / 3, "2:3"),
    (9 / 16, "9:16"),
]

_VALID_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}


# ---------------------------------------------------------------------------
# Ruta de almacenamiento (MISMO patrón que modules/image_generator)
# ---------------------------------------------------------------------------
def _storage_root() -> str:
    """Directorio raíz de almacenamiento de imágenes (igual que image_generator)."""
    return os.getenv("IMAGE_STORAGE_ROOT") or os.path.join("data", "images")


def _images_dir(book_id: int, chapter_number: int) -> str:
    """Mismo patrón de ruta que ``image_generator._images_dir``."""
    root = Path(_storage_root())
    path = root / "books" / str(book_id) / "chapters" / str(chapter_number) / "images"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _write_metadata(images_dir: str, data: dict) -> str:
    os.makedirs(images_dir, exist_ok=True)
    path = os.path.join(images_dir, f"{data['image_id']}.metadata.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# Query de búsqueda
# ---------------------------------------------------------------------------
def _search_query(chapter_title: Optional[str], chapter_text: Optional[str]) -> str:
    """Construye una query determinista a partir del título (fallback: primeras palabras del texto)."""
    title = (chapter_title or "").strip()
    if title:
        return title[:200]
    text = (chapter_text or "").strip()
    words = re.split(r"\s+", text)
    meaningful = [w for w in words if w.strip()]
    if not meaningful:
        return "book illustration"
    return " ".join(meaningful[:12])[:200]


# ---------------------------------------------------------------------------
# Llamadas HTTP a SearXNG (resilientes)
# ---------------------------------------------------------------------------
def _searxng_search(query: str) -> list[dict]:
    """Consulta SearXNG y devuelve la lista de resultados ([] si falla, sin excepción)."""
    try:
        resp = requests.get(
            SEARXNG_URL.rstrip("/") + "/search",
            params={"q": query, "categories": "images", "format": "json"},
            timeout=SEARCH_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
        return list(data.get("results") or [])
    except Exception as exc:  # noqa: BLE001 - timeout/error: no abortar, devolver vacío
        logger.warning("image_search: SearXNG no disponible o error: %s", exc)
        return []


def _download_image(url: str) -> Optional[bytes]:
    """Descarga una imagen; devuelve bytes o None si falla (sin abortar el lote)."""
    try:
        resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, headers={"User-Agent": _USER_AGENT})
        resp.raise_for_status()
        return resp.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("image_search: descarga fallida %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Utilidades de formato
# ---------------------------------------------------------------------------
def _normalize_engine(engine: Any) -> str:
    """SearXNG expone ``engine`` como lista o string; lo normaliza a string."""
    if isinstance(engine, list):
        return ", ".join(str(e) for e in engine if e)
    return str(engine or "searxng")


def _image_extension(url: str) -> str:
    """Extensión derivada de la URL (fallback .png), sin inventar por contenido."""
    try:
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext in _VALID_EXTENSIONS:
            return ext
    except Exception:  # noqa: BLE001
        pass
    return ".png"


def _image_dimensions(data: Optional[bytes]) -> tuple[Optional[int], Optional[int]]:
    """Resolución real de los bytes descargados (PIL); (None, None) si no se puede."""
    if not data:
        return None, None
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return im.width, im.height
    except Exception:  # noqa: BLE001
        return None, None


def _aspect_ratio(width: int, height: int) -> str:
    """Token de aspect_ratio permitido (más cercano) a partir de la resolución."""
    if not width or not height:
        return "16:9"
    ratio = width / height
    return min(_ASPECT_TOKENS, key=lambda t: abs(ratio - t[0]))[1]


# ---------------------------------------------------------------------------
# Construcción de metadatos (mismo shape por imagen que image_generator)
# ---------------------------------------------------------------------------
def _web_meta(
    *,
    image_id: str,
    query: str,
    images_dir: str,
    engine: str,
    status: str,
    attempts: int,
    error: Optional[str],
    source_url: Optional[str] = None,
    width: int = 1024,
    height: int = 576,
    resolution: str = "unknown",
    caption: str = "",
    extra: Optional[dict] = None,
) -> dict:
    """Metadato por imagen con el shape de ``ImageMetadata`` (image_generator).

    Además de los campos estándar de image_generator, incluye la trazabilidad
    web a nivel superior (source_type, source_url, engine, resolution, license)
    tal como se persiste en el ``*.metadata.json``. Pydantic ignora los campos
    extra al validar, por lo que el shape sigue siendo plug-compatible con
    ``validate_output("generate_image", ..)``.
    """
    ok = status == "ok"
    return {
        "image_id": image_id,
        "provider": "searxng",
        "model": engine,
        "seed": 0,
        "width": int(width),
        "height": int(height),
        "steps": attempts,
        "aspect_ratio": _aspect_ratio(int(width), int(height)),
        "prompt": query,
        "negative_prompt": "text, watermark, low quality",
        "image_path": os.path.join(images_dir, f"{image_id}.png") if ok else f"__error__/{image_id}.png",
        "thumbnail_paths": [],
        "status": status,
        "attempts": attempts,
        "error": error,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "extra": dict(extra or {}),
        "source_type": "web_search",
        "source_url": source_url,
        "engine": engine,
        "resolution": resolution,
        "license": None,  # explícito: no inventar licencia
    }


def _error_meta(image_id: str, query: str, images_dir: str, reason: str, source_url: Optional[str] = None) -> dict:
    return _web_meta(
        image_id=image_id,
        query=query,
        images_dir=images_dir,
        engine="web_search",
        status="error",
        attempts=1,
        error=reason,
        source_url=source_url,
    )



# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------
def search_chapter_images(payload: dict) -> dict:
    """Busca hasta ``num_images`` imágenes web para un capítulo y las persiste.

    Payload: book_id, chapter_number, chapter_title, chapter_text, num_images, language.

    Devuelve el MISMO shape que ``generate_image`` (image_generator), para ser
    plug-compatible sin adaptadores.
    """
    data = dict(payload or {})
    book_id = int(data.get("book_id", 0))
    chapter_number = int(data.get("chapter_number", 0))
    language = str(data.get("language") or "es")[:10]
    chapter_title = data.get("chapter_title")
    chapter_text = data.get("chapter_text") or ""

    _num = data.get("num_images")
    num_images = int(_num) if _num is not None else 3
    num_images = max(0, min(num_images, MAX_IMAGES))

    images_dir = _images_dir(book_id, chapter_number)
    query = _search_query(chapter_title, chapter_text)

    raw_results = _searxng_search(query)

    results: list[dict] = []
    requested = num_images
    generated = 0
    failed = 0

    # Cada slot pedido se resuelve con el siguiente resultado útil; si no hay
    # más resultados o la descarga falla, ese slot queda en status=error sin
    # abortar el lote (patrón research/writer).
    slot = 0
    for item in raw_results:
        if slot >= requested:
            break
        img_src = item.get("img_src") or item.get("thumbnail_src")
        if not img_src:
            continue
        image_id = f"img_{slot + 1:02d}_web"
        engine = _normalize_engine(item.get("engine"))
        ext = _image_extension(img_src)
        data_bytes = _download_image(img_src)

        if data_bytes is None:
            results.append(
                _error_meta(image_id, query, images_dir, "download_failed", source_url=img_src)
            )
            failed += 1
            slot += 1
            continue

        width, height = _image_dimensions(data_bytes)
        w = int(width or 1024)
        h = int(height or 576)
        resolution = f"{w}x{h}" if width and height else (str(item.get("resolution") or "unknown"))

        image_path = os.path.join(images_dir, f"{image_id}{ext}")
        try:
            with open(image_path, "wb") as f:
                f.write(data_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("image_search: no se pudo escribir %s: %s", image_path, exc)
            results.append(_error_meta(image_id, query, images_dir, f"write_failed: {exc}", source_url=img_src))
            failed += 1
            slot += 1
            continue

        meta = _web_meta(
            image_id=image_id,
            query=query,
            images_dir=images_dir,
            engine=engine,
            status="ok",
            attempts=1,
            error=None,
            source_url=img_src,
            width=w,
            height=h,
            resolution=resolution,
            caption=item.get("title") or "",
            extra={
                "book_id": book_id,
                "chapter_number": chapter_number,
                "language": language,
                "caption": item.get("title") or "",
                "placement": "Apoyo.",
                "purpose": f"Imagen web {slot + 1} de {requested}.",
            },
        )
        meta["image_path"] = image_path
        _write_metadata(images_dir, meta)
        results.append(meta)
        generated += 1
        slot += 1

    # Llenar los slots pedidos que no pudieron completarse con metadatos de error,
    # de forma que requested == generated + failed (skipped == 0), igual que en
    # generate_image.
    while slot < requested:
        image_id = f"img_{slot + 1:02d}_web"
        results.append(_error_meta(image_id, query, images_dir, "no_results"))
        failed += 1
        slot += 1

    out: dict[str, Any] = {
        "book_id": book_id,
        "chapter_number": chapter_number,
        "language": language,
        "images_dir": images_dir,
        "results": results,
        "requested": requested,
        "generated": generated,
        "skipped": 0,
        "failed": failed,
    }

    # Confirma plug-compatibilidad con image_generator (igual que en su main).
    try:
        from core.schemas import validate_output

        validate_output("generate_image", out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("image_search: validación de salida (generate_image) falló: %s", exc)

    return out


def execute(payload: dict, capability: str = "search_chapter_images") -> dict:
    """Wrapper de ejecución acorde a la convención del proyecto."""
    if capability == "search_chapter_images":
        return search_chapter_images(payload)
    raise ValueError(f"Capability no soportada por image_search: {capability}")


def health_check() -> dict[str, Any]:
    """Health check ligero: verifica dependencias de red sin llamar a SearXNG."""
    try:
        import requests  # noqa: F401
        from PIL import Image  # noqa: F401

        return {
            "healthy": True,
            "dependencies": {"requests": "ok", "pillow": "ok", "searxng": "optional"},
        }
    except Exception as exc:  # noqa: BLE001
        return {"healthy": False, "error": str(exc), "dependencies": {"requests": "error"}}


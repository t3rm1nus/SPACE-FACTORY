"""Módulo image_search: busca imágenes de un capítulo en la web (SearXNG, sin LLM).

Capabilities: search_chapter_images (legacy), search_chapter_images_es,
search_chapter_images_en

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

# Lista mínima conocida de dominios a bloquear (riesgo reputacional/legal: portadas
# de editoriales reales, repositorios académicos/docentes y material con copyright).
# Dominios en minúsculas sin protocolo; se compara por CONTENIDO (subdominios incluidos,
# ej. "es.scribd.com" dispara "scribd.com"). No pretende ser exhaustiva: ampliable.
_DOMAIN_DENYLIST = {
    "scribd.com",
    "scribdassets.com",
    "slideshare.net",
    "coursehero.com",
    "studocu.com",
    "academia.edu",
    "issuu.com",
    "docplayer.net",
    "quizlet.com",
    "mheducation.com",
    "laleo.com",
}


def _is_denylisted(url: Optional[str]) -> bool:
    """True si el dominio de ``url`` contiene algún dominio de ``_DOMAIN_DENYLIST``.

    Tolerante a URL vacía/None (devuelve False, no levanta excepción). Elimina
    el ``www.`` inicial y compara en minúsculas para cubrir subdominios
    (``es.scribd.com`` dispara ``scribd.com``).
    """
    if not url:
        return False
    try:
        netloc = urllib.parse.urlparse(url).netloc.lower()
    except Exception:  # noqa: BLE001 - defensa en depth
        return False
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return any(blocked in netloc for blocked in _DOMAIN_DENYLIST)


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
def _searxng_search(query: str, language: Optional[str] = None) -> list[dict]:
    """Consulta SearXNG y devuelve la lista de resultados ([] si falla, sin excepción).

    ``language`` (opcional): código de idioma SearXNG ("en", "es", ...). Si es
    None se comporta como históricamente (sin filtro de idioma en la request).
    """
    try:
        params: dict[str, str] = {"q": query, "categories": "images", "format": "json"}
        if language:
            # §17 #24: acota resultados por idioma (SearXNG soporta el param
            # nativo `language`; default histórico = sin filtro).
            params["language"] = language
        resp = requests.get(
            SEARXNG_URL.rstrip("/") + "/search",
            params=params,
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
# Filtro de anclaje temático por idioma nativo (§17 #28)
# ---------------------------------------------------------------------------
# Problema (book_67): el filtro §17 #11 reutilizaba _has_anchor_keyword() de
# research, pensado para TEXTO RICO en un solo idioma y con un único topic
# español compartido. Los candidatos de imágenes web exponen casi todo su
# texto como slugs/nombres de fichero EN ("a-cup-of-coffee-sitting..."), así
# que un topic en español no matcheaba NI UNA keyword y se descartaban en masa
# candidatos claramente on-topic. Fix: capabilities ES/EN nativas; cada
# variante ancla contra keywords en SU idioma, sin reutilizar research.
_STOPWORDS_ES_FALLBACK = {
    "el", "la", "los", "las", "de", "del", "en", "un", "una", "unos", "unas",
    "y", "o", "que", "con", "para", "por", "se", "es", "su", "sus", "al", "lo",
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "nor",
    "on", "at", "by", "of", "to", "from", "in", "out", "over", "under",
}

# Stopwords mínimas EN para la variante _en (solo palabras funcionales).
_ANCHOR_STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "by",
    "for", "from", "with", "about", "into", "over", "under", "up", "down",
    "out", "off", "is", "are", "was", "were", "be", "been", "its", "it",
    "that", "this", "these", "those", "all",
}


def _anchor_stopwords(language: str) -> set:
    """Stopwords por idioma: ES reutiliza research._STOPWORDS_ES (mismo criterio
    que §17 #11); EN usa la lista mínima local _ANCHOR_STOPWORDS_EN."""
    if str(language or "").lower().startswith("en"):
        return _ANCHOR_STOPWORDS_EN
    try:
        from modules.research.main import _STOPWORDS_ES  # reuso, sin duplicar

        return _STOPWORDS_ES
    except Exception:  # noqa: BLE001 - defensa: research ausente
        return _STOPWORDS_ES_FALLBACK


def _has_anchor_keyword_img(topic: Optional[str], cand: dict, language: str = "es") -> bool:
    """True si el candidato de imagen se ancla al tema ``topic`` usando las
    keywords del ``language`` indicado.

    Mismo umbral que research (_has_anchor_keyword): 1 keyword si el tema tiene
    una sola palabra útil, >=2 hits para temas multi-palabra. Haystack:
    title + snippet + content del candidato normalizado por image_search
    (título de página / URL fuente / URL de imagen)."""
    if not topic:
        return True
    stop = _anchor_stopwords(language)
    topic_keywords = [
        w for w in re.findall(r"\w+", str(topic).lower())
        if len(w) >= 2 and w not in stop
    ]
    if not topic_keywords:
        return True
    haystack_words = set(re.findall(
        r"\w+",
        str(cand.get("title") or "").lower() + " " +
        str(cand.get("snippet") or "").lower() + " " +
        str(cand.get("content") or "").lower(),
    ))
    hits = sum(1 for w in topic_keywords if w in haystack_words)
    if len(topic_keywords) == 1:
        return hits >= 1
    return hits >= 2


def _search_query_en(data: dict) -> str:
    """Query determinista en inglés nativo para la variante *_en.

    Prioridad: title_en > chapter_title_en > chapter_text_en (primeras
    palabras) > cadena genérica EN. NUNCA cae al título/texto en español:
    era exactamente la causa del mismatch de idioma de book_67."""
    for field in ("title_en", "chapter_title_en"):
        value = str(data.get(field) or "").strip()
        if value:
            return value[:200]
    text = str(data.get("chapter_text_en") or "").strip()
    words = [w for w in re.split(r"\s+", text) if w.strip()]
    if words:
        return " ".join(words[:12])[:200]
    return "book illustration"


_CAPABILITY_LANGUAGES = {
    "search_chapter_images_es": "es",
    "search_chapter_images_en": "en",
}





# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------
def search_chapter_images(payload: dict, language: Optional[str] = None) -> dict:
    """Busca hasta ``num_images`` imágenes web para un capítulo y las persiste.

    Payload: book_id, chapter_number, chapter_title, chapter_text, num_images,
    language (+ title_en/chapter_text_en/topic_en para la variante *_en).

    El parámetro ``language`` (routing desde capability *_es/*_en) tiene
    prioridad sobre payload.language.

    Devuelve el MISMO shape que ``generate_image`` (image_generator), para ser
    plug-compatible sin adaptadores.
    """
    data = dict(payload or {})
    lang_norm = str(language or data.get("language") or "es").lower()
    lang_is_en = lang_norm.startswith("en")
    book_id = int(data.get("book_id", 0))
    chapter_number = int(data.get("chapter_number", 0))
    language = str(lang_norm)[:10]
    chapter_title = data.get("chapter_title")
    chapter_text = data.get("chapter_text") or ""
    topic = data.get("topic")

    _num = data.get("num_images")
    num_images = int(_num) if _num is not None else 3
    num_images = max(0, min(num_images, MAX_IMAGES))

    images_dir = _images_dir(book_id, chapter_number)
    # Query nativa por idioma (§17 #28): ES usa el comportamiento histórico;
    # EN usa campos nativos EN (title_en etc.) si existen.
    if lang_is_en:
        query = _search_query_en(data)
    else:
        query = _search_query(chapter_title, chapter_text)

    # §17 #24: para libros EN se acota SearXNG por idioma; cualquier otro
    # idioma/ausente mantiene el comportamiento histórico (sin filtro).
    searxng_language = "en" if language.lower().startswith("en") else None
    raw_results = _searxng_search(query, language=searxng_language)

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

        # §17 #28 — filtro de relevancia temática por idioma nativo: cada
        # variante capability (_es/_en) ancla contra keywords en SU idioma,
        # sin reutilizar _has_anchor_keyword de research (texto rico
        # monolingüe; causaba descartes masivos de candidatos on-topic EN con
        # topic ES, caso book_67). Mismo umbral que research.
        # Variante EN: el ancla es SOLO topic_en — NUNCA title_en, porque este
        # puede llevar el fallback ES de §17 #21 (chapters.title_en NULL →
        # chapters.title) y un título español como anchor EN no matchea nunca
        # candidatos EN (segundo bug book_67). topic_en="" => fail-open (no
        # filtra), ver §17 #28(b).
        anchor_topic: Optional[str] = None
        anchor_lang = "es"
        if lang_is_en:
            anchor_topic = str(data.get("topic_en") or "").strip() or None
            anchor_lang = "en"
        else:
            # Variante ES: comportamiento histórico intacto (topic del libro).
            anchor_topic = topic
        if anchor_topic:
            page_fetch_url = item.get("url") or item.get("parsed_url") or ""
            _cand = {
                "title": item.get("title") or "",
                "snippet": page_fetch_url,
                "content": img_src,
            }
            if not _has_anchor_keyword_img(str(anchor_topic), _cand, anchor_lang):
                logger.warning(
                    "image_search: resultado descartado por no anclarse al tema (%s [%s]): %s",
                    anchor_topic, anchor_lang, page_fetch_url or img_src,
                )
                continue

        # §17 #5 — denylist de dominios: revisa AMBAS URLs (img_src + página fuente)
        # antes de descargar. SearXNG expone la URL de la página fuente en el
        # campo ``url`` del resultado (también presente como ``parsed_url`` en
        # algunos proveedores/motores). Si CUALQUIERA de las dos es denylisted,
        # se salta el resultado sin ocupar slot ni error-slot.
        page_url = item.get("url") or item.get("parsed_url") or ""
        blocked_domain = (
            _is_denylisted(img_src) and "img_src"
            or (_is_denylisted(page_url) and "page_url")
            or None
        )
        if blocked_domain:
            logger.warning(
                "image_search: resultado bloqueado por denylist (vía %s): %s",
                blocked_domain,
                page_url or img_src,
            )
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

        # Validar que los bytes descargados sean una imagen decodificable (PIL)
        # ANTES de escribir el archivo ni marcarlo status="ok". Previene persistir
        # HTML de error o contenido truncado como .png válido (bug real book_id=31:
        # 5 archivos ~4KB/430B con status="ok" que PIL no podía abrir).
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(data_bytes)) as _im:
                _im.verify()
        except Exception as exc:  # noqa: BLE001 - contenido inválido: fallo de descarga
            logger.warning("image_search: contenido de imagen inválido %s: %s", img_src, exc)
            results.append(
                _error_meta(image_id, query, images_dir, f"invalid image content: {exc}", source_url=img_src)
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
    """Wrapper de ejecución acorde a la convención del proyecto.

    Capabilities soportadas: search_chapter_images (legacy, idioma del payload),
    search_chapter_images_es y search_chapter_images_en (§17 #28, idioma nativo).
    """
    lang = _CAPABILITY_LANGUAGES.get(capability)
    if lang is not None:
        return search_chapter_images(payload, language=lang)
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


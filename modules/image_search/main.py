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

import base64
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
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
DOWNLOAD_TIMEOUT = float(os.environ.get("IMAGE_DOWNLOAD_TIMEOUT", "20"))
MAX_IMAGES = 20
# §17 #30 — presupuesto total (s) de la fase de búsqueda + techo de páginas de
# SearXNG. Mismo patrón env-overridable que WRITER_TOTAL_TIME_BUDGET /
# RESEARCH_TOTAL_TIME_BUDGET. El budget deja ~40% de holgura bajo el
# timeout_seconds=170 del scheduler (modules/image_search/module.json).
IMAGE_SEARCH_TOTAL_TIME_BUDGET = float(
    os.environ.get("IMAGE_SEARCH_TOTAL_TIME_BUDGET", "90")
)
IMAGE_SEARCH_MAX_PAGES = int(os.environ.get("IMAGE_SEARCH_MAX_PAGES", "6"))

# Resultados por página pedidos a SearXNG (param nativo `per_page`). El default
# histórico del meta-buscador es ~10; con 20 se duplican los candidatos por
# request y se necesita menos paginación para el mismo techo de candidatos
# (book_84: déficit en los 20 capítulos por escasez de candidatos útiles).
SEARXNG_PER_PAGE = int(os.environ.get("IMAGE_SEARCH_PER_PAGE", "20"))

# §17 #48 Fase 4 — verificación semántica VLM del candidato seleccionado.
# DEFAULT DESACTIVADO: con el flag a 0 el módulo se comporta EXACTAMENTE igual
# que antes de Fase 4 (cero llamadas de red, cero coste). Mismo patrón
# env-overridable que el resto de constantes del módulo.
VLM_VERIFICATION_ENABLED = os.environ.get("VLM_VERIFICATION_ENABLED", "0").strip().lower() in (
    "1", "true", "yes", "on",
)
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME", "moondream-local")
VLM_TIMEOUT_SECONDS = float(os.environ.get("VLM_TIMEOUT_SECONDS", "15"))
# Servidor Ollama local (single-server, pipeline serial: sin riesgo de
# contención con chapter_writer, confirmado en el diagnóstico de Fase 4).
VLM_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")

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

# §17 #30 — formatos NO-raster que PIL no puede abrir como imagen normal (SVG
# vectorial). Descartarlos ANTES de descargar ahorra DOWNLOAD_TIMEOUT de HTTP
# en candidatos que luego se tirarían por contenido inválido. Ampliable si
# aparecen más formatos problemáticos (p.ej. .ico/.tif no soportados).
_NON_RASTER_EXTENSIONS = {".svg"}
# §17 #48 fix Cambio B: longitud máxima (en palabras) de la query de búsqueda
# final, y número máximo de keywords salientes extraídas del chapter_text. La
# query combina topic+heading+book_topic+keywords, pero se acorta si supera el
# umbral para no abrumar a SearXNG con queries gigantes.
IMAGE_QUERY_MAX_WORDS = int(os.environ.get("IMAGE_QUERY_MAX_WORDS", "12"))
_MAX_QUERY_KEYWORDS = 4

# §17 #48 Cambio C — checks de calidad de la imagen descargada antes de
# aceptarla (dimensiones mínimas y aspect ratio razonable, para descartar
# thumbnails/iconos/banners que SearXNG coló como candidatos). Env-overridable,
# mismo estilo del resto de constantes del módulo.
IMAGE_MIN_WIDTH = int(os.environ.get("IMAGE_MIN_WIDTH", "400"))
IMAGE_MIN_HEIGHT = int(os.environ.get("IMAGE_MIN_HEIGHT", "300"))
# Rango de aspect ratio (w/h) aceptado: descarta tiras muy alargadas o banners.
IMAGE_MAX_ASPECT_RATIO = float(os.environ.get("IMAGE_MAX_ASPECT_RATIO", "3.0"))
IMAGE_MIN_ASPECT_RATIO = 1.0 / IMAGE_MAX_ASPECT_RATIO

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

# Denylist separada de dominios que sirven LIBRERÍAS DE ICONOS/assets de dev
# (SVG vectorial de librerías como devicons, lucide-static, etc.). Nunca dan
# contenido editorial raster válido, pero ocupan slots de intento y agotan el
# margen del ratio (evidencia real: book_72, cdn.jsdelivr.net repetido).
_ICON_LIBRARY_DENYLIST = {
    "cdn.jsdelivr.net",
}


def _is_denylisted(url: Optional[str]) -> bool:
    """True si el dominio de ``url`` contiene algún dominio bloqueado.

    Compara contra ``_DOMAIN_DENYLIST`` (marca/editoriales/copyright) y
    ``_ICON_LIBRARY_DENYLIST`` (CDNs de librerías de iconos, p.ej. jsDelivr).

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
    return any(
        blocked in netloc
        for blocked in _DOMAIN_DENYLIST | _ICON_LIBRARY_DENYLIST
    )


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
# Extracto de keywords salientes del chapter_text (§17 #48 Cambio B)
# ---------------------------------------------------------------------------
def _extract_salient_keywords(
    chapter_text: Optional[str],
    language: Optional[str] = "es",
    max_keywords: int = _MAX_QUERY_KEYWORDS,
) -> list[str]:
    """Extrae hasta ``max_keywords`` palabras/bigramas/n-gramas capitalizados
    del texto del capítulo (candidatos a nombre propio/entidad), excluyendo
    stopwords.

    §17 #48 Cambio B: la query de búsqueda actual (chapter_search_topic +
    topic del libro) no refleja el vocabulario específico del capítulo. Esta
    función extrae los términos capitalizados más frecuentes del
    ``chapter_text`` (sólo nombre propio/biagrama real, no usar el texto
    completo para evitar over-fitting a la redacción del draft).

    Fail-safe: devuelve [] si chapter_text es None/vacío, si no hay candidatos
    claros, o si ocurre cualquier error — NUNCA lanza excepción.
    NO usa LLM: regex + conteo de frecuencia (mismo patrón de stopwords que
    _anchor_stopwords, reutilizando _STOPWORDS_ES de research).
    """
    try:
        text = str(chapter_text or "").strip()
        if not text:
            return []
        stop = _anchor_stopwords(language)
        # 1-3 palabras capitalizadas consecutivas (entidad nominal); mayúscula
        # inicial requerida para evitar falsos positivos de lowercase.
        tokens = re.findall(
            r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÑÜ][a-záéíóúüñ]+){0,2}",
            text,
        )
        cleaned: list[str] = []
        for tok in tokens:
            words = tok.split()
            # filtra candidatos cuyo primer token es stopword (p.ej. "La
            # Casona", "El Bosque") para no engañar el ranking con determinante
            # más nombre genérico.
            if words[0].lower() in stop:
                continue
            # ignora palabras de <4 chars (descarto inicial).
            if any(len(w) < 4 for w in words):
                continue
            cleaned.append(tok)
        if not cleaned:
            return []
        freq: dict[str, int] = {}
        for tok in cleaned:
            freq[tok] = freq.get(tok, 0) + 1
        # prioriza frecuencia DESC, luego longitud DESC (entidades más
        # específicas suelen ser más largas), luego orden alfabético por
        # determinismo.
        ranked = sorted(
            freq.items(),
            key=lambda kv: (-kv[1], -len(kv[0].split()), kv[0]),
        )
        return [tok for tok, _ in ranked[:max_keywords]]
    except Exception:  # noqa: BLE001 - defensa fail-safe total
        return []
def _extract_entity_keywords(
    chapter_text: Optional[str],
    language: Optional[str] = "es",
    max_keywords: int = _MAX_QUERY_KEYWORDS,
) -> list[str]:
    """Extrae hasta ``max_keywords`` siglas/consolas/entidades con dígito o
    token compuesto (categoría ``entity_keywords``, SEPARADA de las keywords
    Title-Case genéricas de ``_extract_salient_keywords``).

    §17 imágenes: el capítulo puede mencionar repetidamente "SNES", "PS2",
    "Xbox Series X/S", "Wii U", "N64"... que la extracción Title-Case no
    captura (acrónimos en mayúsculas / sufijos con dígitos o "/"). Esta
    función los detecta de forma determinista (regex, sin NLP) para que la
    query de imagen pueda diferenciar capítulos cuyo ``chapter_search_topic``
    es genérico e idéntico.

    Patrón 1 — sigla/acrónimo en mayúsculas (2-6 letras), opcionalmente con
    1-2 dígitos (SNES, PS2, PS 2, GBA, NES), o 1 letra + 1-2 dígitos (N64):
        ``\\b(?:[A-Z]{2,6}(?:\\s?\\d{1,2})?|[A-Z]{1,2}\\d{1,2})\\b``

    Patrón 2 — secuencia Title-Case (1-3 palabras) con SUFIJO OBLIGATORIO:
    dígito, o letra suelta MAYÚSCULA (opcionalmente "/"letra), para capturar
    consolas tipo "PlayStation 2", "Xbox Series X/S", "Wii U". El sufijo
    obligatorio es lo que distingue "entity" de un nombre propio Title-Case
    plano (ese caso pertenece a ``_extract_salient_keywords``):
        ``[A-ZÁÉÍÓÚÑÜ][a-záéíóúüñ]+(?:\s+[A-ZÁÉÍÓÚÑÜ][a-záéíóúüñ]+){0,2}
        \s+(?:\d{1,2}|[A-ZÁÉÍÓÚÑÜ](?:/[A-ZÁÉÍÓÚÑÜ])?)``

    Filtra stopwords, cuenta frecuencia (mismo ranking: freq DESC, longitud
    DESC, alfabético) y devuelve lista separada. Fail-safe: [] si no hay
    candidatos, texto vacío, o error — NUNCA lanza excepción.
    NO usa LLM: regex + frecuencia, sin dependencias nuevas.
    """
    try:
        text = str(chapter_text or "").strip()
        if not text:
            return []
        stop = _anchor_stopwords(language)
        # Patrón 1: siglas/acrónimos en mayúsculas (con posible dígito) o
        # 1 letra + dígitos (N64).
        pat_acronym = (
            r"\b(?:[A-Z]{2,6}(?:\s?\d{1,2})?|[A-Z]{1,2}\d{1,2})\b"
        )
        # Patrón 2: secuencia Title-Case con SUFIJO OBLIGATORIO (dígito /
        # letra mayúscula suelta / mayúscula"/"mayúscula). La primera palabra
        # permite camelCase interno sin espacios (PlayStation, GameCube,
        # StarCraft) como UN solo token — antes el regex partía "PlayStation"
        # como "Station" de "PlayStation 2". El lookahead (?!\w) evita robar
        # la 1ª letra de la palabra siguiente ("Napoleón B" no se coge).
        pat_camel_or_word = (
            r"[A-ZÁÉÍÓÚÑÜ][a-záéíóúüñ]*(?:[A-ZÁÉÍÓÚÑÜ][a-záéíóúüñ]+)*"
        )
        pat_follow = r"(?:\s+[A-ZÁÉÍÓÚÑÜ][a-záéíóúüñ]+)"
        pat_suffix = r"(?:\d{1,2}|[A-ZÁÉÍÓÚÑÜ](?:/[A-ZÁÉÍÓÚÑÜ])?(?!\w))"
        pat_title = (
            pat_camel_or_word
            + pat_follow + "{0,2}"
            + r"\s+" + pat_suffix
        )
        tokens: list[str] = []
        tokens.extend(re.findall(pat_acronym, text))
        tokens.extend(re.findall(pat_title, text))
        cleaned: list[str] = []
        for tok in tokens:
            words = tok.split()
            if not words:
                continue
            # filtro stopwords sobre el primer token
            if words[0].lower() in stop:
                continue
            # descarta acrónimos de 1 letra en solitario (p.ej. "X"); los que
            # llevan dígito/sufijo ya son capturados por los patrones.
            if len(words) == 1 and len(words[0]) < 2:
                continue
            cleaned.append(tok)
        # §17 PUNTO 1 — numerales romanos aislados (ruido de siglas): se
        # descartan solo cuando son el token EXACTO y completo (whole-match,
        # case-sensitive). Los romanos fusionados en "Metal Gear Solid II" los
        # captura el patrón 2 como una entidad entera (no "II" suelto).
        _ROMANS = {
            "I", "II", "III", "IV", "V", "VI", "VII", "VIII",
            "IX", "X", "XI", "XII",
        }
        cleaned = [tok for tok in cleaned if tok not in _ROMANS]
        if not cleaned:
            return []
        freq: dict[str, int] = {}
        for tok in cleaned:
            freq[tok] = freq.get(tok, 0) + 1
        ranked = sorted(
            freq.items(),
            key=lambda kv: (-kv[1], -len(kv[0].split()), kv[0]),
        )
        return [tok for tok, _ in ranked[:max_keywords]]
    except Exception:  # noqa: BLE001 - defensa fail-safe total
        return []


def _language_hint(*maybe_texts: Optional[str]) -> str:
    """Inferencia mínima de idioma para elegir stopwords al extraer keywords.

    §17 #48 Cambio B: la query ES/EN se construye en image_search con un solo
    código ``language`` del payload (p.ej. "es"/"en"), pero el helper
    _search_query no siempre recibe el language. Aquí se infiere heurísticamente
    a partir de los textos que aparecen en el payload (título del libro,
    search_topic): si alguno contiene "el/la/los" → "es", si contiene "the/a/an"
    → "en", por defecto "es". Fail-safe: siempre devuelve un código usable por
    _extract_salient_keywords y _anchor_stopwords.
    """
    try:
        combined = " ".join(str(t or "") for t in maybe_texts).lower()
        if re.search(r"\bl[ao]s?\b|\b(de|del|que|con)\b", combined):
            return "es"
        if re.search(r"\b(the|a|an|of|in|and|is|are)\b", combined):
            return "en"
        return "es"
    except Exception:  # noqa: BLE001
        return "es"


# ---------------------------------------------------------------------------
# §17 #30 (P1a, book_72) — dedupe cross-chapter por hash de contenido
# ---------------------------------------------------------------------------
def _content_hashes_path(book_id: int) -> str:
    """Ruta del registro de hashes de contenido de imágenes del libro.

    Vive junto a los directorios per-chapter (``<root>/books/<id>/chapters/``).
    Cada capítulo es una invocación independiente del módulo (task del
    scheduler), así que NO hay set en memoria compartido entre capítulos: el
    registro se persiste en disco y se lee/actualiza por invocación.
    """
    root = Path(_storage_root())
    return str(root / "books" / str(book_id) / "chapters" / "_content_hashes.json")


def _load_content_hashes(book_id: int) -> dict:
    """Carga el registro hash→capítulo del libro (fail-safe: {} si no existe)."""
    try:
        with open(_content_hashes_path(book_id), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - sin registro previo / corrupto: empezar limpio
        return {}


def _save_content_hashes(book_id: int, mapping: dict) -> None:
    """Persiste el registro (best-effort: un fallo de I/O no aborta el lote)."""
    try:
        path = _content_hashes_path(book_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "image_search: no se pudo persistir el registro de hashes: %s", exc
        )


# ---------------------------------------------------------------------------
# Query de búsqueda
# ---------------------------------------------------------------------------
def _search_query(
    chapter_title: Optional[str],
    chapter_text: Optional[str],
    search_topic: Optional[str] = None,
    book_topic: Optional[str] = None,
) -> str:
    """Construye la query de búsqueda: combinación tema-libro + heading del
    capítulo + keywords salientes del chapter_text.

    §17 #30 (P1b, book_72): si el payload trae ``search_topic`` (primer heading
    usable del outline del capítulo, u objective — resuelto en autopilot), se
    usa ESE tema. ``book_topic`` (título del libro, §17 #49 fix) se
    combina aditivamente cuando no está ya contenido en el tema/título.

    §17 #48 Cambio B: se AÑADEN (no reemplazan) hasta
    ``_MAX_QUERY_KEYWORDS`` keywords salientes extraídas de ``chapter_text``
    (véase ``_extract_salient_keywords``) para reflejar el vocabulario
    específico del capítulo. Las keywords se añaden AL FINAL, evitando
    duplicar palabras ya presentes (case-insensitive) y acortando la query si
    supera ``IMAGE_QUERY_MAX_WORDS`` manteniendo el base íntegro.
    Fail-safe: si no hay keywords/chapter_text, la query deja igual que el
    comportamiento histórico (topic > title > chapter_text truncado).
    """
    # Query base histórica (topic > title > chapter_text truncado).
    search_topic = str(search_topic or "").strip()
    if search_topic:
        base = search_topic
        lang_hint = _language_hint(chapter_title, search_topic)
    else:
        title = (chapter_title or "").strip()
        if title:
            base = title
        else:
            text = (chapter_text or "").strip()
            words = [w for w in re.split(r"\s+", text) if w.strip()]
            base = (" ".join(words[:12])[:200] if words else "book illustration")
        lang_hint = _language_hint(chapter_title, book_topic)

    # §17 #49 fix: combina el topic/título del libro (no sólo el del capítulo).
    book_topic = str(book_topic or "").strip()
    if book_topic and book_topic.lower() not in base.lower():
        _combined = f"{base} {book_topic}"
        if len(_combined.split()) <= IMAGE_QUERY_MAX_WORDS:
            base = _combined

    # §17 #48 Cambio B: keywords salientes del chapter_text.
    base_words = {w.lower() for w in base.split()}
    keywords = [
        kw
        for kw in _extract_salient_keywords(chapter_text, lang_hint)
        if kw.lower() not in base_words
    ]
    # §17 imagenes: entity_keywords (siglas/consolas con dígito o "/") se
    # priorizan sobre las genéricas. Se les reserva un presupuesto propio de
    # hasta ENTITY_BUDGET palabras antes de rellenar con las genéricas,
    # respetando siempre el límite total IMAGE_QUERY_MAX_WORDS.
    entity_keywords = [
        kw
        for kw in _extract_entity_keywords(chapter_text, lang_hint)
        if kw.lower() not in base_words
    ]
    if keywords or entity_keywords:
        spare = max(0, IMAGE_QUERY_MAX_WORDS - len(base.split()))
        chosen: list[str] = []
        used = 0
        entity_budget = min(2, spare)  # presupuesto propio de entidades
        entity_chosen, entity_used = [], 0
        for kw in entity_keywords:
            kw_words = len(kw.split())
            if entity_used + kw_words <= entity_budget:
                entity_chosen.append(kw)
                entity_used += kw_words
            if entity_used >= entity_budget:
                break
        chosen.extend(entity_chosen)
        used = entity_used
        # genéricas en el espacio restante, sin duplicar palabras ya usadas
        used_words = base_words | {w.lower() for k in chosen for w in k.split()}
        for kw in keywords:
            kw_words = len(kw.split())
            if used + kw_words <= spare and not (
                any(w.lower() in used_words for w in kw.split())
            ):
                chosen.append(kw)
                used += kw_words
                used_words |= {w.lower() for w in kw.split()}
        candidate = f"{base} {' '.join(chosen)}" if chosen else base
        return candidate[:200]
    return base[:200]


# ---------------------------------------------------------------------------
# Llamadas HTTP a SearXNG (resilientes)
# ---------------------------------------------------------------------------
# §17 #48 Fase 3 — resiliencia ante rate-limiting de SearXNG (caso book_76:
# 0 imágenes por rate-limit indistinguible de "sin resultados reales").
# Constantes env-overridable, mismo estilo del módulo.
SEARXNG_MAX_RETRIES = int(os.environ.get("SEARXNG_MAX_RETRIES", "3"))
# Base (s) del backoff exponencial con jitter: espera = base * 2**intento + jitter.
SEARXNG_BACKOFF_BASE = float(os.environ.get("SEARXNG_BACKOFF_BASE", "1.0"))


def _searxng_fetch(
    query: str,
    language: Optional[str] = None,
    pageno: int = 1,
    deadline: Optional[float] = None,
) -> tuple[list, str]:
    """Consulta SearXNG con reintentos diferenciados por causa.

    §17 #48 Fase 3. Devuelve ``(results, status)`` con status:
    - "ok": respuesta HTTP válida (aunque traiga 0 resultados — un
      0-resultados REAL no es rate-limit; el caller lo distingue).
    - "rate_limited": HTTP 429 y se agotaron los reintentos
      (SEARXNG_MAX_RETRIES) o el backoff no cabía en el budget restante.
    - "error": error de red/conexión/servidor o timeout → falla rápido
      SIN bucle de reintentos infinito.

    ``deadline`` (opcional, time.monotonic()): el backoff NUNCA puede hacer
    que la fase se pase del budget total (IMAGE_SEARCH_TOTAL_TIME_BUDGET);
    si la espera excedería el deadline, corta y degrada a "rate_limited".
    Nunca lanza excepción no controlada.
    """
    import random

    params_base: dict[str, str] = {
        "q": query,
        "categories": "images",
        "format": "json",
        "pageno": str(pageno),
        "per_page": str(SEARXNG_PER_PAGE),
    }
    if language:
        # §17 #24: acota resultados por idioma (SearXNG soporta el param
        # nativo `language`; default histórico = sin filtro).
        params_base["language"] = language

    last_error: Optional[str] = None
    _timeout_retries = 0  # §17 #48 Fase 3: timeout → retry SIMPLE (1 reintento inmediato, sin backoff)
    for attempt in range(SEARXNG_MAX_RETRIES):
        try:
            resp = requests.get(
                SEARXNG_URL.rstrip("/") + "/search",
                params=params_base,
                timeout=SEARCH_TIMEOUT,
                headers={"User-Agent": _USER_AGENT},
            )
            resp.raise_for_status()
            data = resp.json()
            return list(data.get("results") or []), "ok"
        except requests.exceptions.Timeout as exc:
            # Timeout de red: 1 reintento inmediato (sin backoff, acotado);
            # si ya se usó, falla rápido — sin bucle de reintentos.
            if _timeout_retries < 1:
                _timeout_retries += 1
                logger.warning(
                    "image_search: SearXNG timeout en página %d (retry simple %d/1): %s",
                    pageno, _timeout_retries, exc,
                )
                continue
            logger.warning("image_search: SearXNG timeout persistente en página %d: %s", pageno, exc)
            return [], "error"
        except requests.exceptions.RequestException as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) == 429:
                last_error = "HTTP 429 (rate limit)"
                wait = SEARXNG_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.5)
                if deadline is not None and time.monotonic() + wait > deadline:
                    logger.warning(
                        "image_search: rate limit (429) y el backoff (%.1fs) "
                        "excede el budget restante: cortando sin más reintentos",
                        wait,
                    )
                    return [], "rate_limited"
                logger.warning(
                    "image_search: HTTP 429 en página %d (intento %d/%d): backoff %.1fs",
                    pageno, attempt + 1, SEARXNG_MAX_RETRIES, wait,
                )
                if attempt >= SEARXNG_MAX_RETRIES - 1:
                    # Último intento agotado: no dormir inútilmente (no hay retry
                    # posterior); degrada ya a rate_limited.
                    break
                time.sleep(wait)
                continue
            # Conexión caída / HTTP != 429: NO reintentar en bucle — fail fast
            # con log claro (servidor caído no se arregla reintentando).
            logger.warning(
                "image_search: SearXNG error de red/HTTP (página %d): %s", pageno, exc
            )
            return [], "error"
        except Exception as exc:  # noqa: BLE001 - nunca abortar el lote
            logger.warning("image_search: SearXNG error inesperado: %s", exc)
            return [], "error"
    logger.warning(
        "image_search: rate limit persistente tras %d reintentos (último: %s): "
        "página %d marcada rate_limited (≠ 0-resultados real)",
        SEARXNG_MAX_RETRIES, last_error, pageno,
    )
    return [], "rate_limited"


def _searxng_search(
    query: str, language: Optional[str] = None, pageno: int = 1
) -> list[dict]:
    """Consulta SearXNG y devuelve la lista de resultados ([] si falla, sin excepción).

    ``language`` (opcional): código de idioma SearXNG ("en", "es", ...). Si es
    None se comporta como históricamente (sin filtro de idioma en la request).
    ``pageno`` (§17 #30): número de página de resultados (SearXNG soporta el
    param nativo `pageno`; default 1 = comportamiento histórico).
    """
    try:
        params: dict[str, str] = {
            "q": query,
            "categories": "images",
            "format": "json",
            "pageno": str(pageno),
            "per_page": str(SEARXNG_PER_PAGE),
        }
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
    """Extensión REAL de la URL (vía ``_url_extension``) o ".png" solo si la
    URL no tiene extensión (necesario para nombrar el archivo en disco).

    §17 #30 fix logging: antes devolvía ".png" como default INCLUSO cuando la
    URL sí tenía extensión (p.ej. .svg), haciendo que los logs de descarte
    mintieran sobre la extensión real. Ahora la extensión real manda; el
    fallback ".png" aplica únicamente a URLs sin extensión en la ruta.
    """
    return _url_extension(url) or ".png"


def _url_extension(url: str) -> Optional[str]:
    """Extensión (con punto, minúsculas) de la ruta de ``url``, o None si no tiene."""
    try:
        return os.path.splitext(urllib.parse.urlparse(url).path)[1].lower() or None
    except Exception:  # noqa: BLE001
        return None


def _is_non_raster(url: str) -> bool:
    """True si la extensión de ``url`` es un formato no-raster (p.ej. .svg).

    §17 #30: permiten descartar el candidato SIN hacer la petición HTTP, porque
    los bytes serían ilegibles por PIL como imagen normal. Evita gastar
    DOWNLOAD_TIMEOUT en una descarga que se descartaría por contenido inválido.
    """
    return _url_extension(url) in _NON_RASTER_EXTENSIONS


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


def _score_candidate(candidate: dict, keywords: list[str]) -> float:
    """§17 #48 Fase 2 — score de METADATA para el ranking best-first.

    Componentes (pesos, ajustables sin tocar la firma):
    - solapamiento de ``keywords`` (las de Cambio B, pasada como parámetro)
      contra el texto disponible del candidato (title/url/img_src): peso 2.0.
      Sin keywords (fail-safe de Cambio B) → 0.5 neutro.
    - proximidad de resolución al área objetivo de ilustración de capítulo
      (~1024x768; las constantes IMAGE_MIN_* de Fase 1 actúan de suelo vía
      quality check, aquí solo se ORDENA): peso 1.0; sin resolution → 0.5.
    - proximidad de aspect ratio al rango fotográfico estándar 1.33-1.78
      (dentro del rango ya validado por el quality check): peso 0.5.

    TODO (no implementado a medias): penalización por dominio ya usado en
    capítulos anteriores del mismo libro — el registro disponible en este
    punto (_hash_registry) es por CONTENIDO (sha1 post-descarga), no por
    dominio; requeriría un registro previo de dominios que no existe hoy.
    """
    text = " ".join(
        str(candidate.get(k) or "")
        for k in ("title", "url", "img_src", "thumbnail_src")
    ).lower()
    if keywords:
        hits = sum(1 for kw in keywords if kw.lower() in text)
        kw_score = hits / len(keywords)
    else:
        kw_score = 0.5

    res_score = 0.5
    ar_score = 0.5
    m = re.match(
        r"(\d+)\s*[x×]\s*(\d+)", str(candidate.get("resolution") or "")
    )
    if m:
        try:
            w, h = int(m.group(1)), int(m.group(2))
        except (TypeError, ValueError):
            w = h = 0
        if w > 0 and h > 0:
            area = w * h
            target = 1024 * 768
            res_score = min(area, target) / max(area, target)
            ar = w / h
            lo, hi = 1.33, 1.78
            if lo <= ar <= hi:
                ar_score = 1.0
            else:
                dist = min(abs(ar - lo), abs(ar - hi)) / ar
                ar_score = max(0.0, 1.0 - dist)

    return 2.0 * kw_score + 1.0 * res_score + 0.5 * ar_score


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
# §17 #48 Cambio C — checks de calidad de la imagen descargada
# ---------------------------------------------------------------------------
def _vlm_answer_is_yes(answer: str) -> bool:
    """Parseo ROBUSTO de la respuesta del VLM (case-insensitive, sin acentos,
    tolera espacios/puntuación inicial). Fail-open: respuesta ambigua → True
    (mismo espíritu de resiliencia que el resto del módulo)."""
    text = unicodedata.normalize("NFKD", (answer or "").strip())
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"^[\W_]+", "", text)  # espacios/puntuación inicial
    if text.startswith(("si", "yes")):
        return True
    if text.startswith("no"):
        return False
    return True  # ambiguo/irreconocible → fail-open


def _verify_image_relevance(
    image_bytes: Optional[bytes],
    topic: str,
    keywords: Optional[list[str]] = None,
    deadline: Optional[float] = None,
) -> bool:
    """§17 #48 Fase 4 — verificación semántica VLM del candidato ya seleccionado.

    - Flag VLM_VERIFICATION_ENABLED=0 (DEFAULT): devuelve True inmediatamente
      (no-op, cero llamadas de red, comportamiento idéntico al pre-Fase-4).
    - Flag activo: llama a Ollama /api/generate (multimodal, images=[base64])
      con prompt determinista SI/NO. Respuesta "NO" → False (el loop de Fase 2
      descarta ese candidato y prueba el siguiente del pool).
    - NUNCA lanza excepción y NUNCA descarta por fallo del VLM: timeout/error
      de Ollama o presupuesto agotado → fail-open (True), loggeado como warning.
      El fallo del VLM en sí no debe descartar un candidato que ya pasó todos
      los filtros anteriores.
    """
    if not VLM_VERIFICATION_ENABLED:
        return True

    try:
        timeout = VLM_TIMEOUT_SECONDS
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning(
                    "image_search: sin presupuesto para verificación VLM "
                    "(%.2fs restantes): fail-open (True)",
                    remaining,
                )
                return True
            # La llamada VLM respeta el deadline global de la fase: nunca puede
            # consumir más que el presupuesto restante (mismo patrón que
            # _searxng_fetch de Fase 3).
            timeout = min(timeout, remaining)

        kw_ctx = ""
        if keywords:
            kw_ctx = " Temas clave: " + ", ".join(list(keywords)[:5]) + "."
        prompt = (
            f"¿Esta imagen es relevante para el tema: {topic}?{kw_ctx} "
            "Responde únicamente SI o NO."
        )
        b64 = base64.b64encode(image_bytes or b"").decode("ascii")
        resp = requests.post(
            f"{VLM_BASE_URL}/api/generate",
            json={
                "model": VLM_MODEL_NAME,
                "prompt": prompt,
                "images": [b64],
                "stream": False,
                "options": {"num_predict": 8},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        answer = str((resp.json() or {}).get("response") or "")
        ok = _vlm_answer_is_yes(answer)
        if not ok:
            logger.info(
                "image_search: VLM descarta candidato (respuesta=%r)",
                answer.strip()[:80],
            )
        return ok
    except Exception as exc:  # noqa: BLE001 - fail-open: el VLM nunca bloquea la fase
        logger.warning(
            "image_search: verificación VLM no disponible (fail-open, True): %s",
            exc,
        )
        return True


def _passes_quality_check(image_bytes: Optional[bytes]) -> bool:
    """True si la imagen cumple los checks mínimos de calidad editorial.

    §17 #48 Cambio C: SearXNG devuelve thumbnails/iconos/banners que ocupan
    slots sin aportar valor de ilustración de capítulo. Checks (todos
    env-overridable vía constantes del módulo):
      - dimensiones mínimas (IMAGE_MIN_WIDTH x IMAGE_MIN_HEIGHT, defaults
        400x300: ilustración de capítulo, no thumbnail/icono);
      - aspect ratio (w/h) en rango [1/MAX, MAX] (defaults 0.4..3.0: descarta
        tiras muy alargadas y banners).
    Fail-safe total: bytes inválidos/corruptos/formato no soportado → False
    (falla el check, NUNCA lanza excepción no controlada).
    """
    if not image_bytes:
        return False
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as im:
            width, height = im.size
        if width < IMAGE_MIN_WIDTH or height < IMAGE_MIN_HEIGHT:
            return False
        ratio = width / float(height)
        return IMAGE_MIN_ASPECT_RATIO <= ratio <= IMAGE_MAX_ASPECT_RATIO
    except Exception:  # noqa: BLE001 - corrupta/formato no soportado: falla el check
        return False

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
    era exactamente la causa del mismatch de idioma de book_67.

    §17 #48 Cambio B: añade (no reemplaza) hasta ``_MAX_QUERY_KEYWORDS``
    keywords salientes de ``chapter_text_en`` (misma mecánica fail-safe que
    _search_query), sin duplicar palabras ya presentes y acotada a
    ``IMAGE_QUERY_MAX_WORDS`` palabras.
    """
    base: Optional[str] = None
    for field in ("chapter_search_topic", "title_en", "chapter_title_en"):
        value = str(data.get(field) or "").strip()
        if value:
            base = value
            break
    text = str(data.get("chapter_text_en") or "").strip()
    if base is None:
        words = [w for w in re.split(r"\s+", text) if w.strip()]
        base = (" ".join(words[:12])[:200] if words else "book illustration")

    # §17 #48 Cambio B: keywords salientes del texto EN (stopwords EN).
    keywords = [
        kw
        for kw in _extract_salient_keywords(text or None, "en")
        if kw.lower() not in {w.lower() for w in base.split()}
    ]
    if keywords:
        candidate = f"{base} {' '.join(keywords)}"
        words = candidate.split()
        if len(words) > IMAGE_QUERY_MAX_WORDS:
            spare = max(0, IMAGE_QUERY_MAX_WORDS - len(base.split()))
            candidate = f"{base} {' '.join(keywords[:spare])}" if spare else base
        return candidate[:200]
    return base[:200]


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
    # §17 #30 (P1a): registro de hashes de contenido del libro. Cada entrada
    # sha1 -> {"chapter": N, "path": "..."} permite descartar la misma imagen
    # física cuando aparece en OTRO capítulo del mismo libro.
    _hash_registry = _load_content_hashes(book_id)
    # §17 #48 Fase 2 — keywords salientes para el RANKING de candidatos: se
    # calculan UNA sola vez (mismas que Cambio B usa para la query) y se pasan
    # como parámetro a _score_candidate (no se recalculan por candidato).
    _rank_lang = "en" if lang_is_en else "es"
    _rank_keywords = _extract_salient_keywords(chapter_text, _rank_lang)
    # Query nativa por idioma (§17 #28): ES usa el comportamiento histórico;
    # EN usa campos nativos EN (title_en etc.) si existen.
    if lang_is_en:
        query = _search_query_en(data)
    else:
        query = _search_query(
            chapter_title,
            chapter_text,
            search_topic=data.get("chapter_search_topic"),
            book_topic=topic,
        )

    # §17 #24: para libros EN se acota SearXNG por idioma; cualquier otro
    # idioma/ausente mantiene el comportamiento histórico (sin filtro).
    searxng_language = "en" if language.lower().startswith("en") else None

    results: list[dict] = []
    requested = num_images
    generated = 0
    failed = 0

    # Cada slot pedido se resuelve con el siguiente resultado útil; si no hay
    # más resultados o la descarga falla, ese slot queda en status=error sin
    # abortar el lote (patrón research/writer).
    slot = 0
    # §17 #30 — PAGINACIÓN con presupuesto: se piden páginas sucesivas a
    # SearXNG (param nativo `pageno`) hasta completar el cupo, agotar el
    # presupuesto de tiempo, llegar al techo de páginas o quedarse sin
    # resultados nuevos. NUNCA se rinde en el primer lote (antes se cortaba
    # con shortfall y la compensación IA de autopilot tapaba el déficit).
    _start = time.monotonic()
    page = 1
    seen_urls: set[str] = set()
    # §17 #30 — bandera para cortar limpio si el presupuesto se agota a mitad
    # de una página (después de procesar algunos candidatos de la misma). Se
    # setea dentro del loop de candidatos y se lee tras el loop interior para
    # romper también el loop de páginas y pasar directo al relleno final.
    _budget_exhausted = False
    # §17 #48 Fase 3 — bandera de rate-limiting: si SearXNG devolvió 429
    # persistente, el relleno final marca los slots con "rate_limited"
    # (≠ "no_results") para diferenciarlo de un 0-resultados real.
    _rate_limited = False
    # §17 #48 Fase 4 — contador de candidatos probados desde la última
    # aceptación de slot; se persiste como vlm_candidates_tried (trazabilidad
    # de cuántos descartes de VLM hubo antes de aceptar cada imagen).
    _vlm_candidates_tried = 0
    while slot < requested:
        if time.monotonic() - _start >= IMAGE_SEARCH_TOTAL_TIME_BUDGET:
            logger.warning(
                "image_search: presupuesto agotado (%.1fs >= %.1fs) tras %d "
                "página(s): shortfall=%d no cubierto",
                time.monotonic() - _start,
                IMAGE_SEARCH_TOTAL_TIME_BUDGET,
                page,
                requested - slot,
            )
            break
        if page > IMAGE_SEARCH_MAX_PAGES:
            logger.warning(
                "image_search: techo de páginas alcanzado (%d): shortfall=%d "
                "no cubierto",
                IMAGE_SEARCH_MAX_PAGES,
                requested - slot,
            )
            break
        # §17 #48 Fase 3 — llamada con reintentos diferenciados por causa y
        # deadline del budget total: el backoff NUNCA puede superar el
        # presupuesto de la fase (si no cabría, degrada a rate_limited ya).
        page_results, _fetch_status = _searxng_fetch(
            query,
            language=searxng_language,
            pageno=page,
            deadline=_start + IMAGE_SEARCH_TOTAL_TIME_BUDGET,
        )
        if _fetch_status == "rate_limited":
            # Señal aditiva (§17 #48 Fase 3): la página quedó sin resultados
            # por rate-limiting de SearXNG (≠ 0-resultados real). Se propaga
            # al relleno final vía campo `error` (string libre en schema) y
            # se corta la paginación: pedir más páginas a un SearXNG
            # rate-limited solo quemaría budget.
            _rate_limited = True
            logger.warning(
                "image_search: página %d marcada rate_limited (≠ no_results): "
                "cortando paginación con shortfall=%d",
                page,
                requested - slot,
            )
            break

        # Dedupe entre páginas por la MISMA clave que ya usa el filtrado:
        # img_src (clave de descarga) y url de la página fuente (clave de
        # denylist). Un candidato ya visto en una página anterior no se
        # re-procesa. Si la página no aporta NADA nuevo, no hay más resultados
        # → fin de la paginación.
        fresh: list[dict] = []
        for item in page_results:
            img_src = item.get("img_src") or item.get("thumbnail_src") or ""
            page_url = item.get("url") or item.get("parsed_url") or ""
            keys = {k for k in (img_src, page_url) if k}
            if keys and keys <= seen_urls:
                continue
            seen_urls.update(keys)
            fresh.append(item)
        if not fresh:
            logger.info(
                "image_search: página %d sin candidatos nuevos (%d brutos, "
                "%d ya vistos): fin de paginación con shortfall=%d",
                page,
                len(page_results),
                len(page_results) - len(fresh),
                requested - slot,
            )
            break

        # §17 #48 Fase 2 — pool de candidatos de la página que pasan los
        # filtros de metadata (anclaje/denylist/non-raster); se puntúan con
        # _score_candidate y la descarga se hace BEST-FIRST (mayor score
        # primero), no en orden de llegada (first-fit).
        _pool: list[tuple[float, dict]] = []
        for item in fresh:
            # §17 #30 — guard de CUPO dentro de la página: una página puede
            # traer MUCHOS más candidatos que `requested` (p.ej. ~2400 de
            # duckduckgo images en una sola request). Sin este guard, el `for`
            # consume TODA la página aunque `slot` ya alcanzó el cupo
            # (book_72: 2448 resultados para requested=5, 109 "ok" colándose
            # entre fallos). Va ANTES del chequeo de presupuesto: el cupo es
            # la condición de éxito normal, es más barata de comprobar y debe
            # cortar inmediatamente al completarse, sin depender del tiempo.
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

            # §17 #48 Fase 2 — candidato con metadata válida (anclaje + denylist):
            # NO se descarga inline (first-fit). Se puntúa con _score_candidate y
            # se añade al pool; la descarga se hace BEST-FIRST tras el `for`.
            _pool.append((_score_candidate(item, _rank_keywords), item))

        # §17 #48 Fase 2 — descarga BEST-FIRST: se ordena el pool por score
        # descendente y se intenta descargar/validar en ese orden hasta llenar
        # el cupo. El score es solo sobre metadata; la validación de bytes
        # (PIL verify + quality check de Fase 1) sigue siendo necesaria después.
        for _cand_score, item in sorted(_pool, key=lambda t: t[0], reverse=True):
            if slot >= requested:
                break

            img_src = item.get("img_src") or item.get("thumbnail_src") or ""
            image_id = f"img_{slot + 1:02d}_web"
            engine = _normalize_engine(item.get("engine"))
            ext = _image_extension(img_src)

            # §17 #30 — chequeo de presupuesto ANTES de cada descarga. Si se
            # agota a mitad de página, cortamos limpio ambos bucles y el relleno
            # final marca los slots faltantes con "no_results".
            if time.monotonic() - _start >= IMAGE_SEARCH_TOTAL_TIME_BUDGET:
                logger.warning(
                    "image_search: presupuesto agotado (%.1fs >= %.1fs) a mitad "
                    "de página %d: shortfall=%d no cubierto",
                    time.monotonic() - _start,
                    IMAGE_SEARCH_TOTAL_TIME_BUDGET,
                    page,
                    requested - slot,
                )
                _budget_exhausted = True
                break

            # §17 #30 — descarte por extensión ANTES de descargar: los formatos
            # no-raster (p.ej. .svg de lucide-static/devicons del log de
            # book_72) son ilegibles por PIL como imagen normal. Se descartan
            # sin gastar DOWNLOAD_TIMEOUT en una HTTP que tiraría los bytes.
            if _is_non_raster(img_src):
                logger.warning(
                    "image_search: descartado no-raster sin descargar (ext %s): %s",
                    ext,
                    img_src,
                )
                results.append(
                    _error_meta(
                        image_id, query, images_dir,
                        "invalid image content: non-raster extension",
                        source_url=img_src,
                    )
                )
                failed += 1
                slot += 1
                continue

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

            # §17 #48 Cambio C — check de calidad (dimensiones mínimas + aspect
            # ratio) ANTES de aceptar el candidato. Mismo patrón skip-and-
            # continue que la denylist: descarta SIN ocupar slot ni marcar
            # error, y el loop continúa con el siguiente candidato.
            if not _passes_quality_check(data_bytes):
                logger.warning(
                    "image_search: descartado por chequeo de calidad "
                    "(dimensiones/aspect ratio insuficientes): %s",
                    img_src,
                )
                continue

            # §17 #30 (P1a, book_72) — dedupe cross-chapter por contenido: la
            # misma imagen física (mismos bytes) ya usada en OTRO capítulo del
            # mismo libro se descarta y el slot sigue intentando con el
            # siguiente candidato SIN consumir slot de error (mismo tratamiento
            # que la denylist). El mismo hash en el PROPIO capítulo se permite
            # (re-ejecución/overwrite intencional del capítulo).
            _digest = hashlib.sha1(data_bytes).hexdigest()
            _owner = _hash_registry.get(_digest)
            if _owner is not None:
                try:
                    _owner_chapter = int(_owner.get("chapter", -1)) if isinstance(_owner, dict) else int(_owner)
                except (TypeError, ValueError):
                    _owner_chapter = -1
                if _owner_chapter != chapter_number:
                    logger.warning(
                        "image_search: descartada imagen duplicada entre capítulos "
                        "(mismo contenido que cap %s): %s",
                        _owner_chapter,
                        img_src,
                    )
                    continue

            # §17 #48 Fase 4 — verificación semántica VLM (DEFAULT OFF) del
            # candidato ya seleccionado por el ranking best-first y validado
            # por quality check + dedupe. Respuesta "NO" → skip-and-continue
            # (mismo patrón que denylist/quality check: descarta SIN ocupar
            # slot ni marcar error) y el loop prueba el siguiente candidato.
            # El fallo/timeout del VLM en sí es fail-open (True, ver función).
            _vlm_candidates_tried += 1
            if not _verify_image_relevance(
                data_bytes,
                topic,
                _rank_keywords,
                deadline=_start + IMAGE_SEARCH_TOTAL_TIME_BUDGET,
            ):
                logger.warning(
                    "image_search: descartado por verificación semántica VLM "
                    "(tema: %s): %s",
                    topic,
                    img_src,
                )
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
            # §17 #48 Fase 4 — trazabilidad VLM persistida (Optional en schema):
            # distingue "verificado y pasó" (vlm_checked=True) de "nunca se
            # verificó" (flag a 0 → False), y cuántos candidatos se probaron
            # antes de aceptar este (1 = a la primera).
            meta["vlm_checked"] = VLM_VERIFICATION_ENABLED
            meta["vlm_candidates_tried"] = _vlm_candidates_tried
            _vlm_candidates_tried = 0
            _write_metadata(images_dir, meta)
            # §17 #30 (P1a): registra el contenido aceptado para el dedupe
            # cross-chapter de los siguientes capítulos del libro.
            _hash_registry[_digest] = {"chapter": chapter_number, "path": image_path}
            _save_content_hashes(book_id, _hash_registry)
            results.append(meta)
            generated += 1
            slot += 1

        # §17 #30: página procesada. Si el presupuesto se agotó a mitad de esta
        # página (_budget_exhausted), cortamos la paginación aquí mismo; el
        # relleno final marcará los slots faltantes con "no_results". Si el cupo
        # ya está lleno, el while exterior rompe; si no, se pide la siguiente.
        if _budget_exhausted:
            break
        page += 1

    # Llenar los slots pedidos que no pudieron completarse con metadatos de error,
    # de forma que requested == generated + failed (skipped == 0), igual que en
    # generate_image.
    while slot < requested:
        image_id = f"img_{slot + 1:02d}_web"
        # §17 #48 Fase 3 — distinción rate_limited vs no_results: si la causa
        # del shortfall fue rate-limiting de SearXNG, el slot lo refleja para
        # que quality_gate/autopilot puedan diferenciarlo de un 0-resultados
        # real (campo `error` string libre: cambio aditivo, contrato intacto).
        _shortfall_error = "rate_limited" if _rate_limited else "no_results"
        results.append(_error_meta(image_id, query, images_dir, _shortfall_error))
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


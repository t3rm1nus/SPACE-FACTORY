"""Modulo research: busqueda web real, extraccion de texto y almacenamiento de fuentes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from core.book.source_manager import SourceManager
from core.logger import get_logger, log
from core.providers import get as get_provider
from core.providers.base import LLMInvalidResponseError

logger = get_logger(__name__)

# ---- Research parametrizado por idioma (fix book_56 / deuda §19 P3) --------
# Cada idioma soportado usa SU Wikipedia; el pipeline bilingüe ejecuta research
# una vez por idioma y el writer de cada idioma recibe fuentes del idioma correcto.
WIKI_API_BASES = {
    "es": "https://es.wikipedia.org/w/api.php",
    "en": "https://en.wikipedia.org/w/api.php",
}
WIKI_REST_BASES = {
    "es": "https://es.wikipedia.org/api/rest_v1/page/summary",
    "en": "https://en.wikipedia.org/api/rest_v1/page/summary",
}
WIKI_PAGE_URLS = {
    "es": "https://es.wikipedia.org/wiki/",
    "en": "https://en.wikipedia.org/wiki/",
}
# Retrocompatibilidad con el código/tests que importaba las constantes históricas.
WIKI_BASE = WIKI_API_BASES["es"]
WIKI_REST_BASE = WIKI_REST_BASES["es"]
WIKIDATA_BASE = "https://www.wikidata.org/w/api.php"


def _norm_language(language) -> str:
    """Normaliza el idioma activo a 'es'/'en' (tolerante a None/'es,en'/listas)."""
    if isinstance(language, (list, tuple)):
        for part in language:
            p = str(part).strip().lower()
            if p.startswith("en"):
                return "en"
            if p.startswith("es"):
                return "es"
        return "es"
    s = str(language or "").strip().lower()
    if "," in s:
        # Bilingüe "es,en": para UNA pasada de research el orden define el idioma
        # principal histórico ('es'); cada idioma tiene su propia pasada aparte.
        s = s.split(",")[0].strip()
    return "en" if s.startswith("en") else "es"

USER_AGENT = "SpaceLair/1.0 (research agent)"

# --- Timeouts y presupuesto de la capa multi-fuente (PASO 3) ------------------
# Mismos principios que el editor (modules/editor/main.py): acotamos el horizonte
# de la llamada al proveedor en una instancia LOCAL nueva (registry.get() crea una
# instancia por llamada -> seguro de mutar), de forma que un LLM lento/bloqueado
# active el fallback determinista antes de que el timeout del scheduler mate la tarea.
RESEARCH_PROVIDER_TIMEOUT = 40
RESEARCH_MAX_RETRIES = 1
RESEARCH_TOTAL_TIME_BUDGET = 90.0
# Curación con LLM: "1" por defecto (mismo patrón que CHAP_USE_LLM). Cualquier
# valor distinto de "1" fuerza el ranking determinista sin LLM.
RESEARCH_USE_LLM = os.environ.get("RESEARCH_USE_LLM", "1")
# archive.org está implementado pero DESHABILITADO por defecto: solo se activa
# con la variable de entorno RESEARCH_ARCHIVE_ENABLED="1".
RESEARCH_ARCHIVE_ENABLED = os.environ.get("RESEARCH_ARCHIVE_ENABLED", "0") == "1"
RESEARCH_ROUTER_MODEL = os.environ.get("RESEARCH_ROUTER_MODEL") or os.environ.get(
    "ROUTER_MODEL", "qwen-agent:latest"
)
# Umbral mínimo de overlap de keywords (query ↔ fuente). Fuentes por debajo de
# este umbral se descartan ANTES de persistirse: no cuentan para source_count
# ni para el gate de PASS/FAIL. Evita que research almacene fuentes irrelevantes
# (p.ej. "Crozet, Virginia" para "Los Dooms") y marque falsamente PASS. El
# gate de 8H.3 en core/autopilot.py captura source_count=0 → FAIL automáticamente.
# Configurable vía env var RESEARCH_RELEVANCE_MIN_OVERLAP.
RELEVANCE_MIN_OVERLAP = float(os.environ.get("RESEARCH_RELEVANCE_MIN_OVERLAP", "0.15"))

# Prioridad de fuente para el ranking determinista (Wikipedia > Wikidata = SearXNG > archive).
# web_searxng a 2 (mismo nivel que web_wikidata): con el default de producción
# max_sources=8, Wikipedia(3)+Wikidata(2) llenan los primeros 5 huecos; a partir del 6
# entra SearXNG (prioridad 2), dando diversidad sin despulsar la prioridad máxima de
# Wikipedia. Antes (max_sources=5) SearXNG quedaba fuera por orden de llegada.
SOURCE_PRIORITY = {
    "web_wikipedia": 3,
    "web_wikidata": 2,
    "web_searxng": 2,
    "web_archiveorg": 1,
}

PLACEHOLDER_PATTERNS = [
    r"Desarrollar el nucleo",
    r"contenido de prueba",
    r"texto de ejemplo",
    r"Lorem ipsum",
    r"pendiente",
    r"TODO",
    r"insert text",
    r"placeholder",
]


def _request(url: str, timeout: int = 20) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise RuntimeError(f"Error de red en {url}: {e.reason}") from e

def _wiki_search(query: str, limit: int = 5, language: str = "es") -> list[dict[str, Any]]:
    lang = _norm_language(language)
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(limit),
        "format": "json",
    })
    url = f"{WIKI_API_BASES[lang]}?{params}"
    status, body = _request(url)
    if status != 200:
        return []
    data = json.loads(body.decode("utf-8", errors="replace"))
    results: list[dict[str, Any]] = []
    for item in data.get("query", {}).get("search", []):
        results.append({
            "title": item.get("title", ""),
            "pageid": item.get("pageid"),
            "snippet": re.sub(r"<[^>]+>", "", item.get("snippet", "")),
            "url": f"{WIKI_PAGE_URLS[lang]}{urllib.parse.quote(item.get('title', ''))}",
            "timestamp": item.get("timestamp"),
        })
    return results


def _wiki_extract(title: str, sentences: int = 8, language: str = "es") -> Optional[dict[str, Any]]:
    lang = _norm_language(language)
    params = urllib.parse.urlencode({
        "action": "query",
        "prop": "extracts",
        "exintro": "true",
        "explaintext": "true",
        "exsentences": str(sentences),
        "titles": title,
        "format": "json",
    })
    url = f"{WIKI_API_BASES[lang]}?{params}"
    status, body = _request(url)
    if status != 200:
        return None
    data = json.loads(body.decode("utf-8", errors="replace"))
    pages = data.get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    extract = page.get("extract", "").strip()
    if not extract:
        return None
    return {
        "title": page.get("title", title),
        "extract": extract,
        "url": f"{WIKI_PAGE_URLS[_norm_language(language)]}{urllib.parse.quote(title)}",
    }


def _wiki_rest_summary(title: str, language: str = "es") -> Optional[dict[str, Any]]:
    lang = _norm_language(language)
    safe = urllib.parse.quote(title.replace(" ", "_"))
    url = f"{WIKI_REST_BASES[lang]}/{safe}"
    status, body = _request(url)
    if status != 200:
        return None
    data = json.loads(body.decode("utf-8", errors="replace"))
    extract = data.get("extract", "").strip()
    if not extract:
        return None
    return {
        "title": data.get("title", title),
        "extract": extract,
        "url": data.get("content_urls", {}).get("desktop", {}).get("page", url),
    }


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _is_placeholder(text: str) -> bool:
    if not text or not text.strip():
        return True
    for pat in PLACEHOLDER_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def _store_source(title: str, url: str, content: str, source_type: str = "web") -> Optional[dict[str, Any]]:
    try:
        source = SourceManager.add_source(
            url=url,
            title=title,
            source_type=source_type,
            relevance=len(content.split()),
            notes=f"content_snippet={content[:2000]}",
        )
        if source:
            source["hash"] = _hash_text(content)
            source["relevance"] = len(content.split())
            source["status"] = "verified"
        return source
    except Exception as e:
        logger.warning("No se pudo almacenar fuente %s: %s", url, e)
        return None


# ----------------------------------------------------------------------------
# Capa multi-fuente (PASO 3): normalización, dedupe determinista y backends.
# ----------------------------------------------------------------------------
def _normalize_url(url: str) -> str:
    """Normaliza una URL para deduplicación determinista:
    esquema y host en minúsculas, sin fragmento ni slash final, query ordenada."""
    url = (url or "").strip()
    if not url:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    scheme = (parts.scheme or "").lower()
    netloc = (parts.netloc or "").lower()
    path = (parts.path or "").rstrip("/")
    query = parts.query
    if query:
        query = "&".join(sorted(query.split("&")))
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def _candidate_key(cand: dict[str, Any]) -> str:
    """Clave de dedupe: URL normalizada + título en minúsculas."""
    return "|".join(
        (_normalize_url(cand.get("url", "")), (cand.get("title") or "").strip().lower())
    )


def _backend_wikipedia(query: str, per_backend_limit: int, timeout: int, language: str = "es") -> list[dict[str, Any]]:
    """Backend Wikipedia (idioma parametrizado): búsqueda + extracción real del resumen introductorio."""
    lang = _norm_language(language)
    out: list[dict[str, Any]] = []
    try:
        items = _wiki_search(query, limit=per_backend_limit, language=lang)
    except Exception as e:
        logger.warning("Backend wikipedia falló durante búsqueda multi-fuente: %s", e)
        return out
    for item in items[:per_backend_limit]:
        title = str(item.get("title") or "")
        extract_data = _wiki_rest_summary(title, language=lang) or _wiki_extract(title, language=lang)
        if not extract_data:
            continue
        text = str(extract_data.get("extract") or "").strip()
        if _is_placeholder(text):
            continue
        out.append({
            "title": str(extract_data.get("title") or title),
            "url": str(extract_data.get("url") or item.get("url") or ""),
            "snippet": str(item.get("snippet") or ""),
            "content": text,
            "source_type": "web_wikipedia",
        })
        if len(out) >= per_backend_limit:
            break
    return out


def _backend_wikidata(query: str, per_backend_limit: int, timeout: int, language: str = "es") -> list[dict[str, Any]]:
    """Backend Wikidata: búsqueda de entidades (wbsearchentities) en el idioma dado."""
    lang = _norm_language(language)
    params = urllib.parse.urlencode({
        "action": "wbsearchentities",
        "search": query,
        "language": lang,
        "format": "json",
        "limit": str(per_backend_limit),
    })
    url = f"{WIKIDATA_BASE}?{params}"
    out: list[dict[str, Any]] = []
    try:
        status, body = _request(url, timeout=timeout)
        if status != 200:
            return out
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("Backend wikidata falló durante búsqueda multi-fuente: %s", e)
        return out
    for it in (data.get("search") or [])[:per_backend_limit]:
        qid = str(it.get("id") or "")
        label = str(it.get("label") or qid or "Wikidata")
        desc = str(it.get("description") or "")
        if not qid:
            continue
        out.append({
            "title": label,
            "url": f"https://www.wikidata.org/wiki/{qid}",
            "snippet": desc,
            "content": label + (f" — {desc}" if desc else ""),
            "source_type": "web_wikidata",
        })
        if len(out) >= per_backend_limit:
            break
    return out


def _backend_archive(query: str, per_backend_limit: int, timeout: int) -> list[dict[str, Any]]:
    """Backend archive.org (implementado, pero deshabilitado por defecto)."""
    params = urllib.parse.urlencode({
        "q": query,
        "fl[]": "identifier,title",
        "rows": str(per_backend_limit),
        "output": "json",
    })
    url = f"https://archive.org/advancedsearch.php?{params}"
    out: list[dict[str, Any]] = []
    try:
        status, body = _request(url, timeout=timeout)
        if status != 200:
            return out
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("Backend archive.org falló durante búsqueda multi-fuente: %s", e)
        return out
    for doc in (data.get("response", {}).get("docs") or [])[:per_backend_limit]:
        ident = str(doc.get("identifier") or "")
        if not ident:
            continue
        out.append({
            "title": str(doc.get("title") or ident),
            "url": f"https://archive.org/details/{ident}",
            "snippet": "",
            "content": str(doc.get("title") or ident),
            "source_type": "web_archiveorg",
        })
        if len(out) >= per_backend_limit:
            break
    return out


def _search_searxng(query: str, limit: int = 5, timeout: int = 7) -> list[dict[str, Any]]:
    """Consulta la instancia local de SearXNG (infra/searxng) y devuelve fuentes
    con la MISMA estructura que los demás backends (title/url/snippet/content/
    source_type).

    - URL base desde la variable de entorno ``SEARXNG_BASE_URL`` (default
      ``http://localhost:8081``) para no hardcodear el puerto.
    - Timeout corto (7s; servicio local).
    - Si SearXNG está caído/no responde/devuelve error, loguea warning y devuelve
      lista vacía SIN lanzar excepción: el job de research no debe romperse si el
      contenedor no está corriendo.
    """
    base = os.environ.get("SEARXNG_BASE_URL", "http://localhost:8081").rstrip("/")
    params = urllib.parse.urlencode({"q": query, "format": "json"})
    url = f"{base}/search?{params}"
    out: list[dict[str, Any]] = []
    try:
        status, body = _request(url, timeout=timeout)
        if status != 200:
            logger.warning("SearXNG respondió HTTP %s para la consulta: %r", status, query)
            return out
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("SearXNG no disponible (consulta %r): %s", query, e)
        return out
    for item in (data.get("results") or [])[:limit]:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        content = str(item.get("content") or "").strip()
        # §17 #33 — descartar snippets de SERP (fecha/hace N días/"…") cortos:
        # SearXNG entrega el snippet del buscador, no contenido real. skip-and-continue.
        if _is_serp_snippet(content) and len(content) < _SERP_LENGTH_THRESHOLD:
            continue
        out.append({
            "title": title,
            "url": url,
            "snippet": content[:200],
            "content": content,
            "source_type": "web_searxng",
        })
    return out


# §17 #27 — Denylist de redes sociales: SearXNG devuelve posts de TikTok/Instagram
# con metadata de engagement (fecha+likes+comentarios) como resultados; el writer los
# copiaba verbatim y el fact_checker bloqueaba el capítulo (patrón fabricación
# estructural). Se descartan ANTES de curación/ranking, sin ocupar slot ni lanzar error.
_SOCIAL_MEDIA_DENYLIST = {
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
}


def _is_social_media(url: Optional[str]) -> bool:
    """True si el dominio de ``url`` contiene algún dominio de ``_SOCIAL_MEDIA_DENYLIST``.

    Tolerante a URL vacía/None (devuelve False, no levanta excepción). Elimina
    el ``www.`` inicial y compara en minúsculas por CONTENIDO para cubrir
    subdominios (``es.tiktok.com`` dispara ``tiktok.com``).
    """
    if not url:
        return False
    try:
        netloc = urllib.parse.urlparse(str(url)).netloc.lower()
    except Exception:  # noqa: BLE001 - defensa en depth
        return False
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return any(blocked in netloc for blocked in _SOCIAL_MEDIA_DENYLIST)


# §17 #33 — Filtro de snippets de SERP: SearXNG devuelve el `content` como SNIPPET
# del buscador (fecha de indexación "6 sept 2024", "hace N días" y "…" de
# truncamiento del SERP), NO contenido real de la página. El writer los copiaba
# verbatim y el fact_checker los bloqueaba (patrón fabricación estructural).
# Se descartan en `_search_searxng` ANTES de curación/ranking, sin ocupar slot ni
# lanzar error — mismo patrón de skip-and-continue que el denylist §17 #27.
# Motivado por book_69/task_1486: 6 de 9 fuentes eran snippets de SERP (153-166
# chars), frente a 3 de Wikipedia limpias (224-254 chars).
_SERP_LENGTH_THRESHOLD = 250
_SERP_DATE_RE = re.compile(r"\d{1,2}\s+\w{3,4}\.?\s+\d{4}")
_SERP_AGO_RE = re.compile(r"\bhace\s+\d+\s+(día|días|hora|horas)\b")


def _is_serp_snippet(content: Optional[str]) -> bool:
    """True si ``content`` parece un snippet de buscador (SERP), no contenido real.

    Detecta: fecha corta estilo "6 sept 2024", "hace N días/horas", o que el
    texto termine en "..." / "…" tras strip(). Tolerante a None/vacío (False)."""
    if not content:
        return False
    c = str(content).strip()
    if _SERP_DATE_RE.search(c) or _SERP_AGO_RE.search(c):
        return True
    return c.endswith("...") or c.endswith("…")


def _multi_source_search(query: str, max_sources: int, timeout: int, language: str = "es") -> list[dict[str, Any]]:
    """Búsqueda multi-backend con dedupe determinista y tope duro por backend.

    Cada backend aporta a lo sumo ``per_backend_limit`` resultados (tope duro por
    backend) y RESEARCH_TOTAL_TIME_BUDGET frena el proceso si se agota.
    ``language`` parametriza Wikipedia/Wikidata ('es' | 'en').

    Compatibilidad con mocks de tests: los backends se invocan con la firma
    histórica ``(query, n, timeout=timeout)``; el idioma solo se pasa como kwarg
    si el callable lo acepta (los mocks antiguos ``lambda q, n, timeout`` siguen
    funcionando sin cambios).
    """
    lang = _norm_language(language)
    per_backend_limit = max(max_sources, 1)
    started = time.monotonic()
    collected: list[dict[str, Any]] = []

    def _call(fn, *args, **kwargs):
        try:
            return fn(*args, language=lang, **kwargs)
        except TypeError:
            return fn(*args, **kwargs)

    def _wiki_backend(q, n, timeout):
        # globals() para respetar monkeypatch de tests sobre _backend_wikipedia.
        fn = globals().get("_backend_wikipedia")
        return _call(fn, q, n, timeout)

    def _wdata_backend(q, n, timeout):
        fn = globals().get("_backend_wikidata")
        return _call(fn, q, n, timeout)

    backends: list[tuple] = [
        (_wiki_backend, "wikipedia"),
        (_wdata_backend, "wikidata"),
        (_search_searxng, "searxng"),
    ]
    if RESEARCH_ARCHIVE_ENABLED:
        backends.append((_backend_archive, "archive.org"))

    for fn, label in backends:
        if time.monotonic() - started >= RESEARCH_TOTAL_TIME_BUDGET:
            break
        try:
            results = fn(query, per_backend_limit, timeout=timeout)
        except Exception as e:
            logger.warning("Backend %s lanzó excepción durante búsqueda multi-fuente: %s", label, e)
            results = []
        for cand in results[:per_backend_limit]:
            if not cand.get("url"):
                continue
            # §17 #27: descarte silencioso de redes sociales ANTES de dedupe/curación
            # (no ocupa slot, no lanza error, no toca el resto de la lógica).
            if _is_social_media(cand.get("url")):
                continue
            collected.append(cand)

    # Dedupe determinista global (URL normalizada + título), en el orden de llegada
    # (primera ocurrencia gana). Cada backend aporta hasta su propio per_backend_limit
    # antes de cualquier corte: NO se corta aquí por max_sources. El corte decisivo a
    # max_sources pasa SOLO al final, tras rankear por prioridad/overlap. Si se cortara
    # durante la recolección (comportamiento previo), un backend de mayor prioridad
    # implícita 0 (p.ej. web_searxng) quedaba fuera por orden de llegada bajo el
    # default max_sources=5: Wikipedia llenaba los huecos antes de que SearXNG entrara
    # a rankear (bug de starvation por orden de llegada). Ver FASE 8M.2.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for cand in collected:
        key = _candidate_key(cand)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cand)

    # Corte a max_sources SOLO tras dedupe + ranking determinista (prioridad de fuente
    # + overlap léxico). _deterministic_curate hace sorted(by score, reverse=True)[:max_sources].
    # Cada backend lleva su propio timeout; el try/except por-backend sigue intacto arriba.
    return _deterministic_curate(query, unique, max_sources)


# ----------------------------------------------------------------------------
# Curación y ranking (PASO 3)
# ----------------------------------------------------------------------------
# Stopwords en español (y inglés para fallback es→en). Se mantiene lista mínima para
# evitar duplicar lo que ya exista en otro sitio del proyecto; amplíe si es necesario.
_STOPWORDS_ES = {
    "el", "la", "los", "las", "de", "del", "en", "un", "una", "unos", "unas",
    "y", "o", "que", "con", "para", "por", "se", "es", "su", "sus", "al", "lo",
    # Inglés común (fallback es→en)
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for", "nor",
    "on", "at", "by", "of", "to", "from", "in", "out", "over", "under",
}

def _keyword_overlap(query: str, cand: dict[str, Any]) -> float:
    """Fracción de keywords (>=2 chars) de la query que aparecen en la fuente.

    Se tokeniza el haystack en palabras reales (set membership) y se excluyen
    las stopwords de la lista de keywords antes del cálculo de overlap, para
    evitar falsos positivos con palabras comunes en español/inglés.
    """
    # Extraer keywords de la query (palabras >=2 chars)
    all_keywords = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) >= 2]
    # Filtrar stopwords para evitar coincidencias false positive
    keywords = [w for w in all_keywords if w not in _STOPWORDS_ES]
    if not keywords:
        return 0.0
    # Tokenizar haystack en palabras reales (set membership, NO substring)
    haystack_words = set(re.findall(r"\w+", str(cand.get("title") or "").lower() + " " +
                                  str(cand.get("snippet") or "").lower() + " " +
                                  str(cand.get("content") or "").lower()))
    hits = sum(1 for w in keywords if w in haystack_words)
    return hits / len(keywords) if keywords else 0.0


def _has_anchor_keyword(topic: str, cand: dict[str, Any]) -> bool:
    """True si el candidato se ancla al TEMA del libro (topic), no solo a la query.

    Reutiliza la misma tokenización y _STOPWORDS_ES de _keyword_overlap (no
    duplica lógica). Devuelve True (no ancla / no bloquea) si:

    - topic es None/"" ; o
    - tras quitar stopwords no queda ninguna keyword útil en topic.

    Esto preserva compatibilidad total con las llamadas que no pasan topic
    (el anclaje es aditivo: solo añade restricción cuando hay tema real).
    """
    if not topic:
        return True
    # Extraer keywords del topic (palabras >=2 chars, sin stopwords)
    topic_keywords = [
        w for w in re.findall(r"\w+", str(topic).lower())
        if len(w) >= 2 and w not in _STOPWORDS_ES
    ]
    if not topic_keywords:
        return True
    # Tokenizar haystack en palabras reales (set membership, igual que _keyword_overlap)
    haystack_words = set(re.findall(
        r"\w+",
        str(cand.get("title") or "").lower() + " " +
        str(cand.get("snippet") or "").lower() + " " +
        str(cand.get("content") or "").lower(),
    ))
    hits = sum(1 for w in topic_keywords if w in haystack_words)
    if len(topic_keywords) == 1:
        # Tema de una sola palabra: 1 coincidencia basta (comportamiento previo).
        return hits >= 1
    # Tema multi-palabra: exigir al menos 2 keywords ancladas para evitar
    # falsos positivos por homónimos (caso real book_37: "Historia del Doom"
    # anclaba el artículo Marvel "Doctor Doom" solo por la palabra "doom").
    return hits >= 2


def _content_length(cand: dict[str, Any]) -> int:
    return len(str(cand.get("content") or "").split())


def _source_priority(cand: dict[str, Any]) -> int:
    return SOURCE_PRIORITY.get(str(cand.get("source_type")), 0)


def _deterministic_curate(
    query: str, candidates: list[dict[str, Any]], max_sources: int
) -> list[dict[str, Any]]:
    """Ranking sin LLM: keywords + longitud de contenido + prioridad de fuente."""
    def _score(cand: dict[str, Any]) -> float:
        overlap = _keyword_overlap(query, cand)
        length_factor = min(1.0, _content_length(cand) / 200.0)
        return (
            overlap * 3.0
            + length_factor
            + _source_priority(cand) * 0.5
            + _content_length(cand) / 10000.0
        )
    ranked = sorted(candidates, key=_score, reverse=True)
    return ranked[:max_sources]


def _build_curation_prompt(
    query: str, candidates: list[dict[str, Any]], language: str, max_sources: int
) -> str:
    lines: list[str] = []
    for idx, cand in enumerate(candidates):
        snippet = str(cand.get("snippet") or cand.get("content") or "")[:200]
        lines.append(
            f'{idx}. URL: {cand.get("url")} | Título: {cand.get("title")} '
            f'| Tipo: {cand.get("source_type")} | Extracto: {snippet}'
        )
    body = "\n".join(lines) or "(sin candidatos)"
    return (
        "Eres un curador editorial profesional. Idioma destino: " + str(language) + "\n"
        "Consulta a investigar: " + str(query) + "\n\n"
        f"Selecciona las {max_sources} mejores fuentes de la siguiente lista. "
        'Devuelve ÚNICAMENTE JSON con la clave "sources" (un array de objetos con '
        '"url" y "rank"). Cada URL DEBE ser exactamente una de las URLs de la lista; '
        "no inventes URLs.\n\n"
        "Candidatos:\n" + body
    )


def _parse_json_response(text: str) -> Optional[dict[str, Any]]:
    """Parsea JSON tolerando bloques fenced (```json ... ```). None si es inválido."""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.DOTALL)
    if m:
        t = m.group(1).strip()
    try:
        parsed = json.loads(t)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _curate_with_llm(
    query: str, candidates: list[dict[str, Any]], language: str, max_sources: int
) -> tuple[list[dict[str, Any]], str]:
    """Curación con LLM con anti-alucinación. Devuelve (sources, execution_mode).

    - Muta provider.timeout / provider.max_retries ANTES de generate() (igual que editor).
    - Ante CUALQUIER fallo (timeout, conexión, JSON inválido, proveedor ausente) devuelve
      el ranking determinista con execution_mode="deterministic".
    - Cada URL devuelta por el LLM debe existir en los candidatos reales; si no, se
      descarta y se rellena con el siguiente del ranking determinista.
    """
    deterministic = _deterministic_curate(query, candidates, max_sources)

    if RESEARCH_USE_LLM != "1":
        return deterministic, "deterministic"
    if not candidates:
        return deterministic, "deterministic"

    real_by_url: dict[str, dict[str, Any]] = {
        _normalize_url(str(cand.get("url") or "")): cand for cand in candidates
    }

    try:
        provider = get_provider()
        provider.timeout = RESEARCH_PROVIDER_TIMEOUT
        provider.max_retries = RESEARCH_MAX_RETRIES
        prompt = _build_curation_prompt(query, candidates, language, max_sources)
        result = provider.generate(
            prompt,
            system="Eres un curador editorial. Devuelve solo JSON.",
            model=RESEARCH_ROUTER_MODEL,
            max_tokens=800,
            temperature=0.1,
        )
        data = _parse_json_response(getattr(result, "text", "") or "")
        if data is None:
            raise LLMInvalidResponseError("Respuesta del LLM no válida en curación de fuentes")
        selected = data.get("sources") or data.get("source_urls") or []
        if not isinstance(selected, list):
            raise LLMInvalidResponseError("La clave sources del LLM no es una lista")
    except Exception as e:
        log(logger, logging.WARNING, f"Falla la curación con LLM; se usa ranking determinista: {e}")
        return deterministic, "deterministic"

    curated: list[dict[str, Any]] = []
    used_keys: set[str] = set()

    def _add(cand: dict[str, Any]) -> None:
        key = _candidate_key(cand)
        if key in used_keys:
            return
        used_keys.add(key)
        curated.append(cand)

    for sel in selected:
        if len(curated) >= max_sources:
            break
        url = None
        if isinstance(sel, str):
            url = sel
        elif isinstance(sel, dict):
            url = sel.get("url") or sel.get("link")
        if not url:
            continue
        cand = real_by_url.get(_normalize_url(str(url)))
        if cand is None:
            continue  # URL inventada por el LLM: se descarta.
        _add(cand)

    # Rellenar con el siguiente del ranking determinista (nunca inventado).
    for cand in deterministic:
        if len(curated) >= max_sources:
            break
        _add(cand)

    return curated[:max_sources], "llm"


def _shorten_query_for_search(query: str) -> Optional[str]:
    """Deriva un query corto buscable a partir de una frase larga (idea/título).

    Recuperación robusta en ``research_web`` cuando el query crudo da 0 candidatos
    (evidencia book_71: la idea completa = frase larga -> 0 en Wikipedia/Wikidata,
    pero "historia de los videojuegos" -> 8 resultados). Corta en el primer
    """
    if not query or not str(query).strip():
        return None
    q = str(query).strip()
    # Cortar en el primer separador de cláusula más temprano.
    for sep in (", ", " hasta ", " desde ", " y luego ", ","):
        idx = q.lower().find(sep)
        if idx > 0:
            q = q[:idx].strip()
            break
    tokens = [
        w
        for w in re.findall(r"\w+", q)
        if len(w) >= 2 and w.lower() not in _STOPWORDS_ES
    ]
    if not tokens:
        return None
    short = " ".join(tokens[:6])
    if not short.strip() or short.lower().strip() == str(query).replace(",", "").lower().strip():
        return None
    return short


def research_web(query: str, max_sources: int = 8, timeout: int = 20, language: str = "es",
                 topic: Optional[str] = None) -> dict[str, Any]:
    lang = _norm_language(language)
    try:
        candidates = _multi_source_search(query, max_sources, timeout, language=lang)
    except Exception as e:
        return {
            "query": query,
            "status": "FAIL",
            "execution_mode": "failed",
            "sources": [],
            "stored_sources": [],
            "source_count": 0,
            "error": str(e),
            "quality_gate": "FAIL",
        }

    # Recuperación por query derivado: si el crudo (idea/título completo del libro)
    # da 0 candidatos en Wikipedia/Wikidata, se reintenta UNA vez con un query corto
    # derivado del mismo texto (evidencia book_71). topic NO cambia.
    query_effective = query
    if not candidates:
        short_q = _shorten_query_for_search(query)
        if short_q:
            try:
                candidates = _multi_source_search(
                    short_q, max_sources, timeout, language=lang
                )
            except Exception:
                candidates = []
            if candidates:
                query_effective = short_q
    if not candidates:
        return {
            "query": query,
            "status": "FAIL",
            "execution_mode": "deterministic",
            "sources": [],
            "stored_sources": [],
            "source_count": 0,
            "error": "No se obtuvieron fuentes reales.",
            "quality_gate": "FAIL",
        }

    # Curación: LLM (con anti-alucinación) salvo que RESEARCH_USE_LLM != "1",
    # en cuyo caso entra directo el ranking determinista.
    try:
        if RESEARCH_USE_LLM == "1":
            curated, execution_mode = _curate_with_llm(query_effective, candidates, language, max_sources)
        else:
            curated = _deterministic_curate(query_effective, candidates, max_sources)
            execution_mode = "deterministic"
    except Exception as e:
        log(logger, logging.WARNING, f"Falla la curación; se usa ranking determinista: {e}")
        curated = _deterministic_curate(query_effective, candidates, max_sources)
        execution_mode = "deterministic"

    # --- Filtro de relevancia (PASO 4): criterio compuesto. Se descartan las
    # fuentes cuyo overlap de keywords con la query es inferior al umbral, o que
    # no se ANCLAN al tema del libro (topic) cuando hay topic real disponible
    # (ver _has_anchor_keyword). No se persisten ni cuentan para source_count /
    # PASS/FAIL; el gate de 8H.3 en core/autopilot.py falla automáticamente si
    # source_count queda por debajo de min_sources.
    _pre = len(curated)
    curated = [
        c for c in curated
        if _keyword_overlap(query_effective, c) >= RELEVANCE_MIN_OVERLAP
        and _has_anchor_keyword(topic, c)
    ]
    if _pre - len(curated):
        log(
            logger,
            logging.INFO,
            f"Relevance filter: descartadas {_pre - len(curated)} "
            f"fuentes irrelevantes (min_overlap={RELEVANCE_MIN_OVERLAP}, "
            f"topic_anchor={bool(topic)})",
        )

    results: list[dict[str, Any]] = []
    stored: list[dict[str, Any]] = []
    for cand in curated[:max_sources]:
        title = str(cand.get("title") or "")
        url = str(cand.get("url") or "")
        content = str(cand.get("content") or cand.get("snippet") or "")
        source_type = str(cand.get("source_type") or "web")
        if not url:
            continue
        source = _store_source(title, url, content, source_type=source_type)
        if source:
            stored.append(source)
            results.append({
                "title": title,
                "url": url,
                "source_type": source_type,
                "content": content,
                "accessed_at": datetime.now(timezone.utc).isoformat(),
            })

    status = "PASS" if len(stored) >= 1 else "FAIL"
    return {
        "query": query,
        "language": lang,
        "status": status,
        "execution_mode": execution_mode,
        "sources": results,
        "stored_sources": stored,
        "source_count": len(stored),
        "error": None if status == "PASS" else "No se obtuvieron fuentes reales.",
        "quality_gate": "PASS" if status == "PASS" else "FAIL",
    }


def fetch_url(url: str, timeout: int = 20) -> dict[str, Any]:
    status, body = _request(url, timeout=timeout)
    if status != 200:
        return {
            "url": url,
            "status": "FAIL",
            "execution_mode": "failed",
            "content": "",
            "error": f"HTTP {status}",
        }
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return {
        "url": url,
        "status": "PASS",
        "execution_mode": "real",
        "content": text[:5000],
        "error": None,
    }


def extract_text(html_or_text: str, max_chars: int = 5000) -> str:
    text = html_or_text
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def health_check() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    try:
        status, _ = _request(
            "https://es.wikipedia.org/w/api.php?action=query&meta=siteinfo&format=json",
            timeout=10,
        )
        checks["wikipedia_api"] = status == 200
    except Exception as e:
        checks["wikipedia_api"] = False
        checks["error"] = str(e)

    try:
        checks["source_manager"] = hasattr(SourceManager, "add_source")
    except Exception as e:
        checks["source_manager"] = False
        checks["source_manager_error"] = str(e)

    healthy = checks.get("wikipedia_api") is True and checks.get("source_manager") is True
    return {
        "healthy": healthy,
        "dependencies": checks,
        "status": "healthy" if healthy else "unhealthy",
    }


def validate_payload(capability: str, payload: dict) -> dict[str, Any]:
    if capability not in ("research_web", "fetch_url", "extract_text"):
        raise ValueError(f"Capacidad no soportada: {capability}")

    if capability == "research_web":
        query = payload.get("query") or payload.get("topic") or payload.get("idea") or ""
        query = str(query).strip()
        if not query:
            raise ValueError("El payload de research_web requiere 'query', 'topic' o 'idea'.")
        return {
            "query": query,
            "max_sources": int(payload.get("max_sources", 8)),
            "min_sources": int(payload.get("min_sources", 3)),
            "timeout": int(payload.get("timeout", 20)),
            "research_required": bool(payload.get("research_required", True)),
        }

    if capability == "fetch_url":
        url = str(payload.get("url") or "").strip()
        if not url:
            raise ValueError("El payload de fetch_url requiere 'url'.")
        return {"url": url, "timeout": int(payload.get("timeout", 20))}

    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("El payload de extract_text requiere 'text'.")
    return {"text": text, "max_chars": int(payload.get("max_chars", 5000))}


def execute(payload: dict, capability: str = "research_web") -> dict[str, Any]:
    validated = validate_payload(capability, payload)
    query = validated["query"]
    max_sources = validated.get("max_sources", 8)
    min_sources = validated.get("min_sources", 3)
    timeout = validated.get("timeout", 20)
    research_required = validated.get("research_required", True)
    # topic: ancla de relevancia tomada del PAYLOAD CRUDO (el campo ya llega desde
    # editorial.build_payload y lo preserva el schema ResearchPayload). Se usa
    # SOLO para anclar el filtro de relevancia; NO se toca validate_payload ni
    # su dict de retorno.
    topic = payload.get("topic")
    # Idioma activo de la pasada de research ('es'|'en'): parametriza Wikipedia/
    # Wikidata (fix book_56 / deuda §19 P3). Llega desde editorial.build_payload
    # vía el Autopilot, que ejecuta UNA pasada por idioma en libros bilingües.
    language = _norm_language(payload.get("language"))

    # Capability fetch_url y extract_text no requieren research_required
    if capability == "fetch_url":
        url = validated["url"]
        result = fetch_url(url, timeout=timeout)
        if result.get("status") == "PASS" and not _is_placeholder(result.get("content", "")):
            result["status"] = "PASS"
        else:
            result["status"] = "FAIL"
        return result

    if capability == "extract_text":
        text = validated["text"]
        extracted = extract_text(text, max_chars=int(validated.get("max_chars", 5000)))
        return {
            "status": "PASS" if not _is_placeholder(extracted) and extracted else "FAIL",
            "execution_mode": "real",
            "extracted_text": extracted,
            "error": None,
        }

    try:
        result = research_web(query, max_sources=max_sources, timeout=timeout, language=language, topic=topic)
    except Exception as e:
        result = {
            "query": query,
            "status": "FAIL",
            "execution_mode": "failed",
            "sources": [],
            "stored_sources": [],
            "source_count": 0,
            "error": str(e),
            "quality_gate": "FAIL",
        }

    result.setdefault("execution_mode", "real")
    result.setdefault("query", query)
    result.setdefault("language", language)
    result.setdefault("sources", [])
    result.setdefault("stored_sources", [])
    result.setdefault("source_count", 0)
    result.setdefault("error", None)
    result.setdefault("quality_gate", "PASS")

    source_count = result.get("source_count", 0) or 0
    if research_required and source_count < min_sources:
        result["status"] = "FAIL"
        result["error"] = (
            result.get("error")
            or f"research_required=true y se obtuvieron {source_count} fuentes (< mínimo {min_sources})."
        )
        result["quality_gate"] = "FAIL"
    elif not research_required and source_count == 0:
        result["status"] = "PASS"
        result["error"] = None
        result["quality_gate"] = "PASS"

    log(
        logger,
        logging.INFO if result.get("status") == "PASS" else logging.WARNING,
        f"Research finalizado: {result.get('status')} | modo={result.get('execution_mode')} | fuentes={source_count}",
    )
    return result


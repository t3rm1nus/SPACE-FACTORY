"""Modulo research: busqueda web real, extraccion de texto y almacenamiento de fuentes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from core.book.source_manager import SourceManager
from core.logger import get_logger, log

logger = get_logger(__name__)

WIKI_BASE = "https://es.wikipedia.org/w/api.php"
WIKI_REST_BASE = "https://es.wikipedia.org/api/rest_v1/page/summary"
USER_AGENT = "SpaceLair/1.0 (research agent)"

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

def _wiki_search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": str(limit),
        "format": "json",
    })
    url = f"{WIKI_BASE}?{params}"
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
            "url": f"https://es.wikipedia.org/wiki/{urllib.parse.quote(item.get('title', ''))}",
            "timestamp": item.get("timestamp"),
        })
    return results


def _wiki_extract(title: str, sentences: int = 8) -> Optional[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "action": "query",
        "prop": "extracts",
        "exintro": "true",
        "explaintext": "true",
        "exsentences": str(sentences),
        "titles": title,
        "format": "json",
    })
    url = f"{WIKI_BASE}?{params}"
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
        "url": f"https://es.wikipedia.org/wiki/{urllib.parse.quote(title)}",
    }


def _wiki_rest_summary(title: str) -> Optional[dict[str, Any]]:
    safe = urllib.parse.quote(title.replace(" ", "_"))
    url = f"{WIKI_REST_BASE}/{safe}"
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


def research_web(query: str, max_sources: int = 5, timeout: int = 20) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    stored: list[dict[str, Any]] = []
    try:
        search_items = _wiki_search(query, limit=max_sources)
    except Exception as e:
        return {
            "query": query,
            "status": "FAIL",
            "execution_mode": "failed",
            "sources": [],
            "stored_sources": [],
            "error": str(e),
        }

    for item in search_items[:max_sources]:
        title = item["title"]
        extract_data = _wiki_rest_summary(title) or _wiki_extract(title)
        if not extract_data:
            continue
        text = extract_data["extract"]
        if _is_placeholder(text):
            continue
        url = extract_data["url"]
        source = _store_source(title, url, text, source_type="web_wikipedia")
        if source:
            stored.append(source)
            results.append({
                "title": title,
                "url": url,
                "source_type": "web_wikipedia",
                "content": text,
                "accessed_at": datetime.now(timezone.utc).isoformat(),
            })

    status = "PASS" if len(stored) >= 1 else "FAIL"
    return {
        "query": query,
        "status": status,
        "execution_mode": "real",
        "sources": results,
        "stored_sources": stored,
        "source_count": len(stored),
        "error": None if status == "PASS" else "No se obtuvieron fuentes reales.",
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
            "max_sources": int(payload.get("max_sources", 5)),
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
    max_sources = validated.get("max_sources", 5)
    min_sources = validated.get("min_sources", 3)
    timeout = validated.get("timeout", 20)
    research_required = validated.get("research_required", True)

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
        result = research_web(query, max_sources=max_sources, timeout=timeout)
    except Exception as e:
        result = {
            "query": query,
            "status": "FAIL",
            "execution_mode": "failed",
            "sources": [],
            "stored_sources": [],
            "source_count": 0,
            "error": str(e),
        }

    result.setdefault("execution_mode", "real")
    result.setdefault("query", query)
    result.setdefault("sources", [])
    result.setdefault("stored_sources", [])
    result.setdefault("source_count", 0)
    result.setdefault("error", None)

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

    log(
        logger,
        logging.INFO if result.get("status") == "PASS" else logging.WARNING,
        f"Research finalizado: {result.get('status')} | modo={result.get('execution_mode')} | fuentes={source_count}",
    )
    return result


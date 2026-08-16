"""Test focalizado de ``_search_searxng`` (FASE 8M.2, PASO B).

Verifica el parseo de la respuesta HTTP de la instancia local de SearXNG (mockeada,
sin red real) a la MISMA estructura que usan los demás backends de research
(title/url/snippet/content/source_type), y el fallback silencioso cuando el
servicio está caído o responde con error (no debe romper el job).

Función aislada: NO está integrada todavía en ``_multi_source_search`` ni en la
función orquestadora de research.
"""
from __future__ import annotations

import json

import modules.research.main as research

# Estructura real del JSON /search?format=json de SearXNG (subconjunto mínimo).
_FAKE_SEARXNG_RESP = {
    "results": [
        {
            "title": "Isaac Newton - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Isaac_Newton",
            "content": "Isaac Newton was a renowned scientist who contributed to physics.",
            "engine": "wikipedia",
        },
        {
            "title": "Isaac Newton - Wikipedia, la enciclopedia libre",
            "url": "https://es.wikipedia.org/wiki/Isaac_Newton",
            "content": "Fue también un pionero de la mecánica de fluidos.",
            "engine": "wikipedia",
        },
        # Resultado sin contenido (campo opcional en algunos engines de SearXNG)
        {"title": "Isaac Newton", "url": "https://example.org/newton", "engine": "duckduckgo"},
    ]
}


def test_search_searxng_mocked_http_parse_and_fallback(monkeypatch):
    """Un solo test focalizado: parseo correcto + fallback silencioso ante fallos."""
    captured: dict = {}

    def _fake_ok(url, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        return 200, json.dumps(_FAKE_SEARXNG_RESP).encode("utf-8")

    monkeypatch.setattr(research, "_request", _fake_ok)
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:9999")

    # --- Parseo positivo (limit=2 respeta el tope; estructura idéntica a backends) ---
    out = research._search_searxng("Isaac Newton", limit=2)
    assert captured["url"] == (
        "http://127.0.0.1:9999/search?q=Isaac+Newton&format=json"
    ), "debe usar SEARXNG_BASE_URL y el query/format correctos"
    assert captured["timeout"] == 7, "timeout corto por defecto (local)"
    assert len(out) == 2  # limit respetado, el 3er resultado sin content no desborda
    first = out[0]
    assert first["title"] == "Isaac Newton - Wikipedia"
    assert first["url"] == "https://en.wikipedia.org/wiki/Isaac_Newton"
    assert first["content"] == (
        "Isaac Newton was a renowned scientist who contributed to physics."
    )
    assert first["snippet"] == first["content"][:200]
    assert first["source_type"] == "web_searxng"

    # --- Fallback: SearXNG caído (excepción de red) -> lista vacía, no lanza ---
    def _fake_down(url, timeout=None):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(research, "_request", _fake_down)
    assert research._search_searxng("Isaac Newton") == []

    # --- Fallback: HTTP no 200 -> lista vacía, no lanza ---
    monkeypatch.setattr(research, "_request", lambda url, timeout=None: (500, b"oops"))
    assert research._search_searxng("Isaac Newton") == []
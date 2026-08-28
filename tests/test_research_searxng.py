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
# §17 #33 — Filtro de snippets de SERP: SearXNG devuelve el `content` como snippet
# del buscador (fecha "6 sept 2024", "hace N días", "…" de truncamiento), no
# contenido real. Contenidos VERBATIM reales de book_69/task_1486: las 6 fuentes
# SearXNG (snippets SERP) se descartan; las 3 de Wikipedia (limpias) se preservan.
_SERP_REAL_CASES = [
    # 3 fuentes de Wikipedia limpias (224-254 chars, SIN patrones SERP)
    ("Videoconsola, o simplemente una consola, es un sistema electrónico de entretenimiento que ejecuta videojuegos contenidos en cartuchos, discos ópticos, discos magnéticos o redes.", False),
    ("Carl Johnson, también conocido como C.J., es un personaje ficticio y el protagonista jugable del videojuego de 2004 Grand Theft Auto: San Andreas, la quinta entrega principal de la serie.", False),
    ("Claude es un personaje de ficción y el protagonista de Grand Theft Auto 2 y Grand Theft Auto III, videojuegos de la serie Grand Theft Auto de Rockstar Games.", False),
    # 6 fuentes SearXNG = snippets de SERP verbatim (153-166 chars) -> descartados
    ("6 sept 2024 ... El legado de Table Tennis y su importancia en juegos posteriores de Rockstar Games como GTA IV y Red Dead Redemption ... Table Tennis era, a todos ...", True),
    ("7 oct 2025 ... Rockstar Games sacó este juego hace casi 20 años. Table Tennis. Es un juego de ping pong ... GTA 4. Me pegó una picada histórica intentando ganar ...", True),
    ("hace 6 días ... Antes de ser la casa de GTA y Red Dead Redemption, Rockstar probó con tanques, skate, disturbios y hasta un juego de ping-pong. ... 4 puntos del ...", True),
    ("13 oct 2025 ... Es aquí donde comienza lo interesante, pues fue el primer videojuego en usar el propio motor de Rockstar, mejor conocido como RAGE, sirviendo ...", True),
    ("14 abr 2026 ... GTA, en una época también se había desquitado con un juego de Ping Pong ... @newgameplusok138 likes1.5K viewsStreamed 4 months ago more.", True),
    ("hace 6 días ... GTA - Antes de ser la casa de GTA y Red Dead Redemption, Rockstar probó con tanques, skate, disturbios y hasta un juego de ping-pong.", True),
]


def test_is_serp_snippet_real_book69_cases():
    """Las 6 fuentes SearXNG de task_1486 son snippets SERP; las 3 de Wikipedia no."""
    for content, expected in _SERP_REAL_CASES:
        assert research._is_serp_snippet(content) is expected, content[:80]


def test_is_serp_snippet_robust_to_empty_and_clean_long():
    """False para None/vacío y para contenido largo/limpio (evita falsos positivos)."""
    assert research._is_serp_snippet(None) is False
    assert research._is_serp_snippet("") is False
    long_clean = ("Un párrafo extenso de contenido real sobre la historia del videojuego y su "
                  "evolución técnica a lo largo de las décadas, sin fechas cortas de indexación "
                  "ni truncamientos del buscador, que aporta información verificable y razonada.")
    assert research._is_serp_snippet(long_clean) is False
    # mismo texto pero terminado en "..." -> snippet
    assert research._is_serp_snippet(long_clean + "...") is True


def test_search_searxng_filters_serp_snippets(monkeypatch):
    """_search_searxng descarta los 6 snippets SERP cortos y conserva el resto."""
    def _fake(url, timeout=None):
        resp = {"results": []}
        for title, content, is_serp in [
            ("W1", _SERP_REAL_CASES[0][0], False),
            ("S1", _SERP_REAL_CASES[3][0], True),
            ("S2", _SERP_REAL_CASES[5][0], True),
            ("S3", _SERP_REAL_CASES[8][0], True),
        ]:
            resp["results"].append({"title": title, "url": f"https://x/{title}", "content": content})
        return 200, json.dumps(resp).encode("utf-8")

    monkeypatch.setattr(research, "_request", _fake)
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://127.0.0.1:9999")
    out = research._search_searxng("pregunta", limit=10)
    urls = [r["url"] for r in out]
    assert len(out) == 1, f"debe filtrar los 3 SERP, quedó: {urls}"
    assert "W1" in urls[0], "la fuente limpia debe conservarse"
"""Tests del pipeline multi-fuente (PASO 3): dedupe, topes por backend, degrade.

Cubren `_multi_source_search` y su integración con los backends reales
(Wikipedia es, Wikidata, archive.org). Todos los backends se mueven con mocks:
no se hace ninguna llamada de red real.
"""
from __future__ import annotations

import pytest

import modules.research.main as main


@pytest.fixture(autouse=True)
def _stub_searxng(monkeypatch):
    """SearXNG (FASE 8M.2) queda stubbeado a [] en estos tests: el docstring del
    archivo proclama "no se hace ninguna llamada de red real". El backend real
    contra el contenedor local se prueba en tests/test_research_searxng.py y se
    valida el caso integrado en ``test_searxng_integrado_como_candidato``."""
    monkeypatch.setattr(main, "_search_searxng", lambda q, n, timeout: [])


def _cand(url: str, title: str, source_type: str, *, content: str = "", snippet: str = "") -> dict:
    return {
        "url": url,
        "title": title,
        "source_type": source_type,
        "content": content or f"Contenido de {title}",
        "snippet": snippet or f"Resumen de {title}",
    }


def test_dedup_across_backends_by_url_and_title(monkeypatch) -> None:
    """La misma (URL, título) procedente de dos backends se deduplica a una."""
    wiki = [_cand("https://es.wikipedia.org/wiki/A", "Primera", "web_wikipedia")]
    wdata = [_cand("https://es.wikipedia.org/wiki/A", "Primera", "web_wikidata")]
    monkeypatch.setattr(main, "_backend_wikipedia", lambda q, n, timeout: wiki)
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout: wdata)

    results = main._multi_source_search("tema", max_sources=5, timeout=20)
    urls = [c["url"] for c in results]
    assert urls.count("https://es.wikipedia.org/wiki/A") == 1
    assert len(results) == 1


def test_dedup_mismo_url_distinto_titulo_no_se_elimina(monkeypatch) -> None:
    """La clave es URL NORMALIZADA + título: mismo URL con título distinto no es duplicado."""
    a = _cand("https://es.wikipedia.org/wiki/X", "Título A", "web_wikipedia")
    b = _cand("https://es.wikipedia.org/wiki/X", "Título B", "web_wikipedia")
    monkeypatch.setattr(main, "_backend_wikipedia", lambda q, n, timeout: [a])
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout: [b])

    results = main._multi_source_search("tema", max_sources=5, timeout=20)
    assert len(results) == 2


def test_total_respetado_max_sources(monkeypatch) -> None:
    """Nunca se devuelven más de `max_sources` aunque los backends traigan mucho."""
    wiki = [_cand(f"https://es.wikipedia.org/wiki/T{i}", f"T{i}", "web_wikipedia") for i in range(10)]
    data = [_cand(f"https://www.wikidata.org/wiki/Q{i}", f"Q{i}", "web_wikidata") for i in range(10)]
    monkeypatch.setattr(main, "_backend_wikipedia", lambda q, n, timeout: wiki)
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout: data)

    results = main._multi_source_search("tema", max_sources=3, timeout=20)
    assert len(results) <= 3


def test_tope_duro_por_backend(monkeypatch) -> None:
    """Cada backend aporta a lo sumo `per_backend_limit` (= max_sources)."""
    captured = {}

    def fake_wiki(q, n, timeout):
        captured["n"] = n
        return [_cand(f"https://es.wikipedia.org/wiki/W{i}", f"W{i}", "web_wikipedia") for i in range(20)]

    monkeypatch.setattr(main, "_backend_wikipedia", fake_wiki)
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout: [])

    main._multi_source_search("tema", max_sources=4, timeout=20)
    assert captured["n"] == 4  # tope duro por backend == max_sources


def test_graceful_degrade_cuando_un_backend_falla(monkeypatch) -> None:
    """Si un backend lanza excepción, los demás siguen aportando sin romper el flujo."""
    def bomba(q, n, timeout):
        raise RuntimeError("backend fuera de servicio")

    monkeypatch.setattr(main, "_backend_wikipedia", bomba)
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout:
                        [_cand("https://www.wikidata.org/wiki/Q1", "Wikidata 1", "web_wikidata")])

    results = main._multi_source_search("tema", max_sources=5, timeout=20)
    assert len(results) == 1
    assert results[0]["source_type"] == "web_wikidata"


def test_graceful_degrade_url_sin_url_se_ignora(monkeypatch) -> None:
    """Candidatos sin URL se descartan en la dedup global."""
    wiki = [_cand("", "sin URL", "web_wikipedia")]
    monkeypatch.setattr(main, "_backend_wikipedia", lambda q, n, timeout: wiki)
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout: [])

    results = main._multi_source_search("test", max_sources=5, timeout=20)
    assert results == []


def test_archive_backend_habilitado_por_flag(monkeypatch) -> None:
    """archive.org solo participa cuando RESEARCH_ARCHIVE_ENABLED es True."""
    archive_called = {"n": 0}

    def fake_archive(q, n, timeout):
        archive_called["n"] += 1
        return [_cand("https://archive.org/details/it1", "Arch", "web_archiveorg")]

    monkeypatch.setattr(main, "RESEARCH_ARCHIVE_ENABLED", True)
    monkeypatch.setattr(main, "_backend_wikipedia", lambda q, n, timeout: [])
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout: [])
    monkeypatch.setattr(main, "_backend_archive", fake_archive)

    results = main._multi_source_search("a", max_sources=5, timeout=20)
    assert archive_called["n"] == 1
    assert any(c["source_type"] == "web_archiveorg" for c in results)


def test_archive_backend_deshabilitado_por_defecto(monkeypatch) -> None:
    """Sin flag explícito archive.org NO participa de la búsqueda."""
    archive_called = {"n": 0}

    def fake_archive(q, n, timeout):
        archive_called["n"] += 1
        return [_cand("https://archive.org/details/it1", "T", "web_archiveorg")]

    monkeypatch.setattr(main, "RESEARCH_ARCHIVE_ENABLED", False)
    monkeypatch.setattr(main, "_backend_wikipedia", lambda q, n, timeout: [])
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout: [])
    monkeypatch.setattr(main, "_backend_archive", fake_archive)

    main._multi_source_search("a", max_sources=5, timeout=20)
    assert archive_called["n"] == 0


def test_searxng_integrado_como_candidato(monkeypatch):
    """Caso SearXNG en el pool multi-fuente: un backend mockeado aporta candidatos
    con la estructura esperada (title/url/content/source_type=web_searxng)."""
    monkeypatch.setattr(
        main,
        "_search_searxng",
        lambda q, n, timeout: [
            _cand(
                "https://en.wikipedia.org/wiki/Solar_Solar",
                "Sistema solar",
                "web_searxng",
                content="El sistema solar es un sistema estelar.",
            ),
        ],
    )
    monkeypatch.setattr(main, "_backend_wikipedia", lambda q, n, timeout: [])
    monkeypatch.setattr(main, "_backend_wikidata", lambda q, n, timeout: [])

    results = main._multi_source_search("sistema solar", max_sources=5, timeout=20)
    sx = [c for c in results if c["source_type"] == "web_searxng"]
    assert len(sx) == 1
    assert sx[0]["url"] == "https://en.wikipedia.org/wiki/Solar_Solar"
    assert sx[0]["title"] == "Sistema solar"
    assert "sistema estelar" in sx[0]["content"]
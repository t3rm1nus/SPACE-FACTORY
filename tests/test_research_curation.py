"""Tests de curación (PASO 3): fallback determinista y anti-alucinación.

Cubren `_curate_with_llm`:
- fallback determinista ante fallo/timeout/JSON inválido del LLM,
- descarte de URLs inventadas por el LLM que no existen entre los candidatos,
- mutación de provider.timeout/max_retries ANTES de generate() (patrón test_editor.py:635).
"""
from __future__ import annotations

import json

import pytest

import modules.research.main as main


def _cand(url: str, title: str, *, content: str = "") -> dict:
    return {
        "url": url,
        "title": title,
        "source_type": "web_wikipedia",
        "content": content or f"Contenido largo de {title}",
        "snippet": f"Resumen de {title}",
    }


def _llm_text(payload: dict) -> str:
    return json.dumps(payload)


class _Result:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeProvider:
    name = "ollama"
    model = "qwen-agent:latest"
    timeout = 120
    max_retries = 3

    def __init__(self, *, result_text: str = "", error: Exception | None = None, consumed: dict | None = None) -> None:
        self._result_text = result_text
        self._error = error
        self._consumed = consumed if consumed is not None else {}

    def generate(self, *args, **kwargs):
        if self._consumed is not None:
            self._consumed["timeout"] = self.timeout
            self._consumed["max_retries"] = self.max_retries
        if self._error is not None:
            raise self._error
        return _Result(self._result_text)


CANDIDATES = [
    _cand("https://es.wikipedia.org/wiki/Gato", "Gato"),
    _cand("https://es.wikipedia.org/wiki/Felino", "Felino"),
    _cand("https://www.wikidata.org/wiki/Q146", "Gato (Wikidata)", content="gato gato gato"),
]


def test_provider_timeout_retries_mutated_before_generate(monkeypatch) -> None:
    """provider.timeout=40 y provider.max_retries=1 se fijan ANTES de generate()."""
    consumed: dict = {}
    provider = _FakeProvider(
        result_text=json.dumps({"sources": [{"url": CANDIDATES[0]["url"], "rank": 1}]}),
        consumed=consumed,
    )
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "RESEARCH_USE_LLM", "1")

    main._curate_with_llm("gato", CANDIDATES, "es", 3)
    assert consumed["timeout"] == main.RESEARCH_PROVIDER_TIMEOUT
    assert consumed["max_retries"] == main.RESEARCH_MAX_RETRIES


def test_llm_fallback_determinista_si_proveedor_falla(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_provider", lambda: _FakeProvider(error=RuntimeError("boom")))
    monkeypatch.setattr(main, "RESEARCH_USE_LLM", "1")
    sources, mode = main._curate_with_llm("gato", CANDIDATES, "es", 3)
    assert mode == "deterministic"
    assert len(sources) <= 3
    assert all(s["url"] in {c["url"] for c in CANDIDATES} for s in sources)


def test_llm_fallback_determinista_on_invalid_json(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_provider", lambda: _FakeProvider(result_text="esto no es json"))
    monkeypatch.setattr(main, "RESEARCH_USE_LLM", "1")
    sources, mode = main._curate_with_llm("gato", CANDIDATES, "es", 2)
    assert mode == "deterministic"
    assert len(sources) == 2


def test_llm_use_llm_disabled_returns_deterministic(monkeypatch) -> None:
    monkeypatch.setattr(main, "RESEARCH_USE_LLM", "0")

    def no_provider():
        raise AssertionError("no se debe llamar al proveedor con LLM deshabilitado")

    monkeypatch.setattr(main, "get_provider", no_provider)
    sources, mode = main._curate_with_llm("gato", CANDIDATES, "es", 2)
    assert mode == "deterministic"
    assert len(sources) == 2


def test_llm_descarta_url_inventada_y_rellena_con_determinista(monkeypatch) -> None:
    """URL que el LLM inventa NO entra; se rellena con el ranking determinista."""
    real_urls = {c["url"] for c in CANDIDATES}
    invented = "https://vulnerability.example/hacked"
    provider = _FakeProvider(
        result_text=json.dumps({"sources": [
            {"url": invented, "rank": 1},
            {"url": CANDIDATES[0]["url"], "rank": 2},
        ]})
    )
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "RESEARCH_USE_LLM", "1")

    sources, mode = main._curate_with_llm("gato", CANDIDATES, "es", 3)
    assert mode == "llm"
    urls = [s["url"] for s in sources]
    assert invented not in urls
    assert all(u in real_urls for u in urls)
    assert len(sources) == 3


def test_llm_sources_normalizada_coincide(monkeypatch) -> None:
    """La comparación de URLs tolera variaciones triviales (orden de query, slash final)."""
    cand = [{"url": "https://es.wikipedia.org/wiki/Gato?oldid=1", "title": "G", "source_type": "web_wikipedia", "content": "texto"}]
    # El LLM devuelve la URL con slash final y query sin ordenar -> debe cuadrar.
    monkeypatch.setattr(main, "get_provider", lambda: _FakeProvider(
        result_text=json.dumps({"sources": ["https://es.wikipedia.org/wiki/Gato?oldid=1"]})
    ))
    monkeypatch.setattr(main, "RESEARCH_USE_LLM", "1")
    sources, _ = main._curate_with_llm("Gato", cand, "es", 1)
    assert len(sources) == 1
    assert sources[0]["url"] == cand[0]["url"]
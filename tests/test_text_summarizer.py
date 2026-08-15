"""Tests unitarios del módulo text_summarizer."""

from __future__ import annotations

from typing import Any

import pytest

from modules.text_summarizer.main import (
    _build_prompt,
    _fallback_summary,
    execute,
    health_check,
)


class FakeResult:
    """Resultado normalizado simulado por un proveedor LLM."""

    def __init__(self, text: str = "Resumen conciso de ejemplo.") -> None:
        self.text = text
        self.provider = "ollama"
        self.model = "llama3.1"
        self.input_tokens = 12
        self.output_tokens = 9
        self.cost = 0.0
        self.raw_response = {"model": "llama3.1", "response": text}


class _FailProvider:
    name = "ollama"
    model = "llama3.1"

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("ollama no disponible")


def _ok_provider_factory(captured: dict[str, Any] | None = None):
    class _OkProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            if captured is not None:
                captured.update(kwargs)
            return FakeResult("Resumen conciso de ejemplo.")

    return _OkProvider


def _payload(text: str = "Texto largo para resumir. Con segunda oración.", max_words: int | None = None) -> dict:
    payload: dict = {"text": text}
    if max_words:
        payload["max_words"] = max_words
    return payload


def test_health_check_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """health_check sobrevive si get_provider falla al instanciar."""
    import modules.text_summarizer.main as main

    def _boom() -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "get_provider", _boom)
    result = health_check()
    assert result["healthy"] is False
    assert "error" in result["dependencies"]


def test_execute_fallback_when_llm_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM falla, execute devuelve un resumen determinista."""
    import modules.text_summarizer.main as main

    monkeypatch.setattr(main, "get_provider", lambda: _FailProvider())
    out = execute(_payload())
    assert out["provider"] == "fallback"
    assert out["summary"]
    assert out["tokens_input"] == 0
    assert out["tokens_output"] == 0
    assert out["cost"] == 0.0
    assert out["original_length"] == len("Texto largo para resumir. Con segunda oración.")


def test_execute_llm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el proveedor responde, execute usa la respuesta del LLM."""
    import modules.text_summarizer.main as main

    captured: dict[str, Any] = {}
    monkeypatch.setattr(main, "get_provider", lambda: _ok_provider_factory(captured)())
    out = execute(_payload())
    assert out["provider"] == "ollama"
    assert out["summary"] == "Resumen conciso de ejemplo."
    assert out["tokens_input"] == 12
    assert out["tokens_output"] == 9
    assert out["model"] == "llama3.1"
    # La llamada al provider usa la interfaz normalizada (model, max_tokens, temperature)
    assert captured["model"] == main.DEFAULT_ROUTER_MODEL
    assert captured["max_tokens"] == 300


def test_build_prompt_and_fallback_summary() -> None:
    validated = {"text": "Este es un texto. Con segunda oración.", "max_words": None}
    prompt = _build_prompt(validated, None)
    assert "Este es un texto." in prompt
    summary = _fallback_summary(validated, None)
    assert summary.startswith("Este es un texto")
        # max_words limita a N palabras la salida del fallback
    assert _fallback_summary(validated, 4) == "Este es un texto"

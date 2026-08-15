"""Tests de Hermes Master (core.central_ai)."""

from __future__ import annotations

import core.central_ai as central_ai
from core.central_ai import _fallback_by_priority, choose_module


class FakeProvider:
    name = "ollama"
    model = "llama3.1"

    def generate(self, prompt: str, *, system=None, model=None, max_tokens=None, temperature=None, **kwargs):
        return FakeResult()


class FakeResult:
    text = "book_planner"
    provider = "ollama"
    model = "llama3.1"
    input_tokens = 10
    output_tokens = 5
    cost = 0.0
    raw_response = {}


def _modules():
    return [
        {
            "manifest": {
                "id": "book_planner",
                "name": "Planificador",
                "description": "Planifica libros",
                "type": "agent",
                "capabilities": ["create_book_plan"],
                "config": {"priority": 5},
            }
        },
        {
            "manifest": {
                "id": "chapter_writer",
                "name": "Escritor",
                "description": "Escribe capítulos",
                "type": "agent",
                "capabilities": ["create_book_plan"],
                "config": {"priority": 10},
            }
        },
    ]


def test_choose_module_single_candidate() -> None:
    modules = [_modules()[0]]
    assert choose_module("create_book_plan", modules, {}) == "book_planner"


def test_choose_module_uses_router_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(central_ai, "get_provider", lambda: FakeProvider())
    result = choose_module("create_book_plan", _modules(), {})
    assert result == "book_planner"


def test_choose_module_fallback_when_router_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args, **kwargs):
            raise RuntimeError("ollama no disponible")

    monkeypatch.setattr(central_ai, "get_provider", lambda: FailProvider())
    result = choose_module("create_book_plan", _modules(), {})
    assert result == "book_planner"


def test_fallback_by_priority() -> None:
    modules = _modules()
    assert _fallback_by_priority(modules) == "book_planner"

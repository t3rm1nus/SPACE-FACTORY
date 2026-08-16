"""Tests unitarios del módulo book_planner."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from modules.book_planner.main import (
    DEFAULT_IMAGE_REQUIREMENTS,
    _build_prompt,
    _coerce_image_requirements,
    _extract_json,
    _fallback_plan,
    _normalize_plan,
    _resolve_explicit_image_count,
    execute,
    health_check,
)
from core.schemas import BookPlanPayload


def _payload() -> dict:
    return {
        "idea": "Novela corta de ciencia ficción",
        "target_chapters": 25,
        "language": "es",
        "target_audience": "adultos",
        "desired_length": "corta",
        "style": "narrativa",
        "subject_constraints": "sin violencia explícita",
    }


def test_health_check_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """health_check debe sobrevivir si el provider falla al instanciar."""
    import modules.book_planner.main as main

    monkeypatch.setattr(main, "get_provider", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = health_check()
    assert result["healthy"] is False
    assert "error" in result["dependencies"]


def test_build_prompt_contains_idea() -> None:
    prompt = _build_prompt(BookPlanPayload(**_payload()))
    assert "Novela corta de ciencia ficción" in prompt
    assert "25" in prompt


def test_extract_json_happy_path() -> None:
    text = 'antes {"title":"x","chapters":[]} despues'
    data = _extract_json(text)
    assert data["title"] == "x"


def test_extract_json_raises_if_missing() -> None:
    with pytest.raises(ValueError):
        _extract_json("sin json aqui")


def test_fallback_plan_shape() -> None:
    plan = _fallback_plan(BookPlanPayload(**_payload()))
    assert plan["title"] == "Novela corta de ciencia ficción"
    assert len(plan["chapters"]) == 25
    assert plan["chapters"][0]["number"] == 1


def test_execute_fallback_when_llm_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM falla, execute debe devolver un plan fallback válido."""
    import modules.book_planner.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ollama no disponible")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())

    out = execute(_payload())
    assert out["title"] == "Novela corta de ciencia ficción"
    assert len(out["chapters"]) == 25
    assert out["provider"] == "ollama"
    assert out["cost"] == 0.0
    assert out["tokens_input"] == 0
    assert out["tokens_output"] == 0


def test_execute_llm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM responde con JSON válido, execute lo valida y normaliza."""
    import modules.book_planner.main as main

    plan_json = json.dumps(
        {
            "title": "Título generado",
            "subtitle": "Subtítulo",
            "description": "Descripción",
            "target_audience": "adultos",
            "chapters": [
                {
                    "number": 1,
                    "title": "Capítulo 1",
                    "objective": "Objetivo 1",
                    "key_questions": ["Q1"],
                    "estimated_words": 3000,
                    "research_requirements": ["investigar X"],
                    "image_requirements": 2,
                }
            ],
        }
    )

    class FakeResult:
        text = plan_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": plan_json}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["title"] == "Título generado"
    assert len(out["chapters"]) == 1
    assert out["provider"] == "ollama"
    assert out["tokens_input"] == 10
    assert out["tokens_output"] == 20


def test_execute_invalid_llm_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM devuelve texto sin JSON, execute cae a fallback."""
    import modules.book_planner.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any):
            class R:
                text = "esto no es json"
                provider = "ollama"
                model = "llama3.1"
                input_tokens = 1
                output_tokens = 1
                cost = 0.0
                raw_response = {}

            return R()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["title"] == "Novela corta de ciencia ficción"
    assert len(out["chapters"]) == 25


def test_execute_normalizes_chapter_count() -> None:
    """Ejecuta el fallback directo y comprueba el rango 20..40 capítulos."""
    # payload con 30 capítulos objetivo
    plan = _fallback_plan(BookPlanPayload(**_payload()))
    assert 20 <= len(plan["chapters"]) <= 40
    # si pedimos 20, respeta 20
    payload20 = _payload()
    payload20["target_chapters"] = 20
    plan20 = _fallback_plan(BookPlanPayload(**payload20))
    assert len(plan20["chapters"]) == 20
    # si pedimos 40, respeta 40
    payload40 = _payload()
    payload40["target_chapters"] = 40
    plan40 = _fallback_plan(BookPlanPayload(**payload40))
    assert len(plan40["chapters"]) == 40


@pytest.mark.parametrize(
    "raw,expected",
    [
        (3, 3),
        (0, 0),
        (20, 20),
        (25, 20),  # fuera de rango -> clamped
        ([1, 2, 3], 3),  # lista -> len
        ([1], 1),
        ([], 0),  # lista vacía -> 0
        ("3", 3),  # string numérico
        ("0", 0),
        ("seven", DEFAULT_IMAGE_REQUIREMENTS),  # string no numérico -> default
        ("", DEFAULT_IMAGE_REQUIREMENTS),  # string vacío -> default
        (None, DEFAULT_IMAGE_REQUIREMENTS),  # null -> default
        (True, 1),  # bool True
        (False, 0),  # bool False
        (3.9, 3),  # float -> int
    ],
)
def test_coerce_image_requirements(raw, expected) -> None:
    """image_requirements debe normalizarse a int válido de forma determinista."""
    assert _coerce_image_requirements(raw) == expected


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"image_count": 0}, 0),
        ({"image_count": 2}, 2),
        ({"num_images": 3}, 3),
        ({"images": False}, 0),
        ({"images": True}, None),  # True no fuerza; respeta LLM
        ({"images": 0}, 0),
        ({"images": "false"}, 0),
        ({"images": "0"}, 0),
        ({}, None),
        ({"num_images": None}, None),
        ({"image_count": "abc"}, None),  # no numérico -> None (se respeta LLM)
    ],
)
def test_resolve_explicit_image_count(payload, expected) -> None:
    """La config explícita del workflow (image_count/num_images/images) tiene prioridad."""
    assert _resolve_explicit_image_count(payload) == expected


def test_normalize_plan_normalizes_imperfect_llm_output() -> None:
    """Normaliza listas, strings y nulls a int válidos por capítulo."""
    plan = {
        "title": "Test",
        "chapters": [
            {"number": 1, "title": "A", "objective": "O", "image_requirements": 3},
            {"number": 2, "title": "B", "objective": "O", "image_requirements": "3"},
            {"number": 3, "title": "C", "objective": "O", "image_requirements": None},
            {"number": 4, "title": "D", "objective": "O", "image_requirements": [1, 2, 3]},
            {"number": 5, "title": "E", "objective": "O", "image_requirements": []},
            {"number": 6, "title": "F", "objective": "O", "image_requirements": "chaos"},
        ],
    }
    out = _normalize_plan(plan, {})
    ir = {ch["number"]: ch["image_requirements"] for ch in out["chapters"]}
    assert ir == {
        1: 3,
        2: 3,
        3: DEFAULT_IMAGE_REQUIREMENTS,
        4: 3,
        5: 0,
        6: DEFAULT_IMAGE_REQUIREMENTS,
    }


def test_normalize_plan_passes_non_dict_chapters_untouched() -> None:
    """Capítulos no dict se dejan pasar para que Pydantic reporte el error claro."""
    plan = {"chapters": ["no es dict"]}
    out = _normalize_plan(plan, {})
    assert out["chapters"] == ["no es dict"]


def test_normalize_plan_without_chapters_passthrough() -> None:
    """Si el LLM no devuelve chapters, se deja el plan intacto."""
    plan = {"title": "Test", "chapters": None}
    out = _normalize_plan(plan, {})
    assert out["chapters"] is None


def test_normalize_plan_respects_image_count_zero_override() -> None:
    """image_count=0 del workflow fuerza todos los capítulos a 0, incluso si el LLM sugirió 3."""
    plan = {
        "title": "Test",
        "chapters": [
            {"number": 1, "title": "A", "objective": "O", "image_requirements": 3},
            {"number": 2, "title": "B", "objective": "O", "image_requirements": 5},
        ],
    }
    out = _normalize_plan(plan, {"image_count": 0})
    assert all(ch["image_requirements"] == 0 for ch in out["chapters"])


def test_normalize_plan_respects_positive_image_count_override() -> None:
    """Un image_count explícito positivo tiene prioridad sobre la sugestión del LLM."""
    plan = {
        "title": "Test",
        "chapters": [
            {"number": 1, "title": "A", "objective": "O", "image_requirements": 3},
            {"number": 2, "title": "B", "objective": "O", "image_requirements": 5},
        ],
    }
    out = _normalize_plan(plan, {"image_count": 2})
    assert all(ch["image_requirements"] == 2 for ch in out["chapters"])


def test_execute_respects_image_count_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute debe forzar image_requirements=0 cuando el workflow lo pide (image_count=0)."""
    import modules.book_planner.main as main

    plan_json = json.dumps(
        {
            "title": "Título generado",
            "subtitle": "Subtítulo",
            "description": "Descripción",
            "target_audience": "adultos",
            "chapters": [
                {
                    "number": 1,
                    "title": "Capítulo 1",
                    "objective": "Objetivo 1",
                    "image_requirements": 3,
                }
            ],
        }
    )

    class FakeResult:
        text = plan_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": plan_json}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["image_count"] = 0
    out = execute(payload)
    assert out["chapters"][0]["image_requirements"] == 0


def test_execute_keeps_llm_image_requirements_when_no_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin config explícita, la sugerencia del LLM (normalizada) se respeta."""
    import modules.book_planner.main as main

    plan_json = json.dumps(
        {
            "title": "Título generado",
            "subtitle": "Subtítulo",
            "description": "Descripción",
            "target_audience": "adultos",
            "chapters": [
                {
                    "number": 1,
                    "title": "Capítulo 1",
                    "objective": "Objetivo 1",
                    "image_requirements": 2,
                }
            ],
        }
    )

    class FakeResult:
        text = plan_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": plan_json}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any):
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["chapters"][0]["image_requirements"] == 2


def test_normalize_plan_corrects_invalid_estimated_words(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un plan con estimated_words=300 debe ser normalizado a 500 antes de validar."""
    import modules.book_planner.main as main

    plan = {
        "title": "Test",
        "chapters": [
            {"number": 1, "title": "A", "objective": "O", "estimated_words": 300},
        ],
    }
    out = _normalize_plan(plan, {})
    assert out["chapters"][0]["estimated_words"] == 500


def test_normalize_plan_keeps_valid_estimated_words() -> None:
    """Un plan con estimated_words >= 500 no debe ser modificado."""
    plan = {
        "title": "Test",
        "chapters": [
            {"number": 1, "title": "A", "objective": "O", "estimated_words": 3000},
        ],
    }
    out = _normalize_plan(plan, {})
    assert out["chapters"][0]["estimated_words"] == 3000


def test_build_prompt_requires_estimated_words_minimum() -> None:
    """El prompt debe advertir que estimated_words es por capítulo y >= 500."""
    prompt = _build_prompt(BookPlanPayload(**_payload()))
    assert "estimated_words" in prompt
    assert "POR CAPÍTULO" in prompt
    assert ">= 500" in prompt
    assert "target_words" in prompt


def test_execute_normalizes_low_estimated_words_from_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el LLM devuelve estimated_words=300, execute lo corrige a 500."""
    import modules.book_planner.main as main

    plan_json = json.dumps(
        {
            "title": "Título generado",
            "subtitle": "Subtítulo",
            "description": "Descripción",
            "target_audience": "adultos",
            "chapters": [
                {
                    "number": 1,
                    "title": "Capítulo 1",
                    "objective": "Objetivo 1",
                    "estimated_words": 300,
                    "image_requirements": 0,
                }
            ],
        }
    )

    class FakeResult:
        text = plan_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": plan_json}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any):
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["chapters"][0]["estimated_words"] >= 500


def test_normalize_plan_guarantees_sections_systematic() -> None:
    """FASE 1 (outline vacío): sections debe quedar NO vacía en TODO capítulo.

    El writer depende de chapter_outline.sections para estructurar y continuar;
    cuando el planner/outline devuelve sections=None, ausente o [], el pipeline
    dispara NO_TARGET_SECTION y nunca alcanza el mínimo de palabras. Este test
    garantiza que _normalize_plan siempre deje una lista de secciones no vacía.
    """
    plan = {
        "title": "Test",
        "language": "es",
        "chapters": [
            {"number": 1, "title": "A", "objective": "O", "sections": None},
            {"number": 2, "title": "B", "objective": "O"},  # clave ausente
            {"number": 3, "title": "C", "objective": "O", "sections": []},
            {"number": 4, "title": "D", "objective": "O", "sections": [{}]},
        ],
    }
    out = _normalize_plan(plan, {"language": "es"})
    for ch in out["chapters"]:
        sections = ch.get("sections")
        assert isinstance(sections, list) and sections, f"ch{ch['number']} sin sections"
        for s in sections:
            assert (s.get("heading") or "").strip()


def test_normalize_plan_keeps_custom_sections() -> None:
    """Si el LLM devuelve secciones válidas (con heading), se conservan tal cual."""
    plan = {
        "title": "Test",
        "language": "es",
        "chapters": [
            {
                "number": 1,
                "title": "A",
                "objective": "O",
                "sections": [
                    {"heading": "Orígenes", "objective": "Historia"},
                    {"heading": "Impacto", "objective": "Consecuencias"},
                ],
            },
        ],
    }
    out = _normalize_plan(plan, {"language": "es"})
    headings = [s["heading"] for s in out["chapters"][0]["sections"]]
    assert headings == ["Orígenes", "Impacto"]


def test_prompt_requires_sections_field() -> None:
    """El prompt del planner debe pedir sections por capítulo (fallback determinista)."""
    import modules.book_planner.main as main

    prompt = _build_prompt(BookPlanPayload(**_payload()))
    assert "sections" in prompt
    assert "Nunca omitas sections" in prompt

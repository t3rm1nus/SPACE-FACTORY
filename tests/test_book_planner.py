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


def test_fallback_plan_titles_short_no_prefix_long_idea() -> None:
    """Caso real book_55: idea de ~200 chars NO debe acabar baked-in en el título
    del capítulo, ni traer prefijo 'Capítulo N:' (lo añade document_builder)."""
    long_idea = (
        "Protocolo de 30 días para recuperar tu enfoque en la era digital. "
        "Basado en neurociencia, sin postureo. Aprende a entrenar tu atención, "
        "domar la fatiga mental y usar el aburrimiento como herramienta creativa"
    )
    assert len(long_idea) >= 150  # sanity: idea larga, como el caso real
    payload = _payload()
    payload["idea"] = long_idea
    plan = _fallback_plan(BookPlanPayload(**payload))
    for ch in plan["chapters"]:
        title = ch["title"]
        # No contiene la idea completa (ni siquiera su primera frase entera)
        assert long_idea not in title
        # Título corto acotado (8 palabras + " - Parte N")
        assert len(title) <= 100
        # Sin prefijo "Capítulo N:" baked-in
        assert not title.lower().startswith("capítulo ")
    # Los títulos varían entre capítulos (" - Parte {i}")
    titles = {ch["title"] for ch in plan["chapters"]}
    assert len(titles) == len(plan["chapters"])


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
                    "number": i,
                    "title": f"Capítulo {i}",
                    "objective": f"Objetivo {i}",
                    "key_questions": [f"Q{i}"],
                    "estimated_words": 3000,
                    "research_requirements": ["investigar X"],
                    "image_requirements": 2,
                }
                for i in range(1, _payload()["target_chapters"] + 1)
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
    assert len(out["chapters"]) == _payload()["target_chapters"]
    assert out["provider"] == "ollama"
    assert out["tokens_input"] == 10
    assert out["tokens_output"] == 20


def test_execute_falls_back_when_llm_returns_fewer_chapters_than_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON válido pero incompleto (menos capítulos que target) cae al fallback.

    Reproduce el caso real del libro 82 / repro offline: el LLM devolvió 1
    capítulo válido cuando se pidieron 8/25. Antes del fix se aceptaba tal cual;
    ahora el conteo se valida y se cae al fallback determinista (target capítulos).
    """
    import modules.book_planner.main as main

    plan_json = json.dumps(
        {
            "title": "Título incompleto",
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
                    "research_requirements": [],
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
    # Cae al fallback determinista: título de la idea y target capítulos.
    assert out["title"] == _payload()["idea"]
    assert len(out["chapters"]) == _payload()["target_chapters"]


def test_execute_accepts_llm_plan_with_exact_or_more_chapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión: un plan del LLM con >= target capítulos NO cae a fallback.

    Comportamiento actual intacto: el fix solo rechaza planes con MENOS
    capítulos de los pedidos; exactos o superiores se aceptan sin fallback.
    """
    import modules.book_planner.main as main

    target = _payload()["target_chapters"]

    def _llm_plan(n_chapters: int) -> str:
        return json.dumps(
            {
                "title": "Título generado",
                "subtitle": "Subtítulo",
                "description": "Descripción",
                "target_audience": "adultos",
                "chapters": [
                    {
                        "number": i,
                        "title": f"Capítulo {i}",
                        "objective": f"Objetivo {i}",
                        "key_questions": [f"Q{i}"],
                        "estimated_words": 3000,
                        "research_requirements": [],
                        "image_requirements": 2,
                    }
                    for i in range(1, n_chapters + 1)
                ],
            }
        )

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"
        text_plan = None

        def generate(self, *args: Any, **kwargs: Any):
            class R:
                pass

            r = R()
            r.text = FakeProvider.text_plan
            r.provider = "ollama"
            r.model = "llama3.1"
            r.input_tokens = 10
            r.output_tokens = 20
            r.cost = 0.0
            r.raw_response = {"model": "llama3.1", "response": FakeProvider.text_plan}
            return r

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    # Caso 1: exactamente target capítulos → NO fallback, título del LLM.
    FakeProvider.text_plan = _llm_plan(target)
    out = execute(_payload())
    assert out["title"] == "Título generado"
    assert len(out["chapters"]) == target

    # Caso 2: más capítulos que target → NO fallback, se aceptan todos.
    FakeProvider.text_plan = _llm_plan(target + 2)
    out = execute(_payload())
    assert out["title"] == "Título generado"
    assert len(out["chapters"]) == target + 2


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
                    "number": i,
                    "title": f"Capítulo {i}",
                    "objective": f"Objetivo {i}",
                    "image_requirements": 2,
                }
                for i in range(1, _payload()["target_chapters"] + 1)
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

# ---------------------------------------------------------------------------
# §17 #20 PASO 3 — anclaje del outline a fuentes reales (caso book_59)
# ---------------------------------------------------------------------------


def test_build_prompt_includes_sources_when_present() -> None:
    """Payload con sources → el prompt incluye títulos de fuentes + anclaje."""
    payload = _payload()
    payload["sources"] = [
        {"title": "Guerra de Gaza - Wikipedia", "url": "https://es.wikipedia.org/wiki/Guerra_de_Gaza", "summary": "La guerra comenzó el 7 de octubre de 2023."},
        {"title": "Genocidio cultural", "url": "https://es.wikipedia.org/wiki/Genocidio_cultural", "summary": "Destrucción deliberada del patrimonio cultural."},
    ]
    prompt = _build_prompt(BookPlanPayload(**payload))
    assert "Guerra de Gaza - Wikipedia" in prompt
    assert "Fuentes disponibles:" in prompt
    assert "REGLA DE ANCLAJE A FUENTES" in prompt
    assert "sin inventar hechos, nombres propios ni cifras específicas" in prompt


def test_build_prompt_unchanged_without_sources() -> None:
    """Sin sources (None) el prompt es IDÉNTICO al histórico (cero regresión
    para ficción / libros sin research)."""
    base = _payload()
    p_old = BookPlanPayload(**base)
    prompt_none = _build_prompt(p_old)
    # Sin bloque de fuentes ni mención de anclaje
    assert "Fuentes disponibles:" not in prompt_none
    assert "REGLA DE ANCLAJE A FUENTES" not in prompt_none
    # Fragmentos clave del prompt histórico intactos y en orden
    assert "No inventar hechos; separar hechos de hipótesis cuando aplique." in prompt_none
    assert prompt_none.index("Restricciones temáticas") < prompt_none.index("REGLAS:")
    # Y con sources=[] explícito, mismo resultado que None
    base2 = dict(base)
    base2["sources"] = []
    assert _build_prompt(BookPlanPayload(**base2)) == prompt_none


def test_reproduces_book59_scenario() -> None:
    """Caso REAL book_59: idea sensible + las 4 fuentes reales cortas.

    El prompt debe incluir las fuentes y la instrucción de anclaje, y NO debe
    contener mecanismo alguno que fuerce secciones no ancladas (el outline
    cerrado/obligatorio se mantiene, pero ahora se genera con material real).
    """
    payload = {
        "idea": "Historia Completa del Genocidio en Palestina",
        "target_chapters": 3,
        "language": "es",
        "sources": [
            {"title": "Limpieza étnica, ocupación militar y genocidio en Palestina - EHU", "url": "https://www.ehu.eus/es/web/campusa/-/limpieza-etnica-ocupacion-militar-y-genocidio-en-palestina", "summary": "Entre 1947-1949, las milicias sionistas expulsaron del territorio de la Palestina histórica."},
            {"title": "Palestina: genocidio y guerra de liberación - litci.org", "url": "https://litci.org/es/palestina-genocidio-y-guerra-de-liberacion/", "summary": "Estos gobiernos se limitan a realizar protestas verbales contra el genocidio."},
            {"title": "Genocidio cultural", "url": "https://es.wikipedia.org/wiki/Genocidio_cultural", "summary": "El genocidio cultural es la destrucción deliberada del patrimonio cultural."},
            {"title": "Guerra de Gaza - Wikipedia", "url": "https://es.wikipedia.org/wiki/Guerra_de_Gaza", "summary": "La guerra de Gaza comenzó el 7 de octubre de 2023."},
        ],
    }
    prompt = _build_prompt(BookPlanPayload(**payload))
    assert "Historia Completa del Genocidio en Palestina" in prompt
    for title in (
        "EHU",
        "litci.org",
        "Genocidio cultural",
        "Guerra de Gaza",
    ):
        assert title in prompt
    assert "ancla los headings y objectives de cada sección a temas que ellas soporten" in prompt
    # No existe instrucción que obligue a cubrir temas sin soporte
    assert "inventar hechos, nombres propios ni cifras específicas" in prompt


# ---------------------------------------------------------------------------
# §17 #21 (Opción A) — plan bilingüe: traducción EN con UNA llamada LLM extra,
# validación all-or-nothing y fallback determinista sin red.
# ---------------------------------------------------------------------------
def _bilingual_payload() -> dict:
    p = _payload()
    p["target_chapters"] = 2
    p["language"] = "es,en"
    return p


_ES_PLAN = {
    "title": "Alimentacion sana",
    "subtitle": "Sub",
    "description": "Guia de vida longeva",
    "chapters": [
        {
            "number": 1,
            "title": "Fundamentos de la alimentacion",
            "objective": "Objetivo 1",
            "estimated_words": 3000,
            "sections": [
                {"heading": "Introducción", "objective": "Presentar el tema"},
                {"heading": "Desarrollo", "objective": "Desarrollar puntos"},
                {"heading": "Conclusión", "objective": "Sintetizar ideas"},
            ],
        },
        {
            "number": 2,
            "title": "Ejercicio y longevidad",
            "objective": "Objetivo 2",
            "estimated_words": 3000,
            "sections": [
                {"heading": "Introducción", "objective": "Presentar el ejercicio"},
                {"heading": "Conclusión", "objective": "Cerrar el capítulo"},
            ],
        },
    ],
}

_EN_TRANSLATION_OK = {
    "title_en": "Healthy Eating",
    "description_en": "Guide to a long life",
    "chapters": [
        {
            "title_en": "Foundations of nutrition",
            "sections": [
                {"heading_en": "Introduction", "objective_en": "Present the topic"},
                {"heading_en": "Development", "objective_en": "Develop points"},
                {"heading_en": "Conclusion", "objective_en": "Synthesize ideas"},
            ],
        },
        {
            "title_en": "Exercise and longevity",
            "sections": [
                {"heading_en": "Introduction", "objective_en": "Present exercise"},
                {"heading_en": "Conclusion", "objective_en": "Close the chapter"},
            ],
        },
    ],
}


class _FakeResult:
    def __init__(self, text: str):
        self.text = text
        self.provider = "ollama"
        self.model = "llama3.1"
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self.raw_response = {}


class _FakeSeqProvider:
    """Proveedor controlado: devuelve respuestas en orden y registra prompts."""

    name = "ollama"
    model = "llama3.1"
    timeout = None

    def __init__(self, texts: list):
        self._texts = list(texts)
        self.prompts: list[str] = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        if not self._texts:
            raise RuntimeError("no stub response left")
        item = self._texts.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResult(item)


def _run_execute(monkeypatch, provider):
    import modules.book_planner.main as main

    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")
    return execute(_bilingual_payload()), provider


def test_j_bilingual_plan_translates_titles_and_sections_llm_ok(monkeypatch) -> None:
    """Traducción exitosa: título/descripción del libro + títulos/secciones de
    TODOS los capítulos quedan poblados, mismo orden y mismo conteo."""
    provider = _FakeSeqProvider([
        json.dumps(_ES_PLAN),
        json.dumps(_EN_TRANSLATION_OK),
    ])
    out, provider = _run_execute(monkeypatch, provider)
    assert out["title_en"] == "Healthy Eating"
    assert out["description_en"] == "Guide to a long life"
    assert [c["title_en"] for c in out["chapters"]] == [
        "Foundations of nutrition", "Exercise and longevity",
    ]
    first_outline = out["chapters"][0]["outline_en"]
    assert isinstance(first_outline, list)
    assert [s["heading"] for s in first_outline] == [
        "Introduction", "Development", "Conclusion",
    ]
    # La llamada de traducción incluyó título y descripción del libro (Amendment 1).
    assert len(provider.prompts) == 2
    assert "Alimentacion sana" in provider.prompts[1]
    assert "Guia de vida longeva" in provider.prompts[1]


def test_k_bilingual_plan_chapter_count_mismatch_discards_translation(monkeypatch) -> None:
    """LLM devuelve menos capítulos de los esperado → TODO descartado a None,
    sin excepción propagada."""
    bad = json.loads(json.dumps(_EN_TRANSLATION_OK))
    bad["chapters"] = bad["chapters"][:1]
    provider = _FakeSeqProvider([json.dumps(_ES_PLAN), json.dumps(bad)])
    out, _ = _run_execute(monkeypatch, provider)
    assert out["title_en"] is None
    assert out["description_en"] is None
    for c in out["chapters"]:
        assert c["title_en"] is None and c["outline_en"] is None


def test_l_bilingual_plan_section_mismatch_in_one_chapter_discards_all(monkeypatch) -> None:
    """Un solo capítulo con nº de secciones distinto → se descarta el resultado
    COMPLETO (nunca traducción parcial)."""
    bad = json.loads(json.dumps(_EN_TRANSLATION_OK))
    bad["chapters"][1]["sections"].append(
        {"heading_en": "Extra", "objective_en": "Extra objective"}
    )
    provider = _FakeSeqProvider([json.dumps(_ES_PLAN), json.dumps(bad)])
    out, _ = _run_execute(monkeypatch, provider)
    assert out["title_en"] is None
    for c in out["chapters"]:
        assert c["title_en"] is None and c["outline_en"] is None


def test_m_bilingual_plan_discards_when_translation_empty_or_identical(monkeypatch) -> None:
    """Regla dura: heading_en vacío en un capítulo → todo descartado."""
    bad = json.loads(json.dumps(_EN_TRANSLATION_OK))
    bad["chapters"][0]["sections"][0]["heading_en"] = "   "
    provider = _FakeSeqProvider([json.dumps(_ES_PLAN), json.dumps(bad)])
    out, _ = _run_execute(monkeypatch, provider)
    assert out["title_en"] is None
    for c in out["chapters"]:
        assert c["title_en"] is None and c["outline_en"] is None


def test_n_bilingual_plan_identical_to_spanish_discards(monkeypatch) -> None:
    """JSON devuelto byte-idéntico al original ES (sin traducir) → descartado."""
    identical = {
        "title_en": _ES_PLAN["title"],
        "description_en": _ES_PLAN["description"],
        "chapters": [
            {
                "title_en": ch["title"],
                "sections": [
                    {"heading_en": s["heading"], "objective_en": s["objective"]}
                    for s in ch["sections"]
                ],
            }
            for ch in _ES_PLAN["chapters"]
        ],
    }
    provider = _FakeSeqProvider([json.dumps(_ES_PLAN), json.dumps(identical)])
    out, _ = _run_execute(monkeypatch, provider)
    assert out["title_en"] is None
    for c in out["chapters"]:
        assert c["title_en"] is None and c["outline_en"] is None


def test_o_monolingual_es_plan_skips_translation_no_extra_llm_call(monkeypatch) -> None:
    """Regresión cero: libro 'es' → una sola llamada LLM, campos _en None."""
    import modules.book_planner.main as main

    # Mock local con el conteo requerido (target_chapters=25) para que el plan
    # LLM SÍ cumpla la validación de conteo y no caiga al fallback. Replica la
    # estructura de _ES_PLAN (title="Alimentacion sana", secciones canónicas ES)
    # dejando intacto el _ES_PLAN compartido que usan los tests bilingües.
    es_plan_25 = {
        "title": "Alimentacion sana",
        "subtitle": "Sub",
        "description": "Guia de vida longeva",
        "chapters": [
            {
                "number": i,
                "title": f"Capítulo {i}",
                "objective": f"Objetivo {i}",
                "estimated_words": 3000,
                "sections": [
                    {"heading": "Introducción", "objective": "Presentar el tema"},
                    {"heading": "Desarrollo", "objective": "Desarrollar puntos"},
                    {"heading": "Conclusión", "objective": "Sintetizar ideas"},
                ],
            }
            for i in range(1, _payload()["target_chapters"] + 1)
        ],
    }
    provider = _FakeSeqProvider([json.dumps(es_plan_25)])
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")
    out = execute(_payload())
    assert len(provider.prompts) == 1
    assert out["title_en"] is None
    for c in out["chapters"]:
        assert c["title_en"] is None and c["outline_en"] is None
    # Comportamiento histórico intacto
    assert out["title"] == "Alimentacion sana"
    assert len(out["chapters"]) == _payload()["target_chapters"]


def test_p_fallback_plan_populates_outline_en_deterministically(monkeypatch) -> None:
    """Fallback determinista SIN segunda llamada LLM: outline_en solo si las
    secciones son las canónicas ES (mapeo Introducción→Introduction etc.);
    title_en/description_en quedan None."""
    provider = _FakeSeqProvider([RuntimeError("ollama caído")])
    out, provider = _run_execute(monkeypatch, provider)
    # Ahora hasta 2 llamadas: intento + retry único antes de fallback (2026-09-01).
    # Ninguna llamada de traducción (el fallback no traduce).
    assert len(provider.prompts) <= 2
    assert out["title_en"] is None
    assert out["description_en"] is None
    # El fallback determinista inyecta las secciones canónicas ES → mapeo EN aplica.
    for c in out["chapters"]:
        oe = c["outline_en"]
        assert oe is not None
        assert [s["heading"] for s in oe] == ["Introduction", "Development", "Conclusion"]
        assert c["title_en"] is None


# 2026-09-01: _fallback_plan con entidades nombradas extraídas de la idea.
# El fallback usa secuencias de 2+ palabras capitalizadas como títulos de los
# primeros capítulos; el resto conserva el genérico "Parte N".


def test_fallback_plan_uses_named_entities_from_idea() -> None:
    """Idea con entidades reconocibles → los títulos de capítulo usan esas
    entidades (sin "Parte N" en ellos), en orden de aparición."""
    idea = "Reyes Católicos, Imperio Español y Guerra Civil Española: tres épocas"
    plan = _fallback_plan(BookPlanPayload(**{**_payload(), "idea": idea}))
    titles = [c["title"] for c in plan["chapters"]]
    # Entidades en orden de aparición, sin "Parte N".
    assert titles[0] == "Reyes Católicos"
    assert titles[1] == "Imperio Español"
    assert titles[2] == "Guerra Civil Española"
    assert "Parte" not in titles[0] and "Parte" not in titles[1] and "Parte" not in titles[2]


def test_fallback_plan_without_entities_unchanged() -> None:
    """Regresión: idea sin entidades reconocibles → comportamiento histórico
    intacto (todos los títulos son el genérico "short_title - Parte N")."""
    idea = "Novela corta de ciencia ficción"  # sin 2+ palabras capitalizadas
    plan = _fallback_plan(BookPlanPayload(**{**_payload(), "idea": idea}))
    titles = [c["title"] for c in plan["chapters"]]
    for i, t in enumerate(titles, start=1):
        assert t == f"{idea} - Parte {i}"


def test_fallback_plan_fewer_entities_than_target_pads_generic() -> None:
    """N entidades < target_chapters → los primeros N usan entidades y el
    resto completa con el genérico "- Parte N" (sin inventar entidades)."""
    idea = "Isabel la Católica y Fernando el Católico"  # 2 entidades, 25 caps
    plan = _fallback_plan(BookPlanPayload(**{**_payload(), "idea": idea}))
    titles = [c["title"] for c in plan["chapters"]]
    assert titles[0] == "Isabel la Católica"
    assert titles[1] == "Fernando el Católico"
    # El resto completa con el genérico (short_title = idea completa, 7 palabras
    # ≤ 8 → sin "..."), mismo formato que el comportamiento histórico.
    for i in range(2, len(titles)):
        assert titles[i] == f"{idea} - Parte {i + 1}"


def test_max_tokens_scales_with_target_chapters() -> None:
    """§17 #22: el presupuesto de salida escala con target_chapters.

    Fórmula: min(MAX_PLANNER_TOKENS, max(MIN_PLANNER_TOKENS,
    PLANNER_BASE_TOKENS + PLANNER_TOKENS_PER_CHAPTER * target_chapters)).
    - tc=1  → piso MIN_PLANNER_TOKENS=2000 (comportamiento actual preservado).
    - tc=5  → 400+5*150=1150 < 2000 → sigue en el piso 2000.
    - tc=20 → 400+20*150=3400 (>2000, <6000) → presupuesto notablemente mayor.
    - tc=60 → 9400 > techo → capado a MAX_PLANNER_TOKENS=6000.
    Además verifica que execute pasa ese valor al provider.
    """
    import modules.book_planner.main as main

    assert main._planner_max_tokens(1) == main.MIN_PLANNER_TOKENS == 2000
    assert main._planner_max_tokens(5) == 2000
    assert main._planner_max_tokens(20) == 3400
    assert main._planner_max_tokens(20) > main._planner_max_tokens(5)
    assert main._planner_max_tokens(60) == main.MAX_PLANNER_TOKENS == 6000

    captured: dict = {}

    class FakeResult:
        text = json.dumps({
            "title": "T", "subtitle": "S", "description": "D",
            "target_audience": "adultos",
            "chapters": [{
                "number": 1, "title": "C1", "objective": "O",
                "key_questions": ["Q"], "estimated_words": 3000,
                "research_requirements": [], "image_requirements": 3,
                "sections": [{"heading": "Introducción", "objective": "o"}],
            }],
        })
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, prompt, **kwargs):
            captured["max_tokens"] = kwargs.get("max_tokens")
            return FakeResult()

    monkeypatch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
        payload = _payload()
        payload["target_chapters"] = 20
        execute(payload)
        assert captured["max_tokens"] == 3400
    finally:
        monkeypatch.undo()


def test_planner_logs_raw_on_json_parse_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """§17 #22: si el JSON del LLM viene truncado/inválido, el texto crudo se
    registra (DEBUG, truncado a 2000 chars) antes de caer al fallback, que
    debe seguir funcionando igual."""
    import logging

    import modules.book_planner.main as main

    truncated = (
        '{"title":"T","subtitle":"S","description":"D","target_audience":"adultos",'
        '"chapters":[{"number":1,"title":"Capítulo 1","objective":"Objetivo",'
        '"key_questions":["Q"],"estimated_words":3000,"research_requirements":[],'
        '"image_requirements":3,"sections":[{"heading":"Introducción","objective"'
    )  # cortado a mitad del array (simula budget agotado)

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any):
            class R:
                text = truncated
                provider = "ollama"
                model = "llama3.1"
                input_tokens = 10
                output_tokens = 2000
                cost = 0.0
                raw_response = {}

            return R()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    with caplog.at_level(logging.DEBUG, logger="modules.book_planner.main"):
        out = execute(_payload())

    # El fallback sigue funcionando
    assert out["title"] == "Novela corta de ciencia ficción"
    assert len(out["chapters"]) == 25
    # El texto crudo quedó registrado en WARNING (contrato nuevo 2026-09-01:
    # el log del raw se subió de DEBUG a WARNING para que sea visible con
    # LOG_LEVEL=INFO; misma instrumentación, solo cambió el nivel).
    warning_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    raw_logs = [m for m in warning_msgs if "Respuesta cruda del planner LLM" in m]
    assert raw_logs, f"no se loggeó el raw en WARNING: {warning_msgs}"
    assert 'chapters"' in raw_logs[0]

"""Tests unitarios del módulo image_planner (capability: create_chapter_image_plan)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from modules.image_planner.main import (
    _build_fallback_plan,
    _build_prompt,
    _make_image,
    _normalize_images,
    _parse_llm_output,
    _resolve_num_images,
    execute,
    health_check,
)

ALL_KEYS = {
    "image_id", "purpose", "description", "composition", "subject",
    "environment", "lighting", "visual_style", "aspect_ratio",
    "prompt", "negative_prompt", "caption", "placement",
}
VALID_ASPECT = ("16:9", "3:2", "4:3", "1:1", "2:3", "9:16")


def _payload(num: int | None = None) -> dict:
    base = {
        "chapter_text": "El río Amazonas desemboca en el Atlántico. El crecimiento fue del 45% en 2019.",
        "chapter_title": "Introducción",
        "visual_style": "Fotografía editorial realista",
    }
    if num is not None:
        base["num_images"] = num
    return base


def test_health_check_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """health_check debe sobrevivir si el provider falla al instanciar."""
    import modules.image_planner.main as main

    monkeypatch.setattr(main, "get_provider", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = health_check()
    assert result["healthy"] is False
    assert "error" in result["dependencies"]


def test_resolve_num_images_default_three() -> None:
    assert _resolve_num_images(_payload()) == 3
    assert _resolve_num_images({"num_images": None}) == 3


def test_resolve_num_images_explicit() -> None:
    assert _resolve_num_images(_payload(5)) == 5
    assert _resolve_num_images(_payload(1)) == 1


def test_build_prompt_includes_rules() -> None:
    prompt = _build_prompt(_payload())
    assert "EXACTAMENTE 3" in prompt
    assert "función DIFERENTE" in prompt
    assert "NO inventar información" in prompt
    assert "identidad visual" in prompt
    assert "aspect_ratio" in prompt


def test_build_prompt_respects_explicit_num() -> None:
    prompt = _build_prompt(_payload(4))
    assert "EXACTAMENTE 4" in prompt


def test_make_image_has_all_required_keys() -> None:
    img = _make_image("hero", 1, "Intro", "estilo X", "texto")
    assert set(img.keys()) == ALL_KEYS
    assert img["image_id"] == "img_01_hero"
    assert img["aspect_ratio"] == "16:9"


def test_fallback_plan_default_exactly_three_distinct() -> None:
    plan = _build_fallback_plan(_payload())
    assert len(plan["images"]) == 3
    ids = [img["image_id"] for img in plan["images"]]
    assert len(set(ids)) == 3
    roles = [img["image_id"].rsplit("_", 1)[-1] for img in plan["images"]]
    # Las tres funciones deben ser distintas
    assert len(set(roles)) == 3
    for img in plan["images"]:
        assert set(img.keys()) == ALL_KEYS
        assert img["aspect_ratio"] in VALID_ASPECT
        assert img["purpose"]


def test_fallback_plan_respects_explicit_num() -> None:
    plan = _build_fallback_plan(_payload(6))
    assert len(plan["images"]) == 6


def test_parse_llm_output_happy_path() -> None:
    data = _parse_llm_output('{"images":[],"visual_style":"x","identity_notes":[]}')
    assert data["visual_style"] == "x"


def test_parse_llm_output_fenced() -> None:
    data = _parse_llm_output('```json\n{"images":[],"visual_style":"y"}\n```')
    assert data["visual_style"] == "y"


def test_parse_llm_output_invalid_returns_empty() -> None:
    assert _parse_llm_output("texto suelto") == {}


def test_normalize_images_completes_to_num() -> None:
    """Si el LLM devuelve menos imágenes, se completan hasta el número exacto."""
    res = _normalize_images({"images": [{"image_id": "a", "purpose": "p"}]}, _payload(3))
    assert len(res["images"]) == 3
    for img in res["images"]:
        assert set(img.keys()) == ALL_KEYS


def test_normalize_images_forces_aspect_ratio() -> None:
    bad = [{"image_id": "x", "aspect_ratio": "invalid", "purpose": "p"}]
    for k in ALL_KEYS - {"image_id", "aspect_ratio", "purpose"}:
        bad[0][k] = "v"
    res = _normalize_images({"images": bad}, _payload(1))
    assert res["images"][0]["aspect_ratio"] == "4:3"



def test_execute_fallback_when_llm_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM falla, execute devuelve un plan de 3 imágenes válidas."""
    import modules.image_planner.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ollama not available")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())

    out = execute(_payload())
    assert len(out["images"]) == 3
    roles = [img["image_id"].rsplit("_", 1)[-1] for img in out["images"]]
    assert len(set(roles)) == 3
    for img in out["images"]:
        assert set(img.keys()) == ALL_KEYS
        assert img["aspect_ratio"] in VALID_ASPECT


def test_execute_llm_success_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con LLM exitoso, el JSON se valida contra el esquema de salida."""
    import modules.image_planner.main as main

    llm_json = json.dumps({
        "images": [
            {
                "image_id": "img_01_hero",
                "purpose": "Apertura del capítulo",
                "description": "Escena general",
                "composition": "Plano amplio",
                "subject": "Amazonas",
                "environment": "Selva",
                "lighting": "Luz natural",
                "visual_style": "Fotografía editorial realista",
                "aspect_ratio": "16:9",
                "prompt": "A wide shot of the Amazon river",
                "negative_prompt": "no text",
                "caption": "Figura 1",
                "placement": "Apertura del capítulo",
            },
            {
                "image_id": "img_02_diagram",
                "purpose": "Esquema del proceso",
                "description": "Diagrama",
                "composition": "Layout limpio",
                "subject": "Proceso de crecimiento",
                "environment": "Fondo blanco",
                "lighting": "Luz plana",
                "visual_style": "Fotografía editorial realista",
                "aspect_ratio": "4:3",
                "prompt": "A clean diagram of growth",
                "negative_prompt": "no text",
                "caption": "Figura 2",
                "placement": "Sección explicativa",
            },
            {
                "image_id": "img_03_scene",
                "purpose": "Escena ilustrativa",
                "description": "Escena concreta",
                "composition": "Plano medio",
                "subject": "Datos históricos 2019",
                "environment": "Archivo histórico",
                "lighting": "Luz direccional",
                "visual_style": "Fotografía editorial realista",
                "aspect_ratio": "3:2",
                "prompt": "An archival scene from 2019",
                "negative_prompt": "no text",
                "caption": "Figura 3",
                "placement": "Desarrollo del capítulo",
            },
        ],
        "visual_style": "Fotografía editorial realista",
        "identity_notes": ["Consistencia visual entre capítulos."],
    })

    class FakeResult:
        text = llm_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert len(out["images"]) == 3
    roles = [img["image_id"].rsplit("_", 1)[-1] for img in out["images"]]
    assert set(roles) == {"hero", "diagram", "scene"}
    for img in out["images"]:
        assert set(img.keys()) == ALL_KEYS
        assert img["aspect_ratio"] in VALID_ASPECT

    from core.schemas import validate_output

    valid = validate_output("create_chapter_image_plan", out)
    assert len(valid["images"]) == 3


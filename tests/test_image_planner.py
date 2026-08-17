"""Tests unitarios del módulo image_planner (capability: create_chapter_image_plan)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from modules.image_planner.main import (
    _build_fallback_plan,
    _build_prompt,
    _GENRE_STYLE_MAP,
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


def _llm_image(image_id: str, prompt: str, negative_prompt: str = "ai artifacts") -> dict[str, str]:
    return {
        "image_id": image_id, "purpose": "p", "description": "d", "composition": "c",
        "subject": "s", "environment": "e", "lighting": "l", "visual_style": "estilo",
        "aspect_ratio": "16:9", "prompt": prompt, "negative_prompt": negative_prompt,
        "caption": "cap", "placement": "pl",
    }


def test_normalize_rejects_prompt_with_literal_chapter_title() -> None:
    """Guard: prompt del LLM con el título literal del capítulo -> fallback determinista."""
    from modules.image_planner.main import _LOCAL_NEGATIVE

    title = "La Teoría de la Evolución y la Formación del Planeta"
    validated = {
        "chapter_title": title,
        "chapter_text": "Texto del capítulo.",
        "visual_style": "editorial",
        "num_images": 3,
    }
    res = _normalize_images(
        {"images": [
            _llm_image("img_01_hero", f"{title}, entorno editorial. Estilo visual: editorial."),
            _llm_image("img_02_diagram", "Un esquema del sistema solar, limpio y abstracto."),
            _llm_image("img_03_scene", "Un planeta visto desde el espacio."),
        ], "visual_style": "editorial", "identity_notes": []},
        validated,
    )
    assert len(res["images"]) == 3
    # La imagen 1 se descartó: ya no contiene el título literal como sujeto.
    assert title.lower() not in res["images"][0]["prompt"].lower()
    # Fue sustituida por el fallback determinista (hero con \"sin texto\").
    assert "sin texto" in res["images"][0]["prompt"]
    # El negativo SIEMPRE lleva la base robusta.
    for img in res["images"]:
        assert img["negative_prompt"].startswith(_LOCAL_NEGATIVE)


def test_normalize_negative_always_includes_local_negative() -> None:
    """Cambio 1: el negative_prompt final siempre lleva _LOCAL_NEGATIVE, sin depender del LLM."""
    from modules.image_planner.main import _LOCAL_NEGATIVE

    validated = {"chapter_title": "Intro", "chapter_text": "t", "visual_style": "x", "num_images": 1}
    llm_neg = "purple haze, thick fog"
    res = _normalize_images(
        {"images": [_llm_image("img_01_hero", "Un bosque al amanecer.", llm_neg)],
         "visual_style": "x", "identity_notes": []},
        validated,
    )
    img = res["images"][0]
    assert img["negative_prompt"].startswith(_LOCAL_NEGATIVE)
    assert llm_neg in img["negative_prompt"]


def test_build_prompt_instructs_visual_scene_not_literal_title() -> None:
    """Cambio 2: el prompt del LLM instruye escena visual, sin título literal ni página/texto."""
    prompt = _build_prompt(_payload())
    assert "ESCENA VISUAL concreta" in prompt
    assert "NO usar el título del capítulo de forma literal" in prompt
    assert "NO describir la imagen como una página" in prompt
    assert "texto legible" in prompt


def test_fallback_plan_rellena_con_topicos_concretos_del_capitulo() -> None:
    """Front B: el fallback determinista (usado al rechazar el prompt del LLM por
    título literal) debe anclar su prompt en términos concretos del chapter_text
    en vez de quedar genérico título+estilo. Caso real: book 30 '...pong al GTA VI'."""
    from modules.image_planner.main import (
        _build_fallback_plan,
        _extract_chapter_topics,
        _normalize_images,
        _normalize_title,
    )

    title = "Historia de los videojuegos del pong al GTA VI"
    chapter_text = (
        "Hace algo más de un siglo, un grupo de científicos experimentaba con ondas "
        "de radio. Ese experimento dio lugar al primer videojuego: Pong, un sencillo "
        "juego de ping pong que revolucionó la industria. La compañía Atari popularizó "
        "el arcade con máquinas de la década de 1970. Más tarde llegó PlayStation y Xbox."
    )

    # 1) El extractor aísla términos concretos del texto (no el título literal).
    topics = _extract_chapter_topics(chapter_text, exclude=title)
    assert "Pong" in topics
    assert "Atari" in topics

    # 2) El plan fallback menciona los términos concretos...
    plan = _build_fallback_plan(
        {"chapter_title": title, "chapter_text": chapter_text,
         "visual_style": "editorial", "num_images": 3}
    )
    combined = " ".join(img["prompt"] for img in plan["images"]).lower()
    assert "pong" in combined or "atari" in combined
    # ...y no reintroduce el título literal del capítulo (el guard sigue seguro).
    assert _normalize_title(title) not in _normalize_title(combined)

    # 3) Ruta real: el guard rechaza el prompt del LLM (título literal) y el
    # fallback resultante menciona el término concreto y conserva "sin texto".
    normalized = _normalize_images(
        {"images": [
            _llm_image("img_01_hero", f"{title}, entorno editorial. Estilo visual: editorial."),
            _llm_image("img_02_diagram", "Un esquema del sistema solar, limpio y abstracto."),
        ], "visual_style": "editorial", "identity_notes": []},
        {"chapter_title": title, "chapter_text": chapter_text,
         "visual_style": "editorial", "num_images": 3},
    )
    first_prompt = normalized["images"][0]["prompt"]
    assert "Pong" in first_prompt or "Atari" in first_prompt
    assert _normalize_title(title) not in _normalize_title(first_prompt)
    assert "sin texto" in first_prompt


def test_fallback_plan_without_topics_keeps_current_behavior() -> None:
    """Si el chapter_text no aporta términos concretos, el fallback vuelve al
    comportamiento previo (no rompe nada)."""
    from modules.image_planner.main import _build_fallback_plan

    plan = _build_fallback_plan(
        {"chapter_title": "Intro", "chapter_text": "t",
         "visual_style": "Fotografía editorial realista", "num_images": 3}
    )
    assert len(plan["images"]) == 3
    assert "sin texto" in plan["images"][0]["prompt"]


def test_genre_terror_maps_distinct_style() -> None:
    """Género reconocido + sin visual_style explícito → estilo del género en el prompt."""
    from modules.image_planner.main import _build_fallback_plan

    plan = _build_fallback_plan(
        {"chapter_title": "Intro", "chapter_text": "t",
         "genre": "Terror", "num_images": 3}
    )
    first = plan["images"][0]
    assert first["visual_style"] == _GENRE_STYLE_MAP["Terror"]
    assert "sombras dramáticas" in first["prompt"]


def test_no_genre_keeps_default_style() -> None:
    """Sin genre (o no reconocido) → 'realistic' (default real de producción, no regresión)."""
    from modules.image_planner.main import _build_fallback_plan

    default = "realistic"
    plan = _build_fallback_plan(
        {"chapter_title": "Intro", "chapter_text": "t", "num_images": 3}
    )
    first = plan["images"][0]
    assert first["visual_style"] == default
    assert default in first["prompt"]
    # Género no reconocido también cae al default.
    plan_unknown = _build_fallback_plan(
        {"chapter_title": "Intro", "chapter_text": "t",
         "genre": "GéneroInvalido", "num_images": 3}
    )
    assert plan_unknown["images"][0]["visual_style"] == default


def test_explicit_style_wins_over_genre() -> None:
    """Visual_style explícito + genre presente → gana el visual_style (el género no lo pisa)."""
    from modules.image_planner.main import _build_fallback_plan

    plan = _build_fallback_plan(
        {"chapter_title": "Intro", "chapter_text": "t",
         "visual_style": "Acuarela pastel", "genre": "Terror", "num_images": 3}
    )
    first = plan["images"][0]
    assert first["visual_style"] == "Acuarela pastel"
    assert "Acuarela pastel" in first["prompt"]
    assert "sombras dramáticas" not in first["prompt"]

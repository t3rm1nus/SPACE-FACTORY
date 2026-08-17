import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from core.schemas import ImageGenerateOutput, ImageMetadata, validate_output
from modules import image_generator


@pytest.fixture(autouse=True)
def _isolate_fs(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    yield


def _spec(image_id="img_01_hero"):
    return {
        "image_id": image_id,
        "purpose": "Hero de apertura.",
        "description": "Hero del capítulo.",
        "composition": "Amplia.",
        "subject": "Tema central.",
        "environment": "Escenario general.",
        "lighting": "Luz suave.",
        "visual_style": "editorial",
        "aspect_ratio": "16:9",
        "prompt": "Tema central, escenario general, amplia. Estilo visual: editorial.",
        "negative_prompt": "text, watermark, low quality",
        "caption": "Figura 1.",
        "placement": "Apertura.",
    }


def _payload(**overrides):
    payload = {
        "image_plan": {"images": [_spec()], "visual_style": "editorial", "identity_notes": []},
        "book_id": 1,
        "chapter_number": 1,
        "language": "es",
        "provider": "local",
        "model": "placeholder",
        "generate_thumbnails": True,
        "skip_existing": True,
        "max_attempts": 2,
    }
    payload.update(overrides)
    return payload


def test_generate_image_creates_files_and_metadata(tmp_path):
    payload = _payload()
    out = image_generator.generate_image(payload)
    validated = ImageGenerateOutput(**validate_output("generate_image", out))

    assert validated.generated >= 1
    assert validated.failed == 0
    assert validated.images_dir.startswith(str(tmp_path))
    assert os.path.isfile(validated.results[0].image_path)
    assert validated.results[0].status == "ok"
    metadata_path = validated.images_dir + f"/{validated.results[0].image_id}.metadata.json"
    assert os.path.isfile(metadata_path)


def test_generate_image_skips_existing_valid_image(tmp_path):
    payload = _payload()
    first = image_generator.generate_image(payload)
    ImageGenerateOutput(**validate_output("generate_image", first))

    with patch("modules.image_generator.main._generate_single_image") as mocked:
        second = image_generator.generate_image(_payload())
        ImageGenerateOutput(**validate_output("generate_image", second))

    assert mocked.call_count == 0
    assert second["results"][0]["status"] == "ok"


def test_generate_image_records_error_after_max_attempts(tmp_path):
    broken_provider = MagicMock()
    broken_provider.name = "local"
    broken_provider.model = "placeholder"
    broken_provider.health_check.return_value = {"healthy": True}
    broken_provider.generate.side_effect = RuntimeError("boom")

    with patch("modules.image_generator.main.get_image_provider", return_value=broken_provider):
        out = image_generator.generate_image(_payload(max_attempts=2))

    validated = ImageGenerateOutput(**validate_output("generate_image", out))
    assert validated.failed == 1
    assert validated.results[0].status == "error"
    assert "boom" in validated.results[0].error


def test_generate_chapter_images_builds_plan_when_missing(tmp_path):
    payload = {
        "book_id": 1,
        "chapter_number": 1,
        "language": "es",
        "chapter_text": "Texto del capítulo.",
        "chapter_title": "Introducción",
        "visual_style": "Fotografía editorial, paleta cálida.",
        "num_images": 3,
        "provider": "local",
        "model": "placeholder",
        "generate_thumbnails": True,
        "skip_existing": True,
        "max_attempts": 2,
    }
    out = image_generator.generate_chapter_images(payload)
    validated = ImageGenerateOutput(**validate_output("generate_image", out))
    assert validated.requested == 3
    assert validated.generated == 3
    assert validated.failed == 0


def test_generate_chapter_images_is_parallel_safe(tmp_path):
    payload = _payload()
    out_a = image_generator.generate_image(_payload(book_id=1, chapter_number=1, language="es"))
    ImageGenerateOutput(**validate_output("generate_image", out_a))
    out_b = image_generator.generate_image(_payload(book_id=2, chapter_number=1, language="en"))
    ImageGenerateOutput(**validate_output("generate_image", out_b))
    assert out_a["images_dir"] != out_b["images_dir"]
def test_budget_guard_forces_local_fallback_for_remaining_images(tmp_path, monkeypatch):
    """Peor caso: la imagen 1 consume casi todo IMAGE_TOTAL_TIME_BUDGET.

    Verifica que las imágenes 2 y 3 del loop caen a fallback local
    (metadata fallback_reason='time_budget_exhausted') en vez de intentar el
    provider real, confirmando que el loop nunca se acerca al timeout del
    scheduler (360s).
    """
    import modules.image_generator.main as main
    from core.schemas import ImageGenerateOutput, validate_output
    from core.image_providers.registry import get as _reg_get

    real = MagicMock(name="real_provider")
    real.name = "comfyui"
    real.model = "sdxl"
    real.health_check.return_value = {"healthy": True}
    real.generate.return_value = MagicMock(
        provider="comfyui", model="sdxl", seed=1,
        image_path=str(tmp_path / "first.png"),
        metadata={"width": 1024, "height": 1024, "steps": 25},
    )

    def fake_get(name=None):
        if name == "local":
            return _reg_get("local")
        return real

    monkeypatch.setattr(main, "get_provider", fake_get)
    monkeypatch.setattr(main, "IMAGE_TOTAL_TIME_BUDGET", 3.0)
    # La imagen 1 "consume" casi todo el presupuesto simulado (elapsed > budget).
    calls = {"n": 0}

    def fake_perf():
        calls["n"] += 1
        return 3.5 if calls["n"] > 1 else 0.0

    monkeypatch.setattr(main.time, "perf_counter", fake_perf)

    payload = {
        "book_id": 1, "chapter_number": 1, "language": "es",
        "provider": "comfyui", "model": "sdxl",
        "generate_thumbnails": True, "skip_existing": True, "max_attempts": 2,
        "image_plan": {
            "images": [_spec("img_01_hero"), _spec("img_02_detail"), _spec("img_03_closing")],
            "visual_style": "editorial", "identity_notes": [],
        },
    }
    out = main.generate_image(payload)
    ImageGenerateOutput(**validate_output("generate_image", out))

    assert out["requested"] == 3
    assert out["generated"] == 3
    assert out["failed"] == 0
    # El provider real solo se usó en la primera imagen.
    assert real.generate.call_count == 1
    # La primera no se marcó como fallback.
    assert out["results"][0].get("fallback", False) is False
    # Las imágenes 2 y 3 cayeron a fallback local por presupuesto.
    assert out["results"][1]["fallback"] is True
    assert out["results"][1]["fallback_reason"] == "time_budget_exhausted"
    assert out["results"][2]["fallback"] is True
    assert out["results"][2]["fallback_reason"] == "time_budget_exhausted"

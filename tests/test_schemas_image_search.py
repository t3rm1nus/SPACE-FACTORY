"""Tests de esquemas para la capability search_chapter_images.

Verifica el registro en PAYLOAD_SCHEMAS (ImageSearchPayload) y OUTPUT_SCHEMAS
(ImageGenerateOutput, plug-compatible) definido en core/schemas.py.
"""

import datetime

import pytest

from core.schemas import (
    PAYLOAD_SCHEMAS,
    OUTPUT_SCHEMAS,
    ImageGenerateOutput,
    ImageSearchPayload,
    validate_output,
    validate_payload,
)


CAPABILITY = "search_chapter_images"


def _base_meta(image_id="img_01_web") -> dict:
    """Shape real de ImageMetadata tal como produce search_chapter_images."""
    return {
        "image_id": image_id,
        "provider": "searxng",
        "model": "Bing images",
        "seed": 0,
        "width": 1024,
        "height": 576,
        "steps": 1,
        "aspect_ratio": "16:9",
        "prompt": "bosque mágico iluminado por la luna",
        "negative_prompt": "text, watermark, low quality",
        "image_path": "/tmp/books/1/chapters/1/images/img_01_web.png",
        "thumbnail_paths": [],
        "status": "ok",
        "attempts": 1,
        "error": None,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "extra": {"book_id": 1, "chapter_number": 1, "language": "es"},
        # Campos extra de trazabilidad web (ignorados por Pydantic en el schema base)
        "source_type": "web_search",
        "source_url": "https://cdn.example.com/img1.png",
        "engine": "Bing images",
        "resolution": "1024x576",
        "license": None,
    }


def _valid_output() -> dict:
    """Shape real de ImageGenerateOutput tal como devuelve search_chapter_images."""
    return {
        "book_id": 1,
        "chapter_number": 1,
        "language": "es",
        "images_dir": "/tmp/books/1/chapters/1/images",
        "results": [_base_meta()],
        "requested": 1,
        "generated": 1,
        "skipped": 0,
        "failed": 0,
    }


def test_search_chapter_images_payload_valid():
    """Payload con los campos requeridos mínimos valida sin excepción."""
    payload = {
        "book_id": 1,
        "chapter_number": 1,
        "language": "es",
    }
    validated = validate_payload(CAPABILITY, payload)

    assert validated["book_id"] == 1
    assert validated["chapter_number"] == 1
    assert validated["language"] == "es"
    assert validated["chapter_title"] is None
    assert validated["chapter_text"] is None
    assert validated["num_images"] is None


def test_search_chapter_images_payload_missing_required_field_fails():
    """Payload sin book_id lanza excepción de validación."""
    payload = {
        "chapter_number": 1,
        "language": "es",
    }
    with pytest.raises(Exception):
        validate_payload(CAPABILITY, payload)


def test_search_chapter_images_output_accepts_generate_image_shape():
    """El dict de salida con forma real de ImageGenerateOutput valida sin excepción."""
    out = _valid_output()

    validated = validate_output(CAPABILITY, out)

    assert validated["book_id"] == 1
    assert validated["chapter_number"] == 1
    assert validated["language"] == "es"
    assert validated["requested"] == 1
    assert validated["generated"] == 1
    assert validated["skipped"] == 0
    assert validated["failed"] == 0
    assert len(validated["results"]) == 1

    first = validated["results"][0]
    assert first["provider"] == "searxng"
    assert first["status"] == "ok"
    assert first["image_id"] == "img_01_web"

    # El schema registrado como payload es ImageSearchPayload (no ImageGeneratePayload)
    assert CAPABILITY in PAYLOAD_SCHEMAS
    assert PAYLOAD_SCHEMAS[CAPABILITY] is ImageSearchPayload

    # El schema registrado como output reutiliza ImageGenerateOutput
    assert CAPABILITY in OUTPUT_SCHEMAS
    assert OUTPUT_SCHEMAS[CAPABILITY] is ImageGenerateOutput


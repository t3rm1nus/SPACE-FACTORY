"""Tests del módulo pdf_builder."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image as PILImage

from core.book.book_schema import Book, Chapter
from core.schemas import BookPdfPayload
from modules.pdf_builder.main import build_book_pdf, health_check


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield


def test_health_check():
    result = health_check()
    assert result["healthy"] is True
    assert result["dependencies"]["fpdf2"] == "ok"
    assert result["dependencies"]["pypdf"] == "ok"


def _book_payload(tmp_path: Path, with_images: bool = True) -> dict:
    img_path = tmp_path / "hero.png"
    if with_images:
        PILImage.new("RGB", (64, 64), color="red").save(img_path)

    book = {
        "book_id": 1,
        "title": "El libro del espacio",
        "subtitle": "Manual de diseño profesional",
        "description": "Esta es la introducción del libro.",
        "author": "Space Lair",
        "target_audience": "desarrolladores",
        "genre": "tecnología",
        "languages": ["es"],
        "target_chapters": 2,
        "status": "edited",
        "created_at": datetime(2024, 1, 1).isoformat(),
        "chapters": [
            {
                "chapter_id": 10,
                "book_id": 1,
                "number": 1,
                "title": "Introducción",
                "edited_es": "## Antecedentes\n\nContenido del capítulo 1.",
                "images": [str(img_path)] if with_images else [],
            },
            {
                "chapter_id": 11,
                "book_id": 1,
                "number": 2,
                "title": "Diseño avanzado",
                "draft_es": "## Técnicas\n\nContenido del capítulo 2.",
                "images": [],
            },
        ],
    }
    return {
        "book": book,
        "language": "es",
        "page_config": {
            "size": "A4",
            "margins_mm": {"top": 20, "bottom": 20, "left": 20, "right": 20},
        },
    }


def test_build_book_pdf_creates_real_pdf(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=True)
    out = build_book_pdf(payload)

    assert os.path.isfile(out["pdf_path"])
    assert out["language"] == "es"
    assert out["chapter_count"] == 2
    assert out["image_count"] == 1
    assert out["pdf_path"].endswith("book_es.pdf")
    assert len(out["warnings"]) == 0

    from pypdf import PdfReader
    reader = PdfReader(out["pdf_path"])
    assert len(reader.pages) > 0

    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "El libro del espacio" in text
    assert "Introducción" in text
    assert "Diseño avanzado" in text
    assert "Contenido del capítulo 1" in text
    assert "Contenido del capítulo 2" in text
    assert "Figura 1" in text


def test_build_book_pdf_custom_page_size(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    payload["page_config"] = {
        "size": "LETTER",
        "margins_mm": {"top": 15, "bottom": 15, "left": 15, "right": 15},
    }
    out = build_book_pdf(payload)

    from pypdf import PdfReader
    reader = PdfReader(out["pdf_path"])
    assert len(reader.pages) > 0


def test_build_book_pdf_missing_images_are_skipped(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    payload["book"]["chapters"][0]["images"] = [str(tmp_path / "no_existe.png")]
    out = build_book_pdf(payload)

    assert os.path.isfile(out["pdf_path"])
    from pypdf import PdfReader
    reader = PdfReader(out["pdf_path"])
    assert len(reader.pages) > 0
    assert any("Imagen no encontrada" in w for w in out["warnings"])


def test_build_book_pdf_no_duplicate_chapters(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    out = build_book_pdf(payload)

    from pypdf import PdfReader
    reader = PdfReader(out["pdf_path"])
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert text.count("Capítulo 1: Introducción") == 1
    assert text.count("Capítulo 2: Diseño avanzado") == 1
    assert out["chapter_count"] == 2


def test_build_book_pdf_uses_edited_over_draft(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    payload["book"]["chapters"][0]["edited_es"] = "## Editado\n\nContenido editado."
    out = build_book_pdf(payload)

    from pypdf import PdfReader
    reader = PdfReader(out["pdf_path"])
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Contenido editado." in text
    assert "Contenido del capítulo 1" not in text


def test_build_book_pdf_detects_overflow_warnings(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    payload["book"]["chapters"][0]["edited_es"] = (
        "PalabraMuylargaqueexcedeelanchodisponible " * 20
        + "\n\n"
        + "## Título moderadamente largo para probar la detección de desbordamiento"
    )
    out = build_book_pdf(payload)

    assert any("desbordado" in w.lower() for w in out["warnings"])


def test_build_book_pdf_detects_cropped_image(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    # Create a very tall image
    tall_path = tmp_path / "tall.png"
    PILImage.new("RGB", (100, 5000), color="blue").save(tall_path)
    payload["book"]["chapters"][0]["images"] = [str(tall_path)]
    out = build_book_pdf(payload)

    assert any("Imagen escalada" in w or "cortada" in w for w in out["warnings"])

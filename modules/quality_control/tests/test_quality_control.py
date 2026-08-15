"""Tests del módulo quality_control."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image as PILImage

from core.book.book_schema import Book, Chapter
from core.schemas import QualityControlPayload
from modules.quality_control.main import final_quality_control, health_check


def _make_book(tmp_path: Path, chapter_count: int = 25) -> dict:
    img_path = tmp_path / "hero.png"
    PILImage.new("RGB", (64, 64), color="red").save(img_path)
    (tmp_path / "metadata.json").write_text('{"camera": "test"}', encoding="utf-8")

    chapters = []
    for i in range(1, chapter_count + 1):
        chapters.append({
            "chapter_id": i,
            "book_id": 1,
            "number": i,
            "title": "Capítulo {}".format(i),
            "research": "Investigación del capítulo {}".format(i),
            "sources": ["https://reliable-source-{}.com".format(i)],
            "edited_es": "Contenido editado en español del capítulo {}.".format(i),
            "edited_en": "Edited content in English for chapter {}.".format(i),
            "images": [str(img_path)] * 3,
        })

    return {
        "book_id": 1,
        "title": "Libro de prueba",
        "description": "Descripción del libro.",
        "author": "Autor",
        "genre": "Tecnología",
        "target_audience": "Desarrolladores",
        "languages": ["es", "en"],
        "target_chapters": 30,
        "status": "edited",
        "created_at": datetime(2024, 1, 1).isoformat(),
        "chapters": chapters,
    }


def test_health_check():
    result = health_check()
    assert result["healthy"] is True
    assert result["dependencies"]["python-docx"] == "ok"


def test_final_quality_control_pass(tmp_path: Path):
    book = _make_book(tmp_path, chapter_count=30)
    docx_path = tmp_path / "book_es.docx"
    pdf_path = tmp_path / "book_es.pdf"
    docx_path.write_text("fake docx", encoding="utf-8")
    pdf_path.write_text("fake pdf", encoding="utf-8")

    payload = {
        "book": book,
        "docx_path": str(docx_path),
        "pdf_path": str(pdf_path),
        "min_chapters": 20,
        "target_chapters": 30,
        "max_chapters": 40,
    }

    with patch("modules.quality_control.main.Document") as mock_docx, patch(
        "modules.quality_control.main.PdfReader"
    ) as mock_pdf:
        mock_docx.return_value.paragraphs = [type("P", (), {"text": "Índice"})()]
        mock_docx.return_value.inline_shapes = [1]
        mock_pdf.return_value.pages = [1] * 50
        result = final_quality_control(payload)

    assert result["overall_status"] == "PASS"
    assert result["is_complete"] is True


def test_final_quality_control_fail_not_enough_chapters(tmp_path: Path):
    book = _make_book(tmp_path, chapter_count=10)
    payload = {
        "book": book,
        "min_chapters": 20,
        "target_chapters": 30,
        "max_chapters": 40,
    }
    result = final_quality_control(payload)
    assert result["overall_status"] == "FAIL"
    assert result["is_complete"] is False
    assert any("insuficientes" in c["message"] for c in result["chapter_checks"])


def test_final_quality_control_fail_duplicate_numbers(tmp_path: Path):
    book = _make_book(tmp_path, chapter_count=5)
    book["chapters"][1]["number"] = 1
    payload = {
        "book": book,
        "min_chapters": 1,
        "target_chapters": 5,
        "max_chapters": 10,
    }
    result = final_quality_control(payload)
    assert result["overall_status"] == "FAIL"
    assert any("duplicados" in c["message"] for c in result["chapter_checks"])


def test_final_quality_control_fail_empty_chapter(tmp_path: Path):
    book = _make_book(tmp_path, chapter_count=3)
    book["chapters"][0]["title"] = ""
    book["chapters"][0]["edited_es"] = None
    book["chapters"][0]["draft_es"] = None
    book["chapters"][0]["edited_en"] = None
    book["chapters"][0]["draft_en"] = None
    payload = {
        "book": book,
        "min_chapters": 1,
        "target_chapters": 3,
        "max_chapters": 10,
    }
    result = final_quality_control(payload)
    assert result["overall_status"] == "FAIL"
    assert any("vacíos" in c["message"] for c in result["chapter_checks"])


def test_final_quality_control_fail_invented_sources(tmp_path: Path):
    book = _make_book(tmp_path, chapter_count=3)
    book["chapters"][0]["sources"] = ["https://example.com/fake"]
    payload = {
        "book": book,
        "min_chapters": 1,
        "target_chapters": 3,
        "max_chapters": 10,
    }
    result = final_quality_control(payload)
    assert result["overall_status"] == "FAIL"
    assert any("inventadas" in c["message"] for c in result["source_checks"])


def test_final_quality_control_warning_missing_image_metadata(tmp_path: Path):
    book = _make_book(tmp_path, chapter_count=3)
    img_path = tmp_path / "hero.png"
    PILImage.new("RGB", (64, 64), color="red").save(img_path)
    book["chapters"][0]["images"] = [str(img_path)] * 3

    metadata_path = tmp_path / "metadata.json"
    if metadata_path.exists():
        metadata_path.unlink()

    payload = {
        "book": book,
        "min_chapters": 1,
        "target_chapters": 3,
        "max_chapters": 10,
    }
    result = final_quality_control(payload)
    assert result["overall_status"] == "WARNING"
    assert any("sin metadata" in c["message"] for c in result["image_checks"])


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

def test_check_images_uses_book_image_count_not_literal(tmp_path: Path):
    """image_count=5 con 5 imágenes reales por capítulo -> PASS (no el literal 3)."""
    from modules.quality_control.main import _check_images

    img_path = tmp_path / "hero.png"
    PILImage.new("RGB", (64, 64), color="red").save(img_path)
    (tmp_path / "metadata.json").write_text('{"camera": "test"}', encoding="utf-8")

    book = Book.model_validate({
        "title": "Libro de prueba",
        "description": "Descripción del libro.",
        "image_count": 5,
        "chapters": [
            {
                "chapter_id": i,
                "book_id": 1,
                "number": i,
                "edited_es": "Contenido del capítulo {}.".format(i),
                "images": [str(img_path)] * 5,
            }
            for i in range(1, 4)
        ],
    })
    checks = _check_images(book)
    count_checks = [c for c in checks if c.message.startswith("5 imágenes por capítulo")]
    assert len(count_checks) == 1 and count_checks[0].status == "PASS"
    assert not any(c.status == "FAIL" and "Imágenes por capítulo" in c.message for c in checks)


def test_check_images_fail_carries_origin_phase_image_gen(tmp_path: Path):
    """§17 #36 Fase 1: el check FAIL de déficit/desigualdad de imágenes expone
    el campo estructurado origin_phase="image_gen" (fase responsable)."""
    from modules.quality_control.main import _check_images

    img_path = tmp_path / "hero.png"
    PILImage.new("RGB", (64, 64), color="red").save(img_path)

    book = Book.model_validate({
        "title": "Libro de prueba",
        "description": "Descripción del libro.",
        "image_count": 3,
        "chapters": [
            {
                "chapter_id": 1,
                "book_id": 1,
                "number": 1,
                "edited_es": "Texto cap 1.",
                "images": [str(img_path)] * 3,
            },
            {
                "chapter_id": 2,
                "book_id": 1,
                "number": 2,
                "edited_es": "Texto cap 2.",
                "images": [str(img_path)] * 6,  # cap 2 EXCEDE (6>3) -> exceso -> FAIL
            },
        ],
    })
    checks = _check_images(book)
    fails = [c for c in checks if c.status == "FAIL"]
    assert fails, "se espera un FAIL por EXCESO de imágenes entre capítulos (exceso no se degrada)"
    assert all(c.origin_phase == "image_gen" for c in fails)





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


def test_image_metadata_in_image_id_metadata_json_is_recognized(tmp_path: Path):
    """§17 #12 — la metadata en convención real ({image_id}.metadata.json, como
    persisten image_generator e image_search) NO debe disparar el falso warning
    'Imágenes sin metadata' (antes solo se buscaba 'metadata.json' simple)."""
    from modules.quality_control.main import _check_images, _image_has_metadata

    # Convención real: <img_id>.metadata.json junto a la imagen.
    img_path = tmp_path / "img_01_web.png"
    PILImage.new("RGB", (64, 64), color="red").save(img_path)
    (tmp_path / "img_01_web.metadata.json").write_text('{"provider": "searxng", "status": "ok"}', encoding="utf-8")

    assert _image_has_metadata(str(img_path)) is True

    book = _make_book(tmp_path, chapter_count=1)
    book["chapters"][0]["images"] = [str(img_path)] * 3
    checks = _check_images(Book.model_validate(book))
    # Ningún check debe ser WARNING por metadata ausente: el PASS de metadata existe.
    assert not any(c.status == "WARNING" and "metadata" in c.message for c in checks)
    assert any(c.message == "Metadata presente en imágenes" for c in checks)

def _make_images_book(tmp_path: Path, image_count: int, per_chapter: int, ratio) -> Book:
    """Book mínimo para _check_images: image_count esperado, N imágenes reales por cap."""
    img_path = tmp_path / "hero.png"
    PILImage.new("RGB", (64, 64), color="red").save(img_path)
    (tmp_path / "metadata.json").write_text('{"camera": "test"}', encoding="utf-8")
    data = {
        "title": "Libro de prueba",
        "description": "Descripción del libro.",
        "image_count": image_count,
        "chapters": [
            {
                "chapter_id": i,
                "book_id": 1,
                "number": i,
                "edited_es": "Contenido del capítulo {}.".format(i),
                "images": [str(img_path)] * per_chapter,
            }
            for i in range(1, 4)
        ],
    }
    if ratio is not None:
        data["image_search_ratio"] = ratio
    return Book.model_validate(data)


def test_check_images_deficit_of_one_now_warning(tmp_path: Path):
    """§17 #48 Fase 4 (decisión explícita del arquitecto, 2026-09-01): el déficit
    de 1 imagen/capítulo (4/5) degrada a WARNING trazable (nunca PASS silencioso),
    independientemente del ratio. El mensaje es \"déficit tolerado\" (antes decía
    \"ratio=1.0 100% web\", ya derogado — el ratio dejó de condicionar nada)."""
    from modules.quality_control.main import _check_images

    book = _make_images_book(tmp_path, image_count=5, per_chapter=4, ratio=1.0)
    checks = _check_images(book)
    fails = [c for c in checks if c.status == "FAIL" and "Imágenes por capítulo" in c.message]
    assert not fails, [c.message for c in fails]
    warns = [c for c in checks if c.status == "WARNING" and "tolerado" in c.message]
    assert len(warns) == 1 and "déficit tolerado" in warns[0].message
    assert warns[0].origin_phase == "image_gen"


def test_check_images_deficit_of_two_now_warning(tmp_path: Path):
    """§17 #48 Fase 4 (política nueva): un déficit mayor (3/5) también es WARNING,
    no FAIL. Antes (ratio=1.0) toleraba solo <=1; ahora el déficit de cualquier
    magnitud degrada a WARNING."""
    from modules.quality_control.main import _check_images

    book = _make_images_book(tmp_path, image_count=5, per_chapter=3, ratio=1.0)
    checks = _check_images(book)
    fails = [c for c in checks if c.status == "FAIL" and "Imágenes por capítulo" in c.message]
    assert not fails, [c.message for c in fails]
    warns = [c for c in checks if c.status == "WARNING" and "tolerado" in c.message]
    assert len(warns) == 1 and "déficit tolerado" in warns[0].message
    assert warns[0].origin_phase == "image_gen"


def test_check_images_deficit_ratio_not_1_now_warning(tmp_path: Path):
    """§17 #48 Fase 4 (política nueva): el ratio ya no condiciona la severidad.
    Con ratio=0.5 y déficit de 1 imagen (4/5) -> WARNING, igual que con ratio=1.0
    (antes ratio!=1.0 mantenía comparación exacta -> FAIL; eso quedó derogado)."""
    from modules.quality_control.main import _check_images

    book = _make_images_book(tmp_path, image_count=5, per_chapter=4, ratio=0.5)
    checks = _check_images(book)
    fails = [c for c in checks if c.status == "FAIL" and "Imágenes por capítulo" in c.message]
    assert not fails, [c.message for c in fails]
    warns = [c for c in checks if c.status == "WARNING" and "tolerado" in c.message]
    assert len(warns) == 1 and "déficit tolerado" in warns[0].message
    assert warns[0].origin_phase == "image_gen"


@pytest.mark.parametrize(
    "image_count,per_chapter,ratio",
    [
        # (a) Libro sin imágenes (expected=0), 0 imágenes en todos los capítulos
        #     -> comportamiento histórico intacto: PASS "Sin imágenes requeridas".
        (0, 0, None),
        # (b) expected=5, 0 imágenes en TODOS los capítulos, ratio=1.0
        #     -> tras el cambio de política (déficit -> WARNING) ya NO es FAIL;
        #     es déficit de 5 en cada capítulo -> WARNING "déficit tolerado",
        #     nunca "PASS Sin imágenes requeridas" silencioso.
        (5, 0, 1.0),
    ],
)
def test_check_images_zero_images_vs_expected_no_false_pass(tmp_path, image_count, per_chapter, ratio):
    """§17 #40 (book_76): cuando expected>=1 y ningún capítulo tiene imágenes, el gate
    no cae a la rama silenciosa "PASS Sin imágenes requeridas". Tras el cambio de
    política (2026-09-01) ese déficit es WARNING "déficit tolerado", no FAIL.
    Con expected=0 el comportamiento histórico se mantiene (PASS Sin imágenes)."""
    from modules.quality_control.main import _check_images

    book = _make_images_book(tmp_path, image_count=image_count, per_chapter=per_chapter, ratio=ratio)
    checks = _check_images(book)

    if image_count == 0:
        # (a) expected=0: PASS "Sin imágenes requeridas", sin FAIL de imagen.
        assert any(c.status == "PASS" and c.message == "Sin imágenes requeridas" for c in checks)
        assert not any(c.status == "FAIL" and "Imágenes por capítulo" in c.message for c in checks)
    else:
        # (b) expected>=1 con 0 imágenes en todos los capítulos: WARNING "déficit
        # tolerado" (política nueva), nunca un PASS silencioso.
        warns = [c for c in checks if c.status == "WARNING" and "déficit tolerado" in c.message]
        assert len(warns) == 1, [c.message for c in checks]
        assert warns[0].origin_phase == "image_gen"
        assert "cap 1: 0" in warns[0].message and "cap 2: 0" in warns[0].message
        assert not any(c.status == "PASS" and c.message == "Sin imágenes requeridas" for c in checks)

"""Tests del módulo document_builder."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image as PILImage

from core.book.book_schema import Book, Chapter
from core.schemas import BookDocxPayload
from modules.document_builder.main import build_book_docx, health_check


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield


def test_health_check():
    result = health_check()
    assert result["healthy"] is True
    assert result["dependencies"]["python-docx"] == "ok"


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


def test_build_book_docx_creates_real_docx(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=True)
    out = build_book_docx(payload)

    assert os.path.isfile(out["docx_path"])
    assert out["language"] == "es"
    assert out["chapter_count"] == 2
    assert out["image_count"] == 1
    assert out["docx_path"].endswith(f"book_{out['book_id']}_es.docx")

    from docx import Document
    doc = Document(out["docx_path"])

    assert doc.core_properties.title == "El libro del espacio"
    assert doc.core_properties.author == "Space Lair"
    assert doc.core_properties.language == "es"

    texts = [p.text for p in doc.paragraphs]
    assert "El libro del espacio" in texts
    assert "El libro del espacio" in texts  # cover
    assert any("Introducción" in t for t in texts)
    assert any("Diseño avanzado" in t for t in texts)
    assert any("Contenido del capítulo 1" in t for t in texts)
    assert any("Contenido del capítulo 2" in t for t in texts)
    assert any("Figura 1" in t for t in texts)

    # Check images embedded
    assert len(doc.inline_shapes) == 1

    # Check header/footer
    section = doc.sections[0]
    assert "El libro del espacio" in section.header.paragraphs[0].text
    footer_text = section.footer.paragraphs[0].text
    assert f"book_{out['book_id']}_es.docx" in footer_text


def test_build_book_docx_custom_page_size(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    payload["page_config"] = {"size": "LETTER", "margins_mm": {"top": 15, "bottom": 15, "left": 15, "right": 15}}
    out = build_book_docx(payload)

    from docx import Document
    doc = Document(out["docx_path"])
    section = doc.sections[0]
    assert round(section.page_width.inches, 2) == 8.5
    assert round(section.page_height.inches, 2) == 11.0
    assert round(section.top_margin.mm) == 15


def test_build_book_docx_missing_images_are_skipped(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    # Force an image path that does not exist
    payload["book"]["chapters"][0]["images"] = [str(tmp_path / "no_existe.png")]
    out = build_book_docx(payload)

    assert os.path.isfile(out["docx_path"])  # should not crash
    from docx import Document
    doc = Document(out["docx_path"])  # should open fine
    assert len(doc.inline_shapes) == 0


def test_build_book_docx_no_duplicate_chapters(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    # chapters are already unique in payload
    out = build_book_docx(payload)
    assert out["chapter_count"] == 2

    from docx import Document
    doc = Document(out["docx_path"])
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert headings.count("Introducción") == 1
    assert headings.count("Diseño avanzado") == 1


def test_build_book_docx_uses_edited_over_draft(tmp_path: Path):
    payload = _book_payload(tmp_path, with_images=False)
    payload["book"]["chapters"][0]["edited_es"] = "## Editado\n\nContenido editado."
    out = build_book_docx(payload)

    from docx import Document
    doc = Document(out["docx_path"])  # should not crash
    texts = [p.text for p in doc.paragraphs]
    assert "Contenido editado." in texts
    assert "Contenido del capítulo 1" not in texts


# ---------------------------------------------------------------------------
# 8E.6C — ISOLAMIENTO MULTI-LIBRO POR book_id
# Dos libros del mismo idioma NO deben compartir ruta ni sobreescribirse.
# ---------------------------------------------------------------------------
def test_docx_isolation_two_books_same_language(tmp_path: Path):
    """8E.6C: 1 book_id = 1 artefacto DOCX independiente.

    Secuencia obligada: generar A, inspeccionar, generar B, comprobar que A
    no cambió (ni bytes ni identidad) y que B es B. El cross-overwrite entre
    libros del mismo idioma está eliminado estructuralmente.
    """
    import hashlib
    from docx import Document

    def _payload(book_id: int, title: str) -> dict:
        book = {
            "book_id": book_id,
            "title": title,
            "subtitle": "Manual de algo",
            "description": "Descripción.",
            "author": "Space Lair",
            "target_audience": "General",
            "genre": "Divulgación",
            "languages": ["es"],
            "target_chapters": 1,
            "status": "edited",
            "created_at": datetime(2024, 1, 1).isoformat(),
            "chapters": [
                {
                    "chapter_id": book_id * 10,
                    "book_id": book_id,
                    "number": 1,
                    "title": "Capítulo 1",
                    "edited_es": "## Técnicas\n\nContenido del capítulo.",
                    "images": [],
                },
            ],
        }
        return {
            "book": book,
            "language": "es",
            "page_config": {"size": "A4", "margins_mm": {"top": 25, "bottom": 25, "left": 25, "right": 25}},
        }

    # A (español)
    out_a = build_book_docx(_payload(11, "Libro A"))
    path_a = out_a["docx_path"]
    with open(path_a, "rb") as fh:
        bytes_a_before = fh.read()
    hash_a_before = hashlib.sha256(bytes_a_before).hexdigest()
    title_a_before = Document(path_a).core_properties.title

    # B (español) generado DESPUÉS de A
    out_b = build_book_docx(_payload(22, "Libro B"))
    path_b = out_b["docx_path"]
    with open(path_b, "rb") as fh:
        bytes_b = fh.read()
    title_b = Document(path_b).core_properties.title

    # A sigue siendo A (no fue sobreescrito por B)
    with open(path_a, "rb") as fh:
        hash_a_after = hashlib.sha256(fh.read()).hexdigest()
    title_a_after = Document(path_a).core_properties.title

    # 1) paths distintos
    assert path_a != path_b
    # 2) nombres canónicos por book_id
    assert path_a.endswith("book_11_es.docx")
    assert path_b.endswith("book_22_es.docx")
    # 3) ambos existen físicamente
    assert os.path.isfile(path_a)
    assert os.path.isfile(path_b)
    # 4) A no cambió al generar B (isolated)
    assert hash_a_before == hash_a_after
    assert title_a_before == title_a_after == "Libro A"
    # 5) B contiene su propia identidad
    assert title_b == "Libro B"
    assert bytes_a_before != bytes_b
    # 6) footer de B referencia a su propio archivo canónico
    footer_b = Document(path_b).sections[0].footer.paragraphs[0].text
    assert "book_22_es.docx" in footer_b


@pytest.mark.parametrize("preset,exp_font,exp_color", [
    ("moderno", "Arial", (0x6A, 0x3F, 0xB5)),
    ("editorial", "Georgia", (0x1F, 0x3A, 0x5F)),
    ("clasico", "Times New Roman", (0x00, 0x00, 0x00)),
])
def test_build_book_docx_applies_layout_preset(tmp_path, preset, exp_font, exp_color):
    """FASE 6: el preset de maquetación se aplica a los estilos del DOCX."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    payload = _book_payload(tmp_path, with_images=False)
    payload["book"]["layout_config"] = {"preset": preset, "overrides": {}}
    out = build_book_docx(payload)

    doc = Document(out["docx_path"])
    normal = doc.styles["Normal"]
    heading1 = doc.styles["Heading 1"]

    assert normal.font.name == exp_font
    rgb = heading1.font.color.rgb
    assert (rgb[0], rgb[1], rgb[2]) == exp_color


def test_build_book_docx_layout_overrides_win(tmp_path):
    """FASE 6: los overrides manuales anulan los valores del preset."""
    from docx import Document

    payload = _book_payload(tmp_path, with_images=False)
    payload["book"]["layout_config"] = {
        "preset": "editorial",
        "overrides": {
            "font_family": "Courier New",
            "heading_color": "#000000",
            "body_alignment": "left",
        },
    }
    out = build_book_docx(payload)

    doc = Document(out["docx_path"])
    assert doc.styles["Normal"].font.name == "Courier New"
    rgb = doc.styles["Heading 1"].font.color.rgb
    assert (rgb[0], rgb[1], rgb[2]) == (0x00, 0x00, 0x00)

"""Tests §17 #21 (Opción A) — consumo de campos _en en frontend/editorial.py.

Cubre:
- build_payload writer EN selecciona title_en/outline_en cuando existen y cae
  a ES con log cuando son NULL (caso libros históricos 56-60/62, sin tocarlos).
- _build_book_dict usa books.title_en/description_en y chapters.title_en para
  la edición EN bilingüe; fallback ES explícito si NULL.
- Regresión cero: libro monolingüe 'es' sin ningún cambio de comportamiento.
"""
from __future__ import annotations

import json
import os

import pytest

from core.database import init_db
from frontend.editorial import (
    _build_book_dict,
    build_payload,
    create_book,
    update_book_description_en,
    update_book_title_en,
    update_chapter_outline_en,
    update_chapter_title_en,
    _get_chapters,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t21.db"))
    init_db()


_SECTIONS_EN = [
    {"heading": "Introduction", "objective": "Present the topic"},
    {"heading": "Conclusion", "objective": "Close it"},
]


def test_build_payload_writer_en_uses_outline_en_when_present() -> None:
    b = create_book({"title": "Libro bilingüe", "target_chapters": 1, "language": "es,en"})
    cid = _get_chapters(b["book_id"])[0]["id"]
    update_chapter_title_en(b["book_id"], 1, "Foundations of nutrition")
    update_chapter_outline_en(b["book_id"], 1, _SECTIONS_EN)
    payload = build_payload(b["book_id"], "writer", {"idea": "x"}, chapter_id=cid, language="en")
    assert payload["chapter_outline"]["title"] == "Foundations of nutrition"
    assert [s["heading"] for s in payload["chapter_outline"]["sections"]] == [
        "Introduction", "Conclusion",
    ]


def test_build_payload_writer_en_falls_back_to_es_when_null() -> None:
    """Libro tipo ids 56-60/62: campos _en NULL → comportamiento anterior
    exacto (título/outline ES), sin excepción."""
    b = create_book({"title": "Libro histórico", "target_chapters": 1, "language": "es,en"})
    cid = _get_chapters(b["book_id"])[0]["id"]
    payload = build_payload(b["book_id"], "writer", {"idea": "x"}, chapter_id=cid, language="en")
    assert payload["chapter_outline"]["title"] == f"Capítulo {cid and 1}"
    assert payload["chapter_outline"]["title"].startswith("Capítulo")


def test_build_book_dict_en_uses_title_en_when_present_and_es_when_null() -> None:
    book = {
        "id": 1, "languages": "es,en", "title": "Título ES", "title_en": "English Title",
        "description": "Desc ES", "description_en": "English desc",
    }
    chapters = [{"id": 10, "number": 1, "title": "Cap ES", "title_en": "Chapter EN", "draft_en": "x"}]
    d = _build_book_dict(book, chapters, language="en")
    assert d["title"] == "English Title"
    assert d["description"] == "English desc"
    assert d["chapters"][0]["title"] == "Chapter EN"

    # NULL → fallback ES explícito
    book_null = {"id": 2, "languages": "es,en", "title": "Título ES"}
    chapters_null = [{"id": 20, "number": 1, "title": "Cap ES", "draft_en": "x"}]
    d2 = _build_book_dict(book_null, chapters_null, language="en")
    assert d2["title"] == "Título ES"
    assert d2["chapters"][0]["title"] == "Cap ES"


def test_monolingual_es_book_no_change(monkeypatch) -> None:
    """Regresión: libro 'es' ignora por completo los campos _en."""
    b = create_book({"title": "Libro español", "target_chapters": 1, "language": "es"})
    cid = _get_chapters(b["book_id"])[0]["id"]
    update_chapter_title_en(b["book_id"], 1, "Should NOT be used")
    update_book_title_en(b["book_id"], "Should NOT be used either")
    payload = build_payload(b["book_id"], "writer", {"idea": "x"}, chapter_id=cid, language="es")
    assert payload["chapter_outline"]["title"].startswith("Capítulo")
    book = {"id": 99, "languages": "es", "title": "Solo ES", "title_en": "Ignored"}
    d = _build_book_dict(book, [{"id": 1, "number": 1, "title": "Cap ES", "title_en": "Ignored", "draft_es": "x"}], language="es")
    assert d["title"] == "Solo ES"
    assert d["chapters"][0]["title"] == "Cap ES"


def test_persist_roundtrip_title_en_and_outline_en() -> None:
    """Los setters persisten y las columnas nuevas se leen de vuelta (migración)."""
    b = create_book({"title": "Libro roundtrip", "target_chapters": 1, "language": "es,en"})
    bid = b["book_id"]
    cid = _get_chapters(bid)[0]["id"]
    update_chapter_title_en(bid, 1, "Round Trip")
    update_chapter_outline_en(bid, 1, _SECTIONS_EN)
    update_book_title_en(bid, "Round Trip Book")
    update_book_description_en(bid, "An English description")
    ch = _get_chapters(bid)[0]
    assert ch["title_en"] == "Round Trip"
    assert json.loads(ch["outline_en"]) == _SECTIONS_EN
    from frontend.editorial import _get_book
    bk = _get_book(bid)
    assert bk["title_en"] == "Round Trip Book"
    assert bk["description_en"] == "An English description"
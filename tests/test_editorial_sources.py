"""Tests 8D.2 — propagación REAL de fuentes Research→Chapter→Quality Gate.

NO modifican chapter_writer/quality_control ni thresholds.
Fuente de verdad única: SourceManager (sources.chapter_ids).
`run_commands` está corrupto → estos tests son ejecutables vía:
    python -m pytest tests/test_editorial_sources.py -q
(validación estática incluida en este checkpoint al no poder correrse).
"""
from __future__ import annotations

import os

import pytest

from core.database import init_db
from core.book.source_manager import SourceManager
from frontend.editorial import (
    create_book,
    _get_book,
    _get_chapters,
    _build_book_dict,
    persist_chapter_result,
)
from modules.quality_control.main import final_quality_control

_META = {
    "title": "Libro de prueba 8D.2",
    "author": "Space Lair",
    "description": "Descripción de prueba 8D.2",
    "genre": "Divulgación",
    "target_audience": "General",
    "language": "es",
}


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t8d2.db"))
    init_db()


def _make_book(target_chapters: int = 1, **over) -> dict:
    d = dict(_META)
    d["target_chapters"] = target_chapters
    d.update(over)
    return create_book(d)


def _book_dict(book_id: int):
    return _build_book_dict(_get_book(book_id), _get_chapters(book_id))


def _chapter_id(book_id: int) -> int:
    return _get_chapters(book_id)[0]["id"]


def _materialize(book_id: int) -> None:
    """Persiste un draft_es mínimo para que el capítulo aparezca en book_dict.

    `_build_book_dict` filtra capítulos sin draft_es/edited_es (editorial.py L448);
    sin este paso `book_dict["chapters"] == []` y los tests indexan fuera de rango.
    """
    cid = _chapter_id(book_id)
    persist_chapter_result(book_id, cid, "draft_es", "Draft mínimo para materializar el capítulo.")


# a) un capítulo con una fuente asociada REAL aparece con esa fuente
def test_a_chapter_with_associated_source_is_visible():
    b = _make_book(1)
    _materialize(b["book_id"])
    cid = _chapter_id(b["book_id"])
    SourceManager.add_source(url="https://real.example/a", title="A", chapter_ids=[cid])
    d = _book_dict(b["book_id"])
    assert "https://real.example/a" in d["chapters"][0]["sources"]


# b) capítulo sin asociación => sources = []
def test_b_chapter_without_association_sources_empty():
    b = _make_book(1)
    _materialize(b["book_id"])
    d = _book_dict(b["book_id"])
    assert d["chapters"][0]["sources"] == []


# c) varias fuentes reales se conservan
def test_c_multiple_real_sources_preserved():
    b = _make_book(1)
    _materialize(b["book_id"])
    cid = _chapter_id(b["book_id"])
    SourceManager.add_source(url="https://real.example/b", chapter_ids=[cid])
    SourceManager.add_source(url="https://real.example/c", chapter_ids=[cid])
    d = _book_dict(b["book_id"])
    assert set(d["chapters"][0]["sources"]) == {"https://real.example/b", "https://real.example/c"}


# d) recovery: reconstruir el book_dict otra vez conserva las asociaciones reales
def test_d_recovery_rebuilds_from_real_associations():
    b = _make_book(1)
    _materialize(b["book_id"])
    cid = _chapter_id(b["book_id"])
    SourceManager.add_source(url="https://real.example/a", chapter_ids=[cid])
    first = _book_dict(b["book_id"])
    # recovery: releer BD y reconstruir
    second = _build_book_dict(_get_book(b["book_id"]), _get_chapters(b["book_id"]))
    assert first["chapters"][0]["sources"] == second["chapters"][0]["sources"]
    assert "https://real.example/a" in second["chapters"][0]["sources"]


# e) research vacío NUNCA inventa fuentes
def test_e_research_empty_does_not_invent_sources():
    b = _make_book(1)  # sin ninguna add_source => simula research vacío
    _materialize(b["book_id"])
    d = _book_dict(b["book_id"])
    assert d["chapters"][0]["sources"] == []


# f) Quality Gate ve las fuentes reales por capítulo
def test_f_quality_gate_sees_real_sources():
    b = _make_book(1)
    cid = _chapter_id(b["book_id"])
    SourceManager.add_source(url="https://real.example/a", chapter_ids=[cid])
    d = _book_dict(b["book_id"])
    out = final_quality_control({"book": d, "language": "es"})
    sc = out["source_checks"]
    assert sc, "source_checks no fue generado"
    assert all(x["status"] == "PASS" for x in sc), sc


# g) Quality Gate reporta FAIL de fuentes cuando NO hay asociación (no artificial)
def test_g_quality_gate_fails_when_no_sources():
    b = _make_book(1)
    _materialize(b["book_id"])
    d = _book_dict(b["book_id"])
    out = final_quality_control({"book": d, "language": "es"})
    sc = out["source_checks"]
    assert any(x["status"] == "FAIL" for x in sc), sc

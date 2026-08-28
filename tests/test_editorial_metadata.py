"""Tests 8E.2 — metadata del libro a través del flujo real de creación.

Cubre el bug real: la UI enviaba solo {title, target_chapters, idea}; `idea` no se
mapeaba a `description` y author/genre/target_audience llegaban vacíos al Quality
Gate, provocando FAIL "Metadatos incompletos".

Protege el flujo real: create_book -> _get_book/_get_chapters -> _build_book_dict
-> final_quality_control. No modifica Quality Gate ni sus thresholds.
"""
from __future__ import annotations

import os

import pytest

from core.database import init_db
from frontend.editorial import (
    create_book,
    _get_book,
    _get_chapters,
    _build_book_dict,
)
from modules.quality_control.main import final_quality_control


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t8e2.db"))
    init_db()


def _book_dict(book_id: int) -> dict:
    return _build_book_dict(_get_book(book_id), _get_chapters(book_id))


def _metadata_item(qc: dict) -> dict:
    for item in qc.get("book_checks", []):
        if item.get("message") == "Metadatos completos":
            return item
    raise AssertionError("No se encontró el check 'Metadatos completos'")


# A) idea -> description
def test_a_idea_maps_to_description_when_no_explicit_description():
    b = create_book({"title": "Libro de prueba", "idea": "Descripción/idea editorial de prueba"})
    assert _get_book(b["book_id"])["description"] == "Descripción/idea editorial de prueba"


# B) metadata explícita se conserva exactamente
def test_b_explicit_metadata_is_preserved():
    payload = {
        "title": "Libro de prueba",
        "author": "J. A. Charneco",
        "description": "Descripción explícita",
        "genre": "Divulgación",
        "target_audience": "General",
    }
    b = create_book(payload)
    row = _get_book(b["book_id"])
    assert row["author"] == "J. A. Charneco"
    assert row["description"] == "Descripción explícita"
    assert row["genre"] == "Divulgación"
    assert row["target_audience"] == "General"


# C) payload mínimo real de UI (post-fix) queda compatible con el contrato del QC
def test_c_real_ui_payload_is_quality_gate_compatible():
    # Payload real enviado por la UI tras 8E.2 (idea + campos de usuario).
    payload = {
        "title": "Libro de prueba",
        "target_chapters": 1,
        "idea": "Idea/descripción editorial de la UI",
        "author": "Autor/a real",
        "genre": "Divulgación",
        "target_audience": "General",
    }
    b = create_book(payload)
    d = _book_dict(b["book_id"])
    assert d["title"] == "Libro de prueba"
    assert d["description"] == "Idea/descripción editorial de la UI"  # idea -> description
    assert d["author"] == "Autor/a real"
    assert d["genre"] == "Divulgación"
    assert d["target_audience"] == "General"


# D) Quality Gate: "Metadatos completos" == PASS a través del flujo real
def test_d_quality_gate_metadata_pass_via_real_flow():
    payload = {
        "title": "Libro de prueba",
        "target_chapters": 1,
        "idea": "Idea editorial",
        "author": "Autor/a",
        "genre": "Divulgación",
        "target_audience": "General",
    }
    b = create_book(payload)
    qc = final_quality_control({"book": _book_dict(b["book_id"]), "language": "es"})
    assert _metadata_item(qc)["status"] == "PASS"


# E) autor: se persiste y llega al QC (no inventado; provisto por el usuario/UI)
def test_e_author_is_persisted_and_reaches_quality_gate():
    # El autor se propaga junto con el resto de metadata que aporta la UI real.
    payload = {
        "title": "Libro de prueba",
        "idea": "Idea editorial",
        "author": "Autor/a real",
        "genre": "Divulgación",
        "target_audience": "General",
    }
    b = create_book(payload)
    row = _get_book(b["book_id"])
    assert row["author"] == "Autor/a real"
    d = _book_dict(b["book_id"])
    assert d["author"] == "Autor/a real"  # llega al dict que consume el QC
    qc = final_quality_control({"book": d, "language": "es"})
    assert _metadata_item(qc)["status"] == "PASS"


# F) payload mínimo real (solo título + idea) SIN author/genre/target_audience:
#    el Quality Gate debe dar "Metadatos completos" == PASS (campos opcionales).
def test_f_minimal_payload_without_author_genre():
    payload = {
        "title": "Libro mínimo",
        "target_chapters": 1,
        "idea": "Descripción/idea editorial de prueba",
    }
    b = create_book(payload)
    d = _book_dict(b["book_id"])
    # En la ruta real author/genre/target_audience quedan None (nunca se inventan)
    assert d["author"] is None
    assert d["genre"] is None
    assert d["target_audience"] is None
    # Y aún así el QC no bloquea el PASS de "Metadatos completos"
    qc = final_quality_control({"book": d, "language": "es"})
    assert _metadata_item(qc)["status"] == "PASS"


# G) §17 #38: title e idea DISTINTOS → cada campo persiste su valor propio.
# (El bug real estaba aguas abajo — core/autopilot.py pisaba books.title con la
# idea del planner fallback — pero este test fija el contrato de create_book.)
def test_g_title_and_idea_distinct_are_persisted_separately():
    b = create_book({"title": "X", "idea": "Y"})
    row = _get_book(b["book_id"])
    assert row["title"] == "X"
    assert row["description"] == "Y"

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


def _dump_book67_job() -> dict:
    """PASO 3 (§17 #28): inspección del job real de book_67 para plan de reset."""
    path = os.path.join("data", "autopilot", "jobs", "book_67.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def test_book67_job_reset_plan_inspection() -> None:
    """Solo lectura: confirma claves de fases a poner PENDING para reset book_67."""
    job = _dump_book67_job()
    assert job["book_id"] == 67
    phase_ids = [p["id"] for p in job["phases"]]
    for required in ("image_plan", "image_gen", "quality_gate"):
        assert required in phase_ids
    targets = {p["id"]: p["status"] for p in job["phases"]
               if p["id"] in ("image_plan", "image_gen", "quality_gate")}
    assert set(targets) == {"image_plan", "image_gen", "quality_gate"}
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


def test_writer_en_payload_no_es_objective_fallback() -> None:
    """§17 #23: el objective del payload writer EN nunca es el texto ES.

    - Con description_en=NULL → objective=None (NO chapter.objective en español).
    - Con description_en presente → se usa description_en.
    - Libro ES: sin cambio (sigue usando chapter.objective).
    """
    b = create_book({"title": "Libro bilingüe", "target_chapters": 1, "language": "es,en"})
    bid = b["book_id"]
    cid = _get_chapters(bid)[0]["id"]

    # description_en NULL → None, nunca el objective ES del capítulo
    payload = build_payload(bid, "writer", {"idea": "x"}, chapter_id=cid, language="en")
    assert payload["chapter_outline"]["objective"] is None

    # description_en presente → se usa
    update_book_description_en(bid, "An English book description")
    payload2 = build_payload(bid, "writer", {"idea": "x"}, chapter_id=cid, language="en")
    assert payload2["chapter_outline"]["objective"] == "An English book description"

    # Regresión ES: libro monolingüe sigue usando el objective PROPIO del capítulo
    # (cualquiera que sea su valor; create_book lo deja NULL y eso se conserva).
    b_es = create_book({
        "title": "Libro español", "target_chapters": 1, "language": "es",
    })
    cid_es = _get_chapters(b_es["book_id"])[0]["id"]
    payload_es = build_payload(
        b_es["book_id"], "writer", {"idea": "x"}, chapter_id=cid_es, language="es"
    )
    assert (
        payload_es["chapter_outline"]["objective"]
        == _get_chapters(b_es["book_id"])[0]["objective"]
    )


def test_writer_en_payload_no_es_sources_fallback_when_lang_empty() -> None:
    """§17 #23: sin desglose por idioma para 'en', el payload writer EN recibe
    research/sources VACÍOS — NUNCA el contenido ES compartido. Libro ES: sin cambio.
    """
    b = create_book({"title": "Libro bilingüe", "target_chapters": 1, "language": "es,en"})
    bid = b["book_id"]
    cid = _get_chapters(bid)[0]["id"]

    es_sources = [{"url": "https://es.example.com/articulo", "title": "Fuente ES"}]
    data_es_only = {
        "idea": "x",
        # Desglose por idioma SIN entrada para 'en' + dato compartido ES:
        # el fallback cruzado antiguo entregaría esto al writer EN.
        "sources_by_lang": {"es": es_sources},
        "research_by_lang": {"es": "- Hecho en español sobre el café."},
        "sources": es_sources,
        "research": "- Hecho en español sobre el café.",
    }
    payload = build_payload(bid, "writer", data_es_only, chapter_id=cid, language="en")
    assert payload["sources"] == []
    assert not payload["research"]

    # Con desglose EN presente sí llega el contenido EN
    data_with_en = {
        "idea": "x",
        "sources_by_lang": {
            "es": es_sources,
            "en": [{"url": "https://en.example.com/article", "title": "EN source"}],
        },
        "research_by_lang": {"es": "- Hecho ES.", "en": "- English fact about coffee."},
    }
    payload_en_ok = build_payload(bid, "writer", data_with_en, chapter_id=cid, language="en")
    assert [s["url"] for s in payload_en_ok["sources"]] == ["https://en.example.com/article"]
    assert payload_en_ok["research"] == "- English fact about coffee."

    # Regresión ES: libro monolingüe conserva el fallback histórico a sources/research
    b_es = create_book({"title": "Libro español", "target_chapters": 1, "language": "es"})
    cid_es = _get_chapters(b_es["book_id"])[0]["id"]
    payload_es = build_payload(b_es["book_id"], "writer", data_es_only, chapter_id=cid_es, language="es")
    assert [s["url"] for s in payload_es["sources"]] == ["https://es.example.com/articulo"]
    assert payload_es["research"] == "- Hecho en español sobre el café."


def test_image_plan_gen_payload_uses_title_en_when_available() -> None:
    """§17 #24: en libros EN, chapter_title del payload de image_plan e
    image_gen usa chapters.title_en cuando existe; fallback al título ES
    (chapters.title) si title_en es NULL/vacío. Regresión ES intacta."""
    b = create_book({"title": "Libro bilingüe", "target_chapters": 1, "language": "es,en"})
    bid = b["book_id"]
    cid = _get_chapters(bid)[0]["id"]

    # title_en NULL → fallback al título ES (comportamiento histórico)
    p_plan = build_payload(bid, "image_plan", {"idea": "x"}, chapter_id=cid, language="en")
    assert p_plan["chapter_title"].startswith("Capítulo")

    update_chapter_title_en(bid, 1, "Chapter Title EN")

    for phase in ("image_plan", "image_gen"):
        p = build_payload(bid, phase, {"idea": "x"}, chapter_id=cid, language="en")
        assert p["chapter_title"] == "Chapter Title EN"

    # Regresión ES: libro monolingüe sigue usando chapters.title
    b_es = create_book({"title": "Libro español", "target_chapters": 1, "language": "es"})
    cid_es = _get_chapters(b_es["book_id"])[0]["id"]
    p_es = build_payload(b_es["book_id"], "image_plan", {"idea": "x"}, chapter_id=cid_es, language="es")
    assert p_es["chapter_title"].startswith("Capítulo")


def test_image_payloads_no_english_anchor_without_native_en() -> None:
    """§17 #28 bug fix 2026-08-26 (caso real book_67): libro bilingüe con
    books.title_en=NULL, description_en=NULL y topic SOLO en español → el
    payload de image_plan/image_gen NUNCA rellena topic_en con texto español
    (queda vacío => fail-open del anclaje en image_search, ver §17 #28b)."""
    b = create_book({
        "title": "Todo sobre el café, descubrimientos, tipos, cafe en el mundo",
        "target_chapters": 1,
        "language": "es,en",
        # title_en/description_en NO se pasan → quedan NULL en BD.
    })
    bid = b["book_id"]
    cid = _get_chapters(bid)[0]["id"]

    for phase in ("image_plan", "image_gen"):
        # Ni job.data.topic_en ni nada nativo EN disponible.
        p = build_payload(
            bid, phase, {"idea": "x"}, chapter_id=cid, language="en"
        )
        assert not str(p.get("topic_en") or "").strip(), (
            f"{phase}: topic_en contiene texto (fallback ES vivo?): {p.get('topic_en')!r}"
        )
        assert "café" not in str(p.get("topic_en") or "")
        assert "mundo" not in str(p.get("topic_en") or "")

    # Con topic_en REAL en job.data sí viaja (contrato positivo intacto).
    p_ok = build_payload(
        bid, "image_plan",
        {"idea": "x", "topic_en": "Everything about coffee around the world"},
        chapter_id=cid, language="en",
    )
    assert p_ok["topic_en"] == "Everything about coffee around the world"
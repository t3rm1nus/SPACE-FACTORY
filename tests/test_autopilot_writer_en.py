"""Tests FASE generación EN nativa (§20 tarea 6): resolución dinámica de la
capability writer según books.languages, y lectura de columnas _en en
build_payload para libros en inglés.

NO llama al LLM real: la task queue/scheduler se mockean y solo se inspecciona
la capability encolada / el payload construido.
"""

from __future__ import annotations

import os
import tempfile

import os
import tempfile

import pytest

from core.autopilot import (
    _resolve_writer_capability,
    default_executor_factory,
)
from core.database import get_db, init_db
from frontend import editorial


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", tmp.name)
    init_db()
    yield tmp.name
    try:
        os.remove(tmp.name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Helper puro: _resolve_writer_capability
# ---------------------------------------------------------------------------
def test_resolve_writer_capability_matrix():
    assert _resolve_writer_capability({"languages": "en"}) == "write_chapter_en"
    assert _resolve_writer_capability({"languages": ["en"]}) == "write_chapter_en"
    assert _resolve_writer_capability(None) == "write_chapter_es"
    assert _resolve_writer_capability({}) == "write_chapter_es"
    assert _resolve_writer_capability({"languages": None}) == "write_chapter_es"
    assert _resolve_writer_capability({"languages": "es"}) == "write_chapter_es"
    assert _resolve_writer_capability({}) == "write_chapter_es"


def test_writer_phase_resolves_capability_by_language(monkeypatch):
    """La fase writer (id 'writer', capability base write_chapter_es) encola
    write_chapter_en para un libro languages='en' y write_chapter_es para uno 'es'."""
    import core.scheduler as sched_mod
    import core.task_queue as tq_mod

    captured = []

    class FakeTask(dict):
        pass

    def fake_enqueue(capability, payload, max_attempts=1):
        captured.append({"capability": capability, "payload": payload})
        return len(captured)

    def fake_get_task(task_id):
        return {"id": task_id, "status": "done", "result": None,
                "module_id": "chapter_writer", "error": None}

    monkeypatch.setattr(tq_mod, "enqueue_task", fake_enqueue)
    monkeypatch.setattr(tq_mod, "get_task", fake_get_task)
    monkeypatch.setattr(sched_mod, "_process_task",
                        lambda *a, **k: None)

    modules = {"chapter_writer": {"id": "chapter_writer"}}
    cap_map = {
        "write_chapter_es": ["chapter_writer"],
        "write_chapter_en": ["chapter_writer"],
    }
    executor = default_executor_factory(modules=modules, cap_map=cap_map, store=None)
    phase = {"id": "writer", "capability": "write_chapter_es", "label": "CHAPTER WRITER"}

    # Libro EN → capability EN
    book_en = editorial.create_book(
        {"title": "English Book", "language": "en", "target_chapters": 1})
    result = executor(phase, {"book_id": book_en["book_id"], "data": {}})
    assert result.ok is True
    assert captured[-1]["capability"] == "write_chapter_en"

    # Libro ES → comportamiento histórico intacto (regresión cero)
    book_es = editorial.create_book(
        {"title": "Libro español", "language": "es", "target_chapters": 1})
    executor(phase, {"book_id": book_es["book_id"], "data": {}})
    assert captured[-1]["capability"] == "write_chapter_es"

    assert phase["id"] == "writer"  # el id/orden de fases no se muta


def test_build_payload_reads_edited_en_for_english_book():
    """build_payload (fact_check/editor/image_plan/image_gen) lee edited_en/draft_en
    cuando books.languages='en', y edited_es/draft_es para libros 'es'."""
    # Libro EN con edited_en poblado
    book_en = editorial.create_book(
        {"title": "EN Book", "language": "en", "target_chapters": 1})
    bid_en = book_en["book_id"]
    ch_en = editorial.get_chapters(bid_en)[0]
    editorial.persist_chapter_result(bid_en, ch_en["id"], "edited_en",
                                     "English chapter content.")
    # El editor consume el DRAFT del writer: poblar también draft_en.
    editorial.persist_chapter_result(bid_en, ch_en["id"], "draft_en",
                                     "English chapter content.")
    editorial.persist_chapter_result(bid_en, ch_en["id"], "draft_es",
                                     "Borrador español que NO debe usarse.")

    for pid in ("fact_check", "editor", "image_plan", "image_gen"):
        payload = editorial.build_payload(bid_en, pid, {}, chapter_id=ch_en["id"])
        assert payload["chapter_text"] == "English chapter content.", pid

    # Regresión: libro ES sigue leyendo edited_es/draft_es
    book_es = editorial.create_book(
        {"title": "ES Book", "language": "es", "target_chapters": 1})
    bid_es = book_es["book_id"]
    ch_es = editorial.get_chapters(bid_es)[0]
    editorial.persist_chapter_result(bid_es, ch_es["id"], "edited_es",
                                     "Contenido español.")

    payload = editorial.build_payload(bid_es, "image_plan", {},
                                      chapter_id=ch_es["id"])
    assert payload["chapter_text"] == "Contenido español."

    # Sin idioma explícito (default BD 'es'): comportamiento intacto
    book_def = editorial.create_book({"title": "Default Book",
                                      "target_chapters": 1})
    ch_def = editorial.get_chapters(book_def["book_id"])[0]
    editorial.persist_chapter_result(book_def["book_id"], ch_def["id"],
                                     "edited_es", "Texto por defecto.")
    payload = editorial.build_payload(book_def["book_id"], "image_plan", {},
                                      chapter_id=ch_def["id"])
    assert payload["chapter_text"] == "Texto por defecto."


def test_editor_phase_persists_by_language(monkeypatch):
    """CAMBIO 3: el resultado del editor persiste en edited_en para libros EN
    y en edited_es para libros ES/default (comportamiento histórico intacto)."""
    import core.scheduler as sched_mod
    import core.task_queue as tq_mod

    captured = []

    def fake_enqueue(capability, payload, max_attempts=1):
        captured.append(capability)
        return len(captured)

    def fake_get_task(task_id):
        import json as _json
        return {"id": task_id, "status": "done",
                "result": _json.dumps({"edited_text": "Edited content."}),
                "module_id": "editor", "error": None}

    monkeypatch.setattr(tq_mod, "enqueue_task", fake_enqueue)
    monkeypatch.setattr(tq_mod, "get_task", fake_get_task)
    monkeypatch.setattr(sched_mod, "_process_task", lambda *a, **k: None)

    modules = {"editor": {"id": "editor"}}
    cap_map = {"edit_chapter": ["editor"]}
    executor = default_executor_factory(modules=modules, cap_map=cap_map, store=None)
    phase = {"id": "editor", "capability": "edit_chapter", "label": "EDITOR"}

    def _chapter_fields(book_id):
        with get_db() as conn:
            row = conn.execute(
                "SELECT edited_en, edited_es FROM chapters WHERE book_id = ?",
                (book_id,),
            ).fetchone()
            return dict(row)

    # El editor consume el draft del writer (flujo real): poblarlo antes.
    def _seed_draft(book_id, field):
        cid = editorial.get_chapters(book_id)[0]["id"]
        editorial.persist_chapter_result(book_id, cid, field, "Draft.")

    # Libro EN → edited_en
    book_en = editorial.create_book(
        {"title": "EN Editor Book", "language": "en", "target_chapters": 1})
    _seed_draft(book_en["book_id"], "draft_en")
    executor(phase, {"book_id": book_en["book_id"], "data": {}})
    fields = _chapter_fields(book_en["book_id"])
    assert fields["edited_es"] in (None, "")
    assert fields["edited_en"] == "Edited content."

    # Libro ES → edited_es (regresión cero)
    book_es = editorial.create_book(
        {"title": "ES Editor Book", "language": "es", "target_chapters": 1})
    _seed_draft(book_es["book_id"], "draft_es")
    executor(phase, {"book_id": book_es["book_id"], "data": {}})
    fields = _chapter_fields(book_es["book_id"])
    assert fields["edited_es"] == "Edited content."
    assert fields["edited_en"] in (None, "")


def test_build_book_dict_en_and_docx_render():
    """CAMBIO 4: _build_book_dict para libro EN con solo contenido EN devuelve
    capítulos NO vacíos con claves edited_en/draft_en pobladas, y el DOCX
    resultante (build_book_docx, language='en') contiene ese contenido."""
    import tempfile

    from docx import Document as DocxDocument

    from modules.document_builder.main import build_book_docx

    tmp = tempfile.mkdtemp()
    original_dir = os.getcwd()
    os.chdir(tmp)
    try:
        book = editorial.create_book(
            {"title": "English Render Book", "language": "en",
             "target_chapters": 1})
        bid = book["book_id"]
        ch = editorial.get_chapters(bid)[0]
        editorial.persist_chapter_result(bid, ch["id"], "draft_en",
                                         "## Topic\n\nUnique English body text.")

        book_row = editorial._get_book(bid)
        chapters_rows = editorial.get_chapters(bid)
        book_dict = editorial._build_book_dict(book_row, chapters_rows)

        # El dict NO descarta el capítulo EN y expone las claves que
        # document_builder consume por idioma.
        assert len(book_dict["chapters"]) == 1
        assert book_dict["chapters"][0]["draft_en"] == (
            "## Topic\n\nUnique English body text.")
        assert book_dict["chapters"][0]["edited_en"] == (
            "## Topic\n\nUnique English body text.")

        out = build_book_docx({"book": book_dict, "language": "en"})
        texts = [p.text for p in DocxDocument(out["docx_path"]).paragraphs]
        assert any("Unique English body text." in t for t in texts)
        assert "Table of Contents" in texts
        assert not any(t == "Índice" for t in texts)
    finally:
        os.chdir(original_dir)
def test_bilingual_book_populates_draft_es_and_en(monkeypatch):
    """CAMBIO 5 (multidioma): con ``languages="es,en"`` la fase writer ejecuta
    write_chapter_es Y write_chapter_en por capítulo y persiste draft_es Y
    draft_en del MISMO chapter_id. Confirma también el caso 1-idioma intacto."""
    import json

    import core.scheduler as sched_mod
    import core.task_queue as tq_mod

    captured = []

    def fake_enqueue(capability, payload, max_attempts=1):
        captured.append({"capability": capability, "payload": payload})
        return len(captured)

    def fake_get_task(task_id):
        return {"id": task_id, "status": "done",
                "result": json.dumps({
                    "chapter_md_path": None,
                    "metadata": {"text": f"Text for task {task_id}"},
                    "words": 7,
                }),
                "module_id": "chapter_writer", "error": None}

    monkeypatch.setattr(tq_mod, "enqueue_task", fake_enqueue)
    monkeypatch.setattr(tq_mod, "get_task", fake_get_task)
    monkeypatch.setattr(sched_mod, "_process_task", lambda *a, **k: None)

    modules = {"chapter_writer": {"id": "chapter_writer"}}
    cap_map = {
        "write_chapter_es": ["chapter_writer"],
        "write_chapter_en": ["chapter_writer"],
    }
    executor = default_executor_factory(modules=modules, cap_map=cap_map, store=None)
    phase = {"id": "writer", "capability": "write_chapter_es", "label": "CHAPTER WRITER"}

    # Libro bilingüe -> dos capabilities distintas en el bucle por idioma.
    book = editorial.create_book(
        {"title": "Both Languages", "language": "es,en", "target_chapters": 1})
    bid = book["book_id"]
    cid = editorial.get_chapters(bid)[0]["id"]

    result = executor(phase, {"book_id": bid, "data": {}})
    assert result.ok is True
    # Debe haberse encolado exactamente UN write por cada idioma del libro.
    encod_a = [c["capability"] for c in captured
               if isinstance(c.get("payload"), dict)
               and str(c["payload"].get("book_id")) == str(bid)]
    assert sorted(encod_a) == ["write_chapter_en", "write_chapter_es"]

    # AMBAS columnas draft del MISMO capítulo quedan pobladas.
    with get_db() as conn:
        row = conn.execute(
            "SELECT draft_es, draft_en FROM chapters WHERE id = ?", (cid,)).fetchone()
    assert (row["draft_es"] or "").strip() != ""
    assert (row["draft_en"] or "").strip() != ""

    # Retrocompatibilidad: idioma único ES sigue escribiendo SOLO draft_es.
    captured.clear()
    book_es = editorial.create_book(
        {"title": "Solo ES", "language": "es", "target_chapters": 1})
    executor(phase, {"book_id": book_es["book_id"], "data": {}})
    cid_es = editorial.get_chapters(book_es["book_id"])[0]["id"]
    with get_db() as conn:
        row_es = conn.execute(
            "SELECT draft_es, draft_en FROM chapters WHERE id = ?", (cid_es,)).fetchone()
    assert (row_es["draft_es"] or "").strip() != ""
    assert (row_es["draft_en"] or "").strip() in ("", None)
"""Tests del panel editorial (Fase 2): crear libros, pipeline, capítulos, DOCX."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.database import get_db, init_db
from core.task_queue import all_tasks, get_task
from frontend.frontend_api import create_app


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


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _create_book(client, title="Historia de Internet", chapters=3):
    resp = client.post("/api/books", data=json.dumps({
        "title": title,
        "subtitle": "De ARPANET a la IA",
        "author": "J. A. Charneco",
        "genre": "tecnologia",
        "language": "es",
        "target_audience": "General",
        "target_chapters": chapters,
        "target_words": 3000,
        "images_per_chapter": 3,
        "description": "Libro sobre la historia de Internet.",
    }), content_type="application/json")
    return resp


def _make_chapter(book_id, number, text):
    with get_db() as conn:
        conn.execute(
            "UPDATE chapters SET draft_es = ? WHERE book_id = ? AND number = ?",
            (text, book_id, number),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM chapters WHERE book_id = ? AND number = ?",
            (book_id, number),
        ).fetchone()
        return row["id"]


def _chapter_id(book_id, number):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM chapters WHERE book_id = ? AND number = ?",
            (book_id, number),
        ).fetchone()
        return row["id"]


# ============================================
# Crear / cargar libro
# ============================================

def test_create_book_returns_201(client):
    resp = _create_book(client)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["book_id"] > 0
    assert data["chapters"] == 3
    assert data["status"] == "planned"


def test_create_book_requires_title(client):
    resp = client.post("/api/books", data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 400


def test_load_book_creates_chapters(client):
    book = _create_book(client, chapters=3).get_json()
    resp = client.get(f"/api/books/{book['book_id']}/load")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["chapters"]) == 3
    assert data["stats"]["total_chapters"] == 3
    assert data["book"]["title"] == "Historia de Internet"


def test_load_missing_book_404(client):
    resp = client.get("/api/books/9999/load")
    assert resp.status_code == 404


def test_pipeline_endpoint(client):
    resp = client.get("/api/pipeline")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data) == 9
    ids = [p["id"] for p in data]
    assert "planner" in ids and "writer" in ids and "docx" in ids


# ============================================
# Ejecutar fases
# ============================================

def test_run_book_planner_creates_task(client):
    book = _create_book(client).get_json()
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "planner",
        "idea": "Historia de Internet",
        "target_chapters": 3,
        "language": "es",
    }), content_type="application/json")
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["phase"] == "planner"
    assert data["capability"] == "create_book_plan"
    task = get_task(data["task_id"])
    assert task["status"] == "pending"
    payload = json.loads(task["payload"])
    assert payload["book_id"] == book["book_id"]


def test_run_writer_requires_chapter(client):
    book = _create_book(client).get_json()
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "writer",
    }), content_type="application/json")
    assert resp.status_code == 400


def test_run_writer_with_chapter_creates_task(client):
    book = _create_book(client).get_json()
    ch = _chapter_id(book["book_id"], 1)
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "writer",
        "chapter_id": ch,
        "target_words": 3000,
    }), content_type="application/json")
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["capability"] == "write_chapter_es"


def test_run_invalid_phase(client):
    book = _create_book(client).get_json()
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "no_existe",
    }), content_type="application/json")
    assert resp.status_code == 400


def test_run_fact_check_requires_draft(client):
    book = _create_book(client).get_json()
    ch = _chapter_id(book["book_id"], 1)
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "fact_check",
        "chapter_id": ch,
    }), content_type="application/json")
    assert resp.status_code == 400


def test_run_fact_check_with_text(client):
    book = _create_book(client).get_json()
    ch = _make_chapter(book["book_id"], 1, "Texto del capítulo para verificar.")
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "fact_check",
        "chapter_id": ch,
    }), content_type="application/json")
    assert resp.status_code == 201
    assert resp.get_json()["capability"] == "fact_check_chapter"


def test_run_editor_requires_draft(client):
    book = _create_book(client).get_json()
    ch = _chapter_id(book["book_id"], 1)
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "editor",
        "chapter_id": ch,
    }), content_type="application/json")
    assert resp.status_code == 400


def test_run_editor_with_draft(client):
    book = _create_book(client).get_json()
    ch = _make_chapter(book["book_id"], 1, "Borrador que se va a editar.")
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "editor",
        "chapter_id": ch,
        "style_guide": "Claro y directo",
    }), content_type="application/json")
    assert resp.status_code == 201
    assert resp.get_json()["capability"] == "edit_chapter"


def test_run_image_plan_with_chapter(client):
    book = _create_book(client).get_json()
    ch = _make_chapter(book["book_id"], 1, "Texto para generar imágenes.")
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "image_plan",
        "chapter_id": ch,
        "num_images": 3,
    }), content_type="application/json")
    assert resp.status_code == 201
    assert resp.get_json()["capability"] == "create_chapter_image_plan"


def test_build_payload_preserves_num_images_zero(client):
    # Bug "or 3" (#4): num_images=0 se convertía silenciosamente en 3.
    # Ahora 0 se conserva; solo el valor ausente (None) cae al default 3.
    from frontend import editorial
    book = _create_book(client).get_json()
    ch = _make_chapter(book["book_id"], 1, "Texto para imágenes.")

    plan = editorial.build_payload(book["book_id"], "image_plan", {"num_images": 0}, ch)
    assert plan["num_images"] == 0

    gen = editorial.build_payload(book["book_id"], "image_gen", {"num_images": 0}, ch)
    assert gen["num_images"] == 0

    # Ausente (None) sigue usando el default 3
    plan_default = editorial.build_payload(book["book_id"], "image_plan", {}, ch)
    assert plan_default["num_images"] == 3


def test_run_image_gen_requires_chapter(client):
    book = _create_book(client).get_json()
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "image_gen",
    }), content_type="application/json")
    assert resp.status_code == 400


def test_run_docx_builds_payload(client):
    book = _create_book(client).get_json()
    _make_chapter(book["book_id"], 1, "Capítulo 1 con contenido.")
    _make_chapter(book["book_id"], 2, "Capítulo 2 con contenido.")
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "docx",
        "language": "es",
    }), content_type="application/json")
    assert resp.status_code == 201
    assert resp.get_json()["capability"] == "build_book_docx"


def test_run_docx_maps_chapters_in_payload(client):
    book = _create_book(client).get_json()
    _make_chapter(book["book_id"], 1, "Capítulo 1 con contenido.")
    resp = client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "docx",
    }), content_type="application/json")
    task = get_task(resp.get_json()["task_id"])
    payload = json.loads(task["payload"])
    assert payload["book"]["book_id"] == book["book_id"]
    assert payload["language"] == "es"
    assert len(payload["book"]["chapters"]) == 1


# ============================================
# Progreso y DOCX
# ============================================

def test_load_book_docx_not_ready(client):
    book = _create_book(client).get_json()
    data = client.get(f"/api/books/{book['book_id']}/load").get_json()
    assert data["stats"]["docx_ready"] is False
    assert data["stats"]["progress"] == 0


def test_load_book_progress_with_drafts(client):
    book = _create_book(client).get_json()
    _make_chapter(book["book_id"], 1, "Capítulo completado con texto.")
    data = client.get(f"/api/books/{book['book_id']}/load").get_json()
    # Un capítulo con solo draft_es está "en edición", no completo
    assert data["stats"]["editing"] == 1
    assert data["stats"]["done"] == 0


def test_task_linked_to_book_via_payload(client):
    book = _create_book(client).get_json()
    client.post(f"/api/books/{book['book_id']}/run", data=json.dumps({
        "phase": "planner", "idea": "X", "target_chapters": 3, "language": "es",
    }), content_type="application/json")
    tasks = all_tasks()
    assert len(tasks) == 1
    payload = json.loads(tasks[0]["payload"])
    assert payload["book_id"] == book["book_id"]

def test_create_book_persists_and_propagates_layout_config(client):
    resp = client.post("/api/books", data=json.dumps({
        "title": "Libro con maquetación",
        "target_chapters": 2,
        "layout_config": {
            "preset": "moderno",
            "overrides": {"font_family": "Georgia", "heading_color": "#6A3FB5",
                          "body_alignment": "left"},
        },
    }), content_type="application/json")
    book_id = resp.get_json()["book_id"]

    from frontend.editorial import load_book, _build_book_dict, _get_book, _get_chapters
    loaded = load_book(book_id)
    lc = loaded["book"]["layout_config"]
    assert lc["preset"] == "moderno"
    assert lc["overrides"]["font_family"] == "Georgia"

    book = _get_book(book_id)
    chapters = _get_chapters(book_id)
    dd = _build_book_dict(book, chapters)
    assert dd["layout_config"]["preset"] == "moderno"
    assert dd["layout_config"]["overrides"]["heading_color"] == "#6A3FB5"


def test_create_book_without_layout_config_defaults_none(client):
    resp = client.post("/api/books", data=json.dumps({
        "title": "Sin maquetación",
        "target_chapters": 1,
    }), content_type="application/json")
    book_id = resp.get_json()["book_id"]
    from frontend.editorial import _build_book_dict, _get_book, _get_chapters
    book = _get_book(book_id)
    chapters = _get_chapters(book_id)
    dd = _build_book_dict(book, chapters)
    assert dd["layout_config"] is None
def test_create_book_persists_image_search_ratio(client):
    from frontend.editorial import _get_book

    # Con campo presente -> queda 0.5 en BD.
    resp = client.post("/api/books", data=json.dumps({
        "title": "Con ratio",
        "target_chapters": 1,
        "image_search_ratio": 0.5,
    }), content_type="application/json")
    book_id = resp.get_json()["book_id"]
    row = _get_book(book_id)
    assert row["image_search_ratio"] == pytest.approx(0.5)

    # Sin campo -> default 0.0.
    resp2 = client.post("/api/books", data=json.dumps({
        "title": "Sin ratio",
        "target_chapters": 1,
    }), content_type="application/json")
    book_id2 = resp2.get_json()["book_id"]
    row2 = _get_book(book_id2)
    assert row2["image_search_ratio"] == pytest.approx(0.0)

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
# persist_chapter_images — fix §17 #26
# ============================================

def test_persist_chapter_images_discards_orphaned_local_without_metadata(client, monkeypatch, tmp_path):
    """§17 #26: una ruta 'local' sin *.metadata.json en el directorio real del
    capítulo es huérfana (metadata sobrescrita por re-generación) y NO se
    persiste. Una ruta 'local' CON metadata que la respalde SÍ se persiste.
    Las rutas comfyui/web pasan sin cambio (regresión)."""
    from frontend import editorial

    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path))

    assert _create_book(client).status_code == 201
    book_id = 1
    chapter_id = _make_chapter(book_id, 1, "Texto de prueba del capítulo.")

    # Metadata legítima que respalda UNA de las rutas locales (caso general:
    # un placeholder con metadata presente no debe descartarse).
    images_dir = tmp_path / "books" / "1" / "chapters" / "1" / "images"
    images_dir.mkdir(parents=True)
    local_supported = "data/images/local/1111111111_abcdef123456.png"
    (images_dir / "img_01_hero.metadata.json").write_text(
        json.dumps({"image_id": "img_01_hero", "provider": "local",
                    "image_path": local_supported, "status": "ok"}),
        encoding="utf-8",
    )

    orphan_local = "data/images/local\\934705850_a938610a44c0.png"
    comfyui_path = "data/images/comfyui\\4153029846_space_lair_4153029846_00001_.png"

    result = editorial.persist_chapter_images(
        book_id, chapter_id,
        [orphan_local, local_supported, comfyui_path],
    )

    assert result["updated"] is True
    assert result["discarded_orphaned_local"] == 1

    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ? AND book_id = ?",
            (chapter_id, book_id),
        ).fetchone()
    persisted = json.loads(row["images"])
    assert orphan_local not in persisted          # huérfana descartada
    assert local_supported in persisted           # local legítimo conservado
    assert comfyui_path in persisted              # comfyui sin cambio


def test_persist_chapter_images_keeps_all_when_no_local_paths(client, monkeypatch):
    """Regresión: sin rutas 'local', el comportamiento es idéntico al anterior."""
    from frontend import editorial

    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tempfile.mkdtemp()))

    assert _create_book(client).status_code == 201
    chapter_id = _make_chapter(1, 1, "Texto.")
    paths = [
        "data/images/comfyui/a.png",
        "data/images/web/b.png",
    ]
    result = editorial.persist_chapter_images(1, chapter_id, paths)
    assert result["discarded_orphaned_local"] == 0

    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ? AND book_id = ?",
            (chapter_id, 1),
        ).fetchone()
    assert json.loads(row["images"]) == paths

# ============================================
# persist_chapter_images — fix §17 #31 (merge por image_path)
# ============================================

def test_persist_chapter_images_merges_with_existing(client, monkeypatch):
    """§17 #31: persistir 1 imagen NUEVA sobre un capítulo con 2 previas válidas
    conserva las 3 (ninguna perdida) — merge, no overwrite."""
    from frontend import editorial

    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tempfile.mkdtemp()))

    assert _create_book(client).status_code == 201
    chapter_id = _make_chapter(1, 1, "Texto.")
    prev = ["data/images/comfyui/a.png", "data/images/web/b.png"]
    editorial.persist_chapter_images(1, chapter_id, prev)

    new_path = "data/images/comfyui/c.png"
    result = editorial.persist_chapter_images(1, chapter_id, [new_path])
    assert result["updated"] is True
    assert result["images_count"] == 3

    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ? AND book_id = ?",
            (chapter_id, 1),
        ).fetchone()
    persisted = json.loads(row["images"])
    assert len(persisted) == 3
    assert prev[0] in persisted and prev[1] in persisted and new_path in persisted


def test_persist_chapter_images_same_path_overwrites(client, monkeypatch):
    """§17 #31: persistir una imagen con el MISMO path es una regeneración
    intencional — la nueva gana (no hay duplicados en chapters.images)."""
    from frontend import editorial

    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tempfile.mkdtemp()))

    assert _create_book(client).status_code == 201
    chapter_id = _make_chapter(1, 1, "Texto.")
    same_path = "data/images/comfyui/a.png"
    editorial.persist_chapter_images(1, chapter_id, [same_path])

    result = editorial.persist_chapter_images(1, chapter_id, [same_path])
    assert result["images_count"] == 1

    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ? AND book_id = ?",
            (chapter_id, 1),
        ).fetchone()
    persisted = json.loads(row["images"])
    assert persisted == [same_path]  # 1 sola ocurrencia, no 2


def test_persist_chapter_images_empty_new_list_preserves_previous(client, monkeypatch):
    """§17 #31: lista nueva vacía NO borra las imágenes previas válidas
    (edge case: capítulo sin imágenes en esta ejecución)."""
    from frontend import editorial

    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tempfile.mkdtemp()))

    assert _create_book(client).status_code == 201
    chapter_id = _make_chapter(1, 1, "Texto.")
    prev = ["data/images/comfyui/a.png", "data/images/web/b.png"]
    editorial.persist_chapter_images(1, chapter_id, prev)

    result = editorial.persist_chapter_images(1, chapter_id, [])
    assert result["images_count"] == 2  # conservadas, no borradas

    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ? AND book_id = ?",
            (chapter_id, 1),
        ).fetchone()
    assert json.loads(row["images"]) == prev


# ============================================
# Crear / cargar libro
# ============================================

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


def test_build_book_dict_propagates_image_search_ratio_to_docx_payload(client):
    """Fix A: _build_book_dict debe propagar el image_search_ratio real del
    libro al dict de book (que final_quality_control valida vía Book model,
    activando la tolerancia §17 #30 con ratio==1.0). Dio antes 0.0 siempre
    porque el campo se omitía y el Pydantic default era 0.0."""
    from frontend.editorial import build_payload, _get_book, _get_chapters

    # Libro con ratio=1.0 en BD (elección explícita 100% web).
    resp = client.post("/api/books", data=json.dumps({
        "title": "Libro ratio 1.0",
        "target_chapters": 1,
        "image_search_ratio": 1.0,
        "image_count": 5,
    }), content_type="application/json")
    book_id = resp.get_json()["book_id"]

    book = _get_book(book_id)
    assert book["image_search_ratio"] == pytest.approx(1.0)

    # La fase quality_gate se construye como la de "docx" (usa _build_book_dict).
    payload = build_payload(book_id, "docx", {}, chapter_id=None, language="es")
    book_dict = payload["book"]
    assert book_dict["image_search_ratio"] == pytest.approx(1.0)
    assert book_dict["image_count"] == 5

    # Y con build_phase_payload (ruta real del autopilot) también propaga.
    from core.book.book_schema import Book
    parsed = Book.model_validate(book_dict)
    assert parsed.image_search_ratio == pytest.approx(1.0)

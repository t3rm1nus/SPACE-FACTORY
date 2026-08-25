"""Tests del endpoint DELETE /api/books/<book_id> (borrado de libros).

Verifican:
- 200 al borrar un libro existente; desaparece de GET /api/books.
- 404 al borrar un libro inexistente (y al repetir el borrado).
- Los capítulos asociados se borran (books + chapters), sin tocar `sources`.
- 409 cuando el job autopilot está PENDING/RUNNING.
- El DOCX real en output/docx/book_{id}_*.docx se borra best-effort
  (helper `_book_docx_paths` monkeypatcheado para no tocar output/ real).

Aíslan la BD (SPACE_LAIR_DB_PATH) y el BookJobStore (directorio temporal).
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

import frontend.frontend_api as frontend_api
from core.database import get_db, init_db
from frontend.frontend_api import create_app


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", tmp.name)
    init_db()
    yield
    try:
        os.remove(tmp.name)
    except OSError:
        pass


@pytest.fixture
def ap_store(tmp_path):
    return frontend_api.autopilot.BookJobStore(os.path.join(str(tmp_path), "jobs"))


@pytest.fixture
def client(monkeypatch, ap_store):
    monkeypatch.setattr(frontend_api, "get_autopilot_store", lambda: ap_store)
    monkeypatch.setattr(frontend_api, "ensure_autopilot_worker_started", lambda: None)
    monkeypatch.setattr(frontend_api, "_autopilot_worker_started", False)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _create_book(client, title="Libro a borrar"):
    resp = client.post(
        "/api/books",
        data=json.dumps({
            "title": title,
            "language": "es",
            "target_chapters": 2,
            "target_words": 1500,
        }),
        content_type="application/json",
    )
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return resp.get_json()


# ============================================
# 1. DELETE existente -> 200 y desaparece del listado
# ============================================
def test_delete_book_200_and_removed_from_list(client):
    data = _create_book(client)
    book_id = data["book_id"]

    resp = client.delete(f"/api/books/{book_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["deleted"] == book_id

    books = client.get("/api/books").get_json()
    assert all(b["id"] != book_id for b in books)

    # Detalle ya no existe
    assert client.get(f"/api/books/{book_id}").status_code == 404

    # Repetir el borrado -> 404
    assert client.delete(f"/api/books/{book_id}").status_code == 404


def test_delete_book_removes_chapters_but_keeps_sources(client):
    data = _create_book(client)
    book_id = data["book_id"]

    # Capítulo asociado + fuente compartida que NO debe borrarse.
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO chapters (book_id, number, title) VALUES (?, ?, ?)",
            (book_id, 1, "Cap 1"),
        )
        chapter_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO sources (url, title, chapter_ids) VALUES (?, ?, ?)",
            ("https://example.com/fuente", "Fuente", json.dumps([chapter_id])),
        )
        source_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    assert client.delete(f"/api/books/{book_id}").status_code == 200

    conn = get_db()
    try:
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM chapters WHERE book_id = ?", (book_id,)
        ).fetchone()["n"] == 0
        row = conn.execute(
            "SELECT chapter_ids FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        assert row is not None  # la fuente se conserva (compartida/reutilizable)
        assert book_id not in json.loads(row["chapter_ids"])
    finally:
        conn.close()


def test_delete_nonexistent_book_404(client):
    assert client.delete("/api/books/999999").status_code == 404


# ============================================
# 2. DELETE con autopilot PENDING/RUNNING -> 409
# ============================================
def test_delete_book_with_pending_job_409(client):
    data = _create_book(client)
    book_id = data["book_id"]
    resp = client.post(f"/api/books/{book_id}/autopilot/start")
    assert resp.status_code == 201  # job queda PENDING

    resp = client.delete(f"/api/books/{book_id}")
    assert resp.status_code == 409
    # El libro sigue existiendo.
    assert client.get(f"/api/books/{book_id}").status_code == 200


# ============================================
# 3. DOCX best-effort: si existe, se borra; si no, no falla
# ============================================
def test_delete_book_removes_docx_if_present(client, tmp_path, monkeypatch):
    data = _create_book(client)
    book_id = data["book_id"]

    fake_docx = tmp_path / f"book_{book_id}_es.docx"
    fake_docx.write_bytes(b"PK\x03\x04")  # contenido mínimo

    monkeypatch.setattr(
        frontend_api, "_book_docx_paths", lambda bid: [str(fake_docx)]
        if bid == book_id else []
    )

    resp = client.delete(f"/api/books/{book_id}")
    assert resp.status_code == 200
    assert resp.get_json()["docx_removed"] == [fake_docx.name]
    assert not fake_docx.exists()


def test_delete_book_without_docx_still_200(client, monkeypatch):
    data = _create_book(client)
    book_id = data["book_id"]
    monkeypatch.setattr(frontend_api, "_book_docx_paths", lambda bid: [])

    resp = client.delete(f"/api/books/{book_id}")
    assert resp.status_code == 200
    assert resp.get_json()["docx_removed"] == []


# ============================================
# 4. DELETE /api/books (borrado masivo)
# ============================================
def test_delete_all_books_skips_running(client):
    """Borra varios libros con uno RUNNING: ese se salta, el resto se borra."""
    ids = []
    for i in range(3):
        data = _create_book(client, title=f"Masivo {i}")
        ids.append(data["book_id"])

    # El libro del medio queda con job RUNNING.
    running_id = ids[1]
    client.post(f"/api/books/{running_id}/autopilot/start")
    # Poner el job en RUNNING real (start lo deja PENDING).
    import frontend.frontend_api as fa
    store = fa.get_autopilot_store()
    job = store.load_by_book(running_id)
    job["status"] = "RUNNING"
    store.save(job)

    resp = client.delete("/api/books")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total_books"] == 3
    assert sorted(body["skipped_running"]) == [running_id]
    assert sorted(body["deleted"]) == sorted(i for i in ids if i != running_id)

    books = client.get("/api/books").get_json()
    assert [b["id"] for b in books] == [running_id]

    # El individual sigue funcionando sobre el superviviente (409).
    assert client.delete(f"/api/books/{running_id}").status_code == 409


def test_delete_all_books_empty_db_returns_empty_200(client):
    resp = client.delete("/api/books")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"deleted": [], "skipped_running": [], "total_books": 0}


def test_delete_all_books_does_not_touch_sources(client):
    """Mismo criterio que el borrado individual: sources intactas."""
    data = _create_book(client)
    book_id = data["book_id"]

    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO chapters (book_id, number, title) VALUES (?, ?, ?)",
            (book_id, 1, "Cap masivo"),
        )
        chapter_id = cur.lastrowid
        cur = conn.execute(
            "INSERT INTO sources (url, title, chapter_ids) VALUES (?, ?, ?)",
            ("https://example.com/masiva", "Fuente masiva", json.dumps([chapter_id])),
        )
        source_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    resp = client.delete("/api/books")
    assert resp.status_code == 200
    assert resp.get_json()["deleted"] == [book_id]

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT url, title, chapter_ids FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        assert row is not None  # la fuente se conserva
        assert row["url"] == "https://example.com/masiva"
        assert row["title"] == "Fuente masiva"
        assert json.loads(row["chapter_ids"]) == [chapter_id]  # sin mutaciones
        assert conn.execute("SELECT COUNT(*) AS n FROM books").fetchone()["n"] == 0
    finally:
        conn.close()

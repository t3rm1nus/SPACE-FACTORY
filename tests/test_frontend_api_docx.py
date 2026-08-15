"""Tests del endpoint GET /api/books/<id>/docx (Fase 8D.2).

Verifican que el DOCX REAL generado por Document Builder se sirve de forma
segura desde el frontend, sin generar un DOCX nuevo ni simular la respuesta:

- completed + docx existente           -> 200, Content-Type DOCX, tamaño > 0
- job inexistente                      -> 404
- job no completed                     -> 409 (convención del endpoint)
- docx_path sin valor (job completed)  -> 404
- archivo docx ausente en disco        -> 404
- path fuera del directorio permitido  -> 400 (path traversal rechazado)
- otro book_id (sin job para él)       -> 404

El endpoint ya estaba implementado (8A/8B/8C); aquí se fija su comportamiento
real con tests. No se reimplementa lógica ni se usa mock data.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest
from docx import Document

import frontend.frontend_api as frontend_api
from core import autopilot
from core.database import init_db
from frontend.frontend_api import create_app

JOB_COMPLETED = autopilot.JOB_COMPLETED
JOB_RUNNING = autopilot.JOB_RUNNING
JOB_PENDING = autopilot.JOB_PENDING
PHASE_PASS = autopilot.PHASE_PASS

# MIME del DOCX (Office Open XML)
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
PROJ_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


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
    """BookJobStore aislado en un directorio temporal."""
    return autopilot.BookJobStore(os.path.join(str(tmp_path), "jobs"))


@pytest.fixture
def client(monkeypatch, ap_store):
    """Cliente Flask con store temporal y SIN worker real."""
    monkeypatch.setattr(frontend_api, "get_autopilot_store", lambda: ap_store)
    monkeypatch.setattr(frontend_api, "ensure_autopilot_worker_started", lambda: None)
    monkeypatch.setattr(frontend_api, "_autopilot_worker_started", False)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def real_docx_dir():
    """Directorio temporal REAL bajo output/docx (dentro del proyecto) con un
    DOCX físico generado por python-docx. Se limpia al final del test."""
    base = os.path.join(PROJ_ROOT, "output", "docx")
    os.makedirs(base, exist_ok=True)
    d = tempfile.mkdtemp(prefix="_8d_docx_test_", dir=base)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _make_docx(path: str) -> str:
    doc = Document()
    doc.add_paragraph("Space Lair 8D - DOCX real de prueba.")
    doc.save(path)
    return path


def _create_book(client, title="Libro DOCX"):
    resp = client.post(
        "/api/books",
        data=json.dumps({
            "title": title,
            "language": "es",
            "target_chapters": 1,
            "target_words": 1500,
        }),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _complete_job_with_docx(ap_store, book_id, docx_path):
    """Crea un job COMPLETADO con docx_path registrado y fase docx PASS."""
    job = autopilot.create_job(ap_store, book_id)
    job["status"] = JOB_COMPLETED
    job["docx_path"] = docx_path
    for ph in job["phases"]:
        if ph["id"] == "docx":
            ph["status"] = PHASE_PASS
            ph["metrics"] = {"docx_path": docx_path}
    ap_store.save(job)
    return job



# ---------------------------------------------------------------------------
# 1. completed + docx existente -> 200 + DOCX real
# ---------------------------------------------------------------------------
def test_docx_completed_returns_real_file(client, ap_store, real_docx_dir):
    book = _create_book(client)
    docx_path = _make_docx(os.path.join(real_docx_dir, "book_es.docx"))
    _complete_job_with_docx(ap_store, book["book_id"], docx_path)

    resp = client.get(f"/api/books/{book['book_id']}/docx")
    assert resp.status_code == 200
    assert DOCX_MIME in resp.content_type
    assert len(resp.data) > 0

    # El archivo servido es el DOCX real generado (mismo contenido).
    assert resp.data == open(docx_path, "rb").read()


# ---------------------------------------------------------------------------
# 2. job inexistente -> 404
# ---------------------------------------------------------------------------
def test_docx_job_missing_404(client, ap_store):
    book = _create_book(client)
    resp = client.get(f"/api/books/{book['book_id']}/docx")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


# ---------------------------------------------------------------------------
# 3. job no completed -> 409
# ---------------------------------------------------------------------------
def test_docx_job_not_completed_409(client, ap_store):
    book = _create_book(client)
    job = autopilot.create_job(ap_store, book["book_id"])
    assert job["status"] in (JOB_PENDING, JOB_RUNNING)
    resp = client.get(f"/api/books/{book['book_id']}/docx")
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["status"] == job["status"]
    assert "COMPLETED" in body["error"]


# ---------------------------------------------------------------------------
# 4. job completed pero sin docx_path -> 404
# ---------------------------------------------------------------------------
def test_docx_no_docx_path_404(client, ap_store):
    book = _create_book(client)
    job = autopilot.create_job(ap_store, book["book_id"])
    job["status"] = JOB_COMPLETED
    job["docx_path"] = None
    ap_store.save(job)
    resp = client.get(f"/api/books/{book['book_id']}/docx")
    assert resp.status_code == 404
    assert "docx_path" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# 5. archivo docx ausente en disco -> 404
# ---------------------------------------------------------------------------
def test_docx_missing_file_404(client, ap_store, real_docx_dir):
    book = _create_book(client)
    # docx_path registrado pero el archivo NO se crea físicamente
    ghost = os.path.join(real_docx_dir, "no_existe.docx")
    _complete_job_with_docx(ap_store, book["book_id"], ghost)
    assert not os.path.exists(ghost)
    resp = client.get(f"/api/books/{book['book_id']}/docx")
    assert resp.status_code == 404
    assert "no existe" in resp.get_json()["error"].lower()


# ---------------------------------------------------------------------------
# 6. path traversal: fuera del directorio permitido -> 400
# ---------------------------------------------------------------------------
def test_docx_path_traversal_rejected(client, ap_store):
    book = _create_book(client)
    _complete_job_with_docx(ap_store, book["book_id"], "../fuera_proyecto.docx")
    resp = client.get(f"/api/books/{book['book_id']}/docx")
    assert resp.status_code == 400
    assert "traversal" in resp.get_json()["error"].lower()


def test_docx_absolute_outside_rejected(client, ap_store):
    book = _create_book(client)
    outside = os.path.abspath(os.path.join(PROJ_ROOT, "..", "secreto.docx"))
    _complete_job_with_docx(ap_store, book["book_id"], outside)
    resp = client.get(f"/api/books/{book['book_id']}/docx")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 7. otro book_id sin job para él -> 404
# ---------------------------------------------------------------------------
def test_docx_other_book_rejected(client, ap_store, real_docx_dir):
    book_a = _create_book(client, "A")
    docx_a = _make_docx(os.path.join(real_docx_dir, "a.docx"))
    _complete_job_with_docx(ap_store, book_a["book_id"], docx_a)

    # otro libro creado pero SIN job -> no es propietario de ese docx
    book_b = _create_book(client, "B")
    resp = client.get(f"/api/books/{book_b['book_id']}/docx")
    assert resp.status_code == 404

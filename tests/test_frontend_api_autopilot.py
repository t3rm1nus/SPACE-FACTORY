"""Tests de integración Flask del Autopilot editorial (Fase 8B).

Verifican que el motor de core.autopilot (8A) queda expuesto de forma fiable
vía Flask sin cambiar su comportamiento:
- endpoints START/GET/LIST/CANCEL/RETRY
- BookJobStore como fuente de verdad (no hay mock data)
- worker único por proceso (anti-duplicación) y recovery al arranque
- eventos SSE reales del autopilot
- /api/stats con datos reales derivados del job

Aíslan el filesystem (BookJobStore sobre temporal) y la BD (SPACE_LAIR_DB_PATH).
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

import frontend.frontend_api as frontend_api
from core import autopilot, events
from core.database import init_db
from frontend.frontend_api import create_app

AUTOPILOT_PHASES = autopilot.AUTOPILOT_PHASES
JOB_PENDING = autopilot.JOB_PENDING
JOB_RUNNING = autopilot.JOB_RUNNING
JOB_FAILED = autopilot.JOB_FAILED
JOB_COMPLETED = autopilot.JOB_COMPLETED
JOB_CANCELLED = autopilot.JOB_CANCELLED
PHASE_FAIL = autopilot.PHASE_FAIL
PHASE_PENDING = autopilot.PHASE_PENDING
PHASE_RUNNING = autopilot.PHASE_RUNNING


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
    """BookJobStore aislado en un directorio temporal (no toca data/)."""
    return autopilot.BookJobStore(os.path.join(str(tmp_path), "jobs"))


@pytest.fixture
def client(monkeypatch, ap_store):
    """Cliente Flask que usa el store temporal y NO arranca el worker real.

    El worker (único por proceso) y su recovery se verifican por separado en
    los tests dedicados, aislados con monkeypatch.
    """
    monkeypatch.setattr(frontend_api, "get_autopilot_store", lambda: ap_store)
    monkeypatch.setattr(frontend_api, "ensure_autopilot_worker_started", lambda: None)
    monkeypatch.setattr(frontend_api, "_autopilot_worker_started", False)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _create_book(client, title="Libro Autopilot"):
    return client.post(
        "/api/books",
        data=json.dumps({
            "title": title,
            "language": "es",
            "target_chapters": 2,
            "target_words": 1500,
        }),
        content_type="application/json",
    )


# ============================================
# 1. POST start crea job
# ============================================
def test_start_creates_job(client, ap_store):
    book = _create_book(client).get_json()
    resp = client.post(f"/api/books/{book['book_id']}/autopilot/start")
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["job_id"] == f"book_{book['book_id']}"
    assert data["book_id"] == book["book_id"]
    assert data["status"] == JOB_PENDING
    assert data["current_phase"] == "planner"
    assert len(data["phases"]) == len(AUTOPILOT_PHASES)
    assert ap_store.exists(f"book_{book['book_id']}")
    # Agregados derivados de datos reales, no inventados
    assert data["attempts"] == 0
    assert data["duration"] == 0.0


def test_start_missing_book_404(client):
    resp = client.post("/api/books/99999/autopilot/start")
    assert resp.status_code == 404


# ============================================
# 2. start dos veces NO crea dos jobs activos
# ============================================
def test_start_twice_no_duplicate_jobs(client, ap_store):
    book = _create_book(client).get_json()
    r1 = client.post(f"/api/books/{book['book_id']}/autopilot/start")
    r2 = client.post(f"/api/books/{book['book_id']}/autopilot/start")
    assert r1.status_code == 201
    assert r2.status_code == 200  # reutiliza el existente, no crea otro
    assert r1.get_json()["job_id"] == r2.get_json()["job_id"]
    assert len(ap_store.list_all()) == 1
    assert r2.get_json()["status"] == JOB_PENDING
# ============================================
# 3. GET devuelve el job real
# ============================================
def test_get_returns_real_job(client, ap_store):
    book = _create_book(client).get_json()
    client.post(f"/api/books/{book['book_id']}/autopilot/start")
    resp = client.get(f"/api/books/{book['book_id']}/autopilot")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["book_id"] == book["book_id"]
    assert data["status"] == JOB_PENDING
    assert "created_at" in data
    assert "updated_at" in data
    assert "current_phase" in data
    # Coincide con lo persistido en disco
    persisted = ap_store.load(f"book_{book['book_id']}")
    assert data["updated_at"] == persisted["updated_at"]


# ============================================
# 4. GET /api/autopilot lista jobs
# ============================================
def test_list_jobs(client, ap_store):
    b1 = _create_book(client, "Libro A").get_json()
    b2 = _create_book(client, "Libro B").get_json()
    client.post(f"/api/books/{b1['book_id']}/autopilot/start")
    client.post(f"/api/books/{b2['book_id']}/autopilot/start")
    resp = client.get("/api/autopilot")
    assert resp.status_code == 200
    jobs = resp.get_json()
    assert len(jobs) == 2
    ids = {j["job_id"] for j in jobs}
    assert ids == {f"book_{b1['book_id']}", f"book_{b2['book_id']}"}


# ============================================
# 5. cancel cambia correctamente el estado
# ============================================
def test_cancel_changes_state_correctly(client, ap_store):
    book = _create_book(client).get_json()
    client.post(f"/api/books/{book['book_id']}/autopilot/start")
    resp = client.post(f"/api/books/{book['book_id']}/autopilot/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == JOB_CANCELLED
    # Persistido de verdad en el store
    assert ap_store.load(f"book_{book['book_id']}")["status"] == JOB_CANCELLED


def test_cancel_does_not_convert_terminal(client, ap_store):
    book = _create_book(client).get_json()
    job = autopilot.create_job(ap_store, book["book_id"])
    job["status"] = JOB_COMPLETED
    ap_store.save(job)
    resp = client.post(f"/api/books/{book['book_id']}/autopilot/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == JOB_COMPLETED  # no se convierte


def test_cancel_missing_job_404(client):
    book = _create_book(client).get_json()
    resp = client.post(f"/api/books/{book['book_id']}/autopilot/cancel")
    assert resp.status_code == 404
# ============================================
# 6. retry respeta las transiciones del motor
# ============================================
def test_retry_respects_motor_transitions(client, ap_store):
    book = _create_book(client).get_json()
    bid = book["book_id"]
    jid = f"book_{bid}"
    job = autopilot.create_job(ap_store, bid)
    job["status"] = JOB_FAILED
    for ph in job["phases"]:
        if ph["id"] == "research":
            ph["status"] = PHASE_FAIL
            ph["attempts"] = 2
            ph["error"] = "fallo real"
    ap_store.save(job)

    resp = client.post(f"/api/books/{bid}/autopilot/retry")
    assert resp.status_code == 200
    out = resp.get_json()
    assert out["status"] == JOB_PENDING
    research = next(p for p in out["phases"] if p["id"] == "research")
    assert research["status"] == PHASE_PENDING
    assert research["attempts"] == 0
    # Persistido en el store
    assert ap_store.load(jid)["status"] == JOB_PENDING

    # COMPLETED -> no reintentable (400)
    job = ap_store.load(jid)
    job["status"] = JOB_COMPLETED
    ap_store.save(job)
    assert client.post(f"/api/books/{bid}/autopilot/retry").status_code == 400

    # RUNNING -> ya activo (400)
    job = ap_store.load(jid)
    job["status"] = JOB_RUNNING
    ap_store.save(job)
    assert client.post(f"/api/books/{bid}/autopilot/retry").status_code == 400


def test_retry_missing_job_404(client):
    book = _create_book(client).get_json()
    resp = client.post(f"/api/books/{book['book_id']}/autopilot/retry")
    assert resp.status_code == 404


# ============================================
# 7. worker se inicia una sola vez
# ============================================
def test_worker_starts_once(monkeypatch, ap_store):
    calls = []

    def _fake_daemon(store, executor, **kwargs):
        calls.append(store)
        return object()

    monkeypatch.setattr(autopilot, "start_worker_daemon", _fake_daemon)
    monkeypatch.setattr(frontend_api, "get_autopilot_store", lambda: ap_store)
    monkeypatch.setattr(frontend_api, "get_autopilot_executor", lambda: object())
    monkeypatch.setattr(frontend_api, "_autopilot_worker_started", False)

    frontend_api.ensure_autopilot_worker_started()
    frontend_api.ensure_autopilot_worker_started()
    frontend_api.ensure_autopilot_worker_started()
    assert len(calls) == 1


# ============================================
# 8. create_app() repetido no crea workers duplicados
# ============================================
def test_create_app_no_duplicate_workers(monkeypatch, ap_store):
    counter = {"n": 0}

    def _fake_daemon(store, executor, **kwargs):
        counter["n"] += 1
        return object()

    monkeypatch.setattr(autopilot, "start_worker_daemon", _fake_daemon)
    monkeypatch.setattr(frontend_api, "get_autopilot_store", lambda: ap_store)
    monkeypatch.setattr(frontend_api, "get_autopilot_executor", lambda: object())
    monkeypatch.setattr(frontend_api, "_autopilot_worker_started", False)

    create_app()
    create_app()
    create_app()
    assert counter["n"] == 1
# ============================================
# 9. recovery se ejecuta al arrancar el worker
# ============================================
def test_recovery_runs_on_worker_start(monkeypatch, ap_store):
    job = autopilot.create_job(ap_store, 500)
    job["status"] = JOB_RUNNING
    for ph in job["phases"]:
        if ph["id"] == "writer":
            ph["status"] = PHASE_RUNNING
    ap_store.save(job)

    monkeypatch.setattr(autopilot, "start_worker_daemon", lambda store, executor, **k: object())
    monkeypatch.setattr(frontend_api, "get_autopilot_store", lambda: ap_store)
    monkeypatch.setattr(frontend_api, "get_autopilot_executor", lambda: object())
    monkeypatch.setattr(frontend_api, "_autopilot_worker_started", False)

    frontend_api.ensure_autopilot_worker_started()

    recovered = ap_store.load(job["job_id"])
    writer = next(p for p in recovered["phases"] if p["id"] == "writer")
    assert writer["status"] == PHASE_PENDING  # la fase RUNNING se recuperó


# ============================================
# 10. SSE conserva los eventos autopilot reales
# ============================================
def test_sse_conserva_eventos_autopilot(monkeypatch, client):
    recorded = []
    monkeypatch.setattr(frontend_api, "_broadcast_event", lambda event, data: recorded.append((event, data)))
    events.emit("job_started", {"job_id": "book_123"})
    events.emit("phase_failed", {"job_id": "book_123", "phase": "research"})
    types = [t for t, _ in recorded]
    assert "job_started" in types
    assert "phase_failed" in types


# ============================================
# 11. /api/stats devuelve los campos nuevos (real)
# ============================================
def test_stats_includes_real_autopilot_fields(client, ap_store):
    book = _create_book(client).get_json()
    client.post(f"/api/books/{book['book_id']}/autopilot/start")
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["current_book"]["id"] == book["book_id"]
    assert data["current_stats"]["job_status"] == JOB_PENDING
    assert data["current_stats"]["phases_total"] == len(AUTOPILOT_PHASES)
    assert len(data["pipeline"]) == len(AUTOPILOT_PHASES)
    assert all(p["capability"] for p in data["pipeline"])
    assert data["current_stats"]["current_phase"] == "planner"


def test_stats_empty_when_no_job(client, ap_store):
    _create_book(client)
    resp = client.get("/api/stats")
    data = resp.get_json()
    assert data["current_book"] is None
    assert data["current_stats"] == {}
    assert data["pipeline"] == []
    assert data["current_chapters"] == []


# ============================================
# 12. ningún endpoint usa mock data
# ============================================
def test_no_fabricated_data_when_no_job(client, ap_store):
    # Libro sin job -> GET 404, no un job inventado
    book = _create_book(client).get_json()
    resp = client.get(f"/api/books/{book['book_id']}/autopilot")
    assert resp.status_code == 404
    assert "error" in resp.get_json()

    # Lista vacía -> [] real, sin datos ficticios
    assert client.get("/api/autopilot").get_json() == []
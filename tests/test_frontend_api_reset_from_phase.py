"""Tests de integración Flask del endpoint de reset §17 #36 Fase 4.

POST /api/books/<id>/autopilot/reset — wiring de core.autopilot.reset_from_phase:
- 200 con el job actualizado (persistido vía store.save) en reset válido.
- 404 si no existe job para el libro.
- 400 con el motivo real (str del ValueError) ante origen inválido,
  chapter_number sobre fase de origen global o job ya activo.

Aísla el filesystem (BookJobStore sobre temporal) y la BD (SPACE_LAIR_DB_PATH).
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

import frontend.frontend_api as frontend_api
from core import autopilot
from core.database import init_db
from frontend.frontend_api import create_app

PHASE_PENDING = autopilot.PHASE_PENDING
PHASE_PASS = autopilot.PHASE_PASS


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
    return autopilot.BookJobStore(os.path.join(str(tmp_path), "jobs"))


@pytest.fixture
def client(monkeypatch, ap_store):
    monkeypatch.setattr(frontend_api, "get_autopilot_store", lambda: ap_store)
    monkeypatch.setattr(frontend_api, "ensure_autopilot_worker_started", lambda: None)
    monkeypatch.setattr(frontend_api, "_autopilot_worker_started", False)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _job_failed_at_quality_gate(ap_store, book_id=1) -> dict:
    """Job FAILED con fases previas PASS (subs con 3 capítulos) para reset."""
    job = {
        "job_id": f"book_{book_id}",
        "book_id": book_id,
        "status": autopilot.JOB_FAILED,
        "current_phase": "quality_gate",
        "error": "quality_gate#overall_status=FAIL",
        "phases": [],
        "created_at": "2026-08-28T10:00:00",
        "updated_at": "2026-08-28T10:00:00",
    }
    pass_phases = ("planner", "research", "outline", "writer", "fact_check",
                   "editor", "image_plan", "image_gen")
    for phase_def in autopilot.AUTOPILOT_PHASES:
        pid = phase_def["id"]
        ph = {"id": pid, "status": PHASE_PENDING, "attempts": 0,
              "started_at": None, "completed_at": None, "duration": None,
              "error": None, "metrics": {}}
        if pid == "quality_gate":
            ph["status"] = autopilot.PHASE_FAIL
            ph["attempts"] = 1
            ph["error"] = "quality_gate#overall_status=FAIL"
        elif pid in pass_phases:
            ph["status"] = PHASE_PASS
            ph["attempts"] = 1
        if pid in autopilot.PER_CHAPTER_PHASES:
            ph["subs"] = {
                "done": 3,
                "total": 3,
                "chapters": {
                    str(cid): {"status": PHASE_PASS, "attempts": 1, "error": None}
                    for cid in (101, 102, 103)
                },
            }
        job["phases"].append(ph)
    ap_store.save(job)
    return job


def _reset(client, book_id, payload=None):
    return client.post(
        f"/api/books/{book_id}/autopilot/reset",
        data=json.dumps(payload or {}),
        content_type="application/json",
    )


def test_reset_endpoint_success_full_phase(client, ap_store):
    _job_failed_at_quality_gate(ap_store)
    resp = _reset(client, 1, {"from_phase": "writer"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == autopilot.JOB_PENDING
    by_id = {p["id"]: p for p in data["phases"]}
    for pid in ("writer", "fact_check", "editor", "image_plan", "image_gen",
                "quality_gate", "docx"):
        assert by_id[pid]["status"] == PHASE_PENDING, pid
    for pid in ("planner", "research", "outline"):
        assert by_id[pid]["status"] == PHASE_PASS, pid
    # Persistencia real en el store (reset_from_phase no persiste solo).
    stored = ap_store.load("book_1")
    assert stored["status"] == autopilot.JOB_PENDING
    assert {p["id"]: p for p in stored["phases"]}["writer"]["status"] == PHASE_PENDING


def test_reset_endpoint_success_single_chapter(client, ap_store):
    _job_failed_at_quality_gate(ap_store)
    resp = _reset(client, 1, {"from_phase": "image_gen", "chapter_number": 102})
    assert resp.status_code == 200
    by_id = {p["id"]: p for p in resp.get_json()["phases"]}
    # Fases globales de la cascada: reset completo.
    assert by_id["quality_gate"]["status"] == PHASE_PENDING
    assert by_id["docx"]["status"] == PHASE_PENDING
    # Solo el capítulo 102 vuelve a PENDING en image_gen.
    subs = by_id["image_gen"]["subs"]["chapters"]
    assert subs["101"]["status"] == PHASE_PASS
    assert subs["102"]["status"] == PHASE_PENDING
    assert subs["103"]["status"] == PHASE_PASS


def test_reset_endpoint_invalid_phase_returns_400(client, ap_store):
    _job_failed_at_quality_gate(ap_store)
    resp = _reset(client, 1, {"from_phase": "foo"})
    assert resp.status_code == 400
    assert "foo" in (resp.get_json().get("error") or "")


def test_reset_endpoint_chapter_number_on_global_origin_returns_400(
    client, ap_store
):
    _job_failed_at_quality_gate(ap_store)
    resp = _reset(client, 1, {"from_phase": "research", "chapter_number": 1})
    assert resp.status_code == 400


def test_reset_endpoint_running_job_returns_400(client, ap_store):
    job = _job_failed_at_quality_gate(ap_store)
    job["status"] = autopilot.JOB_RUNNING
    ap_store.save(job)
    resp = _reset(client, 1, {"from_phase": "writer"})
    assert resp.status_code == 400


def test_reset_endpoint_book_not_found_returns_404(client, ap_store):
    resp = _reset(client, 999, {"from_phase": "writer"})
    assert resp.status_code == 404

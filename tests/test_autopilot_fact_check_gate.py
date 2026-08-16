"""Fix 8I.2 — la fase fact_check solo debe fallar por su GATE (quality_gate), no por "status".

modules/fact_checker distingue explícitamente:
- "status": hallazgo de claims (informativo, NO gate) — un claim de severidad ERROR
  fuerza status="FAIL", pero NO eleva quality_gate.
- "quality_gate": integridad del proceso de verificación (el gate real).

autopilot._run_single antes hacía `if st == "FAIL" or qg == "FAIL"`, tratando
`status=FAIL` (informativo) como gate y abortando fases válidas (confirmado en
books 9, 18, 19, 23, 25). Ahora fact_check solo aborta cuando `quality_gate == "FAIL"`.

Se ejercita la orquestación REAL (run_job -> default_executor_factory ->
_run_single -> scheduler._process_task -> módulo falso) para cubrir el bloque de
traducción gate_fail de fact_check, igual que test_autopilot_document_output.py
cubre el de DOCX.

Casos:
- fact_check status=FAIL, quality_gate=PASS -> la fase NO produce gate_fail
  (job sigue adelante: la fuente es válida, el gate real es PASS).
- fact_check status=FAIL, quality_gate=FAIL -> SÍ produce gate_fail (legítimo,
  como book 17).
"""
from __future__ import annotations

import os

import pytest

from core import autopilot
from core.database import init_db
from frontend.editorial import create_book, persist_chapter_result

_META = {
    "title": "Libro 8.2 fact_check gate",
    "author": "Space Lair",
    "description": "Verificación del gate de fact_check",
    "genre": "Divulgación",
    "target_audience": "General",
    "language": "es",
}

_NOSLEEP = lambda _s: None  # noqa: E731 (evita esperas reales en tests)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t8e2.db"))
    init_db()


@pytest.fixture
def store(tmp_path):
    return autopilot.BookJobStore(os.path.join(str(tmp_path), "jobs"))


def _make_book_with_draft(target_chapters: int = 1) -> int:
    """Crea un libro real y persiste texto en su capítulo para que el payload
    per-capítulo de fact_check no falle (requiere chapter_text no vacío)."""
    d = dict(_META)
    d["target_chapters"] = target_chapters
    book_id = create_book(d)["book_id"]
    from frontend.editorial import _get_chapters
    cid = _get_chapters(book_id)[0]["id"]
    persist_chapter_result(book_id, cid, "draft_es", "Capítulo con texto suficiente para verificar.")
    return book_id


def _executor_for(fc_execute):
    """Executor de producción real (default_executor_factory) con un módulo falso
    de Fact Check cuyo ``execute`` devuelve el dict con status/quality_gate dado."""
    modules = {
        "fact_check": {
            "manifest": {"id": "fact_check", "config": {"timeout_seconds": 60}},
            "execute": fc_execute,
        }
    }
    cap_map = {"fact_check_chapter": ["fact_check"]}
    return autopilot.default_executor_factory(modules, cap_map)


def _job_ready_at_fact_check(store, book_id):
    """Job con todas las fases anteriores PASS y solo fact_check pendiente."""
    job = autopilot.create_job(store, book_id)
    for ph in job["phases"]:
        if ph["id"] == "fact_check":
            ph["status"] = autopilot.PHASE_PENDING
            ph["attempts"] = 0
        else:
            ph["status"] = autopilot.PHASE_PASS
            ph["attempts"] = 1
    store.save(job)
    return store.load(job["job_id"])


def _run(store, book_id, executor):
    job = _job_ready_at_fact_check(store, book_id)
    collected: list = []
    emit = lambda ev, d: collected.append((ev, d))  # noqa: E731
    final = autopilot.run_job(
        job, store, executor, emit=emit, max_attempts=2, sleep_fn=_NOSLEEP
    )
    return final, collected


def _ev_types(collected):
    return [ev for ev, _ in collected]


def _fc_result(status: str, quality_gate: str) -> dict:
    return {"status": status, "quality_gate": quality_gate, "claims_checked": 1}


# ---------------------------------------------------------------------------
# CASO 1 — status=FAIL, quality_gate=PASS: NO debe producir gate_fail.
# Gusto de la falla 8.2E.2: status es informativo; el gate real (PASS) manda.
# ---------------------------------------------------------------------------
def test_fact_check_status_fail_gate_pass_does_not_fail_phase(store):
    executor = _executor_for(lambda payload: _fc_result("FAIL", "PASS"))
    final, collected = _run(store, _make_book_with_draft(), executor)

    fc_phase = next(p for p in final["phases"] if p["id"] == "fact_check")
    assert fc_phase["status"] == autopilot.PHASE_PASS
    assert fc_phase["error"] is None
    assert "job_failed" not in _ev_types(collected)


# ---------------------------------------------------------------------------
# CASO 2 — status=FAIL, quality_gate=FAIL: SÍ debe dispara gate_fail (legítimo)
# ---------------------------------------------------------------------------
def test_fact_check_status_fail_gate_fail_fails_phase(store):
    book = _make_book_with_draft()
    executor = _executor_for(lambda payload: _fc_result("FAIL", "FAIL"))
    final, collected = _run(store, book, executor)

    fc_phase = next(p for p in final["phases"] if p["id"] == "fact_check")
    assert fc_phase["status"] == autopilot.PHASE_FAIL
    # El error debe reflejar el gate=FAIL real (trazabilidad de st + qg).
    assert "quality_gate=FAIL" in (fc_phase.get("error") or "")
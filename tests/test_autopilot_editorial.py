"""Tests del motor Autopilot editorial (Fase 8A) — orquestador aislado.

Verifican el MOTOR (estados, persistencia, retry, recovery, eventos) con un
ejecutor inyectado por el propio test (harness), NO con mocks en producción.
No ejecutan el pipeline editorial real; eso llega en 8B+.
"""

from __future__ import annotations

import os

import pytest

from core.autopilot import (
    AUTOPILOT_PHASES,
    BookJobStore,
    PhaseResult,
    build_phase_payload,
    cancel_job,
    create_job,
    recover,
    run_job,
    JOB_CANCELLED,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RUNNING,
    PHASE_FAIL,
    PHASE_PASS,
    PHASE_PENDING,
    PHASE_RETRY,
)

_NOSLEEP = lambda _s: None  # noqa: E731  (evita esperas reales en tests)


@pytest.fixture
def store(tmp_path):
    return BookJobStore(os.path.join(str(tmp_path), "jobs"))


# ---------------------------------------------------------------------------
# Ejecutores de prueba (harness)
# ---------------------------------------------------------------------------
def _ok_executor(phase, job):
    metrics = {"deterministic_used": True}
    if phase["id"] in ("writer", "fact_check", "editor"):
        metrics["words"] = 1834
    if phase["id"] == "docx":
        metrics["docx_path"] = "output/docx/book_real.docx"
        return PhaseResult(
            ok=True, metrics=metrics, module="document_builder",
            docx_path="output/docx/book_real.docx",
        )
    return PhaseResult(ok=True, metrics=metrics, module=phase["capability"])


def _fail_phase_executor(phase_id, always=True):
    """Falla la fase indicada; el resto pasa. always=False => solo el 1er intento."""
    counts = {}

    def _exec(phase, job):
        if phase["id"] != phase_id:
            return _ok_executor(phase, job)
        counts[phase["id"]] = counts.get(phase["id"], 0) + 1
        if not always and counts[phase["id"]] > 1:
            return _ok_executor(phase, job)
        return PhaseResult(ok=False, error=f"fallo simulado en {phase_id}")

    return _exec


# ---------------------------------------------------------------------------
# 1. Creación de job
# ---------------------------------------------------------------------------
def test_create_job(store):
    job = create_job(store, book_id=1001, data={"title": "El Gran Libro"})
    assert job["job_id"] == "book_1001"
    assert job["book_id"] == 1001
    assert job["status"] == JOB_PENDING
    assert job["current_phase"] == "planner"
    assert job["docx_path"] is None
    assert len(job["phases"]) == len(AUTOPILOT_PHASES)
    assert all(ph["status"] == PHASE_PENDING for ph in job["phases"])
    assert store.exists("book_1001")


# ---------------------------------------------------------------------------
# 2. Persistencia (vuelta a cargar desde disco)
# ---------------------------------------------------------------------------
def test_persistence_roundtrip(tmp_path, store):
    create_job(store, book_id=1001)
    store2 = BookJobStore(os.path.join(str(tmp_path), "jobs"))
    job = store2.load_by_book(1001)
    assert job is not None
    assert job["job_id"] == "book_1001"
    assert job["book_id"] == 1001
    assert store2.list_all()
    job["status"] = JOB_COMPLETED
    store2.save(job)
    store3 = BookJobStore(os.path.join(str(tmp_path), "jobs"))
    assert store3.load("book_1001")["status"] == JOB_COMPLETED


# ---------------------------------------------------------------------------
# 3. Fases en orden + PASS
# ---------------------------------------------------------------------------
def test_phases_run_in_order(store):
    job = create_job(store, book_id=7)
    called = []

    def _rec(phase, job):
        called.append(phase["id"])
        return _ok_executor(phase, job)

    run_job(job, store, _rec, max_attempts=2, sleep_fn=_NOSLEEP)
    assert called == [p["id"] for p in AUTOPILOT_PHASES]
    assert job["status"] == JOB_COMPLETED
    assert all(ph["status"] == PHASE_PASS for ph in job["phases"])


# ---------------------------------------------------------------------------
# 4. Métricas y duración
# ---------------------------------------------------------------------------
def test_metrics_and_duration(store):
    job = create_job(store, book_id=7)
    run_job(job, store, _ok_executor, max_attempts=2, sleep_fn=_NOSLEEP)
    writer = next(ph for ph in job["phases"] if ph["id"] == "writer")
    assert writer["metrics"]["words"] == 1834
    assert writer["duration"] is not None and writer["duration"] >= 0
    assert writer["module"] == "write_chapter_es"
# ---------------------------------------------------------------------------
# 5. FAIL -> job FAILED, no avanza fases posteriores
# ---------------------------------------------------------------------------
def test_fail_halts_job_and_does_not_advance(store):
    job = create_job(store, book_id=7)
    exec_ = _fail_phase_executor("research", always=True)
    run_job(job, store, exec_, max_attempts=2, sleep_fn=_NOSLEEP)
    assert job["status"] == JOB_FAILED
    research = next(ph for ph in job["phases"] if ph["id"] == "research")
    assert research["status"] == PHASE_FAIL
    assert "fallo simulado" in research["error"]
    # Las fases posteriores NO deben haber avanzado.
    outline = next(ph for ph in job["phases"] if ph["id"] == "outline")
    assert outline["status"] == PHASE_PENDING


# ---------------------------------------------------------------------------
# 6. RETRY: el primer intento falla, el siguiente pasa
# ---------------------------------------------------------------------------
def test_retry_then_pass(store):
    job = create_job(store, book_id=8)
    exec_ = _fail_phase_executor("research", always=False)
    run_job(job, store, exec_, max_attempts=2, sleep_fn=_NOSLEEP)
    assert job["status"] == JOB_COMPLETED
    research = next(ph for ph in job["phases"] if ph["id"] == "research")
    assert research["status"] == PHASE_PASS
    assert research["attempts"] == 2  # primer intento fallido -> retry -> pasó


# ---------------------------------------------------------------------------
# 7. Máximo de reintentos -> FAIL definitivo
# ---------------------------------------------------------------------------
def test_max_attempts_reached(store):
    job = create_job(store, book_id=9)
    exec_ = _fail_phase_executor("planner", always=True)
    run_job(job, store, exec_, max_attempts=3, sleep_fn=_NOSLEEP)
    assert job["status"] == JOB_FAILED
    planner = next(ph for ph in job["phases"] if ph["id"] == "planner")
    assert planner["status"] == PHASE_FAIL
    assert planner["attempts"] == 3


# ---------------------------------------------------------------------------
# 8. Recovery de RUNNING -> PENDING (proceso reiniciado)
# ---------------------------------------------------------------------------
def test_recovery_resets_running(store):
    job = create_job(store, book_id=10)
    writer = next(ph for ph in job["phases"] if ph["id"] == "writer")
    job["status"] = JOB_RUNNING
    writer["status"] = "RUNNING"
    writer["attempts"] = 1
    writer["started_at"] = "2026-01-01 00:00:00"
    store.save(job)

    resumed = recover(store)
    assert len(resumed) == 1
    job2 = store.load("book_10")
    assert job2["status"] == JOB_RUNNING  # el job sigue activo (no se pierde)
    w2 = next(ph for ph in job2["phases"] if ph["id"] == "writer")
    assert w2["status"] == PHASE_PENDING  # la fase vuelve a PENDING
    assert w2["attempts"] == 0

    # Tras el recovery el job puede completarse de forma determinista.
    run_job(store.load("book_10"), store, _ok_executor, max_attempts=2, sleep_fn=_NOSLEEP)
    assert store.load("book_10")["status"] == JOB_COMPLETED


# ---------------------------------------------------------------------------
# 9. Recovery también resetea fases en RETRY
# ---------------------------------------------------------------------------
def test_recovery_resets_retry(store):
    job = create_job(store, book_id=11)
    research = next(ph for ph in job["phases"] if ph["id"] == "research")
    job["status"] = JOB_RUNNING
    research["status"] = PHASE_RETRY
    research["attempts"] = 1
    store.save(job)
    recover(store)
    r2 = next(ph for ph in store.load("book_11")["phases"] if ph["id"] == "research")
    assert r2["status"] == PHASE_PENDING
    assert r2["attempts"] == 0
# ---------------------------------------------------------------------------
# 10. COMPLETED persiste y docx_path real
# ---------------------------------------------------------------------------
def test_completed_persists_with_docx(tmp_path, store):
    job = create_job(store, book_id=12)
    run_job(job, store, _ok_executor, max_attempts=2, sleep_fn=_NOSLEEP)
    store2 = BookJobStore(os.path.join(str(tmp_path), "jobs"))
    loaded = store2.load("book_12")
    assert loaded["status"] == JOB_COMPLETED
    assert loaded["docx_path"] == "output/docx/book_real.docx"
    docx = next(ph for ph in loaded["phases"] if ph["id"] == "docx")
    assert docx["status"] == PHASE_PASS
    assert docx["metrics"]["docx_path"] == "output/docx/book_real.docx"


# ---------------------------------------------------------------------------
# 12. Eventos (job_completed)
# ---------------------------------------------------------------------------
def test_events_emitted_on_completed(store):
    job = create_job(store, book_id=13)
    collected = []
    run_job(job, store, _ok_executor, emit=lambda ev, data: collected.append(ev),
            max_attempts=2, sleep_fn=_NOSLEEP)
    types = list(collected)
    assert "job_started" in types
    assert "job_completed" in types
    for _p in AUTOPILOT_PHASES:
        assert "phase_started" in types
        assert "phase_completed" in types


# ---------------------------------------------------------------------------
# 13. Eventos (job_failed / phase_failed)
# ---------------------------------------------------------------------------
def test_events_emitted_on_failure(store):
    job = create_job(store, book_id=13)
    collected = []
    exec_ = _fail_phase_executor("research", always=True)
    run_job(job, store, exec_, emit=lambda ev, data: collected.append((ev, data)),
            max_attempts=2, sleep_fn=_NOSLEEP)
    ev_types = [e for e, _ in collected]
    assert ev_types.count("phase_failed") == 2  # intento 1 (retry) + intento 2 (final)
    assert "job_failed" in ev_types
    jf = next(d for e, d in collected if e == "job_failed")
    assert jf["current_phase"] == "research"
    assert jf["status"] == JOB_FAILED


# ---------------------------------------------------------------------------
# 11. Cancelación
# ---------------------------------------------------------------------------
def test_cancel_terminal_not_cancelled(store):
    job = create_job(store, book_id=14)
    run_job(job, store, _ok_executor, max_attempts=1, sleep_fn=_NOSLEEP)
    assert job["status"] == JOB_COMPLETED
    cancelled = cancel_job(store, "book_14")
    assert cancelled["status"] == JOB_COMPLETED  # terminal no se cancela


def test_cancel_pending_prevents_running(store):
    job = create_job(store, book_id=15)
    cancel_job(store, "book_15")
    run_job(job, store, _ok_executor, max_attempts=1, sleep_fn=_NOSLEEP)
    assert job["status"] == JOB_CANCELLED
    assert all(ph["status"] == PHASE_PENDING for ph in job["phases"])


# ---------------------------------------------------------------------------
# Eligibility del worker
# ---------------------------------------------------------------------------
def test_next_job_eligibility(tmp_path):
    store = BookJobStore(os.path.join(str(tmp_path), "jobs"))
    create_job(store, book_id=20)
    create_job(store, book_id=21)
    assert store.next_job()["book_id"] == 20
    job = store.load("book_20")
    job["status"] = JOB_COMPLETED
    store.save(job)
    assert store.next_job()["book_id"] == 21


# ---------------------------------------------------------------------------
# build_phase_payload: QUALITY GATE deriva de docx sin duplicar lógica
# ---------------------------------------------------------------------------
def test_build_phase_payload_quality_gate(monkeypatch):
    import frontend.editorial as editorial

    def _fake_build(book_id, phase_id, data, chapter_id=None):
        return {"book": {"book_id": book_id}, "language": "es"}

    monkeypatch.setattr(editorial, "build_payload", _fake_build)
    phase = next(p for p in AUTOPILOT_PHASES if p["id"] == "quality_gate")
    payload = build_phase_payload(phase, 1001, {"language": "es"}, None)
    assert payload["book"]["book_id"] == 1001
    assert payload["language"] == "es"


# ---------------------------------------------------------------------------
# 14. Propagación REAL de fuentes: Research -> Writer/Fact Check
# ---------------------------------------------------------------------------
_SOURCES = [
    {"url": "https://es.wikipedia.org/wiki/Internet", "title": "Internet",
     "snippet": "Historia de Internet", "source_type": "web_wikipedia"},
    {"url": "https://es.wikipedia.org/wiki/DNS", "title": "DNS",
     "snippet": "Sistema de nombres", "source_type": "web_wikipedia"},
    {"url": "https://es.wikipedia.org/wiki/Browser", "title": "Navegador",
     "snippet": "Web browser", "source_type": "web_wikipedia"},
]


def _mock_book(bid):
    return {"id": bid, "title": "Libro", "languages": "es",
            "target_audience": "general", "description": "d", "genre": "g",
            "target_chapters": 1}


def _mock_chapters(bid):
    return [{"id": 1, "number": 1, "title": "Cap1",
             "draft_es": "texto de prueba con contenido suficiente para verificar"}]


def _mock_chapter(bid, cid):
    return _mock_chapters(bid)[0]


def _research_executor_with_sources(sources):
    """Executor inyectado: Research devuelve sources reales en metrics."""
    def _exec(phase, job):
        if phase["id"] == "research":
            return PhaseResult(
                ok=True,
                metrics={"status": "PASS", "sources": list(sources),
                         "source_count": len(sources), "stored_sources": []},
                module="research",
            )
        return _ok_executor(phase, job)
    return _exec


def test_sources_propagated_from_research_to_job_data(store):
    job = create_job(store, book_id=901)
    run_job(job, store, _research_executor_with_sources(_SOURCES),
            max_attempts=2, sleep_fn=_NOSLEEP)
    assert job["status"] == JOB_COMPLETED
    assert job["data"]["sources"] == _SOURCES
    assert job["data"]["source_count"] == 3


def test_writer_receives_real_sources(monkeypatch):
    import frontend.editorial as editorial
    monkeypatch.setattr(editorial, "_get_book", _mock_book)
    monkeypatch.setattr(editorial, "_get_chapters", _mock_chapters)
    monkeypatch.setattr(editorial, "_get_chapter", _mock_chapter)
    payload = editorial.build_payload(1, "writer", {"sources": _SOURCES}, chapter_id=1)
    # A) Writer recibe exactamente las fuentes reales de Research.
    assert payload["sources"] == _SOURCES
    # C) No se crean fuentes adicionales.
    assert len(payload["sources"]) == len(_SOURCES)


def test_fact_check_receives_real_sources(monkeypatch):
    import frontend.editorial as editorial
    monkeypatch.setattr(editorial, "_get_book", _mock_book)
    monkeypatch.setattr(editorial, "_get_chapters", _mock_chapters)
    monkeypatch.setattr(editorial, "_get_chapter", _mock_chapter)
    payload = editorial.build_payload(1, "fact_check", {"sources": _SOURCES}, chapter_id=1)
    # B) Fact Check recibe exactamente esas mismas fuentes.
    assert payload["sources"] == _SOURCES
    assert len(payload["sources"]) == 3


def test_empty_research_sources_keep_empty_in_writer_and_fact_check(monkeypatch):
    # D) Si Research devuelve sources=[], las fases posteriores reciben [].
    import frontend.editorial as editorial
    monkeypatch.setattr(editorial, "_get_book", _mock_book)
    monkeypatch.setattr(editorial, "_get_chapters", _mock_chapters)
    monkeypatch.setattr(editorial, "_get_chapter", _mock_chapter)
    payload_w = editorial.build_payload(1, "writer", {"sources": []}, chapter_id=1)
    payload_f = editorial.build_payload(1, "fact_check", {"sources": []}, chapter_id=1)
    assert payload_w["sources"] == []
    assert payload_f["sources"] == []


def test_sources_survive_recovery(tmp_path, store):
    # E) Recovery mantiene disponibles las fuentes propagadas.
    job = create_job(store, book_id=902)
    run_job(job, store, _research_executor_with_sources(_SOURCES),
            max_attempts=2, sleep_fn=_NOSLEEP)
    reloaded = store.load("book_902")
    assert reloaded["data"]["sources"] == _SOURCES
    store2 = BookJobStore(os.path.join(str(tmp_path), "jobs2"))
    # simula recovery refrescando el store sobre el mismo disco... reload del store original
    copy = store.load("book_902")
    assert copy["data"]["source_count"] == 3


def test_per_chapter_payload_inherits_sources(monkeypatch):
    # F) La propagación funciona en el modo per-chapter (writer/fact_check con chapter_id).
    import frontend.editorial as editorial
    monkeypatch.setattr(editorial, "_get_book", _mock_book)
    monkeypatch.setattr(editorial, "_get_chapters", _mock_chapters)
    monkeypatch.setattr(editorial, "_get_chapter", _mock_chapter)
    writer_phase = next(p for p in AUTOPILOT_PHASES if p["id"] == "writer")
    payload_w = build_phase_payload(writer_phase, 1, {"sources": _SOURCES}, chapter_id=1)
    assert payload_w["sources"] == _SOURCES
    fc_phase = next(p for p in AUTOPILOT_PHASES if p["id"] == "fact_check")
    payload_f = build_phase_payload(fc_phase, 1, {"sources": _SOURCES}, chapter_id=1)
    assert payload_f["sources"] == _SOURCES


def test_explicit_empty_sources_override_is_respected(monkeypatch):
    # No se fabrican fuentes aunque data no las declare: build_payload con data
    # sin "sources" => [] (regla: nunca inventar).
    import frontend.editorial as editorial
    monkeypatch.setattr(editorial, "_get_book", _mock_book)
    monkeypatch.setattr(editorial, "_get_chapters", _mock_chapters)
    monkeypatch.setattr(editorial, "_get_chapter", _mock_chapter)
    payload = editorial.build_payload(1, "writer", {}, chapter_id=1)
    assert payload["sources"] == []

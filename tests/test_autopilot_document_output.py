"""Fix 8E.4 — integridad del estado terminal COMPLETED del Autopilot.

Demo de que la fase DOCX solo puede ser PASS cuando el DOCX existe físicamente
en disco. Se ejercita la orquestación REAL:

    run_job -> default_executor_factory -> _run_single -> scheduler._process_task
        -> modulo 'document_builder' (controlado) -> PhaseResult -> estado

El ejecutor usa el mecanismo real (default_executor_factory) con un módulo falso
para ``build_book_docx``; NO se llama a una función privada de validación
inventada como única cobertura.

Casos:
- DOCX existente            -> job COMPLETED, se emite job_completed.
- ruta inexistente          -> job FAILED, NO job_completed.
- docx_path None            -> job FAILED, NO job_completed.
- ruta apunta a directorio  -> job FAILED, NO job_completed.
- excepción del Document Builder -> retry -> FAIL -> job FAILED (semántica intacta).
"""
from __future__ import annotations

import os

import pytest

from core import autopilot
from core.database import init_db
from frontend.editorial import create_book

_META = {
    "title": "Libro 8E.4",
    "author": "Space Lair",
    "description": "Descripción de integridad de DOCX",
    "genre": "Divulgación",
    "target_audience": "General",
    "language": "es",
}

_NOSLEEP = lambda _s: None  # noqa: E731  (evita esperas reales en tests)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t8e4.db"))
    init_db()


@pytest.fixture
def store(tmp_path):
    return autopilot.BookJobStore(os.path.join(str(tmp_path), "jobs"))


def _make_book(target_chapters: int = 1) -> int:
    d = dict(_META)
    d["target_chapters"] = target_chapters
    return create_book(d)["book_id"]


def _executor_for(docx_execute):
    """Executor de producción real (default_executor_factory) con un módulo falso
    de Document Builder cuyo ``execute`` devuelve/levanta según el caso."""
    modules = {
        "document_builder": {
            "manifest": {"id": "document_builder", "config": {"timeout_seconds": 30}},
            "execute": docx_execute,
        }
    }
    cap_map = {"build_book_docx": ["document_builder"]}
    return autopilot.default_executor_factory(modules, cap_map)


def _job_ready_at_docx(store, book_id):
    """Job con todas las fases anteriores PASS y solo la fase docx pendiente."""
    job = autopilot.create_job(store, book_id)
    for ph in job["phases"]:
        if ph["id"] == "docx":
            ph["status"] = autopilot.PHASE_PENDING
            ph["attempts"] = 0
        else:
            ph["status"] = autopilot.PHASE_PASS
            ph["attempts"] = 1
    store.save(job)
    return store.load(job["job_id"])


def _run(store, book_id, executor):
    """Ejecuta run_job sobre el job listo en la fase docx y captura eventos."""
    job = _job_ready_at_docx(store, book_id)
    collected: list = []
    emit = lambda ev, d: collected.append((ev, d))  # noqa: E731
    final = autopilot.run_job(
        job, store, executor, emit=emit, max_attempts=2, sleep_fn=_NOSLEEP
    )
    return final, collected


def _ev_types(collected):
    return [ev for ev, _ in collected]


# ---------------------------------------------------------------------------
# CASO A — DOCX válido en disco
# ---------------------------------------------------------------------------
def test_docx_existing_file_allows_job_completed(store, tmp_path):
    docx_path = os.path.join(str(tmp_path), "book_es.docx")
    with open(docx_path, "w", encoding="utf-8") as fh:
        fh.write("fake docx bytes")

    executor = _executor_for(lambda payload: {"docx_path": docx_path})
    final, collected = _run(store, _make_book(), executor)

    assert final["status"] == autopilot.JOB_COMPLETED
    assert "job_completed" in _ev_types(collected)
    assert final["docx_path"] == docx_path
    docx_phase = next(p for p in final["phases"] if p["id"] == "docx")
    assert docx_phase["status"] == autopilot.PHASE_PASS


# ---------------------------------------------------------------------------
# CASO B — ruta inexistente en disco
# ---------------------------------------------------------------------------
def test_docx_missing_file_prevents_completed(store, tmp_path):
    docx_path = os.path.join(str(tmp_path), "no_existe.docx")
    assert not os.path.exists(docx_path)

    executor = _executor_for(lambda payload: {"docx_path": docx_path})
    final, collected = _run(store, _make_book(), executor)

    assert final["status"] == autopilot.JOB_FAILED
    assert "job_completed" not in _ev_types(collected)
    assert "job_failed" in _ev_types(collected)
    docx_phase = next(p for p in final["phases"] if p["id"] == "docx")
    assert docx_phase["status"] == autopilot.PHASE_FAIL


# ---------------------------------------------------------------------------
# CASO C — docx_path = None
# ---------------------------------------------------------------------------
def test_docx_none_prevents_completed(store):
    executor = _executor_for(lambda payload: {"docx_path": None})
    final, collected = _run(store, _make_book(), executor)

    assert final["status"] == autopilot.JOB_FAILED
    assert "job_completed" not in _ev_types(collected)
    assert "job_failed" in _ev_types(collected)
    docx_phase = next(p for p in final["phases"] if p["id"] == "docx")
    assert docx_phase["status"] == autopilot.PHASE_FAIL
# ---------------------------------------------------------------------------
# CASO D — ruta apunta a un directorio (os.path.isfile()==False)
# ---------------------------------------------------------------------------
def test_docx_directory_is_not_valid_output(store, tmp_path):
    directory = os.path.join(str(tmp_path), "carpeta_salida")
    os.makedirs(directory, exist_ok=True)
    assert os.path.isdir(directory)

    executor = _executor_for(lambda payload: {"docx_path": directory})
    final, collected = _run(store, _make_book(), executor)

    assert final["status"] == autopilot.JOB_FAILED
    assert "job_completed" not in _ev_types(collected)
    assert "job_failed" in _ev_types(collected)
    docx_phase = next(p for p in final["phases"] if p["id"] == "docx")
    assert docx_phase["status"] == autopilot.PHASE_FAIL


# ---------------------------------------------------------------------------
# CASO E — excepción del Document Builder (semántica existente conservada)
# ---------------------------------------------------------------------------
def test_docx_exception_preserves_existing_failure_flow(store):
    def _raise(payload):
        raise RuntimeError("fallo interno del Document Builder")

    executor = _executor_for(_raise)
    final, collected = _run(store, _make_book(), executor)

    assert final["status"] == autopilot.JOB_FAILED
    assert "job_completed" not in _ev_types(collected)
    assert "job_failed" in _ev_types(collected)
    # Reintento contemplado por el mecanismo existente: RETRY -> FAIL.
    docx_phase = next(p for p in final["phases"] if p["id"] == "docx")
    assert docx_phase["status"] == autopilot.PHASE_FAIL
    assert (docx_phase.get("attempts") or 0) >= 2


# ---------------------------------------------------------------------------
# CASO F — el writer/writer_en puebla chapters.sources desde SourceManager
# ---------------------------------------------------------------------------
def _executor_with_writer(store):
    """Executor de producción real con un módulo falso de chapter_writer."""
    modules = {
        "chapter_writer": {
            "manifest": {"id": "chapter_writer", "config": {"timeout_seconds": 30}},
            "execute": lambda payload: {
                "chapter_md_path": "data/artifacts/test/chapter_1/chapter.md",
                "metadata": {
                    "text": "Capítulo de prueba escrito por el módulo falso.",
                    "words": 12,
                },
                "word_count": 12,
                "sources_used": [],
                "quality_gate": "PASS",
                "execution_mode": "real",
            },
        }
    }
    cap_map = {"write_chapter_es": ["chapter_writer"]}
    return autopilot.default_executor_factory(modules, cap_map, store=store)


def _job_ready_at_writer(store, book_id):
    """Job con todas las fases PASS salvo writer (quedan las per-chapter en su sitio)."""
    job = autopilot.create_job(store, book_id)
    for ph in job["phases"]:
        if ph["id"] == "writer":
            ph["status"] = autopilot.PHASE_PENDING
            ph["attempts"] = 0
        else:
            ph["status"] = autopilot.PHASE_PASS
            ph["attempts"] = 1
    store.save(job)
    return store.load(job["job_id"])


def test_writer_populates_chapters_sources_in_db(store):
    """Tras la fase writer, chapters.sources queda poblado con las URLs de SourceManager."""
    import json

    from core.database import get_db
    from core.book.source_manager import SourceManager
    from frontend.editorial import _get_chapters

    book_id = _make_book(1)
    cid = _get_chapters(book_id)[0]["id"]
    SourceManager.add_source(url="https://real.example/a", title="A", chapter_ids=[cid])
    SourceManager.add_source(url="https://real.example/b", title="B", chapter_ids=[cid])

    job = _job_ready_at_writer(store, book_id)
    final = autopilot.run_job(
        job, store, _executor_with_writer(store), max_attempts=2, sleep_fn=_NOSLEEP
    )

    assert final["status"] == autopilot.JOB_COMPLETED
    with get_db() as conn:
        row = conn.execute(
            "SELECT sources FROM chapters WHERE id = ?", (cid,)
        ).fetchone()
    stored = json.loads(row["sources"])
    assert stored != []
    assert set(stored) == {"https://real.example/a", "https://real.example/b"}
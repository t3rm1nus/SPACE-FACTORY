"""Tests 8E.3 — asociación real Research -> capítulos vía core.autopilot.run_job.

Verifican que las fuentes globales devueltas por Research terminan asociadas a los
capítulos reales del libro en SourceManager, y que esa asociación llega a
`_build_book_dict` -> `Chapter.sources` -> Quality Gate.

Atraviesan el código REAL de core.autopilot.run_job (el punto que almacena y ahora
asocia las fuentes), con un executor controlado SOLO para orquestar las fases sin
red/LLM. No insertan chapter_ids a mano (a diferencia de test_editorial_sources.py).
"""
from __future__ import annotations

import os

import pytest

from core import autopilot
from core.autopilot import BookJobStore, create_job, run_job
from core.book.source_manager import SourceManager
from core.database import init_db
from frontend.editorial import (
    create_book,
    persist_chapter_result,
    _get_book,
    _get_chapters,
    _build_book_dict,
)
from modules.quality_control.main import final_quality_control


SOURCES_GLOBAL = [
    {"url": "https://real.example/a", "title": "Fuente A", "source_type": "web_wikipedia"},
    {"url": "https://real.example/b", "title": "Fuente B", "source_type": "web_wikipedia"},
]


@pytest.fixture
def store(tmp_path):
    jobs_dir = os.path.join(str(tmp_path), "jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    return BookJobStore(jobs_dir)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t8e3.db"))
    init_db()


def _make_executor(sources):
    """Executor controlado: planner/research/outline pasan; research aporta fuentes.
    El resto (writer, etc.) detiene el job; la asociación ya quedó persistida en research."""
    def _exec(phase, job):
        if phase["id"] == "research":
            return autopilot.PhaseResult(
                ok=True,
                metrics={"sources": sources, "source_count": len(sources)},
                module="research",
            )
        if phase["id"] in ("planner", "outline"):
            return autopilot.PhaseResult(ok=True, metrics={}, module="test")
        return autopilot.PhaseResult(ok=False, error="stop-for-test")
    return _exec


def _run_research(store, book_id, sources):
    """Ejecuta run_job hasta completar la fase research (asociación incluida)."""
    job = create_job(store, book_id, {"idea": "test", "target_chapters": 1})
    run_job(job, store, _make_executor(sources), max_attempts=1, sleep_fn=lambda s: None)
    return job


def _chapter_id(book_id: int) -> int:
    return _get_chapters(book_id)[0]["id"]


# A) una fuente de research termina asociada al capítulo real
def test_a_research_source_associated_to_real_chapter(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    urls = [s.get("url") for s in SourceManager.get_chapter_sources(_chapter_id(b["book_id"]))]
    assert "https://real.example/a" in urls


# B) _build_book_dict devuelve esa URL dentro de chapters[0]["sources"]
def test_b_build_book_dict_includes_associated_url(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    cid = _chapter_id(b["book_id"])
    persist_chapter_result(b["book_id"], cid, "draft_es", "Capítulo con texto suficiente para materializarlo.")
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    d = _build_book_dict(_get_book(b["book_id"]), _get_chapters(b["book_id"]))
    assert "https://real.example/a" in d["chapters"][0]["sources"]


# C) multi-capítulo: la fuente global se asocia a TODOS los capítulos reales
def test_c_multichapter_research_source_associated_to_all_chapters(store):
    b = create_book({"title": "Libro", "target_chapters": 3})
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    for c in _get_chapters(b["book_id"]):
        urls = [s.get("url") for s in SourceManager.get_chapter_sources(c["id"])]
        assert "https://real.example/a" in urls
        assert "https://real.example/b" in urls


# D) Quality Gate recibe las fuentes vía Chapter.sources (no job.data.sources)
def test_d_quality_gate_receives_chapter_sources(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    cid = _chapter_id(b["book_id"])
    persist_chapter_result(b["book_id"], cid, "draft_es", "Capítulo con texto suficiente.")
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    d = _build_book_dict(_get_book(b["book_id"]), _get_chapters(b["book_id"]))
    assert d["chapters"][0]["sources"]  # llega via Chapter.sources en el book_dict
    out = final_quality_control({"book": d, "language": "es"})
    sc = out["source_checks"]
    assert sc, "source_checks no fue generado"
    # Demuestra que el QC recibe las fuentes vía Chapter.sources: el check de
    # fuentes presentes debe ser PASS. (El check "Investigación faltante" es un
    # WARNING no relacionado con fuentes y no debe convertirse en FAIL.)
    assert any(
        x["status"] == "PASS" and "Fuentes presentes" in x["message"] for x in sc
    ), sc


# E) deduplicación: la misma fuente no se duplica aunque research repita entradas
def test_e_source_not_duplicated_when_repeated(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    dup = {"url": "https://real.example/dup", "title": "Dup", "source_type": "web_wikipedia"}
    _run_research(store, b["book_id"], [dup, dict(dup)])  # entrada repetida
    urls = [s.get("url") for s in SourceManager.get_chapter_sources(_chapter_id(b["book_id"]))]
    assert urls.count("https://real.example/dup") == 1


# F) job.data.sources conserva las fuentes originales
def test_f_job_data_sources_preserved(store):
    b = create_book({"title": "Libro", "target_chapters": 1})
    _run_research(store, b["book_id"], SOURCES_GLOBAL)
    stored = store.load_by_book(b["book_id"])
    assert [s["url"] for s in stored["data"]["sources"]] == [
        "https://real.example/a", "https://real.example/b",
    ]
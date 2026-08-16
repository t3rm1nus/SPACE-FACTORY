"""FASE 8G.2 — Cobertura de orquestación REAL para _persist_chapter (editor/image_gen).

Cierra la deuda técnica P3 de §19: no existía ningún test de orquestación
autopilot que ejercitara las fases `editor` / `image_gen` de `_persist_chapter`
a través del executor real.

Se reutiliza EXACTAMENTE el patrón de
``test_writer_populates_chapters_sources_in_db``
(tests/test_autopilot_document_output.py):

    run_job -> default_executor_factory -> _run_single(_execute_per_chapter)
        -> scheduler._process_task -> module -> _persist_chapter -> BD

- `editor`: módulo STUB (no se toca modules/editor/main.py), devuelve
  edited_text/editorial_notes/changes_summary fijos. Verifica que
  `_persist_chapter` escribe el texto real en `chapters.edited_es`.
- `image_gen`: módulo REAL `modules/image_generator` (LocalImageProvider,
  offline, sin LLM), vía la capability `generate_chapter_images` (la misma del
  pipeline). Aísla rutas de imágenes a tmp. Verifica que `_persist_chapter`
  puebla `chapters.images` con rutas que existen en disco.

NO se modifica ningún archivo de producción: solo se inyectan módulos en
``default_executor_factory`` (igual que el test del writer).
"""

from __future__ import annotations

import json
import os

import pytest

from core import autopilot
from core.database import get_db, init_db
from frontend.editorial import _get_chapters, create_book, persist_chapter_result
from modules.image_generator import main as img_main

_NOSLEEP = lambda _s: None  # noqa: E731  (evita esperas reales en tests)

_META = {
    "title": "Libro 8G.2",
    "author": "Space Lair",
    "description": "Cobertura de orquestación real de editor e image_gen.",
    "genre": "Divulgación",
    "target_audience": "General",
    "language": "es",
}

_DRAFT_ES = (
    "Capítulo de prueba para cubrir la persistencia real del editor y del "
    "generador de imágenes a través del ejecutor del autopilot."
)

# Texto fijo devuelto por el stub del editor (NO se usa modules/editor/main.py).
_EDITED_TEXT = (
    "Versión editada por el stub del editor con correcciones menores de estilo "
    "y gramática, conservando íntegro el contenido original del capítulo."
)
@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t8g2.db"))
    init_db()


@pytest.fixture
def store(tmp_path):
    return autopilot.BookJobStore(os.path.join(str(tmp_path), "jobs"))


def _make_book(target_chapters: int = 1) -> int:
    d = dict(_META)
    d["target_chapters"] = target_chapters
    return create_book(d)["book_id"]


def _first_chapter_id(book_id: int) -> int:
    return _get_chapters(book_id)[0]["id"]


def _job_ready_at_phase(store, book_id: int, phase_id: str):
    """Job con todas las fases PASS salvo la indicada (patrón del test writer)."""
    job = autopilot.create_job(store, book_id)
    for ph in job["phases"]:
        if ph["id"] == phase_id:
            ph["status"] = autopilot.PHASE_PENDING
            ph["attempts"] = 0
        else:
            ph["status"] = autopilot.PHASE_PASS
            ph["attempts"] = 1
    store.save(job)
    return store.load(job["job_id"])


# ---------------------------------------------------------------------------
# editor -> _persist_chapter persiste chapters.edited_es
# ---------------------------------------------------------------------------
def _editor_stub_executor(store):
    """Executor real con módulo 'editor' STUB (sin tocar modules/editor/main.py)."""
    modules = {
        "editor": {
            "manifest": {"id": "editor", "config": {"timeout_seconds": 30}},
            "execute": lambda payload: {
                "edited_text": _EDITED_TEXT,
                "editorial_notes": ["Correcciones menores aplicadas."],
                "changes_summary": ["Se ha mejorado la redacción."],
                "execution_mode": "real",
            },
        }
    }
    cap_map = {"edit_chapter": ["editor"]}
    return autopilot.default_executor_factory(modules, cap_map, store=store)


def test_editor_populates_edited_es_in_db(store):
    """Tras la fase editor, chapters.edited_es queda con el texto REAL devuelto."""
    book_id = _make_book(1)
    cid = _first_chapter_id(book_id)

    # Requisito de build_payload (editor): el capítulo debe tener draft_es no vacío.
    persist_chapter_result(book_id, cid, "draft_es", _DRAFT_ES)

    job = _job_ready_at_phase(store, book_id, "editor")
    final = autopilot.run_job(
        job, store, _editor_stub_executor(store), max_attempts=2, sleep_fn=_NOSLEEP
    )

    assert final["status"] == autopilot.JOB_COMPLETED
    with get_db() as conn:
        row = conn.execute(
            "SELECT edited_es FROM chapters WHERE id = ?", (cid,)
        ).fetchone()
    assert row["edited_es"] == _EDITED_TEXT
# ---------------------------------------------------------------------------
# image_gen — módulo REAL (LocalImageProvider) persiste chapters.images
# ---------------------------------------------------------------------------
def _real_image_gen_executor(store):
    """Executor real con el módulo REAL image_generator (capability per-fase).

    El scheduler llama ``module['execute'](payload)`` sin pasar la capability,
    por lo que se enlaza a ``generate_chapter_images`` (la capacidad real de la
    fase) para que construya un plan determinista y genere los PNG con el
    proveedor local. NO es un stub: es la ruta real de modules/image_generator.
    """

    def _execute(payload):
        return img_main.execute(payload, capability="generate_chapter_images")

    modules = {
        "image_generator": {
            "manifest": {"id": "image_generator", "config": {"timeout_seconds": 30}},
            "execute": _execute,
        }
    }
    cap_map = {"generate_chapter_images": ["image_generator"]}
    return autopilot.default_executor_factory(modules, cap_map, store=store)


def test_image_gen_populates_images_in_db(store, tmp_path, monkeypatch):
    """Tras image_gen, chapters.images contiene rutas PNG reales en disco."""
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    book_id = _make_book(1)
    cid = _first_chapter_id(book_id)

    # Requisito de build_payload (image_gen): capítulo con texto no vacío.
    persist_chapter_result(book_id, cid, "draft_es", _DRAFT_ES)

    job = _job_ready_at_phase(store, book_id, "image_gen")
    final = autopilot.run_job(
        job, store, _real_image_gen_executor(store), max_attempts=2, sleep_fn=_NOSLEEP
    )

    assert final["status"] == autopilot.JOB_COMPLETED
    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ?", (cid,)
        ).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) >= 1
    for p in stored:
        assert os.path.isfile(p), f"la imagen persistida no existe en disco: {p}"
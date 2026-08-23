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

from core import autopilot, task_queue
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


def test_image_gen_ratio_zero_delegates_to_run_single(store, tmp_path, monkeypatch):
    """No-regresión: image_search_ratio=0.0 => _run_image_gen_split delega EXACTO a
    _run_single: una sola task (generate_chapter_images), sin encolar search_chapter_images."""
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    book_id = _make_book(1)
    cid = _first_chapter_id(book_id)

    # Requisito de build_payload (image_gen): capítulo con texto no vacío.
    persist_chapter_result(book_id, cid, "draft_es", _DRAFT_ES)

    # Campo presente con valor 0.0 (inactivo) => passthrough a _run_single.
    with get_db() as conn:
        conn.execute("UPDATE books SET image_search_ratio = 0.0 WHERE id = ?", (book_id,))

    enqueued = []
    real_enqueue = task_queue.enqueue_task

    def _capture_enqueue(cap, payload, max_attempts=1):
        enqueued.append(cap)
        return real_enqueue(cap, payload, max_attempts=max_attempts)

    monkeypatch.setattr(task_queue, "enqueue_task", _capture_enqueue)

    job = _job_ready_at_phase(store, book_id, "image_gen")
    final = autopilot.run_job(
        job, store, _real_image_gen_executor(store), max_attempts=2, sleep_fn=_NOSLEEP
    )

    assert final["status"] == autopilot.JOB_COMPLETED
    # Passthrough exacto: exactamente una task, de generación, sin search.
    assert enqueued == ["generate_chapter_images"], f"tasks encoladas: {enqueued}"
    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ?", (cid,)
        ).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) >= 1


def test_image_gen_ratio_positive_splits_into_two_tasks(store, tmp_path, monkeypatch):
    """Ratio>0: _run_image_gen_split encola 2 tasks (search + generate) y fusiona results."""
    import core.scheduler as scheduler

    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    book_id = _make_book(1)
    cid = _first_chapter_id(book_id)
    persist_chapter_result(book_id, cid, "draft_es", _DRAFT_ES)

    with get_db() as conn:
        conn.execute("UPDATE books SET image_search_ratio = 0.5 WHERE id = ?", (book_id,))

    img_dir = tmp_path / "imgs"
    img_dir.mkdir(exist_ok=True)

    def _stub(prefix):
        def _execute(payload, capability=""):
            n = int(payload.get("num_images") or 0)
            results = []
            for i in range(n):
                p = os.path.join(str(img_dir), f"{prefix}_{i}.png")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x")
                results.append({"status": "ok", "image_path": p, "image_id": f"{prefix}_{i}"})
            return {"results": results}

        return _execute

    modules = {
        "image_search": {
            "manifest": {"id": "image_search", "config": {"timeout_seconds": 30}},
            "execute": _stub("search"),
        },
        "image_generator": {
            "manifest": {"id": "image_generator", "config": {"timeout_seconds": 30}},
            "execute": _stub("gen"),
        },
    }
    cap_map = {
        "search_chapter_images": ["image_search"],
        "generate_chapter_images": ["image_generator"],
    }

    enqueued = []
    real_enqueue = task_queue.enqueue_task

    def _capture(cap, payload, max_attempts=1):
        enqueued.append(cap)
        return real_enqueue(cap, payload, max_attempts=max_attempts)

    monkeypatch.setattr(task_queue, "enqueue_task", _capture)

    job = _job_ready_at_phase(store, book_id, "image_gen")
    job["data"] = {"num_images": 4}
    store.save(job)

    executor = autopilot.default_executor_factory(modules, cap_map, store=store)
    final = autopilot.run_job(job, store, executor, max_attempts=2, sleep_fn=_NOSLEEP)

    assert final["status"] == autopilot.JOB_COMPLETED
    # Exactamente 2 tasks, una de cada capability.
    assert enqueued == ["search_chapter_images", "generate_chapter_images"], f"tasks: {enqueued}"
    # num_images=4, ratio=0.5 => n_search=2, n_generate=2 (suman 4) y se fusionan
    # los results de ambos orígenes en chapters.images.
    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ?", (cid,)
        ).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) == 4
    for p in stored:
        assert os.path.isfile(p), f"imagen no existe: {p}"
    # Ambas fuentes están representadas (search y generate).
    assert any("search_" in os.path.basename(p) for p in stored)
    assert any("gen_" in os.path.basename(p) for p in stored)


def test_image_gen_split_compensates_shortfall(store, tmp_path, monkeypatch):
    """Fix 2026-08-22: si search_chapter_images pierde imágenes (status != 'ok',
    p.ej. imagen web inválida/SVG descartada), _run_image_gen_split compensa el
    déficit con una generación local extra (una sola ronda, sin bucle).

    Setup: ratio=0.75, num_images=3 -> n_search=2 (devuelve 1 ok + 1 error) /
    n_generate=1 (devuelve 1 ok) => ok_count=2 < 3 -> shortfall=1 -> tercera
    llamada a generate_chapter_images devuelve 1 ok => 3 ok en total."""
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    book_id = _make_book(1)
    cid = _first_chapter_id(book_id)
    persist_chapter_result(book_id, cid, "draft_es", _DRAFT_ES)

    with get_db() as conn:
        conn.execute("UPDATE books SET image_search_ratio = 0.75 WHERE id = ?", (book_id,))

    img_dir = tmp_path / "imgs"
    img_dir.mkdir(exist_ok=True)

    call_counts = {"search": 0, "gen": 0}
    generated_paths: list[str] = []

    def _search(payload, capability=""):
        call_counts["search"] += 1
        results = []
        # 1 ok
        p = os.path.join(str(img_dir), f"search_ok_{call_counts['search']}.png")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        results.append({"status": "ok", "image_path": p, "image_id": f"search_ok_{call_counts['search']}"})
        # 1 error (simula imagen inválida/SVG descartada; sin archivo)
        results.append({"status": "error", "error": "cannot identify image file", "image_path": "data/x.png"})
        return {"results": results}

    def _gen(payload, capability=""):
        call_counts["gen"] += 1
        if call_counts["gen"] == 1:
            # 1ª llamada (n_generate=1): genera una imagen real y única.
            n = int(payload.get("num_images") or 0)
            results = []
            for i in range(n):
                p = os.path.join(str(img_dir), f"gen_{call_counts['gen']}_{i}.png")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x")
                results.append({"status": "ok", "image_path": p, "image_id": f"gen_{call_counts['gen']}_{i}"})
            generated_paths.extend(r["image_path"] for r in results)
            return {"results": results}
        # Llamada de compensación: reproduce el bug real (regresión libro 36):
        # devuelve la MISMA ruta que la generación inicial (como haría generate_image
        # con skip_existing=True reciclando metadata existente). El fix fuerza
        # skip_existing=False en el payload y deduplica por image_path.
        return {"results": [
            {"status": "ok", "image_path": generated_paths[0], "image_id": "gen_1_0"}
        ]}

    modules = {
        "image_search": {
            "manifest": {"id": "image_search", "config": {"timeout_seconds": 30}},
            "execute": _search,
        },
        "image_generator": {
            "manifest": {"id": "image_generator", "config": {"timeout_seconds": 30}},
            "execute": _gen,
        },
    }
    cap_map = {
        "search_chapter_images": ["image_search"],
        "generate_chapter_images": ["image_generator"],
    }

    enqueued = []
    enqueued_payloads = []
    real_enqueue = task_queue.enqueue_task

    def _capture(cap, payload, max_attempts=1):
        enqueued.append(cap)
        enqueued_payloads.append(dict(payload))
        return real_enqueue(cap, payload, max_attempts=max_attempts)

    monkeypatch.setattr(task_queue, "enqueue_task", _capture)

    job = _job_ready_at_phase(store, book_id, "image_gen")
    job["data"] = {"num_images": 3}
    store.save(job)

    executor = autopilot.default_executor_factory(modules, cap_map, store=store)
    exec_result = executor([p for p in job["phases"] if p["id"] == "image_gen"][0], job)

    # El executor de image_gen es per-chapter: metrics se agrega como subs/per_chapter.
    assert exec_result.ok
    # 3 tasks en total: search + generate + 1 de compensación (el déficit dispara
    # una generación local extra).
    assert enqueued == [
        "search_chapter_images",
        "generate_chapter_images",
        "generate_chapter_images",
    ], f"tasks encoladas: {enqueued}"

    # 1) La tarea de compensación (la última generación) lleva skip_existing=False:
    #    no recicla metadata ya existente.
    comp_payload = enqueued_payloads[-1]
    assert comp_payload["skip_existing"] is False, comp_payload
    assert comp_payload["num_images"] == 1, comp_payload

    # 2) Tras el dedup por image_path: la ruta que la compensación recicló NO queda
    #    dos veces en chapters.images. Como la generación inicial aportó 1 ruta única
    #    (gen_1_0) y la comp recicló esa misma ruta, quedan 2 imágenes ÚNICAS
    #    (search_ok + gen_1_0), sin duplicados.
    with get_db() as conn:
        row = conn.execute("SELECT images FROM chapters WHERE id = ?", (cid,)).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) == len(set(stored)) == 2, f"imágenes persistidas: {stored}"
    for p in stored:
        assert os.path.isfile(p), f"imagen no existe en disco: {p}"
def test_image_gen_split_dedupes_preexisting_duplicate_before_shortfall(store, tmp_path, monkeypatch):
    """Regresión (dedup 1a): si la fase inicial search+generate YA trae una ruta
    duplicada entre sí, el dedup por ``image_path`` lo limpia ANTES de calcular
    ok_count/shortfall, para que la misma imagen cuente como UNA y la compensación
    cubra el déficit real.

    Setup: num_images=3, ratio=0.75 -> n_search=2 / n_generate=1.
      - search devuelve 2 copias de la MISMA ruta A (duplicado entre sí).
      - generate devuelve 1 ruta B nueva.
      Sin dedup: ok_count=3 -> shortfall=0 (no compensaría a pesar de tener 2 únicas).
      Con dedup: ok_count=2 -> shortfall=1 -> compensa (ruta C) => 3 imágenes únicas.
    """
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    book_id = _make_book(1)
    cid = _first_chapter_id(book_id)
    persist_chapter_result(book_id, cid, "draft_es", _DRAFT_ES)
    with get_db() as conn:
        conn.execute("UPDATE books SET image_search_ratio = 0.75 WHERE id = ?", (book_id,))

    img_dir = tmp_path / "imgs"
    img_dir.mkdir(exist_ok=True)

    def _search(payload, capability=""):
        # 2 resultados con la MISMA ruta (duplicado entre sí, pre-existente al merge).
        p = os.path.join(str(img_dir), "dup_shared.png")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        return {"results": [
            {"status": "ok", "image_path": p, "image_id": "s0"},
            {"status": "ok", "image_path": p, "image_id": "s1"},
        ]}

    call_counts = {"gen": 0}

    def _gen(payload, capability=""):
        call_counts["gen"] += 1
        n = int(payload.get("num_images") or 0)
        results = []
        for i in range(n):
            p = os.path.join(str(img_dir), f"gen_{call_counts['gen']}_{i}.png")
            with open(p, "w", encoding="utf-8") as f:
                f.write("x")
            results.append({"status": "ok", "image_path": p, "image_id": f"g_{call_counts['gen']}_{i}"})
        return {"results": results}

    modules = {
        "image_search": {
            "manifest": {"id": "image_search", "config": {"timeout_seconds": 30}},
            "execute": _search,
        },
        "image_generator": {
            "manifest": {"id": "image_generator", "config": {"timeout_seconds": 30}},
            "execute": _gen,
        },
    }
    cap_map = {
        "search_chapter_images": ["image_search"],
        "generate_chapter_images": ["image_generator"],
    }

    enqueued = []
    real_enqueue = task_queue.enqueue_task

    def _capture(cap, payload, max_attempts=1):
        enqueued.append(cap)
        return real_enqueue(cap, payload, max_attempts=max_attempts)

    monkeypatch.setattr(task_queue, "enqueue_task", _capture)

    job = _job_ready_at_phase(store, book_id, "image_gen")
    job["data"] = {"num_images": 3}
    store.save(job)

    executor = autopilot.default_executor_factory(modules, cap_map, store=store)
    exec_result = executor([p for p in job["phases"] if p["id"] == "image_gen"][0], job)

    assert exec_result.ok
    # La compensación SÍ se dispara porque el duplicado cuenta como una sola imagen
    # (search 2x A -> 1 única; generate 1x B => ok=2 -> shortfall=1).
    assert enqueued == [
        "search_chapter_images",
        "generate_chapter_images",
        "generate_chapter_images",
    ], f"tasks encoladas: {enqueued}"

    # shortfall trató A como 1 => se compensó 1 => 3 imágenes únicas, sin duplicados.
    with get_db() as conn:
        row = conn.execute("SELECT images FROM chapters WHERE id = ?", (cid,)).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) == len(set(stored)) == 3, f"imágenes persistidas: {stored}"
    for p in stored:
        assert os.path.isfile(p), f"imagen no existe en disco: {p}"
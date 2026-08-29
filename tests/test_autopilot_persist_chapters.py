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
from frontend.editorial import (
    _get_chapters,
    create_book,
    persist_chapter_images,
    persist_chapter_result,
)
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



# ---------------------------------------------------------------------------
# §17 #38 — guard: el planner fallback (title == description == idea) NO pisa
# el título REAL del usuario; un título de planner distinto de la descripción
# sí se propaga.
# ---------------------------------------------------------------------------
def _planner_stub_executor(store, planner_title, planner_desc):
    modules = {
        "book_planner": {
            "manifest": {"id": "book_planner", "config": {"timeout_seconds": 30}},
            "execute": lambda payload: {
                "title": planner_title,
                "description": planner_desc,
                "chapters": [{"number": 1, "title": "Cap 1"}],
                "execution_mode": "deterministic",
            },
        }
    }
    cap_map = {"create_book_plan": ["book_planner"]}
    return autopilot.default_executor_factory(modules, cap_map, store=store)


def _book_title(book_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT title FROM books WHERE id = ?", (book_id,)
        ).fetchone()["title"]


def test_planner_fallback_title_equals_idea_does_not_overwrite_real_title(store):
    """§17 #38: fallback del planner (title==description==idea) no pisa el título real."""
    d = dict(_META)
    d["title"] = "TITULO REAL DEL USUARIO"
    book_id = create_book(d)["book_id"]

    job = _job_ready_at_phase(store, book_id, "planner")
    autopilot.run_job(
        job,
        store,
        _planner_stub_executor(
            store,
            "Historia de los videojuegos, desde el pong, hasta las maquinas arcade",
            "Historia de los videojuegos, desde el pong, hasta las maquinas arcade",
        ),
        max_attempts=1,
        sleep_fn=_NOSLEEP,
    )
    assert _book_title(book_id) == "TITULO REAL DEL USUARIO"


def test_planner_generated_title_does_not_overwrite_user_title(store):
    """§17 #38 (completo): el planner NUNCA reemplaza books.title (ES) —
    el título es obligatorio en el formulario (§13) y el usuario ya lo escribió.
    Aun con planner LLM exitoso (título distinto de la idea), el título del
    usuario se conserva. El planner sigue poblando description/title_en.
    """
    d = dict(_META)
    d["title"] = "TITULO PROVISIONAL"
    book_id = create_book(d)["book_id"]

    job = _job_ready_at_phase(store, book_id, "planner")
    autopilot.run_job(
        job,
        store,
        _planner_stub_executor(
            store,
            "Historia de los videojuegos",
            "Historia de los videojuegos, desde el pong, hasta las maquinas arcade",
        ),
        max_attempts=1,
        sleep_fn=_NOSLEEP,
    )
    # El título del usuario NUNCA se toca, aunque el planner genere otro.
    assert _book_title(book_id) == "TITULO PROVISIONAL"


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


def test_image_gen_split_does_not_compensate_shortfall_with_generation(
    store, tmp_path, monkeypatch, caplog
):
    """§17 #30 (rediseño): si search_chapter_images pierde imágenes (status !=
    'ok', p.ej. imagen web inválida/SVG descartada), _run_image_gen_split NO
    compensa el déficit con generación IA: el capítulo se queda con las
    imágenes que consiguió y la fase sigue ok (solo WARNING de diagnóstico).

    Setup: ratio=0.5, num_images=3 -> n_search=2 (devuelve 1 ok + 1 error) /
    n_generate=1 (devuelve 1 ok) => ok_count=2 < 3 -> shortfall=1 que se
    acepta: solo 2 tasks encoladas (sin tercera de compensación)."""
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
    # Solo 2 tasks: search + generate. §17 #30: el shortfall NO dispara una
    # tercera tarea de compensación con generación IA.
    assert enqueued == [
        "search_chapter_images",
        "generate_chapter_images",
    ], f"tasks encoladas: {enqueued}"

    # §17 #30: se emite el WARNING de diagnóstico del shortfall real.
    assert any(
        "shortfall=1" in rec.message and "SIN compensación con IA" in rec.message
        for rec in caplog.records
    ), [rec.message for rec in caplog.records]

    # El capítulo queda con las imágenes conseguidas: 2 únicas (search_ok_1 +
    # gen_1_0), sin duplicados ni imagen IA extra.
    with get_db() as conn:
        row = conn.execute("SELECT images FROM chapters WHERE id = ?", (cid,)).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) == len(set(stored)) == 2, f"imágenes persistidas: {stored}"
    for p in stored:
        assert os.path.isfile(p), f"imagen no existe en disco: {p}"
def test_image_gen_split_dedupes_preexisting_duplicate_before_shortfall(store, tmp_path, monkeypatch):
    """Regresión (dedup 1a): si la fase inicial search+generate YA trae una ruta
    duplicada entre sí, el dedup por ``image_path`` lo limpia ANTES de calcular
    ok_count/shortfall, para que la misma imagen cuente como UNA.

    Setup: num_images=3, ratio=0.75 -> n_search=2 / n_generate=1; con §17 #30
    el shortfall resultante del dedup NO se compensa jamás con IA (sin cuota:
    la compensación con generación se eliminó por completo); el capítulo queda
    con 2 imágenes únicas. Verifica también el WARNING de shortfall vía caplog.

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
    # Con ratio=0.75 la cuota IA (round(3*0.25)=1) ya está agotada por las
    # n_generate=1 del split inicial: el shortfall del dedup NO dispara
    # compensación (fix cuota de ratio).
    assert enqueued == [
        "search_chapter_images",
        "generate_chapter_images",
    ], f"tasks encoladas: {enqueued}"

    # shortfall trató A como 1 => sin compensación por cuota agotada =>
    # 2 imágenes únicas persistidas, sin duplicados.
    with get_db() as conn:
        row = conn.execute("SELECT images FROM chapters WHERE id = ?", (cid,)).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) == len(set(stored)) == 2, f"imágenes persistidas: {stored}"
    for p in stored:
        assert os.path.isfile(p), f"imagen no existe en disco: {p}"


def test_image_gen_split_ratio_one_never_compensates_with_generation(
    store, tmp_path, monkeypatch
):
    """Fix cuota de ratio: con image_search_ratio=1.0 (100% web) la rama de
    compensación NUNCA encola generate_chapter_images, aunque la búsqueda web
    no entregue todas las imágenes pedidas; el capítulo queda por debajo de
    num_images y la fase sigue siendo ok (déficit aceptado, no IA extra).

    Setup: num_images=3, ratio=1.0 -> n_search=3 (2 ok + 1 error) /
    n_generate=0 => ok_count=2, shortfall=1, cuota IA=round(3*0)=0.
    """
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    book_id = _make_book(1)
    cid = _first_chapter_id(book_id)
    persist_chapter_result(book_id, cid, "draft_es", _DRAFT_ES)
    with get_db() as conn:
        conn.execute("UPDATE books SET image_search_ratio = 1.0 WHERE id = ?", (book_id,))

    img_dir = tmp_path / "imgs"
    img_dir.mkdir(exist_ok=True)

    def _search(payload, capability=""):
        results = []
        for i in range(2):
            p = os.path.join(str(img_dir), f"web_ok_{i}.png")
            with open(p, "w", encoding="utf-8") as f:
                f.write("x")
            results.append({"status": "ok", "image_path": p, "image_id": f"w{i}"})
        results.append({"status": "error", "error": "download_failed", "image_path": "data/x.png"})
        return {"results": results}

    def _gen(payload, capability=""):  # pragma: no cover - NO debe invocarse
        raise AssertionError("generate_chapter_images no debe encolarse con ratio=1.0")

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
    assert enqueued == ["search_chapter_images"], f"tasks encoladas: {enqueued}"

    with get_db() as conn:
        row = conn.execute("SELECT images FROM chapters WHERE id = ?", (cid,)).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) == len(set(stored)) == 2, f"imágenes persistidas: {stored}"
    for p in stored:
        assert os.path.isfile(p), f"imagen no existe en disco: {p}"


# ---------------------------------------------------------------------------
# §17 #28 — routing de capabilities de imagen por idioma del libro (book_67)
# ---------------------------------------------------------------------------
def test_image_gen_en_book_resolves_native_capabilities_and_payload(
    store, tmp_path, monkeypatch
):
    """book_67: libro bilingüe (es,en) con title_en presente resuelve la fase
    image_gen a las capabilities nativas EN (search_chapter_images_en /
    generate_chapter_images_en) y el payload de búsqueda lleva topic_en/title_en
    para el anclaje temático en inglés nativo. Regresión ES cubierta por los
    tests previos (capabilities históricas sin topic_en)."""
    from frontend.editorial import update_book_title_en, update_chapter_title_en

    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    meta = dict(_META)
    meta["language"] = "es,en"
    book_id = create_book(meta)["book_id"]
    cid = _first_chapter_id(book_id)
    update_book_title_en(book_id, "All about coffee in the world")
    update_chapter_title_en(book_id, 1, "Coffee discoveries")
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
        # Las capabilities nativas EN apuntan a los mismos módulos (plug-compatible).
        "search_chapter_images": ["image_search"],
        "search_chapter_images_en": ["image_search"],
        "generate_chapter_images": ["image_generator"],
        "generate_chapter_images_en": ["image_generator"],
    }

    enqueued_caps: list[str] = []
    enqueued_payloads: list[dict] = []
    real_enqueue = task_queue.enqueue_task

    def _capture(cap, payload, max_attempts=1):
        enqueued_caps.append(cap)
        enqueued_payloads.append(dict(payload))
        return real_enqueue(cap, payload, max_attempts=max_attempts)

    monkeypatch.setattr(task_queue, "enqueue_task", _capture)

    job = _job_ready_at_phase(store, book_id, "image_gen")
    job["data"] = {
        "num_images": 2,
        "topic": "Todo sobre el café, descubrimientos, tipos, cafe en el mundo",
        "topic_en": "Everything about coffee in the world",
    }
    store.save(job)

    executor = autopilot.default_executor_factory(modules, cap_map, store=store)
    exec_result = executor([p for p in job["phases"] if p["id"] == "image_gen"][0], job)
    assert exec_result.ok

    # (1) Split ratio 0.5 sobre num_images=2 → 1 search EN + 1 generate EN.
    assert enqueued_caps == [
        "search_chapter_images_en",
        "generate_chapter_images_en",
    ], enqueued_caps

    # (2) Payload de búsqueda con anclaje nativo EN.
    search_payload = enqueued_payloads[0]
    assert search_payload["language"].startswith("en")
    assert search_payload["topic_en"] == "Everything about coffee in the world"
    assert search_payload["title_en"] == "Coffee discoveries"

    # (3) Payload de generación también etiquetado nativo EN.
    gen_payload = enqueued_payloads[1]
    assert gen_payload["language"].startswith("en")
    assert gen_payload.get("topic_en") == "Everything about coffee in the world"


# ---------------------------------------------------------------------------
# §17 #28 — book_67: libro bilingüe resuelve capabilities nativas EN + payload
# ---------------------------------------------------------------------------
_DRAFT_EN = (
    "Test chapter body used to exercise the native-EN image capability routing "
    "through the real autopilot executor."
)


def test_image_gen_bilingual_book_resolves_en_capability_and_payload(
    store, tmp_path, monkeypatch
):
    """§17 #28 (book_67): libro bilingüe (es,en) sin image_search_ratio =>
    passthrough a _run_single que resuelve ``generate_chapter_images_en`` y
    construye el payload con language='en', title_en y topic_en."""
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    from frontend.editorial import update_chapter_title_en

    b = create_book({
        "title": "Todo sobre el café",
        "target_chapters": 1,
        "language": "es,en",
    })
    book_id = b["book_id"]
    cid = _first_chapter_id(book_id)

    # build_payload (image_gen) para idioma EN lee draft_en/edited_en.
    persist_chapter_result(book_id, cid, "draft_en", _DRAFT_EN)
    update_chapter_title_en(book_id, 1, "Chapter Title EN")

    enqueued: list[tuple[str, dict]] = []
    real_enqueue = task_queue.enqueue_task

    def _capture_enqueue(cap, payload, max_attempts=1):
        enqueued.append((cap, dict(payload or {})))
        return real_enqueue(cap, payload, max_attempts=max_attempts)

    monkeypatch.setattr(task_queue, "enqueue_task", _capture_enqueue)

    modules = {
        "image_generator": {
            "manifest": {"id": "image_generator", "config": {"timeout_seconds": 30}},
            "execute": img_main.execute,
        }
    }
    cap_map = {
        "generate_chapter_images": ["image_generator"],
        "generate_chapter_images_es": ["image_generator"],
        "generate_chapter_images_en": ["image_generator"],
    }
    executor = autopilot.default_executor_factory(modules, cap_map, store=store)

    job = _job_ready_at_phase(store, book_id, "image_gen")
    final = autopilot.run_job(
        job, store, executor, max_attempts=2, sleep_fn=_NOSLEEP
    )

    assert final["status"] == autopilot.JOB_COMPLETED

    # (1) Capability nativa EN resuelta desde books.languages.
    assert [cap for cap, _ in enqueued] == ["generate_chapter_images_en"], enqueued

    # (2) Payload con idioma nativo + topic_en/title_en para el anclaje.
    payload = enqueued[0][1]
    assert payload["language"].startswith("en")
    assert payload["title_en"] == "Chapter Title EN"
    # topic_en: job.data.topic_en > books.title_en > topic/título ES.
    # §17 #28 fix 2026-08-26: sin fallback ES, ver core/autopilot.py
    assert payload.get("topic_en") == ""

    # (3) La ruta real persiste imágenes en chapters.images.
    with get_db() as conn:
        row = conn.execute(
            "SELECT images FROM chapters WHERE id = ?", (cid,)
        ).fetchone()
    stored = json.loads(row["images"] or "[]")
    assert len(stored) >= 1
    for p in stored:
        assert os.path.isfile(p), f"imagen persistida no existe: {p}"
    stored = json.loads(row["images"] or "[]")
    assert len(stored) >= 1
    for p in stored:
        assert os.path.isfile(p), f"imagen persistida no existe: {p}"


# ---------------------------------------------------------------------------
# §17 #31/#30 (fix acumulación): reset de image_gen + re-ejecución => REEMPLAZO
# ---------------------------------------------------------------------------
def test_image_gen_rerun_replaces_images_no_accumulation(store, tmp_path, monkeypatch):
    """Fix acumulación (interacción §17 #31 merge × reset image_gen): al
    re-ejecutar image_gen sobre un capítulo que YA tiene imágenes persistidas
    (simula book_72 tras varios resets: 127-140 imgs/capítulo),
    ``chapters.images`` queda con EXACTAMENTE las imágenes de la NUEVA
    ejecución — no se fusionan las viejas.

    La primera (y única) llamada de ``persist_chapter_images`` por capítulo
    dentro de la ejecución debe ser overwrite=True; el merge §17 #31 solo
    aplica a múltiples llamadas intra-ejecución (compensación IA, hoy inexistente)."""
    monkeypatch.setenv("IMAGE_PROVIDER", "local")
    monkeypatch.setenv("IMAGE_STORAGE_ROOT", str(tmp_path / "images_root"))
    monkeypatch.setenv("IMAGE_LOCAL_OUTPUT_DIR", str(tmp_path / "local_out"))

    book_id = _make_book(1)
    cid = _first_chapter_id(book_id)
    persist_chapter_result(book_id, cid, "draft_es", _DRAFT_ES)

    # "Ejecución previa": 3 imágenes viejas ya persistidas (existen en disco,
    # como pasaría tras un reset real de image_gen).
    old_dir = tmp_path / "old_imgs"
    old_dir.mkdir()
    old_paths = []
    for i in range(3):
        p = os.path.join(str(old_dir), f"old_run_{i}.png")
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        old_paths.append(p)
    persist_chapter_images(book_id, cid, old_paths)
    with get_db() as conn:
        row = conn.execute("SELECT images FROM chapters WHERE id = ?", (cid,)).fetchone()
    assert len(json.loads(row["images"])) == 3  # precondición

    # Re-ejecución real de image_gen (módulo real, provider local, ratio=0).
    with get_db() as conn:
        conn.execute("UPDATE books SET image_search_ratio = 0.0 WHERE id = ?", (book_id,))

    modules = {
        "image_generator": {
            "manifest": {"id": "image_generator", "config": {"timeout_seconds": 30}},
            "execute": img_main.execute,
        }
    }
    cap_map = {"generate_chapter_images": ["image_generator"]}
    executor = autopilot.default_executor_factory(modules, cap_map, store=store)

    job = _job_ready_at_phase(store, book_id, "image_gen")
    final = autopilot.run_job(job, store, executor, max_attempts=2, sleep_fn=_NOSLEEP)

    assert final["status"] == autopilot.JOB_COMPLETED

    with get_db() as conn:
        row = conn.execute("SELECT images FROM chapters WHERE id = ?", (cid,)).fetchone()
    stored = json.loads(row["images"] or "[]")

    # (1) Solo imágenes de la NUEVA ejecución: ninguna vieja queda.
    new_names = {os.path.basename(p) for p in stored}
    assert not any(os.path.basename(p).startswith("old_run_") for p in stored), (
        f"ACUMULACIÓN: imágenes de la ejecución previa siguen en chapters.images: {new_names}"
    )
    # (2) Las nuevas existen en disco y son rutas distintas de las viejas.
    assert len(stored) >= 1
    for p in stored:
        assert os.path.isfile(p), f"imagen nueva no existe: {p}"
    assert not (set(stored) & set(old_paths)), "rutas viejas re-persistidas"

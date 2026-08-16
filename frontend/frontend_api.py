"""API para el Frontend de Space Lair.

Sirve el frontend 8-bit y expone endpoints REST + SSE para
actualizaciones en tiempo real.
"""

import json
import logging
import os
import queue
import threading
import time
from typing import Optional

from flask import (
    Flask,
    Response,
    jsonify,
    request,
    send_file,
    send_from_directory,
)

from core import autopilot, events, task_queue
from core.auth import require_auth
from core.database import get_db, init_db
from core.logger import get_logger, log
from core.mcp_bridge import create_mcp_http_handler
from core.module_registry import capabilities_map, check_all_health, load_modules
from core.metrics import summarize_costs
from core.schemas import validate_payload
from core.workflow import all_workflows, cancel_workflow, create_workflow, get_workflow, run_workflow
from frontend.editorial import (
    build_payload,
    create_book,
    load_book,
    phase_capability,
)
from pydantic import ValidationError

logger = get_logger(__name__)

# Directorio del frontend
FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))

# Cola de eventos SSE
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

# Modulos cacheados (se recargan al arrancar)
_modules = {}
_cap_map = {}
# ---------------------------------------------------------------------------
# Autopilot editorial: estado del worker (singleton proceso-local)
# ---------------------------------------------------------------------------
# El worker del autopilot se arranca UNA única vez por proceso vía
# ensure_autopilot_worker_started(), aunque se llame a create_app() varias
# veces o el módulo se importe múltiples veces (flag + lock proceso-local).
# El reloader de desarrollo de Flask lanza un proceso nuevo (flag propio) ->
# un worker por proceso, nunca varios dentro del mismo proceso.
_autopilot_store = None
_autopilot_executor = None
_autopilot_worker_started = False
_autopilot_lock = threading.Lock()





def _broadcast_event(event: str, data: dict) -> None:
    """Envia un evento SSE a todos los clientes conectados."""
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    with sse_lock:
        for client_queue in sse_clients[:]:
            try:
                client_queue.put(payload)
            except Exception:
                sse_clients.remove(client_queue)


def _on_core_event(event_type: str, data: dict) -> None:
    """Callback del bus de eventos del core -> reenvia por SSE."""
    _broadcast_event(event_type, data)


def _serialize_task(task: dict) -> dict:
    """Serializa una tarea para la API (convierte payload/result a dict)."""
    serialized = dict(task)
    for field in ("payload", "result"):
        if serialized.get(field):
            try:
                serialized[field] = json.loads(serialized[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return serialized

def _serialize_job(job: dict) -> dict:
    """Serializa un job del autopilot respetando la estructura real del motor.

    Añade solo agregados derivados de datos reales (attempts = máximo de
    intentos por fase; duration = suma de duraciones de fases). Nunca inventa
    estados ni métricas.
    """
    phases = job.get("phases", [])
    attempts = max((ph.get("attempts") or 0 for ph in phases), default=0)
    duration = round(sum((ph.get("duration") or 0.0 for ph in phases)), 3)
    out = dict(job)
    out["attempts"] = attempts
    out["duration"] = duration
    out["phases"] = phases
    return out


def get_autopilot_store() -> autopilot.BookJobStore:
    """Singleton del BookJobStore (backend = fuente de verdad)."""
    global _autopilot_store
    if _autopilot_store is None:
        _autopilot_store = autopilot.BookJobStore()
    return _autopilot_store


def get_autopilot_executor():
    """Singleton del ejecutor de producción (reutiliza scheduler + editorial)."""
    global _autopilot_executor
    if _autopilot_executor is None:
        _autopilot_executor = autopilot.default_executor_factory(
            _modules, _cap_map, get_autopilot_store()
        )
    return _autopilot_executor


def ensure_autopilot_worker_started() -> None:
    """Arranca el worker del autopilot UNA única vez por proceso (idempotente).

    Protegido por un lock proceso-local: llamadas repetidas (varios create_app(),
    imports múltiples) no crean workers duplicados. Ejecuta el recovery real de
    core.autopilot al arrancar (política RUNNING/RETRY -> PENDING) y luego lanza
    el bucle del worker (que también recupera, de forma idempotente).
    """
    global _autopilot_worker_started
    with _autopilot_lock:
        if _autopilot_worker_started:
            return
        store = get_autopilot_store()
        autopilot.recover(store)
        autopilot.start_worker_daemon(store, get_autopilot_executor())
        _autopilot_worker_started = True


def _autopilot_active_job() -> Optional[dict]:
    """Job activo preferido; si ninguno, el más reciente por updated_at (real)."""
    store = get_autopilot_store()
    jobs = store.list_all()
    if not jobs:
        return None
    active = store.next_job()
    if active is not None:
        return active
    return max(jobs, key=lambda j: j.get("updated_at", ""))


def _autopilot_current_stats(job: dict) -> dict:
    """Estadísticas derivadas del job real (sin mock data)."""
    phases = job.get("phases", [])
    counts = {"pending": 0, "running": 0, "pass": 0, "fail": 0, "retry": 0}
    for p in phases:
        s = (p.get("status") or "PENDING").lower()
        if s == "pass":
            counts["pass"] += 1
        elif s == "fail":
            counts["fail"] += 1
        elif s == "retry":
            counts["retry"] += 1
        elif s == "running":
            counts["running"] += 1
        else:
            counts["pending"] += 1
    return {
        "job_status": job.get("status"),
        "current_phase": job.get("current_phase"),
        "phases_total": len(phases),
        "phases": counts,
        "docx_path": job.get("docx_path"),
    }


def _autopilot_current_book(job: dict) -> Optional[dict]:
    try:
        data = load_book(job.get("book_id"))
    except ValueError:
        return None
    b = data.get("book", {})
    return {
        "id": b.get("id"),
        "title": b.get("title"),
        "status": b.get("status"),
        "progress": (data.get("stats") or {}).get("progress"),
    }


def _autopilot_current_chapters(job: dict) -> list:
    try:
        data = load_book(job.get("book_id"))
    except ValueError:
        return []
    return data.get("chapters", [])


def _autopilot_pipeline(job: dict) -> list:
    status_by_id = {p.get("id"): p.get("status") for p in job.get("phases", [])}
    return [
        {
            "id": p["id"],
            "label": p.get("label"),
            "capability": p.get("capability"),
            "status": status_by_id.get(p["id"], "PENDING"),
        }
        for p in autopilot.AUTOPILOT_PHASES
    ]




def create_app() -> Flask:
    """Crea y configura la aplicacion Flask."""
    app = Flask(__name__, static_folder=None)

    # Inicializar BD y cargar modulos
    init_db()
    global _modules, _cap_map
    _modules = load_modules()
    _cap_map = capabilities_map(_modules)

    # Arrancar el worker del autopilot (singleton por proceso, idempotente).
    ensure_autopilot_worker_started()

    # Suscribirse a eventos del core para reenviarlos por SSE.
    # Los eventos del autopilot (job_*/phase_*) provienen del motor real de
    # core.autopilot; aquí SOLO se reenvían, no se inventan.
    for event_type in (
        "task_started",
        "task_completed",
        "task_failed",
        "central_ai_decision",
        "job_started",
        "phase_started",
        "phase_progress",
        "phase_completed",
        "phase_failed",
        "job_completed",
        "job_failed",
    ):
        events.subscribe(event_type, _on_core_event)

    # ============================================
    # API Endpoints
    # ============================================

    @app.route("/api/tasks")
    def api_tasks():
        """GET -> lista de tareas."""
        tasks = [_serialize_task(t) for t in task_queue.all_tasks()]
        return jsonify(tasks)

    @app.route("/api/modules")
    def api_modules():
        """GET -> lista de modulos con estado."""
        modules = []
        for module_id, module in _modules.items():
            manifest = module["manifest"]
            modules.append({
                "id": module_id,
                "name": manifest.get("name", module_id),
                "description": manifest.get("description", ""),
                "type": manifest.get("type", "tool"),
                "capabilities": manifest.get("capabilities", []),
                "status": "active",
                "requires_human_approval": manifest.get("requires_human_approval", False),
                "config": manifest.get("config", {}),
            })
        return jsonify(modules)

    @app.route("/api/stats")
    def api_stats():
        """GET -> estadisticas del sistema."""
        tasks = task_queue.all_tasks()
        total = len(tasks)
        done = sum(1 for t in tasks if t["status"] == "done")
        error = sum(1 for t in tasks if t["status"] == "error")
        running = sum(1 for t in tasks if t["status"] == "running")
        pending = sum(1 for t in tasks if t["status"] == "pending")
        pending_approval = sum(1 for t in tasks if t["status"] == "pending_approval")

        total_cost = sum(t.get("cost", 0.0) or 0.0 for t in tasks)
        total_tokens_in = sum(t.get("tokens_input", 0) or 0 for t in tasks)
        total_tokens_out = sum(t.get("tokens_output", 0) or 0 for t in tasks)

        # Estado real del autopilot editorial (derivado del BookJobStore,
        # nunca mock). Si no hay job, se devuelven valores vacíos en lugar de inventar.
        job = _autopilot_active_job()
        stats_payload = {
            "total_tasks": total,
            "done": done,
            "error": error,
            "running": running,
            "pending": pending,
            "pending_approval": pending_approval,
            "modules_active": len(_modules),
            "total_cost": round(total_cost, 6),
            "tokens_input": total_tokens_in,
            "tokens_output": total_tokens_out,
            "current_book": _autopilot_current_book(job) if job else None,
            "current_stats": _autopilot_current_stats(job) if job else {},
            "current_chapters": _autopilot_current_chapters(job) if job else [],
            "pipeline": _autopilot_pipeline(job) if job else [],
        }
        return jsonify(stats_payload)

    @app.route("/api/health")
    def api_health():
        """GET -> estado de salud de todos los modulos."""
        health = check_all_health(_modules)
        return jsonify(health)

    @app.route("/api/costs")
    def api_costs():
        """GET -> métricas de coste y tokens (total y por módulo)."""
        costs = summarize_costs()
        return jsonify(costs)

    @app.route("/api/layout-presets")
    def api_layout_presets():
        """GET -> presets de maquetación disponibles para poblar los selectores del Front."""
        from modules.document_builder.main import layout_presets

        return jsonify(layout_presets())


    @app.route("/api/books")
    def api_books():
        """GET -> lista de libros con estad\u00edsticas agregadas."""
        conn = get_db()
        try:
            books = conn.execute("SELECT * FROM books ORDER BY updated_at DESC").fetchall()
            result = []
            for book in books:
                book_id = book["id"]
                chapters = conn.execute(
                    "SELECT * FROM chapters WHERE book_id = ? ORDER BY number",
                    (book_id,),
                ).fetchall()
                chapter_count = len(chapters)

                es_done = sum(
                    1 for c in chapters if (c["draft_es"] or "").strip() or (c["edited_es"] or "").strip()
                )
                progress_es = int((es_done / chapter_count) * 100) if chapter_count else 0

                en_done = sum(
                    1 for c in chapters if (c["draft_en"] or "").strip() or (c["edited_en"] or "").strip()
                )
                progress_en = int((en_done / chapter_count) * 100) if chapter_count else 0

                total_images = 0
                for c in chapters:
                    try:
                        total_images += len(json.loads(c["images"] or "[]"))
                    except (json.JSONDecodeError, TypeError):
                        pass

                chapter_ids = [c["id"] for c in chapters]
                total_sources = 0
                if chapter_ids:
                    all_source_ids = set()
                    for cid in chapter_ids:
                        rows_src = conn.execute(
                            "SELECT id FROM sources WHERE chapter_ids LIKE ? OR chapter_ids LIKE ? OR chapter_ids = ?",
                            (f"%[{cid}]%", f"%{cid}%", str(cid)),
                        ).fetchall()
                        for r in rows_src:
                            all_source_ids.add(r["id"])
                    total_sources = len(all_source_ids)

                book_capabilities = {
                    "create_book_plan",
                    "write_chapter_es",
                    "write_chapter_en",
                    "fact_check_chapter",
                    "edit_chapter",
                    "translate_es_en",
                    "translate_en_es",
                    "generate_chapter_images",
                    "generate_image",
                    "build_book_docx",
                    "build_book_pdf",
                    "final_quality_control",
                }
                tasks = task_queue.all_tasks()
                active_tasks = 0
                task_errors = 0
                for t in tasks:
                    if t.get("capability") in book_capabilities:
                        try:
                            payload = json.loads(t.get("payload") or "{}")
                            if str(payload.get("book_id")) == str(book_id) or (
                                isinstance(payload.get("book"), dict)
                                and str(payload.get("book", {}).get("id")) == str(book_id)
                            ):
                                if t["status"] in ("pending", "running"):
                                    active_tasks += 1
                                elif t["status"] == "error":
                                    task_errors += 1
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            pass

                checkpoint_dir = os.path.join("data", "checkpoints", str(book_id))
                checkpoint_count = 0
                if os.path.isdir(checkpoint_dir):
                    for root, dirs, files in os.walk(checkpoint_dir):
                        checkpoint_count += len([f for f in files if f.endswith(".json") and "status" not in f])

                has_docx = False
                has_pdf = False
                has_qc = False
                for t in tasks:
                    if t.get("capability") == "build_book_docx" and t["status"] == "done":
                        try:
                            result_data = json.loads(t.get("result") or "{}")
                            if result_data.get("docx_path"):
                                has_docx = True
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if t.get("capability") == "build_book_pdf" and t["status"] == "done":
                        try:
                            result_data = json.loads(t.get("result") or "{}")
                            if result_data.get("pdf_path"):
                                has_pdf = True
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if t.get("capability") == "final_quality_control" and t["status"] == "done":
                        has_qc = True

                phases = {
                    "research": "done" if any((c["research"] or "").strip() for c in chapters) else "pending",
                    "outline": "done" if any((c["outline"] or "").strip() for c in chapters) else "pending",
                    "writing_es": "done" if progress_es == 100 else ("partial" if progress_es > 0 else "pending"),
                    "writing_en": "done" if progress_en == 100 else ("partial" if progress_en > 0 else "pending"),
                    "fact_check": "pending",
                    "edit": "partial",
                    "images": "done" if total_images >= chapter_count * 3 else ("partial" if total_images > 0 else "pending") if chapter_count else "pending",
                    "docx": "done" if has_docx else "pending",
                    "pdf": "done" if has_pdf else "pending",
                    "qc": "done" if has_qc else "pending",
                }

                phase_values = {"pending": 0, "partial": 50, "done": 100}
                phase_progress = sum(phase_values.get(v, 0) for v in phases.values())
                progress = int(phase_progress / len(phases)) if phases else 0

                result.append({
                    "id": book_id,
                    "title": book["title"],
                    "status": book["status"],
                    "target_chapters": book["target_chapters"],
                    "chapter_count": chapter_count,
                    "progress_es": progress_es,
                    "progress_en": progress_en,
                    "total_images": total_images,
                    "total_sources": total_sources,
                    "active_tasks": active_tasks,
                    "errors": task_errors,
                    "checkpoints": checkpoint_count,
                    "has_docx": has_docx,
                    "has_pdf": has_pdf,
                    "has_qc": has_qc,
                    "phases": phases,
                    "progress": progress,
                })
            return jsonify(result)
        finally:
            conn.close()

    @app.route("/api/books/<int:book_id>")
    def api_book_detail(book_id: int):
        """GET -> detalle de un libro."""
        conn = get_db()
        try:
            book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
            if not book:
                return jsonify({"error": f"Libro {book_id} no encontrado"}), 404

            chapters = conn.execute(
                "SELECT * FROM chapters WHERE book_id = ? ORDER BY number",
                (book_id,),
            ).fetchall()

            chapter_list = []
            for c in chapters:
                sources = []
                try:
                    sources = json.loads(c["sources"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    pass
                images = []
                try:
                    images = json.loads(c["images"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    pass

                chapter_list.append({
                    "id": c["id"],
                    "number": c["number"],
                    "title": c["title"],
                    "status": c["status"],
                    "research": bool((c["research"] or "").strip()),
                    "outline": bool((c["outline"] or "").strip()),
                    "draft_es": bool((c["draft_es"] or "").strip()),
                    "draft_en": bool((c["draft_en"] or "").strip()),
                    "edited_es": bool((c["edited_es"] or "").strip()),
                    "edited_en": bool((c["edited_en"] or "").strip()),
                    "image_count": len(images),
                    "source_count": len(sources),
                    "quality_status": c.get("quality_status"),
                })

            return jsonify({
                "id": book["id"],
                "title": book["title"],
                "subtitle": book.get("subtitle"),
                "description": book.get("description"),
                "author": book.get("author"),
                "target_audience": book.get("target_audience"),
                "genre": book.get("genre"),
                "languages": book.get("languages", "es"),
                "target_chapters": book["target_chapters"],
                "status": book["status"],
                "created_at": book.get("created_at"),
                "updated_at": book.get("updated_at"),
                "chapters": chapter_list,
            })
        finally:
            conn.close()

    @app.route("/api/enqueue", methods=["POST"])
    def api_enqueue():
        """POST -> encola una nueva tarea."""
        data = request.get_json(silent=True) or {}
        capability = data.get("capability")
        payload = data.get("payload")
        max_attempts = data.get("max_attempts", 1)

        if not capability or not isinstance(payload, dict):
            return jsonify({"error": "Se requiere 'capability' y 'payload' (dict)"}), 400

        if capability not in _cap_map:
            return jsonify({
                "error": f"No hay modulos que soporten la capability '{capability}'"
            }), 400

        # Validar payload con Pydantic antes de encolar
        try:
            validated = validate_payload(capability, payload)
        except ValidationError as e:
            return jsonify({
                "error": "Payload invalido",
                "details": e.errors(),
            }), 400
        except ValueError:
            # Capability no tiene esquema definido (pero existe modulo)
            validated = payload

        task_id = task_queue.enqueue_task(capability, validated, max_attempts=max_attempts)
        _broadcast_event("task_created", {"task_id": task_id, "capability": capability})
        return jsonify({"task_id": task_id, "status": "pending"}), 201

    @app.route("/api/approve/<int:task_id>", methods=["POST"])
    @require_auth()
    def api_approve(task_id: int):
        """POST -> aprueba una tarea pendiente de aprobacion (requiere JWT)."""
        task = task_queue.get_task(task_id)
        if task is None:
            return jsonify({"error": f"Tarea {task_id} no encontrada"}), 404
        if task["status"] != "pending_approval":
            return jsonify({
                "error": f"Tarea {task_id} no esta pendiente de aprobacion "
                         f"(estado: {task['status']})"
            }), 400

        task_queue.approve_task(task_id)
        _broadcast_event("task_approved", {"task_id": task_id})
        return jsonify({"task_id": task_id, "status": "pending"})

    @app.route("/api/reject/<int:task_id>", methods=["POST"])
    @require_auth()
    def api_reject(task_id: int):
        """POST -> rechaza una tarea pendiente de aprobacion (requiere JWT)."""
        task = task_queue.get_task(task_id)
        if task is None:
            return jsonify({"error": f"Tarea {task_id} no encontrada"}), 404
        if task["status"] != "pending_approval":
            return jsonify({
                "error": f"Tarea {task_id} no esta pendiente de aprobacion "
                         f"(estado: {task['status']})"
            }), 400

        task_queue.reject_task(task_id)
        _broadcast_event("task_rejected", {"task_id": task_id})
        return jsonify({"task_id": task_id, "status": "error"})

    @app.route("/api/tasks/<int:task_id>/cancel", methods=["POST"])
    def api_task_cancel(task_id: int):
        """POST -> cancela una tarea individual."""
        task = task_queue.get_task(task_id)
        if task is None:
            return jsonify({"error": f"Tarea {task_id} no encontrada"}), 404
        if task["status"] not in ("pending", "pending_approval", "running"):
            return jsonify({
                "error": f"Tarea {task_id} no se puede cancelar (estado: {task['status']})"
            }), 400

        task_queue.cancel_task(task_id)
        _broadcast_event("task_cancelled", {"task_id": task_id})
        return jsonify({"task_id": task_id, "status": "cancelled"})

    @app.route("/api/tasks/<int:task_id>/retry", methods=["POST"])
    def api_task_retry(task_id: int):
        """POST -> reintenta una tarea fallida o cancelada."""
        task = task_queue.get_task(task_id)
        if task is None:
            return jsonify({"error": f"Tarea {task_id} no encontrada"}), 404
        if task["status"] not in ("error", "cancelled"):
            return jsonify({
                "error": f"Tarea {task_id} no se puede reintentar (estado: {task['status']})"
            }), 400

        task_queue.requeue_task(task_id)
        _broadcast_event("task_retried", {"task_id": task_id})
        return jsonify({"task_id": task_id, "status": "pending"})

    @app.route("/api/books", methods=["POST"])
    def api_books_create():
        """POST -> crea un libro nuevo con sus capítulos."""
        data = request.get_json(silent=True) or {}
        try:
            result = create_book(data)
            book_id = result["book_id"]
            _broadcast_event("book_created", {"book_id": book_id, "title": data.get("title")})
            return jsonify(result), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/books/<int:book_id>/load")
    def api_book_load(book_id: int):
        """GET -> estado completo de un libro (capítulos + progreso real)."""
        try:
            data = load_book(book_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify(data)

    @app.route("/api/books/<int:book_id>/run", methods=["POST"])
    def api_book_run(book_id: int):
        """POST -> ejecuta una fase del pipeline editorial del libro."""
        data = request.get_json(silent=True) or {}
        phase = data.get("phase")
        chapter_id = data.get("chapter_id")

        if phase not in ("planner", "research", "outline", "writer", "writer_en",
                         "fact_check", "editor", "image_plan", "image_gen", "docx"):
            return jsonify({"error": f"Fase inválida: {phase}"}), 400

        try:
            payload = build_payload(book_id, phase, data, chapter_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        capability = phase_capability(phase)
        if capability not in _cap_map:
            return jsonify({"error": f"No hay módulos que soporten la capability '{capability}'"}), 400

        try:
            validated = validate_payload(capability, payload)
        except ValidationError as e:
            return jsonify({"error": "Payload inválido", "details": e.errors()}), 400
        except ValueError:
            validated = payload

        task_id = task_queue.enqueue_task(capability, validated, max_attempts=1)
        _broadcast_event("task_created", {"task_id": task_id, "capability": capability, "book_id": book_id})
        return jsonify({"task_id": task_id, "status": "pending", "phase": phase, "capability": capability}), 201

    @app.route("/api/pipeline")
    def api_pipeline():
        """GET -> mínimo del pipeline de fases."""
        from frontend.editorial import PIPELINE

        result = []
        for p in PIPELINE:
            result.append({"id": p["id"], "label": p["label"], "capability": p["capability"], "per_chapter": p["per_chapter"]})
        return jsonify(result)

    # ============================================
    # Autopilot editorial
    # ============================================
    # Todos los endpoints leen/escriben SOLO vía BookJobStore / el motor real de
    # core.autopilot. No se duplica persistencia ni estados; no hay mock data.

    @app.route("/api/books/<int:book_id>/autopilot/start", methods=["POST"])
    def api_autopilot_start(book_id: int):
        """POST -> crea (o reutiliza) el job autopilot del libro."""
        try:
            load_book(book_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404

        store = get_autopilot_store()
        existing = store.load_by_book(book_id)
        if existing is not None and existing["status"] in (
            autopilot.JOB_PENDING,
            autopilot.JOB_RUNNING,
        ):
            # Ya hay un job activo: se devuelve, no se crea otro (sin duplicados).
            return jsonify(_serialize_job(existing)), 200

        # Leer image_count del libro para pasarlo al job data
        try:
            book_data = load_book(book_id)
            img_count = (book_data.get("book") or {}).get("image_count")
            if img_count is None:
                img_count = 3
        except Exception:
            img_count = 3

        job = autopilot.create_job(store, book_id, data={"num_images": img_count})
        return jsonify(_serialize_job(job)), 201

    @app.route("/api/books/<int:book_id>/autopilot")
    def api_autopilot_get(book_id: int):
        """GET -> estado persistente real del job del libro."""
        store = get_autopilot_store()
        job = store.load_by_book(book_id)
        if job is None:
            return jsonify({"error": f"No existe job autopilot para el libro {book_id}"}), 404
        return jsonify(_serialize_job(job))

    @app.route("/api/autopilot")
    def api_autopilot_list():
        """GET -> lista de jobs persistidos, orden estable por updated_at desc."""
        store = get_autopilot_store()
        jobs = store.list_all()
        jobs.sort(key=lambda j: j.get("updated_at", ""), reverse=True)
        return jsonify([_serialize_job(j) for j in jobs])

    @app.route("/api/books/<int:book_id>/autopilot/cancel", methods=["POST"])
    def api_autopilot_cancel(book_id: int):
        """POST -> delega en el mecanismo real de cancelación del motor.

        No convierte estados terminales en CANCELLED (el motor lo gestiona).
        Si no existe job -> 404. Devuelve siempre el estado real persistido.
        """
        store = get_autopilot_store()
        job = store.load_by_book(book_id)
        if job is None:
            return jsonify({"error": f"No existe job autopilot para el libro {book_id}"}), 404
        cancelled = autopilot.cancel_job(store, job["job_id"])
        return jsonify(_serialize_job(cancelled)), 200

    @app.route("/api/books/<int:book_id>/autopilot/retry", methods=["POST"])
    def api_autopilot_retry(book_id: int):
        """POST -> delega en el retry real del motor (core.autopilot.retry_job).

        La definición de retry vive en el motor, NO en Flask. Los estados
        no reintentables producen 400 con el motivo real.
        """
        store = get_autopilot_store()
        job = store.load_by_book(book_id)
        if job is None:
            return jsonify({"error": f"No existe job autopilot para el libro {book_id}"}), 404
        try:
            retried = autopilot.retry_job(store, job["job_id"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(_serialize_job(retried)), 200

    @app.route("/api/books/<int:book_id>/docx")
    def api_book_docx(book_id: int):
        """GET -> sirve el DOCX REAL generado por Document Builder.

        Reglas:
        - localiza el job real vía BookJobStore (fuente única de verdad).
        - verifica que el job pertenece al book_id solicitado.
        - verifica que el job está COMPLETED.
        - verifica que docx_path existe en el job.
        - verifica que el archivo existe físicamente.
        - previene path traversal: docx_path debe estar dentro del directorio
          permitido (data/checkpoints o output/docx) y el nombre de archivo
          no debe contener '..' ni absolutizar fuera de la base.
        - NO genera un DOCX nuevo, NO copia a ubicación falsa, NO simula.
        """
        store = get_autopilot_store()
        job = store.load_by_book(book_id)
        if job is None:
            return jsonify({"error": f"No existe job autopilot para el libro {book_id}"}), 404

        # 3. verificar estado COMPLETED
        if job.get("status") != autopilot.JOB_COMPLETED:
            return jsonify({
                "error": f"El job aún no está COMPLETED (estado: {job.get('status')})",
                "status": job.get("status"),
            }), 409

        # 4. verificar docx_path
        docx_path = job.get("docx_path")
        if not docx_path:
            return jsonify({"error": "El job completado no tiene docx_path registrado"}), 404

        # 6. prevenir path traversal
        proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        abs_docx = os.path.abspath(docx_path)

        # El DOCX real se genera bajo data/ o output/ (subdirectorios del proyecto).
        allowed_roots = [
            os.path.join(proj_root, "data"),
            os.path.join(proj_root, "output"),
        ]
        allowed_abs = os.path.abspath(abs_docx)
        inside = any(
            allowed_abs == os.path.abspath(r) or allowed_abs.startswith(os.path.abspath(r) + os.sep)
            for r in allowed_roots
        )
        if not inside:
            return jsonify({"error": "docx_path fuera del directorio permitido (path traversal)"}), 400

        # 5. verificar archivo existe físicamente
        if not os.path.isfile(abs_docx):
            return jsonify({"error": f"El archivo DOCX no existe físicamente: {docx_path}"}), 404

        # 7. devolver el archivo DOCX real
        filename = os.path.basename(abs_docx)
        return send_file(
            abs_docx,
            as_attachment=True,
            download_name=f"book_{book_id}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    # ============================================
    # Workflow Endpoints
    # ============================================

    @app.route("/api/workflows", methods=["GET"])
    def api_workflows_list():
        """GET -> lista de workflows."""
        return jsonify(all_workflows())

    @app.route("/api/workflows", methods=["POST"])
    def api_workflows_create():
        """POST -> crea un workflow."""
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        definition = data.get("definition")

        if not name or not definition:
            return jsonify({"error": "Se requiere 'name' y 'definition'"}), 400

        try:
            workflow_id = create_workflow(name, definition)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        _broadcast_event("workflow_created", {"workflow_id": workflow_id, "name": name})
        return jsonify({"workflow_id": workflow_id, "status": "pending"}), 201

    @app.route("/api/workflows/<int:workflow_id>", methods=["GET"])
    def api_workflow_get(workflow_id: int):
        """GET -> detalle de un workflow."""
        wf = get_workflow(workflow_id)
        if wf is None:
            return jsonify({"error": f"Workflow {workflow_id} no encontrado"}), 404
        return jsonify(wf)

    @app.route("/api/workflows/<int:workflow_id>/run", methods=["POST"])
    def api_workflow_run(workflow_id: int):
        """POST -> ejecuta un workflow."""
        try:
            result = run_workflow(workflow_id, _modules, _cap_map)
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/workflows/<int:workflow_id>/cancel", methods=["POST"])
    def api_workflow_cancel(workflow_id: int):
        """POST -> cancela un workflow."""
        try:
            result = cancel_workflow(workflow_id)
            if result is None:
                return jsonify({"error": f"Workflow {workflow_id} no encontrado"}), 404
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    # ============================================
    # MCP Endpoints (Model Context Protocol)
    # ============================================

    # Handler WSGI para servidores MCP locales
    _mcp_handler = create_mcp_http_handler(_modules)

    @app.route("/mcp")
    def mcp_index():
        """GET -> lista de servidores MCP disponibles."""
        from werkzeug.test import EnvironBuilder

        env = EnvironBuilder(path="/mcp", method="GET").get_environ()
        body = b"".join(_mcp_handler(env, lambda *a, **k: None))
        return Response(
            body,
            mimetype="application/json",
        )

    @app.route("/mcp/<module_id>", methods=["POST"])
    def mcp_endpoint(module_id: str):
        """POST -> endpoint JSON-RPC MCP para un módulo."""
        from werkzeug.test import EnvironBuilder

        raw_body = request.get_data() or b"{}"
        env = EnvironBuilder(
            path=f"/mcp/{module_id}",
            method="POST",
            input_stream=None,
            content_type="application/json",
        ).get_environ()
        env["CONTENT_LENGTH"] = str(len(raw_body))
        env["wsgi.input"] = type(
            "BytesIO",
            (),
            {"read": lambda self, n=-1: raw_body if n <= 0 or n >= len(raw_body) else raw_body[:n]},
        )()

        captured = {}

        def _start_response(status, headers):
            captured["status"] = status
            captured["headers"] = headers

        body = b"".join(_mcp_handler(env, _start_response))
        return Response(
            body,
            status=int(captured.get("status", "200 OK").split(" ")[0]),
            mimetype="application/json",
        )

    @app.route("/api/stream")
    def api_stream():
        """GET -> Server-Sent Events para updates en tiempo real."""
        def generate():
            client_queue: queue.Queue = queue.Queue(maxsize=100)
            with sse_lock:
                sse_clients.append(client_queue)

            try:
                # Enviar heartbeat cada 15s para mantener la conexion
                last_heartbeat = time.time()
                while True:
                    try:
                        message = client_queue.get(timeout=1)
                        yield message
                    except queue.Empty:
                        if time.time() - last_heartbeat > 15:
                            yield ": heartbeat\n\n"
                            last_heartbeat = time.time()
            except GeneratorExit:
                with sse_lock:
                    if client_queue in sse_clients:
                        sse_clients.remove(client_queue)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ============================================
    # Frontend estatico
    # ============================================

    @app.route("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(FRONTEND_DIR, filename)

    return app


def run_server(host: str = "0.0.0.0", port: int = 8080, debug: bool = False) -> None:
    """Arranca el servidor web de Space Lair."""
    app = create_app()
    log(
        logger,
        logging.INFO,
        f"Servidor web en http://{host}:{port}",
    )
    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    run_server()

"""Autopilot editorial de Space Lair.

Motor orquestador que, dado un libro, ejecuta TODAS las fases del pipeline
editorial en orden, persistiendo el estado de cada fase y del job en disco
(JSON bajo ``data/autopilot/jobs/``). El BACKEND es la fuente de verdad:
el frontend solo representa este estado.

Principios:
- No depende de un LLM concreto: el ejecutor orquesta los módulos ya cargados
  por el proyecto (el LLM es un proveedor sustituible bajo los módulos).
- No duplica la ejecución de módulos: por defecto reutiliza la ruta de
  ``core.scheduler`` (``_process_task`` / ``_execute_with_timeout``) y de
  ``frontend.editorial.build_payload``, vía un ejecutor inyectable (no mocks en
  producción; la inyección existe para aislar y probar el orquestador).
- Estados de job: PENDING, RUNNING, FAILED, COMPLETED, CANCELLED.
- Estados de fase: PENDING, RUNNING, RETRY, PASS, FAIL.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

from core import events
from core.logger import get_logger, log

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Estados
# ---------------------------------------------------------------------------
JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_FAILED = "FAILED"
JOB_COMPLETED = "COMPLETED"
JOB_CANCELLED = "CANCELLED"

PHASE_PENDING = "PENDING"
PHASE_RUNNING = "RUNNING"
PHASE_RETRY = "RETRY"
PHASE_PASS = "PASS"
PHASE_FAIL = "FAIL"

JOB_ACTIVE = (JOB_PENDING, JOB_RUNNING)
JOB_TERMINAL = (JOB_FAILED, JOB_COMPLETED, JOB_CANCELLED)

# Pipeline editorial objetivo (orden REAL). QUALITY GATE = final_quality_control.
AUTOPILOT_PHASES = [
    {"id": "planner", "capability": "create_book_plan", "label": "BOOK PLANNER"},
    {"id": "research", "capability": "research_web", "label": "RESEARCH"},
    {"id": "outline", "capability": "create_book_plan", "label": "OUTLINE"},
    {"id": "writer", "capability": "write_chapter_es", "label": "CHAPTER WRITER"},
    {"id": "fact_check", "capability": "fact_check_chapter", "label": "FACT CHECK"},
    {"id": "editor", "capability": "edit_chapter", "label": "EDITOR"},
    {"id": "image_plan", "capability": "create_chapter_image_plan", "label": "IMAGE PLAN"},
    {"id": "image_gen", "capability": "generate_chapter_images", "label": "IMAGE GENERATOR"},
    {"id": "quality_gate", "capability": "final_quality_control", "label": "QUALITY GATE"},
    {"id": "docx", "capability": "build_book_docx", "label": "DOCUMENT BUILDER"},
]

# Fases que se ejecutan una vez por capítulo real del libro.
# El subestado por capítulo vive en phase["subs"]["chapters"].
PER_CHAPTER_PHASES = {"writer", "writer_en", "fact_check", "editor", "image_plan", "image_gen"}

# Reintentos por defecto por fase (no infinitos).
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_BACKOFF_STEP = 2.0

# Persistencia robusta de jobs: reintentos cortos y acotados (transitorios de filesystem).
SAVE_MAX_ATTEMPTS = 3
SAVE_BACKOFF_BASE = 0.05  # segundos; backoff lineal (base * intento)


def _default_jobs_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "autopilot", "jobs")


DEFAULT_JOBS_DIR = _default_jobs_dir()


def _now() -> str:
    """Timestamp actual en formato SQLite (UTC)."""
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


# ---------------------------------------------------------------------------
# Persistencia (backend = fuente de verdad)
# ---------------------------------------------------------------------------
class BookJobStore:
    """Persistencia de jobs en JSON (un archivo por job)."""

    def __init__(self, directory: Optional[str] = None) -> None:
        self.directory = directory or DEFAULT_JOBS_DIR
        os.makedirs(self.directory, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, job_id: str) -> str:
        return os.path.join(self.directory, f"{job_id}.json")

    def exists(self, job_id: str) -> bool:
        return os.path.isfile(self._path(job_id))

    def load(self, job_id: str) -> Optional[dict]:
        path = self._path(job_id)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_by_book(self, book_id: int) -> Optional[dict]:
        for job in self.list_all():
            if job.get("book_id") == book_id:
                return job
        return None

    def save(self, job: dict) -> None:
        """Persiste el job de forma atómica y robusta (escritura .tmp + os.replace).

        - Se escribe primero en ``<job_id>.json.tmp`` con ``flush``/``os.fsync`` y luego se
          commitea con ``os.replace`` (atómico); el ``.json`` committed previo queda intacto
          si el commit falla.
        - Reintentos cortos y acotados (`SAVE_MAX_ATTEMPTS`) con backoff breve para errores
          transitorios de filesystem. NO son bucles infinitos ni bloqueos prolongados.
        - En fallo definitivo se registra con ``logger.exception`` y se re-lanza: un error de
          persistencia NUNCA se convierte en éxito.
        - Se limpia un ``.tmp`` residual solo cuando corresponde (tras un intento fallido o
          abortado); jamás se borra ni se corrompe el ``.json`` committed.
        """
        with self._lock:
            final = self._path(job["job_id"])
            tmp = final + ".tmp"
            for attempt in range(1, SAVE_MAX_ATTEMPTS + 1):
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(job, f, ensure_ascii=False, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, final)
                    return
                except OSError as exc:
                    if attempt >= SAVE_MAX_ATTEMPTS:
                        # Fallo definitivo: el .json committed quedó intacto.
                        try:
                            if os.path.exists(tmp):
                                os.remove(tmp)
                        except OSError:
                            pass
                        logger.exception(
                            "save() fallo definitivo de persistencia tras %d intentos",
                            SAVE_MAX_ATTEMPTS,
                            extra={"job_id": job.get("job_id")},
                        )
                        raise
                    log(
                        logger,
                        logging.WARNING,
                        "save() error transitorio de filesystem; reintentando",
                        job_id=job.get("job_id"),
                        error=str(exc),
                        attempt=attempt,
                        max_attempts=SAVE_MAX_ATTEMPTS,
                    )
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except OSError:
                        pass
                    time.sleep(SAVE_BACKOFF_BASE * attempt)
                    continue
                except Exception:
                    # Error no transitorio: .json committed intacto; limpiar .tmp y re-lanzar.
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except OSError:
                        pass
                    logger.exception(
                        "save() fallo no transitorio de persistencia",
                        extra={"job_id": job.get("job_id")},
                    )
                    raise

    def list_all(self) -> list[dict]:
        jobs: list[dict] = []
        for name in sorted(os.listdir(self.directory)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.directory, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    job = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            jobs.append(job)
        jobs.sort(key=lambda j: j.get("created_at", ""))
        return jobs

    def next_job(self) -> Optional[dict]:
        """Primer job activo (PENDING o RUNNING) por antigüedad."""
        for job in self.list_all():
            if job.get("status") in JOB_ACTIVE:
                return job
        return None


def create_job(store: BookJobStore, book_id: int, data: Optional[dict] = None) -> dict:
    """Crea (y persiste) un job nuevo para el libro."""
    now = _now()
    job_id = f"book_{book_id}"
    phases = []
    for p in AUTOPILOT_PHASES:
        phases.append(
            {
                "id": p["id"],
                "label": p["label"],
                "capability": p["capability"],
                "status": PHASE_PENDING,
                "started_at": None,
                "completed_at": None,
                "duration": None,
                "attempts": 0,
                "metrics": {},
                "module": None,
                "task_id": None,
                "error": None,
            }
        )
    job = {
        "job_id": job_id,
        "book_id": book_id,
        "status": JOB_PENDING,
        "created_at": now,
        "updated_at": now,
        "current_phase": phases[0]["id"],
        "phases": phases,
        "docx_path": None,
        "error": None,
        "data": data or {},
    }
    store.save(job)
    return job


def cancel_job(store: BookJobStore, job_id: str) -> Optional[dict]:
    """Cancela un job pendiente o en ejecución (los terminales no se tocan)."""
    job = store.load(job_id)
    if job is None:
        return None
    if job["status"] in (JOB_PENDING, JOB_RUNNING):
        job["status"] = JOB_CANCELLED
        job["updated_at"] = _now()
        job["error"] = "Cancelado por operador"
        store.save(job)
    return job
def retry_job(store: BookJobStore, job_id: str) -> Optional[dict]:
    """Reintenta un job fallido o cancelado (fuente única de retry del motor).

    Reglas de transición (no se inventan estados):
    - PENDING/RUNNING  -> ya está activo; se rechaza con ValueError.
    - COMPLETED        -> terminal irreversible; se rechaza con ValueError.
    - FAILED/CANCELLED -> se marca PENDING y las fases no-PASS vuelven a
      PENDING (sin tocar lo ya PASS), de modo que el worker re-ejecuta desde
      la fase que falló. Nunca se marca COMPLETED sin evidencia real.

    Devuelve el job reintentado, o None si el job no existe.
    """
    job = store.load(job_id)
    if job is None:
        return None
    if job["status"] in (JOB_PENDING, JOB_RUNNING):
        raise ValueError(
            f"El job '{job_id}' está en estado {job['status']} (ya activo)"
        )
    if job["status"] == JOB_COMPLETED:
        raise ValueError(f"El job '{job_id}' está COMPLETED y no se puede reintentar")

    for ph in job["phases"]:
        if ph["status"] in (PHASE_FAIL, PHASE_RETRY, PHASE_RUNNING):
            ph["status"] = PHASE_PENDING
            ph["attempts"] = 0
            ph["started_at"] = None
            ph["completed_at"] = None
            ph["duration"] = None
            ph["error"] = None
            ph["metrics"] = {}
            # Subestado per-capítulo: conservar PASS, resetear el resto.
            subs = ph.get("subs")
            if subs and isinstance(subs.get("chapters"), dict):
                for csub in subs["chapters"].values():
                    if csub.get("status") != "PASS":
                        csub["status"] = "PENDING"
                        csub["attempts"] = 0
                        csub["error"] = None
            if subs is not None:
                subs["done"] = sum(
                    1 for s in subs.get("chapters", {}).values()
                    if s.get("status") == "PASS"
                )
    job["status"] = JOB_PENDING
    job["error"] = None
    job["updated_at"] = _now()
    store.save(job)
    return job

# ---------------------------------------------------------------------------
# Recovery tras reinicio (política determinista: RUNNING/RETRY -> PENDING)
# ---------------------------------------------------------------------------
def recover(store: BookJobStore) -> list[dict]:
    """Recupera jobs huérfanos tras reinicio.

    Todo job RUNNING con una fase RUNNING (o RETRY) se reconstruye: la fase se
    marca PENDING y sus intentos se reinician (re-evaluación limpia). Nunca se
    marca COMPLETED sin evidencia real. Devuelve los jobs recuperados.
    """
    resumed: list[dict] = []
    for job in store.list_all():
        if job["status"] != JOB_RUNNING:
            continue
        changed = False
        for ph in job["phases"]:
            if ph["status"] in (PHASE_RUNNING, PHASE_RETRY):
                ph["status"] = PHASE_PENDING
                ph["attempts"] = 0
                ph["started_at"] = None
                ph["completed_at"] = None
                ph["duration"] = None
                ph["error"] = None
                # Subestado per-capítulo: conservar PASS, resetear el resto.
                subs = ph.get("subs")
                if subs and isinstance(subs.get("chapters"), dict):
                    for csub in subs["chapters"].values():
                        if csub.get("status") in (PHASE_RUNNING, PHASE_RETRY):
                            csub["status"] = "PENDING"
                            csub["attempts"] = 0
                            csub["error"] = None
                changed = True
        if changed:
            job["updated_at"] = _now()
            store.save(job)
            log(
                logger,
                logging.INFO,
                "Job recuperado tras reinicio (fase -> PENDING)",
                job_id=job["job_id"],
                book_id=job["book_id"],
            )
            resumed.append(job)
    # Limpieza determinista de temporales huérfanos: el .json committed es la fuente de verdad.
    _recover_orphan_tmp(store)
    return resumed


def _remove_tmp_file(path: str, job_id: Optional[str], reason: str) -> None:
    """Elimina un ``.tmp`` huérfano y lo registra; nunca toca el ``.json`` committed."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.exception("No se pudo eliminar el .tmp huérfano", extra={"path": path})
        return
    log(
        logger,
        logging.INFO,
        f".tmp huérfano descartado: {reason}",
        job_id=job_id,
    )


def _recover_orphan_tmp(store: BookJobStore) -> None:
    """Detecta y limpia de forma segura temporales ``<job_id>.json.tmp`` huérfanos.

    El ``.json`` committed es la ÚNICA fuente de verdad (el frontend y el worker lo leen).
    Un ``.tmp`` es el intermedio de un ``save()`` atómico interrumpido, p.ej. una transición
    que quedó ``RUNNING`` sin ``task_id`` (la fase nunca llegó a ejecutarse). Se decide por
    caso y NUNCA se promueve un ``.tmp`` a ``.json`` a ciegas: se valida JSON, coherencia de
    job, recencia y carácter recuperable; si no es seguro, se conserva el ``.json`` committed
    y se descarta el temporal. De este modo el worker re-ejecuta la transición desde el estado
    committed (sin duplicar tareas ni marcar éxito falso).
    """
    try:
        names = os.listdir(store.directory)
    except OSError:
        return
    for name in sorted(names):
        if not name.endswith(".json.tmp"):
            continue
        tmp_path = os.path.join(store.directory, name)
        final_name = name[: -len(".tmp")]  # p.ej. book_8.json
        job_id = final_name[: -len(".json")] if final_name.endswith(".json") else None

        # 1) Debe ser JSON válido (si no, es un temporal incompleto).
        try:
            with open(tmp_path, "r", encoding="utf-8") as f:
                tmp_job = json.load(f)
        except (OSError, json.JSONDecodeError):
            _remove_tmp_file(tmp_path, job_id, reason="no es JSON válido (temporal incompleto)")
            continue

        # 2) Debe corresponder al mismo job (nombre coherente con el job_id interno).
        if not isinstance(tmp_job, dict) or job_id is None or tmp_job.get("job_id") != job_id:
            _remove_tmp_file(tmp_path, job_id, reason="no corresponde a un job coherente")
            continue

        final_path = store._path(job_id)

        # 3) Debe existir un .json committed como fuente de verdad (no se promueve un temporal).
        if not os.path.isfile(final_path):
            _remove_tmp_file(tmp_path, job_id, reason="sin .json committed; no se promueve un temporal")
            continue

        # 4) Debe ser estrictamente más reciente que el committed (si no, es obsoleto).
        try:
            if os.path.getmtime(tmp_path) <= os.path.getmtime(final_path):
                _remove_tmp_file(tmp_path, job_id, reason="obsoleto (no más reciente que el .json committed)")
                continue
        except OSError:
            continue

        # 5) Transición recuperable: fase RUNNING/RETRY SIN task_id (nunca llegó a ejecutarse).
        recoverable = any(
            ph.get("status") in (PHASE_RUNNING, PHASE_RETRY) and ph.get("task_id") is None
            for ph in tmp_job.get("phases", [])
        )
        if not recoverable:
            _remove_tmp_file(tmp_path, job_id, reason="sin transición interrumpida recuperable; temporal obsoleto")
            continue

        # Caso crítico (RUNNING + task_id=null): se descarta el intermedio; el .json committed ya
        # refleja la fase previa (p.ej. editor=PASS, quality_gate=PENDING) y el worker re-ejecuta la
        # transición (run_job -> enqueue_task) sin duplicar tareas ni marcar éxito falso.
        _remove_tmp_file(tmp_path, job_id, reason="transición RUNNING sin task_id; se reaprovecha el estado committed")

@dataclass
class PhaseResult:
    """Resultado que devuelve el ejecutor de una fase al orquestador."""

    ok: bool
    error: Optional[str] = None
    metrics: dict = field(default_factory=dict)
    module: Optional[str] = None
    task_id: Optional[int] = None
    progress: Optional[dict] = None
    docx_path: Optional[str] = None


# Tipo del ejecutor: recibe la fase y el job, devuelve un PhaseResult.
Executor = Callable[[dict, dict], PhaseResult]
# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------
def run_job(
    job: dict,
    store: BookJobStore,
    executor: Executor,
    emit: Optional[Callable[[str, dict], None]] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_step: float = DEFAULT_BACKOFF_STEP,
    sleep_fn: Optional[Callable[[float], None]] = time.sleep,
) -> dict:
    """Ejecuta las fases del job en orden hasta COMPLETED o FAILED definitivo.

    Retry con backoff exponencial (sin reintentos infinitos); no avanza fases
    tras un FAIL definitivo; emite eventos SOLO cuando realmente ocurren.
    """
    emit = emit or events.emit

    # Backend = fuente de verdad: re-lee el estado persistido para respetar
    # cancelaciones/recovery previos aunque el llamador tenga una referencia vieja.
    authoritative = store.load(job["job_id"]) if store is not None else None
    if authoritative is not None:
        job.update(authoritative)

    if job["status"] in JOB_TERMINAL:
        return job

    if job["status"] == JOB_PENDING:
        emit("job_started", {
            "job_id": job["job_id"], "book_id": job["book_id"], "status": JOB_RUNNING,
        })

    phases = job["phases"]
    start = 0
    for i, ph in enumerate(phases):
        if ph["status"] in (PHASE_PENDING, PHASE_RETRY, PHASE_RUNNING):
            start = i
            break

    for idx in range(start, len(phases)):
        phase = phases[idx]
        if job["status"] == JOB_CANCELLED:
            break
        if phase["status"] == PHASE_PASS:
            continue

        attempts = phase.get("attempts") or 0
        while attempts < max_attempts:
            attempts += 1
            phase["attempts"] = attempts
            phase["status"] = PHASE_RUNNING
            phase["started_at"] = _now()
            phase["completed_at"] = None
            phase["duration"] = None
            phase["error"] = None
            job["status"] = JOB_RUNNING
            job["current_phase"] = phase["id"]
            job["updated_at"] = _now()
            job["error"] = None
            store.save(job)

            emit("phase_started", {
                "job_id": job["job_id"], "book_id": job["book_id"],
                "phase": phase["id"], "label": phase["label"],
                "capability": phase["capability"], "attempt": attempts,
            })

            t0 = time.monotonic()
            try:
                result = executor(phase, job)
            except Exception as e:
                result = PhaseResult(ok=False, error=f"{type(e).__name__}: {e}")
            duration = round(time.monotonic() - t0, 3)
            phase["duration"] = duration
            phase["completed_at"] = _now()

            if result.progress is not None:
                emit("phase_progress", {
                    "job_id": job["job_id"], "book_id": job["book_id"],
                    "phase": phase["id"], "progress": result.progress,
                })

            if result.ok:
                phase["status"] = PHASE_PASS
                phase["metrics"] = result.metrics or {}
                phase["module"] = result.module
                phase["task_id"] = result.task_id
                phase["error"] = None
                if phase["id"] == "docx":
                    job["docx_path"] = (
                        result.docx_path
                        or result.metrics.get("docx_path")
                        or result.metrics.get("path")
                    )
                # ---- PROPAGACIÓN DE TÍTULOS REALES: Planner -> chapters.title
                # El planner genera títulos descriptivos ("Orígenes de Internet") pero la BD
                # conserva "Capítulo N". Tras planner PASS, actualizamos los títulos reales.
                if phase["id"] == "planner" and result.metrics:
                    planner_chapters = result.metrics.get("chapters") or []
                    if planner_chapters:
                        try:
                            from frontend import editorial as _ed
                            planner_title = result.metrics.get("title") or ""
                            planner_desc = result.metrics.get("description") or ""
                            for ch in planner_chapters:
                                number = ch.get("number")
                                title = ch.get("title")
                                if number and title:
                                    _ed.update_chapter_title(
                                        job["book_id"], number, title
                                    )
                                # Propagar secciones del planner a chapters.outline
                                sections = ch.get("sections")
                                if sections and number:
                                    _ed.update_chapter_outline(
                                        job["book_id"], number, sections
                                    )
                            # También propagar descripción del planner al libro
                            if planner_desc:
                                _ed.update_book_description(
                                    job["book_id"], planner_desc
                                )
                            # Propagar el título del libro si el planner generó uno mejor
                            if planner_title:
                                _ed.update_book_title(
                                    job["book_id"], planner_title
                                )
                        except Exception as e:
                            log(logger, logging.WARNING,
                                f"No se pudieron propagar títulos del planner: {e}")
                # ---- PROPAGACIÓN DE FUENTES REAL: Research -> job data.
                # Las sources reales producidas por Research viajan en el estado
                # del job para que writer/fact_check las consuman. Fuente de
                # verdad = result.metrics["sources"] (nunca inventado).
                if phase["id"] == "research" and result.metrics:
                    job.setdefault("data", {})["sources"] = (
                        result.metrics.get("sources") or []
                    )
                    job["data"]["source_count"] = (
                        result.metrics.get("source_count")
                        or len(job["data"]["sources"])
                    )
                    # ---- ASOCIACIÓN PERSISTENTE Research -> capítulos reales.
                    # Las fuentes globales del libro se asocian a los capítulos REALES
                    # vía SourceManager (única fuente de verdad de Chapter.sources),
                    # para que _build_book_dict -> Quality Gate las consuma. Nunca se
                    # inventan fuentes ni se cambia la propagación previa en job.data;
                    # add_source deduplica por url_hash y re-asocia por unión de ids.
                    try:
                        from frontend import editorial as _editorial
                        from core.book.source_manager import SourceManager as _SM
                        real_chapters = _editorial.get_chapters(job["book_id"]) or []
                        chapter_ids = [int(c["id"]) for c in real_chapters if c.get("id")]
                        for _source in (job["data"].get("sources") or []):
                            if not (_source or {}).get("url"):
                                continue
                            _SM.add_source(
                                url=_source["url"],
                                title=_source.get("title"),
                                source_type=_source.get("source_type") or "web",
                                relevance=int(_source.get("relevance") or 5),
                                chapter_ids=chapter_ids,
                            )
                    except Exception as e:
                        log(logger, logging.WARNING,
                            f"No se pudieron asociar fuentes de research a capítulos: {e}")
                job["updated_at"] = _now()
                store.save(job)
                emit("phase_completed", {
                    "job_id": job["job_id"], "book_id": job["book_id"],
                    "phase": phase["id"], "label": phase["label"],
                    "status": PHASE_PASS, "duration": duration,
                    "metrics": phase["metrics"], "module": phase["module"],
                    "task_id": phase["task_id"], "attempt": attempts,
                })
                break
            else:
                phase["error"] = result.error
                if attempts < max_attempts:
                    phase["status"] = PHASE_RETRY
                    job["updated_at"] = _now()
                    store.save(job)
                    emit("phase_failed", {
                        "job_id": job["job_id"], "book_id": job["book_id"],
                        "phase": phase["id"], "label": phase["label"],
                        "error": result.error, "attempt": attempts,
                        "max_attempts": max_attempts, "will_retry": True,
                    })
                    if sleep_fn:
                        sleep_fn(backoff_step * attempts)
                    continue
                phase["status"] = PHASE_FAIL
                job["status"] = JOB_FAILED
                job["error"] = result.error
                job["updated_at"] = _now()
                store.save(job)
                emit("phase_failed", {
                    "job_id": job["job_id"], "book_id": job["book_id"],
                    "phase": phase["id"], "label": phase["label"],
                    "error": result.error, "attempt": attempts,
                    "max_attempts": max_attempts, "will_retry": False,
                })
                emit("job_failed", {
                    "job_id": job["job_id"], "book_id": job["book_id"],
                    "error": result.error, "current_phase": phase["id"],
                    "status": JOB_FAILED,
                })
                return job

    if job["status"] not in JOB_TERMINAL:
        job["status"] = JOB_COMPLETED
        job["updated_at"] = _now()
        store.save(job)
        emit("job_completed", {
            "job_id": job["job_id"], "book_id": job["book_id"],
            "docx_path": job.get("docx_path"), "status": JOB_COMPLETED,
        })
    return job
def run_worker(
    store: BookJobStore,
    executor: Executor,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    interval: float = 1.0,
    stop_event: Optional[threading.Event] = None,
    sleep_fn: Optional[Callable[[float], None]] = time.sleep,
) -> None:
    """Bucle del worker: recupera huérfanos y procesa jobs activos en orden."""
    recover(store)
    while stop_event is None or not stop_event.is_set():
        job = store.next_job()
        if job is None:
            if sleep_fn:
                sleep_fn(interval)
            continue
        try:
            run_job(job, store, executor, max_attempts=max_attempts)
        except Exception:
            # Resiliencia: un error que escape de run_job NO debe matar silenciosamente el
            # hilo daemon. Se registra y el worker continúa re-evaluando el job desde el estado
            # committed (el orquestador nunca convierte una excepción en PASS ni crea tasks).
            logger.exception(
                "run_worker: error inesperado procesando el job; el worker continúa",
                extra={"job_id": job.get("job_id"), "book_id": job.get("book_id")},
            )
            # Pequeño backoff para evitar un bucle de reintentos inmediato / lavado de CPU.
            if sleep_fn:
                sleep_fn(interval)
            continue
        if sleep_fn:
            sleep_fn(0.2)


def start_worker_daemon(store: BookJobStore, executor: Executor, **kwargs: Any) -> threading.Thread:
    """Arranca el worker en un hilo daemon (para integrar en Flask más tarde)."""
    def _run() -> None:
        run_worker(store, executor, **kwargs)

    thread = threading.Thread(target=_run, daemon=True, name="autopilot-worker")
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Ejecutor de producción (reutiliza scheduler + editorial; sin duplicar lógica)
# ---------------------------------------------------------------------------
def build_phase_payload(
    phase: dict, book_id: int, data: Optional[dict] = None, chapter_id: Optional[int] = None
) -> dict:
    """Construye el payload real de una fase (reutiliza editorial.build_payload).

    QUALITY GATE (final_quality_control) consume un objeto ``book`` igual que el
    Document Builder; se deriva de ahí en lugar de duplicar la lógica.
    """
    from frontend import editorial

    data = data or {}
    if phase["id"] == "quality_gate":
        base = editorial.build_payload(book_id, "docx", data, chapter_id)
        qc_book = base["book"]
        # Los umbrales de capítulos se derivan del target REAL del libro (ya presente
        # en el book_dict); de no propagarlos el Quality Gate rellenaría los defaults
        # del schema (min=20/target=30/max=40) y marcaría FAIL falso a libros 1-19.
        target = int((qc_book or {}).get("target_chapters") or 1)
        return {
            "book": qc_book,
            "language": base.get("language", "es"),
            "min_chapters": target,
            "target_chapters": target,
            "max_chapters": max(target, 1),
        }
    return editorial.build_payload(book_id, phase["id"], data, chapter_id)
def _pick_module(modules: dict, cap_map: dict, capability: str) -> Optional[dict]:
    for mid in cap_map.get(capability, []):
        if mid in modules:
            return modules[mid]
    return None


def default_executor_factory(modules: dict, cap_map: dict, store=None) -> Executor:
    """Devuelve un ejecutor que reutiliza la ruta real de ``core.scheduler``.

    Encola la tarea, la procesa con ``_process_task`` (validación + timeout +
    eventos de tarea) y propaga resultado/error/task_id/métricas. El retry de
    fase lo controla el autopilot (max_attempts=1 en la tarea que lo envuelve).

    Por defecto procesa una fase como una única tarea (fases globales). Para las
    fases ``per-chapter`` (writer/fact_check/editor) itera los capítulos REALES
    del libro (BD) y ejecuta un módulo por capítulo con su ``chapter_id``,
    persistiendo el texto real en la BD antes de marcar cada sub-capítulo PASS.

    Si se provee ``store``, el subestado por capítulo se persiste tras cada
    capítulo (permite recuperar/progreso determinista sin repetir PASS).
    """
    def _run_single(phase: dict, job: dict, chapter_id=None) -> PhaseResult:
        """Ejecuta UN capítulo (o la fase global) vía scheduler real."""
        from core import scheduler as _sched
        from core import task_queue as _tq

        capability = phase["capability"]
        payload = build_phase_payload(phase, job["book_id"], job.get("data"), chapter_id)
        task_id = _tq.enqueue_task(capability, payload, max_attempts=1)
        module = _pick_module(modules, cap_map, capability)

        task = _tq.get_task(task_id)
        _sched._process_task(task, module, capability)
        task = _tq.get_task(task_id)

        module_id = task.get("module_id")
        if task["status"] == "done":
            result: dict = {}
            if task.get("result"):
                try:
                    result = json.loads(task["result"])
                except (json.JSONDecodeError, TypeError):
                    result = {}
            docx_path = None
            if phase["id"] == "docx":
                docx_path = result.get("docx_path") or result.get("path")
                # ----- INTEGRIDAD 8E.4: la fase docx solo es PASS si el DOCX
                # existe físicamente en disco. None / ruta inexistente /
                # directorio => FAIL (se reintenta con el mecanismo existente;
                # nunca se alcanza un falso COMPLETED sin entregable).
                if not docx_path or not os.path.isfile(docx_path):
                    return PhaseResult(
                        ok=False,
                        error="Document Builder no produjo un DOCX válido en disco",
                        metrics=result,
                        module=module_id,
                        task_id=task_id,
                    )

            # ---- HONESTIDAD: interpretar el gate REAL que devuelve el módulo.
            # No se marca PASS solo porque la task terminó técnicamente. El
            # Quality Gate y el Fact Check son autoridad y deben traducirse
            # a ok=True/False.
            gate_fail = None
            if phase["id"] == "quality_gate":
                ov = str(result.get("overall_status", ""))
                if ov.upper() == "FAIL":
                    gate_fail = "quality_gate#overall_status=FAIL"
            elif phase["id"] == "fact_check":
                st = str(result.get("status", "")).upper()
                qg = str(result.get("quality_gate", "")).upper()
                # Gate real de fact_check = quality_gate (integridad del proceso de
                # verificación). "status" es un hallazgo informativo de claims que el
                # módulo NO eleva a gate (quality_gate=FAIL eleva status a FAIL, no al
                # revés). autopilot solo aborta la fase si el GATE falla, manteniendo
                # st en el mensaje solo por trazabilidad.
                if qg == "FAIL":
                    gate_fail = f"fact_check#status={st} quality_gate={qg}"
            elif phase["id"] == "research":
                # La fase research debe traducir su gate REAL a ok=True/False igual
                # que quality_gate/fact_check: FAIL por status, quality_gate o por
                # no alcanzar el mínimo de fuentes exigido por el payload. Sin esto,
                # una research con 0 fuentes paseaba a outline/writer silenciosamente.
                st = str(result.get("status", "")).upper()
                qg = str(result.get("quality_gate", "")).upper()
                source_count = int(result.get("source_count") or 0)
                min_sources = int(payload.get("min_sources") or 3)
                if st == "FAIL" or qg == "FAIL" or source_count < min_sources:
                    gate_fail = (
                        f"research#status={st} quality_gate={qg} "
                        f"source_count={source_count} (min={min_sources})"
                    )
            if gate_fail:
                return PhaseResult(
                    ok=False,
                    error=gate_fail,
                    metrics=result,
                    module=module_id,
                    task_id=task_id,
                )

            return PhaseResult(
                ok=True,
                metrics=result,
                module=module_id,
                task_id=task_id,
                docx_path=docx_path,
            )
        return PhaseResult(
            ok=False,
            error=task.get("error") or "Sin detalle de error",
            task_id=task_id,
        )

    def _run_image_gen_split(phase: dict, job: dict, chapter_id=None) -> PhaseResult:
        """Fase image_gen con split por books.image_search_ratio (SearXNG vs generar).

        - ratio == 0.0 / None / ausente => passthrough EXACTO a ``_run_single``
          (comportamiento actual, sin encolar tasks extra).
        - ratio > 0.0 => reparte ``num_images`` en ``n_search`` (capability
          ``search_chapter_images``) y ``n_generate`` (capability
          ``generate_chapter_images``), encola las tasks con el mismo mecanismo
          que ``_run_single`` y fusiona los ``results`` de ambas en un único
          ``PhaseResult`` con el MISMO shape que hoy produce ``_run_single`` para
          image_gen (``metrics={"results": [...]}``), de modo que
          ``_persist_chapter`` no necesita cambios.
        - Si una task falla => ok=False con el error propagado (igual que
          ``_run_single``), sin dejar el capítulo a medias silenciosamente.
        """
        from core import scheduler as _sched
        from core import task_queue as _tq
        from frontend import editorial

        # Lectura del libro (mismo patrón que build_payload).
        book = editorial._get_book(job["book_id"])
        ratio = float((book or {}).get("image_search_ratio") or 0.0)
        if not ratio or ratio <= 0.0:
            return _run_single(phase, job, chapter_id=chapter_id)

        # Misma fuente de num_images que build_payload/generate_chapter_images.
        base = build_phase_payload(phase, job["book_id"], job.get("data"), chapter_id)
        _num = base.get("num_images")
        num_images = int(_num) if _num is not None else 3
        num_images = max(0, min(num_images, 20))
        n_search = max(0, min(round(num_images * ratio), num_images))
        n_generate = num_images - n_search

        # Evita tasks vacías; si no hay nada que repartir, comportamiento normal.
        if n_search <= 0 and n_generate <= 0:
            return _run_single(phase, job, chapter_id=chapter_id)

        tasks: list[tuple[str, dict]] = []
        if n_search > 0:
            tasks.append((
                "search_chapter_images",
                {
                    "book_id": base.get("book_id"),
                    "chapter_number": base.get("chapter_number"),
                    "chapter_title": base.get("chapter_title"),
                    "chapter_text": base.get("chapter_text", ""),
                    "num_images": n_search,
                    "language": base.get("language", "es"),
                },
            ))
        if n_generate > 0:
            gen_payload = dict(base)
            gen_payload["num_images"] = n_generate
            tasks.append(("generate_chapter_images", gen_payload))

        merged: dict[str, Any] = {"results": []}
        task_ids: list[int] = []
        module_id = None
        for capability, payload in tasks:
            task_id = _tq.enqueue_task(capability, payload, max_attempts=1)
            module = _pick_module(modules, cap_map, capability)
            task = _tq.get_task(task_id)
            _sched._process_task(task, module, capability)
            task = _tq.get_task(task_id)
            task_ids.append(task_id)
            if task["status"] != "done":
                return PhaseResult(
                    ok=False,
                    error=f"image_gen_split#{capability}: {task.get('error') or 'Sin detalle de error'}",
                    task_id=task_id,
                )
            try:
                res = json.loads(task.get("result") or "{}") if task.get("result") else {}
            except (json.JSONDecodeError, TypeError):
                res = {}
            merged["results"].extend(res.get("results") or [])
            module_id = task.get("module_id") or module_id

        return PhaseResult(
            ok=True,
            metrics=merged,
            module=module_id,
            task_id=task_ids[-1] if task_ids else None,
        )

    def _persist_chapter(phase: dict, chapter: dict, result: dict) -> None:
        """Persiste el resultado real de la fase en la BD ANTES de marcar PASS."""
        from frontend import editorial

        cid = chapter["id"]
        try:
            if phase["id"] in ("writer", "writer_en"):
                text = ""
                md = result.get("chapter_md_path")
                if md and os.path.isfile(md):
                    with open(md, "r", encoding="utf-8") as fh:
                        text = fh.read()
                if not text:
                    text = str((result.get("metadata") or {}).get("text", ""))
                field = "draft_es" if phase["id"] == "writer" else "draft_en"
                editorial.persist_chapter_result(chapter["book_id"], cid, field, text)
                # Poblar chapters.sources con las URLs REALES de SourceManager
                # (fuente de verdad única: sources.chapter_ids). Se ejecuta una única
                # vez por capítulo, aquí en el writer/writer_en (las fases posteriores
                # per-chapter como editor/image_gen no reescriben la columna).
                editorial.persist_chapter_sources(
                    chapter["book_id"], cid, editorial._chapter_source_urls(cid)
                )
            elif phase["id"] == "editor":
                text = str(result.get("edited_text") or "")
                editorial.persist_chapter_result(chapter["book_id"], cid, "edited_es", text)
            elif phase["id"] == "image_gen":
                # Persistir rutas de imágenes generadas a chapters.images
                results = result.get("results") or []
                image_paths = []
                for r in results:
                    if r.get("status") == "ok" and r.get("image_path"):
                        image_paths.append(r["image_path"])
                if image_paths:
                    editorial.persist_chapter_images(
                        chapter["book_id"], cid, image_paths
                    )
            # fact_check: no produce texto; las métricas de claims van en el subestado.
        except Exception as e:  # no bloquear el flujo por persistencia colateral
            log(logger, logging.WARNING,
                f"No se pudo persistir resultado de {phase['id']} en capítulo {cid}: {e}")
    def _execute_per_chapter(phase: dict, job: dict) -> PhaseResult:
        from frontend import editorial

        book_id = job["book_id"]
        chapters = editorial.get_chapters(book_id)

        # Subestado persistente (sobrevive retry/recovery)
        subs = phase.setdefault("subs", {})
        cap_subs = subs.setdefault("chapters", {})
        if not cap_subs:
            cap_subs.update({
                str(c["id"]): {"status": "PENDING", "attempts": 0}
                for c in chapters
            })
        subs["total"] = len(chapters)
        subs["done"] = 0
        agg_words = 0
        agg_module = None

        for chapter in chapters:
            cid = str(chapter["id"])
            csub = cap_subs.get(cid) or {"status": "PENDING", "attempts": 0}
            if csub.get("status") == "PASS":
                continue  # capítulo ya completado: terminal, no se re-ejecuta
            csub["attempts"] = csub.get("attempts", 0) + 1

            res = (
                _run_image_gen_split(phase, job, chapter["id"])
                if phase["id"] == "image_gen" else
                _run_single(phase, job, chapter_id=chapter["id"])
            )
            csub["module"] = res.module
            csub["duration"] = res.metrics.get("duration") if res.ok else None
            csub["error"] = res.error
            if res.ok:
                _persist_chapter(phase, chapter, res.metrics)
                csub["status"] = "PASS"
                csub["metrics"] = res.metrics
                agg_words += int(res.metrics.get("words", 0) or 0)
                agg_module = res.module
                subs["done"] = sum(1 for s in cap_subs.values() if s.get("status") == "PASS")
                if store is not None:
                    store.save(job)  # progreso determinista por capítulo
                continue
            # fallo en este capítulo: detener (el orquestador reintenta la fase;
            # los capítulos PASS no se repiten en el reintento).
            csub["status"] = "FAIL"
            subs["done"] = sum(1 for s in cap_subs.values() if s.get("status") == "PASS")
            return PhaseResult(
                ok=False,
                error=f"{phase['id']}#{cid}: {res.error}",
                metrics={"subs": {"total": len(chapters), "done": subs["done"]},
                         "words": agg_words, "per_chapter": True},
                module=agg_module,
            )

        subs["done"] = sum(1 for s in cap_subs.values() if s.get("status") == "PASS")
        return PhaseResult(
            ok=True,
            metrics={
                "subs": {"total": len(chapters), "done": subs["done"]},
                "words": agg_words,
                "per_chapter": True,
            },
            module=agg_module,
            progress={"done": subs["done"], "total": subs["total"]},
        )

    def executor(phase: dict, job: dict) -> PhaseResult:
        if phase["id"] in PER_CHAPTER_PHASES:
            return _execute_per_chapter(phase, job)
        return _run_single(phase, job)

    return executor
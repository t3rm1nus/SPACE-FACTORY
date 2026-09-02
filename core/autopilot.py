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


def _resolve_writer_capability(book: Optional[dict]) -> str:
    """Resuelve la capability de la fase writer según el idioma del libro.

    - book.languages contiene "en" (str —posiblemente separado por comas— o
      lista) → "write_chapter_en".
    - Cualquier otro caso ("es", vacío, None, libro ausente/ilegible) →
      "write_chapter_es" (comportamiento histórico intacto, regresión cero).
    """
    langs = (book or {}).get("languages")
    if isinstance(langs, str):
        candidates = [part.strip().lower() for part in langs.split(",")]
    elif isinstance(langs, (list, tuple)):
        candidates = [str(part).strip().lower() for part in langs]
    else:
        candidates = []
    if any(part.startswith("en") for part in candidates if part):
        return "write_chapter_en"
    return "write_chapter_es"


def _resolve_image_capabilities(book: Optional[dict]) -> tuple[str, str, str]:
    """Resuelve las capabilities de imagen según el idioma del libro (§17 #28).

    Mismo criterio que ``_resolve_writer_capability`` (books.languages contiene
    "en" → variantes nativas EN; cualquier otro caso → históricas, regresión
    cero). Devuelve ``(search_cap, generate_cap, language)``.
    """
    if _resolve_writer_capability(book) == "write_chapter_en":
        return ("search_chapter_images_en", "generate_chapter_images_en", "en")
    return ("search_chapter_images", "generate_chapter_images", "es")


def _resolve_book_languages(book: Optional[dict]) -> list[str]:
    """Alias compatible de ``frontend.editorial._resolve_book_languages``.

    §17 #21: la implementación canónica se MOVIO a frontend/editorial.py (junto
    a _is_english_language) para que book_planner/editorial puedan usarla sin
    dependencia circular. Este wrapper lazy preserva el nombre/import histórico
    en core.autopilot sin cambiar comportamiento.
    """
    from frontend.editorial import _resolve_book_languages as _resolve
    return _resolve(book)

# Reintentos por defecto por fase (no infinitos).
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_BACKOFF_STEP = 2.0

# Persistencia robusta de jobs: reintentos cortos y acotados (transitorios de filesystem).
SAVE_MAX_ATTEMPTS = 3
SAVE_BACKOFF_BASE = 0.05  # segundos; backoff lineal (base * intento)

# §17 #44 (fix book_80): tope de títulos de capítulo usados como queries
# adicionales en el fallback multi-query de research (coste acotado).
RESEARCH_MULTI_QUERY_MAX_CHAPTERS = 5


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
# §17 #36 Fase 3: reset desde fase de origen (retry inteligente)
# ---------------------------------------------------------------------------
# Cascada de dependencias: al resetear una fase de origen, TODA fase posterior
# que consuma su salida debe re-ejecutarse (si no, el retry vuelve a fallar
# por el mismo motivo: quality_gate vería datos stale).
PHASE_RESET_CASCADE = {
    "planner": ["research", "outline", "writer", "fact_check", "editor",
                "image_plan", "image_gen", "quality_gate", "docx"],
    "research": ["outline", "writer", "fact_check", "editor",
                 "image_plan", "image_gen", "quality_gate", "docx"],
    "writer": ["fact_check", "editor", "image_plan", "image_gen",
               "quality_gate", "docx"],
    "image_gen": ["quality_gate", "docx"],
    "docx": [],
}
# Fases GLOBALES (no per-chapter): si from_phase o cualquier fase de su
# cascada es global, chapter_number no es válido (la salida de una fase
# global alimenta a TODOS los capítulos; no se puede acotar a uno).
GLOBAL_PHASES = {"planner", "research", "outline", "quality_gate", "docx"}

# Alias de entrada: origin_phase tal como lo anota quality_control (§17 #36
# Fase 1) -> id real de fase del pipeline.
_ORIGIN_PHASE_ALIASES = {"book_planner": "planner"}


def _reset_phase_dict(ph: dict, subs_all: bool = False) -> None:
    """Resetea una fase a PENDING (mismo criterio que retry_job: L.328-349).

    ``subs_all=False`` conserva los subs PASS (solo resetea no-PASS);
    ``subs_all=True`` resetea TODOS los subs (reset completo de fase).
    """
    ph["status"] = PHASE_PENDING
    ph["attempts"] = 0
    ph["started_at"] = None
    ph["completed_at"] = None
    ph["duration"] = None
    ph["error"] = None
    ph["metrics"] = {}
    subs = ph.get("subs")
    if subs and isinstance(subs.get("chapters"), dict):
        for csub in subs["chapters"].values():
            if subs_all or csub.get("status") != PHASE_PASS:
                csub["status"] = PHASE_PENDING
                csub["attempts"] = 0
                csub["error"] = None
        subs["done"] = sum(
            1 for s in subs["chapters"].values() if s.get("status") == PHASE_PASS
        )
def reset_from_phase(
    job: dict,
    from_phase: str,
    chapter_number: Optional[int] = None,
) -> dict:
    """§17 #36 Fase 3: resetea un job desde una fase de origen y su cascada.

    A diferencia de ``retry_job`` (reset plano de fases no-PASS), retrocede
    sobre fases ya PASS cuando la causa real del FAIL de quality_gate reside
    en una fase anterior (p.ej. image_gen con imágenes insuficientes, §17 #30).

    Reglas:
    - ``from_phase`` se normaliza por alias ("book_planner" -> "planner").
    - Reset = {from_phase} ∪ PHASE_RESET_CASCADE[from_phase], tolerando fases
      ausentes en el job (skip silencioso, p.ej. image_plan con ratio=0).
    - Bilingüe: si la cascada incluye "writer", también se resetea "writer_en"
      cuando existe como fase del job (misma salida de texto, otro idioma).
    - ``chapter_number`` acota el reset a UN capítulo en las fases per-chapter
      del set. Solo es válido si NINGUNA fase del set es global (la salida de
      una fase global alimenta a todos los capítulos); en caso contrario,
      ValueError. El status de la FASE también baja a PENDING (si no,
      run_job no la re-ejecutaría al estar PASS).
    - Las fases ANTERIORES a from_phase quedan intactas.
    - NO persiste en store (patrón de retry_job/cancel_job: el caller decide
      el save).
    """
    from_phase = _ORIGIN_PHASE_ALIASES.get(from_phase, from_phase)
    if from_phase not in PHASE_RESET_CASCADE:
        raise ValueError(
            f"Fase de origen desconocida: '{from_phase}'. "
            f"Válidas: {sorted(PHASE_RESET_CASCADE)}"
        )
    if job["status"] in (JOB_PENDING, JOB_RUNNING):
        raise ValueError(
            f"El job '{job['job_id']}' está en estado {job['status']} (ya activo)"
        )

    phase_ids = {from_phase, *PHASE_RESET_CASCADE[from_phase]}
    # Bilingüe: writer cubre su variante EN si existe en el job.
    if "writer" in phase_ids:
        phase_ids.add("writer_en")

    if chapter_number is not None and from_phase in GLOBAL_PHASES:
        raise ValueError(
            f"La fase de origen '{from_phase}' es global: no se puede acotar "
            "el reset a un capítulo. Usa chapter_number=None (reset de libro "
            "completo)."
        )

    phases_by_id = {p["id"]: p for p in job["phases"]}

    # Resolución chapter_number -> chapter_id (los subs se indexan por el id
    # real de BD, p.ej. "538"). Si la BD no está disponible o no contiene el
    # número, fallback: el propio number como id (subs numéricos / tests).
    chapter_id: Optional[str] = None
    if chapter_number is not None:
        chapter_id = str(chapter_number)
        try:
            from frontend.editorial import get_chapters

            for ch in get_chapters(job["book_id"]) or []:
                if int(ch.get("number", -1)) == int(chapter_number):
                    chapter_id = str(ch.get("id"))
                    break
        except Exception:
            pass

    affected: list[str] = []
    subs_matched = False  # ¿algún sub per-chapter matcheó chapter_number?
    for pid in phase_ids:
        ph = phases_by_id.get(pid)
        if ph is None:
            continue  # fase ausente en este job (p.ej. image_plan ratio=0)
        if chapter_number is not None and (
            pid not in PER_CHAPTER_PHASES
            or not isinstance(ph.get("subs"), dict)
        ):
            # Fase global (o sin subs): se resetea COMPLETA aunque el reset
            # esté acotado a un capítulo (quality_gate/docx deben re-evaluar
            # el libro entero; no se puede acotar).
            _reset_phase_dict(ph, subs_all=False)
        elif chapter_number is not None:
            subs = ph.get("subs") or {}
            chapters = subs.get("chapters") if isinstance(subs, dict) else None
            if not (isinstance(chapters, dict) and chapter_id in chapters):
                continue  # fase per-chapter sin ese sub: nada que resetear
            chapters[chapter_id]["status"] = PHASE_PENDING
            chapters[chapter_id]["attempts"] = 0
            chapters[chapter_id]["error"] = None
            subs["done"] = sum(
                1 for s in chapters.values() if s.get("status") == PHASE_PASS
            )
            subs_matched = True
            # El status de la FASE también baja a PENDING (matiz del diseño:
            # run_job solo re-ejecuta fases != PASS aunque haya subs pendientes;
            # _reset_phase_dict con subs_all=False no toca los subs PASS).
            _reset_phase_dict(ph, subs_all=False)
        else:
            # §17 #36 Fase 3: image_gen puede estar PASS con déficit
            # (§17 #30 tolerancia de déficit ≤1 → capítulo PASS con
            # menos imágenes de las solicitadas). Resetear TODOS los subs
            # para forzar regeneración; el resto mantiene subs_all=False
            # (PASS = completo y correcto en writer/editor/fact_check).
            _reset_phase_dict(
                ph,
                subs_all=(pid == from_phase and from_phase == "image_gen"),
            )
        affected.append(pid)

    if chapter_number is not None and not subs_matched:
        raise ValueError(
            f"El capítulo {chapter_number} no existe en los subs de ninguna "
            "fase per-chapter afectada."
        )

    job["status"] = JOB_PENDING
    job["error"] = None
    job["updated_at"] = _now()
    log(
        logger,
        logging.INFO,
        "Reset desde fase de origen (§17 #36 Fase 3)",
        job_id=job["job_id"],
        book_id=job.get("book_id"),
        from_phase=from_phase,
        chapter_number=chapter_number,
        affected=affected,
    )
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
    # §17 #35 F2: estado de calidad persistible para fases de verificación
    # (fact_check). "PASS_WITH_WARNING" = el gate no bloquea pero hay claims
    # accuracy_partial degradadas que el Document Builder debe marcar.
    quality_status: Optional[str] = None


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
                    planner_title_en = result.metrics.get("title_en") or ""
                    planner_desc_en = result.metrics.get("description_en") or ""
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
                                # §17 #21 (Opción A): persistir traducciones EN
                                # si el planner bilingüe las generó (None/vacío
                                # = no escribir → fallback ES intacto).
                                title_en = ch.get("title_en")
                                if number and title_en:
                                    _ed.update_chapter_title_en(
                                        job["book_id"], number, title_en
                                    )
                                outline_en = ch.get("outline_en")
                                if number and outline_en:
                                    _ed.update_chapter_outline_en(
                                        job["book_id"], number, outline_en
                                        if isinstance(outline_en, list)
                                        else json.loads(outline_en)
                                    )
                            # También propagar descripción del planner al libro
                            if planner_desc:
                                _ed.update_book_description(
                                    job["book_id"], planner_desc
                                )
                            # §17 #38 (completo): el título del planner NUNCA
                            # sobrescribe books.title — "Título" es obligatorio
                            # en el formulario (create_book rechaza title vacío,
                            # §13), así que el usuario siempre escribió uno y
                            # el planner no debe reemplazarlo ni en fallback
                            # (title==idea) ni con LLM exitoso. El planner sigue
                            # propagando description (L.809-812) y el título EN
                            # (books.title_en, que el usuario no rellena).
                        except Exception as e:
                            log(logger, logging.WARNING,
                                f"No se pudieron propagar títulos del planner: {e}")
                    # §17 #21 (Opción A): título/descripción EN del libro.
                    if planner_title_en or planner_desc_en:
                        try:
                            from frontend import editorial as _ed
                            if planner_title_en:
                                _ed.update_book_title_en(job["book_id"], planner_title_en)
                            if planner_desc_en:
                                _ed.update_book_description_en(job["book_id"], planner_desc_en)
                        except Exception as e:
                            log(logger, logging.WARNING,
                                f"No se pudieron propagar títulos EN del planner: {e}")
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
                        # ---- Anti-reciclaje de fuentes ajenas al tema (2026-08-22):
                        # una fuente YA persistida para OTRO libro solo se re-asocia
                        # si sigue anclada al tema de ESTE libro (mismo criterio que
                        # el filtro PASO 4 de research: _has_anchor_keyword con el
                        # topic real del libro). Fuentes nuevas (primera inserción) o
                        # ya pertenecientes SOLO a este libro no se re-validan.
                        from modules.research.main import _has_anchor_keyword as _anchor
                        _book = _editorial.load_book(job["book_id"]) or {}
                        # load_book anida el libro bajo "book"; el data del job puede
                        # traer topic explícito (mismo fallback que build_payload).
                        _topic = (
                            (job.get("data") or {}).get("topic")
                            or (_book.get("book") or {}).get("title")
                        )
                        for _source in (job["data"].get("sources") or []):
                            if not (_source or {}).get("url"):
                                continue
                            _existing = _SM.get_source_by_url(_source["url"])
                            if _existing:
                                _other_books = [
                                    b for b in _SM.book_ids_for_source(_existing["id"])
                                    if b != job["book_id"]
                                ]
                                if _other_books and _topic:
                                    _cand = {
                                        "title": _existing.get("title"),
                                        "snippet": str(_existing.get("notes") or "").replace(
                                            "content_snippet=", "", 1),
                                        "content": "",
                                    }
                                    if not _anchor(_topic, _cand):
                                        log(logger, logging.WARNING,
                                            f"Fuente {_existing['id']} "
                                            f"({_existing.get('title')!r}) NO asociada a "
                                            f"book {job['book_id']}: sin anclaje temático "
                                            f"(_has_anchor_keyword=False, libros previos "
                                            f"{_other_books})")
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
                # Mismo patrón que la rama de éxito: aunque la fase falle por gate
                # (quality_gate/fact_check/research), se conserva el resultado real
                # (p.ej. overall_status) para trazabilidad. Desglose perdido: libro 32.
                phase["metrics"] = result.metrics or {}
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
    phase: dict, book_id: int, data: Optional[dict] = None, chapter_id: Optional[int] = None,
    language: Optional[str] = None,
) -> dict:
    """Construye el payload real de una fase (reutiliza editorial.build_payload).

    ``language`` (opcional) fuerza el idioma activo en las fases de TEXTO del caso
    multidioma; si es None, build_payload deriva como antes (idioma única).

    QUALITY GATE (final_quality_control) consume un objeto ``book`` igual que el
    Document Builder; se deriva de ahí en lugar de duplicar la lógica.
    """
    from frontend import editorial

    data = data or {}
    # kwargs de build_payload: solo se agrega `language` cuando se fuerza un idioma
    # (caso multidioma). Cuando language is None no se pasa el kwarg, conservando la
    # compatibilidad con llamadores/firmas que aceptan solo la firma histórica.
    _bp_kwargs: dict[str, Any] = {}
    if language is not None:
        _bp_kwargs["language"] = language
    if phase["id"] == "quality_gate":
        base = editorial.build_payload(book_id, "docx", data, chapter_id, **_bp_kwargs)
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
    return editorial.build_payload(book_id, phase["id"], data, chapter_id, **_bp_kwargs)
def _pick_module(modules: dict, cap_map: dict, capability: str) -> Optional[dict]:
    for mid in cap_map.get(capability, []):
        if mid in modules:
            return modules[mid]
    return None


# §17 #40 (book_76): headings canónicos que el fallback determinista del
# planner usa como outline genérico (Introduction/Development/Conclusion y su
# versión ES). NO son temas reales de un capítulo: usarlos como query de
# búsqueda de imágenes devuelve pseudo-resultados genéricos (book_76 cap.1 EN
# resuelto como "Introduction"). No bloquean títulos legítimos que los
# contengan (p.ej. "The Conclusion of the Console War") porque la comparación
# es EXACTA tras strip, case-insensitive.
_GENERIC_OUTLINE_HEADINGS = {
    "introducción", "introduccion", "desarrollo", "conclusión", "conclusion",
    "introduction", "development",
}


def _resolve_chapter_search_topic(chapter, img_lang: str = "es") -> str:
    """§17 #39/#40: tema de búsqueda de imágenes de un capítulo, diferenciado.

    Preferencia:
    1. Primer heading NO genérico del outline del capítulo (outline_en para EN,
       outline para ES), que sea usable (>=3 caracteres tras strip).
    2. Si todos los headings son canónicos genéricos o no hay ninguno usable,
       el título real del capítulo (title_en para EN, title para ES).

    Devuelve "" si no hay nada usable (la query histórica por chapter_title se
    usa en image_search). Fail-safe ante outline corrupto.
    """
    if not chapter:
        return ""
    raw = (
        (chapter.get("outline_en") if img_lang == "en" else chapter.get("outline"))
        or chapter.get("outline")
        or ""
    )
    sections: list = []
    if str(raw).strip():
        try:
            parsed = json.loads(raw)
            sections = parsed if isinstance(parsed, list) else (
                parsed.get("sections", []) if isinstance(parsed, dict) else []
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            sections = []
    for sec in sections:
        head = str((sec or {}).get("heading") or "").strip()
        if not head or len(head) < 3:
            continue
        if head.lower() in _GENERIC_OUTLINE_HEADINGS:
            continue
        return head[:2000]
    # Fallback al título real del capítulo (title_en para EN), comportamiento
    # histórico anterior al fix §17 #39 (la query caía a chapter_title).
    title = str(
        (chapter.get("title_en") if img_lang == "en" else chapter.get("title"))
        or ""
    ).strip()
    return title[:2000]


def _combine_chapter_search_topic(book_topic: str, chapter_topic: str) -> str:
    """§17 #48 (book_84): query de imagen = tema del LIBRO + heading del capítulo.

    El heading en solitario (ej. "Minimalismo") es demasiado ambiguo para la
    búsqueda web; combinado con el topic del libro (ej. "Estilos de decoración
    de interiores") obtiene candidatos relevantes. Si el heading ya contiene al
    topic (o viceversa), se devuelve el más largo sin duplicar. Degradación:
    sin book_topic → heading solo; sin heading → book_topic solo (histórico).
    La variante EN usa topic_en (§17 #29 fail-open intacto: topic_en vacío
    degrada al heading sin bloquear por idioma).
    """
    head = str(chapter_topic or "").strip()
    base = str(book_topic or "").strip()
    if not head:
        return base[:2000]
    if not base or head.lower() in base.lower() or base.lower() in head.lower():
        return head[:2000]
    return f"{base} {head}"[:2000]


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
    def _run_single(phase: dict, job: dict, chapter_id=None, language: Optional[str] = None) -> PhaseResult:
        """Ejecuta UN capítulo (o la fase global) vía scheduler real.

        ``language`` (opcional) fuerza el idioma activo de la fase (caso
        multidioma: writer/fact_check/editor/docx). Si es None, se deriva como
        antes de ``books.languages`` (idioma único histórico).
        """
        from core import scheduler as _sched
        from core import task_queue as _tq

        capability = phase["capability"]
        if phase["id"] == "writer":
            # Resolución dinámica por idioma del libro (§20 tarea 6):
            # Si el bucle multidioma fuerza un idioma concreto, se usa ese;
            # si no, se deriva de books.languages (regresión cero en idioma único).
            if language is not None:
                capability = "write_chapter_en" if language == "en" else "write_chapter_es"
            else:
                from frontend import editorial as _editorial

                try:
                    _book = _editorial._get_book(job["book_id"])
                except Exception:
                    _book = None
                capability = _resolve_writer_capability(_book)
            language = language or ("en" if capability == "write_chapter_en" else "es")
        elif phase["id"] == "image_gen":
            # §17 #28: resolución dinámica por idioma del libro — mismas
            # capabilities nativas ES/EN que el Paso 1 (patrón §20 tarea 6).
            if language is None:
                from frontend import editorial as _editorial

                try:
                    _book = _editorial._get_book(job["book_id"])
                except Exception:
                    _book = None
                _, capability, language = _resolve_image_capabilities(_book)
        payload = build_phase_payload(phase, job["book_id"], job.get("data"), chapter_id, language=language)
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
            quality_status = None  # §17 #35 F2: solo fact_check lo informatiza
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
                    # §17 #35 F2: gate DIFERENCIADO por error_type (F1). Solo se
                    # atenúa a PASS_WITH_WARNING cuando hay EVIDENCIA explícita de
                    # que el FAIL es accuracy_partial (ERROR subjetivo degradado por
                    # la pasada de consistencia) y no hay fabricación estructural.
                    # Sin issues (o con error_type desconocido) se bloquea: fail-safe.
                    issues = [i for i in (result.get("issues") or []) if isinstance(i, dict)]
                    types = {str(i.get("error_type", "")) for i in issues}
                    if "fabrication_structural" in types or not types:
                        gate_fail = f"fact_check#status={st} quality_gate={qg} error_type={','.join(sorted(types)) or 'unknown'}"
                    else:
                        quality_status = "PASS_WITH_WARNING"
                        log(
                            logger,
                            logging.INFO,
                            "fact_check FAIL por accuracy_partial: no bloquea el pipeline",
                            status=st,
                            quality_gate=qg,
                            quality_status=quality_status,
                        )
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
                quality_status=quality_status,
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

        # Topic del libro (mismo fallback que build_payload y el anti-reciclaje de
        # fuentes): job.data.topic si lo hay, si no el título del libro. Se propaga
        # a image_search para el filtro de relevancia temática (§17 #11).
        topic = (job.get("data") or {}).get("topic") or (book or {}).get("title")

        # §17 #28: capabilities nativas por idioma del libro (mismo criterio que
        # _resolve_writer_capability / §20 tarea 6). En EN el filtro de anclaje
        # nativo de image_search necesita topic_en/title_en en el payload.
        search_cap, gen_cap, img_lang = _resolve_image_capabilities(book)
        # §17 #28 fix 2026-08-26 (book_67): SIN fallback al topic/título en
        # español — keywords ES contra slugs/títulos EN nunca matchean y
        # descartaban candidatos on-topic en masa. Sin nativo EN => "" y
        # el filtro de anclaje queda en fail-open (no filtra).
        topic_en = (
            (job.get("data") or {}).get("topic_en")
            or (book or {}).get("title_en")
            or ""
        )

        # Misma fuente de num_images que build_payload/generate_chapter_images.
        # §17 #28: con img_lang="en", build_payload resuelve chapter_title a
        # chapters.title_en (necesario para payload title_en del anclaje nativo).
        base = build_phase_payload(
            phase, job["book_id"], job.get("data"), chapter_id, language=img_lang
        )
        _num = base.get("num_images")
        num_images = int(_num) if _num is not None else 3
        num_images = max(0, min(num_images, 20))

        # §17 #30/#40 (P1b, book_72/book_76): query diferenciada por capítulo.
        # Preferimos el primer heading REAL del outline del capítulo (EN nativo
        # si aplica); si es un heading canónico genérico del fallback del planner
        # ("Introduction"/"Development"/"Conclusion"/es), NO se usa como query y
        # se cae al título real del capítulo (title_en/title), como ANTES del fix
        # §17 #39. Fail-safe: sin tema usable, el campo queda vacío y la query
        # histórica (chapter_title) se usa en image_search.
        try:
            _chapter = editorial._get_chapter(job["book_id"], chapter_id)
        except Exception:  # noqa: BLE001
            _chapter = None
        # §17 #48 (book_84): la query combina el tema del libro (topic para ES,
        # topic_en para EN — fail-open §17 #29 intacto si topic_en está vacío)
        # con el heading específico del capítulo resuelto por §17 #39/#40.
        chapter_book_topic = topic_en if img_lang == "en" else topic
        chapter_search_topic = _combine_chapter_search_topic(
            chapter_book_topic, _resolve_chapter_search_topic(_chapter, img_lang)
        )
        n_search = max(0, min(round(num_images * ratio), num_images))
        n_generate = num_images - n_search

        # Evita tasks vacías; si no hay nada que repartir, comportamiento normal.
        if n_search <= 0 and n_generate <= 0:
            return _run_single(phase, job, chapter_id=chapter_id)

        tasks: list[tuple[str, dict]] = []
        if n_search > 0:
            search_payload = {
                "book_id": base.get("book_id"),
                "chapter_number": base.get("chapter_number"),
                "chapter_title": base.get("chapter_title"),
                "chapter_text": base.get("chapter_text", ""),
                "num_images": n_search,
                "language": img_lang,
                "topic": topic,
                "chapter_search_topic": chapter_search_topic,
            }
            if img_lang == "en":
                # §17 #28: keywords EN para el anclaje nativo (el capítulo/
                # título ya llega resuelto a EN desde build_payload).
                search_payload["topic_en"] = str(topic_en or "")[:2000]
                search_payload["title_en"] = str(base.get("chapter_title") or "")[:500]
            tasks.append((search_cap, search_payload))
        if n_generate > 0:
            gen_payload = dict(base)
            gen_payload["num_images"] = n_generate
            gen_payload["language"] = img_lang
            tasks.append((gen_cap, gen_payload))

        merged: dict[str, Any] = {"results": []}
        task_ids: list[int] = []
        module_id = None

        def _run_img_task(capability: str, payload: dict) -> None:
            """Encola+ejecuta una task de imagen y fusiona sus ``results`` en
            ``merged``. Reutilizado por image_gen_split (search+generate) y por
            la ronda de compensación de déficit (evita duplicar el bloque)."""
            nonlocal module_id
            task_id = _tq.enqueue_task(capability, payload, max_attempts=1)
            module = _pick_module(modules, cap_map, capability)
            task = _tq.get_task(task_id)
            _sched._process_task(task, module, capability)
            task = _tq.get_task(task_id)
            task_ids.append(task_id)
            if task["status"] != "done":
                task_ids.pop()
                raise _TaskFailed(capability, task.get("error") or "Sin detalle de error", task_id)
            try:
                res = json.loads(task.get("result") or "{}") if task.get("result") else {}
            except (json.JSONDecodeError, TypeError):
                res = {}
            merged["results"].extend(res.get("results") or [])
            module_id = task.get("module_id") or module_id

        class _TaskFailed(Exception):
            """Fallo real de ejecución de una task de imagen (no solo pocas ok)."""
            def __init__(self, capability, error, task_id):
                self.capability = capability
                self.error = error
                self.task_id = task_id

        def _dedupe_by_path(results: list[dict]) -> list[dict]:
            """Conserva la PRIMERA ocurrencia de cada ruta de imagen única.

            Deduplica por el campo de ruta real (``image_path``) que producen
            ``generate_chapter_images``/``search_chapter_images``. Una misma ruta
            puede aparecer dos veces en ``merged['results']`` cuando la ronda de
            compensación acaba reutilizando metadata ya existente en disco
            (``skip_existing=True``) o por cualquier otra causa previa; aquí se
            normaliza para que ``ok_count``/``shortfall`` no cuenten la misma
            imagen dos veces y para que ``chapters.images`` no guarde duplicados.
            """
            seen: set[str] = set()
            out: list[dict] = []
            for r in results:
                path = r.get("image_path")
                key = path if isinstance(path, str) else id(r)
                if key in seen:
                    continue
                seen.add(key)
                out.append(r)
            return out

        try:
            for capability, payload in tasks:
                _run_img_task(capability, payload)

            # ---- Dedup 1: normaliza las rutas fusionadas de search+generate
            # ANTES de calcular ok_count/shortfall, para que una misma ruta ya
            # repetida en el merge no cuente como 2 imágenes reales.
            merged["results"] = _dedupe_by_path(merged["results"] or [])

            # ---- §17 #30 (rediseño): SIN compensación con IA. image_search
            # ahora pagina (pageno de SearXNG) hasta completar el cupo o
            # agotar su presupuesto (IMAGE_SEARCH_TOTAL_TIME_BUDGET /
            # IMAGE_SEARCH_MAX_PAGES). Si aún así queda shortfall (no hay más
            # imágenes válidas en la web para el tema), el capítulo se queda
            # con las imágenes que consiguió. Se conserva solo el WARNING de
            # diagnóstico; NO se encola ninguna generación extra.
            ok_count = sum(1 for r in merged["results"] if r.get("status") == "ok")
            shortfall = num_images - ok_count
            if shortfall > 0:
                logger.warning(
                    "[fix ratio] image_gen_split: shortfall=%d tras búsqueda "
                    "paginada (ratio=%.2f): el capítulo queda con %d de %d "
                    "imágenes solicitadas, SIN compensación con IA (§17 #30)",
                    shortfall, ratio, ok_count, num_images,
                )
        except _TaskFailed as ex:
            return PhaseResult(
                ok=False,
                error=f"image_gen_split#{ex.capability}: {ex.error}",
                task_id=ex.task_id,
            )

        return PhaseResult(
            ok=True,
            metrics=merged,
            module=module_id,
            task_id=task_ids[-1] if task_ids else None,
        )

    def _persist_chapter(phase: dict, chapter: dict, result: dict, language: Optional[str] = None) -> None:
        """Persiste el resultado real de la fase en la BD ANTES de marcar PASS.

        ``language`` (opcional) fuerza el campo destino (draft_es/draft_en,
        edited_es/edited_en) en el caso multidioma. Si es None, se deriva como
        antes de books.languages (idioma único, regresión cero).
        """
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
                if phase["id"] == "writer_en":
                    field = "draft_en"
                elif language is not None:
                    field = "draft_en" if language == "en" else "draft_es"
                else:  # phase["id"] == "writer": campo según idioma real del libro
                    try:
                        _book = editorial._get_book(chapter["book_id"])
                    except Exception:
                        _book = None
                    field = (
                        "draft_en"
                        if _resolve_writer_capability(_book) == "write_chapter_en"
                        else "draft_es"
                    )
                editorial.persist_chapter_result(chapter["book_id"], cid, field, text)
                # Poblar chapters.sources con las URLs REALES de SourceManager
                # (fuente de verdad única: sources.chapter_ids). Se ejecuta una única
                # vez por capítulo, aquí en el writer/writer_en (las fases posteriores
                # per-chapter como editor/image_gen no reescriben la columna). En el
                # caso multidioma se vuelve a persistir de forma idempotente.
                editorial.persist_chapter_sources(
                    chapter["book_id"], cid, editorial._chapter_source_urls(cid)
                )
            elif phase["id"] == "editor":
                text = str(result.get("edited_text") or "")
                # Campo según idioma activo de la fase (§20 tarea 6): "en" → edited_en;
                # resto → edited_es. Si language es None, deriva del libro (histórico).
                if language is not None:
                    field = "edited_en" if language == "en" else "edited_es"
                else:
                    try:
                        _book_ed = editorial._get_book(chapter["book_id"])
                    except Exception:
                        _book_ed = None
                    field = (
                        "edited_en"
                        if _resolve_writer_capability(_book_ed) == "write_chapter_en"
                        else "edited_es"
                    )
                editorial.persist_chapter_result(chapter["book_id"], cid, field, text)
            elif phase["id"] == "image_gen":
                # Persistir rutas de imágenes generadas a chapters.images
                results = result.get("results") or []
                image_paths = []
                for r in results:
                    if r.get("status") == "ok" and r.get("image_path"):
                        image_paths.append(r["image_path"])
                if image_paths:
                    # §17 #30/#31 (fix acumulación): image_gen se re-ejecuta desde
                    # cero en cada reset (fase regenera sus subs). El merge de
                    # persist_chapter_images (§17 #31) está pensado para MÚLTIPLES
                    # llamadas DENTRO de una misma ejecución (compensación IA),
                    # pero tras §17 #30 esa compensación ya no existe: hay UNA
                    # única llamada por capítulo por ejecución. Sin overwrite, cada
                    # reset ACUMULABA imágenes de ejecuciones previas en
                    # chapters.images (book_72: 127-140/capítulo). Al ser la
                    # primera y única llamada de la ejecución, overwrite=True
                    # deja chapters.images EXACTAMENTE con las de esta pasada.
                    editorial.persist_chapter_images(
                        chapter["book_id"], cid, image_paths, overwrite=True
                    )
            # fact_check: no produce texto; las métricas de claims van en el subestado.
        except Exception as e:  # no bloquear el flujo por persistencia colateral
            log(logger, logging.WARNING,
                f"No se pudo persistir resultado de {phase['id']} en capítulo {cid}: {e}")
    def _execute_per_chapter(phase: dict, job: dict) -> PhaseResult:
        from frontend import editorial

        book_id = job["book_id"]
        chapters = editorial.get_chapters(book_id)

        # Idiomas para las fases de TEXTO (writer/fact_check/editor). En el caso
        # multidioma (languages="es,en") cada idioma ejecuta el módulo una vez por
        # capítulo y persiste su columna es/en. image_plan/image_gen quedan fuera
        # (imágenes compartidas); research/docx/quality_gate son globales.
        text_phase = phase["id"] in ("writer", "fact_check", "editor")
        try:
            _book = editorial._get_book(book_id)
        except Exception:
            _book = None
        langs = _resolve_book_languages(_book) if text_phase else ["es"]

        # Subestado persistente (sobrevive retry/recovery). Para multidioma, la clave
        # del capítulo guarda un dict interno por idioma (los sub-idiomas se marcan
        # individualmente y solo se considera el capítulo PASS cuando todos lo están).
        subs = phase.setdefault("subs", {})
        cap_subs = subs.setdefault("chapters", {})
        if not cap_subs:
            if text_phase and len(langs) > 1:
                cap_subs.update({
                    str(c["id"]): {
                        "status": "PENDING",
                        "attempts": 0,
                        "languages": {lang: {"status": "PENDING"} for lang in langs},
                    }
                    for c in chapters
                })
            else:
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
            csub = cap_subs.get(cid) or {"status": "PENDING", "attempts": 0, "languages": None}
            if csub.get("status") == "PASS":
                continue  # capítulo ya completado: terminal, no se re-ejecuta
            csub["attempts"] = csub.get("attempts", 0) + 1

            def _run_chapter_unit(unit_lang: Optional[str]) -> PhaseResult:
                """Ejecuta una unidad (idioma) del capítulo: la fase real o image_gen."""
                if phase["id"] == "image_gen":
                    return _run_image_gen_split(phase, job, chapter["id"])
                return _run_single(phase, job, chapter_id=chapter["id"], language=unit_lang)

            # En multidioma, ejecutar cada idioma secuencialmente; si alguno falla,
            # se detiene el capítulo (orquestador reintenta la fase; los sub-chunks PASS
            # por idioma no se re-ejecutan gracias al sub-languages del subestado).
            multi = text_phase and len(langs) > 1
            langs_sub = langs if multi else [None]
            ok_all = True
            first_err = None
            last_metrics = None
            for ulang in langs_sub:
                if multi:
                    lang_sub = (csub.setdefault("languages", {})
                                .setdefault(ulang, {"status": "PENDING"}))
                    if lang_sub.get("status") == "PASS":
                        continue  # ese idioma ya completado (retry)
                    lang_sub["attempts"] = lang_sub.get("attempts", 0) + 1

                res = _run_chapter_unit(ulang)
                csub["module"] = res.module
                csub["duration"] = res.metrics.get("duration") if res.ok else None
                csub["error"] = res.error
                if res.ok:
                    if multi:
                        lang_sub["status"] = "PASS"
                        lang_sub["metrics"] = res.metrics
                    _persist_chapter(phase, chapter, res.metrics, language=ulang)
                    # §17 #35 F2.3b: persistir quality_status en BD SOLO para
                    # fact_check cuando la unidad informatiza un estado (p.ej.
                    # PASS_WITH_WARNING). Nunca bloquea el flujo si falla.
                    if phase["id"] == "fact_check" and res.quality_status:
                        try:
                            editorial.set_chapter_quality_status(
                                chapter["book_id"],
                                chapter["number"],
                                ulang,
                                res.quality_status,
                            )
                        except Exception as e_qs:
                            log(logger, logging.WARNING,
                                f"No se pudo persistir quality_status del capítulo "
                                f"{chapter['id']}: {e_qs}")
                    agg_words += int(res.metrics.get("words", 0) or 0)
                    agg_module = res.module
                    last_metrics = res.metrics
                else:
                    if multi:
                        lang_sub["status"] = "FAIL"
                    first_err = res.error
                    ok_all = False
                    break

            if ok_all:
                csub["status"] = "PASS"
                csub["metrics"] = last_metrics if last_metrics is not None else {}
                subs["done"] = sum(1 for s in cap_subs.values() if s.get("status") == "PASS")
                if store is not None:
                    store.save(job)  # progreso determinista por capítulo
                continue
            # fallo en este capítulo: detener (el orquestador reintenta la fase;
            # los capítulos/sub-idiomas PASS no se repiten en el reintento).
            csub["status"] = "FAIL"
            subs["done"] = sum(1 for s in cap_subs.values() if s.get("status") == "PASS")
            return PhaseResult(
                ok=False,
                error=f"{phase['id']}#{cid}: {first_err}",
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

    def _run_docx(phase: dict, job: dict) -> PhaseResult:
        """Fase DOCX (global): genera UN DOCX por idioma del libro.

        - Idiomas únicos ("es" o "en") → comportamiento idéntico a hoy (un solo
          build_book_docx con el idioma del libro).
        - ``languages="es,en"`` → invoca build_book_docx DOS veces (una por idioma),
          con el Mismo book_dict pero distinto ``language``/columnas fuente. El
          naming actual (book_{id}_{lang}.docx) ya produce ficheros separados.
        El PhaseResult es ok=True solo si TODOS los DOCX existen; `docx_path`
        apunta al del primer idioma y `metrics["docx_paths"]` lista todos.
        """
        from frontend import editorial

        langs = _resolve_book_languages(editorial._get_book(job["book_id"]))
        paths: list[str] = []
        module_id = None
        task_ids: list = []
        for lang in langs:
            res = _run_single(phase, job, language=lang)
            if not res.ok:
                return res
            p = res.docx_path or (res.metrics or {}).get("docx_path")
            if not (p and os.path.isfile(p)):
                return PhaseResult(
                    ok=False,
                    error=f"Document Builder no produjo un DOCX válido para {lang}: {p}",
                    metrics=res.metrics, module=res.module, task_id=res.task_id,
                )
            paths.append(p)
            module_id = res.module
            task_id = res.task_id
        return PhaseResult(
            ok=True,
            metrics={**({"docx_paths": paths} if len(paths) > 1 else {}),
                     "docx_path": paths[0] if paths else None},
            module=module_id,
            task_id=task_id if task_id else None,
            docx_path=paths[0] if paths else None,
        )

    def _run_research_with_chapter_fallback(phase: dict, job: dict, language: str) -> PhaseResult:
        """Si la pasada normal de research falla por source_count insuficiente,
        reintenta con títulos de capítulo como queries adicionales (fix book_80,
        §17 #44) antes de rendirse. Regresión cero: si la pasada normal ya pasa,
        esta función no hace nada distinto de _run_single.
        """
        from frontend import editorial

        res = _run_single(phase, job, language=language)
        if res.ok:
            return res
        if not (
            isinstance(res.error, str)
            and res.error.startswith("research#")
            and "source_count=" in res.error
        ):
            return res  # otro tipo de fallo: no se toca, comportamiento histórico

        data = job.setdefault("data", {})
        min_sources = int(data.get("min_sources") or 3)
        original_query = data.get("query")
        original_min_sources = data.get("min_sources")

        try:
            book = editorial._get_book(job["book_id"])
        except Exception:
            book = None
        try:
            chapters = editorial.get_chapters(job["book_id"])
        except Exception:
            chapters = []

        title_field = "title_en" if language == "en" else "title"
        seen_titles: set[str] = set()
        chapter_titles: list[str] = []
        for ch in chapters:
            t = (ch.get(title_field) or ch.get("title") or "").strip()
            if t and t not in seen_titles:
                seen_titles.add(t)
                chapter_titles.append(t)
            if len(chapter_titles) >= RESEARCH_MULTI_QUERY_MAX_CHAPTERS:
                break

        # §17 #49 (fix book_85): si todos los capítulos comparten el mismo
        # título (planner en fallback), los títulos no aportan queries
        # distintivas. Segunda fuente: nombres propios extraídos de
        # book.description (regex simple, sin NLP), respetando el MISMO tope
        # total RESEARCH_MULTI_QUERY_MAX_CHAPTERS combinado con los títulos.
        description_entities: list[str] = []
        try:
            if len(seen_titles) <= 1 and book is not None:
                description = str(book.get("description") or "").strip()
                if description:
                    import re as _re

                    _NAME = _re.compile(
                        r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,2}\b"
                    )
                    seen_names: set[str] = set()
                    for segment in _re.split(r"[.,;]", description):
                        # solo se descarta el DETERMINANTE inicial (La/El/Los/
                        # The...), no la primera palabra entera: así no se
                        # pierden entidades de 1 palabra o nombres completos
                        # en listas ("La Pantoja", "Rocío Jurado, Chiquetete")
                        body = _re.sub(
                            r"^\s*(?:El|La|Los|Las|Un|Una|Unos|Unas|The|A|An)\s+",
                            "",
                            segment,
                            flags=_re.IGNORECASE,
                        )
                        for m in _NAME.finditer(body):
                            name = _re.sub(r"\s+", " ", m.group(0)).strip()
                            if name and name not in seen_names and name not in seen_titles:
                                seen_names.add(name)
                                description_entities.append(name)
                                break  # 1 nombre por segmento basta
        except Exception:
            description_entities = []
        free = RESEARCH_MULTI_QUERY_MAX_CHAPTERS - len(chapter_titles)
        description_entities = description_entities[: max(0, free)]

        if not chapter_titles and not description_entities:
            return res  # nada que probar, comportamiento histórico

        merged_sources: list[dict] = list((res.metrics or {}).get("sources") or [])
        seen_urls: set[str] = {s.get("url") for s in merged_sources if s.get("url")}
        last_res = res

        for query in [*chapter_titles, *description_entities]:
            data["query"] = query
            data["min_sources"] = 0
            try:
                r2 = _run_single(phase, job, language=language)
            finally:
                pass
            if r2.ok:
                last_res = r2
                for s in (r2.metrics or {}).get("sources") or []:
                    url = (s or {}).get("url")
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    merged_sources.append(s)

        data["query"] = original_query
        data["min_sources"] = original_min_sources

        if len(merged_sources) >= min_sources:
            metrics = dict(last_res.metrics or {})
            metrics["sources"] = merged_sources
            metrics["source_count"] = len(merged_sources)
            metrics["execution_mode"] = "multi_query_fallback"
            metrics["chapter_queries_used"] = chapter_titles
            if description_entities:
                metrics["description_entities_used"] = description_entities
            log(
                logger,
                logging.INFO,
                "research: fallback multi-query por capítulo recuperó fuentes suficientes",
                language=language,
                source_count=len(merged_sources),
                chapter_queries=chapter_titles,
                description_entities=description_entities,
            )
            return PhaseResult(
                ok=True,
                metrics=metrics,
                module=last_res.module,
                task_id=last_res.task_id,
            )

        return res  # sigue sin fuentes suficientes: mismo gate_fail que antes

    def _run_research_multilang(phase: dict, job: dict) -> PhaseResult:
        """Fase research (fix book_56 / deuda §19 P3): UNA pasada POR IDIOMA.

        - Libros monolingües ("es" o "en") → una única pasada con el idioma del
          libro (un libro "en" ya NO consulta es.wikipedia).
        - ``languages="es,en"`` → DOS pasadas (es.wikipedia + en.wikipedia, cada
          una con su red + curación LLM). Las fuentes de cada idioma se guardan
          en ``job.data.sources_by_lang[lang]`` para que build_payload entregue
          a CADA writer solo las de su idioma (prompt LLM y backstop determinista
          sin hechos mezclados). El PhaseResult final fusiona ambas listas
          (dedupe por URL) con el MISMO shape histórico, de modo que la
          propagación Research -> job.data.sources -> SourceManager de run_job
          funciona sin cambios.
        """
        from frontend import editorial

        langs = _resolve_book_languages(editorial._get_book(job["book_id"]))
        if len(langs) <= 1:
            return _run_research_with_chapter_fallback(phase, job, language=langs[0])

        merged_sources: list[dict] = []
        seen_urls: set[str] = set()
        per_lang_counts: dict[str, int] = {}
        per_lang_status: dict[str, str] = {}
        last_metrics: dict = {}
        module_id = None
        task_id = None
        # FIX book_62 (fallback research bilingüe): si la pasada del idioma
        # SECUNDARIO falla SOLO por gate de source_count insuficiente (misma
        # condición que construye _run_single para research), no aborta el job:
        # se copian las fuentes del idioma primario a sources_by_lang[secundario]
        # y se registra warning. Cualquier otro fallo (excepción/timeout/error
        # real) o fallo del idioma PRIMARIO sigue abortando igual que antes.
        warnings: list[str] = []
        for idx, lang in enumerate(langs):
            if idx == 0:
                # §17 #44: fallback multi-query SOLO para el idioma primario;
                # el idioma secundario conserva su fallback histórico
                # (reutilización de fuentes del primario, fix book_62).
                res = _run_research_with_chapter_fallback(phase, job, language=lang)
            else:
                res = _run_single(phase, job, language=lang)
            if not res.ok:
                if (
                    idx > 0
                    and isinstance(res.error, str)
                    and res.error.startswith("research#")
                    and "source_count=" in res.error
                ):
                    primary = langs[0]
                    data = job.setdefault("data", {})
                    src_by_lang = data.setdefault("sources_by_lang", {})
                    src_by_lang[lang] = list(src_by_lang.get(primary) or [])
                    warning = (
                        f"research idioma {lang}: source_count insuficiente "
                        f"({res.error}); fallback: se reutilizan "
                        f"{len(src_by_lang[lang])} fuentes del idioma primario '{primary}'"
                    )
                    log(logger, logging.WARNING, warning)
                    warnings.append(warning)
                    per_lang_counts[lang] = len(src_by_lang[lang])
                    per_lang_status[lang] = "FALLBACK"
                    last_metrics = {
                        "query": "",
                        "language": lang,
                        "status": "PASS",
                        "execution_mode": "fallback_primary_sources",
                        "sources": src_by_lang[lang],
                        "source_count": len(src_by_lang[lang]),
                    }
                    continue
                return PhaseResult(
                    ok=False,
                    error=f"{res.error} [research idioma {lang}]",
                    metrics=res.metrics,
                    module=res.module,
                    task_id=res.task_id,
                )
            m = res.metrics or {}
            for s in (m.get("sources") or []):
                url = (s or {}).get("url")
                if url and url in seen_urls:
                    continue
                seen_urls.add(url)
                merged_sources.append(s)
            per_lang_counts[lang] = int(m.get("source_count") or 0)
            per_lang_status[lang] = str(m.get("status") or "")
            # Desglose por idioma para el writer (persistido con el job).
            job.setdefault("data", {}).setdefault("sources_by_lang", {})[lang] = (
                m.get("sources") or []
            )
            last_metrics = m
            module_id = res.module or module_id
            task_id = res.task_id or task_id
        return PhaseResult(
            ok=True,
            metrics={
                **last_metrics,
                "sources": merged_sources,
                "source_count": len(merged_sources),
                "per_language": per_lang_counts,
                "per_language_status": per_lang_status,
                **({"warnings": warnings} if warnings else {}),
            },
            module=module_id,
            task_id=task_id,
        )

    def executor(phase: dict, job: dict) -> PhaseResult:
        if phase["id"] == "research":
            return _run_research_multilang(phase, job)
        if phase["id"] in PER_CHAPTER_PHASES:
            return _execute_per_chapter(phase, job)
        if phase["id"] == "docx":
            return _run_docx(phase, job)
        return _run_single(phase, job)

    return executor
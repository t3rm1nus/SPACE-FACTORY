"""Cola de tareas de Space Lair (operaciones sobre la tabla tasks)."""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from core import storage
from core.database import get_db
from core.logger import get_logger, log

logger = get_logger(__name__)


def _now() -> str:
    """Timestamp actual en formato SQLite (UTC)."""
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def enqueue_task(capability: str, payload: dict, max_attempts: int = 1) -> int:
    """Inserta una nueva tarea en la cola y devuelve su ID."""
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO tasks (capability, payload, max_attempts)
            VALUES (?, ?, ?)
            """,
            (capability, json.dumps(payload, ensure_ascii=False), max_attempts),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_next_pending(capability: Optional[str] = None) -> Optional[dict]:
    """Devuelve la tarea pending más antigua (opcionalmente filtrada por capability).

    Solo considera tareas cuyo next_retry_at ha pasado o es NULL.
    """
    now = _now()
    conn = get_db()
    try:
        if capability:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'pending' AND capability = ?
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (capability, now),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'pending'
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def start_task(task_id: int, module_id: str) -> None:
    """Marca una tarea como 'running' y registra el módulo que la ejecuta."""
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'running', module_id = ?, started_at = ?
            WHERE id = ?
            """,
            (module_id, _now(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def complete_task(task_id: int, result: Any) -> None:
    """Marca una tarea como 'done' y guarda el resultado (JSON).

    Resultados cuyo JSON serializado supere estrictamente 1 MiB se externalizan
    a ``data/results/{task_id}.json`` y en SQLite se guarda una referencia
    (ver ``core.storage``). Primero se escribe el archivo; solo si tiene éxito
    se actualiza la BD, de modo que nunca queda una referencia sin archivo.
    """
    conn = get_db()
    try:
        if storage.should_externalize(result):
            # 1) persistir el contenido primero (escribir antes que la BD).
            ref = {
                storage.REF_KEY: True,
                storage.REF_PATH_KEY: storage.save_result(task_id, result),
            }
            stored_json = storage.serialize(ref)
        else:
            # Resultado pequeño: exactamente el comportamiento actual (inline).
            stored_json = storage.serialize(result)

        conn.execute(
            """
            UPDATE tasks
            SET status = 'done', result = ?, finished_at = ?
            WHERE id = ?
            """,
            (stored_json, _now(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def fail_task(task_id: int, error: str) -> None:
    """Marca una tarea como 'error' y guarda el mensaje de error."""
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'error', error = ?, finished_at = ?
            WHERE id = ?
            """,
            (error, _now(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def approve_task(task_id: int) -> None:
    """Aprueba una tarea en 'pending_approval' y la devuelve a 'pending'."""
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'pending'
            WHERE id = ? AND status = 'pending_approval'
            """,
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


def reject_task(task_id: int) -> None:
    """Rechaza una tarea en 'pending_approval' marcándola como error."""
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'error', error = 'Rechazada por humano', finished_at = ?
            WHERE id = ? AND status = 'pending_approval'
            """,
            (_now(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def cancel_task(task_id: int) -> None:
    """Cancela una tarea (no 'pending_approval') y la marca como 'cancelled'.

    Solo se puede cancelar si la tarea está en un estado no terminal.
    """
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'cancelled', error = 'Cancelled by operator', finished_at = ?
            WHERE id = ? AND status IN ('pending', 'pending_approval', 'running')
            """,
            (_now(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_for_approval(task_id: int) -> None:
    """Marca una tarea como 'pending_approval' (requiere intervención humana)."""
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'pending_approval'
            WHERE id = ?
            """,
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _is_external_ref(value: Optional[str]) -> bool:
    """True si ``value`` deserializa a un marcador de referencia válido.

    El marcador solo se reconoce cuando su estructura es válida: objeto JSON
    con ``_ref_external is True`` y una ``path`` de texto no vacía.
    """
    if not value:
        return False
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    if parsed.get(storage.REF_KEY) is not True:
        return False
    path = parsed.get(storage.REF_PATH_KEY)
    return isinstance(path, str) and bool(path)


def _hydrate_result(row: dict) -> dict:
    """Sustituye ``row['result']`` (referencia) por el JSON del resultado real.

    Mantiene el contrato público: ``row['result']`` es siempre el JSON
    serializado del resultado real, nunca una referencia opaca.

    Si el archivo externo no existe o está corrupto, no se lanza (compatibilidad
    para llamadores que usan ``get_task`` solo para verificar existencia): se
    deja la referencia enriquecida con un campo ``_ref_error`` descriptivo y se
    registra un warning. Es una degradación honesta, nunca un resultado falso.
    """
    result_raw = row.get("result")
    if not _is_external_ref(result_raw):
        return row
    try:
        content = storage.load_result(row["id"])
    except storage.StorageError as exc:
        ref = json.loads(result_raw)
        ref["_ref_error"] = str(exc)
        row["result"] = storage.serialize(ref)
        log(
            logger,
            logging.WARNING,
            f"resultado externalizado no recuperable para la tarea {row['id']}",
            task_id=row["id"],
        )
        return row
    row["result"] = storage.serialize(content)
    return row


def get_task(task_id: int) -> Optional[dict]:
    """SELECT * FROM tasks WHERE id=? (con resultado hidratado si es externo)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _hydrate_result(dict(row)) if row else None
    finally:
        conn.close()


def update_task_metrics(
    task_id: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float = 0.0,
) -> None:
    """Actualiza tokens_input, tokens_output y cost en una tarea."""
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE tasks
            SET tokens_input = tokens_input + ?,
                tokens_output = tokens_output + ?,
                cost = cost + ?
            WHERE id = ?
            """,
            (input_tokens, output_tokens, cost, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def all_tasks() -> list[dict]:
    """Devuelve todas las tareas ordenadas de más reciente a más antigua.

    Los resultados externalizados se hidratan, manteniendo el contrato público
    (result como JSON serializado del resultado real).
    """
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [_hydrate_result(dict(row)) for row in rows]
    finally:
        conn.close()


def increment_attempts(task_id: int) -> None:
    """Incrementa el contador de intentos de una tarea."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE tasks SET attempts = attempts + 1 WHERE id = ?",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


def reset_task_attempts(task_id: int) -> None:
    """Pone a cero el contador de intentos de una tarea (tras éxito con reintentos)."""
    conn = get_db()
    try:
        conn.execute("UPDATE tasks SET attempts = 0 WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()


def requeue_task(task_id: int, delay_seconds: int = 0) -> None:
    """Devuelve una tarea fallida a estado 'pending' para reintentarla.

    Si delay_seconds > 0, establece next_retry_at en el futuro (backoff exponencial).
    """
    conn = get_db()
    try:
        if delay_seconds > 0:
            retry_at = (
                datetime.utcnow() + timedelta(seconds=delay_seconds)
            ).isoformat(sep=" ", timespec="seconds")
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending', error = NULL, finished_at = NULL,
                    next_retry_at = ?
                WHERE id = ?
                """,
                (retry_at, task_id),
            )
        else:
            conn.execute(
                """
                UPDATE tasks
                SET status = 'pending', error = NULL, finished_at = NULL,
                    next_retry_at = NULL
                WHERE id = ?
                """,
                (task_id,),
            )
        conn.commit()
    finally:
        conn.close()


def reset_stale_running() -> int:
    """Resetea tareas 'running' con started_at anterior a hace 300 segundos.

    Devuelve el número de tareas reseteadas.
    """
    from core.database import reset_stale_running_tasks

    return reset_stale_running_tasks()
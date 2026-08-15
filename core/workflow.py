"""Orquestación de workflows en Space Lair.

Un workflow es una secuencia de pasos (steps) que se ejecutan
respetando dependencias. Cada paso tiene:
  - id: identificador único del paso
  - capability: capability a ejecutar
  - depends_on: id(s) de pasos que deben completarse antes
  - parallel: lista de ids de pasos que deben completarse antes
             (los pasos en parallel se ejecutan en paralelo)

Formato de definición (YAML o JSON):
    steps:
      - id: step1
        capability: summarize_text
      - id: step2
        capability: translate_text
        depends_on: step1
      - id: step3
        capability: generate_image
        parallel: [step1, step2]
"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Optional

from core import events, task_queue
from core.database import get_db
from core.logger import get_logger, log
from core.scheduler import _execute_with_timeout, _get_timeout, _requires_approval

logger = get_logger(__name__)

# Estados válidos
WORKFLOW_STATUSES = ("pending", "running", "done", "error", "cancelled")
STEP_STATUSES = ("pending", "running", "done", "error", "skipped")


def _now() -> str:
    """Timestamp actual en formato SQLite (UTC)."""
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def _parse_definition(definition: str) -> dict:
    """Parsea la definición del workflow (YAML o JSON).

    Args:
        definition: String con la definición en YAML o JSON.

    Returns:
        dict con la definición normalizada: {"steps": [...]}
    """
    if not definition or not definition.strip():
        raise ValueError("La definición del workflow no puede estar vacía")

    # Intentar parsear como JSON primero
    try:
        data = json.loads(definition)
    except json.JSONDecodeError:
        # Intentar parsear como YAML (sin dependencia externa, parser simple)
        data = _parse_simple_yaml(definition)

    if not isinstance(data, dict) or "steps" not in data:
        raise ValueError("La definición debe contener una lista 'steps'")

    steps = data["steps"]
    if not isinstance(steps, list) or not steps:
        raise ValueError("'steps' debe ser una lista no vacía")

    # Validar y normalizar cada paso
    normalized = []
    seen_ids = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError(f"Cada paso debe ser un objeto, got: {step!r}")

        step_id = step.get("id")
        capability = step.get("capability")
        if not step_id or not capability:
            raise ValueError(f"Cada paso requiere 'id' y 'capability': {step!r}")

        if step_id in seen_ids:
            raise ValueError(f"ID de paso duplicado: '{step_id}'")
        seen_ids.add(step_id)

        depends_on = step.get("depends_on")
        parallel = step.get("parallel")

        # Normalizar depends_on a lista
        if depends_on is None:
            depends_on = []
        elif isinstance(depends_on, str):
            depends_on = [depends_on]
        elif not isinstance(depends_on, list):
            raise ValueError(f"'depends_on' de '{step_id}' debe ser string o lista")

        # Normalizar parallel a lista
        if parallel is None:
            parallel = []
        elif isinstance(parallel, str):
            parallel = [parallel]
        elif not isinstance(parallel, list):
            raise ValueError(f"'parallel' de '{step_id}' debe ser string o lista")

        # Validar que las dependencias existen
        all_deps = set(depends_on) | set(parallel)
        for dep in all_deps:
            if dep not in seen_ids and dep != step_id:
                raise ValueError(
                    f"Paso '{step_id}' depende de '{dep}' que no existe"
                )

        # Número de reintentos ante errores (por defecto 0)
        retries = step.get("retries", 0)
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise ValueError(f"'retries' de '{step_id}' debe ser un entero >= 0")

        normalized.append({
            "id": step_id,
            "capability": capability,
            "depends_on": depends_on,
            "parallel": parallel,
            "retries": retries,
            "payload": step.get("payload", {}),
        })

    return {"steps": normalized}


def _parse_simple_yaml(text: str) -> dict:
    """Parser YAML mínimo para definiciones de workflow.

    Soporta la estructura:
        steps:
          - id: step1
            capability: summarize_text
          - id: step2
            capability: translate_text
            depends_on: step1
          - id: step3
            capability: generate_image
            parallel: [step1, step2]

    Returns:
        dict con la estructura parseada.
    """
    result: dict[str, Any] = {}
    current_list: Optional[list] = None
    current_item: Optional[dict] = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        if indent == 0 and stripped.endswith(":"):
            key = stripped[:-1].strip()
            result[key] = []
            current_list = result[key]
            current_item = None
            continue

        if indent == 2 and stripped.startswith("- "):
            # Nuevo item en la lista
            item_text = stripped[2:].strip()
            if current_list is None:
                raise ValueError("YAML inválido: item fuera de lista")
            current_item = {}
            current_list.append(current_item)
            if ":" in item_text:
                key, value = item_text.split(":", 1)
                current_item[key.strip()] = _parse_yaml_value(value.strip())
            continue

        if indent >= 4 and current_item is not None and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_item[key.strip()] = _parse_yaml_value(value.strip())
            continue

        raise ValueError(f"YAML inválido en línea: {line!r}")

    return result


def _parse_yaml_value(value: str) -> Any:
    """Parsea un valor YAML simple (string, int, bool, lista)."""
    value = value.strip()
    if not value:
        return None

    # Lista: [a, b, c]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",")]

    # Booleanos
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False

    # Números
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass

    # String (quitar comillas)
    if (value.startswith("'") and value.endswith("'")) or (
        value.startswith('"') and value.endswith('"')
    ):
        return value[1:-1]
    return value


def create_workflow(name: str, definition: str) -> int:
    """Crea un workflow y sus pasos en la BD.

    Args:
        name: Nombre del workflow.
        definition: Definición YAML/JSON del workflow.

    Returns:
        ID del workflow creado.
    """
    parsed = _parse_definition(definition)
    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO workflows (name, definition, status)
            VALUES (?, ?, 'pending')
            """,
            (name, json.dumps(parsed, ensure_ascii=False)),
        )
        workflow_id = cursor.lastrowid

        for step in parsed["steps"]:
            conn.execute(
                """
                INSERT INTO workflow_steps
                    (workflow_id, step_id, capability, depends_on, parallel, payload, retries)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    step["id"],
                    step["capability"],
                    json.dumps(step["depends_on"]),
                    json.dumps(step["parallel"]),
                    json.dumps(step.get("payload", {}), ensure_ascii=False),
                    int(step.get("retries", 0)),
                ),
            )
        conn.commit()
        log(
            logger,
            logging.INFO,
            f"Workflow '{name}' creado con {len(parsed['steps'])} pasos",
            task_id=workflow_id,
        )
        return workflow_id
    finally:
        conn.close()


def get_workflow(workflow_id: int) -> Optional[dict]:
    """Devuelve un workflow con sus pasos."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if row is None:
            return None

        workflow = dict(row)
        workflow["definition"] = json.loads(workflow["definition"])

        step_rows = conn.execute(
            "SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY id",
            (workflow_id,),
        ).fetchall()
        steps = []
        for s in step_rows:
            step = dict(s)
            step["depends_on"] = json.loads(step["depends_on"] or "[]")
            step["parallel"] = json.loads(step["parallel"] or "[]")
            step["retries"] = int(step.get("retries") or 0)
            try:
                step["payload"] = json.loads(step.get("payload") or "{}")
            except (json.JSONDecodeError, TypeError):
                step["payload"] = {}
            if step.get("result"):
                try:
                    step["result"] = json.loads(step["result"])
                except (json.JSONDecodeError, TypeError):
                    pass
            steps.append(step)
        workflow["steps"] = steps
        return workflow
    finally:
        conn.close()


def all_workflows() -> list[dict]:
    """Devuelve todos los workflows."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM workflows ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _update_workflow_status(workflow_id: int, status: str, error: Optional[str] = None) -> None:
    """Actualiza el estado de un workflow."""
    conn = get_db()
    try:
        if status == "running":
            conn.execute(
                """
                UPDATE workflows
                SET status = ?, started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (status, _now(), workflow_id),
            )
        elif status in ("done", "error", "cancelled"):
            conn.execute(
                """
                UPDATE workflows
                SET status = ?, error = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, error, _now(), workflow_id),
            )
        else:
            conn.execute(
                "UPDATE workflows SET status = ? WHERE id = ?",
                (status, workflow_id),
            )
        conn.commit()
    finally:
        conn.close()


def _update_step_status(
    step_id: int,
    status: str,
    result: Any = None,
    error: Optional[str] = None,
    task_id: Optional[int] = None,
) -> None:
    """Actualiza el estado de un paso del workflow."""
    conn = get_db()
    try:
        if status == "running":
            conn.execute(
                """
                UPDATE workflow_steps
                SET status = ?, started_at = COALESCE(started_at, ?)
                WHERE id = ?
                """,
                (status, _now(), step_id),
            )
        elif status in ("done", "error", "skipped"):
            conn.execute(
                """
                UPDATE workflow_steps
                SET status = ?, result = ?, error = ?, task_id = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                    error,
                    task_id,
                    _now(),
                    step_id,
                ),
            )
        else:
            conn.execute(
                "UPDATE workflow_steps SET status = ? WHERE id = ?",
                (status, step_id),
            )
        conn.commit()
    finally:
        conn.close()


def _get_step_dependencies(step: dict, steps: list[dict]) -> list[dict]:
    """Devuelve los pasos de los que depende un paso (depends_on + parallel)."""
    dep_ids = set(step.get("depends_on", [])) | set(step.get("parallel", []))
    return [s for s in steps if s["step_id"] in dep_ids]


def _all_deps_done(step: dict, steps: list[dict]) -> bool:
    """Verifica que todas las dependencias de un paso están completadas."""
    deps = _get_step_dependencies(step, steps)
    return all(dep["status"] == "done" for dep in deps)


def _any_dep_failed(step: dict, steps: list[dict]) -> bool:
    """Verifica si alguna dependencia falló."""
    deps = _get_step_dependencies(step, steps)
    return any(dep["status"] == "error" for dep in deps)


def _build_step_context(step: dict, steps: list[dict]) -> dict:
    """Construye el contexto para un paso con los resultados de sus dependencias.

    El contexto incluye:
      - El payload propio del paso (definido en el workflow), a nivel raíz.
      - results: {step_id: resultado} de todas las dependencias
      - workflow: metadatos del workflow (si están disponibles)
    """
    deps = _get_step_dependencies(step, steps)
    results = {}
    for dep in deps:
        if dep["status"] == "done" and dep.get("result") is not None:
            results[dep["step_id"]] = dep["result"]
    context: dict[str, Any] = dict(step.get("payload") or {})
    context["results"] = results
    return context


def _execute_step(
    step: dict,
    modules: dict[str, dict[str, Any]],
    cap_map: dict[str, list[str]],
    context: dict,
) -> Any:
    """Ejecuta un paso del workflow.

    Args:
        step: Paso del workflow (dict de BD).
        modules: Módulos cargados.
        cap_map: Mapa capability → módulos.
        context: Contexto con resultados de dependencias.

    Returns:
        Resultado de la ejecución.
    """
    capability = step["capability"]
    module_ids = cap_map.get(capability, [])
    if not module_ids:
        raise ValueError(f"No hay módulos que soporten la capability '{capability}'")

    # Seleccionar el primer módulo healthy
    from core.module_registry import check_all_health

    module = None
    for mid in module_ids:
        if mid in modules:
            health = check_all_health(modules).get(mid, {})
            if health.get("healthy", False):
                module = modules[mid]
                break

    if module is None:
        raise ValueError(f"No hay módulos healthy para capability '{capability}'")

    # Si requiere aprobación humana, marcar la tarea
    if _requires_approval(module):
        task_id = task_queue.enqueue_task(capability, context, max_attempts=1)
        task_queue.mark_for_approval(task_id)
        return {"status": "pending_approval", "task_id": task_id}

    # Encolar tarea y ejecutar
    max_attempts = 1 + int(step.get("retries") or 0)
    task_id = task_queue.enqueue_task(capability, context, max_attempts=max_attempts)
    task_queue.start_task(task_id, module["manifest"]["id"])

    timeout = _get_timeout(module)
    attempts = 0
    while True:
        attempts += 1
        try:
            result = _execute_with_timeout(module, context, timeout, capability=capability)
            if attempts > 1:
                task_queue.reset_task_attempts(task_id)
            task_queue.complete_task(task_id, result)
            return result
        except Exception as e:
            if attempts < max_attempts:
                log(
                    logger,
                    logging.WARNING,
                    f"Reintentando paso '{step['step_id']}' ({attempts}/{max_attempts}): {e}",
                    task_id=task_id,
                    capability=capability,
                )
                continue
            task_queue.fail_task(task_id, str(e))
            raise


def _workflow_quality_gate(steps: list[dict]) -> tuple[bool, list[str]]:
    """Valida que ninguna etapa crítica haya fallado antes de permitir COMPLETED.

    Etapas críticas: planning, research, outline, chapter, fact_check, editor.
    Un paso con estado 'error' o una etapa crítica con quality_gate=FAIL
    impide que el workflow se declare 'done'.

    Returns:
        (ok, errores)
    """
    critical_keywords = ("plan", "research", "outline", "chapter", "fact", "editor")
    errors: list[str] = []
    for step in steps:
        if step.get("status") == "error":
            errors.append(
                f"etapa {step.get('step_id')} ({step.get('capability')}) terminó en error"
            )
            continue
        if step.get("status") != "done":
            continue
        result = step.get("result") or {}
        cap = str(step.get("capability") or "").lower()
        if any(k in cap for k in critical_keywords):
            gate = result.get("quality_gate")
            if gate == "FAIL":
                reasons = result.get("quality_errors") or result.get("error") or ""
                errors.append(
                    f"etapa {step.get('step_id')} ({cap}) reprobó control de calidad: {reasons}"
                )
            # research_web con research_required=true debe tener fuentes
            if "research" in cap and result.get("status") == "FAIL":
                errors.append(
                    f"etapa {step.get('step_id')} ({cap}) terminó en FAIL"
                )
    return (len(errors) == 0, errors)


def run_workflow(
    workflow_id: int,
    modules: dict[str, dict[str, Any]],
    cap_map: dict[str, list[str]],
) -> dict:
    """Ejecuta un workflow respetando dependencias y paralelismo.

    Args:
        workflow_id: ID del workflow a ejecutar.
        modules: Módulos cargados.
        cap_map: Mapa capability → módulos.

    Returns:
        dict con el estado final del workflow.
    """
    workflow = get_workflow(workflow_id)
    if workflow is None:
        raise ValueError(f"Workflow {workflow_id} no encontrado")

    if workflow["status"] in ("running", "done", "cancelled"):
        raise ValueError(
            f"Workflow {workflow_id} ya está en estado '{workflow['status']}'"
        )

    steps = workflow["steps"]
    _update_workflow_status(workflow_id, "running")
    events.emit("workflow_started", {"workflow_id": workflow_id, "name": workflow["name"]})

    try:
        # Ejecutar pasos en orden, respetando dependencias
        completed = set()
        failed = False

        while len(completed) < len(steps) and not failed:
            # Encontrar pasos listos para ejecutar (deps completadas)
            ready = []
            for step in steps:
                if step["id"] in completed or step["status"] in ("done", "error", "skipped"):
                    continue
                if _any_dep_failed(step, steps):
                    # Marcar como skipped si una dependencia falló
                    _update_step_status(step["id"], "skipped", error="Dependencia falló")
                    completed.add(step["id"])
                    continue
                if _all_deps_done(step, steps):
                    ready.append(step)

            if not ready:
                # No hay pasos listos y no todos completados → deadlock o error
                if not failed:
                    _update_workflow_status(
                        workflow_id, "error", error="No hay pasos ejecutables (deadlock)"
                    )
                break

            # Ejecutar pasos listos (en paralelo si hay varios)
            if len(ready) == 1:
                step = ready[0]
                _update_step_status(step["id"], "running")
                _update_workflow_status(workflow_id, "running", current_step=step["step_id"])
                events.emit("workflow_step_started", {
                    "workflow_id": workflow_id,
                    "step_id": step["step_id"],
                    "capability": step["capability"],
                })

                try:
                    context = _build_step_context(step, steps)
                    result = _execute_step(step, modules, cap_map, context)
                    _update_step_status(step["id"], "done", result=result)
                    completed.add(step["id"])
                    _save_step_checkpoint(workflow_id, step, result)
                    events.emit("workflow_step_completed", {
                        "workflow_id": workflow_id,
                        "step_id": step["step_id"],
                        "capability": step["capability"],
                    })
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {e}"
                    _update_step_status(step["id"], "error", error=error_msg)
                    _update_workflow_status(workflow_id, "error", error=error_msg)
                    failed = True
                    events.emit("workflow_step_failed", {
                        "workflow_id": workflow_id,
                        "step_id": step["step_id"],
                        "capability": step["capability"],
                        "error": error_msg,
                    })
            else:
                # Ejecutar en paralelo con ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=len(ready)) as executor:
                    futures = {}
                    for step in ready:
                        _update_step_status(step["id"], "running")
                        events.emit("workflow_step_started", {
                            "workflow_id": workflow_id,
                            "step_id": step["step_id"],
                            "capability": step["capability"],
                        })
                        future = executor.submit(
                            _execute_step, step, modules, cap_map,
                            _build_step_context(step, steps),
                        )
                        futures[future] = step

                    for future in as_completed(futures):
                        step = futures[future]
                        try:
                            result = future.result()
                            _update_step_status(step["id"], "done", result=result)
                            completed.add(step["id"])
                            events.emit("workflow_step_completed", {
                                "workflow_id": workflow_id,
                                "step_id": step["step_id"],
                                "capability": step["capability"],
                            })
                        except Exception as e:
                            error_msg = f"{type(e).__name__}: {e}"
                            _update_step_status(step["id"], "error", error=error_msg)
                            _update_workflow_status(workflow_id, "error", error=error_msg)
                            failed = True
                            events.emit("workflow_step_failed", {
                                "workflow_id": workflow_id,
                                "step_id": step["step_id"],
                                "capability": step["capability"],
                                "error": error_msg,
                            })

        if not failed:
            # Control de calidad final del workflow: si una etapa crítica falla,
            # el workflow NO puede declararse 'done' aunque técnicamente se haya
            # ejecutado sin excepción.
            qok, qerrors = _workflow_quality_gate(steps)
            if qok:
                _update_workflow_status(workflow_id, "done")
                events.emit("workflow_completed", {
                    "workflow_id": workflow_id,
                    "name": workflow["name"],
                })
            else:
                error_msg = f"Control de calidad del workflow reprobado: {'; '.join(qerrors)}"
                _update_workflow_status(workflow_id, "error", error=error_msg)
                events.emit("workflow_failed", {
                    "workflow_id": workflow_id,
                    "name": workflow["name"],
                    "error": error_msg,
                })

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        _update_workflow_status(workflow_id, "error", error=error_msg)
        events.emit("workflow_failed", {
            "workflow_id": workflow_id,
            "name": workflow["name"],
            "error": error_msg,
        })

    return get_workflow(workflow_id)


def cancel_workflow(workflow_id: int) -> Optional[dict]:
    """Cancela un workflow pendiente o en ejecución."""
    workflow = get_workflow(workflow_id)
    if workflow is None:
        return None

    if workflow["status"] not in ("pending", "running"):
        raise ValueError(f"Workflow {workflow_id} no se puede cancelar (estado: {workflow['status']})")

    _update_workflow_status(workflow_id, "cancelled", error="Cancelado por el usuario")
    events.emit("workflow_cancelled", {"workflow_id": workflow_id})
    return get_workflow(workflow_id)
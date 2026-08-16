"""Scheduler de Space Lair.

Bucle principal que procesa tareas pendientes asignรกndolas a los mรณdulos
disponibles segรบn su capability.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Optional

from core import events, task_queue
from core.central_ai import choose_module
from core.logger import get_logger, log
from core.mcp_bridge import call_tool
from core.module_registry import check_all_health
from core.schemas import validate_payload

logger = get_logger(__name__)

# Tiempo de espera entre iteraciones del bucle (segundos)
SLEEP_SECONDS = 1

# Timeout por defecto si el mรณdulo no especifica timeout_seconds
DEFAULT_TIMEOUT_SECONDS = 30


def _get_timeout(module: dict[str, Any]) -> int:
    """Obtiene el timeout_seconds del mรณdulo (con valor por defecto)."""
    return int(
        module["manifest"].get("config", {}).get(
            "timeout_seconds", DEFAULT_TIMEOUT_SECONDS
        )
    )


def _requires_approval(module: dict[str, Any]) -> bool:
    """Indica si el mรณdulo requiere aprobaciรณn humana antes de ejecutar."""
    return bool(module["manifest"].get("requires_human_approval", False))


def _execute_with_timeout(module: dict[str, Any], payload: dict, timeout: int, capability: str = "") -> Any:
    """Ejecuta el módulo (local o MCP) con un timeout usando ThreadPoolExecutor.

    - Módulos normales: llama a module["execute"](payload, capability).
      Si el módulo no acepta ``capability`` (``TypeError``), reintenta con
      ``module["execute"](payload)`` para preservar compatibilidad.
    - Módulos MCP externos: llama a call_tool(module, capability, payload).
    """
    is_mcp = module.get("is_mcp", False)

    def _run() -> Any:
        if is_mcp:
            return call_tool(module, capability, payload)
        # Pass capability so module dispatchers (e.g. image_generator.execute)
        # route to the correct sub-handler.  Some legacy modules only accept
        # (payload); fall back gracefully to preserve backwards compatibility.
        try:
            return module["execute"](payload, capability)
        except TypeError:
            return module["execute"](payload)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run)
        return future.result(timeout=timeout)


def _process_task(
    task: dict,
    module: dict[str, Any],
    capability: str,
) -> None:
    """Procesa una รบnica tarea con el mรณdulo seleccionado."""
    task_id = task["id"]
    module_id = module["manifest"]["id"]

    # Si el mรณdulo requiere aprobaciรณn humana, marcar y salir
    if _requires_approval(module):
        task_queue.mark_for_approval(task_id)
        log(
            logger,
            logging.INFO,
            "Tarea marcada para aprobaciรณn humana",
            task_id=task_id,
            module_id=module_id,
            capability=capability,
        )
        return

    # Marcar como running
    task_queue.start_task(task_id, module_id)
    log(
        logger,
        logging.INFO,
        "Tarea en ejecuciรณn",
        task_id=task_id,
        module_id=module_id,
        capability=capability,
    )
    events.emit("task_started", {
        "task_id": task_id,
        "module_id": module_id,
        "capability": capability,
    })

    timeout = _get_timeout(module)
    payload = json.loads(task["payload"])

    # Validar payload con Pydantic antes de pasar al mรณdulo
    try:
        validated_payload = validate_payload(capability, payload)
    except ValueError:
        error_msg = f"Capability '{capability}' no tiene esquema de validaciรณn"
        log(logger, logging.ERROR, error_msg, task_id=task_id, capability=capability)
        events.emit("task_failed", {
            "task_id": task_id,
            "module_id": module_id,
            "capability": capability,
            "error": error_msg,
        })
        _handle_failure(task_id, error_msg)
        return
    except Exception as e:
        error_msg = f"Payload invรกlido: {e}"
        log(logger, logging.ERROR, error_msg, task_id=task_id, capability=capability)
        events.emit("task_failed", {
            "task_id": task_id,
            "module_id": module_id,
            "capability": capability,
            "error": error_msg,
        })
        _handle_failure(task_id, error_msg)
        return

    try:
        result = _execute_with_timeout(module, validated_payload, timeout, capability=capability)
        task_queue.complete_task(task_id, result)

        # Registrar tokens y coste si el módulo lo reportó
        if isinstance(result, dict):
            task_queue.update_task_metrics(
                task_id,
                result.get("tokens_input", 0),
                result.get("tokens_output", 0),
                result.get("cost", 0.0),
            )

        log(
            logger,
            logging.INFO,
            "Tarea completada",
            task_id=task_id,
            module_id=module_id,
            capability=capability,
        )
        events.emit("task_completed", {
            "task_id": task_id,
            "module_id": module_id,
            "capability": capability,
        })
    except FutureTimeoutError:
        error_msg = f"Timeout tras {timeout}s ejecutando {module_id}"
        log(
            logger,
            logging.WARNING,
            error_msg,
            task_id=task_id,
            module_id=module_id,
            capability=capability,
        )
        events.emit("task_failed", {
            "task_id": task_id,
            "module_id": module_id,
            "capability": capability,
            "error": error_msg,
        })
        _handle_failure(task_id, error_msg)
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        log(
            logger,
            logging.ERROR,
            f"Tarea fallรณ: {error_msg}",
            task_id=task_id,
            module_id=module_id,
            capability=capability,
        )
        events.emit("task_failed", {
            "task_id": task_id,
            "module_id": module_id,
            "capability": capability,
            "error": error_msg,
        })
        _handle_failure(task_id, error_msg)


def _handle_failure(task_id: int, error_msg: str) -> None:
    """Maneja el fallo de una tarea con lรณgica de reintentos."""
    task = task_queue.get_task(task_id)
    if task is None:
        return

    task_queue.increment_attempts(task_id)
    attempts = task["attempts"] + 1
    max_attempts = task["max_attempts"]
    capability = task["capability"]

    if attempts < max_attempts:
        # Reintentar con backoff exponencial: 2, 4, 8, 16... segundos
        delay = 2 ** attempts
        task_queue.requeue_task(task_id, delay_seconds=delay)
        log(
            logger,
            logging.INFO,
            f"Reintento programado en {delay}s (intento {attempts}/{max_attempts})",
            task_id=task_id,
            capability=capability,
        )
    else:
        task_queue.fail_task(task_id, error_msg)
        log(
            logger,
            logging.ERROR,
            f"Fallo definitivo tras {attempts} intentos",
            task_id=task_id,
            capability=capability,
        )


def run_loop(
    modules: dict[str, dict[str, Any]],
    cap_map: dict[str, list[str]],
    max_iterations: Optional[int] = None,
) -> None:
    """Bucle principal del scheduler.

    Procesa tareas pendientes para cada capability en cap_map.
    Si max_iterations es None, corre indefinidamente.
    """
    log(
        logger,
        logging.INFO,
        f"Iniciando bucle con {len(modules)} mรณdulos y {len(cap_map)} capabilities",
    )

    iteration = 0

    # Cache de health checks para no repetir en cada iteración
    _health_cache: dict[str, bool] = {}

    def _is_healthy(module_id: str) -> bool:
        if module_id not in _health_cache:
            _health_cache[module_id] = check_all_health(modules)[module_id]["healthy"]
        return _health_cache[module_id]

    try:
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            processed = 0

            for capability, module_ids in cap_map.items():
                task = task_queue.get_next_pending(capability)
                if task is None:
                    continue

                # Obtener módulos candidatos (excluyendo los unhealthy)
                candidate_modules = []
                for mid in module_ids:
                    if mid in modules and _is_healthy(mid):
                        candidate_modules.append(modules[mid])

                if not candidate_modules:
                    unhealthy = [mid for mid in module_ids if mid in modules and not _is_healthy(mid)]
                    log(
                        logger,
                        logging.WARNING if unhealthy else logging.ERROR,
                        f"Módulos no disponibles para capability '{capability}'"
                        + (f" (unhealthy: {unhealthy})" if unhealthy else " (no cargados)"),
                        capability=capability,
                    )
                    continue

                # Seleccionar mรณdulo
                if len(candidate_modules) == 1:
                    module = candidate_modules[0]
                else:
                    module_id = choose_module(
                        capability,
                        candidate_modules,
                        json.loads(task["payload"]),
                    )
                    module = modules[module_id]
                    events.emit("central_ai_decision", {
                        "task_id": task["id"],
                        "capability": capability,
                        "module_id": module_id,
                    })

                _process_task(task, module, capability)
                processed += 1

            if processed:
                log(
                    logger,
                    logging.INFO,
                    f"Iteraciรณn {iteration}: {processed} tarea(s) procesada(s)",
                )

            time.sleep(SLEEP_SECONDS)

    except KeyboardInterrupt:
        log(logger, logging.INFO, "Interrupciรณn recibida. Saliendo limpiamente...")
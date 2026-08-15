"""Space Lair - CLI principal (click).

Comandos:
  space-lair                         → Muestra ayuda
  space-lair demo                    → Ejecuta una demo con tareas de ejemplo
  space-lair serve                   → Ejecuta el scheduler en bucle infinito
  space-lair web                     → Arranca servidor web (frontend + API + SSE)
  space-lair status                  → Muestra todas las tareas en tabla
  space-lair enqueue <cap> <json>    → Encola una tarea
  space-lair token                   → Genera un token JWT para autenticación
  space-lair approve <id> --token T  → Aprueba una tarea (requiere JWT)
  space-lair reject <id> --token T   → Rechaza una tarea (requiere JWT)
  space-lair costs                   → Muestra métricas de coste y tokens
  space-lair task list [--status S]  → Lista tareas (filtrable)
  space-lair task approve <id> --token T → Aprueba una tarea (alias)
  space-lair task reject <id> --token T  → Rechaza una tarea (alias)
  space-lair module list             → Lista módulos cargados
  space-lair module status           → Muestra estado de salud de módulos
"""

import json
import logging
import sys
from typing import Optional

import click

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from core import task_queue
from core.auth import generate_token, verify_token
from core.database import init_db, reset_stale_running_tasks
from core.logger import get_logger, setup_logging
from core.metrics import summarize_costs
from core.module_registry import capabilities_map, check_all_health, load_modules
from core.scheduler import run_loop
from core.schemas import validate_payload
from core.workflow import all_workflows, cancel_workflow, create_workflow, get_workflow, run_workflow

# Configurar logging estructurado (JSON)
setup_logging()
logger = get_logger("run")


# ============================================
# Utilidades
# ============================================
def _print_status_table(tasks: list[dict]) -> None:
    """Muestra las tareas en una tabla formateada."""
    if not tasks:
        click.echo("No hay tareas en la base de datos.")
        return

    headers = ["ID", "Capability", "Status", "Modulo", "Intentos", "Creada"]
    widths = [4, 20, 18, 16, 8, 20]

    def format_row(row: list) -> str:
        return " | ".join(
            str(cell).ljust(widths[i])[:widths[i]] for i, cell in enumerate(row)
        )

    click.echo(format_row(headers))
    click.echo("-" * (sum(widths) + 3 * (len(widths) - 1)))

    for t in tasks:
        click.echo(
            format_row(
                [
                    t["id"],
                    t["capability"],
                    t["status"],
                    t["module_id"] or "-",
                    f"{t['attempts']}/{t['max_attempts']}",
                    t["created_at"],
                ]
            )
        )


def _get_task_or_exit(task_id: int) -> dict:
    """Verifica que la tarea existe; muestra error y sale si no."""
    task = task_queue.get_task(task_id)
    if task is None:
        click.echo(f"❌ No existe la tarea {task_id}")
        sys.exit(1)
    return task


# ============================================
# Grupo principal
# ============================================
@click.group()
@click.version_option(message="Space Lair CLI")
def cli() -> None:
    """Space Lair - Sistema de agentes autónomos."""
    init_db()
    reset_stale_running_tasks()


# ============================================
# Comandos principales
# ============================================
@cli.command()
def demo() -> None:
    """Ejecuta una demo: encola tareas de ejemplo y corre el scheduler."""
    click.echo("🚀 Space Lair - Demo")
    click.echo("=====================")

    modules = load_modules()
    cap_map = capabilities_map(modules)
    click.echo(f"Modulos cargados: {', '.join(modules.keys()) or '(ninguno)'}")

    task_ids: list[int] = []
    task_ids.append(
        task_queue.enqueue_task(
            "count_words", {"text": "Hola mundo desde Space Lair"}, max_attempts=2
        )
    )
    task_ids.append(
        task_queue.enqueue_task(
            "count_words",
            {"text": "Python es un lenguaje de programación muy popular"},
            max_attempts=2,
        )
    )
    task_ids.append(
        task_queue.enqueue_task(
            "summarize_text",
            {
                "text": (
                    "La inteligencia artificial es una rama de la informática "
                    "que busca crear sistemas capaces de realizar tareas que "
                    "normalmente requieren inteligencia humana. Estos sistemas "
                    "aprenden de datos, reconocen patrones y toman decisiones "
                    "autónomas. En los últimos años, los modelos de lenguaje "
                    "grande han revolucionado el campo, permitiendo aplicaciones "
                    "como asistentes virtuales, traducción automática y generación "
                    "de contenido."
                )
            },
            max_attempts=2,
        )
    )
    click.echo(f"Tareas encoladas: {task_ids}")

    click.echo("\nEjecutando scheduler (max. 10 iteraciones)...\n")
    run_loop(modules, cap_map, max_iterations=10)

    click.echo("\n📊 Estado final:")
    _print_status_table(task_queue.all_tasks())


@cli.command()
def serve() -> None:
    """Ejecuta el scheduler en bucle infinito (CLI)."""
    click.echo("🚀 Space Lair - Servidor (scheduler)")
    click.echo("====================================")

    modules = load_modules()
    cap_map = capabilities_map(modules)
    click.echo(f"Modulos cargados: {', '.join(modules.keys()) or '(ninguno)'}")
    click.echo("Scheduler en ejecución. Ctrl+C para salir.\n")

    run_loop(modules, cap_map)


@cli.command()
@click.option("--port", default=8080, show_default=True, help="Puerto del servidor web.")
@click.option("--host", default="0.0.0.0", show_default=True, help="Host de escucha.")
def web(host: str, port: int) -> None:
    """Arranca el servidor web (frontend 8-bit + API + SSE)."""
    from frontend.frontend_api import run_server

    click.echo(f"🚀 Space Lair - Servidor Web (puerto {port})")
    click.echo("================================")
    click.echo(f"Abre http://localhost:{port} en tu navegador")
    click.echo("Ctrl+C para detener.\n")

    run_server(host=host, port=port)


@cli.command()
def status() -> None:
    """Muestra todas las tareas en una tabla formateada."""
    _print_status_table(task_queue.all_tasks())


@cli.command()
@click.argument("capability")
@click.argument("payload_json")
@click.option("--attempts", default=1, show_default=True, help="Máximo de reintentos.")
def enqueue(capability: str, payload_json: str, attempts: int) -> None:
    """Encola una nueva tarea.

    CAPABILITY: tipo de tarea (ej. count_words).
    PAYLOAD_JSON: payload como JSON (ej. '{"text": "hola"}').
    """
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as e:
        click.echo(f"❌ JSON inválido: {e}")
        sys.exit(1)

    if not isinstance(payload, dict):
        click.echo("❌ El payload debe ser un objeto JSON (dict)")
        sys.exit(1)

    # Validar con Pydantic si existe esquema para la capability
    try:
        payload = validate_payload(capability, payload)
    except Exception:
        pass  # No hay esquema definido; encolar tal cual

    task_id = task_queue.enqueue_task(capability, payload, max_attempts=attempts)
    click.echo(f"✅ Tarea creada con ID {task_id} (capability: {capability})")


@cli.command()
@click.option("--subject", default="operator", show_default=True, help="Sujeto del token.")
@click.option("--role", default="admin", show_default=True, help="Rol del token.")
@click.option("--expiry", default=None, type=int, help="Expiración en segundos (default: 24h).")
def token(subject: str, role: str, expiry: Optional[int]) -> None:
    """Genera un token JWT para autenticación."""
    jwt = generate_token(subject=subject, role=role, expiry=expiry)
    click.echo(jwt)


def _require_valid_token(token_value: str) -> dict:
    """Valida el token JWT; muestra error y sale si es inválido."""
    payload = verify_token(token_value)
    if payload is None:
        click.echo("❌ Token JWT inválido o expirado")
        sys.exit(1)
    return payload


@cli.command()
@click.argument("task_id", type=int)
@click.option("--token", "token_value", required=True, help="Token JWT de autenticación.")
def approve(task_id: int, token_value: str) -> None:
    """Aprueba una tarea pendiente de aprobación (requiere --token)."""
    _require_valid_token(token_value)
    task = _get_task_or_exit(task_id)

    if task["status"] != "pending_approval":
        click.echo(
            f"⚠️  La tarea {task_id} no esta pendiente de aprobacion "
            f"(estado: {task['status']})"
        )
        sys.exit(1)

    task_queue.approve_task(task_id)
    click.echo(f"✅ Tarea {task_id} aprobada y devuelta a la cola")


@cli.command()
@click.argument("task_id", type=int)
@click.option("--token", "token_value", required=True, help="Token JWT de autenticación.")
def reject(task_id: int, token_value: str) -> None:
    """Rechaza una tarea pendiente de aprobación (requiere --token)."""
    _require_valid_token(token_value)
    task = _get_task_or_exit(task_id)

    if task["status"] != "pending_approval":
        click.echo(
            f"⚠️  La tarea {task_id} no esta pendiente de aprobacion "
            f"(estado: {task['status']})"
        )
        sys.exit(1)

    task_queue.reject_task(task_id)
    click.echo(f"❌ Tarea {task_id} rechazada")


@cli.command()
def costs() -> None:
    """Muestra métricas de coste y tokens (total y por módulo)."""
    data = summarize_costs()

    click.echo(f"\n📊 Métricas de Coste")
    click.echo("=" * 60)
    click.echo(f"  Tareas completadas: {data['total_tasks_done']}")
    click.echo(f"  Coste total:        ${data['total_cost']:.6f}")
    click.echo(f"  Tokens (entrada):   {data['total_tokens_input']}")
    click.echo(f"  Tokens (salida):    {data['total_tokens_output']}")
    click.echo()

    if data.get("by_module"):
        click.echo(f"  {'Modulo':<20} {'Coste':>12} {'Input':>8} {'Output':>8} {'Tareas':>7}")
        click.echo("  " + "-" * 58)
        for mid, m in data["by_module"].items():
            click.echo(
                f"  {mid:<20} ${m['cost']:>10.6f} {m['tokens_input']:>8} "
                f"{m['tokens_output']:>8} {m['count']:>7}"
            )
    click.echo()


# ============================================
# Grupo: task
# ============================================
@cli.group()
def task() -> None:
    """Gestionar tareas."""


@task.command("list")
@click.option("--status", "status_filter", default=None, help="Filtrar por estado.")
@click.option("--capability", default=None, help="Filtrar por capability.")
def task_list(status_filter: Optional[str], capability: Optional[str]) -> None:
    """Lista todas las tareas (filtrable por estado y capability)."""
    tasks = task_queue.all_tasks()
    if status_filter:
        tasks = [t for t in tasks if t["status"] == status_filter]
    if capability:
        tasks = [t for t in tasks if t["capability"] == capability]

    if not tasks:
        click.echo("No hay tareas que coincidan con los filtros.")
        return

    click.echo(
        f"\n{'ID':>4}  {'CAPABILITY':<20} {'STATUS':<16} {'Modulo':<16} "
        f"{'INT':<5} {'COSTE':>10} {'TOKENS':>10}"
    )
    click.echo("-" * 85)
    for t in tasks:
        click.echo(
            f"{t['id']:>4}  {t['capability']:<20} {t['status']:<16} "
            f"{(t['module_id'] or '-'):<16} "
            f"{str(t['attempts']) + '/' + str(t['max_attempts']):<5} "
            f"${t.get('cost', 0) or 0:>9.6f} "
            f"{str(t.get('tokens_input', 0) or 0) + '/' + str(t.get('tokens_output', 0) or 0):>10}"
        )
    click.echo()


@task.command("approve")
@click.argument("task_id", type=int)
@click.option("--token", "token_value", required=True, help="Token JWT de autenticación.")
def task_approve(task_id: int, token_value: str) -> None:
    """Aprueba una tarea pendiente de aprobación (alias de 'approve')."""
    _require_valid_token(token_value)
    task = _get_task_or_exit(task_id)

    if task["status"] != "pending_approval":
        click.echo(
            f"⚠️  La tarea {task_id} no esta pendiente de aprobacion "
            f"(estado: {task['status']})"
        )
        sys.exit(1)

    task_queue.approve_task(task_id)
    click.echo(f"✅ Tarea {task_id} aprobada y devuelta a la cola")


@task.command("reject")
@click.argument("task_id", type=int)
@click.option("--token", "token_value", required=True, help="Token JWT de autenticación.")
def task_reject(task_id: int, token_value: str) -> None:
    """Rechaza una tarea pendiente de aprobación (alias de 'reject')."""
    _require_valid_token(token_value)
    task = _get_task_or_exit(task_id)

    if task["status"] != "pending_approval":
        click.echo(
            f"⚠️  La tarea {task_id} no esta pendiente de aprobacion "
            f"(estado: {task['status']})"
        )
        sys.exit(1)

    task_queue.reject_task(task_id)
    click.echo(f"❌ Tarea {task_id} rechazada")


# ============================================
# Grupo: module
# ============================================
@cli.group()
def module() -> None:
    """Gestionar módulos."""


@module.command("list")
def module_list() -> None:
    """Lista todos los módulos cargados."""
    modules = load_modules()
    if not modules:
        click.echo("No se han cargado módulos.")
        return

    click.echo(f"\n{'ID':<20} {'NOMBRE':<24} {'TIPO':<8} {'CAPABILITIES':<30}")
    click.echo("-" * 82)
    for mid, mod in modules.items():
        manifest = mod["manifest"]
        caps = ", ".join(manifest.get("capabilities", []))
        click.echo(
            f"{mid:<20} {manifest.get('name', mid):<24} "
            f"{manifest.get('type', '-'):<8} {caps:<30}"
        )
    click.echo()


@module.command("status")
def module_status() -> None:
    """Muestra el estado de salud de todos los módulos."""
    modules = load_modules()
    health = check_all_health(modules)

    if not health:
        click.echo("No se han cargado módulos.")
        return

    click.echo(f"\n{'MODULO':<20} {'ESTADO':<24} {'DETALLES':<40}")
    click.echo("-" * 84)
    for mid, info in health.items():
        checks_str = ", ".join(
            f"{k}: {'OK' if v is True else 'FAIL' if v is False else v}"
            for k, v in info.get("checks", {}).items()
        )
        status_icon = "🟢" if info["healthy"] else "🔴"
        click.echo(
            f"{mid:<20} {status_icon} {info.get('status', 'unknown'):<22} {checks_str:<40}"
        )
    click.echo()


# ============================================
# Grupo: workflow
# ============================================
@cli.group()
def workflow() -> None:
    """Gestionar workflows (orquestación de pasos)."""


@workflow.command("create")
@click.argument("name")
@click.argument("definition_file", type=click.Path(exists=True))
def workflow_create(name: str, definition_file: str) -> None:
    """Crea un workflow desde un archivo YAML/JSON.

    NAME: nombre del workflow.
    DEFINITION_FILE: ruta al archivo YAML/JSON con la definición.
    """
    try:
        with open(definition_file, "r", encoding="utf-8") as f:
            definition = f.read()
        workflow_id = create_workflow(name, definition)
        click.echo(f"✅ Workflow '{name}' creado con ID {workflow_id}")
    except Exception as e:
        click.echo(f"❌ Error creando workflow: {e}")
        sys.exit(1)


@workflow.command("list")
def workflow_list() -> None:
    """Lista todos los workflows."""
    workflows = all_workflows()
    if not workflows:
        click.echo("No hay workflows.")
        return

    click.echo(f"\n{'ID':>4}  {'NOMBRE':<24} {'STATUS':<12} {'PASOS':<6} {'CREADO':<20}")
    click.echo("-" * 70)
    for w in workflows:
        definition = w.get("definition", "{}")
        try:
            steps_count = len(json.loads(definition).get("steps", []))
        except (json.JSONDecodeError, TypeError):
            steps_count = 0
        click.echo(
            f"{w['id']:>4}  {w['name']:<24} {w['status']:<12} {steps_count:<6} {w['created_at']}"
        )
    click.echo()


@workflow.command("show")
@click.argument("workflow_id", type=int)
def workflow_show(workflow_id: int) -> None:
    """Muestra los detalles de un workflow."""
    wf = get_workflow(workflow_id)
    if wf is None:
        click.echo(f"❌ No existe el workflow {workflow_id}")
        sys.exit(1)

    click.echo(f"\n📋 Workflow #{wf['id']}: {wf['name']}")
    click.echo(f"   Estado: {wf['status']}")
    click.echo(f"   Creado: {wf['created_at']}")
    if wf.get("error"):
        click.echo(f"   Error: {wf['error']}")
    click.echo()

    click.echo(f"   {'STEP':<12} {'CAPABILITY':<20} {'STATUS':<10} {'DEPS':<20} {'RESULT':<30}")
    click.echo("   " + "-" * 92)
    for step in wf["steps"]:
        deps = ", ".join(step.get("depends_on", []) + step.get("parallel", [])) or "-"
        result = step.get("result")
        result_str = json.dumps(result, ensure_ascii=False)[:28] if result else "-"
        click.echo(
            f"   {step['step_id']:<12} {step['capability']:<20} {step['status']:<10} "
            f"{deps:<20} {result_str:<30}"
        )
    click.echo()


@workflow.command("run")
@click.argument("workflow_id", type=int)
def workflow_run(workflow_id: int) -> None:
    """Ejecuta un workflow pendiente."""
    modules = load_modules()
    cap_map = capabilities_map(modules)
    try:
        result = run_workflow(workflow_id, modules, cap_map)
        click.echo(f"\n✅ Workflow {workflow_id} finalizado: {result['status']}")
        for step in result["steps"]:
            status_icon = "✅" if step["status"] == "done" else "❌" if step["status"] == "error" else "⏭️" if step["status"] == "skipped" else "⏳"
            click.echo(f"   {status_icon} {step['step_id']}: {step['status']}")
    except Exception as e:
        click.echo(f"❌ Error ejecutando workflow: {e}")
        sys.exit(1)


@workflow.command("cancel")
@click.argument("workflow_id", type=int)
def workflow_cancel(workflow_id: int) -> None:
    """Cancela un workflow pendiente o en ejecución."""
    try:
        result = cancel_workflow(workflow_id)
        if result is None:
            click.echo(f"❌ No existe el workflow {workflow_id}")
            sys.exit(1)
        click.echo(f"❌ Workflow {workflow_id} cancelado")
    except Exception as e:
        click.echo(f"❌ Error cancelando workflow: {e}")
        sys.exit(1)


if __name__ == "__main__":
    cli()

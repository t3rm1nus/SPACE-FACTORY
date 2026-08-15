"""CLI único del orquestador de desarrollo autónomo.

Uso:
    python tools/orchestrator.py verify   # Valida infraestructura (sin tocar prod)
    python tools/orchestrator.py supervised
    python tools/orchestrator.py autonomous --allow
"""

from __future__ import annotations

import argparse
import sys
import os
import json
import re

# Permitir importar el paquete tools.dev aunque no esté en PYTHONPATH.
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_THIS, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.dev import config, runner, parsers, state, security, agent_loop, autonomous  # noqa: E402


def cmd_verify(args: argparse.Namespace) -> int:
    """Valida la infraestructura de desarrollo: parseos y captura, sin correr nada lento."""
    print("=" * 70)
    print("VERIFY — Validación de infraestructura de desarrollo (dry)")
    print("=" * 70)
    config.ensure_dirs()
    # 1) pytest parser contra salida real conocida
    sample_pytest = "12 passed, 2 skipped in 0.45s"
    p = parsers.parse_pytest(["tests"], sample_pytest, 0)
    print(f"  pytest parse: summary='{p['pytest_summary']}' status={p['status']} "
          f"passed={p['passed']}")
    # 2) e2e parser contra reporte existente
    report = parsers.load_e2e_report()
    if report is not None:
        e = parsers.parse_e2e(report)
        print(f"  e2e parse: status={e['e2e_status']} stage={e['failed_stage']} "
              f"word_count={e['word_count']} report={os.path.basename(e['report_path'])}")
    else:
        print("  e2e parse: (sin reporte e2e_001_report.json — E2E no está disponible todavía)")

    # 3) estado inicial
    st = state.load_state()
    print(f"  state: phase={st.get('CURRENT_PHASE')} test={st.get('TEST_STATUS')} e2e={st.get('E2E_STATUS')}")
    print(f"  state.json -> {config.STATE_JSON}")
    print(f"  status.md   -> {config.STATUS_MD}")

    # 4) regla de protección
    cw = os.path.join(config.ROOT, "modules", "chapter_writer", "main.py")
    print(f"  security: chapter_writer es protegido={security.is_protected(cw)}")
    print("  => Infraestructura operativa.")
    return 0


def cmd_supervised(args: argparse.Namespace) -> int:
    return agent_loop.run_supervised(
        run_tests=not args.skip_tests, run_e2e=not args.skip_e2e
    )


def cmd_autonomous(args: argparse.Namespace) -> int:
    return autonomous.run_autonomous(
        max_iterations=args.max_iterations,
        allow=bool(args.allow),
        dry_run=not bool(args.allow),
    )


def cmd_status(args: argparse.Namespace) -> int:
    st = state.load_state()
    print(json.dumps(st, ensure_ascii=False, indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    config.ensure_dirs()
    import datetime
    st = state.load_state()
    # Forzar sincronización con valores reales por si cambió algo externamente.
    st["LAST_VERIFIED"] = datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    state.save_state(st)
    print(f"Estado inicializado/sincronizado en {config.STATUS_MD}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="Orquestador de desarrollo autónomo (supervisado).",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_verify = sub.add_parser("verify", help="Valida infraestructura (dry, rápido).")
    p_verify.set_defaults(func=cmd_verify)

    p_super = sub.add_parser("supervised", help="Modo supervisado (PLAN/TEST/E2E/PROPOSICIÓN).")
    p_super.add_argument("--skip-tests", action="store_true")
    p_super.add_argument("--skip-e2e", action="store_true")
    p_super.set_defaults(func=cmd_supervised)

    p_auto = sub.add_parser("autonomous", help="Modo autónomo (diseñado).")
    p_auto.add_argument("--max-iterations", type=int,
                        default=config.AUTONOMOUS_DEFAULT_MAX_ITERATIONS)
    p_auto.add_argument(config.AUTONOMOUS_ALLOW_FLAG, dest="allow",
                        action="store_true",
                        help="Activa aplicación automática de cambios (USAR CON CUIDADO).")
    p_auto.set_defaults(func=cmd_autonomous)

    p_status = sub.add_parser("status", help="Muestra state.json.")
    p_status.set_defaults(func=cmd_status)

    p_init = sub.add_parser("init", help="Inicializa/sincroniza estado.")
    p_init.set_defaults(func=cmd_init)

    # ---- Autópilot ----
    p_auto2 = sub.add_parser("autopilot", help="Motor de orquestación autónomo (tasks declarativas).")
    p_auto2_sub = p_auto2.add_subparsers(dest="subcmd")
    p_start = p_auto2_sub.add_parser("start", help="Ejecuta una task desde un JSON.")
    p_start.add_argument("task_file", help="Ruta al JSON de la task.")
    p_start.set_defaults(func=cmd_autopilot_start)
    p_status = p_auto2_sub.add_parser("status", help="Muestra state.json de una task.")
    p_status.add_argument("task_id", help="ID de la task.")
    p_status.set_defaults(func=cmd_autopilot_status)
    p_resume = p_auto2_sub.add_parser("resume", help="Reanuda una task pausada (HUMAN_REQUIRED).")
    p_resume.add_argument("task_id", help="ID de la task.")
    p_resume.set_defaults(func=cmd_autopilot_resume)
    p_approve = p_auto2_sub.add_parser("approve", help="Aprobar y reanudar una task pausada.")
    p_approve.add_argument("task_id", help="ID de la task.")
    p_approve.set_defaults(func=cmd_autopilot_approve)
    p_pause = p_auto2_sub.add_parser("pause", help="Pausar una task en ejecución.")
    p_pause.add_argument("task_id", help="ID de la task.")
    p_pause.set_defaults(func=cmd_autopilot_pause)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


# ---------------------------------------------------------------------------
# Autópilot CLI handlers
# ---------------------------------------------------------------------------
def cmd_autopilot_start(args: argparse.Namespace) -> int:
    from tools.dev import autopilot as ap_module
    with open(args.task_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    task = ap_module.Task.from_dict(data)
    result = ap_module.run_task(task)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] in ("completed", "human_required") else 1


def cmd_autopilot_status(args: argparse.Namespace) -> int:
    from tools.dev import autopilot as ap_module
    d = os.path.join(ap_module.config.ROOT, "data", "autopilot", args.task_id, "state.json")
    if not os.path.exists(d):
        print(f"Task {args.task_id} no encontrada.")
        return 1
    print(open(d, "r", encoding="utf-8").read())
    return 0


def cmd_autopilot_resume(args: argparse.Namespace) -> int:
    from tools.dev import autopilot as ap_module
    result = ap_module.resume_task(args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("outcome") == "resumed" else 1


def cmd_autopilot_approve(args: argparse.Namespace) -> int:
    from tools.dev import autopilot as ap_module
    # approve implica transicionar de HUMAN_REQUIRED a RETRYING
    result = ap_module.resume_task(args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("outcome") == "resumed" else 1


def cmd_autopilot_pause(args: argparse.Namespace) -> int:
    from tools.dev import autopilot as ap_module
    result = ap_module.pause_task(args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("outcome") == "paused" else 1


if __name__ == "__main__":
    sys.exit(main())
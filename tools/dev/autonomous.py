"""Modo AUTÓNOMO (diseñado) — NO se activa sin la bandera --allow.

Ciclo: PLAN -> TEST -> E2E -> DIAGNÓSTICO -> CAMBIO MÍNIMO -> TEST -> E2E -> REPETIR.

Incluye: límite de iteraciones, límite de tiempo, archivos protegidos,
detención controlada, registro completo de iteraciones y rollback por copia.

Por defecto ``allow=False`` => se comporta como dry-run / diseño, NO aplica
cambios de código. Sólo en modo autónomo activado y aplicando cambios se
respeta ``ALLOWED_AUTO_EDIT_DIRS`` y se protege ``PROTECTED_FILES``.
"""

from __future__ import annotations

import datetime
import os
import shutil
import time
from typing import Any

from . import agent_loop, config, state


def _elapsed_sec(started: float) -> float:
    return time.time() - started


def run_autonomous(
    *,
    max_iterations: int = config.AUTONOMOUS_DEFAULT_MAX_ITERATIONS,
    allow: bool = False,
    dry_run: bool = True,
    targets: list[str] | None = None,
) -> int:
    if not allow:
        print("MODO AUTÓNOMO: DISEÑADO pero NO ACTIVADO.")
        print(f"  Requiere la bandera {config.AUTONOMOUS_ALLOW_FLAG}. En esta fase el "
              "modo autónomo sólo se valida como dry-run.")
        allow, dry_run = False, True

    max_time_sec = config.AUTONOMOUS_DEFAULT_MAX_TIME_SEC
    started = time.time()
    print("=" * 70)
    print(f"MODO AUTÓNOMO (allow={allow}, dry_run={dry_run})")
    print(f"  max_iterations={max_iterations}, max_time={max_time_sec}s")
    print("=" * 70)

    for i in range(1, max_iterations + 1):
        if _elapsed_sec(started) > max_time_sec:
            print(f"[iter {i}] Límite de tiempo alcanzado; deteniendo.")
            break

        print(f"\n[ITERACIÓN {i}/{max_iterations}]")
        obs = agent_loop.observe(run_tests=True, run_e2e=True)
        diag = obs.get("diagnosis") or {}
        proposal = agent_loop.build_proposal(obs)
        print(f"  Diagnóstico: {diag.get('root_cause')}")
        print(f"  Propuesta: {proposal}")

        e2e_status = (obs.get("e2e_res") or {}).get("status")
        iter_entry = {
            "timestamp": datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
            "iteration": i,
            "proposal": proposal,
            "root_cause": diag.get("root_cause"),
            "e2e_status": e2e_status,
            "pytest": obs.get("pytest_res"),
        }
        state.add_iteration(state.load_state(), iter_entry)

        if e2e_status == "completed":
            print("E2E completado. Criterio de éxito alcanzado. Deteniendo.")
            return 0

        if dry_run:
            print("  [dry-run] NO se modifica código automáticamente.")
            continue

        # --- CAMBIO MÍNIMO (solo en modo autónomo real) ---
        # 1) Huella de seguridad de archivos protegidos.
        for ef in config.PROTECTED_FILES:
            if os.path.isfile(ef):
                state_key = f"fingerprint:{os.path.relpath(ef, config.ROOT)}"
                # (aquí iría la aplicación concreta; se omite por seguridad)
        # 2) Cambio: copia de seguridad, aplicar, TEST -> E2E.
        # (No implementado: requiere LLM interno y aprobación explícita.)
        print("  [not-implemented] aplicación automática desactivada por seguridad.")

        if i >= max_iterations:
            print(f"[iter {i}] Máximo de iteraciones alcanzado. Deteniendo.")

    print("\nFIN del ciclo autónomo (dry-run/simulado).")
    return 2


def backup_file(path: str) -> str | None:
    """Copia de seguridad de rollback de un fichero antes de editarlo."""
    if not os.path.exists(path):
        return None
    config.ensure_dirs()
    bk_dir = os.path.join(config.STATE_DIR, "backups")
    os.makedirs(bk_dir, exist_ok=True)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(bk_dir, f"{os.path.basename(path)}.bak_{ts}")
    shutil.copy2(path, dst)
    return dst

"""Ciclo de agente: modo supervisado (activo) y modo autónomo (diseño).

Modo supervisado (por defecto):
    PLAN -> TEST -> E2E -> DIAGNÓSTICO -> PROPUESTA -> ESPERAR APROBACIÓN

Modo autónomo (diseñado, NO se activa sin --allow-autonomous):
    PLAN -> TEST -> E2E -> DIAGNÓSTICO -> CAMBIO MÍNIMO -> TEST -> E2E -> REPETIR
    Con máximo de iteraciones, máximo de tiempo, archivos protegidos, detención
    controlada, registro completo y rollback por copia.
"""

from __future__ import annotations

import datetime
import os
from typing import Any, Optional

from . import config, parsers, runner, state


def _now() -> str:
    return datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def observe(*, run_tests: bool = True, run_e2e: bool = True) -> dict[str, Any]:
    """Ejecuta tests y/o E2E, captura resultados y arroja un diagnóstico."""
    pytest_res: Optional[dict] = None
    pytest_run = None
    if run_tests:
        print("==> TEST (pytest completo)")
        pytest_run = runner.run_full_tests()
        runner.print_summary(pytest_run)
        pytest_res = parsers.parse_pytest(
            ["tests"], pytest_run["stdout"], pytest_run["returncode"]
        )
        print(f"    pytest: {pytest_res['pytest_summary']} -> {pytest_res['status']}")

    e2e_res: Optional[dict] = None
    e2e_run = None
    if run_e2e:
        print("==> E2E (run_e2e_001_editorial.py)")
        e2e_run = runner.run_e2e()
        runner.print_summary(e2e_run)
        e2e_res = parsers.parse_e2e()
        print(f"    e2e.status={e2e_res.get('status')} stage={e2e_res.get('failed_stage')} "
              f"wc={e2e_res.get('word_count')}")

    diag = parsers.diagnose(e2e_res or {}, pytest_res)
    return {
        "pytest_res": pytest_res,
        "pytest_run": pytest_run,
        "e2e_res": e2e_res,
        "e2e_run": e2e_run,
        "diagnosis": diag,
    }


def update_state_from_observation(
    obs: dict[str, Any],
    *,
    phase: str,
    objective: str,
    change_note: str,
    files_modified: list[str],
    constraints: list[str],
) -> dict[str, Any]:
    """Vuelca la observación al estado persistente."""
    st = state.load_state()
    pytest_res = obs.get("pytest_res") or {}
    e2e_res = obs.get("e2e_res") or {}
    diag = obs.get("diagnosis") or {}

    st["CURRENT_PHASE"] = phase
    st["CURRENT_OBJECTIVE"] = objective
    st["LAST_CHANGE"] = change_note
    st["LAST_VERIFIED"] = _now()
    st["FILES_MODIFIED"] = files_modified or st.get("FILES_MODIFIED", [])
    st["CONSTRAINTS"] = constraints or st.get("CONSTRAINTS", [])

    if pytest_res:
        st["TEST_STATUS"] = pytest_res.get("status")
        st["TEST_COUNT"] = pytest_res.get("passed", st.get("TEST_COUNT"))
    if e2e_res:
        st["E2E_STATUS"] = f"{e2e_res.get('e2e_status')} ({e2e_res.get('status')})"
        st["FAILED_STAGE"] = e2e_res.get("failed_stage") or st.get("FAILED_STAGE")
        st["ROOT_CAUSE"] = diag.get("root_cause", "")
        st["NEXT_ACTION"] = (
            "E2E completado con éxito." if e2e_res.get("status") == "completed"
            else f"E2E no completado ({e2e_res.get('failed_stage')}). Revisar y planificar cambio mínimo."
        )
        if e2e_res.get("docx_path"):
            st["NEXT_ACTION"] += f" DOCX: {e2e_res.get('docx_path')}"

    state.save_state(st)
    return st
def build_proposal(obs: dict[str, Any]) -> str:
    """Formula una PROPUESTA (diagnóstico + recomendación) sin aplicar cambios."""
    diag = obs.get("diagnosis") or {}
    e2e = obs.get("e2e_res") or {}
    stage = e2e.get("failed_stage")
    if e2e.get("status") == "completed":
        return "E2E completado; no se requiere más iteración."
    if stage == "chapter":
        wc = e2e.get("word_count")
        ph = e2e.get("placeholder_detected")
        root = f"Causa candidata: {diag.get('root_cause')}"
        if ph:
            return f"Chapter Writer falla por PLACEHOLDER en el capítulo. {root}"
        return (
            f"Chapter Writer no alcanza 1500 palabras (word_count={wc}). Continuaciones "
            f"rechazadas (heading/duplicado). {root}"
        )
    return f"E2E detenido en etapa '{stage}'. {diag.get('root_cause')}"


def run_supervised(*, run_tests: bool = True, run_e2e: bool = True) -> int:
    """PLAN -> TEST -> E2E -> DIAGNÓSTICO -> PROPUESTA -> ESPERAR APROBACIÓN."""
    print("=" * 70)
    print("MODO SUPERVISADO")
    print("=" * 70)
    print("PLAN: leer estado, NO modificar código funcional todavía.")
    st = state.load_state()
    print(f"  Fase actual: {st.get('CURRENT_PHASE')} | Objetivo: {st.get('CURRENT_OBJECTIVE')}")

    obs = observe(run_tests=run_tests, run_e2e=run_e2e)
    update_state_from_observation(
        obs,
        phase=st.get("CURRENT_PHASE", "7.9D.7"),
        objective=st.get("CURRENT_OBJECTIVE", "diagnóstico"),
        change_note=f"Observación supervisada {_now()}",
        files_modified=[],
        constraints=st.get("CONSTRAINTS", []),
    )
    proposal = build_proposal(obs)
    st = state.load_state()
    st["PROPOSAL"] = proposal
    state.save_state(st)

    print("\nDIAGNÓSTICO:")
    print(f"  {obs['diagnosis'].get('root_cause')}")
    print("\nPROPUESTA (ESPERAR APROBACIÓN):")
    print(f"  {proposal}")
    print("\nEstado actualizado en PROJECT_STATUS.md")
    print("=> DETENIDO. Esperando decisión humana antes de aplicar cualquier cambio.")
    return 0 if (obs.get("e2e_res") or {}).get("status") == "completed" else 1
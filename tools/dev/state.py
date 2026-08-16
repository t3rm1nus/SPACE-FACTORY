"""Estado persistente del proyecto (fuente única de estado).

Almacén estructurado en ``data/dev_ops/state.json`` y render legible en
``PROJECT_STATUS.md``. Ambos se mantienen sincronizados. No depende de la
memoria de ninguna conversación.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from . import config

DEFAULT_STATE: dict[str, Any] = {
    "CURRENT_PHASE": "7.9D.7",
    "CURRENT_OBJECTIVE": "Diagnóstico de continuaciones; NO modificar chapter_writer funcional",
    "STATUS": "KNOWN_BAD",
    "TEST_STATUS": "PASS",
    "TEST_COUNT": 379,
    "E2E_STATUS": "FAIL",
    "FAILED_STAGE": "chapter",
    "ROOT_CAUSE": ("modelo qwen-agent genera continuaciones con headings o contenido duplicado"),
    "LAST_CHANGE": "(inicial)",
    "FILES_MODIFIED": [],
    "KNOWN_GOOD": [
        "Suite de tests pasando (conteo exacto dinámico por checkpoint)",
        "7.9D.5 preservada",
        "7.9D.6 estructura H1/H2 correcta",
    ],
    "KNOWN_BAD": [
        "E2E falla en Chapter Writer por longitud y rechazos de continuación",
    ],
    "CONSTRAINTS": [
        "NO modificar comportamiento funcional de modules/chapter_writer/main.py en esta fase",
        "NO resolver 7.9D.7 todavía",
        "No modificar tests para forzar PASS",
    ],
    "NEXT_ACTION": "Diagnóstico de continuaciones resuelto; decidir siguiente paso tras aprobación.",
    "SUCCESS_CRITERIA": [
        "Infraestructura de desarrollo operativa (estado, protocolo, orquestador, captura, PASS/FAIL)",
    ],
    "LAST_VERIFIED": datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
    "PROPOSAL": "",
    "MODE": "supervised",
    "ITERATIONS": [],
}


def _empty_state() -> dict[str, Any]:
    # Deep-copy del estado por defecto para que las listas no se compartan.
    return json.loads(json.dumps(DEFAULT_STATE))


def load_state() -> dict[str, Any]:
    if os.path.exists(config.STATE_JSON):
        try:
            with open(config.STATE_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = _empty_state()
            merged.update(data)
            return merged
        except (json.JSONDecodeError, OSError):
            return _empty_state()
    return _empty_state()


def save_state(state: dict[str, Any]) -> None:
    config.ensure_dirs()
    state = {k: v for k, v in state.items() if v is not None}
    tmp = config.STATE_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config.STATE_JSON)
    render_status_md(state)


def add_iteration(state: dict[str, Any], entry: dict[str, Any]) -> None:
    config.ensure_dirs()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(config.ITERATION_DIR, f"iter_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    iters = state.get("ITERATIONS") or []
    iters.append({"file": os.path.relpath(path, config.ROOT), **entry})
    state["ITERATIONS"] = iters[-100:]
    save_state(state)


def _list_md(values: list[str]) -> str:
    if not values:
        return "(ninguno)"
    return "\n".join(f"- {v}" for v in values)


def render_status_md(state: dict[str, Any]) -> str:
    ts = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    sections = [
        "# PROJECT STATUS — Space Lair (desarrollo autónomo)",
        "",
        "> Fuente única de estado persistente. Automático: `python tools/orchestrator.py`.",
        "",
        "## Estado",
        f"- **CURRENT_PHASE**: {state.get('CURRENT_PHASE', '')}",
        f"- **CURRENT_OBJECTIVE**: {state.get('CURRENT_OBJECTIVE', '')}",
        f"- **STATUS**: {state.get('STATUS', '')}",
        f"- **TEST_STATUS**: {state.get('TEST_STATUS', '')}",
        f"- **TEST_COUNT**: {state.get('TEST_COUNT', '')}",
        f"- **E2E_STATUS**: {state.get('E2E_STATUS', '')}",
        f"- **FAILED_STAGE**: {state.get('FAILED_STAGE', '')}",
        f"- **ROOT_CAUSE**: {state.get('ROOT_CAUSE', '')}",
        f"- **LAST_CHANGE**: {state.get('LAST_CHANGE', '')}",
        f"- **LAST_VERIFIED**: {state.get('LAST_VERIFIED', '')}",
        f"- **MODE**: {state.get('MODE', '')}",
        "",
        "## Archivos modificados",
        _list_md(state.get('FILES_MODIFIED', [])),
        "",
        "## Conocido bueno (KNOWN_GOOD)",
        _list_md(state.get('KNOWN_GOOD', [])),
        "",
        "## Conocido malo (KNOWN_BAD)",
        _list_md(state.get('KNOWN_BAD', [])),
        "",
        "## Restricciones (CONSTRAINTS)",
        _list_md(state.get('CONSTRAINTS', [])),
        "",
        "## Próxima acción (NEXT_ACTION)",
        state.get('NEXT_ACTION', ''),
        "",
        "## Criterio de éxito (SUCCESS_CRITERIA)",
        _list_md(state.get('SUCCESS_CRITERIA', [])),
        "",
        "## Propuesta actual (PROPOSAL)",
        state.get('PROPOSAL', '') or '(sin propuesta)',
        "",
        "## Iteraciones registradas",
        str(len(state.get('ITERATIONS', []))),
        "",
        f"*Generado automáticamente: {ts}*",
    ]
    with open(config.STATUS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(sections))
    return config.STATUS_MD
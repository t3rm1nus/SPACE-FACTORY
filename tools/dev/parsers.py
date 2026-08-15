"""Parseo de resultados de pytest y del reporte E2E.

Detecta PASS/FAIL de manera determinista (sin depender de listas predefinidas)
leyendo la salida real de los procesos y el JSON de reporte.
"""

from __future__ import annotations

import json
import re
import os
from typing import Any, Optional

from . import config


# ------------------------------------------------------------ pytest parsing
def parse_pytest_result(output: str, returncode: int) -> dict[str, Any]:
    """Interpreta la última línea de resumen de pytest (ej. '379 passed',
    '1 failed, 378 passed'). Devuelve conteos y estado."""
    summary_line = ""
    for line in reversed(output.splitlines()):
        if "passed" in line or "failed" in line or "error" in line:
            if re.search(r"\d+\s+(passed|failed|error)", line):
                summary_line = line.strip()
                break

    def _count(word: str) -> int:
        m = re.search(rf"(\d+)\s+{word}", summary_line)
        return int(m.group(1)) if m else 0

    passed = _count("passed")
    failed = _count("failed")
    errors = _count("error")
    # pytest suele decir 'errors' plural; cubrimos ambos
    if errors == 0:
        m = re.search(r"(\d+)\s+error", summary_line)
        errors = int(m.group(1)) if m else 0

    status = "PASS" if ((returncode == 0) and failed == 0 and errors == 0) else "FAIL"
    return {
        "pytest_summary": summary_line or "(sin resumen)",
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "returncode": returncode,
        "status": status,
    }


def parse_pytest(paths: list[str], output: str, returncode: int) -> dict[str, Any]:
    res = parse_pytest_result(output, returncode)
    res["targets"] = paths
    return res


# -------------------------------------------------------------- E2E parsing
def load_e2e_report(path: Optional[str] = None) -> Optional[dict[str, Any]]:
    path = path or config.E2E_REPORT
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def parse_e2e(report: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Deriva el estado E2E a partir del reporte JSON.

    status: 'completed' -> PASS, cualquier otro -> FAIL.
    Extrae failed_stage, error, calidad y rutas relevantes.
    """
    report = report or load_e2e_report()
    if report is None:
        return {
            "e2e_status": "NO_REPORT",
            "status": "error",
            "failed_stage": None,
            "error": "No se encontró e2e_001_report.json",
            "word_count": None,
            "docx_path": None,
            "last_checkpoint": None,
        }

    status = report.get("status", "error")
    e2e_status = "PASS" if status == "completed" else "FAIL"
    return {
        "e2e_status": e2e_status,
        "status": status,
        "failed_stage": report.get("failed_stage"),
        "error": report.get("error"),
        "word_count": report.get("chapter_word_count"),
        "placeholder_detected": report.get("chapter_placeholder_detected"),
        "docx_path": report.get("docx_path"),
        "docx_status": report.get("docx_status"),
        "last_checkpoint": report.get("last_checkpoint"),
        "report_path": config.E2E_REPORT,
    }


# ------------------------------------------------------------ diagnostico
def diagnose(e2e: dict[str, Any], pytest: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Formula un diagnóstico breve (root cause) a partir de los resultados."""
    notes: list[str] = []

    if pytest is not None:
        if pytest.get("status") == "PASS":
            notes.append(f"Tests: OK (passed={pytest.get('passed')}).")
        else:
            notes.append(f"Tests: FAIL (failed={pytest.get('failed')}, errors={pytest.get('errors')}).")

    stage = e2e.get("failed_stage")
    if e2e.get("e2e_status") == "PASS":
        notes.append("E2E completado (status=completed).")
    else:
        notes.append(f"E2E NO completado (status={e2e.get('status')}).")
        if stage:
            notes.append(f"Etapa fallida: {stage}.")
        err = e2e.get("error")
        if err:
            notes.append(f"Error: {err[:300]}")

    if stage == "chapter" and e2e.get("placeholder_detected"):
        notes.append("Causa raíz candidata: placeholder en capítulo generado.")
    elif stage == "chapter":
        wc = e2e.get("word_count")
        notes.append(f"Capítulo: word_count={wc} (objetivo mínimo=1500).")

    root_cause = ("; ".join(notes)) or "Sin diagnóstico (sin resultados)."
    return {"summary": " ".join(notes), "root_cause": root_cause, "failed_stage": stage}
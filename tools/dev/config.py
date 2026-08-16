"""Configuración central de la infraestructura de desarrollo.

Rutas, comandos, archivos protegidos y límites de seguridad del orquestador.
"""

from __future__ import annotations

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Archivos de estado / protocolo
STATUS_MD = os.path.join(ROOT, "PROJECT_STATUS.md")
AGENTS_MD = os.path.join(ROOT, "AGENTS.md")
STATE_DIR = os.path.join(ROOT, "data", "dev_ops")
STATE_JSON = os.path.join(STATE_DIR, "state.json")
LOG_DIR = os.path.join(STATE_DIR, "logs")
ITERATION_DIR = os.path.join(STATE_DIR, "iterations")

# Comandos de ejecución
PYTHON = "python"
PYTEST_CMD = [PYTHON, "-m", "pytest"]
E2E_CMD = [PYTHON, os.path.join(ROOT, "run_e2e_001_editorial.py")]
E2E_REPORT = os.path.join(ROOT, "e2e_001_report.json")

TEST_DIR = os.path.join(ROOT, "tests")

# Límites de seguridad del modo autónomo (diseñado; no se activa sin `--allow`)
AUTONOMOUS_DEFAULT_MAX_ITERATIONS = 3
AUTONOMOUS_DEFAULT_MAX_TIME_SEC = 60 * 25
AUTONOMOUS_ALLOW_FLAG = "--allow-autonomous"

# Archivos protegidos: el orquestador no debe modificarlos salvo listado
# específico y supervisado. Se usan como referencia para validar cambios.
PROTECTED_FILES = {
    os.path.join(ROOT, "modules", "chapter_writer", "main.py"),
    os.path.join(ROOT, "tests"),
}
ALLOWED_AUTO_EDIT_DIRS = {
    os.path.join(ROOT, "tools"),
}

# Módulos fuera de alcance en esta tarea (no editables automáticamente)
OUT_OF_SCOPE_MODULES = [
    "research",
    "fact_checker",
    "editor",
    "document_builder",
    "quality_control",
    "pdf_builder",
    "image_generator",
    "book_planner",
    "translator",
    "text_summarizer",
    "word_counter",
    "image_planner",
    "mcp_demo",
    "mcp_external",
]


def ensure_dirs() -> None:
    for d in (STATE_DIR, LOG_DIR, ITERATION_DIR):
        os.makedirs(d, exist_ok=True)
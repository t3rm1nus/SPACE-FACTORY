"""Validaciones de seguridad: archivos protegidos, huella de tests, límites.

Proporciona verificaciones que el agente debe ejecutar antes/después de
modificar código, para cumplir las protecciones obligatorias sin depender de
la memoria del agente.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional

from . import config


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def is_protected(path: str) -> bool:
    """True si ``path`` cae bajo PROTECTED_FILES (no debe auto-editarse)."""
    norm = os.path.normpath(path)
    for protected in config.PROTECTED_FILES:
        p = os.path.normpath(protected)
        if norm == p or norm.startswith(p + os.sep):
            return True
    return False


def is_out_of_scope(path: str) -> bool:
    """True si el fichero pertenece a un módulo fuera de alcance."""
    norm = os.path.normpath(path)
    for mod in config.OUT_OF_SCOPE_MODULES:
        prefix = os.path.normpath(os.path.join(config.ROOT, "modules", mod))
        if norm == prefix or norm.startswith(prefix + os.sep):
            return True
    return False


def assert_change_permitted(path: str) -> bool:
    """Regla central: el orquestador/agente solo auto-edita bajo ALLOWED_AUTO_EDIT_DIRS.
    Cualquier cambio fuera de ahí exige aprobación humana explícita."""
    norm = os.path.normpath(path)
    for allowed in config.ALLOWED_AUTO_EDIT_DIRS:
        a = os.path.normpath(allowed)
        if norm == a or norm.startswith(a + os.sep):
            return True
    return False


def test_signature(current: Optional[dict] = None) -> dict:
    """Huella del estado de tests (para detectar cambios no justificados)."""
    pass  # marcado como hook para calcular conteo real vía runner+parsers


VALIDATION_RULES = [
    "No modificar tests únicamente para obtener PASS.",
    "No reducir requisitos de aceptación ni ocultar errores.",
    "No desactivar Quality Gate ni eliminar validaciones.",
    "No declarar PASS sin ejecutar la prueba correspondiente.",
    "Todo cambio se registra con WHY/WHAT/FILES/VERIFICATION/RESULT.",
]
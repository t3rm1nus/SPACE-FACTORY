"""Logger estructurado de Space Lair.

Configura logging en formato JSON con campos de contexto
(task_id, module_id, capability) para facilitar el filtrado.
"""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Optional

# Niveles válidos
VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Campos de contexto adicionales soportados
CONTEXT_FIELDS = ("task_id", "module_id", "capability")


def _load_env_level(default: str = "INFO") -> str:
    """Carga LOG_LEVEL desde variables de entorno (y .env si dotenv está disponible)."""
    level = os.environ.get("LOG_LEVEL", default).upper()
    try:
        from dotenv import load_dotenv

        load_dotenv()
        level = os.environ.get("LOG_LEVEL", default).upper()
    except ImportError:
        pass

    if level not in VALID_LEVELS:
        level = default
    return level


class JsonFormatter(logging.Formatter):
    """Formatter que serializa cada registro en una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Añadir campos de contexto si están presentes
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                log_entry[field] = value

        # Añadir información de excepción si existe
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(level: Optional[str] = None) -> None:
    """Configura el logging estructurado (JSON) a nivel raíz.

    Args:
        level: Nivel de log explícito ('DEBUG', 'INFO', ...).
               Si no se pasa, se lee de LOG_LEVEL en .env (por defecto 'INFO').
    """
    if level is None:
        level = _load_env_level()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.setLevel(getattr(logging, level))

    # Eliminar handlers existentes para evitar duplicados
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Retorna un logger con el nombre dado (asume setup_logging ya llamado)."""
    return logging.getLogger(name)


def log(
    logger: logging.Logger,
    level: int,
    msg: str,
    *,
    task_id: Optional[int] = None,
    module_id: Optional[str] = None,
    capability: Optional[str] = None,
    **extra: Any,
) -> None:
    """Emite un log con contexto estructurado.

    Args:
        logger: Logger a usar.
        level: Nivel de logging (logging.INFO, logging.ERROR, etc.).
        msg: Mensaje del log.
        task_id: ID de la tarea (opcional).
        module_id: ID del módulo (opcional).
        capability: Capability de la tarea (opcional).
        **extra: Campos adicionales de contexto.
    """
    context: dict[str, Any] = {}
    if task_id is not None:
        context["task_id"] = task_id
    if module_id is not None:
        context["module_id"] = module_id
    if capability is not None:
        context["capability"] = capability
    context.update(extra)

    logger.log(level, msg, extra=context)
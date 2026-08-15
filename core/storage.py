"""Persistencia de resultados grandes fuera de SQLite.

Cuando el JSON serializado (UTF-8) de un resultado supera estrictamente 1 MiB,
se externaliza a ``data/results/{task_id}.json`` y en la columna ``result`` de
SQLite solo se guarda una referencia (ver ``core/task_queue.py``).

El tamaño y la serialización se centralizan aquí (``serialize`` / funciones de
umbral) para que el umbral y el contenido persistido nunca puedan divergir.
"""

from __future__ import annotations

import json
import os

from core.database import DATA_DIR

# Resultados cuyo JSON serializado UTF-8 supere estrictamente este umbral se
# externalizan a disco; si es <= se dejan inline tal y como hoy.
LARGE_RESULT_THRESHOLD_BYTES = 1_048_576  # 1 MiB

_ENCODING = "utf-8"

# Variable de entorno para redirigir el directorio de resultados (usos: tests).
_ENV_RESULTS_DIR = "SPACE_LAIR_RESULTS_DIR"
_DEFAULT_RESULTS_DIR = os.path.join(DATA_DIR, "results")

# Claves del marcador de referencia externalizada compartidas con task_queue.
REF_KEY = "_ref_external"
REF_PATH_KEY = "path"


class StorageError(Exception):
    """Error de persistencia o lectura de un resultado externalizado."""


def _results_dir() -> str:
    """Directorio de resultados (sobreescribible vía entorno)."""
    return os.environ.get(_ENV_RESULTS_DIR) or _DEFAULT_RESULTS_DIR


def _result_path(task_id: int) -> str:
    """Ruta física absoluta del resultado de ``task_id`` saneado.

    El nombre de archivo se deriva EXCLUSIVAMENTE de ``int(task_id)``, de modo
    que es imposible inyectar separadores o ``..`` (path traversal). Nunca se
    confía en un path almacenado en la base de datos.
    """
    tid = int(task_id)  # rechaza valores no numéricos / con separadores
    return os.path.join(_results_dir(), f"{tid}.json")


def result_relative_path(task_id: int) -> str:
    """Ruta relativa (legible) usada como valor de la referencia en SQLite."""
    return os.path.join("data", "results", f"{int(task_id)}.json")


def serialize(data) -> str:
    """Serializa un resultado con la semántica de ``complete_task``.

    Mantiene ``ensure_ascii=False`` y ``default=str`` para no alterar el
    comportamiento observable actual.
    """
    return json.dumps(data, ensure_ascii=False, default=str)


def serialized_bytes(result) -> int:
    """Tamaño en bytes UTF-8 del JSON serializado de ``result``."""
    return len(serialize(result).encode(_ENCODING))


def should_externalize(result) -> bool:
    """True si ``result`` debe externalizarse (> 1 MiB estricto)."""
    return serialized_bytes(result) > LARGE_RESULT_THRESHOLD_BYTES


def save_result(task_id: int, data) -> str:
    """Persiste ``data`` en ``data/results/{task_id}.json`` de forma atómica.

    Escritura atómica: archivo temporal en el mismo directorio, flush + fsync
    y ``os.replace`` (renombrado atómico en el mismo sistema de archivos).

    Devuelve la ruta relativa del archivo (valor de la referencia).

    Raises:
        StorageError: si no es posible escribir el resultado.
    """
    tid = int(task_id)
    final_abs = _result_path(tid)
    tmp = final_abs + ".tmp"
    try:
        os.makedirs(_results_dir(), exist_ok=True)
        payload = serialize(data)
        with open(tmp, "w", encoding=_ENCODING, newline="") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final_abs)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise StorageError(
            f"no se pudo persistir el resultado de la tarea {tid}: {exc}"
        ) from exc
    return result_relative_path(tid)


def load_result(task_id: int):
    """Carga el resultado externalizado de ``data/results/{task_id}.json``.

    La ruta se deriva de ``task_id`` saneado; nunca de un path almacenado.

    Raises:
        StorageError: si el archivo no existe o el JSON está corrupto.
    """
    path = _result_path(task_id)
    if not os.path.isfile(path):
        raise StorageError(f"resultado externalizado no encontrado: {path}")
    try:
        with open(path, "r", encoding=_ENCODING) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"resultado externalizado ilegible o corrupto: {path}") from exc
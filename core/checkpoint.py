"""Sistema de checkpoints robusto y versionado para Space Lair.

Permite guardar el estado de un proyecto (libro/capítulo) en cada etapa
del pipeline editorial de forma inmutable y a prueba de caídas:

* book_plan, research, outline, draft, fact_check, edited, translation,
  image_plan, images, layout, final_qc

Cada artefacto (versión) contiene: version, timestamp, status, hash
(SHA-256 del payload) y source_task_id. ``save()`` jamás sobrescribe una
versión válida: siempre escribe una versión nueva (append-only), de forma
atómica (fichero temporal + os.replace), de modo que una caída a mitad de
escritura no corrompe una versión previa.

La recuperación (``recover_latest_valid``) devuelve el último artefacto cuyo
estado es "valid" y cuyo hash coincide con su payload, ignorando versiones
dañadas, fallidas o incompletas.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from enum import Enum
from typing import Any, Optional

DEFAULT_CHECKPOINT_DIR = os.path.join("data", "checkpoints")

VALID_STATUSES = ("valid", "partial", "error", "superseded")


class Stage(str, Enum):
    """Etapas del pipeline editorial que pueden tener checkpoint."""

    BOOK_PLAN = "book_plan"
    RESEARCH = "research"
    OUTLINE = "outline"
    DRAFT = "draft"
    FACT_CHECK = "fact_check"
    EDITED = "edited"
    TRANSLATION = "translation"
    IMAGE_PLAN = "image_plan"
    IMAGES = "images"
    LAYOUT = "layout"
    FINAL_QC = "final_qc"

    @classmethod
    def all(cls) -> list["Stage"]:
        return [s for s in cls]


class CheckpointError(Exception):
    """Error del sistema de checkpoints."""


def _hash_payload(payload: Any) -> str:
    """SHA-256 canónico del payload (independiente del orden de claves)."""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def _atomic_write_json(path: str, data: dict) -> None:
    """Escribe JSON de forma atómica para sobrevivir a cortes de proceso."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def _version_file(version: int) -> str:
    return f"v{int(version):04d}.json"


def _parse_version(filename: str) -> Optional[int]:
    if filename.startswith("v") and filename.endswith(".json"):
        try:
            return int(filename[1:-5])
        except ValueError:
            return None
    return None


class CheckpointManager:
    """Gestiona los checkpoints versionados de un proyecto.

    Estructura en disco::

        data/checkpoints/<book_id>/<scope>/<stage>/v<NNNN>.json
        data/checkpoints/<book_id>/<scope>/<stage>/v<NNNN>.status.json  (opcional)

    ``scope`` distingue artefactos de libro completo ("book") de los de un
    capítulo concreto (p. ej. "chapter_1").
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = base_dir or DEFAULT_CHECKPOINT_DIR

    # ----------------------------------------------------------------- path
    def _stage_dir(self, book_id: int, scope: str, stage: str) -> str:
        return os.path.join(
            self.base_dir, str(book_id), str(scope), str(stage)
        )

    # ------------------------------------------------------------- metadata
    def list_versions(self, book_id: int, stage: str, scope: str = "book") -> list[int]:
        """Devuelve las versiones existentes (crecientes) para un artefacto."""
        directory = self._stage_dir(book_id, scope, stage)
        if not os.path.isdir(directory):
            return []
        versions: list[int] = []
        for filename in os.listdir(directory):
            parsed = _parse_version(filename)
            if parsed is not None:
                versions.append(parsed)
        return sorted(set(versions))

    def next_version(self, book_id: int, stage: str, scope: str = "book") -> int:
        versions = self.list_versions(book_id, stage, scope)
        return (versions[-1] + 1) if versions else 1

    # ----------------------------------------------------------------- save
    def save(
        self,
        book_id: int,
        stage: str,
        payload: Any,
        *,
        scope: str = "book",
        source_task_id: Optional[str] = None,
        status: str = "valid",
        status_reason: Optional[str] = None,
        execution_mode: Optional[str] = None,
        quality_status: Optional[str] = None,
        sources_count: Optional[int] = None,
        word_count: Optional[int] = None,
        error: Optional[str] = None,
    ) -> dict[str, Any]:
        """Crea una **nueva** versión del artefacto (nunca sobrescribe).

        Cada versión queda guardada en su propio fichero con su hash, de
        forma que las versiones previas siempre son recuperables.

        Metadatos de calidad (opcionales):
        - execution_mode: real | fallback | failed
        - quality_status: PASS | FAIL
        - sources_count / word_count: métricas
        - error: descripción del error si lo hubo
        """
        if stage not in [s.value for s in Stage]:
            raise CheckpointError(f"Etapa de checkpoint inválida: {stage!r}")
        if status not in VALID_STATUSES:
            raise CheckpointError(f"Estado inválido: {status!r}")

        version = self.next_version(book_id, stage, scope)
        artifact: dict[str, Any] = {
            "book_id": int(book_id),
            "scope": str(scope),
            "stage": str(stage),
            "version": version,
            "timestamp": _now(),
            "status": status,
            "status_reason": status_reason,
            "execution_mode": execution_mode,
            "quality_status": quality_status,
            "metrics": {
                "sources_count": sources_count,
                "word_count": word_count,
            },
            "error": error,
            "hash": _hash_payload(payload),
            "source_task_id": source_task_id,
            "payload": payload,
        }
        path = os.path.join(
            self._stage_dir(book_id, scope, stage), _version_file(version)
        )
        _atomic_write_json(path, artifact)
        artifact["path"] = path
        return artifact


    # ----------------------------------------------------------------- load
    def load(self, book_id: int, stage: str, version: int, scope: str = "book") -> Optional[dict[str, Any]]:
        """Carga un artefacto por su versión (aplica overrides de estado)."""
        path = os.path.join(
            self._stage_dir(book_id, scope, stage), _version_file(version)
        )
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            artifact = json.load(f)
        artifact["path"] = path
        override = self._load_status_override(book_id, stage, version, scope)
        if override is not None:
            artifact["status"] = override.get("status", artifact.get("status"))
            artifact["status_reason"] = override.get("reason")
        return artifact

    def _status_override_path(self, book_id: int, stage: str, version: int, scope: str) -> str:
        base = _version_file(version)[:-5]  # quita ".json"
        return os.path.join(
            self._stage_dir(book_id, scope, stage), f"{base}.status.json"
        )

    def _load_status_override(self, book_id: int, stage: str, version: int, scope: str) -> Optional[dict[str, Any]]:
        path = self._status_override_path(book_id, stage, version, scope)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    # ----------------------------------------------------------------- mark
    def mark_invalid(
        self, book_id: int, stage: str, version: int, scope: str = "book",
        reason: str = "reported_invalid",
    ) -> bool:
        """Marca una versión como no recuperable sin tocar su contenido.

        Escribe un sidecar ``.status.json``; el artefacto inmutable queda
        intacto para auditoría, pero deja de ser seleccionable al recuperar.
        """
        if version not in self.list_versions(book_id, stage, scope):
            return False
        path = self._status_override_path(book_id, stage, version, scope)
        _atomic_write_json(
            path,
            {"version": int(version), "status": "error", "reason": reason},
        )
        return True

    # ------------------------------------------------------------- validity
    def is_valid(self, artifact: Optional[dict[str, Any]]) -> bool:
        """Verifica que el hash del artefacto coincide con su payload."""
        if not artifact:
            return False
        if artifact.get("status") not in ("valid", None):
            return False
        try:
            return _hash_payload(artifact.get("payload")) == artifact.get("hash")
        except (TypeError, ValueError):
            return False

    def integrity_check(
        self, book_id: int, stage: str, version: int, scope: str = "book"
    ) -> Optional[dict[str, Any]]:
        """Comprueba la integridad de una versión concreta."""
        artifact = self.load(book_id, stage, version, scope)
        if artifact is None:
            return None
        return {
            "version": version,
            "exists": True,
            "status": artifact.get("status"),
            "hash_ok": _hash_payload(artifact.get("payload"))
            == artifact.get("hash"),
        }


    # -------------------------------------------------------------- recovery
    def latest(
        self, book_id: int, stage: str, scope: str = "book", *, valid_only: bool = True
    ) -> Optional[dict[str, Any]]:
        """Devuelve el artefacto con la versión más alta.

        Si ``valid_only`` es True (por defecto), se devuelve el último
        artefacto cuyo estado es válido y cuyo hash coincide; los artefactos
        dañados, erróneos o incompletos se omiten.
        """
        for version in reversed(self.list_versions(book_id, stage, scope)):
            artifact = self.load(book_id, stage, version, scope)
            if artifact is None:
                continue
            if valid_only and not self.is_valid(artifact):
                continue
            return artifact
        return None

    def recover_latest_valid(
        self, book_id: int, stage: str, scope: str = "book"
    ) -> Optional[dict[str, Any]]:
        """Recupera el último artefacto válido (alias de ``latest``)."""
        return self.latest(book_id, stage, scope, valid_only=True)

    # ------------------------------------------------------------- snapshot
    def snapshot(
        self, book_id: int, stages: Optional[list[str]] = None, scope: str = "book"
    ) -> dict[str, Optional[dict[str, Any]]]:
        """Recupera el último artefacto válido de cada etapa.

        Devuelve un mapa {stage: artefacto}. Útil para reconstruir el estado
        completo de un proyecto tras un reinicio o un fallo.
        """
        selected = stages or [s.value for s in Stage]
        result: dict[str, Optional[dict[str, Any]]] = {}
        for stage in selected:
            result[stage] = self.recover_latest_valid(book_id, stage, scope)
        return result


__all__ = ["Stage", "CheckpointManager", "CheckpointError", "VALID_STATUSES"]


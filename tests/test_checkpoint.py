"""Tests del sistema de checkpoints y su recuperación tras fallos simulados."""

from __future__ import annotations

import json
import os

import pytest

from core.checkpoint import CheckpointManager, Stage, VALID_STATUSES


@pytest.fixture
def manager(tmp_path) -> CheckpointManager:
    return CheckpointManager(base_dir=str(tmp_path / "checkpoints"))


# ------------------------------------------------------------ básico / guardado
def test_stage_has_expected_states():
    expected = [
        "book_plan", "research", "outline", "draft", "fact_check", "edited",
        "translation", "image_plan", "images", "layout", "final_qc",
    ]
    assert [s.value for s in Stage.all()] == expected


def test_save_creates_versioned_artifact(manager):
    art = manager.save(1, "draft", {"text": "borrador"}, source_task_id="task_x")
    assert art["version"] == 1
    assert art["stage"] == "draft"
    assert art["status"] == "valid"
    assert art["source_task_id"] == "task_x"
    assert art["timestamp"]
    assert art["hash"]
    assert art["hash"] != art["payload"]
    assert os.path.isfile(art["path"])


def test_save_never_overwrites_a_valid_version(manager):
    manager.save(1, "draft", {"text": "v1"}, source_task_id="a")
    manager.save(1, "draft", {"text": "v2"}, source_task_id="b")
    versions = manager.list_versions(1, "draft")
    assert versions == [1, 2]
    v1 = manager.load(1, "draft", 1)
    v2 = manager.load(1, "draft", 2)
    assert v1["payload"]["text"] == "v1"
    assert v2["payload"]["text"] == "v2"


def test_latest_returns_highest_valid_version(manager):
    manager.save(1, "draft", {"text": "v1"})
    manager.save(1, "draft", {"text": "v2"})
    latest = manager.latest(1, "draft")
    assert latest["version"] == 2
    assert latest["payload"]["text"] == "v2"


def test_save_rejects_unknown_stage_or_status(manager):
    with pytest.raises(Exception):
        manager.save(1, "no_existe", {"x": 1})
    with pytest.raises(Exception):
        manager.save(1, "draft", {"x": 1}, status="bogus")


def test_source_task_id_is_recorded(manager):
    manager.save(1, "edited", {"text": "ok"}, source_task_id="task-42")
    art = manager.recover_latest_valid(1, "edited")
    assert art["source_task_id"] == "task-42"


# --------------------------------------------------------- integridad / corrupción
def test_is_valid_detects_corruption(manager):
    art = manager.save(1, "draft", {"text": "limpio"})
    assert manager.is_valid(art) is True
    art["payload"]["text"] = "manipulado"
    assert manager.is_valid(art) is False


def test_integrity_check_missing_version(manager):
    assert manager.integrity_check(1, "draft", 99) is None


# -------------------------------------------- recuperación tras simulación de fallo
def test_recover_skips_corrupt_latest_version(manager):
    manager.save(1, "draft", {"text": "bueno"}, source_task_id="ok")
    # versión 2 se corrompe en disco (p. ej. corte en la escritura)
    v2_path = os.path.join(manager.base_dir, "1", "book", "draft", "v0002.json")
    os.makedirs(os.path.dirname(v2_path), exist_ok=True)
    with open(v2_path, "w", encoding="utf-8") as f:
        json.dump(
            {"version": 2, "status": "valid",
             "stage": "draft", "payload": {"text": "roto"},
             "hash": "hash-incorrecto"},
            f,
        )
    recovered = manager.recover_latest_valid(1, "draft")
    assert recovered is not None
    assert recovered["version"] == 1
    assert recovered["payload"]["text"] == "bueno"


def test_recover_skips_failed_version(manager):
    manager.save(1, "draft", {"text": "bueno"}, source_task_id="ok")
    manager.save(1, "draft", {"text": "parcial"}, status="error", source_task_id="fail")
    recovered = manager.recover_latest_valid(1, "draft")
    assert recovered["version"] == 1
    assert recovered["payload"]["text"] == "bueno"


def test_recover_after_restart(tmp_path):
    """Simula un reinicio: un gestor nuevo apuntando al mismo directorio."""
    base = str(tmp_path / "checkpoints")
    m1 = CheckpointManager(base_dir=base)
    m1.save(1, "edited", {"text": "revisado"}, source_task_id="editor")
    m1.save(1, "edited", {"text": "revisado v2"}, source_task_id="editor2")

    m2 = CheckpointManager(base_dir=base)  # "reinicio" / nueva instancia
    recovered = m2.recover_latest_valid(1, "edited")
    assert recovered is not None
    assert recovered["version"] == 2
    assert recovered["payload"]["text"] == "revisado v2"


def test_mark_invalid_excludes_version_from_recovery(manager):
    manager.save(1, "outline", {"text": "v1"}, source_task_id="a")
    manager.save(1, "outline", {"text": "v2"}, source_task_id="b")
    assert manager.mark_invalid(1, "outline", 2, reason="q_ko") is True
    recovered = manager.recover_latest_valid(1, "outline")
    assert recovered["version"] == 1
    assert recovered["payload"]["text"] == "v1"


# --------------------------------------------------------- snapshot multi-etapa
def test_snapshot_recovers_last_valid_of_each_stage(manager):
    for stage in Stage.all():
        manager.save(1, stage.value, {"etapa": stage.value})
    # corrompemos la etapa images para simular fallo ahí
    manager.save(1, "images", {"etapa": "images_roto"}, status="error",
                 source_task_id="fail")
    snap = manager.snapshot(1)
    assert set(snap.keys()) == {s.value for s in Stage.all()}
    for stage in Stage.all():
        art = snap[stage.value]
        assert art is not None
        if stage.value == "images":
            assert art["payload"]["etapa"] == "images"  # no el fallido
        else:
            assert art["payload"]["etapa"] == stage.value


def test_recover_returns_none_when_nothing_valid(manager):
    manager.save(1, "draft", {"text": "x"}, status="error", source_task_id="f")
    manager.save(1, "draft", {"text": "y"}, status="error", source_task_id="g")
    assert manager.recover_latest_valid(1, "draft") is None
    assert VALID_STATUSES == ("valid", "partial", "error", "superseded")


"""Tests del Autopilot autónomo (20 casos controlados, sin módulos editoriales)."""

from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.dev import autopilot as ap_module
from tools.dev.autopilot import (
    AutopilotEngine,
    CommandSpec,
    PatchSpec,
    HUMAN_REQUIRED,
    PAUSED,
    PENDING,
    RETRYING,
    FAILED,
    COMPLETED,
    PLANNING,
    EXECUTING,
    VALIDATING,
    DIAGNOSING,
    Task,
    TaskStore,
    _default_state,
)
from tools.dev import security, config as dev_config


def _write_task_json(tmp_path, data):
    p = os.path.join(str(tmp_path), "task.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return p


def _safe_path(p):
    return p.replace("\\", "/")


def _basic_task(task_id="demo_001", max_iterations=3):
    return Task(
        task_id=task_id,
        objective="demo",
        commands=[CommandSpec(name="echo_ok", cmd=["python", "-c", "print('ok')"], timeout_sec=30)],
        allowed_files=[],
        forbidden_files=[],
        max_iterations=max_iterations,
        success_conditions=[{"type": "command_ok", "name": "echo_ok", "expect": "PASS"}],
    )


# 1. Task válida
def test_task_valid():
    t = _basic_task()
    assert t.task_id == "demo_001"
    assert t.max_iterations == 3
    assert len(t.commands) == 1


# 2. Task inválida
def test_task_invalid_missing_fields():
    with pytest.raises(ValueError):
        Task.from_dict({"objective": "falta task_id y commands"})


# 3. Estado persistente
def test_state_store_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = _basic_task("persist_01")
    store = TaskStore(task)
    st = store.load_state()
    assert st["status"] == PENDING
    st["status"] = COMPLETED
    store.save_state(st)
    st2 = store.load_state()
    assert st2["status"] == COMPLETED


# 4. Ejecución de comando permitido
def test_execute_allowed_command(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = _basic_task("cmd_ok")
    result = ap_module.run_task(task)
    assert result["outcome"] == "completed"


# 5. Comando no permitido -> HUMAN_REQUIRED
def test_command_not_allowed_human_required(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = Task(
        task_id="cmd_block",
        objective="comando bloqueado",
        commands=[CommandSpec(name="rm", cmd=["rm", "-rf", "/"], timeout_sec=10)],
        allowed_files=[],
        forbidden_files=[],
        max_iterations=3,
        success_conditions=[],
    )
    result = ap_module.run_task(task)
    assert result["outcome"] == "human_required"


# 6. Archivo permitido
def test_allowed_file_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    target = os.path.join(str(tmp_path), "allowed.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("contenido")
    task = Task(
        task_id="allow_write",
        objective="escribir archivo permitido",
        commands=[CommandSpec(name="write", cmd=["python", "-c", "open(r'" + _safe_path(target) + "', 'w').write('nuevo')"], timeout_sec=30)],
        allowed_files=[target],
        forbidden_files=[],
        max_iterations=3,
        success_conditions=[{"type": "command_ok", "name": "write", "expect": "PASS"}],
    )
    result = ap_module.run_task(task)
    assert result["outcome"] == "completed"
# 12. FAIL reparable -> RETRY
def test_fail_retry_then_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    counter_file = os.path.join(str(tmp_path), "counter.txt")
    safe_counter = _safe_path(counter_file)
    with open(counter_file, "w", encoding="utf-8") as f:
        f.write("0")
    task = Task(
        task_id="retry_demo",
        objective="reintentar hasta pasar",
        commands=[
            CommandSpec(name="inc", cmd=["python", "-c", "f=open('" + safe_counter + "');v=int(f.read())+1;open('" + safe_counter + "','w').write(str(v))"], timeout_sec=30),
            CommandSpec(name="check", cmd=["python", "-c", "v=open('" + safe_counter + "').read(); exit(0 if int(v)>=2 else 1)"], timeout_sec=30, allow_failure=True),
        ],
        allowed_files=[counter_file],
        forbidden_files=[],
        max_iterations=3,
        success_conditions=[{"type": "command_ok", "name": "check", "expect": "PASS"}],
    )
    result = ap_module.run_task(task)
    assert result["outcome"] == "completed"


# 13. max_iterations
def test_max_iterations_reached(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = Task(
        task_id="max_iter",
        objective="siempre falla",
        commands=[CommandSpec(name="fail", cmd=["python", "-c", "raise SystemExit(1)"], timeout_sec=30)],
        allowed_files=[],
        forbidden_files=[],
        max_iterations=2,
        success_conditions=[{"type": "command_ok", "name": "fail", "expect": "PASS"}],
    )
    result = ap_module.run_task(task)
    assert result["outcome"] == "failed"


# 14. Falta de progreso
def test_no_progress_human_required(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = Task(
        task_id="no_progress",
        objective="mismo fallo repetido",
        commands=[CommandSpec(name="fail", cmd=["python", "-c", "raise SystemExit(1)"], timeout_sec=30)],
        allowed_files=[],
        forbidden_files=[],
        max_iterations=3,
        success_conditions=[{"type": "command_ok", "name": "fail", "expect": "PASS"}],
    )
    result = ap_module.run_task(task)
    assert result["outcome"] == "human_required"


# 15. HUMAN_REQUIRED markdown
def test_human_required_markdown_written(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = Task(
        task_id="human_req",
        objective="bloqueo",
        commands=[CommandSpec(name="fail", cmd=["python", "-c", "raise SystemExit(1)"], timeout_sec=30)],
        allowed_files=[],
        forbidden_files=[],
        max_iterations=3,
        success_conditions=[{"type": "command_ok", "name": "fail", "expect": "PASS"}],
    )
    result = ap_module.run_task(task)
    assert result["outcome"] == "human_required"
    md = os.path.join(str(tmp_path), "data", "autopilot", "human_req", "HUMAN_REQUIRED.md")
# 7. Archivo prohibido -> HUMAN_REQUIRED
def test_forbidden_file_blocks_human_required(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    target = os.path.join(str(tmp_path), "forbidden.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("original")
    task = Task(
        task_id="forbid_write",
        objective="intentar escribir prohibido",
        commands=[],
        patches=[PatchSpec(file=target, old="original", new="nuevo")],
        allowed_files=[],
        forbidden_files=[target],
        max_iterations=3,
        success_conditions=[],
    )
    result = ap_module.run_task(task)
    assert result["outcome"] == "human_required"


# 8. Checksum antes/después
def test_checksums_before_after(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    target = os.path.join(str(tmp_path), "watch.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("a")
    before = security.sha256_file(target)
    with open(target, "w", encoding="utf-8") as f:
        f.write("b")
    after = security.sha256_file(target)
    assert before != after


# 9. Backup
def test_backup_created(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = _basic_task("backup_01")
    store = TaskStore(task)
    engine = AutopilotEngine(task, store)
    target = os.path.join(str(tmp_path), "file.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("original")
    bkp = engine._backup_file(target, os.path.join(str(tmp_path), "att"))
    assert bkp is not None and os.path.exists(bkp)


# 10. Rollback
def test_rollback_restores(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = _basic_task("rollback_01")
    store = TaskStore(task)
    engine = AutopilotEngine(task, store)
    target = os.path.join(str(tmp_path), "file.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("original")
    engine._backup_file(target, os.path.join(str(tmp_path), "att"))
    with open(target, "w", encoding="utf-8") as f:
        f.write("cambiado")
    engine._rollback_modified([target], os.path.join(str(tmp_path), "att"))
    with open(target, "r", encoding="utf-8") as f:
        assert f.read() == "original"


# 11. PASS -> COMPLETED
def test_pass_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = _basic_task("pass_comp")
    result = ap_module.run_task(task)
    assert result["outcome"] == "completed"
# 16. pause/resume
def test_pause_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task = _basic_task("pause_resume")
    store = TaskStore(task)
    store.save_state(_default_state(task))
    r = ap_module.pause_task(task.task_id)
    assert r["outcome"] == "paused"
    st = store.load_state()
    assert st["status"] == PAUSED
    r2 = ap_module.resume_task(task.task_id)
    assert r2["outcome"] == "resumed"
    st2 = store.load_state()
    assert st2["status"] == RETRYING


# 17. task inexistente
def test_resume_nonexistent_task(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    r = ap_module.resume_task("no_existe")
    assert "error" in r or r.get("outcome") == "failed"


# 18. CLI básico
def test_cli_autopilot_start(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    task_data = _basic_task("cli_demo").to_dict()
    task_file = _write_task_json(tmp_path, task_data)
    rc = os.system(f"python tools/orchestrator.py autopilot start {task_file}")
    assert rc == 0


# 19. No modificación de archivos prohibidos
def test_no_modification_of_forbidden(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    target = os.path.join(str(tmp_path), "secret.txt")
    with open(target, "w", encoding="utf-8") as f:
        f.write("original")
    before = security.sha256_file(target)
    task = Task(
        task_id="no_mod",
        objective="no tocar prohibido",
        commands=[],
        patches=[PatchSpec(file=target, old="original", new="hack")],
        allowed_files=[],
        forbidden_files=[target],
        max_iterations=1,
        success_conditions=[],
    )
    result = ap_module.run_task(task)
    after = security.sha256_file(target)
    assert result["outcome"] == "human_required"
    assert before == after


# 20. No ejecución de comandos arbitrarios
def test_no_arbitrary_commands_executed(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_config, "ROOT", str(tmp_path))
    executed = []

    def fake_run(cmd, **kwargs):
        executed.append(cmd)
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": "", "cmd": " ".join(cmd)}

    monkeypatch.setattr(ap_module.runner, "run_command", fake_run)
    task = Task(
        task_id="no_arb",
        objective="solo echo permitido",
        commands=[CommandSpec(name="echo_ok", cmd=["python", "-c", "print('ok')"], timeout_sec=30)],
        allowed_files=[],
        forbidden_files=[],
        max_iterations=1,
        success_conditions=[{"type": "command_ok", "name": "echo_ok", "expect": "PASS"}],
    )
    result = ap_module.run_task(task)
    assert len(executed) == 1
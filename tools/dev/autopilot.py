"""Autópilot — motor de orquestación autónomo determinista y seguro.

Ciclo por task: PLANNING -> EXECUTING -> VALIDATING -> DIAGNOSING -> DECIDE
              -> COMPLETED | RETRYING | HUMAN_REQUIRED

Regla de oro: NO ejecuta comandos ni aplica cambios no declarados en la task.
Cualquier deriva -> HUMAN_REQUIRED. Reutiliza runner, parsers, security, state.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from . import config, parsers, runner, security
from .autonomous import backup_file  # reuse existing rollback helper

# Estados
PENDING, PLANNING, EXECUTING, VALIDATING, DIAGNOSING = "PENDING", "PLANNING", "EXECUTING", "VALIDATING", "DIAGNOSING"
RETRYING, COMPLETED, FAILED, HUMAN_REQUIRED, PAUSED = "RETRYING", "COMPLETED", "FAILED", "HUMAN_REQUIRED", "PAUSED"
TERMINAL = {COMPLETED, FAILED, HUMAN_REQUIRED, PAUSED}
_ALLOWED_EXEC = {"python", "python3", "pytest"}


@dataclass
class CommandSpec:
    name: str
    cmd: list
    timeout_sec: int = 1200
    allow_failure: bool = False


@dataclass
class PatchSpec:
    file: str
    old: str
    new: str


@dataclass
class Task:
    task_id: str
    objective: str
    commands: list = field(default_factory=list)
    patches: list = field(default_factory=list)
    allowed_files: list = field(default_factory=list)
    forbidden_files: list = field(default_factory=list)
    success_conditions: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    max_iterations: int = 3
    human_approval_required: bool = False

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "commands": [{"name": c.name, "cmd": c.cmd, "timeout_sec": c.timeout_sec, "allow_failure": c.allow_failure} for c in self.commands],
            "patches": [{"file": p.file, "old": p.old, "new": p.new} for p in self.patches],
            "allowed_files": self.allowed_files,
            "forbidden_files": self.forbidden_files,
            "success_conditions": self.success_conditions,
            "env": self.env,
            "max_iterations": self.max_iterations,
            "human_approval_required": self.human_approval_required,
        }

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "commands": [{"name": c.name, "cmd": c.cmd, "timeout_sec": c.timeout_sec, "allow_failure": c.allow_failure} for c in self.commands],
            "patches": [{"file": p.file, "old": p.old, "new": p.new} for p in self.patches],
            "allowed_files": self.allowed_files,
            "forbidden_files": self.forbidden_files,
            "success_conditions": self.success_conditions,
            "env": self.env,
            "max_iterations": self.max_iterations,
            "human_approval_required": self.human_approval_required,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        if not isinstance(d, dict):
            raise ValueError("La task debe ser un dict.")
        if "task_id" not in d or "commands" not in d:
            raise ValueError("task_id y commands son requeridos.")
        cmds = [CommandSpec(**c) for c in d.get("commands", [])]
        patches = [PatchSpec(**p) for p in d.get("patches", [])]
        return cls(
            task_id=str(d["task_id"]),
            objective=str(d.get("objective", "")),
            commands=cmds,
            patches=patches,
            allowed_files=d.get("allowed_files", []),
            forbidden_files=d.get("forbidden_files", []),
            success_conditions=d.get("success_conditions", []),
            env=d.get("env", {}),
            max_iterations=int(d.get("max_iterations", 3)),
            human_approval_required=bool(d.get("human_approval_required", False)),
        )


class TaskStore:
    def __init__(self, task: Task):
        self.task = task
        self.dir = os.path.join(config.ROOT, "data", "autopilot", task.task_id)
        self.state_path = os.path.join(self.dir, "state.json")
        self.attempts_dir = os.path.join(self.dir, "attempts")

    def _ensure_dirs(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        os.makedirs(self.attempts_dir, exist_ok=True)

    def load_state(self) -> dict:
        self._ensure_dirs()
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return _default_state(self.task)

    def save_state(self, st: dict) -> None:
        self._ensure_dirs()
        st["updated_at"] = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)
        self._write_human_file(st)

    def _write_human_file(self, st: dict) -> None:
        if st.get("status") == HUMAN_REQUIRED:
            with open(os.path.join(self.dir, "HUMAN_REQUIRED.md"), "w", encoding="utf-8") as f:
                f.write(_render_human_required(st))

    def attempt_dir(self, attempt: int) -> str:
        self._ensure_dirs()
        d = os.path.join(self.attempts_dir, f"attempt_{attempt:03d}")
        os.makedirs(d, exist_ok=True)
        return d


def _default_state(task: Task) -> dict:
    return {
        "task_id": task.task_id,
        "objective": task.objective,
        "status": PENDING,
        "phase": None,
        "attempt": 0,
        "commands_run": [],
        "patches_applied": [],
        "last_diff_signature": None,
        "last_diagnosis": None,
        "history": [],
        "created_at": datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
    }


# --------------------------------------------------------------------------- #
# Human-required rendering
# --------------------------------------------------------------------------- #
def _render_human_required(st: dict) -> str:
    diag = st.get("last_diagnosis") or {}
    lines = ["# AUTOPILOT PAUSED -- se requiere intervencion humana", ""]
    lines.append(f"**Task:** {st.get('task_id')}")
    lines.append(f"**Objetivo:** {st.get('objective')}")
    lines.append("")
    lines.append("## Motivo")
    lines.append(st.get("human_reason") or diag.get("reason") or "Sin razon especifica.")
    lines.append("")
    lines.append("## Evidencia")
    for e in diag.get("evidence", []) or [diag.get("error_message")]:
        if e:
            lines.append(f"- {e}")
    lines.append("")
    lines.append("## Opciones")
    opts = st.get("human_options") or ["Reintentar", "Detener"]
    for i, opt in enumerate(opts, 1):
        lines.append(f"{i}. {opt}")
    lines.append("")
    lines.append(f"Para continuar: `python tools/orchestrator.py autopilot approve {st.get('task_id')}`")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Seguridad de archivos
# --------------------------------------------------------------------------- #
def _path_ok_for_write(path: str, task: Task) -> Optional[str]:
    """None si OK, o mensaje de bloqueo si forbidden/protegido/no permitido."""
    norm = os.path.normpath(os.path.join(config.ROOT, path))
    rel = os.path.relpath(norm, config.ROOT)
    if security.is_protected(norm):
        return f"archivo protegido (PROTECTED_FILES): {rel}"
    if security.is_out_of_scope(norm):
        return f"archivo fuera de alcance (OUT_OF_SCOPE_MODULES): {rel}"
    for f in task.forbidden_files:
        fn = os.path.normpath(os.path.join(config.ROOT, f))
        if norm == fn or norm.startswith(fn + os.sep):
            return f"archivo en forbidden_files: {rel}"
    if task.allowed_files:
        allowed = [os.path.normpath(os.path.join(config.ROOT, f)) for f in task.allowed_files]
        if not any(norm == a or norm.startswith(a + os.sep) for a in allowed):
            return f"archivo fuera de allowed_files: {rel}"
    return None


def _apply_patch(patch: PatchSpec, task: Task) -> dict:
    norm = os.path.normpath(os.path.join(config.ROOT, patch.file))
    blocked = _path_ok_for_write(patch.file, task)
    if blocked:
        return {"file": patch.file, "applied": False, "error": blocked}
    if not os.path.exists(norm):
        return {"file": patch.file, "applied": False, "error": "file not found"}
    backup = backup_file(norm)
    sha_before = security.sha256_file(norm)
    try:
        with open(norm, "r", encoding="utf-8") as f:
            content = f.read()
        if patch.old not in content:
            return {"file": patch.file, "applied": False,
                    "error": "old_text not found (no match)",
                    "backup": backup, "sha_before": sha_before}
        new_content = content.replace(patch.old, patch.new, 1)
        with open(norm, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"file": patch.file, "applied": True, "backup": backup,
                "sha_before": sha_before, "sha_after": security.sha256_file(norm)}
    except Exception as e:  # rollback implícito
        if backup and os.path.exists(backup):
            shutil.copy2(backup, norm)
        return {"file": patch.file, "applied": False, "error": str(e), "backup": backup}


@dataclass
class Decision:
    status: str
    reason: str
    evidence: dict
    recommended_action: str = ""


# --------------------------------------------------------------------------- #
# Firmas de diff (checksum estable sin git)
# --------------------------------------------------------------------------- #
def _sha_dir(d: str) -> str:
    if not os.path.isdir(d):
        return ""
    h = hashlib.sha256()
    for root, _, files in os.walk(d):
        for fn in sorted(files):
            if fn == "__pycache__":
                continue
            p = os.path.join(root, fn)
            h.update(p.encode("utf-8"))
            sha = security.sha256_file(p)
            if sha:
                h.update(sha.encode("utf-8"))
    return h.hexdigest()


def _diff_signature(task: Task) -> str:
    parts = []
    if task.allowed_files:
        for t in task.allowed_files:
            p = os.path.normpath(os.path.join(config.ROOT, t))
            sha = _sha_dir(p) if os.path.isdir(p) else security.sha256_file(p)
            parts.append(sha or "")
    else:
        # Sin archivos modificables: firma estable derivada de la definición de la
        # tarea (no varía entre intentos), permitiendo detectar falta de progreso.
        parts.append(f"{task.task_id}|{task.objective}|"
                     f"{[c.name for c in task.commands]}|{task.patches}")
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def _satisfies_conditions(task: Task, validation: dict) -> tuple:
    reasons = []
    for cond in task.success_conditions:
        ctype = cond.get("type")
        expect = cond.get("expect")
        if ctype == "pytest":
            got = validation.get("pytest", {}).get("status", "FAIL")
            if got != expect:
                reasons.append(f"pytest expected {expect}, got {got}")
        elif ctype == "e2e":
            e = validation.get("e2e", {})
            got = "PASS" if e.get("e2e_status") == "PASS" else "FAIL"
            if expect == "PASS" and got != "PASS":
                reasons.append(f"e2e expected PASS, got {got}")
        elif ctype == "word_count_gte":
            wc = validation.get("e2e", {}).get("word_count")
            val = cond.get("value")
            if wc is None or wc < val:
                reasons.append(f"word_count expected >= {val}, got {wc}")
        elif ctype == "command_ok":
            cmd_name = cond.get("name")
            commands_run = validation.get("commands_run", [])
            matching = [r for r in commands_run if r.get("name") == cmd_name]
            if not matching:
                reasons.append(f"command_ok: comando '{cmd_name}' no encontrado en commands_run")
            else:
                ok = matching[0].get("ok", False)
                if expect == "PASS" and not ok:
                    reasons.append(f"command_ok: comando '{cmd_name}' falló (ok={ok})")
        else:
            reasons.append(f"condicion desconocida: {cond}")
    return (len(reasons) == 0, reasons)


def _dump(d: str, name: str, content: str) -> None:
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write(content)


class AutopilotEngine:
    def __init__(self, task: Task, store: TaskStore | None = None):
        self.task = task
        self.store = store or TaskStore(task)

    # PLAN
    def plan(self, st: dict) -> dict:
        st["phase"] = PLANNING
        plan_log = []
        for c in self.task.commands:
            first = c.cmd[0] if c.cmd else ""
            if first not in _ALLOWED_EXEC:
                st["status"] = HUMAN_REQUIRED
                st["human_reason"] = f"comando fuera de allowlist: {first}"
                st["human_options"] = ["Editar la task (allowlist)", "Abandonar task"]
                return {"ok": False, "error": f"blocked: {first}", "plan": plan_log}
            plan_log.append(f"ok: {c.name} -> {' '.join(c.cmd)}")
        st["phase"] = EXECUTING
        return {"ok": True, "plan": plan_log}

    # EXECUTE
    def execute(self, st: dict, attempt_dir: str) -> dict:
        st["phase"] = EXECUTING
        applied = []
        for p in self.task.patches:
            res = _apply_patch(p, self.task)
            applied.append(res)
            if not res["applied"] and res.get("error", "").startswith("archivo"):
                st["status"] = HUMAN_REQUIRED
                st["human_reason"] = res["error"]
                st["human_options"] = ["Ajustar allowed_files/forbidden_files", "Abandonar task"]
                return {"ok": False, "error": res["error"], "applied": applied}
        st["patches_applied"] = applied
        commands_run = []
        for c in self.task.commands:
            if st.get("status") == HUMAN_REQUIRED:
                break
            env = dict(os.environ)
            env.update({k: str(v) for k, v in self.task.env.items()})
            try:
                res = runner.run_command(c.cmd, timeout_sec=c.timeout_sec, label=c.name)
            except Exception as e:
                res = {"returncode": -1, "ok": False, "stdout": "", "stderr": str(e),
                       "timed_out": False, "duration_sec": 0.0, "cmd": " ".join(c.cmd)}
            entry = {
                "name": c.name, "cmd": c.cmd, "returncode": res["returncode"],
                "ok": res["ok"], "timed_out": res.get("timed_out", False),
                "duration_sec": res.get("duration_sec", 0.0),
                "stdout": (res.get("stdout", "") or "")[-2000:],
                "stderr": (res.get("stderr", "") or "")[-2000:],
            }
            commands_run.append(entry)
            _dump(attempt_dir, f"cmd_{c.name}.log",
                  entry["stdout"] + "\n---STDERR---\n" + entry["stderr"])
            if not res["ok"] and not c.allow_failure:
                break
        st["commands_run"] = commands_run
        return {"ok": True, "commands_run": commands_run}

    # VALIDATE
    def validate(self, st: dict, attempt_dir: str) -> dict:
        st["phase"] = VALIDATING
        validation = {"pytest": None, "e2e": None, "quality_gate": None, "commands_run": st.get("commands_run", [])}
        log = os.path.join(attempt_dir, "cmd_pytest.log")
        rc = 0
        for e in st.get("commands_run", []):
            if e["name"].startswith("pytest"):
                rc = e["returncode"]
                break
        if os.path.exists(log):
            out = open(log, "r", encoding="utf-8", errors="replace").read()
            validation["pytest"] = parsers.parse_pytest(["tests"], out, rc)
        e2e_report = parsers.load_e2e_report()
        if e2e_report is not None:
            validation["e2e"] = parsers.parse_e2e(e2e_report)
            validation["quality_gate"] = validation["e2e"].get("e2e_status")
        _dump(attempt_dir, "validation.json", json.dumps(validation, ensure_ascii=False, indent=2))
        st["last_validation"] = validation
        st["phase"] = DIAGNOSING
        return validation

    # DIAGNOSE
    def diagnose(self, st: dict, attempt_dir: str) -> dict:
        st["phase"] = DIAGNOSING
        v = st.get("last_validation", {})
        diag = parsers.diagnose(v.get("e2e") or {}, v.get("pytest"))
        reason = diag.get("root_cause", "")
        evidence = []
        if v.get("pytest"):
            evidence.append(f"pytest: {v['pytest'].get('pytest_summary')} -> {v['pytest'].get('status')}")
        if v.get("e2e"):
            evidence.append(f"e2e stage: {v['e2e'].get('failed_stage')} wc={v['e2e'].get('word_count')}")
        result = {
            "failed_stage": v.get("e2e", {}).get("failed_stage"),
            "error_type": ("pytest_fail" if (v.get("pytest") or {}).get("status") != "PASS"
                           else "e2e_fail" if v.get("e2e") else "none"),
            "error_message": reason,
            "relevant_logs": evidence,
            "suspected_files": [c.name for c in self.task.commands],
            "suspected_root_cause": reason,
            "confidence": 0.6,
            "recommended_action": "revisar logs de pytest en attempt_dir",
            "evidence": evidence,
            "reason": reason,
        }
        _dump(attempt_dir, "diagnose.json", json.dumps(result, ensure_ascii=False, indent=2))
        st["last_diagnosis"] = result
        return result

    def _backup_file(self, path: str, backup_root: str) -> str:
        # Backup canónico con la utilidad existente (guarda en STATE_DIR/backups).
        backup_file(path)
        # Copia local en el directorio del intento para rollback acotado y verificable.
        os.makedirs(backup_root, exist_ok=True)
        local = os.path.join(backup_root, f"{os.path.basename(path)}.bak")
        if os.path.exists(path):
            shutil.copy2(path, local)
        return local

    def _rollback_modified(self, paths: list[str], backup_root: str) -> None:
        if not os.path.isdir(backup_root):
            return
        for p in paths:
            if not os.path.exists(p):
                continue
            bkp = os.path.join(backup_root, f"{os.path.basename(p)}.bak")
            if os.path.exists(bkp):
                shutil.copy2(bkp, p)

    # DECIDE
    def decide(self, st: dict, diag: dict) -> "Decision":
        ok, reasons = _satisfies_conditions(self.task, st.get("last_validation", {}))
        sig = _diff_signature(self.task)
        prev_sig = st.get("last_diff_signature")
        prev_diag = st.get("last_diagnosis")
        same_diff = prev_sig is not None and prev_sig == sig
        same_diag = bool(prev_diag) and prev_diag.get("suspected_root_cause") == diag.get("suspected_root_cause")
        no_progress = same_diff and same_diag and not ok
        if ok:
            st["status"] = COMPLETED
            st["phase"] = COMPLETED
            _dump(self.store.attempt_dir(st["attempt"]), "decision.json",
                  json.dumps({"status": COMPLETED, "reason": "success_conditions met"}, indent=2, ensure_ascii=False))
            return Decision(COMPLETED, "success_conditions satisfied", {"conditions": self.task.success_conditions})
        # max_iterations tiene prioridad sobre no_progress
        if st["attempt"] >= self.task.max_iterations:
            st["status"] = FAILED
            st["phase"] = FAILED
            return Decision(FAILED, "max_iterations reached", {"attempt": st["attempt"]})
        # Si no hay progreso detectado, detener
        if no_progress:
            st["status"] = HUMAN_REQUIRED
            st["human_reason"] = "falta de progreso: mismo diff y mismo diagnostico entre intentos"
            st["human_options"] = ["Revisar diagnose.json y modificar patches/commands",
                                   "Reiniciar task con nueva estrategia", "Abandonar task"]
            return Decision(HUMAN_REQUIRED, "no progress across attempts", {"reasons": reasons})
        st["status"] = RETRYING
        st["phase"] = RETRYING
        return Decision(RETRYING, "reintento permitido", {"reasons": reasons})


def run_task(task: Task) -> dict:
    engine = AutopilotEngine(task)
    st = engine.store.load_state()
    st["attempt"] = int(st.get("attempt", 0))
    if st["status"] in TERMINAL and st["status"] != HUMAN_REQUIRED:
        st = _default_state(task)
    iteration = 0
    max_loop = task.max_iterations + 1
    while iteration < max_loop:
        iteration += 1
        st["attempt"] = int(st.get("attempt", 0)) + 1
        attempt_dir = engine.store.attempt_dir(st["attempt"])
        st["phase"] = PLANNING
        engine.store.save_state(st)
        _dump(attempt_dir, "state_start.json", json.dumps(st, ensure_ascii=False, indent=2))

        plan_res = engine.plan(st)
        if st["status"] == HUMAN_REQUIRED:
            engine.store.save_state(st)
            return _result(st, "human_required", plan_res)

        engine.execute(st, attempt_dir)
        if st["status"] == HUMAN_REQUIRED:
            engine.store.save_state(st)
            return _result(st, "human_required", {"execute": st.get("patches_applied")})

        validation = engine.validate(st, attempt_dir)
        diag = engine.diagnose(st, attempt_dir)
        decision = engine.decide(st, diag)
        _dump(attempt_dir, "decision.json",
              json.dumps({"status": decision.status, "reason": decision.reason}, indent=2, ensure_ascii=False))
        st["last_diff_signature"] = _diff_signature(task)
        st["last_diagnosis"] = diag
        st["history"].append({"attempt": st["attempt"], "phase": st["phase"],
                              "status": st["status"], "diagnosis": diag})
        engine.store.save_state(st)

        if st["status"] == COMPLETED:
            return _result(st, "completed", {"decision": decision.status})
        if st["status"] == HUMAN_REQUIRED:
            return _result(st, "human_required", {"decision": decision.status, "diagnosis": diag})
        if st["status"] == RETRYING:
            continue
        return _result(st, "failed", {"decision": decision.status})
    return _result(st, "max_iterations_reached", {"attempt": st["attempt"]})


def resume_task(task_id: str) -> dict:
    task = Task(task_id=task_id, objective="(resume)")
    store = TaskStore(task)
    if not os.path.exists(store.state_path):
        return {"outcome": "failed", "error": f"Task {task_id} no encontrada.", "task_id": task_id}
    st = store.load_state()
    status = st.get("status", "")
    if status in (HUMAN_REQUIRED, PAUSED):
        st["status"] = RETRYING
        st["updated_at"] = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
        store.save_state(st)
        return {"outcome": "resumed", "task_id": task_id, "status": RETRYING}
    return {"outcome": "no_resume_needed", "task_id": task_id, "status": status}


def pause_task(task_id: str) -> dict:
    task = Task(task_id=task_id, objective="(pause)")
    store = TaskStore(task)
    st = store.load_state()
    st["status"] = PAUSED
    st["updated_at"] = datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    store.save_state(st)
    return {"outcome": "paused", "task_id": task_id}


def _result(st: dict, outcome: str, extra: dict) -> dict:
    out = {"task_id": st.get("task_id"), "outcome": outcome, "status": st.get("status"),
           "attempt": st.get("attempt"), "phase": st.get("phase")}
    out.update(extra)
    return out



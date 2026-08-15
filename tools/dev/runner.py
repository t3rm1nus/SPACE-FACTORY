"""Ejecución de comandos (pytest, E2E) con captura de stdout/stderr y logs."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional
from datetime import datetime

from . import config


def run_command(
    cmd: list[str],
    *,
    timeout_sec: int = 1100,
    label: str = "run",
) -> dict:
    """Ejecuta un comando y captura stdout (y stderr combinado).

    Devuelve ``{cmd, label, returncode, ok, stdout, stderr, duration_sec,
    log_path}``. ``ok`` es True si el código de retorno es 0.
    """
    config.ensure_dirs()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(config.LOG_DIR, f"{label}_{ts}.log")
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=config.ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
        rc, timed_out = proc.returncode, False
        out, err = proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        rc, timed_out = -1, True
        out = e.stdout or ""
        err = (e.stderr or "") + f"\n[TIMEOUT {timeout_sec}s]"
    duration = time.time() - start

    clean = (out + "\n" + err).strip()
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(clean)
    except OSError:
        pass

    return {
        "cmd": " ".join(cmd),
        "label": label,
        "returncode": rc,
        "timed_out": timed_out,
        "ok": rc == 0,
        "stdout": out,
        "stderr": err,
        "duration_sec": round(duration, 2),
        "log_path": log_path,
    }


def run_pytest(paths: list[str], *, quiet: bool = True) -> dict:
    cmd = config.PYTEST_CMD + (["-q"] if quiet else []) + paths
    return run_command(cmd, timeout_sec=1200, label="pytest")


def run_full_tests() -> dict:
    return run_pytest([config.TEST_DIR])


def run_specific_tests(targets: list[str]) -> dict:
    full = [os.path.join(config.TEST_DIR, t) if not os.path.isabs(t) else t for t in targets]
    return run_pytest(full)


def run_e2e(timeout_sec: int = 2400) -> dict:
    return run_command(config.E2E_CMD, timeout_sec=timeout_sec, label="e2e")


def ensure_report_exists() -> Optional[str]:
    if os.path.exists(config.E2E_REPORT):
        return config.E2E_REPORT
    return None


def print_summary(res: dict) -> None:
    status = "OK" if res.get("ok") else ("TIMEOUT" if res.get("timed_out") else "FAIL")
    print(f"  [{status}] rc={res.get('returncode')} dur={res.get('duration_sec')}s "
          f"log={res.get('log_path')}")
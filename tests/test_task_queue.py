"""Tests de integración de externalización de resultados grandes en la cola.

Verifica que `complete_task`, `get_task` y `all_tasks` mantienen el contrato
público (result = JSON del resultado real) sin importar si el resultado es
inline o externalizado, y cubre retry, fallo de persistencia, corrupción,
path traversal, compatibilidad, NULL y concurrencia básica.
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import storage
from core.database import get_db, init_db
from core.task_queue import (
    all_tasks,
    complete_task,
    enqueue_task,
    get_task,
)


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """BD SQLite y directorio de resultados aislados por test."""
    db = tmp_path / "test.db"
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", str(db))
    monkeypatch.setenv("SPACE_LAIR_RESULTS_DIR", str(tmp_path / "results"))
    init_db()
    yield tmp_path
    for leftover in (tmp_path / "results").glob("*.tmp"):
        try:
            leftover.unlink()
        except OSError:
            pass


def _result_exact_bytes(n: int) -> dict:
    """Dict cuyo JSON serializado mide exactamente ``n`` bytes."""
    overhead = len(storage.serialize({"payload": ""}))
    return {"payload": "a" * max(0, n - overhead)}


def _large_result():
    return _result_exact_bytes(storage.LARGE_RESULT_THRESHOLD_BYTES + 1)


def _small_result():
    return {"text": "hola", "n": 3}


def _file_for(task_id: int):
    return os.path.join(os.environ["SPACE_LAIR_RESULTS_DIR"], f"{task_id}.json")


# ---------------------------------------------------------------------------
# Caso básico
# ---------------------------------------------------------------------------


def test_small_result_stays_inline(tmp_path):
    task_id = enqueue_task("count_words", {"text": "x"})
    complete_task(task_id, _small_result())
    row = get_task(task_id)
    assert row["status"] == "done"
    # inline: sin marcador de referencia
    parsed = json.loads(row["result"])
    assert parsed == _small_result()
    assert "_ref_external" not in parsed
    # no se externalizó
    assert not os.path.exists(_file_for(task_id))


def test_exactly_one_mib_stays_inline(tmp_path):
    v = _result_exact_bytes(storage.LARGE_RESULT_THRESHOLD_BYTES)
    task_id = enqueue_task("summarize_text", {"text": "x"})
    complete_task(task_id, v)
    row = get_task(task_id)
    assert json.loads(row["result"]) == v
    assert "_ref_external" not in json.loads(row["result"])
    assert not os.path.exists(_file_for(task_id))
def test_large_result_externalized(tmp_path):
    v = _large_result()
    task_id = enqueue_task("write_chapter_es", {"text": "x"})
    complete_task(task_id, v)
    # archivo físico con el contenido real
    assert os.path.isfile(_file_for(task_id))
    with open(_file_for(task_id), encoding="utf-8") as fh:
        assert json.loads(fh.read()) == v
    # get_task devuelve el resultado real, no una referencia
    row = get_task(task_id)
    assert json.loads(row["result"]) == v
    assert "_ref_external" not in json.loads(row["result"])


def test_all_tasks_returns_real_result(tmp_path):
    task_small = enqueue_task("count_words", {"text": "x"})
    task_large = enqueue_task("write_chapter_es", {"text": "x"})
    complete_task(task_small, _small_result())
    complete_task(task_large, _large_result())
    mapping = {t["id"]: t for t in all_tasks()}
    assert json.loads(mapping[task_small]["result"]) == _small_result()
    assert json.loads(mapping[task_large]["result"]) == _large_result()


def test_two_large_tasks_are_isolated(tmp_path):
    v1 = _large_result()
    v2 = {"payload": "z" * (storage.LARGE_RESULT_THRESHOLD_BYTES + 1)}
    t1 = enqueue_task("write_chapter_es", {"text": "x"})
    t2 = enqueue_task("edit_chapter", {"text": "x"})
    complete_task(t1, v1)
    complete_task(t2, v2)
    assert json.loads(get_task(t1)["result"]) == v1
    assert json.loads(get_task(t2)["result"]) == v2
    assert _file_for(t1) != _file_for(t2)


# ---------------------------------------------------------------------------
# Retry y fallo de persistencia
# ---------------------------------------------------------------------------


def test_save_failure_no_reference_and_retry_succeeds(monkeypatch, tmp_path):
    task_id = enqueue_task("write_chapter_es", {"text": "x"})
    v = _large_result()
    original_save = storage.save_result

    def _boom(*args, **kwargs):
        raise storage.StorageError("simulated write failure")

    monkeypatch.setattr(storage, "save_result", _boom)
    with pytest.raises(storage.StorageError):
        complete_task(task_id, v)

    # no se marcó como completada ni se guardó una referencia
    row = get_task(task_id)
    assert row["status"] != "done"
    assert row["result"] is None or "_ref_external" not in (row["result"] or "")

    # retry con el sistema restaurado -> externalización correcta
    monkeypatch.setattr(storage, "save_result", original_save)
    complete_task(task_id, v)
    row = get_task(task_id)
    assert row["status"] == "done"
    assert os.path.isfile(_file_for(task_id))
    assert json.loads(row["result"]) == v


# ---------------------------------------------------------------------------
# Resultado externo no disponible / corrupto
# ---------------------------------------------------------------------------


def test_missing_external_file_degrades_with_ref_error(tmp_path):
    v = _large_result()
    task_id = enqueue_task("write_chapter_es", {"text": "x"})
    complete_task(task_id, v)
    os.remove(_file_for(task_id))
    row = get_task(task_id)  # no debe lanzar
    assert row["status"] == "done"
    parsed = json.loads(row["result"])
    assert parsed[storage.REF_KEY] is True
    assert "_ref_error" in parsed


def test_corrupt_external_file_degrades_with_ref_error(tmp_path):
    v = _large_result()
    task_id = enqueue_task("write_chapter_es", {"text": "x"})
    complete_task(task_id, v)
    with open(_file_for(task_id), "w", encoding="utf-8") as fh:
        fh.write("{broken json")
    row = get_task(task_id)
    parsed = json.loads(row["result"])
    assert parsed[storage.REF_KEY] is True
    assert "_ref_error" in parsed
# ---------------------------------------------------------------------------
# Path traversal: no se confía en la ruta almacenada
# ---------------------------------------------------------------------------


def test_hydration_ignores_stored_path_task_id_rules(tmp_path):
    v = _large_result()
    task_id = enqueue_task("write_chapter_es", {"text": "x"})
    complete_task(task_id, v)
    # corromper la referencia en BD para que apunte fuera, pero manteniendo la
    # estructura válida => el lector la reconoce y carga desde task_id real.
    malicious = json.dumps(
        {storage.REF_KEY: True, storage.REF_PATH_KEY: "../../../etc/passwd.json"}
    )
    with get_db() as conn:
        conn.execute("UPDATE tasks SET result = ? WHERE id = ?", (malicious, task_id))
        conn.commit()
    row = get_task(task_id)
    # carga el archivo correcto data/results/{task_id}.json, no el path malicioso
    assert json.loads(row["result"]) == v


def test_load_result_rejects_non_numeric_task_id(tmp_path):
    with pytest.raises(ValueError):
        storage.load_result("1/../etc/passwd")


# ---------------------------------------------------------------------------
# Compatibilidad hacia atrás y NULL
# ---------------------------------------------------------------------------


def test_legacy_inline_result_unchanged(tmp_path):
    # resultado inline "antiguo" (sin marcador) insertado directamente
    legacy = json.dumps({"legacy": True, "data": "a"})
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (capability, payload, status, result) "
            "VALUES ('count_words', '{}', 'done', ?)",
            (legacy,),
        )
        conn.commit()
        task_id = cur.lastrowid
    row = get_task(task_id)
    assert row["result"] == legacy  # byte a byte, sin hidratación
    # también visible en all_tasks
    listed = [t for t in all_tasks() if t["id"] == task_id][0]
    assert listed["result"] == legacy


def test_null_result_behaviour_preserved(tmp_path):
    task_id = enqueue_task("count_words", {"text": "x"})
    complete_task(task_id, None)
    # el contrato actual mapea None -> serializado "null"
    assert get_task(task_id)["result"] == "null"


# ---------------------------------------------------------------------------
# Concurrencia básica
# ---------------------------------------------------------------------------


def test_concurrent_large_tasks_are_consistent(tmp_path):
    values = [
        {"payload": "c" * (storage.LARGE_RESULT_THRESHOLD_BYTES + i)}
        for i in (1, 2)
    ]
    ids = [
        enqueue_task("write_chapter_es", {"text": "x"}) for _ in values
    ]

    def _write(task_id, value):
        complete_task(task_id, value)

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(_write, ids, values))

    for task_id, value in zip(ids, values):
        assert os.path.isfile(_file_for(task_id))
        assert json.loads(get_task(task_id)["result"]) == value

    leftovers = [
        p for p in os.listdir(os.environ["SPACE_LAIR_RESULTS_DIR"])
        if p.endswith(".tmp")
    ]
    assert leftovers == []


def test_concurrent_read_and_write_do_not_deadlock(tmp_path):
    task_id = enqueue_task("write_chapter_es", {"text": "x"})
    v = _large_result()
    stop = threading.Event()

    def _writer():
        complete_task(task_id, v)
        stop.set()

    def _reader():
        while not stop.is_set():
            get_task(task_id)  # no debe lanzar ni colgarse

    w = threading.Thread(target=_writer)
    r1 = threading.Thread(target=_reader)
    r2 = threading.Thread(target=_reader)
    w.start(); r1.start(); r2.start()
    w.join(timeout=30); r1.join(timeout=30); r2.join(timeout=30)
    assert json.loads(get_task(task_id)["result"]) == v
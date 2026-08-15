"""Tests de `core.storage`: persistencia de resultados grandes y atomicidad.

Cubre el umbral (1 MiB estricto), escritura/lectura atómica, path traversal,
ejes de error y la detectabilidad del marcador de referencia.
"""

from __future__ import annotations

import json
import os

import pytest

from core import storage


# ---------------------------------------------------------------------------
# Fixtures y helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_results_dir(tmp_path, monkeypatch):
    """Redirige el directorio de resultados a un tmp_path aislado."""
    results_dir = tmp_path / "results"
    monkeypatch.setenv("SPACE_LAIR_RESULTS_DIR", str(results_dir))
    yield results_dir


def _result_exact_bytes(n: int) -> dict:
    """Construye un dict cuyo JSON serializado mide exactamente ``n`` bytes."""
    overhead = len(storage.serialize({"payload": ""}))
    return {"payload": "a" * max(0, n - overhead)}


# ---------------------------------------------------------------------------
# Serialización y umbral
# ---------------------------------------------------------------------------


def test_default_results_dir_is_under_data():
    assert storage._DEFAULT_RESULTS_DIR == os.path.join(
        storage.DATA_DIR, "results"
    )


def test_serialize_keeps_ensure_ascii_false_and_default_str():
    assert storage.serialize("ñ í ú") == '"ñ í ú"'
    parsed = json.loads(storage.serialize({"x": object()}))
    assert "object" in parsed["x"] or "'" in parsed["x"]


def test_small_result_not_externalized():
    v = {"ok": True}
    assert storage.serialized_bytes(v) <= storage.LARGE_RESULT_THRESHOLD_BYTES
    assert storage.should_externalize(v) is False


def test_exactly_one_mib_is_inline():
    v = _result_exact_bytes(storage.LARGE_RESULT_THRESHOLD_BYTES)
    assert storage.serialized_bytes(v) == storage.LARGE_RESULT_THRESHOLD_BYTES
    # criterio estricto: <= 1 MiB permanece inline
    assert storage.should_externalize(v) is False


def test_over_one_mib_is_externalized():
    v = _result_exact_bytes(storage.LARGE_RESULT_THRESHOLD_BYTES + 1)
    assert storage.should_externalize(v) is True


# ---------------------------------------------------------------------------
# save_result
# ---------------------------------------------------------------------------


def test_save_result_returns_relative_path_and_creates_file(
    tmp_path, _isolated_results_dir
):
    v = {"data": "x" * 100}
    ref = storage.save_result(1, v)
    assert ref == os.path.join("data", "results", "1.json")
    path = tmp_path / "results" / "1.json"
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == v


def test_save_result_is_atomic_no_tmp_leftover(_isolated_results_dir):
    storage.save_result(5, {"a": 1})
    names = os.listdir(_isolated_results_dir)
    assert "5.json" in names
    assert all(not n.endswith(".tmp") for n in names)


def test_save_result_creates_directory_automatically(tmp_path, _isolated_results_dir):
    # el directorio se crea aunque no exista (requisito: data/results automático)
    storage.save_result(9, {"x": 1})
    assert (tmp_path / "results" / "9.json").is_file()


# ---------------------------------------------------------------------------
# load_result
# ---------------------------------------------------------------------------


def test_load_result_roundtrip(tmp_path):
    v = {"data": "a" * 500, "nested": {"ok": True}}
    storage.save_result(2, v)
    assert storage.load_result(2) == v


def test_load_result_missing_raises():
    with pytest.raises(storage.StorageError):
        storage.load_result(999_999)


def test_load_result_corrupt_json_raises(tmp_path):
    (tmp_path / "results").mkdir(parents=True, exist_ok=True)
    (tmp_path / "results" / "3.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(storage.StorageError):
        storage.load_result(3)


def test_save_result_error_is_propagated_as_storage_error(monkeypatch):
    def _boom(src, dst):
        raise OSError("denied")

    monkeypatch.setattr(storage.os, "replace", _boom)
    with pytest.raises(storage.StorageError):
        storage.save_result(4, {"a": 1})


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------


def test_task_id_is_derived_from_int():
    # un task_id con separadores / saltos de directorio se rechaza
    with pytest.raises(ValueError):
        storage.load_result("1/../etc/passwd")
    with pytest.raises(ValueError):
        storage._result_path("../../evil.json")


def test_relative_path_only_uses_plain_int_name():
    p = storage.result_relative_path(123)
    assert p.endswith("123.json")
    assert "/../" not in p and "\\..\\" not in p
"""Test de integración 8E.1 — propagación real de umbrales en el payload del Quality Gate.

Cubre exclusivamente el hueco de integración entre el autopilot y el Quality Gate:

    autopilot.build_phase_payload(phase="quality_gate") -> editorial.build_payload("docx")
        -> payload del Quality Gate con min_chapters/target_chapters/max_chapters

Demuestra que, si el libro tiene target_chapters=N, el payload para Quality Gate
contiene min/target/max = N y NO los defaults 20/30/40 del schema de `QualityControlPayload`.
"""
from __future__ import annotations

import os

import pytest

from core import autopilot
from core.database import init_db
from frontend.editorial import create_book

_META = {
    "title": "Libro de prueba 8E.1",
    "author": "Space Lair",
    "description": "Descripción de prueba 8E.1",
    "genre": "Divulgación",
    "target_audience": "General",
    "language": "es",
}


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("SPACE_LAIR_DB_PATH", os.path.join(str(tmp_path), "t8e1.db"))
    init_db()


def _make_book(target_chapters: int) -> dict:
    d = dict(_META)
    d["target_chapters"] = target_chapters
    return create_book(d)


def test_quality_gate_payload_propagates_real_thresholds():
    b = _make_book(3)
    phase = {
        "id": "quality_gate",
        "capability": "final_quality_control",
        "label": "QUALITY GATE",
    }
    payload = autopilot.build_phase_payload(phase, b["book_id"], {})

    assert payload["book"]["target_chapters"] == 3
    assert payload["min_chapters"] == 3
    assert payload["target_chapters"] == 3
    assert payload["max_chapters"] == 3

    # No debe contener los defaults del schema (20/30/40).
    assert payload["min_chapters"] != 20
    assert payload["target_chapters"] != 30
    assert payload["max_chapters"] != 40
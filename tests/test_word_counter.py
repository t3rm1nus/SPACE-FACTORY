"""Tests unitarios del módulo word_counter."""

from __future__ import annotations

import pytest

from modules.word_counter.main import execute, health_check


def test_health_check_always_healthy() -> None:
    """word_counter no tiene dependencias: siempre sano."""
    result = health_check()
    assert result["healthy"] is True
    assert result["status"].startswith("🟢")
    assert result["dependencies"] == {}


def test_execute_counts_words_and_chars() -> None:
    text = "Hola mundo desde Space Lair"
    result = execute({"text": text})
    assert result["word_count"] == 5
    assert result["char_count"] == len(text)
    assert result["char_count_no_spaces"] == len(text.replace(" ", ""))
    expected_avg = round(sum(len(w) for w in text.split()) / 5, 3)
    assert result["avg_word_length"] == expected_avg
    assert result["provider"] == "none"


def test_execute_strips_and_collapses_internal_spaces() -> None:
    # validate_payload recorta el texto (field_validator); el contador usa el texto recortado.
    result = execute({"text": "  Hola   mundo  "})
    assert result["word_count"] == 2
    # "Hola   mundo" → 3 espacios internos preservados, char_count_no_spaces = 9
    assert result["char_count_no_spaces"] == 9


def test_execute_invalid_payload_raises() -> None:
    with pytest.raises(Exception):
        execute({"text": "   "})  # text vacío tras stripped → validator falla

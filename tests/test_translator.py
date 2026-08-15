"""Tests unitarios del módulo translator (capabilities: translate_es_en, translate_en_es)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from modules.translator.main import (
    _build_review_prompt,
    _build_translation_prompt,
    _extract_json,
    _extract_numbers,
    _fallback_review,
    _fallback_translate,
    _languages,
    execute,
    health_check,
)


def _payload_es() -> dict:
    return {
        "source_text": (
            "El río Amazonas desemboca en el Atlántico. "
            "El crecimiento fue del 45% en 2019, según la ONU."
        ),
        "style_guide": "formal",
        "protected_terms": ["Amazonas", "ONU"],
    }


def _payload_en() -> dict:
    return {
        "source_text": (
            "The Amazon river flows into the Atlantic. "
            "Growth reached 45% in 2019, according to the UN."
        ),
        "style_guide": "formal",
        "protected_terms": ["Amazon", "UN"],
    }


def test_health_check_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.translator.main as main

    monkeypatch.setattr(main, "get_provider", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = health_check()
    assert result["healthy"] is False
    assert "error" in result["dependencies"]


def test_languages_mapping() -> None:
    assert _languages("translate_es_en") == ("es", "en")
    assert _languages("translate_en_es") == ("en", "es")


def test_build_translation_prompt_es_en() -> None:
    prompt = _build_translation_prompt(_payload_es(), "es", "en")
    assert "EN" in prompt
    assert "Amazonas" in prompt
    assert "ONU" in prompt
    assert "no una traducción literal" in prompt
    assert "MANTENER TAL CUAL" in prompt
    assert "ADAPTAR DE FORMA NATURAL" in prompt


def test_build_review_prompt_mentions_checks() -> None:
    prompt = _build_review_prompt(_payload_es(), "Algun texto", "es", "en")
    lower = prompt.lower()
    for token in ("omissions", "numbers", "names", "paragraphs", "quotes", "meaning"):
        assert token in lower


def test_extract_json_happy_path() -> None:
    data = _extract_json('{"status":"PASS","issues":[]}')
    assert data["status"] == "PASS"


def test_extract_json_fenced() -> None:
    data = _extract_json('```json\n{"status":"WARNING","issues":[]}\n```')
    assert data["status"] == "WARNING"


def test_extract_json_invalid_returns_empty() -> None:
    assert _extract_json("esto no es json") == {}


def test_extract_numbers_finds_values() -> None:
    nums = _extract_numbers("Crecimiento del 45% en 2019 y $1,200 millones.")
    assert "45%" in nums
    assert "2019" in nums


def test_fallback_review_detects_missing_numbers() -> None:
    review = _fallback_review("El 45% y el 2020.", "Some text without numbers.")
    assert review["status"] == "WARNING"
    assert any(i["issue_type"] == "Numbers" for i in review["issues"])


def test_fallback_review_pass_when_numbers_match() -> None:
    review = _fallback_review("El 45% del total.", "45% of the total.")
    assert review["status"] == "PASS"
    assert review["issues"] == []


def test_fallback_translate_returns_source_unchanged() -> None:
    result = _fallback_translate(_payload_es())
    assert result["translated_text"] == _payload_es()["source_text"]
    assert result["review_status"] == "WARNING"


def test_execute_fallback_when_llm_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM falla, execute devuelve el texto original sin cambios."""
    import modules.translator.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ollama not available")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())

    out = execute(_payload_es(), capability="translate_es_en")
    assert out["translated_text"] == _payload_es()["source_text"]
    assert out["review_status"] == "WARNING"




def test_execute_llm_success_es_en(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con LLM, se traduce y se audita la traducción (dos pasos)."""
    import modules.translator.main as main

    translated = "The Amazon river flows into the Atlantic. Growth reached 45% in 2019, per the UN."
    review_json = json.dumps({"status": "PASS", "issues": []})

    class FakeResult:
        text = ""
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, prompt: str, *args: Any, **kwargs: Any) -> FakeResult:
            r = FakeResult()
            if "control de calidad" in prompt:
                r.text = review_json
            else:
                r.text = translated
            return r

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload_es(), capability="translate_es_en")
    assert out["translated_text"] == translated
    assert out["review_status"] == "PASS"
    assert out["review_issues"] == []

    from core.schemas import validate_output

    valid = validate_output("translate_es_en", out)
    assert valid["translated_text"] == translated


def test_execute_llm_success_en_es(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.translator.main as main

    translated = "El río Amazonas desemboca en el Atlántico. El crecimiento llegó al 45% en 2019, según la ONU."
    review_json = json.dumps({"status": "WARNING", "issues": [
        {"issue_type": "Meaning", "severity": "INFO", "description": "Matiz ajustado."}
    ]})

    class FakeResult:
        text = ""
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 8
        output_tokens = 15
        cost = 0.0
        raw_response = {}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, prompt: str, *args: Any, **kwargs: Any) -> FakeResult:
            r = FakeResult()
            if "control de calidad" in prompt:
                r.text = review_json
            else:
                r.text = translated
            return r

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload_en(), capability="translate_en_es")
    assert out["translated_text"] == translated
    assert out["review_status"] == "WARNING"
    assert out["review_issues"][0]["issue_type"] == "Meaning"

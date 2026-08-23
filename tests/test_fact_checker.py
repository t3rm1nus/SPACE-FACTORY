"""Tests unitarios del módulo fact_checker (capability: fact_check_chapter)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from modules.fact_checker.main import (
    _build_prompt,
    _fallback_result,
    _heuristic_issues,
    _parse_llm_output,
    execute,
    health_check,
)


def _payload() -> dict:
    return {
        "chapter_text": (
            "La compañía aseguró haber vendido el 45% más que el año anterior. "
            "Un portavoz dijo: \"Vamos camino de duplicar la cuota\". "
            "El informe de 2019 estableció el punto de partida."
        ),
        "sources": [
            {"url": "https://example.com/report", "title": "Informe 2019", "source_type": "web"},
        ],
        "target_language": "es",
    }


def test_health_check_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.fact_checker.main as main

    monkeypatch.setattr(main, "get_provider", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = health_check()
    assert result["healthy"] is False
    assert "error" in result["dependencies"]


def test_build_prompt_includes_inputs() -> None:
    prompt = _build_prompt(_payload())
    assert "45% más" in prompt
    assert "https://example.com/report" in prompt
    assert "Informe 2019" in prompt
    assert "NUNCA inventes fuentes" in prompt
    assert "unsupported_claims" in prompt


def test_parse_llm_output_happy_path() -> None:
    data = _parse_llm_output(
        '{"status":"WARNING","claims_checked":2,"issues":[],"corrections":[],"unsupported_claims":[]}'
    )
    assert data["status"] == "WARNING"
    assert data["claims_checked"] == 2


def test_parse_llm_output_fenced_json() -> None:
    text = '```json\n{"status":"FAIL","claims_checked":1,"issues":[],"corrections":[],"unsupported_claims":["x"]}\n```'
    data = _parse_llm_output(text)
    assert data["status"] == "FAIL"
    assert data["unsupported_claims"] == ["x"]


def test_parse_llm_output_invalid_returns_graceful_result() -> None:
    data = _parse_llm_output("esto no es json")
    assert data["status"] == "WARNING"
    assert data["corrections"]


def test_heuristic_detects_numbers_quotes_dates() -> None:
    issues = _heuristic_issues(_payload()["chapter_text"], _payload()["sources"])
    reasons = " ".join(i["reason"] for i in issues)
    assert "valores numéricos" in reasons
    assert "Citas" in reasons
    assert "fechas" in reasons


def test_heuristic_error_when_no_sources() -> None:
    issues = _heuristic_issues("Alguna afirmación sin respaldo.", [])
    assert any(i["severity"] == "ERROR" for i in issues)


def test_fallback_status_fail_with_error_issue() -> None:
    payload = _payload()
    payload["sources"] = []
    result = _fallback_result(payload)
    assert result["status"] == "FAIL"
    assert result["unsupported_claims"]
    # Nunca debe fabricar una URL de fuente: ningún issue aporta source_url real
    assert all(i.get("source_url") is None for i in result["issues"])


def test_fallback_pass_when_clean() -> None:
    payload = _payload()
    payload["chapter_text"] = "Texto sencillo sin cifras, citas ni fechas concretas."
    result = _fallback_result(payload)
    assert result["status"] == "PASS"


def test_execute_fallback_when_llm_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM falla, execute debe devolver un resultado heurístico válido."""
    import modules.fact_checker.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ollama not available")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())

    out = execute(_payload())
    assert out["status"] in ("PASS", "WARNING", "FAIL")
    assert out["claims_checked"] >= 1
    assert isinstance(out["issues"], list)
    assert isinstance(out["corrections"], list)
    assert isinstance(out["unsupported_claims"], list)


def test_execute_llm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM responde, se normaliza el JSON y las claves del contrato."""
    import modules.fact_checker.main as main

    llm_json = json.dumps(
        {
            "status": "FAIL",
            "claims_checked": 3,
            "issues": [
                {
                    "claim": "Ventas +45%",
                    "severity": "ERROR",
                    "reason": "Sin fuente que lo respalde.",
                    "source_url": None,
                    "suggestion": "Aportar informe auditable.",
                }
            ],
            "corrections": ["Añadir fuente al claim de ventas."],
            "unsupported_claims": ["Ventas +45%"],
        }
    )

    class FakeResult:
        text = llm_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": llm_json}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["status"] == "FAIL"
    assert out["claims_checked"] == 1
    assert out["issues"][0]["severity"] == "ERROR"
    # La fuente no se inventa: sigue null en el issue
    assert out["issues"][0]["source_url"] is None
    assert out["unsupported_claims"] == ["Ventas +45%"]

    # El resultado valida contra el esquema de salida
    from core.schemas import validate_output

    valid = validate_output("fact_check_chapter", out)
    assert valid["status"] in ("PASS", "WARNING", "FAIL")


def test_claims_checked_zero_when_issues_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM devuelve claims_checked>0 pero issues=[], debe forzarse a 0."""
    import modules.fact_checker.main as main

    llm_json = json.dumps(
        {
            "status": "PASS",
            "claims_checked": 14,
            "issues": [],
            "corrections": [],
            "unsupported_claims": [],
            "supported_claims": 0,
            "conflicting_claims": 0,
        }
    )

    class FakeResult:
        text = llm_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": llm_json}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["claims_checked"] == 0
    assert out["issues"] == []
    assert out["status"] == "PASS"


def test_claims_checked_matches_issues_when_issues_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si el LLM devuelve issues válidos, claims_checked debe coincidir con ellos."""
    import modules.fact_checker.main as main

    llm_json = json.dumps(
        {
            "status": "WARNING",
            "claims_checked": 14,
            "issues": [
                {
                    "claim": "Cifra sin fuente",
                    "severity": "WARNING",
                    "reason": "Sin fuente",
                    "source_url": None,
                    "suggestion": "Añadir fuente",
                }
            ],
            "corrections": ["Añadir fuente"],
            "unsupported_claims": [],
            "supported_claims": 1,
            "conflicting_claims": 0,
        }
    )

    class FakeResult:
        text = llm_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": llm_json}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["claims_checked"] == 1
    assert len(out["issues"]) == 1
    assert out["issues"][0]["claim"] == "Cifra sin fuente"


def test_execute_dedupes_repeated_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    """El LLM a veces repite la misma claim en un único JSON de salida.

    El dedupe por texto normalizado (lowercase + strip + espacios colapsados)
    debe conservar solo la primera aparición y claims_checked debe reflejar
    el número de claims ÚNICOS (ej. book_39 cap 173: 14 = 7 únicas × 2).
    """
    import modules.fact_checker.main as main

    llm_json = json.dumps(
        {
            "status": "WARNING",
            "claims_checked": 14,
            "issues": [
                {
                    "claim": "Latveria es una nación ficticia de Marvel",
                    "severity": "WARNING",
                    "reason": "Sin fuente",
                    "source_url": None,
                    "suggestion": None,
                },
                {
                    "claim": "  latveria   es una nación ficticia de Marvel  ",
                    "severity": "ERROR",
                    "reason": "Duplicado con distinto formato",
                    "source_url": None,
                    "suggestion": None,
                },
                {
                    "claim": "Doom fue lanzado en 2016",
                    "severity": "INFO",
                    "reason": "Verificar fecha",
                    "source_url": None,
                    "suggestion": None,
                },
                {
                    "claim": "DOOM fue lanzado en 2016.",
                    "severity": "WARNING",
                    "reason": "Duplicado (solo difiere punto final, se cuenta aparte)",
                    "source_url": None,
                    "suggestion": None,
                },
                {
                    "claim": "Doom fue lanzado    en 2016",
                    "severity": "ERROR",
                    "reason": "Duplicado con espacios múltiples",
                    "source_url": None,
                    "suggestion": None,
                },
            ],
            "corrections": [],
            "unsupported_claims": [],
            "supported_claims": 0,
            "conflicting_claims": 0,
        }
    )

    class FakeResult:
        text = llm_json
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": llm_json}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    # 5 issues crudos -> 3 claims únicos tras normalizar (2 duplicados fuera)
    assert out["claims_checked"] == 3
    assert len(out["issues"]) == 3
    claims = [i["claim"] for i in out["issues"]]
    assert len({c.lower() for c in claims}) == len(claims)
    # Se conserva la PRIMERA aparición de cada claim único
    assert claims[0] == "Latveria es una nación ficticia de Marvel"
    assert claims[1] == "Doom fue lanzado en 2016"

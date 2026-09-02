"""Tests unitarios del módulo fact_checker (capability: fact_check_chapter)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from modules.fact_checker.main import (
    _build_prompt,
    _escalate_fabrication_issue,
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
                    # Con source_url presente la segunda pasada SÍ se ejecuta
                    # (ajuste de diseño: sin fuente el ERROR degrada directo).
                    "source_url": "https://example.com/report",
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

        def __init__(self) -> None:
            self.calls = 0

        def generate(self, *args: Any, **kwargs: Any):
            self.calls += 1
            if self.calls == 1:
                return FakeResult()  # primera pasada: el JSON con la issue ERROR
            # §17 #22 segunda pasada: confirma el ERROR de forma binaria.

            class ConfirmResult:
                text = "ERROR"
                provider = "ollama"
                model = "llama3.1"
                input_tokens = 1
                output_tokens = 1
                cost = 0.0
                raw_response = {}

            return ConfirmResult()

    provider = FakeProvider()
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["status"] == "FAIL"
    assert out["claims_checked"] == 1
    assert out["issues"][0]["severity"] == "ERROR"  # segunda pasada CONFIRMA
    assert out["issues"][0]["consistency_check"] == "CONFIRMED"
    # La fuente SÍ está presente en el issue (no se inventa, viene del LLM)
    assert out["issues"][0]["source_url"] == "https://example.com/report"
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


# ---------------------------------------------------------------------------
# §17 #20 PASO 1 — fabricación factual (caso REAL book_59 cap.2)
# ---------------------------------------------------------------------------

BOOK59_SOURCES = [
    {
        "title": "Limpieza étnica, ocupación militar y genocidio en Palestina - EHU",
        "url": "https://www.ehu.eus/es/web/campusa/-/limpieza-etnica-ocupacion-militar-y-genocidio-en-palestina",
        "source_type": "web_searxng",
        "content": "Entre 1947-1949, las milicias sionistas expulsaron del territorio de la Palestina histórica.",
    },
    {
        "title": "Palestina: genocidio y guerra de liberación - litci.org",
        "url": "https://litci.org/es/palestina-genocidio-y-guerra-de-liberacion/",
        "source_type": "web_searxng",
        "content": "Estos gobiernos se limitan a realizar protestas verbales contra el genocidio.",
    },
    {
        "title": "Genocidio cultural",
        "url": "https://es.wikipedia.org/wiki/Genocidio_cultural",
        "source_type": "web_searxng",
        "content": "El genocidio cultural es la destrucción deliberada del patrimonio cultural.",
    },
    {
        "title": "Guerra de Gaza - Wikipedia",
        "url": "https://es.wikipedia.org/wiki/Guerra_de_Gaza",
        "source_type": "web_searxng",
        "content": "La guerra de Gaza comenzó el 7 de octubre de 2023.",
    },
]

# Claims exactas detectadas por fact_checker en task 472 (book_59 cap.2):
# el LLM las clasificó WARNING pese a no tener ningún soporte en las fuentes.
BOOK59_CLAIMS = [
    {
        "claim": (
            "The first major concentration camp in Palestine was established "
            "in 1942, during World War II."
        ),
        "severity": "WARNING",
        "reason": (
            "This statement is not supported by any of the provided sources. "
            "The sources focus on the genocidio and do not mention "
            "concentration camps specifically."
        ),
        "source_url": None,
        "suggestion": None,
    },
    {
        "claim": (
            "Adolf Eichmann's plan was to create a series of camps that would "
            "serve multiple purposes: housing displaced Palestinians, serving "
            "as labor sources for the German military, and ultimately "
            "facilitating their eventual extermination."
        ),
        "severity": "WARNING",
        "reason": (
            "This statement is not supported by any of the provided sources. "
            "The sources focus on the genocidio and do not mention Eichmann's "
            "plans specifically."
        ),
        "source_url": None,
        "suggestion": None,
    },
]


def test_fabrication_signature_detection() -> None:
    """La firma estructural fecha+nombre propio+cifra se detecta correctamente."""
    from modules.fact_checker.main import _has_fabrication_signature

    assert _has_fabrication_signature(
        "The first major concentration camp in Palestine was established in 1942."
    )
    assert _has_fabrication_signature(
        "Majdal Shams albergó entre 50,000 y 100,000 prisioneros hasta 1948."
    )
    # Sin cifra/fecha concreta NO hay firma
    assert not _has_fabrication_signature(
        "El conflicto causó un gran sufrimiento a la población palestina."
    )


def _fake_provider(llm_json: str) -> tuple:
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

    return FakeProvider()


def test_book59_fabricated_claims_escalate_to_error_and_fail_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caso REAL book_59 cap.2: claims de campos de concentración sin soporte.

    El LLM devolvió WARNING para afirmaciones fabricadas (Eichmann, 1942-1948,
    cifras de víctimas) que ninguna de las 4 fuentes reales menciona. Con el
    fix §17 #20 deben salir ERROR y quality_gate=FAIL dentro del módulo.
    """
    import modules.fact_checker.main as main

    llm_json = json.dumps(
        {
            "status": "FAIL",
            "claims_checked": 12,
            "issues": BOOK59_CLAIMS,
            "corrections": [],
            "unsupported_claims": [],
            "supported_claims": 0,
            "conflicting_claims": 0,
        }
    )

    provider = _fake_provider(llm_json)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = (
        "## Los Eventos Trágicos\n\n"
        "The first major concentration camp in Palestine was established in "
        "1942, during World War II. Adolf Eichmann's plan was to create a "
        "series of camps that would serve multiple purposes. Conditions inside "
        "the concentration camps were abysmal and forced labor was common, "
        "with estimates ranging from fifty thousand to one hundred thousand "
        "victims between 1942 and 1948 in Majdal Shams, Nahariyya, Jaffa and "
        "Safed."
    )
    payload["sources"] = BOOK59_SOURCES

    out = execute(payload)

    assert out["status"] == "FAIL"
    # Ambas claims fabricadas escalan a ERROR
    severities = {i["severity"] for i in out["issues"]}
    assert severities == {"ERROR"}
    for issue in out["issues"]:
        assert "fabricación factual" in issue["reason"]
        assert issue["source_url"] is None  # nunca se inventa fuente
    # La pieza clave: el gate del MÓDULO ahora bloquea
    assert out["quality_gate"] == "FAIL"


def test_llm_error_downgraded_when_consistency_check_disagrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ERROR subjetivo del LLM + segunda pasada que disiente -> WARNING."""
    import modules.fact_checker.main as main

    provider = _two_pass_provider(
        _liberica_llm_json(source_url="https://example.com/report"), "DEFENDIBLE"
    )
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    issue = out["issues"][0]
    assert issue["severity"] == "WARNING"  # degradada, ya no bloquea
    assert main._CONSISTENCY_DOWNGRADE_NOTE in issue["reason"]
    assert issue["consistency_check"] == "DOWNGRADED"
    assert provider.calls == 2  # primera pasada + verificación
    assert out["quality_gate"] == "PASS"


def test_llm_error_kept_when_consistency_check_agrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ambas pasadas ERROR -> el ERROR se mantiene y el gate falla."""
    import modules.fact_checker.main as main

    provider = _two_pass_provider(
        _liberica_llm_json(source_url="https://example.com/report"), "ERROR"
    )
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    issue = out["issues"][0]
    assert issue["severity"] == "ERROR"  # confirmado: sin cambios
    assert issue["consistency_check"] == "CONFIRMED"
    assert main._CONSISTENCY_DOWNGRADE_NOTE not in issue["reason"]
    assert out["quality_gate"] == "FAIL"
    assert out["status"] == "FAIL"


def test_structural_fabrication_error_skips_consistency_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un ERROR escalado por _escalate_fabrication_issue NO se re-verifica.

    Es estructural (firma fecha+nombre+cifra), no subjetivo: la segunda pasada
    LLM no debe ocurrir ni poder degradarlo.
    """
    import modules.fact_checker.main as main

    # Claim tipo book_59: WARNING desde el LLM, la escalada la sube a ERROR.
    llm_json = json.dumps(
        {
            "status": "FAIL",
            "claims_checked": 1,
            "issues": [
                {
                    "claim": (
                        "The first major concentration camp in Palestine was "
                        "established in 1942 without any historical support."
                    ),
                    "severity": "WARNING",
                    "reason": (
                        "El capítulo no proporciona evidencia; la afirmación "
                        "no tiene una fuente."
                    ),
                    "source_url": None,
                    "suggestion": None,
                }
            ],
            "corrections": [],
            "unsupported_claims": [],
            "supported_claims": 0,
            "conflicting_claims": 0,
        }
    )
    # Si la segunda pasada se llamara, degradaría: no debe ocurrir.
    provider = _two_pass_provider(llm_json, "DEFENDIBLE")
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = (
        "The first major concentration camp in Palestine was established in "
        "1942, according to claims without sources."
    )

    out = execute(payload)
    issue = out["issues"][0]
    assert provider.calls == 1  # SOLO la primera pasada: no hay re-verificación
    assert issue["severity"] == "ERROR"  # estructural: se mantiene ERROR
    assert "fabricación factual" in issue["reason"]
    assert "consistency_check" not in issue
    assert out["quality_gate"] == "FAIL"


def test_supported_specific_claim_not_escalated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una claim con fuente asociada (source_url) NO debe escalar a ERROR."""
    import modules.fact_checker.main as main

    llm_json = json.dumps(
        {
            "status": "PASS",
            "claims_checked": 1,
            "issues": [
                {
                    "claim": "La guerra de Gaza comenzó el 7 de octubre de 2023 según Wikipedia.",
                    "severity": "INFO",
                    "reason": "Fecha verificable en la fuente citada.",
                    "source_url": "https://es.wikipedia.org/wiki/Guerra_de_Gaza",
                    "suggestion": None,
                }
            ],
            "corrections": [],
            "unsupported_claims": [],
            "supported_claims": 1,
            "conflicting_claims": 0,
        }
    )

    provider = _fake_provider(llm_json)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = (
        "La guerra de Gaza comenzó el 7 de octubre de 2023 según la fuente "
        "citada, con consecuencias documentadas para la población civil."
    )
    payload["sources"] = BOOK59_SOURCES

    out = execute(payload)
    assert out["quality_gate"] == "PASS"
    assert out["status"] in ("PASS", "WARNING")


def test_consistency_check_timeout_defaults_to_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout/error/respuesta inválida en la verificación -> degrada (fail-safe)."""
    import modules.fact_checker.main as main

    for bad_second in (
        RuntimeError("ollama timeout"),
        "no sé",  # salida inválida: ni ERROR ni DEFENDIBLE
        "",       # vacía
    ):
        provider = _two_pass_provider(
            _liberica_llm_json(source_url="https://example.com/report"), bad_second
        )
        monkeypatch.setattr(main, "get_provider", lambda p=provider: p)
        monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

        out = execute(_payload())
        issue = out["issues"][0]
        assert issue["severity"] == "WARNING", f"fallo con segunda pasada={bad_second!r}"
        assert main._CONSISTENCY_DOWNGRADE_NOTE in issue["reason"]
        assert out["quality_gate"] == "PASS"


def test_book65_liberica_real_case_reproduction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproducción del caso real book_65 en ambos escenarios del mock.

    - Escenario A (lo observado en producción antes del ajuste de diseño:
      juicio subjetivo no replicable): la segunda pasada disiente -> la claim
      se degrada y la fase ya no agota reintentos.
    - Escenario B: si la segunda pasada confirma, el ERROR se mantiene (el fix
      no oculta errores reales).
    Ambos escenarios usan una fuente presente para forzar la ruta de segunda
    pasada; el caso real SIN fuente se cubre en
    test_error_without_source_url_skips_consistency_check_and_downgrades.
    """
    import modules.fact_checker.main as main

    # Escenario A: disiente -> WARNING
    provider_a = _two_pass_provider(
        _liberica_llm_json(source_url="https://example.com/report"), "DEFENDIBLE"
    )
    monkeypatch.setattr(main, "get_provider", lambda: provider_a)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")
    out_a = execute(_payload())
    assert out_a["issues"][0]["severity"] == "WARNING"
    assert out_a["issues"][0]["consistency_check"] == "DOWNGRADED"
    assert out_a["quality_gate"] == "PASS"

    # Escenario B: confirma -> ERROR intacto
    provider_b = _two_pass_provider(
        _liberica_llm_json(source_url="https://example.com/report"), "ERROR"
    )
    monkeypatch.setattr(main, "get_provider", lambda: provider_b)
    out_b = execute(_payload())
    assert out_b["issues"][0]["severity"] == "ERROR"
    assert out_b["quality_gate"] == "FAIL"


def test_error_without_source_url_skips_consistency_check_and_downgrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caso real book_65 (tasks 890/891): ERROR subjetivo SIN source_url.

    Se degrada DIRECTAMENTE a WARNING sin invocar la verificación de
    consistencia (CERO llamadas LLM extra) y con trazabilidad
    consistency_check="SKIPPED_NO_SOURCE".
    """
    import modules.fact_checker.main as main

    provider = _two_pass_provider(_liberica_llm_json(), "ERROR")
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    issue = out["issues"][0]
    assert provider.calls == 1  # SOLO la primera pasada: skip sin llamada extra
    assert issue["severity"] == "WARNING"
    assert (
        "[Degradado a WARNING: ERROR sin source_url y sin firma de "
        "fabricación estructural]" in issue["reason"]
    )
    assert issue["consistency_check"] == "SKIPPED_NO_SOURCE"
    assert main._CONSISTENCY_DOWNGRADE_NOTE not in issue["reason"]
    assert out["quality_gate"] == "PASS"


def test_error_with_source_url_still_uses_consistency_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión: con source_url presente la segunda pasada sigue ocurriendo."""
    import modules.fact_checker.main as main

    # Confirma -> ERROR intacto vía _verify_error_consistency
    provider = _two_pass_provider(
        _liberica_llm_json(source_url="https://example.com/report"), "ERROR"
    )
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    issue = out["issues"][0]
    assert provider.calls == 2  # primera pasada + verificación de consistencia
    assert issue["severity"] == "ERROR"
    assert issue["consistency_check"] == "CONFIRMED"
    assert out["quality_gate"] == "FAIL"


# ---------------------------------------------------------------------------
# §17 #22: verificación de consistencia para ERROR subjetivos del LLM
# (fix book_65 "café Liberica" / book_64 cafés de Madrid)
# ---------------------------------------------------------------------------

def _two_pass_provider(first_json: str, second_answer):
    """Provider falso de dos pasadas: 1ª = JSON del fact-check, 2ª = verificación.

    Registra las llamadas en .calls para verificar si la segunda pasada ocurrió.
    """
    class Provider:
        name = "ollama"
        model = "llama3.1"

        def __init__(self) -> None:
            self.calls = 0

        def generate(self, *args: Any, **kwargs: Any):
            self.calls += 1
            if self.calls == 1:
                class R1:
                    text = first_json
                    provider = "ollama"
                    model = "llama3.1"
                    input_tokens = 10
                    output_tokens = 20
                    cost = 0.0
                    raw_response = {}
                return R1()
            if isinstance(second_answer, Exception):
                raise second_answer

            class R2:
                text = second_answer
                provider = "ollama"
                model = "llama3.1"
                input_tokens = 1
                output_tokens = 1
                cost = 0.0
                raw_response = {}
            return R2()

    return Provider()


def _liberica_llm_json(source_url: str | None = None) -> str:
    """Reproducción EXACTA del caso real book_65 cap.431 (task 888).

    En producción real source_url es None (tasks 890/891); los tests que
    ejercitan la segunda pasada usan una fuente presente para forzar esa ruta.
    """
    return json.dumps(
        {
            "status": "FAIL",
            "claims_checked": 1,
            "issues": [
                {
                    "claim": (
                        "El café Liberica es una variedad única de café que "
                        "es muy rara y valiosa."
                    ),
                    "severity": "ERROR",
                    "reason": (
                        "La información sobre el café Liberica en el capítulo "
                        "no es precisa. El café Liberica no es tan raro ni "
                        "valioso como se describe, y su cultivo limitado y "
                        "vulnerabilidad ante enfermedades son temas de debate "
                        "entre los expertos."
                    ),
                    "source_url": source_url,
                    "suggestion": (
                        "El texto debería ser corregido para reflejar la "
                        "realidad del café Liberica."
                    ),
                }
            ],
            "corrections": [],
            "unsupported_claims": [],
            "supported_claims": 0,
            "conflicting_claims": 0,
        }
    )


def test_consistency_budget_exhausted_downgrades_remaining_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Techo agregado (fix task 895): >6 claims ERROR con source_url presente.

    Cada verificación "tarda" >20s (reloj simulado): las primeras claims se
    verifican contra el provider mientras quede presupuesto; en cuanto el
    presupuesto agregado ya no cubre otra llamada completa
    (FACT_CHECK_CONSISTENCY_TIMEOUT), las restantes se degradan DIRECTAMENTE
    a WARNING con consistency_check="SKIPPED_BUDGET_EXHAUSTED", sin llamar al
    provider para esas.
    """
    import modules.fact_checker.main as main

    n_claims = 7
    issues = [
        {
            "claim": (
                "La variedad descrita en el capítulo no es tan escasa como "
                f"se afirma en la afirmación número {idx + 1}."
            ),
            "severity": "ERROR",
            "reason": (
                "La información del capítulo no es precisa según el análisis "
                f"editorial de la afirmación número {idx + 1}."
            ),
            "source_url": "https://example.com/report",
            "suggestion": "Revisar la afirmación contra la fuente citada.",
        }
        for idx in range(n_claims)
    ]
    first_json = json.dumps(
        {
            "status": "FAIL",
            "claims_checked": n_claims,
            "issues": issues,
            "corrections": [],
            "unsupported_claims": [],
            "supported_claims": 0,
            "conflicting_claims": 0,
        }
    )

    provider = _two_pass_provider(first_json, "DEFENDIBLE")
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    # Reloj simulado: cada lectura avanza 30s (> FACT_CHECK_CONSISTENCY_TIMEOUT).
    clock = [0.0]

    def fake_perf_counter() -> float:
        clock[0] += 30.0
        return clock[0]

    monkeypatch.setattr(main.time, "perf_counter", fake_perf_counter)

    payload = _payload()
    payload["chapter_text"] = (
        "La variedad descrita en el capítulo no es tan escasa como se afirma, "
        "según el análisis editorial de cada una de sus partes."
    )
    out = execute(payload)

    # Lecturas del reloj: start(30) -> c1(60) c2(90) c3(120) quedan dentro del
    # budget (120s); c4 en adelante exceden -> SKIPPED_BUDGET_EXHAUSTED.
    verified = [i for i in out["issues"] if i.get("consistency_check") == "DOWNGRADED"]
    skipped = [
        i for i in out["issues"] if i.get("consistency_check") == "SKIPPED_BUDGET_EXHAUSTED"
    ]
    assert len(out["issues"]) == n_claims
    assert len(verified) == 3
    assert len(skipped) == n_claims - 3
    # Provider: 1 llamada (primera pasada) + SOLO las 3 verificaciones con presupuesto.
    assert provider.calls == 4
    for issue in skipped:
        assert issue["severity"] == "WARNING"
        assert "[Degradado a WARNING: presupuesto de verificación" in issue["reason"]
    # Todo degradado -> sin ERROR bloqueante -> gate PASS (fail-safe).
    assert all(i["severity"] != "ERROR" for i in out["issues"])
    assert out["quality_gate"] == "PASS"


def test_consistency_budget_constant_bounds_total_time() -> None:
    """El peor caso teórico SIEMPRE cae bajo timeout_seconds=180 del scheduler.

    Con budget=120s solo se inicia una verificación si quedan ≥20s
    (FACT_CHECK_CONSISTENCY_TIMEOUT), así que el techo agregado es
    budget + timeout = 140s < 180s, independientemente del número de claims.
    """
    import modules.fact_checker.main as main

    worst_case = (
        float(main.FACT_CHECK_CONSISTENCY_TOTAL_BUDGET)
        + float(main.FACT_CHECK_CONSISTENCY_TIMEOUT)
    )
    assert worst_case < 180.0
def test_build_prompt_includes_source_content_book69() -> None:
    """§17 #32-P2: el prompt expone el content real de cada fuente.

    Reproduce el caso book_69/chapter_id=503: una fuente cuyo content SÍ
    menciona la claim (Carl Johnson / GTA) debe aparecer en el texto final
    del prompt, para que el LLM pueda anclar la claim a esa source_url.
    """
    payload = {
        "chapter_text": (
            "Se detalla que Carl Johnson, también conocido como «C.J.», "
            "es el protagonista jugable de Grand Theft Auto: San Andreas."
        ),
        "sources": [
            {
                "url": "https://es.wikipedia.org/wiki/Carl_Johnson_(personaje)",
                "title": "Carl Johnson (personaje)",
                "source_type": "web_wikipedia",
                "content": (
                    "Carl Johnson, también conocido como «C.J.», es un "
                    "personaje ficticio y el protagonista jugable del "
                    "videojuego de 2004 Grand Theft Auto: San Andreas."
                ),
            },
        ],
        "target_language": "es",
    }
    prompt = _build_prompt(payload)
    assert "https://es.wikipedia.org/wiki/Carl_Johnson_(personaje)" in prompt
    assert "Contenido:" in prompt
    assert "Carl Johnson, también conocido como" in prompt


def test_build_prompt_omits_content_when_empty() -> None:
    """§17 #32-P2: una fuente sin content no debe añadir línea 'Contenido:'."""
    payload = {
        "chapter_text": "Afirmación genérica sin cifras concretas.",
        "sources": [
            {"url": "https://example.com/x", "title": "X", "source_type": "web"},
        ],
        "target_language": "es",
    }
    prompt = _build_prompt(payload)
    assert "https://example.com/x" in prompt
    assert "Contenido:" not in prompt


def test_book59_fabrication_claim_still_escalates_to_error() -> None:
    """REGRESIÓN §17 #20/book_59: la barrera de fabricación NO se toca.

    Una claim con firma fecha+nombre+cifra y SIN source_url debe seguir
    escalando a ERROR igual que antes del cambio de _build_prompt (única
    prueba que garantiza que la protección anti-fabricación estructural
    permanece intacta).
    """
    issue = {
        "claim": (
            "Adolf Eichmann estuvo a cargo de los campos de concentración "
            "en Palestina entre 1942 y 1948."
        ),
        "severity": "WARNING",
        "reason": "sin soporte en las fuentes permitidas",
        "source_url": None,
    }
    escalated = _escalate_fabrication_issue(issue)
    assert escalated["severity"] == "ERROR"
    assert "patrón de fabricación factual" in escalated["reason"]


def test_reanchor_matches_verbatim_source_content_book69() -> None:
    """§17 #32-P3/book_69 cap.1: re-anclaje determinista claim→fuente.

    Una claim con firma de fabricación (fecha+nombre+cifra) PERO cuyo texto
    contiene un n-grama de 8 palabras presente verbatim en el ``content`` de
    una fuente permitida NO debe escalar a ERROR: se re-ancla ``source_url``
    a esa fuente y se conserva la severity del LLM (el problema era un fallo
    de anclaje del LLM, no fabricación). Content sintético similar al real de
    Carl_Johnson_(personaje).
    """
    from modules.fact_checker import main as fc_main

    content = (
        "Carl Johnson, también conocido como «C.J.», es un personaje ficticio "
        "y el protagonista jugable del videojuego de 2004 Grand Theft Auto: "
        "San Andreas, la quinta entrega principal de la serie."
    )
    sources = [
        {
            "title": "Carl Johnson (personaje)",
            "url": "https://es.wikipedia.org/wiki/Carl_Johnson_(personaje)",
            "source_type": "web_wikipedia",
            "content": content,
        },
        # Fuente con content vacío: debe saltarse (no romper el matching).
        {
            "title": "Otra fuente",
            "url": "https://example.com/otra",
            "source_type": "web",
            "content": "",
        },
    ]
    issue = {
        "claim": (
            "Carl Johnson es un personaje ficticio y el protagonista jugable "
            "del videojuego de 2004 Grand Theft Auto San Andreas."
        ),
        "severity": "WARNING",
        "reason": "sin soporte en las fuentes permitidas",
        "source_url": None,
    }

    # El claim tiene <8 palabras de match posible solo si no hay solape: aquí
    # el n-grama "es un personaje ficticio y el protagonista" (8 palabras)
    # está verbatim en el content (case/espacios normalizados).
    assert (
        fc_main._find_reanchor_source(
            "es un personaje ficticio y el protagonista jugable", sources
        )
        == "https://es.wikipedia.org/wiki/Carl_Johnson_(personaje)"
    )
    # Claim de menos de 8 palabras: no se puede formar n-grama -> None.
    assert fc_main._find_reanchor_source("Carl Johnson es C.J.", sources) is None

    result = fc_main._escalate_fabrication_issue(issue, sources)
    assert (
        result["source_url"]
        == "https://es.wikipedia.org/wiki/Carl_Johnson_(personaje)"
    )
    # NO escalada: conserva la severity del LLM y el reason sin marcador.
    assert result["severity"] == "WARNING"
    assert "patrón de fabricación factual" not in result["reason"]


# ---------------------------------------------------------------------------
# §17 #35 F1 — campo error_type persistente en issues ERROR
# La clasificación se hace ANTES del filtro de claves "_" dentro de
# _apply_error_consistency_pass: error_type no lleva prefijo _, por lo que
# sobrevive al filtrado y queda expuesto a la orquestación (autopilot).
# ---------------------------------------------------------------------------
def test_book59_error_type_is_fabrication_structural() -> None:
    """REGRESIÓN §17 #35 F1/book_59: un ERROR estructural (firma de fabricación,
    escalado por _escalate_fabrication_issue) recibe error_type=«fabrication_structural»
    y ese campo sobrevive al filtro de claves internas ("_")."""
    from modules.fact_checker.main import _apply_error_consistency_pass

    issue = _escalate_fabrication_issue(
        {
            "claim": (
                "Adolf Eichmann estuvo a cargo de los campos de concentración "
                "en Palestina entre 1942 y 1948."
            ),
            "severity": "WARNING",
            "reason": "sin soporte en las fuentes permitidas",
            "source_url": None,
        }
    )
    assert issue["severity"] == "ERROR"
    # Igual que execute() (main.py): un ERROR que la escalada estructural subió
    # de WARNING se marca internamente antes de la pasada de consistencia.
    issue["_fabrication_structural"] = True

    out = _apply_error_consistency_pass([issue], context="")
    assert len(out) == 1
    assert out[0]["severity"] == "ERROR"
    # Campo persistente (sin prefijo _) y clasificado como fabricación estructural.
    assert out[0]["error_type"] == "fabrication_structural"
    # Ninguna clave interna "_" se filtra hacia el resultado.
    assert all(not k.startswith("_") for k in out[0])


def test_accuracy_partial_error_type_no_fabrication_signature() -> None:
    """§17 #35 F1: un ERROR NO estructural (sin firma de fabricación, origen LLM)
    recibe error_type=«accuracy_partial». Aunque la pasada de consistencia lo
    degrade a WARNING (sin source_url), la clasificación original del ERROR se
    conserva en error_type."""
    from modules.fact_checker.main import _apply_error_consistency_pass

    issue = {
        "claim": "El café Liberica es el más consumido del mundo.",
        "severity": "ERROR",
        "reason": "El LLM considera que la afirmación no es exacta.",
        "source_url": None,
        "_llm_original_error": True,
    }
    out = _apply_error_consistency_pass([issue], context="")
    assert len(out) == 1
    # Degradado por consistencia (fail-safe), pero la clasificación original persiste.
    assert out[0]["error_type"] == "accuracy_partial"
    assert all(not k.startswith("_") for k in out[0])


# ---------------------------------------------------------------------------
# §17 #47 — falsos positivos del bigrama de nombre propio compuesto
# Determinantes/artículos ("El", "La", "Los", ...) formaban bigramas falsos
# con cualquier palabra capitalizada siguiente (book_77/78/80/84).
# ---------------------------------------------------------------------------
def test_determinant_bigrams_are_not_fabrication_signature() -> None:
    """REGRESIÓN §17 #47: claims reales con bigrama artículo+nombre capitalizado
    (book_84 'El Imperio romántico...', book_77 'La Cancionero de Palacio...')
    NO deben disparar _has_fabrication_signature (sin fecha ni cifra, la rama 1
    no aplica; el bigrama era el único disparador)."""
    from modules.fact_checker import main as _fc

    assert (
        _fc._has_fabrication_signature(
            "El Imperio romántico, un subestilo dentro del Imperio, "
            "se destaca por su enfoque en los temas históricos y mitológicos."
        )
        is False
    )
    assert (
        _fc._has_fabrication_signature(
            "La Cancionero de Palacio fue compilada durante el reinado "
            "de los Reyes Católicos."
        )
        is False
    )


def test_real_proper_noun_pair_still_triggers_signature() -> None:
    """NO-regresión §17 #47: el caso real book_59 ('Adolf Eichmann...') sigue
    disparando la firma por bigrama de nombre propio compuesto (barrera dura
    intacta)."""
    from modules.fact_checker import main as _fc

    assert (
        _fc._has_fabrication_signature(
            "Adolf Eichmann estuvo a cargo de los campos de concentración "
            "en Palestina entre 1942 y 1948."
        )
        is True
    )

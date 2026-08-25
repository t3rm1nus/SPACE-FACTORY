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

    provider = _two_pass_provider(_liberica_llm_json(), "DEFENDIBLE")
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

    provider = _two_pass_provider(_liberica_llm_json(), "ERROR")
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
        provider = _two_pass_provider(_liberica_llm_json(), bad_second)
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

    - Escenario A (lo observado en producción: juicio subjetivo no replicable):
      la segunda pasada disiente -> la claim se degrada y la fase ya no agota
      reintentos.
    - Escenario B: si la segunda pasada confirma, el ERROR se mantiene (el fix
      no oculta errores reales).
    """
    import modules.fact_checker.main as main

    # Escenario A: disiente -> WARNING
    provider_a = _two_pass_provider(_liberica_llm_json(), "DEFENDIBLE")
    monkeypatch.setattr(main, "get_provider", lambda: provider_a)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")
    out_a = execute(_payload())
    assert out_a["issues"][0]["severity"] == "WARNING"
    assert out_a["issues"][0]["consistency_check"] == "DOWNGRADED"
    assert out_a["quality_gate"] == "PASS"

    # Escenario B: confirma -> ERROR intacto
    provider_b = _two_pass_provider(_liberica_llm_json(), "ERROR")
    monkeypatch.setattr(main, "get_provider", lambda: provider_b)
    out_b = execute(_payload())
    assert out_b["issues"][0]["severity"] == "ERROR"
    assert out_b["quality_gate"] == "FAIL"


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


def _liberica_llm_json() -> str:
    """Reproducción EXACTA del caso real book_65 cap.431 (task 888)."""
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
                    "source_url": None,
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

"""Tests unitarios del módulo editor (capability: edit_chapter)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from modules.editor.main import (
    _build_prompt,
    _detect_placeholder,
    _fallback_edit,
    _parse_llm_output,
    execute,
    health_check,
)


def _payload() -> dict:
    return {
        "chapter_text": (
            "El río Amazonas es el más caudaloso del mundo. "
            "La empresa Iberdrola inauguró su planta en 2019. "
            "El informe de la ONU señala un aumento del 45%."
        ),
        "style_guide": "formal, periodístico",
        "target_language": "es",
        "protected_terms": ["Amazonas", "Iberdrola", "ONU"],
        "facts": [
            "El Amazonas es el río más caudaloso del mundo.",
            "Iberdrola inauguró su planta en 2019.",
            "El informe de la ONU señala un aumento del 45%.",
        ],
        "references": ["informe de la ONU"],
    }


def test_health_check_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.editor.main as main

    monkeypatch.setattr(main, "get_provider", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = health_check()
    assert result["healthy"] is False
    assert "error" in result["dependencies"]


def test_build_prompt_includes_inputs_and_rules() -> None:
    prompt = _build_prompt(_payload())
    assert "Amazonas" in prompt
    assert "Iberdrola" in prompt
    assert "ONU" in prompt
    assert "NO cambiar hechos verificados" in prompt
    assert "NO eliminar referencias necesarias" in prompt
    assert "NO inventar información" in prompt
    assert "NO modificar nombres propios" in prompt
    assert "edited_text" in prompt
    assert "gramática" in prompt.lower() or "Gramática" in prompt


def test_parse_llm_output_happy_path() -> None:
    data = _parse_llm_output(
        '{"edited_text":"Texto editado.","editorial_notes":["nota"],"changes_summary":["cambio"]}'
    )
    assert data["edited_text"] == "Texto editado."
    assert data["editorial_notes"] == ["nota"]
    assert data["changes_summary"] == ["cambio"]


def test_parse_llm_output_fenced_json() -> None:
    text = '```json\n{"edited_text":"Texto","editorial_notes":[],"changes_summary":[]}\n```'
    data = _parse_llm_output(text)
    assert data["edited_text"] == "Texto"


def test_parse_llm_output_invalid_returns_empty() -> None:
    data = _parse_llm_output("esto no es json")
    assert data["edited_text"] == ""
    assert data["editorial_notes"] == []


def test_fallback_edit_preserves_text_and_never_invents() -> None:
    result = _fallback_edit(_payload())
    assert result["edited_text"] == _payload()["chapter_text"]
    assert result["editorial_notes"]
    assert "Sin cambios aplicados" in result["changes_summary"][0]


def test_fallback_edit_does_not_alter_facts_or_names() -> None:
    result = _fallback_edit(_payload())
    edited = result["edited_text"]
    # Nombres propios y hechos intactos en el fallback
    assert "Amazonas" in edited
    assert "Iberdrola" in edited
    assert "ONU" in edited
    assert "2019" in edited
    assert "45%" in edited


def test_execute_fallback_when_llm_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM falla, execute debe devolver el texto original sin cambios."""
    import modules.editor.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ollama not available")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())

    out = execute(_payload())
    assert out["edited_text"] == _payload()["chapter_text"]
    assert isinstance(out["editorial_notes"], list)
    assert isinstance(out["changes_summary"], list)


def test_execute_llm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM responde, se normaliza el JSON y la salida valida el esquema."""
    import modules.editor.main as main

    base_text = (
        "El río Amazonas es el más caudaloso del mundo. "
        "La empresa Iberdrola inauguró su planta en 2019. "
        "El informe de la ONU señala un aumento del 45%. "
    )
    llm_json = json.dumps(
        {
            "edited_text": base_text * 72,
            "editorial_notes": ["Se simplificó una redundancia."],
            "changes_summary": ["Normalización de puntuación.", "Eliminada repetición."],
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
    assert "Amazonas" in out["edited_text"]
    assert out["editorial_notes"] == ["Se simplificó una redundancia."]
    assert len(out["changes_summary"]) == 2


def test_execute_refusal_llm_triggers_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM devuelve un rechazo ('Lo siento, pero no puedo ayudar con
    eso.'), debe activarse _fallback_edit y el capítulo queda idéntico al
    original, sin la frase de rechazo."""
    import json as _json

    import modules.editor.main as main

    refusal_json = _json.dumps(
        {
            "edited_text": "Lo siento, pero no puedo ayudar con eso.",
            "editorial_notes": [],
            "changes_summary": [],
        }
    )

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            result = MagicMock()
            result.text = refusal_json
            result.raw_response = None
            result.input_tokens = 10
            result.output_tokens = 10
            return result

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())

    original = _payload()["chapter_text"]
    out = execute(_payload())
    assert "Lo siento" not in out["edited_text"]
    assert "no puedo ayudar" not in out["edited_text"]
    assert out["execution_mode"] == "fallback"
    # El capítulo se conserva sin cambios (fallback determinista).
    assert out["edited_text"].strip() == original.strip()


def test_detect_placeholders() -> None:
    assert _detect_placeholder("[TODO]") is True
    assert _detect_placeholder("[pendiente]") is True
    assert _detect_placeholder("{{TODO}}") is True
    assert _detect_placeholder("[ARPANET (web_wikipedia)]") is False
    assert _detect_placeholder("[ARPANET](https://es.wikipedia.org/wiki/ARPANET)") is False
    assert _detect_placeholder("texto normal") is False
    assert _detect_placeholder("pendiente") is False
    assert _detect_placeholder("TODO") is True
    assert _detect_placeholder("insert text") is False
    assert _detect_placeholder("[insert text]") is True


def test_short_output_triggers_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.editor.main as main

    short_text = "palabra " * 320
    llm_json = json.dumps(
        {
            "edited_text": short_text,
            "editorial_notes": [],
            "changes_summary": [],
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

    payload = _payload()
    payload["chapter_text"] = "palabra " * 2418

    out = execute(payload)
    assert out["quality_gate"] == "PASS"
    assert out["output_words"] == out["input_words"]
    assert out["execution_mode"] == "fallback"


def test_sufficient_output_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.editor.main as main

    long_text = "palabra " * 2000
    llm_json = json.dumps(
        {
            "edited_text": long_text,
            "editorial_notes": [],
            "changes_summary": [],
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

    payload = _payload()
    payload["chapter_text"] = "palabra " * 2418

    out = execute(payload)
    assert out["quality_gate"] == "PASS"
    assert out["output_words"] >= out["input_words"] * 0.75


def test_dynamic_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.editor.main as main

    captured: dict[str, Any] = {}

    class FakeResult:
        text = json.dumps(
            {
                "edited_text": "palabra " * 2000,
                "editorial_notes": [],
                "changes_summary": [],
            }
        )
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": ""}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, prompt: str, *args: Any, **kwargs: Any) -> FakeResult:
            captured["kwargs"] = kwargs
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 2418
    out = execute(payload)
    # Política acotada: int(2418 * 1.25) == 3022 (no >= 8000)
    assert captured["kwargs"]["max_tokens"] == int(2418 * 1.25)  # 3022
    assert out["quality_gate"] == "PASS"


def test_retry_max_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.editor.main as main

    call_count = 0

    class FakeResult:
        text = json.dumps(
            {
                "edited_text": "palabra " * 2000,
                "editorial_notes": [],
                "changes_summary": [],
            }
        )
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": ""}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            nonlocal call_count
            call_count += 1
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 2418
    out = execute(payload)
    assert call_count == 1
    assert out["quality_gate"] == "PASS"


def test_fallback_preserves_length(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.editor.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("provider down")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())

    payload = _payload()
    payload["chapter_text"] = "palabra " * 2418
    out = execute(payload)
    assert out["execution_mode"] == "fallback"
    assert out["output_words"] == out["input_words"]
    assert out["quality_gate"] == "PASS"


def test_editor_conserves_text_on_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import modules.editor.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("provider down")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())

    payload = _payload()
    out = execute(payload)
    assert out["edited_text"] == payload["chapter_text"]
    assert out["execution_mode"] == "fallback"


    from core.schemas import validate_output

    valid = validate_output("edit_chapter", out)
    assert valid["edited_text"] == out["edited_text"]


# ---------------------------------------------------------------------------
# Tests de detección de placeholders (precisión para texto editorial real)
# ---------------------------------------------------------------------------


def test_editor_placeholder_word_todo_todos_toda_is_false() -> None:
    """palabras normales como todo/todos/toda no son placeholder."""
    assert _detect_placeholder("Este texto habla de todo, todos y toda la gente.") is False


def test_editor_placeholder_markdown_headers_is_false() -> None:
    """Los encabezados Markdown no son placeholders."""
    assert _detect_placeholder("# El nacimiento de Internet\n## Introducción") is False


def test_editor_placeholder_bibliographic_reference_is_false() -> None:
    """Referencias bibliográficas con paréntesis no son placeholders."""
    assert _detect_placeholder("[ARPANET (web_wikipedia)]") is False


def test_editor_placeholder_todo_uppercase_is_true() -> None:
    """TODO en mayúsculas como palabra completa es un placeholder técnico."""
    assert _detect_placeholder("TODO: implementar esto") is True


def test_editor_placeholder_insert_text_here_is_true() -> None:
    """INSERT TEXT HERE es un placeholder técnico."""
    assert _detect_placeholder("INSERT TEXT HERE") is True


def test_editor_placeholder_lorem_ipsum_is_true() -> None:
    """Lorem ipsum es un placeholder técnico."""
    assert _detect_placeholder("Lorem ipsum dolor sit amet") is True


def test_editor_placeholder_texto_de_ejemplo_is_true() -> None:
    """texto de ejemplo es un placeholder técnico."""
    assert _detect_placeholder("Este es el texto de ejemplo.") is True


def test_editor_placeholder_double_braces_is_true() -> None:
    """Marcadores {{variable}} son placeholders técnicos."""
    assert _detect_placeholder("{{chapter_content}}") is True


def test_editor_placeholder_historical_text_is_false() -> None:
    """Texto histórico real con palabras potencialmente problemáticas no es placeholder."""
    text = (
        "El término 'independientemente' se usa como sinónimo de 'por separado'. "
        "La historia menciona que todo el mundo estaba pendiente de los resultados. "
        "Este texto usa toda la biblioteca disponible."
    )
    assert _detect_placeholder(text) is False


# ---------------------------------------------------------------------------
# Tests de max_tokens dinámico y robustez ante respuestas LLM
# ---------------------------------------------------------------------------


def _fake_editor_provider(captured: dict, edited_words: int, raw_text: str | None = None):
    """Builds a FakeProvider pairs to capture kwargs and return a fixed edited_text."""
    import modules.editor.main as main
    import json as _json
    from unittest.mock import patch as _patch

    if raw_text is None:
        raw_text = _json.dumps(
            {
                "edited_text": "palabra " * edited_words,
                "editorial_notes": [],
                "changes_summary": [],
            }
        )

    class FakeResult:
        text = raw_text
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": raw_text}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, prompt: str, *args, **kwargs):
            captured["kwargs"] = kwargs
            captured["prompt"] = prompt
            return FakeResult()

    return main, FakeProvider()


def test_max_tokens_1700_words_uses_bounded_policy(monkeypatch) -> None:
    """1700 palabras deben derivar en max_tokens acotado (2125), no 8000."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=1700)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 1700
    execute(payload)
    mt = captured["kwargs"]["max_tokens"]
    assert mt == int(1700 * 1.25)  # 2125
    assert mt != 4000
    assert captured["kwargs"].get("num_predict", mt) == mt


def test_max_tokens_3000_words_3750(monkeypatch) -> None:
    """3000 palabras deben derivar en max_tokens == 3750 (int(3000*1.25))."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=3000)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 3000
    execute(payload)
    assert captured["kwargs"]["max_tokens"] == int(3000 * 1.25)  # 3750


def test_max_tokens_capped_at_16000(monkeypatch) -> None:
    """Textos muy largos deben limitar max_tokens a 16000 (nunca más)."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=20000)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 20000
    execute(payload)
    assert captured["kwargs"]["max_tokens"] == 16000


def test_truncated_json_triggers_fallback_and_no_crash(monkeypatch) -> None:
    """JSON truncado debe activar fallback sin romper el módulo."""
    import modules.editor.main as main

    captured: dict = {}
    truncated = '{"edited_text": "Este es un cap"'
    main_mod, provider = _fake_editor_provider(captured, edited_words=0, raw_text=truncated)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 1700
    out = execute(payload)
    assert out["execution_mode"] == "fallback"
    assert out["output_words"] == out["input_words"]  # conserva el original
    assert out["quality_gate"] == "PASS"


def test_extremely_short_output_not_silently_accepted(monkeypatch) -> None:
    """Una respuesta extremadamente corta no debe aceptarse como edición válida."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=100)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 3000
    out = execute(payload)
    # Fallback conserva el texto original completo, no el resumen de 100 palabras
    assert out["execution_mode"] == "fallback"
    assert out["output_words"] == out["input_words"] == 3000


def test_word_count_reaches_provider_and_prompt(monkeypatch) -> None:
    """El conteo de palabras / target llega correctamente al prompt y al retorno."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=2418)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 2418
    out = execute(payload)
    assert out["execution_mode"] == "real"
    assert out["input_words"] == 2418
    assert f"2418" in captured["prompt"]
    assert "No cortes edited_text" in captured["prompt"]
    assert "Nunca finalices prematuramente" in captured["prompt"]
# ---------------------------------------------------------------------------
# Tests del fix: límites max_tokens deterministas + timeout/retry acotados
# ---------------------------------------------------------------------------


def test_max_tokens_999_words_1248(monkeypatch) -> None:
    """Un capítulo de 999 palabras debe derivar en max_tokens == 1248."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=999)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 999
    execute(payload)
    assert captured["kwargs"]["max_tokens"] == int(999 * 1.25)  # 1248


def test_max_tokens_1500_words_1875(monkeypatch) -> None:
    """Un capítulo de 1500 palabras debe derivar en max_tokens == 1875."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=1500)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 1500
    execute(payload)
    assert captured["kwargs"]["max_tokens"] == int(1500 * 1.25)  # 1875


def test_max_tokens_never_below_1024(monkeypatch) -> None:
    """max_tokens nunca debe bajar del suelo MIN_EDITOR_TOKENS (1024)."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=1024)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "frase corta. " * 10  # muy pocas palabras
    execute(payload)
    assert captured["kwargs"]["max_tokens"] == 1024


def test_max_tokens_never_above_16000(monkeypatch) -> None:
    """max_tokens nunca debe superar MAX_EDITOR_TOKENS (16000)."""
    import modules.editor.main as main

    captured: dict = {}
    main_mod, provider = _fake_editor_provider(captured, edited_words=50000)
    monkeypatch.setattr(main, "get_provider", lambda: provider)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    payload = _payload()
    payload["chapter_text"] = "palabra " * 50000
    execute(payload)
    assert captured["kwargs"]["max_tokens"] == 16000


def test_execute_bounds_provider_timeout_and_retries(monkeypatch) -> None:
    """Antes de generate(), el provider local recibe timeout=60 y max_retries=1."""
    import modules.editor.main as main
def test_provider_timeout_triggers_fallback_preserving_original(monkeypatch) -> None:
    """Un timeout del proveedor activa el fallback y conserva el original íntegro."""
    import modules.editor.main as main
    from core.providers.base import LLMTimeoutError

    original = _payload()["chapter_text"]

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"
        timeout = 120
        max_retries = 3

        def generate(self, *args, **kwargs):
            raise LLMTimeoutError("timeout del proveedor en test")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["execution_mode"] == "fallback"
    assert out["edited_text"] == original  # texto original conservado integro
    assert "Sin cambios aplicados" in out["changes_summary"][0]  # no inventa contenido


def test_generic_exception_triggers_fallback_execution_mode(monkeypatch) -> None:
    """Cualquier excepción del proveedor se marca como fallback (no PASS falso)."""
    import modules.editor.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"
        timeout = 120
        max_retries = 3

        def generate(self, *args, **kwargs):
            raise RuntimeError("sistema LLM no disponible")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    out = execute(_payload())
    assert out["execution_mode"] == "fallback"
    assert out["edited_text"] == _payload()["chapter_text"]


def test_does_not_mutate_global_provider_defaults(monkeypatch) -> None:
    """El ajuste de timeout/retries es local: una instancia nueva conserva los
    valores por defecto globales (no se altera la configuración global)."""
    import modules.editor.main as main

    produced = []

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"
        timeout = 120
        max_retries = 3

        def generate(self, *args, **kwargs):
            llm_json = json.dumps(
                {
                    "edited_text": "palabra " * 1800,
                    "editorial_notes": [],
                    "changes_summary": [],
                }
            )

            class R:
                text = llm_json
                provider = "ollama"
                model = "llama3.1"
                input_tokens = 1
                output_tokens = 1
                cost = 0.0
                raw_response = {"model": "llama3.1", "response": llm_json}

            return R()

    def factory():
        produced.append(FakeProvider())
        return produced[-1]

    monkeypatch.setattr(main, "get_provider", factory)
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    execute(_payload())
    # la instancia usada por el editor quedó acotada localmente
    assert produced[0].timeout == 60
    assert produced[0].max_retries == 1
    # pero una instancia nueva (como devolvería registry.get()) conserva los defaults
    fresh = FakeProvider()
    assert fresh.timeout == 120
    assert fresh.max_retries == 3

    consumed: dict = {}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"
        timeout = 120
        max_retries = 3

        def generate(self, *args, **kwargs):
            consumed["timeout"] = self.timeout
            consumed["max_retries"] = self.max_retries
            llm_json = json.dumps(
                {
                    "edited_text": "palabra " * 1800,
                    "editorial_notes": [],
                    "changes_summary": [],
                }
            )

            class R:
                text = llm_json
                provider = "ollama"
                model = "llama3.1"
                input_tokens = 1
                output_tokens = 1
                cost = 0.0
                raw_response = {"model": "llama3.1", "response": llm_json}

            return R()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")

    execute(_payload())
    assert consumed["timeout"] == 60
    assert consumed["max_retries"] == 1


# ---------------------------------------------------------------------------
# §17 #30 (P2, book_72 cap.4) — mismo detector de refusal acotado que writer
# ---------------------------------------------------------------------------
def test_editor_refusal_acusacion_entendido_al_inicio_de_parrafo():
    """La copia del detector del editor también detecta el acuse 'Entendido.
    No reproduciré...' a inicio de párrafo (gap real book_72 cap.4)."""
    from modules.editor.main import _detect_refusal

    texto = (
        "Entendido. No generaré contenido duplicado ni reproduciré texto\n"
        "existente. Solo proporcionaré los párrafos nuevos."
    )
    assert _detect_refusal(texto)


def test_editor_refusal_sin_negacion_o_a_mitad_no_detecta():
    """'Entendido' sin negación de meta-instrucción, o en mitad de párrafo,
    NO se detecta (misma acotación que chapter_writer)."""
    from modules.editor.main import _detect_refusal

    assert not _detect_refusal(
        "Entendido el contexto histórico, la era de los 8 bits marcó un antes "
        "y un después en la industria del videojuego."
    )
    assert not _detect_refusal(
        "Los arcades dominaron la década. Entendido este fenómeno, no hay "
        "manera de generar la crónica sin citar el salto doméstico."
    )

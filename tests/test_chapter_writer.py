"""Tests unitarios del módulo chapter_writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from modules.chapter_writer.main import (
    _build_prompt,
    _build_prompt_en,
    _build_prompt_es,
    _build_section_continuation_prompt,
    _build_subsections_map,
    _canonicalize_headings,
    _choose_target_section,
    _contains_markdown_heading,
    _detect_duplicate_sections,
    _detect_placeholder,
    _detect_structural_anomalies,
    _detect_unexpected_sections,
    _extract_heading_structure,
    _fallback_chapter,
    _get_section_text,
    _get_section_word_counts,
    _has_strong_text_overlap,
    _insert_into_section,
    _required_min_words,
    _write_artifacts,
    execute,
    health_check,
)
from core.schemas import ChapterWritePayload


def _payload() -> dict:
    return {
        "book_metadata": {"title": "Libro", "book_id": 1},
        "chapter_outline": {
            "number": 1,
            "title": "Introducción",
            "objective": "Presentar el tema",
            "sections": [
                {"heading": "Antecedentes", "objective": "Contexto histórico"},
            ],
        },
        "research": "Datos verificados del tema.",
        "sources": [
            {"url": "https://example.com/1", "title": "Fuente 1", "source_type": "web"},
            {"url": "https://example.com/2", "title": "Fuente 2", "source_type": "web"},
        ],
        "previous_chapter_summaries": ["Resumen previo."],
        "target_word_count": 1500,
        "style_guide": "formal",
    }


def test_health_check_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """health_check debe sobrevivir si el provider falla al instanciar."""
    import modules.chapter_writer.main as main

    monkeypatch.setattr(main, "get_provider", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    result = health_check()
    assert result["healthy"] is False
    assert "error" in result["dependencies"]


def test_build_prompt_includes_inputs() -> None:
    prompt = _build_prompt(_payload())
    assert "Libro" in prompt
    assert "Introducción" in prompt
    assert "Fuente 1" in prompt
    assert "Datos verificados del tema." in prompt
    assert "formal" in prompt


def test_build_prompt_includes_anti_repetition_rules_es() -> None:
    """El prompt inicial en español debe incluir instrucciones anti-repetición."""
    prompt = _build_prompt(_payload(), language="es")
    assert "No repitas información ya explicada en secciones anteriores" in prompt
    assert "cada sección debe ser una unidad con contenido propio" in prompt
    assert "No conviertas la conclusión en un resumen detallado" in prompt
    assert "no reexplicar el cuerpo" in prompt
    assert "Evita especialmente repetir" in prompt


def test_build_prompt_includes_anti_repetition_rules_en() -> None:
    """El prompt inicial en inglés debe incluir instrucciones anti-repetición."""
    prompt = _build_prompt(_payload(), language="en")
    assert "do not repeat information already explained in previous sections" in prompt
    assert "self-contained unit" in prompt
    assert "Do not turn the conclusion into a detailed summary" in prompt
    assert "The conclusion must not reproduce the body of the chapter" in prompt


def test_build_prompt_en_excludes_es_skeleton() -> None:
    """_build_prompt_en no debe contener las frases del esqueleto/pie es español; sí sus equivalentes EN."""
    prompt_en = _build_prompt_en(_payload())
    for frase in (
        "El objetivo de longitud es un REQUISITO",
        "No añadas relleno",
        "Devuelve únicamente el capítulo Markdown",
    ):
        assert frase not in prompt_en
    assert "The length objective is a REQUIREMENT" in prompt_en
    assert "Do not add filler" in prompt_en
    assert "Return ONLY the final Markdown chapter" in prompt_en


def test_build_prompt_es_keeps_es_skeleton() -> None:
    """_build_prompt_es conserva el esqueleto/pie en español."""
    prompt_es = _build_prompt_es(_payload())
    assert "El objetivo de longitud es un REQUISITO" in prompt_es
    assert "No añadas relleno" in prompt_es
    assert "Devuelve únicamente el capítulo Markdown" in prompt_es


def test_build_prompt_includes_conclusion_synthesis_rule() -> None:
    """La conclusión debe tener una instrucción específica de síntesis y no repetición."""
    prompt_es = _build_prompt(_payload(), language="es")
    prompt_en = _build_prompt(_payload(), language="en")
    assert "conclusión" in prompt_es and "sintetizar la tesis central" in prompt_es
    assert "synthesize the central thesis in a concise closing perspective" in prompt_en


def test_continuation_prompt_includes_anti_repeat_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    """El prompt de continuación dirigido prohíbe repetir el contenido existente."""
    import modules.chapter_writer.main as main

    outline_headings = "\n".join(
        f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"]
    )
    short_md = outline_headings + "\n\n" + "word " * 200
    expansion = "newcontent " * 1600
    responses = [_fake_result(short_md), _fake_result(expansion)]
    call_count = [0]
    captured_prompts = []

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, prompt: str, *args: Any, **kwargs: Any) -> Any:
            captured_prompts.append(prompt)
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    cont_prompt = [p for p in captured_prompts if "contenido nuevo" in p.lower() or "new content" in p.lower()]
    assert cont_prompt, "No se generó ningún prompt de continuación dirigida"
    assert "NO" in cont_prompt[0] and "reproduzcas" in cont_prompt[0]


def test_fallback_chapter_shape() -> None:
    result = _fallback_chapter(_payload())
    assert result["word_count"] == 1500
    assert "Introducción" in result["chapter_md"]
    assert "Conclusión" in result["chapter_md"]
    assert "https://example.com/1" in result["chapter_md"]
    assert result["sources_used"] == ["https://example.com/1", "https://example.com/2"]


def test_fallback_chapter_no_sources() -> None:
    payload = _payload()
    payload["sources"] = []
    result = _fallback_chapter(payload)
    assert "(Sin fuentes proporcionadas)" in result["chapter_md"]
    assert result["sources_used"] == []


def test_write_artifacts_creates_files(tmp_path: str) -> None:
    import os

    base = str(tmp_path)
    md = "# Capítulo\n\nContenido."
    meta = {"key": "value"}
    md_path = _write_artifacts(1, 1, md, meta)
    assert os.path.exists(md_path)
    assert os.path.exists(os.path.join(os.path.dirname(md_path), "metadata.json"))
    with open(md_path, "r", encoding="utf-8") as f:
        assert f.read() == md
    with open(os.path.join(os.path.dirname(md_path), "metadata.json"), "r", encoding="utf-8") as f:
        assert json.load(f) == meta


def test_execute_fallback_when_llm_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM falla, execute debe devolver un draft fallback válido."""
    import modules.chapter_writer.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ollama no disponible")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["word_count"] >= 1
    assert out["chapter_md_path"].startswith("data/artifacts/")
    assert out["chapter_md_path"].endswith("chapter.md")
    assert out["provider"] == "ollama"
    assert out["cost"] == 0.0
    assert out["tokens_input"] == 0
    assert out["tokens_output"] == 0


def test_execute_llm_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM responde, execute guarda el draft y devuelve metadatos."""
    import modules.chapter_writer.main as main

    md = "# Introducción\n\nContenido de ejemplo.\n\n## Fuentes utilizadas\n- https://example.com/1"

    class FakeResult:
        text = md
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": md}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["word_count"] == len(md.split())
    assert out["chapter_md_path"].endswith("chapter.md")
    assert out["provider"] == "ollama"
    assert out["tokens_input"] == 10
    assert out["tokens_output"] == 20


def test_execute_uses_sources_from_payload() -> None:
    """sources_used debe provenir de las URLs del payload, sin inventar fuentes."""
    payload = _payload()
    payload["sources"] = [{"url": "https://only-this.com"}]
    out = execute(payload)
    assert out["sources_used"] == ["https://only-this.com"]


def _payload_en() -> dict:
    return {
        "book_metadata": {"title": "Book", "book_id": 1},
        "chapter_outline": {
            "number": 1,
            "title": "Introduction",
            "objective": "Introduce the topic",
            "sections": [
                {"heading": "Background", "objective": "Historical context"},
            ],
        },
        "research": "Verified topic data.",
        "sources": [
            {"url": "https://example.com/1", "title": "Source 1", "source_type": "web"},
        ],
        "previous_chapter_summaries": ["Previous summary."],
        "target_word_count": 1500,
        "style_guide": "formal",
    }


def test_build_prompt_includes_inputs_en() -> None:
    prompt = _build_prompt(_payload_en(), language="en")
    assert "Book" in prompt
    assert "Introduction" in prompt
    assert "Source 1" in prompt
    assert "Verified topic data." in prompt
    assert "formal" in prompt
    assert "English" in prompt
    # Comportamiento actual (fix §17 #13/#19): el prompt NO instruye generar la
    # sección de fuentes; el sistema la añade automáticamente.
    assert "Do not include any sources, references, or bibliography section" in prompt


def test_build_prompt_en_editorial_rules() -> None:
    """English prompt must enforce natural prose, no literal translation, and preserve facts/structure/tone."""
    prompt = _build_prompt(_payload_en(), language="en")
    assert "Write as if the text was originally authored in English" in prompt
    assert "Avoid literal translation patterns from Spanish" in prompt
    assert "Preserve all provided facts, structure, references, citations, meaning, tone, and approximate length" in prompt
    assert "Do not invent information, statistics, quotes, sources, or people" in prompt
    assert "Adapt idioms and culturally specific expressions into natural English equivalents when needed" in prompt
    assert "Keep the tone consistent with the book metadata and style guide" in prompt
    # El prompt YA NO instruye generar '## Sources used' (el sistema la añade).
    assert "Include a '## Sources used' section at the end, listing only valid provided URLs" not in prompt
    assert "Do not include any sources, references, or bibliography section" in prompt


def test_build_prompt_no_longer_instructs_to_add_sources_section() -> None:
    """Fix #17 (document_builder sources): el prompt YA NO debe ordenar crear
    '## Fuentes utilizadas' / '## Sources used', y SÍ debe indicar que el sistema
    añade esa sección automáticamente (instrucción única, no contradictoria)."""
    es = _build_prompt(_payload(), language="es")
    en = _build_prompt(_payload(), language="en")

    # NO ordena generar la sección de fuentes (ni ES ni EN).
    assert "## Fuentes utilizadas" not in es
    assert "## Sources used" not in en
    assert "Incluye al final una sección" not in es
    assert "Include a '## Sources used' section" not in en

    # SÍ contiene la nueva instrucción: no incluir la sección; el sistema la añade.
    assert "No incluyas ninguna sección de fuentes, referencias ni bibliografía" in es
    assert "el sistema las añade automáticamente" in es
    assert "Do not include any sources, references, or bibliography section" in en
    assert "the system adds them automatically" in en

    # La regla de no inventar información/citas/fuentes se mantiene intacta.
    assert "No inventes información, estadísticas, citas ni fuentes" in es
    assert "Do not invent information, statistics, quotes, sources, or people" in en


def test_fallback_chapter_no_sources_en() -> None:
    payload = _payload_en()
    payload["sources"] = []
    result = _fallback_chapter(payload, language="en")
    assert "(No sources provided)" in result["chapter_md"]
    assert result["sources_used"] == []


def test_execute_fallback_when_llm_fails_en(monkeypatch: pytest.MonkeyPatch) -> None:
    """If LLM fails, execute must return a valid English fallback draft."""
    import modules.chapter_writer.main as main

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("ollama not available")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload_en(), capability="write_chapter_en")
    assert out["word_count"] >= 1
    assert out["chapter_md_path"].startswith("data/artifacts/")
    assert out["chapter_md_path"].endswith("chapter.md")
    assert out["provider"] == "ollama"
    assert out["cost"] == 0.0
    assert out["tokens_input"] == 0
    assert out["tokens_output"] == 0


def test_execute_llm_success_en(monkeypatch: pytest.MonkeyPatch) -> None:
    """If LLM responds, execute saves the draft and returns metadata."""
    import modules.chapter_writer.main as main

    md = "# Introduction\n\nExample content.\n\n## Sources used\n- https://example.com/1"

    class FakeResult:
        text = md
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": md}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload_en(), capability="write_chapter_en")
    assert out["word_count"] == len(md.split())
    assert out["chapter_md_path"].endswith("chapter.md")
    assert out["provider"] == "ollama"
    assert out["tokens_input"] == 10
    assert out["tokens_output"] == 20


def test_chapter_en_structure_checks() -> None:
    """English fallback must contain introduction, sections, conclusion and sources section."""
    payload = _payload_en()
    payload["sources"] = [
        {"url": "https://a.com"},
        {"url": "https://b.com"},
    ]
    payload["chapter_outline"]["sections"] = [
        {"heading": "Background", "objective": "Context"},
        {"heading": "Analysis", "objective": "Deep dive"},
    ]
    result = _fallback_chapter(payload, language="en")
    md = result["chapter_md"]
    assert md.startswith("# Introduction")
    assert "## Background" in md
    assert "## Analysis" in md
    assert "## Conclusion" in md
    assert "## Sources used" in md
    assert "https://a.com" in md
    assert "https://b.com" in md
    assert result["sources_used"] == ["https://a.com", "https://b.com"]


def test_execute_real_text_without_placeholders_short_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un capítulo real corto sin placeholders debe fallar solo por longitud."""
    import modules.chapter_writer.main as main

    md = """
# El nacimiento de Internet

## Introducción

El siglo XX fue un período de revolución tecnológica que marcó el surgimiento y la evolución de muchas de las tecnologías modernas. En este contexto, el nacimiento de Internet es un hito fundamental que ha transformado la forma en que nos comunicamos, trabajamos y nos relacionamos entre nosotros. Este capítulo explora los orígenes del Internet, desde sus inicios como una red militar hasta su evolución hacia la plataforma global que conocemos hoy.
"""

    class FakeResult:
        text = md
        provider = "ollama"
        model = "llama3.1"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "llama3.1", "response": md}

    class FakeProvider:
        name = "ollama"
        model = "llama3.1"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "llama3.1")
    monkeypatch.setattr(
        main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md"
    )

    out = execute(_payload())
    assert out["word_count"] < 1500
    assert out["quality_gate"] == "FAIL"
    assert any("menos de 1500 palabras" in e for e in out["quality_errors"])
    assert all("contiene texto placeholder" not in e for e in out["quality_errors"])
    assert "text" in out["metadata"]

def test_target_word_count_reaches_provider_as_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """target_word_count=3000 debe derivar en max_tokens >= 8000."""
    import modules.chapter_writer.main as main

    captured: dict = {}

    long_md = "# Capítulo\n\n## Antecedentes\n\n" + "palabra " * 2000

    class FakeResult:
        text = long_md
        provider = "ollama"
        model = "qwen-agent:latest"
        input_tokens = 10
        output_tokens = 2000
        cost = 0.0
        raw_response = {"model": "qwen-agent:latest", "response": long_md}

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            captured["kwargs"] = kwargs
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(
        main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md"
    )

    payload = _payload()
    payload["target_word_count"] = 3000
    out = execute(payload)
    assert captured["kwargs"]["max_tokens"] >= 8000
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"


def test_no_accidentally_low_max_tokens_for_large_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Debe existir un margen seguro; nunca max_tokens == 4000 para target 3000."""
    import modules.chapter_writer.main as main

    captured: dict = {}

    class FakeResult:
        text = "# Capítulo\n\n" + "palabra " * 2000
        provider = "ollama"
        model = "qwen-agent:latest"
        input_tokens = 10
        output_tokens = 2000
        cost = 0.0
        raw_response = {"model": "qwen-agent:latest", "response": text}

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            captured["kwargs"] = kwargs
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(
        main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md"
    )

    payload = _payload()
    payload["target_word_count"] = 3000
    execute(payload)
    assert captured["kwargs"]["max_tokens"] != 4000
    assert captured["kwargs"]["max_tokens"] >= 8000


def test_execute_real_long_text_passes_quality_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una respuesta real de >1500 palabras sin placeholders debe pasar."""
    import modules.chapter_writer.main as main

    long_md = "# Capítulo\n\n## Antecedentes\n\n" + "palabra " * 2000

    class FakeResult:
        text = long_md
        provider = "ollama"
        model = "qwen-agent:latest"
        input_tokens = 10
        output_tokens = 2000
        cost = 0.0
        raw_response = {"model": "qwen-agent:latest", "response": long_md}

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(
        main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md"
    )

    out = execute(_payload())
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert out["execution_mode"] == "real"


def test_prompt_includes_explicit_length_instructions() -> None:
    """El prompt debe exigir explícitamente la longitud objetivo y mínimo 1800 palabras."""
    payload = _payload()
    payload["target_word_count"] = 3000
    prompt = _build_prompt(payload)
    assert "aproximadamente 3000 palabras" in prompt
    assert "1800 palabras" in prompt
    assert "Desarrolla TODAS las secciones" in prompt
    assert "No termines prematuramente" in prompt


def test_placeholder_false_for_normal_long_text() -> None:
    """placeholder_detected=False para texto normal largo."""
    long_md = "# Capítulo\n\n" + "palabra " * 2000
    assert _detect_placeholder(long_md) is False


# ---------------------------------------------------------------------------
# Tests de detección de placeholders (precisión para texto editorial real)
# ---------------------------------------------------------------------------


def _load_real_chapter_checkpoint() -> str | None:
    """Carga el texto del capítulo real desde el checkpoint v0014."""
    checkpoint_path = Path("data/checkpoints/1001/book/draft/v0014.json")
    if not checkpoint_path.exists():
        return None
    import json
    with open(checkpoint_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["payload"]["result"]["metadata"]["text"]


def test_placeholder_real_chapter_from_checkpoint_is_false() -> None:
    """El capítulo real del checkpoint v0014 no debe marcarse como placeholder.

    Fallo previo: el patrón ``pendiente`` coincidía como subcadena dentro de
    ``independientemente`` en el texto editorial real.
    """
    text = _load_real_chapter_checkpoint()
    if text is None:
        pytest.skip("Checkpoint v0014 no disponible")
    assert _detect_placeholder(text) is False


def test_placeholder_word_todo_todos_toda_is_false() -> None:
    """palabras normales como todo/todos/toda no son placeholder."""
    assert _detect_placeholder("Este texto habla de todo, todos y toda la gente.") is False


def test_placeholder_markdown_headers_is_false() -> None:
    """Los encabezados Markdown no son placeholders."""
    assert _detect_placeholder("# El nacimiento de Internet\n## Introducción") is False


def test_placeholder_bibliographic_reference_is_false() -> None:
    """Referencias bibliográficas con paréntesis no son placeholders."""
    assert _detect_placeholder("[ARPANET (web_wikipedia)]") is False


def test_placeholder_todo_uppercase_is_true() -> None:
    """TODO en mayúsculas como palabra completa es un placeholder técnico."""
    assert _detect_placeholder("TODO: implementar esto") is True


def test_placeholder_insert_text_here_is_true() -> None:
    """INSERT TEXT HERE es un placeholder técnico."""
    assert _detect_placeholder("INSERT TEXT HERE") is True


def test_placeholder_lorem_ipsum_is_true() -> None:
    """Lorem ipsum es un placeholder técnico."""
    assert _detect_placeholder("Lorem ipsum dolor sit amet") is True


def test_placeholder_texto_de_ejemplo_is_true() -> None:
    """texto de ejemplo es un placeholder técnico."""
    assert _detect_placeholder("Este es el texto de ejemplo.") is True


def test_placeholder_double_braces_is_true() -> None:
    """Marcadores {{variable}} son placeholders técnicos."""
    assert _detect_placeholder("{{chapter_content}}") is True


def test_placeholder_historical_text_is_false() -> None:
    """Texto histórico real con palabras potencialmente problemáticas no es placeholder."""
    text = (
        "El término 'independientemente' se usa como sinónimo de 'por separado'. "
        "La historia menciona que todo el mundo estaba pendiente de los resultados. "
        "Este texto usa toda la biblioteca disponible."
    )
    assert _detect_placeholder(text) is False


# ---------------------------------------------------------------------------
# Tests de continuación adaptativa (máx. 3 intentos: inicial + 2 continuaciones)
# ---------------------------------------------------------------------------

def _fake_result(text: str) -> Any:
    class _Result:
        pass

    _Result.text = text
    _Result.raw_response = {"model": "qwen-agent:latest", "context": {}}
    _Result.input_tokens = 10
    _Result.output_tokens = 20
    _Result.provider = "ollama"
    _Result.model = "qwen-agent:latest"
    _Result.cost = 0.0
    return _Result()


def test_continuation_first_attempt_reaches_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generación inicial corta + primera continuación dirigida suficiente -> PASS en 2 llamadas."""
    import modules.chapter_writer.main as main

    outline_headings = "\n".join(
        f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"]
    )
    short_md = outline_headings + "\n\n" + "word " * 100
    expansion = "newcontent " * 1600  # solo texto nuevo, sin headings
    responses = [_fake_result(short_md), _fake_result(expansion)]
    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert call_count[0] == 2
    text = out["metadata"]["text"]
    assert "word " * 100 in text
    assert len(text.split()) >= 1500


def test_continuation_two_attempts_reach_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    """Generación inicial corta + 2 continuaciones -> PASS en 3 llamadas."""
    import modules.chapter_writer.main as main

    outline_headings = "\n".join(
        f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"]
    )
    short_md = outline_headings + "\n\n" + "word " * 100
    first_expansion = "nextcontent " * 800
    second_expansion = "newercontent " * 1000
    responses = [_fake_result(short_md), _fake_result(first_expansion), _fake_result(second_expansion)]
    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert call_count[0] == 3
    text = out["metadata"]["text"]
    assert "word " * 100 in text
    assert "nextcontent " * 800 in text
    assert len(text.split()) >= 1500


def test_continuation_stops_on_no_significant_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si la continuación añade <5 palabras, se detiene sin reintentar."""
    import modules.chapter_writer.main as main

    outline_headings = "\n".join(
        f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"]
    )
    short_md = outline_headings + "\n\n" + "word " * 100
    tiny_md = "xyz"
    responses = [_fake_result(short_md), _fake_result(tiny_md)]
    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["word_count"] < 1500
    assert out["quality_gate"] == "FAIL"
    assert call_count[0] == 2


def test_continuation_target_words_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    """El prompt de continuación dirigido informa la sección objetivo y cantidad."""
    import modules.chapter_writer.main as main

    outline_headings = "\n".join(
        f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"]
    )
    short_md = outline_headings + "\n\n" + "word " * 100
    expansion = "newcontent " * 1600
    responses = [_fake_result(short_md), _fake_result(expansion)]
    call_count = [0]
    captured_prompts = []

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, prompt: str, *args: Any, **kwargs: Any) -> Any:
            captured_prompts.append(prompt)
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert call_count[0] == 2
    cont_prompt = [p for p in captured_prompts if "contenido nuevo" in p.lower() or "new content" in p.lower()]
    assert cont_prompt
    # El prompt dirigido nombra la sección objetivo que se está ampliando.
    target_section = _choose_target_section(
        _get_section_word_counts(short_md, _payload()),
        max(250, round(3000 / 3)),
    )
    assert target_section
    assert target_section in cont_prompt[0]
    # Se informa el número de palabras nuevas pedidas.
    assert "palabras NUEVAS" in cont_prompt[0] or "NEW words" in cont_prompt[0]


# ---------------------------------------------------------------------------
# Anti-redundancia: control de outline y duplicados (FASE 7.9C)
# ---------------------------------------------------------------------------


def test_unexpected_sections_known_heading_not_flagged() -> None:
    """Un heading del outline no se marca como inesperado."""
    validated = _payload()
    md = "# Título\n\n## Antecedentes\n\nTexto de la sección."
    assert _detect_unexpected_sections(md, validated) == []


def test_unexpected_sections_unknown_heading_detected() -> None:
    """La detección ignora diferencias en acentos/mayúsculas y marca lo de fuera."""
    validated = _payload()
    md = (
        "# Título\n\n"
        "## Antecedentes\n\nTexto.\n\n"
        "## El FUTURO de Internet\n\nTexto extra."
    )
    out = _detect_unexpected_sections(md, validated)
    assert out == ["El FUTURO de Internet"]


def test_strong_overlap_different_paragraphs_false() -> None:
    """Párrafos que solo hablan de temas distintos NO se consideran duplicados."""
    existing = (
        "La historia de internet comienza con ARPANET y sus orígenes militares y académicos. "
        "La red creció hasta conectar universidades y laboratorios. "
    )
    new = (
        "La economía global depende hoy de los mercados financieros y del comercio internacional. "
        "Las cadenas de suministro conectan a los fabricantes con los consumidores. "
    )
    assert _has_strong_text_overlap(existing, new) is False


def test_strong_overlap_identical_paragraph_true() -> None:
    """Un párrafo prácticamente idéntico se detecta como duplicado claro."""
    paragraph = (
        "Este es un párrafo suficientemente largo con contenido histórico detallado "
        "acerca de la red ARPANET, sus orígenes y sus primeras conexiones universitarias. "
    )
    assert _has_strong_text_overlap(paragraph, paragraph) is True


def test_duplicate_continuation_not_concatenated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una continuación que es esencialmente repetición no se concatena."""
    import modules.chapter_writer.main as main

    initial = "# T\n\n## Antecedentes\n\n" + "Contenido inicial de la sección. " * 15
    responses = [_fake_result(initial), _fake_result(initial)]
    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["continuation_rejected_as_duplicate"] is True
    assert out["duplicate_detected"] is True
    # El texto no se duplicó: el word_count sigue siendo el de la generación inicial (1 llamada de gen + 1 rechazada).
    assert call_count[0] == 2


def test_duplicate_continuation_keeps_original_md_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    """El texto original md permanece íntegro aunque la continuación sea duplicada."""
    import modules.chapter_writer.main as main

    initial = "# T\n\n## Antecedentes\n\n" + "Contenido inicial de la sección. " * 15
    responses = [_fake_result(initial), _fake_result(initial)]
    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["metadata"]["text"] == initial
    assert out["word_count"] == len(initial.split())


def test_continuations_bounded_by_deficit_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """El nº de continuaciones deriva del déficit (no de un tope arbitrario).

    Con mínimo 1500 y generación inicial ~100 palabras, el déficit es ~1400.
    El presupuesto = ceil(déficit / AVG_WORDS_PER_CONTINUATION) = 2 continuaciones.
    Como cada continuación solo aporta 300 palabras, el mínimo no se alcanza y
    el Quality Gate devuelve FAIL al agotar el presupuesto determinista (3 llamadas:
    1 inicial + 2 continuaciones). El nombre cambió respecto al antiguo
    ``capped_at_two``: el límite ya no es un número fijo, sino el resultado del
    cálculo por déficit.
    """
    import modules.chapter_writer.main as main

    outline_headings = "\n".join(
        f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"]
    )
    gen = outline_headings + "\n\n" + "word " * 100
    cont1 = "second " * 300
    cont2 = "third " * 300
    responses = [_fake_result(gen), _fake_result(cont1), _fake_result(cont2)]
    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert call_count[0] == 3  # inicial + 2 continuaciones = 3 llamadas
    assert out["word_count"] < 1500
    assert out["quality_gate"] == "FAIL"


def test_target_word_count_and_minimum_unchanged() -> None:
    """Los defaults target_word_count=3000 y minimum_words=1500 siguen intactos."""
    from core.schemas import ChapterWritePayload

    assert _required_min_words({"minimum_words": None}) == 1500
    assert _required_min_words({"minimum_words": 2000}) == 2000
    assert ChapterWritePayload.model_fields["target_word_count"].default == 3000

# ---------------------------------------------------------------------------
# Diagnóstico estructural determinista (FASE 7.9D.1)
# ---------------------------------------------------------------------------


def test_extract_heading_structure_levels_and_normalization() -> None:
    """Detecta correctamente headings `##` y `###` con nivel y normalización."""
    from modules.chapter_writer.main import _build_subsections_map

    md = "# Título\n\n## Introducción\n\n## Conclusión\n\n### Subsección"
    structure = _extract_heading_structure(md)
    level2 = [e for e in structure if e["level"] == 2]
    assert [e["normalized"] for e in level2] == ["introduccion", "conclusion"]
    assert [e["title"] for e in level2] == ["Introducción", "Conclusión"]
    assert [e["position"] for e in level2] == [1, 2]
    # El título original se conserva.
    assert any(e["title"] == "Subsección" and e["level"] == 3 for e in structure)


def test_duplicate_sections_detected() -> None:
    """Detecta una `## Conclusión` repetida como duplicate_sections."""
    md = "## Antecedentes\n\nTexto.\n\n## Conclusión\n\nFin.\n\n## Fuentes utilizadas\n\n[Src]\n\n## Conclusión\n\nOtra."
    dups = _detect_duplicate_sections(md)
    assert len(dups) == 1
    assert dups[0]["heading"] == "Conclusión"
    assert dups[0]["occurrences"] == 2
    assert len(dups[0]["positions"]) == 2


def test_subsections_map_assigns_child_to_parent() -> None:
    """Asigna cada `###` a su `##` padre más reciente, conservando títulos."""
    md = "## La llegada de TCP/IP\n\nTexto.\n\n### Protocolos adicionales\n\n### Seguridad"
    subs_map = _build_subsections_map(md)
    assert subs_map.get("la llegada de tcp ip") == ["Protocolos adicionales", "Seguridad"]


def test_section_after_fuentes_is_structural_anomaly() -> None:
    """Un `##` editorial tras `## Fuentes utilizadas` es una anomalía de orden."""
    md = "## Antecedentes\n\nTexto.\n\n## Fuentes utilizadas\n\n[Src]\n\n## Sección nueva\n\nContenido."
    anomalies = _detect_structural_anomalies(md)
    assert any(
        a["type"] == "section_after_closing_section" and a["heading"] == "Sección nueva"
        for a in anomalies
    )


def test_second_conclusion_is_duplicate_closing_anomaly() -> None:
    """Una segunda `## Conclusión` tras el cierre es duplicate_closing_section."""
    md = "## Antecedentes\n\nTexto.\n\n## Conclusión\n\nFin.\n\n## Fuentes utilizadas\n\n[Src]\n\n## Conclusión\n\nOtra."
    anomalies = _detect_structural_anomalies(md)
    assert any(
        a["type"] == "duplicate_closing_section" and a["occurrences"] == 2
        for a in anomalies
    )


def test_structural_diagnostics_do_not_modify_text() -> None:
    """Las funciones de diagnóstico no alteran el Markdown original."""
    from modules.chapter_writer.main import _detect_unexpected_sections

    md = ("## Antecedentes\n\nTexto.\n\n## Conclusión\n\nFin.\n\n"
          "## Fuentes utilizadas\n\n[Src]\n\n## Sección extra\n\nContenido.")
    snapshot = md
    _extract_heading_structure(md)
    _detect_duplicate_sections(md)
    _build_subsections_map(md)
    _detect_structural_anomalies(md)
    _detect_unexpected_sections(md, _payload())
    assert md == snapshot


def test_structural_anomalies_do_not_force_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las anomalías estructurales no fuerzan el quality gate a FAIL."""
    import modules.chapter_writer.main as main

    md = (
        "## Antecedentes\n\n" + "word " * 1600
        + "\n\n## Fuentes utilizadas\n\nfuente\n\n"
        + "## Sección extra\n\n" + "word " * 50
    )
    responses = [_fake_result(md)]
    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            return responses[0]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["quality_gate"] == "PASS"
    assert out["unexpected_sections"] == ["Sección extra"]
    assert out["structural_anomalies"], "se esperaba al menos una anomalía estructural"
    assert call_count[0] == 1


def test_unexpected_sections_still_works() -> None:
    """El mecanismo previo de unexpected_sections sigue operativo."""
    md = "## Antecedentes\n\nTexto.\n\n## El futuro de Internet\n\nMás."
    assert _detect_unexpected_sections(md, _payload()) == ["El futuro de Internet"]
# ---------------------------------------------------------------------------
# Refuerzo estructural del prompt (FASE 7.9D.2)
# ---------------------------------------------------------------------------


def test_prompt_outline_is_closed_structure() -> None:
    """El outline se declara como estructura cerrada y obligatoria."""
    es = _build_prompt(_payload(), language="es")
    en = _build_prompt(_payload(), language="en")
    assert "CERRADA" in es and "OBLIGATORIA" in es
    assert "CLOSED" in en and "MANDATORY" in en


def test_prompt_forbids_new_main_sections() -> None:
    """El prompt prohíbe crear nuevas secciones principales ## fuera del outline."""
    es = _build_prompt(_payload(), language="es")
    en = _build_prompt(_payload(), language="en")
    assert "No crees nuevas secciones principales" in es
    assert "do not create new main sections" in en.lower()


def test_prompt_requires_respect_order() -> None:
    """El prompt exige respetar el orden de las secciones del outline."""
    es = _build_prompt(_payload(), language="es")
    assert "respeta exactamente el orden" in es.lower()


def test_prompt_single_conclusion_only() -> None:
    """La conclusión debe aparecer una sola vez."""
    es = _build_prompt(_payload(), language="es")
    assert "una sola vez" in es and "Conclusión" in es


def test_prompt_conclusion_is_closing_section() -> None:
    """La conclusión debe ser la última sección editorial."""
    es = _build_prompt(_payload(), language="es")
    en = _build_prompt(_payload(), language="en")
    assert "última sección editorial" in es
    assert "last editorial section" in en


def test_prompt_conclusion_no_new_concepts() -> None:
    """La conclusión no debe introducir conceptos nuevos."""
    es = _build_prompt(_payload(), language="es")
    en = _build_prompt(_payload(), language="en")
    assert "no introduzcas" in es and "conceptos" in es and "nuevos" in es
    assert "do not introduce new concepts" in en.lower()


def test_prompt_no_content_after_sources() -> None:
    """El prompt debe delimitar el final editorial del capítulo: ninguna sección
    después de la Conclusión (comportamiento actual tras fix §17 #19: la sección
    de fuentes ya no se pide al LLM, la añade el sistema)."""
    es = _build_prompt(_payload(), language="es")
    en = _build_prompt(_payload(), language="en")
    assert "No añadas ninguna sección después de `Conclusión`" in es
    assert "ninguna sección editorial `##` después de `Conclusión`" in es
    assert "Do not add any section after `Conclusion`" in en
    assert "no editorial `##` after `Conclusion`" in en


def test_prompt_no_repetition_for_length() -> None:
    """No se debe repetir contenido ni usar listas para alcanzar longitud."""
    es = _build_prompt(_payload(), language="es")
    en = _build_prompt(_payload(), language="en")
    assert "no repitas información para aumentar el número de palabras" in es.lower()
    assert "do not repeat information to increase the word count" in en.lower()
    assert "para aumentar longitud" in es.lower()


def test_continuation_prompt_no_new_sections_or_second_conclusion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las continuaciones no crean secciones nuevas ni una segunda conclusión."""
    import modules.chapter_writer.main as main

    outline_headings = "\n".join(
        f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"]
    )
    short_md = outline_headings + "\n\n" + "word " * 200
    continuation_md = "word " * 1500
    responses = [_fake_result(short_md), _fake_result(continuation_md)]
    call_count = [0]
    captured_prompts = []

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, prompt: str, *args: Any, **kwargs: Any) -> Any:
            captured_prompts.append(prompt)
            idx = call_count[0]
            call_count[0] += 1
            return responses[idx]

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md")

    out = execute(_payload())
    assert out["word_count"] >= 1500
    cont = [p for p in captured_prompts if "contenido nuevo" in p.lower() or "new content" in p.lower()]
    assert cont, "No se generó ningún prompt de continuación dirigida"
    # El prompt dirigido prohíbe headings y no pide continuar el capítulo completo.
    assert "Continue writing the chapter" not in cont[0]
    assert "##" in cont[0]
    assert "###" in cont[0]
    assert "no escribas una introduccion ni una conclusion" in cont[0].lower() or \
        "do not write an introduction or a conclusion" in cont[0].lower()
    assert "no cree" in cont[0].lower()


# ---------------------------------------------------------------------------
# CONTROL DETERMINISTA DE CONTINUACIÓN (FASE 7.9D.7)
# El programa manda, el LLM genera, Python valida y decide.
# ---------------------------------------------------------------------------


def _det_payload(minimum_words: int = 1500, target_word_count: int = 1500) -> dict:
    """Payload de tests con mínimo y objetivo separados."""
    p = dict(_payload())
    p["minimum_words"] = minimum_words
    p["target_word_count"] = target_word_count
    return p


def _run_det(payload: dict, responses: list) -> tuple:
    """Ejecuta execute con un FakeProvider; devuelve (out, call_count)."""
    import modules.chapter_writer.main as main

    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            idx = call_count[0]
            call_count[0] += 1
            if idx >= len(responses):
                return _fake_result("")
            return responses[idx]

    main.get_provider = lambda: FakeProvider()
    main.DEFAULT_ROUTER_MODEL = "qwen-agent:latest"
    main._write_artifacts = lambda *a, **k: "data/artifacts/1/chapter_1/chapter.md"
    out = main.execute(payload)
    return out, call_count


def _outline_body(words: int, word: str = "word ") -> str:
    heads = "\n".join(f"## {s['heading']}" for s in _payload()["chapter_outline"]["sections"])
    return heads + "\n\n" + word * words


def test_plan_deficit_zero_min_budget_zero() -> None:
    import modules.chapter_writer.main as main

    plan = main._plan_continuation_deficit(2000, 1500, 3000)
    assert plan["deficit"] == 0
    assert plan["min_budget"] == 0


def test_plan_deficit_computes_min_budget_from_deficit() -> None:
    import modules.chapter_writer.main as main

    # 700 palabras -> déficit 800 -> ceil(800/700) = 2 continuaciones para el mínimo.
    plan = main._plan_continuation_deficit(700, 1500, 3000)
    assert plan["deficit"] == 800
    assert plan["min_budget"] == 2


def test_plan_deficit_min_budget_capped_by_hard_limit() -> None:
    import modules.chapter_writer.main as main

    # Déficit enorme: el presupuesto mínimo se pega al HARD LIMIT absoluto.
    plan = main._plan_continuation_deficit(10, 10000, 10000)
    assert plan["deficit"] == 9990
    assert plan["min_budget"] == main.ABSOLUTE_HARD_LIMIT
    assert plan["min_budget"] + plan["target_budget"] <= main.ABSOLUTE_HARD_LIMIT


def test_plan_deficit_target_budget_bounded_no_infinite() -> None:
    import modules.chapter_writer.main as main

    plan = main._plan_continuation_deficit(1500, 1500, 3000)
    assert plan["deficit"] == 0
    assert plan["min_budget"] == 0
    assert plan["target_budget"] > 0
    assert plan["target_budget"] <= main.ABSOLUTE_HARD_LIMIT


def test_plan_deficit_total_never_exceeds_hard_limit() -> None:
    import modules.chapter_writer.main as main

    for words in (100, 700, 1490, 1500, 2900):
        plan = main._plan_continuation_deficit(words, 1500, 3000)
        assert plan["min_budget"] + plan["target_budget"] <= main.ABSOLUTE_HARD_LIMIT


def test_continuation_request_words_from_pending_deficit() -> None:
    import modules.chapter_writer.main as main

    assert main._continuation_request_words(1500, 700) == 960
    assert main._continuation_request_words(1500, 1490) == main.MIN_CONTINUATION_REQUEST
    assert main._continuation_request_words(1500, 1600) == 0


# ---------------------------------------------------------------------------
# Tests conductuales del control determinista
# ---------------------------------------------------------------------------


def test_700_plus_valid_800_reaches_minimum_no_extra_calls() -> None:
    """700 + continuación válida de 800 -> alcanza el mínimo en 2 llamadas."""
    out, call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500),
        [_fake_result(_outline_body(700)), _fake_result("newcontent " * 800)],
    )
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert call_count[0] == 2
    assert out["successful_continuations"] == 1
    assert out["total_continuation_attempts"] == 1


def test_duplicate_continuation_rejected_not_complete() -> None:
    """Una continuación duplicada se rechaza (y se reintenta), pero NO implica
    fin del capítulo: la siguiente continuación válida completa el mínimo."""
    gen = _outline_body(700)  # cuerpo = "word " * 700
    out, call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500),
        [
            _fake_result(gen),                      # generación inicial corta
            _fake_result("word " * 700),           # duplicado fuerte del cuerpo -> rechazado
            _fake_result("newcontent " * 800),     # válido -> inserta hasta el mínimo
        ],
    )
    # El rechazo no finaliza: Python vuelve a intentar y alcanza el mínimo.
    assert out["metadata"]["duplicate_rejections"] == 1
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert call_count[0] == 3  # inicial + 1 rechazada + 1 válida; NO se pide más


def test_after_valid_continuation_recompute_deficit() -> None:
    """Tras una continuación válida, Python vuelve a evaluar el déficit."""
    gen = _outline_body(700)
    out, call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500),
        [_fake_result(gen), _fake_result("nextcontent " * 300), _fake_result("newercontent " * 800)],
    )
    # cont1 aporta 300 (1002 < 1500): no llega al mínimo; se pide otra.
    # cont2 aporta 800 (1802 >= 1500): se detiene.
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert call_count[0] == 3
    assert out["successful_continuations"] == 2


def test_1490_plus_20_reaches_minimum_stops() -> None:
    """1490 + continuación de ~20 alcanza 1500 y se detiene (sin 3ª llamada)."""
    gen = _outline_body(1490)
    out, call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500),
        [_fake_result(gen), _fake_result("tiny " * 20)],
    )
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert call_count[0] == 2  # inicial + 1 continuación; NO se pide más


def test_minimum_reached_no_llm_for_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con >= minimum_words iniciales, NO se llama al LLM para continuar."""
    import modules.chapter_writer.main as main

    call_count = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            return _fake_result(_outline_body(1600))

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *a, **k: "data/artifacts/1/chapter_1/chapter.md")
    out = main.execute(_det_payload(minimum_words=1500, target_word_count=1500))
    assert call_count[0] == 1
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"


def test_1500_with_target_3000_bounded_no_infinite_loop() -> None:
    """1500 palabras (mínimo) con target 3000: se acerca al target con un
    presupuesto best-effort acotado y NUNCA hace un bucle infinito."""
    import modules.chapter_writer.main as main

    gen = _outline_body(1600)  # ya supera el mínimo (1500)
    # El proveedor devuelve trozos válidos pero pequeños y diferentes, de modo
    # que nunca alcanza 3000 por sí solo: el sistema DEBE detenerse por
    # presupuesto determinista, no por agotar paciencia.
    responses = [_fake_result(gen)]
    for i in range(main.ABSOLUTE_HARD_LIMIT + 5):
        responses.append(_fake_result(f"filler{i} " * 50))

    out, call_count = _run_det(_det_payload(minimum_words=1500, target_word_count=3000), responses)

    # La fase mínima se salta (ya se supera 1500); solo se gasta el presupuesto
    # best-effort del target, acotado por el HARD LIMIT.
    assert out["word_count"] >= 1500            # mínimo garantizado
    assert out["quality_gate"] == "PASS"
    assert call_count[0] <= 1 + main.ABSOLUTE_HARD_LIMIT  # NUNCA loop infinito
    assert out["total_continuation_attempts"] <= main.ABSOLUTE_HARD_LIMIT


def test_empty_provider_terminates_controlled() -> None:
    """Un proveedor que devuelve contenido vacío termina de forma controlada."""
    gen = _outline_body(700)
    out, call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500),
        [_fake_result(gen), _fake_result("")],
    )
    # La continuación vacía produce progreso insignificante -> terminación
    # controlada. No se reintenta (no while True).
    assert call_count[0] == 2
    assert out["word_count"] < 1500
    assert out["quality_gate"] == "FAIL"
    assert out["total_continuation_attempts"] == 1


def test_constant_duplicate_provider_stops_on_repeated_content_fail() -> None:
    """Proveedor que siempre devuelve el mismo contenido duplicado: Python lo
    detecta (repetición idéntica), deja de insistir y devuelve FAIL. Nunca un
    loop infinito (garantizado por el HARD LIMIT)."""
    import modules.chapter_writer.main as main

    gen = _outline_body(700)            # cuerpo = "word " * 700
    dup = "word " * 700                 # duplicado fuerte y repetido idénticamente
    responses = [_fake_result(gen), _fake_result(dup), _fake_result(dup), _fake_result(dup)]
    out, call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500), responses
    )
    assert out["quality_gate"] == "FAIL"
    assert out["word_count"] < 1500
    # Termina de forma acotada (HARD LIMIT / detección de repetición) y sin loop:
    assert call_count[0] <= 1 + main.ABSOLUTE_HARD_LIMIT
    assert out["metadata"]["duplicate_rejections"] >= 1
    assert out["metadata"]["repeated_continuation"] is True


def test_content_never_truncated() -> None:
    """El contenido editorial (inicial + continuaciones) nunca se trunca."""
    gen = _outline_body(700)
    cont = "newcontent " * 800
    out, call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500),
        [_fake_result(gen), _fake_result(cont)],
    )
    text = out["metadata"]["text"]
    assert "word " * 100 in text            # todo el contenido inicial conservado
    assert "newcontent " * 100 in text      # todo el contenido de la continuación conservado
    assert out["total_added_words"] >= 800  # nada truncado
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"


def test_quality_gate_remains_final_authority() -> None:
    """Las continuaciones pueden tener éxito, pero el Quality Gate sigue decidiendo
    PASS/FAIL: un placeholder en el texto fuerza FAIL aun habiendo >= 1500 palabras."""
    gen = _outline_body(700) + "\n\nTODO pendiente de rellenar"
    out, call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500),
        [_fake_result(gen), _fake_result("newcontent " * 800)],
    )
    assert out["word_count"] >= 1500      # éxito de longitud
    assert out["quality_gate"] == "FAIL"  # ...pero Quality Gate rechaza (placeholder)
    assert out["quality_errors"]
    assert any("placeholder" in e.lower() for e in out["quality_errors"])


# ---------------------------------------------------------------------------
# Tests del motor determinista (FASE 7.9D.7) — backstop sin depender del LLM
# ---------------------------------------------------------------------------


def test_deterministic_complete_reaches_minimum_without_new_headings() -> None:
    """El backstop amplía secciones existentes hasta el mínimo sin crear headings nuevos ni placeholders."""
    import modules.chapter_writer.main as main

    md = "# Introducción\n\n## Antecedentes\n\n" + "dato " * 100  # ~100 palabras < 1500.
    out_md, out_words = main._deterministic_complete(
        md, len(md.split()), 1500, _payload(), "es",
        facts=["La capa de enlace usa Ethernet.", "IP divide los paquetes en tramas."],
    )
    assert out_words >= 1500
    assert "## Antecedentes" in out_md
    # No se crean headings nuevos fuera del outline.
    assert out_md.count("## ") == 1
    # Ni placeholders técnicos.
    assert main._detect_placeholder(out_md) is False


def test_deterministic_complete_preserves_outline_headings() -> None:
    """El backstop conserva el H1 y los H2 del outline y respeta el orden del outline."""
    import modules.chapter_writer.main as main

    md = "# Título del Capítulo\n\n## Antecedentes\n\n" + "dato " * 50
    facts = ["Cada hecho distinto se reutiliza rotativamente por semilla."]
    out_md, out_words = main._deterministic_complete(
        md, len(md.split()), 1500, _payload(), "es", facts=facts
    )
    assert out_words >= 1500
    assert out_md.startswith("# Título del Capítulo")
    assert "## Antecedentes" in out_md
    assert main._detect_placeholder(out_md) is False

def _shared_book_facts() -> list[str]:
    """Pool de hechos compartido a nivel de LIBRO (patrón book_37).

    Simula sources cuyo ``content`` cubre varios capítulos del mismo libro:
    todos los capítulos extraen del mismo pool via `_extract_research_facts`.
    """
    import modules.chapter_writer.main as main

    sources = [
        {"url": "https://example.com/s1", "title": "Fuente A",
         "source_type": "web", "chapter_ids": [1, 2, 3],
         "content": "La capa de enlace usa tramas Ethernet para encapsular los datos."},
        {"url": "https://example.com/s2", "title": "Fuente B",
         "source_type": "web", "chapter_ids": [1, 2],
         "content": "El protocolo IP enruta paquetes entre redes distintas de forma autónoma."},
        {"url": "https://example.com/s3", "title": "Fuente C",
         "source_type": "web", "chapter_ids": [2, 3],
         "content": "TCP garantiza la entrega fiable y ordenada de los segmentos transmitidos."},
    ]
    return main._extract_research_facts(None, sources)


def test_deterministic_complete_varies_output_across_chapters() -> None:
    """Fix §17 #7 (book_37): capítulos distintos NO generan párrafos literales idénticos.

    Mismo pool de hechos compartido + mismo minimum_words, distinto chapter_number
    → el texto completo generado debe diferir (comparación de string completa).
    """
    import modules.chapter_writer.main as main

    md = "# Introducción\n\n## Antecedentes\n\n" + "dato " * 100
    facts = _shared_book_facts()

    payload_ch1 = _payload()
    payload_ch1["chapter_outline"]["number"] = 1
    out_md_1, out_words_1 = main._deterministic_complete(
        md, len(md.split()), 1500, payload_ch1, "es", facts=facts,
    )

    payload_ch2 = _payload()
    payload_ch2["chapter_outline"]["number"] = 2
    out_md_2, out_words_2 = main._deterministic_complete(
        md, len(md.split()), 1500, payload_ch2, "es", facts=facts,
    )

    assert out_words_1 >= 1500 and out_words_2 >= 1500
    # Comparación de string completa, no solo longitud.
    assert out_md_1 != out_md_2


def test_deterministic_complete_no_verbatim_overlap_across_chapters_small_pool() -> None:
    """Re-fix regresión §17 #7 (book_43): rangos de seed DISJUNTOS por capítulo.

    Con pool de hechos PEQUEÑO y muchos párrafos por capítulo, el offset +1
    viejo hacía que cap1 (seeds 1..M) y cap2 (seeds 2..K) recorrieran casi el
    mismo rango → mismo seed_total producía párrafos verbatim idénticos entre
    capítulos. Con chapter_number*1000 los rangos son disjuntos: ningún
    párrafo del backstop de un capítulo puede aparecer en el otro.
    """
    import modules.chapter_writer.main as main

    facts = _shared_book_facts()[:3]  # pool deliberadamente pobre (3 hechos)
    md = "# Introducción\n\n## Antecedentes\n\n" + "dato " * 100

    def _backstop_paragraphs(out_md: str) -> set[str]:
        return {
            p.strip() for p in out_md.split("\n\n")
            if any(p.strip().startswith(opener) for opener in main._DET_OPENERS_ES)
        }

    paras_by_chapter: dict[int, set[str]] = {}
    # Estrés al peor caso realista: minimum alto genera ~90+ párrafos backstop
    # por capítulo (~P_max: ABSOLUTE_HARD_LIMIT*8 iteraciones, párrafos ~30-38w).
    for number in (1, 2):
        payload = _payload()
        payload["chapter_outline"]["number"] = number
        out_md, out_words = main._deterministic_complete(
            md, len(md.split()), 3200, payload, "es", facts=facts,
        )
        assert out_words >= 3200
        paras_by_chapter[number] = _backstop_paragraphs(out_md)

    # Suficientes párrafos backstop por capítulo para que cualquier solape de
    # rangos o de espacio combinatorio se manifieste.
    assert len(paras_by_chapter[1]) >= 20
    assert len(paras_by_chapter[2]) >= 20
    # NINGÚN párrafo verbatim compartido entre capítulos.
    assert not (paras_by_chapter[1] & paras_by_chapter[2])



def test_deterministic_complete_same_chapter_is_stable() -> None:
    """No-regresión: mismo chapter_number + mismos hechos → MISMO resultado (determinismo)."""
    import modules.chapter_writer.main as main

    md = "# Introducción\n\n## Antecedentes\n\n" + "dato " * 100
    facts = _shared_book_facts()

    payload_a = _payload()
    payload_a["chapter_outline"]["number"] = 1
    out_md_a, _ = main._deterministic_complete(
        md, len(md.split()), 1500, payload_a, "es", facts=facts,
    )

    payload_b = _payload()
    payload_b["chapter_outline"]["number"] = 1
    out_md_b, _ = main._deterministic_complete(
        md, len(md.split()), 1500, payload_b, "es", facts=facts,
    )

    assert out_md_a == out_md_b


def test_extract_research_facts_unique_sentences() -> None:
    """_extract_research_facts devuelve oraciones únicas (>= 6 palabras), sin duplicados."""
    import modules.chapter_writer.main as main

    research = (
        "TCP fue introducido en 1970. IP divide paquetes.\n"
        "- La capa de enlace usa Ethernet.\n"
        "- La capa de red usa IP.\n"
    )
    facts = main._extract_research_facts(research, None, max_facts=10)
    assert len(facts) == len(set(facts))
    assert "La capa de enlace usa Ethernet." in facts
    assert "La capa de red usa IP." in facts


def test_execute_no_llm_mode_reaches_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    """CHAP_USE_LLM=0: el pipeline ni instancia el provider ni llama al LLM; el backstep garantiza el mínimo."""
    import modules.chapter_writer.main as main

    monkeypatch.setenv("CHAP_USE_LLM", "0")
    monkeypatch.setattr(main, "_write_artifacts", lambda *a, **k: "data/artifacts/1/chapter_1/chapter.md")
    out = main.execute(_det_payload(minimum_words=1500, target_word_count=1500))
    assert out["provider"] == "none"
    assert out["execution_mode"] == "deterministic"
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert out["deterministic_used"] is True


def test_execute_force_min_completes_short_chapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """CHAP_FORCE_MIN=1: aunque el LLM genera poco y la continuación vacía no suma, el backstep alcanza el mínimo."""
    import modules.chapter_writer.main as main

    monkeypatch.setenv("CHAP_FORCE_MIN", "1")
    calls = [0]

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            calls[0] += 1
            if calls[0] == 1:
                return _fake_result(_outline_body(100))
            return _fake_result("")

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *a, **k: "data/artifacts/1/chapter_1/chapter.md")
    out = main.execute(_det_payload(minimum_words=1500, target_word_count=1500))
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert out["deterministic_used"] is True
    assert out["execution_mode"] == "deterministic"
    assert calls[0] >= 1


def test_execute_force_min_not_triggered_when_minimum_already_met(monkeypatch: pytest.MonkeyPatch) -> None:
    """Si el LLM ya supera el mínimo, CHAP_FORCE_MIN no debe re-amplificar (deterministic_used=False)."""
    import modules.chapter_writer.main as main

    monkeypatch.setenv("CHAP_FORCE_MIN", "1")
    out, _call_count = _run_det(
        _det_payload(minimum_words=1500, target_word_count=1500),
        [_fake_result(_outline_body(1600))],
    )
    assert out["word_count"] >= 1500
    assert out["quality_gate"] == "PASS"
    assert out["deterministic_used"] is False
def _outline_canon_6() -> dict:
    """Outline de 6 secciones equivalente al usado por el E2E editorial 001."""
    return {"chapter_outline": {"sections": [
        {"heading": "Introducción"},
        {"heading": "Orígenes: ARPA y ARPANET"},
        {"heading": "El nacimiento de ARPANET"},
        {"heading": "La llegada de TCP/IP"},
        {"heading": "Hacia la Internet moderna"},
        {"heading": "Conclusión"},
    ]}}


def test_canonicalize_heading_differences_case_accents_punctuation() -> None:
    """Diferencias de mayúsculas/acentos/puntuación -> el heading se canoniza."""
    out = _canonicalize_headings(
        "# Título\n\n## introduccion:\nMismo contenido.\n",
        _outline_canon_6(),
    )
    assert "## Introducción" in out
    assert "## introduccion:" not in out


def test_canonicalize_semantic_variant_of_outline_heading() -> None:
    """Una variante semántica clara de un heading del outline se canoniza (E2E 001)."""
    md = (
        "# El nacimiento de Internet\n"
        "## Introducción\nIntro.\n"
        "## Orígenes: ARPA y ARPANET\nOrigen.\n"
        "## El nacimiento de ARPANET\nNacimiento.\n"
        "## La llegada de TCP/IP\nTcpip.\n"
        "## ARPANET evoluciona hasta convertirse en Internet\nLa evolucion de ARPANET.\n"
        "## Conclusión\nCierre.\n"
        "## Fuentes utilizadas\n[Fuente (web_wikipedia)]\n"
    )
    out = _canonicalize_headings(md, _outline_canon_6())
    assert "## Hacia la Internet moderna" in out
    assert "## ARPANET evoluciona hasta convertirse en Internet" not in out


def test_canonicalize_preserves_content_under_heading() -> None:
    """El contenido editorial debajo del heading se conserva íntegro al canonizar."""
    md = (
        "# El nacimiento de Internet\n"
        "## Introducción\nIntro.\n"
        "## Orígenes: ARPA y ARPANET\nOrigen.\n"
        "## El nacimiento de ARPANET\nNacimiento.\n"
        "## La llegada de TCP/IP\nTcpip.\n"
        "## ARPANET evoluciona hasta convertirse en Internet\n"
        "La evolucion hacia Internet fue gradual y fundacional.\n"
        "## Conclusión\nCierre.\n"
    )
    out = _canonicalize_headings(md, _outline_canon_6())
    assert "## Hacia la Internet moderna" in out
    # El párrafo bajo el heading original se conserva sin alteración.
    assert "La evolucion hacia Internet fue gradual y fundacional." in out


def test_canonicalize_rejects_arbitrary_heading() -> None:
    """Un heading arbitrario sin relación con el outline NO se convierte en válido."""
    md = (
        "## Introducción\nIntro.\n"
        "## Inventario de componentes electrónicos\nTexto sin relacion con el outline.\n"
    )
    out = _canonicalize_headings(md, _outline_canon_6())
    assert "## Inventario de componentes electrónicos" in out
    # No puede inventar una sección canónica a partir de un heading arbitrario.
    assert "## Hacia la Internet moderna" not in out


def test_canonicalize_all_final_sections_belong_to_outline() -> None:
    """Tras canonizar, toda sección presente pertenece al outline (ninguna inesperada)."""
    md = (
        "# El nacimiento de Internet\n"
        "## Introducción\nIntro.\n"
        "## Orígenes: ARPA y ARPANET\nOrigen.\n"
        "## El nacimiento de ARPANET\nNacimiento.\n"
        "## La llegada de TCP/IP\nTcpip.\n"
        "## ARPANET evoluciona hasta convertirse en Internet\nEvolucion.\n"
        "## Conclusión\nCierre.\n"
    )
    out = _canonicalize_headings(md, _outline_canon_6())
    assert _detect_unexpected_sections(out, _outline_canon_6()) == []


def test_canonicalize_does_not_duplicate_sections() -> None:
    """La canonicalización no crea ni duplica secciones del outline."""
    md = (
        "## Introducción\nA.\n"
        "## Orígenes: ARPA y ARPANET\nB.\n"
        "## El nacimiento de ARPANET\nC.\n"
        "## La llegada de TCP/IP\nD.\n"
        "## ARPANET evoluciona hasta convertirse en Internet\nE.\n"
        "## Conclusión\nF.\n"
    )
    out = _canonicalize_headings(md, _outline_canon_6())
    assert _detect_duplicate_sections(out) == []
    for h in ("Introducción", "Orígenes: ARPA y ARPANET", "El nacimiento de ARPANET",
              "La llegada de TCP/IP", "Hacia la Internet moderna", "Conclusión"):
        assert f"## {h}" in out
def _canon_pay_6(minimum_words: int = 1500, target_word_count: int = 1500) -> dict:
    """Payload realista de 6 secciones (equivalente al E2E 001)."""
    p = dict(_payload())
    p["chapter_outline"] = {
        "number": 1,
        "title": "El nacimiento de Internet",
        "objective": "Nacimiento y evolucion inicial de Internet",
        "sections": [
            {"heading": "Introducción", "objective": "Contexto y pregunta central"},
            {"heading": "Orígenes: ARPA y ARPANET", "objective": "Proyectos militares y académicos"},
            {"heading": "El nacimiento de ARPANET", "objective": "Primera red de conmutación de paquetes"},
            {"heading": "La llegada de TCP/IP", "objective": "Unificación de protocolos"},
            {"heading": "Hacia la Internet moderna", "objective": "De ARPANET a Internet"},
            {"heading": "Conclusión", "objective": "Impacto y legado"},
        ],
    }
    p["minimum_words"] = minimum_words
    p["target_word_count"] = target_word_count
    return p


def _rephrased_body_6(words_per_section: int = 300) -> str:
    """Capítulo con todos los headings del outline excepto 'Hacia la Internet moderna',
    que el LLM reescribe como 'ARPANET evoluciona hasta convertirse en Internet'."""
    heads = [
        "## Introducción",
        "## Orígenes: ARPA y ARPANET",
        "## El nacimiento de ARPANET",
        "## La llegada de TCP/IP",
        "## ARPANET evoluciona hasta convertirse en Internet",  # variante del LLM
        "## Conclusión",
    ]
    parts = ["# El nacimiento de Internet"]
    for h in heads:
        parts.append(f"{h}\n\n" + "dato " * words_per_section)
    return "\n".join(parts)


def test_execute_canonicalizes_rephrased_heading_to_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """execute() canoniza un heading del outline reformulado por el LLM y alcanza PASS.

    Reproduce exactamente el fallo del E2E 001 (antes: 'falta la sección del outline:
    Hacia la Internet moderna' + 'secciones fuera del outline'). Con la canonicalización
    determinista, el capítulo final tiene todas las secciones canónicas => quality_gate
    PASS >1500 palabras, sin duplicados ni placeholders.
    """
    import modules.chapter_writer.main as main

    monkeypatch.setenv("CHAP_FORCE_MIN", "1")
    initial = _rephrased_body_6(words_per_section=300)  # ~6*300 = 1800 palabras
    out, call_count = _run_det(
        _canon_pay_6(minimum_words=1500, target_word_count=1500),
        [_fake_result(initial), _fake_result("")],
    )
    text = out["metadata"]["text"]
    assert out["quality_gate"] == "PASS"
    assert out["word_count"] >= 1500
    assert "## Hacia la Internet moderna" in text
    assert "## ARPANET evoluciona hasta convertirse en Internet" not in text
    assert out["metadata"]["unexpected_sections"] == []
    # El contenido bajo el heading reformulado se conserva (contribuye al mínimo).
    assert "dato " * 300 in text

def test_writer_local_provider_receives_timeout_and_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Antes de generate(), el provider local recibe timeout=60 y max_retries=1.

    Mismo patrón que el Editor: si no se acota el horizonte, un Ollama lento o
    bloqueado puede superar el timeout externo del scheduler (180s) antes de que
    el fallback/backstop se ejecute.
    """
    import modules.chapter_writer.main as main

    seen: dict = {}

    class FakeResult:
        text = "# Capítulo\n\n" + "palabra " * 2000
        provider = "ollama"
        model = "qwen-agent:latest"
        input_tokens = 10
        output_tokens = 2000
        cost = 0.0
        raw_response = {"model": "qwen-agent:latest", "response": text}

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            seen["provider"] = self
            return FakeResult()

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(
        main, "_write_artifacts", lambda *args, **kwargs: "data/artifacts/1/chapter_1/chapter.md"
    )

    execute(_payload())
    assert seen["provider"].timeout == 60
    assert seen["provider"].max_retries == 1


def test_writer_budget_exhausted_triggers_deterministic_backstop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si la fase LLM agota el presupuesto de tiempo, el writer cae al backstop
    determinista y entrega un capítulo válido sin quedar colgado."""
    import modules.chapter_writer.main as main

    captured = {"det_calls": 0}

    class FakeResult:
        text = "# Capítulo\n\n## Antecedentes\n\nTexto corto."  # < minimum_words
        provider = "ollama"
        model = "qwen-agent:latest"
        input_tokens = 10
        output_tokens = 20
        cost = 0.0
        raw_response = {"model": "qwen-agent:latest", "response": text}

    class FakeProvider:
        name = "ollama"
        model = "qwen-agent:latest"

        def generate(self, *args: Any, **kwargs: Any) -> FakeResult:
            return FakeResult()

    long_md = "# Capítulo\n\n## Antecedentes\n\n" + "palabra " * 1600

    def fake_deterministic(md, words, minimum_words, validated, language):
        captured["det_calls"] += 1
        return long_md, len(long_md.split())

    monkeypatch.setattr(main, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main, "DEFAULT_ROUTER_MODEL", "qwen-agent:latest")
    monkeypatch.setattr(main, "_write_artifacts", lambda *a, **k: "data/artifacts/1/chapter_1/chapter.md")
    # Simula que el presupuesto de tiempo de la fase LLM se agotó de inmediato.
    monkeypatch.setattr(main, "_llm_budget_exhausted", lambda _start: True)
    monkeypatch.setattr(main, "_deterministic_complete", fake_deterministic)

    out = execute(_payload())
    # El backstop determinista se ejecutó porque el presupuesto se agotó.
    assert captured["det_calls"] >= 1
    assert out["execution_mode"] in ("deterministic", "fallback")
    assert out["word_count"] >= 1
    assert out["quality_gate"] in ("PASS", "WARNING")


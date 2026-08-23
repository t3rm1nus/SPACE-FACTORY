"""Tests del detector de placeholders de ``chapter_writer``.

Verifican que el token técnico ``TODO`` se detecte como palabra independiente
en mayúsculas (evitando el falso positivo con el español ``todo``) y que el
resto de placeholders técnicos sigan detectándose.

También cubren referencias bibliográficas legítimas como
``[ARPANET (web_wikipedia)]`` que no deben marcarse como placeholder.
"""
from __future__ import annotations

import pytest
from typing import Any

from modules.chapter_writer.main import _detect_placeholder


# Tokens técnicos que DEBEN ser detectados como placeholder.
DEBE_DETECTAR = [
    "TODO",
    "TODO:",
    "[TODO]",
    "{{TODO}}",
    "Lorem ipsum dolor sit amet",
    "Desarrollar el núcleo del tema",
    "Desarrollar el nucleo del capítulo",
    "contenido de prueba",
    "insert text here",
    "[pendiente de revisión]",
    "{{nombre}}",
    "Haz clic en {{nombre}} para continuar",
    "Revisar [sección] antes de publicar",
]


# Texto natural en español o referencias legítimas que NO debe ser detectado.
NO_DEBE_DETECTAR = [
    "todo",
    "Todo",
    "Todos",
    "todavía",
    "entorno",
    "toda",
    "Toda",
    "todas",
    "todo lo anterior",
    "Este capítulo desarrolla todo lo necesario.",
    "En 1969 se creó ARPANET, lo que da lugar a todo Internet.",
    # Referencias bibliográficas legítimas (formato [Nombre (web_wikipedia)]).
    "[ARPANET (web_wikipedia)]",
    "[Internet (web_wikipedia)]",
    "[Familia de protocolos de internet (web_wikipedia)]",
    "[Agencia de Proyectos de Investigación Avanzados de Defensa (web_wikipedia)]",
    # Enlaces Markdown legítimos.
    "[ARPANET](https://es.wikipedia.org/wiki/ARPANET)",
    "[Creeper (virus)](https://es.wikipedia.org/wiki/Creeper_(virus))",
    "[Internet](https://es.wikipedia.org/wiki/Internet)",
]


@pytest.mark.parametrize("text", DEBE_DETECTAR)
def test_placeholder_debe_detectar(text: str) -> None:
    assert _detect_placeholder(text) is True


@pytest.mark.parametrize("text", NO_DEBE_DETECTAR)
def test_placeholder_no_debe_detectar(text: str) -> None:
    assert _detect_placeholder(text) is False


def test_todo_es_case_sensitive() -> None:
    """`TODO` mayúsculas se detecta; el español todo/Todo/todos no."""
    assert _detect_placeholder("TODO") is True
    assert _detect_placeholder("todo") is False
    assert _detect_placeholder("Todo") is False
    assert _detect_placeholder("tOdO") is False
    assert _detect_placeholder("Todos") is False
    assert _detect_placeholder("toda") is False
    assert _detect_placeholder("todavía") is False
    assert _detect_placeholder("entorno") is False


def test_falso_positivo_minuscula_resuelto() -> None:
    """Regresión: el español 'todo' dejó de ser un placeholder técnico."""
    assert _detect_placeholder("Hoy se publica todo el contenido acordado.") is False
    assert _detect_placeholder("Ver TODO: revisar con el equipo") is True


def test_texto_real_sin_placeholders() -> None:
    """Un capítulo real corto sin placeholders no debe ser detectado."""
    texto_real = """
# El nacimiento de Internet

## Introducción

El siglo XX fue un período de revolución tecnológica que marcó el surgimiento y la evolución de muchas de las tecnologías modernas. En este contexto, el nacimiento de Internet es un hito fundamental que ha transformado la forma en que nos comunicamos, trabajamos y nos relacionamos entre nosotros. Este capítulo explora los orígenes del Internet, desde sus inicios como una red militar hasta su evolución hacia la plataforma global que conocemos hoy.
"""
    assert _detect_placeholder(texto_real) is False


def test_referencias_bibliograficas_no_son_placeholder() -> None:
    """Las referencias [Nombre (web_wikipedia)] no se marcan como placeholder."""
    assert _detect_placeholder("[ARPANET (web_wikipedia)]") is False
    assert _detect_placeholder("[Internet (web_wikipedia)]") is False
    assert _detect_placeholder(
        "[Familia de protocolos de internet (web_wikipedia)]"
    ) is False
    assert _detect_placeholder(
        "[Agencia de Proyectos de Investigación Avanzados de Defensa (web_wikipedia)]"
    ) is False


def test_placeholders_tecnicos_en_corchetes() -> None:
    """Los placeholders técnicos dentro de corchetes siguen detectándose."""
    assert _detect_placeholder("[TODO]") is True
    assert _detect_placeholder("[pendiente]") is True
    assert _detect_placeholder("[nombre]") is True
    assert _detect_placeholder("[autor]") is True
    assert _detect_placeholder("[fecha]") is True
    assert _detect_placeholder("[insert text]") is True


def test_enlaces_markdown_no_son_placeholder() -> None:
    """Los enlaces Markdown [texto](url) no se marcan como placeholder."""
    assert _detect_placeholder("[ARPANET](https://es.wikipedia.org/wiki/ARPANET)") is False
    assert _detect_placeholder("[Creeper (virus)](https://es.wikipedia.org/wiki/Creeper_(virus))") is False
    assert _detect_placeholder("[Internet](https://es.wikipedia.org/wiki/Internet)") is False


def test_archivo_real_chapter_no_es_placeholder() -> None:
    """El capítulo real generado no debe marcarse como placeholder."""
    from pathlib import Path

    chapter_path = Path("data/artifacts/1001/chapter_1/chapter.md")
    if chapter_path.exists():
        text = chapter_path.read_text(encoding="utf-8")
        assert _detect_placeholder(text) is False


# --- Detección de rechazos del LLM (REFUSAL_PATTERNS) -----------------------

from modules.chapter_writer.main import REFUSAL_PATTERNS, _continuation_step, _detect_refusal


FRASE_RECHAZO_REAL = "Lo siento, pero no puedo ayudar con eso."


def test_detect_refusal_frase_real_produccion() -> None:
    """La frase de rechazo real encontrada en producción se detecta."""
    assert _detect_refusal(FRASE_RECHAZO_REAL) is True


def test_detect_refusal_variantes_case_insensitive() -> None:
    assert _detect_refusal("LO SIENTO, PERO NO PUEDO AYUDAR CON ESO.") is True
    assert _detect_refusal("As an AI language model, I cannot assist with that request.") is True
    assert _detect_refusal("No puedo continuar con esa solicitud.") is True


def test_detect_refusal_contenido_valido_no_se_marca() -> None:
    """Texto editorial normal no debe marcarse como rechazo."""
    assert _detect_refusal("El protocolo TCP permitió la transmisión fiable de datos.") is False
    assert _detect_refusal("") is False


def test_continuation_step_rechaza_refusal() -> None:
    """Una propuesta de continuación que es un rechazo del LLM se descarta
    (mismo camino que rejected_duplicate): status 'rejected_refusal' y el
    texto NO se inserta en el capítulo."""

    class _FakeResult:
        text = FRASE_RECHAZO_REAL
        input_tokens = 10
        output_tokens = 12

    class _FakeProvider:
        name = "fake"

        def generate(self, *args: Any, **kwargs: Any) -> _FakeResult:
            return _FakeResult()

    validated = {
        "book_metadata": {"title": "Libro", "language": "es"},
        "chapter_outline": {
            "number": 1,
            "title": "Introducción",
            "sections": [{"heading": "Antecedentes", "objective": "Contexto"}],
        },
        "target_word_count": 1500,
    }
    md = "# Libro\n\n## Antecedentes\n\nTexto existente suficiente para operar.\n"
    step = _continuation_step(
        md,
        len(md.split()),
        1500,
        validated,
        _FakeProvider(),
        context=None,
        input_tokens=0,
        output_tokens=0,
        max_tokens=512,
        attempt=0,
        previous_proposal_norm="",
    )
    assert step["status"] == "rejected_refusal"
    assert step["md"] == md  # la frase de rechazo NO aparece en el capítulo
    assert FRASE_RECHAZO_REAL not in step["md"]

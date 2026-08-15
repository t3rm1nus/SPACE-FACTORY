"""Módulo word_counter: cuenta palabras y caracteres de un texto.

Capability: ``count_words``. Módulo tipo ``tool``: no depende de proveedores LLM
ni de servicios externos; es completamente determinista.
"""

from __future__ import annotations

from core.logger import get_logger
from core.schemas import CountWordsPayload, validate_payload

logger = get_logger(__name__)


def health_check() -> dict:
    """Verifica la salud del módulo word_counter.

    No depende de servicios externos, siempre está sano.
    """
    return {
        "healthy": True,
        "dependencies": {},
        "status": "🟢 healthy",
    }


def _word_stats(text: str) -> dict:
    """Calcula estadísticas de conteo a partir del texto validado."""
    words = text.split()
    word_count = len(words)
    char_count = len(text)
    char_count_no_spaces = len(text.replace(" ", ""))
    avg_word_length = (
        round(sum(len(w) for w in words) / word_count, 3) if word_count else 0.0
    )
    return {
        "word_count": word_count,
        "char_count": char_count,
        "char_count_no_spaces": char_count_no_spaces,
        "avg_word_length": avg_word_length,
    }


def execute(payload: dict) -> dict:
    """Cuenta palabras y caracteres del texto recibido en el payload.

    Args:
        payload: dict con la clave ``text`` (str).

    Returns:
        dict con ``word_count``, ``char_count``, ``char_count_no_spaces``,
        ``avg_word_length`` y ``provider`` (``"none"``, herramienta sin IA).
    """
    validated = validate_payload("count_words", payload)
    text = validated.get("text", "") or ""
    stats = _word_stats(text)
    # Herramienta sin IA: no consume un proveedor de tokens/coste.
    stats["provider"] = "none"
    logger.info("Texto analizado: %d palabras", stats["word_count"])
    return stats

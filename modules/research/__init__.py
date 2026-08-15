"""Módulo research: búsqueda web real, extracción de texto y almacenamiento de fuentes."""

from .main import (
    research_web,
    fetch_url,
    extract_text,
    health_check,
    execute,
)

__all__ = [
    "research_web",
    "fetch_url",
    "extract_text",
    "health_check",
    "execute",
]
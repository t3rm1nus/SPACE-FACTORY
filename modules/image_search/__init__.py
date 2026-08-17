"""Paquete image_search: busca y descarga imágenes web de capítulos (sin LLM)."""

from __future__ import annotations

from .main import (
    execute,
    health_check,
    search_chapter_images,
)

__all__ = [
    "execute",
    "health_check",
    "search_chapter_images",
]

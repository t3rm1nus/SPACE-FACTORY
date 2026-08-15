"""Schemas Pydantic del modelo editorial de Space Lair.

Define las estructuras de Book, Chapter y Source, además de validaciones
básicas. La persistencia se delega en core/book/book_storage.py para no
duplicar lógica de SQLite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from core.book.book_state import BookStatus, ChapterStatus, SourceType


class Book(BaseModel):
    """Proyecto editorial completo."""

    book_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=500)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    author: Optional[str] = Field(default=None, max_length=200)
    target_audience: Optional[str] = Field(default=None, max_length=200)
    genre: Optional[str] = Field(default=None, max_length=100)
    languages: list[str] = Field(default_factory=lambda: ["es"])
    target_chapters: int = Field(default=10, ge=1, le=500)
    status: BookStatus = BookStatus.PLANNED
    image_count: int = Field(default=3, ge=0, le=20)
    layout_config: Optional[dict] = Field(default=None)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    chapters: list[Chapter] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_validator("languages", mode="before")
    @classmethod
    def normalize_languages(cls, v):
        if v is None:
            return ["es"]
        if isinstance(v, str):
            v = [v]
        return [lang.strip().lower() for lang in v if isinstance(lang, str) and lang.strip()]

    @field_validator("subtitle", "description", "author", "target_audience", "genre", mode="before")
    @classmethod
    def blank_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v


class Chapter(BaseModel):
    """Capítulo de un libro."""

    chapter_id: Optional[int] = None
    book_id: int
    number: int = Field(..., ge=1, le=1000)
    title: Optional[str] = Field(default=None, max_length=300)
    objective: Optional[str] = Field(default=None, max_length=2000)
    status: ChapterStatus = ChapterStatus.PLANNED
    research: Optional[str] = Field(default=None)
    sources: list[str] = Field(default_factory=list)
    outline: Optional[str] = Field(default=None)
    draft_es: Optional[str] = Field(default=None)
    draft_en: Optional[str] = Field(default=None)
    edited_es: Optional[str] = Field(default=None)
    edited_en: Optional[str] = Field(default=None)
    images: list[str] = Field(default_factory=list)
    quality_status: Optional[str] = Field(default=None, max_length=100)

    model_config = {"from_attributes": True}

    @field_validator("research", "outline", "draft_es", "draft_en", "edited_es", "edited_en", "quality_status", mode="before")
    @classmethod
    def blank_str_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("sources", "images", mode="before")
    @classmethod
    def normalize_str_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x) for x in v if str(x).strip()]


class Source(BaseModel):
    """Fuente de investigación para un capítulo/libro."""

    source_id: Optional[int] = None
    url: Optional[str] = Field(default=None, max_length=2000)
    title: Optional[str] = Field(default=None, max_length=500)
    publisher: Optional[str] = Field(default=None, max_length=200)
    author: Optional[str] = Field(default=None, max_length=200)
    publication_date: Optional[str] = Field(default=None, max_length=20)
    accessed_at: Optional[datetime] = None
    source_type: SourceType = SourceType.WEB
    relevance: int = Field(default=5, ge=1, le=10)
    notes: Optional[str] = Field(default=None, max_length=5000)
    chapter_ids: list[int] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_validator("chapter_ids", mode="before")
    @classmethod
    def normalize_int_list(cls, v):
        if v is None:
            return []
        if isinstance(v, int):
            v = [v]
        return [int(x) for x in v if isinstance(x, (int, str)) and str(x).strip().isdigit()]

"""Estados explícitos para libros, capítulos y fuentes de Space Lair."""

from __future__ import annotations

from enum import Enum


class BookStatus(str, Enum):
    PLANNED = "planned"
    RESEARCHED = "researched"
    OUTLINED = "outlined"
    DRAFTED = "drafted"
    FACT_CHECKED = "fact_checked"
    EDITED = "edited"
    IMAGES_PLANNED = "images_planned"
    IMAGES_CREATED = "images_created"
    APPROVED = "approved"
    LAYOUT_READY = "layout_ready"
    ERROR = "error"


class ChapterStatus(str, Enum):
    PLANNED = "planned"
    RESEARCHED = "researched"
    OUTLINED = "outlined"
    DRAFTED = "drafted"
    FACT_CHECKED = "fact_checked"
    EDITED = "edited"
    IMAGES_PLANNED = "images_planned"
    IMAGES_CREATED = "images_created"
    APPROVED = "approved"
    LAYOUT_READY = "layout_ready"
    ERROR = "error"


class SourceType(str, Enum):
    WEB = "web"
    BOOK = "book"
    PAPER = "paper"
    DOCUMENT = "document"
    OTHER = "other"


class WebSourceType(str, Enum):
    OFFICIAL = "official"
    ACADEMIC = "academic"
    GOVERNMENT = "government"
    PRIMARY_SOURCE = "primary_source"
    REPUTABLE_MEDIA = "reputable_media"
    SPECIALIST = "specialist"
    SECONDARY = "secondary"

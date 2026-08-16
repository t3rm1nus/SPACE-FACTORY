"""Schemas de validación de payloads con Pydantic.

Define los esquemas esperados para cada capability y proporciona
una función para validar payloads antes de pasarlos al módulo.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class TaskPayload(BaseModel):
    """Payload base para todas las tareas."""
    model_config = {"extra": "allow"}


class CountWordsPayload(TaskPayload):
    """Payload para word_counter (capability: count_words)."""
    text: str = Field(..., min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("El texto no puede estar vacío")
        return v.strip()


class CountWordsOutput(BaseModel):
    """Salida del word_counter (capability: count_words)."""
    word_count: int = Field(..., ge=0)
    char_count: int = Field(..., ge=0)
    char_count_no_spaces: int = Field(..., ge=0)
    avg_word_length: float = Field(..., ge=0.0)


class SummarizePayload(TaskPayload):
    """Payload para text_summarizer (capability: summarize_text)."""
    text: str = Field(..., min_length=1)
    max_words: Optional[int] = Field(default=None, ge=1, le=500)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("El texto no puede estar vacío")
        return v.strip()


class SummarizeOutput(BaseModel):
    """Salida del text_summarizer (capability: summarize_text)."""
    summary: str = Field(..., min_length=1)
    provider: str
    model: Optional[str] = None
    original_length: int = Field(..., ge=0)
    max_words: Optional[int] = None
    tokens_input: int = Field(..., ge=0)
    tokens_output: int = Field(..., ge=0)
    cost: float = Field(..., ge=0.0)


class ReverseTextPayload(TaskPayload):
    """Payload para mcp_demo (capability: reverse_text)."""
    text: str = Field(..., min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("El texto no puede estar vacío")
        return v.strip()


class ExternalToolPayload(TaskPayload):
    """Payload para mcp_external (capability: external_tool)."""
    text: str = Field(..., min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("El texto no puede estar vacío")
        return v.strip()


class BookPlanChapter(BaseModel):
    """Capítulo del plan editorial."""

    number: int = Field(..., ge=1, le=50)
    title: str = Field(..., min_length=1, max_length=300)
    objective: str = Field(..., min_length=1, max_length=2000)
    key_questions: list[str] = Field(default_factory=list)
    estimated_words: int = Field(default=3000, ge=500, le=20000)
    research_requirements: list[str] = Field(default_factory=list)
    image_requirements: int = Field(default=3, ge=0, le=20)
    sections: Optional[list[dict]] = Field(default=None,
        description="Secciones del capítulo: cada una con heading y objective")


class BookPlanPayload(TaskPayload):
    """Payload para book_planner (capability: create_book_plan)."""

    idea: str = Field(..., min_length=1, max_length=5000)
    target_chapters: int = Field(default=30, ge=1, le=40)
    language: str = Field(default="es", min_length=2, max_length=10)
    target_audience: Optional[str] = Field(default=None, max_length=200)
    desired_length: Optional[str] = Field(default=None, max_length=100)
    style: Optional[str] = Field(default=None, max_length=200)
    subject_constraints: Optional[str] = Field(default=None, max_length=5000)


class BookPlanOutput(BaseModel):
    """Salida del plan editorial generado."""

    title: str = Field(..., min_length=1, max_length=500)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    target_audience: Optional[str] = Field(default=None, max_length=200)
    chapters: list[BookPlanChapter] = Field(default_factory=list)


class ChapterWritePayload(TaskPayload):
    """Payload para chapter_writer (capability: write_chapter_es)."""

    book_metadata: dict = Field(..., min_length=1)
    chapter_outline: dict = Field(..., min_length=1)
    research: Optional[str] = Field(default=None, max_length=20000)
    sources: list[dict] = Field(default_factory=list)
    previous_chapter_summaries: list[str] = Field(default_factory=list)
    target_word_count: int = Field(default=3000, ge=500, le=20000)
    minimum_words: Optional[int] = Field(default=None, ge=100, le=50000)
    research_required: bool = Field(default=True)
    style_guide: Optional[str] = Field(default=None, max_length=2000)


class ChapterWriteOutput(BaseModel):
    """Salida del capítulo escrito."""

    chapter_md_path: str = Field(..., min_length=1)
    metadata: dict = Field(..., min_length=1)
    word_count: int = Field(..., ge=1)
    sources_used: list[str] = Field(default_factory=list)
    quality_gate: str = Field(default="PASS", pattern="^(PASS|FAIL)$")
    quality_errors: list[str] = Field(default_factory=list)
    execution_mode: str = Field(default="real", pattern="^(real|fallback|failed)$")


class ChapterWriteEnPayload(TaskPayload):
    """Payload para chapter_writer (capability: write_chapter_en)."""

    book_metadata: dict = Field(..., min_length=1)
    chapter_outline: dict = Field(..., min_length=1)
    research: Optional[str] = Field(default=None, max_length=20000)
    sources: list[dict] = Field(default_factory=list)
    previous_chapter_summaries: list[str] = Field(default_factory=list)
    target_word_count: int = Field(default=3000, ge=500, le=20000)
    minimum_words: Optional[int] = Field(default=None, ge=100, le=50000)
    research_required: bool = Field(default=True)
    style_guide: Optional[str] = Field(default=None, max_length=2000)


class ChapterWriteEnOutput(BaseModel):
    """Salida del capítulo escrito en inglés."""

    chapter_md_path: str = Field(..., min_length=1)
    metadata: dict = Field(..., min_length=1)
    word_count: int = Field(..., ge=1)
    sources_used: list[str] = Field(default_factory=list)
    quality_gate: str = Field(default="PASS", pattern="^(PASS|FAIL)$")
    quality_errors: list[str] = Field(default_factory=list)
    execution_mode: str = Field(default="real", pattern="^(real|fallback|failed)$")


class FactCheckIssue(BaseModel):
    """Problema detectado en una afirmación."""

    claim: str = Field(..., min_length=1)
    severity: str = Field(..., pattern="^(INFO|WARNING|ERROR)$")
    reason: str = Field(..., min_length=1)
    source_url: Optional[str] = Field(default=None)
    suggestion: Optional[str] = Field(default=None)


class ResearchPayload(TaskPayload):
    """Payload para research (capability: research_web)."""

    query: Optional[str] = Field(default=None, max_length=5000)
    topic: Optional[str] = Field(default=None, max_length=5000)
    idea: Optional[str] = Field(default=None, max_length=5000)
    max_sources: int = Field(default=8, ge=1, le=20)
    min_sources: int = Field(default=3, ge=1, le=20)
    timeout: int = Field(default=20, ge=5, le=120)
    research_required: bool = Field(default=True)


class ResearchSource(BaseModel):
    """Fuente devuelta por el research."""

    title: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    source_type: str = Field(default="web_wikipedia")
    content: str = Field(..., min_length=1)
    accessed_at: str = Field(..., min_length=1)


class ResearchOutput(BaseModel):
    """Salida del research."""

    query: str = Field(..., min_length=1)
    status: str = Field(..., pattern="^(PASS|FAIL)$")
    execution_mode: str = Field(..., pattern="^(real|fallback|failed)$")
    sources: list[dict] = Field(default_factory=list)
    stored_sources: list[dict] = Field(default_factory=list)
    source_count: int = Field(..., ge=0)
    error: Optional[str] = Field(default=None)


class FetchUrlPayload(TaskPayload):
    """Payload para research (capability: fetch_url)."""

    url: str = Field(..., min_length=1)
    timeout: int = Field(default=20, ge=5, le=120)


class FetchUrlOutput(BaseModel):
    """Salida de fetch_url."""

    url: str = Field(..., min_length=1)
    status: str = Field(..., pattern="^(PASS|FAIL)$")
    execution_mode: str = Field(..., pattern="^(real|fallback|failed)$")
    content: str = Field(default="")
    error: Optional[str] = Field(default=None)


class ExtractTextPayload(TaskPayload):
    """Payload para research (capability: extract_text)."""

    text: str = Field(..., min_length=1)
    max_chars: int = Field(default=5000, ge=100, le=100000)


class ExtractTextOutput(BaseModel):
    """Salida de extract_text."""

    status: str = Field(..., pattern="^(PASS|FAIL)$")
    execution_mode: str = Field(..., pattern="^(real|fallback|failed)$")
    extracted_text: str = Field(default="")
    error: Optional[str] = Field(default=None)


class FactCheckPayload(TaskPayload):
    """Payload para fact_checker (capability: fact_check_chapter)."""

    chapter_text: str = Field(..., min_length=1)
    sources: list[dict] = Field(default_factory=list)
    target_language: str = Field(default="es", min_length=2, max_length=10)
    research_required: bool = Field(default=True)


class FactCheckOutput(BaseModel):
    """Salida de la verificación de hechos."""

    status: str = Field(..., pattern="^(PASS|WARNING|FAIL)$")
    claims_checked: int = Field(..., ge=0)
    issues: list[FactCheckIssue] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    supported_claims: int = Field(default=0, ge=0)
    conflicting_claims: int = Field(default=0, ge=0)
    quality_gate: str = Field(default="PASS", pattern="^(PASS|FAIL)$")
    execution_mode: str = Field(default="real", pattern="^(real|fallback|failed)$")


class EditorPayload(TaskPayload):
    """Payload para editor (capability: edit_chapter)."""

    chapter_text: str = Field(..., min_length=1)
    style_guide: Optional[str] = Field(default=None, max_length=2000)
    target_language: str = Field(default="es", min_length=2, max_length=10)
    protected_terms: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


class EditorOutput(BaseModel):
    """Salida de la edición editorial de un capítulo."""

    edited_text: str = Field(..., min_length=1)
    editorial_notes: list[str] = Field(default_factory=list)
    changes_summary: list[str] = Field(default_factory=list)
    input_words: int = Field(default=0, ge=0)
    output_words: int = Field(default=0, ge=0)
    placeholder_detected: bool = Field(default=False)
    quality_gate: str = Field(default="PASS", pattern="^(PASS|FAIL)$")
    execution_mode: str = Field(default="real", pattern="^(real|fallback|failed)$")


class TranslatorReviewIssue(BaseModel):
    """Problema detectado en la auditoría post-traducción."""

    issue_type: str = Field(..., min_length=1)
    severity: str = Field(..., pattern="^(INFO|WARNING|ERROR)$")
    description: str = Field(..., min_length=1)


class TranslatorPayload(TaskPayload):
    """Payload para translator (capabilities: translate_es_en / translate_en_es)."""

    source_text: str = Field(..., min_length=1)
    style_guide: Optional[str] = Field(default=None, max_length=2000)
    target_language: Optional[str] = Field(default=None, max_length=10)
    protected_terms: list[str] = Field(default_factory=list)


class TranslatorOutput(BaseModel):
    """Salida de la traducción editorial con auditoría automática."""

    translated_text: str = Field(..., min_length=1)
    review_status: str = Field(..., pattern="^(PASS|WARNING)$")
    review_issues: list[TranslatorReviewIssue] = Field(default_factory=list)
    changes_summary: list[str] = Field(default_factory=list)


class ImageSpec(BaseModel):
    """Especificación de una imagen del plan."""

    image_id: str = Field(..., min_length=1, max_length=50)
    purpose: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1, max_length=2000)
    composition: str = Field(..., min_length=1, max_length=500)
    subject: str = Field(..., min_length=1, max_length=500)
    environment: str = Field(..., min_length=1, max_length=500)
    lighting: str = Field(..., min_length=1, max_length=300)
    visual_style: str = Field(..., min_length=1, max_length=300)
    aspect_ratio: str = Field(..., pattern="^(16:9|3:2|4:3|1:1|2:3|9:16)$")
    prompt: str = Field(..., min_length=1, max_length=3000)
    negative_prompt: str = Field(..., min_length=1, max_length=2000)
    caption: str = Field(..., min_length=1, max_length=500)
    placement: str = Field(..., min_length=1, max_length=200)


class ImagePlanPayload(TaskPayload):
    """Payload para image_planner (capability: create_chapter_image_plan)."""

    chapter_text: str = Field(..., min_length=1)
    chapter_title: Optional[str] = Field(default=None, max_length=300)
    visual_style: Optional[str] = Field(default=None, max_length=300)
    num_images: Optional[int] = Field(default=None, ge=0, le=20)
    language: str = Field(default="es", min_length=2, max_length=10)


class ImagePlanOutput(BaseModel):
    """Salida del plan de imágenes de un capítulo."""

    images: list[ImageSpec] = Field(..., min_length=1)
    visual_style: str = Field(..., min_length=1, max_length=300)
    identity_notes: list[str] = Field(default_factory=list)


class ImageMetadata(BaseModel):
    """Metadatos de una imagen generada."""

    image_id: str = Field(..., min_length=1, max_length=50)
    provider: str = Field(..., min_length=1, max_length=50)
    model: str = Field(..., min_length=1, max_length=120)
    seed: int = Field(..., ge=0)
    width: int = Field(..., ge=64, le=8192)
    height: int = Field(..., ge=64, le=8192)
    steps: int = Field(..., ge=1, le=500)
    aspect_ratio: str = Field(..., min_length=1, max_length=10)
    prompt: str = Field(..., min_length=1, max_length=4000)
    negative_prompt: str = Field(..., min_length=1, max_length=4000)
    image_path: str = Field(..., min_length=1)
    thumbnail_paths: list[str] = Field(default_factory=list)
    status: str = Field(..., pattern="^(ok|error)$")
    attempts: int = Field(..., ge=0)
    error: Optional[str] = Field(default=None, max_length=2000)
    created_at: str = Field(..., min_length=1)
    extra: dict[str, Any] = Field(default_factory=dict)


class ImageGeneratePayload(TaskPayload):
    """Payload para image_generator (capability: generate_image, generate_chapter_images)."""

    image_plan: Optional[dict] = Field(default=None,
        description="Plan de imágenes; si es None/empty, se genera uno simple")
    book_id: int = Field(..., ge=1)
    chapter_number: int = Field(..., ge=1)
    language: str = Field(..., min_length=2, max_length=10)
    chapter_text: Optional[str] = Field(default=None)
    chapter_title: Optional[str] = Field(default=None)
    num_images: Optional[int] = Field(default=None, ge=0, le=20)
    provider: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, max_length=120)
    generate_thumbnails: bool = Field(default=True)
    skip_existing: bool = Field(default=True)
    max_attempts: int = Field(default=3, ge=1, le=10)


class ImageGenerateOutput(BaseModel):
    """Salida de la generación de imágenes."""

    book_id: int = Field(..., ge=1)
    chapter_number: int = Field(..., ge=1)
    language: str = Field(..., min_length=2, max_length=10)
    images_dir: str = Field(..., min_length=1)
    results: list[ImageMetadata]
    requested: int = Field(..., ge=0)
    generated: int = Field(..., ge=0)
    skipped: int = Field(..., ge=0)
    failed: int = Field(..., ge=0)


class BookDocxPayload(TaskPayload):
    """Payload para document_builder (capability: build_book_docx)."""

    book: dict = Field(..., min_length=1)
    language: str = Field(..., min_length=2, max_length=10)
    page_config: Optional[dict] = Field(default=None)


class BookDocxOutput(BaseModel):
    """Salida de la generación del DOCX del libro."""

    docx_path: str = Field(..., min_length=1)
    book_id: int = Field(..., ge=1)
    language: str = Field(..., min_length=2, max_length=10)
    chapter_count: int = Field(..., ge=0)
    image_count: int = Field(..., ge=0)


class BookPdfPayload(TaskPayload):
    """Payload para pdf_builder (capability: build_book_pdf)."""

    book: dict = Field(..., min_length=1)
    language: str = Field(..., min_length=2, max_length=10)
    page_config: Optional[dict] = Field(default=None)


class BookPdfOutput(BaseModel):
    """Salida de la generación del PDF del libro."""

    pdf_path: str = Field(..., min_length=1)
    book_id: int = Field(..., ge=1)
    language: str = Field(..., min_length=2, max_length=10)
    chapter_count: int = Field(..., ge=0)
    image_count: int = Field(..., ge=0)
    warnings: list[str] = Field(default_factory=list)


class QualityControlItem(BaseModel):
    """Resultado individual de una verificación de calidad."""

    status: str = Field(..., pattern="^(PASS|WARNING|FAIL)$")
    message: str = Field(..., min_length=1, max_length=1000)


class QualityControlPayload(TaskPayload):
    """Payload para quality_control (capability: final_quality_control)."""

    book: dict = Field(..., min_length=1)
    docx_path: Optional[str] = Field(default=None)
    pdf_path: Optional[str] = Field(default=None)
    min_chapters: int = Field(default=20, ge=1)
    target_chapters: int = Field(default=30, ge=1)
    max_chapters: int = Field(default=40, ge=1)
    reasonable_page_range: tuple[int, int] = Field(default=(10, 500))


class QualityControlOutput(BaseModel):
    """Salida del control de calidad final."""

    overall_status: str = Field(..., pattern="^(PASS|WARNING|FAIL)$")
    is_complete: bool
    book_checks: list[QualityControlItem] = Field(default_factory=list)
    chapter_checks: list[QualityControlItem] = Field(default_factory=list)
    language_checks: list[QualityControlItem] = Field(default_factory=list)
    source_checks: list[QualityControlItem] = Field(default_factory=list)
    image_checks: list[QualityControlItem] = Field(default_factory=list)
    document_checks: list[QualityControlItem] = Field(default_factory=list)


# Mapeo de capability → esquema de validación
PAYLOAD_SCHEMAS = {
    "count_words": CountWordsPayload,
    "summarize_text": SummarizePayload,
    "reverse_text": ReverseTextPayload,
    "external_tool": ExternalToolPayload,
    "create_book_plan": BookPlanPayload,
    "write_chapter_es": ChapterWritePayload,
    "write_chapter_en": ChapterWriteEnPayload,
    "fact_check_chapter": FactCheckPayload,
    "edit_chapter": EditorPayload,
    "translate_es_en": TranslatorPayload,
    "translate_en_es": TranslatorPayload,
    "create_chapter_image_plan": ImagePlanPayload,
    "generate_image": ImageGeneratePayload,
    "generate_chapter_images": ImageGeneratePayload,
    "build_book_docx": BookDocxPayload,
    "build_book_pdf": BookPdfPayload,
    "final_quality_control": QualityControlPayload,
    "research_web": ResearchPayload,
    "fetch_url": FetchUrlPayload,
    "extract_text": ExtractTextPayload,
}

OUTPUT_SCHEMAS = {
    "count_words": CountWordsOutput,
    "summarize_text": SummarizeOutput,
    "write_chapter_es": ChapterWriteOutput,
    "write_chapter_en": ChapterWriteEnOutput,
    "fact_check_chapter": FactCheckOutput,
    "edit_chapter": EditorOutput,
    "translate_es_en": TranslatorOutput,
    "translate_en_es": TranslatorOutput,
    "create_chapter_image_plan": ImagePlanOutput,
    "generate_image": ImageGenerateOutput,
    "generate_chapter_images": ImageGenerateOutput,
    "build_book_docx": BookDocxOutput,
    "build_book_pdf": BookPdfOutput,
    "final_quality_control": QualityControlOutput,
    "research_web": ResearchOutput,
    "fetch_url": FetchUrlOutput,
    "extract_text": ExtractTextOutput,
}


def validate_payload(capability: str, payload: dict) -> dict:
    """Valida un payload contra el esquema correspondiente a la capability."""
    if capability not in PAYLOAD_SCHEMAS:
        raise ValueError(
            f"No hay esquema de validación para capability '{capability}'. "
            f"Known: {list(PAYLOAD_SCHEMAS.keys())}"
        )
    schema = PAYLOAD_SCHEMAS[capability]
    validated = schema(**payload)
    return validated.model_dump()


# Esquemas de salida opcionales (para validación posterior a la ejecución).
def validate_output(capability: str, data: dict) -> dict:
    """Valida la salida de un módulo contra el esquema registrado (si existe)."""
    schema = OUTPUT_SCHEMAS.get(capability)
    if schema is None:
        return data
    validated = schema(**data)
    return validated.model_dump()
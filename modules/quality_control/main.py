"""Control de calidad final de libros Space Lair."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from docx import Document
from PIL import Image as PILImage
from pypdf import PdfReader

from core.book.book_schema import Book, Chapter
from core.schemas import (
    QualityControlItem,
    QualityControlOutput,
    QualityControlPayload,
)

logger = logging.getLogger(__name__)

_INVENTED_DOMAINS = {
    "example.com",
    "placeholder.com",
    "test.com",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
}


def _check_url(url: Optional[str]) -> bool:
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    if not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return True


def _is_invented_url(url: Optional[str]) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(str(url))
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host in _INVENTED_DOMAINS
    except Exception:
        return False


def _image_has_metadata(image_path: str) -> bool:
    path = Path(image_path)
    candidates = [
        path.parent / "metadata.json",
        path.parent.parent / "metadata.json",
    ]
    # Convención real de persistencia de image_generator E image_search (mismo
    # patrón en ambos): <images_dir>/<image_id>.metadata.json, junto a la propia
    # imagen (p.ej. img_01_web.metadata.json). El glob cubre cualquier image_id.
    candidates += list(path.parent.glob("*.metadata.json"))
    for candidate in candidates:
        if candidate.is_file():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return isinstance(data, dict) and len(data) > 0
            except Exception:
                continue
    try:
        with PILImage.open(path) as img:
            exif = img._getexif() if hasattr(img, "_getexif") else img.info
            return bool(exif)
    except Exception:
        return False


def _check_book(book: Book) -> list[QualityControlItem]:
    checks: list[QualityControlItem] = []
    checks.append(
        QualityControlItem(
            status="PASS" if book.book_id or book.title else "FAIL",
            message="Libro existe" if book.book_id or book.title else "Libro sin identificador ni título",
            origin_phase="book_planner",
        )
    )

    # Metadatos mínimos obligatorios: `title` y `description`. author/genre/
    # target_audience son OPCIONALES en el formulario (frontend) y `create_book`
    # nunca los inventa, por lo que su ausencia NO debe bloquear el Quality Gate.
    # Se bajan a WARNING para conservar visibilidad de qué falta sin romper PASS.
    has_required_metadata = all(
        [
            bool(book.title),
            bool(book.description),
        ]
    )
    optional_missing = [
        label
        for label, value in (
            ("autor", book.author),
            ("género", book.genre),
            ("público objetivo", book.target_audience),
        )
        if not value
    ]
    checks.append(
        QualityControlItem(
            status="PASS" if has_required_metadata else "FAIL",
            message="Metadatos completos" if has_required_metadata else "Metadatos incompletos",
            origin_phase="book_planner",
        )
    )
    if has_required_metadata and optional_missing:
        checks.append(
            QualityControlItem(
                status="WARNING",
                message="Metadatos opcionales ausentes: {}".format(", ".join(optional_missing)),
            )
        )

    checks.append(
        QualityControlItem(
            status="PASS",
            message="Índice presente (se generará a partir de los capítulos)",
        )
    )

    checks.append(
        QualityControlItem(
            status="PASS" if len(book.chapters) > 0 else "FAIL",
            message="Capítulos presentes" if len(book.chapters) > 0 else "Sin capítulos",
            origin_phase="book_planner",
        )
    )

    return checks


def _check_chapters(
    chapters: list[Chapter],
    min_chapters: int,
    target_chapters: int,
    max_chapters: int,
) -> list[QualityControlItem]:
    checks: list[QualityControlItem] = []
    count = len(chapters)

    if count < min_chapters:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Capítulos insuficientes: {} < mínimo {}".format(count, min_chapters),
                origin_phase="book_planner",
            )
        )
    elif count < target_chapters:
        checks.append(
            QualityControlItem(
                status="WARNING",
                message="Capítulos por debajo del objetivo: {} < objetivo {}".format(count, target_chapters),
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Capítulos suficientes: {}".format(count),
            )
        )

    if count > max_chapters:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Capítulos exceden el máximo configurado: {} > máximo {}".format(count, max_chapters),
                origin_phase="book_planner",
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="No excede máximo configurado: {} <= {}".format(count, max_chapters),
            )
        )

    numbers = [ch.number for ch in chapters]
    if len(set(numbers)) != len(numbers):
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Números de capítulo duplicados detectados",
                origin_phase="book_planner",
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Números de capítulo únicos",
            )
        )

    all_numbered = all(isinstance(ch.number, int) and ch.number >= 1 for ch in chapters)
    checks.append(
        QualityControlItem(
            status="PASS" if all_numbered else "FAIL",
            message="Todos los capítulos están numerados" if all_numbered else "Capítulos sin numerar",
            origin_phase="book_planner",
        )
    )

    empty_chapters = [
        ch.number
        for ch in chapters
        if not ch.title
        or not any(
            [
                ch.edited_es,
                ch.draft_es,
                ch.edited_en,
                ch.draft_en,
            ]
        )
    ]
    if empty_chapters:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Capítulos vacíos: {}".format(empty_chapters),
                origin_phase="writer",
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Ningún capítulo vacío",
            )
        )

    return checks


def _check_languages(book: Book) -> list[QualityControlItem]:
    checks: list[QualityControlItem] = []
    languages = [
        lang.strip().lower()
        for lang in book.languages
        if isinstance(lang, str) and lang.strip()
    ]
    if not languages:
        languages = ["es"]

    has_es = "es" in languages
    has_en = "en" in languages

    checks.append(
        QualityControlItem(
            status="PASS" if has_es else "WARNING",
            message="Español solicitado" if has_es else "Español no solicitado",
        )
    )
    checks.append(
        QualityControlItem(
            status="PASS",
            message="Inglés solicitado" if has_en else "Inglés no solicitado",
        )
    )

    chapters = sorted(book.chapters or [], key=lambda c: c.number)

    if has_es:
        missing_es = [
            ch.number
            for ch in chapters
            if not any([ch.edited_es, ch.draft_es])
        ]
        if missing_es:
            checks.append(
                QualityControlItem(
                    status="FAIL",
                    message="Español incompleto en capítulos: {}".format(missing_es),
                    origin_phase="writer",
                )
            )
        else:
            checks.append(
                QualityControlItem(
                    status="PASS",
                    message="Español completo en todos los capítulos",
                )
            )

    if has_en:
        missing_en = [
            ch.number
            for ch in chapters
            if not any([ch.edited_en, ch.draft_en])
        ]
        if missing_en:
            checks.append(
                QualityControlItem(
                    status="FAIL",
                    message="Inglés incompleto en capítulos: {}".format(missing_en),
                    origin_phase="writer",
                )
            )
        else:
            checks.append(
                QualityControlItem(
                    status="PASS",
                    message="Inglés completo en todos los capítulos",
                )
            )

    if has_es and has_en:
        es_chapters = [ch.number for ch in chapters if any([ch.edited_es, ch.draft_es])]
        en_chapters = [ch.number for ch in chapters if any([ch.edited_en, ch.draft_en])]
        if es_chapters != en_chapters:
            checks.append(
                QualityControlItem(
                    status="WARNING",
                    message="Estructuras de idioma no equivalentes",
                )
            )
        else:
            checks.append(
                QualityControlItem(
                    status="PASS",
                    message="Estructuras de idioma equivalentes",
                )
            )

    return checks



def _check_sources(book: Book) -> list[QualityControlItem]:
    checks: list[QualityControlItem] = []
    chapters = sorted(book.chapters or [], key=lambda c: c.number)

    missing_research = [
        ch.number for ch in chapters if not ch.research or not str(ch.research).strip()
    ]
    if missing_research:
        checks.append(
            QualityControlItem(
                status="WARNING",
                message="Investigación faltante en capítulos: {}".format(missing_research),
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Investigación presente en todos los capítulos",
            )
        )

    missing_sources = [ch.number for ch in chapters if not ch.sources]
    if missing_sources:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Fuentes faltantes en capítulos: {}".format(missing_sources),
                origin_phase="research",
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Fuentes presentes en todos los capítulos",
            )
        )

    all_urls = [url for ch in chapters for url in ch.sources if url]
    invalid_urls = [url for url in all_urls if not _check_url(url)]
    if invalid_urls:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="URLs inválidas detectadas: {}".format(invalid_urls[:5]),
                origin_phase="research",
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="URLs válidas",
            )
        )

    invented = [url for url in all_urls if _is_invented_url(url)]
    if invented:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Fuentes inventadas detectadas: {}".format(invented[:5]),
                origin_phase="research",
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Sin fuentes inventadas",
            )
        )

    return checks


def _check_images(book: Book) -> list[QualityControlItem]:
    checks: list[QualityControlItem] = []
    chapters = sorted(book.chapters or [], key=lambda c: c.number)

    # El total esperado por capítulo es el REAL del libro (books.image_count,
    # propagado a Book.image_count con default 3). No un literal fijo.
    expected = max(0, min(int(book.image_count or 3), 20))

    wrong_count = []
    has_images = False
    for ch in chapters:
        if len(ch.images) > 0:
            has_images = True
        if has_images and len(ch.images) != expected:
            wrong_count.append("cap {}: {}".format(ch.number, len(ch.images)))
    if wrong_count:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Imágenes por capítulo != {}: {}".format(expected, wrong_count),
                origin_phase="image_gen",
            )
        )
    elif has_images:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="{} imágenes por capítulo".format(expected),
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Sin imágenes requeridas",
            )
        )

    missing = []
    unreadable = []
    no_metadata = []
    for ch in chapters:
        for img in ch.images:
            if not os.path.isfile(img):
                missing.append(img)
                continue
            try:
                with PILImage.open(img) as im:
                    im.verify()
            except Exception:
                unreadable.append(img)
                continue
            if not _image_has_metadata(img):
                no_metadata.append(img)

    if missing:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Imágenes faltantes: {}".format(missing[:5]),
                origin_phase="image_gen",
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Todos los archivos de imagen existen",
            )
        )

    if unreadable:
        checks.append(
            QualityControlItem(
                status="FAIL",
                message="Imágenes ilegibles: {}".format(unreadable[:5]),
                origin_phase="image_gen",
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Imágenes legibles",
            )
        )

    if no_metadata:
        checks.append(
            QualityControlItem(
                status="WARNING",
                message="Imágenes sin metadata: {}".format(no_metadata[:5]),
            )
        )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="Metadata presente en imágenes",
            )
        )

    return checks




def _check_documents(
    docx_path: Optional[str],
    pdf_path: Optional[str],
    page_range: tuple[int, int],
) -> list[QualityControlItem]:
    checks: list[QualityControlItem] = []

    if docx_path:
        if not os.path.isfile(docx_path):
            checks.append(
                QualityControlItem(
                    status="FAIL",
                    message="DOCX no encontrado: {}".format(docx_path),
                    origin_phase="docx",
                )
            )
        else:
            try:
                doc = Document(docx_path)
                paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
                if not paragraphs:
                    checks.append(
                        QualityControlItem(
                            status="FAIL",
                            message="DOCX vacío",
                            origin_phase="docx",
                        )
                    )
                else:
                    checks.append(
                        QualityControlItem(
                            status="PASS",
                            message="DOCX válido y con contenido",
                        )
                    )
                inline = len(doc.inline_shapes)
                if inline == 0:
                    checks.append(
                        QualityControlItem(
                            status="PASS",
                            message="DOCX sin imágenes embebidas",
                        )
                    )
                else:
                    checks.append(
                        QualityControlItem(
                            status="PASS",
                            message="Imágenes presentes en DOCX: {}".format(inline),
                        )
                    )
                toc_keywords = ["índice", "indice", "tabla de contenido", "contenido"]
                has_toc = any(
                    any(kw in p.lower() for kw in toc_keywords) for p in paragraphs[:20]
                )
                checks.append(
                    QualityControlItem(
                        status="PASS" if has_toc else "WARNING",
                        message="Índice presente en DOCX" if has_toc else "Índice no detectado en DOCX",
                    )
                )
            except Exception as exc:
                checks.append(
                    QualityControlItem(
                        status="FAIL",
                        message="DOCX inválido: {}".format(exc),
                        origin_phase="docx",
                    )
                )
    else:
        checks.append(
            QualityControlItem(
                status="WARNING",
                message="Ruta DOCX no proporcionada",
            )
        )

    if pdf_path:
        if not os.path.isfile(pdf_path):
            checks.append(
                QualityControlItem(
                    status="FAIL",
                    message="PDF no encontrado: {}".format(pdf_path),
                    origin_phase="docx",
                )
            )
        else:
            try:
                reader = PdfReader(pdf_path)
                pages = len(reader.pages)
                if pages == 0:
                    checks.append(
                        QualityControlItem(
                            status="FAIL",
                            message="PDF sin páginas",
                            origin_phase="docx",
                        )
                    )
                else:
                    checks.append(
                        QualityControlItem(
                            status="PASS",
                            message="PDF válido con {} páginas".format(pages),
                        )
                    )
                min_pages, max_pages = page_range
                if pages < min_pages or pages > max_pages:
                    checks.append(
                        QualityControlItem(
                            status="WARNING",
                            message="Número de páginas fuera de rango razonable: {} (esperado {}-{})".format(pages, min_pages, max_pages),
                        )
                    )
                else:
                    checks.append(
                        QualityControlItem(
                            status="PASS",
                            message="Número de páginas razonable",
                        )
                    )
            except Exception as exc:
                checks.append(
                    QualityControlItem(
                        status="FAIL",
                        message="PDF inválido: {}".format(exc),
                        origin_phase="docx",
                    )
                )
    else:
        checks.append(
            QualityControlItem(
                status="PASS",
                message="PDF no requerido",
            )
        )

    return checks


def final_quality_control(payload: dict[str, Any]) -> dict[str, Any]:
    validated = QualityControlPayload(**payload)
    book = Book.model_validate(validated.book)

    book_checks = _check_book(book)
    chapter_checks = _check_chapters(
        book.chapters,
        validated.min_chapters,
        validated.target_chapters,
        validated.max_chapters,
    )
    language_checks = _check_languages(book)
    source_checks = _check_sources(book)
    image_checks = _check_images(book)
    document_checks = _check_documents(
        validated.docx_path, validated.pdf_path, validated.reasonable_page_range
    )

    all_items = (
        book_checks
        + chapter_checks
        + language_checks
        + source_checks
        + image_checks
        + document_checks
    )
    if any(item.status == "FAIL" for item in all_items):
        overall_status = "FAIL"
    elif any(item.status == "WARNING" for item in all_items):
        overall_status = "WARNING"
    else:
        overall_status = "PASS"

    output = QualityControlOutput(
        overall_status=overall_status,
        is_complete=overall_status != "FAIL",
        book_checks=book_checks,
        chapter_checks=chapter_checks,
        language_checks=language_checks,
        source_checks=source_checks,
        image_checks=image_checks,
        document_checks=document_checks,
    )
    return output.model_dump()


def health_check() -> dict[str, Any]:
    try:
        import docx  # noqa: F401
        import pypdf  # noqa: F401
        from PIL import Image  # noqa: F401

        return {
            "healthy": True,
            "dependencies": {
                "python-docx": "ok",
                "pypdf": "ok",
                "Pillow": "ok",
            },
        }
    except Exception as exc:
        return {"healthy": False, "error": str(exc)}


def execute(payload: dict, capability: str = "final_quality_control") -> dict:
    """Wrapper de ejecución: control de calidad final del libro.

    Delega en final_quality_control(payload).
    """
    return final_quality_control(payload)




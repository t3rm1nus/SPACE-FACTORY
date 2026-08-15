"""Generacion de libros PDF profesionales a partir del modelo editorial."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from fpdf import FPDF
from PIL import Image as PILImage
from pypdf import PdfReader

from core.book.book_schema import Book, Chapter
from core.schemas import BookPdfOutput, BookPdfPayload

logger = logging.getLogger(__name__)
DEFAULT_PAGE_SIZE = "A4"
DEFAULT_MARGINS_MM = {"top": 25.4, "bottom": 25.4, "left": 25.4, "right": 25.4}
PAGE_SIZES_MM = {"A4": (210.0, 297.0), "LETTER": (215.9, 279.4), "LEGAL": (215.9, 355.6)}


class BookPDF(FPDF):
    def __init__(self, book, language, page_config, warnings):
        super().__init__()
        self.book = book
        self.language = language
        self.warnings = warnings
        self.page_config = page_config
        self._apply_page_config()

    def _apply_page_config(self):
        size_name = DEFAULT_PAGE_SIZE
        width_mm = None
        height_mm = None
        margins = DEFAULT_MARGINS_MM
        if self.page_config:
            size_name = self.page_config.get("size", DEFAULT_PAGE_SIZE)
            width_mm = self.page_config.get("width_mm")
            height_mm = self.page_config.get("height_mm")
            margins = self.page_config.get("margins_mm", DEFAULT_MARGINS_MM)
        if width_mm and height_mm:
            self.w = float(width_mm)
            self.h = float(height_mm)
        else:
            w, h = PAGE_SIZES_MM.get(size_name.upper(), PAGE_SIZES_MM["A4"])
            self.w = w
            self.h = h
        self.set_auto_page_break(True, margin=float(margins.get("bottom", 25.4)))
        self.set_margins(
            float(margins.get("left", 25.4)),
            float(margins.get("top", 25.4)),
            float(margins.get("right", 25.4)),
        )
        self.add_page()
        self.alias_nb_pages()

    @property
    def _avail_w(self) -> float:
        return self.w - self.l_margin - self.r_margin

    def _start_line(self) -> None:
        """Reset X to the left margin so multi_cell width is computed correctly."""
        self.set_x(self.l_margin)

    def _overflow(self, text, font_size=11):
        self.set_font("Helvetica", "", font_size)
        tw = self.get_string_width(text)
        if tw > self._avail_w:
            self.warnings.append(
                f"Posible texto desbordado ({tw:.1f}mm > {self._avail_w:.1f}mm): {text[:80]}..."
            )
            return True
        return False

    def _ensure_new_page(self):
        if self.get_y() > 20:
            self.add_page()

    def _avoid_empty_page(self):
        if self.get_y() < 10 and self.page_no() > 1:
            self.warnings.append("Pagina vacia detectada tras salto de capitulo.")

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, self.book.title or "", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 10, f"Pagina {self.page_no()}", align="C", new_x="RIGHT", new_y="LAST")

    def add_cover(self):
        self.set_font("Helvetica", "B", 28)
        self.set_text_color(20, 20, 20)
        self.ln(60)
        self._start_line()
        self.multi_cell(self._avail_w, 12, self.book.title or "Sin titulo", align="C")
        self.ln(8)
        if self.book.subtitle:
            self.set_font("Helvetica", "", 18)
            self._start_line()
            self.multi_cell(self._avail_w, 10, self.book.subtitle, align="C")
            self.ln(8)
        if self.book.author:
            self.set_font("Helvetica", "", 14)
            self._start_line()
            self.multi_cell(self._avail_w, 8, self.book.author, align="C")

    def add_legal(self):
        self._ensure_new_page()
        self.set_font("Helvetica", "", 10)
        self.set_text_color(40, 40, 40)
        year = self.book.created_at.year if self.book.created_at else 2024
        author = self.book.author or "Autor"
        title = self.book.title or "Sin titulo"
        legal = (
            f"Titulo: {title}\n"
            f"Autor: {author}\n"
            f"(c) {year} {author}. Todos los derechos reservados.\n"
            "Queda prohibida la reproduccion total o parcial de esta obra, "
            "por cualquier medio o procedimiento, sin permiso expreso del autor."
        )
        self._start_line()
        self.multi_cell(self._avail_w, 6, legal, align="L")


    def add_toc(self, chapters):
        self._ensure_new_page()
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(20, 20, 20)
        self.cell(0, 10, "Indice", new_x="LMARGIN", new_y="NEXT")
        self.ln(4)
        for ch in chapters:
            self.set_font("Helvetica", "", 11)
            title = ch.title or f"Capítulo {ch.number}"
            text = f"Capítulo {ch.number}: {title}"
            self._overflow(text)
            self._start_line()
            self.multi_cell(self._avail_w, 8, text, new_x="LMARGIN", new_y="NEXT", align="L")
            self.set_x(self.l_margin)

    def add_introduction(self):
        if not self.book.description:
            return
        self._ensure_new_page()
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(20, 20, 20)
        self.cell(0, 10, "Introduccion", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)
        self.set_font("Helvetica", "", 11)
        self.set_text_color(30, 30, 30)
        self._write_markdown(self.book.description)

    def add_chapter(self, chapter):
        self._ensure_new_page()
        self._avoid_empty_page()
        title = chapter.title or f"Capítulo {chapter.number}"
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(20, 20, 20)
        self._overflow(title, font_size=16)
        self._start_line()
        self.multi_cell(self._avail_w, 10, title, new_x="LMARGIN", new_y="NEXT", align="L")
        self.ln(2)
        content = getattr(chapter, f"edited_{self.language}") or getattr(chapter, f"draft_{self.language}")
        if content:
            self.set_font("Helvetica", "", 11)
            self.set_text_color(30, 30, 30)
            self._write_markdown(content)
        else:
            self.set_font("Helvetica", "", 11)
            self._start_line()
            self.multi_cell(self._avail_w, 8, "(Sin contenido disponible para este capitulo)", align="L")
        for idx, img_path in enumerate(chapter.images, start=1):
            caption = f"Figura {idx}: {chapter.title or f'Capítulo {chapter.number}'}"
            self._add_image(img_path, caption)


    def _write_markdown(self, text):
        lines = text.splitlines()
        bullet = False
        for line in lines:
            stripped = line.rstrip()
            if not stripped.strip():
                if bullet:
                    self.ln(3)
                    bullet = False
                continue
            s = stripped.strip()
            if s.startswith("# ") or s.startswith("## ") or s.startswith("### "):
                self._render_heading(s)
            elif s.startswith("- ") or s.startswith("* "):
                self._render_bullet(s[2:])
                bullet = True
            else:
                if bullet:
                    self.ln(2)
                    bullet = False
                self._overflow(s, font_size=11)
                self._start_line()
                self.multi_cell(self._avail_w, 6, s, align="L")
                self.set_x(self.l_margin)

    def _render_heading(self, line):
        if line.startswith("# "):
            text = line[2:].strip()
            size = 14
        elif line.startswith("## "):
            text = line[3:].strip()
            size = 13
        else:
            text = line[4:].strip()
            size = 12
        self.set_font("Helvetica", "B", size)
        self._overflow(text, font_size=size)
        self._start_line()
        self.multi_cell(self._avail_w, 8, text, new_x="LMARGIN", new_y="NEXT", align="L")
        self.ln(1)
        self.set_x(self.l_margin)

    def _render_bullet(self, text):
        self.set_font("Helvetica", "", 11)
        self._overflow(text, font_size=11)
        self._start_line()
        self.cell(5, 6, "*", new_x="RIGHT", new_y="TOP")
        self.multi_cell(self._avail_w - 5, 6, text, align="L")
        self.set_x(self.l_margin)

    def _add_image(self, path, caption):
        if not path or not os.path.isfile(path):
            logger.warning("Imagen no encontrada, se omite: %s", path)
            self.warnings.append(f"Imagen no encontrada: {path}")
            return
        try:
            with PILImage.open(path) as img:
                img_w, img_h = img.size
        except Exception as exc:
            logger.warning("No se pudo abrir la imagen %s: %s", path, exc)
            self.warnings.append(f"No se pudo abrir imagen: {path}")
            return
        avail_w = self._avail_w
        avail_h = self.h - self.t_margin - self.b_margin - self.get_y() - 10
        if avail_w <= 0 or avail_h <= 0:
            self.warnings.append(f"Imagen cortada por falta de espacio: {path}")
            return
        ratio = min(avail_w / img_w, avail_h / img_h)
        display_w = img_w * ratio
        display_h = img_h * ratio
        if ratio < 1.0:
            self.warnings.append(f"Imagen escalada por espacio limitado: {path}")
        x = (self.w - display_w) / 2
        y = self.get_y()
        self.image(path, x=x, y=y, w=display_w, h=display_h)
        self.set_y(y + display_h + 2)
        self.set_font("Helvetica", "I", 10)
        self.set_text_color(80, 80, 80)
        self._overflow(caption, font_size=10)
        self._start_line()
        self.multi_cell(self._avail_w, 6, caption, align="C")
        self.set_x(self.l_margin)



def build_book_pdf(payload: dict[str, Any]) -> dict[str, Any]:
    validated = BookPdfPayload(**payload)
    book = Book.model_validate(validated.book)
    language = validated.language
    page_config = validated.page_config or {}
    warnings: list[str] = []
    output_dir = Path("output") / "pdf"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"book_{language}.pdf"
    pdf_path = str(output_dir / filename)
    pdf = BookPDF(book, language, page_config, warnings)
    chapters = sorted(book.chapters or [], key=lambda c: c.number)
    pdf.add_cover()
    pdf.add_legal()
    pdf.add_toc(chapters)
    pdf.add_introduction()
    for ch in chapters:
        pdf.add_chapter(ch)
    pdf.output(pdf_path)
    try:
        reader = PdfReader(pdf_path)
        if len(reader.pages) == 0:
            warnings.append("El PDF generado no contiene paginas.")
    except Exception as exc:
        warnings.append(f"No se pudo validar el PDF: {exc}")
    return BookPdfOutput(
        pdf_path=pdf_path, book_id=book.book_id or 0, language=language,
        chapter_count=len(chapters),
        image_count=sum(len(ch.images) for ch in chapters),
        warnings=warnings,
    ).model_dump()


def health_check() -> dict[str, Any]:
    try:
        import fpdf  # noqa: F401
        import pypdf  # noqa: F401
        return {"healthy": True, "dependencies": {"fpdf2": "ok", "pypdf": "ok"}}
    except Exception as exc:
        return {"healthy": False, "error": str(exc), "dependencies": {"fpdf2": "error", "pypdf": "error"}}


def execute(payload: dict, capability: str = "build_book_pdf") -> dict:
    """Wrapper de ejecución: genera el PDF del libro.

    Delega en build_book_pdf(payload).
    """
    return build_book_pdf(payload)



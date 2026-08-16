"""Lógica del panel editorial: construye payloads por fase y arma el libro DOCX.

Este módulo centraliza la construcción de payloads para cada capability del
pipeline editorial usando los schemas reales del proyecto (core/schemas.py).
El frontend solo envía datos de formulario; aquí se montan los payloads
completos a partir del estado real de libros/capítulos en la BD.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from core.database import get_db, init_db
from core.book.source_manager import SourceManager

init_db()


# Definición del pipeline editorial (orden, capability real y fase por capítulo)
PIPELINE = [
    {"id": "planner", "capability": "create_book_plan", "label": "BOOK PLANNER", "per_chapter": False},
    {"id": "research", "capability": "research_web", "label": "RESEARCH", "per_chapter": False},
    {"id": "outline", "capability": "create_book_plan", "label": "OUTLINE", "per_chapter": False},
    {"id": "writer", "capability": "write_chapter_es", "label": "CHAPTER WRITER", "per_chapter": True},
    {"id": "fact_check", "capability": "fact_check_chapter", "label": "FACT CHECK", "per_chapter": True},
    {"id": "editor", "capability": "edit_chapter", "label": "EDITOR", "per_chapter": True},
    {"id": "image_plan", "capability": "create_chapter_image_plan", "label": "IMAGE PLAN", "per_chapter": True},
    {"id": "image_gen", "capability": "generate_chapter_images", "label": "IMAGE GENERATOR", "per_chapter": True},
    {"id": "docx", "capability": "build_book_docx", "label": "DOCUMENT BUILDER", "per_chapter": False},
]

# Fases que solo son válidas sobre un capítulo concreto
CHAPTER_PHASES = {p["id"] for p in PIPELINE if p["per_chapter"]}


def _get_book(book_id: int) -> Optional[dict]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_chapters(book_id: int) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY number", (book_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_chapter(book_id: int, chapter_id: Optional[int]) -> Optional[dict]:
    if chapter_id is None:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM chapters WHERE id = ? AND book_id = ?",
            (chapter_id, book_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_chapters(book_id: int) -> list[dict]:
    """Devuelve los capítulos reales del libro (determinista, por número).

    Reutiliza la consulta existente; helper público para el Autopilot.
    """
    return _get_chapters(book_id)


# Campos de texto persistibles por fase per-capítulo (resultado real de writer/editor).
_PERSISTABLE_TEXT_FIELDS = ("draft_es", "draft_en", "edited_es", "edited_en")


def persist_chapter_result(
    book_id: int, chapter_id: int, field: str, text: str
) -> dict:
    """Persiste el resultado real de una fase en un capítulo.

    - localiza el capítulo real por (book_id, chapter_id);
    - escribe únicamente ``field`` conservando el resto de columnas;
    - no trunca contenido;
    - devuelve evidencia del cambio.
    """
    if field not in _PERSISTABLE_TEXT_FIELDS:
        raise ValueError(f"Campo no persistible: {field}")
    text = text or ""
    conn = get_db()
    try:
        cur = conn.execute(
            f"UPDATE chapters SET {field} = ?, updated_at = datetime('now') "
            f"WHERE id = ? AND book_id = ?",
            (text, chapter_id, book_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {
                "updated": False,
                "reason": "chapter_not_found",
                "book_id": book_id,
                "chapter_id": chapter_id,
                "field": field,
            }
        return {
            "updated": True,
            "book_id": book_id,
            "chapter_id": chapter_id,
            "field": field,
            "chars": len(text),
            "words": len(text.split()),
        }
    finally:
        conn.close()


def update_chapter_title(book_id: int, number: int, title: str) -> dict:
    """Actualiza el título de un capítulo en BD por book_id + number.

    Usado por autopilot tras planner PASS para propagar títulos reales.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE chapters SET title = ?, updated_at = datetime('now') "
            "WHERE book_id = ? AND number = ?",
            (title, book_id, number),
        )
        conn.commit()
        return {
            "updated": cur.rowcount > 0,
            "book_id": book_id,
            "number": number,
            "title": title,
        }
    finally:
        conn.close()


def update_book_description(book_id: int, description: str) -> dict:
    """Actualiza la descripción de un libro en BD.

    Usado por autopilot tras planner PASS para propagar descripción real.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE books SET description = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (description, book_id),
        )
        conn.commit()
        return {
            "updated": cur.rowcount > 0,
            "book_id": book_id,
            "description": description[:80],
        }
    finally:
        conn.close()


def update_book_title(book_id: int, title: str) -> dict:
    """Actualiza el título de un libro en BD.

    Usado por autopilot tras planner PASS para propagar título generado.
    """
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE books SET title = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (title, book_id),
        )
        conn.commit()
        return {
            "updated": cur.rowcount > 0,
            "book_id": book_id,
            "title": title,
        }
    finally:
        conn.close()


def update_chapter_outline(book_id: int, number: int, sections: list[dict]) -> dict:
    """Persiste el outline (secciones) de un capítulo en BD.

    Serializa sections como JSON en chapters.outline.
    """
    import json
    conn = get_db()
    try:
        outline_json = json.dumps(sections, ensure_ascii=False)
        cur = conn.execute(
            "UPDATE chapters SET outline = ?, updated_at = datetime('now') "
            "WHERE book_id = ? AND number = ?",
            (outline_json, book_id, number),
        )
        conn.commit()
        return {
            "updated": cur.rowcount > 0,
            "book_id": book_id,
            "number": number,
            "sections_count": len(sections),
        }
    finally:
        conn.close()

def persist_chapter_images(book_id: int, chapter_id: int, image_paths: list[str]) -> dict:
    """Persiste las rutas de imágenes generadas a chapters.images.

    Serializa la lista como JSON. Usado por autopilot tras image_gen PASS.
    """
    import json
    conn = get_db()
    try:
        images_json = json.dumps(image_paths, ensure_ascii=False)
        cur = conn.execute(
            "UPDATE chapters SET images = ?, updated_at = datetime('now') "
            "WHERE id = ? AND book_id = ?",
            (images_json, chapter_id, book_id),
        )
        conn.commit()
        return {
            "updated": cur.rowcount > 0,
            "book_id": book_id,
            "chapter_id": chapter_id,
            "images_count": len(image_paths),
        }
    finally:
        conn.close()

def persist_chapter_sources(book_id: int, chapter_id: int, sources: list[str]) -> dict:
    """Persiste las URLs de fuentes asociadas a chapters.sources (JSON).

    Serializa la lista como JSON. Usado por el autopilot tras writer/writer_en PASS
    para poblar `chapters.sources` a partir de SourceManager (fuente de verdad única:
    `sources.chapter_ids`). Una vez por capítulo, en el momento del writer.
    """
    sources_json = json.dumps(sources, ensure_ascii=False)
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE chapters SET sources = ?, updated_at = datetime('now') "
            "WHERE id = ? AND book_id = ?",
            (sources_json, chapter_id, book_id),
        )
        conn.commit()
        return {
            "updated": cur.rowcount > 0,
            "book_id": book_id,
            "chapter_id": chapter_id,
            "sources_count": len(sources),
        }
    finally:
        conn.close()

def _latest_task(book_id: int, capability: str) -> Optional[dict]:
    """Devuelve la tarea más reciente de una capability asociada al libro."""
    from core.task_queue import all_tasks

    tasks = all_tasks()
    for t in tasks:
        if t.get("capability") != capability:
            continue
        try:
            payload = json.loads(t.get("payload") or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if str(payload.get("book_id")) == str(book_id):
            return t
    return None


def _chapters_text(chapters: list[dict]) -> str:
    parts = []
    for c in chapters:
        txt = (c.get("edited_es") or c.get("draft_es") or "").strip()
        if txt:
            parts.append(f"## {c.get('title') or ('Capítulo ' + str(c.get('number')))}\n{txt}")
    return "\n\n".join(parts)
def create_book(data: dict) -> dict:
    """Crea un libro y sus capítulos vacíos."""
    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("Se requiere 'title'")

    # Normalizado de metadata para satisfacer el contrato del Quality Gate
    # (_check_book exige title/author/description/genre/target_audience no vacíos).
    # - description: legítimamente derivable de `idea` si no se proporciona.
    # - author/genre/target_audience: NUNCA se inventan; se conserva el valor
    #   explícito del creador o queda None (la UI añade los campos reales).
    description = (data.get("description") or data.get("idea") or "").strip() or None
    subtitle = (data.get("subtitle") or "").strip() or None
    author = (data.get("author") or "").strip() or None
    target_audience = (data.get("target_audience") or "").strip() or None
    genre = (data.get("genre") or "").strip() or None

    chapters_count = int(data.get("target_chapters") or 1)
    chapters_count = max(1, min(50, chapters_count))
    image_count = data.get("image_count")
    image_count = 3 if image_count is None else int(image_count)
    image_count = max(0, min(20, image_count))
    layout_config = data.get("layout_config")
    layout_config_json = json.dumps(layout_config, ensure_ascii=False) if layout_config else None

    conn = get_db()
    try:
        cursor = conn.execute(
            """
            INSERT INTO books (title, subtitle, description, author, target_audience, genre, languages, target_chapters, image_count, layout_config, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned')
            """,
            (
                title,
                subtitle,
                description,
                author,
                target_audience,
                genre,
                data.get("language") or "es",
                chapters_count,
                image_count,
                layout_config_json,
            ),
        )
        book_id = cursor.lastrowid
        for n in range(1, chapters_count + 1):
            conn.execute(
                "INSERT INTO chapters (book_id, number, title) VALUES (?, ?, ?)",
                (book_id, n, f"Capítulo {n}"),
            )
        conn.commit()
        return {"book_id": book_id, "status": "planned", "chapters": chapters_count, "image_count": image_count}
    finally:
        conn.close()


def load_book(book_id: int) -> dict:
    """Devuelve el libro con sus capítulos y estado agregado."""
    book = _get_book(book_id)
    if book is None:
        raise ValueError(f"Libro {book_id} no encontrado")

    chapters = _get_chapters(book_id)

    done_ch = 0
    editing_ch = 0
    pending_ch = 0
    total_words = 0
    for c in chapters:
        edited = (c.get("edited_es") or "").strip()
        draft = (c.get("draft_es") or "").strip()
        total_words += len(edited.split()) if edited else len(draft.split())
        if c.get("quality_status") == "done" or edited:
            done_ch += 1
        elif draft or edited:
            editing_ch += 1
        else:
            pending_ch += 1

    total_images = 0
    for c in chapters:
        try:
            total_images += len(json.loads(c.get("images") or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass

    docx_task = _latest_task(book_id, "build_book_docx")

    progress = 0
    if chapters:
        progress = int((done_ch / len(chapters)) * 100)

    docx_path = None
    if docx_task and docx_task.get("result"):
        try:
            docx_path = json.loads(docx_task["result"]).get("docx_path")
        except (json.JSONDecodeError, TypeError):
            docx_path = None

    return {
        "book": {
            "id": book["id"],
            "title": book["title"],
            "subtitle": book["subtitle"],
            "author": book["author"],
            "description": book["description"],
            "genre": book["genre"],
            "languages": book["languages"],
            "target_audience": book["target_audience"],
            "target_chapters": book["target_chapters"],
            "image_count": book["image_count"] if book.get("image_count") is not None else 3,
            "layout_config": _parse_layout_config(book.get("layout_config")),
            "status": book["status"],
        },
        "chapters": [
            {
                "id": c["id"],
                "number": c["number"],
                "title": c["title"],
                "status": c["status"],
                "quality_status": c.get("quality_status"),
                "has_draft": bool((c.get("draft_es") or "").strip()),
                "has_edited": bool((c.get("edited_es") or "").strip()),
                "image_count": len(json.loads(c.get("images") or "[]"))
                if (c.get("images") or "").strip()
                else 0,
                "word_count": len((c.get("draft_es") or "").split()),
            }
            for c in chapters
        ],
        "stats": {
            "total_chapters": len(chapters),
            "done": done_ch,
            "editing": editing_ch,
            "pending": pending_ch,
            "total_words": total_words,
            "total_images": total_images,
            "progress": progress,
            "docx_ready": bool(docx_task and docx_task.get("status") == "done"),
            "docx_path": docx_path,
        },
    }
def build_payload(book_id: int, phase_id: str, data: dict, chapter_id: Optional[int] = None) -> dict:
    """Construye el payload de una fase según el schema real de la capability."""
    book = _get_book(book_id)
    if book is None:
        raise ValueError(f"Libro {book_id} no encontrado")

    chapters = _get_chapters(book_id)
    chapter = _get_chapter(book_id, chapter_id) if chapter_id else None

    language = book.get("languages") or data.get("language") or "es"

    # book_id siempre incluido para poder asociar tarea -> libro
    base = {"book_id": book_id}

    if phase_id == "planner":
        payload = {
            "idea": data.get("idea") or book.get("description") or book.get("title") or "",
            "target_chapters": int(data.get("target_chapters") or book.get("target_chapters") or 1),
            "language": language,
            "target_audience": data.get("target_audience") or book.get("target_audience"),
            "desired_length": data.get("desired_length"),
            "style": data.get("style"),
            "subject_constraints": data.get("subject_constraints"),
            "image_count": int(data.get("num_images") or book.get("image_count") or 3),
        }
    elif phase_id == "research":
        payload = {
            "query": data.get("query") or data.get("idea") or book.get("title"),
            "topic": data.get("topic") or book.get("title"),
            "idea": data.get("idea") or book.get("description"),
            "max_sources": int(data.get("max_sources") or 8),
            "min_sources": int(data.get("min_sources") or 3),
            "timeout": int(data.get("timeout") or 20),
            "research_required": True,
        }
    elif phase_id == "outline":
        payload = {
            "idea": data.get("idea") or book.get("title"),
            "target_chapters": int(data.get("target_chapters") or book.get("target_chapters") or 1),
            "language": language,
            "target_audience": book.get("target_audience"),
            "desired_length": data.get("desired_length") or "3000 palabras",
            "style": data.get("style") or "Divulgativo, riguroso y claro",
            "subject_constraints": book.get("description"),
            "image_count": int(data.get("num_images") or book.get("image_count") or 3),
        }
    elif phase_id in ("writer", "writer_en"):
        if chapter is None:
            raise ValueError("Se requiere un capítulo para write_chapter")
        # Leer outline persistido del planner (chapters.outline)
        outline_sections = []
        try:
            raw_outline = chapter.get("outline") or ""
            if raw_outline.strip():
                parsed = json.loads(raw_outline)
                if isinstance(parsed, list):
                    outline_sections = parsed
                elif isinstance(parsed, dict):
                    outline_sections = parsed.get("sections", []) or []
        except (json.JSONDecodeError, TypeError, ValueError):
            outline_sections = []
        payload = {
            "book_metadata": {
                "book_id": book_id,
                "title": book.get("title"),
                "language": language,
            },
            "chapter_outline": {
                "title": chapter.get("title") or f"Capítulo {chapter.get('number')}",
                "number": chapter.get("number"),
                "objective": chapter.get("objective"),
                "sections": outline_sections,
            },
                                    "research": data.get("research"),
            "sources": data.get("sources") or [],  # propagación REAL de Research (job data)
            "previous_chapter_summaries": [],
            "target_word_count": int(data.get("target_words") or 3000),
            "research_required": True,
            "style_guide": data.get("style_guide") or "Divulgativo, riguroso y claro",
        }
    elif phase_id == "fact_check":
        if chapter is None:
            raise ValueError("Se requiere un capítulo para fact_check")
        text = (chapter.get("edited_es") or chapter.get("draft_es") or "").strip()
        if not text:
            raise ValueError("El capítulo no tiene texto para verificar")
        payload = {
            "chapter_text": text,
            "sources": data.get("sources") or [],
            "target_language": language,
            "research_required": True,
        }
    elif phase_id == "editor":
        if chapter is None:
            raise ValueError("Se requiere un capítulo para edit")
        text = (chapter.get("draft_es") or "").strip()
        if not text:
            raise ValueError("El capítulo no tiene borrador que editar")
        payload = {
            "chapter_text": text,
            "style_guide": data.get("style_guide") or "Divulgativo, riguroso y claro",
            "target_language": language,
        }
    elif phase_id == "image_plan":
        if chapter is None:
            raise ValueError("Se requiere un capítulo para image_plan")
        text = (chapter.get("edited_es") or chapter.get("draft_es") or "").strip()
        payload = {
            "chapter_text": text or "Capítulo sin texto todavía.",
            "chapter_title": chapter.get("title"),
            "visual_style": data.get("style") or "realistic",
            "num_images": int(data.get("num_images") if data.get("num_images") is not None else 3),
            "language": language,
        }
    elif phase_id == "image_gen":
        if chapter is None:
            raise ValueError("Se requiere un capítulo para generate image")
        text = (chapter.get("edited_es") or chapter.get("draft_es") or "").strip()
        payload = {
            "book_id": book_id,
            "chapter_number": int(chapter.get("number") or 1),
            "chapter_text": text or "Capítulo sin texto todavía.",
            "chapter_title": chapter.get("title"),
            "num_images": int(data.get("num_images") if data.get("num_images") is not None else 3),
            "language": language,
            "provider": data.get("provider"),
            "model": data.get("model"),
            "image_plan": data.get("image_plan") or {},
            "generate_thumbnails": True,
            "skip_existing": True,
            "max_attempts": 3,
        }
    elif phase_id == "docx":
        payload = {
            "book": _build_book_dict(book, chapters),
            "language": language,
            "page_config": data.get("page_config") or {
                "size": "A4",
                "margins_mm": {"top": 25.4, "bottom": 25.4, "left": 25.4, "right": 25.4},
            },
        }
    else:
        raise ValueError(f"Phase desconocida: {phase_id}")

    # Asegura que 'book_id' esté presente para vincular tarea -> libro
    base.update(payload)
    return base


def _chapter_source_urls(chapter_id: Optional[int]) -> list[str]:
    """Devuelve las URLs de fuentes REALMENTE asociadas al capítulo.

    Fuente de verdad única: `sources.chapter_ids` (SourceManager.get_chapter_sources).
    - chapter_id None / sin asociaciones -> [] (nunca inventa).
    - Sólo devuelve URLs no vacías que provienen de asociaciones REALMENTE persistidas.
    """
    if not chapter_id:
        return []
    try:
        associated = SourceManager.get_chapter_sources(chapter_id) or []
    except Exception:
        # Si la tabla/consulta no está disponible, no fabricamos fuentes.
        return []
    return [s.get("url") for s in associated if s.get("url")]


def _parse_layout_config(raw: Any) -> Optional[dict]:
    """Devuelve layout_config como dict desde su forma persistida (JSON string)."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _build_book_dict(book: dict, chapters: list[dict]) -> dict:
    """Arma el dict de libro requerido por build_book_docx y build_book_pdf."""
    return {
        "book_id": book["id"],
        "title": book.get("title"),
        "subtitle": book.get("subtitle"),
        "description": book.get("description"),
        "author": book.get("author"),
        "target_audience": book.get("target_audience"),
        "genre": book.get("genre"),
        "languages": [book.get("languages") or "es"],
        "target_chapters": book.get("target_chapters"),
        "image_count": 3 if book.get("image_count") is None else int(book.get("image_count")),
        "layout_config": _parse_layout_config(book.get("layout_config")),
        "status": book.get("status"),
        "chapters": [
            {
                "chapter_id": c["id"],
                "book_id": book["id"],
                "number": c.get("number"),
                "title": c.get("title"),
                                                "edited_es": c.get("edited_es") or c.get("draft_es"),
                "draft_es": c.get("draft_es"),
                "images": json.loads(c.get("images") or "[]"),
                # sources: fuente de verdad úNICAMENTE las asociaciones REALMENTE
                # persistidas por SourceManager en `sources.chapter_ids`. Nunca
                # `job.data.sources` globales. Si no hay asociación => []. No inventa.
                "sources": _chapter_source_urls(c.get("id")),
            }
            for c in chapters
            if (c.get("edited_es") or c.get("draft_es") or "").strip()
        ],
    }


def phase_capability(phase_id: str) -> str:
    for p in PIPELINE:
        if p["id"] == phase_id:
            return p["capability"]
    raise ValueError(f"Phase desconocida: {phase_id}")
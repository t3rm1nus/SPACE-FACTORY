"""Gestor de fuentes de Space Lair."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Optional

from core.database import get_db
from core.logger import get_logger, log

logger = get_logger(__name__)


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.utcnow().isoformat(sep=" ", timespec="seconds")


def _row_to_source(row: Any) -> Optional[dict[str, Any]]:
    if not row:
        return None
    data = dict(row)
    data["chapter_ids"] = json.loads(data.get("chapter_ids") or "[]")
    return data


class SourceManager:
    @staticmethod
    def add_source(
        url: str,
        title: Optional[str] = None,
        publisher: Optional[str] = None,
        author: Optional[str] = None,
        publication_date: Optional[str] = None,
        source_type: str = "web",
        relevance: int = 5,
        notes: Optional[str] = None,
        chapter_ids: Optional[list[int]] = None,
    ) -> dict[str, Any]:
        url_hash = _url_hash(url)
        conn = get_db()
        try:
            existing = conn.execute(
                "SELECT * FROM sources WHERE url_hash = ?", (url_hash,)
            ).fetchone()
            if existing:
                source = _row_to_source(existing)
                log(
                    logger,
                    logging.DEBUG,
                    "Fuente duplicada detectada por hash",
                    extra={"source_id": source.get("id")},
                )
                if chapter_ids:
                    current = set(source.get("chapter_ids") or [])
                    updated = sorted(current.union(int(c) for c in chapter_ids if str(c).strip().isdigit()))
                    conn.execute(
                        "UPDATE sources SET chapter_ids = ? WHERE id = ?",
                        (json.dumps(updated, ensure_ascii=False), source["id"]),
                    )
                    conn.commit()
                    source["chapter_ids"] = updated
                return source

            data = {
                "url": url,
                "url_hash": url_hash,
                "title": title,
                "publisher": publisher,
                "author": author,
                "publication_date": publication_date,
                "accessed_at": _now(),
                "source_type": source_type,
                "relevance": int(relevance),
                "notes": notes,
                "chapter_ids": json.dumps(chapter_ids or [], ensure_ascii=False),
            }
            cursor = conn.execute(
                """
                INSERT INTO sources
                (url, url_hash, title, publisher, author, publication_date,
                 accessed_at, source_type, relevance, notes, chapter_ids)
                VALUES
                (:url, :url_hash, :title, :publisher, :author, :publication_date,
                 :accessed_at, :source_type, :relevance, :notes, :chapter_ids)
                """,
                data,
            )
            conn.commit()
            source_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            return _row_to_source(row)
        finally:
            conn.close()

    @staticmethod
    def get_source(source_id: int) -> Optional[dict[str, Any]]:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            return _row_to_source(row)
        finally:
            conn.close()

    @staticmethod
    def update_source(source_id: int, **fields: Any) -> Optional[dict[str, Any]]:
        forbidden = {"id", "url", "url_hash"}
        if not fields or forbidden.intersection(fields):
            raise ValueError("Campos no modificables: id, url, url_hash")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [source_id]
        conn = get_db()
        try:
            conn.execute(f"UPDATE sources SET {set_clause} WHERE id = ?", values)
            conn.commit()
            row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
            return _row_to_source(row)
        finally:
            conn.close()

    @staticmethod
    def mark_verified(source_id: int) -> Optional[dict[str, Any]]:
        return SourceManager.update_source(source_id, notes="verified=True")

    @staticmethod
    def mark_conflicting(source_id: int, conflict_reason: str) -> Optional[dict[str, Any]]:
        return SourceManager.update_source(source_id, notes=f"conflict: {conflict_reason}")

    @staticmethod
    def delete_source(source_id: int) -> bool:
        conn = get_db()
        try:
            row = conn.execute("SELECT chapter_ids FROM sources WHERE id = ?", (source_id,)).fetchone()
            if not row:
                return False
            chapter_ids = json.loads(row["chapter_ids"] or "[]")
            if chapter_ids:
                raise ValueError(
                    f"No se puede eliminar la fuente {source_id}: está asociada a capítulos {chapter_ids}"
                )
            conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def get_chapter_sources(chapter_id: int) -> list[dict[str, Any]]:
        conn = get_db()
        try:
            rows = conn.execute("SELECT * FROM sources").fetchall()
            results = []
            for row in rows:
                data = _row_to_source(row)
                if str(chapter_id) in {str(x) for x in data.get("chapter_ids", [])}:
                    results.append(data)
            return sorted(results, key=lambda x: x.get("relevance", 0), reverse=True)
        finally:
            conn.close()

    @staticmethod
    def associate_chapter(source_id: int, chapter_id: int) -> Optional[dict[str, Any]]:
        conn = get_db()
        try:
            row = conn.execute("SELECT chapter_ids FROM sources WHERE id = ?", (source_id,)).fetchone()
            if not row:
                return None
            chapter_ids = json.loads(row["chapter_ids"] or "[]")
            if chapter_id not in chapter_ids:
                chapter_ids.append(chapter_id)
                conn.execute(
                    "UPDATE sources SET chapter_ids = ? WHERE id = ?",
                    (json.dumps(chapter_ids, ensure_ascii=False), source_id),
                )
                conn.commit()
            return SourceManager.get_source(source_id)
        finally:
            conn.close()

    @staticmethod
    def calculate_relevance(source: dict[str, Any]) -> int:
        base = int(source.get("relevance") or 5)
        bonus = 0
        stype = (source.get("source_type") or "").lower()
        if stype in {"official", "government", "primary_source"}:
            bonus += 2
        elif stype in {"academic", "reputable_media", "specialist"}:
            bonus += 1
        if source.get("publication_date"):
            bonus += 1
        if source.get("author") and source.get("publisher"):
            bonus += 1
        return max(1, min(10, base + bonus))

    @staticmethod
    def export_sources_json(path: str, chapter_id: Optional[int] = None) -> str:
        sources = (
            SourceManager.get_chapter_sources(chapter_id)
            if chapter_id is not None
            else SourceManager.search_sources()
        )
        export = []
        for src in sources:
            export.append(
                {
                    "id": src.get("id"),
                    "url": src.get("url"),
                    "title": src.get("title"),
                    "publisher": src.get("publisher"),
                    "author": src.get("author"),
                    "publication_date": src.get("publication_date"),
                    "accessed_at": src.get("accessed_at"),
                    "source_type": src.get("source_type"),
                    "relevance": SourceManager.calculate_relevance(src),
                    "notes": src.get("notes"),
                    "chapter_ids": src.get("chapter_ids", []),
                }
            )
        with open(path, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        return path

    @staticmethod
    def search_sources(query: Optional[str] = None, source_type: Optional[str] = None) -> list[dict[str, Any]]:
        conn = get_db()
        try:
            sql = "SELECT * FROM sources WHERE 1=1"
            params: list[Any] = []
            if query:
                sql += " AND (title LIKE ? OR notes LIKE ? OR url LIKE ?)"
                like = f"%{query}%"
                params.extend([like, like, like])
            if source_type:
                sql += " AND source_type = ?"
                params.append(source_type)
            sql += " ORDER BY accessed_at DESC"
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_source(row) for row in rows]
        finally:
            conn.close()

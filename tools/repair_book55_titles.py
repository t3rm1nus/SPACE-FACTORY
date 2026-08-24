"""Reparación puntual de chapters.title para book_55 (autorización explícita).

EJECUTADO el 2026-08-24: 24 UPDATE aplicados a data/space_lair.db; backup previo
en data/backups/chapters_backup_book_55.json. Idempotente salvo el backup
(solo se escribe si no existe).

Genera títulos cortos con la MISMA lógica que book_planner._short_idea_title
(primeras 8 palabras + '...' si trunca) y formato '{corto} - Parte {N}'.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BOOK_ID = 55
BACKUP = ROOT / "data" / "backups" / f"chapters_backup_book_{BOOK_ID}.json"


def short_idea_title(idea: str, max_words: int = 8) -> str:
    words = (idea or "").strip().split()
    if not words:
        return "Capítulo"
    short = " ".join(words[:max_words])
    if len(words) > max_words:
        short += "..."
    return short


def main() -> None:
    conn = sqlite3.connect(ROOT / "data" / "space_lair.db")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id AS chapter_id, number, title FROM chapters "
            "WHERE book_id = ? ORDER BY number",
            (BOOK_ID,),
        ).fetchall()
        assert len(rows) == 24, f"Se esperaban 24 capítulos, hay {len(rows)}"

        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        if not BACKUP.exists():
            BACKUP.write_text(
                json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Backup escrito: {BACKUP}")

        book_title = conn.execute(
            "SELECT title FROM books WHERE id = ?", (BOOK_ID,)
        ).fetchone()[0]
        base = short_idea_title(book_title)

        for r in rows:
            new_title = f"{base} - Parte {r['number']}"
            conn.execute(
                "UPDATE chapters SET title = ?, updated_at = datetime('now') "
                "WHERE id = ? AND book_id = ?",
                (new_title, r["chapter_id"], BOOK_ID),
            )
            print(r["number"], "->", new_title)
        conn.commit()
        print("UPDATE aplicado a", len(rows), "capítulos.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

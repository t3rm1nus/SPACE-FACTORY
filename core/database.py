"""Gestión de la base de datos SQLite de Space Lair."""

import hashlib
import os
import sqlite3
from datetime import datetime, timedelta

# Ruta de la base de datos (data/space_lair.db relativo a la raíz del proyecto)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "space_lair.db")

# Umbral de "stale" para tareas running (segundos)
# 420s (7 min): margen sobre el timeout_seconds más alto real de los módulos
# (document_builder/image_generator = 360s), para no resetear tareas
# legítimamente largas en caliente (§17 #48).
STALE_RUNNING_SECONDS = 420


def get_db() -> sqlite3.Connection:
    """Retorna una conexión SQLite con row_factory configurado."""
    os.makedirs(DATA_DIR, exist_ok=True)
    db_path = os.environ.get("SPACE_LAIR_DB_PATH", DB_PATH)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _migrate_tasks_cancelled(conn: sqlite3.Connection) -> None:
    """Recrea la tabla tasks para soportar el estado 'cancelled' (CHECK constraint)."""
    existing = [row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "cancelled" in existing or not existing:
        return

    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capability TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'error', 'pending_approval', 'cancelled')),
                module_id TEXT,
                result TEXT,
                error TEXT,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                finished_at DATETIME,
                next_retry_at DATETIME,
                cost REAL DEFAULT 0.0,
                tokens_input INTEGER DEFAULT 0,
                tokens_output INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tasks_new
            SELECT id, capability, payload, status, module_id, result, error,
                   attempts, max_attempts, created_at, started_at, finished_at,
                   next_retry_at, cost, tokens_input, tokens_output
            FROM tasks
            """
        )
        conn.execute("DROP TABLE tasks")
        conn.execute("ALTER TABLE tasks_new RENAME TO tasks")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_status ON tasks (status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_capability ON tasks (capability)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_module_id ON tasks (module_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_next_retry_at ON tasks (next_retry_at)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate(conn: sqlite3.Connection) -> None:
    """Aplica migraciones a las tablas (columnas añadidas en versiones nuevas)."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "next_retry_at" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN next_retry_at DATETIME")
    if "cost" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN cost REAL DEFAULT 0.0")
    if "tokens_input" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN tokens_input INTEGER DEFAULT 0")
    if "tokens_output" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN tokens_output INTEGER DEFAULT 0")

    # Migración: soportar estado 'cancelled' en tasks (CHECK constraint no se puede ALTER)
    _migrate_tasks_cancelled(conn)

    columns_books = [row[1] for row in conn.execute("PRAGMA table_info(books)").fetchall()]
    if "subtitle" not in columns_books:
        conn.execute("ALTER TABLE books ADD COLUMN subtitle TEXT")
    if "description" not in columns_books:
        conn.execute("ALTER TABLE books ADD COLUMN description TEXT")
    if "author" not in columns_books:
        conn.execute("ALTER TABLE books ADD COLUMN author TEXT")
    if "target_audience" not in columns_books:
        conn.execute("ALTER TABLE books ADD COLUMN target_audience TEXT")
    if "genre" not in columns_books:
        conn.execute("ALTER TABLE books ADD COLUMN genre TEXT")
    if "languages" not in columns_books:
        conn.execute("ALTER TABLE books ADD COLUMN languages TEXT NOT NULL DEFAULT 'es'")
    if "target_chapters" not in columns_books:
        conn.execute("ALTER TABLE books ADD COLUMN target_chapters INTEGER NOT NULL DEFAULT 10")

    columns_chapters = [row[1] for row in conn.execute("PRAGMA table_info(chapters)").fetchall()]
    if "title" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN title TEXT")
    if "objective" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN objective TEXT")
    if "research" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN research TEXT")
    if "sources" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN sources TEXT NOT NULL DEFAULT '[]'")
    if "outline" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN outline TEXT")
    if "draft_es" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN draft_es TEXT")
    if "draft_en" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN draft_en TEXT")
    if "edited_es" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN edited_es TEXT")
    if "edited_en" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN edited_en TEXT")
    if "images" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")
    if "quality_status" not in columns_chapters:
        conn.execute("ALTER TABLE chapters ADD COLUMN quality_status TEXT")

    columns_sources = [row[1] for row in conn.execute("PRAGMA table_info(sources)").fetchall()]
    if "url" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN url TEXT")
    if "title" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN title TEXT")
    if "publisher" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN publisher TEXT")
    if "author" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN author TEXT")
    if "publication_date" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN publication_date TEXT")
    if "accessed_at" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN accessed_at DATETIME")
    if "source_type" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN source_type TEXT NOT NULL DEFAULT 'web'")
    if "relevance" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN relevance INTEGER NOT NULL DEFAULT 5")
    if "notes" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN notes TEXT")
    if "chapter_ids" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN chapter_ids TEXT NOT NULL DEFAULT '[]'")
    if "url_hash" not in columns_sources:
        conn.execute("ALTER TABLE sources ADD COLUMN url_hash TEXT")
    if "url_hash" in columns_sources:
        rows = conn.execute("SELECT id, url FROM sources WHERE url_hash IS NULL").fetchall()
        for row in rows:
            url = row["url"] or ""
            url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
            conn.execute("UPDATE sources SET url_hash = ? WHERE id = ?", (url_hash, row["id"]))

    columns_wf_steps = [row[1] for row in conn.execute("PRAGMA table_info(workflow_steps)").fetchall()]
    if "payload" not in columns_wf_steps:
        conn.execute("ALTER TABLE workflow_steps ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
    if "retries" not in columns_wf_steps:
        conn.execute("ALTER TABLE workflow_steps ADD COLUMN retries INTEGER NOT NULL DEFAULT 0")
    conn.commit()


def init_db() -> None:
    """Crea la tabla tasks y sus índices si no existen."""
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capability TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'error', 'pending_approval', 'cancelled')),
                module_id TEXT,
                result TEXT,
                error TEXT,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                finished_at DATETIME,
                next_retry_at DATETIME,
                cost REAL DEFAULT 0.0,
                tokens_input INTEGER DEFAULT 0,
                tokens_output INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                definition TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'error', 'cancelled')),
                current_step TEXT,
                error TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                finished_at DATETIME
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id INTEGER NOT NULL,
                step_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                depends_on TEXT,
                parallel TEXT,
                task_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'error', 'skipped')),
                result TEXT,
                error TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                started_at DATETIME,
                finished_at DATETIME,
                FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                title_en TEXT,
                subtitle TEXT,
                description TEXT,
                description_en TEXT,
                author TEXT,
                target_audience TEXT,
                genre TEXT,
                languages TEXT NOT NULL DEFAULT 'es',
                target_chapters INTEGER NOT NULL DEFAULT 10,
                image_count INTEGER NOT NULL DEFAULT 3,
                layout_config TEXT,
                image_search_ratio REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'planned',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                number INTEGER NOT NULL,
                title TEXT,
                objective TEXT,
                status TEXT NOT NULL DEFAULT 'planned',
                                research TEXT,
                sources TEXT NOT NULL DEFAULT '[]',
                outline TEXT,
                outline_en TEXT,
                title_en TEXT,
                draft_es TEXT,
                draft_en TEXT,
                edited_es TEXT,
                edited_en TEXT,
                images TEXT NOT NULL DEFAULT '[]',
                quality_status TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
                        )
            """
        )
        # Migración: añadir image_count en bases existentes
        try:
            conn.execute("ALTER TABLE books ADD COLUMN image_count INTEGER NOT NULL DEFAULT 3")
        except Exception:
            pass  # columna ya existe
        # Migración: añadir layout_config en bases existentes
        try:
            conn.execute("ALTER TABLE books ADD COLUMN layout_config TEXT")
        except Exception:
            pass  # columna ya existe
        # Migración: añadir image_search_ratio en bases existentes
        try:
            conn.execute("ALTER TABLE books ADD COLUMN image_search_ratio REAL NOT NULL DEFAULT 0.0")
        except Exception:
            pass  # columna ya existe
        # §17 #21 (Opción A): título/descripción del libro en inglés para la
        # edición EN de libros bilingües (NULL = sin traducción → fallback ES).
        try:
            conn.execute("ALTER TABLE books ADD COLUMN title_en TEXT")
        except Exception:
            pass  # columna ya existe
        try:
            conn.execute("ALTER TABLE books ADD COLUMN description_en TEXT")
        except Exception:
            pass  # columna ya existe
        # §17 #21 (Opción A): título y outline del capítulo en inglés
        # (outline_en = JSON [{heading, objective}] en inglés, misma forma que
        # `chapters.outline`). NULL = sin traducción → fallback ES intacto.
        try:
            conn.execute("ALTER TABLE chapters ADD COLUMN title_en TEXT")
        except Exception:
            pass  # columna ya existe
        try:
            conn.execute("ALTER TABLE chapters ADD COLUMN outline_en TEXT")
        except Exception:
            pass  # columna ya existe
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                url_hash TEXT,
                title TEXT,
                publisher TEXT,
                author TEXT,
                publication_date TEXT,
                accessed_at DATETIME,
                source_type TEXT NOT NULL DEFAULT 'web',
                relevance INTEGER NOT NULL DEFAULT 5,
                notes TEXT,
                chapter_ids TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.commit()
        _migrate(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON tasks (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_capability ON tasks (capability)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_module_id ON tasks (module_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_next_retry_at ON tasks (next_retry_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_status ON workflows (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_steps_wf ON workflow_steps (workflow_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_workflow_steps_status ON workflow_steps (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_books_status ON books (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chapters_book ON chapters (book_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chapters_status ON chapters (status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_type ON sources (source_type)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_url_hash ON sources (url_hash)")
        conn.commit()
    finally:
        conn.close()


def reset_stale_running_tasks() -> int:
    """Resetea tareas 'running' que llevan más de STALE_RUNNING_SECONDS segundos.

    Devuelve el número de tareas reseteadas.
    """
    conn = get_db()
    try:
        cutoff = (datetime.utcnow() - timedelta(seconds=STALE_RUNNING_SECONDS)).isoformat(
            sep=" ", timespec="seconds"
        )
        cursor = conn.execute(
            """
            UPDATE tasks
            SET status = 'pending', started_at = NULL
            WHERE status = 'running' AND started_at < ?
            """,
            (cutoff,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
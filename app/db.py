"""Database connection and schema management."""

import sqlite3
from contextlib import contextmanager
from typing import Generator

from app.settings import settings

# Schema version for migrations
SCHEMA_VERSION = 8

# Database schema SQL
SCHEMA_SQL = """
-- Core files table
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'indexing', 'indexed', 'error')),
    last_error TEXT,
    pages INTEGER,
    extracted_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    public_read BOOLEAN NOT NULL DEFAULT 0,
    employer_name TEXT,
    union_local TEXT,
    effective_date TEXT,
    expiry_date TEXT,
    region TEXT,
    short_name TEXT
);

-- Extracted pages
CREATE TABLE IF NOT EXISTS pdf_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    raw_text TEXT,
    UNIQUE(file_id, page_number)
);

-- FTS5 virtual table for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
    file_id UNINDEXED,
    page_id UNINDEXED,
    page_number UNINDEXED,
    text,
    tokenize='porter unicode61'
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- Bug reports table
CREATE TABLE IF NOT EXISTS bug_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_name TEXT,
    reporter_email TEXT,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'low' CHECK(severity IN ('low','medium','high','critical')),
    metadata TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','triaged','closed')),
    created_at TEXT DEFAULT (datetime('now')),
    resolved_at TEXT
);

-- Custom synonyms table
CREATE TABLE IF NOT EXISTS custom_synonyms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_term TEXT NOT NULL UNIQUE,
    synonyms TEXT NOT NULL,  -- JSON array of synonyms
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Page embeddings for vector search (RAG)
CREATE TABLE IF NOT EXISTS page_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL UNIQUE REFERENCES pdf_pages(id) ON DELETE CASCADE,
    text_hash INTEGER,  -- Hash of text to detect changes
    embedding_json TEXT,  -- Stored embedding or 'indexed' flag
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Semantic chunks for structure-aware indexing
CREATE TABLE IF NOT EXISTS document_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_number INTEGER NOT NULL,
    text TEXT NOT NULL,
    heading TEXT,                 -- Current section heading
    parent_heading TEXT,          -- Parent section (e.g., Article for a Section)
    section_number TEXT,          -- Extracted section/article number
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    headings_json TEXT,           -- JSON array of all headings in this chunk
    chunk_type TEXT DEFAULT 'text', -- 'text' or 'table'
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(file_id, chunk_number)
);

-- Extracted tables from PDFs
CREATE TABLE IF NOT EXISTS document_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    table_index INTEGER NOT NULL DEFAULT 0,
    headers_json TEXT,            -- JSON array of column headers
    rows_json TEXT,               -- JSON array of row arrays
    markdown_text TEXT NOT NULL,  -- Markdown-formatted table
    context_heading TEXT,         -- Heading above the table
    is_wage_table BOOLEAN NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- FTS5 for chunk-based search
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    file_id UNINDEXED,
    chunk_id UNINDEXED,
    heading,
    text,
    tokenize='porter unicode61'
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_filename ON files(filename);
CREATE INDEX IF NOT EXISTS idx_files_public_read ON files(public_read);
CREATE INDEX IF NOT EXISTS idx_pages_file ON pdf_pages(file_id);
CREATE INDEX IF NOT EXISTS idx_bug_reports_status ON bug_reports(status);
CREATE INDEX IF NOT EXISTS idx_custom_synonyms_canonical ON custom_synonyms(canonical_term);
CREATE INDEX IF NOT EXISTS idx_page_embeddings_page ON page_embeddings(page_id);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON document_chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_heading ON document_chunks(heading);
CREATE INDEX IF NOT EXISTS idx_chunks_type ON document_chunks(chunk_type);
CREATE INDEX IF NOT EXISTS idx_tables_file ON document_tables(file_id);
CREATE INDEX IF NOT EXISTS idx_tables_wage ON document_tables(is_wage_table);
"""


def get_connection() -> sqlite3.Connection:
    """Create a new database connection."""
    conn = sqlite3.connect(str(settings.DATABASE_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections with automatic commit/rollback."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database schema."""
    # Ensure data directory exists
    settings.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with get_db() as conn:
        # Check current schema version
        try:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            current_version = row["version"] if row else 0
        except sqlite3.OperationalError:
            current_version = 0

        # Apply schema if needed
        if current_version < SCHEMA_VERSION:
            if current_version == 0:
                # Fresh install
                conn.executescript(SCHEMA_SQL)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                # Run migrations
                if current_version < 2:
                    # Migration v1 -> v2: Add raw_text column
                    try:
                        conn.execute("ALTER TABLE pdf_pages ADD COLUMN raw_text TEXT")
                    except sqlite3.OperationalError:
                        pass  # Column already exists

                if current_version < 3:
                    # Migration v2 -> v3: Add bug_reports table
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS bug_reports (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            reporter_name TEXT,
                            reporter_email TEXT,
                            subject TEXT NOT NULL,
                            description TEXT NOT NULL,
                            severity TEXT NOT NULL DEFAULT 'low' CHECK(severity IN ('low','medium','high','critical')),
                            metadata TEXT,
                            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','triaged','closed')),
                            created_at TEXT DEFAULT (datetime('now')),
                            resolved_at TEXT
                        )
                    """)
                    try:
                        conn.execute("CREATE INDEX idx_bug_reports_status ON bug_reports(status)")
                    except sqlite3.OperationalError:
                        pass  # Index already exists

                if current_version < 4:
                    # Migration v3 -> v4: Add custom_synonyms table
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS custom_synonyms (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            canonical_term TEXT NOT NULL UNIQUE,
                            synonyms TEXT NOT NULL,
                            created_at TEXT DEFAULT (datetime('now')),
                            updated_at TEXT DEFAULT (datetime('now'))
                        )
                    """)
                    try:
                        conn.execute("CREATE INDEX idx_custom_synonyms_canonical ON custom_synonyms(canonical_term)")
                    except sqlite3.OperationalError:
                        pass  # Index already exists

                if current_version < 5:
                    # Migration v4 -> v5: Add public_read column to files table
                    try:
                        conn.execute("ALTER TABLE files ADD COLUMN public_read BOOLEAN NOT NULL DEFAULT 0")
                    except sqlite3.OperationalError:
                        pass  # Column already exists
                    try:
                        conn.execute("CREATE INDEX idx_files_public_read ON files(public_read)")
                    except sqlite3.OperationalError:
                        pass  # Index already exists

                if current_version < 6:
                    # Migration v5 -> v6: Add page_embeddings table for RAG vector search
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS page_embeddings (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            page_id INTEGER NOT NULL UNIQUE REFERENCES pdf_pages(id) ON DELETE CASCADE,
                            text_hash INTEGER,
                            embedding_json TEXT,
                            created_at TEXT DEFAULT (datetime('now')),
                            updated_at TEXT DEFAULT (datetime('now'))
                        )
                    """)
                    try:
                        conn.execute("CREATE INDEX idx_page_embeddings_page ON page_embeddings(page_id)")
                    except sqlite3.OperationalError:
                        pass  # Index already exists

                if current_version < 7:
                    # Migration v6 -> v7: Add document_chunks table for semantic chunking
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS document_chunks (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                            chunk_number INTEGER NOT NULL,
                            text TEXT NOT NULL,
                            heading TEXT,
                            parent_heading TEXT,
                            section_number TEXT,
                            page_start INTEGER NOT NULL,
                            page_end INTEGER NOT NULL,
                            headings_json TEXT,
                            created_at TEXT DEFAULT (datetime('now')),
                            UNIQUE(file_id, chunk_number)
                        )
                    """)
                    # Create FTS5 table for chunk search
                    conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                            file_id UNINDEXED,
                            chunk_id UNINDEXED,
                            heading,
                            text,
                            tokenize='porter unicode61'
                        )
                    """)
                    try:
                        conn.execute("CREATE INDEX idx_chunks_file ON document_chunks(file_id)")
                    except sqlite3.OperationalError:
                        pass
                    try:
                        conn.execute("CREATE INDEX idx_chunks_heading ON document_chunks(heading)")
                    except sqlite3.OperationalError:
                        pass

                if current_version < 8:
                    # Migration v7 -> v8: Add chunk_type, document_tables, file metadata
                    try:
                        conn.execute("ALTER TABLE document_chunks ADD COLUMN chunk_type TEXT DEFAULT 'text'")
                    except sqlite3.OperationalError:
                        pass  # Column already exists

                    # Create document_tables table
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS document_tables (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                            page_number INTEGER NOT NULL,
                            table_index INTEGER NOT NULL DEFAULT 0,
                            headers_json TEXT,
                            rows_json TEXT,
                            markdown_text TEXT NOT NULL,
                            context_heading TEXT,
                            is_wage_table BOOLEAN NOT NULL DEFAULT 0,
                            created_at TEXT DEFAULT (datetime('now'))
                        )
                    """)

                    # Add metadata columns to files
                    for col in ['employer_name TEXT', 'union_local TEXT', 'effective_date TEXT',
                                'expiry_date TEXT', 'region TEXT', 'short_name TEXT']:
                        try:
                            conn.execute(f"ALTER TABLE files ADD COLUMN {col}")
                        except sqlite3.OperationalError:
                            pass  # Column already exists

                    # Create indexes
                    for idx_sql in [
                        "CREATE INDEX IF NOT EXISTS idx_chunks_type ON document_chunks(chunk_type)",
                        "CREATE INDEX IF NOT EXISTS idx_tables_file ON document_tables(file_id)",
                        "CREATE INDEX IF NOT EXISTS idx_tables_wage ON document_tables(is_wage_table)",
                    ]:
                        try:
                            conn.execute(idx_sql)
                        except sqlite3.OperationalError:
                            pass

                conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))


def get_db_stats() -> dict:
    """Get database statistics for diagnostics."""
    with get_db() as conn:
        files_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        indexed_count = conn.execute("SELECT COUNT(*) FROM files WHERE status = 'indexed'").fetchone()[0]
        error_count = conn.execute("SELECT COUNT(*) FROM files WHERE status = 'error'").fetchone()[0]
        pages_count = conn.execute("SELECT COUNT(*) FROM pdf_pages").fetchone()[0]

        return {
            "total_files": files_count,
            "indexed_files": indexed_count,
            "error_files": error_count,
            "total_pages": pages_count,
        }


def get_public_files() -> list[dict]:
    """Get only files that are marked as public_read."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, path, filename, sha256, mtime, size, status,
                      last_error, pages, extracted_at, created_at, public_read
               FROM files
               WHERE public_read = 1
               ORDER BY filename"""
        ).fetchall()

        return [dict(row) for row in rows]


def toggle_file_public_read(file_id: int) -> bool:
    """Toggle the public_read status of a file. Returns the new status."""
    with get_db() as conn:
        # Get current status
        row = conn.execute(
            "SELECT public_read FROM files WHERE id = ?", (file_id,)
        ).fetchone()

        if not row:
            raise ValueError(f"File with id {file_id} not found")

        new_status = not bool(row["public_read"])
        conn.execute(
            "UPDATE files SET public_read = ? WHERE id = ?",
            (1 if new_status else 0, file_id)
        )

        return new_status

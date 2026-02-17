"""File scanner service - discovers and tracks PDF files."""

import hashlib
from pathlib import Path
from typing import Optional

from app.db import get_db
from app.settings import settings
from app.models import FileInfo


def compute_sha256(filepath: Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_agreements() -> dict:
    """
    Scan the agreements directory for PDF files.
    Adds new files, updates changed files, marks missing files.

    Returns dict with counts of new, changed, unchanged, missing files.
    """
    results = {"new": 0, "changed": 0, "unchanged": 0, "missing": 0, "errors": []}

    # Get all PDF files in directory
    pdf_files = list(settings.AGREEMENTS_DIR.glob("*.pdf"))
    pdf_paths = {str(p.resolve()) for p in pdf_files}

    with get_db() as conn:
        # Get existing files from database
        existing_rows = conn.execute("SELECT id, path, sha256 FROM files").fetchall()
        existing_paths = {row["path"]: row for row in existing_rows}

        # Check for new or changed files
        for pdf_path in pdf_files:
            path_str = str(pdf_path.resolve())
            try:
                stat = pdf_path.stat()
                sha256 = compute_sha256(pdf_path)

                if path_str not in existing_paths:
                    # New file - default public_read to False
                    conn.execute(
                        """INSERT INTO files (path, filename, sha256, mtime, size, status, public_read)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (path_str, pdf_path.name, sha256, stat.st_mtime, stat.st_size, "pending", 0),
                    )
                    results["new"] += 1
                else:
                    existing = existing_paths[path_str]
                    if existing["sha256"] != sha256:
                        # File changed
                        conn.execute(
                            """UPDATE files
                               SET sha256 = ?, mtime = ?, size = ?, status = 'pending',
                                   last_error = NULL, pages = NULL, extracted_at = NULL
                               WHERE id = ?""",
                            (sha256, stat.st_mtime, stat.st_size, existing["id"]),
                        )
                        # Clear old pages
                        conn.execute("DELETE FROM pdf_pages WHERE file_id = ?", (existing["id"],))
                        conn.execute("DELETE FROM page_fts WHERE file_id = ?", (existing["id"],))
                        results["changed"] += 1
                    else:
                        results["unchanged"] += 1

            except Exception as e:
                results["errors"].append({"path": path_str, "error": str(e)})

        # Check for missing files (in DB but not on disk)
        for path_str, row in existing_paths.items():
            if path_str not in pdf_paths:
                # File was deleted from disk
                conn.execute("DELETE FROM files WHERE id = ?", (row["id"],))
                results["missing"] += 1

    return results


_FILE_SELECT_COLS = """id, path, filename, sha256, mtime, size, status,
                      last_error, pages, extracted_at, created_at, public_read,
                      employer_name, union_local, effective_date, expiry_date, region, short_name"""


def _row_to_fileinfo(row) -> FileInfo:
    """Convert a database row to FileInfo, handling missing metadata columns."""
    return FileInfo(
        id=row["id"],
        path=row["path"],
        filename=row["filename"],
        sha256=row["sha256"],
        mtime=row["mtime"],
        size=row["size"],
        status=row["status"],
        last_error=row["last_error"],
        pages=row["pages"],
        extracted_at=row["extracted_at"],
        created_at=row["created_at"],
        public_read=bool(row["public_read"]),
        employer_name=row["employer_name"] if "employer_name" in row.keys() else None,
        union_local=row["union_local"] if "union_local" in row.keys() else None,
        effective_date=row["effective_date"] if "effective_date" in row.keys() else None,
        expiry_date=row["expiry_date"] if "expiry_date" in row.keys() else None,
        region=row["region"] if "region" in row.keys() else None,
        short_name=row["short_name"] if "short_name" in row.keys() else None,
    )


def get_all_files() -> list[FileInfo]:
    """Get all files from the database."""
    with get_db() as conn:
        try:
            rows = conn.execute(
                f"SELECT {_FILE_SELECT_COLS} FROM files ORDER BY filename"
            ).fetchall()
        except Exception:
            # Fallback for older schema
            rows = conn.execute(
                """SELECT id, path, filename, sha256, mtime, size, status,
                          last_error, pages, extracted_at, created_at, public_read
                   FROM files ORDER BY filename"""
            ).fetchall()

        return [_row_to_fileinfo(row) for row in rows]


def get_file_by_id(file_id: int) -> Optional[FileInfo]:
    """Get a single file by ID."""
    with get_db() as conn:
        try:
            row = conn.execute(
                f"SELECT {_FILE_SELECT_COLS} FROM files WHERE id = ?",
                (file_id,),
            ).fetchone()
        except Exception:
            row = conn.execute(
                """SELECT id, path, filename, sha256, mtime, size, status,
                          last_error, pages, extracted_at, created_at, public_read
                   FROM files WHERE id = ?""",
                (file_id,),
            ).fetchone()

        if not row:
            return None

        return _row_to_fileinfo(row)


def get_public_files() -> list[FileInfo]:
    """Get only files that are marked as public_read."""
    with get_db() as conn:
        try:
            rows = conn.execute(
                f"SELECT {_FILE_SELECT_COLS} FROM files WHERE public_read = 1 ORDER BY filename"
            ).fetchall()
        except Exception:
            rows = conn.execute(
                """SELECT id, path, filename, sha256, mtime, size, status,
                          last_error, pages, extracted_at, created_at, public_read
                   FROM files WHERE public_read = 1 ORDER BY filename"""
            ).fetchall()

        return [_row_to_fileinfo(row) for row in rows]

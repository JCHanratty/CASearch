"""Indexer service - stores extracted pages and manages FTS5 index."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.db import get_db
from app.services.pdf_extract import extract_pdf_pages, extract_all_tables, ExtractionError
from app.services.structure_extract import extract_with_structure

logger = logging.getLogger(__name__)


def index_file(file_id: int, use_structure: bool = True, build_embeddings: bool = False) -> dict:
    """
    Extract PDF text and populate pdf_pages + FTS5 index.
    Also creates semantic chunks with heading metadata.

    Args:
        file_id: Database ID of the file to index
        use_structure: Whether to use structure-aware extraction (default True)
        build_embeddings: Whether to build semantic embeddings (default False for speed)

    Returns:
        dict with status, page count, and chunk count

    Raises:
        ValueError: If file not found
        ExtractionError: If extraction fails
    """
    with get_db() as conn:
        # Get file path
        row = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
        if not row:
            raise ValueError(f"File {file_id} not found")

        filepath = Path(row["path"])

        # Mark as indexing
        conn.execute("UPDATE files SET status = 'indexing' WHERE id = ?", (file_id,))
        conn.commit()

        try:
            # Extract pages (traditional method for backward compatibility)
            pages = extract_pdf_pages(filepath)

            # Clear existing data for this file
            conn.execute("DELETE FROM pdf_pages WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM page_fts WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM document_chunks WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM chunk_fts WHERE file_id = ?", (file_id,))
            try:
                conn.execute("DELETE FROM document_tables WHERE file_id = ?", (file_id,))
            except Exception:
                pass  # Table may not exist yet on older schemas

            # Insert pages (traditional page-based storage)
            for page in pages:
                cursor = conn.execute(
                    "INSERT INTO pdf_pages (file_id, page_number, text, raw_text) VALUES (?, ?, ?, ?)",
                    (file_id, page.page_number, page.text, page.raw_text),
                )
                page_id = cursor.lastrowid

                # Add to FTS index (uses cleaned text for better search)
                conn.execute(
                    "INSERT INTO page_fts (file_id, page_id, page_number, text) VALUES (?, ?, ?, ?)",
                    (file_id, page_id, page.page_number, page.text),
                )

            # Extract tables using pdfplumber
            all_tables = []
            try:
                all_tables = extract_all_tables(filepath, pages)

                # Store tables in document_tables
                for table in all_tables:
                    try:
                        import json as _json
                        conn.execute(
                            """INSERT INTO document_tables
                               (file_id, page_number, table_index, headers_json,
                                rows_json, markdown_text, context_heading, is_wage_table)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                file_id,
                                table.page_number,
                                table.table_index,
                                _json.dumps(table.headers),
                                _json.dumps(table.rows),
                                table.markdown_text,
                                table.context_heading,
                                1 if table.is_wage_table else 0,
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to store table from page {table.page_number}: {e}")

                if all_tables:
                    logger.info(f"Stored {len(all_tables)} tables for file {file_id}")
            except Exception as e:
                logger.warning(f"Table extraction failed for file {file_id}: {e}")

            # Structure-aware extraction and chunking
            chunk_count = 0
            if use_structure:
                try:
                    structured_pages, chunks = extract_with_structure(filepath, tables=all_tables)

                    # Insert semantic chunks
                    for chunk in chunks:
                        cursor = conn.execute(
                            """INSERT INTO document_chunks
                               (file_id, chunk_number, text, heading, parent_heading,
                                section_number, page_start, page_end, headings_json, chunk_type)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                file_id,
                                chunk.chunk_id,
                                chunk.text,
                                chunk.heading,
                                chunk.parent_heading,
                                chunk.section_number,
                                chunk.page_start,
                                chunk.page_end,
                                json.dumps(chunk.headings_in_chunk) if chunk.headings_in_chunk else None,
                                chunk.chunk_type,
                            ),
                        )
                        chunk_id = cursor.lastrowid

                        # Add to chunk FTS index (includes heading for boosted search)
                        conn.execute(
                            "INSERT INTO chunk_fts (file_id, chunk_id, heading, text) VALUES (?, ?, ?, ?)",
                            (file_id, chunk_id, chunk.heading or '', chunk.text),
                        )
                        chunk_count += 1
                except Exception as e:
                    # Log but don't fail - fall back to page-only indexing
                    logger.warning(f"Structure extraction failed for file {file_id}: {e}")

            # Build semantic embeddings if requested
            embeddings_count = 0
            if build_embeddings and chunk_count > 0:
                try:
                    from app.services.semantic_search import add_chunk_embedding, delete_file_embeddings

                    # Clear existing embeddings for this file
                    delete_file_embeddings(file_id)

                    # Get filename for metadata
                    file_row = conn.execute(
                        "SELECT filename, path FROM files WHERE id = ?", (file_id,)
                    ).fetchone()
                    filename = file_row["filename"] if file_row else ""
                    file_path = file_row["path"] if file_row else ""

                    # Add embeddings for each chunk
                    chunk_rows = conn.execute(
                        """SELECT id, text, heading, page_start, page_end
                           FROM document_chunks WHERE file_id = ?
                           ORDER BY chunk_number""",
                        (file_id,),
                    ).fetchall()

                    for chunk_row in chunk_rows:
                        if add_chunk_embedding(
                            chunk_id=chunk_row["id"],
                            file_id=file_id,
                            text=chunk_row["text"],
                            heading=chunk_row["heading"],
                            page_start=chunk_row["page_start"],
                            page_end=chunk_row["page_end"],
                            filename=filename,
                            file_path=file_path,
                        ):
                            embeddings_count += 1

                    logger.info(f"Created {embeddings_count} embeddings for file {file_id}")
                except Exception as e:
                    logger.warning(f"Embedding creation failed for file {file_id}: {e}")

            # Update file status
            conn.execute(
                """UPDATE files
                   SET status = 'indexed',
                       pages = ?,
                       extracted_at = ?,
                       last_error = NULL
                   WHERE id = ?""",
                (len(pages), datetime.utcnow().isoformat(), file_id),
            )

            return {"status": "success", "pages": len(pages), "chunks": chunk_count, "embeddings": embeddings_count}

        except ExtractionError as e:
            conn.execute(
                "UPDATE files SET status = 'error', last_error = ? WHERE id = ?",
                (str(e), file_id),
            )
            raise

        except Exception as e:
            conn.execute(
                "UPDATE files SET status = 'error', last_error = ? WHERE id = ?",
                (str(e), file_id),
            )
            raise


def get_file_pages(file_id: int, page_number: Optional[int] = None) -> list[dict]:
    """
    Get extracted pages for a file.

    Args:
        file_id: Database ID of the file
        page_number: Optional specific page to retrieve

    Returns:
        List of page dicts with page_number and text
    """
    with get_db() as conn:
        if page_number:
            rows = conn.execute(
                """SELECT page_number, text FROM pdf_pages
                   WHERE file_id = ? AND page_number = ?
                   ORDER BY page_number""",
                (file_id, page_number),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT page_number, text FROM pdf_pages
                   WHERE file_id = ?
                   ORDER BY page_number""",
                (file_id,),
            ).fetchall()

        return [{"page_number": r["page_number"], "text": r["text"]} for r in rows]


def reindex_all() -> dict:
    """Reindex all files in the database."""
    results = {"success": 0, "failed": 0, "errors": []}

    with get_db() as conn:
        rows = conn.execute("SELECT id FROM files").fetchall()

    for row in rows:
        try:
            index_file(row["id"])
            results["success"] += 1
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"file_id": row["id"], "error": str(e)})

    return results

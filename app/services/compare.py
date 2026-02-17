"""Compare service - side-by-side document comparison."""

import re
from typing import Optional

from app.db import get_db
from app.services.file_scanner import get_file_by_id


def get_document_pages(file_id: int) -> list[dict]:
    """
    Get all pages for a document.

    Args:
        file_id: Database ID of the file

    Returns:
        List of dicts with page_number and text
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT page_number, text FROM pdf_pages
               WHERE file_id = ?
               ORDER BY page_number""",
            (file_id,),
        ).fetchall()

        return [{"page": r["page_number"], "text": r["text"]} for r in rows]


def find_term_locations(pages: list[dict], term: str) -> list[dict]:
    """
    Find all occurrences of a term across pages.

    Args:
        pages: List of page dicts
        term: Search term

    Returns:
        List of match dicts with page, start, end, context
    """
    matches = []
    term_lower = term.lower()
    # Split into words for multi-word search
    term_words = term_lower.split()

    for page in pages:
        text = page["text"]
        text_lower = text.lower()

        # Search for the term
        start = 0
        while True:
            pos = text_lower.find(term_lower, start)
            if pos == -1:
                break

            # Get context (50 chars before and after)
            context_start = max(0, pos - 50)
            context_end = min(len(text), pos + len(term) + 50)

            # Clean up context boundaries (don't cut words)
            if context_start > 0:
                space_pos = text.find(" ", context_start)
                if space_pos != -1 and space_pos < pos:
                    context_start = space_pos + 1

            if context_end < len(text):
                space_pos = text.rfind(" ", pos + len(term), context_end)
                if space_pos != -1:
                    context_end = space_pos

            context = text[context_start:context_end]

            # Highlight the term in context
            highlighted_context = re.sub(
                f"({re.escape(term)})",
                r"<mark>\1</mark>",
                context,
                flags=re.IGNORECASE,
            )

            matches.append(
                {
                    "page": page["page"],
                    "start": pos,
                    "end": pos + len(term),
                    "context": highlighted_context,
                }
            )
            start = pos + 1

    return matches


def compare_documents(
    file_id_a: int, file_id_b: int, topic: Optional[str] = None
) -> Optional[dict]:
    """
    Compare two documents, optionally filtering by topic.

    Args:
        file_id_a: First document ID
        file_id_b: Second document ID
        topic: Optional search term to filter by

    Returns:
        Dict with comparison results or None if invalid
    """
    # Get file info
    doc_a = get_file_by_id(file_id_a)
    doc_b = get_file_by_id(file_id_b)

    if not doc_a or not doc_b:
        return None

    if doc_a.status != "indexed" or doc_b.status != "indexed":
        return None

    # Get pages for both documents
    pages_a = get_document_pages(file_id_a)
    pages_b = get_document_pages(file_id_b)

    result = {
        "doc_a": {
            "file_id": file_id_a,
            "filename": doc_a.filename,
            "pages": pages_a,
        },
        "doc_b": {
            "file_id": file_id_b,
            "filename": doc_b.filename,
            "pages": pages_b,
        },
        "matches_a": [],
        "matches_b": [],
    }

    if topic and topic.strip():
        # Find matching sections in both documents
        result["matches_a"] = find_term_locations(pages_a, topic.strip())
        result["matches_b"] = find_term_locations(pages_b, topic.strip())

    return result


def compare_documents_multi(
    doc_ids: list[int], topic: Optional[str] = None
) -> dict:
    """
    Compare multiple documents, optionally filtering by topic.

    Args:
        doc_ids: List of document IDs to compare
        topic: Optional search term to filter by

    Returns:
        Dict with comparison results containing:
        - documents: list of document info dicts
        - matches: list of match dicts with file_id, filename, page_number, snippet
    """
    documents = []
    all_matches = []

    for file_id in doc_ids:
        doc = get_file_by_id(file_id)
        if not doc or doc.status != "indexed":
            continue

        pages = get_document_pages(file_id)

        doc_info = {
            "file_id": file_id,
            "filename": doc.filename,
            "page_count": len(pages),
        }
        documents.append(doc_info)

        if topic and topic.strip():
            matches = find_term_locations(pages, topic.strip())
            for match in matches:
                all_matches.append({
                    "file_id": file_id,
                    "filename": doc.filename,
                    "page_number": match["page"],
                    "snippet": match["context"],
                })

    return {
        "documents": documents,
        "matches": all_matches,
        "topic": topic.strip() if topic else None,
    }

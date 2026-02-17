"""Search service - full-text search using SQLite FTS5."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.db import get_db
from app.models import SearchResult
from app.settings import settings

logger = logging.getLogger(__name__)


STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
    'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'or', 'that',
    'the', 'to', 'was', 'were', 'will', 'with', 'what', 'when', 'where',
    'which', 'who', 'why', 'how', 'can', 'could', 'would', 'should',
    'do', 'does', 'did', 'have', 'had', 'this', 'these', 'those',
    'i', 'you', 'we', 'they', 'my', 'your', 'our', 'their',
}


def parse_query(query: str) -> tuple[list[str], list[str]]:
    """
    Parse query into phrases and individual words.

    Quoted phrases are kept together, unquoted text is split into words.

    Args:
        query: Raw user query

    Returns:
        Tuple of (phrases, words) where phrases are quoted strings
        and words are individual unquoted terms
    """
    phrases = []
    words = []

    # Extract quoted phrases first
    phrase_pattern = r'"([^"]+)"'
    phrase_matches = re.findall(phrase_pattern, query)
    phrases.extend([p.strip() for p in phrase_matches if p.strip()])

    # Remove quoted phrases from query to get remaining words
    remaining = re.sub(phrase_pattern, ' ', query)

    # Clean and split remaining text
    remaining = re.sub(r'[^\w\s\-\']', ' ', remaining)

    for word in remaining.split():
        word = word.strip().lower()
        if word and word not in STOPWORDS and len(word) > 1:
            words.append(word)

    return phrases, words


def build_fts_query(query: str, mode: str = "and") -> str:
    """
    Build FTS5 query with support for AND/OR modes and quoted phrases.

    Args:
        query: Raw user query (may contain quoted phrases)
        mode: "and" (default) - all terms must match
              "or" - any term can match

    Returns:
        FTS5-compatible query string
    """
    phrases, words = parse_query(query)

    if not phrases and not words:
        return ""

    parts = []

    # Add phrases (exact phrase match in FTS5)
    for phrase in phrases:
        # Escape any special characters within the phrase
        clean_phrase = re.sub(r'[^\w\s]', ' ', phrase)
        clean_phrase = ' '.join(clean_phrase.split())  # Normalize whitespace
        if clean_phrase:
            parts.append(f'"{clean_phrase}"')

    # Add individual words with prefix matching
    for word in words:
        # Use FTS5 prefix syntax without extra quotes: word*
        # Quoting a single token then appending * ("word"*) is invalid
        # and causes the MATCH query to fail. Use bare prefix tokens.
        parts.append(f'{word}*')

    if not parts:
        return ""

    # Join with appropriate operator
    operator = " AND " if mode == "and" else " OR "

    # For AND mode with only words (no phrases), be more lenient
    # If AND returns nothing, caller can retry with OR
    return operator.join(parts)


def escape_fts_query(query: str, mode: str = "and") -> str:
    """
    Escape and prepare query for FTS5.

    Args:
        query: Raw user query
        mode: "and" or "or" for matching mode

    Returns:
        Escaped query safe for FTS5 MATCH
    """
    return build_fts_query(query, mode)


def search_pages(
    query: str,
    limit: Optional[int] = None,
    mode: str = "and",
    file_id: Optional[int] = None,
    fallback_to_or: bool = True,
) -> list[SearchResult]:
    """
    Search indexed pages using FTS5.

    Args:
        query: Search query string (supports quoted phrases)
        limit: Maximum results (defaults to settings.MAX_RETRIEVAL_RESULTS)
        mode: "and" (all terms required) or "or" (any term matches)
        file_id: Optional file ID to restrict search to specific document
        fallback_to_or: If True and AND mode returns no results, retry with OR

    Returns:
        List of SearchResult objects with citations
    """
    if limit is None:
        limit = settings.MAX_RETRIEVAL_RESULTS

    # Prepare query
    fts_query = escape_fts_query(query, mode)
    if not fts_query:
        return []

    def execute_search(fts_q: str) -> list[SearchResult]:
        with get_db() as conn:
            try:
                if file_id:
                    rows = conn.execute(
                        """
                        SELECT
                            f.id as file_id,
                            f.path,
                            f.filename,
                            page_fts.page_number,
                            snippet(page_fts, 3, '<mark>', '</mark>', '...', 64) as snippet,
                            rank
                        FROM page_fts
                        JOIN pdf_pages p ON page_fts.page_id = p.id
                        JOIN files f ON p.file_id = f.id
                        WHERE page_fts MATCH ? AND f.id = ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (fts_q, file_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT
                            f.id as file_id,
                            f.path,
                            f.filename,
                            page_fts.page_number,
                            snippet(page_fts, 3, '<mark>', '</mark>', '...', 64) as snippet,
                            rank
                        FROM page_fts
                        JOIN pdf_pages p ON page_fts.page_id = p.id
                        JOIN files f ON p.file_id = f.id
                        WHERE page_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (fts_q, limit),
                    ).fetchall()

                return [
                    SearchResult(
                        file_id=r["file_id"],
                        file_path=r["path"],
                        filename=r["filename"],
                        page_number=r["page_number"],
                        snippet=r["snippet"],
                        score=abs(r["rank"]),  # BM25 scores are negative
                    )
                    for r in rows
                ]

            except Exception as e:
                # Log error but return empty results for bad queries
                logger.warning(f"FTS search error: {e}")
                return []

    # Execute search
    results = execute_search(fts_query)

    # Fallback to OR mode if AND returns nothing
    if not results and mode == "and" and fallback_to_or:
        or_query = escape_fts_query(query, "or")
        if or_query and or_query != fts_query:
            results = execute_search(or_query)

    return results


def search_in_file(file_id: int, query: str, limit: int = 20, mode: str = "and") -> list[SearchResult]:
    """
    Search within a specific file.

    Args:
        file_id: File ID to search within
        query: Search query
        limit: Maximum results
        mode: "and" or "or" for matching mode

    Returns:
        List of SearchResult objects
    """
    # Use the main search function with file_id filter
    return search_pages(query, limit=limit, mode=mode, file_id=file_id)


def rank_results_by_phrase_proximity(
    results: list[SearchResult],
    query: str,
) -> list[SearchResult]:
    """
    Re-rank results to prioritize heading matches, exact phrases, and term proximity.

    Results with heading matches rank highest, followed by exact phrase matches,
    then those with scattered term matches.

    Args:
        results: Initial search results
        query: Original search query

    Returns:
        Re-ranked results list
    """
    if not results:
        return results

    phrases, words = parse_query(query)

    def score_result(result: SearchResult) -> tuple[int, int, int, float]:
        """
        Score a result for ranking.
        Returns (heading_score, phrase_matches, proximity_score, original_score)
        Higher is better for heading_score, phrase_matches, and proximity_score.
        """
        # Check for heading match (highest priority)
        heading_match, _ = page_has_heading_match(result.file_id, result.page_number, query)
        heading_score = 100 if heading_match else 0

        snippet_lower = result.snippet.lower()

        # Count exact phrase matches
        phrase_score = 0
        for phrase in phrases:
            if phrase.lower() in snippet_lower:
                phrase_score += 10  # Significant boost for phrase match

        # Calculate proximity score for individual words
        proximity_score = 0
        if len(words) >= 2:
            # Check if consecutive words appear close together
            word_positions = []
            for word in words:
                pos = snippet_lower.find(word.lower())
                if pos >= 0:
                    word_positions.append(pos)

            if len(word_positions) >= 2:
                word_positions.sort()
                # Score based on how close words are together
                for i in range(len(word_positions) - 1):
                    gap = word_positions[i + 1] - word_positions[i]
                    if gap < 50:  # Words within 50 chars
                        proximity_score += 5
                    elif gap < 100:
                        proximity_score += 2

        return (heading_score, phrase_score, proximity_score, result.score)

    # Sort by (heading_score desc, phrase_score desc, proximity_score desc, original_score asc)
    # Note: BM25 scores are already absolute, lower is better
    scored_results = [(score_result(r), r) for r in results]
    scored_results.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2], x[0][3]))

    return [r for _, r in scored_results]


def get_page_text(file_id: int, page_number: int) -> Optional[str]:
    """
    Get the full text of a specific page.

    Args:
        file_id: File ID
        page_number: Page number (1-indexed)

    Returns:
        Page text or None if not found
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT text FROM pdf_pages WHERE file_id = ? AND page_number = ?",
            (file_id, page_number),
        ).fetchone()

        return row["text"] if row else None


def _is_heading_line(line: str, line_index: int) -> bool:
    """
    Check if a line is likely a heading.

    Args:
        line: The text line to check
        line_index: 0-based index of line in the page

    Returns:
        True if line appears to be a heading
    """
    line = line.strip()
    if not line:
        return False

    # Check for common heading prefixes
    lower_line = line.lower()
    if lower_line.startswith(("article", "section")):
        return True

    # Check if line is mostly uppercase (>= 60% of alphabetic chars)
    alpha_chars = [c for c in line if c.isalpha()]
    if alpha_chars:
        upper_count = sum(1 for c in alpha_chars if c.isupper())
        if upper_count / len(alpha_chars) >= 0.6:
            return True

    # Short lines near top of page (first 10 lines, < 120 chars)
    if line_index < 10 and len(line) < 120:
        # Additional check: contains a number or dash (common in article headings)
        if re.search(r'[\d\-—:]', line):
            return True

    return False


def get_heading_lines(text: str) -> list[str]:
    """
    Extract candidate heading lines from page text.

    Args:
        text: Full page text

    Returns:
        List of heading line strings
    """
    lines = text.split('\n')
    headings = []

    for i, line in enumerate(lines):
        if _is_heading_line(line, i):
            headings.append(line.strip())

    return headings


def page_has_heading_match(file_id: int, page_number: int, query: str) -> tuple[bool, Optional[str]]:
    """
    Check if query matches a heading line on the page.

    Args:
        file_id: File ID
        page_number: Page number (1-indexed)
        query: Search query string

    Returns:
        Tuple of (has_match, matched_heading_line or None)
    """
    text = get_page_text(file_id, page_number)
    if not text:
        return False, None

    headings = get_heading_lines(text)
    if not headings:
        return False, None

    # Parse query for phrases and keywords
    phrases, words = parse_query(query)
    query_lower = query.lower()

    for heading in headings:
        heading_lower = heading.lower()

        # Check if full query appears in heading
        if query_lower in heading_lower:
            return True, heading

        # Check if any quoted phrase appears in heading
        for phrase in phrases:
            if phrase.lower() in heading_lower:
                return True, heading

        # Check if keywords appear in heading
        if words:
            matches = sum(1 for w in words if w in heading_lower)
            if matches >= len(words) * 0.5:  # At least half the keywords match
                return True, heading

    return False, None


def get_search_stats() -> dict:
    """Get search index statistics."""
    with get_db() as conn:
        total_pages = conn.execute("SELECT COUNT(*) FROM page_fts").fetchone()[0]
        total_files = conn.execute(
            "SELECT COUNT(DISTINCT file_id) FROM page_fts"
        ).fetchone()[0]

        return {
            "indexed_pages": total_pages,
            "indexed_files": total_files,
        }


def get_fts_sync_status() -> dict:
    """Check if FTS index is in sync with pdf_pages table."""
    with get_db() as conn:
        # Get counts per file from both tables
        pdf_pages_counts = conn.execute(
            """SELECT f.id, f.filename, COUNT(p.id) as page_count
               FROM files f
               LEFT JOIN pdf_pages p ON f.id = p.file_id
               WHERE f.status = 'indexed'
               GROUP BY f.id"""
        ).fetchall()

        fts_counts = conn.execute(
            """SELECT file_id, COUNT(*) as fts_count
               FROM page_fts
               GROUP BY file_id"""
        ).fetchall()

        fts_dict = {r["file_id"]: r["fts_count"] for r in fts_counts}

        out_of_sync = []
        for row in pdf_pages_counts:
            file_id = row["id"]
            pdf_count = row["page_count"]
            fts_count = fts_dict.get(file_id, 0)

            if pdf_count != fts_count:
                out_of_sync.append({
                    "file_id": file_id,
                    "filename": row["filename"],
                    "pdf_pages": pdf_count,
                    "fts_pages": fts_count,
                })

        return {
            "in_sync": len(out_of_sync) == 0,
            "out_of_sync": out_of_sync,
        }


def rebuild_fts_index() -> dict:
    """Rebuild FTS index from pdf_pages table."""
    with get_db() as conn:
        # Clear existing FTS entries
        conn.execute("DELETE FROM page_fts")

        # Repopulate from pdf_pages
        rows = conn.execute(
            """SELECT p.id, p.file_id, p.page_number, p.text
               FROM pdf_pages p
               JOIN files f ON p.file_id = f.id
               WHERE f.status = 'indexed'"""
        ).fetchall()

        for row in rows:
            conn.execute(
                "INSERT INTO page_fts (file_id, page_id, page_number, text) VALUES (?, ?, ?, ?)",
                (row["file_id"], row["id"], row["page_number"], row["text"]),
            )

        return {
            "rebuilt": True,
            "pages_indexed": len(rows),
        }


# --- Chunk-based search functions ---

@dataclass
class ChunkSearchResult:
    """Result from chunk-based search."""
    file_id: int
    file_path: str
    filename: str
    chunk_id: int
    heading: Optional[str]
    parent_heading: Optional[str]
    section_number: Optional[str]
    page_start: int
    page_end: int
    snippet: str
    score: float


def search_chunks(
    query: str,
    limit: Optional[int] = None,
    mode: str = "and",
    file_id: Optional[int] = None,
    fallback_to_or: bool = True,
) -> list[ChunkSearchResult]:
    """
    Search semantic chunks using FTS5.

    Chunks include heading metadata for better context.

    Args:
        query: Search query string
        limit: Maximum results
        mode: "and" or "or" matching mode
        file_id: Optional file ID to restrict search
        fallback_to_or: If True, retry with OR if AND returns nothing

    Returns:
        List of ChunkSearchResult with heading context
    """
    if limit is None:
        limit = settings.MAX_RETRIEVAL_RESULTS

    fts_query = escape_fts_query(query, mode)
    if not fts_query:
        return []

    def execute_chunk_search(fts_q: str) -> list[ChunkSearchResult]:
        with get_db() as conn:
            try:
                # Check if chunk_fts table exists
                table_check = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_fts'"
                ).fetchone()
                if not table_check:
                    return []

                if file_id:
                    rows = conn.execute(
                        """
                        SELECT
                            f.id as file_id,
                            f.path,
                            f.filename,
                            c.id as chunk_id,
                            c.heading,
                            c.parent_heading,
                            c.section_number,
                            c.page_start,
                            c.page_end,
                            snippet(chunk_fts, 3, '<mark>', '</mark>', '...', 64) as snippet,
                            rank
                        FROM chunk_fts
                        JOIN document_chunks c ON chunk_fts.chunk_id = c.id
                        JOIN files f ON c.file_id = f.id
                        WHERE chunk_fts MATCH ? AND f.id = ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (fts_q, file_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT
                            f.id as file_id,
                            f.path,
                            f.filename,
                            c.id as chunk_id,
                            c.heading,
                            c.parent_heading,
                            c.section_number,
                            c.page_start,
                            c.page_end,
                            snippet(chunk_fts, 3, '<mark>', '</mark>', '...', 64) as snippet,
                            rank
                        FROM chunk_fts
                        JOIN document_chunks c ON chunk_fts.chunk_id = c.id
                        JOIN files f ON c.file_id = f.id
                        WHERE chunk_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (fts_q, limit),
                    ).fetchall()

                return [
                    ChunkSearchResult(
                        file_id=r["file_id"],
                        file_path=r["path"],
                        filename=r["filename"],
                        chunk_id=r["chunk_id"],
                        heading=r["heading"],
                        parent_heading=r["parent_heading"],
                        section_number=r["section_number"],
                        page_start=r["page_start"],
                        page_end=r["page_end"],
                        snippet=r["snippet"],
                        score=abs(r["rank"]),
                    )
                    for r in rows
                ]

            except Exception as e:
                logger.warning(f"Chunk search error: {e}")
                return []

    results = execute_chunk_search(fts_query)

    if not results and mode == "and" and fallback_to_or:
        or_query = escape_fts_query(query, "or")
        if or_query and or_query != fts_query:
            results = execute_chunk_search(or_query)

    return results


def get_chunk_text(chunk_id: int) -> Optional[dict]:
    """
    Get full text and metadata for a specific chunk.

    Args:
        chunk_id: Chunk ID

    Returns:
        Dict with text and metadata, or None if not found
    """
    with get_db() as conn:
        row = conn.execute(
            """SELECT c.*, f.filename, f.path
               FROM document_chunks c
               JOIN files f ON c.file_id = f.id
               WHERE c.id = ?""",
            (chunk_id,),
        ).fetchone()

        if not row:
            return None

        return {
            "chunk_id": row["id"],
            "file_id": row["file_id"],
            "filename": row["filename"],
            "file_path": row["path"],
            "text": row["text"],
            "heading": row["heading"],
            "parent_heading": row["parent_heading"],
            "section_number": row["section_number"],
            "page_start": row["page_start"],
            "page_end": row["page_end"],
        }


def get_chunks_by_heading(heading_pattern: str, file_id: Optional[int] = None) -> list[dict]:
    """
    Get all chunks matching a heading pattern.

    Args:
        heading_pattern: SQL LIKE pattern for heading
        file_id: Optional file restriction

    Returns:
        List of chunk dicts
    """
    with get_db() as conn:
        if file_id:
            rows = conn.execute(
                """SELECT c.*, f.filename
                   FROM document_chunks c
                   JOIN files f ON c.file_id = f.id
                   WHERE c.heading LIKE ? AND f.id = ?
                   ORDER BY f.filename, c.chunk_number""",
                (heading_pattern, file_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT c.*, f.filename
                   FROM document_chunks c
                   JOIN files f ON c.file_id = f.id
                   WHERE c.heading LIKE ?
                   ORDER BY f.filename, c.chunk_number""",
                (heading_pattern,),
            ).fetchall()

        return [dict(row) for row in rows]


def get_document_structure(file_id: int) -> list[dict]:
    """
    Get the structure outline for a document.

    Args:
        file_id: File ID

    Returns:
        List of heading entries with page ranges
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT DISTINCT heading, parent_heading, section_number,
                      MIN(page_start) as page_start, MAX(page_end) as page_end
               FROM document_chunks
               WHERE file_id = ? AND heading IS NOT NULL
               GROUP BY heading
               ORDER BY page_start""",
            (file_id,),
        ).fetchall()

        return [dict(row) for row in rows]


def hybrid_search(
    query: str,
    limit: Optional[int] = None,
    file_id: Optional[int] = None,
) -> list[SearchResult]:
    """
    Hybrid search combining page-based and chunk-based results.

    Uses Reciprocal Rank Fusion (RRF) to combine rankings.

    Args:
        query: Search query
        limit: Maximum results
        file_id: Optional file restriction

    Returns:
        Combined SearchResult list
    """
    if limit is None:
        limit = settings.MAX_RETRIEVAL_RESULTS

    # Get results from both search methods
    page_results = search_pages(query, limit=limit * 2, file_id=file_id)
    chunk_results = search_chunks(query, limit=limit * 2, file_id=file_id)

    # Convert chunk results to page references (use page_start)
    # Create unified scoring using RRF
    k = 60  # RRF constant

    # Score by (file_id, page) key
    rrf_scores = {}

    # Add page results scores
    for rank, result in enumerate(page_results, start=1):
        key = (result.file_id, result.page_number)
        rrf_scores[key] = rrf_scores.get(key, 0) + 1.0 / (k + rank)

    # Add chunk results scores (mapped to page_start)
    for rank, chunk in enumerate(chunk_results, start=1):
        # Boost chunk results slightly since they have structural context
        key = (chunk.file_id, chunk.page_start)
        rrf_scores[key] = rrf_scores.get(key, 0) + 1.2 / (k + rank)

    # Merge and sort
    # Keep the original SearchResult objects for deduplication
    result_map = {}
    for result in page_results:
        key = (result.file_id, result.page_number)
        if key not in result_map:
            result_map[key] = result

    # Sort by RRF score
    sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

    final_results = []
    for key in sorted_keys[:limit]:
        if key in result_map:
            result = result_map[key]
            # Update score with RRF score for consistency
            result.score = rrf_scores[key]
            final_results.append(result)

    return final_results

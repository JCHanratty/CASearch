"""AI-powered document comparison service using Claude API."""

import logging
from typing import Optional

import anthropic

from app.db import get_db
from app.services.search import search_pages, search_chunks, get_page_text
from app.services.semantic_search import search_semantic_with_rerank, get_semantic_index_stats
from app.settings import settings

logger = logging.getLogger(__name__)

# Context size for comparison - token-aware budgeting
MAX_CONTEXT_PER_SOURCE = 8000  # Per-source soft cap
MAX_CONTEXT_BUDGET = 200000  # Total character budget (~50K tokens)


# System prompt for document comparison
COMPARISON_SYSTEM_PROMPT = """You are comparing collective agreements. Extract ONLY what is explicitly written.

## RULES
1. Extract specific values: dollar amounts, hours, rates, percentages
2. Use the EXACT wording from the documents - do not paraphrase numbers
3. If a provision exists but value isn't shown, write "Not in excerpts"
4. NEVER add qualifiers not in the source (like "after 8 hours" unless it says "8 hours")

## FORMAT

### Key Differences
- **[Provision]**: [Value from Doc A] vs [Value from Doc B] [citations]

Use exact wording. Examples of GOOD vs BAD:
- GOOD: "two (2) times the regular hourly rate" (exact quote)
- BAD: "2x after 8 hours" (added "after 8 hours" - hallucination)
- GOOD: "$130,845.26 annual" (exact from document)
- BAD: "$60/hr" (calculated, not stated)

### Comparison Table
| Provision | [Doc A Short Name] | [Doc B Short Name] |
|-----------|-------|-------|

Table cells should contain:
- Exact values as written in documents
- "Not in excerpts" if not found

### Notable Quotes
Include 1-2 direct quotes per document showing key contract language.

## DO NOT
- Add time thresholds (8 hours, etc.) unless explicitly stated
- Calculate hourly from annual or vice versa
- Use general knowledge about labor law
- Assume standard values"""


def get_relevant_content(
    file_ids: list[int],
    topic: str,
    limit_per_doc: int = 5
) -> dict[int, list[dict]]:
    """
    Get relevant content about a topic from multiple documents.

    Uses semantic search with cross-encoder re-ranking for best results,
    with fallback to chunk/page search if semantic search unavailable.
    Also searches for pages with dollar amounts to find actual wage/rate data.

    Args:
        file_ids: List of file IDs to search within
        topic: The topic or query to search for
        limit_per_doc: Maximum number of results per document

    Returns:
        Dict mapping file_id to list of {filename, page_number, text} results
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[int, list[dict]] = {}

    # Keywords that suggest we need pages with actual numbers
    needs_numbers = any(kw in topic.lower() for kw in [
        'wage', 'salary', 'pay', 'rate', 'hour', 'compensation',
        'overtime', 'benefit', 'allowance', 'premium', 'differential'
    ])

    # Check if semantic search is available
    semantic_available = False
    try:
        stats = get_semantic_index_stats()
        semantic_available = stats.get("index_exists") and stats.get("items_indexed", 0) > 0
    except Exception:
        pass

    def _retrieve_for_file(file_id: int) -> tuple[int, list[dict]]:
        """Retrieve relevant content for a single file (thread-safe)."""
        doc_results: list[dict] = []

        # Try semantic search first (best quality with re-ranking)
        if semantic_available:
            try:
                semantic_results = search_semantic_with_rerank(
                    topic,
                    limit=limit_per_doc,
                    file_id=file_id,
                    initial_limit=limit_per_doc * 3,
                )

                for result in semantic_results:
                    text = result.text
                    if len(text) < 500:
                        page_text = get_page_text(result.file_id, result.page_number)
                        if page_text:
                            text = page_text[:MAX_CONTEXT_PER_SOURCE]

                    doc_results.append({
                        "filename": result.filename,
                        "page_number": result.page_number,
                        "text": text[:MAX_CONTEXT_PER_SOURCE],
                        "heading": result.heading,
                    })

                if doc_results:
                    if needs_numbers:
                        existing_pages = {r["page_number"] for r in doc_results}
                        dollar_pages = _find_pages_with_numbers(file_id, existing_pages, limit=3)
                        doc_results.extend(dollar_pages)
                    return file_id, doc_results
            except Exception as e:
                logger.warning(f"Semantic search failed for file {file_id}: {e}")

        # Fallback: Try chunk-based search
        chunk_results = search_chunks(
            topic,
            limit=limit_per_doc,
            mode="or",
            file_id=file_id,
            fallback_to_or=True
        )

        if chunk_results:
            for chunk in chunk_results:
                text = chunk.snippet
                if len(text) < 500:
                    page_text = get_page_text(chunk.file_id, chunk.page_start)
                    if page_text:
                        text = page_text[:MAX_CONTEXT_PER_SOURCE]

                doc_results.append({
                    "filename": chunk.filename,
                    "page_number": chunk.page_start,
                    "text": text[:MAX_CONTEXT_PER_SOURCE],
                    "heading": chunk.heading,
                })
        else:
            # Last resort: page-based search
            page_results = search_pages(
                topic,
                limit=limit_per_doc,
                mode="or",
                file_id=file_id,
                fallback_to_or=True
            )

            for result in page_results:
                page_text = get_page_text(result.file_id, result.page_number)
                text = page_text[:MAX_CONTEXT_PER_SOURCE] if page_text else result.snippet

                doc_results.append({
                    "filename": result.filename,
                    "page_number": result.page_number,
                    "text": text,
                    "heading": None,
                })

        if needs_numbers:
            existing_pages = {r["page_number"] for r in doc_results}
            dollar_pages = _find_pages_with_numbers(file_id, existing_pages, limit=3)
            doc_results.extend(dollar_pages)

        return file_id, doc_results

    # Run retrieval in parallel across all documents
    max_workers = min(len(file_ids), 8)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_retrieve_for_file, fid): fid for fid in file_ids}
        for future in as_completed(futures):
            try:
                fid, doc_results = future.result()
                results[fid] = doc_results
            except Exception as e:
                fid = futures[future]
                logger.warning(f"Retrieval failed for file {fid}: {e}")
                results[fid] = []

    return results


def _find_pages_with_numbers(file_id: int, exclude_pages: set, limit: int = 3) -> list[dict]:
    """Find pages with wage/rate data, preferring document_tables over LIKE heuristic."""
    with get_db() as conn:
        # Get file info
        file_row = conn.execute(
            "SELECT filename FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if not file_row:
            return []

        filename = file_row["filename"]
        results = []

        # First try: use document_tables (structured table data)
        try:
            table_rows = conn.execute(
                """SELECT page_number, markdown_text, context_heading
                   FROM document_tables
                   WHERE file_id = ? AND is_wage_table = 1
                   ORDER BY page_number
                   LIMIT ?""",
                (file_id, limit * 2),
            ).fetchall()

            for row in table_rows:
                if row["page_number"] not in exclude_pages:
                    results.append({
                        "filename": filename,
                        "page_number": row["page_number"],
                        "text": row["markdown_text"][:MAX_CONTEXT_PER_SOURCE],
                        "heading": row["context_heading"] or "Wage/Rate Schedule",
                    })
                    if len(results) >= limit:
                        return results
        except Exception:
            pass  # document_tables may not exist yet

        # Fallback: LIKE-based heuristic for pages with dollar amounts
        if len(results) < limit:
            all_exclude = exclude_pages | {r["page_number"] for r in results}
            rows = conn.execute('''
                SELECT page_number, text
                FROM pdf_pages
                WHERE file_id = ?
                  AND (
                      (text LIKE '%$%' AND (text LIKE '%hour%' OR text LIKE '%annual%' OR text LIKE '%biweekly%'))
                      OR (text LIKE '%Appendix%' AND text LIKE '%$%')
                      OR (text LIKE '%Schedule%' AND text LIKE '%$%')
                  )
                  AND page_number NOT IN ({})
                ORDER BY
                    CASE
                        WHEN text LIKE '%Appendix%' THEN 0
                        WHEN text LIKE '%Schedule%' THEN 1
                        ELSE 2
                    END,
                    page_number
                LIMIT ?
            '''.format(','.join('?' * len(all_exclude)) if all_exclude else '-1'),
               (file_id, *all_exclude, limit - len(results)) if all_exclude else (file_id, limit - len(results))
            ).fetchall()

            for row in rows:
                results.append({
                    "filename": filename,
                    "page_number": row["page_number"],
                    "text": row["text"][:MAX_CONTEXT_PER_SOURCE],
                    "heading": "Wage/Rate Schedule",
                })

        return results


def _get_file_info(file_id: int) -> Optional[dict]:
    """Get file information from database."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, filename, path FROM files WHERE id = ?",
            (file_id,)
        ).fetchone()
        if row:
            return {"id": row["id"], "filename": row["filename"], "path": row["path"]}
        return None


def ai_compare_documents(
    file_ids: list[int],
    topic: Optional[str]
) -> dict:
    """
    Compare documents on a specific topic using AI analysis.

    Retrieves relevant content from all selected documents, then uses
    Claude to analyze and compare the content across documents.

    Args:
        file_ids: List of file IDs to compare
        topic: The topic to compare across documents

    Returns:
        Dict with:
            - analysis: The AI-generated comparison text
            - sources: List of {file_id, filename, page_number} used
            - documents: List of document names compared
            - topic: The comparison topic
            - error: Error message if something went wrong (optional)
    """
    # Check for API key
    if not settings.ANTHROPIC_API_KEY:
        return {
            "analysis": "",
            "sources": [],
            "documents": [],
            "topic": topic,
            "error": "API key not configured. Please set ANTHROPIC_API_KEY in your .env file."
        }

    # Validate file_ids
    if not file_ids or len(file_ids) < 2:
        return {
            "analysis": "",
            "sources": [],
            "documents": [],
            "topic": topic or "",
            "error": "At least two documents are required for comparison."
        }

    # Validate topic
    if not topic or not topic.strip():
        return {
            "analysis": "",
            "sources": [],
            "documents": [],
            "topic": "",
            "error": "Please enter a topic to compare across the documents."
        }

    # Get file information
    documents: list[dict] = []
    for file_id in file_ids:
        file_info = _get_file_info(file_id)
        if file_info:
            documents.append(file_info)

    if len(documents) < 2:
        return {
            "analysis": "",
            "sources": [],
            "documents": [],
            "topic": topic,
            "error": "Could not find enough valid documents for comparison."
        }

    # Get relevant content from all documents (more results for better comparison)
    try:
        content_by_doc = get_relevant_content(file_ids, topic, limit_per_doc=8)
    except Exception as e:
        return {
            "analysis": "",
            "sources": [],
            "documents": [d["filename"] for d in documents],
            "topic": topic,
            "error": f"Error retrieving document content: {str(e)}"
        }

    # Check if any content was found
    total_results = sum(len(results) for results in content_by_doc.values())
    if total_results == 0:
        return {
            "analysis": "",
            "sources": [],
            "documents": [d["filename"] for d in documents],
            "topic": topic,
            "error": f"No relevant content found for topic '{topic}' in the selected documents. Try a different search term or ensure documents are properly indexed."
        }

    # Build context string with labeled sections per document
    context_parts = []
    all_sources: list[dict] = []

    for doc in documents:
        file_id = doc["id"]
        filename = doc["filename"]
        doc_content = content_by_doc.get(file_id, [])

        if doc_content:
            context_parts.append(f"=== DOCUMENT: {filename} ===\n")

            for i, content in enumerate(doc_content, 1):
                page_num = content["page_number"]
                text = content["text"]
                heading = content.get("heading")

                heading_line = f" (Section: {heading})" if heading else ""
                context_parts.append(
                    f"[{filename}, Page {page_num}]{heading_line}:\n{text}\n\n"
                )

                all_sources.append({
                    "file_id": file_id,
                    "filename": filename,
                    "page_number": page_num
                })

            context_parts.append("\n")
        else:
            context_parts.append(f"=== DOCUMENT: {filename} ===\n")
            context_parts.append("No relevant content found for this topic.\n\n")

    context = "".join(context_parts)

    # Build user message
    user_message = f"""Compare these documents on: "{topic}"

IMPORTANT:
- Extract values EXACTLY as written in the text
- Do NOT add qualifiers not in the source (e.g., don't add "after 8 hours" unless the text says "8 hours")
- If information isn't in the excerpts, write "Not in excerpts"
- Quote the actual contract language when possible

Document excerpts:

{context}

Create your comparison using ONLY information from the text above. Do not add anything from general knowledge."""

    # Call Claude API
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            system=COMPARISON_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        analysis_text = response.content[0].text

        return {
            "analysis": analysis_text,
            "sources": all_sources,
            "documents": [d["filename"] for d in documents],
            "topic": topic,
        }

    except anthropic.AuthenticationError:
        return {
            "analysis": "",
            "sources": all_sources,
            "documents": [d["filename"] for d in documents],
            "topic": topic,
            "error": "Authentication failed. Please check your ANTHROPIC_API_KEY."
        }
    except anthropic.RateLimitError:
        return {
            "analysis": "",
            "sources": all_sources,
            "documents": [d["filename"] for d in documents],
            "topic": topic,
            "error": "Rate limit exceeded. Please try again in a moment."
        }
    except anthropic.APIError as e:
        return {
            "analysis": "",
            "sources": all_sources,
            "documents": [d["filename"] for d in documents],
            "topic": topic,
            "error": f"API error: {str(e)}"
        }
    except Exception as e:
        return {
            "analysis": "",
            "sources": all_sources,
            "documents": [d["filename"] for d in documents],
            "topic": topic,
            "error": f"An error occurred while processing the comparison: {str(e)}"
        }

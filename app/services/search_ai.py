"""AI-powered search analysis service using Claude API."""

import logging
from typing import Optional

import anthropic

from app.db import get_db
from app.services.search import search_pages, get_page_text
from app.services.semantic_search import search_semantic_with_rerank, get_semantic_index_stats
from app.settings import settings

logger = logging.getLogger(__name__)

# Context size for analysis - token-aware budgeting
MAX_CONTEXT_PER_SOURCE = 8000  # Per-source soft cap
MAX_CONTEXT_BUDGET = 200000  # Total character budget (~50K tokens)


# System prompt for search analysis
SEARCH_ANALYSIS_SYSTEM_PROMPT = """You are analyzing collective agreements to answer questions. Extract ONLY what is explicitly written.

## RULES
1. Extract specific values: dollar amounts, hours, rates, percentages, dates
2. Use the EXACT wording from the documents - do not paraphrase numbers
3. If information isn't in the excerpts, write "Not found in excerpts"
4. NEVER add qualifiers not in the source text
5. Quote the actual contract language when possible

## FORMAT

### Summary
Provide a clear, direct answer to the query based on the document excerpts.

### Key Details
- **[Provision/Item]**: [Exact value or quote from document] [citation]

Use exact wording. Examples of GOOD vs BAD:
- GOOD: "two (2) times the regular hourly rate" (exact quote)
- BAD: "2x after 8 hours" (added "after 8 hours" - hallucination)
- GOOD: "$130,845.26 annual" (exact from document)
- BAD: "$60/hr" (calculated, not stated)

### Relevant Quotes
Include 2-3 direct quotes from the documents showing key contract language.

## DO NOT
- Add time thresholds (8 hours, etc.) unless explicitly stated
- Calculate hourly from annual or vice versa
- Use general knowledge about labor law
- Assume standard values
- Make up information not in the excerpts"""


def get_relevant_content_for_query(
    query: str,
    file_id: Optional[int] = None,
    limit: int = 8
) -> list[dict]:
    """
    Get relevant content about a query from documents.

    Uses semantic search with cross-encoder re-ranking for best results,
    with fallback to page search if semantic search unavailable.
    Also searches for pages with dollar amounts for wage-related queries.

    Args:
        query: The search query
        file_id: Optional file ID to restrict search
        limit: Maximum number of results

    Returns:
        List of {filename, file_id, page_number, text, heading} results
    """
    results: list[dict] = []

    # Keywords that suggest we need pages with actual numbers
    needs_numbers = any(kw in query.lower() for kw in [
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

    # Try semantic search first (best quality with re-ranking)
    if semantic_available:
        try:
            semantic_results = search_semantic_with_rerank(
                query,
                limit=limit,
                file_id=file_id,
                initial_limit=limit * 3,  # Get more candidates for re-ranking
            )

            for result in semantic_results:
                # Get full text - use more context for analysis
                text = result.text
                if len(text) < 500:
                    page_text = get_page_text(result.file_id, result.page_number)
                    if page_text:
                        text = page_text[:MAX_CONTEXT_PER_SOURCE]

                results.append({
                    "filename": result.filename,
                    "file_id": result.file_id,
                    "page_number": result.page_number,
                    "text": text[:MAX_CONTEXT_PER_SOURCE],
                    "heading": result.heading,
                })

            if results:
                # Add dollar pages for wage-related searches
                if needs_numbers:
                    existing_pages = {(r["file_id"], r["page_number"]) for r in results}
                    dollar_pages = _find_pages_with_numbers(existing_pages, file_id, limit=3)
                    results.extend(dollar_pages)
                return results
        except Exception:
            pass  # Fall through to page search

    # Fallback: page-based search
    page_results = search_pages(
        query,
        limit=limit,
        mode="or",
        file_id=file_id,
        fallback_to_or=True
    )

    for result in page_results:
        page_text = get_page_text(result.file_id, result.page_number)
        text = page_text[:MAX_CONTEXT_PER_SOURCE] if page_text else result.snippet

        results.append({
            "filename": result.filename,
            "file_id": result.file_id,
            "page_number": result.page_number,
            "text": text,
            "heading": None,
        })

    # If searching for wages/rates and we need actual numbers, also get pages with $
    if needs_numbers:
        existing_pages = {(r["file_id"], r["page_number"]) for r in results}
        dollar_pages = _find_pages_with_numbers(existing_pages, file_id, limit=3)
        results.extend(dollar_pages)

    return results


def _find_pages_with_numbers(exclude_pages: set, file_id: Optional[int] = None, limit: int = 3) -> list[dict]:
    """Find pages that contain dollar amounts (likely wage/rate tables)."""
    with get_db() as conn:
        # Build query based on whether we're filtering by file
        if file_id:
            rows = conn.execute('''
                SELECT f.filename, p.file_id, p.page_number, p.text
                FROM pdf_pages p
                JOIN files f ON f.id = p.file_id
                WHERE p.file_id = ?
                  AND (
                      (p.text LIKE '%$%' AND (p.text LIKE '%hour%' OR p.text LIKE '%annual%' OR p.text LIKE '%biweekly%'))
                      OR (p.text LIKE '%Appendix%' AND p.text LIKE '%$%')
                      OR (p.text LIKE '%Schedule%' AND p.text LIKE '%$%')
                  )
                ORDER BY
                    CASE
                        WHEN p.text LIKE '%Appendix%' THEN 0
                        WHEN p.text LIKE '%Schedule%' THEN 1
                        ELSE 2
                    END,
                    p.page_number
                LIMIT ?
            ''', (file_id, limit * 2)).fetchall()
        else:
            rows = conn.execute('''
                SELECT f.filename, p.file_id, p.page_number, p.text
                FROM pdf_pages p
                JOIN files f ON f.id = p.file_id
                WHERE (
                      (p.text LIKE '%$%' AND (p.text LIKE '%hour%' OR p.text LIKE '%annual%' OR p.text LIKE '%biweekly%'))
                      OR (p.text LIKE '%Appendix%' AND p.text LIKE '%$%')
                      OR (p.text LIKE '%Schedule%' AND p.text LIKE '%$%')
                  )
                ORDER BY
                    CASE
                        WHEN p.text LIKE '%Appendix%' THEN 0
                        WHEN p.text LIKE '%Schedule%' THEN 1
                        ELSE 2
                    END,
                    p.page_number
                LIMIT ?
            ''', (limit * 2,)).fetchall()

        results = []
        for row in rows:
            key = (row["file_id"], row["page_number"])
            if key not in exclude_pages:
                results.append({
                    "filename": row["filename"],
                    "file_id": row["file_id"],
                    "page_number": row["page_number"],
                    "text": row["text"][:MAX_CONTEXT_PER_SOURCE],
                    "heading": "Wage/Rate Schedule",
                })
                if len(results) >= limit:
                    break

        return results


def ai_analyze_search(
    query: str,
    file_id: Optional[int] = None
) -> dict:
    """
    Analyze search results using AI.

    Retrieves relevant content and uses Claude to analyze and summarize
    the information about the query topic.

    Args:
        query: The search query/topic to analyze
        file_id: Optional file ID to restrict search

    Returns:
        Dict with:
            - analysis: The AI-generated analysis text
            - sources: List of {file_id, filename, page_number} used
            - query: The search query
            - error: Error message if something went wrong (optional)
    """
    # Check for API key
    if not settings.ANTHROPIC_API_KEY:
        return {
            "analysis": "",
            "sources": [],
            "query": query,
            "error": "API key not configured. Please set ANTHROPIC_API_KEY in your .env file."
        }

    # Validate query
    if not query or not query.strip():
        return {
            "analysis": "",
            "sources": [],
            "query": "",
            "error": "Please enter a search query."
        }

    # Get relevant content
    try:
        content_results = get_relevant_content_for_query(query, file_id=file_id, limit=10)
    except Exception as e:
        return {
            "analysis": "",
            "sources": [],
            "query": query,
            "error": f"Error retrieving document content: {str(e)}"
        }

    # Check if any content was found
    if not content_results:
        return {
            "analysis": "",
            "sources": [],
            "query": query,
            "error": f"No relevant content found for '{query}'. Try a different search term or ensure documents are properly indexed."
        }

    # Build context string
    context_parts = []
    all_sources: list[dict] = []
    seen_sources = set()

    for content in content_results:
        file_id_c = content["file_id"]
        filename = content["filename"]
        page_num = content["page_number"]
        text = content["text"]
        heading = content.get("heading")

        heading_line = f" (Section: {heading})" if heading else ""
        context_parts.append(
            f"[{filename}, Page {page_num}]{heading_line}:\n{text}\n\n"
        )

        # Track unique sources
        source_key = (file_id_c, page_num)
        if source_key not in seen_sources:
            seen_sources.add(source_key)
            all_sources.append({
                "file_id": file_id_c,
                "filename": filename,
                "page_number": page_num
            })

    context = "".join(context_parts)

    # Build user message
    user_message = f"""Analyze and answer this query: "{query}"

IMPORTANT:
- Extract values EXACTLY as written in the text
- Do NOT add qualifiers not in the source (e.g., don't add "after 8 hours" unless the text says "8 hours")
- If information isn't in the excerpts, write "Not found in excerpts"
- Quote the actual contract language when possible

Document excerpts:

{context}

Provide your analysis using ONLY information from the text above. Do not add anything from general knowledge."""

    # Call Claude API
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            system=SEARCH_ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        analysis_text = response.content[0].text

        return {
            "analysis": analysis_text,
            "sources": all_sources,
            "query": query,
        }

    except anthropic.AuthenticationError:
        return {
            "analysis": "",
            "sources": all_sources,
            "query": query,
            "error": "Authentication failed. Please check your ANTHROPIC_API_KEY."
        }
    except anthropic.RateLimitError:
        return {
            "analysis": "",
            "sources": all_sources,
            "query": query,
            "error": "Rate limit exceeded. Please try again in a moment."
        }
    except anthropic.APIError as e:
        return {
            "analysis": "",
            "sources": all_sources,
            "query": query,
            "error": f"API error: {str(e)}"
        }
    except Exception as e:
        return {
            "analysis": "",
            "sources": all_sources,
            "query": query,
            "error": f"An error occurred while processing the analysis: {str(e)}"
        }

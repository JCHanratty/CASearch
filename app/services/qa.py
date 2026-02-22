"""Q&A service - RAG pipeline with Claude API."""

import logging
import re
from pathlib import Path
from typing import Optional

import anthropic

from app.db import get_db
from app.models import Citation, QAResponse, SearchResult

logger = logging.getLogger(__name__)
from app.services.search import (
    search_pages, get_page_text, STOPWORDS, page_has_heading_match,
    search_chunks, get_chunk_text, hybrid_search, ChunkSearchResult
)
from app.services.synonyms import detect_document_reference, expand_query, get_synonyms
from app.services.rag import search_similar, vector_search_to_search_result, get_vector_index_stats
from app.services.semantic_search import (
    search_semantic, search_semantic_with_rerank, semantic_to_search_result,
    get_semantic_index_stats, SemanticSearchResult
)

# Context window configuration - token-aware budgeting
# ~4 chars per token for English text, budget ~50K tokens for context
MAX_CONTEXT_BUDGET = 200000  # Total character budget (~50K tokens)
MAX_CONTEXT_PER_SOURCE = 8000  # Per-source soft cap (increased from 4000)


def classify_query(query: str) -> dict:
    """
    Classify query type to optimize retrieval strategy and response format.

    Args:
        query: User's question

    Returns:
        Dict with query classification metadata
    """
    query_lower = query.lower()

    classification = {
        "type": "factual",  # factual, comparison, procedural, definition
        "expected_length": "short",  # short, medium, long
        "needs_multiple_docs": False,
        "needs_exact_match": False,
    }

    # Detect comparison queries
    comparison_indicators = ["compare", "difference", "vs", "versus", "between", "differ"]
    if any(ind in query_lower for ind in comparison_indicators):
        classification["type"] = "comparison"
        classification["needs_multiple_docs"] = True
        classification["expected_length"] = "medium"

    # Detect procedural queries
    procedural_indicators = ["how to", "how do", "process", "procedure", "steps", "what happens", "file a"]
    if any(ind in query_lower for ind in procedural_indicators):
        classification["type"] = "procedural"
        classification["expected_length"] = "long"

    # Detect definition queries
    definition_indicators = ["what is", "define", "meaning of", "definition", "what does", "what are"]
    if any(ind in query_lower for ind in definition_indicators):
        classification["type"] = "definition"

    # Detect specific value queries (need exact match)
    value_indicators = ["how much", "how many", "rate", "amount", "percentage", "days", "hours", "salary", "wage"]
    if any(ind in query_lower for ind in value_indicators):
        classification["needs_exact_match"] = True

    return classification
from app.settings import settings


# Base system prompt enforcing strict citation requirements
BASE_SYSTEM_PROMPT = """You are a contract analysis assistant for union local executives reviewing collective bargaining agreements.

CRITICAL RULES:
1. ONLY answer using the provided document excerpts. Never make up or infer information not explicitly stated.
2. ALWAYS cite your sources using [Source X] format for EVERY factual claim. No unsourced statements.
3. If the excerpts don't contain the answer, respond ONLY with: "Not found in the documents provided."
4. Be concise and direct. Quote specific contract language when relevant.
5. When citing, mention the document name and page number for clarity.
6. Do not speculate or provide general knowledge about labor law—stick to what's in the excerpts.
7. If information is partial or unclear in the excerpts, acknowledge the limitation.

FORMAT RULES (STRICTLY ENFORCED):
1. HEADING (REQUIRED if provided in context):
   - If a HEADING is detected in the context, you MUST start your response with that heading in bold
   - Format: **Exact Heading Text** (e.g., **Article 5 — Sick Time**)
   - The heading must be on its own line followed by a blank line

2. BULLET POINTS (REQUIRED):
   - Use the bullet character • (not -, *, or other markers)
   - Maximum 6 bullet points per response
   - Each bullet MUST contain a [Source X] citation
   - Keep each bullet focused on a single fact or provision
   - Format: • Statement about the contract provision [Source X]

3. CITATIONS (REQUIRED):
   - Every bullet point MUST end with a [Source X] citation
   - Use the exact format [Source 1], [Source 2], etc.
   - Multiple sources can be cited: [Source 1, Source 2]

4. SOURCE SUMMARY (REQUIRED):
   - End your response with a blank line followed by "Sources:"
   - List each cited source with document name and page number
   - Format: Sources:\n- Source 1: DocumentName.pdf, Page X\n- Source 2: DocumentName.pdf, Page Y

EXAMPLE RESPONSE FORMAT:
**Article 5 — Sick Time**

• Full-time employees accrue sick leave at one day per month [Source 1]
• Maximum accrual is 12 days per calendar year [Source 1]
• Sick time can be used for personal illness or family care [Source 2]

Sources:
- Source 1: Contract_2024.pdf, Page 15
- Source 2: Contract_2024.pdf, Page 16"""


# Query-type specific prompt additions
COMPARISON_PROMPT_ADDITION = """
COMPARISON FORMAT (REQUIRED for this query):
- Create a comparison table with SPECIFIC VALUES from each document
- Format: | Aspect | Document A | Document B |
- Every cell must have a specific value (numbers, dates, rates) or "Not specified"
- After the table, highlight the 2-3 most significant differences
- Cite sources for each cell value: [Source X]
"""

PROCEDURAL_PROMPT_ADDITION = """
PROCEDURE FORMAT (REQUIRED for this query):
- Present steps in numbered order (1, 2, 3...)
- Quote exact procedural language from the contract when available
- Include any deadlines or timeframes mentioned (e.g., "within 5 days")
- Note any exceptions or special conditions
- Each step MUST have a [Source X] citation
"""

DEFINITION_PROMPT_ADDITION = """
DEFINITION FORMAT (REQUIRED for this query):
- Start with the exact definition from the contract in quotes
- Quote the relevant text directly with citation
- Note any qualifications, conditions, or exceptions
- If multiple definitions exist across documents, list each separately
"""

VALUE_PROMPT_ADDITION = """
SPECIFIC VALUE REQUIREMENT:
- You MUST provide the exact numerical values requested
- Include: amounts ($X), rates (X%), durations (X days/hours), dates
- Format numbers clearly and consistently
- If different values exist for different conditions, list each separately
- NEVER use vague terms like "detailed schedule" or "varies" - find the actual numbers
"""


def get_adaptive_system_prompt(query_classification: dict) -> str:
    """
    Generate system prompt tailored to query type.

    Args:
        query_classification: Result from classify_query()

    Returns:
        Complete system prompt with query-specific additions
    """
    prompt = BASE_SYSTEM_PROMPT

    # Add query-type specific instructions
    if query_classification["type"] == "comparison":
        prompt += COMPARISON_PROMPT_ADDITION
    elif query_classification["type"] == "procedural":
        prompt += PROCEDURAL_PROMPT_ADDITION
    elif query_classification["type"] == "definition":
        prompt += DEFINITION_PROMPT_ADDITION

    # Add specific value requirement if needed
    if query_classification.get("needs_exact_match"):
        prompt += VALUE_PROMPT_ADDITION

    return prompt


# Default system prompt for backward compatibility
SYSTEM_PROMPT = BASE_SYSTEM_PROMPT


def _extract_keywords(question: str) -> list[str]:
    """
    Extract meaningful keywords from a question for fallback search.

    Args:
        question: User's question

    Returns:
        List of keywords (lowercased, stopwords removed)
    """
    # Remove punctuation and split
    words = re.sub(r'[^\w\s\-\']', ' ', question).lower().split()
    # Filter stopwords and short words
    keywords = [w for w in words if w not in STOPWORDS and len(w) > 2]
    return keywords


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """
    Truncate text at the nearest sentence boundary before max_chars.
    Falls back to word boundary if no sentence boundary found.
    """
    if len(text) <= max_chars:
        return text

    # Look for sentence boundary near the limit
    truncated = text[:max_chars]
    # Find last sentence-ending punctuation
    for i in range(len(truncated) - 1, max(0, len(truncated) - 200), -1):
        if truncated[i] in '.!?\n' and (i + 1 >= len(truncated) or truncated[i + 1] in ' \n\t'):
            return truncated[:i + 1]

    # Fall back to word boundary
    last_space = truncated.rfind(' ')
    if last_space > max_chars * 0.8:
        return truncated[:last_space]

    return truncated


def verify_content_against_sources(answer_text: str, context_parts: list[str], citations: list) -> list[str]:
    """
    Verify that specific values in Claude's answer appear in the source text.
    Extracts dollar amounts, percentages, day counts, and dates from the answer,
    then checks each appears verbatim in the cited source context.

    Returns list of warning strings for unverified values.
    """
    warnings = []
    source_text = ' '.join(context_parts).lower()

    # Extract dollar amounts from answer (e.g., $25.50, $130,845.26)
    dollar_pattern = r'\$[\d,]+(?:\.\d{1,2})?'
    dollar_amounts = re.findall(dollar_pattern, answer_text)
    for amount in dollar_amounts:
        # Normalize: remove commas for matching
        normalized = amount.replace(',', '')
        if normalized.lower() not in source_text and amount.lower() not in source_text:
            # Also try without $ sign in source
            num_only = normalized.replace('$', '')
            if num_only not in source_text:
                warnings.append(f"Unverified dollar amount: {amount}")

    # Extract percentages (e.g., 5%, 1.5%)
    pct_pattern = r'\d+(?:\.\d+)?%'
    percentages = re.findall(pct_pattern, answer_text)
    for pct in percentages:
        if pct.lower() not in source_text:
            warnings.append(f"Unverified percentage: {pct}")

    # Extract day/hour counts (e.g., "14 days", "8 hours", "3 months")
    duration_pattern = r'(\d+)\s+(days?|hours?|weeks?|months?|years?|shifts?)'
    durations = re.findall(duration_pattern, answer_text, re.IGNORECASE)
    for num, unit in durations:
        # Check both "14 days" and "fourteen (14) days" patterns
        search_patterns = [
            f"{num} {unit.lower()}",
            f"({num}) {unit.lower()}",
            f"{num}{unit.lower()}",
        ]
        found = any(p in source_text for p in search_patterns)
        if not found:
            warnings.append(f"Unverified duration: {num} {unit}")

    # Extract dates (e.g., "January 1, 2024", "2024-01-01")
    date_pattern = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}'
    dates = re.findall(date_pattern, answer_text, re.IGNORECASE)
    for date_str in dates:
        if date_str.lower() not in source_text:
            warnings.append(f"Unverified date: {date_str}")

    return warnings


def validate_qa_response(answer_text: str, heading_expected: bool = False) -> dict:
    """
    Validate that a QA response follows the required citation and formatting rules.

    Checks:
    - Has heading in bold format if heading_expected is True
    - Has citations in [Source X] format
    - Uses bullet points with the bullet character

    Args:
        answer_text: The response text to validate
        heading_expected: Whether a heading was provided in the context

    Returns:
        dict with {valid: bool, issues: list[str]}
    """
    issues = []

    # Skip validation for "not found" responses
    not_found_phrases = [
        "not found in the documents",
        "not found in documents",
        "no information available",
    ]
    answer_lower = answer_text.lower().strip()
    if any(phrase in answer_lower for phrase in not_found_phrases):
        # "Not found" responses don't need formatting validation
        return {"valid": True, "issues": []}

    # Check 1: Heading validation (if expected)
    if heading_expected:
        # Look for bold heading at the start: **Something**
        heading_pattern = r'^\*\*[^*]+\*\*'
        if not re.match(heading_pattern, answer_text.strip()):
            issues.append("Missing heading: Response should start with bold heading (e.g., **Article Title**)")

    # Check 2: Citation validation - look for [Source X] format
    citation_pattern = r'\[Source\s*\d+(?:\s*,\s*Source\s*\d+)*\]'
    citations_found = re.findall(citation_pattern, answer_text, re.IGNORECASE)
    if not citations_found:
        issues.append("Missing citations: No [Source X] citations found in response")

    # Check 3: Bullet point validation - look for the bullet character
    bullet_pattern = r'•'
    bullets_found = re.findall(bullet_pattern, answer_text)
    if not bullets_found:
        issues.append("Missing bullet points: Response should use bullet points with the bullet character")

    # Check 4: Each bullet should have a citation
    # Split by bullet and check each bullet line has a citation
    bullet_lines = [line.strip() for line in answer_text.split('•') if line.strip()]
    # Skip the first split part if it's just the heading
    if bullet_lines and bullets_found:
        # First part before any bullet is typically heading/intro
        bullet_content = bullet_lines[1:] if len(bullet_lines) > 1 else bullet_lines
        uncited_bullets = []
        for i, bullet in enumerate(bullet_content, 1):
            # Check if this bullet line contains a citation
            if not re.search(citation_pattern, bullet, re.IGNORECASE):
                # Only flag if this looks like a substantive bullet (not just whitespace or sources section)
                bullet_text = bullet.split('\n')[0].strip()  # First line of bullet
                if bullet_text and not bullet_text.lower().startswith('source'):
                    uncited_bullets.append(i)
        if uncited_bullets:
            issues.append(f"Uncited bullets: Bullet(s) {uncited_bullets} missing [Source X] citation")

    # Check 5: Maximum 6 bullets
    if len(bullets_found) > 6:
        issues.append(f"Too many bullets: Found {len(bullets_found)} bullets, maximum is 6")

    return {
        "valid": len(issues) == 0,
        "issues": issues
    }


def _sql_like_search(keywords: list[str], limit: int = 10) -> list:
    """
    Fallback search using SQL LIKE for substring matching.
    Post-filters with word-boundary regex to avoid false positives.

    Args:
        keywords: List of keywords to search
        limit: Maximum results

    Returns:
        List of result dicts with file_id, path, filename, page_number, snippet, score
    """
    if not keywords:
        return []

    with get_db() as conn:
        # Build LIKE conditions - any keyword match
        conditions = []
        params = []
        for kw in keywords[:5]:  # Limit to 5 keywords to avoid huge queries
            conditions.append("p.text LIKE ?")
            params.append(f"%{kw}%")

        if not conditions:
            return []

        where_clause = " OR ".join(conditions)
        # Fetch more than limit since we'll post-filter
        params.append(limit * 3)

        rows = conn.execute(
            f"""
            SELECT
                f.id as file_id,
                f.path,
                f.filename,
                p.page_number,
                p.text,
                1.0 as score
            FROM pdf_pages p
            JOIN files f ON p.file_id = f.id
            WHERE f.status = 'indexed' AND ({where_clause})
            ORDER BY f.filename, p.page_number
            LIMIT ?
            """,
            params,
        ).fetchall()

        # Post-filter: ensure whole-word matches
        results = []
        for r in rows:
            text = r["text"]
            has_whole_word = any(
                re.search(rf'\b{re.escape(kw)}\b', text, re.IGNORECASE)
                for kw in keywords[:5]
            )
            if has_whole_word:
                results.append({
                    "file_id": r["file_id"],
                    "path": r["path"],
                    "filename": r["filename"],
                    "page_number": r["page_number"],
                    "snippet": text[:200],
                    "score": r["score"],
                })
                if len(results) >= limit:
                    break

        return results


def _sql_like_search_in_file(keywords: list[str], file_id: int, limit: int = 10) -> list:
    """
    Fallback search using SQL LIKE within a specific file.
    Post-filters with word-boundary regex to avoid false positives.

    Args:
        keywords: List of keywords to search
        file_id: File ID to search within
        limit: Maximum results

    Returns:
        List of result dicts
    """
    if not keywords:
        return []

    with get_db() as conn:
        conditions = []
        like_params = []
        for kw in keywords[:5]:
            conditions.append("p.text LIKE ?")
            like_params.append(f"%{kw}%")

        if not conditions:
            return []

        where_clause = " OR ".join(conditions)
        # Parameters: file_id, then LIKE patterns, then limit
        params = [file_id] + like_params + [limit * 3]

        rows = conn.execute(
            f"""
            SELECT
                f.id as file_id,
                f.path,
                f.filename,
                p.page_number,
                p.text,
                1.0 as score
            FROM pdf_pages p
            JOIN files f ON p.file_id = f.id
            WHERE f.status = 'indexed' AND f.id = ? AND ({where_clause})
            ORDER BY p.page_number
            LIMIT ?
            """,
            params,
        ).fetchall()

        # Post-filter: ensure whole-word matches
        results = []
        for r in rows:
            text = r["text"]
            has_whole_word = any(
                re.search(rf'\b{re.escape(kw)}\b', text, re.IGNORECASE)
                for kw in keywords[:5]
            )
            if has_whole_word:
                results.append({
                    "file_id": r["file_id"],
                    "path": r["path"],
                    "filename": r["filename"],
                    "page_number": r["page_number"],
                    "snippet": text[:200],
                    "score": r["score"],
                })
                if len(results) >= limit:
                    break

        return results


def _vector_search(question: str, limit: int = 10, file_id: int = None) -> list[SearchResult]:
    """
    Perform vector similarity search using TF-IDF embeddings.

    Args:
        question: Search query
        limit: Maximum results
        file_id: Optional file ID to restrict search

    Returns:
        List of SearchResult objects
    """
    # Check if vector index exists
    stats = get_vector_index_stats()
    if not stats.get("index_exists") or stats.get("pages_indexed", 0) == 0:
        return []

    vector_results = search_similar(question, limit=limit, file_id=file_id)
    return [vector_search_to_search_result(r) for r in vector_results]


def _semantic_search(question: str, limit: int = 10, file_id: int = None, use_rerank: bool = True) -> tuple[list[SearchResult], list[SemanticSearchResult]]:
    """
    Perform semantic similarity search using sentence-transformers + ChromaDB.

    Uses two-stage retrieval with cross-encoder re-ranking for better precision.

    Args:
        question: Search query
        limit: Maximum results
        file_id: Optional file ID to restrict search
        use_rerank: Whether to use cross-encoder re-ranking (default True)

    Returns:
        Tuple of (SearchResult list, raw SemanticSearchResult list for heading context)
    """
    # Check if semantic index exists
    stats = get_semantic_index_stats()
    if not stats.get("index_exists") or stats.get("items_indexed", 0) == 0:
        return [], []

    try:
        if use_rerank:
            # Two-stage retrieval: bi-encoder retrieval + cross-encoder re-ranking
            semantic_results = search_semantic_with_rerank(question, limit=limit, file_id=file_id)
        else:
            # Single-stage bi-encoder only
            semantic_results = search_semantic(question, limit=limit, file_id=file_id)
        search_results = [semantic_to_search_result(r) for r in semantic_results]
        return search_results, semantic_results
    except Exception as e:
        import logging
        logging.warning(f"Semantic search failed: {e}")
        return [], []


def _merge_hybrid_results(
    fts_results: list[SearchResult],
    vector_results: list[SearchResult],
    limit: int = 10
) -> list[SearchResult]:
    """
    Merge FTS and vector search results using reciprocal rank fusion.

    This combines keyword-based (FTS) and semantic (vector) search results,
    giving weight to documents that appear in both result sets.

    Args:
        fts_results: Results from FTS5 search
        vector_results: Results from vector similarity search
        limit: Maximum results to return

    Returns:
        Merged and re-ranked list of SearchResult objects
    """
    # Reciprocal Rank Fusion constant (typical value is 60)
    k = 60

    # Build score map keyed by (file_id, page_number)
    scores = {}

    # Score FTS results
    for rank, result in enumerate(fts_results):
        key = (result.file_id, result.page_number)
        rrf_score = 1.0 / (k + rank + 1)
        if key not in scores:
            scores[key] = {"result": result, "score": 0.0}
        scores[key]["score"] += rrf_score

    # Score vector results
    for rank, result in enumerate(vector_results):
        key = (result.file_id, result.page_number)
        rrf_score = 1.0 / (k + rank + 1)
        if key not in scores:
            scores[key] = {"result": result, "score": 0.0}
        scores[key]["score"] += rrf_score

    # Sort by combined score (descending) and return top results
    sorted_items = sorted(scores.values(), key=lambda x: x["score"], reverse=True)

    return [item["result"] for item in sorted_items[:limit]]


def _weighted_rrf_fusion(
    result_lists: list[list[SearchResult]],
    weights: list[float] = None,
    k: int = 60,
    limit: int = 10,
) -> list[SearchResult]:
    """
    Weighted Reciprocal Rank Fusion for combining multiple result lists.

    Args:
        result_lists: List of SearchResult lists from different retrieval methods
        weights: Optional weights for each result list (default: semantic gets 1.5x)
        k: RRF constant (default 60)
        limit: Maximum results to return

    Returns:
        Merged and re-ranked list of SearchResult objects
    """
    if not result_lists:
        return []

    # Default weights: semantic gets higher weight
    if weights is None:
        weights = [1.5] + [1.0] * (len(result_lists) - 1)
    weights = weights[:len(result_lists)]

    scores = {}
    result_map = {}

    for list_idx, results in enumerate(result_lists):
        if not results:
            continue
        weight = weights[list_idx] if list_idx < len(weights) else 1.0

        for rank, result in enumerate(results):
            key = (result.file_id, result.page_number)
            rrf_score = weight / (k + rank + 1)

            if key not in scores:
                scores[key] = 0.0
                result_map[key] = result
            scores[key] += rrf_score

    # Sort by fused score
    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    return [result_map[key] for key in sorted_keys[:limit]]


def _query_wage_tables(question: str, file_id: int = None, limit: int = 5) -> list[SearchResult]:
    """
    Query document_tables for wage/rate data when query needs exact values.

    Args:
        question: The user's question
        file_id: Optional file ID to scope search
        limit: Maximum results

    Returns:
        List of SearchResult objects from wage tables
    """
    query_lower = question.lower()
    # Only query wage tables for value-related queries
    value_indicators = [
        'wage', 'salary', 'pay', 'rate', 'hour', 'compensation',
        'how much', 'how many', 'amount', 'percentage', 'step',
        'classification', 'grade', 'overtime', 'premium', 'schedule',
    ]
    if not any(ind in query_lower for ind in value_indicators):
        return []

    try:
        with get_db() as conn:
            if file_id:
                rows = conn.execute(
                    """SELECT dt.*, f.path, f.filename
                       FROM document_tables dt
                       JOIN files f ON dt.file_id = f.id
                       WHERE dt.is_wage_table = 1 AND dt.file_id = ?
                       ORDER BY dt.page_number
                       LIMIT ?""",
                    (file_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT dt.*, f.path, f.filename
                       FROM document_tables dt
                       JOIN files f ON dt.file_id = f.id
                       WHERE dt.is_wage_table = 1
                       ORDER BY dt.page_number
                       LIMIT ?""",
                    (limit,),
                ).fetchall()

            results = []
            for r in rows:
                results.append(SearchResult(
                    file_id=r["file_id"],
                    file_path=r["path"],
                    filename=r["filename"],
                    page_number=r["page_number"],
                    snippet=r["markdown_text"][:200],
                    score=2.0,  # High base score for wage tables
                ))
            return results
    except Exception as e:
        logger.warning(f"Wage table query failed: {e}")
        return []


def _parallel_hybrid_retrieve(
    question: str,
    limit: int = 10,
    file_id: int = None,
) -> tuple[list[SearchResult], str, list]:
    """
    Run multiple retrieval strategies in parallel and fuse results.

    Much faster and more robust than sequential fallback approach.

    Args:
        question: User's question
        limit: Maximum results
        file_id: Optional file ID to scope search

    Returns:
        Tuple of (results list, retrieval_method string, context_results for heading info)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Define retrieval strategies to run in parallel
    def run_semantic():
        results, raw = _semantic_search(question, limit=limit * 2, file_id=file_id, use_rerank=True)
        return ("semantic", results, raw)

    def run_chunk_fts():
        chunks = search_chunks(question, limit=limit * 2, mode="or", file_id=file_id, fallback_to_or=False)
        return ("chunk", _chunk_results_to_search_results(chunks), chunks)

    def run_page_fts():
        results = search_pages(question, limit=limit * 2, mode="or", file_id=file_id, fallback_to_or=False)
        return ("fts", results, [])

    def run_expanded():
        expanded_queries = expand_query(question)
        if len(expanded_queries) > 1:
            results = search_pages(expanded_queries[1], limit=limit, mode="or", file_id=file_id, fallback_to_or=False)
            return ("expanded", results, [])
        return ("expanded", [], [])

    # Run strategies in parallel
    all_results = []
    context_results = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(run_semantic),
            executor.submit(run_chunk_fts),
            executor.submit(run_page_fts),
            executor.submit(run_expanded),
        ]

        try:
            for future in as_completed(futures, timeout=30):
                try:
                    method, results, raw = future.result(timeout=10)
                    if results:
                        all_results.append(results)
                        # Keep semantic or chunk context for heading info
                        if method in ("semantic", "chunk") and raw:
                            context_results.extend(raw)
                except Exception as e:
                    logger.warning(f"Retrieval strategy failed: {e}")
                    continue
        except TimeoutError:
            logger.warning("Parallel retrieval timed out after 30s")

    if not all_results:
        return [], "none", []

    # Fuse results with weighted RRF (semantic gets 1.5x weight)
    weights = [1.5, 1.2, 1.0, 0.8][:len(all_results)]
    fused = _weighted_rrf_fusion(all_results, weights=weights, limit=limit)

    return fused, "hybrid_parallel", context_results


def _chunk_results_to_search_results(chunk_results: list[ChunkSearchResult]) -> list[SearchResult]:
    """Convert chunk search results to SearchResult objects for compatibility."""
    results = []
    for chunk in chunk_results:
        # Use page_start as the page number reference
        results.append(SearchResult(
            file_id=chunk.file_id,
            file_path=chunk.file_path,
            filename=chunk.filename,
            page_number=chunk.page_start,
            snippet=chunk.snippet,
            score=chunk.score,
        ))
    return results


def _retrieve_with_fallback(question: str, limit: int = None) -> tuple[list, str, list]:
    """
    Retrieve relevant pages with parallel hybrid retrieval (primary) and multi-stage fallback.

    Primary: Parallel hybrid retrieval - runs semantic, chunk FTS, page FTS, and expanded
    queries simultaneously, then fuses results with weighted RRF.

    Fallback stages (if parallel fails):
    0. Detect document reference and scope search
    1. Semantic search (sentence-transformers + ChromaDB) - best for meaning
    2. Chunk-based FTS search (has heading context)
    3. FTS5 AND mode (all terms required)
    4. FTS5 OR mode (any term matches)
    5. Synonym expansion search
    6. SQL LIKE substring search

    Args:
        question: User's question
        limit: Maximum results

    Returns:
        Tuple of (results list, retrieval_method string, context_results for heading info)
    """
    if limit is None:
        limit = settings.MAX_RETRIEVAL_RESULTS

    # Stage 0: Detect if query references a specific document (e.g., "sick leave for Spruce Grove")
    scoped_file_id, topic_query = detect_document_reference(question)

    # Primary: Try parallel hybrid retrieval first (faster and more robust)
    if not scoped_file_id:
        results, method, context = _parallel_hybrid_retrieve(question, limit=limit)
        if results:
            # If query needs exact values, also fetch wage table data
            table_results = _query_wage_tables(question, file_id=scoped_file_id)
            if table_results:
                # Fuse table results with existing results (tables get 2.0x weight)
                all_lists = [table_results, results]
                weights = [2.0, 1.0]
                fused = _weighted_rrf_fusion(all_lists, weights=weights, limit=limit)
                return fused, method + "+tables", context
            return results, method, context

    if scoped_file_id:
        # Document-scoped search: search within the specific document
        # Try semantic search first (best for understanding meaning)
        semantic_results, raw_semantic = _semantic_search(topic_query, limit=limit, file_id=scoped_file_id)
        if semantic_results:
            return semantic_results, "semantic_scoped", raw_semantic

        # Try chunk FTS search (has heading context)
        chunk_results = search_chunks(topic_query, limit=limit, mode="and", file_id=scoped_file_id, fallback_to_or=False)
        if chunk_results:
            return _chunk_results_to_search_results(chunk_results), "chunk_scoped_and", chunk_results

        chunk_results = search_chunks(topic_query, limit=limit, mode="or", file_id=scoped_file_id, fallback_to_or=False)
        if chunk_results:
            return _chunk_results_to_search_results(chunk_results), "chunk_scoped_or", chunk_results

        # Try FTS within the document
        results = search_pages(topic_query, limit=limit, mode="and", file_id=scoped_file_id, fallback_to_or=False)
        if results:
            return results, "fts_scoped_and", []

        results = search_pages(topic_query, limit=limit, mode="or", file_id=scoped_file_id, fallback_to_or=False)
        if results:
            return results, "fts_scoped_or", []

        # Try synonym expansion within the scoped document
        expanded_queries = expand_query(topic_query)
        for expanded in expanded_queries[1:]:  # Skip original (already tried)
            results = search_pages(expanded, limit=limit, mode="or", file_id=scoped_file_id, fallback_to_or=False)
            if results:
                return results, "fts_scoped_synonym", []

        # Try vector search within the scoped document
        vector_results = _vector_search(topic_query, limit=limit, file_id=scoped_file_id)
        if vector_results:
            return vector_results, "vector_scoped", []

        # Fallback to LIKE search within document
        keywords = _extract_keywords(topic_query)
        if keywords:
            like_results = _sql_like_search_in_file(keywords, scoped_file_id, limit=limit)
            if like_results:
                results = [
                    SearchResult(
                        file_id=r["file_id"],
                        file_path=r["path"],
                        filename=r["filename"],
                        page_number=r["page_number"],
                        snippet=r["snippet"],
                        score=r["score"],
                    )
                    for r in like_results
                ]
                return results, "sql_like_scoped", []

    # Stage 1: Semantic search (best for understanding meaning)
    semantic_results, raw_semantic = _semantic_search(question, limit=limit)
    if semantic_results:
        return semantic_results, "semantic", raw_semantic

    # Stage 2: Chunk-based FTS search (has heading context)
    chunk_results = search_chunks(question, limit=limit, mode="and", fallback_to_or=False)
    if chunk_results:
        return _chunk_results_to_search_results(chunk_results), "chunk_and", chunk_results

    chunk_results = search_chunks(question, limit=limit, mode="or", fallback_to_or=False)
    if chunk_results:
        return _chunk_results_to_search_results(chunk_results), "chunk_or", chunk_results

    # Stage 3: FTS5 AND mode (strict) - full query
    results = search_pages(question, limit=limit, mode="and", fallback_to_or=False)
    if results:
        return results, "fts_and", []

    # Stage 4: FTS5 OR mode (relaxed)
    results = search_pages(question, limit=limit, mode="or", fallback_to_or=False)
    if results:
        return results, "fts_or", []

    # Stage 4: Synonym expansion
    expanded_queries = expand_query(question)
    for expanded in expanded_queries[1:]:  # Skip original (already tried)
        results = search_pages(expanded, limit=limit, mode="or", fallback_to_or=False)
        if results:
            return results, "fts_synonym", []

    # Stage 5: SQL LIKE substring search
    keywords = _extract_keywords(question)
    if keywords:
        like_results = _sql_like_search(keywords, limit=limit)
        if like_results:
            results = [
                SearchResult(
                    file_id=r["file_id"],
                    file_path=r["path"],
                    filename=r["filename"],
                    page_number=r["page_number"],
                    snippet=r["snippet"],
                    score=r["score"],
                )
                for r in like_results
            ]
            return results, "sql_like", []

    # Stage 6: Vector similarity search (semantic search via TF-IDF)
    vector_results = _vector_search(question, limit=limit)
    if vector_results:
        return vector_results, "vector", []

    # Stage 7: Hybrid search - try combining FTS OR with vector search
    # This can help when neither method alone finds good results
    # by leveraging both keyword and semantic matching
    fts_or_results = search_pages(question, limit=limit * 2, mode="or", fallback_to_or=False)
    vector_for_hybrid = _vector_search(question, limit=limit * 2)

    if fts_or_results or vector_for_hybrid:
        hybrid_results = _merge_hybrid_results(fts_or_results, vector_for_hybrid, limit=limit)
        if hybrid_results:
            return hybrid_results, "hybrid", []

    return [], "none", []


def answer_question(question: str) -> QAResponse:
    """
    Answer a question using RAG pipeline.

    1. Retrieve relevant pages via FTS5 with fallback
    2. Format excerpts for Claude
    3. Call Claude with citation instructions
    4. Parse response

    Args:
        question: User's question

    Returns:
        QAResponse with answer, citations, and no_evidence flag
    """
    # Step 0: Classify query for adaptive prompting
    query_class = classify_query(question)

    # Step 1: Check for API key
    if not settings.ANTHROPIC_API_KEY:
        return QAResponse(
            answer="API key not configured. Please set ANTHROPIC_API_KEY in your .env file.",
            citations=[],
            no_evidence=True,
        )

    # Step 2: Retrieve relevant pages with fallback
    search_results, retrieval_method, chunk_results = _retrieve_with_fallback(
        question, limit=settings.MAX_RETRIEVAL_RESULTS
    )

    # Build retrieval diagnostics
    diagnostics = {
        "method": retrieval_method,
        "results_count": len(search_results),
        "chunk_results_count": len(chunk_results) if chunk_results else 0,
        "query_classification": query_class,
    }
    logger.info(f"Retrieval: method={retrieval_method}, results={len(search_results)}, chunks={len(chunk_results) if chunk_results else 0}")

    if not search_results:
        return QAResponse(
            answer="Not found in the documents provided. No relevant content was found in the indexed collective agreements. Make sure documents are indexed and try rephrasing your question.",
            citations=[],
            no_evidence=True,
        )

    # Step 3: Build text context from search results
    context_parts = []
    citations_list = []
    detected_heading = None
    heading_detected = False

    # Check for heading from chunk results first (more reliable)
    if chunk_results:
        for chunk in chunk_results:
            if chunk.heading:
                heading_detected = True
                detected_heading = chunk.heading
                break

    # Fallback: Check if top result has a heading match from page analysis
    if not heading_detected and search_results:
        top_result = search_results[0]
        has_heading, heading_line = page_has_heading_match(
            top_result.file_id, top_result.page_number, question
        )
        if has_heading and heading_line:
            heading_detected = True
            detected_heading = heading_line

    # Build a map of context data by (file_id, page) for easy lookup
    # Supports both ChunkSearchResult (has page_start) and SemanticSearchResult (has page_number)
    context_map = {}
    if chunk_results:
        for chunk in chunk_results:
            # Handle both ChunkSearchResult and SemanticSearchResult
            page_key = getattr(chunk, 'page_start', None) or getattr(chunk, 'page_number', 1)
            key = (chunk.file_id, page_key)
            if key not in context_map:
                context_map[key] = chunk

    total_context_chars = 0
    context_truncated = False

    for i, result in enumerate(search_results):
        # Check budget before adding more context
        if total_context_chars >= MAX_CONTEXT_BUDGET:
            logger.info(f"Context budget reached at source {i+1}/{len(search_results)} ({total_context_chars} chars)")
            context_truncated = True
            break

        remaining_budget = MAX_CONTEXT_BUDGET - total_context_chars
        source_limit = min(MAX_CONTEXT_PER_SOURCE, remaining_budget)

        # Check if we have context data for this result (better context)
        context_key = (result.file_id, result.page_number)
        context_data = context_map.get(context_key)

        if context_data:
            # Try to get full chunk text from database if chunk_id is available
            chunk_id = getattr(context_data, 'chunk_id', None)
            if chunk_id:
                chunk_full = get_chunk_text(chunk_id)
                if chunk_full:
                    text_preview = _truncate_at_sentence(chunk_full["text"], source_limit)
                else:
                    text_preview = getattr(context_data, 'text', '') or getattr(context_data, 'snippet', '')
                    text_preview = _truncate_at_sentence(text_preview, source_limit)
            else:
                text_preview = getattr(context_data, 'text', '') or getattr(context_data, 'snippet', '')
                text_preview = _truncate_at_sentence(text_preview, source_limit)

            source_label = f"Source {i+1}"

            # Include heading context from metadata (works for both types)
            heading_info = ""
            heading = getattr(context_data, 'heading', None)
            if heading:
                heading_info = f"\nHEADING: {heading}"
                parent_heading = getattr(context_data, 'parent_heading', None)
                section_number = getattr(context_data, 'section_number', None)
                if parent_heading:
                    heading_info = f"\nPARENT: {parent_heading}{heading_info}"
                if section_number:
                    heading_info += f" (Section {section_number})"

            page_start = getattr(context_data, 'page_start', None) or getattr(context_data, 'page_number', result.page_number)
            page_end = getattr(context_data, 'page_end', None) or page_start
            page_range = f"Pages {page_start}-{page_end}" if page_start != page_end else f"Page {page_start}"

            part = f"[{source_label}] {result.filename}, {page_range}:{heading_info}\n{text_preview}\n"
            context_parts.append(part)
            total_context_chars += len(part)

            citations_list.append(
                Citation(
                    file_id=result.file_id,
                    file_path=result.file_path,
                    filename=result.filename,
                    page_number=result.page_number,
                    cited_text=text_preview[:200],
                )
            )
            continue

        # Fallback to page-based text
        page_text = get_page_text(result.file_id, result.page_number)
        if page_text:
            text_preview = _truncate_at_sentence(page_text, source_limit)
            source_label = f"Source {i+1}"

            if i == 0 and detected_heading:
                part = f"[{source_label}] {result.filename}, Page {result.page_number}:\nHEADING: {detected_heading}\n{text_preview}\n"
            else:
                part = f"[{source_label}] {result.filename}, Page {result.page_number}:\n{text_preview}\n"

            context_parts.append(part)
            total_context_chars += len(part)

            citations_list.append(
                Citation(
                    file_id=result.file_id,
                    file_path=result.file_path,
                    filename=result.filename,
                    page_number=result.page_number,
                    cited_text=text_preview[:200],
                )
            )

    if context_truncated:
        diagnostics["context_truncated"] = True
        diagnostics["sources_used"] = len(context_parts)
        diagnostics["sources_available"] = len(search_results)

    if not context_parts:
        return QAResponse(
            answer="Not found in the documents provided. Could not retrieve page content.",
            citations=[],
            no_evidence=True,
        )

    context = "\n---\n".join(context_parts)

    # Build retrieval note for transparency
    retrieval_parts = []
    retrieval_parts.append(f"Retrieval method: {retrieval_method.upper().replace('_', '-')}")
    if heading_detected:
        retrieval_parts.append("Heading match detected: Yes")
    else:
        retrieval_parts.append("Heading match detected: No")
    retrieval_note = "\n[" + ", ".join(retrieval_parts) + "]"

    # Step 4: Call Claude with adaptive prompt
    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        # Get adaptive system prompt based on query type
        system_prompt = get_adaptive_system_prompt(query_class)

        # Build format instructions based on whether heading was detected
        heading_instruction = ""
        if heading_detected and detected_heading:
            heading_instruction = f"""
HEADING DETECTED: "{detected_heading}"
You MUST start your response with this heading in bold: **{detected_heading}**
"""
        else:
            heading_instruction = """
No heading detected. Start directly with bullet points.
"""

        user_message = f"""Here are excerpts from collective agreement documents:

{context}

---

Question: {question}
{heading_instruction}
FORMAT REQUIREMENTS (follow exactly):
1. {"Start with bold heading: **" + detected_heading + "**" if heading_detected else "Start directly with bullet points"}
2. Use bullet character for all points
3. Each bullet MUST have [Source X] citation at the end
4. Maximum 6 bullets
5. End with "Sources:" section listing document names and page numbers

Answer based ONLY on the excerpts above. If the answer is not in the excerpts, say "Not found in the documents provided."
{retrieval_note}
"""

        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,  # Increased for detailed responses
            system=system_prompt,  # Adaptive prompt based on query type
            messages=[{"role": "user", "content": user_message}],
            timeout=60.0,
        )

        answer_text = response.content[0].text

        # Check if the response indicates no evidence
        # Only flag as no_evidence if the response is PRIMARILY a "not found" message
        # If citations are present, evidence was found regardless of partial "not found" notes
        no_evidence_phrases = [
            "not found in the documents",
            "not found in documents",
            "no information available",
            "documents do not contain",
            "cannot find",
            "no relevant information",
            "not mentioned in",
            "does not contain",
        ]

        answer_lower = answer_text.lower().strip()

        # Check if citations are present in the answer (e.g., [Source 1], [Source 2])
        has_citations = bool(re.search(r'\[source\s*\d+\]', answer_lower))

        # Only flag no_evidence if:
        # 1. Response starts with a "not found" phrase, OR
        # 2. Response is very short (<200 chars) and contains a "not found" phrase
        # AND no citations are present
        starts_with_not_found = any(answer_lower.startswith(phrase) for phrase in no_evidence_phrases)
        is_short_not_found = len(answer_text) < 200 and any(phrase in answer_lower for phrase in no_evidence_phrases)

        no_evidence = (starts_with_not_found or is_short_not_found) and not has_citations

        # Extract which sources were cited (check various citation patterns)
        cited_sources = []
        for i, citation in enumerate(citations_list):
            source_num = i + 1
            # Check for [Source X], Source X, or filename/page mentions
            patterns = [
                f"[Source {source_num}]",
                f"Source {source_num}",
                f"source {source_num}",
            ]
            # Also check if filename and page are mentioned together
            filename_mentioned = citation.filename.lower() in answer_text.lower()
            page_mentioned = f"page {citation.page_number}" in answer_text.lower()

            if any(p in answer_text for p in patterns) or (filename_mentioned and page_mentioned):
                cited_sources.append(citation)

        # If no specific sources cited but answer given, include all as potential sources
        if not cited_sources and not no_evidence:
            cited_sources = citations_list[:3]  # Top 3 sources

        # Build synonyms_used dict if synonym expansion was used
        synonyms_used = None
        if "synonym" in retrieval_method:
            # Identify which terms were expanded
            question_lower = question.lower()
            question_words = question_lower.split()
            synonyms_used = {}
            for word in question_words:
                syns = get_synonyms(word)
                if len(syns) > 1:  # Has synonyms beyond the original term
                    synonyms_used[word] = [s for s in syns if s != word]

        # Run content verification against sources
        verification_warnings = None
        if not no_evidence and context_parts:
            warnings = verify_content_against_sources(answer_text, context_parts, cited_sources)
            if warnings:
                verification_warnings = warnings
                logger.warning(f"Content verification warnings: {warnings}")

        return QAResponse(
            answer=answer_text,
            citations=cited_sources,
            no_evidence=no_evidence,
            retrieval_method=retrieval_method,
            synonyms_used=synonyms_used,
            retrieval_diagnostics=diagnostics,
            verification_warnings=verification_warnings,
        )

    except anthropic.AuthenticationError:
        return QAResponse(
            answer="Authentication failed. Please check your ANTHROPIC_API_KEY.",
            citations=[],
            no_evidence=True,
        )
    except anthropic.RateLimitError:
        return QAResponse(
            answer="Rate limit exceeded. Please try again in a moment.",
            citations=[],
            no_evidence=True,
        )
    except Exception as e:
        return QAResponse(
            answer=f"An error occurred while processing your question: {str(e)}",
            citations=[],
            no_evidence=True,
        )

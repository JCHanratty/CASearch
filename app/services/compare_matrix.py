"""Matrix comparison service for structured multi-document analysis.

Accepts a topic and list of documents, retrieves relevant content,
batches documents for Claude API calls, and returns structured matrix
data suitable for rendering as a sortable comparison table.
"""

import hashlib
import json
import logging
import time
from typing import Optional

import anthropic

from app.db import get_db
from app.services.compare_ai import get_relevant_content
from app.settings import settings

logger = logging.getLogger(__name__)

# Per-source character cap for context sent to Claude
MAX_CONTEXT_PER_SOURCE = 8000

# Cache TTL: 24 hours in seconds
CACHE_TTL_SECONDS = 24 * 60 * 60

# System prompt for structured matrix extraction
MATRIX_SYSTEM_PROMPT = """You are a document analysis assistant that extracts structured comparison data.

## RULES
1. Extract ONLY values explicitly stated in the provided text.
2. Use the EXACT wording or numbers from the documents - do not paraphrase.
3. If a value is not found in a document's excerpts, use exactly "Not specified".
4. NEVER calculate derived values (e.g., do not compute hourly from annual salary).
5. NEVER add qualifiers, context, or assumptions not present in the source text.
6. Identify the most relevant comparison aspects for the given topic.

## OUTPUT FORMAT
You MUST respond with valid JSON only. No markdown fencing, no explanation, just the JSON object.

{
  "topic": "<the comparison topic>",
  "aspects": ["<aspect1>", "<aspect2>", ...],
  "documents": {
    "<document_name>": {
      "<aspect1>": "<exact value or Not specified>",
      "<aspect2>": "<exact value or Not specified>"
    }
  }
}

## GUIDELINES FOR ASPECTS
- Choose 5-15 aspects that are most relevant to the topic across the documents.
- Use clear, concise aspect names (e.g., "Overtime Rate", "Vacation Days After 5 Years").
- Prefer aspects where at least one document has a concrete value.
- Order aspects from most to least important for the topic.

## DO NOT
- Add time thresholds unless explicitly stated
- Calculate hourly from annual or vice versa
- Use general knowledge about labor law or industry standards
- Assume standard values or fill in from external knowledge
- Include markdown formatting in the JSON output"""


def _ensure_cache_table() -> None:
    """Create the comparison_cache table if it does not exist."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comparison_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT NOT NULL UNIQUE,
                topic TEXT NOT NULL,
                file_ids_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_comparison_cache_key "
                "ON comparison_cache(cache_key)"
            )
        except Exception:
            pass
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_comparison_cache_expires "
                "ON comparison_cache(expires_at)"
            )
        except Exception:
            pass


def _make_cache_key(topic: str, file_ids: list[int]) -> str:
    """Generate a deterministic cache key from topic and sorted file IDs."""
    key_input = topic.strip().lower() + "|" + ",".join(str(fid) for fid in sorted(file_ids))
    return hashlib.sha256(key_input.encode("utf-8")).hexdigest()


def get_cached_matrix(topic: str, file_ids: list[int]) -> Optional[dict]:
    """Retrieve a cached matrix result if it exists and has not expired.

    Args:
        topic: The comparison topic.
        file_ids: List of file IDs that were compared.

    Returns:
        The cached result dict, or None if not found / expired.
    """
    _ensure_cache_table()
    cache_key = _make_cache_key(topic, file_ids)
    now = time.time()

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT result_json FROM comparison_cache "
                "WHERE cache_key = ? AND expires_at > ?",
                (cache_key, now),
            ).fetchone()

            if row:
                logger.debug("Cache hit for matrix comparison: topic=%r", topic)
                return json.loads(row["result_json"])

            # Clean up expired entries opportunistically
            conn.execute(
                "DELETE FROM comparison_cache WHERE expires_at <= ?", (now,)
            )
    except Exception as e:
        logger.warning("Failed to read matrix cache: %s", e)

    return None


def cache_matrix_result(topic: str, file_ids: list[int], result: dict) -> None:
    """Store a matrix comparison result in the cache.

    Args:
        topic: The comparison topic.
        file_ids: List of file IDs that were compared.
        result: The matrix result dict to cache.
    """
    _ensure_cache_table()
    cache_key = _make_cache_key(topic, file_ids)
    now = time.time()
    expires_at = now + CACHE_TTL_SECONDS

    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO comparison_cache
                       (cache_key, topic, file_ids_json, result_json, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cache_key) DO UPDATE SET
                       result_json = excluded.result_json,
                       created_at = excluded.created_at,
                       expires_at = excluded.expires_at
                """,
                (
                    cache_key,
                    topic,
                    json.dumps(sorted(file_ids)),
                    json.dumps(result),
                    now,
                    expires_at,
                ),
            )
        logger.debug("Cached matrix result: topic=%r, file_ids=%s", topic, file_ids)
    except Exception as e:
        logger.warning("Failed to cache matrix result: %s", e)


def _build_context_for_docs(
    content_by_doc: dict[int, list[dict]],
    doc_names: dict[int, str],
    file_ids_subset: list[int],
) -> tuple[str, list[dict]]:
    """Build a context string and source list for a subset of documents.

    Args:
        content_by_doc: Mapping of file_id to list of content dicts.
        doc_names: Mapping of file_id to document filename.
        file_ids_subset: The file IDs to include in this context block.

    Returns:
        Tuple of (context_string, sources_list).
    """
    context_parts = []
    sources = []

    for file_id in file_ids_subset:
        filename = doc_names.get(file_id, f"Document {file_id}")
        doc_content = content_by_doc.get(file_id, [])

        context_parts.append(f"=== DOCUMENT: {filename} ===\n")

        if doc_content:
            for content in doc_content:
                page_num = content["page_number"]
                text = content["text"][:MAX_CONTEXT_PER_SOURCE]
                heading = content.get("heading")

                heading_line = f" (Section: {heading})" if heading else ""
                context_parts.append(
                    f"[{filename}, Page {page_num}]{heading_line}:\n{text}\n\n"
                )

                sources.append({
                    "file_id": file_id,
                    "filename": filename,
                    "page_number": page_num,
                })
        else:
            context_parts.append("No relevant content found for this topic.\n\n")

        context_parts.append("\n")

    return "".join(context_parts), sources


def _call_claude_for_matrix(topic: str, context: str, doc_names_in_batch: list[str]) -> dict:
    """Make a single Claude API call to extract matrix data.

    Args:
        topic: The comparison topic.
        context: The assembled context string with document excerpts.
        doc_names_in_batch: List of document names in this batch.

    Returns:
        Parsed JSON dict from Claude's response.

    Raises:
        ValueError: If Claude's response cannot be parsed as JSON.
        anthropic.APIError: On API failures.
    """
    doc_list_str = ", ".join(f'"{name}"' for name in doc_names_in_batch)

    user_message = f"""Extract a structured comparison matrix for the topic: "{topic}"

Documents to compare: {doc_list_str}

IMPORTANT:
- Extract values EXACTLY as written in the text
- Use "Not specified" for any aspect not found in a document's excerpts
- Do NOT calculate or derive values
- Do NOT add qualifiers not present in the source text
- Respond with valid JSON only

Document excerpts:

{context}

Respond with the JSON matrix only."""

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    response = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=4096,
        system=MATRIX_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        timeout=90.0,
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        raw_text = "\n".join(lines).strip()

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Claude matrix response as JSON: %s", e)
        logger.debug("Raw response: %s", raw_text[:500])
        raise ValueError(f"Claude returned invalid JSON: {e}") from e


def _batch_extract(
    topic: str,
    content_by_doc: dict[int, list[dict]],
    doc_names: dict[int, str],
    batch_size: int = 6,
) -> dict:
    """Extract matrix data by batching documents into groups for API calls.

    When there are more documents than can fit in a single API call,
    this function splits them into batches, calls Claude for each batch,
    and merges the results into a unified matrix.

    Args:
        topic: The comparison topic.
        content_by_doc: Mapping of file_id to list of content dicts.
        doc_names: Mapping of file_id to document filename.
        batch_size: Number of documents per batch (default 6, range 5-8).

    Returns:
        Merged matrix dict with keys: topic, aspects, documents.
    """
    all_file_ids = list(doc_names.keys())
    total_docs = len(all_file_ids)

    # Clamp batch_size to valid range
    batch_size = max(5, min(8, batch_size))

    # Create batches
    batches = []
    for i in range(0, total_docs, batch_size):
        batches.append(all_file_ids[i : i + batch_size])

    logger.info(
        "Matrix extraction: %d documents in %d batch(es) of up to %d",
        total_docs,
        len(batches),
        batch_size,
    )

    # Collect results from all batches
    merged_aspects: list[str] = []
    merged_documents: dict[str, dict] = {}  # name -> {aspect: value}
    all_sources: list[dict] = []

    for batch_idx, batch_file_ids in enumerate(batches):
        batch_names = [doc_names[fid] for fid in batch_file_ids]
        logger.info(
            "Processing batch %d/%d: %s",
            batch_idx + 1,
            len(batches),
            batch_names,
        )

        context, sources = _build_context_for_docs(
            content_by_doc, doc_names, batch_file_ids
        )
        all_sources.extend(sources)

        batch_result = _call_claude_for_matrix(topic, context, batch_names)

        # Merge aspects (preserve order, avoid duplicates)
        batch_aspects = batch_result.get("aspects", [])
        for aspect in batch_aspects:
            if aspect not in merged_aspects:
                merged_aspects.append(aspect)

        # Merge document data
        batch_docs = batch_result.get("documents", {})
        for doc_name, values in batch_docs.items():
            if doc_name not in merged_documents:
                merged_documents[doc_name] = {}
            merged_documents[doc_name].update(values)

    # Ensure all documents have all aspects (fill missing with "Not specified")
    for doc_name in merged_documents:
        for aspect in merged_aspects:
            if aspect not in merged_documents[doc_name]:
                merged_documents[doc_name][aspect] = "Not specified"

    return {
        "topic": topic,
        "aspects": merged_aspects,
        "documents": merged_documents,
        "sources": all_sources,
    }


def compare_matrix(topic: str, file_ids: list[int]) -> dict:
    """Compare multiple documents on a topic and return structured matrix data.

    This is the main entry point for matrix comparisons. It retrieves relevant
    content from each document, batches the documents for Claude API calls,
    and returns a structured result suitable for rendering as a sortable table.

    Args:
        topic: The topic or provision to compare across documents.
        file_ids: List of file IDs to include in the comparison.

    Returns:
        Dict with structure:
            {
                "topic": str,
                "aspects": list[str],
                "documents": list[{
                    "name": str,
                    "file_id": int,
                    "values": dict[str, str]
                }],
                "sources": list[dict],
                "error": optional str
            }
    """
    # ------------------------------------------------------------------
    # Validate inputs
    # ------------------------------------------------------------------
    if not settings.ANTHROPIC_API_KEY:
        return {
            "topic": topic or "",
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": "API key not configured. Please set ANTHROPIC_API_KEY in your .env file.",
        }

    if not file_ids or len(file_ids) < 2:
        return {
            "topic": topic or "",
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": "At least two documents are required for matrix comparison.",
        }

    if not topic or not topic.strip():
        return {
            "topic": "",
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": "Please enter a topic to compare across the documents.",
        }

    topic = topic.strip()

    # ------------------------------------------------------------------
    # Check cache
    # ------------------------------------------------------------------
    cached = get_cached_matrix(topic, file_ids)
    if cached:
        logger.info("Returning cached matrix result for topic=%r", topic)
        return cached

    # ------------------------------------------------------------------
    # Resolve file metadata
    # ------------------------------------------------------------------
    doc_names: dict[int, str] = {}
    try:
        with get_db() as conn:
            for file_id in file_ids:
                row = conn.execute(
                    "SELECT id, filename, short_name FROM files WHERE id = ?", (file_id,)
                ).fetchone()
                if row:
                    name = row["short_name"] if row["short_name"] else row["filename"]
                    doc_names[row["id"]] = name
    except Exception as e:
        logger.error("Failed to resolve file metadata: %s", e)
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": f"Database error resolving files: {e}",
        }

    if len(doc_names) < 2:
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": "Could not find enough valid documents for matrix comparison.",
        }

    # ------------------------------------------------------------------
    # Retrieve relevant content
    # ------------------------------------------------------------------
    try:
        content_by_doc = get_relevant_content(
            list(doc_names.keys()), topic, limit_per_doc=8
        )
    except Exception as e:
        logger.error("Content retrieval failed: %s", e)
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": f"Error retrieving document content: {e}",
        }

    total_results = sum(len(v) for v in content_by_doc.values())
    if total_results == 0:
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": (
                f"No relevant content found for topic '{topic}' in the selected "
                "documents. Try a different search term or ensure documents are "
                "properly indexed."
            ),
        }

    logger.info(
        "Matrix comparison: topic=%r, documents=%d, total_excerpts=%d",
        topic,
        len(doc_names),
        total_results,
    )

    # ------------------------------------------------------------------
    # Call Claude via batched extraction
    # ------------------------------------------------------------------
    try:
        raw_result = _batch_extract(topic, content_by_doc, doc_names)
    except anthropic.AuthenticationError:
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": "Authentication failed. Please check your ANTHROPIC_API_KEY.",
        }
    except anthropic.RateLimitError:
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": "Rate limit exceeded. Please try again in a moment.",
        }
    except anthropic.APIError as e:
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": f"API error: {e}",
        }
    except ValueError as e:
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": f"Failed to parse structured response: {e}",
        }
    except Exception as e:
        logger.exception("Unexpected error during matrix extraction")
        return {
            "topic": topic,
            "aspects": [],
            "documents": [],
            "sources": [],
            "error": f"An error occurred during matrix comparison: {e}",
        }

    # ------------------------------------------------------------------
    # Reshape into the final return format
    # ------------------------------------------------------------------
    # Build a reverse map: filename -> file_id
    name_to_id: dict[str, int] = {name: fid for fid, name in doc_names.items()}

    documents_list: list[dict] = []
    raw_docs = raw_result.get("documents", {})

    for doc_name, values in raw_docs.items():
        file_id = name_to_id.get(doc_name)
        if file_id is None:
            # Try a fuzzy match in case Claude slightly altered the name
            for known_name, known_id in name_to_id.items():
                if known_name in doc_name or doc_name in known_name:
                    file_id = known_id
                    break
        documents_list.append({
            "name": doc_name,
            "file_id": file_id,
            "values": values,
        })

    # Ensure every requested document appears in the output, even if Claude
    # omitted it (can happen if no content was found for a document).
    included_ids = {d["file_id"] for d in documents_list}
    for fid, fname in doc_names.items():
        if fid not in included_ids:
            documents_list.append({
                "name": fname,
                "file_id": fid,
                "values": {
                    aspect: "Not specified"
                    for aspect in raw_result.get("aspects", [])
                },
            })

    result: dict = {
        "topic": raw_result.get("topic", topic),
        "aspects": raw_result.get("aspects", []),
        "documents": documents_list,
        "sources": raw_result.get("sources", []),
    }

    # ------------------------------------------------------------------
    # Cache the successful result
    # ------------------------------------------------------------------
    cache_matrix_result(topic, file_ids, result)

    logger.info(
        "Matrix comparison complete: topic=%r, aspects=%d, documents=%d",
        topic,
        len(result["aspects"]),
        len(result["documents"]),
    )

    return result

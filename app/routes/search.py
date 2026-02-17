"""Search routes - full-text search across documents."""

from typing import Optional
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, Response

from app.db import get_db
from app.services.search import search_pages, rank_results_by_phrase_proximity
from app.services.search_ai import ai_analyze_search
from app.services.synonyms import expand_query, get_synonyms
from app.services.export import export_search_results_html, export_search_results_docx
from app.templates import templates

router = APIRouter()


def get_indexed_files() -> list[dict]:
    """Get list of indexed files for document filter dropdown."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, filename FROM files
               WHERE status = 'indexed'
               ORDER BY filename"""
        ).fetchall()
        return [{"id": r["id"], "filename": r["filename"]} for r in rows]


@router.get("/", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str = "",
    mode: str = "and",
    file_id: Optional[int] = None,
    expand_synonyms: Optional[str] = None,
    search_mode: str = "text",  # "text" or "ai"
):
    """
    Search page with optional query, mode, document filter, and synonym expansion.

    Args:
        q: Search query (supports quoted phrases)
        mode: "and" (all terms) or "or" (any term)
        file_id: Optional file ID to restrict search
        expand_synonyms: "true" to enable synonym expansion
        search_mode: "text" for traditional search, "ai" for AI analysis
    """
    results = []
    files = get_indexed_files()
    expanded_queries = []
    synonyms_used = {}
    ai_result = None

    # Validate modes
    if mode not in ("and", "or"):
        mode = "and"
    if search_mode not in ("text", "ai"):
        search_mode = "text"

    # Parse expand_synonyms checkbox value
    use_synonyms = expand_synonyms == "true"

    if q.strip():
        query = q.strip()

        # AI analysis mode
        if search_mode == "ai":
            ai_result = ai_analyze_search(query, file_id=file_id)
        else:
            # Text search mode
            # If synonym expansion is enabled, expand the query
            if use_synonyms:
                expanded_queries = expand_query(query, include_original=True)

                # Identify which terms were expanded for display
                query_lower = query.lower()
                query_words = query_lower.split()
                for word in query_words:
                    synonyms = get_synonyms(word)
                    if len(synonyms) > 1:  # Has synonyms beyond the original term
                        synonyms_used[word] = [s for s in synonyms if s != word]

                # Search with all expanded queries and combine results
                all_results = []
                seen_keys = set()

                for exp_query in expanded_queries:
                    exp_results = search_pages(
                        exp_query,
                        mode=mode,
                        file_id=file_id,
                        fallback_to_or=(mode == "and"),
                    )
                    for r in exp_results:
                        key = (r.file_id, r.page_number)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            all_results.append(r)

                results = all_results
            else:
                # Standard search without synonym expansion
                results = search_pages(
                    query,
                    mode=mode,
                    file_id=file_id,
                    fallback_to_or=(mode == "and"),
                )

            # Apply phrase/proximity re-ranking
            results = rank_results_by_phrase_proximity(results, query)

    # Check if HTMX request (partial) or full page
    if request.headers.get("HX-Request"):
        if search_mode == "ai":
            return templates.TemplateResponse(
                "components/search_ai_results.html",
                {
                    "request": request,
                    "ai_result": ai_result,
                    "query": q,
                },
            )
        else:
            return templates.TemplateResponse(
                "components/search_results.html",
                {
                    "request": request,
                    "query": q,
                    "results": results,
                    "mode": mode,
                    "file_id": file_id,
                    "expand_synonyms": use_synonyms,
                    "expanded_queries": expanded_queries[1:] if expanded_queries else [],
                    "synonyms_used": synonyms_used,
                },
            )

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "query": q,
            "results": results,
            "files": files,
            "mode": mode,
            "file_id": file_id,
            "expand_synonyms": use_synonyms,
            "expanded_queries": expanded_queries[1:] if expanded_queries else [],
            "synonyms_used": synonyms_used,
            "search_mode": search_mode,
            "ai_result": ai_result,
            "page_title": "Search",
        },
    )


@router.get("/export")
async def export_search(
    q: str = "",
    mode: str = "and",
    file_id: Optional[int] = None,
    format: str = Query("html", regex="^(html|docx)$"),
):
    """
    Export search results to HTML or DOCX format.

    Args:
        q: Search query
        mode: "and" or "or" matching mode
        file_id: Optional file ID to restrict search
        format: Export format ("html" or "docx")

    Returns:
        HTML or DOCX file download
    """
    results = []

    if q.strip():
        results = search_pages(
            q.strip(),
            mode=mode,
            file_id=file_id,
            fallback_to_or=(mode == "and"),
        )
        results = rank_results_by_phrase_proximity(results, q.strip())

    if format == "docx":
        content = export_search_results_docx(results, q)
        filename = f"search_results_{q[:20].replace(' ', '_')}.docx"
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        content = export_search_results_html(results, q)
        filename = f"search_results_{q[:20].replace(' ', '_')}.html"
        return Response(
            content=content,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

"""Documents routes - PDF file management."""

import asyncio
import json
import logging

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, StreamingResponse

from app.db import get_db, toggle_file_public_read
from app.services.file_scanner import scan_agreements, get_all_files, get_file_by_id, get_public_files
from app.services.indexer import index_file, get_file_pages
from app.services.pdf_extract import ExtractionError
from app.templates import templates

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def list_documents(request: Request):
    """List all PDF documents."""
    documents = get_all_files()

    return templates.TemplateResponse(
        "documents.html",
        {
            "request": request,
            "documents": documents,
            "page_title": "Documents",
        },
    )


@router.post("/scan", response_class=HTMLResponse)
async def scan_documents(request: Request):
    """Scan for new/changed PDF files."""
    results = scan_agreements()
    documents = get_all_files()

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/documents_table.html",
            {
                "request": request,
                "documents": documents,
                "scan_results": results,
            },
        )

    return templates.TemplateResponse(
        "documents.html",
        {
            "request": request,
            "documents": documents,
            "scan_results": results,
            "page_title": "Documents",
        },
    )


@router.post("/{file_id}/index", response_class=HTMLResponse)
async def index_document(request: Request, file_id: int):
    """Index a specific PDF document."""
    doc = get_file_by_id(file_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        result = index_file(file_id)
        doc = get_file_by_id(file_id)
    except ExtractionError as e:
        doc = get_file_by_id(file_id)
    except Exception as e:
        doc = get_file_by_id(file_id)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/document_row.html",
            {"request": request, "doc": doc},
        )

    documents = get_all_files()
    return templates.TemplateResponse(
        "documents.html",
        {"request": request, "documents": documents, "page_title": "Documents"},
    )


@router.get("/{file_id}/view", response_class=HTMLResponse)
async def view_document(request: Request, file_id: int, page: int = 1, highlight: str = ""):
    """View extracted text from a document with optional text highlighting."""
    doc = get_file_by_id(file_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.status != "indexed":
        raise HTTPException(status_code=400, detail="Document not indexed yet")

    pages = get_file_pages(file_id, page_number=page)
    if not pages:
        pages = get_file_pages(file_id, page_number=1)
        page = 1

    return templates.TemplateResponse(
        "document_view.html",
        {
            "request": request,
            "document": doc,
            "pages": pages,
            "current_page": page,
            "highlight": highlight.strip(),
            "page_title": f"View: {doc.filename}",
        },
    )


@router.post("/index-all", response_class=HTMLResponse)
async def index_all_documents(request: Request):
    """Index all pending documents."""
    documents = get_all_files()
    indexed_count = 0
    error_count = 0

    for doc in documents:
        if doc.status == "pending":
            try:
                index_file(doc.id)
                indexed_count += 1
            except Exception:
                error_count += 1

    documents = get_all_files()

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/documents_table.html",
            {
                "request": request,
                "documents": documents,
                "scan_results": {"indexed": indexed_count, "errors": error_count},
            },
        )

    return templates.TemplateResponse(
        "documents.html",
        {"request": request, "documents": documents, "page_title": "Documents"},
    )


@router.get("/index-all-stream")
async def index_all_stream(request: Request):
    """SSE endpoint that indexes all pending/error documents and streams progress."""

    async def event_generator():
        documents = get_all_files()
        pending = [d for d in documents if d.status in ("pending", "error")]
        total = len(pending)

        if total == 0:
            yield f"data: {json.dumps({'type': 'complete', 'indexed': 0, 'errors': 0, 'total': 0})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"

        indexed = 0
        errors = 0

        for i, doc in enumerate(pending):
            # Check if client disconnected
            if await request.is_disconnected():
                logger.info("Client disconnected during batch indexing")
                return

            filename = doc.short_name or doc.filename
            yield f"data: {json.dumps({'type': 'progress', 'current': i + 1, 'total': total, 'filename': filename, 'indexed': indexed, 'errors': errors})}\n\n"

            try:
                # Run synchronous index_file in thread pool to avoid blocking
                await asyncio.to_thread(index_file, doc.id)
                indexed += 1
            except Exception as e:
                errors += 1
                logger.warning("Failed to index %s: %s", doc.filename, e)

        yield f"data: {json.dumps({'type': 'complete', 'indexed': indexed, 'errors': errors, 'total': total})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{file_id}/toggle-public", response_class=HTMLResponse)
async def toggle_document_public(request: Request, file_id: int):
    """Toggle the public_read status of a document."""
    doc = get_file_by_id(file_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        new_status = toggle_file_public_read(file_id)
        doc = get_file_by_id(file_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/document_row.html",
            {"request": request, "doc": doc},
        )

    documents = get_all_files()
    return templates.TemplateResponse(
        "documents.html",
        {"request": request, "documents": documents, "page_title": "Documents"},
    )


@router.post("/{file_id}/metadata", response_class=HTMLResponse)
async def update_document_metadata(
    request: Request,
    file_id: int,
    employer_name: str = Form(default=""),
    union_local: str = Form(default=""),
    effective_date: str = Form(default=""),
    expiry_date: str = Form(default=""),
    region: str = Form(default=""),
    short_name: str = Form(default=""),
):
    """Update metadata for a document via HTMX inline editing."""
    doc = get_file_by_id(file_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    with get_db() as conn:
        conn.execute(
            """UPDATE files SET
                employer_name = ?, union_local = ?, effective_date = ?,
                expiry_date = ?, region = ?, short_name = ?
               WHERE id = ?""",
            (
                employer_name.strip() or None,
                union_local.strip() or None,
                effective_date.strip() or None,
                expiry_date.strip() or None,
                region.strip() or None,
                short_name.strip() or None,
                file_id,
            ),
        )

    doc = get_file_by_id(file_id)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/document_row.html",
            {"request": request, "doc": doc},
        )

    documents = get_all_files()
    return templates.TemplateResponse(
        "documents.html",
        {"request": request, "documents": documents, "page_title": "Documents"},
    )

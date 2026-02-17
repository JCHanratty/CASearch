"""Dashboard route - main landing page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db import get_db, get_db_stats
from app.templates import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Render the main dashboard page."""
    stats = get_db_stats()

    # Calculate pending files
    with get_db() as conn:
        pending = conn.execute("SELECT COUNT(*) FROM files WHERE status = 'pending'").fetchone()[0]

    stats["pending_files"] = pending

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "page_title": "Dashboard",
        }
    )

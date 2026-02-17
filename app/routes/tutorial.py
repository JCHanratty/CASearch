"""Tutorial routes - help and getting started guide."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.templates import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def tutorial_page(request: Request):
    """Tutorial and getting started page."""
    return templates.TemplateResponse(
        "tutorial.html",
        {
            "request": request,
            "page_title": "Tutorial",
        },
    )

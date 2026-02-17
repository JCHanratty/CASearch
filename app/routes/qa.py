"""Q&A routes - AI-assisted question answering with citations."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse

from app.services.qa import answer_question
from app.templates import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def qa_page(request: Request):
    """Q&A interface page."""
    return templates.TemplateResponse(
        "qa.html",
        {
            "request": request,
            "page_title": "Q&A",
        },
    )


@router.post("/ask", response_class=HTMLResponse)
async def ask_question(request: Request, question: str = Form(...)):
    """Process a question and return an answer with citations."""
    response = None

    if question.strip():
        response = answer_question(question.strip())

    # Check if HTMX request (partial) or full page
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "components/qa_answer.html",
            {
                "request": request,
                "response": response,
                "question": question,
            },
        )

    return templates.TemplateResponse(
        "qa.html",
        {
            "request": request,
            "response": response,
            "question": question,
            "page_title": "Q&A",
        },
    )

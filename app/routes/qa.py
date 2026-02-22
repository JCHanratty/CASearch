"""Q&A routes - AI-assisted question answering with citations."""

import json

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse

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


@router.post("/ask-stream")
async def ask_question_stream(request: Request, question: str = Form(...)):
    """SSE streaming version of Q&A with progress updates."""
    async def event_stream():
        def send(event_type, data):
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        if not question.strip():
            yield send("error", {"message": "Please enter a question."})
            return

        yield send("progress", {"pct": 10, "step": "Searching documents..."})

        try:
            response = answer_question(question.strip())
        except Exception as e:
            yield send("error", {"message": f"Error: {str(e)}"})
            return

        yield send("progress", {"pct": 85, "step": "Formatting answer..."})

        # Render the answer partial template
        html_content = templates.get_template("components/qa_answer.html").render(
            response=response,
            question=question,
        )

        yield send("complete", {"html": html_content})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

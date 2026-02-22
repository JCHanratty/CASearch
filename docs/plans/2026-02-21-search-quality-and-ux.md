# Search Quality & UX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix broken search/Q&A/matrix features and add progress feedback for long operations.

**Architecture:** Three layers — (1) fix search quality by upgrading Claude model, fixing text extraction, adding timeouts; (2) enable semantic search with embeddings; (3) add SSE-based progress modal for long operations. Each layer is independently valuable.

**Tech Stack:** FastAPI + SSE (StreamingResponse), Anthropic API (Sonnet 4.5), ChromaDB + sentence-transformers, HTMX + vanilla JS

---

## Task 1: Upgrade Claude Model

**Files:**
- Modify: `.env:7`
- Modify: `.env.example:7`

**Step 1: Update .env to use Sonnet 4.5**

Change line 7 in `.env`:
```
CLAUDE_MODEL=claude-sonnet-4-5-20241022
```

**Step 2: Verify .env.example already matches**

`.env.example` line 7 already says `claude-sonnet-4-5-20241022` — confirm it matches.

**Step 3: Verify model loads**

Run: `python -c "from app.settings import settings; print(settings.CLAUDE_MODEL)"`
Expected: `claude-sonnet-4-5-20241022`

**Step 4: Commit**

```bash
git add .env .env.example
git commit -m "fix: upgrade Claude model from Haiku 3 to Sonnet 4.5"
```

---

## Task 2: Fix Broken Word Extraction

41 of 205 pages have spurious spaces in words like "member s", "pe rform", "qualifications o f". These break FTS keyword matching.

**Files:**
- Modify: `app/services/pdf_extract.py:283-308`
- Test: `tests/test_pdf_extract.py` (new test)

**Step 1: Write failing test**

Add to `tests/test_pdf_extract.py` (create if needed):
```python
"""Tests for PDF text extraction post-processing."""

from app.services.pdf_extract import normalize_text


def test_rejoin_spurious_spaces():
    """normalize_text should rejoin single-letter splits like 'member s'."""
    assert "members" in normalize_text("member s of the union")
    assert "perform" in normalize_text("pe rform duties")
    assert "qualifications of" in normalize_text("qualifications o f")
    # Should NOT join legitimate single-letter words
    assert "a union" in normalize_text("a union")
    assert "I am" in normalize_text("I am")


def test_rejoin_preserves_normal_text():
    """normalize_text should not alter correctly spaced text."""
    text = "The employee shall receive overtime pay at 1.5 times the regular rate."
    result = normalize_text(text)
    assert "overtime" in result
    assert "1.5 times" in result
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pdf_extract.py::test_rejoin_spurious_spaces -v`
Expected: FAIL — "members" not found in output because rejoin logic doesn't exist yet.

**Step 3: Add rejoin logic to normalize_text**

In `app/services/pdf_extract.py`, modify `normalize_text()` (line 283). Insert the rejoin step after dehyphenation and before whitespace normalization:

```python
def normalize_text(text: str) -> str:
    """
    Normalize text for consistent indexing.

    - Dehyphenates line-break splits
    - Rejoins spurious single-letter splits (e.g., "member s" → "members")
    - Normalizes whitespace
    - Removes excessive blank lines
    """
    # First, dehyphenate
    text = dehyphenate(text)

    # Rejoin spurious single-letter splits from PDF extraction
    # Pattern: word ending + space + single lowercase letter + space + lowercase continuation
    # e.g., "member s" → "members", "pe rform" → "perform", "o f" → "of"
    import re as _re
    # Match: word char(s) + space + single lowercase letter + space + lowercase letter(s)
    # But skip legitimate words: "a", "I" followed by normal words
    text = _re.sub(
        r'(\w) ([a-z]) (?=[a-z])',
        r'\1\2',
        text,
    )
    # Also handle end-of-pattern: "member s" at end of phrase (before punctuation or uppercase)
    text = _re.sub(
        r'(\w{2,}) ([a-z])(?=\s|[.,;:!?\)]|$)',
        lambda m: m.group(1) + m.group(2) if len(m.group(2)) == 1 else m.group(0),
        text,
    )

    # Normalize various whitespace characters
    text = text.replace('\r\n', '\n')
    text = text.replace('\r', '\n')

    # Normalize lines
    lines = text.split('\n')
    normalized_lines = []

    for line in lines:
        # Normalize spaces within each line
        normalized_line = ' '.join(line.split())
        if normalized_line:
            normalized_lines.append(normalized_line)

    return '\n'.join(normalized_lines)
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pdf_extract.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add app/services/pdf_extract.py tests/test_pdf_extract.py
git commit -m "fix: rejoin spurious single-letter splits in PDF text extraction"
```

---

## Task 3: Add Timeouts to All API and Search Calls

Without timeouts, the Matrix feature hangs indefinitely when semantic search tries to load ChromaDB with no embeddings.

**Files:**
- Modify: `app/services/qa.py:1204` (Claude API call)
- Modify: `app/services/compare_ai.py:430` (Claude API call)
- Modify: `app/services/compare_matrix.py:261` (Claude API call)
- Modify: `app/services/qa.py:761` (ThreadPoolExecutor in parallel retrieval)
- Modify: `app/services/semantic_search.py:50-91` (model/client init)

**Step 1: Add timeout to QA Claude API call**

In `app/services/qa.py`, line 1204, add `timeout=60.0`:
```python
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            timeout=60.0,
        )
```

**Step 2: Add timeout to Compare AI Claude API call**

In `app/services/compare_ai.py`, line 430, add `timeout=60.0`:
```python
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4096,
            system=COMPARISON_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            timeout=60.0,
        )
```

**Step 3: Add timeout to Matrix Claude API call**

In `app/services/compare_matrix.py`, line 261, add `timeout=90.0` (matrix may need longer):
```python
    response = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=4096,
        system=MATRIX_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        timeout=90.0,
    )
```

**Step 4: Add timeout to ThreadPoolExecutor in parallel retrieval**

In `app/services/qa.py`, around line 769, add timeout to `future.result()`:
```python
        for future in as_completed(futures, timeout=30):
            try:
                method, results, raw = future.result(timeout=10)
```

Also add a `try/except TimeoutError` around the whole block:
```python
    try:
        for future in as_completed(futures, timeout=30):
            try:
                method, results, raw = future.result(timeout=10)
                if results:
                    all_results.append(results)
                    if method in ("semantic", "chunk") and raw:
                        context_results.extend(raw)
            except Exception as e:
                logger.warning(f"Retrieval strategy failed: {e}")
                continue
    except TimeoutError:
        logger.warning("Parallel retrieval timed out after 30s")
```

**Step 5: Add timeout protection to semantic search init**

In `app/services/semantic_search.py`, wrap `_get_embedding_model()` and `_get_chroma_client()` with timeout handling. Add at the top of `search_semantic()` (line 263 area) and `search_semantic_with_rerank()`:

The functions already have try/except. The key fix is in `_get_collection()` — if ChromaDB has zero items and was never initialized, don't let it block. The existing code at `search_semantic` line ~270 already checks `collection.count() == 0` and returns early. The issue is that `_get_chroma_client()` itself can hang during first initialization when dependencies are missing.

Add a `signal`-based or thread-based timeout wrapper. Since this is Windows, use threading:

In `app/services/semantic_search.py`, add near the top (after imports):
```python
import threading

def _with_timeout(func, timeout_seconds=15, default=None):
    """Run a function with a timeout. Returns default if timeout exceeded."""
    result = [default]
    error = [None]

    def target():
        try:
            result[0] = func()
        except Exception as e:
            error[0] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)

    if thread.is_alive():
        logger.warning(f"{func.__name__} timed out after {timeout_seconds}s")
        return default
    if error[0]:
        raise error[0]
    return result[0]
```

Then in `_get_embedding_model()`, wrap the heavy load:
```python
def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
            _embedding_model = _with_timeout(
                lambda: SentenceTransformer(EMBEDDING_MODEL),
                timeout_seconds=30,
            )
            if _embedding_model is None:
                raise TimeoutError("Embedding model load timed out")
            logger.info("Embedding model loaded successfully")
        except Exception as e:
            logger.error(f"Error loading embedding model: {e}")
            raise
    return _embedding_model
```

**Step 6: Run existing tests**

Run: `python -m pytest tests/test_routes.py tests/test_tutorial_pages.py -v`
Expected: All PASS (timeouts don't affect test behavior)

**Step 7: Commit**

```bash
git add app/services/qa.py app/services/compare_ai.py app/services/compare_matrix.py app/services/semantic_search.py
git commit -m "fix: add timeouts to Claude API calls and semantic search initialization"
```

---

## Task 4: Enable Embeddings by Default in Indexer

The root cause of matrix hanging and poor search quality: `build_embeddings=False` means ZERO embeddings exist. Semantic search always returns empty.

**Files:**
- Modify: `app/services/indexer.py:16` (change default)
- Modify: `app/services/indexer.py:243-258` (reindex_all passes build_embeddings)
- Modify: `app/routes/admin.py:205` (scan-and-index passes build_embeddings)

**Step 1: Change default in index_file**

In `app/services/indexer.py`, line 16:
```python
def index_file(file_id: int, use_structure: bool = True, build_embeddings: bool = True) -> dict:
```

**Step 2: Update reindex_all to pass build_embeddings=True**

In `app/services/indexer.py`, line 252:
```python
            index_file(row["id"], build_embeddings=True)
```

**Step 3: Update scan-and-index route**

In `app/routes/admin.py`, line 205:
```python
                index_file(doc.id, build_embeddings=True)
```

**Step 4: Run existing tests**

Run: `python -m pytest tests/test_routes.py -v`
Expected: PASS — tests use mock settings so won't actually try to build embeddings

**Step 5: Commit**

```bash
git add app/services/indexer.py app/routes/admin.py
git commit -m "feat: enable semantic embeddings by default during indexing"
```

---

## Task 5: Create Progress Modal Component

Long operations (Q&A, Compare, Matrix, Indexing) provide no feedback. Users see a spinner and think the app is broken.

**Files:**
- Create: `templates/components/progress_modal.html`
- Create: `static/js/progress.js`
- Modify: `templates/layout.html:227` (add progress.js script tag)

**Step 1: Create the progress modal HTML component**

Create `templates/components/progress_modal.html`:
```html
<!-- Progress Modal — included once in layout, controlled via JS -->
<div id="progress-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center">
    <!-- Backdrop -->
    <div class="absolute inset-0 bg-surface-900/70 backdrop-blur-sm"></div>

    <!-- Modal card -->
    <div class="relative bg-surface-800 border border-surface-700 rounded-xl shadow-2xl w-full max-w-md mx-4 p-6 animate-fade-in">
        <!-- Title -->
        <h3 id="progress-title" class="text-lg font-serif text-surface-100 mb-4">Processing...</h3>

        <!-- Progress bar -->
        <div class="w-full bg-surface-700 rounded-full h-2.5 mb-3 overflow-hidden">
            <div id="progress-bar"
                 class="h-full rounded-full transition-all duration-300 ease-out"
                 style="width: 0%; background: linear-gradient(90deg, #b45309, #d99a3a);">
            </div>
        </div>

        <!-- Step description -->
        <p id="progress-step" class="text-sm text-surface-400 mb-4">Initializing...</p>

        <!-- Error area (hidden by default) -->
        <div id="progress-error" class="hidden mt-3 p-3 bg-red-900/20 border border-red-800/50 rounded-lg">
            <p id="progress-error-text" class="text-sm text-red-400"></p>
        </div>

        <!-- Close button (hidden while running) -->
        <div id="progress-actions" class="hidden mt-4 flex justify-end">
            <button onclick="ProgressModal.close()"
                    class="px-4 py-2 text-sm bg-surface-700 hover:bg-surface-600 text-surface-200 rounded-lg transition-colors">
                Close
            </button>
        </div>
    </div>
</div>
```

**Step 2: Create the progress JS controller**

Create `static/js/progress.js`:
```javascript
/**
 * ProgressModal — SSE-driven progress feedback for long operations.
 *
 * Usage:
 *   ProgressModal.start('/qa/ask-stream', {
 *     method: 'POST',
 *     body: formData,
 *     title: 'Answering Question',
 *     onComplete: (data) => { /* handle result */ }
 *   });
 */
const ProgressModal = (() => {
    let _eventSource = null;
    let _onComplete = null;
    let _onError = null;

    function _el(id) { return document.getElementById(id); }

    function open(title) {
        _el('progress-title').textContent = title || 'Processing...';
        _el('progress-bar').style.width = '0%';
        _el('progress-step').textContent = 'Initializing...';
        _el('progress-error').classList.add('hidden');
        _el('progress-actions').classList.add('hidden');
        _el('progress-modal').classList.remove('hidden');
    }

    function update(pct, step) {
        if (pct !== undefined && pct !== null) {
            _el('progress-bar').style.width = Math.min(100, Math.max(0, pct)) + '%';
        }
        if (step) {
            _el('progress-step').textContent = step;
        }
    }

    function showError(message) {
        _el('progress-error-text').textContent = message;
        _el('progress-error').classList.remove('hidden');
        _el('progress-actions').classList.remove('hidden');
        _el('progress-bar').style.width = '100%';
        _el('progress-bar').style.background = '#991b1b';
    }

    function close() {
        _el('progress-modal').classList.add('hidden');
        if (_eventSource) {
            _eventSource.close();
            _eventSource = null;
        }
    }

    /**
     * Start a progress-tracked operation.
     *
     * For SSE endpoints: sends a POST, gets back SSE events.
     * Events expected:
     *   event: progress  data: {"pct": 30, "step": "Searching documents..."}
     *   event: complete  data: {"html": "<div>...</div>"}
     *   event: error     data: {"message": "Something went wrong"}
     */
    function start(url, options = {}) {
        const { method = 'POST', body = null, title = 'Processing', onComplete, onError } = options;
        _onComplete = onComplete || null;
        _onError = onError || null;

        open(title);

        // Use fetch + ReadableStream to handle POST SSE
        fetch(url, {
            method: method,
            body: body,
            headers: body instanceof FormData ? {} : { 'Content-Type': 'application/x-www-form-urlencoded' },
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            function processStream() {
                reader.read().then(({ done, value }) => {
                    if (done) {
                        // Stream ended without explicit complete event
                        close();
                        return;
                    }

                    buffer += decoder.decode(value, { stream: true });

                    // Parse SSE events from buffer
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // Keep incomplete line in buffer

                    let eventType = null;
                    let eventData = '';

                    for (const line of lines) {
                        if (line.startsWith('event: ')) {
                            eventType = line.slice(7).trim();
                        } else if (line.startsWith('data: ')) {
                            eventData = line.slice(6);
                        } else if (line === '' && eventType) {
                            // End of event
                            _handleEvent(eventType, eventData);
                            eventType = null;
                            eventData = '';
                        }
                    }

                    processStream();
                }).catch(err => {
                    showError('Connection lost: ' + err.message);
                });
            }

            processStream();
        })
        .catch(err => {
            showError('Request failed: ' + err.message);
        });
    }

    function _handleEvent(type, rawData) {
        let data;
        try {
            data = JSON.parse(rawData);
        } catch {
            data = { message: rawData };
        }

        switch (type) {
            case 'progress':
                update(data.pct, data.step);
                break;

            case 'complete':
                update(100, 'Done!');
                setTimeout(() => {
                    close();
                    if (_onComplete) _onComplete(data);
                }, 400);
                break;

            case 'error':
                showError(data.message || 'An error occurred');
                if (_onError) _onError(data);
                break;
        }
    }

    return { open, update, showError, close, start };
})();
```

**Step 3: Include in layout.html**

In `templates/layout.html`, add after the `search.js` script tag (line 227):
```html
    <script src="/static/js/progress.js"></script>
```

Also add the progress modal include before the closing `</body>` tag, after the toast container (around line 192):
```html
    {% include "components/progress_modal.html" %}
```

**Step 4: Verify layout loads**

Run: `python -m pytest tests/test_routes.py::test_dashboard_loads -v`
Expected: PASS

**Step 5: Commit**

```bash
git add templates/components/progress_modal.html static/js/progress.js templates/layout.html
git commit -m "feat: add reusable SSE progress modal component"
```

---

## Task 6: Add SSE Progress to Scan & Index

The scan-and-index operation is synchronous and gives no feedback while indexing multiple documents with embeddings.

**Files:**
- Modify: `app/routes/admin.py:191-221` (add streaming endpoint)

**Step 1: Add SSE streaming endpoint for scan-and-index**

Add a new SSE endpoint alongside the existing one. In `app/routes/admin.py`, add imports at top:

```python
import asyncio
from fastapi.responses import StreamingResponse
```

Add new endpoint after the existing `scan_and_index`:

```python
@router.post("/scan-and-index-stream")
async def scan_and_index_stream(request: FastAPIRequest):
    """SSE streaming version of scan-and-index with progress updates."""
    if not verify_session(request):
        return JSONResponse({"error": "Admin login required"}, status_code=403)

    async def event_stream():
        import json as _json

        def send_event(event_type, data):
            return f"event: {event_type}\ndata: {_json.dumps(data)}\n\n"

        yield send_event("progress", {"pct": 5, "step": "Scanning for new files..."})

        scan_results = scan_agreements()

        documents = get_all_files()
        pending = [doc for doc in documents if doc.status == "pending"]
        total = len(pending)

        if total == 0:
            yield send_event("progress", {"pct": 100, "step": "No new files to index."})
            yield send_event("complete", {"html": "", "indexed": 0, "errors": 0})
            return

        indexed = 0
        errors = 0
        for i, doc in enumerate(pending):
            pct = int(10 + (i / total) * 85)
            yield send_event("progress", {
                "pct": pct,
                "step": f"Indexing {doc.filename} ({i+1}/{total})..."
            })

            try:
                index_file(doc.id, build_embeddings=True)
                indexed += 1
            except Exception as e:
                logger.warning("Failed to index %s: %s", doc.filename, e)
                errors += 1

        yield send_event("progress", {"pct": 98, "step": "Finalizing..."})

        # Get updated document list for the response
        documents = get_all_files()
        yield send_event("complete", {
            "indexed": indexed,
            "errors": errors,
            "total_docs": len(documents),
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

**Step 2: Update the admin panel JS to use progress modal**

This will be wired up in the admin panel template. For now, the endpoint exists and works. The existing non-streaming endpoint remains as fallback.

**Step 3: Run tests**

Run: `python -m pytest tests/test_routes.py -v`
Expected: All PASS

**Step 4: Commit**

```bash
git add app/routes/admin.py
git commit -m "feat: add SSE streaming endpoint for scan-and-index with progress"
```

---

## Task 7: Add SSE Progress to Q&A

Q&A can take 10-30 seconds with Sonnet 4.5. Users need to see progress.

**Files:**
- Modify: `app/routes/qa.py` (add streaming endpoint)

**Step 1: Add SSE streaming endpoint for Q&A**

In `app/routes/qa.py`, add the streaming endpoint:

```python
import json
from fastapi.responses import StreamingResponse

@router.post("/ask-stream")
async def ask_question_stream(request: Request, question: str = Form(...)):
    """SSE streaming version of Q&A with progress updates."""
    if not question.strip():
        return StreamingResponse(
            _error_stream("Please enter a question."),
            media_type="text/event-stream",
        )

    async def event_stream():
        def send_event(event_type, data):
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        yield send_event("progress", {"pct": 10, "step": "Searching documents..."})

        try:
            response = answer_question(question.strip())
        except Exception as e:
            yield send_event("error", {"message": f"Error: {str(e)}"})
            return

        yield send_event("progress", {"pct": 90, "step": "Formatting answer..."})

        # Render the answer partial
        html_content = templates.get_template("components/qa_answer.html").render(
            response=response,
            question=question,
        )

        yield send_event("complete", {"html": html_content})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _error_stream(message: str):
    """Helper to yield a single error SSE event."""
    yield f"event: error\ndata: {json.dumps({'message': message})}\n\n"
```

**Step 2: Update Q&A page form to use progress modal**

In `templates/qa.html`, the form currently submits via HTMX. Add an alternative JS handler that uses the progress modal when submitting. Find the form's `hx-post="/qa/ask"` and add an `onsubmit` override:

The form should be modified to support both: the existing HTMX path (kept as fallback) and the new SSE path with progress modal. Add a script block at the bottom of `qa.html`:

```html
<script>
document.addEventListener('DOMContentLoaded', function() {
    const form = document.querySelector('form[hx-post="/qa/ask"]');
    if (form && typeof ProgressModal !== 'undefined') {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            // Prevent HTMX from handling this
            e.stopPropagation();

            const formData = new FormData(form);
            const resultTarget = document.getElementById('qa-result');

            ProgressModal.start('/qa/ask-stream', {
                method: 'POST',
                body: formData,
                title: 'Analyzing Your Question',
                onComplete: (data) => {
                    if (data.html && resultTarget) {
                        resultTarget.innerHTML = data.html;
                        lucide.createIcons();
                    }
                },
            });
        });
    }
});
</script>
```

**Step 3: Run tests**

Run: `python -m pytest tests/test_routes.py::test_qa_page_loads -v`
Expected: PASS

**Step 4: Commit**

```bash
git add app/routes/qa.py templates/qa.html
git commit -m "feat: add SSE progress feedback to Q&A"
```

---

## Task 8: Add SSE Progress to Compare and Matrix

Compare and Matrix can take 15-60+ seconds. Matrix was the original broken feature that hung forever.

**Files:**
- Modify: `app/routes/compare.py` (add streaming endpoint for AI compare)
- Modify: `app/routes/matrix.py` (add streaming endpoint for matrix compare)
- Modify: `templates/matrix.html` (wire up progress modal)
- Modify: `templates/compare.html` (wire up progress modal)

**Step 1: Add SSE streaming endpoint for AI Compare**

In `app/routes/compare.py`, add:

```python
import json
from fastapi.responses import StreamingResponse

@router.get("/results-stream")
async def compare_results_stream(
    request: Request,
    topic: str = "",
    doc_ids: list[int] = Query(None),
    mode: str = "ai",
):
    """SSE streaming AI comparison with progress."""
    if not doc_ids or len(doc_ids) < 2:
        return StreamingResponse(
            _compare_error_stream("Select at least 2 documents."),
            media_type="text/event-stream",
        )

    async def event_stream():
        def send_event(event_type, data):
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        yield send_event("progress", {"pct": 10, "step": "Retrieving content from documents..."})

        try:
            ai_result = ai_compare_documents(doc_ids, topic if topic.strip() else None)
        except Exception as e:
            yield send_event("error", {"message": str(e)})
            return

        yield send_event("progress", {"pct": 90, "step": "Rendering comparison..."})

        if ai_result.get("error"):
            yield send_event("error", {"message": ai_result["error"]})
            return

        html_content = templates.get_template("components/compare_ai_results.html").render(
            ai_result=ai_result,
            topic=topic,
        )

        yield send_event("complete", {"html": html_content})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _compare_error_stream(message: str):
    yield f"event: error\ndata: {json.dumps({'message': message})}\n\n"
```

**Step 2: Add SSE streaming endpoint for Matrix Compare**

In `app/routes/matrix.py`, add:

```python
import json
from fastapi.responses import StreamingResponse

@router.post("/compare-stream")
async def matrix_compare_stream(
    request: Request,
    topic: str = Form(""),
    file_ids: list[int] = Form([]),
):
    """SSE streaming matrix comparison with progress."""
    if not topic.strip():
        return StreamingResponse(
            _matrix_error_stream("Please enter a topic."),
            media_type="text/event-stream",
        )
    if len(file_ids) < 2:
        return StreamingResponse(
            _matrix_error_stream("Select at least 2 documents."),
            media_type="text/event-stream",
        )

    async def event_stream():
        def send_event(event_type, data):
            return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        yield send_event("progress", {"pct": 10, "step": "Retrieving content from documents..."})

        try:
            yield send_event("progress", {"pct": 30, "step": "Analyzing with Claude..."})
            matrix_result = compare_matrix(topic.strip(), file_ids)
        except Exception as e:
            yield send_event("error", {"message": f"Matrix comparison failed: {str(e)}"})
            return

        if matrix_result.get("error"):
            yield send_event("error", {"message": matrix_result["error"]})
            return

        yield send_event("progress", {"pct": 90, "step": "Building comparison table..."})

        html_content = templates.get_template("components/matrix_results.html").render(
            matrix=matrix_result,
            topic=topic.strip(),
            file_ids=file_ids,
            error=None,
        )

        yield send_event("complete", {"html": html_content})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _matrix_error_stream(message: str):
    yield f"event: error\ndata: {json.dumps({'message': message})}\n\n"
```

**Step 3: Wire up matrix form to use progress modal**

In `templates/matrix.html`, add a script block to intercept the form submit and use ProgressModal instead of HTMX spinner:

```html
<script>
document.addEventListener('DOMContentLoaded', function() {
    const form = document.querySelector('form[hx-post="/matrix/compare"]');
    if (form && typeof ProgressModal !== 'undefined') {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            e.stopPropagation();

            const formData = new FormData(form);
            const resultTarget = document.getElementById('matrix-results');

            ProgressModal.start('/matrix/compare-stream', {
                method: 'POST',
                body: formData,
                title: 'Building Comparison Matrix',
                onComplete: (data) => {
                    if (data.html && resultTarget) {
                        resultTarget.innerHTML = data.html;
                        lucide.createIcons();
                    }
                },
            });
        });
    }
});
</script>
```

**Step 4: Wire up compare form similarly**

In `templates/compare.html`, add similar JS for the AI compare mode form.

**Step 5: Run tests**

Run: `python -m pytest tests/test_routes.py -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add app/routes/compare.py app/routes/matrix.py templates/matrix.html templates/compare.html
git commit -m "feat: add SSE progress feedback to Compare and Matrix features"
```

---

## Task 9: Run Full Test Suite and Fix Any Issues

**Step 1: Run all tests**

Run: `python -m pytest tests/ -v --tb=short`

**Step 2: Fix any failures caused by our changes**

Common issues to watch for:
- Template rendering errors from new progress_modal include
- Import errors from new SSE endpoints
- Form submission behavior changes

**Step 3: Manual smoke test**

1. Start the app: `python run.py`
2. Go to Q&A, ask "What is the overtime rate?" — should see progress modal, then answer
3. Go to Matrix Compare, select 2 docs, enter "wages" — should see progress modal, then table
4. Go to Admin > Scan & Index — should see progress modal during indexing

**Step 4: Final commit if needed**

```bash
git add -A
git commit -m "fix: address test failures from search quality and UX changes"
```

---

## Post-Implementation: Re-index Documents

After all code changes are deployed, the user must re-index documents to:
1. Apply the broken-word text fixes
2. Build semantic embeddings for the first time

**Steps:**
1. Start the app: `python run.py`
2. Go to Admin Panel > Scan & Index All
3. Wait for progress modal to complete (will take 2-5 minutes for 3 documents + 570 chunks)
4. Verify on Diagnostics page that embeddings count > 0

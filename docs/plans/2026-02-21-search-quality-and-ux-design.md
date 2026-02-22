# Search Quality & UX Improvements Design

**Date:** 2026-02-21
**Approach:** Full fix (Layers 1 + 2 + 3)

## Overview

The core search, Q&A, and matrix features are returning poor results or hanging indefinitely. Three root causes identified:

1. **Claude model too weak** — Using Haiku 3 (2024) instead of Sonnet 4.5
2. **No semantic search** — Vector embeddings disabled; only keyword FTS active
3. **PDF text extraction artifacts** — Broken words like "member s" degrade keyword matching

Additionally, long operations (Q&A, Compare, Matrix, Indexing) provide no progress feedback, making the app feel broken when they take time.

## Root Cause Analysis

### Why search returns garbage
- FTS keyword search matches literal words, not meaning. "Overtime" finds any page with that word, regardless of relevance.
- Haiku 3 is too small to reliably extract precise values from legal text, follow citation rules, or avoid hallucination.
- 41/205 pages have broken words ("member s", "pe rform") that FTS cannot match.
- Synonym expansion exists in code but isn't wired into the primary search path.

### Why Matrix hangs forever
- Matrix calls semantic search, which tries to load ChromaDB. No embeddings exist, so it hangs with no timeout.
- ThreadPoolExecutor blocks indefinitely waiting for responses.
- No timeout on Claude API calls.

### Do we need manual parsing?
No. Text extraction quality is good — clean, readable legal text with proper article/section structure. 570 structured chunks exist. The problem is the search layer, not the extraction layer.

## Layer 1: Fix Search Quality

### 1a. Upgrade Claude Model
- Change .env CLAUDE_MODEL from `claude-3-haiku-20240307` to `claude-sonnet-4-5-20241022`
- Update .env.example default
- Single highest-impact change

### 1b. Fix Broken Word Extraction
- Add post-processing in `app/services/pdf_extract.py` to rejoin spurious spaces
- Pattern: isolated single letter + space + lowercase continuation
- Apply during text normalization pipeline
- Re-index all documents after fix

### 1c. Add Timeouts
- `timeout=60` on all `anthropic.messages.create()` calls
- `timeout=30` on ChromaDB/semantic search
- `timeout=10` per-document on ThreadPoolExecutor retrieval
- Return clear error messages on timeout instead of hanging

### 1d. Wire Up Synonyms
- Seed custom_synonyms table with built-in labor terms
- Ensure synonym expansion runs in primary retrieval path, not just as late fallback

**Files:** `.env`, `.env.example`, `app/services/pdf_extract.py`, `app/services/qa.py`, `app/services/compare_ai.py`, `app/services/compare_matrix.py`, `app/services/semantic_search.py`, `app/services/synonyms.py`

## Layer 2: Enable Semantic Search

### 2a. Enable Embeddings in Indexing
- Change `build_embeddings` default from `False` to `True` in `app/services/indexer.py`
- Embeddings built automatically during "Scan & Index All"

### 2b. Ensure Dependencies
- sentence-transformers, chromadb, torch must be installed
- BGE-base-en-v1.5 model downloaded on first use (~440MB)
- ChromaDB stores in `data/index/chroma/`

### 2c. Re-index All Documents
- Full re-index of all 3 documents with embeddings enabled
- Builds vectors for all 570 chunks
- Activates hybrid search path (FTS + semantic + chunk FTS with weighted RRF)

### 2d. Graceful Fallback
- If ChromaDB fails, fall back to FTS-only (current behavior)
- Log warning, don't crash
- Diagnostics page shows embedding availability

**Files:** `app/services/indexer.py`, `app/services/semantic_search.py`, `app/routes/admin.py`

## Layer 3: Progress UX

### 3a. Progress Modal Component
- Reusable modal for long operations
- Shows: operation name, animated progress bar, step description
- Server-Sent Events (SSE) for real-time streaming
- Non-dismissable while running; auto-closes on completion
- Shows error with "Close" button on failure

### 3b. Operations with Progress
- Q&A: "Searching documents..." → "Analyzing with Claude..." → "Verifying citations..."
- Compare: "Retrieving content..." → "Comparing documents..." → done
- Matrix: "Retrieving content for [doc]..." (per doc) → "Building matrix..." → done
- Indexing: "Extracting text..." → "Building chunks..." → "Generating embeddings..." → done

### 3c. Implementation
- Backend: StreamingResponse yielding SSE events
- Frontend: EventSource API updating modal in real-time
- Fallback: Existing spinner behavior if SSE fails

**Files:** `templates/components/progress_modal.html` (new), `static/js/progress.js` (new), `app/routes/qa.py`, `app/routes/compare.py`, `app/routes/matrix.py`, `app/routes/admin.py`

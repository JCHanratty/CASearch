# Search v2 Audit Report

## Overview

This document details the Search v2 improvements implemented to enhance search quality and retrieval accuracy in the Contract Dashboard application.

## Changes Implemented

### 1. PDF Text Extraction Improvements

**File:** `app/services/pdf_extract.py`

#### 1.1 Dehyphenation
- **Problem:** PDFs often split words across lines with hyphens (e.g., "bene-\nfits")
- **Solution:** Added `dehyphenate()` function that joins hyphenated words when the second part starts with lowercase
- **Logic:** `word-\nlowercase` becomes `wordlowercase`, but `word-\nUppercase` keeps the hyphen

#### 1.2 Text Normalization
- **Problem:** Inconsistent whitespace, CRLF line endings, excessive blank lines
- **Solution:** Added `normalize_text()` function that:
  - Normalizes all line endings to `\n`
  - Removes excessive whitespace within lines
  - Removes excessive blank lines

#### 1.3 Header/Footer Detection and Stripping
- **Problem:** Repeated headers/footers pollute search results
- **Solution:** Added `detect_repeated_lines()` and `remove_repeated_lines()` functions
- **Logic:**
  - Lines appearing on >= 60% of pages are considered headers/footers
  - Lines starting with "Article" are preserved (important content)
  - Requires minimum 3 pages to detect patterns
- **Data Model:** `PageText` now has both `text` (cleaned) and `raw_text` (normalized but complete)

### 2. Database Schema Changes

**File:** `app/db.py`

- **Schema Version:** Upgraded from 1 to 2
- **New Column:** Added `raw_text` column to `pdf_pages` table
- **Migration:** Automatic migration adds column for existing databases
- **FTS Index:** Uses cleaned `text` for better search accuracy

### 3. Search Query Improvements

**File:** `app/services/search.py`

#### 3.1 Query Parsing
- **New Function:** `parse_query()` separates quoted phrases from individual words
- **Quoted Phrases:** `"overtime rate"` is kept as exact phrase match
- **Stopwords:** Common words (the, is, what, etc.) are filtered from individual terms
- **Note:** Stopwords inside quoted phrases are preserved

#### 3.2 Query Building
- **New Function:** `build_fts_query()` constructs FTS5-compatible queries
- **AND Mode (default):** All terms must match
- **OR Mode:** Any term can match
- **Prefix Matching:** Individual words use prefix matching (`word*`)

#### 3.3 Search Function Enhancements
- **Mode Parameter:** `search_pages(query, mode="and"|"or")`
- **File Filter:** `search_pages(query, file_id=123)` restricts to specific document
- **Fallback:** If AND mode returns no results, automatically retries with OR

#### 3.4 Result Ranking
- **New Function:** `rank_results_by_phrase_proximity()`
- **Ranking Priority:**
  1. Exact phrase matches (highest)
  2. Terms appearing close together (proximity)
  3. Original BM25 score (lowest)

### 4. Search UI Improvements

**File:** `templates/search.html`

- **Document Filter:** Dropdown to restrict search to specific document
- **Mode Toggle:** Radio buttons for AND (all terms) vs OR (any term)
- **Help Text:** Tip for using quoted phrases
- **Placeholder:** Updated to indicate phrase support

### 5. Route Updates

**File:** `app/routes/search.py`

- Added `mode` parameter (default: "and")
- Added `file_id` parameter for document filtering
- Applies phrase/proximity re-ranking to results
- Returns indexed files list for dropdown

## Test Coverage

### New Tests Added

**File:** `tests/test_search.py`
- Query parsing tests (7 tests)
- FTS query building tests (5 tests)
- Ranking tests (2 tests)

**File:** `tests/test_pdf_extract.py`
- Dehyphenation tests (3 tests)
- Text normalization tests (2 tests)
- Header/footer detection tests (5 tests)
- PageText structure tests (1 test)
- Integration tests (2 updated)

**Total:** 33 tests, all passing

## Usage Examples

### Basic Search
```
overtime rate
```
Searches for documents containing both "overtime" AND "rate"

### Phrase Search
```
"overtime rate"
```
Searches for the exact phrase "overtime rate"

### Mixed Search
```
"overtime rate" wages benefits
```
Searches for documents with exact phrase "overtime rate" AND both "wages" AND "benefits"

### OR Mode
Toggle to "Any Term (OR)" mode:
```
overtime rate
```
Searches for documents containing either "overtime" OR "rate"

### Document Filter
Select a specific document from dropdown to restrict search

## Reindexing Note

To take advantage of dehyphenation and header/footer stripping, existing documents should be reindexed:

1. Go to Documents page
2. Click "Reindex All" on the Diagnostics page, or
3. Manually reindex each document by clicking "Reindex" button

## Manual Verification Steps

### Verifying "Spruce Grove benefits" Search Behavior

To confirm the search improvements are working correctly:

1. **Setup:** Ensure you have at least one PDF containing "Spruce Grove" and "benefits" indexed
2. **Test AND mode (default):**
   - Search: `Spruce Grove benefits`
   - Expected: Only pages containing ALL terms appear
   - Results should be ranked with pages where terms appear close together at the top

3. **Test phrase search:**
   - Search: `"Spruce Grove" benefits`
   - Expected: Pages must have exact phrase "Spruce Grove" AND the term "benefits"
   - Results with the exact phrase should rank higher

4. **Test document filter:**
   - Select a specific document from the dropdown
   - Search: `benefits`
   - Expected: Only results from the selected document

5. **Test OR mode:**
   - Toggle to "Any Term (OR)"
   - Search: `Spruce Grove benefits`
   - Expected: Pages with ANY of the terms appear (more results than AND mode)

6. **Verify header/footer stripping:**
   - Search for text that appears in headers/footers (e.g., agreement title)
   - Expected: Reduced noise - header/footer text should not dominate results

### Checking Clean vs Raw Text

To verify header/footer stripping is working:

```sql
-- Run in SQLite
SELECT
    f.filename,
    p.page_number,
    length(p.text) as clean_len,
    length(p.raw_text) as raw_len
FROM pdf_pages p
JOIN files f ON p.file_id = f.id
WHERE p.raw_text IS NOT NULL
LIMIT 10;
```

If `clean_len < raw_len`, header/footer lines were removed from the indexed text.

## Performance Considerations

- Header/footer detection requires 3+ pages per document
- Phrase proximity ranking adds minimal overhead
- FTS5 index handles AND/OR modes efficiently
- Fallback to OR mode only triggers when AND returns no results

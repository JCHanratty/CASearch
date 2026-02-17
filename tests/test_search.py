"""Tests for search service."""

import pytest


# ============================================================================
# Query Parsing Tests
# ============================================================================

def test_parse_query_simple_words():
    """Test parsing simple words."""
    from app.services.search import parse_query

    phrases, words = parse_query("hourly rate")
    assert phrases == []
    assert "hourly" in words
    assert "rate" in words


def test_parse_query_quoted_phrase():
    """Test parsing quoted phrases."""
    from app.services.search import parse_query

    phrases, words = parse_query('"overtime rate" wages')
    assert "overtime rate" in phrases
    assert "wages" in words


def test_parse_query_multiple_phrases():
    """Test parsing multiple quoted phrases."""
    from app.services.search import parse_query

    phrases, words = parse_query('"overtime rate" "holiday pay"')
    assert "overtime rate" in phrases
    assert "holiday pay" in phrases
    assert words == []


def test_parse_query_stopwords_removed():
    """Test that stopwords are removed from words."""
    from app.services.search import parse_query

    phrases, words = parse_query("what is the overtime rate")
    assert "what" not in words
    assert "is" not in words
    assert "the" not in words
    assert "overtime" in words
    assert "rate" in words


def test_parse_query_stopwords_kept_in_phrases():
    """Test that stopwords inside phrases are kept."""
    from app.services.search import parse_query

    phrases, words = parse_query('"what is the rate"')
    # The phrase should be kept as-is
    assert "what is the rate" in phrases


# ============================================================================
# FTS Query Building Tests
# ============================================================================

def test_build_fts_query_and_mode():
    """Test FTS query building with AND mode."""
    from app.services.search import build_fts_query

    result = build_fts_query("hourly rate", mode="and")
    assert " AND " in result
    # Single words use bare prefix syntax (word*) not quoted ("word"*)
    # because FTS5 doesn't support "word"* syntax
    assert 'hourly*' in result
    assert 'rate*' in result


def test_build_fts_query_or_mode():
    """Test FTS query building with OR mode."""
    from app.services.search import build_fts_query

    result = build_fts_query("hourly rate", mode="or")
    assert " OR " in result
    # Single words use bare prefix syntax
    assert 'hourly*' in result
    assert 'rate*' in result


def test_build_fts_query_with_phrase():
    """Test FTS query building with quoted phrase."""
    from app.services.search import build_fts_query

    result = build_fts_query('"overtime rate" wages', mode="and")
    # Phrases are quoted, single words are not
    assert '"overtime rate"' in result
    assert 'wages*' in result
    assert " AND " in result


def test_escape_fts_query_simple():
    """Test FTS query escaping for simple terms."""
    from app.services.search import escape_fts_query

    result = escape_fts_query("wages")
    # Single words use bare prefix syntax
    assert result == 'wages*'


def test_escape_fts_query_multiple_words():
    """Test FTS query escaping for multiple words with AND mode."""
    from app.services.search import escape_fts_query

    result = escape_fts_query("hourly rate", mode="and")
    # Single words use bare prefix syntax
    assert 'hourly*' in result
    assert 'rate*' in result
    assert " AND " in result


def test_escape_fts_query_special_chars():
    """Test FTS query escaping removes special characters."""
    from app.services.search import escape_fts_query

    result = escape_fts_query("test@query!")
    # Special chars should be removed
    assert "@" not in result
    assert "!" not in result


def test_escape_fts_query_empty():
    """Test FTS query escaping with empty string."""
    from app.services.search import escape_fts_query

    result = escape_fts_query("")
    assert result == ""


# ============================================================================
# Ranking Tests
# ============================================================================

def test_rank_results_by_phrase_proximity():
    """Test phrase/proximity ranking."""
    from app.services.search import rank_results_by_phrase_proximity
    from app.models import SearchResult

    # Create mock results
    results = [
        SearchResult(
            file_id=1, file_path="/a.pdf", filename="a.pdf",
            page_number=1, snippet="scattered hourly text and rate here", score=0.5
        ),
        SearchResult(
            file_id=2, file_path="/b.pdf", filename="b.pdf",
            page_number=1, snippet="the overtime rate is determined", score=0.6
        ),
    ]

    ranked = rank_results_by_phrase_proximity(results, '"overtime rate"')

    # Result with phrase match should be first
    assert ranked[0].file_id == 2


def test_rank_results_proximity_boost():
    """Test proximity scoring for close words."""
    from app.services.search import rank_results_by_phrase_proximity
    from app.models import SearchResult

    results = [
        SearchResult(
            file_id=1, file_path="/a.pdf", filename="a.pdf",
            page_number=1, snippet="hourly xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx rate", score=0.5
        ),
        SearchResult(
            file_id=2, file_path="/b.pdf", filename="b.pdf",
            page_number=1, snippet="the hourly rate is determined", score=0.6
        ),
    ]

    ranked = rank_results_by_phrase_proximity(results, "hourly rate")

    # Result with closer words should be first (file_id=2 has words closer together)
    assert ranked[0].file_id == 2


def test_search_empty_index(test_db):
    """Test searching with no indexed documents."""
    from app.services.search import search_pages

    results = search_pages("wages")
    assert results == []


def test_search_with_indexed_content(test_db, sample_pdf):
    """Test search after indexing."""
    from app.services.file_scanner import scan_agreements
    from app.services.indexer import index_file
    from app.services.search import search_pages
    from app.db import get_db

    # Scan and index
    scan_agreements()
    with get_db() as conn:
        file_row = conn.execute("SELECT id FROM files").fetchone()
        if file_row:
            index_file(file_row["id"])

    # Search (may not find anything if PDF has no text)
    results = search_pages("test")
    # Just verify no errors - content depends on PDF
    assert isinstance(results, list)


def test_get_search_stats(test_db):
    """Test getting search statistics."""
    from app.services.search import get_search_stats

    stats = get_search_stats()
    assert "indexed_pages" in stats
    assert "indexed_files" in stats

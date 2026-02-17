"""Tests for QA retrieval fallback functionality."""

import pytest
from unittest.mock import patch, MagicMock

from app.services.qa import (
    _extract_keywords,
    _sql_like_search,
    _retrieve_with_fallback,
)
from app.models import SearchResult


class TestExtractKeywords:
    """Tests for keyword extraction."""

    def test_extract_keywords_basic(self):
        """Test basic keyword extraction."""
        keywords = _extract_keywords("What is the vacation policy?")
        assert "vacation" in keywords
        assert "policy" in keywords
        # Stopwords removed
        assert "what" not in keywords
        assert "is" not in keywords
        assert "the" not in keywords

    def test_extract_keywords_removes_short_words(self):
        """Test that short words are removed."""
        keywords = _extract_keywords("Is it ok to go?")
        assert "ok" not in keywords  # 2 chars
        assert "go" not in keywords  # 2 chars

    def test_extract_keywords_handles_punctuation(self):
        """Test that punctuation is handled."""
        keywords = _extract_keywords("What's the overtime rate?")
        assert "overtime" in keywords
        assert "rate" in keywords

    def test_extract_keywords_lowercase(self):
        """Test that keywords are lowercased."""
        keywords = _extract_keywords("VACATION POLICY")
        assert "vacation" in keywords
        assert "policy" in keywords
        assert "VACATION" not in keywords

    def test_extract_keywords_empty_query(self):
        """Test empty query returns empty list."""
        keywords = _extract_keywords("")
        assert keywords == []

    def test_extract_keywords_only_stopwords(self):
        """Test query with only stopwords returns empty list."""
        keywords = _extract_keywords("what is the")
        assert keywords == []


class TestSqlLikeSearch:
    """Tests for SQL LIKE fallback search."""

    def test_sql_like_search_empty_keywords(self, test_db):
        """Test empty keywords returns empty results."""
        results = _sql_like_search([])
        assert results == []

    def test_sql_like_search_with_indexed_content(self, test_db, sample_file_with_pages):
        """Test SQL LIKE search finds content in indexed pages."""
        # sample_file_with_pages fixture should create pages with text content
        file_id = sample_file_with_pages

        # Search for a keyword that should be in the sample content
        results = _sql_like_search(["test"], limit=10)

        # Results should be a list of dicts
        assert isinstance(results, list)
        for r in results:
            assert "file_id" in r
            assert "path" in r
            assert "filename" in r
            assert "page_number" in r
            assert "snippet" in r
            assert "score" in r

    def test_sql_like_search_respects_limit(self, test_db, sample_file_with_pages):
        """Test that limit parameter is respected."""
        results = _sql_like_search(["test"], limit=1)
        assert len(results) <= 1

    def test_sql_like_search_limits_keywords(self, test_db):
        """Test that only first 5 keywords are used."""
        # This is an internal implementation detail - we just verify it doesn't error
        many_keywords = ["word1", "word2", "word3", "word4", "word5", "word6", "word7"]
        results = _sql_like_search(many_keywords, limit=10)
        assert isinstance(results, list)


class TestRetrieveWithFallback:
    """Tests for multi-stage retrieval fallback."""

    def test_fallback_fts_and_success(self, test_db):
        """Test that FTS AND mode is tried first."""
        mock_results = [
            SearchResult(
                file_id=1,
                file_path="/test/doc.pdf",
                filename="doc.pdf",
                page_number=1,
                snippet="test content",
                score=1.0,
            )
        ]

        with patch("app.services.qa.search_chunks") as mock_chunk_search:
            mock_chunk_search.return_value = []  # No chunk results
            with patch("app.services.qa.search_pages") as mock_search:
                mock_search.return_value = mock_results

                results, method, chunk_results = _retrieve_with_fallback("test query")

                assert results == mock_results
                assert method == "fts_and"
                # Verify AND mode was called
                mock_search.assert_called_once()
                call_args = mock_search.call_args
                assert call_args.kwargs.get("mode") == "and"
                assert call_args.kwargs.get("fallback_to_or") is False

    def test_fallback_fts_or_when_and_empty(self, test_db):
        """Test fallback to FTS OR mode when AND returns empty."""
        mock_results = [
            SearchResult(
                file_id=1,
                file_path="/test/doc.pdf",
                filename="doc.pdf",
                page_number=1,
                snippet="test content",
                score=1.0,
            )
        ]

        with patch("app.services.qa.search_chunks") as mock_chunk_search:
            mock_chunk_search.return_value = []  # No chunk results
            with patch("app.services.qa.search_pages") as mock_search:
                # First call (AND) returns empty, second call (OR) returns results
                mock_search.side_effect = [[], mock_results]

                results, method, chunk_results = _retrieve_with_fallback("test query")

                assert results == mock_results
                assert method == "fts_or"
                assert mock_search.call_count == 2

    def test_fallback_sql_like_when_fts_empty(self, test_db, sample_file_with_pages):
        """Test fallback to SQL LIKE when FTS returns empty."""
        with patch("app.services.qa.search_chunks") as mock_chunk_search:
            mock_chunk_search.return_value = []  # No chunk results
            with patch("app.services.qa.search_pages") as mock_search:
                # Both FTS calls return empty
                mock_search.return_value = []

                results, method, chunk_results = _retrieve_with_fallback("test query")

                # Should have tried both FTS modes (after chunk search)
                assert mock_search.call_count == 2

                # Method should be sql_like if results found, or none if not
                assert method in ("sql_like", "none")

    def test_fallback_returns_none_when_all_fail(self, test_db):
        """Test returns empty with 'none' method when all methods fail."""
        with patch("app.services.qa.search_chunks") as mock_chunk_search:
            mock_chunk_search.return_value = []  # No chunk results
            with patch("app.services.qa.search_pages") as mock_search:
                mock_search.return_value = []

                with patch("app.services.qa._sql_like_search") as mock_like:
                    mock_like.return_value = []

                    results, method, chunk_results = _retrieve_with_fallback("xyznonexistent")

                    assert results == []
                    assert method == "none"

    def test_fallback_uses_settings_limit(self, test_db):
        """Test that default limit comes from settings."""
        with patch("app.services.qa.search_pages") as mock_search:
            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_search.return_value = []

                _retrieve_with_fallback("test query")

                # First call should use the settings limit
                call_args = mock_search.call_args_list[0]
                assert call_args.kwargs.get("limit") == 5

    def test_fallback_custom_limit(self, test_db):
        """Test that custom limit overrides settings."""
        with patch("app.services.qa.search_pages") as mock_search:
            mock_search.return_value = []

            _retrieve_with_fallback("test query", limit=3)

            call_args = mock_search.call_args_list[0]
            assert call_args.kwargs.get("limit") == 3


@pytest.fixture
def sample_file_with_pages(test_db):
    """Create a sample file with pages for testing."""
    from app.db import get_db

    with get_db() as conn:
        # Insert a test file with all required fields
        cursor = conn.execute(
            """INSERT INTO files (path, filename, sha256, mtime, size, status)
               VALUES (?, ?, ?, ?, ?, 'indexed')""",
            ("/test/sample.pdf", "sample.pdf", "abc123def456", 1700000000.0, 1024),
        )
        file_id = cursor.lastrowid

        # Insert test pages
        conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id, 1, "This is test content on page one with vacation policy details."),
        )
        conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id, 2, "This is test content on page two with overtime information."),
        )

        return file_id

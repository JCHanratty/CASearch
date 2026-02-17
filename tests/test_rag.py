"""Tests for the RAG (Retrieval Augmented Generation) service."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.services.rag import (
    embed_text,
    add_page_embedding,
    search_similar,
    rebuild_vector_index,
    get_vector_index_stats,
    vector_search_to_search_result,
    VectorSearchResult,
    _get_index_path,
)
from app.models import SearchResult


@pytest.fixture
def temp_index_dir(tmp_path):
    """Create a temporary directory for the vector index."""
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    return index_dir


@pytest.fixture
def mock_settings(temp_index_dir, tmp_path):
    """Mock settings with temporary paths."""
    from app.settings import Settings

    db_path = tmp_path / "test.db"
    agreements_dir = tmp_path / "agreements"
    agreements_dir.mkdir()

    return Settings(
        DATABASE_PATH=db_path,
        AGREEMENTS_DIR=agreements_dir,
        INDEX_DIR=temp_index_dir,
        ANTHROPIC_API_KEY="test-key",
        MAX_RETRIEVAL_RESULTS=5,
    )


@pytest.fixture
def test_db_with_pages(mock_settings, monkeypatch):
    """Initialize test database with sample pages."""
    monkeypatch.setattr("app.settings.settings", mock_settings)
    monkeypatch.setattr("app.db.settings", mock_settings)
    monkeypatch.setattr("app.services.rag.settings", mock_settings)

    from app.db import init_db, get_db

    init_db()

    # Insert sample files and pages
    with get_db() as conn:
        # Insert a test file
        conn.execute("""
            INSERT INTO files (path, filename, sha256, mtime, size, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("/test/contract1.pdf", "contract1.pdf", "abc123", 1234567890.0, 1000, "indexed"))

        file_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert test pages with contract-like content
        pages = [
            (file_id, 1, "Article 1: Wages and Compensation. The hourly wage rate shall be twenty-five dollars for all regular employees."),
            (file_id, 2, "Article 2: Vacation Leave. Employees are entitled to fifteen days of paid vacation per year after one year of service."),
            (file_id, 3, "Article 3: Sick Leave Policy. Employees may accrue up to twelve sick days annually. Unused sick leave may be carried over."),
            (file_id, 4, "Article 4: Grievance Procedure. Step 1: Informal discussion with supervisor. Step 2: Written grievance to department head."),
            (file_id, 5, "Article 5: Overtime. Overtime work shall be compensated at one and one-half times the regular hourly rate."),
        ]

        for page in pages:
            conn.execute("""
                INSERT INTO pdf_pages (file_id, page_number, text)
                VALUES (?, ?, ?)
            """, page)

        # Insert another file
        conn.execute("""
            INSERT INTO files (path, filename, sha256, mtime, size, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("/test/contract2.pdf", "contract2.pdf", "def456", 1234567891.0, 2000, "indexed"))

        file_id2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Insert more pages
        pages2 = [
            (file_id2, 1, "Section 1: Health Benefits. The employer shall provide comprehensive health insurance coverage."),
            (file_id2, 2, "Section 2: Retirement Plan. Employees are eligible for the pension plan after five years of service."),
        ]

        for page in pages2:
            conn.execute("""
                INSERT INTO pdf_pages (file_id, page_number, text)
                VALUES (?, ?, ?)
            """, page)

    yield mock_settings


class TestEmbedText:
    """Tests for embed_text function."""

    def test_embed_text_returns_empty_without_index(self, mock_settings, monkeypatch):
        """Test that embed_text returns empty list when no index exists."""
        monkeypatch.setattr("app.services.rag.settings", mock_settings)
        monkeypatch.setattr("app.services.rag._vectorizer", None)
        monkeypatch.setattr("app.services.rag._embeddings_matrix", None)
        monkeypatch.setattr("app.services.rag._page_metadata", [])

        result = embed_text("test query")
        assert result == []

    def test_embed_text_returns_vector_with_index(self, test_db_with_pages, monkeypatch):
        """Test that embed_text returns a vector after index is built."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        # Build the index first
        rebuild_result = rebuild_vector_index()
        assert rebuild_result["success"] is True

        # Now embed some text
        result = embed_text("vacation leave policy")

        # Should return a non-empty list of floats
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(x, float) for x in result)

    def test_embed_text_different_texts_produce_different_vectors(self, test_db_with_pages, monkeypatch):
        """Test that different texts produce different embeddings."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        # Build the index first
        rebuild_vector_index()

        vec1 = embed_text("vacation leave policy")
        vec2 = embed_text("overtime compensation rate")

        assert vec1 != vec2


class TestAddAndSearchSimilar:
    """Tests for add_page_embedding and search_similar functions."""

    def test_search_similar_empty_without_index(self, mock_settings, monkeypatch):
        """Test that search returns empty list when no index exists."""
        monkeypatch.setattr("app.services.rag.settings", mock_settings)
        monkeypatch.setattr("app.services.rag._vectorizer", None)
        monkeypatch.setattr("app.services.rag._embeddings_matrix", None)
        monkeypatch.setattr("app.services.rag._page_metadata", [])

        results = search_similar("test query", limit=5)
        assert results == []

    def test_search_similar_returns_results(self, test_db_with_pages, monkeypatch):
        """Test that search returns relevant results after indexing."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        # Build the index
        rebuild_result = rebuild_vector_index()
        assert rebuild_result["success"] is True

        # Search for vacation-related content
        results = search_similar("vacation leave days", limit=5)

        # Should return results
        assert len(results) > 0
        assert isinstance(results[0], VectorSearchResult)

        # Top result should be vacation-related (page 2)
        top_result = results[0]
        assert "vacation" in top_result.text.lower() or top_result.page_number == 2

    def test_search_similar_respects_limit(self, test_db_with_pages, monkeypatch):
        """Test that search respects the limit parameter."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        rebuild_vector_index()

        results = search_similar("employee benefits", limit=2)
        assert len(results) <= 2

    def test_search_similar_filters_by_file_id(self, test_db_with_pages, monkeypatch):
        """Test that search can filter by file_id."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        rebuild_vector_index()

        # Search only in file_id=1
        results = search_similar("benefits", limit=10, file_id=1)

        # All results should be from file_id=1
        for result in results:
            assert result.file_id == 1

    def test_search_similar_scores_in_range(self, test_db_with_pages, monkeypatch):
        """Test that similarity scores are in valid range (0-1)."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        rebuild_vector_index()

        results = search_similar("wages compensation hourly", limit=10)

        for result in results:
            assert 0 <= result.score <= 1


class TestRebuildVectorIndex:
    """Tests for rebuild_vector_index function."""

    def test_rebuild_creates_index(self, test_db_with_pages, monkeypatch):
        """Test that rebuild creates a vector index."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        result = rebuild_vector_index()

        assert result["success"] is True
        assert result["pages_indexed"] == 7  # 5 + 2 pages
        assert result["vocabulary_size"] > 0

    def test_rebuild_with_progress_callback(self, test_db_with_pages, monkeypatch):
        """Test that progress callback is called during rebuild."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        progress_calls = []

        def track_progress(current, total, message):
            progress_calls.append((current, total, message))

        result = rebuild_vector_index(progress_callback=track_progress)

        assert result["success"] is True
        assert len(progress_calls) > 0

    def test_rebuild_handles_empty_database(self, mock_settings, monkeypatch):
        """Test that rebuild handles empty database gracefully."""
        monkeypatch.setattr("app.settings.settings", mock_settings)
        monkeypatch.setattr("app.db.settings", mock_settings)
        monkeypatch.setattr("app.services.rag.settings", mock_settings)

        from app.db import init_db
        init_db()

        result = rebuild_vector_index()

        assert result["success"] is False
        assert result["pages_indexed"] == 0
        assert "No indexed pages" in result["message"]


class TestHybridSearch:
    """Tests for hybrid search combining FTS and vector search."""

    def test_hybrid_search_combines_results(self, test_db_with_pages, monkeypatch):
        """Test that hybrid search combines FTS and vector results."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)
        monkeypatch.setattr("app.services.search.settings", test_db_with_pages)
        monkeypatch.setattr("app.services.qa.settings", test_db_with_pages)

        # Build vector index
        rebuild_vector_index()

        # Import the hybrid merge function
        from app.services.qa import _merge_hybrid_results

        # Create mock FTS results
        fts_results = [
            SearchResult(file_id=1, file_path="/test/c1.pdf", filename="c1.pdf", page_number=1, snippet="wages", score=1.0),
            SearchResult(file_id=1, file_path="/test/c1.pdf", filename="c1.pdf", page_number=2, snippet="vacation", score=0.9),
        ]

        # Create mock vector results (overlapping and non-overlapping)
        vector_results = [
            SearchResult(file_id=1, file_path="/test/c1.pdf", filename="c1.pdf", page_number=2, snippet="vacation", score=0.8),
            SearchResult(file_id=1, file_path="/test/c1.pdf", filename="c1.pdf", page_number=3, snippet="sick", score=0.7),
        ]

        merged = _merge_hybrid_results(fts_results, vector_results, limit=5)

        # Should have combined results
        assert len(merged) > 0

        # Page 2 should rank higher (appears in both)
        page_numbers = [r.page_number for r in merged]
        assert 2 in page_numbers

    def test_retrieve_with_fallback_uses_vector(self, test_db_with_pages, monkeypatch):
        """Test that _retrieve_with_fallback can fall back to vector search."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)
        monkeypatch.setattr("app.services.search.settings", test_db_with_pages)
        monkeypatch.setattr("app.services.qa.settings", test_db_with_pages)
        monkeypatch.setattr("app.services.synonyms.settings", test_db_with_pages)
        monkeypatch.setattr("app.db.settings", test_db_with_pages)

        # Build vector index
        rebuild_vector_index()

        # Also need to populate FTS index for fair comparison
        from app.db import get_db
        with get_db() as conn:
            rows = conn.execute("""
                SELECT p.id, p.file_id, p.page_number, p.text
                FROM pdf_pages p
                JOIN files f ON p.file_id = f.id
                WHERE f.status = 'indexed'
            """).fetchall()

            for row in rows:
                conn.execute("""
                    INSERT INTO page_fts (file_id, page_id, page_number, text)
                    VALUES (?, ?, ?, ?)
                """, (row["file_id"], row["id"], row["page_number"], row["text"]))

        from app.services.qa import _retrieve_with_fallback

        # Search with a term that should match
        results, method, chunk_results = _retrieve_with_fallback("vacation leave policy")

        # Should return results
        assert len(results) > 0
        # Method should be one of the valid methods
        assert method in ["fts_and", "fts_or", "fts_synonym", "sql_like", "vector", "hybrid", "none", "chunk_and", "chunk_or"]


class TestVectorIndexStats:
    """Tests for get_vector_index_stats function."""

    def test_stats_without_index(self, mock_settings, monkeypatch):
        """Test stats when no index exists."""
        monkeypatch.setattr("app.services.rag.settings", mock_settings)
        monkeypatch.setattr("app.services.rag._vectorizer", None)
        monkeypatch.setattr("app.services.rag._embeddings_matrix", None)
        monkeypatch.setattr("app.services.rag._page_metadata", [])

        stats = get_vector_index_stats()

        assert stats["index_exists"] is False
        assert stats["pages_indexed"] == 0

    def test_stats_with_index(self, test_db_with_pages, monkeypatch):
        """Test stats after building index."""
        monkeypatch.setattr("app.services.rag.settings", test_db_with_pages)

        rebuild_vector_index()
        stats = get_vector_index_stats()

        assert stats["index_exists"] is True
        assert stats["index_loaded"] is True
        assert stats["pages_indexed"] == 7
        assert stats["vocabulary_size"] > 0
        assert "index_size_mb" in stats


class TestVectorSearchResultConversion:
    """Tests for converting VectorSearchResult to SearchResult."""

    def test_conversion(self):
        """Test that VectorSearchResult converts to SearchResult correctly."""
        vector_result = VectorSearchResult(
            file_id=1,
            page_id=10,
            page_number=5,
            filename="test.pdf",
            file_path="/path/to/test.pdf",
            text="Some text snippet",
            score=0.85,
        )

        search_result = vector_search_to_search_result(vector_result)

        assert isinstance(search_result, SearchResult)
        assert search_result.file_id == 1
        assert search_result.page_number == 5
        assert search_result.filename == "test.pdf"
        assert search_result.file_path == "/path/to/test.pdf"
        assert search_result.snippet == "Some text snippet"
        assert search_result.score == 0.85

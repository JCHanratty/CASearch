"""Tests for search heading priority and detection."""

import pytest

from app.db import get_db
from app.services.search import (
    search_pages,
    rank_results_by_phrase_proximity,
    page_has_heading_match,
    get_heading_lines,
)
from app.models import SearchResult


@pytest.fixture
def pages_with_heading_and_body(test_db):
    """Create two pages: one with query in heading, one with query in body only."""
    with get_db() as conn:
        # Insert test file
        cursor = conn.execute(
            """INSERT INTO files (path, filename, sha256, mtime, size, status)
               VALUES (?, ?, ?, ?, ?, 'indexed')""",
            ("/test/contract.pdf", "contract.pdf", "hash_heading_test", 1700000000.0, 2048),
        )
        file_id = cursor.lastrowid

        # Page 1: Query appears in a heading
        page1_text = """COLLECTIVE AGREEMENT

Article 5 — Sick Time

Employees shall be entitled to sick leave benefits as follows:
- Full-time employees receive 10 sick days per year
- Part-time employees receive prorated sick days
- Unused sick days may be carried forward"""

        cursor = conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id, 1, page1_text),
        )
        page1_id = cursor.lastrowid

        # Page 2: Query appears only in body text, not heading
        page2_text = """ARTICLE 12 — LEAVES OF ABSENCE

12.1 Personal Leave
Employees may request unpaid personal leave for various reasons.

12.2 Medical Provisions
When an employee is absent due to illness, they may use their
accumulated sick time to cover the absence. The sick time policy
applies to all regular employees."""

        cursor = conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id, 2, page2_text),
        )
        page2_id = cursor.lastrowid

        # Insert into FTS index
        conn.execute(
            """INSERT INTO page_fts (file_id, page_id, page_number, text)
               VALUES (?, ?, ?, ?)""",
            (file_id, page1_id, 1, page1_text),
        )
        conn.execute(
            """INSERT INTO page_fts (file_id, page_id, page_number, text)
               VALUES (?, ?, ?, ?)""",
            (file_id, page2_id, 2, page2_text),
        )

        return file_id


class TestHeadingDetection:
    """Tests for heading line detection."""

    def test_get_heading_lines_detects_article(self, test_db):
        """Test that Article lines are detected as headings."""
        text = """Some intro text

Article 5 — Sick Time

Body text here."""
        headings = get_heading_lines(text)
        assert any("Article 5" in h for h in headings)

    def test_get_heading_lines_detects_uppercase(self, test_db):
        """Test that uppercase lines are detected as headings."""
        text = """COLLECTIVE AGREEMENT

This is body text that is not uppercase."""
        headings = get_heading_lines(text)
        assert any("COLLECTIVE AGREEMENT" in h for h in headings)

    def test_get_heading_lines_detects_section(self, test_db):
        """Test that Section lines are detected as headings."""
        text = """Introduction

Section 1: Overview

Body text."""
        headings = get_heading_lines(text)
        assert any("Section" in h for h in headings)

    def test_page_has_heading_match_returns_true(self, test_db, pages_with_heading_and_body):
        """Test page_has_heading_match returns True for heading containing query."""
        file_id = pages_with_heading_and_body

        has_match, heading = page_has_heading_match(file_id, 1, "Sick Time")

        assert has_match is True
        assert heading is not None
        assert "Sick Time" in heading

    def test_page_has_heading_match_returns_false(self, test_db, pages_with_heading_and_body):
        """Test page_has_heading_match returns False for body-only match."""
        file_id = pages_with_heading_and_body

        # Page 2 has "sick time" in body but not in headings
        has_match, heading = page_has_heading_match(file_id, 2, "sick time")

        assert has_match is False
        assert heading is None


class TestSearchHeadingPriority:
    """Tests for search result heading prioritization."""

    def test_search_heading_page_ranked_first(self, client, test_db, pages_with_heading_and_body):
        """Test that page with heading match is ranked first."""
        file_id = pages_with_heading_and_body

        # Search for "Sick Time" - should find both pages
        results = search_pages("Sick Time", limit=5)
        assert len(results) >= 1

        # Apply ranking
        ranked_results = rank_results_by_phrase_proximity(results, "Sick Time")

        # Page 1 (with heading) should be first
        assert ranked_results[0].page_number == 1

    def test_rank_results_boosts_heading_matches(self, test_db, pages_with_heading_and_body):
        """Test that rank_results_by_phrase_proximity boosts heading matches."""
        file_id = pages_with_heading_and_body

        # Create mock results in reverse order (body match first)
        results = [
            SearchResult(
                file_id=file_id,
                file_path="/test/contract.pdf",
                filename="contract.pdf",
                page_number=2,  # Body match
                snippet="sick time policy applies",
                score=1.0,
            ),
            SearchResult(
                file_id=file_id,
                file_path="/test/contract.pdf",
                filename="contract.pdf",
                page_number=1,  # Heading match
                snippet="Article 5 — Sick Time",
                score=2.0,  # Worse initial score
            ),
        ]

        ranked = rank_results_by_phrase_proximity(results, "Sick Time")

        # Heading match should be boosted to first position
        assert ranked[0].page_number == 1

    def test_heading_boost_higher_than_phrase_proximity(self, test_db, pages_with_heading_and_body):
        """Test that heading match scores higher than phrase proximity."""
        file_id = pages_with_heading_and_body

        # Create results where page 2 has better phrase proximity
        results = [
            SearchResult(
                file_id=file_id,
                file_path="/test/contract.pdf",
                filename="contract.pdf",
                page_number=2,
                snippet="sick time policy sick time benefits",  # Multiple matches
                score=0.5,  # Better base score
            ),
            SearchResult(
                file_id=file_id,
                file_path="/test/contract.pdf",
                filename="contract.pdf",
                page_number=1,
                snippet="Article 5 Sick Time",
                score=1.0,
            ),
        ]

        ranked = rank_results_by_phrase_proximity(results, "Sick Time")

        # Heading match should still be first despite page 2 having better phrase proximity
        assert ranked[0].page_number == 1

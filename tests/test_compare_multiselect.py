"""Tests for multi-select document comparison feature."""

import pytest

from app.db import get_db
from app.services.compare import compare_documents_multi


@pytest.fixture
def two_indexed_files_with_shared_term(test_db):
    """Create two indexed files with pages containing a shared term."""
    with get_db() as conn:
        # Insert first file
        cursor = conn.execute(
            """INSERT INTO files (path, filename, sha256, mtime, size, status)
               VALUES (?, ?, ?, ?, ?, 'indexed')""",
            ("/test/contract_a.pdf", "contract_a.pdf", "hash_a_123", 1700000000.0, 1024),
        )
        file_id_a = cursor.lastrowid

        # Insert pages for first file with "overtime" term
        conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id_a, 1, "This contract covers overtime pay at time and a half."),
        )
        conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id_a, 2, "Vacation policy details are covered here."),
        )

        # Insert second file
        cursor = conn.execute(
            """INSERT INTO files (path, filename, sha256, mtime, size, status)
               VALUES (?, ?, ?, ?, ?, 'indexed')""",
            ("/test/contract_b.pdf", "contract_b.pdf", "hash_b_456", 1700000001.0, 2048),
        )
        file_id_b = cursor.lastrowid

        # Insert pages for second file with "overtime" term
        conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id_b, 1, "Article 5 - Overtime shall be compensated at double time."),
        )
        conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id_b, 2, "Grievance procedures are outlined in this section."),
        )

        return file_id_a, file_id_b


class TestCompareMultiSelect:
    """Tests for multi-select comparison functionality."""

    def test_compare_multi_select_returns_matches(self, client, two_indexed_files_with_shared_term):
        """Test GET /compare/results?doc_ids=1&doc_ids=2&topic=overtime returns matches from both files."""
        file_id_a, file_id_b = two_indexed_files_with_shared_term

        response = client.get(
            f"/compare/results?doc_ids={file_id_a}&doc_ids={file_id_b}&topic=overtime"
        )

        assert response.status_code == 200
        html = response.text

        # Should contain matches from both documents
        assert "contract_a.pdf" in html
        assert "contract_b.pdf" in html
        # Should contain the matched term (highlighted)
        assert "overtime" in html.lower()

    def test_compare_no_doc_ids_behaviour(self, client, two_indexed_files_with_shared_term):
        """Test /compare with no doc_ids returns document list for selection."""
        response = client.get("/compare")

        assert response.status_code == 200
        html = response.text

        # Should show the document selection UI
        assert "Select Documents to Compare" in html
        # Should contain checkboxes with doc_ids name
        assert 'name="doc_ids"' in html
        # Should list the available documents
        assert "contract_a.pdf" in html
        assert "contract_b.pdf" in html

    def test_compare_multi_select_service_function(self, test_db, two_indexed_files_with_shared_term):
        """Test compare_documents_multi service function directly."""
        file_id_a, file_id_b = two_indexed_files_with_shared_term

        result = compare_documents_multi([file_id_a, file_id_b], topic="overtime")

        # Should have two documents
        assert len(result["documents"]) == 2
        filenames = [d["filename"] for d in result["documents"]]
        assert "contract_a.pdf" in filenames
        assert "contract_b.pdf" in filenames

        # Should have matches from both documents
        assert len(result["matches"]) >= 2
        match_files = set(m["filename"] for m in result["matches"])
        assert "contract_a.pdf" in match_files
        assert "contract_b.pdf" in match_files

        # Topic should be set
        assert result["topic"] == "overtime"

    def test_compare_multi_select_no_matches(self, client, two_indexed_files_with_shared_term):
        """Test comparison with a term that doesn't exist in documents."""
        file_id_a, file_id_b = two_indexed_files_with_shared_term

        response = client.get(
            f"/compare/results?doc_ids={file_id_a}&doc_ids={file_id_b}&topic=nonexistentterm"
        )

        assert response.status_code == 200
        html = response.text

        # Should indicate no matches found
        assert "No matches found" in html

    def test_compare_multi_select_single_doc_falls_back(self, client, two_indexed_files_with_shared_term):
        """Test that selecting only one document shows selection prompt."""
        file_id_a, _ = two_indexed_files_with_shared_term

        response = client.get(
            f"/compare/results?doc_ids={file_id_a}&topic=overtime"
        )

        assert response.status_code == 200
        html = response.text

        # Should show prompt to select more documents (falls back to default view)
        assert "two or more" in html.lower() or "Select Documents" in html

    def test_compare_multi_select_empty_topic(self, client, two_indexed_files_with_shared_term):
        """Test comparison without a topic shows prompt to enter one."""
        file_id_a, file_id_b = two_indexed_files_with_shared_term

        response = client.get(
            f"/compare/results?doc_ids={file_id_a}&doc_ids={file_id_b}"
        )

        assert response.status_code == 200
        html = response.text

        # Should show the documents being compared
        assert "Comparing" in html
        # Should prompt to enter a search term
        assert "Enter a search term" in html

    def test_compare_results_htmx_partial(self, client, two_indexed_files_with_shared_term):
        """Test HTMX request returns partial HTML."""
        file_id_a, file_id_b = two_indexed_files_with_shared_term

        response = client.get(
            f"/compare/results?doc_ids={file_id_a}&doc_ids={file_id_b}&topic=overtime",
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 200
        html = response.text

        # Should not contain full page layout (no doctype or html tag)
        assert "<!DOCTYPE" not in html
        # Should contain the results
        assert "overtime" in html.lower()

    def test_compare_multi_select_includes_page_links(self, client, two_indexed_files_with_shared_term):
        """Test that results include links to document pages."""
        file_id_a, file_id_b = two_indexed_files_with_shared_term

        response = client.get(
            f"/compare/results?doc_ids={file_id_a}&doc_ids={file_id_b}&topic=overtime"
        )

        assert response.status_code == 200
        html = response.text

        # Should contain links to view pages
        assert "/documents/" in html
        assert "/page/" in html
        assert "View page" in html

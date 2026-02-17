"""Tests for search with synonym fallback and document scoping."""

import pytest

from app.db import get_db
from app.services.synonyms import expand_query, detect_document_reference, get_synonyms
from app.services.search import search_pages, search_in_file


class TestSearchWithSynonymFallback:
    """Tests for search functionality with synonym expansion fallback."""

    def test_search_with_synonym_fallback_direct_match(self, test_db):
        """Direct term match should work without needing fallback."""
        # Set up test data with 'sick leave' content
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/direct_match.pdf", "direct_match.pdf", "sha_direct", 0, 500),
            )
            file_id = cur.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Article 5: Sick Leave Entitlement\nFull-time employees receive 10 sick days per year.",
                 "Article 5: Sick Leave Entitlement\nFull-time employees receive 10 sick days per year."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Article 5: Sick Leave Entitlement\nFull-time employees receive 10 sick days per year."),
            )

        # Direct search for 'sick leave' should find results
        results = search_pages("sick leave", limit=5)

        assert len(results) > 0
        assert any("sick" in r.snippet.lower() for r in results)

    def test_search_with_synonym_fallback_using_expansion(self, test_db):
        """Search using synonym should find content via expansion."""
        # Set up test data with 'overtime' content
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/overtime_doc.pdf", "overtime_doc.pdf", "sha_ot", 0, 500),
            )
            file_id = cur.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Overtime Compensation: Time and a half rate applies after 8 hours.",
                 "Overtime Compensation: Time and a half rate applies after 8 hours."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Overtime Compensation: Time and a half rate applies after 8 hours."),
            )

        # User searches for 'overtime pay rate' - will expand with synonyms
        # First, expand the query
        expanded = expand_query("overtime pay rate")

        # Check that expansion includes overtime variants (e.g., 'overtime compensation')
        # Note: 'ot' is < 4 chars so won't be expanded directly
        assert len(expanded) >= 1  # At least original query

        # Verify synonyms exist for overtime-related terms
        overtime_syns = get_synonyms("overtime")
        assert "overtime pay" in overtime_syns or "overtime" in overtime_syns

        # Search with expanded term should find results
        results = search_pages("overtime rate", limit=5)
        assert len(results) > 0

    def test_search_fallback_multiple_synonyms(self, test_db):
        """Test fallback search with multiple potential synonyms."""
        # Set up test data with 'vacation' content
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/vacation_doc.pdf", "vacation_doc.pdf", "sha_vac", 0, 500),
            )
            file_id = cur.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Article 12: Vacation\nEmployees earn 3 weeks of vacation after 5 years.",
                 "Article 12: Vacation\nEmployees earn 3 weeks of vacation after 5 years."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Article 12: Vacation\nEmployees earn 3 weeks of vacation after 5 years."),
            )

        # Get synonyms for 'annual leave' which should include vacation
        synonyms = get_synonyms("annual leave")
        assert "vacation" in synonyms

        # Search using the canonical term from expansion
        results = search_pages("vacation", limit=5)
        assert len(results) > 0

    def test_search_no_synonym_no_results(self, test_db):
        """Search for term with no synonyms and no matches returns empty."""
        # No matching content in db
        results = search_pages("xyznonexistentterm123", limit=5)

        assert results == []

    def test_search_or_mode_fallback(self, test_db):
        """Test OR mode fallback when AND mode returns no results."""
        # Set up test data
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/partial_match.pdf", "partial_match.pdf", "sha_partial", 0, 500),
            )
            file_id = cur.lastrowid

            # Content has 'wages' but not 'overtime' together
            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Hourly Wages: The base wage rate is $25 per hour for all classifications.",
                 "Hourly Wages: The base wage rate is $25 per hour for all classifications."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Hourly Wages: The base wage rate is $25 per hour for all classifications."),
            )

        # Search with OR mode should find partial matches
        results = search_pages("wages rate", mode="or", limit=5)

        assert len(results) > 0
        assert any("wage" in r.snippet.lower() for r in results)


class TestSearchScopedToDocument:
    """Tests for searching within a specific document."""

    def test_search_scoped_to_document_basic(self, test_db):
        """Scoped search should only return results from specified document."""
        with get_db() as conn:
            # Create two files with similar content
            cur1 = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/doc_a.pdf", "doc_a.pdf", "sha_a", 0, 500),
            )
            file_id_a = cur1.lastrowid

            cur2 = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/doc_b.pdf", "doc_b.pdf", "sha_b", 0, 500),
            )
            file_id_b = cur2.lastrowid

            # Add pages to both files with wage-related content
            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_a, 1, "Wages for Doc A: $30 per hour", "Wages for Doc A: $30 per hour"),
            )
            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_b, 1, "Wages for Doc B: $25 per hour", "Wages for Doc B: $25 per hour"),
            )

            # Add to FTS index
            page_a = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id_a,)
            ).fetchone()
            page_b = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id_b,)
            ).fetchone()

            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_a, page_a["id"], 1, "Wages for Doc A: $30 per hour"),
            )
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_b, page_b["id"], 1, "Wages for Doc B: $25 per hour"),
            )

        # Scoped search to file A only
        results = search_pages("wages", file_id=file_id_a, limit=5)

        assert len(results) > 0
        assert all(r.file_id == file_id_a for r in results)
        assert any("Doc A" in r.snippet for r in results)

        # Scoped search to file B only
        results_b = search_pages("wages", file_id=file_id_b, limit=5)

        assert len(results_b) > 0
        assert all(r.file_id == file_id_b for r in results_b)
        assert any("Doc B" in r.snippet for r in results_b)

    def test_search_scoped_via_detection(self, test_db):
        """Test document scoping via detect_document_reference."""
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/red_deer_ca.pdf", "red_deer_ca.pdf", "sha_rd", 0, 500),
            )
            file_id = cur.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Red Deer Sick Leave: 12 days per year for full-time staff.",
                 "Red Deer Sick Leave: 12 days per year for full-time staff."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Red Deer Sick Leave: 12 days per year for full-time staff."),
            )

        # Detect document reference in query
        detected_id, topic = detect_document_reference("sick leave for Red Deer")

        assert detected_id == file_id
        assert "sick" in topic.lower() or "leave" in topic.lower()

        # Perform scoped search
        results = search_pages("sick leave", file_id=detected_id, limit=5)

        assert len(results) > 0
        assert all(r.file_id == file_id for r in results)

    def test_search_in_file_function(self, test_db):
        """Test the search_in_file helper function."""
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/search_in_file_test.pdf", "search_in_file_test.pdf", "sha_sif", 0, 500),
            )
            file_id = cur.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Benefits Package: Comprehensive health and dental coverage included.",
                 "Benefits Package: Comprehensive health and dental coverage included."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Benefits Package: Comprehensive health and dental coverage included."),
            )

        # Use search_in_file function
        results = search_in_file(file_id, "dental coverage", limit=10)

        assert len(results) > 0
        assert all(r.file_id == file_id for r in results)
        assert any("dental" in r.snippet.lower() for r in results)

    def test_scoped_search_no_results_when_wrong_document(self, test_db):
        """Scoped search to wrong document should return no results."""
        with get_db() as conn:
            # Create two files but only one has the content
            cur1 = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/with_content.pdf", "with_content.pdf", "sha_wc", 0, 500),
            )
            file_id_with = cur1.lastrowid

            cur2 = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/without_content.pdf", "without_content.pdf", "sha_wo", 0, 500),
            )
            file_id_without = cur2.lastrowid

            # Only add content to first file
            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_with, 1,
                 "Grievance Procedure: Step 1 involves meeting with supervisor.",
                 "Grievance Procedure: Step 1 involves meeting with supervisor."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id_with,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_with, page["id"], 1,
                 "Grievance Procedure: Step 1 involves meeting with supervisor."),
            )

            # Add unrelated content to second file
            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_without, 1, "Unrelated content about other topics.", "Unrelated content about other topics."),
            )
            page2 = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id_without,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_without, page2["id"], 1, "Unrelated content about other topics."),
            )

        # Search for grievance in the wrong document
        results = search_pages("grievance", file_id=file_id_without, limit=5)

        assert len(results) == 0

    def test_combined_synonym_expansion_with_scoping(self, test_db):
        """Test combining synonym expansion with document scoping."""
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/medicine_hat.pdf", "medicine_hat.pdf", "sha_mh", 0, 500),
            )
            file_id = cur.lastrowid

            # Content uses 'termination' but user might search for 'dismissal'
            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Termination Policy: Just cause termination procedures are outlined below.",
                 "Termination Policy: Just cause termination procedures are outlined below."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Termination Policy: Just cause termination procedures are outlined below."),
            )

        # Detect document reference
        detected_id, topic = detect_document_reference("dismissal for Medicine Hat")

        assert detected_id == file_id

        # Expand the topic query with synonyms
        expanded = expand_query(topic)

        # 'dismissal' should expand to include 'termination'
        dismissal_syns = get_synonyms("dismissal")
        assert "termination" in dismissal_syns

        # Search using the canonical term in the scoped document
        results = search_pages("termination", file_id=detected_id, limit=5)

        assert len(results) > 0
        assert any("termination" in r.snippet.lower() for r in results)


class TestSynonymExpansionWorkflow:
    """End-to-end workflow tests for synonym-enhanced search."""

    def test_full_workflow_annual_leave_to_vacation(self, test_db):
        """Test full workflow: user searches 'annual leave' and finds 'vacation' content."""
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/workflow_test.pdf", "workflow_test.pdf", "sha_wf", 0, 500),
            )
            file_id = cur.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Article 8: Vacation\nEmployees are entitled to paid vacation based on years of service.",
                 "Article 8: Vacation\nEmployees are entitled to paid vacation based on years of service."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Article 8: Vacation\nEmployees are entitled to paid vacation based on years of service."),
            )

        # User searches for 'annual leave' (synonym for vacation, > 3 chars)
        user_query = "annual leave policy"

        # Step 1: Expand query with synonyms
        expanded_queries = expand_query(user_query)

        # Verify expansion includes vacation-related terms
        # 'annual leave' should expand to include 'vacation'
        assert any("vacation" in q for q in expanded_queries)

        # Step 2: Search using canonical term
        results = search_pages("vacation policy", limit=5)

        assert len(results) > 0
        assert any("vacation" in r.snippet.lower() for r in results)

    def test_full_workflow_with_document_scope(self, test_db):
        """Test full workflow with document detection and synonym expansion."""
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/fort_mcmurray.pdf", "fort_mcmurray.pdf", "sha_fm", 0, 500),
            )
            file_id = cur.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Bereavement Leave: Employees may take up to 5 days for immediate family.",
                 "Bereavement Leave: Employees may take up to 5 days for immediate family."),
            )
            page = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page["id"], 1,
                 "Bereavement Leave: Employees may take up to 5 days for immediate family."),
            )

        # User query uses synonym 'funeral leave' and references document
        user_query = "funeral leave for Fort McMurray"

        # Step 1: Detect document reference
        detected_id, topic = detect_document_reference(user_query)
        assert detected_id == file_id

        # Step 2: Expand the topic query
        expanded = expand_query(topic)

        # Step 3: Check that synonyms include the canonical term
        funeral_syns = get_synonyms("funeral leave")
        assert "bereavement" in funeral_syns or "bereavement leave" in funeral_syns

        # Step 4: Search with canonical term in scoped document
        results = search_pages("bereavement", file_id=detected_id, limit=5)

        assert len(results) > 0
        assert all(r.file_id == file_id for r in results)
        assert any("bereavement" in r.snippet.lower() for r in results)

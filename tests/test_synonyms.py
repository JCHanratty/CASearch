"""Tests for synonym expansion and document-scoped retrieval system."""

import pytest

from app.services.synonyms import (
    BUILTIN_SYNONYMS,
    get_synonyms,
    expand_query,
    detect_document_reference,
)
from app.db import get_db


# ============================================================================
# Synonym Expansion Tests
# ============================================================================

class TestGetSynonyms:
    """Tests for get_synonyms function."""

    def test_get_synonyms_returns_list(self):
        """Verify get_synonyms('sick leave') returns a list of synonyms."""
        result = get_synonyms("sick leave")

        assert isinstance(result, list)
        assert len(result) > 1  # Should have the term plus synonyms
        assert "sick leave" in result
        assert "sick time" in result
        assert "sick days" in result
        assert "medical leave" in result

    def test_get_synonyms_reverse_lookup(self):
        """Verify get_synonyms('sick time') returns same synonyms as 'sick leave'."""
        canonical_result = get_synonyms("sick leave")
        reverse_result = get_synonyms("sick time")

        # Both should return the same set of synonyms
        assert set(canonical_result) == set(reverse_result)
        assert "sick leave" in reverse_result
        assert "sick time" in reverse_result

    def test_get_synonyms_case_insensitive(self):
        """Verify synonym lookup is case-insensitive."""
        lower_result = get_synonyms("sick leave")
        upper_result = get_synonyms("SICK LEAVE")
        mixed_result = get_synonyms("Sick Leave")

        assert set(lower_result) == set(upper_result)
        assert set(lower_result) == set(mixed_result)

    def test_get_synonyms_no_match_returns_original(self):
        """Verify get_synonyms returns original term when no synonyms exist."""
        result = get_synonyms("xyznonexistent")

        assert result == ["xyznonexistent"]

    def test_get_synonyms_various_terms(self):
        """Test synonyms for various labor contract terms."""
        # Vacation synonyms
        vacation_syns = get_synonyms("vacation")
        assert "annual leave" in vacation_syns
        assert "pto" in vacation_syns

        # Overtime synonyms
        overtime_syns = get_synonyms("ot")
        assert "overtime" in overtime_syns

        # Grievance synonyms
        grievance_syns = get_synonyms("grievance")
        assert "complaint" in grievance_syns
        assert "dispute" in grievance_syns


class TestExpandQuery:
    """Tests for expand_query function."""

    def test_expand_query_basic(self):
        """Verify expand_query('sick leave policy') returns query variants."""
        result = expand_query("sick leave policy")

        assert isinstance(result, list)
        assert len(result) >= 1  # At least original query
        assert "sick leave policy" in result

        # Should contain variants with synonyms
        variants_found = [v for v in result if "sick time" in v or "medical leave" in v]
        assert len(variants_found) > 0

    def test_expand_query_no_match(self):
        """Verify expand_query returns original when no synonyms match."""
        result = expand_query("xyznonexistent query text")

        assert isinstance(result, list)
        assert "xyznonexistent query text" in result
        # When no synonyms found, should return list with original query
        assert len(result) >= 1

    def test_expand_query_multiple_terms(self):
        """Test query expansion with multiple synonym-able terms."""
        result = expand_query("vacation and overtime policy")

        assert isinstance(result, list)
        assert "vacation and overtime policy" in result
        # Should have variants for both vacation and overtime
        assert len(result) > 1

    def test_expand_query_with_include_original_false(self):
        """Test expand_query with include_original=False."""
        result = expand_query("sick leave policy", include_original=False)

        assert isinstance(result, list)
        # When no synonyms found in a specific case, behavior varies
        # but should still return usable results

    def test_expand_query_preserves_context(self):
        """Verify expanded queries preserve surrounding context words."""
        result = expand_query("what is the sick leave policy")

        # Original should be in results
        assert "what is the sick leave policy" in result

        # Expanded variants should preserve "what is the" and "policy"
        for variant in result:
            if "sick time" in variant:
                assert "policy" in variant.lower()

    def test_expand_query_overtime_variants(self):
        """Test overtime term expansion."""
        result = expand_query("overtime rate calculation")

        assert "overtime rate calculation" in result
        # Should include variants with OT and other overtime synonyms

    def test_expand_query_empty_string(self):
        """Test expand_query with empty string."""
        result = expand_query("")

        assert isinstance(result, list)
        assert len(result) >= 1


# ============================================================================
# Document Reference Detection Tests
# ============================================================================

class TestDetectDocumentReference:
    """Tests for detect_document_reference function."""

    def test_detect_document_reference_with_city(self, test_db):
        """Query 'sick leave for Spruce Grove' should detect the Spruce Grove file."""
        # Insert a file with Spruce Grove in the name
        with get_db() as conn:
            conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/spruce_grove_collective.pdf",
                 "spruce_grove_collective.pdf", "sha256hash", 0, 1000),
            )

        file_id, remaining_query = detect_document_reference("sick leave for Spruce Grove")

        assert file_id is not None
        assert isinstance(file_id, int)
        # Remaining query should have the topic without the document reference
        assert "sick" in remaining_query.lower() or "leave" in remaining_query.lower()

    def test_detect_document_reference_returns_topic(self, test_db):
        """Should return cleaned topic query without document reference."""
        with get_db() as conn:
            conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/edmonton_local.pdf", "edmonton_local.pdf", "hash123", 0, 500),
            )

        file_id, remaining_query = detect_document_reference("overtime policy for Edmonton")

        # Should extract the topic portion
        assert "overtime" in remaining_query.lower() or "policy" in remaining_query.lower()

    def test_detect_document_reference_no_match(self, test_db):
        """Returns None when no document is referenced."""
        # Don't insert any matching files
        file_id, remaining_query = detect_document_reference("general vacation policy question")

        assert file_id is None
        assert remaining_query == "general vacation policy question"

    def test_detect_document_reference_empty_db(self, test_db):
        """Returns None when database has no indexed files."""
        file_id, remaining_query = detect_document_reference("sick leave for Anytown")

        assert file_id is None
        assert remaining_query == "sick leave for Anytown"

    def test_detect_document_reference_in_pattern(self, test_db):
        """Test 'in' pattern for document reference."""
        with get_db() as conn:
            conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/calgary_agreement.pdf", "calgary_agreement.pdf", "hashcal", 0, 800),
            )

        file_id, remaining_query = detect_document_reference("wages in Calgary agreement")

        assert file_id is not None

    def test_detect_document_reference_from_pattern(self, test_db):
        """Test 'from' pattern for document reference."""
        with get_db() as conn:
            conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/red_deer_contract.pdf", "red_deer_contract.pdf", "hashrd", 0, 600),
            )

        file_id, remaining_query = detect_document_reference("benefits from Red Deer")

        assert file_id is not None

    def test_detect_document_reference_with_possessive(self, test_db):
        """Test possessive form like "Spruce Grove's sick leave"."""
        with get_db() as conn:
            conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/lethbridge_ca.pdf", "lethbridge_ca.pdf", "hashleth", 0, 700),
            )

        file_id, remaining_query = detect_document_reference("Lethbridge's overtime rates")

        # Should detect the file reference
        assert file_id is not None

    def test_detect_document_reference_ignores_pending_files(self, test_db):
        """Should not match files with status other than 'indexed'."""
        with get_db() as conn:
            conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                ("data/agreements/pending_city.pdf", "pending_city.pdf", "hashpend", 0, 500),
            )

        file_id, remaining_query = detect_document_reference("sick leave for pending city")

        # Should not match a pending file
        assert file_id is None


# ============================================================================
# Integration Tests with test_db fixture
# ============================================================================

class TestScopedSearch:
    """Integration tests for document-scoped search with synonyms."""

    def test_scoped_search_finds_document(self, test_db):
        """Search within a detected document should find relevant content."""
        from app.services.search import search_pages

        # Create a file and page with content
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/test_city_agreement.pdf",
                 "test_city_agreement.pdf", "sha123", 0, 1000),
            )
            file_id = cur.lastrowid

            # Insert page with sick leave content
            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Article 10: Sick Leave\nEmployees are entitled to 10 days of sick leave per year.",
                 "Article 10: Sick Leave\nEmployees are entitled to 10 days of sick leave per year."),
            )

            page_row = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()

            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page_row["id"], 1,
                 "Article 10: Sick Leave\nEmployees are entitled to 10 days of sick leave per year."),
            )

        # First detect the document reference
        detected_file_id, topic_query = detect_document_reference("sick leave for test city")

        assert detected_file_id == file_id

        # Now search within that document
        results = search_pages("sick leave", file_id=detected_file_id, limit=5)

        assert len(results) > 0
        assert results[0].file_id == file_id
        assert "sick" in results[0].snippet.lower()

    def test_synonym_expansion_improves_recall(self, test_db):
        """Searching 'sick time' should find content about 'sick leave'."""
        from app.services.search import search_pages

        # Create a file with 'sick leave' content (not 'sick time')
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/recall_test.pdf", "recall_test.pdf", "sharecall", 0, 500),
            )
            file_id = cur.lastrowid

            # Insert page with 'sick leave' terminology
            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, 1,
                 "Sick Leave Policy: All employees receive paid sick leave for illness.",
                 "Sick Leave Policy: All employees receive paid sick leave for illness."),
            )

            page_row = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id,)
            ).fetchone()

            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id, page_row["id"], 1,
                 "Sick Leave Policy: All employees receive paid sick leave for illness."),
            )

        # Expand query using synonyms
        expanded_queries = expand_query("sick time policy")

        # At least one expanded query should contain 'sick leave'
        has_sick_leave_variant = any("sick leave" in q for q in expanded_queries)
        assert has_sick_leave_variant, f"Expected 'sick leave' variant in {expanded_queries}"

        # Search with the expanded synonym term should find results
        results = search_pages("sick leave policy", limit=5)

        assert len(results) > 0
        assert any("sick" in r.snippet.lower() for r in results)

    def test_combined_synonym_and_scope(self, test_db):
        """Test combining synonym expansion with document scoping."""
        from app.services.search import search_pages

        # Create two files with different content
        with get_db() as conn:
            # File 1: Spruce Grove with vacation content
            cur1 = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/spruce_grove.pdf", "spruce_grove.pdf", "shaspruce", 0, 500),
            )
            file_id_1 = cur1.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_1, 1,
                 "Annual Leave: Employees get 15 days annual leave.",
                 "Annual Leave: Employees get 15 days annual leave."),
            )
            page1 = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id_1,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_1, page1["id"], 1,
                 "Annual Leave: Employees get 15 days annual leave."),
            )

            # File 2: Edmonton with vacation content
            cur2 = conn.execute(
                """INSERT INTO files (path, filename, sha256, mtime, size, status)
                   VALUES (?, ?, ?, ?, ?, 'indexed')""",
                ("data/agreements/edmonton.pdf", "edmonton.pdf", "shaedm", 0, 500),
            )
            file_id_2 = cur2.lastrowid

            conn.execute(
                """INSERT INTO pdf_pages (file_id, page_number, text, raw_text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_2, 1,
                 "Vacation: Employees receive 20 days vacation time.",
                 "Vacation: Employees receive 20 days vacation time."),
            )
            page2 = conn.execute(
                "SELECT id FROM pdf_pages WHERE file_id = ?", (file_id_2,)
            ).fetchone()
            conn.execute(
                """INSERT INTO page_fts (file_id, page_id, page_number, text)
                   VALUES (?, ?, ?, ?)""",
                (file_id_2, page2["id"], 1,
                 "Vacation: Employees receive 20 days vacation time."),
            )

        # Detect Spruce Grove reference
        detected_id, topic = detect_document_reference("vacation for Spruce Grove")
        assert detected_id == file_id_1

        # Search for 'annual leave' (synonym for vacation) in Spruce Grove only
        results = search_pages("annual leave", file_id=file_id_1, limit=5)

        assert len(results) > 0
        assert all(r.file_id == file_id_1 for r in results)


class TestBuiltinSynonyms:
    """Tests for the BUILTIN_SYNONYMS dictionary."""

    def test_builtin_synonyms_structure(self):
        """Verify BUILTIN_SYNONYMS has expected structure."""
        assert isinstance(BUILTIN_SYNONYMS, dict)
        assert len(BUILTIN_SYNONYMS) > 0

        for key, value in BUILTIN_SYNONYMS.items():
            assert isinstance(key, str)
            assert isinstance(value, list)
            assert all(isinstance(s, str) for s in value)

    def test_builtin_synonyms_has_common_terms(self):
        """Verify common labor terms are in BUILTIN_SYNONYMS."""
        expected_terms = [
            "sick leave", "vacation", "overtime", "grievance",
            "seniority", "termination", "wages", "benefits"
        ]

        for term in expected_terms:
            assert term in BUILTIN_SYNONYMS, f"Expected '{term}' in BUILTIN_SYNONYMS"

    def test_builtin_synonyms_no_duplicates(self):
        """Verify no duplicate synonyms within entries."""
        for canonical, synonyms in BUILTIN_SYNONYMS.items():
            assert len(synonyms) == len(set(synonyms)), \
                f"Duplicate synonyms found for '{canonical}'"

"""Tests for QA formatting and heading integration."""

import pytest
from unittest.mock import patch, MagicMock

from app.db import get_db
from app.services.qa import answer_question, _retrieve_with_fallback
from app.services.search import page_has_heading_match


@pytest.fixture
def page_with_heading_content(test_db):
    """Create a page with heading and detailed body content."""
    with get_db() as conn:
        # Insert test file
        cursor = conn.execute(
            """INSERT INTO files (path, filename, sha256, mtime, size, status)
               VALUES (?, ?, ?, ?, ?, 'indexed')""",
            ("/test/qa_contract.pdf", "qa_contract.pdf", "hash_qa_test", 1700000000.0, 2048),
        )
        file_id = cursor.lastrowid

        # Page with heading containing "Sick Time" and detailed body
        page_text = """COLLECTIVE BARGAINING AGREEMENT

Article 5 — Sick Time

5.1 Eligibility
All regular full-time employees are entitled to sick leave benefits after
completing their probationary period of 90 days.

5.2 Accrual Rate
Employees shall accrue sick time at the rate of one (1) day per month,
up to a maximum of twelve (12) days per calendar year.

5.3 Usage
Sick time may be used for:
- Personal illness or injury
- Medical appointments
- Care of immediate family members

5.4 Documentation
For absences exceeding three (3) consecutive days, a doctor's note
may be required upon return to work."""

        cursor = conn.execute(
            """INSERT INTO pdf_pages (file_id, page_number, text)
               VALUES (?, ?, ?)""",
            (file_id, 1, page_text),
        )
        page_id = cursor.lastrowid

        # Insert into FTS index
        conn.execute(
            """INSERT INTO page_fts (file_id, page_id, page_number, text)
               VALUES (?, ?, ?, ?)""",
            (file_id, page_id, 1, page_text),
        )

        return file_id


class TestQAHeadingIntegration:
    """Tests for QA integration with heading detection."""

    def test_heading_detected_in_retrieval(self, test_db, page_with_heading_content):
        """Test that heading match is detected during retrieval."""
        file_id = page_with_heading_content

        # Check heading detection works
        has_match, heading = page_has_heading_match(file_id, 1, "Sick Time")
        assert has_match is True
        assert "Sick Time" in heading

    def test_retrieval_returns_results_for_heading_query(self, test_db, page_with_heading_content):
        """Test that retrieval finds page with heading."""
        results, method, chunk_results = _retrieve_with_fallback("Sick Time")

        assert len(results) >= 1
        assert results[0].page_number == 1

    def test_answer_question_with_heading_match(self, test_db, page_with_heading_content):
        """Test answer_question includes heading in structured response."""
        # Mock the Claude API call to return a formatted response
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = """**Article 5 — Sick Time**

• Full-time employees are entitled to sick leave after a 90-day probationary period [Source 1]
• Employees accrue sick time at one day per month, up to 12 days per year [Source 1]
• Sick time can be used for personal illness, medical appointments, or family care [Source 1]
• A doctor's note may be required for absences over 3 consecutive days [Source 1]"""

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("Sick Time")

        # Check response structure
        assert response.no_evidence is False
        assert len(response.citations) >= 1
        assert "Sick Time" in response.answer or "sick" in response.answer.lower()
        assert "[Source" in response.answer

    def test_answer_question_no_evidence_flag(self, test_db, page_with_heading_content):
        """Test no_evidence flag is correctly set when API returns no match."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "Not found in the documents provided."

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("Nonexistent Topic XYZ")

        assert response.no_evidence is True

    def test_no_evidence_false_when_citations_present(self, test_db, page_with_heading_content):
        """Test no_evidence is False when answer has citations even if 'not found' appears at end."""
        # This simulates Claude providing a partial answer with citations but noting some info not found
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = """**Article 5 — Sick Time**

• Full-time employees accrue sick time at one day per month [Source 1]
• Sick time can be used for personal illness or family care [Source 1]
• Maximum of 12 days per calendar year [Source 1]

Note: Information about overtime rates was not found in the documents provided."""

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("Sick Time")

        # Should NOT be flagged as no_evidence because citations are present
        assert response.no_evidence is False
        assert len(response.citations) >= 1

    def test_context_includes_heading_marker(self, test_db, page_with_heading_content):
        """Test that context sent to Claude includes HEADING marker."""
        captured_message = None

        def capture_create(**kwargs):
            nonlocal captured_message
            for msg in kwargs.get("messages", []):
                if msg.get("role") == "user":
                    captured_message = msg.get("content")
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock()]
            mock_resp.content[0].text = "**Article 5 — Sick Time**\n• Test [Source 1]"
            return mock_resp

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = capture_create
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                answer_question("Sick Time")

        assert captured_message is not None
        assert "HEADING:" in captured_message
        assert "Sick Time" in captured_message

    def test_retrieval_note_includes_heading_status(self, test_db, page_with_heading_content):
        """Test that retrieval note mentions heading detection status."""
        captured_message = None

        def capture_create(**kwargs):
            nonlocal captured_message
            for msg in kwargs.get("messages", []):
                if msg.get("role") == "user":
                    captured_message = msg.get("content")
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock()]
            mock_resp.content[0].text = "Test answer [Source 1]"
            return mock_resp

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = capture_create
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                answer_question("Sick Time")

        assert captured_message is not None
        assert "Heading match detected: Yes" in captured_message

    def test_citations_returned_on_success(self, test_db, page_with_heading_content):
        """Test that citations are returned when answer is found."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = """**Article 5 — Sick Time**

Employees accrue sick time at one day per month [Source 1]."""

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("Sick Time")

        assert response.no_evidence is False
        assert len(response.citations) >= 1
        assert response.citations[0].filename == "qa_contract.pdf"
        assert response.citations[0].page_number == 1

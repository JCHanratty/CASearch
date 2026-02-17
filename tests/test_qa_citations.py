"""Tests for QA citation and heading enforcement."""

import pytest
from unittest.mock import patch, MagicMock

from app.db import get_db
from app.services.qa import answer_question, validate_qa_response


@pytest.fixture
def page_with_heading_content(test_db):
    """Create a page with heading and detailed body content."""
    with get_db() as conn:
        # Insert test file
        cursor = conn.execute(
            """INSERT INTO files (path, filename, sha256, mtime, size, status)
               VALUES (?, ?, ?, ?, ?, 'indexed')""",
            ("/test/citation_contract.pdf", "citation_contract.pdf", "hash_citation_test", 1700000000.0, 2048),
        )
        file_id = cursor.lastrowid

        # Page with heading containing "Vacation Policy" and detailed body
        page_text = """COLLECTIVE BARGAINING AGREEMENT

Article 7 — Vacation Policy

7.1 Eligibility
All regular full-time employees are entitled to vacation benefits after
completing their first year of employment.

7.2 Accrual Rate
Employees shall accrue vacation time as follows:
- 1-5 years: 10 days per year
- 6-10 years: 15 days per year
- 11+ years: 20 days per year

7.3 Scheduling
Vacation requests must be submitted at least two weeks in advance.
Seniority will be considered when scheduling conflicts arise.

7.4 Carryover
Unused vacation days may be carried over to the next year,
up to a maximum of 5 days."""

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


class TestResponseHasHeadingWhenProvided:
    """Test that responses include headings when provided in context."""

    def test_response_has_heading_when_provided(self, test_db, page_with_heading_content):
        """Test that response starts with bold heading when heading is detected."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = """**Article 7 — Vacation Policy**

• Full-time employees are entitled to vacation after one year of employment [Source 1]
• Vacation accrual: 10 days (1-5 years), 15 days (6-10 years), 20 days (11+ years) [Source 1]
• Vacation requests require two weeks advance notice [Source 1]
• Up to 5 unused days may be carried over to next year [Source 1]

Sources:
- Source 1: citation_contract.pdf, Page 1"""

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("Vacation Policy")

        # Check response starts with bold heading
        assert response.answer.strip().startswith("**")
        assert "Vacation" in response.answer
        assert response.no_evidence is False

    def test_response_without_heading_when_not_detected(self, test_db, page_with_heading_content):
        """Test that response can work without heading when not detected."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = """• The contract mentions vacation benefits [Source 1]
• Specific details about retirement are not found in the documents provided [Source 1]

Sources:
- Source 1: citation_contract.pdf, Page 1"""

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                # Query something that won't match a heading
                response = answer_question("retirement benefits")

        # Response should still be valid even without heading
        assert response.no_evidence is False
        assert "[Source" in response.answer


class TestResponseHasCitations:
    """Test that responses include proper citations."""

    def test_response_has_citations(self, test_db, page_with_heading_content):
        """Test that response includes [Source X] citations."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = """**Article 7 — Vacation Policy**

• Employees accrue vacation time based on years of service [Source 1]
• 1-5 years: 10 days, 6-10 years: 15 days, 11+ years: 20 days [Source 1]

Sources:
- Source 1: citation_contract.pdf, Page 1"""

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("Vacation Policy")

        # Check for citations
        assert "[Source 1]" in response.answer
        assert response.no_evidence is False
        assert len(response.citations) >= 1


class TestResponseUsesBulletPoints:
    """Test that responses use bullet points."""

    def test_response_uses_bullet_points(self, test_db, page_with_heading_content):
        """Test that response uses bullet character for bullet points."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = """**Article 7 — Vacation Policy**

• Full-time employees get vacation after one year [Source 1]
• Accrual rates vary by years of service [Source 1]
• Vacation requests need two weeks notice [Source 1]

Sources:
- Source 1: citation_contract.pdf, Page 1"""

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("Vacation Policy")

        # Check for bullet character
        assert "•" in response.answer
        assert response.no_evidence is False


class TestValidateQAResponse:
    """Tests for the validate_qa_response function."""

    def test_validate_qa_response_valid(self):
        """Test validation passes for properly formatted response."""
        valid_response = """**Article 7 — Vacation Policy**

• Full-time employees get vacation after one year of employment [Source 1]
• Vacation accrual varies by years of service [Source 1]
• Requests require two weeks advance notice [Source 1]

Sources:
- Source 1: Contract.pdf, Page 15"""

        result = validate_qa_response(valid_response, heading_expected=True)

        assert result["valid"] is True
        assert result["issues"] == []

    def test_validate_qa_response_valid_no_heading_expected(self):
        """Test validation passes when heading not expected and not present."""
        response = """• Full-time employees get vacation after one year [Source 1]
• Vacation accrual varies by years of service [Source 1]

Sources:
- Source 1: Contract.pdf, Page 15"""

        result = validate_qa_response(response, heading_expected=False)

        assert result["valid"] is True
        assert result["issues"] == []

    def test_validate_qa_response_missing_citations(self):
        """Test validation fails when citations are missing."""
        response_no_citations = """**Article 7 — Vacation Policy**

• Full-time employees get vacation after one year of employment
• Vacation accrual varies by years of service
• Requests require two weeks advance notice"""

        result = validate_qa_response(response_no_citations, heading_expected=True)

        assert result["valid"] is False
        assert any("citation" in issue.lower() for issue in result["issues"])

    def test_validate_qa_response_missing_heading(self):
        """Test validation fails when heading expected but missing."""
        response_no_heading = """• Full-time employees get vacation after one year [Source 1]
• Vacation accrual varies by years of service [Source 1]

Sources:
- Source 1: Contract.pdf, Page 15"""

        result = validate_qa_response(response_no_heading, heading_expected=True)

        assert result["valid"] is False
        assert any("heading" in issue.lower() for issue in result["issues"])

    def test_validate_qa_response_missing_bullets(self):
        """Test validation fails when bullet points are missing."""
        response_no_bullets = """**Article 7 — Vacation Policy**

Full-time employees get vacation after one year of employment [Source 1].
Vacation accrual varies by years of service [Source 1].

Sources:
- Source 1: Contract.pdf, Page 15"""

        result = validate_qa_response(response_no_bullets, heading_expected=True)

        assert result["valid"] is False
        assert any("bullet" in issue.lower() for issue in result["issues"])

    def test_validate_qa_response_not_found_skips_validation(self):
        """Test that 'not found' responses skip formatting validation."""
        not_found_response = "Not found in the documents provided."

        result = validate_qa_response(not_found_response, heading_expected=True)

        # Should pass validation since it's a "not found" response
        assert result["valid"] is True
        assert result["issues"] == []

    def test_validate_qa_response_too_many_bullets(self):
        """Test validation fails when more than 6 bullets."""
        response_many_bullets = """**Article 7 — Vacation Policy**

• Point 1 [Source 1]
• Point 2 [Source 1]
• Point 3 [Source 1]
• Point 4 [Source 1]
• Point 5 [Source 1]
• Point 6 [Source 1]
• Point 7 [Source 1]
• Point 8 [Source 1]

Sources:
- Source 1: Contract.pdf, Page 15"""

        result = validate_qa_response(response_many_bullets, heading_expected=True)

        assert result["valid"] is False
        assert any("too many bullets" in issue.lower() for issue in result["issues"])

    def test_validate_qa_response_multiple_citations(self):
        """Test validation passes with multiple source citations."""
        response_multi_cite = """**Article 7 — Vacation Policy**

• Vacation eligibility is defined in Article 7.1 [Source 1]
• Accrual rates are specified in Article 7.2 [Source 1, Source 2]
• Scheduling procedures in Article 7.3 [Source 2]

Sources:
- Source 1: Contract_A.pdf, Page 15
- Source 2: Contract_B.pdf, Page 22"""

        result = validate_qa_response(response_multi_cite, heading_expected=True)

        assert result["valid"] is True
        assert result["issues"] == []


class TestNoEvidenceDetectionAccuracy:
    """Tests for no_evidence detection accuracy."""

    def test_no_evidence_detection_accuracy(self, test_db, page_with_heading_content):
        """Test that no_evidence flag is accurately set based on response content."""
        # Test 1: Clear "not found" response should set no_evidence=True
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

                response = answer_question("Vacation Policy")

        assert response.no_evidence is True

    def test_no_evidence_false_with_citations(self, test_db, page_with_heading_content):
        """Test that no_evidence is False when citations are present."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = """**Article 7 — Vacation Policy**

• Employees are entitled to vacation benefits [Source 1]
• Accrual rates vary by years of service [Source 1]

Note: Information about dental coverage was not found in the documents provided.

Sources:
- Source 1: citation_contract.pdf, Page 1"""

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("Vacation Policy")

        # Should NOT be flagged as no_evidence because citations are present
        assert response.no_evidence is False

    def test_no_evidence_short_not_found_response(self, test_db, page_with_heading_content):
        """Test that short responses with 'not found' set no_evidence=True."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "The documents do not contain information about retirement plans."

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                response = answer_question("retirement plans")

        assert response.no_evidence is True


class TestUserMessageFormatInstructions:
    """Tests verifying user message includes format instructions."""

    def test_user_message_includes_heading_instruction_when_detected(self, test_db, page_with_heading_content):
        """Test that user message includes heading instruction when heading is detected."""
        captured_message = None

        def capture_create(**kwargs):
            nonlocal captured_message
            for msg in kwargs.get("messages", []):
                if msg.get("role") == "user":
                    captured_message = msg.get("content")
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock()]
            mock_resp.content[0].text = "**Article 7 — Vacation Policy**\n• Test [Source 1]"
            return mock_resp

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = capture_create
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                answer_question("Vacation Policy")

        assert captured_message is not None
        assert "HEADING DETECTED" in captured_message or "bold heading" in captured_message.lower()

    def test_user_message_includes_format_requirements(self, test_db, page_with_heading_content):
        """Test that user message includes format requirements section."""
        captured_message = None

        def capture_create(**kwargs):
            nonlocal captured_message
            for msg in kwargs.get("messages", []):
                if msg.get("role") == "user":
                    captured_message = msg.get("content")
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock()]
            mock_resp.content[0].text = "**Article 7 — Vacation Policy**\n• Test [Source 1]"
            return mock_resp

        with patch("app.services.qa.anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = capture_create
            mock_anthropic.return_value = mock_client

            with patch("app.services.qa.settings") as mock_settings:
                mock_settings.ANTHROPIC_API_KEY = "test-key"
                mock_settings.MAX_RETRIEVAL_RESULTS = 5
                mock_settings.CLAUDE_MODEL = "claude-3-haiku-20240307"

                answer_question("Vacation Policy")

        assert captured_message is not None
        assert "FORMAT REQUIREMENTS" in captured_message
        assert "[Source X]" in captured_message
        assert "Maximum 6 bullets" in captured_message or "6 bullets" in captured_message

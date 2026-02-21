"""Tests for tutorial and suggested prompts feature."""

import pytest

from app.settings import settings


class TestTutorialAndPrompts:
    """Tests for tutorial page and suggested prompts in sidebar."""

    def test_qa_page_includes_suggested_prompts(self, client, test_db):
        """Test GET /qa contains suggested prompts as clickable chips."""
        response = client.get("/qa")

        assert response.status_code == 200
        html = response.text

        # Should contain at least one of the suggestion strings
        found_prompt = False
        for prompt in settings.SUGGESTED_PROMPTS:
            if prompt[:50] in html:  # Q&A page truncates at 50 chars
                found_prompt = True
                break
        assert found_prompt, "Expected at least one suggested prompt on Q&A page"

        # Should contain the prompt chip click handler
        assert "data-prompt=" in html

    def test_tutorial_page_loads(self, client, test_db):
        """Test GET /tutorial returns 200 and contains Tutorial header and example prompts."""
        response = client.get("/tutorial")

        assert response.status_code == 200
        html = response.text

        # Should contain the Tutorial header
        assert "Tutorial" in html

        # Should contain at least one example prompt
        found_prompt = False
        for prompt in settings.SUGGESTED_PROMPTS:
            if prompt in html:
                found_prompt = True
                break
        assert found_prompt, "Expected at least one example prompt on tutorial page"

        # Should contain the usePrompt JavaScript function
        assert "usePrompt(" in html

    def test_tutorial_page_contains_sections(self, client, test_db):
        """Test tutorial page contains all expected sections."""
        response = client.get("/tutorial")

        assert response.status_code == 200
        html = response.text

        # Should contain main sections (updated for new tutorial layout)
        assert "Quick Start Guide" in html
        assert "Try These Common Questions" in html
        assert "Search Tips" in html
        assert "Q&amp;A Best Practices" in html or "Q&A Best Practices" in html
        assert "Comparing Contracts" in html

    def test_qa_page_has_suggested_prompt_section(self, client, test_db):
        """Test Q&A page contains the 'Try a Suggested Prompt' section."""
        response = client.get("/qa")

        assert response.status_code == 200
        html = response.text

        # Should contain the suggested prompt section heading
        assert "Try a Suggested Prompt" in html

    def test_settings_has_suggested_prompts(self):
        """Test that settings contains SUGGESTED_PROMPTS with at least 6 items."""
        assert hasattr(settings, "SUGGESTED_PROMPTS")
        assert len(settings.SUGGESTED_PROMPTS) >= 6

        # Verify prompts are strings
        for prompt in settings.SUGGESTED_PROMPTS:
            assert isinstance(prompt, str)
            assert len(prompt) > 0

    def test_tutorial_link_in_sidebar(self, client, test_db):
        """Test that sidebar contains link to tutorial page."""
        response = client.get("/")

        assert response.status_code == 200
        html = response.text

        # Should contain link to tutorial
        assert 'href="/tutorial"' in html
